"""
ZeroChainAI — Configuration for all agents.
"""

import os
from pathlib import Path

# ─── Core ────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("ZEROCHAINAI_BOT_TOKEN", "")
ADMIN_CHAT_ID = os.environ.get("ZEROCHAINAI_ADMIN_ID", "")
# Public Telegram channel (e.g. @ZeroChainAI_News)
NEWS_CHANNEL_ID = os.environ.get("ZEROCHAINAI_CHANNEL_ID", "")

# ─── Paths ───────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "zerochainai.db"

# ─── News Monitor ────────────────────────────────────────────
MONITOR_INTERVAL_MINUTES = 30

RSS_FEEDS = {
    # --- Crypto security (primary) ---
    "Rekt News": "https://rekt.news/feed.xml",
    "Immunefi Blog": "https://medium.com/feed/immunefi",
    "SlowMist": "https://medium.com/feed/@peckshield",
    "Chainalysis Blog": "https://blog.chainalysis.com/feed/",

    # --- Crypto general ---
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "The Block": "https://www.theblock.co/rss.xml",
    "CryptoSlate": "https://cryptoslate.com/feed/",
    "Decrypt": "https://decrypt.co/feed",
    "Cointelegraph": "https://cointelegraph.com/rss",

    # --- Reddit ---
    "r/cryptocurrency": "https://www.reddit.com/r/cryptocurrency/.rss",
    "r/defi": "https://www.reddit.com/r/defi/.rss",
    "r/ethdev": "https://www.reddit.com/r/ethdev/.rss",
    "r/solidity": "https://www.reddit.com/r/solidity/.rss",
}

# ─── Keywords for filtering ─────────────────────────────────
# Critical (immediate alert)
CRITICAL_KEYWORDS = [
    "exploit", "exploited", "hack", "hacked", "drained",
    "vulnerability", "0-day", "zero-day", "zero day",
    "rug pull", "rugpull", "flash loan attack",
    "bridge hack", "stolen", "breach", "compromised",
    "emergency", "critical vulnerability", "backdoor",
    "private key leak", "oracle manipulation",
]

# Industry (daily digest)
INDUSTRY_KEYWORDS = [
    "audit", "security", "smart contract",
    "defi", "protocol", "blockchain",
    "solidity", "rust", "move",
    "tvl", "liquidity", "staking",
    "regulation", "sec", "compliance",
    "layer 2", "rollup", "zk-proof",
    "mev", "sandwich attack",
]

# Competitor tracking
COMPETITOR_KEYWORDS = [
    "certik", "hacken", "quantstamp", "trail of bits",
    "openzeppelin", "consensys diligence", "peckshield",
    "slowmist", "halborn", "immunefi",
    "zerochainai", "0chain.ai", "zerochain",
]

# ─── Categories ──────────────────────────────────────────────
CATEGORY_CRITICAL = "🔴 CRITICAL"
CATEGORY_INDUSTRY = "🟡 Industry"
CATEGORY_PR_OPP = "🟢 PR Opportunity"
CATEGORY_COMPETITOR = "🔵 Competitor"
CATEGORY_GENERAL = "⚪ General"

# ─── Digest Schedule ────────────────────────────────────────
# 1x per day — evening digest (UTC)
DIGEST_HOUR_UTC = 12  # 12:00 UTC = 19:00 ICT (Bangkok)

# ─── Twitter Draft Mode ─────────────────────────────────────
# Templates for auto-generated tweet drafts
TWEET_TEMPLATES = {
    "threat_alert": (
        "🔴 ALERT: {title}\n\n"
        "{summary}\n\n"
        "Our analysis → 0chain.ai\n\n"
        "#BlockchainSecurity #Web3 #0day"
    ),
    "insight": (
        "🧵 {title}\n\n"
        "{summary}\n\n"
        "Read more: {url}\n\n"
        "#DeFiSecurity #SmartContracts"
    ),
    "company_news": (
        "🛡️ {title}\n\n"
        "{summary}\n\n"
        "Learn more → 0chain.ai\n\n"
        "#ZeroChainAI #BlockchainAudit"
    ),
    "industry_comment": (
        "💡 {title}\n\n"
        "{summary}\n\n"
        "#Crypto #Web3 #Security"
    ),
}

# ─── Request headers ─────────────────────────────────────────
REQUEST_HEADERS = {
    "User-Agent": (
        "ZeroChainAI-Monitor/1.0 "
        "(+https://0chain.ai; security research)"
    ),
}
REQUEST_TIMEOUT = 15  # seconds
