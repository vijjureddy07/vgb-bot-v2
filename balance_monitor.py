"""
VGB Delta Bot v2 — Balance Monitor
=====================================
Tracks balance changes to detect withdrawals vs trading PnL.
Prevents confusion when capital is withdrawn mid-operation.
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
        self.balance_history = []

    def update(self, new_balance, has_open_position=False):
        """
        Update balance and detect withdrawals/deposits.
        Returns: (adjusted_balance, event_type, event_amount)
        event_type: None, 'WITHDRAWAL', 'DEPOSIT', 'TRADE_PNL'
        """
        if new_balance is None:
            return self.last_known_balance, None, 0

        diff = new_balance - self.last_known_balance

        event_type = None
        event_amount = 0

        # If no open position and balance changed significantly (>5%), it's likely a withdrawal/deposit
        if not has_open_position and abs(diff) > self.last_known_balance * 0.05 and abs(diff) > 10:
            if diff < 0:
                event_type = 'WITHDRAWAL'
                event_amount = abs(diff)
                self.total_withdrawn += event_amount
                self.withdrawal_events.append({
                    'time': get_ist_now().strftime('%Y-%m-%d %H:%M:%S'),
                    'amount': event_amount,
                    'balance_before': self.last_known_balance,
                    'balance_after': new_balance
                })
            elif diff > 0:
                event_type = 'DEPOSIT'
                event_amount = diff
                self.total_deposited += event_amount

        elif diff != 0:
            event_type = 'TRADE_PNL'
            event_amount = diff

        # Record history
        self.balance_history.append({
            'time': get_ist_now().strftime('%Y-%m-%d %H:%M:%S'),
            'balance': new_balance,
            'change': diff,
            'event': event_type
        })

        # Keep history manageable
        if len(self.balance_history) > 1000:
            self.balance_history = self.balance_history[-500:]

        self.last_known_balance = new_balance
        return new_balance, event_type, event_amount

    def set_session_start(self, balance):
        """Mark balance at session start for session PnL tracking."""
        self.session_start_balance = balance

    def get_session_pnl(self, current_balance):
        """Get PnL since session start."""
        return current_balance - self.session_start_balance

    def get_trading_pnl(self):
        """Get total PnL from trading only (excluding deposits/withdrawals)."""
        return self.last_known_balance - self.total_deposited + self.total_withdrawn

    def is_safe_to_trade(self, current_balance):
        """
        Check if balance is reasonable for trading.
        Returns False if balance is too low or looks abnormal.
        """
        if current_balance < 5:  # Less than $5
            return False, "Balance too low to trade"

        # Check for sudden massive drop (>80%) that isn't from normal trading
        if self.last_known_balance > 0:
            drop_pct = (self.last_known_balance - current_balance) / self.last_known_balance * 100
            if drop_pct > 80:
                return False, f"Balance dropped {drop_pct:.0f}% — possible liquidation or error"

        return True, None

    def get_summary(self):
        """Get balance summary."""
        return {
            'current': self.last_known_balance,
            'total_deposited': self.total_deposited,
            'total_withdrawn': self.total_withdrawn,
            'trading_pnl': self.get_trading_pnl(),
            'withdrawals': len(self.withdrawal_events)
        }