"""
IIE — Impulse Collector

Background scanner that detects impulses across all exchanges/timeframes
and records them into the IIE database with full context.

Uses the same z-score engine as Soldier (paper_trader.py) and
Pump Hunter V3 (volume_impulse.py).
"""
import time
import logging
import numpy as np
import pandas as pd
import requests
from typing import Optional, List, Dict, Tuple
from concurrent.futures import ThreadPoolExecutor

from . import config
from .impulse_db import ImpulseDB, Impulse

logger = logging.getLogger("iie.collector")

# ─── Z-Score Engine (shared with Soldier/Pump Hunter) ────────

def rolling_zscore(series: pd.Series, lookback: int) -> pd.Series:
    baseline = series.shift(1)
    min_p = max(10, lookback // 4)
    mean = baseline.rolling(lookback, min_periods=min_p).mean()
    std = baseline.rolling(lookback, min_periods=min_p).std().replace(0, np.nan)
    return ((series - mean) / std).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def compute_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return 0.0
    h, l, cp = df['high'], df['low'], df['close'].shift(1)
    tr = pd.concat([h - l, (h - cp).abs(), (l - cp).abs()], axis=1).max(axis=1)
    val = tr.rolling(period).mean().iloc[-1]
    return float(val) if pd.notna(val) else 0.0


# ─── Exchange Data Fetchers ──────────────────────────────────

def fetch_top_symbols(limit: int = 200) -> List[dict]:
    """Fetch top symbols by 24h turnover from Bybit."""
    try:
        resp = requests.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear"}, timeout=10)
        data = resp.json()
        if data.get("retCode") != 0:
            return []
        tickers = [
            {"symbol": t["symbol"], "exchange": "bybit",
             "turnover24h": float(t.get("turnover24h", 0))}
            for t in data["result"]["list"]
            if t["symbol"].endswith("USDT")
            and float(t.get("turnover24h", 0)) > config.COLLECTOR_MIN_TURNOVER_24H
        ]
        tickers.sort(key=lambda x: x["turnover24h"], reverse=True)
        return tickers[:limit]
    except Exception as e:
        logger.warning(f"fetch_top_symbols error: {e}")
        return []


def fetch_klines_bybit(symbol: str, interval: str = "5",
                       limit: int = 200) -> pd.DataFrame:
    try:
        resp = requests.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "linear", "symbol": symbol,
                    "interval": interval, "limit": limit},
            timeout=10)
        data = resp.json()
        if data.get("retCode") != 0:
            return pd.DataFrame()
        rows = data["result"]["list"]
        rows.reverse()
        df = pd.DataFrame(rows, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "turnover"])
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna().reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


# ─── Impulse Detection ───────────────────────────────────────

def detect_impulses_in_df(
    df: pd.DataFrame,
    symbol: str,
    exchange: str,
    timeframe: str,
    lookback: int,
    min_vol_z: float,
    min_ret_z: float,
    scan_bars: int = 3,
) -> List[Impulse]:
    """
    Detect impulses in OHLCV dataframe.
    Returns list of Impulse objects (not yet saved to DB).
    """
    if df is None or len(df) < lookback // 2:
        return []

    frame = df.copy()
    close = frame["close"].astype(float)
    volume = frame["volume"].astype(float)

    frame["ret_pct"] = close.pct_change() * 100
    frame["abs_ret"] = frame["ret_pct"].abs()
    frame["dollar_volume"] = close * volume
    frame["dollar_volume_z"] = rolling_zscore(frame["dollar_volume"], lookback)
    frame["abs_ret_z"] = rolling_zscore(frame["abs_ret"], lookback)
    frame["rsi"] = compute_rsi(close, 14)
    frame["ema_50"] = close.ewm(span=50, adjust=False).mean()

    # ATR for context
    atr = compute_atr(frame, 14)

    # Price range for location detection
    recent_high = float(frame["high"].tail(lookback).max())
    recent_low = float(frame["low"].tail(lookback).min())
    price_range = recent_high - recent_low if recent_high > recent_low else 1e-8

    impulses = []
    n = len(frame)
    search_end = n - 1  # Last completed bar
    search_start = max(lookback, search_end - scan_bars)

    for i in range(search_start, search_end):
        row = frame.iloc[i]
        vol_z = float(row.get("dollar_volume_z", 0))
        ret_z = float(row.get("abs_ret_z", 0))

        if vol_z < min_vol_z or ret_z < min_ret_z:
            continue

        ret_pct = float(row.get("ret_pct", 0))
        if ret_pct == 0:
            continue

        direction = "long" if ret_pct > 0 else "short"
        price = float(row["close"])
        open_p = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])

        # Candle anatomy
        body = abs(price - open_p)
        total_range = high - low if high > low else 1e-8
        candle_body_pct = (body / total_range) * 100

        wick_top = (high - max(price, open_p)) / total_range * 100
        wick_bottom = (min(price, open_p) - low) / total_range * 100

        # Location: where in the range is this impulse?
        position_in_range = (price - recent_low) / price_range
        if position_in_range > 0.8:
            location = "at_high"
        elif position_in_range < 0.2:
            location = "at_low"
        else:
            location = "mid_range"

        # EMA deviation
        ema = float(row.get("ema_50", price))
        ema_dev = ((price - ema) / ema * 100) if ema > 0 else 0

        rsi = float(row.get("rsi", 50))
        ts = float(row["timestamp"].timestamp()) if hasattr(row["timestamp"], "timestamp") else time.time()

        imp = Impulse(
            symbol=symbol,
            exchange=exchange,
            timeframe=timeframe,
            timestamp=ts,
            direction=direction,
            vol_z=round(vol_z, 2),
            ret_z=round(ret_z, 2),
            combined_score=round(vol_z + ret_z, 2),
            rsi_at_impulse=round(rsi, 1),
            ema_deviation_pct=round(ema_dev, 2),
            price_at_impulse=price,
            candle_body_pct=round(candle_body_pct, 1),
            wick_ratio_top=round(wick_top, 1),
            wick_ratio_bottom=round(wick_bottom, 1),
            impulse_location=location,
            atr_at_impulse=round(atr, 8),
            source="collector",
        )
        impulses.append(imp)

    return impulses


