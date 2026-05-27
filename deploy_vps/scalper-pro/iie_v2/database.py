"""
Scalper Pro — IIE v2 Database

Extended schema on top of IIE v1. Stores:
  - Trades with full price verification (3 exchanges)
  - 4 checkpoints per trade (15m/1h/4h after open + close)
  - Hypotheses per (symbol, direction, score_range, situation)
  - Scaling events (add/cut during trade)
  - Daily performance metrics
"""
import sqlite3
import json
import time
import logging
from pathlib import Path
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any

import config

logger = logging.getLogger("scalper.db")


@dataclass
class ProTrade:
    """Extended trade record with verification and checkpoints."""
    id: int = 0
    symbol: str = ""
    direction: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    entry_time: float = 0.0
    exit_time: float = 0.0
    pnl_pct: float = 0.0
    pnl_pct_after_commission: float = 0.0
    exit_reason: str = ""
    position_size_usdt: float = 0.0

    # IIE context at entry
    iie_score: float = 0.0
    iie_confidence: float = 0.0
    market_phase: str = ""
    vol_z: float = 0.0
    ret_z: float = 0.0
    rsi: float = 50.0
    impulse_location: str = ""
    combined_score: float = 0.0

    # Price verification
    entry_bybit: float = 0.0
    entry_binance: float = 0.0
    entry_okx: float = 0.0
    entry_divergence: float = 0.0
    entry_verified: bool = False
    exit_bybit: float = 0.0
    exit_binance: float = 0.0
    exit_okx: float = 0.0
    exit_divergence: float = 0.0
    exit_verified: bool = False

    # Dynamic stop phases reached
    stop_phase: str = ""  # "initial" / "confirm" / "trail" / "protect"
    peak_price: float = 0.0
    max_favorable_pct: float = 0.0
    max_adverse_pct: float = 0.0

    # Scaling
    scale_events: str = ""  # JSON: [{"time":..., "type":"in"/"out", "mult":...}]
    final_position_mult: float = 1.0

    # Hypothesis link
    hypothesis_id: str = ""

    # Status
    status: str = "open"  # "open" / "closed" / "analyzed"


@dataclass
class TradeCheckpoint:
    """Price checkpoint at a specific time after trade event."""
    id: int = 0
    trade_id: int = 0
    phase: str = ""          # "after_open" / "after_close"
    label: str = ""          # "15m" / "1h" / "4h"
    target_time: float = 0.0
    actual_time: float = 0.0
    price: float = 0.0
    pnl_vs_entry: float = 0.0
    pnl_vs_exit: float = 0.0
    impulse_developing: bool = False
    vol_z_at_check: float = 0.0
    completed: bool = False

    # Price verification at checkpoint
    bybit_price: float = 0.0
    binance_price: float = 0.0
    okx_price: float = 0.0
    verified: bool = False


@dataclass
class Hypothesis:
    """Trading hypothesis for a specific situation."""
    id: str = ""             # "BTCUSDT_long_high_trending"
    symbol: str = ""
    direction: str = ""
    score_bin: str = ""      # "low" / "medium" / "high" / "extreme"
    market_phase: str = ""
    impulse_location: str = ""

    # Accumulated stats
    sample_count: int = 0
    win_count: int = 0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    avg_max_favorable: float = 0.0
    avg_max_adverse: float = 0.0
    total_pnl: float = 0.0

    # Optimized parameters (learned)
    optimal_sl_pct: float = 1.5
    optimal_tp_pct: float = 3.0
    optimal_hold_bars: int = 10
    optimal_trail_pct: float = 0.15

    # Checkpoint patterns
    pct_profitable_15m: float = 0.0
    pct_profitable_1h: float = 0.0
    pct_profitable_4h: float = 0.0
    avg_close_miss_pct: float = 0.0  # How much left on table after exit

    # Scaling recommendations
    should_scale_in: bool = False
    scale_in_trigger: float = 0.3
    should_cut_early: bool = False

    # Metadata
    created_at: float = 0.0
    updated_at: float = 0.0
    is_mature: bool = False  # sample_count >= HYPOTHESIS_MIN_SAMPLES


