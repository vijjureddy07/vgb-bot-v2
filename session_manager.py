"""
VGB Bot v3.0 — Session Manager
==============================
NY session ONLY, IST:
  Mon-Thu: 18:30 -> 01:00 (next day)
  Fri:     18:30 -> 23:30
  Sat/Sun: no trading

Preserves v2 function names so watchdog.py, main.py, etc. keep working:
  - get_ist_now()
  - get_current_session(now_ist) -> (session_name, session_config, session_end)
  - is_session_transition(prev, curr) -> bool
  - format_session_status(name, cfg, end) -> str

Added v3-specific helpers for the main loop:
  - seconds_until_next_session(now)
  - seconds_until_session_end(now)
  - next_m3_boundary_at_or_after(dt)
  - last_m3_close_before(dt)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config


IST = ZoneInfo("Asia/Kolkata")
WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ============================================================
# Time helpers
# ============================================================
def get_ist_now() -> datetime:
    """Current IST datetime. Used by v2 main.py extensively — keep name."""
    return datetime.now(IST)


def _to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        # Assume IST for naive datetimes (matches v2 assumption).
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


# ============================================================
# Session window resolution
# ============================================================
def _window_starting_on(day_dt: datetime):
    """
    Given a date (in IST), return (start_dt, end_dt) for the NY session that
    STARTS on that date, or None if that weekday has no session.
    """
    day_dt = _to_ist(day_dt)
    wd = day_dt.weekday()
    spec = config.NY_WINDOWS_BY_WEEKDAY.get(wd)
    if spec is None:
        return None

    sh, sm, eh, em = spec
    base = day_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    start = base.replace(hour=sh, minute=sm)
    if eh >= 24:
        end = (base + timedelta(days=1)).replace(hour=eh - 24, minute=em)
    else:
        end = base.replace(hour=eh, minute=em)
    return start, end


def _active_session_window(now: datetime):
    """
    Return (start, end) of the session CURRENTLY active, or None if not in one.
    Handles overnight rollover: at 00:30 Tue we're still in the session that
    started Mon 18:30.
    """
    now = _to_ist(now)
    # Check yesterday's session (overnight case) first.
    yest_win = _window_starting_on(now - timedelta(days=1))
    if yest_win and yest_win[0] <= now < yest_win[1]:
        return yest_win
    today_win = _window_starting_on(now)
    if today_win and today_win[0] <= now < today_win[1]:
        return today_win
    return None


def _next_session_window(now: datetime):
    """Return (start, end) of the next upcoming session. Searches up to 7 days."""
    now = _to_ist(now)
    # If today's session hasn't started yet, that's next.
    today_win = _window_starting_on(now)
    if today_win and now < today_win[0]:
        return today_win
    for delta in range(1, 8):
        win = _window_starting_on(now + timedelta(days=delta))
        if win is not None:
            return win
    raise RuntimeError("No upcoming session in next 7 days — check config.NY_WINDOWS_BY_WEEKDAY")


# ============================================================
# v2-compatible API
# ============================================================
def get_current_session(now_ist: datetime):
    """
    Returns (session_name, session_config, session_end).
    v3 only has NY. Returns (None, None, None) outside session.
    """
    win = _active_session_window(now_ist)
    if win is None:
        return None, None, None
    _, end = win
    return "NY", config.SESSIONS["NY"], end


def is_session_transition(prev_session, curr_session) -> bool:
    """True if prev -> curr represents a state change."""
    return prev_session != curr_session


def format_session_status(session_name, session_config, session_end) -> str:
    if session_name is None:
        secs = seconds_until_next_session()
        win = _next_session_window(get_ist_now())
        start, end = win
        return (f"Idle — next session {WEEKDAY_LABELS[start.weekday()]} "
                f"{start.strftime('%H:%M')} IST (in {secs//3600}h {(secs%3600)//60}m)")
    now = get_ist_now()
    mins_left = max(0, int((session_end - now).total_seconds() // 60))
    mode = session_config.get("mode", "?")
    htf = session_config.get("htf_timeframe", "?")
    return f"{session_name} active ({mode}, {htf}) — ends {session_end.strftime('%H:%M')} ({mins_left}m left)"


# ============================================================
# v3 additions
# ============================================================
def seconds_until_next_session(now: datetime = None) -> int:
    if now is None:
        now = get_ist_now()
    win = _active_session_window(now)
    if win is not None:
        return 0
    start, _ = _next_session_window(now)
    return max(0, int((start - now).total_seconds()))


def seconds_until_session_end(now: datetime = None) -> int:
    if now is None:
        now = get_ist_now()
    win = _active_session_window(now)
    if win is None:
        return 0
    _, end = win
    return max(0, int((end - now).total_seconds()))


def is_in_session(now: datetime = None) -> bool:
    if now is None:
        now = get_ist_now()
    return _active_session_window(now) is not None


# ============================================================
# M3 boundary helpers
# ============================================================
def next_m3_boundary_at_or_after(dt: datetime) -> datetime:
    """Next 3-minute boundary (minute % 3 == 0) at or after dt."""
    dt = _to_ist(dt)
    if dt.second > 0 or dt.microsecond > 0:
        dt = (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)
    while dt.minute % 3 != 0:
        dt += timedelta(minutes=1)
    return dt


def last_m3_close_before(dt: datetime) -> datetime:
    dt = _to_ist(dt).replace(second=0, microsecond=0)
    while dt.minute % 3 != 0:
        dt -= timedelta(minutes=1)
    return dt


# ============================================================
# Self-test
# ============================================================
if __name__ == "__main__":
    tests = [
        ("Mon 18:00 — pre-open",   datetime(2026, 4, 20, 18,  0, tzinfo=IST), None),
        ("Mon 18:30 — open",        datetime(2026, 4, 20, 18, 30, tzinfo=IST), "NY"),
        ("Mon 22:00 — middle",      datetime(2026, 4, 20, 22,  0, tzinfo=IST), "NY"),
        ("Tue 00:59 — last min",    datetime(2026, 4, 21,  0, 59, tzinfo=IST), "NY"),
        ("Tue 01:00 — close",       datetime(2026, 4, 21,  1,  0, tzinfo=IST), None),
        ("Fri 23:29 — last min",    datetime(2026, 4, 24, 23, 29, tzinfo=IST), "NY"),
        ("Fri 23:30 — close",       datetime(2026, 4, 24, 23, 30, tzinfo=IST), None),
        ("Sat 22:00 — no trade",    datetime(2026, 4, 25, 22,  0, tzinfo=IST), None),
        ("Sun 18:30 — no trade",    datetime(2026, 4, 19, 18, 30, tzinfo=IST), None),
    ]
    ok = True
    for desc, ts, expected in tests:
        name, cfg, end = get_current_session(ts)
        flag = "OK " if name == expected else "FAIL"
        if name != expected:
            ok = False
        end_s = end.strftime("%a %H:%M") if end else "-"
        print(f"{flag}  {desc:28s}  got={name!s:5s}  end={end_s}")

    print("\nM3 boundary tests:")
    btests = [
        (datetime(2026, 4, 20, 18, 30,  0, tzinfo=IST), datetime(2026, 4, 20, 18, 30, tzinfo=IST)),
        (datetime(2026, 4, 20, 18, 30,  1, tzinfo=IST), datetime(2026, 4, 20, 18, 33, tzinfo=IST)),
        (datetime(2026, 4, 21,  0, 59, 30, tzinfo=IST), datetime(2026, 4, 21,  1,  0, tzinfo=IST)),
        (datetime(2026, 4, 21,  1,  0,  1, tzinfo=IST), datetime(2026, 4, 21,  1,  3, tzinfo=IST)),
    ]
    for ts, expected in btests:
        got = next_m3_boundary_at_or_after(ts)
        flag = "OK " if got == expected else "FAIL"
        if got != expected:
            ok = False
        print(f"{flag}  at/after {ts.strftime('%H:%M:%S')} -> {got.strftime('%H:%M:%S')} (expected {expected.strftime('%H:%M:%S')})")

    print("\nALL PASSED" if ok else "\nFAILURES PRESENT")