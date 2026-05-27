"""
IIE — SQLite Database Layer

Tables:
  impulses            — every detected impulse with context
  post_impulse_outcomes — what happened after (filled async)
  coin_profiles       — aggregated per-coin scoring
  market_phases       — BTC/ETH macro regime
  trade_outcomes      — real trades linked to impulses
"""
import sqlite3
import json
import time
import logging
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from . import config

logger = logging.getLogger("iie.db")

# ─── Dataclasses ─────────────────────────────────

@dataclass
class Impulse:
    id: int = 0
    symbol: str = ""
    exchange: str = ""
    timeframe: str = ""
    timestamp: float = 0.0
    direction: str = ""          # "long" / "short"
    vol_z: float = 0.0
    ret_z: float = 0.0
    combined_score: float = 0.0
    rsi_at_impulse: float = 50.0
    ema_deviation_pct: float = 0.0
    price_at_impulse: float = 0.0
    candle_body_pct: float = 0.0
    wick_ratio_top: float = 0.0
    wick_ratio_bottom: float = 0.0
    impulse_location: str = ""   # "at_high" / "at_low" / "mid_range"
    consolidation_days: int = 0
    atr_at_impulse: float = 0.0
    source: str = ""             # "collector" / "soldier" / "pump_hunter" / "insider"


@dataclass
class PostImpulseOutcome:
    id: int = 0
    impulse_id: int = 0
    price_after_5m: float = 0.0
    price_after_15m: float = 0.0
    price_after_1h: float = 0.0
    price_after_4h: float = 0.0
    price_after_24h: float = 0.0
    price_after_48h: float = 0.0
    price_after_7d: float = 0.0
    max_favorable_pct: float = 0.0
    max_adverse_pct: float = 0.0
    continuation_impulses: int = 0
    reversal_pct: float = 0.0
    was_stop_hunt: bool = False
    level_break: bool = False
    new_extremum: bool = False
    tracking_complete: bool = False
    last_updated: float = 0.0


@dataclass
class CoinProfile:
    symbol: str = ""
    impulse_count: int = 0
    avg_continuation_pct: float = 0.0
    avg_retracement_pct: float = 0.0
    stop_hunt_frequency: float = 0.0
    avg_time_to_extremum: float = 0.0
    level_respect_score: float = 50.0
    volatility_regime: str = "medium"
    listing_age_days: int = 0
    impulse_regularity: float = 0.0
    best_tf: str = "5"
    recommended_sl_mult: float = 1.5
    recommended_hold_bars: int = 10
    momentum_persistence: float = 50.0
    impulse_quality_score: float = 50.0
    predictability_score: float = 50.0
    last_updated: float = 0.0


@dataclass
class MarketPhase:
    id: int = 0
    timestamp: float = 0.0
    btc_price: float = 0.0
    eth_price: float = 0.0
    btc_monthly_change_pct: float = 0.0
    eth_monthly_change_pct: float = 0.0
    btc_weekly_change_pct: float = 0.0
    btc_ema_fast: float = 0.0
    btc_ema_slow: float = 0.0
    btc_atr_daily: float = 0.0
    phase: str = "sideways"      # trending_up / trending_down / sideways / volatile
    alt_correlation: float = 0.5


@dataclass
class TradeOutcome:
    id: int = 0
    symbol: str = ""
    exchange: str = ""
    direction: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""
    strategy_name: str = ""
    bot_name: str = ""           # "soldier" / "pump_hunter" / "insider"
    impulse_id: Optional[int] = None
    market_phase_at_entry: str = ""
    entry_time: float = 0.0
    exit_time: float = 0.0
    post_exit_max_favorable: float = 0.0
    post_exit_max_adverse: float = 0.0


# ─── Database ────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS impulses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp REAL NOT NULL,
    direction TEXT NOT NULL,
    vol_z REAL DEFAULT 0,
    ret_z REAL DEFAULT 0,
    combined_score REAL DEFAULT 0,
    rsi_at_impulse REAL DEFAULT 50,
    ema_deviation_pct REAL DEFAULT 0,
    price_at_impulse REAL DEFAULT 0,
    candle_body_pct REAL DEFAULT 0,
    wick_ratio_top REAL DEFAULT 0,
    wick_ratio_bottom REAL DEFAULT 0,
    impulse_location TEXT DEFAULT '',
    consolidation_days INTEGER DEFAULT 0,
    atr_at_impulse REAL DEFAULT 0,
    source TEXT DEFAULT 'collector'
);

