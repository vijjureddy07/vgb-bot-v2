"""
VGB Delta Bot v2.2 + v3 — Telegram Alerts (USD only)
=====================================================
Base: v2.2 (unchanged — all original alert formats preserved).
Added at bottom: 4 new alerts needed by v3 main.py.
"""

import requests
from datetime import datetime
import config


def send_message(text):
    if not config.TELEGRAM_ENABLED or not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            'chat_id': config.TELEGRAM_CHAT_ID,
            'text': text, 'parse_mode': 'HTML'
        }, timeout=10)
    except:
        pass


def alert_entry(side, price, size, session, mode, leverage, capital):
    if not config.ALERT_ON_ENTRY: return
    emoji = "🟢" if side == "BUY" else "🔴"
    send_message(
        f"{emoji} <b>NEW {side}</b>\n"
        f"Price: ${price:,.1f}\n"
        f"Size: {size} BTC\n"
        f"Session: {session} | Mode: {mode}\n"
        f"Leverage: {leverage}x\n"
        f"Capital: ${capital:,.2f}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )


def alert_exit(side, entry_price, exit_price, pnl, reason, capital):
    if not config.ALERT_ON_EXIT: return
    emoji = "✅" if pnl >= 0 else "❌"
    pnl_str = f"+${pnl:,.2f}" if pnl >= 0 else f"-${abs(pnl):,.2f}"
    if side == 'BUY':
        pct = (exit_price - entry_price) / entry_price * 100
    else:
        pct = (entry_price - exit_price) / entry_price * 100
    send_message(
        f"{emoji} <b>CLOSED {side}</b>\n"
        f"Entry: ${entry_price:,.1f} → Exit: ${exit_price:,.1f}\n"
        f"Move: {pct:+.3f}%\n"
        f"PnL: {pnl_str}\n"
        f"Reason: {reason}\n"
        f"Capital: ${capital:,.2f}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )


def alert_bias_change(htf_timeframe, new_bias, price):
    if not config.ALERT_ON_BIAS_CHANGE: return
    emoji = "📈" if new_bias == "BUY" else "📉"
    send_message(
        f"{emoji} <b>BIAS FLIP: {htf_timeframe} → {new_bias}</b>\n"
        f"Price: ${price:,.1f}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )


def alert_session_change(session_name, action):
    if not config.ALERT_ON_SESSION_CHANGE: return
    emoji = "🔔" if action == "OPEN" else "🔕"
    send_message(f"{emoji} <b>Session {action}: {session_name}</b>\nTime: {datetime.now().strftime('%H:%M:%S')} IST")


def alert_error(error_msg):
    if not config.ALERT_ON_ERROR: return
    send_message(f"⚠️ <b>BOT ERROR</b>\n{error_msg}\nTime: {datetime.now().strftime('%H:%M:%S')} IST")


def alert_safety_sl_hit(side, entry_price, sl_price, capital):
    if not config.ALERT_ON_SL_HIT: return
    send_message(
        f"🚨 <b>SAFETY SL HIT</b>\n"
        f"{side} from ${entry_price:,.1f} stopped at ${sl_price:,.1f}\n"
        f"Capital: ${capital:,.2f}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )


def alert_breakeven(side, entry_price, current_price):
    """Alert when SL moved to breakeven."""
    send_message(
        f"🔒 <b>BREAKEVEN SET</b>\n"
        f"{side} from ${entry_price:,.1f} | Now ${current_price:,.1f}\n"
        f"SL moved to entry — risk free trade\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )


def alert_daily_summary(trades_today, pnl_today, capital, wins, losses):
    pnl_str = f"+${pnl_today:,.2f}" if pnl_today >= 0 else f"-${abs(pnl_today):,.2f}"
    send_message(
        f"📊 <b>DAILY SUMMARY</b>\n"
        f"Trades: {trades_today} (W:{wins} L:{losses})\n"
        f"PnL: {pnl_str}\n"
        f"Capital: ${capital:,.2f}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )


def alert_startup(capital):
    send_message(
        f"🤖 <b>VGB Bot v3.0 Started</b>\n"
        f"Capital: ${capital:,.2f}\n"
        f"Exchange: {'Testnet' if config.USE_TESTNET else 'LIVE'}\n"
        f"Strategy: NY only | M3 flip | OLD Gaussian\n"
        f"Leverage: {config.DEFAULT_LEVERAGE}x | Alloc: {int(config.DEFAULT_CAPITAL_PCT*100)}%\n"
        f"Breakeven: {'ON' if config.BREAKEVEN_ENABLED else 'OFF'} | "
        f"Safety SL: {config.SAFETY_SL_PCT}%\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )


# ============================================================
# V3 ADDITIONS — new alerts needed by v3 main.py
# ============================================================

def alert_session_end_summary(session_pnl, trades, capital, reason='scheduled end'):
    """Sent at NY session close (1am Mon-Thu / 11:30pm Fri)."""
    if not config.TELEGRAM_ENABLED:
        return
    emoji = "📈" if session_pnl >= 0 else "📉"
    pnl_str = f"+${session_pnl:,.2f}" if session_pnl >= 0 else f"-${abs(session_pnl):,.2f}"
    send_message(
        f"⏹ <b>Session end — {reason}</b>\n"
        f"Trades: {trades}\n"
        f"Session PnL: {emoji} {pnl_str}\n"
        f"Capital: ${capital:,.2f}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )


def alert_warning(message):
    """Generic warning (clock drift, failed SL placement, etc)."""
    if not config.TELEGRAM_ENABLED:
        return
    send_message(
        f"⚠️ <b>WARNING</b>\n{message}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )


def alert_info(message):
    """Generic info (session start banner, etc)."""
    if not config.TELEGRAM_ENABLED:
        return
    send_message(
        f"ℹ️ {message}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )


def alert_balance_floor_breach(balance, floor):
    """Auto-pause notice when mainnet balance drops below floor ($70)."""
    if not config.TELEGRAM_ENABLED:
        return
    send_message(
        f"🛑 <b>BALANCE FLOOR BREACHED</b>\n"
        f"Balance: ${balance:,.2f} fell below floor ${floor:,.2f}\n"
        f"Auto-pausing. Review before resuming.\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )