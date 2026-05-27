"""
IIE — Market Phase Detector

Determines macro market regime using BTC/ETH price dynamics.
Runs every 4 hours. Used by all bots for directional bias.

Phases:
  trending_up   — BTC monthly >+10%, EMA20 > EMA50
  trending_down — BTC monthly <-10%, EMA20 < EMA50
  sideways      — |BTC monthly| < 10%
  volatile      — ATR(14) > 2× median ATR
"""
import time
import logging
import numpy as np
import pandas as pd
import requests
from typing import Optional

from . import config
from .impulse_db import ImpulseDB, MarketPhase

logger = logging.getLogger("iie.market_phase")


def _fetch_daily_klines(symbol: str, limit: int = 35) -> pd.DataFrame:
    """Fetch daily klines from Bybit."""
    try:
        resp = requests.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "linear", "symbol": symbol,
                    "interval": "D", "limit": limit},
            timeout=10)
        data = resp.json()
        if data.get("retCode") != 0:
            return pd.DataFrame()
        rows = data["result"]["list"]
        rows.reverse()
        df = pd.DataFrame(rows, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "turnover"])
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna().reset_index(drop=True)
    except Exception as e:
        logger.warning(f"Daily klines fetch error ({symbol}): {e}")
        return pd.DataFrame()


def _fetch_4h_klines(symbol: str, limit: int = 60) -> pd.DataFrame:
    """Fetch 4h klines from Bybit for EMA calculation."""
    try:
        resp = requests.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "linear", "symbol": symbol,
                    "interval": "240", "limit": limit},
            timeout=10)
        data = resp.json()
        if data.get("retCode") != 0:
            return pd.DataFrame()
        rows = data["result"]["list"]
        rows.reverse()
        df = pd.DataFrame(rows, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "turnover"])
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna().reset_index(drop=True)
    except Exception as e:
        logger.warning(f"4h klines fetch error ({symbol}): {e}")
        return pd.DataFrame()


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return 0.0
    h, l, cp = df['high'], df['low'], df['close'].shift(1)
    tr = pd.concat([h - l, (h - cp).abs(), (l - cp).abs()], axis=1).max(axis=1)
    val = tr.rolling(period).mean().iloc[-1]
    return float(val) if pd.notna(val) else 0.0


def detect_market_phase(db: ImpulseDB) -> Optional[MarketPhase]:
    """
    Compute current market phase and store in DB.
    Returns the MarketPhase object.
    """
    # 1. Fetch BTC daily data
    btc_daily = _fetch_daily_klines("BTCUSDT", 35)
    if btc_daily.empty or len(btc_daily) < 30:
        logger.warning("Not enough BTC daily data for phase detection")
        return None

    # 2. Fetch ETH daily data
    eth_daily = _fetch_daily_klines("ETHUSDT", 35)

    # 3. Fetch BTC 4h for EMA
    btc_4h = _fetch_4h_klines("BTCUSDT", 60)

    # ─── BTC Metrics ────────────────────────────
    btc_price = float(btc_daily["close"].iloc[-1])
    btc_30d_ago = float(btc_daily["close"].iloc[0]) if len(btc_daily) >= 30 else btc_price
    btc_7d_ago = float(btc_daily["close"].iloc[-8]) if len(btc_daily) >= 8 else btc_price

    btc_monthly = (btc_price / btc_30d_ago - 1.0) * 100 if btc_30d_ago > 0 else 0
    btc_weekly = (btc_price / btc_7d_ago - 1.0) * 100 if btc_7d_ago > 0 else 0

    # ATR
    btc_atr = compute_atr(btc_daily, 14)
    median_atr = float(btc_daily["high"].sub(btc_daily["low"]).rolling(14).mean().median())

    # ─── ETH Metrics ────────────────────────────
    eth_price = 0.0
    eth_monthly = 0.0
    if not eth_daily.empty and len(eth_daily) >= 30:
        eth_price = float(eth_daily["close"].iloc[-1])
        eth_30d_ago = float(eth_daily["close"].iloc[0])
        eth_monthly = (eth_price / eth_30d_ago - 1.0) * 100 if eth_30d_ago > 0 else 0

    # ─── EMA on 4h ──────────────────────────────
    btc_ema_fast = 0.0
    btc_ema_slow = 0.0
    if not btc_4h.empty and len(btc_4h) >= 50:
        close_4h = btc_4h["close"]
        btc_ema_fast = float(close_4h.ewm(
            span=config.MARKET_PHASE_EMA_FAST, adjust=False).mean().iloc[-1])
        btc_ema_slow = float(close_4h.ewm(
            span=config.MARKET_PHASE_EMA_SLOW, adjust=False).mean().iloc[-1])

    # ─── Alt correlation ────────────────────────
    alt_corr = _compute_alt_correlation(btc_daily)

    # ─── Phase determination ────────────────────
    threshold = config.MARKET_PHASE_TRENDING_THRESHOLD  # 10%

    if median_atr > 0 and btc_atr > median_atr * 2:
        phase = "volatile"
    elif btc_monthly > threshold and btc_ema_fast > btc_ema_slow:
        phase = "trending_up"
    elif btc_monthly < -threshold and btc_ema_fast < btc_ema_slow:
        phase = "trending_down"
    else:
        phase = "sideways"

    mp = MarketPhase(
        timestamp=time.time(),
        btc_price=btc_price,
        eth_price=eth_price,
        btc_monthly_change_pct=round(btc_monthly, 2),
        eth_monthly_change_pct=round(eth_monthly, 2),
        btc_weekly_change_pct=round(btc_weekly, 2),
        btc_ema_fast=round(btc_ema_fast, 2),
        btc_ema_slow=round(btc_ema_slow, 2),
        btc_atr_daily=round(btc_atr, 2),
        phase=phase,
        alt_correlation=round(alt_corr, 3),
    )

    db.insert_market_phase(mp)
    logger.info(
        f"🧭 Market phase: {phase.upper()} | "
        f"BTC: ${btc_price:,.0f} ({btc_monthly:+.1f}% mo / {btc_weekly:+.1f}% wk) | "
        f"ETH: ${eth_price:,.0f} ({eth_monthly:+.1f}% mo) | "
        f"EMA20/50: {btc_ema_fast:,.0f}/{btc_ema_slow:,.0f} | "
        f"Alt corr: {alt_corr:.2f}"
    )
    return mp


def _compute_alt_correlation(btc_daily: pd.DataFrame, top_alts: int = 5) -> float:
    """
    Compute average Pearson correlation between BTC 7d returns
    and top alt returns. High correlation means alts follow BTC.
    """
    if len(btc_daily) < 8:
        return 0.5

    btc_returns = btc_daily["close"].pct_change().dropna().tail(7)
    if len(btc_returns) < 5:
        return 0.5

    alt_symbols = ["ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT"]
    correlations = []

    for sym in alt_symbols[:top_alts]:
        try:
            alt_daily = _fetch_daily_klines(sym, 10)
            if alt_daily.empty or len(alt_daily) < 8:
                continue
            alt_returns = alt_daily["close"].pct_change().dropna().tail(7)
            if len(alt_returns) < 5:
                continue
            # Align lengths
            min_len = min(len(btc_returns), len(alt_returns))
            corr = float(np.corrcoef(
                btc_returns.values[-min_len:],
                alt_returns.values[-min_len:]
            )[0, 1])
            if not np.isnan(corr):
                correlations.append(corr)
        except Exception:
            continue

    return float(np.mean(correlations)) if correlations else 0.5
