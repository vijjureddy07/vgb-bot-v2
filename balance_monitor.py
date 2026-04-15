"""
VGB Delta Bot v2.2 — Balance Monitor (Fixed)
===============================================
Tracks balance changes. Won't misidentify trading PnL as deposits/withdrawals.
"""

import config
from session_manager import get_ist_now


class BalanceMonitor:
    def __init__(self, initial_balance=0):
        self.last_known_balance = initial_balance
        self.session_start_balance = initial_balance
        self.total_deposited = initial_balance
        self.total_withdrawn = 0
        self.withdrawal_events = []
        self.just_closed_position = False  # flag to prevent false deposit alerts

    def mark_position_closed(self):
        """Call this when a position is closed — prevents false deposit detection."""
        self.just_closed_position = True

    def update(self, new_balance, has_open_position=False):
        """
        Update balance and detect withdrawals/deposits.
        Returns: (adjusted_balance, event_type, event_amount)
        """
        if new_balance is None:
            return self.last_known_balance, None, 0

        diff = new_balance - self.last_known_balance
        event_type = None
        event_amount = 0

        # If we just closed a position, any balance change is trading PnL, not deposit/withdrawal
        if self.just_closed_position:
            self.just_closed_position = False
            if diff != 0:
                event_type = 'TRADE_PNL'
                event_amount = diff
            self.last_known_balance = new_balance
            return new_balance, event_type, event_amount

        # Only flag as deposit/withdrawal if:
        # 1. No open position
        # 2. Balance changed significantly (>5% and >$10)
        # 3. We didn't just close a position
        if not has_open_position and abs(diff) > self.last_known_balance * 0.05 and abs(diff) > 10:
            if diff < 0:
                event_type = 'WITHDRAWAL'
                event_amount = abs(diff)
                self.total_withdrawn += event_amount
                self.withdrawal_events.append({
                    'time': get_ist_now().strftime('%Y-%m-%d %H:%M:%S'),
                    'amount': event_amount,
                })
            elif diff > 0:
                event_type = 'DEPOSIT'
                event_amount = diff
                self.total_deposited += event_amount
        elif diff != 0:
            event_type = 'TRADE_PNL'
            event_amount = diff

        self.last_known_balance = new_balance
        return new_balance, event_type, event_amount

    def set_session_start(self, balance):
        self.session_start_balance = balance

    def get_session_pnl(self, current_balance):
        return current_balance - self.session_start_balance

    def is_safe_to_trade(self, current_balance):
        if current_balance < 5:
            return False, "Balance too low to trade"
        if self.last_known_balance > 0:
            drop_pct = (self.last_known_balance - current_balance) / self.last_known_balance * 100
            if drop_pct > 80:
                return False, f"Balance dropped {drop_pct:.0f}%"
        return True, None