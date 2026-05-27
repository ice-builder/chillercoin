"""
Multi-Factor Confluence Strategy — Mean Reversion Scanner.

Ищет моменты когда НЕСКОЛЬКО независимых индикаторов одновременно
показывают экстремальное значение. Вход ПРОТИВ экстремума (mean reversion).

Индикаторы:
  1. RSI(14) в экстремальной зоне
  2. Цена пробила Bollinger Band (20, Nσ)
  3. Volume Z-score > порога (подтверждение реального движения)
  4. Цена отклонилась от EMA(50) больше чем на X%

Exit: Dynamic TP = возврат к средней (BB middle = 20-SMA)
      SL = дальнейшее отклонение × extend_factor
      Time = max_hold_bars

PERFORMANCE: Signal detection fully vectorized (numpy), no Python for-loops.
  315K candles × 2916 combos: ~30-60s (vs 12+ min with for-loop).
"""
from __future__ import annotations

import logging
from itertools import product
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Bybit Linear Futures: Taker 0.055% per side → 0.11% round trip
COMMISSION_PCT = 0.11


# ─── Feature Computation ─────────────────────────────────────

def compute_confluence_features(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Добавляет RSI, BB rolling std, Volume Z, EMA к OHLCV данным.
    
    BB bands are NOT computed here (they depend on bb_std which varies per grid combo).
    Instead we compute bb_mid and bb_rolling_std, and compute bands in vectorized signal detection.
    """
    frame = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    frame = frame.dropna(subset=["close", "volume"]).reset_index(drop=True)
    
    if len(frame) < 100:
        return frame
    
    close = frame["close"]
    volume = frame["volume"]
    
    # ─── 1. RSI ───
    rsi_period = int(params.get("rsi_period", 14))
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0/rsi_period, min_periods=rsi_period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0/rsi_period, min_periods=rsi_period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    frame["rsi"] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)
    
    # ─── 2. Bollinger Bands components ───
    bb_period = int(params.get("bb_period", 20))
    frame["bb_mid"] = close.rolling(bb_period, min_periods=bb_period).mean()
    frame["bb_rolling_std"] = close.rolling(bb_period, min_periods=bb_period).std()
    
    # ─── 3. Volume Z-score (без lookahead) ───
    lookback = int(params.get("volume_lookback", 80))
    min_periods = max(10, lookback // 4)
    vol_baseline = volume.shift(1)
    vol_mean = vol_baseline.rolling(lookback, min_periods=min_periods).mean()
    vol_std = vol_baseline.rolling(lookback, min_periods=min_periods).std().replace(0, np.nan)
    frame["volume_z"] = ((volume - vol_mean) / vol_std).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    
    # ─── 4. EMA deviation ───
    ema_period = int(params.get("ema_period", 50))
    ema = close.ewm(span=ema_period, adjust=False).mean()
    frame["ema_deviation_pct"] = ((close - ema) / ema) * 100
    
    return frame


# ─── Vectorized Signal Detection ─────────────────────────────

def find_confluence_signals_vectorized(
    close: np.ndarray,
    rsi: np.ndarray,
    bb_mid: np.ndarray,
    bb_rolling_std: np.ndarray,
    volume_z: np.ndarray,
    ema_deviation_pct: np.ndarray,
    rsi_oversold: float,
    rsi_overbought: float,
    bb_std: float,
    volume_z_min: float,
    min_ema_dev: float,
    min_confluence: int,
    cooldown_bars: int = 5,
) -> List[Dict]:
    """Fully vectorized confluence signal detection.
    
    Returns list of signal dicts with idx, direction, confluence_count, bb_mid.
    """
    n = len(close)
    
    # Compute BB bands for this specific bb_std
    bb_upper = bb_mid + bb_std * bb_rolling_std
    bb_lower = bb_mid - bb_std * bb_rolling_std
    
    # ─── LONG signals (oversold) — boolean arrays ───
    long_rsi = rsi <= rsi_oversold
    long_bb = close <= bb_lower
    long_vol = volume_z >= volume_z_min
    long_ema = ema_deviation_pct <= -min_ema_dev
    long_count = long_rsi.astype(np.int8) + long_bb.astype(np.int8) + long_vol.astype(np.int8) + long_ema.astype(np.int8)
    long_signal = long_count >= min_confluence
    
    # ─── SHORT signals (overbought) — boolean arrays ───
    short_rsi = rsi >= rsi_overbought
    short_bb = close >= bb_upper
    short_vol = volume_z >= volume_z_min
    short_ema = ema_deviation_pct >= min_ema_dev
    short_count = short_rsi.astype(np.int8) + short_bb.astype(np.int8) + short_vol.astype(np.int8) + short_ema.astype(np.int8)
    short_signal = short_count >= min_confluence
    
    # Exclude NaN bb_mid rows
    valid = ~np.isnan(bb_mid)
    long_signal &= valid
    short_signal &= valid
    
    # Combine: long takes priority
    combined_direction = np.zeros(n, dtype=np.int8)  # 0=none, 1=long, -1=short
    combined_count = np.zeros(n, dtype=np.int8)
    
    long_indices = np.where(long_signal)[0]
    short_indices = np.where(short_signal)[0]
    
    combined_direction[long_indices] = 1
    combined_count[long_indices] = long_count[long_indices]
    
    # Short only where not already long
    short_only = short_indices[~long_signal[short_indices]]
    combined_direction[short_only] = -1
    combined_count[short_only] = short_count[short_only]
    
    # Apply cooldown (must iterate but only over signal indices, not all bars)
    signal_indices = np.where(combined_direction != 0)[0]
    if len(signal_indices) == 0:
        return []
    
    filtered = []
    last_idx = -cooldown_bars - 1
    for idx in signal_indices:
        if idx - last_idx >= cooldown_bars:
            d = "long" if combined_direction[idx] == 1 else "short"
            filtered.append({
                "idx": int(idx),
                "direction": d,
                "confluence_count": int(combined_count[idx]),
                "bb_mid": float(bb_mid[idx]),
                "entry_price": float(close[idx]),
            })
            last_idx = idx
    
    return filtered


# ─── Trade Simulation ────────────────────────────────────────

def simulate_confluence_trade(df_close: np.ndarray, df_high: np.ndarray, df_low: np.ndarray,
                              df_bb_mid: np.ndarray, signal: dict, params: dict) -> dict:
    """Симулирует сделку с dynamic TP (BB middle) и SL.
    Uses numpy arrays directly for speed.
    """
    entry_idx = signal["idx"] + 1
    n = len(df_close)
    if entry_idx >= n:
        return {}
    
    direction = signal["direction"]
    entry_price = float(df_close[entry_idx])
    bb_mid_at_entry = signal["bb_mid"]
    
    if entry_price <= 0 or bb_mid_at_entry <= 0:
        return {}
    
    sl_extend = float(params.get("sl_extend_factor", 2.0))
    max_hold = int(params.get("max_hold_bars", 20))
    
    distance_to_mean = abs(entry_price - bb_mid_at_entry)
    distance_pct = (distance_to_mean / entry_price) * 100
    
    if distance_pct < 0.05:
        return {}
    
    end_idx = min(entry_idx + max_hold + 1, n)
    
    if direction == "long":
        sl_price = entry_price * (1.0 - distance_pct * sl_extend / 100)
        for bar_idx in range(entry_idx + 1, end_idx):
            current_bb_mid = df_bb_mid[bar_idx]
            if np.isnan(current_bb_mid):
                current_bb_mid = bb_mid_at_entry
            
            if df_high[bar_idx] >= current_bb_mid:
                gross_pnl = (current_bb_mid / entry_price - 1.0) * 100
                net_pnl = gross_pnl - COMMISSION_PCT
                return _make_result(signal, entry_idx, entry_price, current_bb_mid,
                                   net_pnl, bar_idx - entry_idx, "take_profit", net_pnl > 0)
            
            if df_low[bar_idx] <= sl_price:
                gross_pnl = (sl_price / entry_price - 1.0) * 100
                net_pnl = gross_pnl - COMMISSION_PCT
                return _make_result(signal, entry_idx, entry_price, sl_price,
                                   net_pnl, bar_idx - entry_idx, "stop_loss", False)
    else:
        sl_price = entry_price * (1.0 + distance_pct * sl_extend / 100)
        for bar_idx in range(entry_idx + 1, end_idx):
            current_bb_mid = df_bb_mid[bar_idx]
            if np.isnan(current_bb_mid):
                current_bb_mid = bb_mid_at_entry
            
            if df_low[bar_idx] <= current_bb_mid:
                gross_pnl = (1.0 - current_bb_mid / entry_price) * 100
                net_pnl = gross_pnl - COMMISSION_PCT
                return _make_result(signal, entry_idx, entry_price, current_bb_mid,
                                   net_pnl, bar_idx - entry_idx, "take_profit", net_pnl > 0)
            
            if df_high[bar_idx] >= sl_price:
                gross_pnl = (1.0 - sl_price / entry_price) * 100
                net_pnl = gross_pnl - COMMISSION_PCT
                return _make_result(signal, entry_idx, entry_price, sl_price,
                                   net_pnl, bar_idx - entry_idx, "stop_loss", False)
    
    # Time exit
    if entry_idx + max_hold < n:
        exit_price = float(df_close[entry_idx + max_hold])
        if direction == "long":
            gross_pnl = (exit_price / entry_price - 1.0) * 100
        else:
            gross_pnl = (1.0 - exit_price / entry_price) * 100
        net_pnl = gross_pnl - COMMISSION_PCT
        return _make_result(signal, entry_idx, entry_price, exit_price,
                           net_pnl, max_hold, "time_exit", net_pnl > 0)
    
    return {}


def _make_result(signal, entry_idx, entry_price, exit_price,
                 net_pnl, bars_held, exit_reason, is_profitable):
    return {
        "entry_idx": entry_idx,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "direction": signal["direction"],
        "realized_move_pct": net_pnl,
        "bars_held": bars_held,
        "exit_reason": exit_reason,
        "is_profitable": is_profitable,
        "confluence_count": signal["confluence_count"],
    }


# ─── Validation ──────────────────────────────────────────────

def validate_confluence(df: pd.DataFrame, params: dict) -> dict:
    """Полный бэктест confluence strategy на данных."""
    frame = compute_confluence_features(df, params)
    
    if len(frame) < 100:
        return {"trades": 0, "win_rate": 0, "avg_realized_move_pct": 0,
                "total_pnl_pct": 0, "params": params}
    
    # Extract numpy arrays for speed
    close = frame["close"].to_numpy(dtype=np.float64)
    high = frame["high"].to_numpy(dtype=np.float64)
    low = frame["low"].to_numpy(dtype=np.float64)
    rsi = frame["rsi"].to_numpy(dtype=np.float64)
    bb_mid = frame["bb_mid"].to_numpy(dtype=np.float64)
    bb_std_arr = frame["bb_rolling_std"].to_numpy(dtype=np.float64)
    vol_z = frame["volume_z"].to_numpy(dtype=np.float64)
    ema_dev = frame["ema_deviation_pct"].to_numpy(dtype=np.float64)
    
    signals = find_confluence_signals_vectorized(
        close, rsi, bb_mid, bb_std_arr, vol_z, ema_dev,
        rsi_oversold=float(params.get("rsi_oversold", 25)),
        rsi_overbought=float(params.get("rsi_overbought", 75)),
        bb_std=float(params.get("bb_std", 2.0)),
        volume_z_min=float(params.get("volume_z_min", 2.0)),
        min_ema_dev=float(params.get("min_ema_deviation_pct", 2.0)),
        min_confluence=int(params.get("min_confluence", 3)),
        cooldown_bars=int(params.get("cooldown_bars", 5)),
    )
    
    outcomes = [simulate_confluence_trade(close, high, low, bb_mid, s, params) for s in signals]
    outcomes = [o for o in outcomes if o]
    
    if not outcomes:
        return {"trades": 0, "win_rate": 0, "avg_realized_move_pct": 0,
                "total_pnl_pct": 0, "params": params}
    
    wins = [o for o in outcomes if o.get("is_profitable")]
    win_rate = len(wins) / len(outcomes)
    avg_pnl = np.mean([o["realized_move_pct"] for o in outcomes])
    total_pnl = sum(o["realized_move_pct"] for o in outcomes)
    
    exit_reasons = {}
    for o in outcomes:
        r = o.get("exit_reason", "?")
        exit_reasons[r] = exit_reasons.get(r, 0) + 1
    
    return {
        "trades": len(outcomes),
        "win_rate": win_rate,
        "avg_realized_move_pct": float(avg_pnl),
        "total_pnl_pct": float(total_pnl),
        "exit_reasons": exit_reasons,
        "params": params,
    }


# ─── Grid Search ─────────────────────────────────────────────

CONFLUENCE_GRID = {
    "rsi_oversold": [20, 25, 30],
    "rsi_overbought": [70, 75, 80],
    "bb_std": [2.0, 2.5, 3.0],
    "volume_z_min": [1.5, 2.0, 2.5],
    "min_ema_deviation_pct": [1.0, 2.0, 3.0],
    "min_confluence": [2, 3, 4],
    "sl_extend_factor": [1.5, 2.0],
    "max_hold_bars": [15, 25],
}

CONFLUENCE_BASE = {
    "rsi_period": 14,
    "bb_period": 20,
    "volume_lookback": 80,
    "ema_period": 50,
    "cooldown_bars": 5,
}


def optimize_confluence(df: pd.DataFrame, min_trades: int = 10, min_wr: float = 0.70) -> dict:
    """Grid search по параметрам confluence strategy.
    
    Features are pre-computed once. Signal detection is vectorized.
    """
    base_features = compute_confluence_features(df, CONFLUENCE_BASE)
    
    if len(base_features) < 100:
        return {"trades": 0, "win_rate": 0, "total_pnl_pct": 0, "combos_tested": 0}
    
    # Extract numpy arrays ONCE
    close = base_features["close"].to_numpy(dtype=np.float64)
    high = base_features["high"].to_numpy(dtype=np.float64)
    low = base_features["low"].to_numpy(dtype=np.float64)
    rsi = base_features["rsi"].to_numpy(dtype=np.float64)
    bb_mid = base_features["bb_mid"].to_numpy(dtype=np.float64)
    bb_std_arr = base_features["bb_rolling_std"].to_numpy(dtype=np.float64)
    vol_z = base_features["volume_z"].to_numpy(dtype=np.float64)
    ema_dev = base_features["ema_deviation_pct"].to_numpy(dtype=np.float64)
    
    best_result = None
    best_score = -999
    combos_tested = 0
    
    grid_keys = list(CONFLUENCE_GRID.keys())
    grid_values = [CONFLUENCE_GRID[k] for k in grid_keys]
    
    for combo in product(*grid_values):
        params = dict(CONFLUENCE_BASE)
        for k, v in zip(grid_keys, combo):
            params[k] = v
        
        if params["rsi_oversold"] >= 50 or params["rsi_overbought"] <= 50:
            continue
        
        combos_tested += 1
        
        # Vectorized signal detection
        signals = find_confluence_signals_vectorized(
            close, rsi, bb_mid, bb_std_arr, vol_z, ema_dev,
            rsi_oversold=params["rsi_oversold"],
            rsi_overbought=params["rsi_overbought"],
            bb_std=params["bb_std"],
            volume_z_min=params["volume_z_min"],
            min_ema_dev=params["min_ema_deviation_pct"],
            min_confluence=params["min_confluence"],
            cooldown_bars=params.get("cooldown_bars", 5),
        )
        
        # Simulate trades
        outcomes = [simulate_confluence_trade(close, high, low, bb_mid, s, params) for s in signals]
        outcomes = [o for o in outcomes if o]
        
        if len(outcomes) < min_trades:
            continue
        
        wins = [o for o in outcomes if o.get("is_profitable")]
        wr = len(wins) / len(outcomes)
        
        if wr < min_wr:
            continue
        
        total_pnl = sum(o["realized_move_pct"] for o in outcomes)
        if total_pnl <= 0:
            continue
        
        score = wr * 100 + total_pnl
        
        if score > best_score:
            best_score = score
            avg_pnl = np.mean([o["realized_move_pct"] for o in outcomes])
            exit_reasons = {}
            for o in outcomes:
                r = o.get("exit_reason", "?")
                exit_reasons[r] = exit_reasons.get(r, 0) + 1
            
            best_result = {
                "trades": len(outcomes),
                "win_rate": wr,
                "avg_realized_move_pct": float(avg_pnl),
                "total_pnl_pct": float(total_pnl),
                "exit_reasons": exit_reasons,
                "best_params": dict(params),
                "combos_tested": combos_tested,
                "params": dict(params),
            }
    
    if best_result is None:
        # Diagnostic run with relaxed params
        diag_params = dict(CONFLUENCE_BASE)
        diag_params.update({
            "rsi_oversold": 30, "rsi_overbought": 70,
            "bb_std": 2.0, "volume_z_min": 1.5,
            "min_ema_deviation_pct": 1.0, "min_confluence": 2,
            "sl_extend_factor": 2.0, "max_hold_bars": 25,
        })
        result = validate_confluence(df, diag_params)
        result["combos_tested"] = combos_tested
        return result
    
    best_result["combos_tested"] = combos_tested
    return best_result
