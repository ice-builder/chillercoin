"""
OneProp Signal Hub — FastAPI Server (v3: Daily Signal System)

Signal flow:
  1. Soldier opens trade → POST /api/signals → check if daily signal needed
     - YES + 9-21 MSK → mark as daily signal, post to @onepropru
     - NO → save to DB only (private group post)
  2. Autonomous tracker checks P&L at 15m, 1h, 4h → posts only for daily signals
  3. Soldier closes trade → POST /api/signals/close
     - If daily signal → post P&L result to @onepropru
     - If closed at loss → next signal can become new daily signal
  4. 21:00 MSK → daily summary posted to TG
  5. Dashboard reads only is_daily_signal=1 signals

Endpoints:
  POST /api/signals          — record new signal (Soldier only)
  POST /api/signals/close    — record trade close (Soldier only)
  GET  /api/signals          — list signals (public: daily only)
  GET  /api/signals/{id}     — single signal with all data
  GET  /api/signals/{id}/klines — kline data for chart (proxied from Bybit)
  GET  /api/stats            — aggregated stats for dashboard
  GET  /api/health           — health check

Auth: X-API-Key header for POST endpoints (shared secret)
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

import requests
from fastapi import FastAPI, HTTPException, Header, Query, Path
from fastapi.middleware.cors import CORSMiddleware

from config import SERVER_HOST, SERVER_PORT, API_SECRET, CORS_ORIGINS
from database import (
    init_db, add_signal, close_signal, mark_posted,
    get_signals, get_signal_by_id, get_todays_signal, get_stats,
    needs_new_daily_signal, mark_daily_signal, is_signal_daily,
    save_tg_msg_id, get_tg_msg_id, get_streak, get_model_rating,
)
from models import SignalCreate, SignalClose
from telegram_poster import TelegramPoster, daily_summary_scheduler
from result_tracker import result_tracker_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("SignalHub")

poster: TelegramPoster = None

MSK = timezone(timedelta(hours=3))

# ─── Working hours: 09:00 - 21:00 MSK ─────────────────────
SIGNAL_HOUR_START = 9
SIGNAL_HOUR_END = 21


def is_within_working_hours() -> bool:
    """Check if current time is within 9:00-21:00 MSK."""
    now_msk = datetime.now(MSK)
    return SIGNAL_HOUR_START <= now_msk.hour < SIGNAL_HOUR_END


def verify_api_key(x_api_key: str = Header(default="")):
    """Simple API key auth."""
    if x_api_key != API_SECRET:
        raise HTTPException(status_code=403, detail="Invalid API key")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global poster

    await init_db()
    poster = TelegramPoster()

    # Start background tasks
    asyncio.create_task(result_tracker_loop(poster=poster))
    asyncio.create_task(daily_summary_scheduler(
        poster=poster,
        get_stats_fn=get_stats,
        get_todays_signal_fn=get_todays_signal,
        get_streak_fn=lambda: get_streak(limit=10),
        get_model_rating_fn=lambda: get_model_rating(days=30),
    ))

    logger.info("🚀 Signal Hub v4 started (Updatable Posts + Gamification)")
    if poster.enabled:
        poster._send(
            poster.private_chat_id or poster.public_channel,
            "🚀 *Signal Hub v4 STARTED*\nUpdatable posts | Score tiers | Streak | Model rating",
            poster.private_thread if poster.private_chat_id else None,
        )

    yield

    logger.info("Signal Hub stopped")


app = FastAPI(title="OneProp Signal Hub v4", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Endpoints ───────────────────────────────────────────

@app.get("/api/health")
async def health():
    """Health check."""
    return {"ok": True, "service": "signal-hub-v4", "source": "soldier-daily"}


@app.post("/api/signals")
async def create_signal(
    signal: SignalCreate,
    x_api_key: str = Header(default=""),
):
    """Record a new signal from Soldier.

    Daily Signal logic:
    1. Save ALL signals to DB (for internal tracking)
    2. Check if we need a daily signal (no open/winning daily signal today)
    3. If YES + within 9-21 MSK → mark as daily, post to @onepropru
    4. If NO → save to DB only, post to private group
    """
    verify_api_key(x_api_key)

    # v3: Only accept signals from soldier
    if signal.source != "soldier":
        raise HTTPException(
            status_code=400,
            detail=f"Only 'soldier' source accepted, got '{signal.source}'"
        )

    signal_id = await add_signal(
        source=signal.source,
        signal_type=signal.signal_type,
        symbol=signal.symbol,
        direction=signal.direction,
        price_at_signal=signal.price_at_signal,
        exchange=signal.exchange,
        strength=signal.strength,
        description=signal.description,
        metadata=signal.metadata,
        entry_target=signal.entry_target or signal.price_at_signal,
        exit_target=signal.exit_target,
    )

    logger.info(
        f"📥 Signal #{signal_id}: {signal.symbol} {signal.direction} "
        f"entry={signal.entry_target or signal.price_at_signal} "
        f"exit={signal.exit_target} ({signal.source})"
    )

    # ─── Daily Signal Decision ───────────────────────────
    should_be_daily = False
    posted_public = False

    if is_within_working_hours() and await needs_new_daily_signal():
        # This signal becomes the Daily Signal
        should_be_daily = True
        await mark_daily_signal(signal_id)
        logger.info(f"⭐ Signal #{signal_id} → DAILY SIGNAL (posting to @onepropru)")
    else:
        now_msk = datetime.now(MSK)
        if not is_within_working_hours():
            logger.info(
                f"🕐 Signal #{signal_id}: outside working hours "
                f"({now_msk.strftime('%H:%M')} MSK) — skipping public post"
            )
        else:
            logger.info(
                f"📋 Signal #{signal_id}: daily signal already exists — saving to DB only"
            )

    # Build signal dict for TG posting
    signal_dict = {
        "id": signal_id,
        "source": signal.source,
        "signal_type": signal.signal_type,
        "symbol": signal.symbol,
        "exchange": signal.exchange,
        "direction": signal.direction,
        "price_at_signal": signal.price_at_signal,
        "entry_target": signal.entry_target or signal.price_at_signal,
        "exit_target": signal.exit_target,
        "strength": signal.strength,
        "description": signal.description,
        "metadata_json": signal.metadata or {},
    }

    if poster:
        # Public channel — ONLY for daily signals
        if should_be_daily:
            # Get streak and model rating for the post
            streak = await get_streak(limit=10)
            rating = await get_model_rating(days=30)
            pub_id = poster.post_signal_opened(
                signal_dict, streak=streak, rating=rating
            )
            if pub_id:
                await mark_posted(signal_id, "public")
                await save_tg_msg_id(signal_id, pub_id)
                posted_public = True
                logger.info(f"📌 Saved msg_id={pub_id} for signal #{signal_id}")

        # Private group — always (for internal monitoring)
        priv_id = poster.post_signal_private(signal_dict)
        if priv_id:
            await mark_posted(signal_id, "private")

    return {
        "ok": True,
        "signal_id": signal_id,
        "is_daily_signal": should_be_daily,
        "posted_public": posted_public,
    }


@app.post("/api/signals/close")
async def close_signal_endpoint(
    close: SignalClose,
    x_api_key: str = Header(default=""),
):
    """Record a trade close from Soldier.

    Only posts to public channel if this is a daily signal.
    """
    verify_api_key(x_api_key)

    signal_id = await close_signal(
        symbol=close.symbol,
        direction=close.direction,
        entry_price=close.entry_price,
        exit_price=close.exit_price,
        exit_reason=close.exit_reason,
        pnl_pct=close.pnl_pct,
    )

    if not signal_id:
        logger.warning(f"Close signal: no matching open signal for {close.symbol} {close.direction}")
        signal_id = 0

    logger.info(
        f"📤 Close #{signal_id}: {close.symbol} {close.direction} "
        f"P&L={close.pnl_pct:+.2f}% ({close.exit_reason})"
    )

    close_dict = {
        "symbol": close.symbol,
        "direction": close.direction,
        "entry_price": close.entry_price,
        "exit_price": close.exit_price,
        "exit_reason": close.exit_reason,
        "pnl_pct": close.pnl_pct,
        "bars_held": close.bars_held,
    }

    if poster:
        # ─── Only update if this is a daily signal ───
        is_daily = await is_signal_daily(signal_id) if signal_id else False

        if is_daily and signal_id:
            # EDIT the original post with final result
            msg_id = await get_tg_msg_id(signal_id)
            if msg_id:
                full_signal = await get_signal_by_id(signal_id)
                # Build checkpoints from DB
                checkpoints = {}
                if full_signal.get("pnl_15m") is not None:
                    checkpoints["15m"] = full_signal["pnl_15m"]
                if full_signal.get("pnl_1h") is not None:
                    checkpoints["1h"] = full_signal["pnl_1h"]
                if full_signal.get("pnl_4h") is not None:
                    checkpoints["4h"] = full_signal["pnl_4h"]

                streak = await get_streak(limit=10)
                rating = await get_model_rating(days=30)

                poster.update_signal_post(
                    message_id=msg_id,
                    signal=full_signal,
                    checkpoints=checkpoints,
                    close_data=close_dict,
                    streak=streak,
                    rating=rating,
                )
                logger.info(f"📝 Daily signal #{signal_id} post EDITED with close result")
            else:
                logger.warning(f"⚠️ No msg_id for daily signal #{signal_id}")

            if close.pnl_pct <= 0:
                logger.info(
                    f"📉 Daily signal #{signal_id} closed at loss — "
                    f"next signal will become new daily signal"
                )
        else:
            logger.info(f"📋 Non-daily signal close (not editing): {close.symbol}")

        # Always post to private group
        poster.post_trade_closed_private(close_dict)

    return {
        "ok": True,
        "signal_id": signal_id,
        "pnl_pct": close.pnl_pct,
        "was_daily_signal": is_daily if poster else False,
    }


@app.get("/api/signals")
async def list_signals(
    source: str = Query(default=None),
    signal_type: str = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0),
):
    """List signals with results. Public endpoint for dashboard.
    Only returns daily signals (is_daily_signal=1)."""
    signals = await get_signals(
        source=source,
        signal_type=signal_type,
        limit=limit,
        offset=offset,
        daily_only=True,  # Public dashboard only sees daily signals
    )
    return {"signals": signals, "total": len(signals)}


@app.get("/api/signals/{signal_id}")
async def get_signal(signal_id: int = Path(...)):
    """Get single signal with all data. Public endpoint."""
    signal = await get_signal_by_id(signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    return signal


@app.get("/api/signals/{signal_id}/klines")
async def get_signal_klines(
    signal_id: int = Path(...),
    interval: str = Query(default="5", description="Bybit kline interval: 1, 5, 15, 60"),
    before: int = Query(default=120, description="Minutes before signal to show"),
    after: int = Query(default=300, description="Minutes after signal to show"),
):
    """Get kline data for a signal's chart. Proxied from Bybit.

    Returns candlestick data + entry/exit markers for TradingView Lightweight Charts.
    """
    signal = await get_signal_by_id(signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    symbol = signal["symbol"]
    created_at = datetime.fromisoformat(signal["created_at"])

    # Calculate time range
    start_time = created_at - timedelta(minutes=before)
    end_time = created_at + timedelta(minutes=after)

    # If trade is closed, extend chart to include close time
    if signal.get("closed_at"):
        try:
            closed_at = datetime.fromisoformat(signal["closed_at"])
            end_time = max(end_time, closed_at + timedelta(minutes=30))
        except Exception:
            pass

    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)

    # Fetch klines from Bybit
    try:
        resp = requests.get(
            "https://api.bybit.com/v5/market/kline",
            params={
                "category": "linear",
                "symbol": symbol,
                "interval": interval,
                "start": start_ms,
                "end": end_ms,
                "limit": 1000,
            },
            timeout=10,
        )
        data = resp.json()
        if data.get("retCode") != 0:
            raise HTTPException(status_code=502, detail=f"Bybit error: {data.get('retMsg')}")

        raw_klines = data["result"]["list"]
        raw_klines.reverse()  # Bybit returns newest first

        # Convert to lightweight-charts format
        klines = []
        for k in raw_klines:
            ts = int(k[0]) // 1000  # ms → seconds (UTC)
            klines.append({
                "time": ts,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })

    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Bybit fetch error: {e}")

    # Build entry/exit markers
    entry_time = int(created_at.timestamp())
    markers = {
        "entry": {
            "time": entry_time,
            "price": signal["price_at_signal"],
            "direction": signal["direction"],
        },
        "tp": signal.get("exit_target", 0),
        "sl": 0,
    }

    # Extract SL from metadata
    try:
        meta = json.loads(signal.get("metadata_json", "{}")) if isinstance(signal.get("metadata_json"), str) else signal.get("metadata_json", {})
        markers["sl"] = meta.get("stop_price", 0)
    except Exception:
        pass

    # Exit marker (if trade is closed)
    if signal.get("closed_at") and signal.get("exit_price"):
        try:
            exit_time = int(datetime.fromisoformat(signal["closed_at"]).timestamp())
            markers["exit"] = {
                "time": exit_time,
                "price": signal["exit_price"],
                "reason": signal.get("exit_reason", ""),
                "pnl_pct": signal.get("exit_pnl_pct", 0),
            }
        except Exception:
            pass

    # Checkpoint markers (15m, 1h, 4h prices)
    checkpoints = []
    for iv, seconds in [("15m", 900), ("1h", 3600), ("4h", 14400)]:
        pnl_key = f"pnl_{iv}"
        price_key = f"price_{iv}"
        if signal.get(pnl_key) is not None:
            checkpoints.append({
                "interval": iv,
                "time": entry_time + seconds,
                "pnl_pct": signal[pnl_key],
                "price": signal.get(price_key, 0),
            })
    markers["checkpoints"] = checkpoints

    return {
        "klines": klines,
        "markers": markers,
        "symbol": symbol,
        "direction": signal["direction"],
    }


@app.get("/api/stats")
async def stats(days: int = Query(default=30, le=365)):
    """Aggregated statistics for the dashboard. Public endpoint.
    Only counts daily signals."""
    data = await get_stats(days=days)
    return data


# ─── Entry Point ─────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)
