#!/usr/bin/env python3
"""
ORDER FLOW ENGINE — Stage 1 v2 (two separate single-stream connections)
========================================================================
Connects to Binance USDS-M Futures MAINNET via TWO separate WebSockets:
  - /ws/btcusdt@depth20@100ms  (top 20 partial book snapshots, every 100ms)
  - /ws/btcusdt@aggTrade        (every executed trade with aggressor side)

Single-stream endpoints send the data object directly (no {stream, data} wrapper),
which is more reliable than the combined-stream multiplexer.

Public market data — no API key needed. Zero risk.
"""

import asyncio
import json
import time
from collections import deque
from datetime import datetime
from threading import Thread, Lock
import websockets

# Two separate single-stream endpoints
WS_DEPTH = "wss://fstream.binance.com/ws/btcusdt@depth20@100ms"
WS_TRADE = "wss://fstream.binance.com/ws/btcusdt@aggTrade"

TOP_N_LEVELS = 5
DELTA_WINDOW_SECONDS = 300
TREND_RECENT_SECONDS = 60
RECONNECT_DELAY = 3


class OrderFlowEngine:
    """Real-time order flow state from Binance Futures public streams."""

    def __init__(self):
        self._bids = []
        self._asks = []
        self._book_lock = Lock()
        self._last_book_update = 0.0

        self._trades = deque()
        self._trade_lock = Lock()
        self._last_trade_time = 0.0

        self._msg_depth = 0
        self._msg_trade = 0
        self._msg_depth_bad = 0
        self._msg_trade_bad = 0

        self._running = False
        self._thread = None
        self._loop = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = Thread(target=self._run_loop, daemon=True, name="OrderFlow")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._loop and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main_loop())
        except Exception as e:
            if self._running:
                print(f"[FLOW] Loop ended unexpectedly: {e}")
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _main_loop(self):
        await asyncio.gather(
            self._stream_consumer(WS_DEPTH, self._on_depth_msg, "depth"),
            self._stream_consumer(WS_TRADE, self._on_trade_msg, "trade"),
        )

    async def _stream_consumer(self, url, handler, label):
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=15) as ws:
                    print(f"[FLOW] {label} stream connected")
                    async for msg in ws:
                        if not self._running:
                            return
                        try:
                            data = json.loads(msg)
                        except Exception:
                            continue
                        handler(data)
            except asyncio.CancelledError:
                return
            except Exception as e:
                if self._running:
                    print(f"[FLOW] {label} stream error: {e} — reconnecting in {RECONNECT_DELAY}s")
                    await asyncio.sleep(RECONNECT_DELAY)
                else:
                    return

    def _on_depth_msg(self, data):
        if self._msg_depth == 0:
            print(f"[FLOW] first depth msg keys: {list(data.keys())[:10]}")
        bids = data.get('b') or data.get('bids')
        asks = data.get('a') or data.get('asks')
        if not bids or not asks:
            self._msg_depth_bad += 1
            return
        try:
            new_bids = [[float(p), float(q)] for p, q in bids[:TOP_N_LEVELS]]
            new_asks = [[float(p), float(q)] for p, q in asks[:TOP_N_LEVELS]]
        except Exception:
            self._msg_depth_bad += 1
            return
        with self._book_lock:
            self._bids = new_bids
            self._asks = new_asks
            self._last_book_update = time.time()
        self._msg_depth += 1

    def _on_trade_msg(self, data):
        if self._msg_trade == 0:
            print(f"[FLOW] first trade msg: {data}")
        try:
            qty = float(data.get('q', 0))
            is_buyer_maker = bool(data.get('m', False))
            ts = data.get('T', 0) / 1000.0
        except Exception:
            self._msg_trade_bad += 1
            return
        signed = -qty if is_buyer_maker else qty
        with self._trade_lock:
            self._trades.append((ts, signed))
            self._last_trade_time = time.time()
            cutoff = ts - DELTA_WINDOW_SECONDS
            while self._trades and self._trades[0][0] < cutoff:
                self._trades.popleft()
        self._msg_trade += 1

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
        }


def main():
    print("=" * 70)
    print("ORDER FLOW ENGINE — Stage 1 v2 (separate streams)")
    print(f"Depth: {WS_DEPTH}")
    print(f"Trade: {WS_TRADE}")
    print(f"Duration: 2 minutes")
    print("=" * 70)

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
                  f"depth_msgs={snap['msg_depth']} trade_msgs={snap['msg_trade']}")
            last_print = time.time()
        time.sleep(0.5)

    engine.stop()
    time.sleep(0.5)
    print("\n" + "=" * 70)
    print("Stage 1 v2 complete.")
    print("Sanity expectations:")
    print("  - imbalance: typically 0.3 to 3.0 on BTCUSDT")
    print("  - delta5m: a few BTC +/- during normal flow")
    print("  - book_age: under 1 second")
    print("  - trade_age: under a few seconds during active trading")
    print("  - trade_msgs should grow rapidly (100s/min normally)")
    print("=" * 70)


if __name__ == '__main__':
    main()