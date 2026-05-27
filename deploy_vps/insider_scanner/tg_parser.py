"""CryptoAttack TG Web Parser — Reads OI/flow alerts via public t.me/s/ pages.
No Telethon, no API credentials needed. Uses requests + regex.

Channels:
  @cryptoattack24 — OI gainers/losers, pump summaries
  @cryptoarsenal   — #CEXTrack activity/buying, #CEXFlows24 inflows/outflows

Parses messages like:
  📊 Top 10 OI Gainers (1h) #OpenInterest #TopGainers
  #LAB Binance OI Change (1h): 22.77% Price: 0.828
  💰 #LAB активность на 1.13M USDT за 13 мин
  #CEXTrack 💰 Activity alerts (buying/selling)
  #CEXFlows24 net inflows/outflows
"""
import re
import time
import logging
import requests
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone
import html as htmlmod

logger = logging.getLogger("insider.tg_parser")


# ─── Data Classes ──────────────────────────────────
@dataclass
class TGOIAlert:
    """Parsed OI alert from CryptoAttack channel."""
    symbol: str           # e.g. "LABUSDT"
    exchange: str         # e.g. "binance"
    oi_change_1h: float   # e.g. 22.77
    price: float          # e.g. 0.828
    timestamp: float = 0.0
    source: str = "cryptoattack24"

@dataclass
class TGFlowAlert:
    """Parsed CEX flow/activity alert."""
    symbol: str
    exchange: str
    amount_usdt: float    # e.g. 1_130_000
    period_min: int       # e.g. 13
    direction: str        # "buy" or "sell" or "activity"
    timestamp: float = 0.0
    source: str = ""

@dataclass
class TGMessage:
    """Raw parsed TG message."""
    post_id: str
    text: str
    timestamp: str
    channel: str


# ─── Regex Patterns ────────────────────────────────
# OI alert: "#LAB Binance OI Change (1h): 22.77% Price: 0.828"
OI_PATTERN = re.compile(
    r'#(\w+)\s+'
    r'(\w+)\s+OI\s+Change\s*\(1h\):\s*'
    r'([\d.]+)%\s*'
    r'(?:Price:\s*([\d.]+))?',
    re.IGNORECASE
)

# Flow alert RU: "#LAB активность на 1.13M USDT за 13 мин"
FLOW_PATTERN_RU = re.compile(
    r'#(\w+)\s+(?:активность|покупают|продают)\s*.*?'
    r'на\s+([\d.,]+)\s*([KkMm]?)\s*USDT\s+'
    r'за\s+(\d+)\s*мин',
    re.IGNORECASE
)

# Flow alert EN: "#LAB buy: $44974 sell: $22830"
FLOW_PATTERN_EN = re.compile(
    r'#(\w+)\s+buy:\s*\$?([\d,]+)\s+sell:\s*\$?([\d,]+)',
    re.IGNORECASE
)

# CEXTrack activity (old format): "Activity 💰 ... buying/selling"
CEXTRACK_PATTERN = re.compile(
    r'#CEXTrack.*?#(\w+).*?(buying|selling|activity)',
    re.IGNORECASE | re.DOTALL
)

# CEXTrack activity (@cryptoarsenal format):
# "#SAGA buying 💚 701K USDT in 12 min (11%) on Bybit"
# "#BABY activity 🤔 1,34M USDT in 4 min (10%) on Binance Futures"
CEXTRACK_ACTIVITY = re.compile(
    r'#(\w+)\s+(activity|buying|selling)\s*.*?'
    r'([\d][\d,.]*)\s*([KkMm]?)\s*USDT\s+in\s+(\d+)\s*min'
    r'(?:.*?on\s+(\w+))?',
    re.IGNORECASE | re.DOTALL
)

# CEXFlows net inflow: "#BTCUSDT Net Inflow: $1.2M"
CEXFLOWS_PATTERN = re.compile(
    r'#(\w+).*?(?:Net\s+Inflow|Inflow|Outflow).*?\$?([\d.,]+)\s*([KkMm]?)',
    re.IGNORECASE
)

