"""Pump Hunter — Triple Strategy (v1 + v2 + v3 parallel)

Runs all detection algorithms simultaneously:
  v1: Daily consolidation range + adaptive trailing stop
  v2: 6-Phase (hourly SMA/RSI/MACD + reversal scoring + SHORT)
  v3: Volume-Impulse z-score detection (15m/30m/1h, LONG+SHORT)

All can open positions on the same symbol independently.
Trades are tagged with strategy_version for comparison.
"""
import json, time, logging, signal, os
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Optional
import requests
from dotenv import load_dotenv
load_dotenv()

from config import (
    TG_TOKEN, TG_CHAT_ID, TG_THREAD_ID,
    SCAN_INTERVAL_SEC, KLINES_CACHE_TTL_SEC, HOURLY_KLINES_CACHE_TTL,
    MIN_TURNOVER_24H, PREFILTER_24H_CHANGE_PCT,
    ALERT_COOLDOWN_SEC, FALSE_BREAKOUT_COOLDOWN_SEC,
    ENABLE_BYBIT, ENABLE_MEXC, ENABLE_GATEIO, ENABLE_BITGET,
    AUTO_ENTER, DEMO_BALANCE, DEMO_STATE_FILE,
    MAX_CONCURRENT_POSITIONS, LEVERAGE,
    LONG_SIZE_PCT, SHORT_SIZE_PCT,
    V3_MIN_VOLUME_Z, V3_MIN_RETURN_Z, V3_MIN_COMBINED_SCORE,
    V3_TRAIL_PCT, V3_TRAIL_TIGHT_PCT, V3_BREAKEVEN_AT_PCT,
    V3_PARTIAL_EXIT_PCT, V3_PARTIAL_EXIT_SIZE,
    V3_COOLDOWN_SEC, V3_CACHE_TTL_SEC, V3_SIZE_PCT, V3_LEVERAGE,
    MAX_LOSS_PER_TRADE_PCT, DAILY_LOSS_LIMIT_PCT,
)
from phases import (
    Position, ConsolidationZone,
    detect_pump, setup_long_entry, check_addon_buy, execute_addon,
    compute_reversal_score, check_profit_taking,
    setup_short_entry, check_short_exit, check_short_addon,
    update_long_trailing,
)
from volume_impulse import detect_multi_tf, manage_v3_position

try:
    from exchange_executor import ExchangeExecutor
except ImportError:
    ExchangeExecutor = None

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("PumpHunterV2")

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from pump_scanner import (
    TelegramBot,
    fetch_bybit_tickers, fetch_mexc_tickers,
    fetch_gateio_tickers, fetch_bitget_tickers,
    fetch_klines, _get_current_price,
    analyze_for_pump, get_trail_pct,
    TRAIL_PHASES,
)


# ─── State persistence ───────────────────────────────────────

def _load_state() -> dict:
    p = Path(__file__).parent / DEMO_STATE_FILE
    try:
        if p.exists():
            return json.loads(p.read_text())
    except Exception as e:
        log.warning(f"Load state failed: {e}")
    return {}


def _sync_external_closes(positions: Dict[str, Position], completed: list) -> float:
    """Sync positions with state file to detect HQ manual closes.
    Returns updated balance (including PnL from externally closed positions)."""
    saved = _load_state()
    saved_active = saved.get("active_positions", {})
    saved_completed = saved.get("completed_trades", [])
    saved_balance = saved.get("demo_balance", DEMO_BALANCE)
    pnl_delta = 0.0
    to_remove = []
    for key, pos in positions.items():
        if pos.exited or not pos.confirmed:
            continue
        state_key = f"{pos.symbol}:{pos.strategy_version}"
        if state_key not in saved_active:
            # Position was closed externally (HQ dashboard)
            # Check if it appeared in completed trades
            ext_trade = None
            for t in saved_completed:
                if (t.get("symbol") == pos.symbol and
                    t.get("exit_reason") == "manual_close_hq"):
                    ext_trade = t
            pos.exited = True
            pos.exit_reason = "manual_close_hq"
            if ext_trade:
                pos.pnl_pct = ext_trade.get("pnl_pct", 0)
            # v5.0 fix: Apply PnL to balance so profit isn't lost
            pnl_delta += _calc_balance_pnl(pos, saved_balance)
            to_remove.append(key)
            log.info(f"🔄 Synced external close: {pos.symbol} [{pos.strategy_version}] PnL: {pos.pnl_pct:+.1f}%")
    for k in to_remove:
        del positions[k]
    # Sync completed trades from state file (may have HQ-added entries)
    if len(saved_completed) > len(completed):
        completed.clear()
        completed.extend(saved_completed)
    return saved_balance + pnl_delta

