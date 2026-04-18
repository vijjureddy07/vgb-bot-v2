"""
VGB Bot v3.0 — Main Loop
========================
NY session ONLY | M3 crossover flip | OLD Gaussian | 35x / 25% | Safety SL 3% |
No breakeven | No momentum | No Asia | No London.

Drop-in replacement for v2 main.py. Uses the existing executor.py, data_feed.py,
watchdog.py, kill_switch.py, balance_monitor.py, weekly_report.py, news_filter.py
with NO changes to those files.

Loop (inside NY session):
  - Wait until next M3 candle close + 5s settling delay.
  - Fetch M3 candles via CandleManager.
  - GaussianTracker.update(df) -> signal.
  - If signal and flat -> open_position(signal).
  - If signal and opposite position -> flip_position(signal).
  - If signal matches current position -> do nothing.
At session end (01:00 Mon-Thu / 23:30 Fri), or at next M3 boundary after:
  - Close any open position, cancel all orders, send Telegram summary.
"""

from __future__ import annotations

import csv
import os
import sys
import time as _time
import traceback
from datetime import timedelta

import config
from data_feed import CandleManager
from gaussian_engine import GaussianTracker
from session_manager import (
    get_ist_now,
    get_current_session,
    is_session_transition,
    format_session_status,
    next_m3_boundary_at_or_after,
    seconds_until_next_session,
)
from executor import (
    get_balance,
    get_position,
    open_position,
    close_position,
    flip_position,
    cancel_all_orders,
    set_leverage,
    set_margin_type,
    get_ticker_price,
    get_server_time,
)
from telegram_alerts import (
    alert_startup,
    alert_entry,
    alert_exit,
    alert_session_change,
    alert_error,
    alert_daily_summary,
    alert_session_end_summary,
    alert_warning,
    alert_info,
    alert_balance_floor_breach,
    send_message,
)
from watchdog import Watchdog, recover_position_state, perform_daily_restart
from kill_switch import check_kill_switch, check_pause_switch, execute_emergency_shutdown
from balance_monitor import BalanceMonitor
from weekly_report import generate_weekly_report, generate_health_report


