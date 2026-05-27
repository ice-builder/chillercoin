"""
ZeroChainAI — SQLite storage for news items and tweet drafts.
"""
from __future__ import annotations

import aiosqlite
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List
from config import DB_PATH


async def init_db():
    """Create tables if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                summary TEXT DEFAULT '',
                source TEXT NOT NULL,
                category TEXT DEFAULT '⚪ General',
                keywords_matched TEXT DEFAULT '[]',
                published_at TEXT,
                fetched_at TEXT NOT NULL,
                alerted INTEGER DEFAULT 0,
                digested INTEGER DEFAULT 0,
                tweet_draft_id INTEGER DEFAULT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tweet_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                news_item_id INTEGER,
                tweet_text TEXT NOT NULL,
                template_type TEXT DEFAULT 'insight',
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                approved_at TEXT,
                FOREIGN KEY (news_item_id) REFERENCES news_items(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS digest_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                digest_type TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                items_count INTEGER DEFAULT 0,
                message_text TEXT
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_news_url ON news_items(url)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_news_fetched ON news_items(fetched_at)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_news_category ON news_items(category)
        """)
        await db.commit()


async def insert_news(url: str, title: str, summary: str,
                      source: str, category: str,
                      keywords_matched: list,
                      published_at: str | None = None) -> int | None:
    """Insert a news item. Returns id or None if duplicate."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        try:
            cursor = await db.execute(
                """INSERT INTO news_items
                   (url, title, summary, source, category,
                    keywords_matched, published_at, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (url, title, summary, source, category,
                 json.dumps(keywords_matched),
                 published_at,
                 datetime.now(timezone.utc).isoformat())
            )
            await db.commit()
            return cursor.lastrowid
        except aiosqlite.IntegrityError:
            return None  # duplicate URL


async def get_unalerted_critical() -> list[dict]:
    """Get critical news not yet sent as alerts."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM news_items
               WHERE category = '🔴 CRITICAL' AND alerted = 0
               ORDER BY fetched_at DESC"""
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def mark_alerted(item_id: int):
    """Mark a news item as alerted."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(
            "UPDATE news_items SET alerted = 1 WHERE id = ?",
            (item_id,)
        )
        await db.commit()


async def get_undigested_news(limit: int = 50) -> list[dict]:
    """Get news items not yet included in a digest."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM news_items
               WHERE digested = 0
               ORDER BY fetched_at DESC
               LIMIT ?""",
            (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def mark_digested(item_ids: list[int]):
    """Mark news items as digested."""
    if not item_ids:
        return
    async with aiosqlite.connect(str(DB_PATH)) as db:
        placeholders = ",".join("?" * len(item_ids))
        await db.execute(
            f"UPDATE news_items SET digested = 1 WHERE id IN ({placeholders})",
            item_ids
        )
        await db.commit()


async def get_recent_news(limit: int = 10) -> list[dict]:
    """Get most recent news items."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM news_items
               ORDER BY fetched_at DESC
               LIMIT ?""",
            (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def insert_tweet_draft(news_item_id: int | None,
                             tweet_text: str,
                             template_type: str = "insight") -> int:
    """Insert a tweet draft. Returns draft id."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        cursor = await db.execute(
            """INSERT INTO tweet_drafts
               (news_item_id, tweet_text, template_type, created_at)
               VALUES (?, ?, ?, ?)""",
            (news_item_id, tweet_text, template_type,
             datetime.now(timezone.utc).isoformat())
        )
        await db.commit()
        return cursor.lastrowid


async def get_pending_drafts(limit: int = 5) -> list[dict]:
    """Get pending tweet drafts."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM tweet_drafts
               WHERE status = 'pending'
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def update_draft_status(draft_id: int, status: str):
    """Update tweet draft status (approved / skipped)."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        approved_at = datetime.now(timezone.utc).isoformat() if status == "approved" else None
        await db.execute(
            """UPDATE tweet_drafts
               SET status = ?, approved_at = ?
               WHERE id = ?""",
            (status, approved_at, draft_id)
        )
        await db.commit()


async def log_digest(digest_type: str, items_count: int, message_text: str):
    """Log a sent digest."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(
            """INSERT INTO digest_log (digest_type, sent_at, items_count, message_text)
               VALUES (?, ?, ?, ?)""",
            (digest_type, datetime.now(timezone.utc).isoformat(),
             items_count, message_text)
        )
        await db.commit()


async def get_news_stats() -> dict:
    """Get statistics about collected news."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM news_items")
        total = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM news_items WHERE category = '🔴 CRITICAL'")
        critical = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM news_items WHERE digested = 0")
        undigested = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM tweet_drafts WHERE status = 'pending'")
        pending_drafts = (await cursor.fetchone())[0]

        cursor = await db.execute(
            """SELECT source, COUNT(*) as cnt FROM news_items
               GROUP BY source ORDER BY cnt DESC LIMIT 5""")
        top_sources = await cursor.fetchall()

        return {
            "total_news": total,
            "critical": critical,
            "undigested": undigested,
            "pending_drafts": pending_drafts,
            "top_sources": [(r[0], r[1]) for r in top_sources],
        }