# ─── Main Collector ──────────────────────────────────────────

class ImpulseCollector:
    """Scans top coins for impulses and records them to DB."""

    def __init__(self, db: ImpulseDB):
        self.db = db
        self.klines_cache: Dict[str, Tuple[pd.DataFrame, float]] = {}
        self.cache_ttl = 120  # 2 min cache for klines

    def run_scan(self) -> int:
        """Run one full scan. Returns number of impulses found."""
        symbols = fetch_top_symbols(config.COLLECTOR_TOP_COINS)
        if not symbols:
            logger.warning("No symbols fetched")
            return 0

        total_found = 0
        timeframes = config.IMPULSE_LOOKBACK_BARS  # {"5": 100, "15": 100, "60": 168}

        for ticker in symbols:
            sym = ticker["symbol"]
            exch = ticker["exchange"]

            for tf_interval, lookback in timeframes.items():
                # Fetch klines (with cache)
                df = self._fetch_cached(sym, tf_interval, lookback + 50)
                if df is None or df.empty:
                    continue

                # Detect impulses
                impulses = detect_impulses_in_df(
                    df, sym, exch, tf_interval, lookback,
                    min_vol_z=config.IMPULSE_MIN_VOL_Z,
                    min_ret_z=config.IMPULSE_MIN_RET_Z,
                    scan_bars=3,
                )

                for imp in impulses:
                    # Dedup: skip if already recorded recently
                    if self.db.has_recent_impulse(
                        sym, exch, tf_interval,
                        within_sec=max(300, int(tf_interval) * 60 if tf_interval.isdigit() else 3600)
                    ):
                        continue

                    imp_id = self.db.insert_impulse(imp)
                    total_found += 1
                    logger.info(
                        f"⚡ {sym} [{tf_interval}] {imp.direction.upper()} "
                        f"vol_z={imp.vol_z} ret_z={imp.ret_z} "
                        f"score={imp.combined_score} loc={imp.impulse_location}"
                    )

        return total_found

    def _fetch_cached(self, symbol: str, interval: str,
                      limit: int) -> Optional[pd.DataFrame]:
        key = f"{symbol}:{interval}"
        now = time.time()
        if key in self.klines_cache:
            df, ts = self.klines_cache[key]
            if now - ts < self.cache_ttl:
                return df

        df = fetch_klines_bybit(symbol, interval, limit)
        if df is not None and not df.empty:
            self.klines_cache[key] = (df, now)
            return df
        return None


# ─── Historical Trade Importer ───────────────────────────────

def import_completed_trades(db: ImpulseDB, state_path: str, bot_name: str):
    """
    Import existing completed_trades from Soldier/Pump Hunter state files
    into the IIE trade_outcomes table.
    """
    import json
    from pathlib import Path
    from .impulse_db import TradeOutcome

    path = Path(state_path)
    if not path.exists():
        logger.warning(f"State file not found: {path}")
        return 0

    data = json.loads(path.read_text(encoding="utf-8"))
    trades = data.get("completed_trades", [])
    imported = 0

    for t in trades:
        symbol = t.get("symbol", "")
        if not symbol:
            continue

        # Parse entry_time to timestamp
        entry_time_str = t.get("entry_time", "")
        exit_time_str = t.get("exit_time", t.get("time", ""))
        entry_ts = _parse_ts(entry_time_str)
        exit_ts = _parse_ts(exit_time_str)

        trade = TradeOutcome(
            symbol=symbol,
            exchange=t.get("exchange", "bybit"),
            direction=t.get("direction", "long"),
            entry_price=float(t.get("entry_price", t.get("entry", 0))),
            exit_price=float(t.get("exit_price", t.get("exit", 0))),
            pnl_pct=float(t.get("realized_pnl_pct", t.get("pnl_pct", 0))),
            exit_reason=t.get("exit_reason", ""),
            strategy_name=t.get("strategy_name", ""),
            bot_name=bot_name,
            entry_time=entry_ts,
            exit_time=exit_ts,
        )
        db.insert_trade(trade)
        imported += 1

    logger.info(f"📥 Imported {imported} trades from {bot_name} ({path.name})")
    return imported


def _parse_ts(s: str) -> float:
    """Parse ISO timestamp string to epoch float."""
    if not s:
        return 0.0
    try:
        from datetime import datetime, timezone
        # Handle both formats: "2026-05-07T..." and pandas Timestamp
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0
