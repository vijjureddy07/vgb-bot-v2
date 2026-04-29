#!/usr/bin/env python3
"""
ORDER FLOW ENGINE — Stage 1 v3 (depth via WS, trades via REST polling)
=======================================================================
Hybrid approach after WS aggTrade stream proved unreliable from both
local and GCP IPs:

  - Depth:  WebSocket  wss://fstream.binance.com/ws/btcusdt@depth20@100ms
  - Trades: REST poll  https://fapi.binance.com/fapi/v1/aggTrades

Trades are pulled every 2 seconds via REST. We track the last seen trade
ID to avoid duplicates. Lag is 2-3 seconds — acceptable since the bot
acts on M3 bar closes (3-min cadence), not tick-by-tick.

Public market data — no API key needed. Zero risk.
"""

import asyncio
import json
import time
from collections import deque
from datetime import datetime
from threading import Thread, Lock
import requests
import websockets

WS_DEPTH = "wss://fstream.binance.com/ws/btcusdt@depth20@100ms"
REST_TRADES = "https://fapi.binance.com/fapi/v1/aggTrades"
SYMBOL = "BTCUSDT"

TOP_N_LEVELS = 5
DELTA_WINDOW_SECONDS = 300   # 5 minutes
TREND_RECENT_SECONDS = 60    # last 1 min vs prior 4 min for trend
WS_RECONNECT_DELAY = 3
TRADE_POLL_INTERVAL = 2.0    # seconds between REST polls
TRADE_POLL_LIMIT = 1000      # max trades per poll (Binance allows 1000)


