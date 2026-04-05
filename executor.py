"""
VGB Delta Bot v2 — Binance Futures Executor
==============================================
Handles all Binance USDT-M Futures API calls:
- HMAC-SHA256 authentication
- Balance, position queries
- Market orders, SL placement
- Position close/flip
- Leverage setting
"""

import requests
import hmac
import hashlib
import time as _time
from urllib.parse import urlencode
import config


def _get_base_url():
    return config.BINANCE_TESTNET_URL if config.USE_TESTNET else config.BINANCE_LIVE_URL


def _sign(params):
    """Add timestamp and HMAC signature to params."""
    params['timestamp'] = int(_time.time() * 1000)
    query = urlencode(params)
    signature = hmac.new(
        config.BINANCE_API_SECRET.encode('utf-8'),
        query.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    params['signature'] = signature
    return params


def _headers():
    return {
        'X-MBX-APIKEY': config.BINANCE_API_KEY,
        'Content-Type': 'application/x-www-form-urlencoded'
    }


def _get(path, params=None):
    """Authenticated GET request."""
    if params is None:
        params = {}
    params = _sign(params)
    url = f"{_get_base_url()}{path}"
    try:
        r = requests.get(url, params=params, headers=_headers(), timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[EXEC] GET {path} error: {e}")
        return None


def _post(path, params=None):
    """Authenticated POST request."""
    if params is None:
        params = {}
    params = _sign(params)
    url = f"{_get_base_url()}{path}"
    try:
        r = requests.post(url, data=params, headers=_headers(), timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        error_body = e.response.text if e.response else str(e)
        print(f"[EXEC] POST {path} HTTP error: {error_body}")
        return {'error': error_body}
    except Exception as e:
        print(f"[EXEC] POST {path} error: {e}")
        return None


def _delete(path, params=None):
    """Authenticated DELETE request."""
    if params is None:
        params = {}
    params = _sign(params)
    url = f"{_get_base_url()}{path}"
    try:
        r = requests.delete(url, params=params, headers=_headers(), timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[EXEC] DELETE {path} error: {e}")
        return None


# ============================================================
# ACCOUNT
# ============================================================
def get_balance():
    """Get USDT futures wallet balance."""
    data = _get('/fapi/v2/balance')
    if data:
        for asset in data:
            if asset.get('asset') == 'USDT':
                return float(asset.get('availableBalance', 0))
    return None


def get_account_info():
    """Get full account info including positions."""
    return _get('/fapi/v2/account')


# ============================================================
# POSITION
# ============================================================
def get_position():
    """Get current open position for BTCUSDT."""
    data = _get('/fapi/v2/positionRisk', {'symbol': config.SYMBOL})
    if data:
        for pos in data:
            if pos.get('symbol') == config.SYMBOL:
                amt = float(pos.get('positionAmt', 0))
                if amt != 0:
                    return {
                        'side': 'BUY' if amt > 0 else 'SELL',
                        'size': abs(amt),
                        'entry_price': float(pos.get('entryPrice', 0)),
                        'unrealized_pnl': float(pos.get('unRealizedProfit', 0)),
                        'leverage': int(pos.get('leverage', 25)),
                        'mark_price': float(pos.get('markPrice', 0))
                    }
    return None


# ============================================================
# ORDERS
# ============================================================
def calculate_order_size(capital_usdt, capital_pct, leverage, btc_price):
    """
    Calculate order quantity in BTC.
    Binance BTCUSDT futures: quantity in BTC, min 0.001
    """
    notional = capital_usdt * capital_pct * leverage

    if notional > config.MAX_NOTIONAL_USDT:
        notional = config.MAX_NOTIONAL_USDT

    quantity = notional / btc_price
    quantity = round(quantity, 3)  # Binance BTC precision = 3 decimals
    return max(0.001, quantity)


def place_market_order(side, quantity, reduce_only=False):
    """Place a market order."""
    params = {
        'symbol': config.SYMBOL,
        'side': side.upper(),
        'type': 'MARKET',
        'quantity': str(quantity),
    }
    if reduce_only:
        params['reduceOnly'] = 'true'

    for attempt in range(config.MAX_ORDER_RETRIES):
        result = _post('/fapi/v1/order', params.copy())
        if result and 'orderId' in result:
            return {
                'success': True,
                'order_id': result['orderId'],
                'side': side,
                'size': quantity,
                'price': float(result.get('avgPrice', 0)),
                'status': result.get('status', 'unknown')
            }
        elif result and 'error' in result:
            print(f"[EXEC] Order failed (attempt {attempt+1}): {result['error']}")
        else:
            print(f"[EXEC] Order returned None (attempt {attempt+1})")

        if attempt < config.MAX_ORDER_RETRIES - 1:
            _time.sleep(config.RETRY_DELAY_SECONDS)

    return {'success': False, 'error': 'Max retries exceeded'}


def place_stop_market(side, quantity, stop_price):
    """Place a stop market order (for safety SL)."""
    params = {
        'symbol': config.SYMBOL,
        'side': side.upper(),
        'type': 'STOP_MARKET',
        'quantity': str(quantity),
        'stopPrice': str(round(stop_price, 2)),
        'reduceOnly': 'true',
        'workingType': 'MARK_PRICE',
    }
    result = _post('/fapi/v1/order', params)
    if result and 'orderId' in result:
        return {'success': True, 'order_id': result['orderId']}
    return {'success': False, 'error': str(result)}


def cancel_all_orders():
    """Cancel all open orders for the symbol."""
    result = _delete('/fapi/v1/allOpenOrders', {'symbol': config.SYMBOL})
    if result:
        print(f"[EXEC] All orders cancelled")
        return True
    return False


def get_open_orders():
    """Get all open orders."""
    return _get('/fapi/v1/openOrders', {'symbol': config.SYMBOL}) or []


# ============================================================
# POSITION MANAGEMENT
# ============================================================
def close_position():
    """Close any open position."""
    pos = get_position()
    if pos is None:
        return True

    close_side = 'SELL' if pos['side'] == 'BUY' else 'BUY'
    result = place_market_order(close_side, pos['size'], reduce_only=True)
    return result.get('success', False)


def open_position(side, capital_usdt, capital_pct, leverage, btc_price):
    """Open a new position with safety SL."""
    quantity = calculate_order_size(capital_usdt, capital_pct, leverage, btc_price)
    print(f"[EXEC] Opening {side} | Qty: {quantity} BTC | Capital: ${capital_usdt:.2f}")

    result = place_market_order(side, quantity)

    if result.get('success') and config.SAFETY_SL_ENABLED:
        entry_price = result.get('price', btc_price)
        if entry_price == 0:
            entry_price = btc_price

        if side == 'BUY':
            sl_price = entry_price * (1 - config.SAFETY_SL_PCT / 100)
            sl_side = 'SELL'
        else:
            sl_price = entry_price * (1 + config.SAFETY_SL_PCT / 100)
            sl_side = 'BUY'

        sl_result = place_stop_market(sl_side, quantity, sl_price)
        if sl_result.get('success'):
            print(f"[EXEC] Safety SL at ${sl_price:.2f}")
        else:
            print(f"[EXEC] WARNING: SL failed: {sl_result.get('error')}")

    return result


def flip_position(new_side, capital_usdt, capital_pct, leverage, btc_price):
    """Close current and open opposite."""
    cancel_all_orders()
    _time.sleep(0.5)
    close_position()
    _time.sleep(0.5)
    return open_position(new_side, capital_usdt, capital_pct, leverage, btc_price)


# ============================================================
# LEVERAGE
# ============================================================
def set_leverage(leverage):
    """Set leverage for the symbol."""
    result = _post('/fapi/v1/leverage', {
        'symbol': config.SYMBOL,
        'leverage': leverage
    })
    if result and 'leverage' in result:
        print(f"[EXEC] Leverage set to {result['leverage']}x")
        return True
    print(f"[EXEC] Leverage set failed: {result}")
    return False


def set_margin_type(margin_type='CROSSED'):
    """Set margin type (CROSSED or ISOLATED)."""
    result = _post('/fapi/v1/marginType', {
        'symbol': config.SYMBOL,
        'marginType': margin_type
    })
    if result:
        if 'code' in result and result['code'] == -4046:
            print(f"[EXEC] Margin type already {margin_type}")
            return True
        print(f"[EXEC] Margin type set to {margin_type}")
        return True
    return False


# ============================================================
# MARKET DATA (public, no auth needed)
# ============================================================
def get_ticker_price():
    """Get current BTC price."""
    url = f"{_get_base_url()}/fapi/v1/ticker/price"
    try:
        r = requests.get(url, params={'symbol': config.SYMBOL}, timeout=5)
        data = r.json()
        return float(data.get('price', 0))
    except:
        return None


def get_server_time():
    """Check server connectivity and time."""
    url = f"{_get_base_url()}/fapi/v1/time"
    try:
        r = requests.get(url, timeout=5)
        return r.json().get('serverTime')
    except:
        return None
