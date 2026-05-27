"""
Scalper Pro — Configuration v2.0
Adaptive trading bot with IIE v2 feedback loop.
Runs alongside Soldier/PH without affecting them.

v2.0 changes:
  - Position cap: 50% → 15% of balance
  - Emergency stop: 5% → 3%
  - Trail: 0.15% → 0.8%
  - Portfolio exposure limit: 100%
  - Direction balance: max 3 per side
  - Symbol loss cooldown: pause after 2 consecutive losses
  - Hypothesis maturity: 10 → 5 trades
"""
import os
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

STATE_FILE = DATA_DIR / "scalper_pro_state.json"
DB_PATH = DATA_DIR / "scalper_pro.db"
HYPOTHESES_CACHE = DATA_DIR / "hypotheses.json"

# IIE v1 DB (read-only access for shared intelligence)
IIE_V1_DB = Path(os.getenv(
    "IIE_V1_DB_PATH",
    "/home/trader/soldier/iie/data/impulses.db"
))

# ── Telegram ──────────────────────────────────────────────────────────────────
TG_BOT_TOKEN = os.getenv(
    "SP_TG_TOKEN",
    "8849503586:AAHzo4CuINxaaaQzVpE0n5LVHBBL_5GClAI",
)
TG_CHAT_ID = os.getenv("SP_TG_CHAT_ID", "5166249217")

# ── Trading Parameters ────────────────────────────────────────────────────────
VIRTUAL_BALANCE = 5000.0           # Starting virtual balance (USDT)
COMMISSION_MAKER_PCT = 0.02        # Binance Futures maker (limit orders)
COMMISSION_TAKER_PCT = 0.04        # Binance Futures taker (market orders)
COMMISSION_ROUNDTRIP_PCT = 0.08    # taker open + taker close (worst case)

MAX_POSITIONS = 5                  # Max concurrent positions
ACCOUNT_RISK_PCT = 1.5             # % of balance risked per trade (was 2.0)
MAX_POSITION_PCT = 15.0            # Max single position as % of balance (was 50!)
MAX_PORTFOLIO_EXPOSURE_PCT = 100.0 # Max total exposure as % of balance
MAX_SAME_DIRECTION = 3             # Max positions in same direction
MAX_SESSION_LOSS_PCT = -15.0       # Auto drawdown stop

# ── Symbol Risk Management ───────────────────────────────────────────────────
SYMBOL_MAX_CONSECUTIVE_LOSSES = 2  # Pause symbol after N consecutive losses
SYMBOL_LOSS_COOLDOWN_SEC = 3600    # 1 hour cooldown after consecutive losses

# ── Dynamic Stops (Phase 4) ──────────────────────────────────────────────────
EMERGENCY_STOP_PCT = 3.0           # Hard stop — NEVER removed (was 5.0)
INITIAL_STOP_MULT = 1.5            # Initial wide stop = SL × this (was 2.0)
CONFIRM_WINDOW_SEC = 900           # 15 min to confirm impulse
TRAIL_ATR_MULT = 1.5               # ATR-based trailing multiplier
DEFAULT_TRAIL_PCT = 0.8            # Default trail distance % (was 0.15 — way too tight)

# ── Adaptive Sizing (Phase 4) ────────────────────────────────────────────────
SCALE_IN_TRIGGER_PCT = 0.5         # Add position when +0.5% in favor (was 0.3)
SCALE_IN_MULTIPLIER = 1.5          # 1.5x position on scale-in (was 2.0)
SCALE_OUT_TRIGGER_PCT = -0.5       # Cut position when -0.5% against (was -0.2)
SCALE_OUT_DIVISOR = 2.0            # Halve position on scale-out
MAX_POSITION_MULT = 1.5            # Max total position = initial × 1.5 (was 2.0)
MIN_POSITION_MULT = 0.5            # Min total position = initial × 0.5

# ── Checkpoints (Phase 2) ────────────────────────────────────────────────────
CHECKPOINT_INTERVALS = {
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
}

# ── Hypothesis Engine (Phase 3) ──────────────────────────────────────────────
HYPOTHESIS_MIN_SAMPLES = 5         # Min trades before hypothesis is "mature" (was 10)
HYPOTHESIS_ROLLING_WINDOW = 50     # Rolling window for stats
HYPOTHESIS_SCORE_BINS = [
    (0, 40, "low"),
    (40, 60, "medium"),
    (60, 80, "high"),
    (80, 100, "extreme"),
]
# Allow hypothesis creation from partial checkpoints (15m + 1h done = enough)
HYPOTHESIS_MIN_CHECKPOINTS = 4     # At least 4 of 6 checkpoints completed

# ── Price Verification ────────────────────────────────────────────────────────
# Multi-exchange price sources for trade verification
PRICE_SOURCES = [
    {
        "name": "bybit",
        "url": "https://api.bybit.com/v5/market/tickers",
        "params_template": {"category": "linear", "symbol": "{symbol}"},
        "price_path": ["result", "list", 0, "lastPrice"],
    },
    {
        "name": "binance",
        "url": "https://fapi.binance.com/fapi/v1/ticker/price",
        "params_template": {"symbol": "{symbol}"},
        "price_path": ["price"],
    },
    {
        "name": "okx",
        "url": "https://www.okx.com/api/v5/market/ticker",
        "params_template": {"instId": "{symbol_okx}"},
        "price_path": ["data", 0, "last"],
    },
]
# Max acceptable price divergence between exchanges (%)
PRICE_DIVERGENCE_THRESHOLD = 0.5

# ── Scan & Loop ───────────────────────────────────────────────────────────────
MAIN_LOOP_INTERVAL_SEC = 15        # Main loop tick
SIGNAL_COOLDOWN_SEC = 300          # Min gap between signals on same symbol
PRICE_CACHE_TTL = 5                # Price cache TTL (seconds)

# ── Reports ───────────────────────────────────────────────────────────────────
DAILY_REPORT_HOUR_MSK = 10         # Send daily digest at 10:00 MSK
MSK_UTC_OFFSET = 3

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("SP_LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