# Top OI Gainers header
TOP_OI_HEADER = re.compile(
    r'Top\s+\d+\s+OI\s+(Gainers|Losers)\s*\(1h\)',
    re.IGNORECASE
)

# HTML message extractor for t.me/s/ pages
MSG_BLOCK_PATTERN = re.compile(
    r'data-post="(\w+/\d+)".*?'
    r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL
)

MSG_DATE_PATTERN = re.compile(
    r'datetime="([^"]+)"'
)


def parse_multiplier(value: str, suffix: str) -> float:
    """Parse numeric value with K/M suffix."""
    v = float(value.replace(",", "."))
    if suffix.upper() == "M":
        return v * 1_000_000
    elif suffix.upper() == "K":
        return v * 1_000
    return v


def clean_html(text: str) -> str:
    """Strip HTML tags and decode entities."""
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = htmlmod.unescape(text)
    return text.strip()


def parse_message_text(text: str, source: str = "") -> Tuple[List[TGOIAlert], List[TGFlowAlert]]:
    """Parse a single message text. Returns (oi_alerts, flow_alerts)."""
    oi_alerts = []
    flow_alerts = []
    now = time.time()

    # Parse OI alerts
    for match in OI_PATTERN.finditer(text):
        symbol_raw = match.group(1).upper()
        # Normalize: add USDT if not present
        symbol = symbol_raw if symbol_raw.endswith("USDT") else symbol_raw + "USDT"
        exchange = match.group(2).lower()
        oi_change = float(match.group(3))
        price = float(match.group(4)) if match.group(4) else 0.0

        oi_alerts.append(TGOIAlert(
            symbol=symbol, exchange=exchange,
            oi_change_1h=oi_change, price=price,
            timestamp=now, source=source
        ))

    # Parse flow alerts (Russian format)
    for match in FLOW_PATTERN_RU.finditer(text):
        symbol_raw = match.group(1).upper()
        symbol = symbol_raw if symbol_raw.endswith("USDT") else symbol_raw + "USDT"
        amount = parse_multiplier(match.group(2), match.group(3))
        period = int(match.group(4))

        direction = "activity"
        lower_text = text.lower()
        if "покупают" in lower_text:
            direction = "buy"
        elif "продают" in lower_text:
            direction = "sell"

        flow_alerts.append(TGFlowAlert(
            symbol=symbol, exchange="multi",
            amount_usdt=amount, period_min=period,
            direction=direction, timestamp=now, source=source
        ))

    # Parse flow alerts (English buy/sell)
    for match in FLOW_PATTERN_EN.finditer(text):
        symbol_raw = match.group(1).upper()
        symbol = symbol_raw if symbol_raw.endswith("USDT") else symbol_raw + "USDT"
        buy_val = float(match.group(2).replace(",", ""))
        sell_val = float(match.group(3).replace(",", ""))
        net = buy_val - sell_val

        flow_alerts.append(TGFlowAlert(
            symbol=symbol, exchange="multi",
            amount_usdt=abs(net), period_min=0,
            direction="buy" if net > 0 else "sell",
            timestamp=now, source=source
        ))

    # Parse CEXTrack activity alerts (@cryptoarsenal format)
    for match in CEXTRACK_ACTIVITY.finditer(text):
        symbol_raw = match.group(1).upper()
        symbol = symbol_raw if symbol_raw.endswith("USDT") else symbol_raw + "USDT"
        direction_raw = match.group(2).lower()
        amount = parse_multiplier(match.group(3), match.group(4))
        period = int(match.group(5))
        exchange = match.group(6).lower() if match.group(6) else "multi"

        direction = "buy" if direction_raw == "buying" else ("sell" if direction_raw == "selling" else "activity")

        flow_alerts.append(TGFlowAlert(
            symbol=symbol, exchange=exchange,
            amount_usdt=amount, period_min=period,
            direction=direction, timestamp=now, source=source
        ))

    return oi_alerts, flow_alerts


