"""
IIE v4 Strategy Integration for Pump Hunter

Reads pending signals from IIE SQLite DB and opens/manages v4 positions.
Called from pump_scanner_v2.py main loop.
"""
import sys
import time
import logging
from pathlib import Path
from typing import Optional, Dict, List

logger = logging.getLogger("PumpHunterV2.v4")

# IIE DB path — same SQLite used by iie-engine
IIE_DB_PATH = Path("/home/trader/soldier/iie/data/impulses.db")

# Lazy import to avoid breaking pump_scanner if IIE not installed
_iie_db = None


def _get_db():
    """Lazy-load IIE DB connection."""
    global _iie_db
    if _iie_db is not None:
        return _iie_db

    try:
        sys.path.insert(0, str(Path("/home/trader/soldier")))
        from iie.impulse_db import ImpulseDB
        _iie_db = ImpulseDB(IIE_DB_PATH)
        logger.info("🧠 IIE DB connected for v4 signals")
        return _iie_db
    except Exception as e:
        logger.warning(f"IIE DB not available: {e}")
        return None


def get_iie_signals() -> List[dict]:
    """Fetch pending IIE signals for v4 execution."""
    db = _get_db()
    if not db:
        return []
    try:
        return db.get_pending_signals(limit=10)
    except Exception as e:
        logger.warning(f"Failed to fetch IIE signals: {e}")
        return []


def mark_signal_done(signal_id: int):
    """Mark signal as processed after opening position."""
    db = _get_db()
    if db:
        try:
            db.mark_signal_processed(signal_id)
        except Exception as e:
            logger.warning(f"Failed to mark signal {signal_id}: {e}")


def record_v4_outcome(symbol: str, exchange: str, direction: str,
                      entry_price: float, exit_price: float,
                      pnl_pct: float, exit_reason: str,
                      entry_time: float, exit_time: float,
                      impulse_id: int = None):
    """Record v4 trade outcome back to IIE for learning."""
    db = _get_db()
    if not db:
        return

    try:
        from iie.impulse_db import TradeOutcome
        phase = db.get_current_phase()
        trade = TradeOutcome(
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason,
            strategy_name="v4_iie",
            bot_name="pump_hunter",
            impulse_id=impulse_id,
            market_phase_at_entry=phase.phase if phase else "unknown",
            entry_time=entry_time,
            exit_time=exit_time,
        )
        db.insert_trade(trade)
        logger.info(f"🧠 Recorded v4 outcome: {symbol} PnL={pnl_pct:+.2f}%")
    except Exception as e:
        logger.warning(f"Failed to record v4 outcome: {e}")


def send_v4_close_notification(symbol: str, direction: str,
                               entry_price: float, exit_price: float,
                               pnl_pct: float, exit_reason: str,
                               peak_price: float = 0, held_bars: int = 0):
    """Send close notification to PH group via IIE signals module."""
    try:
        sys.path.insert(0, str(Path("/home/trader/soldier")))
        from iie.iie_signals import send_close_notification
        send_close_notification(
            symbol=symbol, direction=direction,
            entry_price=entry_price, exit_price=exit_price,
            pnl_pct=pnl_pct, exit_reason=exit_reason,
            peak_price=peak_price, held_bars=held_bars,
        )
    except Exception as e:
        logger.warning(f"Close notification failed: {e}")
