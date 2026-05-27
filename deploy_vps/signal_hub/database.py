"""
OneProp Signal Hub — Database (SQLite + aiosqlite)
Stores Soldier signals with entry/exit targets, trade close data,
and auto-tracked P&L checkpoints at 15m/1h/4h.

v3: "Signal of the Day" system:
  - is_daily_signal flag marks which signals are shown on the public site
  - Only 1 daily signal at a time (first in 9-21 MSK window)
  - If daily signal closes at a loss → next signal becomes daily signal
  - Public TG posts / website only show is_daily_signal=1 records
"""
import aiosqlite
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from config import DB_PATH

logger = logging.getLogger("Database")

MSK = timezone(timedelta(hours=3))


async def init_db():
    """Create tables if they don't exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL DEFAULT 'soldier',
                signal_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                exchange TEXT DEFAULT 'bybit',
                direction TEXT NOT NULL,
                price_at_signal REAL NOT NULL,
                entry_target REAL DEFAULT 0,
                exit_target REAL DEFAULT 0,
                strength REAL DEFAULT 0,
                description TEXT DEFAULT '',
                metadata_json TEXT DEFAULT '{}',
                posted_public INTEGER DEFAULT 0,
                posted_private INTEGER DEFAULT 0,
                is_daily_signal INTEGER DEFAULT 0,
                -- Trade close data (filled when Soldier closes the trade)
                exit_price REAL DEFAULT 0,
                exit_reason TEXT DEFAULT '',
                exit_pnl_pct REAL DEFAULT 0,
                closed_at TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS signal_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL REFERENCES signals(id),
                check_interval TEXT NOT NULL,
                price_at_check REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                is_win INTEGER NOT NULL,
                checked_at TEXT NOT NULL,
                UNIQUE(signal_id, check_interval)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,
                total_signals INTEGER DEFAULT 0,
                wins_15m INTEGER DEFAULT 0,
                losses_15m INTEGER DEFAULT 0,
                win_rate_15m REAL DEFAULT 0,
                wins_1h INTEGER DEFAULT 0,
                losses_1h INTEGER DEFAULT 0,
                win_rate_1h REAL DEFAULT 0,
                wins_4h INTEGER DEFAULT 0,
                losses_4h INTEGER DEFAULT 0,
                win_rate_4h REAL DEFAULT 0,
                avg_pnl_15m REAL DEFAULT 0,
                avg_pnl_1h REAL DEFAULT 0,
                avg_pnl_4h REAL DEFAULT 0
            )
        """)
        # ─── Auto-migration: add is_daily_signal if missing ───
        # Must run BEFORE indexes to avoid error on existing DBs
        cursor = await db.execute("PRAGMA table_info(signals)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "is_daily_signal" not in columns:
            logger.info("🔄 Migrating: adding is_daily_signal column")
            await db.execute(
                "ALTER TABLE signals ADD COLUMN is_daily_signal INTEGER DEFAULT 0")
            # Mark all existing signals as daily (backwards compat)
            await db.execute("UPDATE signals SET is_daily_signal = 1")
            await db.commit()

        # ─── Auto-migration: add tg_public_msg_id (for editMessageText) ───
        if "tg_public_msg_id" not in columns:
            logger.info("🔄 Migrating: adding tg_public_msg_id column")
            await db.execute(
                "ALTER TABLE signals ADD COLUMN tg_public_msg_id INTEGER DEFAULT 0")
            await db.commit()

        # Indexes for fast queries
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at)")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_source ON signals(source)")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_results_signal ON signal_results(signal_id)")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_daily ON signals(is_daily_signal)")

        await db.commit()


# ─── Daily Signal Logic ──────────────────────────────────

async def needs_new_daily_signal() -> bool:
    """Check if we need a new daily signal.

    Returns True if:
      - No daily signal exists today (MSK date)
      - OR the current daily signal closed at a LOSS (pnl <= 0)
        AND there's no still-open daily signal

    Returns False if:
      - A daily signal is still OPEN today
      - OR a daily signal closed at a PROFIT today
    """
    today_msk = datetime.now(MSK).strftime("%Y-%m-%d")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Find all daily signals for today (MSK)
        # We store created_at in UTC, so we need to check the MSK date
        cursor = await db.execute("""
            SELECT id, closed_at, exit_pnl_pct
            FROM signals
            WHERE is_daily_signal = 1
              AND DATE(created_at, '+3 hours') = ?
            ORDER BY created_at DESC
        """, (today_msk,))
        rows = await cursor.fetchall()

        if not rows:
            return True  # No daily signal today at all

        # Check each daily signal (most recent first)
        for row in rows:
            closed_at = row["closed_at"]
            pnl = row["exit_pnl_pct"]

            if not closed_at or closed_at == "":
                # Signal is still OPEN → don't need a new one
                return False

            if pnl is not None and pnl > 0:
                # Found a signal that closed at profit → done for today
                return False

        # All daily signals today closed at a loss → need a new one
        return True


