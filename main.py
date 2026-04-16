"""
VGB Delta Bot v2.2 — Main Loop
=================================
Updates:
- London M5, NY M3 (faster crossovers)
- Asia momentum 0.04%
- Breakeven protection at +0.3%
- Candle close confirmation
- Fixed false deposit alerts
- All USD
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
    get_ticker_price, get_server_time, place_stop_market
)
from telegram_alerts import (
    alert_entry, alert_exit, alert_bias_change, alert_session_change,
    alert_error, alert_startup, alert_daily_summary, alert_breakeven, send_message
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


def check_breakeven(position, current_price):
    """
    Check if trade has reached breakeven trigger level.
    Returns True if SL should be moved to breakeven.
    """
    if not config.BREAKEVEN_ENABLED:
        return False

    if position.get('breakeven_set'):
        return False  # already set

    entry = position['entry_price']
    trigger = config.BREAKEVEN_TRIGGER_PCT / 100

    if position['side'] == 'BUY':
        profit_pct = (current_price - entry) / entry
        return profit_pct >= trigger
    else:
        profit_pct = (entry - current_price) / entry
        return profit_pct >= trigger


def set_breakeven_sl(position):
    """Move SL to breakeven (entry price + small buffer for fees)."""
    entry = position['entry_price']
    offset = config.BREAKEVEN_OFFSET_PCT / 100

    if position['side'] == 'BUY':
        be_price = entry * (1 + offset)  # slightly above entry
        sl_side = 'SELL'
    else:
        be_price = entry * (1 - offset)  # slightly below entry
        sl_side = 'BUY'

    # Cancel existing SL orders first
    cancel_all_orders()
    _time.sleep(0.3)

    # Place new SL at breakeven
    result = place_stop_market(sl_side, position['size'], be_price)
    if result.get('success'):
        log(f"BREAKEVEN SET: SL moved to ${be_price:.1f} (entry was ${entry:.1f})")
        alert_breakeven(position['side'], entry, be_price)
        return True
    else:
        log(f"BREAKEVEN FAILED: {result.get('error')}", 'WARNING')
        return False


def close_and_record(position, exit_price, reason, session_name, balance, now_ist, balance_monitor):
    """Close position and record the trade. Returns (pnl, new_balance)."""
    cancel_all_orders()
    close_position()

    # Mark position closed so balance monitor doesn't flag as deposit
    if balance_monitor:
        balance_monitor.mark_position_closed()

    _time.sleep(0.5)
    new_bal = get_balance()
    pnl = (new_bal - balance) if new_bal else 0
    if new_bal:
        balance = new_bal

    alert_exit(position['side'], position['entry_price'], exit_price, pnl, reason, balance)
    log_trade({
        'timestamp': now_ist.strftime('%Y-%m-%d %H:%M:%S'),
        'session': session_name, 'side': position['side'],
        'entry_price': position['entry_price'], 'exit_price': exit_price,
        'size': position.get('size', 0), 'pnl': pnl, 'reason': reason,
        'capital_after': balance, 'mode': position.get('mode', '')
    })

    return pnl, balance


def main():
    log("=" * 60)
    log("VGB DELTA BOT v2.2 — STARTING")
    log(f"Mode: {'TESTNET' if config.USE_TESTNET else '*** LIVE ***'}")
    log(f"Symbol: {config.SYMBOL}")
    log(f"TFs: Asia {config.SESSIONS['ASIA']['htf_timeframe']}→{config.SESSIONS['ASIA']['entry_timeframe']} | "
        f"London {config.SESSIONS['LONDON']['htf_timeframe']} | NY {config.SESSIONS['NY']['htf_timeframe']}")
    log(f"Breakeven: {'ON at +' + str(config.BREAKEVEN_TRIGGER_PCT) + '%' if config.BREAKEVEN_ENABLED else 'OFF'}")
    log("=" * 60)

    # Initialize
    candle_mgr = CandleManager()
    watchdog = Watchdog()

    trackers = {
        '1m': GaussianTracker('1m', config.GAUSSIAN_LENGTH, config.GAUSSIAN_DISTANCE, config.GAUSSIAN_MODE),
        '3m': GaussianTracker('3m', config.GAUSSIAN_LENGTH, config.GAUSSIAN_DISTANCE, config.GAUSSIAN_MODE),
        '5m': GaussianTracker('5m', config.GAUSSIAN_LENGTH, config.GAUSSIAN_DISTANCE, config.GAUSSIAN_MODE),
        '15m': GaussianTracker('15m', config.GAUSSIAN_LENGTH, config.GAUSSIAN_DISTANCE, config.GAUSSIAN_MODE),
    }

    # Track last candle time per TF to only act on NEW closed candles
    last_candle_time = {}

    # State
    prev_session = None
    current_position = None
    htf_bias = {}
    daily_trades = 0
    daily_pnl = 0.0
    daily_wins = 0
    daily_losses = 0
    last_daily_reset = get_ist_now().date()
    last_weekly_report = get_ist_now().isocalendar()[1]
    balance_monitor = None

    # Connect
    server_time = get_server_time()
    if server_time is None:
        log("CRITICAL: Cannot connect to Binance.", 'ERROR')
        alert_error("Cannot connect to Binance.")
        return

    log(f"Binance connected. Server time: {server_time}")
    set_margin_type('CROSSED')
    set_leverage(config.DEFAULT_LEVERAGE)

    balance = get_balance()
    if balance is None:
        log("CRITICAL: Cannot get balance.", 'ERROR')
        alert_error("Cannot get balance. Check API keys.")
        return

    log(f"Balance: ${balance:.2f} USDT")
    balance_monitor = BalanceMonitor(balance)

    # Recover position
    recovered_pos = recover_position_state()
    if recovered_pos:
        current_position = {
            'side': recovered_pos['side'], 'entry_price': recovered_pos['entry_price'],
            'size': recovered_pos['size'], 'session': 'RECOVERED',
            'entry_time': get_ist_now(), 'mode': 'recovered', 'breakeven_set': False
        }
        log(f"Recovered position: {recovered_pos['side']} {recovered_pos['size']} BTC")

    alert_startup(balance)

    # ============================================================
    # MAIN LOOP
    # ============================================================
    while True:
        try:
            now_ist = get_ist_now()

            # --- KILL SWITCH ---
            if check_kill_switch():
                log("KILL SWITCH ACTIVATED", 'CRITICAL')
                execute_emergency_shutdown()

            # --- PAUSE ---
            if check_pause_switch():
                if watchdog.should_heartbeat():
                    log("PAUSED")
                _time.sleep(30)
                continue

            # --- Weekly report (Sunday) ---
            current_week = now_ist.isocalendar()[1]
            if current_week != last_weekly_report and now_ist.weekday() == 6:
                report, formatted = generate_weekly_report()
                send_message(formatted)
                health = generate_health_report()
                send_message(health)
                last_weekly_report = current_week

            # --- Balance monitor ---
            if balance_monitor and not current_position:
                fresh = get_balance()
                if fresh is not None:
                    _, evt, amt = balance_monitor.update(fresh, current_position is not None)
                    if evt == 'WITHDRAWAL':
                        log(f"WITHDRAWAL: ${amt:.2f}")
                        send_message(f"💸 Withdrawal: ${amt:.2f}\nBalance: ${fresh:.2f}")
                        balance = fresh
                    elif evt == 'DEPOSIT':
                        log(f"DEPOSIT: +${amt:.2f}")
                        send_message(f"💰 Deposit: +${amt:.2f}\nBalance: ${fresh:.2f}")
                        balance = fresh

                    safe, reason = balance_monitor.is_safe_to_trade(fresh)
                    if not safe:
                        log(f"UNSAFE: {reason}", 'WARNING')
                        send_message(f"⚠️ {reason}")
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

            # --- Daily restart ---
            if watchdog.should_daily_restart():
                if current_position:
                    cancel_all_orders()
                    close_position()
                    if balance_monitor: balance_monitor.mark_position_closed()
                    current_position = None
                send_message("🔄 Daily restart")
                perform_daily_restart()

            # --- Safe mode ---
            wait = watchdog.should_wait()
            if wait > 0:
                log(f"Safe mode: waiting {wait}s")
                _time.sleep(wait)
                watchdog.record_success()
                continue

            # --- Session detection ---
            session_name, session_config, session_end = get_current_session(now_ist)

            # --- Session transition ---
            if is_session_transition(prev_session, session_name):
                if prev_session and session_name != prev_session:
                    log(f"Session ending: {prev_session}")
                    alert_session_change(prev_session, 'CLOSE')

                    if current_position and config.CLOSE_AT_SESSION_END:
                        exit_price = get_ticker_price() or current_position['entry_price']
                        pnl, balance = close_and_record(
                            current_position, exit_price, 'SESSION_END',
                            prev_session, balance, now_ist, balance_monitor
                        )
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
                    if balance_monitor:
                        balance_monitor.set_session_start(balance)

                prev_session = session_name

            # --- No session ---
            if session_name is None:
                if watchdog.should_heartbeat():
                    log(format_session_status(None, None, None))
                if watchdog.should_telegram_heartbeat():
                    send_message(f"💤 Idle | ${balance:.2f} | Up: {watchdog.get_status()['uptime']}")
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

            # --- News filter ---
            if config.NEWS_FILTER_ENABLED:
                blocked, reason = news_filter.is_blocked()
                if blocked:
                    if watchdog.should_heartbeat():
                        log(f"NEWS BLOCK: {reason}")
                    _time.sleep(30)
                    continue

            # --- Fetch candles ---
            df_htf = candle_mgr.get_candles(htf_tf)
            df_entry = candle_mgr.get_candles(entry_tf) if entry_tf != htf_tf else df_htf

            if df_htf is None:
                watchdog.record_failure(f"No {htf_tf} data")
                _time.sleep(10)
                continue

            watchdog.record_success()

            # --- Stale data check ---
            if watchdog.is_data_stale():
                log("WARNING: Data may be stale", 'WARNING')
                candle_mgr.invalidate()
                _time.sleep(5)
                continue

            # --- CANDLE CLOSE CONFIRMATION ---
            # Only process crossover if we have a NEW closed candle
            htf_latest_time = df_htf.index[-1] if len(df_htf) > 0 else None
            is_new_htf_candle = False
            if htf_latest_time:
                prev_time = last_candle_time.get(htf_tf)
                if prev_time is None or htf_latest_time > prev_time:
                    last_candle_time[htf_tf] = htf_latest_time
                    is_new_htf_candle = True

            # --- Breakeven check on open position ---
            if current_position and config.BREAKEVEN_ENABLED and not current_position.get('breakeven_set'):
                current_price = get_ticker_price()
                if current_price and check_breakeven(current_position, current_price):
                    success = set_breakeven_sl(current_position)
                    if success:
                        current_position['breakeven_set'] = True

            # --- Update Gaussian (only on new candle) ---
            htf_signal = None
            htf_details = None
            if is_new_htf_candle:
                htf_signal, htf_details = trackers[htf_tf].update(df_htf)

            # --- HTF crossover ---
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
                        pnl, balance = close_and_record(
                            current_position, price, 'HTF_BIAS_FLIP',
                            session_name, balance, now_ist, balance_monitor
                        )
                        daily_pnl += pnl
                        daily_trades += 1
                        if pnl >= 0: daily_wins += 1
                        else: daily_losses += 1
                        current_position = None

            # ---- HTF_BIAS_MOM mode (Asia) ----
            if mode == 'HTF_BIAS_MOM':
                bias = htf_bias.get(htf_tf)
                if bias and df_entry is not None:
                    # Check M1 candle close confirmation
                    m1_latest = df_entry.index[-1] if len(df_entry) > 0 else None
                    is_new_m1 = False
                    if m1_latest:
                        prev_m1 = last_candle_time.get(entry_tf)
                        if prev_m1 is None or m1_latest > prev_m1:
                            last_candle_time[entry_tf] = m1_latest
                            is_new_m1 = True

                    if is_new_m1:
                        entry_signal, entry_details = trackers[entry_tf].update(df_entry)

                        if entry_signal and entry_signal == bias:
                            if check_momentum(entry_details, config.MOMENTUM_THRESHOLD_PCT):
                                price = entry_details['price']
                                log(f"SIGNAL: {entry_signal} on {entry_tf} (bias {htf_tf}={bias}) @ ${price:.1f}")

                                if current_position and current_position['side'] == entry_signal and config.ALLOW_RE_ENTRY:
                                    pnl, balance = close_and_record(
                                        current_position, price, 'M1_REENTRY',
                                        session_name, balance, now_ist, balance_monitor
                                    )
                                    daily_pnl += pnl
                                    daily_trades += 1
                                    if pnl >= 0: daily_wins += 1
                                    else: daily_losses += 1
                                    current_position = None

                                if current_position is None and balance > 0:
                                    result = open_position(entry_signal, balance, cap_pct, lev, price)
                                    if result.get('success'):
                                        fill = result.get('price', price) or price
                                        current_position = {
                                            'side': entry_signal, 'entry_price': fill,
                                            'size': result.get('size', 0),
                                            'session': session_name, 'entry_time': now_ist,
                                            'mode': mode, 'breakeven_set': False
                                        }
                                        alert_entry(entry_signal, fill, result.get('size', 0),
                                                    session_name, mode, lev, balance)
                                        log(f"ENTRY: {entry_signal} @ ${fill:.1f}")
                                    else:
                                        log(f"ORDER FAILED: {result.get('error')}", 'ERROR')

            # ---- HTF_ONLY mode (London/NY) ----
            elif mode == 'HTF_ONLY' and htf_signal and is_new_htf_candle:
                price = htf_details['price']
                log(f"SIGNAL: {htf_signal} on {htf_tf} (HTF_ONLY) @ ${price:.1f}")

                if current_position and current_position['side'] != htf_signal:
                    # Flip
                    pnl, balance = close_and_record(
                        current_position, price, 'CROSSOVER_FLIP',
                        session_name, balance, now_ist, balance_monitor
                    )
                    daily_pnl += pnl
                    daily_trades += 1
                    if pnl >= 0: daily_wins += 1
                    else: daily_losses += 1

                    _time.sleep(0.5)
                    result = open_position(htf_signal, balance, cap_pct, lev, price)
                    if result.get('success'):
                        fill = result.get('price', price) or price
                        current_position = {
                            'side': htf_signal, 'entry_price': fill,
                            'size': result.get('size', 0),
                            'session': session_name, 'entry_time': now_ist,
                            'mode': mode, 'breakeven_set': False
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
                            'session': session_name, 'entry_time': now_ist,
                            'mode': mode, 'breakeven_set': False
                        }
                        alert_entry(htf_signal, fill, result.get('size', 0),
                                    session_name, mode, lev, balance)
                        log(f"ENTRY: {htf_signal} @ ${fill:.1f}")
                    else:
                        log(f"ORDER FAILED: {result.get('error')}", 'ERROR')

            # --- Heartbeat ---
            if watchdog.should_heartbeat():
                pos = get_position()
                pos_str = f"{pos['side']} {pos['size']}BTC @ ${pos['entry_price']:.1f}" if pos else "Flat"
                status = watchdog.get_status()
                be_str = " [BE]" if current_position and current_position.get('breakeven_set') else ""
                log(f"Health: {session_name} | {pos_str}{be_str} | ${balance:.2f} | "
                    f"T:{daily_trades} PnL:${daily_pnl:+.2f} | {status['uptime']}")

            if watchdog.should_telegram_heartbeat():
                pos = get_position()
                pos_str = f"{pos['side']} {pos['size']}BTC" if pos else "Flat"
                be_str = " 🔒" if current_position and current_position.get('breakeven_set') else ""
                send_message(
                    f"💓 {session_name} | {pos_str}{be_str} | ${balance:.2f}\n"
                    f"Today: {daily_trades}tr | ${daily_pnl:+.2f}"
                )

            # --- Sleep ---
            sleep_map = {'1m': 5, '3m': 10, '5m': 15, '15m': 30}
            _time.sleep(sleep_map.get(entry_tf, 10))

        except KeyboardInterrupt:
            log("Stopped by user")
            if current_position:
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