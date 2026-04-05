"""
VGB Delta Bot v2 — Telegram Alerts
=====================================
Sends trading alerts and status updates via Telegram.
"""

import requests
from datetime import datetime
import config


def send_message(text):
    """Send a message to Telegram."""
    if not config.TELEGRAM_ENABLED or not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': config.TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'HTML'
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        if not r.ok:
            print(f"[TG] Failed to send: {r.text[:100]}")
    except Exception as e:
        print(f"[TG] Error: {e}")


def alert_entry(side, price, size, session, mode, leverage, capital):
    """Alert on trade entry."""
    if not config.ALERT_ON_ENTRY:
        return
    emoji = "🟢" if side == "BUY" else "🔴"
    text = (
        f"{emoji} <b>NEW {side}</b>\n"
        f"Price: ${price:,.1f}\n"
        f"Size: {size} contracts\n"
        f"Session: {session} | Mode: {mode}\n"
        f"Leverage: {leverage}x\n"
        f"Capital: ₹{capital:,.0f}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )
    send_message(text)


def alert_exit(side, entry_price, exit_price, pnl, reason, capital):
    """Alert on trade exit."""
    if not config.ALERT_ON_EXIT:
        return
    emoji = "✅" if pnl >= 0 else "❌"
    pnl_str = f"+₹{pnl:,.2f}" if pnl >= 0 else f"-₹{abs(pnl):,.2f}"
    pct = ((exit_price - entry_price) / entry_price * 100) if side == 'BUY' else ((entry_price - exit_price) / entry_price * 100)
    text = (
        f"{emoji} <b>CLOSED {side}</b>\n"
        f"Entry: ${entry_price:,.1f} → Exit: ${exit_price:,.1f}\n"
        f"Move: {pct:+.3f}%\n"
        f"PnL: {pnl_str}\n"
        f"Reason: {reason}\n"
        f"Capital: ₹{capital:,.0f}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )
    send_message(text)


def alert_bias_change(htf_timeframe, new_bias, price):
    """Alert on HTF bias change."""
    if not config.ALERT_ON_BIAS_CHANGE:
        return
    emoji = "📈" if new_bias == "BUY" else "📉"
    text = (
        f"{emoji} <b>BIAS FLIP: {htf_timeframe} → {new_bias}</b>\n"
        f"Price: ${price:,.1f}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )
    send_message(text)


def alert_session_change(session_name, action):
    """Alert on session open/close."""
    if not config.ALERT_ON_SESSION_CHANGE:
        return
    emoji = "🔔" if action == "OPEN" else "🔕"
    text = (
        f"{emoji} <b>Session {action}: {session_name}</b>\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )
    send_message(text)


def alert_error(error_msg):
    """Alert on critical error."""
    if not config.ALERT_ON_ERROR:
        return
    text = f"⚠️ <b>BOT ERROR</b>\n{error_msg}\nTime: {datetime.now().strftime('%H:%M:%S')} IST"
    send_message(text)


def alert_safety_sl_hit(side, entry_price, sl_price, capital):
    """Alert when safety SL is hit."""
    if not config.ALERT_ON_SL_HIT:
        return
    text = (
        f"🚨 <b>SAFETY SL HIT</b>\n"
        f"{side} from ${entry_price:,.1f} stopped at ${sl_price:,.1f}\n"
        f"Capital: ₹{capital:,.0f}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )
    send_message(text)


def alert_daily_summary(trades_today, pnl_today, capital, wins, losses):
    """Send daily summary."""
    emoji = "📊"
    pnl_str = f"+₹{pnl_today:,.2f}" if pnl_today >= 0 else f"-₹{abs(pnl_today):,.2f}"
    text = (
        f"{emoji} <b>DAILY SUMMARY</b>\n"
        f"Trades: {trades_today} (W:{wins} L:{losses})\n"
        f"PnL: {pnl_str}\n"
        f"Capital: ₹{capital:,.0f}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )
    send_message(text)


def alert_startup(capital):
    """Alert on bot startup."""
    text = (
        f"🤖 <b>VGB Bot v2 Started</b>\n"
        f"Capital: ₹{capital:,.0f}\n"
        f"Exchange: {'Testnet' if config.USE_TESTNET else 'LIVE'}\n"
        f"Sessions: Asia({config.SESSIONS['ASIA']['mode']}) | "
        f"London({config.SESSIONS['LONDON']['mode']}) | "
        f"NY({config.SESSIONS['NY']['mode']})\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )
    send_message(text)
