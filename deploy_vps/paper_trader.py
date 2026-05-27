"""
Self-contained Multi-Symbol Paper Trader v8.2 for Impulse Scalper.
Optimized for VPS deployment (no internal package dependencies).

v8.2: Safety hardening + wider stops (2026-05-20)
    - CRITICAL FIX: Exchange close failure no longer removes position from state
      (prevents orphaned positions sitting on exchange without bot tracking)
    - WIDER STOPS: max_stop 4%→6%, min_stop 0.6%→1.0%, ATR mult 2.5→3.0
      (data: too-tight stops caused frequent noise stop-outs on alts)
    - Position stays in active_positions until exchange close is VERIFIED
    - Automatic retry on next tick if close fails

v8.0: IIE-trailing-only exits (2026-05-19)
    - EXIT OVERHAUL: ONLY iie_trailing_stop and take_profit exits remain
    - DISABLED: time_exit (12% WR, -20.64% on 49 trades — silent PnL killer)
    - DISABLED: atr_stop/fixed_stop (0% WR, -4.59% on 3 trades)
    - NEW: catastrophic_stop at 2× original SL as black-swan safety net
    - NEW: Testnet symbol validation — prevents phantom trades on missing pairs
    - Breakeven exit still active for partial-TP positions

v7.0: Data-driven optimization (2026-05-14)
    - IIE min score raised: 60→70 (score 60-69 was net -$5.38 on 27 trades)
    - Legacy strategy pack DISABLED (22% WR, -$5.35 on 11 trades)
    - fixed_stop REPLACED with dynamic ATR stop (fixed_stop had 0% WR, -$10.95)
    - max_hold_bars increased: 30→50 for IIE trades (time_exit losing money at 20 bars)
    - IIE-only signal pipeline: all entries must pass IIE score ≥70

v6.0: Full IIE integration & feedback loop (2026-05-13)
    - Signal source: pending_signals table (pre-evaluated by IIE SignalEngine)
    - IIE-adapted SL/TP/trail/size from AdaptivePositionManager
    - NEW: Trailing stop based on IIE trail_pct
    - Feedback loop: trade outcomes recorded to IIE for ML retraining
    - Strategy pack signals gated through IIE evaluate_signal()
    - IIE manager singleton (no per-loop re-creation)
    - Removed duplicate ML confidence gate (relies on IIE score)

v5.0: IIE-driven signal intake (2026-05-11)
    - PRIMARY signal source: IIE impulse database (score>=60, vol_z>=10, TF 15m/60m)
    - REMOVED: retest_5m and default_zscore fallback strategies (0-36% WR, net losers)
    - ATR stops widened: ATR(14) × 2.0 (was 1.5) — reduce noise stop-outs
    - min_stop_pct raised: 0.50% (was 0.30%) — give trades breathing room
    - Strategy pack signals still active for symbol-specific hypotheses

v4.0: Strategic overhaul (2026-05-09)
    - BTC macro-trend bias: EMA20 vs EMA50 on 1h → LONG-only in uptrend, SHORT-only in downtrend
    - ATR-based dynamic stops: SL = ATR(14) × 1.5, scaled per-symbol volatility
    - Cooldown after SL: 10min→30min; SL streak cooldown: 4h→8h

v3.5: Anti-bleed hardening (2026-05-06)
v3.3: Fix breakeven drain & strategy pack activation
v3.2: Profit maximization & signal quality
v3.1: Exit price accuracy & duplicate prevention
v3: Risk management hardening
"""
import json
import time
import logging
import signal
import sys
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

# Import exchange executor (optional — works without it in paper mode)
try:
    from exchange_executor import ExchangeExecutor
except ImportError:
    ExchangeExecutor = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("PaperTrader")

