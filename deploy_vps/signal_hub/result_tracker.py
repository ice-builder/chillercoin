"""
OneProp Signal Hub — Result Tracker (Autonomous Checkpoints)

v2: Uses editMessageText to update the original signal post
instead of creating separate checkpoint posts.

Flow:
  1. Soldier opens trade → Signal Hub records signal + posts to @onepropru
  2. Result tracker runs every 2 minutes in background
  3. For each interval (15m, 1h, 4h):
     - Find signals where created_at + interval has passed
     - Fetch current price from exchange
     - Calculate P&L vs entry price
     - Store result + EDIT the original TG post
"""
import asyncio
import logging
import requests
from datetime import datetime, timezone, timedelta

from config import TRACK_INTERVALS, TRACK_INTERVALS_SEC
from database import (
    get_pending_checks, add_result, is_signal_daily,
    get_signal_by_id, get_tg_msg_id, get_streak, get_model_rating,
)

logger = logging.getLogger("ResultTracker")

MSK = timezone(timedelta(hours=3))


def get_price(symbol: str, exchange: str = "bybit") -> float | None:
    """Fetch current price for a symbol from exchange."""
    try:
        if exchange == "mexc":
            mexc_sym = symbol.replace("USDT", "_USDT")
            resp = requests.get(
                "https://contract.mexc.com/api/v1/contract/ticker", timeout=5)
            for t in resp.json().get("data", []):
                if t.get("symbol") == mexc_sym:
                    return float(t["lastPrice"])

        elif exchange == "gateio":
            gate_sym = symbol.replace("USDT", "_USDT")
            resp = requests.get(
                "https://api.gateio.ws/api/v4/futures/usdt/tickers",
                params={"contract": gate_sym}, timeout=5)
            data = resp.json()
            if isinstance(data, list) and data:
                return float(data[0].get("last", 0))

        elif exchange == "bitget":
            resp = requests.get(
                "https://api.bitget.com/api/v2/mix/market/ticker",
                params={"productType": "USDT-FUTURES", "symbol": symbol},
                timeout=5)
            data = resp.json()
            if data.get("data"):
                return float(data["data"][0].get("lastPr", 0))

        else:  # bybit (default)
            resp = requests.get(
                "https://api.bybit.com/v5/market/tickers",
                params={"category": "linear", "symbol": symbol}, timeout=5)
            data = resp.json()
            if data.get("retCode") == 0 and data["result"]["list"]:
                return float(data["result"]["list"][0]["lastPrice"])

    except Exception as e:
        logger.debug(f"Price fetch error {symbol}@{exchange}: {e}")
    return None


async def _build_checkpoints_dict(signal_id: int) -> dict:
    """Build checkpoints dict from DB for the given signal."""
    signal = await get_signal_by_id(signal_id)
    if not signal:
        return {}
    checkpoints = {}
    if signal.get("pnl_15m") is not None:
        checkpoints["15m"] = signal["pnl_15m"]
    if signal.get("pnl_1h") is not None:
        checkpoints["1h"] = signal["pnl_1h"]
    if signal.get("pnl_4h") is not None:
        checkpoints["4h"] = signal["pnl_4h"]
    return checkpoints


async def check_interval(interval: str, poster=None):
    """Check all pending signals for one interval and update posts.
    Only processes daily signals (is_daily_signal=1)."""
    interval_sec = TRACK_INTERVALS_SEC[interval]
    pending = await get_pending_checks(interval, interval_sec, daily_only=True)

    if not pending:
        return

    logger.info(f"⏰ Checking {len(pending)} daily signals for {interval} checkpoints")

    for signal in pending:
        price = get_price(signal["symbol"], signal.get("exchange", "bybit"))
        if price is None:
            continue

        entry = signal["price_at_signal"]
        if signal["direction"] == "long":
            pnl_pct = round((price / entry - 1) * 100, 4)
        else:
            pnl_pct = round((entry / price - 1) * 100, 4)

        await add_result(signal["id"], interval, price, pnl_pct)

        emoji = "✅" if pnl_pct > 0 else "❌"
        logger.info(
            f"  {emoji} #{signal['id']} {signal['symbol']} {signal['direction']} "
            f"| entry={entry} → {price} | {pnl_pct:+.2f}% ({interval})"
        )

        # EDIT the original post instead of creating a new one
        if poster and signal.get("is_daily_signal"):
            try:
                msg_id = await get_tg_msg_id(signal["id"])
                if msg_id:
                    # Rebuild full signal data with checkpoints
                    full_signal = await get_signal_by_id(signal["id"])
                    checkpoints = await _build_checkpoints_dict(signal["id"])
                    streak = await get_streak(limit=10)
                    rating = await get_model_rating(days=30)

                    # Build close_data if signal is already closed
                    close_data = None
                    if full_signal.get("closed_at") and full_signal["closed_at"] != "":
                        close_data = {
                            "pnl_pct": full_signal.get("exit_pnl_pct", 0),
                            "exit_price": full_signal.get("exit_price", 0),
                            "exit_reason": full_signal.get("exit_reason", ""),
                        }

                    poster.update_signal_post(
                        message_id=msg_id,
                        signal=full_signal,
                        checkpoints=checkpoints,
                        close_data=close_data,
                        streak=streak,
                        rating=rating,
                    )
                else:
                    logger.warning(
                        f"  ⚠️ No msg_id for signal #{signal['id']} — cannot edit"
                    )
            except Exception as e:
                logger.warning(f"Checkpoint edit error: {e}")


async def result_tracker_loop(poster=None):
    """Main background loop — checks results every 2 minutes.

    This is the autonomous checkpoint system:
    - Runs continuously as a background asyncio task
    - Every 2 minutes, checks if any signals have reached their next checkpoint
    - 15m: first check ~15 minutes after signal created
    - 1h: second check ~1 hour after signal created
    - 4h: third check ~4 hours after signal created
    - Each checkpoint is recorded exactly once (UNIQUE constraint in DB)
    - Checkpoints UPDATE the original TG post (editMessageText)
    """
    logger.info("📊 Autonomous checkpoint tracker started (15m → 1h → 4h) — updatable posts v2")
    await asyncio.sleep(30)  # Wait for startup

    while True:
        try:
            for interval in TRACK_INTERVALS:
                await check_interval(interval, poster=poster)
        except Exception as e:
            logger.warning(f"Result tracker error: {e}")

        await asyncio.sleep(120)  # Check every 2 minutes