async def get_current_daily_signal_id() -> Optional[int]:
    """Get the ID of the current (most recent) active daily signal today.
    Returns None if no open daily signal exists."""
    today_msk = datetime.now(MSK).strftime("%Y-%m-%d")

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT id FROM signals
            WHERE is_daily_signal = 1
              AND DATE(created_at, '+3 hours') = ?
              AND (closed_at = '' OR closed_at IS NULL)
            ORDER BY created_at DESC LIMIT 1
        """, (today_msk,))
        row = await cursor.fetchone()
        return row[0] if row else None


async def mark_daily_signal(signal_id: int):
    """Mark a signal as the daily signal."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE signals SET is_daily_signal = 1 WHERE id = ?",
            (signal_id,))
        await db.commit()
    logger.info(f"⭐ Signal #{signal_id} marked as daily signal")


async def is_signal_daily(signal_id: int) -> bool:
    """Check if a specific signal is a daily signal."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT is_daily_signal FROM signals WHERE id = ?",
            (signal_id,))
        row = await cursor.fetchone()
        return bool(row and row[0])


# ─── Core CRUD ───────────────────────────────────────────

async def add_signal(
    source: str,
    signal_type: str,
    symbol: str,
    direction: str,
    price_at_signal: float,
    exchange: str = "bybit",
    strength: float = 0.0,
    description: str = "",
    metadata: dict = None,
    entry_target: float = 0.0,
    exit_target: float = 0.0,
) -> int:
    """Insert a new signal. Returns signal ID."""
    now = datetime.now(timezone.utc).isoformat()
    meta_json = json.dumps(metadata or {}, default=str)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO signals
               (source, signal_type, symbol, exchange, direction,
                price_at_signal, entry_target, exit_target,
                strength, description, metadata_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (source, signal_type, symbol, exchange, direction,
             price_at_signal, entry_target, exit_target,
             strength, description, meta_json, now)
        )
        await db.commit()
        return cursor.lastrowid


async def close_signal(
    symbol: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    exit_reason: str,
    pnl_pct: float,
) -> Optional[int]:
    """Mark a signal as closed. Finds the matching open signal by symbol+direction+entry.
    Returns signal_id or None if not found."""
    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        # Find the most recent unclosed signal for this symbol/direction
        cursor = await db.execute(
            """SELECT id FROM signals
               WHERE symbol = ? AND direction = ? AND closed_at = ''
               ORDER BY created_at DESC LIMIT 1""",
            (symbol, direction)
        )
        row = await cursor.fetchone()
        if not row:
            return None

        signal_id = row[0]
        await db.execute(
            """UPDATE signals
               SET exit_price = ?, exit_reason = ?, exit_pnl_pct = ?, closed_at = ?
               WHERE id = ?""",
            (exit_price, exit_reason, pnl_pct, now, signal_id)
        )
        await db.commit()
        return signal_id


async def mark_posted(signal_id: int, channel: str):
    """Mark signal as posted to public/private channel."""
    col = "posted_public" if channel == "public" else "posted_private"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE signals SET {col}=1 WHERE id=?", (signal_id,))
        await db.commit()


async def add_result(signal_id: int, interval: str,
                     price_at_check: float, pnl_pct: float):
    """Record result for a signal at given interval."""
    now = datetime.now(timezone.utc).isoformat()
    is_win = 1 if pnl_pct > 0 else 0

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO signal_results
               (signal_id, check_interval, price_at_check, pnl_pct, is_win, checked_at)
               VALUES (?,?,?,?,?,?)""",
            (signal_id, interval, price_at_check, pnl_pct, is_win, now)
        )
        await db.commit()


