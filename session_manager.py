"""
VGB Delta Bot v2.2 — Session Manager (FIXED)
===============================================
Properly handles NY session spanning midnight.
Thursday 7pm → Friday 2am now works correctly.
"""

from datetime import datetime, timedelta
import config


def get_ist_now():
    """Get current time in IST (UTC+5:30)."""
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
            if dow not in config.NO_TRADE_DAYS and start_m <= time_minutes < end_m:
                session_end = ts_ist.replace(
                    hour=sess_cfg['end_hour'], minute=sess_cfg['end_min'],
                    second=0, microsecond=0
                )
                return session_name, sess_cfg, session_end

        elif session_name == 'LONDON':
            start_m = sess_cfg['start_hour'] * 60 + sess_cfg['start_min']
            end_m = sess_cfg['end_hour'] * 60 + sess_cfg['end_min']
            if dow not in config.NO_TRADE_DAYS and start_m <= time_minutes < end_m:
                session_end = ts_ist.replace(
                    hour=sess_cfg['end_hour'], minute=sess_cfg['end_min'],
                    second=0, microsecond=0
                )
                return session_name, sess_cfg, session_end

        elif session_name == 'NY':
            end_m = sess_cfg['end_hour'] * 60 + sess_cfg['end_min']  # 120 (2am)

            # CASE 1: Before 2am — check if this is continuation from previous day's NY
            if time_minutes < end_m:
                # What day was yesterday?
                prev_dow = (dow - 1) % 7
                
                # NY continues from previous day if:
                # - Previous day was Mon-Thu (dow 0-3) → normal NY runs until 2am
                # - Previous day was Friday (dow 4) → Friday NY ends at 11:30pm, NO continuation
                # - Previous day was weekend → no session
                
                if prev_dow in config.NO_TRADE_DAYS:
                    continue  # previous day was weekend, no NY to continue
                
                if prev_dow == 4:
                    # Previous day was Friday — NY ended at 11:30pm, no midnight continuation
                    continue
                
                # Valid continuation (previous day was Mon-Thu)
                session_end = ts_ist.replace(
                    hour=sess_cfg['end_hour'], minute=sess_cfg['end_min'],
                    second=0, microsecond=0
                )
                return session_name, sess_cfg, session_end

            # CASE 2: After start time on a weekday — new NY session starting
            if dow in config.NO_TRADE_DAYS:
                continue

            # Monday delayed start
            if dow == 0:
                start_m = sess_cfg['monday_start_hour'] * 60 + sess_cfg['monday_start_min']
            else:
                start_m = sess_cfg['start_hour'] * 60 + sess_cfg['start_min']

            # Friday early close
            if dow == 4:
                end_friday = sess_cfg['friday_end_hour'] * 60 + sess_cfg['friday_end_min']
                if start_m <= time_minutes < end_friday:
                    session_end = ts_ist.replace(
                        hour=sess_cfg['friday_end_hour'],
                        minute=sess_cfg['friday_end_min'],
                        second=0, microsecond=0
                    )
                    return session_name, sess_cfg, session_end
            else:
                # Normal NY: starts at 7pm, ends 2am next day
                if time_minutes >= start_m:
                    session_end = (ts_ist + timedelta(days=1)).replace(
                        hour=sess_cfg['end_hour'], minute=sess_cfg['end_min'],
                        second=0, microsecond=0
                    )
                    return session_name, sess_cfg, session_end

    return None, None, None


def get_session_mode(session_config):
    if session_config is None:
        return None
    return session_config.get('mode')


def get_session_timeframes(session_config):
    if session_config is None:
        return None, None
    return session_config.get('htf_timeframe'), session_config.get('entry_timeframe')


def is_session_transition(prev_session, current_session):
    if prev_session is None and current_session is not None:
        return True
    if prev_session is not None and current_session is None:
        return True
    if prev_session != current_session:
        return True
    return False


def time_until_next_session(ts_ist=None):
    if ts_ist is None:
        ts_ist = get_ist_now()

    dow = ts_ist.weekday()
    current_minutes = ts_ist.hour * 60 + ts_ist.minute

    # Check today's remaining sessions
    sessions_today = []
    for sname, scfg in config.SESSIONS.items():
        if not scfg['enabled']:
            continue
        if sname == 'NY' and dow == 0:
            start_m = scfg['monday_start_hour'] * 60 + scfg['monday_start_min']
        elif sname == 'NY':
            start_m = scfg['start_hour'] * 60 + scfg['start_min']
        else:
            start_m = scfg['start_hour'] * 60 + scfg['start_min']

        if start_m > current_minutes and dow not in config.NO_TRADE_DAYS:
            sessions_today.append((sname, start_m))

    if sessions_today:
        sessions_today.sort(key=lambda x: x[1])
        next_name, next_start = sessions_today[0]
        seconds = (next_start - current_minutes) * 60
        return next_name, seconds

    # Next trading day
    days_ahead = 1
    next_dow = (dow + 1) % 7
    while next_dow in config.NO_TRADE_DAYS:
        days_ahead += 1
        next_dow = (next_dow + 1) % 7

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