# ─── Web Scraper ───────────────────────────────────
CHANNELS = [
    "cryptoattack24",
    "cryptoarsenal",
]

# Session with headers to avoid bot detection
_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/125.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
})


def fetch_channel_messages(channel: str, limit: int = 20) -> List[TGMessage]:
    """Fetch latest messages from a public Telegram channel via t.me/s/."""
    url = f"https://t.me/s/{channel}"
    messages = []

    try:
        resp = _session.get(url, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"TG web fetch failed for @{channel}: HTTP {resp.status_code}")
            return []

        html = resp.text

        # Extract post IDs
        post_ids = re.findall(r'data-post="([^"]+)"', html)
        # Extract message texts
        texts = re.findall(
            r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
            html, re.DOTALL
        )
        # Extract timestamps
        timestamps = re.findall(r'<time[^>]*datetime="([^"]*)"', html)

        # Pair them up (texts may be fewer if some messages are media-only)
        for i, raw_text in enumerate(texts[-limit:]):
            clean = clean_html(raw_text)
            if clean:
                idx = len(texts) - limit + i if len(texts) > limit else i
                post_id = post_ids[idx] if idx < len(post_ids) else f"{channel}/{i}"
                ts = timestamps[idx] if idx < len(timestamps) else ""
                messages.append(TGMessage(
                    post_id=post_id,
                    text=clean,
                    timestamp=ts,
                    channel=channel
                ))

    except requests.RequestException as e:
        logger.warning(f"TG web request failed for @{channel}: {e}")
    except Exception as e:
        logger.error(f"TG parse error for @{channel}: {e}")

    return messages


class TGWebParser:
    """Periodically scrapes public TG channels for OI + flow alerts."""

    def __init__(self, channels: List[str] = None):
        self.channels = channels or CHANNELS
        self.seen_posts: set = set()  # Track seen post IDs to avoid duplicates
        self.max_seen = 500           # Max tracked post IDs
        self.last_fetch: Dict[str, float] = {}
        self.fetch_interval = 120     # 2 min between fetches per channel

    def scan(self) -> Tuple[List[TGOIAlert], List[TGFlowAlert], List[TGMessage]]:
        """Fetch all channels and parse alerts. Returns (oi_alerts, flow_alerts, raw_messages)."""
        all_oi = []
        all_flow = []
        all_msgs = []
        now = time.time()

        for channel in self.channels:
            # Rate limit per channel
            if channel in self.last_fetch:
                elapsed = now - self.last_fetch[channel]
                if elapsed < self.fetch_interval:
                    continue

            messages = fetch_channel_messages(channel, limit=20)
            self.last_fetch[channel] = time.time()

            new_count = 0
            for msg in messages:
                if msg.post_id in self.seen_posts:
                    continue

                self.seen_posts.add(msg.post_id)
                new_count += 1
                all_msgs.append(msg)

                oi_alerts, flow_alerts = parse_message_text(msg.text, source=channel)
                all_oi.extend(oi_alerts)
                all_flow.extend(flow_alerts)

            if new_count > 0:
                logger.info(f"📡 TG @{channel}: {new_count} new messages, "
                           f"{len([m for m in messages if m.post_id not in self.seen_posts or True])} total")

            # Trim seen set
            if len(self.seen_posts) > self.max_seen:
                excess = len(self.seen_posts) - self.max_seen // 2
                for _ in range(excess):
                    self.seen_posts.pop()

        return all_oi, all_flow, all_msgs

    def get_latest_messages(self, channel: str = None, limit: int = 20) -> List[TGMessage]:
        """Get latest messages from a specific channel (for display)."""
        channels = [channel] if channel else self.channels
        all_msgs = []
        for ch in channels:
            msgs = fetch_channel_messages(ch, limit=limit)
            all_msgs.extend(msgs)
        return all_msgs
