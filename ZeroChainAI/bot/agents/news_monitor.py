"""
ZeroChainAI — News Monitor Agent.

Collects news from RSS feeds every 30 min, categorises them,
stores in SQLite, and sends instant alerts for critical items.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from html import unescape

import aiohttp
import feedparser

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    RSS_FEEDS, CRITICAL_KEYWORDS, INDUSTRY_KEYWORDS,
    COMPETITOR_KEYWORDS, MONITOR_INTERVAL_MINUTES,
    CATEGORY_CRITICAL, CATEGORY_INDUSTRY, CATEGORY_PR_OPP,
    CATEGORY_COMPETITOR, CATEGORY_GENERAL,
    REQUEST_HEADERS, REQUEST_TIMEOUT,
)
import storage

logger = logging.getLogger("NewsMonitor")


def _clean_html(text: str) -> str:
    """Strip HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]  # truncate long summaries


def _categorise(title: str, summary: str) -> tuple[str, list[str]]:
    """Categorise news by matched keywords. Returns (category, keywords)."""
    text = (title + " " + summary).lower()
    matched = []

    # Check critical first
    for kw in CRITICAL_KEYWORDS:
        if kw.lower() in text:
            matched.append(kw)
    if matched:
        return CATEGORY_CRITICAL, matched

    # Check competitors
    comp_matched = []
    for kw in COMPETITOR_KEYWORDS:
        if kw.lower() in text:
            comp_matched.append(kw)
    if comp_matched:
        return CATEGORY_COMPETITOR, comp_matched

    # Check industry
    ind_matched = []
    for kw in INDUSTRY_KEYWORDS:
        if kw.lower() in text:
            ind_matched.append(kw)
    if len(ind_matched) >= 2:
        # If multiple industry keywords match + security related → PR opportunity
        security_words = {"audit", "security", "vulnerability", "smart contract"}
        if security_words & set(k.lower() for k in ind_matched):
            return CATEGORY_PR_OPP, ind_matched
        return CATEGORY_INDUSTRY, ind_matched

    if ind_matched:
        return CATEGORY_INDUSTRY, ind_matched

    return CATEGORY_GENERAL, []


async def _fetch_feed(session: aiohttp.ClientSession,
                      name: str, url: str) -> list[dict]:
    """Fetch and parse a single RSS feed."""
    items = []
    try:
        async with session.get(
            url, headers=REQUEST_HEADERS, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        ) as resp:
            if resp.status != 200:
                logger.warning(f"[{name}] HTTP {resp.status}")
                return items
            body = await resp.text()
    except Exception as e:
        logger.warning(f"[{name}] Fetch error: {e}")
        return items

    feed = feedparser.parse(body)
    for entry in feed.entries[:15]:  # max 15 per source
        link = entry.get("link", "")
        title = _clean_html(entry.get("title", ""))
        summary = _clean_html(
            entry.get("summary", entry.get("description", ""))
        )
        pub_date = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                pub_date = datetime(*entry.published_parsed[:6],
                                    tzinfo=timezone.utc).isoformat()
            except Exception:
                pass

        if link and title:
            category, keywords = _categorise(title, summary)
            items.append({
                "url": link,
                "title": title,
                "summary": summary,
                "source": name,
                "category": category,
                "keywords_matched": keywords,
                "published_at": pub_date,
            })
    return items


async def fetch_all_feeds() -> list[dict]:
    """Fetch all configured RSS feeds concurrently."""
    new_items = []
    async with aiohttp.ClientSession() as session:
        tasks = [
            _fetch_feed(session, name, url)
            for name, url in RSS_FEEDS.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, list):
                new_items.extend(result)
            elif isinstance(result, Exception):
                logger.error(f"Feed task error: {result}")
    return new_items


async def run_monitor_cycle() -> dict:
    """
    Run one monitoring cycle:
    1. Fetch all feeds
    2. Store new items (dedup by URL)
    3. Return stats
    """
    stats = {"fetched": 0, "new": 0, "critical": 0}

    items = await fetch_all_feeds()
    stats["fetched"] = len(items)

    for item in items:
        item_id = await storage.insert_news(
            url=item["url"],
            title=item["title"],
            summary=item["summary"],
            source=item["source"],
            category=item["category"],
            keywords_matched=item["keywords_matched"],
            published_at=item.get("published_at"),
        )
        if item_id is not None:
            stats["new"] += 1
            if item["category"] == CATEGORY_CRITICAL:
                stats["critical"] += 1

    logger.info(
        f"Monitor cycle: fetched={stats['fetched']}, "
        f"new={stats['new']}, critical={stats['critical']}"
    )
    return stats


async def monitor_loop(alert_callback=None, channel_callback=None):
    """
    Background loop — runs every MONITOR_INTERVAL_MINUTES minutes.
    alert_callback(item: dict) — called for each critical item (send to admin).
    channel_callback(item: dict) — called for each critical item (send to channel).
    """
    logger.info(f"📡 News monitor started (interval: {MONITOR_INTERVAL_MINUTES}min)")
    await storage.init_db()

    while True:
        try:
            await run_monitor_cycle()

            # Send critical alerts
            if alert_callback or channel_callback:
                critical_items = await storage.get_unalerted_critical()
                for item in critical_items:
                    if alert_callback:
                        try:
                            await alert_callback(item)
                        except Exception as e:
                            logger.error(f"Alert callback error: {e}")
                    if channel_callback:
                        try:
                            await channel_callback(item)
                        except Exception as e:
                            logger.error(f"Channel callback error: {e}")
                    await storage.mark_alerted(item["id"])

        except Exception as e:
            logger.error(f"Monitor cycle error: {e}")

        await asyncio.sleep(MONITOR_INTERVAL_MINUTES * 60)
