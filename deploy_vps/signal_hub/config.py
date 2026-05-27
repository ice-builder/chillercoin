"""
OneProp Signal Hub — Configuration
Soldier-only signal system: daily signal + auto tracking + TG posting
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── Telegram ────────────────────────────────────────────
# Shared bot token (same as screener / pump hunter)
TG_TOKEN = os.getenv("SIGNAL_HUB_TG_TOKEN", "")

# Public channel @onepropru (для публичных постов — сигналы + итоги)
TG_PUBLIC_CHANNEL = os.getenv("SIGNAL_HUB_PUBLIC_CHANNEL", "")  # e.g. "@onepropru"

# Private group (закрытая группа с топиками)
TG_PRIVATE_CHAT_ID = os.getenv("SIGNAL_HUB_PRIVATE_CHAT_ID", "")
TG_PRIVATE_THREAD_SIGNALS = int(os.getenv("SIGNAL_HUB_THREAD_SIGNALS", "0") or 0)

# ─── Server ──────────────────────────────────────────────
SERVER_HOST = "0.0.0.0"
SERVER_PORT = int(os.getenv("SIGNAL_HUB_PORT", "8090"))
API_SECRET = os.getenv("SIGNAL_HUB_API_SECRET", "oneprop-hub-2026")

# ─── Database ────────────────────────────────────────────
DB_PATH = os.getenv("SIGNAL_HUB_DB_PATH", "signals.db")

# ─── Result Tracking (checkpoints after signal) ─────────
# P&L checked at 15 min, 1 hour, 4 hours after signal open
TRACK_INTERVALS = ["15m", "1h", "4h"]
TRACK_INTERVALS_SEC = {
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
}

# ─── CORS ────────────────────────────────────────────────
CORS_ORIGINS = [
    "https://oneprop.ru",
    "https://screener.oneprop.ru",
    "http://localhost:3000",
    "*",
]
