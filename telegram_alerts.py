"""
VGB Delta Bot v2.2 — Telegram Alerts (USD only)
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
        f"🤖 <b>VGB Bot v2.2 Started</b>\n"
        f"Capital: ${capital:,.2f}\n"
        f"Exchange: {'Testnet' if config.USE_TESTNET else 'LIVE'}\n"
        f"Asia: {config.SESSIONS['ASIA']['htf_timeframe']}→{config.SESSIONS['ASIA']['entry_timeframe']} | "
        f"London: {config.SESSIONS['LONDON']['htf_timeframe']} | "
        f"NY: {config.SESSIONS['NY']['htf_timeframe']}\n"
        f"Breakeven: {'ON at +' + str(config.BREAKEVEN_TRIGGER_PCT) + '%' if config.BREAKEVEN_ENABLED else 'OFF'}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')} IST"
    )