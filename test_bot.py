"""
VGB Delta Bot v2 — Connection Test (Binance)
"""

import config
from data_feed import CandleManager
from gaussian_engine import GaussianTracker
from session_manager import get_current_session, format_session_status, get_ist_now
from executor import get_balance, get_position, set_leverage, set_margin_type, get_server_time, get_ticker_price
from telegram_alerts import send_message
from news_filter import news_filter


def test_all():
    print("=" * 60)
    print("VGB BOT v2 — CONNECTION TEST")
    print(f"Exchange: Binance {'Testnet' if config.USE_TESTNET else 'LIVE'}")
    print(f"Symbol: {config.SYMBOL}")
    print("=" * 60)
    errors = []

    # 1. Session
    print("\n1. Session Check...")
    now = get_ist_now()
    print(f"   IST: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    sn, sc, se = get_current_session(now)
    print(f"   {format_session_status(sn, sc, se)}")
    print("   ✅ OK")

    # 2. Binance connectivity
    print("\n2. Binance API...")
    st = get_server_time()
    if st:
        print(f"   Server time: {st} ✅")
    else:
        print("   ❌ Cannot reach Binance")
        errors.append("Binance unreachable")

    price = get_ticker_price()
    if price:
        print(f"   BTC price: ${price:,.2f} ✅")
    else:
        print("   ❌ Ticker failed")
        errors.append("Ticker failed")

    # 3. Auth & Balance
    print("\n3. Authentication...")
    if not config.BINANCE_API_KEY:
        print("   ⚠️ API keys not set — fill config.py")
    else:
        bal = get_balance()
        if bal is not None:
            print(f"   Balance: ${bal:,.2f} USDT ✅")
        else:
            print("   ❌ Balance failed — check keys")
            errors.append("Auth failed")

        pos = get_position()
        print(f"   Position: {pos['side'] + ' ' + str(pos['size']) + ' BTC' if pos else 'None'}")

        set_margin_type('CROSSED')
        set_leverage(config.DEFAULT_LEVERAGE)

    # 4. Candles
    print("\n4. Candle Data...")
    mgr = CandleManager()
    for tf in ['1m', '3m', '5m', '15m']:
        df = mgr.get_candles(tf, force_refresh=True)
        if df is not None and len(df) > 0:
            print(f"   {tf}: {len(df)} candles | Close: ${float(df['close'].iloc[-1]):,.1f}")
        else:
            print(f"   {tf}: ❌ FAILED")
            errors.append(f"{tf} fetch failed")

    # 5. Gaussian
    print("\n5. Gaussian Bands...")
    for tf in ['1m', '5m', '15m']:
        df = mgr.get_candles(tf, force_refresh=True)
        if df is not None:
            t = GaussianTracker(tf)
            sig, det = t.update(df)
            b, u, l = t.get_current_bands()
            if b:
                print(f"   {tf}: B={b:.1f} U={u:.1f} L={l:.1f} | Bias={t.get_bias()}")
                if sig:
                    print(f"        ⚡ CROSSOVER: {sig}")

    # 6. News filter
    print("\n6. News Filter...")
    blocked, reason = news_filter.is_blocked()
    if blocked:
        print(f"   🚫 BLOCKED: {reason}")
    else:
        print("   ✅ No news block active")

    # 7. Telegram
    print("\n7. Telegram...")
    if config.TELEGRAM_ENABLED and config.TELEGRAM_BOT_TOKEN:
        send_message("🧪 VGB Bot v2 test — Binance connected!")
        print("   Message sent — check Telegram")
    else:
        print("   ⚠️ Not configured")

    # Summary
    print("\n" + "=" * 60)
    if errors:
        print(f"❌ {len(errors)} ERRORS:")
        for e in errors:
            print(f"   - {e}")
    else:
        print("✅ ALL CHECKS PASSED")
    print("=" * 60)

    print(f"\nSessions:")
    for sn, sc in config.SESSIONS.items():
        print(f"  {sn}: {'ON' if sc['enabled'] else 'OFF'} | {sc['mode']} | HTF:{sc['htf_timeframe']} Entry:{sc['entry_timeframe']}")
    print(f"\nRun: python3 main.py")


if __name__ == '__main__':
    test_all()
