"""
ZeroChainAI — Daily Digest Agent.

Generates a structured daily digest of collected news,
categorised and formatted for Telegram delivery.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage
from config import (
    CATEGORY_CRITICAL, CATEGORY_INDUSTRY, CATEGORY_PR_OPP,
    CATEGORY_COMPETITOR, CATEGORY_GENERAL,
)

logger = logging.getLogger("Digest")


async def generate_digest() -> str | None:
    """
    Generate a daily digest from undigested news items.
    Returns formatted Telegram message or None if nothing to report.
    """
    items = await storage.get_undigested_news(limit=100)
    if not items:
        return None

    # Group by category
    by_category = defaultdict(list)
    for item in items:
        by_category[item["category"]].append(item)

    # Build digest text
    now = datetime.now(timezone.utc)
    lines = [
        f"📊 <b>ZeroChainAI Daily Digest</b>",
        f"🕐 {now.strftime('%d %B %Y, %H:%M UTC')}",
        f"📰 <b>{len(items)} items collected</b>",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    # Category order for display
    cat_order = [
        CATEGORY_CRITICAL,
        CATEGORY_COMPETITOR,
        CATEGORY_PR_OPP,
        CATEGORY_INDUSTRY,
        CATEGORY_GENERAL,
    ]

    cat_emojis = {
        CATEGORY_CRITICAL: "🔴",
        CATEGORY_COMPETITOR: "🔵",
        CATEGORY_PR_OPP: "🟢",
        CATEGORY_INDUSTRY: "🟡",
        CATEGORY_GENERAL: "⚪",
    }

    for cat in cat_order:
        cat_items = by_category.get(cat, [])
        if not cat_items:
            continue

        emoji = cat_emojis.get(cat, "📰")
        lines.append(f"\n{emoji} <b>{cat}</b> ({len(cat_items)})")
        lines.append("")

        # Show up to 5 items per category
        for item in cat_items[:5]:
            title = item["title"][:70]
            source = item["source"]
            url = item["url"]
            lines.append(f"• <a href='{url}'>{title}</a>")
            lines.append(f"  <i>via {source}</i>")

        if len(cat_items) > 5:
            lines.append(f"  <i>...and {len(cat_items) - 5} more</i>")

    # Stats footer
    stats = await storage.get_news_stats()
    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📈 <b>Total in DB:</b> {stats['total_news']} items",
        f"🔴 Critical total: {stats['critical']}",
        f"📝 Pending tweet drafts: {stats['pending_drafts']}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "💡 <b>Actions:</b>",
        "/news — latest 10 items",
        "/drafts — pending tweet drafts",
        "/monitor — agent status",
    ])

    # Mark all as digested
    item_ids = [item["id"] for item in items]
    await storage.mark_digested(item_ids)

    # Log the digest
    digest_text = "\n".join(lines)
    await storage.log_digest(
        digest_type="daily",
        items_count=len(items),
        message_text=digest_text[:4000],
    )

    return digest_text


async def generate_channel_digest() -> str | None:
    """
    Generate a shorter, public-facing digest for the @ZeroChainAI_News channel.
    Only includes Critical and PR Opportunity items.
    """
    items = await storage.get_recent_news(limit=30)
    if not items:
        return None

    # Filter to interesting categories only
    public_cats = {CATEGORY_CRITICAL, CATEGORY_PR_OPP, CATEGORY_INDUSTRY}
    public_items = [i for i in items if i["category"] in public_cats]

    if not public_items:
        return None

    now = datetime.now(timezone.utc)
    lines = [
        f"🛡️ <b>ZeroChainAI Security Briefing</b>",
        f"🕐 {now.strftime('%d %B %Y')}",
        "",
    ]

    for item in public_items[:8]:
        cat_emoji = {
            CATEGORY_CRITICAL: "🔴",
            CATEGORY_PR_OPP: "🟢",
            CATEGORY_INDUSTRY: "🟡",
        }.get(item["category"], "📰")

        title = item["title"][:80]
        url = item["url"]
        lines.append(f"{cat_emoji} <a href='{url}'>{title}</a>")

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🌐 <a href='https://0chain.ai'>0chain.ai</a> · "
        "🤖 @ZeroChainAIbot",
    ])

    return "\n".join(lines)
