"""Pump Hunter v2 — Configuration
6-Phase Pump & Dump Trading Algorithm
v8.0: Deep analysis optimization (2026-05-20)
  - TRAIL_PHASES: 30% → phased 15/10/7/5/3% (data: trail captured only 4-13% of peak)
  - V3 trail: 20→10% / 12→6% (sim shows 2x more profit captured)
  - Partial TP: 50% → 5% threshold (sim: -$1,457 → +$243 on 19 trades)
  - Breakeven: 5% → 3% (many trades reach +3% but not +5%)
v7.0: Data-driven optimization (2026-05-14)
  - Leverage: 10→5x (avg loss was $500+ at 10x, 73% trades hit max_loss_cap)
  - Max loss per trade: 5→8% (adjusted for lower leverage)
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── Telegram ───────────────────────────────────
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TG_THREAD_ID = os.getenv("TELEGRAM_THREAD_ID", "")

# ─── Scan Settings ──────────────────────────────
SCAN_INTERVAL_SEC = 600           # 10 min between full scans
KLINES_CACHE_TTL_SEC = 21600      # Re-fetch daily klines every 6h
HOURLY_KLINES_CACHE_TTL = 900     # Re-fetch hourly klines every 15 min

# ─── Phase 1: Detection Criteria ────────────────
CONSOLIDATION_DAYS = 20           # Min days of flat consolidation
CONSOLIDATION_SMA_PERIOD = 480    # 20-day SMA on hourly candles
VOLATILITY_MAX = 0.05             # Max σ/SMA ratio for consolidation zone
PUMP_THRESHOLD = 0.50             # Min 50% rise from consolidation mean
VOLUME_MULTIPLIER = 2.0           # Volume must be 2x+ above SMA
RSI_PUMP_MIN = 60                 # RSI must be > 60 for pump confirmation

# ─── Legacy detection (kept for compatibility) ──
CONSOLIDATION_MAX_RANGE_PCT = 50
MIN_PUMP_PCT = 50
MIN_TURNOVER_24H = 500_000
PREFILTER_24H_CHANGE_PCT = 10

# ─── Alert Tiers ────────────────────────────────
TIER_EARLY = 50                   # 🟡 Early warning
TIER_CONFIRMED = 100              # 🟠 Confirmed pump
TIER_MEGA = 200                   # 🔴 Mega pump

# ─── Phase 2: LONG Position ────────────────────
LONG_SIZE_PCT = 20                # 20% of balance for LONG entry
LEVERAGE = 5                      # v7.0: 10→5x (data: avg loss $500+ at 10x, 73% hit max_loss_cap)
TRAILING_STEP = 0.05              # 5% trailing stop step
INITIAL_SL_OFFSET = 0.02          # SL at P_consolidation × 0.98

# ─── Phase 3: Add-on Buy ───────────────────────
ADDON_PROFIT_THRESHOLD = 0.50     # Profit must be ≥50% for add-on
ADDON_PULLBACK_PCT = 0.03         # Price must drop 3%+ from peak
ADDON_RSI_MAX = 50                # RSI must bounce from ≤50

# ─── Phase 4: Reversal Scoring ──────────────────
REVERSAL_THRESHOLD_1 = 6          # First signal → sell 20%
REVERSAL_THRESHOLD_2 = 12         # Second signal → sell 30%
REVERSAL_THRESHOLD_3 = 18         # Third signal → close all
DROP_CONFIRM_PCT = 0.10           # 10% drop from max confirms reversal

# ─── Phase 5: Profit Taking ────────────────────
FIX_PART_1 = 0.20                 # 20% on first reversal signal
FIX_PART_2 = 0.30                 # 30% on second reversal signal
# Remaining 50% closed on third signal or trailing stop

# ─── Phase 6: SHORT Position ───────────────────
SHORT_SIZE_PCT = 20               # v5.0: 50→20% (matched with LONG to control risk)
SHORT_ADDON_PROFIT = 0.50         # Add to short at 50% profit
DUMP_TARGET = 0.70                # Close short when dump reaches 70%
REBOUND_EXIT = 0.20               # Close short on 20% rebound from low
SHORT_SL_OFFSET = 0.03            # SL at P_max × 1.03

# ─── Trailing Stop Phases ──────────────────────
# v8.0: Tighter phases — data showed 30% trail lets trades give back 90%+ of peak
TRAIL_PHASES = [
    (0,    15),   # 0-5% profit → 15% trail (was 30% — way too loose)
    (5,    10),   # 5-10% → 10% trail
    (10,    7),   # 10-20% → 7% trail
    (20,    5),   # 20-50% → 5% trail
    (50,    3),   # 50%+ → 3% trail (lock in big moves)
]

# ─── Exchanges ──────────────────────────────────
ENABLE_BYBIT = True
ENABLE_MEXC = True
ENABLE_GATEIO = True
ENABLE_BITGET = True

# ─── Trading ───────────────────────────────────
AUTO_ENTER = True
DEMO_BALANCE = 10_000.0
DEMO_POSITION_SIZE_PCT = 5
DEMO_MAX_POSITIONS = 3            # Max 3 concurrent positions
DEMO_STATE_FILE = "demo_state.json"

# ─── Risk Management ───────────────────────────
MAX_SPREAD_PCT = 0.5              # Don't trade if spread > 0.5%
MIN_DAILY_VOLUME = 1_000_000      # Min $1M daily volume
MAX_CONCURRENT_POSITIONS = 3
INTER_TRADE_COOLDOWN_SEC = 14400  # 4h between trades on same asset
DAILY_LOSS_LIMIT_PCT = 15         # v5.0: 30→15% max drawdown before halt
MAX_LOSS_PER_TRADE_PCT = 8        # v7.0: 5→8% hard cap adjusted for 5x leverage

# ─── Cooldowns ──────────────────────────────────
ALERT_COOLDOWN_SEC = 3600
FALSE_BREAKOUT_COOLDOWN_SEC = 86400

# ─── v3: Volume-Impulse Detection ───────────────
V3_LOOKBACK_1H = 168              # 7 days of hourly candles
V3_LOOKBACK_30M = 336             # 7 days of 30m candles
V3_LOOKBACK_15M = 672             # 7 days of 15m candles
V3_MIN_VOLUME_Z = 4.0             # Volume z-score threshold
V3_MIN_RETURN_Z = 3.0             # Price return z-score threshold
V3_MIN_COMBINED_SCORE = 8.0       # vol_z + ret_z minimum
V3_CONFIRM_VOLUME_MULT = 1.5     # Next candle volume must be 1.5x avg
V3_RSI_LONG_MIN = 50              # Min RSI for LONG
V3_RSI_SHORT_MAX = 50             # Max RSI for SHORT
V3_TRAIL_PCT = 0.10               # v8.0: 20→10% trailing stop (data: 20% let trades give back 90% of peak)
V3_TRAIL_TIGHT_PCT = 0.06         # v8.0: 12→6% trail when volume drying up
V3_BREAKEVEN_AT_PCT = 3.0         # v8.0: 5→3% (data: many trades reach +3% but not +5%)
V3_PARTIAL_EXIT_PCT = 5.0         # v8.0: 50→5% — partial TP at +5% (sim: -$1,457 → +$243)
V3_PARTIAL_EXIT_SIZE = 0.50       # v8.0: 0.30→0.50 — sell 50% at partial exit (lock in half)
V3_COOLDOWN_SEC = 43200           # 12h cooldown per symbol
V3_CACHE_TTL_SEC = 300            # 5 min cache for v3 klines
V3_SIZE_PCT = 20                  # Same as LONG_SIZE_PCT
V3_LEVERAGE = 5                   # v7.0: 10→5x (matched with main leverage)