# ─── Telegram Notifications ──────────────────────────────────
class TelegramNotifier:
    """Sends trade notifications via Telegram Bot API."""

    def __init__(self, token: str = "", chat_id: str = ""):
        self.token = token or os.getenv("TELEGRAM_SCALPER_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.token and self.chat_id)
        if self.enabled:
            logger.info(f"📱 Telegram notifications ON (chat: {self.chat_id[:6]}...)")
        else:
            logger.info("📱 Telegram notifications OFF (no token/chat_id)")

    def send(self, text: str):
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            resp = requests.post(
                url,
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning(f"Telegram API error: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")


# ─── Signal Hub Integration ──────────────────────────────────
# Sends signals to OneProp Signal Hub for TG group posting + website tracking.
# Signal Hub runs on same VPS at localhost:8090.
# Does NOT affect Soldier trading logic — fire-and-forget HTTP calls.

SIGNAL_HUB_URL = os.getenv("SIGNAL_HUB_URL", "http://localhost:8090")
SIGNAL_HUB_SECRET = os.getenv("SIGNAL_HUB_API_SECRET", "")

def post_signal_to_hub(pos, score: float = 0):
    """Notify Signal Hub about a new trade opened by Soldier.
    Fire-and-forget — errors are logged but never block trading."""
    if not SIGNAL_HUB_SECRET:
        return
    try:
        requests.post(f"{SIGNAL_HUB_URL}/api/signals", json={
            "source": "soldier",
            "signal_type": pos.strategy_name,
            "symbol": pos.symbol,
            "exchange": "bybit",
            "direction": pos.direction,
            "price_at_signal": pos.entry_price,
            "entry_target": pos.entry_price,
            "exit_target": pos.tp_price,
            "strength": min(score / 100, 1.0) if score else 0.5,
            "description": f"IIE Score: {score:.0f}" if score else pos.strategy_name,
            "metadata": {"stop_price": pos.stop_price, "strategy_id": pos.strategy_id},
        }, headers={"X-API-Key": SIGNAL_HUB_SECRET}, timeout=5)
        logger.info(f"📡 Signal Hub: opened {pos.symbol} {pos.direction}")
    except Exception as e:
        logger.warning(f"Signal Hub post failed (non-critical): {e}")


def post_close_to_hub(pos):
    """Notify Signal Hub about a trade closed by Soldier.
    Fire-and-forget — errors are logged but never block trading."""
    if not SIGNAL_HUB_SECRET:
        return
    try:
        requests.post(f"{SIGNAL_HUB_URL}/api/signals/close", json={
            "symbol": pos.symbol,
            "direction": pos.direction,
            "entry_price": pos.entry_price,
            "exit_price": pos.exit_price,
            "exit_reason": pos.exit_reason,
            "pnl_pct": pos.realized_pnl_pct,
            "bars_held": pos.bars_held,
        }, headers={"X-API-Key": SIGNAL_HUB_SECRET}, timeout=5)
        logger.info(f"📡 Signal Hub: closed {pos.symbol} P&L={pos.realized_pnl_pct:+.2f}%")
    except Exception as e:
        logger.warning(f"Signal Hub close failed (non-critical): {e}")

# ─── Strategy Logic (Self-Contained) ─────────────────────────
def rolling_zscore(series: pd.Series, lookback: int) -> pd.Series:
    baseline = series.shift(1)
    mean = baseline.rolling(lookback, min_periods=max(10, lookback // 4)).mean()
    std = baseline.rolling(lookback, min_periods=max(10, lookback // 4)).std().replace(0, np.nan)
    return ((series - mean) / std).replace([np.inf, -np.inf], np.nan).fillna(0.0)

def prepare_features(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    frame = df.copy()
    lookback = int(params.get("lookback_bars", 100))
    ema_period = int(params.get("trend_ema_period", 50))
    frame["ret_pct"] = frame["close"].pct_change() * 100
    frame["abs_ret"] = frame["ret_pct"].abs()
    frame["dollar_volume"] = frame["close"] * frame["volume"]
    frame["dollar_volume_z"] = rolling_zscore(frame["dollar_volume"], lookback)
    frame["abs_ret_z"] = rolling_zscore(frame["abs_ret"], lookback)
    frame["direction"] = 0
    frame.loc[frame["ret_pct"] > 0, "direction"] = 1
    frame.loc[frame["ret_pct"] < 0, "direction"] = -1
    if ema_period > 0:
        frame["trend_ema"] = frame["close"].ewm(span=ema_period, adjust=False).mean()
    return frame

def detect_live_signal(frame: pd.DataFrame, params: dict) -> dict:
    if len(frame) < 5: return {}
    trigger_row = frame.iloc[-2]
    current_row = frame.iloc[-1]
    min_volume_z = float(params["min_dollar_volume_z"])
    min_ret_z = float(params["min_price_return_z"])
    impulse_dir = int(trigger_row["direction"])
    is_impulse = (
        trigger_row["dollar_volume_z"] >= min_volume_z and 
        trigger_row["abs_ret_z"] >= min_ret_z and 
        impulse_dir != 0
    )
    if not is_impulse: return {}
    
    # Strategy mode: continuation (trade WITH impulse) or reversal (trade AGAINST)
    mode = params.get("strategy_mode", "continuation")
    if mode == "reversal":
        direction = -impulse_dir  # Flip direction for mean-reversion
    else:
        direction = impulse_dir
    
    # v3.2: Dual-EMA trend filter (EMA20 vs EMA50)
    if mode != "reversal" and len(frame) >= 50:
        ema20 = frame["close"].ewm(span=20, adjust=False).mean()
        ema50 = frame["close"].ewm(span=50, adjust=False).mean()
        last_ema20 = float(ema20.iloc[-1])
        last_ema50 = float(ema50.iloc[-1])
        close_price = float(trigger_row["close"])
        # Long: price > EMA50 AND EMA20 > EMA50 (uptrend)
        if direction > 0 and (close_price < last_ema50 or last_ema20 < last_ema50): return {}
        # Short: price < EMA50 AND EMA20 < EMA50 (downtrend)
        if direction < 0 and (close_price > last_ema50 or last_ema20 > last_ema50): return {}
    close_at_impulse = float(trigger_row["close"])
    open_at_impulse = float(trigger_row["open"])
    pullback_pct = float(params.get("entry_pullback_pct", 0.0))
    entry_price = close_at_impulse
    if pullback_pct > 0:
        if direction > 0:
            entry_price = close_at_impulse - (close_at_impulse - open_at_impulse) * pullback_pct
        else:
            entry_price = close_at_impulse + (open_at_impulse - close_at_impulse) * pullback_pct
    curr_low = float(current_row["low"])
    curr_high = float(current_row["high"])
    is_filled = False
    if direction > 0 and curr_low <= entry_price: is_filled = True
    if direction < 0 and curr_high >= entry_price: is_filled = True
    if not is_filled: return {}
    stop_pct = float(params["fixed_stop_loss_pct"])
    # v4.0: ATR-based dynamic stops (preferred over impulse-wick based)
    atr_value = params.get("_atr_value")  # Pre-computed ATR injected by caller
    if atr_value and atr_value > 0 and entry_price > 0:
        atr_stop_mult = float(params.get("atr_stop_multiplier", 1.5))
        stop_pct = max(
            float(params.get("min_stop_pct", 0.30)),
            (atr_value * atr_stop_mult / entry_price) * 100
        )
    elif params.get("use_dynamic_stop", False):
        if direction > 0:
            impulse_low = float(trigger_row["low"])
            stop_pct = max(0.1, (entry_price - impulse_low) / entry_price * 100)
        else:
            impulse_high = float(trigger_row["high"])
            stop_pct = max(0.1, (impulse_high - entry_price) / entry_price * 100)
    # v3: Hard cap on stop loss to prevent catastrophic losses on volatile coins
    max_stop = float(params.get("max_stop_loss_pct", 2.00))
    if stop_pct > max_stop:
        stop_pct = max_stop
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

# ─── Confluence Strategy (Self-Contained) ─────────────────────
def compute_confluence_features(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """RSI + BB + Volume Z + EMA deviation. All vectorized."""
    frame = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    if len(frame) < 80:
        return frame
    close = frame["close"]
    volume = frame["volume"]

    # RSI
    rsi_period = int(params.get("rsi_period", 14))
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0/rsi_period, min_periods=rsi_period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0/rsi_period, min_periods=rsi_period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    frame["rsi"] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

    # Bollinger Bands
    bb_period = int(params.get("bb_period", 20))
    frame["bb_mid"] = close.rolling(bb_period, min_periods=bb_period).mean()
    frame["bb_std"] = close.rolling(bb_period, min_periods=bb_period).std()

    # Volume Z-score
    lookback = int(params.get("volume_lookback", 80))
    vol_baseline = volume.shift(1)
    vol_mean = vol_baseline.rolling(lookback, min_periods=max(10, lookback // 4)).mean()
    vol_std = vol_baseline.rolling(lookback, min_periods=max(10, lookback // 4)).std().replace(0, np.nan)
    frame["volume_z"] = ((volume - vol_mean) / vol_std).replace([np.inf, -np.inf], np.nan).fillna(0.0)

    # EMA deviation
    ema_period = int(params.get("ema_period", 50))
    ema = close.ewm(span=ema_period, adjust=False).mean()
    frame["ema_deviation_pct"] = ((close - ema) / ema) * 100

    return frame


def detect_confluence_signal(frame: pd.DataFrame, params: dict) -> dict:
    """Check the LAST completed bar for confluence signal."""
    if len(frame) < 80:
        return {}

    bb_std_mult = float(params.get("bb_std", 2.5))
    rsi_oversold = float(params.get("rsi_oversold", 25))
    rsi_overbought = float(params.get("rsi_overbought", 75))
    volume_z_min = float(params.get("volume_z_min", 2.0))
    min_ema_dev = float(params.get("min_ema_deviation_pct", 2.0))
    min_confluence = int(params.get("min_confluence", 4))
    sl_extend = float(params.get("sl_extend_factor", 2.0))

    row = frame.iloc[-2]  # Last COMPLETED bar
    current = frame.iloc[-1]  # Current forming bar (for entry)

    rsi = row.get("rsi", 50)
    close = float(row["close"])
    bb_mid = row.get("bb_mid", np.nan)
    bb_std_val = row.get("bb_std", np.nan)
    vol_z = row.get("volume_z", 0)
    ema_dev = row.get("ema_deviation_pct", 0)

    if pd.isna(bb_mid) or pd.isna(bb_std_val):
        return {}

    bb_upper = bb_mid + bb_std_mult * bb_std_val
    bb_lower = bb_mid - bb_std_mult * bb_std_val

    # Check LONG (oversold)
    long_count = sum([
        rsi <= rsi_oversold,
        close <= bb_lower,
        vol_z >= volume_z_min,
        ema_dev <= -min_ema_dev,
    ])

    if long_count >= min_confluence:
        entry_price = float(current["close"])
        distance_pct = abs(entry_price - bb_mid) / entry_price * 100
        if distance_pct < 0.05:
            return {}
        tp_price = float(bb_mid)
        tp_pct = (tp_price / entry_price - 1.0) * 100
        sl_pct = distance_pct * sl_extend
        # v3: Hard cap on stop loss
        max_stop = float(params.get("max_stop_loss_pct", 0.50))
        sl_pct = min(sl_pct, max_stop)
        stop_price = entry_price * (1.0 - sl_pct / 100)
        return {
            "direction": "long",
            "entry_price": round(entry_price, 6),
            "stop_price": round(stop_price, 6),
            "tp_price": round(tp_price, 6),
            "stop_pct": round(sl_pct, 4),
            "tp_pct": round(tp_pct, 4),
            "bb_mid": float(bb_mid),
            "confluence_count": long_count,
            "strategy_type": "confluence",
        }

    # Check SHORT (overbought)
    short_count = sum([
        rsi >= rsi_overbought,
        close >= bb_upper,
        vol_z >= volume_z_min,
        ema_dev >= min_ema_dev,
    ])

    if short_count >= min_confluence:
        entry_price = float(current["close"])
        distance_pct = abs(entry_price - bb_mid) / entry_price * 100
        if distance_pct < 0.05:
            return {}
        tp_price = float(bb_mid)
        tp_pct = (1.0 - tp_price / entry_price) * 100
        sl_pct = distance_pct * sl_extend
        # v3: Hard cap on stop loss
        max_stop = float(params.get("max_stop_loss_pct", 0.50))
        sl_pct = min(sl_pct, max_stop)
        stop_price = entry_price * (1.0 + sl_pct / 100)
        return {
            "direction": "short",
            "entry_price": round(entry_price, 6),
            "stop_price": round(stop_price, 6),
            "tp_price": round(tp_price, 6),
            "stop_pct": round(sl_pct, 4),
            "tp_pct": round(tp_pct, 4),
            "bb_mid": float(bb_mid),
            "confluence_count": short_count,
            "strategy_type": "confluence",
        }

    return {}


# ─── Retest After Impulse Strategy ───────────────────────────
def detect_retest_signal(frame: pd.DataFrame, params: dict) -> dict:
    """
    Retest-after-Impulse strategy (v1):
      1. Scan last `retest_impulse_lookback` bars for a strong impulse
         (high volume z-score AND high return z-score).
      2. Define retest zone at `retest_zone_fib` (default 0.5 = 50%) of the impulse body.
      3. If the CURRENT bar's low (long) or high (short) touches the retest zone
         within `retest_tolerance_pct`, generate an entry signal in the IMPULSE direction.
      4. SL = below impulse low (long) / above impulse high (short).
      5. Invalidate if more than `retest_max_wait_bars` have passed since the impulse.
    """
    lookback = int(params.get("lookback_bars", 100))
    if len(frame) < lookback + 5:
        return {}

    # Ensure features are computed
    if "dollar_volume_z" not in frame.columns:
        frame = prepare_features(frame, params)

    min_vol_z   = float(params.get("min_dollar_volume_z", 3.0))
    min_ret_z   = float(params.get("min_price_return_z", 2.5))
    imp_lb      = int(params.get("retest_impulse_lookback", 8))    # bars to look back
    fib_level   = float(params.get("retest_zone_fib", 0.50))       # 50% Fibonacci
    tol_pct     = float(params.get("retest_tolerance_pct", 0.20))  # ±0.20% around zone
    max_wait    = int(params.get("retest_max_wait_bars", 10))       # invalidate after N bars
    max_stop    = float(params.get("max_stop_loss_pct", 2.00))
    tp_rr       = float(params.get("take_profit_rr", 2.0))

    # Search window: last imp_lb completed bars (not including current)
    n = len(frame)
    search_end = n - 2          # last completed bar index
    search_start = max(0, search_end - imp_lb)

    best_idx   = None
    best_score = 0.0

    for i in range(search_start, search_end):
        row = frame.iloc[i]
        vol_z = float(row.get("dollar_volume_z", 0))
        ret_z = float(row.get("abs_ret_z", 0))
        direction = int(row.get("direction", 0))
        if vol_z >= min_vol_z and ret_z >= min_ret_z and direction != 0:
            score = vol_z + ret_z
            if score > best_score:
                best_score = score
                best_idx   = i

    if best_idx is None:
        return {}

    bars_since = (n - 1) - best_idx
    if bars_since > max_wait:
        return {}

    impulse     = frame.iloc[best_idx]
    imp_dir     = int(impulse["direction"])          # +1 long, -1 short
    imp_open    = float(impulse["open"])
    imp_close   = float(impulse["close"])
    imp_high    = float(impulse["high"])
    imp_low     = float(impulse["low"])
    body        = abs(imp_close - imp_open)

    if body < 1e-8:
        return {}    # flat candle — skip

    # Retest zone price (50% of body from the impulse base)
    if imp_dir > 0:   # bullish impulse — long setup
        # zone = open + body * fib_level  (retrace down into lower half of body)
        zone_price = imp_open + body * fib_level
    else:             # bearish impulse — short setup
        zone_price = imp_open - body * fib_level

    tol_abs = zone_price * tol_pct / 100.0
    current  = frame.iloc[-1]    # current forming bar (price data for live check)
    curr_high = float(current["high"])
    curr_low  = float(current["low"])
    curr_close = float(current["close"])

    # Check if current bar touches the retest zone
    if imp_dir > 0:   # long: price pulls back DOWN into zone
        touched = curr_low <= (zone_price + tol_abs)
        entry_price = zone_price + tol_abs   # slight buffer above zone
        still_above_zone = curr_close >= (zone_price - tol_abs)
        if not (touched and still_above_zone):
            return {}
        sl_price  = imp_low * (1 - 0.05 / 100)   # just below impulse wick
        direction_str = "long"
    else:             # short: price bounces UP into zone
        touched = curr_high >= (zone_price - tol_abs)
        entry_price = zone_price - tol_abs   # slight buffer below zone
        still_below_zone = curr_close <= (zone_price + tol_abs)
        if not (touched and still_below_zone):
            return {}
        sl_price  = imp_high * (1 + 0.05 / 100)  # just above impulse wick
        direction_str = "short"

    entry_price = round(max(entry_price, 1e-8), 6)

    if direction_str == "long":
        stop_pct = (entry_price - sl_price) / entry_price * 100
    else:
        stop_pct = (sl_price - entry_price) / entry_price * 100

    stop_pct = max(0.10, min(stop_pct, max_stop))
    tp_pct   = stop_pct * tp_rr

    if direction_str == "long":
        stop_price = entry_price * (1 - stop_pct / 100)
        tp_price   = entry_price * (1 + tp_pct  / 100)
    else:
        stop_price = entry_price * (1 + stop_pct / 100)
        tp_price   = entry_price * (1 - tp_pct  / 100)

    return {
        "direction":          direction_str,
        "entry_price":        round(entry_price, 6),
        "stop_price":         round(stop_price,  6),
        "tp_price":           round(tp_price,    6),
        "stop_pct":           round(stop_pct,    4),
        "tp_pct":             round(tp_pct,      4),
        "impulse_strength":   round(best_score,  2),
        "bars_since_impulse": bars_since,
        "strategy_type":      "retest",
    }


# ─── Strategy Pack ────────────────────────────────────────────
def load_strategy_pack(path: Path) -> List[Dict]:
    """Load strategy pack JSON. Returns list of strategy dicts."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        strategies = data.get("strategies", [])
        logger.info(f"📦 Loaded strategy pack: {len(strategies)} strategies")
        return strategies
    except Exception as e:
        logger.warning(f"Failed to load strategy pack: {e}")
        return []

def detect_signal_with_strategy(frame: pd.DataFrame, strategy: Dict) -> dict:
    """Detect signal using a specific strategy's parameters."""
    params = strategy.get("params", {})
    if not params:
        return {}
    # Apply strategy-specific params, with defaults from DEFAULT_PARAMS
    merged = dict(DEFAULT_PARAMS)
    merged.update(params)
    sig = detect_live_signal(frame, merged)
    if sig:
        sig["strategy_id"] = strategy.get("hypothesis_id", 0)
        sig["strategy_name"] = strategy.get("name", "unknown")
        sig["strategy_win_rate"] = strategy.get("win_rate", 0.0)
    return sig

def find_best_signal(klines_cache: Dict[str, pd.DataFrame], symbol: str, strategies: List[Dict], default_params: dict) -> dict:
    """Check all strategies for a symbol. Return best signal.

    klines_cache: dict of interval -> DataFrame (e.g. {"5": df_5m, "15": df_15m, "60": df_1h})
    """
    best_signal = {}
    best_score  = 0.0

    for strategy in strategies:
        strat_symbol = strategy.get("symbol", "*")
        if strat_symbol != "*" and strat_symbol != symbol:
            continue

        strat_params = strategy.get("params", {})
        strat_type   = strat_params.get("strategy_type", "zscore")
        is_confluence = strat_type == "confluence" or "confluence" in strategy.get("name", "").lower()
        is_retest     = strat_type == "retest"

        # Determine which timeframe this strategy needs
        tf       = strategy.get("timeframe", "5m")
        interval = TF_TO_INTERVAL.get(tf, "5")
        frame    = klines_cache.get(interval)
        if frame is None or frame.empty:
            continue

        merged = dict(default_params)
        merged.update(strat_params)

        if is_retest:
            rt_frame = prepare_features(frame, merged)
            sig = detect_retest_signal(rt_frame, merged)
        elif is_confluence:
            conf_frame = compute_confluence_features(frame, strat_params)
            sig = detect_confluence_signal(conf_frame, strat_params)
        else:
            strat_frame = prepare_features(frame, merged)
            sig = detect_live_signal(strat_frame, merged)

        if sig:
            sig["strategy_id"]       = strategy.get("hypothesis_id", 0)
            sig["strategy_name"]     = strategy.get("name", "unknown")
            sig["strategy_win_rate"] = strategy.get("win_rate", 0.0)
            score = strategy.get("win_rate", 0.5) * 100
            if score > best_score:
                best_score  = score
                best_signal = sig

    # v5.0: REMOVED retest_5m and default_zscore fallbacks
    # These strategies had 0-36% WR and were net losers (-4.5% combined).
    # Signal intake now comes from IIE impulse database (see fetch_iie_signals).

    return best_signal


# ─── IIE Pending Signal Reader (v7.0) ────────────────────────
# v7.0: Min score raised from 60 to 70 — score 60-69 was net -$5.38 on 27 trades
IIE_MIN_SCORE = 70

def fetch_pending_signals(db_path: str, max_age_sec: int = 3600) -> List[dict]:
    """Read pending signals from IIE database.

    These signals have already been evaluated by IIE SignalEngine:
      - Score >= IIE_MIN_SCORE (70) — v7.0: raised from 60
      - Stop hunt prob < 70%
      - OI >= $1M on at least one exchange
      - Cooldown per symbol (30min)
      - ML prediction + coin profile applied

    Returns signals sorted by score DESC.
    Note: We do NOT mark signals as processed (that's for Pump Hunter).
    Soldier tracks consumed signal IDs in memory to avoid duplicates.
    """
    cutoff = time.time() - max_age_sec

    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT id, impulse_id, symbol, direction, price, score,
                      confidence, sl_pct, tp_pct, trail_pct, hold_bars,
                      size_mult, market_phase, will_continue_prob,
                      stop_hunt_prob, coin_quality, reason, created_at
               FROM pending_signals
               WHERE created_at > ? AND score >= ?
               ORDER BY score DESC
               LIMIT 20""",
            (cutoff, IIE_MIN_SCORE)
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"IIE pending_signals query failed: {e}")
        return []
    finally:
        conn.close()


# Timeframe mapping
TF_TO_INTERVAL = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1D": "D"}

def _get_needed_intervals(symbol: str, strategies: List[Dict]) -> List[str]:
    """Return list of Bybit intervals needed for this symbol."""
    intervals = {"5"}  # Always need 5m for fallback
    for strat in strategies:
        sym = strat.get("symbol", "*")
        if sym != "*" and sym != symbol:
            continue
        tf = strat.get("timeframe", "5m")
        ivl = TF_TO_INTERVAL.get(tf, "5")
        intervals.add(ivl)
    return sorted(intervals)


# ─── Data & Helpers ──────────────────────────────────────────
@dataclass
class PaperPosition:
    symbol: str
    direction: str
    entry_price: float
    entry_time: str
    stop_price: float
    tp_price: float
    stop_pct: float
    tp_pct: float
    size_usdt: float
    strategy_id: int = 0
    strategy_name: str = "default_zscore"
    config_version: str = "v1"
    breakeven_activated: bool = False
    partial_taken: bool = False
    partial_exit_price: float = 0.0
    realized_pnl_pct: float = 0.0
    exit_price: float = 0.0
    exit_time: str = ""
    exit_reason: str = ""
    bars_held: int = 0
    iie_trail_pct: float = 0.0
    peak_price: float = 0.0
    iie_signal_id: int = 0

@dataclass
class PaperTraderState:
    symbols: List[str]
    exchange_balance: float = 0.0  # live balance from exchange (no fake deposit)
    active_positions: Dict[str, PaperPosition] = field(default_factory=dict)
    completed_trades: List[Dict] = field(default_factory=list)
    total_pnl_pct: float = 0.0
    wins: int = 0
    losses: int = 0
    signals_seen: int = 0
    max_positions: int = 5

def fetch_bybit_klines(symbol: str, interval: str = "5", limit: int = 200) -> pd.DataFrame:
    url = "https://api.bybit.com/v5/market/kline"
    params = {"category": "linear", "symbol": symbol, "interval": interval, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("retCode") != 0: return pd.DataFrame()
        rows = data["result"]["list"]
        rows.reverse()
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna().reset_index(drop=True)
    except Exception: return pd.DataFrame()

def discover_hot_symbols(limit: int = 20) -> List[str]:
    url = "https://api.bybit.com/v5/market/tickers"
    params = {"category": "linear"}
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("retCode") != 0: return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
        tickers = data["result"]["list"]
        # v3.2: raised from 20M to 50M — excludes illiquid traps like WLFI
        BLACKLIST = {
            'WLFIUSDT', 'NAORISUSDT', 'MOVEUSDT',
            # Meme/high-volatility tokens excluded from automated discovery
            'FARTCOINUSDT', '1000PEPEUSDT', 'BONKUSDT', 'WIFUSDT', 'BOMEUSDT',
            'NEIROUSDT', 'MEMEUSDT', 'DOGSUSDT', 'POPCATUSDT', 'ACTUSDT',
            'SHIBUSDT', 'FLOKIUSDT', '1000SHIBUSDT', 'TURBOUSDT', 'GOATUSDT',
            # v3.5: confirmed session losers
            'PENGUUSDT', 'BSBUSDT', 'RAVEUSDT', 'ENAUSDT',
            # v4.0: session analysis — consistent losers
            'ZECUSDT', 'LTCUSDT',
        }
        candidates = [t for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in BLACKLIST and float(t.get('turnover24h', 0)) > 50_000_000]
        candidates = sorted(candidates, key=lambda x: float(x.get('turnover24h', 0)), reverse=True)[:50]
        def get_z_score(sym):
            df = fetch_bybit_klines(sym, interval="60", limit=24)
            if len(df) < 10: return None
            mean_vol = df["volume"].mean()
            std_vol = df["volume"].std()
            if std_vol == 0: return 0
            z = (df["volume"].iloc[-1] - mean_vol) / std_vol
            return (sym, z)
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(get_z_score, [c['symbol'] for c in candidates]))
            hot_scores = [r for r in results if r is not None]
        hot_scores = sorted(hot_scores, key=lambda x: x[1], reverse=True)
        return [s[0] for s in hot_scores[:limit]]
    except Exception: return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]

def check_exit(pos: PaperPosition, high: float, low: float, close: float, params: dict) -> Optional[str]:
    """Check exit conditions. Sets pos.exit_price to real fill price (not candle close).
    PnL is calculated from actual entry→exit prices.

    v8.0: EXIT OVERHAUL — only IIE trailing stop + TP + breakeven + catastrophic safety.
    DISABLED: time_exit (12% WR, -20.64%), atr_stop (0% WR, -4.59%).
    """
    pos.bars_held += 1
    if pos.direction == "long":
        favorable = (high / pos.entry_price - 1.0) * 100
        adverse = (1.0 - low / pos.entry_price) * 100
    else:
        favorable = (1.0 - low / pos.entry_price) * 100
        adverse = (high / pos.entry_price - 1.0) * 100

    # v6.0: Update peak price for trailing stop
    if pos.iie_trail_pct > 0:
        if pos.direction == "long":
            if pos.peak_price == 0:
                pos.peak_price = pos.entry_price
            pos.peak_price = max(pos.peak_price, high)
        else:
            if pos.peak_price == 0:
                pos.peak_price = pos.entry_price
            pos.peak_price = min(pos.peak_price, low)

    # Breakeven activation + partial TP
    be_rr = float(params.get("breakeven_at_rr", 0.3))
    be_pct = pos.stop_pct * be_rr
    # Guard: partial TP must cover commission, otherwise breakeven always loses
    min_profitable_be = COMMISSION_PCT * 2.5  # ~0.275%
    be_pct = max(be_pct, min_profitable_be)

    if favorable >= be_pct and not pos.breakeven_activated:
        pos.breakeven_activated = True
        if params.get("partial_tp_at_be", True) and not pos.partial_taken:
            pos.partial_taken = True
            # Record actual partial fill price
            if pos.direction == "long":
                pos.partial_exit_price = pos.entry_price * (1 + be_pct / 100)
            else:
                pos.partial_exit_price = pos.entry_price * (1 - be_pct / 100)

    # --- TP hit: fill at tp_price ---
    if favorable >= pos.tp_pct:
        if pos.partial_taken:
            pos.exit_price = round((pos.partial_exit_price + pos.tp_price) / 2, 6)
        else:
            pos.exit_price = pos.tp_price
        _calc_pnl_from_prices(pos)
        return "take_profit"

    # --- v6.0: IIE Trailing stop (PRIMARY exit method since v8.0) ---
    if pos.iie_trail_pct > 0 and favorable > 0.5:
        if pos.direction == "long":
            trail_price = pos.peak_price * (1 - pos.iie_trail_pct / 100)
            if low <= trail_price:
                pos.exit_price = round(trail_price, 6)
                _calc_pnl_from_prices(pos)
                return "iie_trailing_stop"
        else:
            trail_price = pos.peak_price * (1 + pos.iie_trail_pct / 100)
            if high >= trail_price:
                pos.exit_price = round(trail_price, 6)
                _calc_pnl_from_prices(pos)
                return "iie_trailing_stop"

    # --- v8.1: REGULAR SL for positions that NEVER went profitable ---
    # If breakeven was never activated, the trade never made it to profit.
    # In that case, respect the original SL to prevent unlimited bleeding.
    if not pos.breakeven_activated and adverse >= pos.stop_pct:
        pos.exit_price = round(pos.stop_price, 6)
        _calc_pnl_from_prices(pos)
        return "iie_stop_loss"

    # --- Breakeven exit (for positions that activated BE but are now reversing) ---
    if pos.breakeven_activated and adverse >= 0.05:
        # Position was profitable enough to activate BE, now pulling back
        if pos.direction == "long":
            be_fill = pos.entry_price * (1 + 0.05 / 100)
        else:
            be_fill = pos.entry_price * (1 - 0.05 / 100)
        if pos.partial_taken:
            pos.exit_price = round((pos.partial_exit_price + be_fill) / 2, 6)
        else:
            pos.exit_price = round(be_fill, 6)
        _calc_pnl_from_prices(pos)
        return "breakeven"

    # v8.1: time_exit DISABLED (12% WR, -20.64% on 49 trades)
    # Trades that went profitable ride with IIE trailing stop.
    # Trades that didn't → closed by iie_stop_loss above.

    return None


def _calc_pnl_from_prices(pos: PaperPosition):
    """Calculate realized PnL from actual entry→exit prices."""
    if pos.direction == "long":
        pos.realized_pnl_pct = (pos.exit_price / pos.entry_price - 1.0) * 100
    else:
        pos.realized_pnl_pct = (1.0 - pos.exit_price / pos.entry_price) * 100
    pos.realized_pnl_pct -= COMMISSION_PCT  # round-trip commission


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Compute Average True Range for ATR-based stop calculation.
    Returns ATR value in price units (not percentage)."""
    if len(df) < period + 1:
        return 0.0
    high = df['high']
    low = df['low']
    close_prev = df['close'].shift(1)
    tr = pd.concat([
        high - low,
        (high - close_prev).abs(),
        (low - close_prev).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    val = atr.iloc[-1]
    return float(val) if pd.notna(val) else 0.0


def get_btc_macro_bias() -> str:
    """Check BTC 1h EMA20 vs EMA50 to determine macro trend.
    Returns: 'long_only', 'short_only', or 'both'.
    """
    try:
        df = fetch_bybit_klines("BTCUSDT", interval="60", limit=60)
        if len(df) < 50:
            return "both"
        close = df['close']
        ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
        spread_pct = (ema20 - ema50) / ema50 * 100
        if spread_pct > 0.3:    # EMA20 clearly above EMA50 → uptrend
            return "long_only"
        elif spread_pct < -0.3:  # EMA20 clearly below EMA50 → downtrend
            return "short_only"
        else:
            return "both"  # Range / flat
    except Exception as e:
        logger.warning(f"BTC macro bias check failed: {e}")
        return "both"

def save_state(state: PaperTraderState, path: Path):
    active_pos_dict = {s: asdict(p) for s, p in state.active_positions.items()}
    data = {
        "symbols": state.symbols, "exchange_balance": state.exchange_balance,
        "total_pnl_pct": state.total_pnl_pct,
        "wins": state.wins, "losses": state.losses, "signals_seen": state.signals_seen,
        "win_rate": state.wins / max(1, state.wins + state.losses) * 100,
        "active_positions": active_pos_dict, "completed_trades": state.completed_trades[-100:],
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

def _tg_escape(s: str) -> str:
    """Escape Telegram Markdown special chars (underscores, asterisks, backticks)."""
    return str(s).replace('_', '\_').replace('*', '\\*').replace('`', '\\`')

def format_trade_message(pos: PaperPosition, state: PaperTraderState) -> str:
    dir_icon = "🟢 LONG" if pos.direction == "long" else "🔴 SHORT"
    pnl_icon = "✅" if pos.realized_pnl_pct > 0 else "❌"
    wr = state.wins / max(1, state.wins + state.losses) * 100
    strat_safe  = _tg_escape(pos.strategy_name)
    reason_safe = _tg_escape(pos.exit_reason)       # fixed_stop / time_exit / take_profit etc.
    strat_line = f"Strategy: {strat_safe} (#{pos.strategy_id})\n" if pos.strategy_id else ""
    partial_line = ""
    if pos.partial_taken and pos.partial_exit_price > 0:
        partial_line = f"Partial TP: {pos.partial_exit_price} (50%)\n"
    return (
        f"{pnl_icon} *Paper Trade #{state.wins + state.losses}*\n"
        f"{dir_icon} {pos.symbol}\n"
        f"{strat_line}"
        f"Entry: {pos.entry_price} Exit: {pos.exit_price}\n"
        f"{partial_line}"
        f"PnL: {pos.realized_pnl_pct:+.3f}% | Reason: {reason_safe}\n"
        f"Bars held: {pos.bars_held}\n"
        f"Session: W{state.wins}/L{state.losses} | WR: {wr:.0f}% | Total: {state.total_pnl_pct:+.3f}%"
    )


def format_entry_message(signal: dict, symbol: str, state: PaperTraderState) -> str:
    dir_icon = "🟢 LONG" if signal["direction"] == "long" else "🔴 SHORT"
    strat_name = _tg_escape(signal.get("strategy_name", "default"))
    strat_wr = signal.get("strategy_win_rate", 0)
    strat_line = f"Strategy: {strat_name}" + (f" (WR: {strat_wr*100:.0f}%)" if strat_wr else "") + "\n"
    return (
        f"📡 **New Signal Detected!**\n"
        f"{dir_icon} {symbol}\n"
        f"{strat_line}"
        f"Entry: {signal['entry_price']} | SL: {signal['stop_price']} | TP: {signal['tp_price']}\n"
        f"Risk: {signal['stop_pct']:.2f}% | Reward: {signal['tp_pct']:.2f}%\n"
        f"Active: {len(state.active_positions)}/{state.max_positions}"
    )

# ─── Main Loop ────────────────────────────────────────────────
DEFAULT_PARAMS = {
    "lookback_bars": 100, "min_dollar_volume_z": 3.0, "min_price_return_z": 2.5,
    "fixed_stop_loss_pct": 1.00, "max_stop_loss_pct": 6.00,  # v8.2: 4.00→6.00 — alts need wider stops to avoid noise
    "take_profit_rr": 2.5, "max_hold_bars": 50,  # v7.0: raised 30→50 to give IIE trades more room
    "entry_pullback_pct": 0.5, "trend_ema_period": 50, "breakeven_at_rr": 1.5,
    "partial_tp_at_be": True, "use_dynamic_stop": True, "account_risk_pct": 0.10,
    "min_impulse_strength": 10.0,
    # v8.2: ATR-based stop parameters — widened further (data: tight stops = noise stop-outs)
    "atr_stop_multiplier": 3.0,   # SL = ATR × 3.0 (was 2.5) — reduce false stop-outs
    "atr_period": 14,              # ATR lookback
    "min_stop_pct": 1.00,          # v8.2: Minimum stop 0.60→1.00% — alts move 1-2% on noise
}

# Bybit Linear Futures: Taker 0.055% per side → 0.11% round trip
COMMISSION_PCT = 0.11

# v4.0: Cooldown after closing a position on a symbol (seconds)
# After any SL → 30 minutes; normal exits → 10 minutes
TRADE_COOLDOWN_SEC = 600       # Normal cooldown (non-SL exits)
SL_COOLDOWN_SEC = 1800         # 30 minutes after any stop loss

def run_paper_trader(symbols: List[str], deposit: float, interval: int, max_pos: int, tg_token: str, tg_chat: str):
    params = dict(DEFAULT_PARAMS)
    # deposit arg is now only a fallback for paper mode; in demo/live, exchange balance is used
    state = PaperTraderState(symbols=symbols, exchange_balance=deposit, max_positions=max_pos)
    tg = TelegramNotifier(tg_token, tg_chat)
    state_dir = Path.cwd() / ".local_ai" / "paper_trading"
    state_path = state_dir / "paper_state_multi.json"
    opt_params_path = state_dir / "optimized_params.json"
    strategy_pack_path = state_dir / "strategy_pack.json"
    kill_switch_path = state_dir / ".kill_switch"  # HQ kill switch
    history_path = state_dir / "strategy_history.json"

    # Load current config version from strategy history
    config_version = "v1"
    try:
        if history_path.exists():
            history = json.loads(history_path.read_text(encoding="utf-8"))
            config_version = history.get("current_version", "v1")
    except Exception:
        pass
    logger.info(f"📋 Config version: {config_version}")

    # Load strategy pack
    strategy_pack: List[Dict] = load_strategy_pack(strategy_pack_path)

    # Expand symbol list with symbols from strategy pack
    pack_symbols = set()
    for strat in strategy_pack:
        sym = strat.get("symbol", "*")
        if sym != "*" and sym not in symbols:
            pack_symbols.add(sym)
    if pack_symbols:
        symbols = list(set(symbols) | pack_symbols)
        state.symbols = symbols
        logger.info(f"📋 Expanded symbol list with pack symbols: +{len(pack_symbols)} → {len(symbols)} total")

    if state_path.exists():
        try:
            prev = json.loads(state_path.read_text())
            state.wins = prev.get("wins", 0); state.losses = prev.get("losses", 0)
            state.total_pnl_pct = prev.get("total_pnl_pct", 0.0); state.signals_seen = prev.get("signals_seen", 0)
            state.completed_trades = prev.get("completed_trades", [])
            # Restore active positions from saved state
            saved_positions = prev.get("active_positions", {})
            for sym, pos_data in saved_positions.items():
                try:
                    pos = PaperPosition(
                        symbol=pos_data["symbol"],
                        direction=pos_data["direction"],
                        entry_price=float(pos_data["entry_price"]),
                        entry_time=str(pos_data.get("entry_time", "")),
                        stop_price=float(pos_data["stop_price"]),
                        tp_price=float(pos_data["tp_price"]),
                        stop_pct=float(pos_data.get("stop_pct", 0.35)),
                        tp_pct=float(pos_data.get("tp_pct", 0.875)),
                        size_usdt=float(pos_data.get("size_usdt", 0)),
                        strategy_id=int(pos_data.get("strategy_id", 0)),
                        strategy_name=str(pos_data.get("strategy_name", "default_zscore")),
                        config_version=str(pos_data.get("config_version", "v1")),
                        breakeven_activated=bool(pos_data.get("breakeven_activated", False)),
                        partial_taken=bool(pos_data.get("partial_taken", False)),
                        partial_exit_price=float(pos_data.get("partial_exit_price", 0)),
                        bars_held=int(pos_data.get("bars_held", 0)),
                        iie_trail_pct=float(pos_data.get("iie_trail_pct", 0)),
                        peak_price=float(pos_data.get("peak_price", 0)),
                        iie_signal_id=int(pos_data.get("iie_signal_id", 0)),
                    )
                    state.active_positions[sym] = pos
                    logger.info(f"📂 Restored position: {sym} {pos.direction.upper()} @ {pos.entry_price}")
                except Exception as e:
                    logger.warning(f"⚠️ Failed to restore position {sym}: {e}")
            if state.active_positions:
                logger.info(f"📂 Restored {len(state.active_positions)} active positions from state")
        except Exception as e:
            logger.warning(f"⚠️ Failed to load state: {e}")

    running = True
    kill_announced = False  # Send TG only once when kill switch activates
    def stop(s, f): nonlocal running; running = False
    signal.signal(signal.SIGINT, stop); signal.signal(signal.SIGTERM, stop)

    # ─── Auto drawdown stop threshold ───────────────────────────
    MAX_SESSION_LOSS_PCT = float(os.getenv("MAX_SESSION_LOSS_PCT", "-3.0"))  # e.g. -3.0

    # NOTE: No startup kill switch check here — PM2 would restart endlessly.
    # Kill switch is checked inside the main loop instead.

    pack_info = f" | Strategies: {len(strategy_pack)}" if strategy_pack else ""
    msg = f"🚀 *PAPER TRADER v8 STARTED*\nSymbols: {len(symbols)} | Max Pos: {max_pos} | Risk: {params['account_risk_pct']}% | Exit: IIE-trail-only{pack_info}"
    logger.info(msg); tg.send(msg)

    is_dynamic = os.getenv("DYNAMIC_SYMBOLS", "0") == "1"
    last_discovery = 0; last_params_check = 0; last_pack_check = 0
    cooldowns: Dict[str, float] = {}  # symbol → cooldown expiry timestamp
    sl_streak: Dict[str, int] = {}  # symbol → consecutive SL count
    MAX_CONSECUTIVE_SL = int(os.getenv("MAX_CONSECUTIVE_SL", "2"))  # pause after N SL in a row
    SL_STREAK_COOLDOWN_SEC = int(os.getenv("SL_STREAK_COOLDOWN_SEC", "28800"))  # v4.0: 8h (was 4h)
    start_ts = time.time(); last_heartbeat = 0  # Heartbeat immediately on first loop

     # ─── Exchange Executor ─────────────────────────────────
    executor = None
    trading_mode = os.getenv("TRADING_MODE", "paper").lower()
    if ExchangeExecutor and trading_mode != "paper":
        try:
            executor = ExchangeExecutor.from_env(bot_id="soldier")
            info = executor.test_connection()
            ex_bal = info.get('balance_usdt', 0)
            state.exchange_balance = ex_bal  # use real exchange balance
            ex_msg = f"⚡ Exchange connected: {executor} | Balance: ${ex_bal:.2f}"
            logger.info(ex_msg); tg.send(ex_msg)
        except Exception as e:
            logger.error(f"⚠️ Exchange init failed: {e} — falling back to paper")
            executor = None
    else:
        logger.info("📝 Running in PAPER mode (no exchange orders)")

    # ─── v6.0: IIE Manager Singleton ────────────────────────
    iie_manager = None
    iie_db_path = Path.cwd() / "iie" / "data" / "impulses.db"
    if iie_db_path.exists():
        try:
            from iie.adaptive_manager import AdaptivePositionManager
            iie_manager = AdaptivePositionManager()
            logger.info("🧠 IIE AdaptivePositionManager initialized")
        except Exception as e:
            logger.warning(f"⚠️ IIE init failed: {e} — signals will be consumed without ML gate")
    else:
        logger.info("📝 IIE DB not found — running without IIE signals")
    soldier_processed_ids: set = set()  # Track consumed pending_signal IDs in memory

    # ─── Exchange Position Sync ────────────────────────────
    # Reconcile local state with actual exchange positions on startup
    if executor:
        try:
            ex_positions = executor.get_positions()
            if ex_positions:
                synced = 0
                for ep in ex_positions:
                    # Convert ccxt symbol (BTC/USDT:USDT) back to exchange symbol (BTCUSDT)
                    raw_sym = ep.symbol.split('/')[0] + ep.symbol.split('/')[1].split(':')[0]
                    if raw_sym not in state.active_positions:
                        # This position exists on exchange but not in local state — create tracking entry
                        pos = PaperPosition(
                            symbol=raw_sym,
                            direction=ep.side,
                            entry_price=ep.entry_price,
                            entry_time=datetime.now(timezone.utc).isoformat(),
                            stop_price=ep.entry_price * (0.995 if ep.side == "long" else 1.005),
                            tp_price=ep.entry_price * (1.01 if ep.side == "long" else 0.99),
                            stop_pct=0.50,
                            tp_pct=1.25,
                            size_usdt=ep.notional / (ep.leverage or 1),
                            strategy_id=0,
                            strategy_name="exchange_sync",
                            config_version="sync",
                        )
                        state.active_positions[raw_sym] = pos
                        synced += 1
                        logger.info(f"🔄 Synced from exchange: {raw_sym} {ep.side.upper()} @ {ep.entry_price} (uPnL: ${ep.unrealized_pnl:+.4f})")
                if synced:
                    sync_msg = f"🔄 *EXCHANGE SYNC*: Recovered {synced} positions from exchange that were missing from local state"
                    logger.info(sync_msg); tg.send(sync_msg)
                    save_state(state, state_path)
                else:
                    logger.info(f"✅ Exchange sync OK: {len(ex_positions)} positions match local state")
        except Exception as e:
            logger.warning(f"⚠️ Exchange sync failed: {e}")

    _last_balance_refresh = 0  # Track when we last refreshed exchange balance
    _BALANCE_REFRESH_SEC = 300  # Refresh exchange balance every 5 minutes

    while running:
        loop_start = time.time()

        # ─── Periodic exchange balance refresh (every 5 min) ──
        if executor and (loop_start - _last_balance_refresh >= _BALANCE_REFRESH_SEC):
            try:
                _fresh_bal = executor.get_balance()
                if _fresh_bal > 0:
                    state.exchange_balance = _fresh_bal
            except Exception:
                pass
            _last_balance_refresh = loop_start

        # ─── AUTO DRAWDOWN STOP ───────────────────────────────
        if state.total_pnl_pct <= MAX_SESSION_LOSS_PCT and not kill_switch_path.exists():
            dd_msg = (
                f"🚨 *AUTO DRAWDOWN STOP*\n"
                f"Session PnL reached `{state.total_pnl_pct:+.3f}%` "
                f"(threshold: `{MAX_SESSION_LOSS_PCT:+.1f}%`)\n"
                f"🛑 Trading halted. Use `/resume` to restart."
            )
            logger.warning(dd_msg)
            tg.send(dd_msg)
            # Create kill switch file
            kill_switch_path.parent.mkdir(parents=True, exist_ok=True)
            kill_switch_path.write_text(json.dumps({
                "stopped_at": datetime.now(timezone.utc).isoformat(),
                "reason": f"Auto drawdown stop: PnL {state.total_pnl_pct:+.3f}% <= {MAX_SESSION_LOSS_PCT:+.1f}%",
            }), encoding="utf-8")
            save_state(state, state_path)
        # ─── END AUTO DRAWDOWN STOP ───────────────────────────

        # ─── KILL SWITCH CHECK ────────────────────────────────
        if kill_switch_path.exists():
            if not kill_announced:
                logger.warning("🛑 KILL SWITCH DETECTED — Trading halted by HQ")
                tg.send("🛑 *KILL SWITCH ACTIVE* — Soldier halted.\n_Use `/resume` to restart._")
                kill_announced = True
            # Sleep quietly — do NOT process any signals, do NOT spam TG
            time.sleep(interval)
            continue
        else:
            if kill_announced:
                logger.info("▶️ Kill switch removed — Resuming trading")
                tg.send("▶️ *RESUMED* — Kill switch removed. Soldier back on duty.")
                kill_announced = False
        # ─── END KILL SWITCH ──────────────────────────────────

        if is_dynamic and time.time() - last_discovery > 3600:
            symbols = discover_hot_symbols(len(symbols)); state.symbols = symbols; last_discovery = time.time()

        # Reload params + strategy pack every hour
        if time.time() - last_params_check > 3600:
            if opt_params_path.exists():
                try: params.update(json.loads(opt_params_path.read_text()))
                except: pass
            last_params_check = time.time()
        if time.time() - last_pack_check > 3600:
            new_pack = load_strategy_pack(strategy_pack_path)
            if new_pack:
                strategy_pack = new_pack
                # Re-expand symbols
                for strat in strategy_pack:
                    sym = strat.get("symbol", "*")
                    if sym != "*" and sym not in symbols:
                        symbols.append(sym)
                state.symbols = symbols
            last_pack_check = time.time()

        # v4.0: BTC macro-trend bias — check once per scan loop
        macro_bias = get_btc_macro_bias()
        if macro_bias != "both":
            logger.info(f"🧭 BTC macro bias: {macro_bias.upper()}")

        # ─── v6.0: IIE SIGNAL INTAKE ─────────────────────────────
        # Consume pre-evaluated signals from IIE pending_signals table
        if iie_db_path.exists() and len(state.active_positions) < state.max_positions:
            try:
                pending = fetch_pending_signals(str(iie_db_path), max_age_sec=3600)
                for sig_row in pending:
                    sig_id = sig_row["id"]
                    if sig_id in soldier_processed_ids:
                        continue
                    soldier_processed_ids.add(sig_id)

                    iie_sym = sig_row["symbol"]
                    iie_dir = sig_row["direction"]

                    if iie_sym in state.active_positions:
                        continue
                    if len(state.active_positions) >= state.max_positions:
                        break
                    if kill_switch_path.exists():
                        break
                    if iie_sym in cooldowns and time.time() < cooldowns[iie_sym]:
                        continue
                    if macro_bias == "long_only" and iie_dir == "short":
                        continue
                    if macro_bias == "short_only" and iie_dir == "long":
                        continue
                    MAX_SAME_DIR = int(os.getenv("MAX_SAME_DIRECTION_POS", "3"))
                    same_dir = sum(1 for p in state.active_positions.values() if p.direction == iie_dir)
                    if same_dir >= MAX_SAME_DIR:
                        continue

                    # Use IIE-evaluated parameters directly
                    entry_price = float(sig_row["price"])
                    if entry_price <= 0:
                        continue
                    # Get live price for better entry
                    try:
                        live_df = fetch_bybit_klines(iie_sym, interval="5", limit=5)
                        if not live_df.empty:
                            entry_price = float(live_df["close"].iloc[-1])
                    except Exception:
                        pass

                    sl_pct = float(sig_row["sl_pct"])
                    tp_pct = float(sig_row["tp_pct"])
                    trail_pct = float(sig_row["trail_pct"])
                    hold_bars = int(sig_row["hold_bars"])
                    size_mult = float(sig_row["size_mult"])
                    score = float(sig_row["score"])
                    confidence = float(sig_row["confidence"])

                    if iie_dir == "long":
                        stop_price = entry_price * (1 - sl_pct / 100)
                        tp_price = entry_price * (1 + tp_pct / 100)
                    else:
                        stop_price = entry_price * (1 + sl_pct / 100)
                        tp_price = entry_price * (1 - tp_pct / 100)

                    state.signals_seen += 1
                    # Use cached exchange balance for sizing (refreshed every 5 min)
                    _sizing_bal = state.exchange_balance or deposit
                    risk_usdt = _sizing_bal * float(params["account_risk_pct"]) / 100.0
                    size = min(risk_usdt / (sl_pct / 100.0), _sizing_bal * 0.5)
                    size = size * size_mult

                    strategy_name = f"iie_s{score:.0f}_c{confidence:.0f}"
                    pos = PaperPosition(
                        symbol=iie_sym, direction=iie_dir,
                        entry_price=round(entry_price, 6),
                        entry_time=datetime.now(timezone.utc).isoformat(),
                        stop_price=round(stop_price, 6),
                        tp_price=round(tp_price, 6),
                        stop_pct=round(sl_pct, 4), tp_pct=round(tp_pct, 4),
                        size_usdt=round(size, 2),
                        strategy_id=sig_id,
                        strategy_name=strategy_name,
                        config_version=config_version,
                        iie_trail_pct=trail_pct,
                        iie_signal_id=sig_id,
                    )

                    if executor:
                        # v8.0: Validate symbol exists on exchange before opening
                        ccxt_sym = executor._normalize_symbol(iie_sym)
                        try:
                            if ccxt_sym not in executor._exchange.markets:
                                executor._exchange.load_markets()
                            if ccxt_sym not in executor._exchange.markets:
                                logger.warning(f"⚠️ {iie_sym} NOT FOUND on exchange — SKIPPING (demo mode requires exchange listing)")
                                continue
                        except Exception as e:
                            logger.warning(f"⚠️ Symbol validation failed for {iie_sym}: {e} — SKIPPING")
                            continue

                        open_fn = executor.open_long if iie_dir == "long" else executor.open_short
                        ex_result = open_fn(
                            iie_sym, round(size, 2),
                            stop_price=round(stop_price, 6),
                            tp_price=round(tp_price, 6)
                        )
                        if ex_result.success:
                            if ex_result.fill_price > 0:
                                pos.entry_price = ex_result.fill_price
                            logger.info(f"⚡ Exchange open: {iie_dir.upper()} {iie_sym} @ {ex_result.fill_price}")
                        else:
                            logger.error(f"⚠️ Exchange open FAILED for {iie_sym}: {ex_result.error} — SKIPPING (no phantom positions)")
                            continue

                    state.active_positions[iie_sym] = pos
                    if iie_sym not in symbols:
                        symbols.append(iie_sym)
                        state.symbols = symbols
                    sig_dict = {
                        "direction": iie_dir, "entry_price": pos.entry_price,
                        "stop_price": pos.stop_price, "tp_price": pos.tp_price,
                        "stop_pct": sl_pct, "tp_pct": tp_pct,
                        "strategy_name": strategy_name, "strategy_win_rate": 0,
                    }
                    msg = format_entry_message(sig_dict, iie_sym, state)
                    logger.info(msg); tg.send(msg)
                    post_signal_to_hub(pos, score=score)  # → Signal Hub → TG group + website
                    save_state(state, state_path)
                    logger.info(
                        f"🧠 IIE v6: {iie_dir.upper()} {iie_sym} "
                        f"score={score:.0f} conf={confidence:.0f}% "
                        f"SL={sl_pct:.1f}% TP={tp_pct:.1f}% trail={trail_pct:.2f}% "
                        f"size={size_mult:.1f}x hold={hold_bars}bars"
                    )
            except Exception as e:
                logger.warning(f"⚠️ IIE signal check failed: {e}")
        # ─── END IIE SIGNAL INTAKE ────────────────────────────────

        for symbol in symbols:
            # Build klines cache for all needed intervals
            needed_intervals = _get_needed_intervals(symbol, strategy_pack)
            klines_cache: Dict[str, pd.DataFrame] = {}
            for ivl in needed_intervals:
                df = fetch_bybit_klines(symbol, interval=ivl)
                if not df.empty:
                    klines_cache[ivl] = df

            if not klines_cache:
                continue
            # Use 5m as default for price/exit checks
            primary_df = klines_cache.get("5")
            if primary_df is None or primary_df.empty:
                primary_df = next(iter(klines_cache.values()))

            # v4.0: Compute ATR for this symbol and inject into params
            atr_val = compute_atr(primary_df, int(params.get("atr_period", 14)))
            params["_atr_value"] = atr_val
            current_price = float(primary_df["close"].iloc[-1])
            current_ts = str(primary_df["timestamp"].iloc[-1])

            if symbol in state.active_positions:
                pos = state.active_positions[symbol]
                reason = check_exit(pos, float(primary_df["high"].iloc[-1]), float(primary_df["low"].iloc[-1]), current_price, params)
                if reason:
                    # exit_price is already set by check_exit to real fill price
                    pos.exit_time = current_ts; pos.exit_reason = reason
                    state.total_pnl_pct += pos.realized_pnl_pct
                    if pos.realized_pnl_pct > 0: state.wins += 1
                    else: state.losses += 1

                    # ─── v8.2: Exchange close with safety guard ───
                    # CRITICAL: If exchange close fails, do NOT remove from state.
                    # Position stays tracked and retry happens on next tick.
                    exchange_close_ok = True  # Default for paper-only mode
                    if executor:
                        ex_result = executor.close_position_verified(symbol, pos.direction)
                        if ex_result.success:
                            pos.exit_price = ex_result.fill_price or pos.exit_price
                            if ex_result.verified:
                                logger.info(f"⚡ Exchange close VERIFIED: {symbol} @ {ex_result.fill_price}")
                            else:
                                warn_msg = f"⚠️ *CLOSE NOT VERIFIED*: {symbol} — check exchange manually!"
                                logger.warning(warn_msg); tg.send(warn_msg)
                        else:
                            exchange_close_ok = False
                            err_msg = (
                                f"🚨 *EXCHANGE CLOSE FAILED*: {symbol}\n"
                                f"Error: {ex_result.error}\n"
                                f"Position kept in state — will retry next tick."
                            )
                            logger.error(err_msg); tg.send(err_msg)
                            # v8.2: Revert the PnL/stats changes since trade isn't actually closed
                            state.total_pnl_pct -= pos.realized_pnl_pct
                            if pos.realized_pnl_pct > 0: state.wins -= 1
                            else: state.losses -= 1
                            # Reset exit fields so check_exit can fire again next tick
                            pos.exit_price = 0.0
                            pos.exit_time = ""
                            pos.exit_reason = ""
                            pos.realized_pnl_pct = 0.0
                            save_state(state, state_path)
                            continue  # ← Skip trade recording, keep position alive
                    # ───────────────────────────────────────────

                    # v6.0: Record trade outcome to IIE for ML learning
                    if iie_manager:
                        try:
                            iie_manager.record_trade_outcome(
                                symbol=pos.symbol,
                                exchange="bybit",
                                direction=pos.direction,
                                entry_price=pos.entry_price,
                                exit_price=pos.exit_price,
                                pnl_pct=pos.realized_pnl_pct,
                                exit_reason=pos.exit_reason,
                                strategy_name=pos.strategy_name,
                                bot_name="soldier",
                            )
                        except Exception as e:
                            logger.warning(f"⚠️ IIE outcome recording failed: {e}")

                    msg = format_trade_message(pos, state); logger.info(msg); tg.send(msg)
                    post_close_to_hub(pos)  # → Signal Hub → TG group + website
                    # v4.0: Track consecutive SL streaks + improved cooldowns
                    # v8.0: Track catastrophic stops for streak detection
                    if reason in ("iie_stop_loss", "catastrophic_stop"):
                        sl_streak[symbol] = sl_streak.get(symbol, 0) + 1
                        if sl_streak[symbol] >= MAX_CONSECUTIVE_SL:
                            cooldowns[symbol] = time.time() + SL_STREAK_COOLDOWN_SEC
                            warn = (
                                f"⚠️ *CATASTROPHIC SL STREAK* on {symbol}: "
                                f"{sl_streak[symbol]} consecutive stops — "
                                f"pausing {SL_STREAK_COOLDOWN_SEC//3600}h"
                            )
                            logger.warning(warn); tg.send(warn)
                            sl_streak[symbol] = 0  # reset after cooldown applied
                        else:
                            cooldowns[symbol] = time.time() + SL_COOLDOWN_SEC
                    else:
                        sl_streak[symbol] = 0  # reset streak on non-SL exit
                    # v3.3: Deduplication guard — prevent duplicate completed trades
                    trade_dict = asdict(pos)
                    is_dup = any(
                        t.get("symbol") == trade_dict["symbol"]
                        and t.get("entry_time") == trade_dict["entry_time"]
                        and t.get("direction") == trade_dict["direction"]
                        for t in state.completed_trades[-20:]
                    )
                    if not is_dup:
                        state.completed_trades.append(trade_dict)
                    else:
                        logger.warning(f"⚠️ Duplicate trade blocked: {symbol} @ {trade_dict['entry_time']}")
                    del state.active_positions[symbol]
                    cooldowns[symbol] = time.time() + TRADE_COOLDOWN_SEC
                    save_state(state, state_path)
            # v7.0: Legacy strategy pack signals DISABLED
            # Data: 11 trades, 22% WR, -$5.35 net loss. All profitable entries
            # now come through IIE pipeline above.
            elif False and len(state.active_positions) < state.max_positions:
                # ─── RACE CONDITION GUARD: re-check kill switch before opening ───
                # Prevents new positions opening in the same tick that triggered kill switch
                if kill_switch_path.exists():
                    continue
                # ──────────────────────────────────────────────────────────────────
                # Cooldown check: skip if recently closed on this symbol
                if symbol in cooldowns:
                    if time.time() < cooldowns[symbol]:
                        continue
                    else:
                        del cooldowns[symbol]
                sig = find_best_signal(klines_cache, symbol, strategy_pack, params)
                if sig:
                    # v6.0: IIE quality gate for strategy pack signals
                    if iie_manager:
                        try:
                            pack_rec = iie_manager.evaluate_signal(
                                symbol=symbol,
                                direction=sig["direction"],
                                source="soldier_pack",
                            )
                            if pack_rec.score < 40:
                                logger.info(
                                    f"⏭️ {symbol} pack signal blocked by IIE: "
                                    f"score={pack_rec.score:.0f} < 40"
                                )
                                continue
                            # Apply IIE trailing if recommended
                            if pack_rec.recommended_trail_pct > 0:
                                sig["_iie_trail_pct"] = pack_rec.recommended_trail_pct
                        except Exception:
                            pass  # IIE gate failed — allow signal through

                    # ─── v4.0: BTC MACRO-TREND BIAS FILTER ────────
                    sig_dir = sig["direction"]
                    if macro_bias == "long_only" and sig_dir == "short":
                        logger.info(f"🧭 Skip {symbol} SHORT: BTC macro = LONG_ONLY")
                        continue
                    if macro_bias == "short_only" and sig_dir == "long":
                        logger.info(f"🧭 Skip {symbol} LONG: BTC macro = SHORT_ONLY")
                        continue
                    # ──────────────────────────────────────────────

                    # ─── SAME-DIRECTION LIMIT ─────────────────────
                    # Prevent correlated mass exposure (e.g. 5 LONGs at once)
                    MAX_SAME_DIR = int(os.getenv("MAX_SAME_DIRECTION_POS", "3"))
                    same_dir_count = sum(
                        1 for p in state.active_positions.values()
                        if p.direction == sig["direction"]
                    )
                    if same_dir_count >= MAX_SAME_DIR:
                        logger.info(
                            f"⏭️ Skip {symbol}: max {MAX_SAME_DIR} "
                            f"{sig['direction'].upper()} positions reached "
                            f"({same_dir_count} active)"
                        )
                        continue
                    # ──────────────────────────────────────────────

                    state.signals_seen += 1
                    # Use cached exchange balance for sizing (refreshed every 5 min)
                    _sizing_bal = state.exchange_balance or deposit
                    risk_usdt = _sizing_bal * float(params["account_risk_pct"]) / 100.0
                    size = min(risk_usdt / (sig["stop_pct"] / 100.0), _sizing_bal * 0.5)
                    pos = PaperPosition(
                        symbol=symbol, direction=sig["direction"], entry_price=sig["entry_price"],
                        entry_time=current_ts, stop_price=sig["stop_price"], tp_price=sig["tp_price"],
                        stop_pct=sig["stop_pct"], tp_pct=sig["tp_pct"], size_usdt=round(size, 2),
                        strategy_id=sig.get("strategy_id", 0),
                        strategy_name=sig.get("strategy_name", "default_zscore"),
                        config_version=config_version,
                        iie_trail_pct=float(sig.get("_iie_trail_pct", 0)),
                    )

                    # ─── Exchange: open position on exchange ───
                    if executor:
                        open_fn = executor.open_long if sig["direction"] == "long" else executor.open_short
                        ex_result = open_fn(
                            symbol, round(size, 2),
                            stop_price=sig["stop_price"],
                            tp_price=sig["tp_price"]
                        )
                        if ex_result.success:
                            if ex_result.fill_price > 0:
                                pos.entry_price = ex_result.fill_price
                            logger.info(f"⚡ Exchange open: {sig['direction'].upper()} {symbol} @ {ex_result.fill_price}")
                        else:
                            logger.error(f"⚠️ Exchange open failed: {ex_result.error} — position recorded as paper")
                    # ───────────────────────────────────────────

                    state.active_positions[symbol] = pos
                    msg = format_entry_message(sig, symbol, state); logger.info(msg); tg.send(msg)
                    save_state(state, state_path)

        wr = state.wins / max(1, state.wins + state.losses) * 100
        strat_info = f" | Pack: {len(strategy_pack)}" if strategy_pack else ""
        logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] Active: {len(state.active_positions)} | PnL: {state.total_pnl_pct:+.3f}% | WR: {wr:.0f}%{strat_info}")

        # ─── Heartbeat: soldier reports to HQ every hour ──
        if time.time() - last_heartbeat > 3600:
            uptime_sec = int(time.time() - start_ts)
            uptime_h = uptime_sec // 3600
            uptime_m = (uptime_sec % 3600) // 60
            active_list = ", ".join(state.active_positions.keys()) or "—"
            active_safe = _tg_escape(active_list)
            # Refresh exchange balance for heartbeat
            if executor:
                try:
                    state.exchange_balance = executor.get_balance()
                except Exception:
                    pass
            bal_line = f"💵 Баланс: ${state.exchange_balance:,.2f}\n" if state.exchange_balance > 0 else ""
            heartbeat_msg = (
                f"📡 *ПУЛЬС — Солдат активен*\n"
                f"⏱ Аптайм: {uptime_h}ч {uptime_m}м\n"
                f"📊 Сессия: П{state.wins}/У{state.losses} | Винрейт: {wr:.0f}%\n"
                f"💰 PnL: {state.total_pnl_pct:+.3f}%\n"
                f"{bal_line}"
                f"🎯 Активных: {len(state.active_positions)}/{state.max_positions} {active_safe}\n"
                f"📦 Стратегий: {len(strategy_pack)}\n"
                f"🔍 Сигналов: {state.signals_seen}\n"
                f"🪙 Монет: {len(symbols)}"
            )
            logger.info(heartbeat_msg)
            tg.send(heartbeat_msg)
            last_heartbeat = time.time()

        time.sleep(max(1, interval - (time.time() - loop_start)))

    save_state(state, state_path); logger.info("Stopped.")

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="XRPUSDT,SOLUSDT,BTCUSDT,ETHUSDT,DOGEUSDT")
    p.add_argument("--top", type=int, default=0)
    p.add_argument("--deposit", type=float, default=1000.0)
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--max-pos", type=int, default=5)
    p.add_argument("--tg-token", default="")
    p.add_argument("--tg-chat", default="")
    a = p.parse_args()
    syms = discover_hot_symbols(a.top) if a.top > 0 else [s.strip() for s in a.symbols.split(",")]
    run_paper_trader(syms, a.deposit, a.interval, a.max_pos, a.tg_token, a.tg_chat)