def _save_state(positions: Dict[str, Position], completed: list,
                scan_count: int, start_ts: float, balance: float,
                executor=None, max_balance: float = 0.0):
    p = Path(__file__).parent / DEMO_STATE_FILE
    active = {}
    for k, pos in positions.items():
        if pos.confirmed and not pos.exited:
            # Use versioned key so v1 and v2 positions on same symbol don't collide
            state_key = f"{pos.symbol}:{pos.strategy_version}"
            active[state_key] = pos.to_dict()
            active[state_key]["strategy_name"] = f"pump_hunter_{pos.strategy_version}"
    wins = sum(1 for t in completed if t.get("pnl_pct", 0) > 0)
    state = {
        "scanner": "pump_hunter_v2",
        "scan_count": scan_count,
        "uptime_sec": int(time.time() - start_ts),
        "start_ts": start_ts,
        "demo_balance": balance,
        "wins": wins,
        "losses": len(completed) - wins,
        "total_pnl_pct": round(sum(t.get("pnl_pct", 0) for t in completed), 4),
        "trading_mode": os.getenv("TRADING_MODE", "paper"),
        "active_positions": active,
        "completed_trades": completed,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "max_balance": max_balance if max_balance > 0 else balance,
    }
    try:
        p.write_text(json.dumps(state, indent=2, default=str))
    except Exception as e:
        log.warning(f"Save state failed: {e}")


# ─── Telegram messages ───────────────────────────────────────

def tg_entry(tg: TelegramBot, pos: Position):
    ver = pos.strategy_version.upper()
    if pos.strategy_version == "v1":
        tg.send(
            f"🚀 *AUTO-ENTRY LONG* [{ver}]\n"
            f"🪙 *{pos.symbol}* ({pos.exchange.upper()})\n"
            f"💰 Entry: `{pos.entry_price}`\n"
            f"🛑 SL: `{pos.stop_loss:.8g}` (top of zone)\n"
            f"📐 Trail: {TRAIL_PHASES[0][1]}%→{TRAIL_PHASES[-1][1]}%\n"
            f"📈 Pump: +{pos.pump_pct:.0f}% | Zone: {pos.p_consolidation:.8g}"
        )
    else:
        tg.send(
            f"🚀 *AUTO-ENTRY LONG* [{ver}]\n"
            f"🪙 *{pos.symbol}* ({pos.exchange.upper()}) Phase 2\n"
            f"💰 Entry: `{pos.entry_price}` | Size: {LONG_SIZE_PCT}% × {LEVERAGE}x\n"
            f"🛑 SL: `{pos.stop_loss:.8g}` (P_consolidation × 0.98)\n"
            f"📈 Pump: +{pos.pump_pct:.0f}% | Zone: {pos.p_consolidation:.8g}"
        )

def tg_addon(tg: TelegramBot, pos: Position, price: float):
    tg.send(
        f"➕ *ADD-ON BUY* Phase 3\n"
        f"🪙 *{pos.symbol}* @ `{price}`\n"
        f"📈 Profit so far: +{pos.pnl_pct:.1f}% | Pullback confirmed\n"
        f"💰 Full balance now deployed"
    )

def tg_partial_exit(tg: TelegramBot, pos: Position, action: str, price: float):
    pct = "20%" if action == "fix_20" else "30%"
    tg.send(
        f"💸 *PARTIAL EXIT* {pct} — Phase 5\n"
        f"🪙 *{pos.symbol}* @ `{price}`\n"
        f"📊 Reversal Score: {pos.reversal_score}\n"
        f"📈 PnL: *{pos.pnl_pct:+.1f}%* | Remaining: {pos.remaining_qty_pct*100:.0f}%"
    )

def tg_exit(tg: TelegramBot, pos: Position, price: float, reason: str):
    direction = "LONG" if pos.direction == "long" else "SHORT"
    ver = pos.strategy_version.upper()
    emoji = "🏁" if pos.pnl_pct >= 0 else "💀"
    tg.send(
        f"{emoji} *EXIT {direction}* [{ver}] — {reason}\n"
        f"🪙 *{pos.symbol}* | Entry: `{pos.entry_price}` → `{price}`\n"
        f"📈 PnL: *{pos.pnl_pct:+.1f}%* | Peak: `{pos.peak_price:.8g}`\n"
        f"📊 Reversal Score: {pos.reversal_score}"
    )

