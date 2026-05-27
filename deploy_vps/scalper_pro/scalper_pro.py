"""
Scalper Pro — Adaptive Trading Bot v2.0

Paper trades on Bybit mainnet prices with multi-exchange verification.
Uses IIE v2 feedback loop for continuous learning.

Key differences from Soldier:
  - NO Binance Testnet — pure paper trading with verified prices
  - Dynamic stops (4 phases: initial→confirm→trail→protect)
  - Adaptive position sizing (scale in/out during trade)
  - Feedback loop: trade→checkpoints→hypothesis→optimization
  - Separate TG bot and HQ dashboard tab
"""
import json
import time
import logging
import signal
import sys
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Project imports ───────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    TG_BOT_TOKEN, TG_CHAT_ID, VIRTUAL_BALANCE,
    COMMISSION_TAKER_PCT, COMMISSION_ROUNDTRIP_PCT,
    MAX_POSITIONS, ACCOUNT_RISK_PCT, MAX_SESSION_LOSS_PCT,
    MAX_POSITION_PCT, MAX_PORTFOLIO_EXPOSURE_PCT, MAX_SAME_DIRECTION,
    SYMBOL_MAX_CONSECUTIVE_LOSSES, SYMBOL_LOSS_COOLDOWN_SEC,
    EMERGENCY_STOP_PCT, MAIN_LOOP_INTERVAL_SEC, SIGNAL_COOLDOWN_SEC,
    DEFAULT_TRAIL_PCT,
    STATE_FILE, DB_PATH, IIE_V1_DB, LOG_LEVEL, LOG_FORMAT,
    MSK_UTC_OFFSET, DAILY_REPORT_HOUR_MSK,
)
from price_verifier import get_verified_price, get_price_fast, _refresh_batch_prices
from iie_v2.database import ScalperProDB, ProTrade
from iie_v2.feedback_loop import FeedbackLoop
from iie_v2.dynamic_stops import DynamicStopManager, StopState
from iie_v2.adaptive_sizer import AdaptiveSizer

logging.basicConfig(level=getattr(logging, LOG_LEVEL), format=LOG_FORMAT)
logger = logging.getLogger("ScalperPro")

# NOTE: Scalper Pro uses IIE v2 (built-in hypothesis engine, feedback loop).
# Signals come from IIE v1's pending_signals table (read-only), but all
# evaluation, stops, sizing — done by IIE v2 modules.


# ── Telegram ──────────────────────────────────────────────────────────────────

class TelegramNotifier:
    def __init__(self):
        self.token = TG_BOT_TOKEN
        self.chat_id = TG_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)
        if self.enabled:
            logger.info(f"📱 TG bot connected (chat: {self.chat_id[:6]}...)")

    def send(self, text: str, parse_mode: str = "Markdown"):
        if not self.enabled:
            return
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode},
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning(f"TG error: {resp.status_code}")
        except Exception as e:
            logger.warning(f"TG send failed: {e}")


def _tg_escape(s: str) -> str:
    for ch in "_*[]()~`>#+-=|{}.!":
        s = s.replace(ch, f"\\{ch}")
    return s


# ── Position Dataclass ────────────────────────────────────────────────────────

@dataclass
class ProPosition:
    """Active position in Scalper Pro."""
    symbol: str = ""
    direction: str = "long"
    entry_price: float = 0.0
    entry_time: float = 0.0
    size_usdt: float = 0.0
    stop_price: float = 0.0
    tp_price: float = 0.0
    stop_pct: float = 1.5
    tp_pct: float = 3.0

    # IIE context
    iie_score: float = 0.0
    iie_confidence: float = 0.0
    combined_score: float = 0.0
    market_phase: str = ""
    impulse_location: str = ""
    vol_z: float = 0.0
    ret_z: float = 0.0
    rsi: float = 50.0

    # Price verification at entry
    entry_bybit: float = 0.0
    entry_binance: float = 0.0
    entry_okx: float = 0.0
    entry_divergence: float = 0.0
    entry_verified: bool = False

    # Trade tracking
    db_trade_id: int = 0     # ID in scalper_pro.db
    hypothesis_id: str = ""
    bars_held: int = 0
    peak_price: float = 0.0
    max_favorable_pct: float = 0.0
    max_adverse_pct: float = 0.0

    # Exit
    exit_price: float = 0.0
    exit_time: float = 0.0
    exit_reason: str = ""
    realized_pnl_pct: float = 0.0


@dataclass
class ProState:
    """Global state for Scalper Pro."""
    balance: float = VIRTUAL_BALANCE
    total_pnl_pct: float = 0.0
    wins: int = 0
    losses: int = 0
    signals_seen: int = 0
    active_positions: Dict[str, ProPosition] = field(default_factory=dict)
    max_positions: int = MAX_POSITIONS
    start_time: float = field(default_factory=time.time)


