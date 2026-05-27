"""
OneProp Signal Hub — Telegram Poster v2
Single updatable post per signal (editMessageText)

Post lifecycle:
  1. Signal opened → post with placeholders for checkpoints
  2. +15m checkpoint → EDIT post, fill 15m line
  3. +1h checkpoint → EDIT post, fill 1h line
  4. +4h checkpoint → EDIT post, fill 4h line
  5. Trade closed → EDIT post, fill final P&L + update status

Features:
  • Score tiers: 🔥 90+ / ⚡ 80-89 / 📡 70-79 / 📊 <70
  • Win/loss streak: 🟢🟢🔴🟢🟢 (4 из 5)
  • Model rating: 67% WR за 30 дней (142 сделки)
  • Daily summary (21:00 MSK) — separate post
"""
import asyncio
import logging
import json
from datetime import datetime, timezone, timedelta
import requests
from config import TG_TOKEN, TG_PUBLIC_CHANNEL, TG_PRIVATE_CHAT_ID, TG_PRIVATE_THREAD_SIGNALS

logger = logging.getLogger("TGPoster")

MSK = timezone(timedelta(hours=3))

# ─── Labels ───────────────────────────────────────────────
DIRECTION_EMOJI = {"long": "📈 LONG", "short": "📉 SHORT"}
DIRECTION_ICON = {"long": "🟢", "short": "🔴"}

EXIT_REASON_LABELS = {
    "take_profit": "🎯 Take Profit",
    "iie_trailing_stop": "📊 Trailing Stop",
    "iie_stop_loss": "🛑 Stop Loss",
    "breakeven": "🔄 Безубыток",
    "catastrophic_stop": "🚨 Аварийный стоп",
}

INTERVAL_LABELS = {
    "15m": "15 мин",
    "1h": "1 час",
    "4h": "4 часа",
}


# ─── Score Tier ───────────────────────────────────────────

def _score_tier(strength: float) -> tuple:
    """Returns (emoji, bar) based on AI score (0-1 or 0-100)."""
    # Normalize: if <2 assume 0-1 range, else 0-100
    score = strength * 100 if strength <= 1 else strength
    score = int(min(score, 100))

    filled = round(score / 10)
    empty = 10 - filled
    bar = "🟩" * filled + "⬜" * empty

    if score >= 90:
        return "🔥", bar, score
    elif score >= 80:
        return "⚡", bar, score
    elif score >= 70:
        return "📡", bar, score
    else:
        return "📊", bar, score


def _streak_line(streak: list) -> str:
    """Build streak visual: 🟢🟢🔴🟢🟢 (4 из 5)"""
    if not streak:
        return ""
    dots = "".join("🟢" if s["win"] else "🔴" for s in streak)
    wins = sum(1 for s in streak if s["win"])
    total = len(streak)
    return f"🏆 Серия: {dots} ({wins} из {total})"


def _model_line(rating: dict) -> str:
    """Build model rating line: 📊 Модель: 67% WR за 30 дней (142 сделки)"""
    if not rating or rating.get("total", 0) == 0:
        return "📊 Модель: набираем данные..."
    wr = rating["wr"]
    total = rating["total"]
    avg = rating.get("avg_pnl", 0)
    return f"📊 Модель: {wr:.0f}% WR за 30 дней ({total} сделок, avg {avg:+.2f}%)"


# ─── Build Signal Post Text ──────────────────────────────