# ============================================================
# Logging
# ============================================================
def log(msg: str, level: str = "INFO"):
    ts = get_ist_now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    try:
        with open(config.LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def log_trade(data: dict):
    if not config.LOG_TRADES_TO_CSV:
        return
    exists = os.path.exists(config.TRADE_LOG_FILE)
    keys = ["timestamp", "session", "side", "entry_price", "exit_price",
            "size", "pnl", "reason", "capital_after", "mode"]
    try:
        with open(config.TRADE_LOG_FILE, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            if not exists:
                w.writeheader()
            w.writerow(data)
    except Exception as e:
        log(f"trade log write failed: {e}", "WARNING")


# ============================================================
# Close + record helper
# ============================================================
def close_and_record(position: dict, exit_price: float, reason: str,
                     session_name: str, balance: float, now_ist,
                     balance_monitor: BalanceMonitor):
    """
    Cancel orders, market-close position, refresh balance, fire Telegram exit,
    append trade log row. Returns (pnl, new_balance).
    """
    cancel_all_orders()
    close_position()
    if balance_monitor:
        balance_monitor.mark_position_closed()
    _time.sleep(0.5)

    new_bal = get_balance()
    if new_bal is None:
        new_bal = balance
    pnl = new_bal - balance

    alert_exit(
        position["side"], position["entry_price"], exit_price,
        pnl, reason, new_bal,
    )
    log_trade({
        "timestamp":     now_ist.strftime("%Y-%m-%d %H:%M:%S"),
        "session":       session_name,
        "side":          position["side"],
        "entry_price":   position["entry_price"],
        "exit_price":    exit_price,
        "size":          position.get("size", 0),
        "pnl":           pnl,
        "reason":        reason,
        "capital_after": new_bal,
        "mode":          position.get("mode", "NY_M3_FLIP"),
    })
    return pnl, new_bal


# ============================================================
# Wait helpers
# ============================================================
def _sleep_until(target_dt, guard_sec: int = 10):
    """Sleep until target_dt IST, checking kill/pause periodically."""
    while True:
        now = get_ist_now()
        remaining = (target_dt - now).total_seconds()
        if remaining <= 0:
            return
        if check_kill_switch():
            return
        _time.sleep(min(remaining, guard_sec))


def _wait_for_next_m3_settled():
    """Sleep to (next M3 boundary) + POST_CLOSE_DELAY_SEC."""
    now = get_ist_now()
    target = next_m3_boundary_at_or_after(now + timedelta(seconds=1))
    target = target + timedelta(seconds=config.POST_CLOSE_DELAY_SEC)
    _sleep_until(target)


# ============================================================
# Clock drift
# ============================================================
def _check_clock_drift() -> bool:
    server_ms = get_server_time()
    if server_ms is None:
        return True  # don't block if we can't check — watchdog will catch it
    local_ms = int(_time.time() * 1000)
    drift = abs(server_ms - local_ms)
    if drift > config.MAX_CLOCK_DRIFT_MS:
        log(f"Clock drift {drift} ms exceeds {config.MAX_CLOCK_DRIFT_MS} ms", "ERROR")
        alert_warning(f"Clock drift {drift} ms — skipping bar")
        return False
    return True


# ============================================================
# Main
# ============================================================
def main():
    log("=" * 60)
    log(f"{config.BOT_NAME} v{config.BOT_VERSION} — STARTING")
    log(f"Mode: {'TESTNET' if config.USE_TESTNET else '*** MAINNET / REAL MONEY ***'}")
    log(f"Symbol: {config.SYMBOL}   Lev: {config.DEFAULT_LEVERAGE}x   "
        f"Alloc: {int(config.DEFAULT_CAPITAL_PCT*100)}%   Safety SL: {config.SAFETY_SL_PCT}%")
    log("Engine: OLD Gaussian | NY only | M3 flip | No BE | No momentum")
    log("=" * 60)

    # --- Init components ---
    candle_mgr = CandleManager()
    watchdog   = Watchdog()
    tracker    = GaussianTracker(
        "3m", config.GAUSSIAN_LENGTH, config.GAUSSIAN_DISTANCE, "OLD"
    )

    # --- State ---
    prev_session        = None
    current_position    = None
    daily_trades        = 0
    daily_pnl           = 0.0
    daily_wins          = 0
    daily_losses        = 0
    session_trades      = 0
    session_start_bal   = None
    session_id          = None
    last_daily_reset    = get_ist_now().date()
    last_weekly_report  = get_ist_now().isocalendar()[1]
    last_bar_time_seen  = None     # to detect new M3 candle arrival
    balance_monitor     = None
    session_ending      = False    # set True once past session end time

    # --- Connect ---
    if get_server_time() is None:
        log("CRITICAL: cannot reach Binance", "ERROR")
        alert_error("Cannot reach Binance at startup.")
        return

    set_margin_type("CROSSED")
    set_leverage(config.DEFAULT_LEVERAGE)

    balance = get_balance()
    if balance is None:
        log("CRITICAL: cannot fetch balance (bad keys?)", "ERROR")
        alert_error("Cannot fetch balance — check API keys & IP whitelist.")
        return
    log(f"Balance: {balance:.2f} USDT")

    balance_monitor = BalanceMonitor(balance)

    # --- Recover open position from prior process ---
    recovered = recover_position_state()
    if recovered:
        current_position = {
            "side":         recovered["side"],
            "entry_price":  recovered["entry_price"],
            "size":         recovered["size"],
            "session":      "RECOVERED",
            "entry_time":   get_ist_now(),
            "mode":         "NY_M3_FLIP",
            "breakeven_set": False,
        }
        log(f"Recovered {recovered['side']} {recovered['size']} BTC")

    alert_startup(balance)

    # ============================================================
    # MAIN LOOP
    # ============================================================
    while True:
        try:
            now_ist = get_ist_now()

            # --- Kill switch ---
            if check_kill_switch():
                log("KILL SWITCH", "CRITICAL")
                execute_emergency_shutdown()
                return

            # --- Pause ---
            if check_pause_switch():
                if watchdog.should_heartbeat():
                    log("PAUSED")
                _time.sleep(30)
                continue

            # --- Weekly report (Sunday) ---
            wk = now_ist.isocalendar()[1]
            if wk != last_weekly_report and now_ist.weekday() == 6:
                try:
                    _, formatted = generate_weekly_report()
                    send_message(formatted)
                    send_message(generate_health_report())
                except Exception as e:
                    log(f"weekly report failed: {e}", "WARNING")
                last_weekly_report = wk

            # --- Daily reset ---
            if now_ist.date() != last_daily_reset:
                if daily_trades > 0:
                    alert_daily_summary(daily_trades, daily_pnl, balance,
                                        daily_wins, daily_losses)
                daily_trades = 0
                daily_pnl    = 0.0
                daily_wins   = 0
                daily_losses = 0
                last_daily_reset = now_ist.date()
                log(f"--- New day: {now_ist.date()} ---")

            # --- Daily restart ---
            if watchdog.should_daily_restart():
                if current_position:
                    cancel_all_orders()
                    close_position()
                    if balance_monitor:
                        balance_monitor.mark_position_closed()
                    current_position = None
                send_message("🔄 Daily restart")
                perform_daily_restart()

            # --- Safe mode (watchdog backoff) ---
            wait = watchdog.should_wait()
            if wait > 0:
                log(f"Safe mode: waiting {wait}s")
                _time.sleep(wait)
                watchdog.record_success()
                continue

            # --- Session detection ---
            session_name, session_cfg, session_end = get_current_session(now_ist)

            # --- Session transitions ---
            if is_session_transition(prev_session, session_name):
                # Session ending?
                if prev_session and session_name != prev_session:
                    log(f"Session ending: {prev_session}")
                    alert_session_change(prev_session, "CLOSE")
                    if current_position and config.CLOSE_AT_SESSION_END:
                        exit_px = get_ticker_price() or current_position["entry_price"]
                        pnl, balance = close_and_record(
                            current_position, exit_px, "SESSION_END",
                            prev_session, balance, now_ist, balance_monitor,
                        )
                        daily_pnl += pnl
                        daily_trades += 1
                        if pnl >= 0: daily_wins += 1
                        else:        daily_losses += 1
                        session_trades += 1
                        current_position = None
                    # Send session-end summary if we were actively trading.
                    if session_start_bal is not None:
                        alert_session_end_summary(
                            balance - session_start_bal,
                            session_trades, balance,
                            reason="scheduled end",
                        )
                    # Reset session state
                    session_trades    = 0
                    session_start_bal = None
                    session_id        = None
                    tracker.reset()
                    last_bar_time_seen = None

                # Session starting?
                if session_name and session_name != prev_session:
                    log(f"Session starting: {session_name}")
                    log(format_session_status(session_name, session_cfg, session_end))
                    alert_session_change(session_name, "OPEN")
                    set_leverage(session_cfg.get("leverage", config.DEFAULT_LEVERAGE))
                    fresh_bal = get_balance()
                    if fresh_bal is not None:
                        balance = fresh_bal
                    session_start_bal = balance
                    session_trades    = 0
                    session_id        = now_ist.strftime("%Y-%m-%d-%a")
                    tracker.reset()
                    last_bar_time_seen = None
                    if balance_monitor:
                        balance_monitor.set_session_start(balance)

                prev_session = session_name

            # --- Outside any session: idle ---
            if session_name is None:
                # Defensive: ensure flat outside session.
                pos_check = get_position()
                if pos_check is not None:
                    log("Position open outside session — closing.", "WARNING")
                    cancel_all_orders()
                    close_position()
                    if balance_monitor:
                        balance_monitor.mark_position_closed()
                    current_position = None

                if watchdog.should_heartbeat():
                    log(format_session_status(None, None, None))
                if watchdog.should_telegram_heartbeat():
                    send_message(
                        f"💤 Idle | {config.SYMBOL} | Balance ${balance:.2f} | "
                        f"Up: {watchdog.get_status()['uptime']}"
                    )

                # Sleep until next session start, but wake periodically.
                secs = seconds_until_next_session(now_ist)
                _time.sleep(min(secs, 60))
                continue

            # ============================================================
            # ACTIVE NY SESSION
            # ============================================================

            # --- Balance monitor (only when flat) ---
            if balance_monitor and current_position is None:
                fresh = get_balance()
                if fresh is not None:
                    _, evt, amt = balance_monitor.update(fresh, False)
                    if evt == "WITHDRAWAL":
                        log(f"WITHDRAWAL: ${amt:.2f}")
                        send_message(f"💸 Withdrawal: ${amt:.2f}\nBalance: ${fresh:.2f}")
                        balance = fresh
                    elif evt == "DEPOSIT":
                        log(f"DEPOSIT: +${amt:.2f}")
                        send_message(f"💰 Deposit: +${amt:.2f}\nBalance: ${fresh:.2f}")
                        balance = fresh
                    safe, reason = balance_monitor.is_safe_to_trade(fresh)
                    if not safe:
                        log(f"UNSAFE: {reason}", "WARNING")
                        send_message(f"⚠️ {reason}")
                        _time.sleep(60)
                        continue

            # --- Auto-pause on mainnet balance floor breach ---
            if (not config.USE_TESTNET and
                balance is not None and
                balance < config.AUTO_PAUSE_BALANCE_FLOOR_USD and
                not os.path.exists(config.PAUSE_FILE)):
                log(f"Balance {balance:.2f} below floor — auto-pausing", "ERROR")
                alert_balance_floor_breach(balance, config.AUTO_PAUSE_BALANCE_FLOOR_USD)
                try:
                    open(config.PAUSE_FILE, "w").close()
                except Exception:
                    pass
                continue

            # --- Wait for next M3 candle close + settling delay ---
            _wait_for_next_m3_settled()

            if check_kill_switch():
                continue

            now_ist = get_ist_now()

            # If the session has ended during our sleep, let the transition
            # logic at the top of the loop handle the close.
            still_in = get_current_session(now_ist)[0]
            if still_in != "NY":
                continue

            # --- Clock drift guard ---
            if not _check_clock_drift():
                _time.sleep(10)
                continue

            # --- Fetch candles ---
            df = candle_mgr.get_candles("3m")
            if df is None or len(df) == 0:
                watchdog.record_failure("no 3m data")
                _time.sleep(10)
                continue
            watchdog.record_success()

            if watchdog.is_data_stale():
                log("Data stale — invalidating cache", "WARNING")
                candle_mgr.invalidate()
                _time.sleep(5)
                continue

            # --- New bar? ---
            latest_bar_time = df.index[-1]
            if last_bar_time_seen is not None and latest_bar_time <= last_bar_time_seen:
                # Same bar we already processed; nothing to do.
                continue
            last_bar_time_seen = latest_bar_time

            # --- Signal ---
            signal, details = tracker.update(df)
            if details is None:
                log(f"Insufficient candles for bands ({len(df)})")
                continue

            price = details["price"]

            if signal is None:
                if watchdog.should_heartbeat():
                    pos_str = "Flat"
                    if current_position:
                        pos_str = f"{current_position['side']} {current_position['size']}BTC"
                    log(f"Bar {latest_bar_time.strftime('%H:%M')} | close={price:.1f} "
                        f"U={details['upper']:.1f} L={details['lower']:.1f} | {pos_str} "
                        f"| Bal ${balance:.2f} | T:{daily_trades} PnL:${daily_pnl:+.2f}")
                continue

            # --- We have a fresh crossover ---
            log(f"SIGNAL {signal} @ {price:.1f} "
                f"(U={details['upper']:.1f} L={details['lower']:.1f})")

            if current_position is None:
                # Fresh entry.
                result = open_position(
                    signal, balance,
                    config.DEFAULT_CAPITAL_PCT, config.DEFAULT_LEVERAGE,
                    price,
                )
                if result and result.get("success"):
                    fill = result.get("price", price) or price
                    current_position = {
                        "side":          signal,
                        "entry_price":   fill,
                        "size":          result.get("size", 0),
                        "session":       session_name,
                        "entry_time":    now_ist,
                        "mode":          "NY_M3_FLIP",
                        "breakeven_set": False,
                    }
                    alert_entry(signal, fill, result.get("size", 0),
                                session_name, "NY_M3_FLIP",
                                config.DEFAULT_LEVERAGE, balance)
                    log(f"ENTRY {signal} @ {fill:.1f}")
                else:
                    err = (result or {}).get("error", "unknown")
                    log(f"ENTRY FAILED: {err}", "ERROR")
                    alert_error(f"Entry failed: {err}")

            elif current_position["side"] == signal:
                # Already aligned — no action (per handoff Section 5.2c).
                log(f"Signal {signal} matches current position — no action")

            else:
                # FLIP: close current, open opposite. We record the close
                # ourselves (for PnL attribution) then let flip_position open.
                old_side = current_position["side"]

                # Close & record the exit first so the trade log is clean.
                pnl, balance = close_and_record(
                    current_position, price, "CROSSOVER_FLIP",
                    session_name, balance, now_ist, balance_monitor,
                )
                daily_pnl += pnl
                daily_trades += 1
                session_trades += 1
                if pnl >= 0: daily_wins += 1
                else:        daily_losses += 1

                _time.sleep(0.5)

                result = open_position(
                    signal, balance,
                    config.DEFAULT_CAPITAL_PCT, config.DEFAULT_LEVERAGE,
                    price,
                )
                if result and result.get("success"):
                    fill = result.get("price", price) or price
                    current_position = {
                        "side":          signal,
                        "entry_price":   fill,
                        "size":          result.get("size", 0),
                        "session":       session_name,
                        "entry_time":    now_ist,
                        "mode":          "NY_M3_FLIP",
                        "breakeven_set": False,
                    }
                    alert_entry(signal, fill, result.get("size", 0),
                                session_name, "NY_M3_FLIP",
                                config.DEFAULT_LEVERAGE, balance)
                    log(f"FLIP {old_side} -> {signal} @ {fill:.1f}")
                else:
                    current_position = None
                    err = (result or {}).get("error", "unknown")
                    log(f"FLIP OPEN FAILED: {err}", "ERROR")
                    alert_error(f"Flip open failed: {err}")

            # --- Telegram heartbeat ---
            if watchdog.should_telegram_heartbeat():
                pos_str = "Flat"
                if current_position:
                    pos_str = f"{current_position['side']} {current_position['size']}BTC"
                send_message(
                    f"💓 NY | {pos_str} | ${balance:.2f}\n"
                    f"Today: {daily_trades}tr | ${daily_pnl:+.2f}"
                )

        except KeyboardInterrupt:
            log("Stopped by user")
            if current_position:
                cancel_all_orders()
                close_position()
            break
        except Exception as e:
            log(f"ERROR in main loop: {e}", "ERROR")
            log(traceback.format_exc(), "ERROR")
            watchdog.record_failure(str(e))
            alert_error(f"{e}\n{traceback.format_exc()[:1500]}")
            _time.sleep(30)


if __name__ == "__main__":
    main()