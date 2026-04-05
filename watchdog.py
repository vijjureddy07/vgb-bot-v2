"""
VGB Delta Bot v2 — Self-Repair Watchdog
==========================================
Handles:
- API failure detection & safe mode
- Position state recovery on restart
- Stale candle data detection
- Daily scheduled restart
- Heartbeat monitoring
"""

import time as _time
import os
import sys
from datetime import datetime, timedelta
import config
from session_manager import get_ist_now


class Watchdog:
    def __init__(self):
        self.consecutive_failures = 0
        self.safe_mode = False
        self.safe_mode_retries = 0
        self.last_heartbeat = _time.time()
        self.last_telegram_heartbeat = _time.time()
        self.last_successful_candle = _time.time()
        self.startup_time = _time.time()
        self.errors_today = 0
        self.last_error_reset = get_ist_now().date()

    def record_success(self):
        """Record a successful API call."""
        self.consecutive_failures = 0
        self.last_successful_candle = _time.time()
        if self.safe_mode:
            self.safe_mode = False
            self.safe_mode_retries = 0
            print("[WATCHDOG] Recovered from safe mode")

    def record_failure(self, error_msg=""):
        """Record an API failure."""
        self.consecutive_failures += 1
        self.errors_today += 1
        print(f"[WATCHDOG] Failure #{self.consecutive_failures}: {error_msg}")

        if self.consecutive_failures >= config.MAX_CONSECUTIVE_FAILURES:
            self._enter_safe_mode()

    def _enter_safe_mode(self):
        """Enter safe mode — close positions, stop trading."""
        if self.safe_mode:
            self.safe_mode_retries += 1
            if self.safe_mode_retries >= config.SAFE_MODE_MAX_RETRIES:
                print("[WATCHDOG] CRITICAL: Max safe mode retries. Alerting and stopping.")
                return 'STOP'
            print(f"[WATCHDOG] Safe mode retry {self.safe_mode_retries}/{config.SAFE_MODE_MAX_RETRIES}")
            return 'WAIT'

        self.safe_mode = True
        self.safe_mode_retries = 1
        print(f"[WATCHDOG] ENTERING SAFE MODE after {self.consecutive_failures} failures")
        print(f"[WATCHDOG] Waiting {config.SAFE_MODE_WAIT_SECONDS}s before retry")
        return 'SAFE'

    def is_safe_mode(self):
        return self.safe_mode

    def should_wait(self):
        """Returns seconds to wait if in safe mode, 0 otherwise."""
        if self.safe_mode:
            return config.SAFE_MODE_WAIT_SECONDS
        return 0

    def is_data_stale(self):
        """Check if candle data is too old."""
        age = _time.time() - self.last_successful_candle
        return age > config.MAX_CANDLE_AGE_SECONDS

    def should_daily_restart(self):
        """Check if it's time for daily clean restart."""
        if not config.DAILY_RESTART_ENABLED:
            return False
        now = get_ist_now()
        if (now.hour == config.DAILY_RESTART_HOUR and
            now.minute == config.DAILY_RESTART_MINUTE and
            _time.time() - self.startup_time > 3600):  # don't restart within first hour
            return True
        return False

    def should_heartbeat(self):
        """Check if it's time for a health log."""
        now = _time.time()
        if now - self.last_heartbeat >= config.HEARTBEAT_INTERVAL:
            self.last_heartbeat = now
            return True
        return False

    def should_telegram_heartbeat(self):
        """Check if it's time for a Telegram health ping."""
        now = _time.time()
        if now - self.last_telegram_heartbeat >= config.HEARTBEAT_TELEGRAM_INTERVAL:
            self.last_telegram_heartbeat = now
            return True
        return False

    def get_status(self):
        """Get watchdog status dict for logging."""
        now = get_ist_now()
        if now.date() != self.last_error_reset:
            self.errors_today = 0
            self.last_error_reset = now.date()

        uptime_secs = _time.time() - self.startup_time
        hours = int(uptime_secs // 3600)
        mins = int((uptime_secs % 3600) // 60)

        return {
            'uptime': f"{hours}h {mins}m",
            'safe_mode': self.safe_mode,
            'consecutive_failures': self.consecutive_failures,
            'errors_today': self.errors_today,
            'data_stale': self.is_data_stale(),
            'candle_age_secs': int(_time.time() - self.last_successful_candle),
        }


def recover_position_state():
    """
    On startup, check exchange for any open positions.
    Returns position dict or None.
    """
    if not config.RECOVER_POSITION_ON_STARTUP:
        return None

    try:
        from executor import get_position
        pos = get_position()
        if pos:
            print(f"[WATCHDOG] Recovered position: {pos['side']} {pos['size']} BTC @ {pos['entry_price']}")
            return pos
        else:
            print("[WATCHDOG] No open position found on startup")
            return None
    except Exception as e:
        print(f"[WATCHDOG] Position recovery failed: {e}")
        return None


def perform_daily_restart():
    """
    Clean restart: close positions, save state, restart process.
    """
    print("[WATCHDOG] Performing daily restart...")

    try:
        from executor import close_position, cancel_all_orders
        cancel_all_orders()
        close_position()
        print("[WATCHDOG] Positions closed for restart")
    except Exception as e:
        print(f"[WATCHDOG] Error closing positions: {e}")

    _time.sleep(2)

    # Restart via exec (systemd will restart the service)
    print("[WATCHDOG] Restarting process...")
    os.execv(sys.executable, [sys.executable] + sys.argv)
