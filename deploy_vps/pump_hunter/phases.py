"""
Pump Hunter v2 — 6-Phase Trading Logic
Phase 1: Consolidation detection + pump trigger
Phase 2: LONG entry (20% balance × 20x)
Phase 3: Add-on buy (remaining balance at pullback)
Phase 4: Reversal scoring (12 indicators)
Phase 5: Staged profit-taking (20% → 30% → close all)
Phase 6: SHORT after LONG closed
"""
import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass, field, asdict
from typing import Optional, Tuple, List

from config import (
    CONSOLIDATION_SMA_PERIOD, VOLATILITY_MAX, PUMP_THRESHOLD,
    VOLUME_MULTIPLIER, RSI_PUMP_MIN,
    LONG_SIZE_PCT, LEVERAGE, TRAILING_STEP, INITIAL_SL_OFFSET,
    ADDON_PROFIT_THRESHOLD, ADDON_PULLBACK_PCT, ADDON_RSI_MAX,
    REVERSAL_THRESHOLD_1, REVERSAL_THRESHOLD_2, REVERSAL_THRESHOLD_3,
    DROP_CONFIRM_PCT,
    FIX_PART_1, FIX_PART_2,
    SHORT_SIZE_PCT, SHORT_ADDON_PROFIT, DUMP_TARGET, REBOUND_EXIT,
    SHORT_SL_OFFSET,
)

log = logging.getLogger("PumpHunter")


# ─── Data Classes ────────────────────────────────────────────

@dataclass
class ConsolidationZone:
    high: float
    low: float
    mean: float          # P_consolidation (SMA)
    volatility_pct: float
    days: int

@dataclass
class Position:
    symbol: str
    exchange: str
    strategy_version: str = "v2"  # "v1" or "v2" — for A/B comparison
    phase: int = 1              # current phase 1-6
    direction: str = "long"     # "long" or "short"
    entry_price: float = 0.0
    entry_time: float = 0.0
    size_pct: float = 0.0       # % of balance used
    leverage: int = LEVERAGE
    stop_loss: float = 0.0
    trailing_stop: float = 0.0
    peak_price: float = 0.0     # P_max
    dump_min: float = 0.0       # P_dump_min (for short)
    p_consolidation: float = 0.0
    p_max_final: float = 0.0    # max before reversal started
    consolidation: Optional[ConsolidationZone] = None
    reversal_score: int = 0
    reversal_stage: int = 0     # 0,1,2,3 — how many partial exits done
    addon_done: bool = False
    remaining_qty_pct: float = 1.0  # 1.0 → 0.8 → 0.5 → 0
    pnl_pct: float = 0.0
    pump_pct: float = 0.0
    detected_at: float = 0.0
    exited: bool = False
    exit_reason: str = ""
    # For TG
    message_id: int = 0
    confirmed: bool = False
    cancelled: bool = False

    def to_dict(self):
        d = {}
        for k, v in self.__dict__.items():
            # Handle ConsolidationZone from any module (v1 or v2)
            if hasattr(v, 'high') and hasattr(v, 'low') and hasattr(v, 'mean'):
                d[k] = {'high': v.high, 'low': v.low, 'mean': v.mean,
                         'volatility_pct': v.volatility_pct, 'days': v.days}
            else:
                d[k] = v
        return d

    @classmethod
    def from_dict(cls, d: dict) -> 'Position':
        d = dict(d)  # don't mutate original
        cons = d.pop('consolidation', None)
        if cons and isinstance(cons, dict):
            cons = ConsolidationZone(**cons)
        elif cons and isinstance(cons, str):
            cons = None  # discard string repr from bad state
        # Strip keys that aren't Position fields (e.g. strategy_name from state)
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        d = {k: v for k, v in d.items() if k in valid_fields}
        return cls(consolidation=cons, **d)


# ─── Technical Indicators ────────────────────────────────────