# ── State Persistence ─────────────────────────────────────────────────────────

def save_state(state: ProState, path: Path):
    data = {
        "balance": state.balance,
        "total_pnl_pct": state.total_pnl_pct,
        "wins": state.wins,
        "losses": state.losses,
        "signals_seen": state.signals_seen,
        "max_positions": state.max_positions,
        "start_time": state.start_time,
        "active_positions": {
            sym: asdict(pos) for sym, pos in state.active_positions.items()
        },
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(path)


def load_state(path: Path) -> ProState:
    state = ProState()
    if not path.exists():
        return state
    try:
        data = json.loads(path.read_text())
        state.balance = data.get("balance", VIRTUAL_BALANCE)
        state.total_pnl_pct = data.get("total_pnl_pct", 0)
        state.wins = data.get("wins", 0)
        state.losses = data.get("losses", 0)
        state.signals_seen = data.get("signals_seen", 0)
        state.max_positions = data.get("max_positions", MAX_POSITIONS)
        state.start_time = data.get("start_time", time.time())
        for sym, pos_data in data.get("active_positions", {}).items():
            state.active_positions[sym] = ProPosition(**{
                k: v for k, v in pos_data.items()
                if k in ProPosition.__dataclass_fields__
            })
        logger.info(
            f"📂 State loaded: balance=${state.balance:.2f} "
            f"PnL={state.total_pnl_pct:+.3f}% "
            f"W{state.wins}/L{state.losses} "
            f"active={len(state.active_positions)}"
        )
    except Exception as e:
        logger.error(f"State load failed: {e}")
    return state


# ── IIE Signal Intake ─────────────────────────────────────────────────────────

def get_iie_signals(min_score: float = 70) -> List[dict]:
    """
    Read pending signals from IIE v1 database.
    Same source as Soldier — we just read, never write to IIE v1 DB.
    """
    if not IIE_V1_DB.exists():
        return []
    try:
        conn = sqlite3.connect(str(IIE_V1_DB), timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM pending_signals
            WHERE processed = 0 AND score >= ?
            ORDER BY score DESC
            LIMIT 20
        """, (min_score,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"IIE signal read failed: {e}")
        return []


def mark_signal_consumed(signal_id: int, bot_name: str = "scalper_pro"):
    """Mark signal as consumed in IIE v1 DB (separate consumed_by field)."""
    # We don't mark consumed — we let Soldier handle its own signals.
    # Instead, we use our own tracking to avoid duplicate entries.
    pass


# ── Main Trading Logic ────────────────────────────────────────────────────────

def run_scalper_pro():
    """Main entry point for Scalper Pro bot."""
    logger.info("🚀 Scalper Pro v2.0 — Adaptive Trading Bot")
    logger.info(f"   Balance: ${VIRTUAL_BALANCE:.2f} (virtual)")
    logger.info(f"   Max positions: {MAX_POSITIONS}")
    logger.info(f"   Emergency stop: {EMERGENCY_STOP_PCT}%")
    logger.info(f"   IIE v2: ✅ (hypothesis engine + feedback loop)")
    logger.info(f"   Signal source: IIE pending_signals ({IIE_V1_DB})")

    # Initialize components
    db = ScalperProDB()
    feedback = FeedbackLoop(db)
    stops = DynamicStopManager()
    sizer = AdaptiveSizer()
    tg = TelegramNotifier()
    state = load_state(STATE_FILE)

    # Start TG command bot (background thread)
    from tg_bot import ScalperProTGBot
    tg_bot = ScalperProTGBot(db)
    tg_bot.start()

    # Tracking
    cooldowns: Dict[str, float] = {}
    seen_signals: set = set()  # Signal IDs we've already processed
    last_daily_report = 0

    # Graceful shutdown
    running = True
    def _shutdown(sig, frame):
        nonlocal running
        running = False
        logger.info("🛑 Shutdown requested...")
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Startup message
    tg.send(
        f"🚀 *Scalper Pro запущен*\n"
        f"💰 Баланс: ${state.balance:,.2f}\n"
        f"📊 PnL: {state.total_pnl_pct:+.3f}% | W{state.wins}/L{state.losses}\n"
        f"🧠 IIE v2: ✅ (гипотезы + feedback loop)\n"
        f"📋 Активных: {len(state.active_positions)}/{MAX_POSITIONS}"
    )

    # ── Pre-flight check: session loss limit ─────────────────────────────
    if state.total_pnl_pct <= MAX_SESSION_LOSS_PCT:
        msg = (
            f"⏸ *Scalper Pro — ПАУЗА (сессионный лимит)*\n\n"
            f"PnL: {state.total_pnl_pct:+.3f}% (лимит: {MAX_SESSION_LOSS_PCT}%)\n"
            f"Баланс: ${state.balance:,.2f}\n\n"
            f"Бот НЕ будет перезапускаться автоматически.\n"
            f"Для сброса: обнулите `total_pnl_pct` в стейте\n"
            f"и перезапустите: `pm2 restart scalper-pro`"
        )
        tg.send(msg)
        logger.warning(
            f"⏸ Session loss limit already reached at startup: "
            f"{state.total_pnl_pct:+.3f}% <= {MAX_SESSION_LOSS_PCT}%. "
            f"Exiting cleanly (no restart)."
        )
        save_state(state, STATE_FILE)
        sys.exit(0)

    logger.info("🔄 Entering main loop...")

    while running:
        loop_start = time.time()

        try:
            # ── 1. Refresh prices ─────────────────────────────────────────
            _refresh_batch_prices()

            # ── 1.5. Kill switch check ────────────────────────────────────
            from tg_bot import ScalperProTGBot
            is_killed = ScalperProTGBot.is_killed()
            if is_killed and state.active_positions:
                # Force close all positions at market
                logger.warning("🔴 KILL SWITCH ACTIVE — closing all positions!")
                for symbol, pos in list(state.active_positions.items()):
                    price = get_price_fast(symbol)
                    if price <= 0:
                        continue
                    pos.exit_price = price
                    pos.exit_time = time.time()
                    pos.exit_reason = "kill_switch"

                    if pos.direction == "long":
                        pos.realized_pnl_pct = (pos.exit_price / pos.entry_price - 1) * 100
                    else:
                        pos.realized_pnl_pct = (1 - pos.exit_price / pos.entry_price) * 100

                    final_mult = 1.0
                    commission = COMMISSION_ROUNDTRIP_PCT * final_mult
                    pnl_after = pos.realized_pnl_pct - commission

                    exit_snap = get_verified_price(symbol)
                    pnl_usdt = state.balance * pnl_after / 100
                    state.balance += pnl_usdt
                    state.total_pnl_pct += pnl_after
                    if pnl_after > 0:
                        state.wins += 1
                    else:
                        state.losses += 1

                    db.close_trade(
                        trade_id=pos.db_trade_id,
                        exit_price=pos.exit_price, exit_time=pos.exit_time,
                        pnl_pct=pos.realized_pnl_pct, pnl_after_comm=pnl_after,
                        exit_reason="kill_switch",
                        peak_price=pos.peak_price,
                        max_fav=pos.max_favorable_pct, max_adv=pos.max_adverse_pct,
                        stop_phase="killed",
                        scale_events="[]", final_mult=final_mult,
                        exit_bybit=exit_snap.bybit_price,
                        exit_binance=exit_snap.binance_price,
                        exit_okx=exit_snap.okx_price,
                        exit_div=exit_snap.divergence_pct,
                        exit_verified=exit_snap.is_verified,
                    )
                    feedback.on_trade_close(pos.db_trade_id, pos.exit_time)
                    stops.remove_trade(pos.db_trade_id)
                    sizer.remove_trade(pos.db_trade_id)

                    tg.send(
                        f"🔴 *KILL — ЗАКРЫТО*\n"
                        f"{symbol} {pos.direction.upper()} "
                        f"PnL: {pnl_after:+.3f}%"
                    )
                    logger.warning(
                        f"🔴 KILL CLOSED {symbol} {pos.direction} "
                        f"PnL={pnl_after:+.3f}%"
                    )

                state.active_positions.clear()
                save_state(state, STATE_FILE)
                logger.warning("🔴 All positions closed. Waiting for /resume...")

            # ── 2. Check active positions ─────────────────────────────────
            closed_symbols = []
            for symbol, pos in list(state.active_positions.items()):
                price = get_price_fast(symbol)
                if price <= 0:
                    continue

                pos.bars_held += 1

                # Update favorable/adverse
                if pos.direction == "long":
                    fav = (price / pos.entry_price - 1) * 100
                    adv = (1 - price / pos.entry_price) * 100
                else:
                    fav = (1 - price / pos.entry_price) * 100
                    adv = (price / pos.entry_price - 1) * 100
                pos.max_favorable_pct = max(pos.max_favorable_pct, fav)
                pos.max_adverse_pct = max(pos.max_adverse_pct, adv)

                # Dynamic stops check
                stop_state, exit_signal = stops.update(
                    pos.db_trade_id, price
                )

                # Adaptive sizing check
                sizer_state, scale_event = sizer.evaluate(
                    pos.db_trade_id, price
                )
                if scale_event:
                    pos.size_usdt = sizer.get_effective_size(pos.db_trade_id)

                # Exit if triggered
                if exit_signal:
                    pos.exit_price = price
                    pos.exit_time = time.time()
                    pos.exit_reason = exit_signal

                    if pos.direction == "long":
                        pos.realized_pnl_pct = (pos.exit_price / pos.entry_price - 1) * 100
                    else:
                        pos.realized_pnl_pct = (1 - pos.exit_price / pos.entry_price) * 100

                    # Account for commission and scaling
                    final_mult = sizer_state.current_mult if sizer_state else 1.0
                    commission = COMMISSION_ROUNDTRIP_PCT * final_mult
                    pnl_after = pos.realized_pnl_pct - commission

                    # Verify exit price
                    exit_snap = get_verified_price(symbol)

                    # Update balance
                    pnl_usdt = state.balance * pnl_after / 100
                    state.balance += pnl_usdt
                    state.total_pnl_pct += pnl_after
                    if pnl_after > 0:
                        state.wins += 1
                    else:
                        state.losses += 1

                    # Save to DB
                    db.close_trade(
                        trade_id=pos.db_trade_id,
                        exit_price=pos.exit_price,
                        exit_time=pos.exit_time,
                        pnl_pct=pos.realized_pnl_pct,
                        pnl_after_comm=pnl_after,
                        exit_reason=exit_signal,
                        peak_price=pos.peak_price,
                        max_fav=pos.max_favorable_pct,
                        max_adv=pos.max_adverse_pct,
                        stop_phase=stop_state.phase if stop_state else "unknown",
                        scale_events=sizer.get_events_json(pos.db_trade_id),
                        final_mult=final_mult,
                        exit_bybit=exit_snap.bybit_price,
                        exit_binance=exit_snap.binance_price,
                        exit_okx=exit_snap.okx_price,
                        exit_div=exit_snap.divergence_pct,
                        exit_verified=exit_snap.is_verified,
                    )

                    # Create after_close checkpoints
                    feedback.on_trade_close(pos.db_trade_id, pos.exit_time)

                    # Cleanup
                    stops.remove_trade(pos.db_trade_id)
                    sizer.remove_trade(pos.db_trade_id)
                    closed_symbols.append(symbol)
                    cooldowns[symbol] = time.time() + SIGNAL_COOLDOWN_SEC

                    # TG notification
                    pnl_icon = "✅" if pnl_after > 0 else "❌"
                    wr = state.wins / max(1, state.wins + state.losses) * 100
                    tg.send(
                        f"{pnl_icon} *SCALPER PRO — ЗАКРЫТИЕ*\n\n"
                        f"📊 {symbol} {pos.direction.upper()}\n"
                        f"💰 Вход: {pos.entry_price:.6g} → Выход: {pos.exit_price:.6g}\n"
                        f"📈 PnL: {pos.realized_pnl_pct:+.3f}% "
                        f"(после комиссии: {pnl_after:+.3f}%)\n"
                        f"🛑 Причина: {exit_signal}\n"
                        f"📐 Фаза стопа: {stop_state.phase if stop_state else '?'}\n"
                        f"🔄 Scaling: x{final_mult:.1f}\n"
                        f"✅ Верификация: {exit_snap.sources_count} источников "
                        f"({'✅' if exit_snap.is_verified else '⚠️'})\n\n"
                        f"💵 Баланс: ${state.balance:,.2f}\n"
                        f"📊 Сессия: W{state.wins}/L{state.losses} | "
                        f"WR: {wr:.0f}% | PnL: {state.total_pnl_pct:+.3f}%"
                    )
                    logger.info(
                        f"{'🟢' if pnl_after > 0 else '🔴'} CLOSED {symbol} "
                        f"{pos.direction} PnL={pnl_after:+.3f}% "
                        f"reason={exit_signal} phase={stop_state.phase if stop_state else '?'}"
                    )

            for sym in closed_symbols:
                del state.active_positions[sym]

            # ── 3. Look for new signals ───────────────────────────────────
            if is_killed:
                pass  # Kill switch active — skip opening new positions
            elif len(state.active_positions) < state.max_positions:
                signals = get_iie_signals(min_score=70)

                for sig in signals:
                    sig_id = sig.get("id", 0)
                    symbol = sig.get("symbol", "")

                    # Skip if already processed or in cooldown
                    if sig_id in seen_signals:
                        continue
                    if symbol in state.active_positions:
                        continue
                    if symbol in cooldowns and time.time() < cooldowns[symbol]:
                        continue

                    direction = sig.get("direction", "")
                    if not symbol or not direction:
                        seen_signals.add(sig_id)
                        continue

                    # ── v2.0 RISK GUARD: Direction balance ────────────────
                    dir_count = sum(
                        1 for p in state.active_positions.values()
                        if p.direction == direction
                    )
                    if dir_count >= MAX_SAME_DIRECTION:
                        logger.info(
                            f"⚠️ {symbol}: max {direction} positions "
                            f"({MAX_SAME_DIRECTION}) reached, skipping"
                        )
                        continue

                    # ── v2.0 RISK GUARD: Portfolio exposure ───────────────
                    total_exposure = sum(
                        p.size_usdt for p in state.active_positions.values()
                    )
                    if total_exposure >= state.balance * MAX_PORTFOLIO_EXPOSURE_PCT / 100:
                        logger.info(
                            f"⚠️ Portfolio exposure {total_exposure:.0f} >= "
                            f"{MAX_PORTFOLIO_EXPOSURE_PCT}% of balance, skipping"
                        )
                        break  # Stop looking at all signals

                    # ── v2.0 RISK GUARD: Symbol consecutive losses ────────
                    try:
                        recent_trades = db.get_symbol_recent_trades(symbol, limit=SYMBOL_MAX_CONSECUTIVE_LOSSES)
                        if len(recent_trades) >= SYMBOL_MAX_CONSECUTIVE_LOSSES:
                            all_losses = all(
                                t.get("pnl_pct_after_commission", 0) < 0
                                for t in recent_trades
                            )
                            if all_losses:
                                last_exit = max(
                                    t.get("exit_time", 0) for t in recent_trades
                                )
                                if time.time() - last_exit < SYMBOL_LOSS_COOLDOWN_SEC:
                                    logger.info(
                                        f"🚫 {symbol}: {SYMBOL_MAX_CONSECUTIVE_LOSSES} "
                                        f"consecutive losses, blacklisted for "
                                        f"{SYMBOL_LOSS_COOLDOWN_SEC}s"
                                    )
                                    continue
                    except Exception as e:
                        logger.warning(f"Symbol loss check failed: {e}")

                    seen_signals.add(sig_id)
                    score = sig.get("score", 0)

                    # Get verified price for entry
                    entry_snap = get_verified_price(symbol)
                    if entry_snap.median_price <= 0:
                        logger.warning(f"⚠️ No price for {symbol}, skipping")
                        continue
                    if not entry_snap.is_verified:
                        logger.warning(
                            f"⚠️ {symbol} price NOT verified "
                            f"(div={entry_snap.divergence_pct:.3f}%), skipping"
                        )
                        continue

                    entry_price = entry_snap.median_price

                    # Query IIE v2 hypothesis for optimized parameters
                    hyp_rec = feedback.get_signal_recommendation(
                        symbol=symbol,
                        direction=direction,
                        iie_score=score,
                        market_phase=sig.get("market_phase", "unknown"),
                        impulse_location=sig.get("impulse_location", "mid_range"),
                    )

                    # Determine SL/TP/Trail:
                    #   Priority 1: IIE v2 mature hypothesis (learned from feedback)
                    #   Priority 2: Signal's own params (from IIE v1 engine)
                    #   Priority 3: Conservative defaults
                    if hyp_rec and hyp_rec.get("is_mature"):
                        sl_pct = hyp_rec["optimal_sl_pct"]
                        tp_pct = hyp_rec["optimal_tp_pct"]
                        trail_pct = hyp_rec["optimal_trail_pct"]
                        hyp_id = hyp_rec["hypothesis_id"]
                        logger.info(
                            f"🧠 {symbol}: IIE v2 hypothesis {hyp_id} "
                            f"(N={hyp_rec['sample_count']} WR={hyp_rec['win_rate']:.0f}%)"
                        )
                    elif sig.get("sl_pct") and sig.get("tp_pct"):
                        sl_pct = sig["sl_pct"]
                        tp_pct = sig["tp_pct"]
                        trail_pct = sig.get("trail_pct", DEFAULT_TRAIL_PCT)
                        hyp_id = ""
                        logger.info(
                            f"📡 {symbol}: using signal params "
                            f"SL={sl_pct:.2f}% TP={tp_pct:.2f}%"
                        )
                    else:
                        sl_pct = 1.5
                        tp_pct = 3.0
                        trail_pct = DEFAULT_TRAIL_PCT
                        hyp_id = ""

                    # Calculate stops
                    if direction == "long":
                        stop_price = entry_price * (1 - sl_pct / 100)
                        tp_price = entry_price * (1 + tp_pct / 100)
                    else:
                        stop_price = entry_price * (1 + sl_pct / 100)
                        tp_price = entry_price * (1 - tp_pct / 100)

                    # Position sizing (v2.0: capped at MAX_POSITION_PCT)
                    risk_usdt = state.balance * ACCOUNT_RISK_PCT / 100
                    size_usdt = min(
                        risk_usdt / (sl_pct / 100),
                        state.balance * MAX_POSITION_PCT / 100,
                    )

                    # Create position
                    pos = ProPosition(
                        symbol=symbol,
                        direction=direction,
                        entry_price=entry_price,
                        entry_time=time.time(),
                        size_usdt=round(size_usdt, 2),
                        stop_price=stop_price,
                        tp_price=tp_price,
                        stop_pct=sl_pct,
                        tp_pct=tp_pct,
                        iie_score=score,
                        iie_confidence=sig.get("confidence", 0),
                        combined_score=sig.get("coin_quality", 0),
                        market_phase=sig.get("market_phase", ""),
                        impulse_location=sig.get("impulse_location", ""),
                        vol_z=sig.get("vol_z", 0),
                        ret_z=sig.get("ret_z", 0),
                        rsi=sig.get("rsi", 50),
                        entry_bybit=entry_snap.bybit_price,
                        entry_binance=entry_snap.binance_price,
                        entry_okx=entry_snap.okx_price,
                        entry_divergence=entry_snap.divergence_pct,
                        entry_verified=entry_snap.is_verified,
                        hypothesis_id=hyp_id,
                        peak_price=entry_price,
                    )

                    # Save to DB
                    trade = ProTrade(
                        symbol=symbol, direction=direction,
                        entry_price=entry_price,
                        entry_time=pos.entry_time,
                        position_size_usdt=size_usdt,
                        iie_score=score,
                        iie_confidence=pos.iie_confidence,
                        market_phase=pos.market_phase,
                        vol_z=pos.vol_z, ret_z=pos.ret_z,
                        rsi=pos.rsi,
                        impulse_location=pos.impulse_location,
                        combined_score=pos.combined_score,
                        entry_bybit=entry_snap.bybit_price,
                        entry_binance=entry_snap.binance_price,
                        entry_okx=entry_snap.okx_price,
                        entry_divergence=entry_snap.divergence_pct,
                        entry_verified=entry_snap.is_verified,
                        hypothesis_id=hyp_id,
                    )
                    pos.db_trade_id = db.insert_trade(trade)

                    # Initialize dynamic stops
                    stops.init_trade(
                        trade_id=pos.db_trade_id,
                        entry_price=entry_price,
                        direction=direction,
                        sl_pct=sl_pct,
                        tp_pct=tp_pct,
                        trail_pct=trail_pct,
                    )

                    # Initialize adaptive sizer
                    sizer.init_trade(
                        trade_id=pos.db_trade_id,
                        direction=direction,
                        entry_price=entry_price,
                        initial_size_usdt=size_usdt,
                        hypothesis_hints=hyp_rec,
                    )

                    # Create after_open checkpoints
                    feedback.on_trade_open(pos.db_trade_id, pos.entry_time)

                    state.active_positions[symbol] = pos
                    state.signals_seen += 1

                    # TG notification
                    hyp_line = ""
                    if hyp_rec and hyp_rec.get("is_mature"):
                        hyp_line = (
                            f"\n🧠 Гипотеза: {hyp_id}\n"
                            f"   N={hyp_rec['sample_count']} "
                            f"WR={hyp_rec['win_rate']:.0f}% "
                            f"avg\\_pnl={hyp_rec['avg_pnl']:+.2f}%"
                        )
                    tg.send(
                        f"🧪 *SCALPER PRO — ОТКРЫТИЕ*\n\n"
                        f"📊 {symbol} {direction.upper()}\n"
                        f"💰 Вход: {entry_price:.6g}\n"
                        f"🛑 SL: {stop_price:.6g} ({sl_pct:.2f}%)\n"
                        f"🎯 TP: {tp_price:.6g} ({tp_pct:.2f}%)\n"
                        f"📐 Размер: ${size_usdt:,.2f}\n"
                        f"🧠 IIE Score: {score:.0f}\n"
                        f"✅ Верификация: {entry_snap.sources_count} источников "
                        f"({'✅' if entry_snap.is_verified else '⚠️'})"
                        f"{hyp_line}"
                    )
                    logger.info(
                        f"🟢 OPEN {symbol} {direction} @ {entry_price:.6g} "
                        f"SL={sl_pct:.2f}% TP={tp_pct:.2f}% "
                        f"score={score:.0f} verified={entry_snap.is_verified}"
                    )

                    if len(state.active_positions) >= state.max_positions:
                        break

            # ── 4. Feedback loop tick ─────────────────────────────────────
            feedback.tick()

            # ── 5. Session drawdown check ─────────────────────────────────
            if state.total_pnl_pct <= MAX_SESSION_LOSS_PCT:
                tg.send(
                    f"🚨 *SCALPER PRO — АВАРИЙНАЯ ОСТАНОВКА*\n"
                    f"PnL достиг {state.total_pnl_pct:+.3f}% "
                    f"(лимит {MAX_SESSION_LOSS_PCT}%)"
                )
                logger.error(
                    f"🚨 Session loss limit reached: {state.total_pnl_pct:+.3f}%"
                )
                break

            # ── 6. Daily report ───────────────────────────────────────────
            now_msk = datetime.now(timezone.utc) + timedelta(hours=MSK_UTC_OFFSET)
            if (now_msk.hour == DAILY_REPORT_HOUR_MSK
                    and now_msk.minute < 2
                    and time.time() - last_daily_report > 3600):
                _send_daily_report(state, db, feedback, tg)
                last_daily_report = time.time()

            # ── 7. Save state ─────────────────────────────────────────────
            save_state(state, STATE_FILE)

            # ── 8. Log status ─────────────────────────────────────────────
            wr = state.wins / max(1, state.wins + state.losses) * 100
            logger.info(
                f"[{datetime.now().strftime('%H:%M:%S')}] "
                f"Active: {len(state.active_positions)} | "
                f"PnL: {state.total_pnl_pct:+.3f}% | WR: {wr:.0f}% | "
                f"Bal: ${state.balance:,.2f}"
            )

        except Exception as e:
            logger.error(f"Main loop error: {e}", exc_info=True)

        # Sleep remainder of interval
        elapsed = time.time() - loop_start
        sleep_time = max(1, MAIN_LOOP_INTERVAL_SEC - elapsed)
        time.sleep(sleep_time)

    # Shutdown
    save_state(state, STATE_FILE)
    logger.info("🛑 Scalper Pro stopped.")


# ── Daily Report ──────────────────────────────────────────────────────────────

def _send_daily_report(state: ProState, db: ScalperProDB,
                       feedback: FeedbackLoop, tg: TelegramNotifier):
    """Send daily digest + comparison with Soldier at 10:00 MSK."""
    wr = state.wins / max(1, state.wins + state.losses) * 100
    progress = feedback.get_learning_progress()

    # ── Part 1: Scalper Pro report ────────────────────────────────────────
    report = (
        f"📊 *SCALPER PRO — ЕЖЕДНЕВНЫЙ ОТЧЁТ*\n\n"
        f"💰 Баланс: ${state.balance:,.2f}\n"
        f"📈 PnL: {state.total_pnl_pct:+.3f}%\n"
        f"📊 W{state.wins}/L{state.losses} | WR: {wr:.0f}%\n"
        f"🎯 Активных: {len(state.active_positions)}/{state.max_positions}\n\n"
        f"🧠 *IIE v2 Прогресс:*\n"
        f"   Сделок: {progress['trades_total']} "
        f"(закрытых: {progress['trades_closed']})\n"
        f"   Гипотез: {progress['hypotheses_total']} "
        f"(зрелых: {progress['hypotheses_mature']})\n"
        f"   Ожидают чекпоинтов: {progress['pending_checkpoints']}\n"
    )

    if progress.get("hypotheses_mature", 0) > 0:
        report += (
            f"   Avg WR гипотез: {progress['avg_win_rate']:.1f}%\n"
            f"   Avg PnL гипотез: {progress['avg_pnl']:+.3f}%\n"
        )
        if progress.get("best_hypothesis"):
            best = progress["best_hypothesis"]
            report += (
                f"\n🏆 Лучшая: {best['id']}\n"
                f"   WR={best['win_rate']:.0f}% PnL={best['avg_pnl']:+.2f}% "
                f"N={best['sample_count']}\n"
            )

    tg.send(report)

    # ── Part 2: Soldier vs Scalper Pro comparison ─────────────────────────
    _send_comparison_report(state, db, tg)

    # ── Save daily metrics to DB ──────────────────────────────────────────
    now_msk = datetime.now(timezone.utc) + timedelta(hours=MSK_UTC_OFFSET)
    db.save_daily_metrics(now_msk.strftime("%Y-%m-%d"), {
        "trades_count": state.wins + state.losses,
        "wins": state.wins,
        "losses": state.losses,
        "win_rate": wr,
        "total_pnl": state.total_pnl_pct,
        "avg_pnl": state.total_pnl_pct / max(1, state.wins + state.losses),
        "hypotheses_total": progress["hypotheses_total"],
        "hypotheses_mature": progress["hypotheses_mature"],
        "balance": state.balance,
    })
    logger.info("📊 Daily report + comparison sent")


def _send_comparison_report(state: ProState, db: ScalperProDB,
                            tg: TelegramNotifier):
    """
    Side-by-side comparison: Soldier vs Scalper Pro.
    Reads Soldier's paper_state_multi.json for its stats.
    """
    # ── Read Soldier state ────────────────────────────────────────────────
    soldier_state_path = Path("/home/trader/soldier/paper_state_multi.json")
    try:
        sol = json.loads(soldier_state_path.read_text())
    except Exception as e:
        logger.warning(f"Cannot read Soldier state: {e}")
        sol = {}

    s_wins = sol.get("wins", 0)
    s_losses = sol.get("losses", 0)
    s_pnl = sol.get("total_pnl_pct", 0)
    s_total = s_wins + s_losses
    s_wr = s_wins / max(1, s_total) * 100
    s_active = len(sol.get("active_positions", {}))
    s_balance = sol.get("exchange_balance", 0) or sol.get("deposit", 1000)

    # ── Scalper Pro stats ─────────────────────────────────────────────────
    p_wins = state.wins
    p_losses = state.losses
    p_pnl = state.total_pnl_pct
    p_total = p_wins + p_losses
    p_wr = p_wins / max(1, p_total) * 100
    p_active = len(state.active_positions)
    p_balance = state.balance

    # ── Today's trades (Scalper Pro from DB) ──────────────────────────────
    now_msk = datetime.now(timezone.utc) + timedelta(hours=MSK_UTC_OFFSET)
    yesterday_ts = (now_msk - timedelta(hours=24)).timestamp()
    today_sp = db.get_closed_trades(limit=100)
    today_sp_trades = [t for t in today_sp if t.get("exit_time", 0) >= yesterday_ts]
    sp_today_count = len(today_sp_trades)
    sp_today_pnl = sum(t.get("pnl_pct_after_commission", 0) for t in today_sp_trades)
    sp_today_wins = sum(1 for t in today_sp_trades if t.get("pnl_pct_after_commission", 0) > 0)
    sp_today_wr = sp_today_wins / max(1, sp_today_count) * 100

    # ── Verdicts ──────────────────────────────────────────────────────────
    pnl_winner = "🧪 Scalper Pro" if p_pnl > s_pnl else "⚔️ Soldier"
    wr_winner = "🧪 Scalper Pro" if p_wr > s_wr else "⚔️ Soldier"
    pnl_diff = abs(p_pnl - s_pnl)
    wr_diff = abs(p_wr - s_wr)

    # Overall winner
    sp_score = 0
    if p_pnl > s_pnl:
        sp_score += 1
    if p_wr > s_wr:
        sp_score += 1
    if p_total > 0 and (p_pnl / max(1, p_total)) > (s_pnl / max(1, s_total)):
        sp_score += 1
    overall = "🧪 Scalper Pro" if sp_score >= 2 else "⚔️ Soldier"

    comparison = (
        f"⚔️ *СРАВНЕНИЕ: SOLDIER vs SCALPER PRO*\n"
        f"{'━' * 36}\n\n"
        f"*⚔️ Soldier:*\n"
        f"   💰 Баланс: ${s_balance:,.2f}\n"
        f"   📈 PnL: {s_pnl:+.3f}%\n"
        f"   📊 WR: {s_wr:.0f}% (W{s_wins}/L{s_losses})\n"
        f"   🎯 Активных: {s_active}\n"
        f"   📋 Всего сделок: {s_total}\n\n"
        f"*🧪 Scalper Pro:*\n"
        f"   💰 Баланс: ${p_balance:,.2f}\n"
        f"   📈 PnL: {p_pnl:+.3f}%\n"
        f"   📊 WR: {p_wr:.0f}% (W{p_wins}/L{p_losses})\n"
        f"   🎯 Активных: {p_active}\n"
        f"   📋 Всего сделок: {p_total}\n\n"
    )

    # Today section (Scalper Pro only — we have DB access)
    if sp_today_count > 0:
        comparison += (
            f"*📅 Scalper Pro за 24ч:*\n"
            f"   Сделок: {sp_today_count} | WR: {sp_today_wr:.0f}%\n"
            f"   PnL: {sp_today_pnl:+.3f}%\n\n"
        )

    comparison += (
        f"{'━' * 36}\n"
        f"*🏆 ВЕРДИКТ:*\n"
        f"   По PnL: {pnl_winner} (разница: {pnl_diff:.3f}%)\n"
        f"   По WR: {wr_winner} (разница: {wr_diff:.1f}%)\n"
        f"   Общий: {overall}\n"
    )

    # Footer with advice
    if p_total < 20:
        comparison += (
            f"\n💡 _Scalper Pro набрал {p_total} сделок. "
            f"Статистически значимое сравнение — от 50+ сделок._"
        )
    elif sp_score >= 2:
        comparison += "\n🚀 _Scalper Pro показывает лучшие результаты. Продолжаем сбор данных._"
    else:
        comparison += "\n⚔️ _Soldier пока впереди. IIE v2 продолжает обучение._"

    tg.send(comparison)
    logger.info("⚔️ Comparison report sent")


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run_scalper_pro()