class ScalperProDB:
    """SQLite database for Scalper Pro trades, checkpoints, and hypotheses."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(config.DB_PATH)
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS pro_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL DEFAULT 0,
                    entry_time REAL NOT NULL,
                    exit_time REAL DEFAULT 0,
                    pnl_pct REAL DEFAULT 0,
                    pnl_pct_after_commission REAL DEFAULT 0,
                    exit_reason TEXT DEFAULT '',
                    position_size_usdt REAL DEFAULT 0,

                    iie_score REAL DEFAULT 0,
                    iie_confidence REAL DEFAULT 0,
                    market_phase TEXT DEFAULT '',
                    vol_z REAL DEFAULT 0,
                    ret_z REAL DEFAULT 0,
                    rsi REAL DEFAULT 50,
                    impulse_location TEXT DEFAULT '',
                    combined_score REAL DEFAULT 0,

                    entry_bybit REAL DEFAULT 0,
                    entry_binance REAL DEFAULT 0,
                    entry_okx REAL DEFAULT 0,
                    entry_divergence REAL DEFAULT 0,
                    entry_verified INTEGER DEFAULT 0,
                    exit_bybit REAL DEFAULT 0,
                    exit_binance REAL DEFAULT 0,
                    exit_okx REAL DEFAULT 0,
                    exit_divergence REAL DEFAULT 0,
                    exit_verified INTEGER DEFAULT 0,

                    stop_phase TEXT DEFAULT 'initial',
                    peak_price REAL DEFAULT 0,
                    max_favorable_pct REAL DEFAULT 0,
                    max_adverse_pct REAL DEFAULT 0,

                    scale_events TEXT DEFAULT '[]',
                    final_position_mult REAL DEFAULT 1.0,

                    hypothesis_id TEXT DEFAULT '',
                    status TEXT DEFAULT 'open'
                );

                CREATE TABLE IF NOT EXISTS trade_checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    label TEXT NOT NULL,
                    target_time REAL NOT NULL,
                    actual_time REAL DEFAULT 0,
                    price REAL DEFAULT 0,
                    pnl_vs_entry REAL DEFAULT 0,
                    pnl_vs_exit REAL DEFAULT 0,
                    impulse_developing INTEGER DEFAULT 0,
                    vol_z_at_check REAL DEFAULT 0,
                    completed INTEGER DEFAULT 0,
                    bybit_price REAL DEFAULT 0,
                    binance_price REAL DEFAULT 0,
                    okx_price REAL DEFAULT 0,
                    verified INTEGER DEFAULT 0,
                    FOREIGN KEY (trade_id) REFERENCES pro_trades(id)
                );

                CREATE TABLE IF NOT EXISTS hypotheses (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    score_bin TEXT DEFAULT '',
                    market_phase TEXT DEFAULT '',
                    impulse_location TEXT DEFAULT '',

                    sample_count INTEGER DEFAULT 0,
                    win_count INTEGER DEFAULT 0,
                    win_rate REAL DEFAULT 0,
                    avg_pnl REAL DEFAULT 0,
                    avg_max_favorable REAL DEFAULT 0,
                    avg_max_adverse REAL DEFAULT 0,
                    total_pnl REAL DEFAULT 0,

                    optimal_sl_pct REAL DEFAULT 1.5,
                    optimal_tp_pct REAL DEFAULT 3.0,
                    optimal_hold_bars INTEGER DEFAULT 10,
                    optimal_trail_pct REAL DEFAULT 0.15,

                    pct_profitable_15m REAL DEFAULT 0,
                    pct_profitable_1h REAL DEFAULT 0,
                    pct_profitable_4h REAL DEFAULT 0,
                    avg_close_miss_pct REAL DEFAULT 0,

                    should_scale_in INTEGER DEFAULT 0,
                    scale_in_trigger REAL DEFAULT 0.3,
                    should_cut_early INTEGER DEFAULT 0,

                    created_at REAL DEFAULT 0,
                    updated_at REAL DEFAULT 0,
                    is_mature INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS daily_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    trades_count INTEGER DEFAULT 0,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    win_rate REAL DEFAULT 0,
                    total_pnl REAL DEFAULT 0,
                    avg_pnl REAL DEFAULT 0,
                    best_trade_pnl REAL DEFAULT 0,
                    worst_trade_pnl REAL DEFAULT 0,
                    hypotheses_total INTEGER DEFAULT 0,
                    hypotheses_mature INTEGER DEFAULT 0,
                    hypothesis_accuracy REAL DEFAULT 0,
                    balance REAL DEFAULT 0,
                    timestamp REAL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_trades_status ON pro_trades(status);
                CREATE INDEX IF NOT EXISTS idx_trades_symbol ON pro_trades(symbol);
                CREATE INDEX IF NOT EXISTS idx_checkpoints_trade ON trade_checkpoints(trade_id);
                CREATE INDEX IF NOT EXISTS idx_checkpoints_pending
                    ON trade_checkpoints(completed, target_time);
            """)
        logger.info(f"📦 Scalper Pro DB initialized: {self.db_path}")

    # ── Trades ────────────────────────────────────────────────────────────────

    def insert_trade(self, trade: ProTrade) -> int:
        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO pro_trades (
                    symbol, direction, entry_price, entry_time,
                    position_size_usdt, iie_score, iie_confidence,
                    market_phase, vol_z, ret_z, rsi, impulse_location,
                    combined_score, entry_bybit, entry_binance, entry_okx,
                    entry_divergence, entry_verified, hypothesis_id, status
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                trade.symbol, trade.direction, trade.entry_price, trade.entry_time,
                trade.position_size_usdt, trade.iie_score, trade.iie_confidence,
                trade.market_phase, trade.vol_z, trade.ret_z, trade.rsi,
                trade.impulse_location, trade.combined_score,
                trade.entry_bybit, trade.entry_binance, trade.entry_okx,
                trade.entry_divergence, int(trade.entry_verified),
                trade.hypothesis_id, "open",
            ))
            return cur.lastrowid

    def close_trade(self, trade_id: int, exit_price: float, exit_time: float,
                    pnl_pct: float, pnl_after_comm: float, exit_reason: str,
                    peak_price: float, max_fav: float, max_adv: float,
                    stop_phase: str, scale_events: str, final_mult: float,
                    exit_bybit: float = 0, exit_binance: float = 0,
                    exit_okx: float = 0, exit_div: float = 0,
                    exit_verified: bool = False):
        with self._conn() as conn:
            conn.execute("""
                UPDATE pro_trades SET
                    exit_price=?, exit_time=?, pnl_pct=?,
                    pnl_pct_after_commission=?, exit_reason=?,
                    peak_price=?, max_favorable_pct=?, max_adverse_pct=?,
                    stop_phase=?, scale_events=?, final_position_mult=?,
                    exit_bybit=?, exit_binance=?, exit_okx=?,
                    exit_divergence=?, exit_verified=?,
                    status='closed'
                WHERE id=?
            """, (
                exit_price, exit_time, pnl_pct, pnl_after_comm, exit_reason,
                peak_price, max_fav, max_adv, stop_phase, scale_events,
                final_mult, exit_bybit, exit_binance, exit_okx, exit_div,
                int(exit_verified), trade_id,
            ))

    def get_open_trades(self) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM pro_trades WHERE status='open'"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_closed_trades(self, limit: int = 50) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM pro_trades WHERE status IN ('closed','analyzed') "
                "ORDER BY exit_time DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_symbol_recent_trades(self, symbol: str, limit: int = 3) -> List[dict]:
        """Get most recent closed trades for a symbol (for consecutive loss check)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM pro_trades WHERE symbol=? "
                "AND status IN ('closed','analyzed') "
                "ORDER BY exit_time DESC LIMIT ?", (symbol, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_trades_ready_for_hypothesis(self, min_checkpoints: int = 4) -> List[dict]:
        """Get closed trades with enough checkpoints for hypothesis analysis.
        
        Unlike the strict 'all checkpoints completed' requirement, this allows
        hypothesis creation from partial data (e.g. 15m + 1h done but not 4h).
        """
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT t.*, 
                       COUNT(c.id) as total_checkpoints,
                       SUM(c.completed) as completed_checkpoints
                FROM pro_trades t
                LEFT JOIN trade_checkpoints c ON t.id = c.trade_id
                WHERE t.status = 'closed'
                GROUP BY t.id
                HAVING completed_checkpoints >= ?
                ORDER BY t.exit_time DESC
            """, (min_checkpoints,)).fetchall()
            return [dict(r) for r in rows]


    def mark_trade_analyzed(self, trade_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE pro_trades SET status='analyzed' WHERE id=?",
                (trade_id,),
            )

    # ── Checkpoints ───────────────────────────────────────────────────────────

    def insert_checkpoints(self, trade_id: int, phase: str,
                           entry_time_or_exit_time: float):
        """Create pending checkpoints for a trade event (open or close)."""
        with self._conn() as conn:
            for label, offset_sec in config.CHECKPOINT_INTERVALS.items():
                target = entry_time_or_exit_time + offset_sec
                conn.execute("""
                    INSERT INTO trade_checkpoints
                        (trade_id, phase, label, target_time)
                    VALUES (?, ?, ?, ?)
                """, (trade_id, phase, label, target))

    def get_pending_checkpoints(self) -> List[dict]:
        """Get all incomplete checkpoints whose target_time has passed."""
        now = time.time()
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT c.*, t.symbol, t.direction, t.entry_price, t.exit_price
                FROM trade_checkpoints c
                JOIN pro_trades t ON c.trade_id = t.id
                WHERE c.completed = 0 AND c.target_time <= ?
                ORDER BY c.target_time
            """, (now,)).fetchall()
            return [dict(r) for r in rows]

    def complete_checkpoint(self, cp_id: int, price: float, pnl_vs_entry: float,
                           pnl_vs_exit: float, impulse_developing: bool,
                           vol_z: float, bybit: float, binance: float,
                           okx: float, verified: bool):
        with self._conn() as conn:
            conn.execute("""
                UPDATE trade_checkpoints SET
                    actual_time=?, price=?, pnl_vs_entry=?, pnl_vs_exit=?,
                    impulse_developing=?, vol_z_at_check=?,
                    bybit_price=?, binance_price=?, okx_price=?,
                    verified=?, completed=1
                WHERE id=?
            """, (
                time.time(), price, pnl_vs_entry, pnl_vs_exit,
                int(impulse_developing), vol_z,
                bybit, binance, okx, int(verified), cp_id,
            ))

    def get_trade_checkpoints(self, trade_id: int) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_checkpoints WHERE trade_id=? ORDER BY target_time",
                (trade_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Hypotheses ────────────────────────────────────────────────────────────

    def get_hypothesis(self, hyp_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM hypotheses WHERE id=?", (hyp_id,)
            ).fetchone()
            return dict(row) if row else None

    def upsert_hypothesis(self, hyp: Hypothesis):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO hypotheses (
                    id, symbol, direction, score_bin, market_phase,
                    impulse_location, sample_count, win_count, win_rate,
                    avg_pnl, avg_max_favorable, avg_max_adverse, total_pnl,
                    optimal_sl_pct, optimal_tp_pct, optimal_hold_bars,
                    optimal_trail_pct, pct_profitable_15m, pct_profitable_1h,
                    pct_profitable_4h, avg_close_miss_pct,
                    should_scale_in, scale_in_trigger, should_cut_early,
                    created_at, updated_at, is_mature
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    sample_count=excluded.sample_count,
                    win_count=excluded.win_count,
                    win_rate=excluded.win_rate,
                    avg_pnl=excluded.avg_pnl,
                    avg_max_favorable=excluded.avg_max_favorable,
                    avg_max_adverse=excluded.avg_max_adverse,
                    total_pnl=excluded.total_pnl,
                    optimal_sl_pct=excluded.optimal_sl_pct,
                    optimal_tp_pct=excluded.optimal_tp_pct,
                    optimal_hold_bars=excluded.optimal_hold_bars,
                    optimal_trail_pct=excluded.optimal_trail_pct,
                    pct_profitable_15m=excluded.pct_profitable_15m,
                    pct_profitable_1h=excluded.pct_profitable_1h,
                    pct_profitable_4h=excluded.pct_profitable_4h,
                    avg_close_miss_pct=excluded.avg_close_miss_pct,
                    should_scale_in=excluded.should_scale_in,
                    scale_in_trigger=excluded.scale_in_trigger,
                    should_cut_early=excluded.should_cut_early,
                    updated_at=excluded.updated_at,
                    is_mature=excluded.is_mature
            """, (
                hyp.id, hyp.symbol, hyp.direction, hyp.score_bin,
                hyp.market_phase, hyp.impulse_location,
                hyp.sample_count, hyp.win_count, hyp.win_rate,
                hyp.avg_pnl, hyp.avg_max_favorable, hyp.avg_max_adverse,
                hyp.total_pnl, hyp.optimal_sl_pct, hyp.optimal_tp_pct,
                hyp.optimal_hold_bars, hyp.optimal_trail_pct,
                hyp.pct_profitable_15m, hyp.pct_profitable_1h,
                hyp.pct_profitable_4h, hyp.avg_close_miss_pct,
                int(hyp.should_scale_in), hyp.scale_in_trigger,
                int(hyp.should_cut_early),
                hyp.created_at, hyp.updated_at, int(hyp.is_mature),
            ))

    def get_all_hypotheses(self, mature_only: bool = False) -> List[dict]:
        with self._conn() as conn:
            q = "SELECT * FROM hypotheses"
            if mature_only:
                q += " WHERE is_mature=1"
            q += " ORDER BY sample_count DESC"
            rows = conn.execute(q).fetchall()
            return [dict(r) for r in rows]

    def get_matching_hypothesis(self, symbol: str, direction: str,
                                 score_bin: str, market_phase: str,
                                 impulse_location: str) -> Optional[dict]:
        """Find best matching hypothesis for a new signal."""
        with self._conn() as conn:
            # Try exact match first
            row = conn.execute("""
                SELECT * FROM hypotheses
                WHERE symbol=? AND direction=? AND score_bin=?
                  AND market_phase=? AND impulse_location=?
                  AND is_mature=1
            """, (symbol, direction, score_bin, market_phase,
                  impulse_location)).fetchone()
            if row:
                return dict(row)

            # Fallback: same symbol + direction + score_bin (any phase/location)
            row = conn.execute("""
                SELECT * FROM hypotheses
                WHERE symbol=? AND direction=? AND score_bin=?
                  AND is_mature=1
                ORDER BY sample_count DESC LIMIT 1
            """, (symbol, direction, score_bin)).fetchone()
            if row:
                return dict(row)

            # Fallback: any symbol with same score_bin + direction
            row = conn.execute("""
                SELECT * FROM hypotheses
                WHERE direction=? AND score_bin=?
                  AND is_mature=1
                ORDER BY sample_count DESC LIMIT 1
            """, (direction, score_bin)).fetchone()
            return dict(row) if row else None

    # ── Daily Metrics ─────────────────────────────────────────────────────────

    def save_daily_metrics(self, date_str: str, metrics: dict):
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO daily_metrics (
                    date, trades_count, wins, losses, win_rate,
                    total_pnl, avg_pnl, best_trade_pnl, worst_trade_pnl,
                    hypotheses_total, hypotheses_mature, hypothesis_accuracy,
                    balance, timestamp
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                date_str, metrics.get("trades_count", 0),
                metrics.get("wins", 0), metrics.get("losses", 0),
                metrics.get("win_rate", 0), metrics.get("total_pnl", 0),
                metrics.get("avg_pnl", 0), metrics.get("best_trade_pnl", 0),
                metrics.get("worst_trade_pnl", 0),
                metrics.get("hypotheses_total", 0),
                metrics.get("hypotheses_mature", 0),
                metrics.get("hypothesis_accuracy", 0),
                metrics.get("balance", 0), time.time(),
            ))

    def get_daily_metrics(self, days: int = 7) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM daily_metrics ORDER BY date DESC LIMIT ?",
                (days,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Stats ─────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        with self._conn() as conn:
            trades = conn.execute("SELECT COUNT(*) FROM pro_trades").fetchone()[0]
            open_t = conn.execute(
                "SELECT COUNT(*) FROM pro_trades WHERE status='open'"
            ).fetchone()[0]
            closed = conn.execute(
                "SELECT COUNT(*) FROM pro_trades WHERE status IN ('closed','analyzed')"
            ).fetchone()[0]
            hyps = conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0]
            mature = conn.execute(
                "SELECT COUNT(*) FROM hypotheses WHERE is_mature=1"
            ).fetchone()[0]
            pending_cp = conn.execute(
                "SELECT COUNT(*) FROM trade_checkpoints WHERE completed=0"
            ).fetchone()[0]
            return {
                "trades_total": trades,
                "trades_open": open_t,
                "trades_closed": closed,
                "hypotheses_total": hyps,
                "hypotheses_mature": mature,
                "pending_checkpoints": pending_cp,
            }