async def get_pending_checks(interval: str, interval_sec: int,
                             daily_only: bool = True) -> list:
    """Get signals that need result checking for the given interval.
    If daily_only=True, only returns daily signals."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        daily_filter = "AND s.is_daily_signal = 1" if daily_only else ""
        cursor = await db.execute(f"""
            SELECT s.* FROM signals s
            WHERE strftime('%Y-%m-%d %H:%M:%S', s.created_at)
                  <= strftime('%Y-%m-%d %H:%M:%S', 'now', ?)
              AND s.id NOT IN (
                  SELECT signal_id FROM signal_results WHERE check_interval = ?
              )
              {daily_filter}
            ORDER BY s.id
            LIMIT 50
        """, (f"-{interval_sec} seconds", interval))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_signal_by_id(signal_id: int) -> Optional[dict]:
    """Get a single signal with all result data."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT s.*,
                   r15.pnl_pct as pnl_15m, r15.is_win as win_15m, r15.price_at_check as price_15m,
                   r1.pnl_pct as pnl_1h, r1.is_win as win_1h, r1.price_at_check as price_1h,
                   r4.pnl_pct as pnl_4h, r4.is_win as win_4h, r4.price_at_check as price_4h
            FROM signals s
            LEFT JOIN signal_results r15 ON s.id = r15.signal_id AND r15.check_interval = '15m'
            LEFT JOIN signal_results r1 ON s.id = r1.signal_id AND r1.check_interval = '1h'
            LEFT JOIN signal_results r4 ON s.id = r4.signal_id AND r4.check_interval = '4h'
            WHERE s.id = ?
        """, (signal_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_todays_signal() -> Optional[dict]:
    """Get today's daily signal (most recent daily signal created today UTC)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        today_msk = datetime.now(MSK).strftime("%Y-%m-%d")
        cursor = await db.execute("""
            SELECT s.*,
                   r15.pnl_pct as pnl_15m, r15.is_win as win_15m,
                   r1.pnl_pct as pnl_1h, r1.is_win as win_1h,
                   r4.pnl_pct as pnl_4h, r4.is_win as win_4h
            FROM signals s
            LEFT JOIN signal_results r15 ON s.id = r15.signal_id AND r15.check_interval = '15m'
            LEFT JOIN signal_results r1 ON s.id = r1.signal_id AND r1.check_interval = '1h'
            LEFT JOIN signal_results r4 ON s.id = r4.signal_id AND r4.check_interval = '4h'
            WHERE s.is_daily_signal = 1
              AND DATE(s.created_at, '+3 hours') = ?
            ORDER BY s.created_at DESC LIMIT 1
        """, (today_msk,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_signals(
    source: str = None,
    signal_type: str = None,
    limit: int = 50,
    offset: int = 0,
    daily_only: bool = True,
) -> list:
    """Get signals with optional filters.
    daily_only=True → only return is_daily_signal=1 for public display."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        conditions = []
        params = []

        if daily_only:
            conditions.append("s.is_daily_signal = 1")
        if source:
            conditions.append("s.source = ?")
            params.append(source)
        if signal_type:
            conditions.append("s.signal_type = ?")
            params.append(signal_type)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.extend([limit, offset])

        cursor = await db.execute(f"""
            SELECT s.*,
                   r15.pnl_pct as pnl_15m, r15.is_win as win_15m,
                   r1.pnl_pct as pnl_1h, r1.is_win as win_1h,
                   r4.pnl_pct as pnl_4h, r4.is_win as win_4h
            FROM signals s
            LEFT JOIN signal_results r15 ON s.id = r15.signal_id AND r15.check_interval = '15m'
            LEFT JOIN signal_results r1 ON s.id = r1.signal_id AND r1.check_interval = '1h'
            LEFT JOIN signal_results r4 ON s.id = r4.signal_id AND r4.check_interval = '4h'
            {where}
            ORDER BY s.created_at DESC
            LIMIT ? OFFSET ?
        """, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_stats(days: int = 30) -> dict:
    """Get aggregated statistics for the dashboard.
    Only counts is_daily_signal=1 signals for public stats."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Total daily signals
        total = (await (await db.execute(
            "SELECT COUNT(*) FROM signals WHERE is_daily_signal = 1 AND created_at >= datetime('now', ?)",
            (f"-{days} days",)
        )).fetchone())[0]

        stats = {"total_signals": total, "days": days}

        # Stats per interval (15m, 1h, 4h) — only for daily signals
        for interval in ["15m", "1h", "4h"]:
            row = await (await db.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN is_win = 1 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN is_win = 0 THEN 1 ELSE 0 END) as losses,
                    ROUND(AVG(pnl_pct), 2) as avg_pnl,
                    ROUND(MAX(pnl_pct), 2) as best_pnl,
                    ROUND(MIN(pnl_pct), 2) as worst_pnl
                FROM signal_results sr
                JOIN signals s ON sr.signal_id = s.id
                WHERE sr.check_interval = ?
                  AND s.is_daily_signal = 1
                  AND s.created_at >= datetime('now', ?)
            """, (interval, f"-{days} days"))).fetchone()

            total_checked = row["total"] or 0
            wins = row["wins"] or 0
            win_rate = round(wins / total_checked * 100, 1) if total_checked > 0 else 0

            stats[interval] = {
                "total": total_checked,
                "wins": wins,
                "losses": row["losses"] or 0,
                "win_rate": win_rate,
                "avg_pnl": row["avg_pnl"] or 0,
                "best_pnl": row["best_pnl"] or 0,
                "worst_pnl": row["worst_pnl"] or 0,
            }

        # Trade close stats (actual soldier trades — daily signals only)
        close_row = await (await db.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN exit_pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN exit_pnl_pct <= 0 THEN 1 ELSE 0 END) as losses,
                ROUND(AVG(exit_pnl_pct), 2) as avg_pnl,
                ROUND(SUM(exit_pnl_pct), 2) as total_pnl
            FROM signals
            WHERE closed_at != ''
              AND is_daily_signal = 1
              AND created_at >= datetime('now', ?)
        """, (f"-{days} days",))).fetchone()

        close_total = close_row["total"] or 0
        close_wins = close_row["wins"] or 0
        stats["trades"] = {
            "total": close_total,
            "wins": close_wins,
            "losses": close_row["losses"] or 0,
            "win_rate": round(close_wins / close_total * 100, 1) if close_total > 0 else 0,
            "avg_pnl": close_row["avg_pnl"] or 0,
            "total_pnl": close_row["total_pnl"] or 0,
        }

        # Stats by signal_type (daily signals only)
        cursor = await db.execute("""
            SELECT signal_type, COUNT(*) as cnt,
                   ROUND(AVG(CASE WHEN sr.check_interval = '4h' THEN sr.pnl_pct END), 2) as avg_pnl_4h
            FROM signals s
            LEFT JOIN signal_results sr ON s.id = sr.signal_id AND sr.check_interval = '4h'
            WHERE s.is_daily_signal = 1
              AND s.created_at >= datetime('now', ?)
            GROUP BY signal_type
            ORDER BY cnt DESC
        """, (f"-{days} days",))
        stats["by_type"] = [
            {"type": row["signal_type"], "count": row["cnt"], "avg_pnl_4h": row["avg_pnl_4h"] or 0}
            for row in await cursor.fetchall()
        ]

        # P&L curve (daily cumulative from actual trades — daily signals only)
        cursor = await db.execute("""
            SELECT DATE(s.created_at) as day,
                   ROUND(SUM(CASE WHEN s.exit_pnl_pct != 0 THEN s.exit_pnl_pct ELSE 0 END), 2) as daily_pnl
            FROM signals s
            WHERE s.is_daily_signal = 1
              AND s.created_at >= datetime('now', ?)
            GROUP BY day
            ORDER BY day
        """, (f"-{days} days",))
        pnl_curve = []
        cumulative = 0.0
        for row in await cursor.fetchall():
            cumulative += row["daily_pnl"] or 0
            pnl_curve.append({"date": row["day"], "daily_pnl": row["daily_pnl"], "cumulative": round(cumulative, 2)})
        stats["pnl_curve"] = pnl_curve

        return stats


