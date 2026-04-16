"""
VGB Delta Bot v2.2 — Configuration
=====================================
Asia: M3 Bias → M1 + Momentum (0.04%)
London: M5 Crossover Only
NY: M3 Crossover Only

Breakeven protection: move SL to entry at +0.3% profit
All currency in USD
"""

# ============================================================
# EXCHANGE — BINANCE FUTURES
# ============================================================
EXCHANGE = 'binance'
BINANCE_TESTNET_URL = "https://testnet.binancefuture.com"
BINANCE_LIVE_URL = "https://fapi.binance.com"
BINANCE_API_KEY = ""
BINANCE_API_SECRET = ""
USE_TESTNET = True
SYMBOL = "BTCUSDT"
CANDLE_SOURCE = 'binance'

# ============================================================
# STRATEGY
# ============================================================
GAUSSIAN_LENGTH = 20    # Pine Script default (was 23 incorrectly)
GAUSSIAN_DISTANCE = 1
GAUSSIAN_MODE = 'AVG'  # 'AVG', 'MEDIAN', or 'MODE'
MOMENTUM_THRESHOLD_PCT = 0.04

# ============================================================
# SESSIONS (IST) — UPDATED TIMEFRAMES
# ============================================================
SESSIONS = {
    'ASIA': {
        'enabled': True,
        'start_hour': 5, 'start_min': 30,
        'end_hour': 13, 'end_min': 30,
        'mode': 'HTF_BIAS_MOM',
        'htf_timeframe': '3m',
        'entry_timeframe': '1m',
        'capital_pct': 0.25,
        'leverage': 25,
    },
    'LONDON': {
        'enabled': True,
        'start_hour': 13, 'start_min': 30,
        'end_hour': 19, 'end_min': 0,
        'mode': 'HTF_ONLY',
        'htf_timeframe': '5m',      # changed from 15m
        'entry_timeframe': '5m',     # changed from 15m
        'capital_pct': 0.25,
        'leverage': 25,
    },
    'NY': {
        'enabled': True,
        'start_hour': 19, 'start_min': 0,
        'end_hour': 2, 'end_min': 0,
        'monday_start_hour': 19, 'monday_start_min': 45,
        'friday_end_hour': 23, 'friday_end_min': 30,
        'mode': 'HTF_ONLY',
        'htf_timeframe': '3m',      # changed from 5m
        'entry_timeframe': '3m',     # changed from 5m
        'capital_pct': 0.25,
        'leverage': 25,
    },
}
TRADING_DAYS = [0, 1, 2, 3, 4]
NO_TRADE_DAYS = [5, 6]

# ============================================================
# RISK — BREAKEVEN PROTECTION
# ============================================================
SAFETY_SL_ENABLED = True
SAFETY_SL_PCT = 3.0  # disaster protection only

# Breakeven system: move SL to entry price when trade is +0.3% in profit
BREAKEVEN_ENABLED = True
BREAKEVEN_TRIGGER_PCT = 0.3  # activate when +0.3% in profit
BREAKEVEN_OFFSET_PCT = 0.02  # SL at entry + tiny buffer (0.02%) to cover fees

DEFAULT_CAPITAL_PCT = 0.25
DEFAULT_LEVERAGE = 25
MAX_NOTIONAL_USDT = 500_000

# ============================================================
# NEWS FILTER
# ============================================================
NEWS_FILTER_ENABLED = False
NEWS_BLOCK_MINUTES = 15
NEWS_CALENDAR_URL = "https://api.faireconomy.media/economic_calendar"
NEWS_CACHE_SECONDS = 3600
NEWS_HIGH_IMPACT = [
    'CPI', 'Consumer Price Index',
    'FOMC', 'Federal Funds Rate', 'Interest Rate Decision',
    'Non-Farm', 'NFP', 'Nonfarm Payrolls',
    'Fed Chair', 'Powell',
    'GDP', 'PPI', 'Unemployment Rate', 'Retail Sales',
]

# ============================================================
# SELF-REPAIR
# ============================================================
MAX_CONSECUTIVE_FAILURES = 5
SAFE_MODE_WAIT_SECONDS = 300
SAFE_MODE_MAX_RETRIES = 3
RECOVER_POSITION_ON_STARTUP = True
DAILY_RESTART_ENABLED = True
DAILY_RESTART_HOUR = 2
DAILY_RESTART_MINUTE = 30
MAX_CANDLE_AGE_SECONDS = 300
HEARTBEAT_INTERVAL = 300
HEARTBEAT_TELEGRAM_INTERVAL = 3600

# ============================================================
# CANDLE FETCH
# ============================================================
M1_CANDLE_BUFFER = 200    # needs 100 for SMA + 40 for filters
M3_CANDLE_BUFFER = 200
M5_CANDLE_BUFFER = 200
M15_CANDLE_BUFFER = 150

# ============================================================
# TELEGRAM
# ============================================================
TELEGRAM_ENABLED = True
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""
ALERT_ON_ENTRY = True
ALERT_ON_EXIT = True
ALERT_ON_BIAS_CHANGE = True
ALERT_ON_SESSION_CHANGE = True
ALERT_ON_ERROR = True
ALERT_ON_SL_HIT = True
ALERT_ON_NEWS_BLOCK = True
ALERT_ON_SELF_REPAIR = True

# ============================================================
# LOGGING
# ============================================================
LOG_FILE = "vgb_bot.log"
LOG_LEVEL = "INFO"
LOG_TRADES_TO_CSV = True
TRADE_LOG_FILE = "trade_log.csv"

# ============================================================
# EXECUTION
# ============================================================
ORDER_TYPE = 'market'
MAX_ORDER_RETRIES = 3
RETRY_DELAY_SECONDS = 2
CLOSE_AT_SESSION_END = True
ALLOW_RE_ENTRY = True

# ============================================================
# CURRENCY (display only — all values in USD)
# ============================================================
CURRENCY_SYMBOL = "$"

FEES = {
    'binance': {'taker': 0.0004, 'maker': 0.0002, 'gst': 0.0}
}