class OrderFlowEngine:
    """Real-time order flow state — depth via WS, trades via REST polling."""

    def __init__(self):
        # Order book state
        self._bids = []
        self._asks = []
        self._book_lock = Lock()
        self._last_book_update = 0.0

        # Trade tape state
        self._trades = deque()
        self._trade_lock = Lock()
        self._last_trade_time = 0.0
        self._last_trade_id = 0  # for REST pagination

        # Diagnostics
        self._msg_depth = 0
        self._msg_trade = 0
        self._poll_count = 0
        self._poll_errors = 0

        self._running = False
        self._ws_thread = None
        self._poll_thread = None
        self._loop = None

    # =====================================================================
    # Lifecycle
    # =====================================================================
    def start(self):
        if self._running:
            return
        self._running = True
        self._ws_thread = Thread(target=self._run_ws_loop, daemon=True, name="OrderFlowWS")
        self._poll_thread = Thread(target=self._run_poll_loop, daemon=True, name="OrderFlowPoll")
        self._ws_thread.start()
        self._poll_thread.start()

    def stop(self):
        self._running = False
        if self._loop and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
        if self._ws_thread:
            self._ws_thread.join(timeout=5)
        if self._poll_thread:
            self._poll_thread.join(timeout=5)

    # =====================================================================
    # WebSocket: depth stream
    # =====================================================================
    def _run_ws_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._depth_consumer())
        except Exception as e:
            if self._running:
                print(f"[FLOW] depth loop ended: {e}")
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _depth_consumer(self):
        while self._running:
            try:
                async with websockets.connect(WS_DEPTH, ping_interval=20, ping_timeout=15) as ws:
                    print(f"[FLOW] depth WS connected")
                    async for msg in ws:
                        if not self._running:
                            return
                        try:
                            data = json.loads(msg)
                        except Exception:
                            continue
                        self._on_depth(data)
            except asyncio.CancelledError:
                return
            except Exception as e:
                if self._running:
                    print(f"[FLOW] depth WS error: {e} — reconnecting in {WS_RECONNECT_DELAY}s")
                    await asyncio.sleep(WS_RECONNECT_DELAY)
                else:
                    return

    def _on_depth(self, data):
        if self._msg_depth == 0:
            print(f"[FLOW] first depth msg keys: {list(data.keys())[:10]}")
        bids = data.get('b') or data.get('bids')
        asks = data.get('a') or data.get('asks')
        if not bids or not asks:
            return
        try:
            new_bids = [[float(p), float(q)] for p, q in bids[:TOP_N_LEVELS]]
            new_asks = [[float(p), float(q)] for p, q in asks[:TOP_N_LEVELS]]
        except Exception:
            return
        with self._book_lock:
            self._bids = new_bids
            self._asks = new_asks
            self._last_book_update = time.time()
        self._msg_depth += 1

    # =====================================================================
    # REST polling: trades
    # =====================================================================
    def _run_poll_loop(self):
        """Poll /fapi/v1/aggTrades every TRADE_POLL_INTERVAL seconds."""
        # First poll: just get latest trade ID to anchor (don't import history)
        try:
            r = requests.get(REST_TRADES,
                             params={'symbol': SYMBOL, 'limit': 1},
                             timeout=5)
            data = r.json()
            if isinstance(data, list) and data:
                self._last_trade_id = int(data[-1]['a'])
                print(f"[FLOW] trade poll initialized at trade_id={self._last_trade_id}")
        except Exception as e:
            print(f"[FLOW] trade poll init failed: {e}")

        while self._running:
            start = time.time()
            try:
                params = {'symbol': SYMBOL, 'limit': TRADE_POLL_LIMIT}
                if self._last_trade_id:
                    params['fromId'] = self._last_trade_id + 1
                r = requests.get(REST_TRADES, params=params, timeout=5)
                self._poll_count += 1
                if r.status_code == 200:
                    trades = r.json()
                    if isinstance(trades, list) and trades:
                        if self._msg_trade == 0:
                            print(f"[FLOW] first trade batch: {len(trades)} trades, "
                                  f"sample={trades[-1]}")
                        for t in trades:
                            self._on_trade(t)
                        self._last_trade_id = int(trades[-1]['a'])
                else:
                    self._poll_errors += 1
                    if self._poll_errors <= 3:
                        print(f"[FLOW] trade poll HTTP {r.status_code}: {r.text[:200]}")
            except Exception as e:
                self._poll_errors += 1
                if self._poll_errors <= 3:
                    print(f"[FLOW] trade poll error: {e}")

            # Sleep until next poll, accounting for elapsed time
            elapsed = time.time() - start
            sleep_for = max(0.1, TRADE_POLL_INTERVAL - elapsed)
            if not self._running:
                return
            time.sleep(sleep_for)

    def _on_trade(self, t):
        try:
            qty = float(t.get('q', 0))
            is_buyer_maker = bool(t.get('m', False))
            ts = t.get('T', 0) / 1000.0
        except Exception:
            return
        # m=True -> buyer is maker -> aggressor was SELLER -> negative
        signed = -qty if is_buyer_maker else qty
        with self._trade_lock:
            self._trades.append((ts, signed))
            self._last_trade_time = time.time()
            cutoff = ts - DELTA_WINDOW_SECONDS
            while self._trades and self._trades[0][0] < cutoff:
                self._trades.popleft()
        self._msg_trade += 1

    # =====================================================================
    # Computed metrics
    # =====================================================================
    def _imbalance(self):
        with self._book_lock:
            bid_sum = sum(q for _, q in self._bids)
            ask_sum = sum(q for _, q in self._asks)
        if ask_sum <= 0:
            return None
        return bid_sum / ask_sum

    def _delta_5m(self):
        with self._trade_lock:
            return sum(s for _, s in self._trades)

    def _delta_trend(self):
        if not self._trades:
            return 'flat'
        now_ts = self._trades[-1][0]
        recent_cutoff = now_ts - TREND_RECENT_SECONDS
        with self._trade_lock:
            recent = sum(s for ts, s in self._trades if ts >= recent_cutoff)
            older = sum(s for ts, s in self._trades if ts < recent_cutoff)
        recent_rate = recent / max(TREND_RECENT_SECONDS, 1)
        older_rate = older / max(DELTA_WINDOW_SECONDS - TREND_RECENT_SECONDS, 1)
        diff = recent_rate - older_rate
        if diff > 0.05:
            return 'rising'
        if diff < -0.05:
            return 'falling'
        return 'flat'

    def get_snapshot(self):
        now = time.time()
        return {
            'timestamp':       now,
            'imbalance':       self._imbalance(),
            'delta_5m':        self._delta_5m(),
            'delta_trend':     self._delta_trend(),
            'book_age_sec':    now - self._last_book_update if self._last_book_update else None,
            'trade_age_sec':   now - self._last_trade_time if self._last_trade_time else None,
            'trade_count_5m':  len(self._trades),
            'top_bid':         self._bids[0][0] if self._bids else None,
            'top_ask':         self._asks[0][0] if self._asks else None,
            'msg_depth':       self._msg_depth,
            'msg_trade':       self._msg_trade,
            'poll_count':      self._poll_count,
            'poll_errors':     self._poll_errors,
        }


# ============================================================
# OrderFlowFilter — scoring + decision for crossover signals
# ============================================================
# Scoring (delta-only, tight rules):
#   +1 if delta sign matches signal direction
#   +1 if delta trend matches signal direction (NOT 'flat')
# Score: 0, 1, or 2
#
# Observation mode: bot takes ALL signals regardless of score.
# Filter decision is logged for post-hoc analysis.
# ============================================================

MIN_TRADES_FOR_FILTER = 50  # need at least this many trades in window to score

