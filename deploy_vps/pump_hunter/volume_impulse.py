"""
Pump Hunter v3 — Volume-Impulse Detection Strategy

Detects explosive volume spikes (z-score) accompanied by sharp price moves.
Adapts the Soldier bot's proven z-score engine to hourly/30m/15m timeframes.

Pattern: Months of quiet → sudden volume explosion → ride the momentum.
Examples: ZECUSDT May 2026 (+98%), TONUSDT May 2026 (+87%).

Entry: When volume spike detected (combined z-score ≥ 8.0)
Stop: Below the candle before the impulse (start of volume rise)
Trail: Adaptive 15% → 8% when volume dries up
"""
import logging
import time
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple

log = logging.getLogger("PumpHunterV3")


# ─── Z-Score Engine (adapted from Soldier paper_trader.py) ────

def rolling_zscore(series: pd.Series, lookback: int) -> pd.Series:
    """Rolling z-score: how many standard deviations from rolling mean."""
    baseline = series.shift(1)
    min_periods = max(10, lookback // 4)
    mean = baseline.rolling(lookback, min_periods=min_periods).mean()
    std = baseline.rolling(lookback, min_periods=min_periods).std().replace(0, np.nan)
    return ((series - mean) / std).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def prepare_impulse_features(df: pd.DataFrame, lookback: int = 168) -> pd.DataFrame:
    """
    Compute z-score features on OHLCV data.
    
    Args:
        df: DataFrame with columns [open, high, low, close, volume]
        lookback: Rolling window for z-score calculation
                  168 = 7 days of hourly, 672 = 7 days of 15m
    Returns:
        DataFrame with added columns: dollar_volume_z, abs_ret_z, direction, ema_24
    """
    frame = df.copy()
    
    # Price return (percentage)
    frame["ret_pct"] = frame["close"].pct_change() * 100
    frame["abs_ret"] = frame["ret_pct"].abs()
    
    # Dollar volume = close × volume
    frame["dollar_volume"] = frame["close"].astype(float) * frame["volume"].astype(float)
    
    # Z-scores
    frame["dollar_volume_z"] = rolling_zscore(frame["dollar_volume"], lookback)
    frame["abs_ret_z"] = rolling_zscore(frame["abs_ret"], lookback)
    
    # Direction: +1 bullish, -1 bearish, 0 flat
    frame["direction"] = 0
    frame.loc[frame["ret_pct"] > 0, "direction"] = 1
    frame.loc[frame["ret_pct"] < 0, "direction"] = -1
    
    # Trend filter: EMA(24 bars)
    frame["ema_24"] = frame["close"].astype(float).ewm(span=24, adjust=False).mean()
    
    # RSI(14)
    frame["rsi"] = _compute_rsi(frame["close"].astype(float), 14)
    
    # Volume SMA for drying-up detection
    frame["volume_sma"] = frame["volume"].astype(float).rolling(20, min_periods=5).mean()
    
    return frame


def _compute_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


# ─── Detection ───────────────────────────────────────────────

@dataclass
class ImpulseSignal:
    """Result of volume-impulse detection."""
    direction: str          # "long" or "short"
    entry_price: float
    stop_price: float       # Low/high of pre-impulse candle
    vol_z: float            # Volume z-score at impulse
    ret_z: float            # Return z-score at impulse
    combined_score: float   # vol_z + ret_z
    timeframe: str          # "15m", "30m", "1h"
    impulse_bar_idx: int    # Index of the impulse bar in dataframe


def detect_volume_impulse(
    df: pd.DataFrame,
    min_vol_z: float = 4.0,
    min_ret_z: float = 3.0,
    min_combined: float = 8.0,
    rsi_long_min: float = 50.0,
    rsi_short_max: float = 50.0,
    lookback: int = 168,
    confirm_volume_mult: float = 1.5,
    timeframe: str = "1h",
    scan_bars: int = 5,
) -> Optional[ImpulseSignal]:
    """
    Detect volume-impulse signal on prepared dataframe.
    
    Scans the last `scan_bars` completed bars for z-score spikes.
    Picks the strongest impulse (highest combined score).
    
    Args:
        df: Raw OHLCV dataframe
        min_vol_z: Minimum volume z-score
        min_ret_z: Minimum price return z-score
        min_combined: Minimum combined score (vol_z + ret_z)
        rsi_long_min: Minimum RSI for LONG signals
        rsi_short_max: Maximum RSI for SHORT signals
        lookback: Z-score rolling window
        confirm_volume_mult: Current bar volume must be >= this × avg
        timeframe: "15m", "30m", or "1h"
        scan_bars: How many recent completed bars to scan
    
    Returns:
        ImpulseSignal or None
    """
    min_bars = max(lookback + 10, 50)
    if df is None or len(df) < min_bars:
        return None
    
    frame = prepare_impulse_features(df, lookback)
    n = len(frame)
    
    # Search window: last scan_bars completed bars (not current forming bar)
    search_end = n - 1   # Last completed bar
    search_start = max(0, search_end - scan_bars)
    
    best_signal = None
    best_score = 0.0
    
    for i in range(search_start, search_end):
        row = frame.iloc[i]
        vol_z = float(row.get("dollar_volume_z", 0))
        ret_z = float(row.get("abs_ret_z", 0))
        direction = int(row.get("direction", 0))
        rsi = float(row.get("rsi", 50))
        
        # Z-score thresholds
        if vol_z < min_vol_z or ret_z < min_ret_z:
            continue
        if direction == 0:
            continue
        
        combined = vol_z + ret_z
        if combined < min_combined:
            continue
        
        # Direction filter with RSI
        if direction > 0 and rsi < rsi_long_min:
            continue
        if direction < 0 and rsi > rsi_short_max:
            continue
        
        # Trend filter: for LONG, price should be above EMA(24)
        price = float(row["close"])
        ema = float(row.get("ema_24", price))
        if direction > 0 and price < ema * 0.98:  # Allow 2% tolerance
            continue
        if direction < 0 and price > ema * 1.02:
            continue
        
        if combined > best_score:
            best_score = combined
            
            # Stop loss: low/high of the candle BEFORE the impulse
            pre_idx = max(0, i - 1)
            pre_bar = frame.iloc[pre_idx]
            
            if direction > 0:
                # LONG: stop below the low before impulse
                sl_price = float(pre_bar["low"]) * 0.98
                entry_price = float(row["close"])
            else:
                # SHORT: stop above the high before impulse
                sl_price = float(pre_bar["high"]) * 1.02
                entry_price = float(row["close"])
            
            # Verify volume confirmation on next bar(s)
            if i + 1 < n:
                next_bar = frame.iloc[i + 1]
                next_vol = float(next_bar["volume"])
                avg_vol = float(row.get("volume_sma", next_vol))
                if avg_vol > 0 and next_vol / avg_vol < confirm_volume_mult * 0.5:
                    # Volume completely died — weak signal, skip
                    continue
            
            dir_str = "long" if direction > 0 else "short"
            best_signal = ImpulseSignal(
                direction=dir_str,
                entry_price=entry_price,
                stop_price=round(sl_price, 8),
                vol_z=round(vol_z, 2),
                ret_z=round(ret_z, 2),
                combined_score=round(combined, 2),
                timeframe=timeframe,
                impulse_bar_idx=i,
            )
    
    return best_signal


# ─── Multi-Timeframe Detection ───────────────────────────────

# Timeframe configs: (tf_label, klines_interval, lookback_bars, scan_bars)
TIMEFRAME_CONFIGS = [
    ("1h",  "60",  168,  5),   # 7 days lookback, scan last 5 bars
    ("30m", "30",  336,  8),   # 7 days lookback on 30m, scan last 8
    ("15m", "15",  672, 12),   # 7 days lookback on 15m, scan last 12
]


def detect_multi_tf(
    klines_fetcher,
    symbol: str,
    exchange: str,
    params: dict,
    cache: dict,
    cache_ttl: int = 300,
) -> Optional[ImpulseSignal]:
    """
    Scan multiple timeframes for volume impulse.
    Returns the strongest signal across all timeframes.
    
    Args:
        klines_fetcher: Callable(symbol, exchange, interval, limit) -> DataFrame
        symbol: Trading pair
        exchange: Exchange name
        params: v3 config params
        cache: Shared klines cache dict
        cache_ttl: Cache TTL in seconds (default 5 min)
    """
    now = time.time()
    best = None
    best_score = 0.0
    
    min_vol_z = params.get("min_volume_z", 4.0)
    min_ret_z = params.get("min_return_z", 3.0)
    min_combined = params.get("min_combined_score", 8.0)
    
    for tf_label, interval, lookback, scan_bars in TIMEFRAME_CONFIGS:
        cache_key = f"{symbol}:{exchange}:{interval}:v3"
        
        # Check cache
        cached = cache.get(cache_key)
        if cached and isinstance(cached, tuple):
            df, ts = cached
            if now - ts < cache_ttl:
                pass  # Use cached
            else:
                df = _safe_fetch(klines_fetcher, symbol, exchange, interval, lookback + 50)
                if df is not None:
                    cache[cache_key] = (df, now)
        else:
            df = _safe_fetch(klines_fetcher, symbol, exchange, interval, lookback + 50)
            if df is not None:
                cache[cache_key] = (df, now)
        
        if df is None or len(df) < lookback // 2:
            continue
        
        signal = detect_volume_impulse(
            df,
            min_vol_z=min_vol_z,
            min_ret_z=min_ret_z,
            min_combined=min_combined,
            lookback=lookback,
            timeframe=tf_label,
            scan_bars=scan_bars,
        )
        
        if signal and signal.combined_score > best_score:
            best_score = signal.combined_score
            best = signal
            # Use current price from the dataframe for entry
            best.entry_price = float(df["close"].iloc[-1])
    
    return best


def _safe_fetch(fetcher, symbol, exchange, interval, limit):
    """Fetch klines with error handling."""
    try:
        df = fetcher(symbol, exchange, interval, limit)
        if df is not None and not df.empty:
            return df
    except Exception as e:
        log.warning(f"v3 fetch {symbol}/{exchange}/{interval}: {e}")
    return None


# ─── Position Management ─────────────────────────────────────

def manage_v3_position(
    pos,  # Position dataclass from phases.py
    price: float,
    hourly_df: Optional[pd.DataFrame] = None,
    trail_pct: float = 0.15,
    trail_tight_pct: float = 0.08,
    breakeven_at_pct: float = 10.0,
    partial_exit_pct: float = 50.0,
    partial_exit_size: float = 0.30,
) -> Optional[str]:
    """
    Manage a v3 position with adaptive trailing stop.
    
    Returns: exit action string or None
        - "trailing_stop" — trail hit
        - "stop_loss" — initial SL hit
        - "v3_partial_30" — partial exit 30%
    """
    if pos.exited:
        return None
    
    is_long = pos.direction == "long"
    
    # Calculate PnL
    if is_long:
        profit_pct = (price / pos.entry_price - 1.0) * 100
    else:
        profit_pct = (pos.entry_price / price - 1.0) * 100 if price > 0 else 0
    pos.pnl_pct = profit_pct
    
    # Update peak/trough
    if is_long:
        if price > pos.peak_price:
            pos.peak_price = price
    else:
        if pos.dump_min <= 0 or price < pos.dump_min:
            pos.dump_min = price
    
    # Check initial stop loss
    if is_long and price <= pos.stop_loss:
        return "stop_loss"
    if not is_long and price >= pos.stop_loss:
        return "short_sl"
    
    # Breakeven: move SL to entry after +breakeven_at_pct%
    if profit_pct >= breakeven_at_pct and is_long:
        if pos.trailing_stop < pos.entry_price:
            pos.trailing_stop = pos.entry_price
            log.info(f"🔒 v3 Breakeven: {pos.symbol} SL → entry {pos.entry_price}")
    
    # Detect volume drying up → tighten trail
    active_trail = trail_pct
    if hourly_df is not None and len(hourly_df) >= 25:
        try:
            vol = hourly_df["volume"].astype(float)
            vol_sma = vol.rolling(20, min_periods=5).mean()
            recent_vol = float(vol.tail(3).mean())
            avg_vol = float(vol_sma.dropna().iloc[-1]) if len(vol_sma.dropna()) > 0 else 0
            if avg_vol > 0 and recent_vol < avg_vol * 0.5:
                active_trail = trail_tight_pct  # Tighten to 8%
        except Exception:
            pass
    
    # Trailing stop calculation
    if is_long:
        new_stop = pos.peak_price * (1 - active_trail)
        if new_stop > pos.trailing_stop:
            pos.trailing_stop = new_stop
        
        if price <= pos.trailing_stop:
            return "trailing_stop"
    else:
        # SHORT trailing: stop moves DOWN
        ref_price = pos.dump_min if pos.dump_min > 0 else pos.entry_price
        new_stop = ref_price * (1 + active_trail)
        if new_stop < pos.trailing_stop or pos.trailing_stop <= 0:
            pos.trailing_stop = new_stop
        
        if price >= pos.trailing_stop:
            return "short_trailing"
    
    # Partial exit at +partial_exit_pct%
    if profit_pct >= partial_exit_pct and pos.reversal_stage < 1:
        pos.reversal_stage = 1
        pos.remaining_qty_pct -= partial_exit_size
        pos.phase = 5
        return "v3_partial_30"
    
    return None
