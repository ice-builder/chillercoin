"""
ZeroChainAI Telegram Bot — @ZeroChainAIbot
Lead collection + News monitoring + PR/Twitter drafts + Daily digests.

Setup:
  1. pip install -r requirements.txt
  2. export ZEROCHAINAI_BOT_TOKEN="your-token"
  3. export ZEROCHAINAI_ADMIN_ID="your-telegram-id"
  4. export ZEROCHAINAI_CHANNEL_ID="@YourChannel"  (optional)
  5. python bot.py

Requirements: see requirements.txt
"""

import os
import sys
import logging
import asyncio
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters,
)

# Ensure local imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    BOT_TOKEN, ADMIN_CHAT_ID, NEWS_CHANNEL_ID,
    MONITOR_INTERVAL_MINUTES, DIGEST_HOUR_UTC,
    RSS_FEEDS, CRITICAL_KEYWORDS,
)
import storage
from agents.news_monitor import monitor_loop, fetch_all_feeds, run_monitor_cycle
from agents.twitter_agent import (
    generate_drafts_from_news, format_draft_for_telegram,
)
from agents.digest import generate_digest, generate_channel_digest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-16s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("ZeroChainAI_Bot")

# Conversation states for /audit flow
PROJECT_NAME, PROJECT_URL, SERVICE_TYPE, EMAIL, EXTRA_INFO = range(5)

# Track agent state
_agent_state = {
    "monitor_running": False,
    "started_at": None,
    "last_cycle": None,
    "total_alerts": 0,
}


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def is_admin(user_id: int) -> bool:
    """Check if user is the admin."""
    return str(user_id) == str(ADMIN_CHAT_ID)


