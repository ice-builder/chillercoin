"""Insider Position Manager v2 — manages insider positions with proper risk controls.
Own state file, hard stop, trailing stop, time stop.
v2: Dynamic direction, IIE gate + feedback, wider stops at 5x leverage.
"""
import json
import time
import logging
import requests
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Optional

import config

logger = logging.getLogger("insider.pm")

# v2: IIE integration (optional — graceful fallback if unavailable)
_iie_manager = None
_iie_db = None
try:
    import sys
    sys.path.insert(0, "/home/trader/soldier")
    from iie.adaptive_manager import AdaptivePositionManager
    from iie.impulse_db import ImpulseDB, TradeOutcome
    _iie_manager = AdaptivePositionManager()
    _iie_db = ImpulseDB()
    logger.info("🧠 IIE integration active for Insider Scanner")
except Exception as e:
    logger.info(f"📝 IIE not available: {e} — running without ML gate")

STATE_FILE = Path(config.STATE_FILE)  # insider_positions.json
TRADES_FILE = Path("insider_trades.json")


class InsiderPositionManager:
    """Manages insider auto-enter positions with stop loss logic."""

    def __init__(self, tg_notifier=None):
        self.tg = tg_notifier
        self.state = self._load_state()

    # ─── State I/O ─────────────────────────────────

    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"Failed to load state: {e}")
        return {
            "balance": 10000.0,
            "active_positions": {},
            "daily_pnl": 0.0,
            "daily_reset": "",
            "total_trades": 0,
            "total_pnl_usd": 0.0,
        }

    def _save_state(self):
        self.state["last_updated"] = datetime.now(timezone.utc).isoformat()
        STATE_FILE.write_text(
            json.dumps(self.state, indent=2, default=str), encoding="utf-8"
        )

    def _record_trade(self, trade: dict):
        """Append completed trade to history file."""
        trades = []
        if TRADES_FILE.exists():
            try:
                trades = json.loads(TRADES_FILE.read_text(encoding="utf-8"))
            except Exception:
                trades = []
        trades.append(trade)
        # Keep last 500 trades
        if len(trades) > 500:
            trades = trades[-500:]
        TRADES_FILE.write_text(
            json.dumps(trades, indent=2, default=str), encoding="utf-8"
        )

    # ─── Price Fetching ────────────────────────────

    @staticmethod
    def fetch_price(symbol: str, exchange: str = "bybit") -> float:
        """Get current price. Prefer Bybit for reliability."""
        try:
            r = requests.get(
                f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}",
                timeout=5,
            )
            d = r.json()
            if d.get("result", {}).get("list"):
                return float(d["result"]["list"][0]["lastPrice"])
        except Exception:
            pass
        return 0.0

    # ─── Open Position ─────────────────────────────

    def open_position(self, score) -> bool:
        """Open a new insider position from a scored signal.
        Args: score = InsiderScore dataclass from scorer.py
        Returns: True if position opened.
        """
        active = self.state.get("active_positions", {})

        # ── Daily loss limit ──
        self._check_daily_reset()
        if abs(self.state.get("daily_pnl", 0)) >= self.state["balance"] * config.MAX_LOSS_PER_DAY_PCT / 100:
            logger.info(f"Daily loss limit reached ({config.MAX_LOSS_PER_DAY_PCT}%), skipping {score.symbol}")
            return False

        # ── Max positions ──
        if len(active) >= config.MAX_POSITIONS:
            logger.info(f"Max positions ({config.MAX_POSITIONS}) reached, skipping {score.symbol}")
            return False

        # ── Duplicate check ──
        key = f"{score.symbol}:insider"
        if key in active:
            logger.info(f"Already tracking {score.symbol}, skipping")
            return False

        # ── Price validation ──
        price = score.price
        if price < config.MIN_ENTRY_PRICE:
            price = self.fetch_price(score.symbol)
        if price < config.MIN_ENTRY_PRICE:
            logger.warning(f"Invalid price for {score.symbol}: {price}, skipping")
            return False

        # ── v2: Direction detection ──
        if config.ENTRY_DIRECTION == "auto":
            # Price rising + OI surge = accumulation → LONG
            # Price falling + OI surge = distribution → SHORT
            if score.price_change_24h > 5:
                direction = "long"
            elif score.price_change_24h < -5:
                direction = "short"
            else:
                direction = "long"  # Default to long for OI-driven signals
        else:
            direction = config.ENTRY_DIRECTION

        # ── v2: IIE quality gate ──
        if _iie_manager:
            try:
                iie_rec = _iie_manager.evaluate_signal(
                    symbol=score.symbol,
                    direction=direction,
                    source="insider",
                )
                if iie_rec.score < 40:
                    logger.info(
                        f"⏭️ {score.symbol} blocked by IIE: score={iie_rec.score:.0f} < 40"
                    )
                    return False
                logger.info(f"🧠 IIE approved {score.symbol}: score={iie_rec.score:.0f}")
            except Exception as e:
                logger.debug(f"IIE gate check failed: {e}")

        # ── Exchange selection ──
        exchange = "bybit"
        if score.oi_exchanges:
            preferred = ["bybit", "binance", "bitget", "mexc", "gateio"]
            for pex in preferred:
                if pex in score.oi_exchanges:
                    exchange = pex
                    break

        # ── Position sizing (v7.0: tiered by score) ──
        balance = self.state["balance"]
        # High-conviction signals (score ≥ 22) get larger position
        size_pct = config.POSITION_SIZE_HIGH_PCT if score.total_score >= getattr(config, 'HIGH_SCORE_THRESHOLD', 22) else config.POSITION_SIZE_PCT
        size_usdt = balance * size_pct / 100
        notional = size_usdt * config.LEVERAGE

        # ── v2: Direction-aware stop ──
        if direction == "long":
            hard_stop = round(price * (1 + config.HARD_STOP_PCT / 100), 8)
        else:
            hard_stop = round(price * (1 - config.HARD_STOP_PCT / 100), 8)

        # ── Create position ──
        now = datetime.now(timezone.utc)
        position = {
            "symbol": score.symbol,
            "exchange": exchange,
            "direction": direction,
            "entry_price": price,
            "current_price": price,
            "peak_price": price,
            "size_usdt": round(size_usdt, 2),
            "leverage": config.LEVERAGE,
            "insider_score": score.total_score,
            "insider_breakdown": score.breakdown,
            "oi_exchanges": score.oi_exchanges,
            "flow_exchanges": score.flow_exchanges,
            "entry_time": now.isoformat(),
            "hard_stop": hard_stop,
            "trail_active": False,
            "trail_price": 0.0,
        }

        active[key] = position
        self.state["active_positions"] = active
        self._save_state()

        dir_icon = "🟢 LONG" if direction == "long" else "🔴 SHORT"
        msg = (
            f"🕵️ *INSIDER OPEN*\n"
            f"{dir_icon} *{score.symbol}* on {exchange.upper()}\n"
            f"Score: *{score.total_score}*\n"
            f"Entry: ${price:.6g}\n"
            f"Size: ${size_usdt:,.0f} × {config.LEVERAGE}x = ${notional:,.0f}\n"
            f"Stop: ${hard_stop:.6g} ({config.HARD_STOP_PCT}%)\n"
            f"OI: {', '.join(score.oi_exchanges)}\n"
            f"Flow: {', '.join(score.flow_exchanges)}"
        )
        logger.info(msg)
        if self.tg:
            self.tg.send(msg)
        return True

    # ─── Check Exits ───────────────────────────────

    def check_exits(self):
        """Check all open positions for exit conditions. Called every scan cycle."""
        active = self.state.get("active_positions", {})
        to_close = []

        for key, pos in active.items():
            symbol = pos["symbol"]
            entry = pos["entry_price"]
            direction = pos.get("direction", "long")
            cur = self.fetch_price(symbol, pos.get("exchange", "bybit"))
            if cur <= 0:
                continue

            pos["current_price"] = cur

            # v2: Direction-aware PnL
            if direction == "long":
                pnl_pct = ((cur / entry) - 1) * 100 if entry > 0 else 0
            else:
                pnl_pct = ((entry / cur) - 1) * 100 if cur > 0 else 0

            # Update peak (direction-aware)
            if direction == "long":
                if cur > pos.get("peak_price", 0):
                    pos["peak_price"] = cur
            else:
                if pos.get("peak_price", 0) == 0 or cur < pos["peak_price"]:
                    pos["peak_price"] = cur

            # ── Hard stop ──
            if pnl_pct <= config.HARD_STOP_PCT:
                to_close.append((key, "hard_stop", cur, pnl_pct))
                continue

            # ── Trailing stop (direction-aware) ──
            if pnl_pct >= config.TRAIL_ACTIVATION_PCT:
                pos["trail_active"] = True
                if direction == "long":
                    trail_price = pos["peak_price"] * (1 - config.TRAIL_STOP_PCT / 100)
                    pos["trail_price"] = trail_price
                    if cur <= trail_price:
                        to_close.append((key, "trailing_stop", cur, pnl_pct))
                        continue
                else:
                    trail_price = pos["peak_price"] * (1 + config.TRAIL_STOP_PCT / 100)
                    pos["trail_price"] = trail_price
                    if cur >= trail_price:
                        to_close.append((key, "trailing_stop", cur, pnl_pct))
                        continue

            # ── Time stop ──
            entry_time = pos.get("entry_time", "")
            if entry_time:
                try:
                    et = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                    hours = (datetime.now(timezone.utc) - et).total_seconds() / 3600
                    if hours >= config.TIME_STOP_HOURS and abs(pnl_pct) < 1.0:
                        to_close.append((key, "time_stop", cur, pnl_pct))
                        continue
                except Exception:
                    pass

        # ── Execute closes ──
        for key, reason, exit_price, pnl_pct in to_close:
            self._close_position(key, reason, exit_price, pnl_pct)

        self._save_state()

    def _close_position(self, key: str, reason: str, exit_price: float, pnl_pct: float):
        """Close a position and record the trade."""
        active = self.state.get("active_positions", {})
        pos = active.pop(key, None)
        if not pos:
            return

        size = pos.get("size_usdt", 0)
        lev = pos.get("leverage", config.LEVERAGE)
        pnl_usd = size * lev * pnl_pct / 100
        now = datetime.now(timezone.utc)

        # Update balance
        self.state["balance"] += pnl_usd
        self.state["daily_pnl"] = self.state.get("daily_pnl", 0) + pnl_usd
        self.state["total_trades"] = self.state.get("total_trades", 0) + 1
        self.state["total_pnl_usd"] = self.state.get("total_pnl_usd", 0) + pnl_usd
        self.state["active_positions"] = active

        # Record trade
        trade = {
            **pos,
            "exit_price": exit_price,
            "exit_time": now.isoformat(),
            "exit_reason": reason,
            "pnl_pct": round(pnl_pct, 2),
            "pnl_usd": round(pnl_usd, 2),
            "duration_min": 0,
        }
        try:
            et = datetime.fromisoformat(pos["entry_time"].replace("Z", "+00:00"))
            trade["duration_min"] = round((now - et).total_seconds() / 60)
        except Exception:
            pass
        self._record_trade(trade)

        emoji = "🟢" if pnl_pct >= 0 else "🔴"
        direction = pos.get('direction', 'long')
        dir_icon = "🟢 LONG" if direction == "long" else "🔴 SHORT"
        msg = (
            f"🕵️ *INSIDER CLOSE*\n"
            f"{emoji} {dir_icon} *{pos['symbol']}* — {reason.upper()}\n"
            f"Entry: ${pos['entry_price']:.6g} → Exit: ${exit_price:.6g}\n"
            f"PnL: *{pnl_pct:+.1f}%* (${pnl_usd:+,.0f})\n"
            f"Duration: {trade['duration_min']}min\n"
            f"Balance: ${self.state['balance']:,.0f}"
        )
        logger.info(msg)
        if self.tg:
            self.tg.send(msg)

        # v2: IIE feedback loop — record outcome for ML learning
        if _iie_db:
            try:
                _iie_db.insert_trade(TradeOutcome(
                    symbol=pos['symbol'],
                    exchange=pos.get('exchange', 'bybit'),
                    direction=direction,
                    entry_price=pos['entry_price'],
                    exit_price=exit_price,
                    pnl_pct=round(pnl_pct, 2),
                    exit_reason=reason,
                    strategy_name=f"insider_s{pos.get('insider_score', 0)}",
                    bot_name="insider",
                    entry_time=time.time() - trade['duration_min'] * 60,
                    exit_time=time.time(),
                ))
                logger.info(f"🧠 IIE outcome recorded: {pos['symbol']} {pnl_pct:+.1f}%")
            except Exception as e:
                logger.debug(f"IIE outcome recording failed: {e}")

    # ─── Helpers ───────────────────────────────────

    def _check_daily_reset(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state.get("daily_reset") != today:
            self.state["daily_pnl"] = 0.0
            self.state["daily_reset"] = today

    def get_active_count(self) -> int:
        return len(self.state.get("active_positions", {}))

    def get_balance(self) -> float:
        return self.state.get("balance", 10000)