def score_signal(signal_side: str, snapshot: dict) -> dict:
    """
    Score a crossover signal against current order flow state.
    Returns dict with: score (0-2), decision (str), reason (str), ready (bool).
    """
    side = signal_side.upper()
    delta = snapshot.get('delta_5m')
    trend = snapshot.get('delta_trend')
    n_trades = snapshot.get('trade_count_5m', 0)

    # Filter not ready yet (just started up)
    if n_trades < MIN_TRADES_FOR_FILTER or delta is None:
        return {
            'score':    None,
            'decision': 'NOT_READY',
            'reason':   f'only {n_trades} trades in window',
            'ready':    False,
        }

    score = 0
    reasons = []

    # Component 1: delta sign matches
    if side == 'BUY' and delta > 0:
        score += 1
        reasons.append('delta+')
    elif side == 'SELL' and delta < 0:
        score += 1
        reasons.append('delta-')
    else:
        reasons.append('delta_against')

    # Component 2: trend matches (TIGHT — flat does not count)
    if side == 'BUY' and trend == 'rising':
        score += 1
        reasons.append('trend_rising')
    elif side == 'SELL' and trend == 'falling':
        score += 1
        reasons.append('trend_falling')
    else:
        reasons.append(f'trend_{trend}')

    # Decision mapping
    if score == 2:
        decision = 'TAKE_FULL'
    elif score == 1:
        decision = 'TAKE_WEAK'
    else:
        decision = 'WOULD_BLOCK'

    return {
        'score':    score,
        'decision': decision,
        'reason':   '|'.join(reasons),
        'ready':    True,
    }


def log_flow_decision(csv_path: str, timestamp_str: str, signal_side: str,
                      signal_price: float, snapshot: dict, score_result: dict,
                      actual_action: str = 'TAKE'):
    """
    Append one row to flow_log.csv. Creates header if file doesn't exist.
    """
    import os, csv
    file_exists = os.path.isfile(csv_path)
    with open(csv_path, 'a', newline='') as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow([
                'timestamp', 'signal_side', 'signal_price',
                'imbalance', 'delta_5m', 'delta_trend', 'trade_count_5m',
                'score', 'filter_decision', 'reason', 'actual_action',
            ])
        w.writerow([
            timestamp_str, signal_side, f"{signal_price:.2f}",
            f"{snapshot.get('imbalance'):.3f}" if snapshot.get('imbalance') is not None else '',
            f"{snapshot.get('delta_5m'):.4f}" if snapshot.get('delta_5m') is not None else '',
            snapshot.get('delta_trend', ''),
            snapshot.get('trade_count_5m', 0),
            score_result.get('score', ''),
            score_result.get('decision', ''),
            score_result.get('reason', ''),
            actual_action,
        ])


# ============================================================
def main():
    print("=" * 75)
    print("ORDER FLOW ENGINE — Stage 1 v3 (WS depth + REST trade polling)")
    print(f"Depth WS:    {WS_DEPTH}")
    print(f"Trade REST:  {REST_TRADES} (poll every {TRADE_POLL_INTERVAL}s)")
    print(f"Duration: 2 minutes")
    print("=" * 75)

    engine = OrderFlowEngine()
    engine.start()

    print("\nWaiting 10 seconds for initial data...")
    time.sleep(10)

    end_time = time.time() + 120
    last_print = 0
    while time.time() < end_time:
        if time.time() - last_print >= 5:
            snap = engine.get_snapshot()
            ts = datetime.fromtimestamp(snap['timestamp']).strftime("%H:%M:%S")
            imb = snap['imbalance']
            d5m = snap['delta_5m']
            trend = snap['delta_trend']
            book_age = snap['book_age_sec']
            trade_age = snap['trade_age_sec']
            trades = snap['trade_count_5m']
            top_bid = snap['top_bid']
            top_ask = snap['top_ask']

            imb_s = f"{imb:.2f}" if imb is not None else "n/a"
            d5m_s = f"{d5m:+.3f}" if d5m is not None else "n/a"
            book_s = f"{book_age:.1f}s" if book_age is not None else "—"
            trade_s = f"{trade_age:.1f}s" if trade_age is not None else "—"
            spread = (top_ask - top_bid) if (top_bid and top_ask) else None
            spread_s = f"${spread:.2f}" if spread is not None else "—"

            print(f"[{ts}] imb={imb_s}  d5m={d5m_s}BTC ({trend})  "
                  f"trades_5m={trades}  spread={spread_s}  "
                  f"book/trade_age={book_s}/{trade_s}  "
                  f"depth={snap['msg_depth']} trades={snap['msg_trade']} "
                  f"polls={snap['poll_count']}/err={snap['poll_errors']}")
            last_print = time.time()
        time.sleep(0.5)

    engine.stop()
    time.sleep(0.5)
    print("\n" + "=" * 75)
    print("Stage 1 v3 complete.")
    print("Sanity expectations:")
    print("  - imbalance: typically 0.3 to 3.0 on BTCUSDT")
    print("  - delta5m: a few BTC +/- during normal flow")
    print("  - book_age: under 1 second")
    print("  - trade_age: under 3 seconds (we poll every 2s)")
    print("  - trades counter should grow rapidly")
    print("  - poll_errors should be 0 (or very low)")
    print("=" * 75)


if __name__ == '__main__':
    main()