def compute_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def compute_macd(closes: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = closes.ewm(span=fast).mean()
    ema_slow = closes.ewm(span=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def compute_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def compute_volume_sma(volumes: pd.Series, period: int = 20) -> pd.Series:
    return volumes.rolling(period).mean()


# ─── Phase 1: Detection ─────────────────────────────────────

def detect_pump(hourly_df: pd.DataFrame, daily_df: pd.DataFrame
                ) -> Optional[Tuple[ConsolidationZone, float, float]]:
    """
    Phase 1: Detect consolidation zone + pump breakout.
    Uses hourly SMA(480) for consolidation, checks volatility < 5%,
    pump >= 50%, volume 2x+, RSI > 60, MACD cross up.
    Returns (zone, pump_pct, current_price) or None.
    """
    if hourly_df is None or len(hourly_df) < CONSOLIDATION_SMA_PERIOD:
        # Fallback to daily if not enough hourly data
        if daily_df is None or len(daily_df) < 20:
            return None
        return _detect_pump_daily(daily_df)

    closes_h = hourly_df["close"].astype(float)
    current_price = float(closes_h.iloc[-1])

    # SMA(480) on hourly = ~20 days
    sma = closes_h.rolling(CONSOLIDATION_SMA_PERIOD).mean()
    p_consolidation = float(sma.dropna().iloc[-1]) if len(sma.dropna()) > 0 else 0
    if p_consolidation <= 0:
        return None

    # Volatility: std/SMA of consolidation period
    cons_window = closes_h.iloc[-CONSOLIDATION_SMA_PERIOD:]
    volatility = float(cons_window.std() / p_consolidation)
    if volatility > VOLATILITY_MAX:
        return None

    # Pump check
    pump_pct = (current_price / p_consolidation - 1.0)
    if pump_pct < PUMP_THRESHOLD:
        return None

    # Volume check (2x above SMA)
    if "volume" in hourly_df.columns:
        vol = hourly_df["volume"].astype(float)
        vol_sma = vol.rolling(CONSOLIDATION_SMA_PERIOD).mean()
        if len(vol_sma.dropna()) > 0:
            current_vol = float(vol.iloc[-1])
            avg_vol = float(vol_sma.dropna().iloc[-1])
            if avg_vol > 0 and current_vol / avg_vol < VOLUME_MULTIPLIER:
                return None

    # RSI > 60
    rsi = compute_rsi(closes_h)
    if len(rsi.dropna()) > 0:
        current_rsi = float(rsi.dropna().iloc[-1])
        if current_rsi < RSI_PUMP_MIN:
            return None

    # MACD cross up (histogram positive)
    _, _, hist = compute_macd(closes_h)
    if len(hist.dropna()) >= 2:
        if float(hist.dropna().iloc[-1]) <= 0:
            return None

    # Build consolidation zone from daily data
    if daily_df is not None and len(daily_df) >= 5:
        split = max(5, len(daily_df) - 3)
        cons_df = daily_df.iloc[:split]
        cons_high = float(cons_df["high"].max())
        cons_low = float(cons_df["low"].min())
        days = len(cons_df)
    else:
        cons_high = float(cons_window.max())
        cons_low = float(cons_window.min())
        days = CONSOLIDATION_SMA_PERIOD // 24

    zone = ConsolidationZone(
        high=round(cons_high, 8),
        low=round(cons_low, 8),
        mean=round(p_consolidation, 8),
        volatility_pct=round(volatility * 100, 1),
        days=days,
    )
    return zone, round(pump_pct * 100, 1), current_price


def _detect_pump_daily(daily_df: pd.DataFrame
                       ) -> Optional[Tuple[ConsolidationZone, float, float]]:
    """Fallback detection using daily candles only."""
    if len(daily_df) < 10:
        return None
    split = max(5, len(daily_df) - 3)
    cons_df = daily_df.iloc[:split]
    current_price = float(daily_df["close"].iloc[-1])

    cons_high = float(cons_df["high"].max())
    cons_low = float(cons_df["low"].min())
    cons_mean = float(cons_df["close"].mean())
    if cons_mean <= 0:
        return None

    range_pct = (cons_high - cons_low) / cons_mean
    if range_pct > VOLATILITY_MAX * 20:  # Lenient for daily (allow up to 100% range)
        return None

    pump_pct = (current_price / cons_mean - 1.0)
    if pump_pct < PUMP_THRESHOLD:
        return None

    zone = ConsolidationZone(
        high=round(cons_high, 8), low=round(cons_low, 8),
        mean=round(cons_mean, 8), volatility_pct=round(range_pct * 100, 1),
        days=len(cons_df),
    )
    return zone, round(pump_pct * 100, 1), current_price


# ─── Phase 2: LONG Entry Setup ──────────────────────────────

def setup_long_entry(pos: Position, price: float, balance: float):
    """Configure LONG position parameters."""
    pos.phase = 2
    pos.direction = "long"
    pos.entry_price = price
    pos.peak_price = price
    pos.size_pct = LONG_SIZE_PCT
    pos.leverage = LEVERAGE
    pos.stop_loss = pos.p_consolidation * (1 - INITIAL_SL_OFFSET)
    pos.trailing_stop = pos.stop_loss
    pos.remaining_qty_pct = 1.0
    pos.addon_done = False
    pos.reversal_score = 0
    pos.reversal_stage = 0
    pos.confirmed = True
    log.info(f"📈 Phase 2 LONG: {pos.symbol} @ {price} | "
             f"SL={pos.stop_loss:.8g} | Size={LONG_SIZE_PCT}% × {LEVERAGE}x")


# ─── Phase 3: Add-on Buy Check ──────────────────────────────

def check_addon_buy(pos: Position, price: float,
                    hourly_df: pd.DataFrame) -> bool:
    """
    Phase 3: Check if we should add to LONG position.
    Conditions: profit >= 50%, price pulled back 3%+ from peak,
    2 bullish candles, RSI bounced from <= 50.
    """
    if pos.addon_done or pos.direction != "long" or pos.phase < 2:
        return False

    profit_pct = (price / pos.entry_price - 1.0)
    if profit_pct < ADDON_PROFIT_THRESHOLD:
        return False

    pullback = (pos.peak_price - price) / pos.peak_price
    if pullback < ADDON_PULLBACK_PCT:
        return False

    # Check RSI bounced from <= 50
    if hourly_df is not None and len(hourly_df) >= 20:
        rsi = compute_rsi(hourly_df["close"].astype(float))
        rsi_vals = rsi.dropna().tail(5)
        if len(rsi_vals) >= 3:
            had_low = any(float(v) <= ADDON_RSI_MAX for v in rsi_vals.iloc[:-1])
            now_rising = float(rsi_vals.iloc[-1]) > ADDON_RSI_MAX
            if not (had_low and now_rising):
                return False

    # Check 2 bullish candles
    if hourly_df is not None and len(hourly_df) >= 3:
        last2 = hourly_df.tail(2)
        bullish = all(
            float(row["close"]) > float(row["open"])
            for _, row in last2.iterrows()
        )
        if not bullish:
            return False

    return True


def execute_addon(pos: Position, price: float):
    """Execute add-on buy — use remaining balance."""
    pos.addon_done = True
    pos.phase = 3
    pos.size_pct = 100  # Now using full balance
    log.info(f"📈 Phase 3 ADD-ON: {pos.symbol} @ {price} | Full balance")


# ─── Phase 4: Reversal Scoring ───────────────────────────────

def compute_reversal_score(pos: Position, price: float,
                           hourly_df: pd.DataFrame) -> int:
    """
    Phase 4: Compute reversal score from 12 indicators.
    Each indicator contributes 1-3 points. Max ~30.
    """
    if hourly_df is None or len(hourly_df) < 30:
        return 0

    score = 0
    closes = hourly_df["close"].astype(float)
    opens = hourly_df["open"].astype(float)
    highs = hourly_df["high"].astype(float)
    lows = hourly_df["low"].astype(float)
    volumes = hourly_df["volume"].astype(float) if "volume" in hourly_df.columns else pd.Series([0]*len(hourly_df))

    # 1. Volume drying up (last 5 candles volume < SMA)
    vol_sma = compute_volume_sma(volumes, 20)
    if len(vol_sma.dropna()) > 0:
        recent_vol = float(volumes.tail(5).mean())
        avg_vol = float(vol_sma.dropna().iloc[-1])
        if avg_vol > 0 and recent_vol < avg_vol * 0.7:
            score += 2

    # 2. Sell dominance (more red candles in last 5)
    last5 = hourly_df.tail(5)
    red_count = sum(1 for _, r in last5.iterrows() if float(r["close"]) < float(r["open"]))
    if red_count >= 3:
        score += 2
    elif red_count >= 4:
        score += 3

    # 3. RSI bearish divergence (price up but RSI down)
    rsi = compute_rsi(closes)
    rsi_vals = rsi.dropna()
    if len(rsi_vals) >= 10:
        price_up = float(closes.iloc[-1]) > float(closes.iloc[-10])
        rsi_down = float(rsi_vals.iloc[-1]) < float(rsi_vals.iloc[-10])
        if price_up and rsi_down:
            score += 3

    # 4. MACD cross down
    _, _, hist = compute_macd(closes)
    hist_vals = hist.dropna()
    if len(hist_vals) >= 2:
        if float(hist_vals.iloc[-2]) > 0 and float(hist_vals.iloc[-1]) <= 0:
            score += 3

    # 5. Pin bar (long upper wick)
    last_candle = hourly_df.iloc[-1]
    body = abs(float(last_candle["close"]) - float(last_candle["open"]))
    upper_wick = float(last_candle["high"]) - max(float(last_candle["close"]), float(last_candle["open"]))
    if body > 0 and upper_wick > body * 2:
        score += 2

    # 6. Doji (small body relative to range)
    candle_range = float(last_candle["high"]) - float(last_candle["low"])
    if candle_range > 0 and body / candle_range < 0.1:
        score += 1

    # 7. Bearish engulfing
    if len(hourly_df) >= 2:
        prev = hourly_df.iloc[-2]
        curr = hourly_df.iloc[-1]
        prev_bullish = float(prev["close"]) > float(prev["open"])
        curr_bearish = float(curr["close"]) < float(curr["open"])
        engulfs = (float(curr["open"]) >= float(prev["close"]) and
                   float(curr["close"]) <= float(prev["open"]))
        if prev_bullish and curr_bearish and engulfs:
            score += 3

    # 8. Evening star (3-candle pattern)
    if len(hourly_df) >= 3:
        c1 = hourly_df.iloc[-3]
        c2 = hourly_df.iloc[-2]
        c3 = hourly_df.iloc[-1]
        c1_bull = float(c1["close"]) > float(c1["open"])
        c2_small = abs(float(c2["close"]) - float(c2["open"])) < abs(float(c1["close"]) - float(c1["open"])) * 0.3
        c3_bear = float(c3["close"]) < float(c3["open"])
        c3_deep = float(c3["close"]) < (float(c1["open"]) + float(c1["close"])) / 2
        if c1_bull and c2_small and c3_bear and c3_deep:
            score += 3

    # 9. No new highs (peak was 5+ candles ago)
    peak_idx = int(highs.tail(20).idxmax()) if len(highs) >= 20 else len(highs) - 1
    candles_since_peak = len(highs) - 1 - peak_idx
    if candles_since_peak >= 5:
        score += 2

    # 10. RSI overbought then declining
    if len(rsi_vals) >= 3:
        was_ob = float(rsi_vals.iloc[-3]) > 70
        declining = float(rsi_vals.iloc[-1]) < float(rsi_vals.iloc[-2]) < float(rsi_vals.iloc[-3])
        if was_ob and declining:
            score += 2

    # 11. Price below short-term EMA
    ema_8 = closes.ewm(span=8).mean()
    if len(ema_8.dropna()) > 0 and price < float(ema_8.iloc[-1]):
        score += 2

    # 12. Drop from peak > threshold
    if pos.peak_price > 0:
        drop = (pos.peak_price - price) / pos.peak_price
        if drop >= DROP_CONFIRM_PCT:
            score += 3

    pos.reversal_score = score
    if score >= REVERSAL_THRESHOLD_1:
        pos.phase = max(pos.phase, 4)
    return score


# ─── Phase 5: Profit Taking ─────────────────────────────────

def check_profit_taking(pos: Position, price: float) -> Optional[str]:
    """
    Phase 5: Staged profit taking based on reversal score.
    Returns action: "fix_20", "fix_30", "close_all", or None.
    """
    if pos.direction != "long" or pos.exited:
        return None

    score = pos.reversal_score

    # Stage 1: score >= 6 → sell 20%
    if score >= REVERSAL_THRESHOLD_1 and pos.reversal_stage < 1:
        pos.reversal_stage = 1
        pos.remaining_qty_pct -= FIX_PART_1  # 1.0 → 0.8
        pos.phase = 5
        return "fix_20"

    # Stage 2: score >= 12 → sell 30%
    if score >= REVERSAL_THRESHOLD_2 and pos.reversal_stage < 2:
        pos.reversal_stage = 2
        pos.remaining_qty_pct -= FIX_PART_2  # 0.8 → 0.5
        return "fix_30"

    # Stage 3: score >= 18 OR -10% from peak → close all
    if pos.peak_price > 0:
        drop = (pos.peak_price - price) / pos.peak_price
        if score >= REVERSAL_THRESHOLD_3 or drop >= DROP_CONFIRM_PCT:
            if pos.reversal_stage < 3:
                pos.reversal_stage = 3
                pos.remaining_qty_pct = 0
                return "close_all"

    return None


# ─── Phase 6: SHORT Setup ───────────────────────────────────

def setup_short_entry(pos: Position, price: float, balance: float):
    """Phase 6: Configure SHORT position after LONG closed."""
    pos.phase = 6
    pos.direction = "short"
    pos.entry_price = price
    pos.peak_price = pos.p_max_final  # Keep the max from LONG phase
    pos.dump_min = price  # Track dump minimum
    pos.size_pct = SHORT_SIZE_PCT
    pos.leverage = LEVERAGE
    pos.stop_loss = pos.p_max_final * (1 + SHORT_SL_OFFSET)
    pos.trailing_stop = pos.stop_loss
    pos.remaining_qty_pct = 1.0
    pos.addon_done = False
    pos.reversal_score = 0
    pos.reversal_stage = 0
    pos.exited = False
    pos.exit_reason = ""
    log.info(f"📉 Phase 6 SHORT: {pos.symbol} @ {price} | "
             f"SL={pos.stop_loss:.8g} | Size={SHORT_SIZE_PCT}%")


def check_short_exit(pos: Position, price: float) -> Optional[str]:
    """Check SHORT exit conditions."""
    if pos.direction != "short" or pos.exited:
        return None

    # Update dump minimum
    if price < pos.dump_min:
        pos.dump_min = price

    # Stop loss
    if price >= pos.stop_loss:
        return "short_sl"

    # Dump target: price reached 70% of (P_max → P_consolidation)
    if pos.p_max_final > 0 and pos.p_consolidation > 0:
        total_range = pos.p_max_final - pos.p_consolidation
        target = pos.p_max_final - total_range * DUMP_TARGET
        if price <= target:
            return "dump_target_70"

    # Rebound exit: 20% rebound from dump minimum
    # dump_min must be meaningfully below entry (actual dump occurred)
    if pos.dump_min > 0 and pos.dump_min < pos.entry_price * 0.95:
        rebound = (price - pos.dump_min) / pos.dump_min
        if rebound >= REBOUND_EXIT - 1e-9:
            return "rebound_20"

    return None


def check_short_addon(pos: Position, price: float) -> bool:
    """Check if we should add to SHORT at 50% profit."""
    if pos.addon_done or pos.direction != "short":
        return False
    profit = (pos.entry_price - price) / pos.entry_price
    return profit >= SHORT_ADDON_PROFIT


# ─── Trailing Stop (LONG) ───────────────────────────────────

def update_long_trailing(pos: Position, price: float) -> Optional[str]:
    """Update LONG trailing stop. Returns exit reason or None."""
    if pos.direction != "long" or pos.exited:
        return None

    profit_pct = (price / pos.entry_price - 1.0) * 100
    pos.pnl_pct = profit_pct

    # Update peak
    if price > pos.peak_price:
        pos.peak_price = price

    # Trailing stop: 5% below peak
    new_stop = pos.peak_price * (1 - TRAILING_STEP)
    if new_stop > pos.trailing_stop:
        pos.trailing_stop = new_stop

    # Floor: never below initial SL
    if pos.trailing_stop < pos.stop_loss:
        pos.trailing_stop = pos.stop_loss

    # Check stop hit
    if price <= pos.trailing_stop:
        return "trailing_stop"

    # Check initial SL hit
    if price <= pos.stop_loss:
        return "stop_loss"

    return None