def build_signal_text(
    signal: dict,
    checkpoints: dict = None,
    close_data: dict = None,
    streak: list = None,
    rating: dict = None,
) -> str:
    """Build the full updatable signal post text.

    Args:
        signal: signal dict from DB
        checkpoints: {"15m": pnl, "1h": pnl, "4h": pnl} or None for pending
        close_data: {"pnl_pct": float, "exit_price": float, "exit_reason": str} or None
        streak: list of {pnl, win} for streak display
        rating: {wr, total, avg_pnl} for model rating
    """
    checkpoints = checkpoints or {}

    # Score tier
    strength = signal.get("strength", 0)
    tier_emoji, score_bar, score_int = _score_tier(strength)

    # Direction
    direction = signal.get("direction", "long")
    dir_emoji = DIRECTION_EMOJI.get(direction, "📊")

    # Meta for stop price
    meta = signal.get("metadata_json", "{}")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}

    symbol = signal.get("symbol", "???")
    entry = signal.get("entry_target") or signal.get("price_at_signal", 0)
    target = signal.get("exit_target", 0)
    stop = meta.get("stop_price", "—")

    # ─── Header ───
    lines = [
        f"{tier_emoji} *СИГНАЛ ДНЯ: {symbol} {direction.upper()}*",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"{dir_emoji}",
        f"💰 Вход: `{entry}`",
        f"🎯 Цель: `{target}`" if target else "",
        f"🛑 Стоп: `{stop}`",
        f"🧠 AI Score: {score_int}/100 `{score_bar}`",
        "",
    ]
    # Remove empty lines from missing target
    lines = [l for l in lines if l is not None]

    # ─── Status ───
    if close_data:
        pnl = close_data.get("pnl_pct", 0)
        reason = EXIT_REASON_LABELS.get(
            close_data.get("exit_reason", ""), close_data.get("exit_reason", ""))
        if pnl > 0:
            lines.append(f"✅ Статус: *ЗАКРЫТА {pnl:+.2f}%*")
        else:
            lines.append(f"❌ Статус: *ЗАКРЫТА {pnl:+.2f}%*")
    else:
        lines.append("⏳ Статус: *В ИГРЕ*")
    lines.append("")

    # ─── Checkpoints ───
    for interval, label in [("15m", "15 мин"), ("1h", "1 час"), ("4h", "4 часа")]:
        pnl = checkpoints.get(interval)
        if pnl is not None:
            cp_emoji = "📈" if pnl > 0 else "📉"
            lines.append(f"◷ {label}:  {cp_emoji} *{pnl:+.2f}%*")
        else:
            lines.append(f"◷ {label}:  ⏳ ожидание...")

    # Final
    if close_data:
        pnl = close_data.get("pnl_pct", 0)
        reason = EXIT_REASON_LABELS.get(
            close_data.get("exit_reason", ""), close_data.get("exit_reason", "—"))
        final_emoji = "✅" if pnl > 0 else "❌"
        lines.append(f"◷ Финал:  {final_emoji} *{pnl:+.2f}%* ({reason})")
    else:
        lines.append("◷ Финал:  ⏳ ожидание...")

    lines.append("")

    # ─── Streak ───
    streak_text = _streak_line(streak or [])
    if streak_text:
        lines.append(streak_text)

    # ─── Model Rating ───
    lines.append(_model_line(rating or {}))

    lines.extend([
        "",
        "📊 [Все результаты](https://oneprop.ru/results)",
    ])

    return "\n".join(lines)