def tg_short_entry(tg: TelegramBot, pos: Position):
    tg.send(
        f"📉 *SHORT ENTRY* — Phase 6\n"
        f"🪙 *{pos.symbol}* @ `{pos.entry_price}`\n"
        f"💰 Size: {SHORT_SIZE_PCT}% × {LEVERAGE}x\n"
        f"🛑 SL: `{pos.stop_loss:.8g}` (P_max × 1.03)\n"
        f"🎯 Target: 70% of dump range"
    )

def tg_v3_entry(tg: TelegramBot, pos: Position, signal):
    dir_emoji = "📈" if pos.direction == "long" else "📉"
    dir_label = "LONG" if pos.direction == "long" else "SHORT"
    tg.send(
        f"⚡ *V3 {dir_label}* — Volume Impulse\n"
        f"🪙 *{pos.symbol}* ({pos.exchange.upper()})\n"
        f"{dir_emoji} Entry: `{pos.entry_price}`\n"
        f"🛑 SL: `{pos.stop_loss:.8g}`\n"
        f"📊 Score: {signal.combined_score} (vol_z={signal.vol_z} + ret_z={signal.ret_z})\n"
        f"⏱ TF: {signal.timeframe} | Size: {V3_SIZE_PCT}% × {V3_LEVERAGE}x"
    )


# ─── v1 Trailing Stop Logic ───────────────────────────────────

def _update_v1_trailing(pos: Position, price: float) -> Optional[str]:
    """v1-style trailing stop: adaptive trail from pump_scanner."""
    if pos.direction != "long" or pos.exited:
        return None

    profit_pct = (price / pos.entry_price - 1.0) * 100
    pos.pnl_pct = profit_pct

    # Update peak
    if price > pos.peak_price:
        pos.peak_price = price

    # Adaptive trailing stop
    trail_pct = get_trail_pct(profit_pct)
    new_stop = pos.peak_price * (1 - trail_pct / 100)
    if new_stop > pos.trailing_stop:
        pos.trailing_stop = new_stop

    # Floor: never below initial SL
    if pos.trailing_stop < pos.stop_loss:
        pos.trailing_stop = pos.stop_loss

    # Check trailing stop hit
    if price <= pos.trailing_stop:
        return "trailing_stop"

    # Check false breakout (price back in consolidation zone)
    if pos.consolidation and price <= pos.consolidation.high:
        return "false_breakout"

    return None


# ─── Position update loop ─────────────────────────────────────

