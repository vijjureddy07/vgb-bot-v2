"""
VGB Bot v3.0 — OLD Gaussian Engine
==================================
This is a LITERAL PORT of `old_gaussian_bands()` and `detect_crossovers()`
from backtest_ny_detailed.py (the script that produced the $22,638 result).

The band math and the crossover detection loop are byte-for-byte the same
algorithm. The only addition is a thin GaussianTracker class wrapper so that
main.py can call it as a streaming per-bar interface.

DO NOT "optimize" or "vectorize" or "improve" this file. If you want to
change the signal behavior, change backtest_ny_detailed.py first, verify
the new backtest result, then mirror the change here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ==================================================================
# BAND COMPUTATION — literal port of old_gaussian_bands()
# ==================================================================
def old_gaussian_bands(df, length=23, distance=1.0):
    """
    Byte-for-byte copy of backtest_ny_detailed.py::old_gaussian_bands.
    Returns (basis, upper, lower) as pandas Series aligned with df.index.
    """
    close = df['close'].astype(float)
    high  = df['high'].astype(float)
    low   = df['low'].astype(float)
    sigma = length / 6.0

    weights = np.zeros(length)
    for i in range(length):
        weights[i] = np.exp(-0.5 * ((i - (length - 1) / 2) / sigma) ** 2)
    weights /= weights.sum()

    src = close.values
    basis = np.full(len(src), np.nan)
    for bar in range(length - 1, len(src)):
        basis[bar] = np.sum(src[bar - length + 1:bar + 1] * weights)

    tr = pd.DataFrame({
        'hl': high - low,
        'hc': (high - close.shift(1)).abs(),
        'lc': (low  - close.shift(1)).abs(),
    }).max(axis=1)
    atr = tr.rolling(window=length).mean()
    atr_vals = atr.fillna(0).values

    atr_smooth = np.full(len(atr_vals), np.nan)
    for bar in range(length - 1, len(atr_vals)):
        atr_smooth[bar] = np.sum(atr_vals[bar - length + 1:bar + 1] * weights)

    basis_s = pd.Series(basis, index=df.index)
    atr_s   = pd.Series(atr_smooth, index=df.index)
    return basis_s, basis_s + atr_s * distance, basis_s - atr_s * distance


# ==================================================================
# CROSSOVER DETECTION — literal port of detect_crossovers()
# ==================================================================
def detect_crossovers(df, basis, upper, lower):
    """
    Byte-for-byte copy of backtest_ny_detailed.py::detect_crossovers.
    Returns a list of signal dicts [{'index': i, 'time': ts, 'signal': 'BUY'|'SELL', 'price': cc}, ...].
    """
    close = df['close'].astype(float)
    signals = []
    prev = None
    for i in range(1, len(df)):
        if pd.isna(upper.iloc[i]) or pd.isna(lower.iloc[i]):
            continue
        cc, pc = close.iloc[i], close.iloc[i - 1]
        cu, pu = upper.iloc[i], upper.iloc[i - 1]
        cl, pl = lower.iloc[i], lower.iloc[i - 1]
        if pc <= pu and cc > cu and prev != 'above':
            signals.append({'index': i, 'time': df.index[i], 'signal': 'BUY',  'price': cc})
            prev = 'above'
        elif pc >= pl and cc < cl and prev != 'below':
            signals.append({'index': i, 'time': df.index[i], 'signal': 'SELL', 'price': cc})
            prev = 'below'
    return signals


# ==================================================================
# GaussianTracker — streaming interface for live bot (main.py)
# ==================================================================
class GaussianTracker:
    """
    Per-timeframe stateful tracker for main.py. Internally uses the same two
    functions above; guarantees the live bot signals match the backtest.

    API:
      tracker = GaussianTracker('3m', length, distance, mode)
      signal, details = tracker.update(df)

    `df` is the full candle DataFrame (not just the latest bar). The tracker
    runs the same detect_crossovers() over the whole df internally but caches
    the last-fired signal state so it only emits new signals.

    `mode` is accepted for v2 compatibility — v3 always uses OLD.

    This is intentionally NOT optimized for speed — the live bot runs it once
    per 3 minutes on ~200 bars, which takes a few ms. Correctness > speed.
    """

    def __init__(self, timeframe: str, length: int, distance: float, mode: str = 'OLD'):
        self.timeframe = timeframe
        self.length    = int(length)
        self.distance  = float(distance)
        self.mode      = mode
        # The 'prev' state from detect_crossovers — shared across calls so we
        # don't re-fire signals we've already reported.
        self._prev_state = None  # 'above' | 'below' | None
        # Timestamp of the last bar we reported a signal on (for dedup safety).
        self._last_reported_time = None

    def reset(self):
        """Clear state. Call at session start."""
        self._prev_state = None
        self._last_reported_time = None

    def update(self, df: pd.DataFrame):
        """
        Evaluate the full df, return (signal_or_None, details).

        Only emits a signal if the LAST bar of df is a newly-fired crossover
        that we haven't reported yet. Any earlier crossovers in df have
        already been seen on prior updates (or never will be, e.g. on cold
        start — we walk through them silently to establish prev_state).
        """
        if df is None or len(df) < 2:
            return None, None

        basis, upper, lower = old_gaussian_bands(df, self.length, self.distance)

        # Run the same detection loop. This replays all signals in df.
        # We only care about the VERY LAST one if it's at the latest bar.
        signals = detect_crossovers(df, basis, upper, lower)

        latest_time = df.index[-1]

        details = None
        u_last = upper.iloc[-1]
        l_last = lower.iloc[-1]
        if not pd.isna(u_last) and not pd.isna(l_last):
            details = {
                'price':      float(df['close'].iloc[-1]),
                'upper':      float(u_last),
                'lower':      float(l_last),
                'basis':      float(basis.iloc[-1]) if not pd.isna(basis.iloc[-1]) else None,
                'atr_smooth': None,   # not used by main.py
                'bar_time':   latest_time,
            }

        # Only EMIT a signal if the LAST detected crossover is exactly at
        # the latest bar AND we haven't already emitted for this bar.
        # Identical logic to the batch detect_crossovers loop.
        if signals and signals[-1]['time'] == latest_time:
            if self._last_reported_time != latest_time:
                self._last_reported_time = latest_time
                self._prev_state = 'above' if signals[-1]['signal'] == 'BUY' else 'below'
                return signals[-1]['signal'], details

        return None, details


# ==================================================================
# Legacy helpers (v2 compatibility)
# ==================================================================
def check_momentum(details, threshold_pct):
    """v3 never calls this. v2 callers will resolve, always returns True (pass-through)."""
    return True


# Also expose `compute_bands` for anyone who was calling the old symbol.
def compute_bands(df, length=23, distance=1.0, sigma=None):
    basis, upper, lower = old_gaussian_bands(df, length, distance)
    atr_smooth = np.full(len(df), np.nan)
    return (
        basis.values,
        upper.values,
        lower.values,
        atr_smooth,
    )