# ─── Updatable Post Helpers ──────────────────────────────

async def save_tg_msg_id(signal_id: int, msg_id: int):
    """Store the TG message_id of the public post for later editing."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE signals SET tg_public_msg_id = ? WHERE id = ?",
            (msg_id, signal_id))
        await db.commit()


async def get_tg_msg_id(signal_id: int) -> int:
    """Get the stored TG message_id for a signal."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT tg_public_msg_id FROM signals WHERE id = ?",
            (signal_id,))
        row = await cursor.fetchone()
        return row[0] if row and row[0] else 0


async def get_streak(limit: int = 10) -> list:
    """Get last N closed daily signals for streak display.
    Returns list of dicts: [{pnl: float, win: bool}, ...] (oldest first)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT exit_pnl_pct FROM signals
            WHERE is_daily_signal = 1
              AND closed_at != '' AND closed_at IS NOT NULL
            ORDER BY closed_at DESC
            LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        # Return oldest-first for visual display
        return [
            {"pnl": row["exit_pnl_pct"], "win": row["exit_pnl_pct"] > 0}
            for row in reversed(rows)
        ]


async def get_model_rating(days: int = 30) -> dict:
    """Get model performance rating for display in posts.
    Returns {wr: float, total: int, avg_pnl: float, total_pnl: float}."""
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN exit_pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(AVG(exit_pnl_pct), 2) as avg_pnl,
                ROUND(SUM(exit_pnl_pct), 2) as total_pnl
            FROM signals
            WHERE closed_at != '' AND closed_at IS NOT NULL
              AND is_daily_signal = 1
              AND created_at >= datetime('now', ?)
        """, (f"-{days} days",))).fetchone()

        total = row[0] or 0
        wins = row[1] or 0
        return {
            "wr": round(wins / total * 100, 1) if total > 0 else 0,
            "total": total,
            "avg_pnl": row[2] or 0,
            "total_pnl": row[3] or 0,
        }