def _update_positions(tg, positions: Dict[str, Position],
                      completed: list, hourly_cache: dict,
                      executor=None, balance: float = DEMO_BALANCE):
    to_remove = []
    pending_shorts = []  # Symbols to open short after long exit (v2 only)
    pnl_delta = 0.0

    for key, pos in positions.items():
        if pos.exited or not pos.confirmed:
            continue

        price = _get_current_price(pos.symbol, pos.exchange)
        if not price:
            continue

        # v5.0: PER-TRADE LOSS CAP — force close if loss exceeds MAX_LOSS_PER_TRADE_PCT
        if pos.direction == "long":
            current_pnl = (price / pos.entry_price - 1.0) * 100
        else:
            current_pnl = (pos.entry_price - price) / pos.entry_price * 100
        trade_loss_usd = abs(min(0, current_pnl) * balance * pos.size_pct / 100 * pos.leverage / 100)
        max_loss_usd = balance * MAX_LOSS_PER_TRADE_PCT / 100
        if current_pnl < 0 and trade_loss_usd >= max_loss_usd:
            pos.exited = True
            pos.pnl_pct = current_pnl
            pos.exit_reason = f"max_loss_cap_{MAX_LOSS_PER_TRADE_PCT}pct"
            tg_exit(tg, pos, price, f"🚨 MAX LOSS CAP ({MAX_LOSS_PER_TRADE_PCT}% of balance)")
            _record_trade(completed, pos, price, balance)
            pnl_delta += _calc_balance_pnl(pos, balance)
            if executor:
                executor.close_position_verified(pos.symbol, pos.direction)
            to_remove.append(key)
            log.warning(f"🚨 MAX LOSS CAP: {pos.symbol} [{pos.strategy_version}] loss=${trade_loss_usd:.0f} >= cap=${max_loss_usd:.0f}")
            continue

        # ── v1 LONG: simple trailing stop ─────────────────
        if pos.direction == "long" and pos.strategy_version == "v1":
            exit_r = _update_v1_trailing(pos, price)
            if exit_r:
                pos.exited = True
                pos.exit_reason = exit_r
                tg_exit(tg, pos, price, exit_r)
                _record_trade(completed, pos, price, balance)
                pnl_delta += _calc_balance_pnl(pos, balance)
                if executor:
                    executor.close_position_verified(pos.symbol, "long")
                to_remove.append(key)
            continue

        # ── v3: Volume-Impulse (LONG + SHORT) ─────────────
        if pos.strategy_version == "v3":
            hk = f"{pos.symbol}:{pos.exchange}:1h"
            h_df_cached = hourly_cache.get(hk)
            h_df = None
            if h_df_cached and isinstance(h_df_cached, tuple):
                h_df, _ = h_df_cached
            action = manage_v3_position(
                pos, price, hourly_df=h_df,
                trail_pct=V3_TRAIL_PCT, trail_tight_pct=V3_TRAIL_TIGHT_PCT,
                breakeven_at_pct=V3_BREAKEVEN_AT_PCT,
                partial_exit_pct=V3_PARTIAL_EXIT_PCT,
                partial_exit_size=V3_PARTIAL_EXIT_SIZE,
            )
            if action == "v3_partial_30":
                tg_partial_exit(tg, pos, "fix_30", price)
                if executor:
                    log.info(f"💸 v3 partial 30%: {pos.symbol}")
            elif action:
                pos.exited = True
                pos.exit_reason = action
                tg_exit(tg, pos, price, action)
                _record_trade(completed, pos, price, balance)
                pnl_delta += _calc_balance_pnl(pos, balance)
                if executor:
                    executor.close_position_verified(pos.symbol, pos.direction)
                to_remove.append(key)
            continue

        # ── v2 LONG active ────────────────────────────────
        if pos.direction == "long":
            profit = (price / pos.entry_price - 1.0) * 100
            pos.pnl_pct = profit
            if price > pos.peak_price:
                pos.peak_price = price

            hk = f"{pos.symbol}:{pos.exchange}:1h"
            h_df_cached = hourly_cache.get(hk)
            # hourly_cache stores (dataframe, timestamp) tuples — unpack
            if h_df_cached and isinstance(h_df_cached, tuple):
                h_df, h_ts = h_df_cached
                # Re-fetch if stale (> 15 min)
                if time.time() - h_ts > HOURLY_KLINES_CACHE_TTL:
                    h_df = _fetch_hourly(pos.symbol, pos.exchange)
                    hourly_cache[hk] = (h_df, time.time())
            else:
                # Cache miss — fetch now
                h_df = _fetch_hourly(pos.symbol, pos.exchange)
                if h_df is not None:
                    hourly_cache[hk] = (h_df, time.time())

            # Phase 3: addon check
            if not pos.addon_done and h_df is not None:
                if check_addon_buy(pos, price, h_df):
                    execute_addon(pos, price)
                    tg_addon(tg, pos, price)
                    if executor:
                        sz = balance * (100 - LONG_SIZE_PCT) / 100
                        executor.open_long(pos.symbol, sz, leverage=LEVERAGE)

            # Phase 4: reversal scoring
            if h_df is not None:
                compute_reversal_score(pos, price, h_df)

            # Phase 5: profit taking
            action = check_profit_taking(pos, price)
            if action in ("fix_20", "fix_30"):
                tg_partial_exit(tg, pos, action, price)
                if executor:
                    # Partial close: sell portion
                    close_pct = 0.20 if action == "fix_20" else 0.30
                    log.info(f"💸 Partial exit {close_pct*100:.0f}%: {pos.symbol}")
                log.info(f"💸 {action} for {pos.symbol} @ {price}")
            elif action == "close_all":
                pos.exited = True
                pos.exit_reason = f"reversal_score_{pos.reversal_score}"
                pos.p_max_final = pos.peak_price
                tg_exit(tg, pos, price, pos.exit_reason)
                _record_trade(completed, pos, price, balance)
                pnl_delta += _calc_balance_pnl(pos, balance)
                if executor:
                    executor.close_position_verified(pos.symbol, "long")
                pending_shorts.append(pos)
                to_remove.append(key)
                continue

            # Trailing stop
            exit_r = update_long_trailing(pos, price)
            if exit_r:
                pos.exited = True
                pos.exit_reason = exit_r
                pos.p_max_final = pos.peak_price
                tg_exit(tg, pos, price, exit_r)
                _record_trade(completed, pos, price, balance)
                pnl_delta += _calc_balance_pnl(pos, balance)
                if executor:
                    executor.close_position_verified(pos.symbol, "long")
                pending_shorts.append(pos)
                to_remove.append(key)

        # ── SHORT active ─────────────────────────────────────
        elif pos.direction == "short":
            profit = (pos.entry_price - price) / pos.entry_price * 100
            pos.pnl_pct = profit

            # Addon at 50% profit
            if check_short_addon(pos, price):
                pos.addon_done = True
                log.info(f"➕ SHORT addon: {pos.symbol} @ {price}")
                if executor:
                    executor.open_short(pos.symbol, balance * SHORT_SIZE_PCT / 100,
                                        leverage=LEVERAGE)

            # Exit check
            exit_r = check_short_exit(pos, price)
            if exit_r:
                pos.exited = True
                pos.exit_reason = exit_r
                tg_exit(tg, pos, price, exit_r)
                _record_trade(completed, pos, price, balance)
                pnl_delta += _calc_balance_pnl(pos, balance)
                if executor:
                    executor.close_position_verified(pos.symbol, "short")
                to_remove.append(key)

    # Remove exited
    for k in to_remove:
        del positions[k]

    # Open shorts for completed longs
    new_balance = balance + pnl_delta
    for long_pos in pending_shorts:
        price = _get_current_price(long_pos.symbol, long_pos.exchange)
        if not price:
            continue
        active_short_count = sum(
            1 for p in positions.values()
            if p.direction == "short" and not p.exited
        )
        if active_short_count >= MAX_CONCURRENT_POSITIONS:
            continue
        short_pos = Position(
            symbol=long_pos.symbol,
            exchange=long_pos.exchange,
            strategy_version="v2",  # Only v2 does SHORT
            p_consolidation=long_pos.p_consolidation,
            p_max_final=long_pos.peak_price,
            pump_pct=long_pos.pump_pct,
            consolidation=long_pos.consolidation,
            detected_at=time.time(),
            confirmed=True,
        )
        setup_short_entry(short_pos, price, new_balance)
        sk = f"{long_pos.symbol}:{long_pos.exchange}:short"
        positions[sk] = short_pos
        tg_short_entry(tg, short_pos)
        if executor:
            executor.open_short(
                long_pos.symbol,
                new_balance * SHORT_SIZE_PCT / 100,
                stop_price=short_pos.stop_loss,
                leverage=LEVERAGE,
            )

    return new_balance


