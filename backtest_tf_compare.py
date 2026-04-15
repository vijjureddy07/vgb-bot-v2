#!/usr/bin/env python3
"""
VGB Bot v2 — Timeframe Comparison Backtest
=============================================
Current: Asia M3→M1 | London M15 | NY M5
Proposed: Asia M3→M1 (looser) | London M5 | NY M3

Tests on last 2 weeks of data with fees.
Also tests candle-close confirmation vs immediate entry.
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import warnings
warnings.filterwarnings('ignore')

BASE_URL = "https://testnet.binancefuture.com"
SYMBOL = "BTCUSDT"
STARTING_CAPITAL = 5000.0
CAPITAL_PCT = 0.25
LEVERAGE = 25
FEE_PER_SIDE = 0.0004  # Binance taker 0.04%

START_DATE = datetime(2026, 4, 1, 0, 0, 0)
END_DATE = datetime(2026, 4, 15, 23, 59, 59)

GAUSSIAN_LENGTH = 23
GAUSSIAN_DISTANCE = 1


# ============================================================
# DATA
# ============================================================
def fetch_binance_candles(symbol, interval, start_date, end_date):
    all_candles = []
    interval_ms = {'1m': 60000, '3m': 180000, '5m': 300000, '15m': 900000}
    ms = interval_ms.get(interval, 60000)
    
    start_ts = int(start_date.timestamp() * 1000)
    end_ts = int(end_date.timestamp() * 1000)
    current = start_ts

    while current < end_ts:
        url = f"{BASE_URL}/fapi/v1/klines"
        params = {'symbol': symbol, 'interval': interval, 'startTime': current, 'limit': 1500}
        try:
            r = requests.get(url, params=params, timeout=15)
            data = r.json()
            if not data: break
            for k in data:
                all_candles.append([int(k[0])//1000, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])])
            current = int(data[-1][0]) + ms
            time.sleep(0.2)
        except:
            break

    df = pd.DataFrame(all_candles, columns=['time','open','high','low','close','volume'])
    df = df.drop_duplicates(subset='time').sort_values('time').reset_index(drop=True)
    df['datetime_utc'] = pd.to_datetime(df['time'], unit='s')
    df['datetime_ist'] = df['datetime_utc'] + timedelta(hours=5, minutes=30)
    df.set_index('datetime_ist', inplace=True)
    for c in ['open','high','low','close','volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


# ============================================================
# GAUSSIAN
# ============================================================
def gaussian_kernel(length):
    sigma = length / 6.0
    x = np.arange(length) - (length - 1) / 2.0
    kernel = np.exp(-0.5 * (x / sigma) ** 2)
    return kernel / kernel.sum()

def gaussian_smooth(series, length):
    kernel = gaussian_kernel(length)
    padded = np.concatenate([np.full(length-1, series.iloc[0]), series.values])
    smoothed = np.convolve(padded, kernel, mode='valid')
    return pd.Series(smoothed, index=series.index)

def compute_bands(df):
    close = df['close'].astype(float)
    high = df['high'].astype(float)
    low = df['low'].astype(float)
    basis = gaussian_smooth(close, GAUSSIAN_LENGTH)
    tr = pd.DataFrame({'hl': high-low, 'hc': (high-close.shift(1)).abs(), 'lc': (low-close.shift(1)).abs()}).max(axis=1)
    atr = tr.rolling(window=GAUSSIAN_LENGTH).mean()
    atr_smooth = gaussian_smooth(atr.fillna(0), GAUSSIAN_LENGTH)
    return basis, basis + atr_smooth * GAUSSIAN_DISTANCE, basis - atr_smooth * GAUSSIAN_DISTANCE

def detect_crossovers(df, basis, upper, lower):
    close = df['close'].astype(float)
    signals = []
    prev = None
    for i in range(1, len(df)):
        if pd.isna(upper.iloc[i]) or pd.isna(lower.iloc[i]): continue
        cc, pc = close.iloc[i], close.iloc[i-1]
        cu, pu = upper.iloc[i], upper.iloc[i-1]
        cl, pl = lower.iloc[i], lower.iloc[i-1]
        if pc <= pu and cc > cu and prev != 'above':
            signals.append({'index': i, 'time': df.index[i], 'signal': 'BUY', 'price': cc, 'upper': cu, 'lower': cl})
            prev = 'above'
        elif pc >= pl and cc < cl and prev != 'below':
            signals.append({'index': i, 'time': df.index[i], 'signal': 'SELL', 'price': cc, 'upper': cu, 'lower': cl})
            prev = 'below'
    return signals


# ============================================================
# SESSIONS
# ============================================================
def build_sessions(start, end, stype):
    sessions = []
    current = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while current <= end:
        dow = current.weekday()
        if dow < 5:
            if stype == 'ASIA':
                sessions.append((current.replace(hour=5, minute=30), current.replace(hour=13, minute=30)))
            elif stype == 'LONDON':
                sessions.append((current.replace(hour=13, minute=30), current.replace(hour=19, minute=0)))
            elif stype == 'NY':
                if dow == 0:
                    sessions.append((current.replace(hour=19, minute=45), (current+timedelta(days=1)).replace(hour=2, minute=0)))
                elif dow == 4:
                    sessions.append((current.replace(hour=19, minute=0), current.replace(hour=23, minute=30)))
                elif dow in [1,2,3]:
                    sessions.append((current.replace(hour=19, minute=0), (current+timedelta(days=1)).replace(hour=2, minute=0)))
        current += timedelta(days=1)
    return sessions

def is_in(ts, sessions):
    for s,e in sessions:
        if s <= ts <= e: return e
    return None


# ============================================================
# SIMULATE
# ============================================================
def simulate_config(df_m1, htf_signals_dict, config, start_date, end_date):
    capital = STARTING_CAPITAL
    trades = []
    position = None
    bias = {}
    total_fees = 0

    asia = build_sessions(start_date, end_date, 'ASIA')
    london = build_sessions(start_date, end_date, 'LONDON')
    ny = build_sessions(start_date, end_date, 'NY')

    # Build HTF events
    htf_events = {}
    for htf_name, sigs in htf_signals_dict.items():
        htf_events[htf_name] = {s['time']: s['signal'] for s in sigs}

    htf_sorted = {k: sorted(v.keys()) for k, v in htf_events.items()}
    htf_ptrs = {k: 0 for k in htf_sorted}

    # M1 signals for Asia
    m1_sigs = {}
    if 'm1_signals' in config:
        m1_sigs = {s['time']: s for s in config['m1_signals']}

    mom_threshold = config.get('momentum_pct', 0.05)

    for i in range(len(df_m1)):
        ct = df_m1.index[i]
        cc = float(df_m1['close'].iloc[i])

        # Update HTF biases
        for hname in htf_sorted:
            times = htf_sorted[hname]
            ptr = htf_ptrs[hname]
            while ptr < len(times) and times[ptr] <= ct:
                bias[hname] = htf_events[hname][times[ptr]]
                ptr += 1
            htf_ptrs[hname] = ptr

        # Session check
        sess_end = is_in(ct, asia)
        sess_name = 'ASIA' if sess_end else None
        if not sess_name:
            sess_end = is_in(ct, london)
            sess_name = 'LONDON' if sess_end else None
        if not sess_name:
            sess_end = is_in(ct, ny)
            sess_name = 'NY' if sess_end else None

        # Session end close
        if position and ct >= position['sess_end']:
            pnl_raw = capital * CAPITAL_PCT * LEVERAGE * ((cc - position['ep']) / position['ep']) if position['side'] == 'BUY' else capital * CAPITAL_PCT * LEVERAGE * ((position['ep'] - cc) / position['ep'])
            # Recalculate with position's size_inr
            if position['side'] == 'BUY':
                pnl_raw = position['size'] * (cc - position['ep']) / position['ep']
            else:
                pnl_raw = position['size'] * (position['ep'] - cc) / position['ep']
            fees = position['size'] * FEE_PER_SIDE * 2
            pnl = pnl_raw - fees
            total_fees += fees
            capital += pnl
            trades.append({'session': position['sess'], 'side': position['side'], 'ep': position['ep'], 'xp': cc, 'pnl': pnl, 'reason': 'SESSION_END', 'cap': capital})
            position = None
            continue

        if not sess_name:
            continue

        # Determine mode
        sess_cfg = config['sessions'].get(sess_name)
        if not sess_cfg:
            continue

        mode = sess_cfg['mode']
        htf_tf = sess_cfg['htf']

        # HTF bias flip close
        if position and position.get('bias_htf'):
            bh = position['bias_htf']
            if bh in bias and bias[bh] != position['side']:
                if position['side'] == 'BUY':
                    pnl_raw = position['size'] * (cc - position['ep']) / position['ep']
                else:
                    pnl_raw = position['size'] * (position['ep'] - cc) / position['ep']
                fees = position['size'] * FEE_PER_SIDE * 2
                pnl = pnl_raw - fees
                total_fees += fees
                capital += pnl
                trades.append({'session': position['sess'], 'side': position['side'], 'ep': position['ep'], 'xp': cc, 'pnl': pnl, 'reason': 'HTF_FLIP', 'cap': capital})
                position = None

        # HTF_ONLY mode
        if mode == 'HTF_ONLY':
            if htf_tf in htf_events and ct in htf_events[htf_tf]:
                sig = htf_events[htf_tf][ct]
                if position and position['side'] != sig:
                    if position['side'] == 'BUY':
                        pnl_raw = position['size'] * (cc - position['ep']) / position['ep']
                    else:
                        pnl_raw = position['size'] * (position['ep'] - cc) / position['ep']
                    fees = position['size'] * FEE_PER_SIDE * 2
                    pnl = pnl_raw - fees
                    total_fees += fees
                    capital += pnl
                    trades.append({'session': sess_name, 'side': position['side'], 'ep': position['ep'], 'xp': cc, 'pnl': pnl, 'reason': 'FLIP', 'cap': capital})
                    position = None

                if position is None and capital > 0:
                    size = capital * CAPITAL_PCT * LEVERAGE
                    position = {'side': sig, 'ep': cc, 'size': size, 'sess': sess_name, 'sess_end': sess_end, 'bias_htf': None}

        # HTF_BIAS_MOM mode (Asia)
        elif mode == 'HTF_BIAS_MOM':
            if htf_tf not in bias:
                continue
            b = bias[htf_tf]
            if ct in m1_sigs:
                s = m1_sigs[ct]
                if s['signal'] == b:
                    # Momentum check
                    if s['signal'] == 'BUY':
                        if s['price'] < s['upper'] * (1 + mom_threshold / 100):
                            continue
                    else:
                        if s['price'] > s['lower'] * (1 - mom_threshold / 100):
                            continue

                    if position:
                        if position['side'] == 'BUY':
                            pnl_raw = position['size'] * (cc - position['ep']) / position['ep']
                        else:
                            pnl_raw = position['size'] * (position['ep'] - cc) / position['ep']
                        fees = position['size'] * FEE_PER_SIDE * 2
                        pnl = pnl_raw - fees
                        total_fees += fees
                        capital += pnl
                        trades.append({'session': sess_name, 'side': position['side'], 'ep': position['ep'], 'xp': cc, 'pnl': pnl, 'reason': 'REENTRY', 'cap': capital})
                        position = None

                    if capital > 0:
                        size = capital * CAPITAL_PCT * LEVERAGE
                        position = {'side': s['signal'], 'ep': cc, 'size': size, 'sess': sess_name, 'sess_end': sess_end, 'bias_htf': htf_tf}

    # Close remaining
    if position:
        cc = float(df_m1['close'].iloc[-1])
        if position['side'] == 'BUY':
            pnl_raw = position['size'] * (cc - position['ep']) / position['ep']
        else:
            pnl_raw = position['size'] * (position['ep'] - cc) / position['ep']
        fees = position['size'] * FEE_PER_SIDE * 2
        pnl = pnl_raw - fees
        total_fees += fees
        capital += pnl
        trades.append({'session': 'END', 'side': position['side'], 'ep': position['ep'], 'xp': cc, 'pnl': pnl, 'reason': 'DATA_END', 'cap': capital})

    return trades, capital, total_fees


def print_results(name, trades, capital, fees):
    wins = sum(1 for t in trades if t['pnl'] >= 0)
    losses = sum(1 for t in trades if t['pnl'] < 0)
    wr = (wins/len(trades)*100) if trades else 0
    net = capital - STARTING_CAPITAL
    max_dd = 0
    peak = STARTING_CAPITAL
    for t in trades:
        if t['cap'] > peak: peak = t['cap']
        dd = (peak - t['cap'])/peak*100
        if dd > max_dd: max_dd = dd

    # Session breakdown
    sess_pnl = {}
    for t in trades:
        s = t['session']
        if s not in sess_pnl: sess_pnl[s] = {'trades': 0, 'pnl': 0}
        sess_pnl[s]['trades'] += 1
        sess_pnl[s]['pnl'] += t['pnl']

    print(f"\n  {name}")
    print(f"    Trades: {len(trades)} | W:{wins} L:{losses} | WR: {wr:.1f}%")
    print(f"    Final: ${capital:,.2f} | Net: ${net:+,.2f} ({net/STARTING_CAPITAL*100:+.1f}%) | Fees: ${fees:,.2f} | DD: {max_dd:.1f}%")
    for s, v in sorted(sess_pnl.items()):
        print(f"    {s}: {v['trades']}tr ${v['pnl']:+,.2f}")


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 80)
    print("VGB Bot v2 — TIMEFRAME COMPARISON (Apr 1-15, 2026)")
    print(f"Capital: ${STARTING_CAPITAL} | Leverage: {LEVERAGE}x | Fees: {FEE_PER_SIDE*100}%/side")
    print("=" * 80)

    # Fetch all timeframes
    print("\nFetching data...")
    # Use UTC times for Binance API
    start_utc = START_DATE - timedelta(hours=5, minutes=30)
    end_utc = END_DATE - timedelta(hours=5, minutes=30)

    df_m1 = fetch_binance_candles(SYMBOL, '1m', start_utc, end_utc)
    print(f"  M1: {len(df_m1)} candles")

    dfs = {}
    for tf in ['3m', '5m', '15m']:
        dfs[tf] = fetch_binance_candles(SYMBOL, tf, start_utc, end_utc)
        print(f"  {tf}: {len(dfs[tf])} candles")

    # Compute bands and crossovers for all TFs
    print("\nComputing crossovers...")
    sigs = {}
    for tf, df in dfs.items():
        b, u, l = compute_bands(df)
        s = detect_crossovers(df, b, u, l)
        sigs[tf] = s
        print(f"  {tf}: {len(s)} crossovers")

    b1, u1, l1 = compute_bands(df_m1)
    m1_signals = detect_crossovers(df_m1, b1, u1, l1)
    print(f"  M1: {len(m1_signals)} crossovers")

    # ============================================================
    # CONFIG 1: CURRENT (Asia M3→M1@0.05 | London M15 | NY M5)
    # ============================================================
    config_current = {
        'sessions': {
            'ASIA': {'mode': 'HTF_BIAS_MOM', 'htf': '3m'},
            'LONDON': {'mode': 'HTF_ONLY', 'htf': '15m'},
            'NY': {'mode': 'HTF_ONLY', 'htf': '5m'},
        },
        'm1_signals': m1_signals,
        'momentum_pct': 0.05,
    }

    # ============================================================
    # CONFIG 2: PROPOSED (Asia M3→M1@0.03 | London M5 | NY M3)
    # ============================================================
    config_proposed = {
        'sessions': {
            'ASIA': {'mode': 'HTF_BIAS_MOM', 'htf': '3m'},
            'LONDON': {'mode': 'HTF_ONLY', 'htf': '5m'},
            'NY': {'mode': 'HTF_ONLY', 'htf': '3m'},
        },
        'm1_signals': m1_signals,
        'momentum_pct': 0.03,
    }

    # ============================================================
    # CONFIG 3: MID (Asia M3→M1@0.03 | London M5 | NY M5)
    # ============================================================
    config_mid = {
        'sessions': {
            'ASIA': {'mode': 'HTF_BIAS_MOM', 'htf': '3m'},
            'LONDON': {'mode': 'HTF_ONLY', 'htf': '5m'},
            'NY': {'mode': 'HTF_ONLY', 'htf': '5m'},
        },
        'm1_signals': m1_signals,
        'momentum_pct': 0.03,
    }

    # ============================================================
    # CONFIG 4: AGGRESSIVE (Asia M3→M1@0.02 | London M3 | NY M3)
    # ============================================================
    config_aggressive = {
        'sessions': {
            'ASIA': {'mode': 'HTF_BIAS_MOM', 'htf': '3m'},
            'LONDON': {'mode': 'HTF_ONLY', 'htf': '3m'},
            'NY': {'mode': 'HTF_ONLY', 'htf': '3m'},
        },
        'm1_signals': m1_signals,
        'momentum_pct': 0.02,
    }

    # Run all
    print("\n" + "=" * 80)
    print("RESULTS")
    print("=" * 80)

    configs = [
        ("CURRENT: Asia M3→M1@0.05 | London M15 | NY M5", config_current),
        ("PROPOSED: Asia M3→M1@0.03 | London M5 | NY M3", config_proposed),
        ("MID: Asia M3→M1@0.03 | London M5 | NY M5", config_mid),
        ("AGGRESSIVE: Asia M3→M1@0.02 | London M3 | NY M3", config_aggressive),
    ]

    for name, cfg in configs:
        htf_sigs = {}
        for sess_cfg in cfg['sessions'].values():
            htf = sess_cfg['htf']
            if htf in sigs and htf not in htf_sigs:
                htf_sigs[htf] = sigs[htf]

        trades, capital, fees = simulate_config(df_m1, htf_sigs, cfg, START_DATE, END_DATE)
        print_results(name, trades, capital, fees)

    print("\n" + "=" * 80)


if __name__ == '__main__':
    main()