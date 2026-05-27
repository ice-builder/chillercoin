"""Insider Pump Scanner — Configuration
Detects pre-pump patterns via OI surges + CEX spot flow analysis.
v8.0: Deep analysis optimization (2026-05-20)
  - Hard stop: -5% → -10% PnL (at 5x, -5% = 1% price move = noise. 16/20 losses were false hard_stops)
  - Leverage: 5x → 3x (reduces stop-out severity, -10% at 3x = 3.3% price move = real reversal)
  - Position size: 5/7% → 3/5% (lower exposure per trade)
  - Trail: activation 3→5%, trail 2→3% (give winners more room)
  - Time stop: 6h → 12h (data: 2-6h trades were best, 6h cutoff killed them)
v7.0: Data-driven optimization (2026-05-14)
  - AUTO_ENTER_THRESHOLD: 25→20 (0 auto-enters in 12h was too strict)
  - Tiered position sizing: 7% for score≥22 (scale into high-conviction)
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── Telegram ───────────────────────────────────
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TG_THREAD_ID = os.getenv("TELEGRAM_THREAD_ID", "")

# ─── CryptoAttack TG Parser ────────────────────
# Telethon credentials for reading CryptoAttack channel
TG_API_ID = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH = os.getenv("TG_API_HASH", "")
CRYPTOATTACK_CHANNEL = os.getenv("CRYPTOATTACK_CHANNEL", "cryptoattack24")
CRYPTOATTACK_BOT = os.getenv("CRYPTOATTACK_BOT", "cryptoattackbot")

# ─── Scan Settings ──────────────────────────────
SCAN_INTERVAL_SEC = 300           # 5 min between full scans
OI_SNAPSHOT_RETENTION = 288       # Keep 24h of 5-min snapshots (288 × 5min = 24h)
OI_HISTORY_FILE = "oi_history.json"
STATE_FILE = "insider_positions.json"

# ─── Exchange APIs (all free, no auth required for market data) ──
EXCHANGES = {
    "bybit": {
        "enabled": True,
        "futures_tickers": "https://api.bybit.com/v5/market/tickers?category=linear",
        "oi_endpoint": "https://api.bybit.com/v5/market/open-interest",
        "spot_tickers": "https://api.bybit.com/v5/market/tickers?category=spot",
    },
    "binance": {
        "enabled": True,
        "futures_tickers": "https://fapi.binance.com/fapi/v1/ticker/24hr",
        "oi_endpoint": "https://fapi.binance.com/fapi/v1/openInterest",
        "spot_tickers": "https://api.binance.com/api/v3/ticker/24hr",
    },
    "bitget": {
        "enabled": True,
        "futures_tickers": "https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES",
        "oi_endpoint": "https://api.bitget.com/api/v2/mix/market/open-interest?productType=USDT-FUTURES",
        "spot_tickers": "https://api.bitget.com/api/v2/spot/market/tickers",
    },
    "mexc": {
        "enabled": True,
        "futures_tickers": "https://contract.mexc.com/api/v1/contract/ticker",
        "spot_tickers": "https://api.mexc.com/api/v3/ticker/24hr",
    },
    "gateio": {
        "enabled": True,
        "futures_tickers": "https://api.gateio.ws/api/v4/futures/usdt/tickers",
        "spot_tickers": "https://api.gateio.ws/api/v4/spot/tickers",
    },
}

# ─── OI Detection Thresholds ───────────────────
OI_CHANGE_1H_MIN = 10.0          # Min 10% OI change in 1h to flag
OI_CHANGE_4H_MIN = 25.0          # Min 25% OI change in 4h to flag
OI_Z_SCORE_MIN = 3.0             # Z-score threshold for anomaly
OI_TOP_N = 10                    # Track top N OI movers

# ─── CEX Flow Thresholds ───────────────────────
SPOT_VOLUME_Z_MIN = 3.0          # Spot volume z-score threshold
SPOT_BUY_RATIO_MIN = 1.5         # Buy/sell ratio threshold (1.5 = 60/40)
MIN_SPOT_TURNOVER_24H = 500_000  # Min $500K spot volume to consider

# ─── Scoring ───────────────────────────────────
SCORE_WEIGHTS = {
    "oi_surge_1_exchange":    2,   # OI spike on 1 exchange
    "oi_surge_2_exchanges":   5,   # OI spike on 2+ exchanges
    "oi_surge_3_exchanges":   8,   # OI spike on 3+ exchanges
    "spot_flow_1_exchange":   2,   # Spot buy pressure on 1 exchange
    "spot_flow_2_exchanges":  5,   # Spot buy pressure on 2+ exchanges
    "confluence_bonus":       3,   # OI + Spot on same exchange
    "early_entry_bonus":      2,   # Price still near 24h low (<20% above) — reduced 3→2
    "oi_leader_bonus":        2,   # #1 in OI rankings
    "multi_tf_oi":            2,   # OI rising on both 1h and 4h
    # ─── NEW: LAB/TAG pattern bonuses ──────────
    "bitget_origin_bonus":    2,   # OI surge started on Bitget (pump launchpad) — reduced 3→2
    "small_cap_bonus":        2,   # Low total OI = small cap = easier to pump — reduced 3→2
    "weekly_oi_trend":        3,   # OI growing for 3+ days (accumulation phase)
    "syndicate_bonus":        0,   # DISABLED: too noisy with 2300+ symbols (always fires)
}

# ─── Small Cap Detection ──────────────────────
# Total OI below this = small/micro cap (easier to manipulate)
LOW_OI_THRESHOLD_USD = 2_000_000   # $2M total OI across exchanges (tightened from $5M)
# Exchanges known as pump launchpads (LAB, TAG, RAVE all started here)
PUMP_ORIGIN_EXCHANGES = {"bitget", "mexc"}

# ─── Weekly OI Trend ─────────────────────────
# Track multi-day OI growth for early accumulation detection
WEEKLY_TREND_FILE = "oi_weekly_trend.json"
WEEKLY_TREND_MIN_DAYS = 3          # Min consecutive days of OI growth to flag
SYNDICATE_MIN_TOKENS = 8           # Min tokens surging on one exchange to flag syndicate (raised from 5)

ALERT_THRESHOLD = 15              # Score >= 15 → TG alert
AUTO_ENTER_THRESHOLD = 20         # v7.0: Score >= 20 → auto-enter (25 was too strict: 0 entries in 12h)

# ─── Auto-Entry Settings ───────────────────────
POSITION_SIZE_PCT = 3             # v8.0: 5→3% of balance (data: lower exposure per losing trade)
POSITION_SIZE_HIGH_PCT = 5        # v8.0: 7→5% for score >= 22
HIGH_SCORE_THRESHOLD = 22         # Score threshold for larger position sizing
LEVERAGE = 3                      # v8.0: 5→3x (data: at 5x, -5% stop = 1% price move = noise)
MAX_POSITIONS = 3                 # Max 3 insider positions
ENTRY_DIRECTION = "auto"          # v2: auto-detect direction from price action (was "long" only)

# ─── Risk Management ─────────────────────────
# v8.0: Hard stop widened — 16/20 losses were false hard_stops on noise
# At 3x leverage, -10% PnL = 3.3% adverse price move (real reversal signal)
HARD_STOP_PCT = -10.0             # v8.0: -5→-10% (at 3x, this = 3.3% price move vs 1% before)
TRAIL_ACTIVATION_PCT = 5.0        # v8.0: 3→5% (data: +3% activation was too early, false trails)
TRAIL_STOP_PCT = 3.0              # v8.0: 2→3% (give winning trades room to run)
TIME_STOP_HOURS = 12              # v8.0: 6→12h (data: 2-6h trades were 63% WR, 6h cutoff killed them)
MAX_LOSS_PER_DAY_PCT = 8.0        # v2: Daily limit 8% (was 5% — adjusted for lower lev)
MIN_ENTRY_PRICE = 0.0000001       # Skip entries with price = 0 (broken feed)

# ─── Alert Cooldowns ──────────────────────────
ALERT_COOLDOWN_SEC = 3600         # 1h cooldown per symbol
RESCAN_COOLDOWN_SEC = 1800        # 30min cooldown before re-scoring same symbol

# ─── Blacklist ─────────────────────────────────
# Tokens known to be manipulated/rugged — never auto-enter
BLACKLIST = {
    "RAVEUSDT",  # Bitget manipulation case (ZachXBT)
}