def _calc_balance_pnl(pos: Position, balance: float) -> float:
    """Calculate absolute $ PnL for a closed position."""
    position_size = balance * pos.size_pct / 100
    return position_size * pos.leverage * pos.pnl_pct / 100


def _record_trade(completed: list, pos: Position, exit_price: float,
                  balance: float = DEMO_BALANCE):
    position_size = balance * pos.size_pct / 100
    pnl_usd = position_size * pos.leverage * pos.pnl_pct / 100
    completed.append({
        "symbol": pos.symbol,
        "exchange": pos.exchange,
        "direction": pos.direction,
        "strategy_version": pos.strategy_version,
        "phase": pos.phase,
        "entry": pos.entry_price,
        "exit": exit_price,
        "peak": pos.peak_price,
        "pnl_pct": round(pos.pnl_pct, 2),
        "pnl_usd": round(pnl_usd, 2),
        "size_pct": pos.size_pct,
        "leverage": pos.leverage,
        "pump_pct": pos.pump_pct,
        "exit_reason": pos.exit_reason,
        "reversal_score": pos.reversal_score,
        "remaining_qty_pct": pos.remaining_qty_pct,
        "balance_after": round(balance + pnl_usd, 2),
        "time": datetime.now(timezone.utc).isoformat(),
    })


# ─── Fetch hourly klines ─────────────────────────────────────

def _fetch_hourly(symbol: str, exchange: str) -> object:
    try:
        from pump_scanner import fetch_klines as fk
        return fk(symbol, exchange, "60", 500)
    except Exception:
        return None


# ─── Main scanner loop ────────────────────────────────────────

