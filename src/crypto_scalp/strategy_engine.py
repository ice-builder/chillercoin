"""
Impulse Scalper Core Strategy Engine.
Shared logic for Research App, Paper Trader, and Auto-Optimizer.
"""
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger("strategy_engine")

# Bybit Linear Futures: Taker 0.055% per side → 0.11% round trip
COMMISSION_PCT = 0.11

def rolling_zscore(series: pd.Series, lookback: int) -> pd.Series:
    """Standard z-score without lookahead bias."""
    baseline = series.shift(1)
    mean = baseline.rolling(lookback, min_periods=max(10, lookback // 4)).mean()
    std = baseline.rolling(lookback, min_periods=max(10, lookback // 4)).std().replace(0, np.nan)
    return ((series - mean) / std).replace([np.inf, -np.inf], np.nan).fillna(0.0)

def prepare_features(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Calculate Z-scores and EMAs for the strategy."""
    frame = df.copy()
    lookback = int(params.get("lookback_bars", 100))
    ema_period = int(params.get("trend_ema_period", 50))

    # Metrics
    frame["ret_pct"] = frame["close"].pct_change() * 100
    frame["abs_ret"] = frame["ret_pct"].abs()
    frame["dollar_volume"] = frame["close"] * frame["volume"]
    
    # Z-scores
    frame["dollar_volume_z"] = rolling_zscore(frame["dollar_volume"], lookback)
    frame["abs_ret_z"] = rolling_zscore(frame["abs_ret"], lookback)
    
    # Direction
    frame["direction"] = 0
    frame.loc[frame["ret_pct"] > 0, "direction"] = 1
    frame.loc[frame["ret_pct"] < 0, "direction"] = -1
    
    # Trend Filter
    if ema_period > 0:
        frame["trend_ema"] = frame["close"].ewm(span=ema_period, adjust=False).mean()
    
    return frame

def detect_live_signal(frame: pd.DataFrame, params: dict) -> dict:
    """
    Checks if the last completed candle (index -2) triggers an impulse.
    Then checks if current price (index -1) meets the pullback entry requirement.
    """
    if len(frame) < 5: return {}
    
    # We trigger on the LAST COMPLETED candle
    trigger_row = frame.iloc[-2]
    current_row = frame.iloc[-1]
    
    min_volume_z = float(params["min_dollar_volume_z"])
    min_ret_z = float(params["min_price_return_z"])
    direction = int(trigger_row["direction"])
    
    # 1. Impulse Detection
    is_impulse = (
        trigger_row["dollar_volume_z"] >= min_volume_z and 
        trigger_row["abs_ret_z"] >= min_ret_z and 
        direction != 0
    )
    
    if not is_impulse: return {}
    
    # 2. Trend Filter
    if "trend_ema" in frame.columns:
        ema = float(trigger_row["trend_ema"])
        if direction > 0 and float(trigger_row["close"]) < ema: return {}
        if direction < 0 and float(trigger_row["close"]) > ema: return {}

    # 3. Entry Logic (Pullback)
    close_at_impulse = float(trigger_row["close"])
    open_at_impulse = float(trigger_row["open"])
    pullback_pct = float(params.get("entry_pullback_pct", 0.0))
    
    # Entry price is current price IF it pulled back enough
    entry_price = close_at_impulse
    if pullback_pct > 0:
        if direction > 0:
            entry_price = close_at_impulse - (close_at_impulse - open_at_impulse) * pullback_pct
        else:
            entry_price = close_at_impulse + (open_at_impulse - close_at_impulse) * pullback_pct

    # Live check: Did current low/high hit the entry price?
    curr_low = float(current_row["low"])
    curr_high = float(current_row["high"])
    
    is_filled = False
    if direction > 0 and curr_low <= entry_price: is_filled = True
    if direction < 0 and curr_high >= entry_price: is_filled = True
    
    if not is_filled: return {}

    # 4. Stop Loss & Take Profit
    stop_pct = float(params["fixed_stop_loss_pct"])
    if params.get("use_dynamic_stop", False):
        if direction > 0:
            impulse_low = float(trigger_row["low"])
            stop_pct = max(0.1, (entry_price - impulse_low) / entry_price * 100)
        else:
            impulse_high = float(trigger_row["high"])
            stop_pct = max(0.1, (impulse_high - entry_price) / entry_price * 100)

    tp_rr = float(params["take_profit_rr"])
    tp_pct = stop_pct * tp_rr
    
    if direction > 0:
        stop_price = entry_price * (1 - stop_pct / 100)
        tp_price = entry_price * (1 + tp_pct / 100)
    else:
        stop_price = entry_price * (1 + stop_pct / 100)
        tp_price = entry_price * (1 - tp_pct / 100)

    return {
        "direction": "long" if direction > 0 else "short",
        "entry_price": round(entry_price, 6),
        "stop_price": round(stop_price, 6),
        "tp_price": round(tp_price, 6),
        "stop_pct": round(stop_pct, 4),
        "tp_pct": round(tp_pct, 4),
    }

def simulate_backtest(frame: pd.DataFrame, params: dict) -> list[dict]:
    """Efficiently runs the strategy over a historical dataframe."""
    lookback = int(params["lookback_bars"])
    ema_period = int(params.get("trend_ema_period", 50))
    min_volume_z = float(params["min_dollar_volume_z"])
    min_ret_z = float(params["min_price_return_z"])
    pullback_pct = float(params.get("entry_pullback_pct", 0.0))
    max_hold = int(params.get("max_hold_bars", 50))
    tp_rr = float(params.get("take_profit_rr", 3.0))
    be_rr = float(params.get("breakeven_at_rr", 0.3))
    partial_tp = bool(params.get("partial_tp_at_be", True))
    fixed_sl = float(params.get("fixed_stop_loss_pct", 0.35))
    dyn_sl = bool(params.get("use_dynamic_stop", True))

    trades = []
    i = lookback + 1
    while i < len(frame) - max_hold - 1:
        trigger = frame.iloc[i-1]
        direction = int(trigger["direction"])
        
        # Impulse check
        if trigger["dollar_volume_z"] < min_volume_z or trigger["abs_ret_z"] < min_ret_z or direction == 0:
            i += 1
            continue
            
        # Trend check
        if ema_period > 0 and "trend_ema" in frame.columns:
            ema = float(trigger["trend_ema"])
            if direction > 0 and float(trigger["close"]) < ema: 
                i += 1; continue
            if direction < 0 and float(trigger["close"]) > ema: 
                i += 1; continue

        # Entry (Pullback)
        imp_open, imp_close = float(trigger["open"]), float(trigger["close"])
        entry_target = imp_close
        if pullback_pct > 0:
            if direction > 0: entry_target = imp_close - (imp_close - imp_open) * pullback_pct
            else: entry_target = imp_close + (imp_open - imp_close) * pullback_pct
        
        # Check if filled in next bar(s)
        filled_idx = -1
        for j in range(i, i + 3): # Check next 3 bars for entry
            if j >= len(frame): break
            bar = frame.iloc[j]
            if direction > 0 and float(bar["low"]) <= entry_target:
                filled_idx = j; break
            if direction < 0 and float(bar["high"]) >= entry_target:
                filled_idx = j; break
        
        if filled_idx == -1:
            i += 1; continue
            
        # SL/TP
        if dyn_sl:
            if direction > 0: sl_pct = max(0.1, (entry_target - float(trigger["low"])) / entry_target * 100)
            else: sl_pct = max(0.1, (float(trigger["high"]) - entry_target) / entry_target * 100)
        else:
            sl_pct = fixed_sl
            
        tp_pct = sl_pct * tp_rr
        be_pct = sl_pct * be_rr
        
        # Manage trade
        realized = 0.0
        exit_reason = "time"
        be_active = False
        partial_done = False
        
        for j in range(filled_idx + 1, min(len(frame), filled_idx + max_hold)):
            bar = frame.iloc[j]
            high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
            
            if direction > 0:
                favorable = (high / entry_target - 1.0) * 100
                adverse = (1.0 - low / entry_target) * 100
                final = (close / entry_target - 1.0) * 100
            else:
                favorable = (1.0 - low / entry_target) * 100
                adverse = (high / entry_target - 1.0) * 100
                final = (1.0 - close / entry_target) * 100
            
            if favorable >= be_pct and not be_active:
                be_active = True
                if partial_tp:
                    partial_done = True
                    realized += be_pct * 0.5
            
            sl_trigger = sl_pct if not be_active else -0.05
            if adverse >= sl_trigger:
                if partial_done: realized += -sl_trigger * 0.5
                else: realized = -sl_trigger
                exit_reason = "stop" if not be_active else "breakeven"
                break
            
            if favorable >= tp_pct:
                if partial_done: realized += tp_pct * 0.5
                else: realized = tp_pct
                exit_reason = "tp"
                break
                
            realized = final
            
        trades.append({"symbol": frame.get("symbol", "??"), "pnl": realized - COMMISSION_PCT, "win": (realized - COMMISSION_PCT) > 0, "reason": exit_reason})
        i = j + 1
        
    return trades
