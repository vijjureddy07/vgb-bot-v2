"""
VGB Delta Bot v2 — Gaussian Engine
====================================
Computes Gaussian Volatility Bands and detects crossovers.
Handles M1, M3, M5, M15 timeframes.
"""

import numpy as np
import pandas as pd


def gaussian_kernel(length):
    """Generate normalized Gaussian kernel weights."""
    sigma = length / 6.0
    x = np.arange(length) - (length - 1) / 2.0
    kernel = np.exp(-0.5 * (x / sigma) ** 2)
    return kernel / kernel.sum()


def gaussian_smooth(series, length):
    """Apply Gaussian smoothing to a pandas Series."""
    kernel = gaussian_kernel(length)
    padded = np.concatenate([np.full(length - 1, series.iloc[0]), series.values])
    smoothed = np.convolve(padded, kernel, mode='valid')
    return pd.Series(smoothed, index=series.index)


def compute_bands(df, length=23, distance=1):
    """
    Compute Gaussian Volatility Bands (BigBeluga style).
    Returns: (basis, upper, lower)
    """
    close = df['close'].astype(float)
    high = df['high'].astype(float)
    low = df['low'].astype(float)

    basis = gaussian_smooth(close, length)

    tr = pd.DataFrame({
        'hl': high - low,
        'hc': (high - close.shift(1)).abs(),
        'lc': (low - close.shift(1)).abs()
    }).max(axis=1)
    atr = tr.rolling(window=length).mean()
    atr_smooth = gaussian_smooth(atr.fillna(0), length)

    upper = basis + atr_smooth * distance
    lower = basis - atr_smooth * distance

    return basis, upper, lower


def detect_crossover(df, basis, upper, lower):
    """
    Check the latest candle for a crossover.
    Returns: 'BUY', 'SELL', or None
    Also returns the signal details dict if crossover detected.
    """
    if len(df) < 2:
        return None, None

    i = len(df) - 1
    if pd.isna(upper.iloc[i]) or pd.isna(lower.iloc[i]):
        return None, None

    curr_close = float(df['close'].iloc[i])
    prev_close = float(df['close'].iloc[i - 1])
    curr_upper = float(upper.iloc[i])
    prev_upper = float(upper.iloc[i - 1])
    curr_lower = float(lower.iloc[i])
    prev_lower = float(lower.iloc[i - 1])

    # Bullish crossover
    if prev_close <= prev_upper and curr_close > curr_upper:
        return 'BUY', {
            'signal': 'BUY',
            'price': curr_close,
            'upper': curr_upper,
            'lower': curr_lower,
            'basis': float(basis.iloc[i])
        }

    # Bearish crossover
    if prev_close >= prev_lower and curr_close < curr_lower:
        return 'SELL', {
            'signal': 'SELL',
            'price': curr_close,
            'upper': curr_upper,
            'lower': curr_lower,
            'basis': float(basis.iloc[i])
        }

    return None, None


def check_momentum(signal_details, threshold_pct=0.05):
    """
    Check if crossover has sufficient momentum.
    Price must exceed band by at least threshold_pct%.
    """
    if signal_details is None:
        return False

    price = signal_details['price']

    if signal_details['signal'] == 'BUY':
        min_price = signal_details['upper'] * (1 + threshold_pct / 100)
        return price >= min_price
    else:
        max_price = signal_details['lower'] * (1 - threshold_pct / 100)
        return price <= max_price


class GaussianTracker:
    """
    Tracks Gaussian bands and crossovers for a single timeframe.
    Maintains state across candle updates.
    """

    def __init__(self, timeframe, length=23, distance=1):
        self.timeframe = timeframe
        self.length = length
        self.distance = distance
        self.df = None
        self.basis = None
        self.upper = None
        self.lower = None
        self.last_position = None  # 'above' or 'below'
        self.current_bias = None   # 'BUY' or 'SELL' — set by crossovers

    def update(self, df):
        """
        Update with new candle data.
        Returns crossover signal if detected: ('BUY'/'SELL', details_dict) or (None, None)
        """
        self.df = df
        self.basis, self.upper, self.lower = compute_bands(df, self.length, self.distance)

        signal, details = detect_crossover(df, self.basis, self.upper, self.lower)

        if signal is not None:
            # Prevent duplicate signals in same direction
            if signal == 'BUY' and self.last_position != 'above':
                self.last_position = 'above'
                self.current_bias = 'BUY'
                return signal, details
            elif signal == 'SELL' and self.last_position != 'below':
                self.last_position = 'below'
                self.current_bias = 'SELL'
                return signal, details

        return None, None

    def get_bias(self):
        """Get current directional bias."""
        return self.current_bias

    def get_current_bands(self):
        """Get latest band values."""
        if self.basis is None or len(self.basis) == 0:
            return None, None, None
        return (
            float(self.basis.iloc[-1]),
            float(self.upper.iloc[-1]),
            float(self.lower.iloc[-1])
        )
