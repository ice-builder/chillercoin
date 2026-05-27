"""
ZeroChainAI — Twitter/PR Draft Agent.

Generates tweet drafts from news items and sends them
to admin in Telegram for manual posting.
Mode: Draft only (no API posting).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    TWEET_TEMPLATES, CATEGORY_CRITICAL, CATEGORY_PR_OPP,
    CATEGORY_INDUSTRY, CATEGORY_COMPETITOR,
)
import storage

logger = logging.getLogger("TwitterAgent")


def generate_draft(news_item: dict) -> str | None:
    """
    Generate a tweet draft text from a news item.
    Returns None if item is not worth tweeting.
    """
    category = news_item.get("category", "")
    title = news_item.get("title", "")
    summary = news_item.get("summary", "")
    url = news_item.get("url", "")

    # Shorten summary for tweet (max ~200 chars)
    short_summary = summary[:180] + "..." if len(summary) > 180 else summary

    if category == CATEGORY_CRITICAL:
        template = TWEET_TEMPLATES["threat_alert"]
        return template.format(
            title=title[:80],
            summary=short_summary,
            url=url,
        )

    elif category == CATEGORY_PR_OPP:
        template = TWEET_TEMPLATES["industry_comment"]
        return template.format(
            title=title[:80],
            summary=short_summary,
            url=url,
        )

    elif category == CATEGORY_INDUSTRY:
        template = TWEET_TEMPLATES["insight"]
        return template.format(
            title=title[:80],
            summary=short_summary,
            url=url,
        )

    elif category == CATEGORY_COMPETITOR:
        # Don't tweet about competitors directly — but flag for digest
        return None

    return None


async def generate_drafts_from_news(max_drafts: int = 3) -> list[dict]:
    """
    Look at recent undigested news, generate tweet drafts
    for the most relevant items.
    """
    news = await storage.get_undigested_news(limit=30)
    drafts = []

    # Priority: Critical → PR Opportunity → Industry
    priority_order = [CATEGORY_CRITICAL, CATEGORY_PR_OPP, CATEGORY_INDUSTRY]

    for priority_cat in priority_order:
        for item in news:
            if item["category"] == priority_cat and len(drafts) < max_drafts:
                text = generate_draft(item)
                if text and len(text) <= 280:
                    draft_id = await storage.insert_tweet_draft(
                        news_item_id=item["id"],
                        tweet_text=text,
                        template_type=_get_template_type(item["category"]),
                    )
                    drafts.append({
                        "id": draft_id,
                        "news_item_id": item["id"],
                        "tweet_text": text,
                        "source_title": item["title"],
                        "source_url": item["url"],
                        "category": item["category"],
                    })

    logger.info(f"Generated {len(drafts)} tweet drafts")
    return drafts


def _get_template_type(category: str) -> str:
    """Map category to template type."""
    mapping = {
        CATEGORY_CRITICAL: "threat_alert",
        CATEGORY_PR_OPP: "industry_comment",
        CATEGORY_INDUSTRY: "insight",
    }
    return mapping.get(category, "insight")


def format_draft_for_telegram(draft: dict) -> str:
    """Format a tweet draft for display in Telegram admin message."""
    return (
        f"📝 <b>Tweet Draft #{draft['id']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<code>{draft['tweet_text']}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📰 Source: {draft.get('source_title', '—')[:60]}\n"
        f"🔗 {draft.get('source_url', '')}\n"
        f"📊 Category: {draft.get('category', '—')}\n"
        f"📏 Length: {len(draft['tweet_text'])}/280\n\n"
        f"Copy the text above and post to Twitter/X"
    )