CREATE TABLE IF NOT EXISTS post_impulse_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    impulse_id INTEGER NOT NULL UNIQUE,
    price_after_5m REAL DEFAULT 0,
    price_after_15m REAL DEFAULT 0,
    price_after_1h REAL DEFAULT 0,
    price_after_4h REAL DEFAULT 0,
    price_after_24h REAL DEFAULT 0,
    price_after_48h REAL DEFAULT 0,
    price_after_7d REAL DEFAULT 0,
    max_favorable_pct REAL DEFAULT 0,
    max_adverse_pct REAL DEFAULT 0,
    continuation_impulses INTEGER DEFAULT 0,
    reversal_pct REAL DEFAULT 0,
    was_stop_hunt INTEGER DEFAULT 0,
    level_break INTEGER DEFAULT 0,
    new_extremum INTEGER DEFAULT 0,
    tracking_complete INTEGER DEFAULT 0,
    last_updated REAL DEFAULT 0,
    FOREIGN KEY (impulse_id) REFERENCES impulses(id)
);

CREATE TABLE IF NOT EXISTS coin_profiles (
    symbol TEXT PRIMARY KEY,
    impulse_count INTEGER DEFAULT 0,
    avg_continuation_pct REAL DEFAULT 0,
    avg_retracement_pct REAL DEFAULT 0,
    stop_hunt_frequency REAL DEFAULT 0,
    avg_time_to_extremum REAL DEFAULT 0,
    level_respect_score REAL DEFAULT 50,
    volatility_regime TEXT DEFAULT 'medium',
    listing_age_days INTEGER DEFAULT 0,
    impulse_regularity REAL DEFAULT 0,
    best_tf TEXT DEFAULT '5',
    recommended_sl_mult REAL DEFAULT 1.5,
    recommended_hold_bars INTEGER DEFAULT 10,
    momentum_persistence REAL DEFAULT 50,
    impulse_quality_score REAL DEFAULT 50,
    predictability_score REAL DEFAULT 50,
    last_updated REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS market_phases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    btc_price REAL DEFAULT 0,
    eth_price REAL DEFAULT 0,
    btc_monthly_change_pct REAL DEFAULT 0,
    eth_monthly_change_pct REAL DEFAULT 0,
    btc_weekly_change_pct REAL DEFAULT 0,
    btc_ema_fast REAL DEFAULT 0,
    btc_ema_slow REAL DEFAULT 0,
    btc_atr_daily REAL DEFAULT 0,
    phase TEXT DEFAULT 'sideways',
    alt_correlation REAL DEFAULT 0.5
);

CREATE TABLE IF NOT EXISTS trade_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    exchange TEXT DEFAULT '',
    direction TEXT NOT NULL,
    entry_price REAL DEFAULT 0,
    exit_price REAL DEFAULT 0,
    pnl_pct REAL DEFAULT 0,
    exit_reason TEXT DEFAULT '',
    strategy_name TEXT DEFAULT '',
    bot_name TEXT DEFAULT '',
    impulse_id INTEGER,
    market_phase_at_entry TEXT DEFAULT '',
    entry_time REAL DEFAULT 0,
    exit_time REAL DEFAULT 0,
    post_exit_max_favorable REAL DEFAULT 0,
    post_exit_max_adverse REAL DEFAULT 0,
    FOREIGN KEY (impulse_id) REFERENCES impulses(id)
);