# ═══════════════════════════════════════════════════════════════
# Public commands (/start, /services, /contact)
# ═══════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context):
    """Welcome message"""
    keyboard = [
        [InlineKeyboardButton("🔍 Request Audit", callback_data="start_audit")],
        [InlineKeyboardButton("📋 Our Services", callback_data="show_services")],
        [
            InlineKeyboardButton("🌐 Website", url="https://0chain.ai"),
            InlineKeyboardButton("📧 Email", url="mailto:contact@0chain.ai"),
        ],
    ]
    await update.message.reply_text(
        "🛡️ <b>Welcome to ZeroChainAI</b>\n\n"
        "We provide AI-powered <b>0-day vulnerability intelligence</b> "
        "for blockchain protocols.\n\n"
        "Our analysis covers <b>5 layers</b>:\n"
        "• L1 — Smart Contracts (Solidity, Rust, Move)\n"
        "• L2 — Protocol Design & Economics\n"
        "• L3 — Infrastructure & Supply Chain\n"
        "• L4 — Consensus & Network\n"
        "• L5 — Zero-Day Research (compiler, VM, crypto)\n\n"
        "How can we help you?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_services(update: Update, context):
    """Show services overview"""
    text = (
        "📋 <b>ZeroChainAI Services</b>\n\n"
        "⚡ <b>ZeroScan</b> — Instant AI Scan\n"
        "AI-powered smart contract analysis with remediation guidance.\n"
        "From <b>$500</b>\n\n"
        "🔍 <b>ZeroAudit</b> — Full Protocol Audit\n"
        "AI analysis + expert review. Comprehensive security report.\n"
        "From <b>$20,000</b>\n\n"
        "🛡️ <b>ZeroGuard</b> — 24/7 Monitoring\n"
        "Real-time threat detection and instant alerts.\n"
        "From <b>$5,000/mo</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🌐 <a href='https://0chain.ai'>0chain.ai</a> · "
        "📧 contact@0chain.ai"
    )
    keyboard = [[InlineKeyboardButton("🔍 Request Audit", callback_data="start_audit")]]
    msg = update.message or update.callback_query.message
    if update.callback_query:
        await update.callback_query.answer()
        await msg.reply_text(text, parse_mode="HTML",
                             reply_markup=InlineKeyboardMarkup(keyboard),
                             disable_web_page_preview=True)
    else:
        await msg.reply_text(text, parse_mode="HTML",
                             reply_markup=InlineKeyboardMarkup(keyboard),
                             disable_web_page_preview=True)


async def cmd_contact(update: Update, context):
    """Contact info"""
    await update.message.reply_text(
        "📬 <b>Contact ZeroChainAI</b>\n\n"
        "📧 Email: <code>contact@0chain.ai</code>\n"
        "💬 Telegram: @ZeroChainAIbot (this bot)\n"
        "🌐 Website: https://0chain.ai\n\n"
        "For audit requests, use /audit command.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ═══════════════════════════════════════════════════════════════
# Audit request conversation flow
# ═══════════════════════════════════════════════════════════════

async def audit_start(update: Update, context):
    """Begin audit request flow"""
    text = (
        "🔍 <b>Audit Request</b>\n\n"
        "Let's collect some info about your project.\n\n"
        "📋 <b>Step 1/4:</b> What is your <b>project name</b>?"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode="HTML")
    else:
        await update.message.reply_text(text, parse_mode="HTML")
    return PROJECT_NAME


async def recv_project_name(update: Update, context):
    context.user_data["project_name"] = update.message.text
    await update.message.reply_text(
        "✅ Got it!\n\n"
        "🔗 <b>Step 2/4:</b> Share your <b>GitHub repo URL</b> or "
        "<b>contract address</b>.\n\n"
        "Type /skip if not available yet.",
        parse_mode="HTML",
    )
    return PROJECT_URL


async def recv_project_url(update: Update, context):
    text = update.message.text
    context.user_data["project_url"] = "—" if text == "/skip" else text

    keyboard = [
        [InlineKeyboardButton("⚡ ZeroScan ($500+)", callback_data="svc_scan")],
        [InlineKeyboardButton("🔍 ZeroAudit ($20K+)", callback_data="svc_audit")],
        [InlineKeyboardButton("🛡️ ZeroGuard ($5K/mo)", callback_data="svc_guard")],
        [InlineKeyboardButton("❓ Not sure yet", callback_data="svc_unknown")],
    ]
    await update.message.reply_text(
        "🎯 <b>Step 3/4:</b> Which service are you interested in?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SERVICE_TYPE


async def recv_service_type(update: Update, context):
    query = update.callback_query
    await query.answer()
    labels = {
        "svc_scan": "ZeroScan",
        "svc_audit": "ZeroAudit",
        "svc_guard": "ZeroGuard",
        "svc_unknown": "Not decided",
    }
    context.user_data["service_type"] = labels.get(query.data, query.data)
    await query.message.reply_text(
        f"✅ <b>{context.user_data['service_type']}</b> selected.\n\n"
        "📧 <b>Step 4/4:</b> Please share your <b>email address</b> "
        "so we can send the report.",
        parse_mode="HTML",
    )
    return EMAIL


async def recv_email(update: Update, context):
    context.user_data["email"] = update.message.text
    await update.message.reply_text(
        "💬 Any additional details? (description of your protocol, "
        "specific concerns, timeline)\n\n"
        "Type /skip to finish.",
        parse_mode="HTML",
    )
    return EXTRA_INFO


async def recv_extra_info(update: Update, context):
    text = update.message.text
    context.user_data["extra_info"] = "" if text == "/skip" else text

    ud = context.user_data
    user = update.message.from_user
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    summary = (
        "🚨 <b>New Audit Request via Telegram</b>\n\n"
        f"👤 <b>User:</b> {user.full_name} (@{user.username or 'no_username'})\n"
        f"📋 <b>Project:</b> {ud.get('project_name', '—')}\n"
        f"🔗 <b>URL/Contract:</b> {ud.get('project_url', '—')}\n"
        f"🎯 <b>Service:</b> {ud.get('service_type', '—')}\n"
        f"📧 <b>Email:</b> {ud.get('email', '—')}\n"
        f"💬 <b>Details:</b> {ud.get('extra_info', '—')}\n\n"
        f"🕐 {timestamp}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    await update.message.reply_text(
        "✅ <b>Request submitted!</b>\n\n"
        f"📋 Project: <b>{ud.get('project_name')}</b>\n"
        f"🎯 Service: <b>{ud.get('service_type')}</b>\n"
        f"📧 Email: <b>{ud.get('email')}</b>\n\n"
        "We will review your request and contact you within <b>24 hours</b>.\n\n"
        "📧 Urgent? Email us at <code>contact@0chain.ai</code>",
        parse_mode="HTML",
    )

    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text=summary, parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Failed to notify admin: {e}")

    context.user_data.clear()
    return ConversationHandler.END


async def audit_cancel(update: Update, context):
    """Cancel audit flow"""
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Audit request cancelled.\n\nUse /audit to start again anytime.",
        parse_mode="HTML",
    )
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
# Admin commands — News / Digest / Drafts / Monitor
# ═══════════════════════════════════════════════════════════════

async def cmd_news(update: Update, context):
    """Show recent 10 news items (admin only)."""
    if not is_admin(update.message.from_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return

    items = await storage.get_recent_news(limit=10)
    if not items:
        await update.message.reply_text("📭 No news collected yet. Monitor may still be fetching.")
        return

    lines = ["📰 <b>Latest News</b>\n"]
    for i, item in enumerate(items, 1):
        cat = item.get("category", "")
        title = item["title"][:65]
        source = item["source"]
        url = item["url"]
        lines.append(f"{i}. {cat} <a href='{url}'>{title}</a>")
        lines.append(f"   <i>via {source}</i>")

    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def cmd_digest(update: Update, context):
    """Force-generate digest now (admin only)."""
    if not is_admin(update.message.from_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return

    await update.message.reply_text("⏳ Generating digest...")

    digest_text = await generate_digest()
    if not digest_text:
        await update.message.reply_text("📭 No new items to digest.")
        return

    # Split long messages (Telegram limit: 4096 chars)
    if len(digest_text) > 4000:
        parts = [digest_text[i:i+4000] for i in range(0, len(digest_text), 4000)]
        for part in parts:
            await update.message.reply_text(
                part, parse_mode="HTML", disable_web_page_preview=True)
    else:
        await update.message.reply_text(
            digest_text, parse_mode="HTML", disable_web_page_preview=True)


async def cmd_drafts(update: Update, context):
    """Show pending tweet drafts (admin only)."""
    if not is_admin(update.message.from_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return

    # Generate new drafts from recent news
    new_drafts = await generate_drafts_from_news(max_drafts=3)

    # Get all pending
    drafts = await storage.get_pending_drafts(limit=5)
    if not drafts:
        await update.message.reply_text(
            "📝 No pending tweet drafts.\n\n"
            "Drafts are generated from news. "
            "Wait for the monitor to collect some items."
        )
        return

    for draft in drafts:
        text = format_draft_for_telegram(draft)
        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"draft_approve_{draft['id']}"),
                InlineKeyboardButton("❌ Skip", callback_data=f"draft_skip_{draft['id']}"),
            ]
        ]
        await update.message.reply_text(
            text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True,
        )


async def cmd_monitor(update: Update, context):
    """Show agent status (admin only)."""
    if not is_admin(update.message.from_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return

    stats = await storage.get_news_stats()
    status_text = (
        "🤖 <b>Agent Status</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📡 Monitor: {'🟢 Running' if _agent_state['monitor_running'] else '🔴 Stopped'}\n"
        f"⏱️ Interval: {MONITOR_INTERVAL_MINUTES} min\n"
        f"🕐 Started: {_agent_state.get('started_at', '—')}\n\n"
        f"📰 Total news: <b>{stats['total_news']}</b>\n"
        f"🔴 Critical: {stats['critical']}\n"
        f"📋 Undigested: {stats['undigested']}\n"
        f"📝 Pending drafts: {stats['pending_drafts']}\n\n"
        f"📡 RSS sources: {len(RSS_FEEDS)}\n"
        f"🔑 Critical keywords: {len(CRITICAL_KEYWORDS)}\n"
    )

    if stats["top_sources"]:
        status_text += "\n<b>Top sources:</b>\n"
        for source, count in stats["top_sources"]:
            status_text += f"  • {source}: {count}\n"

    status_text += (
        "\n━━━━━━━━━━━━━━━━━━━━\n"
        f"📢 Channel: {NEWS_CHANNEL_ID or '❌ Not configured'}\n"
        f"👤 Admin: {ADMIN_CHAT_ID}"
    )

    await update.message.reply_text(
        status_text, parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def cmd_sources(update: Update, context):
    """List all monitored sources (admin only)."""
    if not is_admin(update.message.from_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return

    lines = ["📡 <b>Monitored Sources</b>\n"]
    for i, (name, url) in enumerate(RSS_FEEDS.items(), 1):
        lines.append(f"{i}. <b>{name}</b>")
        lines.append(f"   <code>{url[:60]}...</code>")

    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def cmd_fetch(update: Update, context):
    """Force a monitor cycle now (admin only)."""
    if not is_admin(update.message.from_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return

    await update.message.reply_text("⏳ Fetching all sources...")
    stats = await run_monitor_cycle()

    await update.message.reply_text(
        f"✅ <b>Fetch complete</b>\n\n"
        f"📥 Fetched: {stats['fetched']} items\n"
        f"🆕 New: {stats['new']}\n"
        f"🔴 Critical: {stats['critical']}",
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════════
# Callback query router
# ═══════════════════════════════════════════════════════════════

async def button_handler(update: Update, context):
    query = update.callback_query

    if query.data == "start_audit":
        return await audit_start(update, context)
    elif query.data == "show_services":
        return await cmd_services(update, context)

    # Draft approval/skip
    elif query.data.startswith("draft_approve_"):
        draft_id = int(query.data.replace("draft_approve_", ""))
        await storage.update_draft_status(draft_id, "approved")
        await query.answer("✅ Draft approved!")
        await query.message.reply_text(
            f"✅ Draft #{draft_id} approved. Copy the text and post to Twitter/X!")
    elif query.data.startswith("draft_skip_"):
        draft_id = int(query.data.replace("draft_skip_", ""))
        await storage.update_draft_status(draft_id, "skipped")
        await query.answer("❌ Draft skipped")
        await query.message.reply_text(f"❌ Draft #{draft_id} skipped.")


# ═══════════════════════════════════════════════════════════════
# Free-text fallback
# ═══════════════════════════════════════════════════════════════

async def handle_message(update: Update, context):
    """Handle any free-text message outside conversation"""
    keyboard = [
        [InlineKeyboardButton("🔍 Request Audit", callback_data="start_audit")],
        [InlineKeyboardButton("📋 Our Services", callback_data="show_services")],
    ]
    await update.message.reply_text(
        "👋 Thanks for reaching out!\n\n"
        "Use one of the options below, or type:\n"
        "/audit — Request a security audit\n"
        "/services — View our services\n"
        "/contact — Contact information",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ═══════════════════════════════════════════════════════════════
# Background agents
# ═══════════════════════════════════════════════════════════════

async def _send_critical_alert(bot, item: dict):
    """Send critical alert to admin."""
    text = (
        f"🚨🔴 <b>CRITICAL ALERT</b>\n\n"
        f"<b>{item['title']}</b>\n\n"
        f"{item.get('summary', '')[:300]}\n\n"
        f"🔗 {item['url']}\n"
        f"📡 Source: {item['source']}\n"
        f"🕐 {item.get('fetched_at', '')}"
    )
    if ADMIN_CHAT_ID:
        await bot.send_message(
            chat_id=int(ADMIN_CHAT_ID),
            text=text, parse_mode="HTML",
            disable_web_page_preview=True,
        )
    _agent_state["total_alerts"] += 1


async def _send_critical_to_channel(bot, item: dict):
    """Send critical alert to public channel."""
    if not NEWS_CHANNEL_ID:
        return
    text = (
        f"🔴 <b>Security Alert</b>\n\n"
        f"<b>{item['title']}</b>\n\n"
        f"{item.get('summary', '')[:200]}\n\n"
        f"🔗 <a href='{item['url']}'>Read more</a>\n\n"
        f"🛡️ @ZeroChainAIbot · 0chain.ai"
    )
    try:
        await bot.send_message(
            chat_id=NEWS_CHANNEL_ID,
            text=text, parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"Channel send error: {e}")


async def _digest_scheduler(bot):
    """Run daily digest at configured hour."""
    logger.info(f"📊 Digest scheduler started (daily at {DIGEST_HOUR_UTC}:00 UTC)")
    while True:
        now = datetime.now(timezone.utc)
        # Calculate seconds until next digest time
        target = now.replace(hour=DIGEST_HOUR_UTC, minute=0, second=0, microsecond=0)
        if now >= target:
            # Already past today's time — schedule for tomorrow
            from datetime import timedelta
            target += timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        logger.info(f"Next digest in {wait_seconds/3600:.1f} hours")
        await asyncio.sleep(wait_seconds)

        try:
            # Admin digest
            digest_text = await generate_digest()
            if digest_text and ADMIN_CHAT_ID:
                if len(digest_text) > 4000:
                    parts = [digest_text[i:i+4000]
                             for i in range(0, len(digest_text), 4000)]
                    for part in parts:
                        await bot.send_message(
                            chat_id=int(ADMIN_CHAT_ID),
                            text=part, parse_mode="HTML",
                            disable_web_page_preview=True)
                else:
                    await bot.send_message(
                        chat_id=int(ADMIN_CHAT_ID),
                        text=digest_text, parse_mode="HTML",
                        disable_web_page_preview=True)

            # Channel digest
            if NEWS_CHANNEL_ID:
                ch_text = await generate_channel_digest()
                if ch_text:
                    await bot.send_message(
                        chat_id=NEWS_CHANNEL_ID,
                        text=ch_text, parse_mode="HTML",
                        disable_web_page_preview=True)

            logger.info("✅ Daily digest sent")
        except Exception as e:
            logger.error(f"Digest error: {e}")

        # Wait at least 1 hour to avoid double-sends
        await asyncio.sleep(3600)


async def post_init(application: Application):
    """Called after bot starts — launch background agents."""
    bot = application.bot
    await storage.init_db()

    _agent_state["started_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    _agent_state["monitor_running"] = True

    # Alert callbacks that capture the bot instance
    async def alert_cb(item):
        await _send_critical_alert(bot, item)

    async def channel_cb(item):
        await _send_critical_to_channel(bot, item)

    # Start background tasks
    asyncio.create_task(monitor_loop(
        alert_callback=alert_cb,
        channel_callback=channel_cb,
    ))
    asyncio.create_task(_digest_scheduler(bot))

    logger.info("🚀 All background agents launched")

    # Notify admin that bot is online
    if ADMIN_CHAT_ID:
        try:
            await bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text=(
                    "🟢 <b>ZeroChainAI Bot Online</b>\n\n"
                    f"📡 Monitor: every {MONITOR_INTERVAL_MINUTES} min\n"
                    f"📊 Digest: daily at {DIGEST_HOUR_UTC}:00 UTC\n"
                    f"📡 Sources: {len(RSS_FEEDS)}\n"
                    f"📢 Channel: {NEWS_CHANNEL_ID or '❌ not set'}\n\n"
                    "Commands:\n"
                    "/news — latest items\n"
                    "/digest — force digest\n"
                    "/drafts — tweet drafts\n"
                    "/fetch — force fetch\n"
                    "/monitor — agent status\n"
                    "/sources — list sources"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Admin startup notification failed: {e}")


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        print("❌ Set ZEROCHAINAI_BOT_TOKEN environment variable")
        print("   export ZEROCHAINAI_BOT_TOKEN='your-token-here'")
        return

    if not ADMIN_CHAT_ID:
        print("⚠️  ZEROCHAINAI_ADMIN_ID not set — admin features disabled")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Audit conversation flow
    audit_conv = ConversationHandler(
        entry_points=[
            CommandHandler("audit", audit_start),
            CallbackQueryHandler(audit_start, pattern="^start_audit$"),
        ],
        states={
            PROJECT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_project_name)],
            PROJECT_URL: [MessageHandler(filters.TEXT, recv_project_url)],
            SERVICE_TYPE: [CallbackQueryHandler(recv_service_type, pattern="^svc_")],
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_email)],
            EXTRA_INFO: [MessageHandler(filters.TEXT, recv_extra_info)],
        },
        fallbacks=[CommandHandler("cancel", audit_cancel)],
    )

    # Register handlers
    app.add_handler(audit_conv)

    # Public commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("services", cmd_services))
    app.add_handler(CommandHandler("contact", cmd_contact))

    # Admin commands
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("drafts", cmd_drafts))
    app.add_handler(CommandHandler("monitor", cmd_monitor))
    app.add_handler(CommandHandler("sources", cmd_sources))
    app.add_handler(CommandHandler("fetch", cmd_fetch))

    # Callbacks & fallback
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🛡️ ZeroChainAI Bot starting — @ZeroChainAIbot")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
