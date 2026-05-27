"""
IIE — Configuration
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ─── Paths ─────────────────────────────────────────
IIE_DIR = Path(os.getenv("IIE_DIR", Path(__file__).parent))
DATA_DIR = IIE_DIR / "data"
DB_PATH = DATA_DIR / "impulses.db"
MODEL_DIR = DATA_DIR / "models"

# Ensure dirs exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# ─── Telegram ──────────────────────────────────────
TG_TOKEN = os.getenv("TELEGRAM_SCALPER_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── Impulse Collector ────────────────────────────
COLLECTOR_INTERVAL_SEC = 300          # 5 min between scans
COLLECTOR_TOP_COINS = 200             # Scan top N coins by volume
COLLECTOR_MIN_TURNOVER_24H = 500_000  # Min $500K 24h volume

# Z-score thresholds for impulse detection
IMPULSE_MIN_VOL_Z = 3.0               # Volume z-score threshold
IMPULSE_MIN_RET_Z = 2.0               # Return z-score threshold
IMPULSE_LOOKBACK_BARS = {
    "5":  100,   # 5m: ~8 hours lookback
    "15": 100,   # 15m: ~25 hours lookback
    "60": 168,   # 1h: 7 days lookback
}

# ─── Post-Trade Tracker ──────────────────────────
POST_TRACKER_INTERVAL_SEC = 180       # 3 min between updates (turbo mode)
POST_TRACKER_BATCH_LIMIT = 5000       # Process up to 5000 outcomes per cycle
POST_TRACKER_CHECKPOINTS = [
    ("5m",    5 * 60),
    ("15m",  15 * 60),
    ("1h",    1 * 3600),
    ("4h",    4 * 3600),
    ("24h",  24 * 3600),
    ("48h",  48 * 3600),
]
# Impulses older than this are considered fully tracked
POST_TRACKER_MAX_AGE_SEC = 24 * 3600  # 24 hours (turbo: was 48h)

# Stop hunt detection: reversal > X% within N bars
STOP_HUNT_REVERSAL_PCT = 50.0         # Retraced 50%+ of impulse move
STOP_HUNT_MAX_BARS = 3                # Within 3 bars

# ─── Market Phase ────────────────────────────────
MARKET_PHASE_INTERVAL_SEC = 4 * 3600  # Every 4 hours
MARKET_PHASE_TRENDING_THRESHOLD = 10.0  # BTC monthly change > 10% = trending
MARKET_PHASE_EMA_FAST = 20
MARKET_PHASE_EMA_SLOW = 50

# ─── Coin Scorer ─────────────────────────────────
COIN_SCORER_INTERVAL_SEC = 3600       # Every 1 hour
COIN_SCORER_MIN_IMPULSES = 10         # Min impulses to build a profile
COIN_NEW_LISTING_DAYS = 30            # < 30 days = new listing
COIN_OLD_LISTING_DAYS = 365           # > 365 days = established

# ─── ML Predictor ────────────────────────────────
PREDICTOR_RETRAIN_INTERVAL_SEC = 6 * 3600   # Retrain every 6h (turbo: was 24h)
PREDICTOR_MIN_SAMPLES = 100           # Min samples for first training
PREDICTOR_INCREMENTAL_INTERVAL_SEC = 1800   # Incremental update every 30min (turbo: was 1h)

# ─── Exchanges to monitor ───────────────────────
EXCHANGES = ["bybit", "mexc", "gateio", "bitget"]

# ─── Pump Hunter TG (group) ─────────────────────
PH_TG_TOKEN = os.getenv("PH_TELEGRAM_BOT_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", ""))
PH_TG_CHAT_ID = os.getenv("PH_TELEGRAM_CHAT_ID", "")
PH_TG_THREAD_ID = os.getenv("PH_TELEGRAM_THREAD_ID", "")

# ─── Signal Settings ────────────────────────────
SIGNAL_MIN_SCORE = 60           # Min IIE score to trigger signal
SIGNAL_COOLDOWN_SEC = 1800      # 30 min cooldown per symbol (was 5min — caused duplicates)
SIGNAL_MAX_STOP_HUNT_PROB = 70  # Block if stop_hunt_prob > 70%
