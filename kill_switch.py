"""
VGB Delta Bot v2 — Kill Switch
=================================
Emergency shutdown mechanism.
Two ways to trigger:
1. File-based: touch /opt/vgb_bot_v2/KILL
2. Telegram: send /kill to the bot (if webhook enabled)

When triggered: cancels all orders, closes positions, stops bot.
"""

import os
import sys

KILL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'KILL')
PAUSE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'PAUSE')


def check_kill_switch():
    """
    Check if kill switch is activated.
    Returns True if bot should shut down.
    """
    if os.path.exists(KILL_FILE):
        return True
    return False


def check_pause_switch():
    """
    Check if pause switch is activated.
    Returns True if bot should pause (no new trades, keep monitoring).
    """
    if os.path.exists(PAUSE_FILE):
        return True
    return False


def activate_kill():
    """Activate kill switch."""
    with open(KILL_FILE, 'w') as f:
        f.write('KILL ACTIVATED')
    print("[KILL] Kill switch activated")


def deactivate_kill():
    """Remove kill switch file."""
    if os.path.exists(KILL_FILE):
        os.remove(KILL_FILE)
        print("[KILL] Kill switch deactivated")


def activate_pause():
    """Activate pause — no new trades but keep monitoring."""
    with open(PAUSE_FILE, 'w') as f:
        f.write('PAUSED')
    print("[KILL] Pause activated — no new trades")


def deactivate_pause():
    """Remove pause file."""
    if os.path.exists(PAUSE_FILE):
        os.remove(PAUSE_FILE)
        print("[KILL] Pause deactivated — trading resumed")


def execute_emergency_shutdown():
    """
    Full emergency shutdown:
    - Cancel all orders
    - Close all positions
    - Stop the bot
    """
    print("[KILL] *** EMERGENCY SHUTDOWN INITIATED ***")

    try:
        from executor import cancel_all_orders, close_position
        cancel_all_orders()
        close_position()
        print("[KILL] All orders cancelled, positions closed")
    except Exception as e:
        print(f"[KILL] Error during shutdown: {e}")

    try:
        from telegram_alerts import send_message
        send_message("🛑 <b>EMERGENCY SHUTDOWN</b>\nKill switch activated. All positions closed. Bot stopped.")
    except:
        pass

    # Remove kill file so bot can start clean next time
    deactivate_kill()

    print("[KILL] Bot shutting down")
    sys.exit(0)