def run_scanner():
    tg = TelegramBot(TG_TOKEN, TG_CHAT_ID, TG_THREAD_ID)
    positions: Dict[str, Position] = {}
    cooldowns: Dict[str, float] = {}
    daily_cache: Dict[str, tuple] = {}
    hourly_cache: Dict[str, tuple] = {}
    v3_cache: Dict[str, tuple] = {}  # Separate cache for v3 multi-TF klines
    completed = []

    running = True
    def _stop(sig, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    # Load persisted state
    saved = _load_state()
    balance = saved.get("demo_balance", DEMO_BALANCE)
    completed = saved.get("completed_trades", [])
    for sym, pdata in saved.get("active_positions", {}).items():
        try:
            pos = Position.from_dict(pdata)
            ver = pos.strategy_version
            k = f"{pos.symbol}:{pos.exchange}:{ver}"
            if pos.direction == "short":
                k += ":short"
            positions[k] = pos
            log.info(f"📂 Restored: {sym} [{ver}] {pos.direction} @ {pos.entry_price}")
        except Exception as e:
            log.warning(f"Restore failed {sym}: {e}")

    scan_count = saved.get("scan_count", 0)
    start_ts = time.time()
    # v6.0: Track high-water mark for drawdown calculation
    max_balance = max(balance, saved.get("max_balance", DEMO_BALANCE))

    # Exchange executor
    executor = None
    mode = os.getenv("TRADING_MODE", "paper")
    if ExchangeExecutor and mode != "paper":
        try:
            executor = ExchangeExecutor.from_env(bot_id="pump_hunter_v2")
            info = executor.test_connection()
            log.info(f"⚡ Exchange: {executor} | ${info.get('balance_usdt',0):.2f}")
        except Exception as e:
            log.error(f"Executor init failed: {e}")
            executor = None

    tg.send(
        f"🎯 *PUMP HUNTER TRIPLE STARTED*\n"
        f"🔀 v1 (trailing) + v2 (6-phase) + v3 (volume-impulse)\n"
        f"⚡ v3: z-score on 15m/30m/1h | vol_z≥{V3_MIN_VOLUME_Z} ret_z≥{V3_MIN_RETURN_Z}\n"
        f"Max positions: {MAX_CONCURRENT_POSITIONS}\n"
        f"💰 Balance: ${balance:,.0f} | Mode: {mode.upper()}\n"
        f"📂 Restored: {len(positions)} positions | {len(completed)} trades"
    )
    log.info("Pump Hunter TRIPLE (v1+v2+v3) started")

    while running:
        loop_start = time.time()
        scan_count += 1
        now = time.time()

        # v6.0: DRAWDOWN HALT — use high-water mark instead of initial deposit
        max_balance = max(max_balance, balance)
        drawdown_pct = (1 - balance / max_balance) * 100 if max_balance > 0 else 0
        halted = drawdown_pct >= DAILY_LOSS_LIMIT_PCT
        if halted:
            log.warning(f"🚨 DRAWDOWN HALT: balance ${balance:,.0f} vs HWM ${max_balance:,.0f} = -{drawdown_pct:.1f}% (limit: {DAILY_LOSS_LIMIT_PCT}%)")

        # Sync external closes (HQ dashboard manual close fix)
        synced_bal = _sync_external_closes(positions, completed)
        if synced_bal != balance:
            balance = synced_bal

        # Update active positions (returns updated balance)
        balance = _update_positions(tg, positions, completed, hourly_cache,
                                    executor=executor, balance=balance)

        # Scan for new pumps
        all_tickers = []
        if ENABLE_BYBIT:
            all_tickers.extend(fetch_bybit_tickers())
        if ENABLE_MEXC:
            all_tickers.extend(fetch_mexc_tickers())
        if ENABLE_GATEIO:
            all_tickers.extend(fetch_gateio_tickers())
        if ENABLE_BITGET:
            all_tickers.extend(fetch_bitget_tickers())

        log.info(f"🔍 Scan #{scan_count} — {len(all_tickers)} tickers")

        active_count = sum(1 for p in positions.values()
                           if p.confirmed and not p.exited)

        # v5.0: Skip new entries when drawdown halted (still monitor existing positions)
        if halted:
            log.info(f"⏸️ Drawdown halt active — skipping new entries (monitoring {active_count} positions)")
            _save_state(positions, completed, scan_count, start_ts, balance, executor, max_balance)
            elapsed = time.time() - loop_start
            sleep_sec = max(10, SCAN_INTERVAL_SEC - elapsed)
            sleep_end = time.time() + sleep_sec
            while running and time.time() < sleep_end:
                time.sleep(5)
                _sync_external_closes(positions, completed)
                balance = _update_positions(tg, positions, completed, hourly_cache,
                                            executor=executor, balance=balance)
                _save_state(positions, completed, scan_count, start_ts, balance, executor, max_balance)
            continue

        candidates = [
            t for t in all_tickers
            if (t["symbol"] not in cooldowns or now >= cooldowns.get(t["symbol"], 0))
            and abs(t["price24hPcnt"]) * 100 >= PREFILTER_24H_CHANGE_PCT
        ]

        for t in candidates[:40]:
            sym, exch = t["symbol"], t["exchange"]
            dk = f"{sym}:{exch}:D"
            hk = f"{sym}:{exch}:1h"

            # Daily cache
            if dk not in daily_cache or now - daily_cache[dk][1] > KLINES_CACHE_TTL_SEC:
                df = fetch_klines(sym, exch, "D", 60)
                daily_cache[dk] = (df, now)
            daily_df, _ = daily_cache[dk]

            # Hourly cache
            if hk not in hourly_cache or now - hourly_cache[hk][1] > HOURLY_KLINES_CACHE_TTL:
                hdf = _fetch_hourly(sym, exch)
                hourly_cache[hk] = (hdf, now)
            hourly_df, _ = hourly_cache[hk]

            if daily_df is None or daily_df.empty:
                continue

            # ── Triple detection: v1, v2, v3 independently ─────
            v2_result = detect_pump(hourly_df, daily_df)
            v1_result = analyze_for_pump(daily_df)

            # v3: Volume-impulse z-score on multi-TF
            v3_params = {
                "min_volume_z": V3_MIN_VOLUME_Z,
                "min_return_z": V3_MIN_RETURN_Z,
                "min_combined_score": V3_MIN_COMBINED_SCORE,
            }
            v3_result = detect_multi_tf(
                fetch_klines, sym, exch, v3_params,
                v3_cache, cache_ttl=V3_CACHE_TTL_SEC,
            )

            any_detected = v1_result or v2_result or v3_result
            if not any_detected:
                continue

            if v3_result:
                log.info(f"⚡ v3 IMPULSE: {sym} ({exch}) score={v3_result.combined_score} "
                         f"vol_z={v3_result.vol_z} ret_z={v3_result.ret_z} tf={v3_result.timeframe} "
                         f"dir={v3_result.direction}")

            # ── Open v2 position if detected ──────────────────
            k_v2 = f"{sym}:{exch}:v2"
            if v2_result and k_v2 not in positions:
                if active_count >= MAX_CONCURRENT_POSITIONS:
                    log.info(f"⏭️ Skip {sym} v2: max positions")
                else:
                    zone, pump_pct, price = v2_result
                    pos = Position(
                        symbol=sym, exchange=exch,
                        strategy_version="v2",
                        pump_pct=pump_pct,
                        p_consolidation=zone.mean,
                        consolidation=zone,
                        detected_at=now,
                    )
                    if AUTO_ENTER:
                        setup_long_entry(pos, price, balance)
                        pos.confirmed = True
                        positions[k_v2] = pos
                        active_count += 1
                        cooldowns[sym] = now + ALERT_COOLDOWN_SEC
                        tg_entry(tg, pos)
                        if executor:
                            r = executor.open_long(
                                sym,
                                balance * LONG_SIZE_PCT / 100,
                                stop_price=pos.stop_loss,
                                leverage=LEVERAGE,
                            )
                            if r.success and r.fill_price > 0:
                                pos.entry_price = r.fill_price
                                pos.trailing_stop = pos.stop_loss
                    else:
                        positions[k_v2] = pos
                        cooldowns[sym] = now + ALERT_COOLDOWN_SEC

            # ── Open v1 position if detected ──────────────────
            k_v1 = f"{sym}:{exch}:v1"
            if v1_result and k_v1 not in positions:
                if active_count >= MAX_CONCURRENT_POSITIONS:
                    log.info(f"⏭️ Skip {sym} v1: max positions")
                else:
                    zone_v1, pump_pct_v1, price_v1 = v1_result
                    pos_v1 = Position(
                        symbol=sym, exchange=exch,
                        strategy_version="v1",
                        pump_pct=pump_pct_v1,
                        p_consolidation=zone_v1.mean,
                        consolidation=zone_v1,
                        detected_at=now,
                    )
                    if AUTO_ENTER:
                        # v1 entry: use zone.high as SL, simple trailing
                        pos_v1.phase = 2
                        pos_v1.direction = "long"
                        pos_v1.entry_price = price_v1
                        pos_v1.peak_price = price_v1
                        pos_v1.size_pct = LONG_SIZE_PCT
                        pos_v1.leverage = LEVERAGE
                        pos_v1.stop_loss = zone_v1.high  # v1: SL at top of zone
                        pos_v1.trailing_stop = zone_v1.high
                        pos_v1.confirmed = True
                        positions[k_v1] = pos_v1
                        active_count += 1
                        tg_entry(tg, pos_v1)
                        if executor:
                            r = executor.open_long(
                                sym,
                                balance * LONG_SIZE_PCT / 100,
                                stop_price=pos_v1.stop_loss,
                                leverage=LEVERAGE,
                            )
                            if r.success and r.fill_price > 0:
                                pos_v1.entry_price = r.fill_price
                                pos_v1.trailing_stop = pos_v1.stop_loss
                    else:
                        positions[k_v1] = pos_v1

            # ── Open v3 position if detected ──────────────────
            k_v3 = f"{sym}:{exch}:v3"
            k_v3s = f"{sym}:{exch}:v3:short"
            if v3_result and k_v3 not in positions and k_v3s not in positions:
                if active_count >= MAX_CONCURRENT_POSITIONS:
                    log.info(f"⏭️ Skip {sym} v3: max positions")
                elif sym in cooldowns and now < cooldowns.get(sym, 0):
                    pass  # In cooldown
                else:
                    sig = v3_result
                    cur_price = _get_current_price(sym, exch) or sig.entry_price
                    pos_v3 = Position(
                        symbol=sym, exchange=exch,
                        strategy_version="v3",
                        pump_pct=0,
                        p_consolidation=sig.stop_price,
                        detected_at=now,
                    )
                    if AUTO_ENTER:
                        pos_v3.phase = 2
                        pos_v3.direction = sig.direction
                        pos_v3.entry_price = cur_price
                        pos_v3.peak_price = cur_price
                        pos_v3.dump_min = cur_price if sig.direction == "short" else 0
                        pos_v3.size_pct = V3_SIZE_PCT
                        pos_v3.leverage = V3_LEVERAGE
                        pos_v3.stop_loss = sig.stop_price
                        pos_v3.trailing_stop = sig.stop_price
                        pos_v3.confirmed = True
                        key_v3 = k_v3s if sig.direction == "short" else k_v3
                        positions[key_v3] = pos_v3
                        active_count += 1
                        cooldowns[sym] = now + V3_COOLDOWN_SEC
                        tg_v3_entry(tg, pos_v3, sig)
                        if executor:
                            open_fn = executor.open_short if sig.direction == "short" else executor.open_long
                            r = open_fn(
                                sym,
                                balance * V3_SIZE_PCT / 100,
                                stop_price=pos_v3.stop_loss,
                                leverage=V3_LEVERAGE,
                            )
                            if r.success and r.fill_price > 0:
                                pos_v3.entry_price = r.fill_price
                    else:
                        positions[k_v3] = pos_v3
                        cooldowns[sym] = now + V3_COOLDOWN_SEC

        _save_state(positions, completed, scan_count, start_ts, balance, executor, max_balance)

        active = sum(1 for p in positions.values() if p.confirmed and not p.exited)
        v1_a = sum(1 for p in positions.values() if p.confirmed and not p.exited and p.strategy_version == "v1")
        v2_a = sum(1 for p in positions.values() if p.confirmed and not p.exited and p.strategy_version == "v2")
        v3_a = sum(1 for p in positions.values() if p.confirmed and not p.exited and p.strategy_version == "v3")
        log.info(f"Active: {active} (v1:{v1_a} v2:{v2_a} v3:{v3_a}) | "
                 f"Completed: {len(completed)} | Balance: ${balance:,.0f}")

        # Sleep with position monitoring
        elapsed = time.time() - loop_start
        sleep_sec = max(10, SCAN_INTERVAL_SEC - elapsed)
        log.info(f"💤 Next scan in {sleep_sec/60:.0f} min")
        sleep_end = time.time() + sleep_sec
        while running and time.time() < sleep_end:
            time.sleep(5)
            _sync_external_closes(positions, completed)
            balance = _update_positions(tg, positions, completed, hourly_cache,
                                        executor=executor, balance=balance)
            _save_state(positions, completed, scan_count, start_ts, balance, executor, max_balance)

    tg.send("🛑 *Pump Hunter TRIPLE STOPPED*")
    log.info("Stopped.")


if __name__ == "__main__":
    run_scanner()
