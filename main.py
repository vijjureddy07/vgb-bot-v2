"""
VGB Delta Bot v2 — Main Loop (Production)
============================================
Session-adaptive Gaussian system with:
- Self-repair watchdog
- Macro news filter
- Position recovery on restart
- Binance Futures execution
"""

import time as _time
import sys
import csv
import os

import config
from data_feed import CandleManager
from gaussian_engine import GaussianTracker, check_momentum
from session_manager import get_current_session, is_session_transition, format_session_status, get_ist_now
from executor import (
    get_balance, get_position, open_position, close_position,
    flip_position, cancel_all_orders, set_leverage, set_margin_type,
    get_ticker_price, get_server_time
)
from telegram_alerts import (
    alert_entry, alert_exit, alert_bias_change, alert_session_change,
    alert_error, alert_startup, alert_daily_summary, send_message
)
from news_filter import news_filter
from watchdog import Watchdog, recover_position_state, perform_daily_restart
from kill_switch import check_kill_switch, check_pause_switch, execute_emergency_shutdown
from balance_monitor import BalanceMonitor
from weekly_report import generate_weekly_report, generate_health_report


def log(msg, level='INFO'):
    ts = get_ist_now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    try:
        with open(config.LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except:
        pass


def log_trade(data):
    if not config.LOG_TRADES_TO_CSV:
        return
    exists = os.path.exists(config.TRADE_LOG_FILE)
    keys = ['timestamp', 'session', 'side', 'entry_price', 'exit_price',
            'size', 'pnl', 'reason', 'capital_after', 'mode']
    with open(config.TRADE_LOG_FILE, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=keys)
        if not exists:
            w.writeheader()
        w.writerow(data)


def main():
    log("=" * 60)
    log("VGB DELTA BOT v2 — STARTING (Binance Futures)")
    log(f"Mode: {'TESTNET' if config.USE_TESTNET else '*** LIVE ***'}")
    log(f"Symbol: {config.SYMBOL}")
    log("=" * 60)

    # Initialize
    candle_mgr = CandleManager()
    watchdog = Watchdog()

    trackers = {
        '1m': GaussianTracker('1m', config.GAUSSIAN_LENGTH, config.GAUSSIAN_DISTANCE),
        '3m': GaussianTracker('3m', config.GAUSSIAN_LENGTH, config.GAUSSIAN_DISTANCE),
        '5m': GaussianTracker('5m', config.GAUSSIAN_LENGTH, config.GAUSSIAN_DISTANCE),
        '15m': GaussianTracker('15m', config.GAUSSIAN_LENGTH, config.GAUSSIAN_DISTANCE),
    }

    # State
    prev_session = None
    current_position = None
    htf_bias = {}
    daily_trades = 0
    daily_pnl = 0.0
    daily_wins = 0
    daily_losses = 0
    last_daily_reset = get_ist_now().date()
    last_weekly_report = get_ist_now().isocalendar()[1]  # week number
    balance_monitor = None  # initialized after getting balance

    # Verify exchange connection
    server_time = get_server_time()
    if server_time is None:
        log("CRITICAL: Cannot connect to Binance. Check network.", 'ERROR')
        alert_error("Cannot connect to Binance. Check API keys and network.")
        return

    log(f"Binance connected. Server time: {server_time}")

    # Set margin type and leverage
    set_margin_type('CROSSED')
    set_leverage(config.DEFAULT_LEVERAGE)

    # Get balance
    balance = get_balance()
    if balance is None:
        log("CRITICAL: Cannot get balance. Check API keys.", 'ERROR')
        alert_error("Cannot get balance. Check API keys.")
        return

    log(f"Balance: ${balance:.2f} USDT")

    # Initialize balance monitor
    balance_monitor = BalanceMonitor(balance)

    # Recover position state
    recovered_pos = recover_position_state()
    if recovered_pos:
        current_position = {
            'side': recovered_pos['side'],
            'entry_price': recovered_pos['entry_price'],
            'size': recovered_pos['size'],
            'session': 'RECOVERED',
            'entry_time': get_ist_now(),
            'mode': 'recovered'
        }
        log(f"Recovered position: {recovered_pos['side']} {recovered_pos['size']} BTC")

    alert_startup(balance)

    # ============================================================
    # MAIN LOOP
    # ============================================================
    while True:
        try:
            now_ist = get_ist_now()

            # --- KILL SWITCH CHECK (highest priority) ---
            if check_kill_switch():
                log("KILL SWITCH ACTIVATED", 'CRITICAL')
                execute_emergency_shutdown()

            # --- PAUSE CHECK ---
            if check_pause_switch():
                if watchdog.should_heartbeat():
                    log("PAUSED — no new trades (file: PAUSE exists)")
                _time.sleep(30)
                continue

            # --- Weekly report (every Sunday) ---
            current_week = now_ist.isocalendar()[1]
            if current_week != last_weekly_report and now_ist.weekday() == 6:  # Sunday
                log("Generating weekly report...")
                report, formatted = generate_weekly_report()
                send_message(formatted)
                health = generate_health_report()
                send_message(health)
                last_weekly_report = current_week
                log("Weekly report sent")

            # --- Balance monitor update ---
            if balance_monitor:
                has_pos = current_position is not None
                fresh_balance = get_balance()
                if fresh_balance is not None:
                    _, event_type, event_amount = balance_monitor.update(fresh_balance, has_pos)
                    if event_type == 'WITHDRAWAL':
                        log(f"WITHDRAWAL DETECTED: ${event_amount:.2f} — balance now ${fresh_balance:.2f}")
                        send_message(f"💸 Withdrawal detected: ${event_amount:.2f}\nNew balance: ${fresh_balance:.2f}")
                        balance = fresh_balance
                    elif event_type == 'DEPOSIT':
                        log(f"DEPOSIT DETECTED: ${event_amount:.2f} — balance now ${fresh_balance:.2f}")
                        send_message(f"💰 Deposit detected: +${event_amount:.2f}\nNew balance: ${fresh_balance:.2f}")
                        balance = fresh_balance

                    # Safety check
                    safe, reason = balance_monitor.is_safe_to_trade(fresh_balance)
                    if not safe:
                        log(f"UNSAFE BALANCE: {reason}", 'WARNING')
                        send_message(f"⚠️ {reason}")
                        if current_position:
                            cancel_all_orders()
                            close_position()
                            current_position = None
                        _time.sleep(60)
                        continue

            # --- Daily reset ---
            if now_ist.date() != last_daily_reset:
                if daily_trades > 0:
                    alert_daily_summary(daily_trades, daily_pnl, balance, daily_wins, daily_losses)
                daily_trades = 0
                daily_pnl = 0.0
                daily_wins = 0
                daily_losses = 0
                last_daily_reset = now_ist.date()
                log(f"--- New day: {now_ist.date()} ---")

            # --- Daily restart check ---
            if watchdog.should_daily_restart():
                log("Daily restart triggered")
                if current_position:
                    cancel_all_orders()
                    close_position()
                    current_position = None
                send_message("🔄 Daily restart — cleaning memory")
                perform_daily_restart()

            # --- Safe mode check ---
            wait_secs = watchdog.should_wait()
            if wait_secs > 0:
                log(f"Safe mode: waiting {wait_secs}s")
                _time.sleep(wait_secs)
                watchdog.record_success()  # try to recover
                continue

            # --- Session detection ---
            session_name, session_config, session_end = get_current_session(now_ist)

            # --- Session transition ---
            if is_session_transition(prev_session, session_name):
                if prev_session and session_name != prev_session:
                    log(f"Session ending: {prev_session}")
                    alert_session_change(prev_session, 'CLOSE')

                    if current_position and config.CLOSE_AT_SESSION_END:
                        log("Closing position at session end")
                        cancel_all_orders()
                        close_result = close_position()

                        new_bal = get_balance()
                        pnl = (new_bal - balance) if new_bal else 0
                        if new_bal:
                            balance = new_bal

                        exit_price = get_ticker_price() or current_position['entry_price']

                        alert_exit(current_position['side'], current_position['entry_price'],
                                   exit_price, pnl, 'SESSION_END', balance)
                        log_trade({
                            'timestamp': now_ist.strftime('%Y-%m-%d %H:%M:%S'),
                            'session': prev_session, 'side': current_position['side'],
                            'entry_price': current_position['entry_price'],
                            'exit_price': exit_price, 'size': current_position.get('size', 0),
                            'pnl': pnl, 'reason': 'SESSION_END',
                            'capital_after': balance, 'mode': current_position.get('mode', '')
                        })
                        daily_pnl += pnl
                        daily_trades += 1
                        if pnl >= 0: daily_wins += 1
                        else: daily_losses += 1
                        current_position = None

                if session_name and session_name != prev_session:
                    log(f"Session starting: {session_name}")
                    log(format_session_status(session_name, session_config, session_end))
                    alert_session_change(session_name, 'OPEN')
                    lev = session_config.get('leverage', config.DEFAULT_LEVERAGE)
                    set_leverage(lev)
                    balance = get_balance() or balance

                prev_session = session_name

            # --- No session → sleep ---
            if session_name is None:
                if watchdog.should_heartbeat():
                    status = watchdog.get_status()
                    log(f"Health: No session | Uptime: {status['uptime']} | Errors: {status['errors_today']}")
                if watchdog.should_telegram_heartbeat():
                    status = watchdog.get_status()
                    send_message(f"💤 Idle | Balance: ${balance:.2f} | Uptime: {status['uptime']}")
                _time.sleep(30)
                continue

            # ============================================================
            # ACTIVE SESSION
            # ============================================================
            mode = session_config['mode']
            htf_tf = session_config['htf_timeframe']
            entry_tf = session_config['entry_timeframe']
            cap_pct = session_config.get('capital_pct', config.DEFAULT_CAPITAL_PCT)
            lev = session_config.get('leverage', config.DEFAULT_LEVERAGE)

            # --- News filter check ---
            news_blocked, news_reason = news_filter.is_blocked()
            if news_blocked:
                if watchdog.should_heartbeat():
                    log(f"NEWS BLOCK: {news_reason}")
                if config.ALERT_ON_NEWS_BLOCK and watchdog.should_heartbeat():
                    send_message(f"📰 Trading blocked: {news_reason}")
                _time.sleep(30)
                continue

            # --- Fetch candles ---
            df_htf = candle_mgr.get_candles(htf_tf)
            df_entry = candle_mgr.get_candles(entry_tf) if entry_tf != htf_tf else df_htf

            if df_htf is None:
                watchdog.record_failure(f"No {htf_tf} candle data")
                _time.sleep(10)
                continue

            watchdog.record_success()

            # --- Stale data check ---
            if watchdog.is_data_stale():
                log("WARNING: Candle data may be stale", 'WARNING')
                candle_mgr.invalidate()
                _time.sleep(5)
                continue

            # --- Update Gaussian ---
            htf_signal, htf_details = trackers[htf_tf].update(df_htf)

            # --- HTF crossover (bias change) ---
            if htf_signal:
                old_bias = htf_bias.get(htf_tf)
                htf_bias[htf_tf] = htf_signal
                price = float(df_htf['close'].iloc[-1])
                log(f"HTF {htf_tf} CROSSOVER: {htf_signal} (was {old_bias}) @ ${price:.1f}")
                alert_bias_change(htf_tf, htf_signal, price)

                # Bias flip closes position in HTF_BIAS_MOM mode
                if mode == 'HTF_BIAS_MOM' and current_position:
                    if htf_signal != current_position['side']:
                        log(f"HTF bias flip — closing {current_position['side']}")
                        cancel_all_orders()
                        close_position()

                        new_bal = get_balance()
                        pnl = (new_bal - balance) if new_bal else 0
                        if new_bal: balance = new_bal

                        alert_exit(current_position['side'], current_position['entry_price'],
                                   price, pnl, 'HTF_BIAS_FLIP', balance)
                        log_trade({
                            'timestamp': now_ist.strftime('%Y-%m-%d %H:%M:%S'),
                            'session': session_name, 'side': current_position['side'],
                            'entry_price': current_position['entry_price'],
                            'exit_price': price, 'size': current_position.get('size', 0),
                            'pnl': pnl, 'reason': 'HTF_BIAS_FLIP',
                            'capital_after': balance, 'mode': mode
                        })
                        daily_pnl += pnl
                        daily_trades += 1
                        if pnl >= 0: daily_wins += 1
                        else: daily_losses += 1
                        current_position = None

            # ---- HTF_BIAS_MOM mode (Asia) ----
            if mode == 'HTF_BIAS_MOM':
                bias = htf_bias.get(htf_tf)
                if bias and df_entry is not None:
                    entry_signal, entry_details = trackers[entry_tf].update(df_entry)

                    if entry_signal and entry_signal == bias:
                        if check_momentum(entry_details, config.MOMENTUM_THRESHOLD_PCT):
                            price = entry_details['price']
                            log(f"SIGNAL: {entry_signal} on {entry_tf} (bias {htf_tf}={bias}) @ ${price:.1f}")

                            if current_position and current_position['side'] == entry_signal and config.ALLOW_RE_ENTRY:
                                cancel_all_orders()
                                close_position()
                                new_bal = get_balance()
                                pnl = (new_bal - balance) if new_bal else 0
                                if new_bal: balance = new_bal
                                daily_pnl += pnl
                                daily_trades += 1
                                if pnl >= 0: daily_wins += 1
                                else: daily_losses += 1
                                current_position = None

                            if current_position is None:
                                result = open_position(entry_signal, balance, cap_pct, lev, price)
                                if result.get('success'):
                                    fill = result.get('price', price) or price
                                    current_position = {
                                        'side': entry_signal, 'entry_price': fill,
                                        'size': result.get('size', 0),
                                        'session': session_name,
                                        'entry_time': now_ist, 'mode': mode
                                    }
                                    alert_entry(entry_signal, fill, result.get('size', 0),
                                                session_name, mode, lev, balance)
                                    log(f"ENTRY: {entry_signal} @ ${fill:.1f}")
                                else:
                                    log(f"ORDER FAILED: {result.get('error')}", 'ERROR')
                                    alert_error(f"Order failed: {result.get('error')}")

            # ---- HTF_ONLY mode (London/NY) ----
            elif mode == 'HTF_ONLY' and htf_signal:
                price = htf_details['price']
                log(f"SIGNAL: {htf_signal} on {htf_tf} (HTF_ONLY) @ ${price:.1f}")

                if current_position and current_position['side'] != htf_signal:
                    cancel_all_orders()
                    result = flip_position(htf_signal, balance, cap_pct, lev, price)

                    new_bal = get_balance()
                    pnl = (new_bal - balance) if new_bal else 0
                    if new_bal: balance = new_bal

                    alert_exit(current_position['side'], current_position['entry_price'],
                               price, pnl, 'CROSSOVER_FLIP', balance)
                    log_trade({
                        'timestamp': now_ist.strftime('%Y-%m-%d %H:%M:%S'),
                        'session': session_name, 'side': current_position['side'],
                        'entry_price': current_position['entry_price'],
                        'exit_price': price, 'size': current_position.get('size', 0),
                        'pnl': pnl, 'reason': 'CROSSOVER_FLIP',
                        'capital_after': balance, 'mode': mode
                    })
                    daily_pnl += pnl
                    daily_trades += 1
                    if pnl >= 0: daily_wins += 1
                    else: daily_losses += 1

                    if result.get('success'):
                        fill = result.get('price', price) or price
                        current_position = {
                            'side': htf_signal, 'entry_price': fill,
                            'size': result.get('size', 0),
                            'session': session_name,
                            'entry_time': now_ist, 'mode': mode
                        }
                        alert_entry(htf_signal, fill, result.get('size', 0),
                                    session_name, mode, lev, balance)
                    else:
                        current_position = None
                        log(f"FLIP FAILED: {result.get('error')}", 'ERROR')

                elif current_position is None:
                    result = open_position(htf_signal, balance, cap_pct, lev, price)
                    if result.get('success'):
                        fill = result.get('price', price) or price
                        current_position = {
                            'side': htf_signal, 'entry_price': fill,
                            'size': result.get('size', 0),
                            'session': session_name,
                            'entry_time': now_ist, 'mode': mode
                        }
                        alert_entry(htf_signal, fill, result.get('size', 0),
                                    session_name, mode, lev, balance)
                        log(f"ENTRY: {htf_signal} @ ${fill:.1f}")
                    else:
                        log(f"ORDER FAILED: {result.get('error')}", 'ERROR')

            # --- Heartbeat ---
            if watchdog.should_heartbeat():
                pos = get_position()
                pos_str = f"{pos['side']} {pos['size']} BTC @ ${pos['entry_price']:.1f}" if pos else "None"
                status = watchdog.get_status()
                log(f"Health: {session_name} | Pos={pos_str} | Bal=${balance:.2f} | "
                    f"Trades={daily_trades} | PnL=${daily_pnl:.2f} | Up={status['uptime']}")

            if watchdog.should_telegram_heartbeat():
                pos = get_position()
                pos_str = f"{pos['side']} {pos['size']}BTC" if pos else "Flat"
                send_message(
                    f"💓 {session_name} | {pos_str} | ${balance:.2f}\n"
                    f"Today: {daily_trades}tr | ${daily_pnl:+.2f}"
                )

            # --- Sleep ---
            sleep_map = {'1m': 5, '3m': 10, '5m': 15, '15m': 30}
            _time.sleep(sleep_map.get(entry_tf, 10))

        except KeyboardInterrupt:
            log("Stopped by user")
            if current_position:
                log("Closing position...")
                cancel_all_orders()
                close_position()
            break

        except Exception as e:
            log(f"ERROR: {e}", 'ERROR')
            watchdog.record_failure(str(e))
            alert_error(str(e))
            _time.sleep(30)


if __name__ == '__main__':
    main()