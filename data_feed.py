"""
VGB Delta Bot v2 — Data Feed (Binance Futures)
=================================================
Fetches OHLCV candles from Binance USDT-M Futures.
"""

import requests
import pandas as pd
from datetime import timedelta
import time as _time
import config


def fetch_binance_candles(symbol, interval, limit=100):
    """Fetch candles from Binance Futures."""
    base_url = config.BINANCE_TESTNET_URL if config.USE_TESTNET else config.BINANCE_LIVE_URL
    url = f"{base_url}/fapi/v1/klines"

    interval_map = {'1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m'}
    params = {
        'symbol': symbol,
        'interval': interval_map.get(interval, '1m'),
        'limit': limit
    }

    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        if not data:
            return None

        rows = []
        for k in data:
            rows.append([
                int(k[0]) // 1000,  # open time ms → seconds
                float(k[1]),        # open
                float(k[2]),        # high
                float(k[3]),        # low
                float(k[4]),        # close
                float(k[5]),        # volume
            ])

        df = pd.DataFrame(rows, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        df = df.sort_values('time').drop_duplicates(subset='time').reset_index(drop=True)
        df['datetime_utc'] = pd.to_datetime(df['time'], unit='s')
        df['datetime_ist'] = df['datetime_utc'] + timedelta(hours=5, minutes=30)
        df.set_index('datetime_ist', inplace=True)
        return df

    except Exception as e:
        print(f"[DATA] Error fetching {interval}: {e}")
        return None


def fetch_candles(resolution, limit=100):
    """Fetch candles from configured source."""
    return fetch_binance_candles(config.SYMBOL, resolution, limit)


def get_latest_price():
    """Get latest BTC price."""
    df = fetch_candles('1m', limit=2)
    if df is not None and len(df) > 0:
        return float(df['close'].iloc[-1])
    return None


class CandleManager:
    """Manages candle data with caching."""

    def __init__(self):
        self.cache = {}
        self.fetch_intervals = {'1m': 55, '3m': 170, '5m': 290, '15m': 890}

    def get_candles(self, timeframe, limit=100, force_refresh=False):
        now = _time.time()
        if not force_refresh and timeframe in self.cache:
            cached = self.cache[timeframe]
            age = now - cached['last_fetch']
            if age < self.fetch_intervals.get(timeframe, 60) and cached['df'] is not None:
                return cached['df']

        buffer = {'1m': config.M1_CANDLE_BUFFER, '3m': config.M3_CANDLE_BUFFER,
                  '5m': config.M5_CANDLE_BUFFER, '15m': config.M15_CANDLE_BUFFER}
        df = fetch_candles(timeframe, buffer.get(timeframe, limit))
        if df is not None:
            self.cache[timeframe] = {'df': df, 'last_fetch': now}
        return df

    def invalidate(self, timeframe=None):
        if timeframe:
            self.cache.pop(timeframe, None)
        else:
            self.cache.clear()