class TelegramPoster:
    def __init__(self):
        self.token = TG_TOKEN
        self.public_channel = TG_PUBLIC_CHANNEL
        self.private_chat_id = TG_PRIVATE_CHAT_ID
        self.private_thread = TG_PRIVATE_THREAD_SIGNALS
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.enabled = bool(self.token)

        if self.enabled:
            logger.info(
                f"📱 TG Poster ON | "
                f"Public: {self.public_channel} | "
                f"Private: {self.private_chat_id}:{self.private_thread}"
            )

    def _send(self, chat_id: str, text: str, thread_id: int = None,
              disable_preview: bool = True) -> int:
        """Send a message. Returns message_id or 0."""
        if not self.enabled:
            return 0
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": disable_preview,
        }
        if thread_id:
            payload["message_thread_id"] = thread_id
        try:
            resp = requests.post(
                f"{self.base_url}/sendMessage", json=payload, timeout=15
            )
            data = resp.json()
            if data.get("ok"):
                return data["result"]["message_id"]
            else:
                logger.warning(f"TG send error: {data}")
        except Exception as e:
            logger.warning(f"TG send error: {e}")
        return 0

    def _edit(self, chat_id: str, message_id: int, text: str,
              disable_preview: bool = True) -> bool:
        """Edit an existing message. Returns True on success."""
        if not self.enabled or not message_id:
            return False
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": disable_preview,
        }
        try:
            resp = requests.post(
                f"{self.base_url}/editMessageText", json=payload, timeout=15
            )
            data = resp.json()
            if data.get("ok"):
                return True
            else:
                # "message is not modified" is OK — same content
                desc = data.get("description", "")
                if "not modified" in desc:
                    return True
                logger.warning(f"TG edit error: {data}")
        except Exception as e:
            logger.warning(f"TG edit error: {e}")
        return False

    # ─── Signal Opened: initial post with placeholders ────

    def post_signal_opened(self, signal: dict,
                           streak: list = None,
                           rating: dict = None) -> int:
        """Post new signal to public channel.
        Returns message_id for future edits."""
        if not self.public_channel:
            return 0

        text = build_signal_text(
            signal=signal,
            checkpoints={},
            close_data=None,
            streak=streak,
            rating=rating,
        )
        msg_id = self._send(self.public_channel, text)
        if msg_id:
            logger.info(f"📢 Signal #{signal.get('id')} posted (msg_id={msg_id})")
        return msg_id

    # ─── Checkpoint: EDIT the original post ───────────────

    def update_signal_post(self, message_id: int, signal: dict,
                           checkpoints: dict,
                           close_data: dict = None,
                           streak: list = None,
                           rating: dict = None) -> bool:
        """Update the signal post with new checkpoint data."""
        if not self.public_channel or not message_id:
            return False

        text = build_signal_text(
            signal=signal,
            checkpoints=checkpoints,
            close_data=close_data,
            streak=streak,
            rating=rating,
        )
        ok = self._edit(self.public_channel, message_id, text)
        if ok:
            what = "close" if close_data else "checkpoint"
            logger.info(f"✏️ Signal #{signal.get('id')} post updated ({what})")
        return ok

    # ─── Private group post (unchanged, always separate) ──

    def post_signal_private(self, signal: dict) -> int:
        """Post signal to private group for internal monitoring."""
        if not self.private_chat_id:
            return 0

        direction = DIRECTION_EMOJI.get(signal.get("direction", ""), "📊")
        meta = signal.get("metadata_json", "{}")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}

        lines = [
            f"🔍 *СИГНАЛ #{signal['id']}* | {signal['symbol']}",
            "",
            f"{direction}",
            f"💰 Вход: `{signal.get('entry_target') or signal.get('price_at_signal')}`",
            f"🎯 Целевой выход: `{signal.get('exit_target', '—')}`",
            f"🛡 Стоп: `{meta.get('stop_price', '—')}`",
            f"💪 Сила: *{signal.get('strength', 0):.0%}*",
            "",
            f"📋 _{signal.get('description', 'Soldier AI')}_",
            "",
            f"🕐 Автотрекинг: 15м / 1ч / 4ч",
            f"📊 → oneprop.ru/results",
        ]

        text = "\n".join(lines)
        msg_id = self._send(self.private_chat_id, text, self.private_thread)
        if msg_id:
            logger.info(f"🔒 Private signal post: {signal['symbol']}")
        return msg_id

    def post_trade_closed_private(self, close_data: dict) -> int:
        """Post trade close to private group."""
        if not self.private_chat_id:
            return 0

        pnl = close_data.get("pnl_pct", 0)
        emoji = "✅ WIN" if pnl > 0 else "❌ LOSS"

        text = (
            f"📊 *{emoji}* | {close_data.get('symbol', '')}\n\n"
            f"Entry: `{close_data.get('entry_price')}` → `{close_data.get('exit_price')}`\n"
            f"P&L: *{pnl:+.2f}%*\n"
            f"Причина: {close_data.get('exit_reason', '—')}"
        )
        return self._send(self.private_chat_id, text, self.private_thread)

    # ─── Daily Summary (21:00 MSK) — separate post ───────

    def post_daily_summary(self, todays_signal: dict = None,
                           stats: dict = None,
                           streak: list = None,
                           rating: dict = None) -> int:
        """Post daily summary at 21:00 MSK as a separate post."""
        if not self.public_channel:
            return 0

        now = datetime.now(MSK)
        lines = [
            f"🌙 *ИТОГИ ДНЯ* | {now.strftime('%d.%m.%Y')}",
            "",
        ]

        if todays_signal:
            dir_icon = DIRECTION_ICON.get(todays_signal.get("direction", ""), "⚪")
            symbol = todays_signal.get("symbol", "—")
            lines.append(f"📊 Сигнал: {dir_icon} {symbol} {todays_signal.get('direction', '').upper()}")

            # Checkpoints
            for interval, label in [("15m", "15 мин"), ("1h", "1 час"), ("4h", "4 часа")]:
                pnl_key = f"pnl_{interval}"
                pnl = todays_signal.get(pnl_key)
                if pnl is not None:
                    cp_emoji = "📈" if pnl > 0 else "📉"
                    lines.append(f"◷ {label}: {cp_emoji} *{pnl:+.2f}%*")
                else:
                    lines.append(f"◷ {label}: ⏳")

            # Trade result
            if todays_signal.get("closed_at") and todays_signal.get("exit_pnl_pct"):
                pnl = todays_signal["exit_pnl_pct"]
                reason = EXIT_REASON_LABELS.get(todays_signal.get("exit_reason", ""), "")
                emoji = "✅" if pnl > 0 else "❌"
                lines.append(f"{emoji} Финал: *{pnl:+.2f}%* ({reason})")
            else:
                lines.append("⏳ Сделка ещё открыта")
        else:
            lines.append("📊 Сигналов сегодня не было. Рынок спокоен.")

        lines.append("")

        # Streak
        streak_text = _streak_line(streak or [])
        if streak_text:
            lines.append(streak_text)

        # Model rating
        lines.append(_model_line(rating or {}))

        # Stats
        if stats:
            trades = stats.get("trades", {})
            total = trades.get("total", 0)
            if total > 0:
                lines.extend([
                    "",
                    f"📈 *Статистика ({stats.get('days', 30)} дней):*",
                    f"Сделок: {total} | Avg P&L: {trades.get('avg_pnl', 0):+.2f}%",
                ])

        lines.extend([
            "",
            "📊 [Все результаты](https://oneprop.ru/results)",
            "",
            "#oneprop #итоги",
        ])

        text = "\n".join(lines)
        msg_id = self._send(self.public_channel, text)
        if msg_id:
            logger.info(f"📢 Daily summary posted at {now.strftime('%H:%M')} MSK")
        return msg_id


# ─── Daily summary scheduler (21:00 MSK) ─────────────────

async def daily_summary_scheduler(poster: TelegramPoster, get_stats_fn,
                                  get_todays_signal_fn, get_streak_fn=None,
                                  get_model_rating_fn=None):
    """Run forever, send daily summary at 21:00 MSK."""
    logger.info("⏰ Daily summary scheduler started: 21:00 MSK")
    last_summary_date = ""

    while True:
        now = datetime.now(MSK)
        today_str = now.strftime("%Y-%m-%d")

        # Post at 21:00 MSK, once per day
        if now.hour == 21 and today_str != last_summary_date:
            try:
                stats = await get_stats_fn(days=30)
                todays_signal = await get_todays_signal_fn()
                streak = await get_streak_fn() if get_streak_fn else []
                rating = await get_model_rating_fn() if get_model_rating_fn else {}
                poster.post_daily_summary(
                    todays_signal=todays_signal, stats=stats,
                    streak=streak, rating=rating,
                )
                last_summary_date = today_str
                logger.info(f"⏰ Daily summary sent at {now.strftime('%H:%M')} MSK")
            except Exception as e:
                logger.warning(f"Daily summary error: {e}")

        await asyncio.sleep(60)
