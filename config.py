"""
VGB Bot v3.0 — Configuration
=============================
NY-only | OLD Gaussian (L=23, 1 filter, sigma=L/6, ATR) | 35x / 25% | No BE | M3 flip

Preserves EVERY config key read by v2 executor / watchdog / balance_monitor /
weekly_report / news_filter / telegram_alerts / kill_switch so none of those
files need changes.

Secrets (BINANCE_API_KEY / BINANCE_API_SECRET / TELEGRAM_*) are BLANK here on
purpose — they are filled in directly on the GCP VM and never committed to git.
"""

# ============================================================
# EXCHANGE — BINANCE FUTURES
# ============================================================
EXCHANGE = 'binance'
BINANCE_TESTNET_URL = "https://testnet.binancefuture.com"
BINANCE_LIVE_URL    = "https://fapi.binance.com"

BINANCE_API_KEY    = ""     # FILL ON VM, NEVER COMMIT
BINANCE_API_SECRET = ""     # FILL ON VM, NEVER COMMIT

USE_TESTNET = True          # Flip to False on VM for real money

SYMBOL = "BTCUSDT"
CANDLE_SOURCE = 'binance'

# ============================================================
# VERSION
# ============================================================
BOT_VERSION = "3.0.0"
BOT_NAME    = "VGB Bot v3 — NY M3 Flip"

# ============================================================
# STRATEGY — GAUSSIAN (OLD ENGINE)
# v2 used 20 (Pine default). v3 reverts to 23 per backtest winner.
# ============================================================
GAUSSIAN_LENGTH   = 23
GAUSSIAN_DISTANCE = 1
GAUSSIAN_MODE     = 'OLD'   # v3 engine always uses OLD single-filter + ATR
MOMENTUM_THRESHOLD_PCT = 0.04   # unused in v3, kept for v2 compat

# ============================================================
# SESSIONS — v3 only trades NY
# v2 SESSIONS dict preserved for any file that reads it; ASIA/LONDON disabled.
# v3 session_manager uses NY_WINDOWS_BY_WEEKDAY below.
# ============================================================
SESSIONS = {
    'ASIA': {
        'enabled':         False,
        'start_hour': 5,  'start_min': 30,
        'end_hour':   13, 'end_min':   30,
        'mode':             'HTF_BIAS_MOM',
        'htf_timeframe':    '3m',
        'entry_timeframe':  '1m',
        'capital_pct':      0.25,
        'leverage':         35,
    },
    'LONDON': {
        'enabled':         False,
        'start_hour': 13, 'start_min': 30,
        'end_hour':   19, 'end_min':    0,
        'mode':             'HTF_ONLY',
        'htf_timeframe':    '5m',
        'entry_timeframe':  '5m',
        'capital_pct':      0.25,
        'leverage':         35,
    },
    'NY': {
        'enabled':              True,
        'start_hour':           18, 'start_min': 30,
        'end_hour':              1, 'end_min':    0,
        'monday_start_hour':    18, 'monday_start_min': 30,
        'friday_end_hour':      23, 'friday_end_min':   30,
        'mode':             'HTF_ONLY',
        'htf_timeframe':    '3m',
        'entry_timeframe':  '3m',
        'capital_pct':      0.25,
        'leverage':         35,
    },
}

TRADING_DAYS  = [0, 1, 2, 3, 4]
NO_TRADE_DAYS = [5, 6]

# v3-specific NY window table used by v3 session_manager.
# (start_hour, start_min, end_hour, end_min) — end_hour >= 24 means next day.
NY_WINDOWS_BY_WEEKDAY = {
    0: (18, 30, 25, 0),     # Mon 18:30 -> Tue 01:00
    1: (18, 30, 25, 0),     # Tue 18:30 -> Wed 01:00
    2: (18, 30, 25, 0),     # Wed 18:30 -> Thu 01:00
    3: (18, 30, 25, 0),     # Thu 18:30 -> Fri 01:00
    4: (18, 30, 23, 30),    # Fri 18:30 -> 23:30
}

