"""
VGB Delta Bot v2 — Session Manager
=====================================
Detects current trading session (Asia/London/NY).
Handles Monday delayed start, Friday early close.
Returns active mode and config for the current session.
"""

from datetime import datetime, timedelta
import config


def get_ist_now():
    """Get current time in IST (UTC+5:30)."""
    import time as _time
    utc_now = datetime.utcnow()
    ist_now = utc_now + timedelta(hours=5, minutes=30)
    return ist_now


def get_current_session(ts_ist=None):
    """
    Determine which session is active at the given IST timestamp.
    Returns: (session_name, session_config, session_end_time) or (None, None, None)
    """
    if ts_ist is None:
        ts_ist = get_ist_now()

    dow = ts_ist.weekday()  # 0=Mon, 6=Sun

    # No trading on weekends
    if dow in config.NO_TRADE_DAYS:
        return None, None, None

    h = ts_ist.hour
    m = ts_ist.minute
    time_minutes = h * 60 + m

    # Check each session
    for session_name, sess_cfg in config.SESSIONS.items():
        if not sess_cfg['enabled']:
            continue

        if session_name == 'ASIA':
            start_m = sess_cfg['start_hour'] * 60 + sess_cfg['start_min']
            end_m = sess_cfg['end_hour'] * 60 + sess_cfg['end_min']
            if start_m <= time_minutes < end_m:
                session_end = ts_ist.replace(
                    hour=sess_cfg['end_hour'],
                    minute=sess_cfg['end_min'],
                    second=0, microsecond=0
                )
                return session_name, sess_cfg, session_end

        elif session_name == 'LONDON':
            start_m = sess_cfg['start_hour'] * 60 + sess_cfg['start_min']
            end_m = sess_cfg['end_hour'] * 60 + sess_cfg['end_min']
            if start_m <= time_minutes < end_m:
                session_end = ts_ist.replace(
                    hour=sess_cfg['end_hour'],
                    minute=sess_cfg['end_min'],
                    second=0, microsecond=0
                )
                return session_name, sess_cfg, session_end

        elif session_name == 'NY':
            # Monday delayed start
            if dow == 0:
                start_m = sess_cfg['monday_start_hour'] * 60 + sess_cfg['monday_start_min']
            else:
                start_m = sess_cfg['start_hour'] * 60 + sess_cfg['start_min']

            # Friday early close
            if dow == 4:
                end_m = sess_cfg['friday_end_hour'] * 60 + sess_cfg['friday_end_min']
                if start_m <= time_minutes < end_m:
                    session_end = ts_ist.replace(
                        hour=sess_cfg['friday_end_hour'],
                        minute=sess_cfg['friday_end_min'],
                        second=0, microsecond=0
                    )
                    return session_name, sess_cfg, session_end
            else:
                # NY spans midnight: 7pm - 2am
                end_m = sess_cfg['end_hour'] * 60 + sess_cfg['end_min']  # 120 (2am)

                if time_minutes >= start_m:  # After 7pm
                    session_end = (ts_ist + timedelta(days=1)).replace(
                        hour=sess_cfg['end_hour'],
                        minute=sess_cfg['end_min'],
                        second=0, microsecond=0
                    )
                    return session_name, sess_cfg, session_end
                elif time_minutes < end_m:  # Before 2am (continuation from previous day)
                    # Check if previous day was a trading day
                    prev_dow = (dow - 1) % 7
                    if prev_dow not in config.NO_TRADE_DAYS:
                        session_end = ts_ist.replace(
                            hour=sess_cfg['end_hour'],
                            minute=sess_cfg['end_min'],
                            second=0, microsecond=0
                        )
                        return session_name, sess_cfg, session_end

    return None, None, None


def get_session_mode(session_config):
    """Get the trading mode for a session."""
    if session_config is None:
        return None
    return session_config.get('mode')


def get_session_timeframes(session_config):
    """Get the HTF and entry timeframes for a session."""
    if session_config is None:
        return None, None
    return session_config.get('htf_timeframe'), session_config.get('entry_timeframe')


def is_session_transition(prev_session, current_session):
    """Check if we've transitioned between sessions."""
    if prev_session is None and current_session is not None:
        return True
    if prev_session is not None and current_session is None:
        return True
    if prev_session != current_session:
        return True
    return False


def time_until_next_session(ts_ist=None):
    """Returns (session_name, seconds_until_start) for the next session."""
    if ts_ist is None:
        ts_ist = get_ist_now()

    dow = ts_ist.weekday()
    h = ts_ist.hour
    m = ts_ist.minute
    current_minutes = h * 60 + m

    # Check today's remaining sessions
    sessions_today = []
    for sname, scfg in config.SESSIONS.items():
        if not scfg['enabled']:
            continue
        if sname == 'NY' and dow == 0:
            start_m = scfg['monday_start_hour'] * 60 + scfg['monday_start_min']
        else:
            start_m = scfg['start_hour'] * 60 + scfg['start_min']

        if start_m > current_minutes:
            sessions_today.append((sname, start_m))

    if sessions_today:
        sessions_today.sort(key=lambda x: x[1])
        next_name, next_start = sessions_today[0]
        seconds = (next_start - current_minutes) * 60
        return next_name, seconds

    # No more sessions today — find tomorrow's first
    # Skip weekends
    days_ahead = 1
    next_dow = (dow + 1) % 7
    while next_dow in config.NO_TRADE_DAYS:
        days_ahead += 1
        next_dow = (next_dow + 1) % 7

    # First session of next trading day (Asia at 5:30am)
    first_session = min(
        ((sname, scfg['start_hour'] * 60 + scfg['start_min'])
         for sname, scfg in config.SESSIONS.items() if scfg['enabled']),
        key=lambda x: x[1]
    )
    minutes_remaining_today = (24 * 60) - current_minutes
    minutes_in_between = (days_ahead - 1) * 24 * 60
    minutes_to_start = first_session[1]
    total_seconds = (minutes_remaining_today + minutes_in_between + minutes_to_start) * 60

    return first_session[0], total_seconds


def format_session_status(session_name, session_config, session_end):
    """Format session info for display/logging."""
    if session_name is None:
        next_sess, secs = time_until_next_session()
        mins = secs // 60
        hours = mins // 60
        mins = mins % 60
        return f"No active session. Next: {next_sess} in {hours}h {mins}m"

    mode = get_session_mode(session_config)
    htf, entry = get_session_timeframes(session_config)
    end_str = session_end.strftime('%H:%M') if session_end else '??:??'
    return f"{session_name} active until {end_str} IST | Mode: {mode} | HTF: {htf} | Entry: {entry}"
