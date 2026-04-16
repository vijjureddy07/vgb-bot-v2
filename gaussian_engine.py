"""
VGB Delta Bot v2.2 — Gaussian Engine (CORRECTED)
===================================================
Matches BigBeluga's "Volatility Gaussian Bands" Pine Script exactly.

Key differences from our previous implementation:
1. 21 Gaussian filters (length to length+20), averaged/medianed/moded
2. Sigma = 10 (fixed), NOT length/6
3. Volatility = SMA(high - low, 100), NOT Gaussian-smoothed ATR
4. Bands = basis ± (volatility × distance)
5. Crossover: close crosses above upper = BUY, close crosses below lower = SELL
"""

import numpy as np
import pandas as pd


def gaussian_filter(src, length, sigma=10):
    """
    Apply Gaussian filter matching Pine Script exactly.
    Pine: weight = exp(-0.5 * ((i - length/2) / sigma)^2) / sqrt(sigma * 2 * pi)
    Then normalize weights and convolve.
    """
    weights = np.zeros(length)
    total = 0.0

    for i in range(length):
        w = np.exp(-0.5 * ((i - length / 2) / sigma) ** 2) / np.sqrt(sigma * 2 * np.pi)
        weights[i] = w
        total += w

    # Normalize
    weights = weights / total

    # Apply filter: src[0] is current bar, src[i] is i bars ago
    # In pandas, we need to reverse — iloc[-1] is current, iloc[-1-i] is i bars ago
    result = np.full(len(src), np.nan)

    for bar in range(length - 1, len(src)):
        s = 0.0
        for i in range(length):
            s += src.iloc[bar - i] * weights[i]
        result[bar] = s

    return pd.Series(result, index=src.index)


def compute_bands(df, length=20, distance=1.0, mode='AVG'):
    """
    Compute Volatility Gaussian Bands matching BigBeluga's Pine Script.

    1. Compute 21 Gaussian filters with lengths from (length) to (length+20)
    2. Average/median/mode them to get basis
    3. Volatility = SMA(high - low, 100)
    4. Upper = basis + volatility * distance
    5. Lower = basis - volatility * distance
    """
    close = df['close'].astype(float)
    high = df['high'].astype(float)
    low = df['low'].astype(float)

    # Step 1: Compute 21 Gaussian filtered values
    g_values = []
    for step in range(21):  # 0 to 20
        filt_len = length + step
        gf = gaussian_filter(close, filt_len, sigma=10)
        g_values.append(gf)

    # Step 2: Aggregate based on mode
    g_matrix = pd.DataFrame({f'g{i}': g_values[i] for i in range(21)})

    if mode == 'AVG':
        basis = g_matrix.mean(axis=1)
    elif mode == 'MEDIAN':
        basis = g_matrix.median(axis=1)
    elif mode == 'MODE':
        # Mode is tricky with floats — use avg as fallback
        basis = g_matrix.mean(axis=1)
    else:
        basis = g_matrix.mean(axis=1)

    # Step 3: Volatility = SMA(high - low, 100)
    candle_range = high - low
    volatility = candle_range.rolling(window=100).mean()

    # Step 4: Bands
    upper = basis + volatility * distance
    lower = basis - volatility * distance

    return basis, upper, lower


def detect_crossover(df, basis, upper, lower):
    """
    Check the latest CLOSED candle for a crossover.
    BUY: close crosses above upper band
    SELL: close crosses below lower band
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

    # BUY: price crosses above upper band
    if prev_close <= prev_upper and curr_close > curr_upper:
        return 'BUY', {
            'signal': 'BUY', 'price': curr_close,
            'upper': curr_upper, 'lower': curr_lower,
            'basis': float(basis.iloc[i])
        }

    # SELL: price crosses below lower band
    if prev_close >= prev_lower and curr_close < curr_lower:
        return 'SELL', {
            'signal': 'SELL', 'price': curr_close,
            'upper': curr_upper, 'lower': curr_lower,
            'basis': float(basis.iloc[i])
        }

    return None, None


def check_momentum(signal_details, threshold_pct=0.04):
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

    def __init__(self, timeframe, length=20, distance=1, mode='AVG'):
        self.timeframe = timeframe
        self.length = length
        self.distance = distance
        self.mode = mode
        self.df = None
        self.basis = None
        self.upper = None
        self.lower = None
        self.last_position = None  # 'above' or 'below'
        self.current_bias = None   # 'BUY' or 'SELL'

    def update(self, df):
        """
        Update with new candle data.
        Returns (signal, details) or (None, None)
        """
        self.df = df
        self.basis, self.upper, self.lower = compute_bands(
            df, self.length, self.distance, self.mode
        )

        signal, details = detect_crossover(df, self.basis, self.upper, self.lower)

        if signal is not None:
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
        return self.current_bias

    def get_current_bands(self):
        if self.basis is None or len(self.basis) == 0:
            return None, None, None
        return (
            float(self.basis.iloc[-1]),
            float(self.upper.iloc[-1]),
            float(self.lower.iloc[-1])
        )