CREATE INDEX IF NOT EXISTS idx_impulses_symbol ON impulses(symbol);
CREATE INDEX IF NOT EXISTS idx_impulses_ts ON impulses(timestamp);
CREATE INDEX IF NOT EXISTS idx_impulses_sym_ts ON impulses(symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_outcomes_impulse ON post_impulse_outcomes(impulse_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_complete ON post_impulse_outcomes(tracking_complete);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trade_outcomes(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_bot ON trade_outcomes(bot_name);
CREATE INDEX IF NOT EXISTS idx_market_ts ON market_phases(timestamp);

CREATE TABLE IF NOT EXISTS pending_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    impulse_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    direction TEXT NOT NULL,
    price REAL DEFAULT 0,
    score REAL DEFAULT 0,
    confidence REAL DEFAULT 0,
    sl_pct REAL DEFAULT 1.0,
    tp_pct REAL DEFAULT 3.0,
    trail_pct REAL DEFAULT 0.15,
    hold_bars INTEGER DEFAULT 10,
    size_mult REAL DEFAULT 1.0,
    market_phase TEXT DEFAULT '',
    will_continue_prob REAL DEFAULT 0.5,
    stop_hunt_prob REAL DEFAULT 0.5,
    coin_quality REAL DEFAULT 50,
    reason TEXT DEFAULT '',
    created_at REAL NOT NULL,
    processed INTEGER DEFAULT 0,
    processed_at REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_signals_pending ON pending_signals(processed, created_at);
"""


class ImpulseDB:
    """SQLite database for impulse data. Thread-safe via WAL mode."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = str(db_path or config.DB_PATH)
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)
            logger.info(f"📦 IIE DB initialized: {self.db_path}")

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ─── Impulses ────────────────────────────────

    def insert_impulse(self, imp: Impulse) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO impulses
                   (symbol, exchange, timeframe, timestamp, direction,
                    vol_z, ret_z, combined_score, rsi_at_impulse, ema_deviation_pct,
                    price_at_impulse, candle_body_pct, wick_ratio_top, wick_ratio_bottom,
                    impulse_location, consolidation_days, atr_at_impulse, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (imp.symbol, imp.exchange, imp.timeframe, imp.timestamp, imp.direction,
                 imp.vol_z, imp.ret_z, imp.combined_score, imp.rsi_at_impulse,
                 imp.ema_deviation_pct, imp.price_at_impulse, imp.candle_body_pct,
                 imp.wick_ratio_top, imp.wick_ratio_bottom, imp.impulse_location,
                 imp.consolidation_days, imp.atr_at_impulse, imp.source))
            imp_id = cur.lastrowid
            # Auto-create outcome row for tracking
            conn.execute(
                "INSERT INTO post_impulse_outcomes (impulse_id, last_updated) VALUES (?, ?)",
                (imp_id, time.time()))
            return imp_id

    def get_impulse(self, impulse_id: int) -> Optional[Impulse]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM impulses WHERE id = ?", (impulse_id,)).fetchone()
            return self._row_to_impulse(row) if row else None

    def get_recent_impulses(self, symbol: str = None, hours: float = 24,
                            limit: int = 100) -> List[Impulse]:
        cutoff = time.time() - hours * 3600
        with self._conn() as conn:
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM impulses WHERE symbol = ? AND timestamp > ? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (symbol, cutoff, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM impulses WHERE timestamp > ? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (cutoff, limit)).fetchall()
            return [self._row_to_impulse(r) for r in rows]

    def has_recent_impulse(self, symbol: str, exchange: str,
                           timeframe: str, within_sec: int = 300) -> bool:
        """Check if impulse was already recorded recently (dedup)."""
        cutoff = time.time() - within_sec
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM impulses WHERE symbol=? AND exchange=? "
                "AND timeframe=? AND timestamp>? LIMIT 1",
                (symbol, exchange, timeframe, cutoff)).fetchone()
            return row is not None

    def _row_to_impulse(self, row) -> Impulse:
        return Impulse(**{k: row[k] for k in row.keys()})

    # ─── Post-Impulse Outcomes ───────────────────

    def get_pending_outcomes(self, limit: int = 200) -> List[dict]:
        """Get outcomes that still need tracking updates."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT o.*, i.symbol, i.exchange, i.direction, i.price_at_impulse,
                          i.timestamp as impulse_ts, i.timeframe
                   FROM post_impulse_outcomes o
                   JOIN impulses i ON o.impulse_id = i.id
                   WHERE o.tracking_complete = 0
                   ORDER BY i.timestamp ASC LIMIT ?""", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def update_outcome(self, impulse_id: int, updates: Dict[str, Any]):
        """Update specific fields of an outcome row."""
        if not updates:
            return
        updates["last_updated"] = time.time()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [impulse_id]
        with self._conn() as conn:
            conn.execute(
                f"UPDATE post_impulse_outcomes SET {set_clause} WHERE impulse_id = ?",
                values)

    def get_outcome(self, impulse_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM post_impulse_outcomes WHERE impulse_id = ?",
                (impulse_id,)).fetchone()
            return dict(row) if row else None

    # ─── Coin Profiles ───────────────────────────

    def upsert_coin_profile(self, profile: CoinProfile):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO coin_profiles
                   (symbol, impulse_count, avg_continuation_pct, avg_retracement_pct,
                    stop_hunt_frequency, avg_time_to_extremum, level_respect_score,
                    volatility_regime, listing_age_days, impulse_regularity,
                    best_tf, recommended_sl_mult, recommended_hold_bars,
                    momentum_persistence, impulse_quality_score, predictability_score,
                    last_updated)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(symbol) DO UPDATE SET
                    impulse_count=excluded.impulse_count,
                    avg_continuation_pct=excluded.avg_continuation_pct,
                    avg_retracement_pct=excluded.avg_retracement_pct,
                    stop_hunt_frequency=excluded.stop_hunt_frequency,
                    avg_time_to_extremum=excluded.avg_time_to_extremum,
                    level_respect_score=excluded.level_respect_score,
                    volatility_regime=excluded.volatility_regime,
                    listing_age_days=excluded.listing_age_days,
                    impulse_regularity=excluded.impulse_regularity,
                    best_tf=excluded.best_tf,
                    recommended_sl_mult=excluded.recommended_sl_mult,
                    recommended_hold_bars=excluded.recommended_hold_bars,
                    momentum_persistence=excluded.momentum_persistence,
                    impulse_quality_score=excluded.impulse_quality_score,
                    predictability_score=excluded.predictability_score,
                    last_updated=excluded.last_updated""",
                (profile.symbol, profile.impulse_count, profile.avg_continuation_pct,
                 profile.avg_retracement_pct, profile.stop_hunt_frequency,
                 profile.avg_time_to_extremum, profile.level_respect_score,
                 profile.volatility_regime, profile.listing_age_days,
                 profile.impulse_regularity, profile.best_tf, profile.recommended_sl_mult,
                 profile.recommended_hold_bars, profile.momentum_persistence,
                 profile.impulse_quality_score, profile.predictability_score,
                 profile.last_updated))

    def get_coin_profile(self, symbol: str) -> Optional[CoinProfile]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM coin_profiles WHERE symbol = ?", (symbol,)).fetchone()
            if not row:
                return None
            return CoinProfile(**{k: row[k] for k in row.keys()})

    def get_all_coin_profiles(self) -> List[CoinProfile]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM coin_profiles ORDER BY impulse_quality_score DESC"
            ).fetchall()
            return [CoinProfile(**{k: r[k] for k in r.keys()}) for r in rows]

    # ─── Market Phases ───────────────────────────

    def insert_market_phase(self, mp: MarketPhase) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO market_phases
                   (timestamp, btc_price, eth_price, btc_monthly_change_pct,
                    eth_monthly_change_pct, btc_weekly_change_pct,
                    btc_ema_fast, btc_ema_slow, btc_atr_daily,
                    phase, alt_correlation)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (mp.timestamp, mp.btc_price, mp.eth_price, mp.btc_monthly_change_pct,
                 mp.eth_monthly_change_pct, mp.btc_weekly_change_pct,
                 mp.btc_ema_fast, mp.btc_ema_slow, mp.btc_atr_daily,
                 mp.phase, mp.alt_correlation))
            return cur.lastrowid

    def get_current_phase(self) -> Optional[MarketPhase]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM market_phases ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            return MarketPhase(**{k: row[k] for k in row.keys()})

    # ─── Trade Outcomes ──────────────────────────

    def insert_trade(self, trade: TradeOutcome) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO trade_outcomes
                   (symbol, exchange, direction, entry_price, exit_price,
                    pnl_pct, exit_reason, strategy_name, bot_name,
                    impulse_id, market_phase_at_entry, entry_time, exit_time,
                    post_exit_max_favorable, post_exit_max_adverse)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (trade.symbol, trade.exchange, trade.direction, trade.entry_price,
                 trade.exit_price, trade.pnl_pct, trade.exit_reason, trade.strategy_name,
                 trade.bot_name, trade.impulse_id, trade.market_phase_at_entry,
                 trade.entry_time, trade.exit_time,
                 trade.post_exit_max_favorable, trade.post_exit_max_adverse))
            return cur.lastrowid

    def get_trades_by_symbol(self, symbol: str, limit: int = 50) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_outcomes WHERE symbol = ? "
                "ORDER BY exit_time DESC LIMIT ?", (symbol, limit)).fetchall()
            return [dict(r) for r in rows]

    # ─── Stats ───────────────────────────────────

    def stats(self) -> Dict[str, int]:
        with self._conn() as conn:
            counts = {}
            for table in ["impulses", "post_impulse_outcomes", "coin_profiles",
                          "market_phases", "trade_outcomes"]:
                row = conn.execute(f"SELECT COUNT(*) as c FROM {table}").fetchone()
                counts[table] = row["c"]
            return counts

    # ─── Queries for Scorer / Predictor ──────────

    def get_impulses_for_coin(self, symbol: str, limit: int = 500) -> List[dict]:
        """Get impulses + outcomes joined for scoring."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT i.*, o.max_favorable_pct, o.max_adverse_pct,
                          o.continuation_impulses, o.reversal_pct,
                          o.was_stop_hunt, o.new_extremum, o.tracking_complete,
                          o.price_after_1h, o.price_after_4h, o.price_after_24h
                   FROM impulses i
                   LEFT JOIN post_impulse_outcomes o ON i.id = o.impulse_id
                   WHERE i.symbol = ? AND o.tracking_complete = 1
                   ORDER BY i.timestamp DESC LIMIT ?""",
                (symbol, limit)).fetchall()
            return [dict(r) for r in rows]

    def get_all_completed_data(self, limit: int = 5000) -> List[dict]:
        """Get all impulses with completed outcomes for ML training."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT i.*, o.max_favorable_pct, o.max_adverse_pct,
                          o.continuation_impulses, o.reversal_pct,
                          o.was_stop_hunt, o.new_extremum,
                          o.price_after_1h, o.price_after_4h, o.price_after_24h,
                          c.impulse_quality_score, c.stop_hunt_frequency,
                          c.momentum_persistence, c.level_respect_score
                   FROM impulses i
                   JOIN post_impulse_outcomes o ON i.id = o.impulse_id
                   LEFT JOIN coin_profiles c ON i.symbol = c.symbol
                   WHERE o.tracking_complete = 1
                   ORDER BY i.timestamp DESC LIMIT ?""",
                (limit,)).fetchall()
            return [dict(r) for r in rows]

    def get_unique_symbols_with_impulses(self) -> List[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM impulses ORDER BY symbol"
            ).fetchall()
            return [r["symbol"] for r in rows]

    # ─── Pending Signals (IIE → Pump Hunter) ─────

    def insert_signal(self, impulse_id: int, symbol: str, exchange: str,
                      direction: str, price: float, score: float,
                      confidence: float, sl_pct: float, tp_pct: float,
                      trail_pct: float, hold_bars: int, size_mult: float,
                      market_phase: str, will_continue_prob: float,
                      stop_hunt_prob: float, coin_quality: float,
                      reason: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO pending_signals
                   (impulse_id, symbol, exchange, direction, price, score,
                    confidence, sl_pct, tp_pct, trail_pct, hold_bars,
                    size_mult, market_phase, will_continue_prob,
                    stop_hunt_prob, coin_quality, reason, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (impulse_id, symbol, exchange, direction, price, score,
                 confidence, sl_pct, tp_pct, trail_pct, hold_bars,
                 size_mult, market_phase, will_continue_prob,
                 stop_hunt_prob, coin_quality, reason, time.time()))
            return cur.lastrowid

    def get_pending_signals(self, limit: int = 20) -> List[dict]:
        """Get unprocessed signals for Pump Hunter."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_signals WHERE processed = 0 "
                "ORDER BY created_at ASC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def mark_signal_processed(self, signal_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE pending_signals SET processed = 1, processed_at = ? "
                "WHERE id = ?", (time.time(), signal_id))

    def has_recent_signal(self, symbol: str, within_sec: int = 300) -> bool:
        """Check if we already sent a signal for this symbol recently."""
        cutoff = time.time() - within_sec
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM pending_signals WHERE symbol = ? "
                "AND created_at > ? LIMIT 1", (symbol, cutoff)).fetchone()
            return row is not None