# ============================================================
# RISK
# ============================================================
SAFETY_SL_ENABLED = True
SAFETY_SL_PCT     = 3.0     # percent — v2 executor expects percent, NOT fraction

# Breakeven DISABLED in v3 — proven to hurt (handoff Section 2)
BREAKEVEN_ENABLED     = False
BREAKEVEN_TRIGGER_PCT = 0.3
BREAKEVEN_OFFSET_PCT  = 0.02

DEFAULT_CAPITAL_PCT = 0.25
DEFAULT_LEVERAGE    = 35            # was 25 in v2
MAX_NOTIONAL_USDT   = 500_000

# ============================================================
# NEWS FILTER
# ============================================================
NEWS_FILTER_ENABLED = False
NEWS_BLOCK_MINUTES  = 15
NEWS_CALENDAR_URL   = "https://api.faireconomy.media/economic_calendar"
NEWS_CACHE_SECONDS  = 3600
NEWS_HIGH_IMPACT = [
    'CPI', 'Consumer Price Index',
    'FOMC', 'Federal Funds Rate', 'Interest Rate Decision',
    'Non-Farm', 'NFP', 'Nonfarm Payrolls',
    'Fed Chair', 'Powell',
    'GDP', 'PPI', 'Unemployment Rate', 'Retail Sales',
]

# ============================================================
# SELF-REPAIR / WATCHDOG
# ============================================================
MAX_CONSECUTIVE_FAILURES = 5
SAFE_MODE_WAIT_SECONDS   = 300
SAFE_MODE_MAX_RETRIES    = 3
RECOVER_POSITION_ON_STARTUP = True

DAILY_RESTART_ENABLED = True
DAILY_RESTART_HOUR    = 2
DAILY_RESTART_MINUTE  = 30

MAX_CANDLE_AGE_SECONDS      = 300
HEARTBEAT_INTERVAL          = 300
HEARTBEAT_TELEGRAM_INTERVAL = 3600

# ============================================================
# CANDLE BUFFERS
# ============================================================
M1_CANDLE_BUFFER  = 200
M3_CANDLE_BUFFER  = 200
M5_CANDLE_BUFFER  = 200
M15_CANDLE_BUFFER = 150

# ============================================================
# TELEGRAM
# ============================================================
TELEGRAM_ENABLED   = True
TELEGRAM_BOT_TOKEN = ""     # FILL ON VM, NEVER COMMIT
TELEGRAM_CHAT_ID   = ""     # FILL ON VM, NEVER COMMIT

ALERT_ON_ENTRY           = True
ALERT_ON_EXIT            = True
ALERT_ON_BIAS_CHANGE     = True
ALERT_ON_SESSION_CHANGE  = True
ALERT_ON_ERROR           = True
ALERT_ON_SL_HIT          = True
ALERT_ON_NEWS_BLOCK      = True
ALERT_ON_SELF_REPAIR     = True

# ============================================================
# LOGGING
# ============================================================
LOG_FILE          = "vgb_bot.log"
LOG_LEVEL         = "INFO"
LOG_TRADES_TO_CSV = True
TRADE_LOG_FILE    = "trade_log.csv"

# ============================================================
# EXECUTION
# ============================================================
ORDER_TYPE            = 'market'
MAX_ORDER_RETRIES     = 3
RETRY_DELAY_SECONDS   = 2
CLOSE_AT_SESSION_END  = True
ALLOW_RE_ENTRY        = False       # v3 does NOT re-enter on M1

# ============================================================
# CURRENCY
# ============================================================
CURRENCY_SYMBOL = "$"
FEES = {
    'binance': {'taker': 0.0004, 'maker': 0.0002, 'gst': 0.0}
}

# ============================================================
# V3 ADDITIONS (new knobs read only by v3 code)
# ============================================================
AUTO_PAUSE_BALANCE_FLOOR_USD = 70.0
MAX_CLOCK_DRIFT_MS           = 3000
POST_CLOSE_DELAY_SEC         = 5