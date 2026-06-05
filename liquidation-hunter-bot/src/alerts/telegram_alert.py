"""
Telegram Alert Sender — отправка уведомлений о ликвидациях и сигналах в Telegram.

Переменные окружения:
    LHB_TG_BOT_TOKEN — токен Telegram бота
    LHB_TG_CHAT_ID — ID чата/группы для уведомлений
    LHB_TG_PROXY_URL — (опционально) URL CF Worker прокси для обхода блокировки TG
    LHB_TG_PROXY_TOKEN — (опционально) auth-токен для CF Worker прокси
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# Минимальный интервал между однотипными сообщениями (секунды)
MIN_ALERT_INTERVAL_SEC = 30


class TelegramAlertSender:
    """Асинхронный отправщик уведомлений в Telegram через Bot API.

    Поддерживает два режима:
    1. Direct — api.telegram.org (по умолчанию)
    2. CF Proxy — через Cloudflare Worker (если задан LHB_TG_PROXY_URL)
    """

    def __init__(self, bot_token: str | None = None, chat_id: str | None = None):
        self.bot_token = bot_token or os.environ.get("LHB_TG_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("LHB_TG_CHAT_ID", "")
        self._enabled = bool(self.bot_token and self.chat_id)
        self._session: aiohttp.ClientSession | None = None
        # Rate limiter: {alert_type: last_sent_ts}
        self._last_sent: dict[str, float] = {}

        # CF Worker Proxy (для VPS где api.telegram.org заблокирован)
        self.proxy_url = os.environ.get("LHB_TG_PROXY_URL", "").rstrip("/")
        self.proxy_token = os.environ.get("LHB_TG_PROXY_TOKEN", "")
        self._use_proxy = bool(self.proxy_url and self.proxy_token)

        if not self._enabled:
            logger.warning(
                "TelegramAlertSender disabled: LHB_TG_BOT_TOKEN or LHB_TG_CHAT_ID not set"
            )
        elif self._use_proxy:
            logger.info("TelegramAlertSender: using CF proxy at %s", self.proxy_url)
        else:
            logger.info("TelegramAlertSender: direct mode (api.telegram.org)")

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _should_send(self, alert_type: str) -> bool:
        """Rate limiter — не чаще чем раз в MIN_ALERT_INTERVAL_SEC секунд."""
        now = time.time()
        last = self._last_sent.get(alert_type, 0.0)
        if now - last < MIN_ALERT_INTERVAL_SEC:
            return False
        self._last_sent[alert_type] = now
        return True

    def _build_url(self, method: str) -> str:
        """Строит URL для API-запроса (прямой или через прокси)."""
        if self._use_proxy:
            # CF Worker: POST https://proxy.workers.dev/bot<token>/sendMessage?token=<auth>
            return f"{self.proxy_url}/bot{self.bot_token}/{method}?token={self.proxy_token}"
        else:
            return f"https://api.telegram.org/bot{self.bot_token}/{method}"

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Отправляет произвольное сообщение в Telegram (прямо или через прокси)."""
        if not self._enabled:
            return False

        try:
            session = await self._ensure_session()
            url = self._build_url("sendMessage")
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    return True
                else:
                    body = await resp.text()
                    logger.warning("TG send failed: %d %s", resp.status, body[:200])
                    return False
        except Exception as exc:
            logger.error("TG send error: %s", exc)
            return False

    # ──────────────────────────────────────────────────────────────
    #  Готовые шаблоны алертов
    # ──────────────────────────────────────────────────────────────

    async def alert_signal(self, signal: dict[str, Any]) -> bool:
        """Алерт о новом торговом сигнале."""
        sig_type = signal.get("signal_type", "UNKNOWN")
        if not self._should_send(f"signal_{sig_type}_{signal.get('symbol', '')}"):
            return False

        emoji = {
            "PAPER_LONG_SETUP": "🟢",
            "PAPER_SHORT_SETUP": "🔴",
            "PAPER_CASCADE_RISK": "⚠️",
            "PAPER_REVERSION_WATCH": "👀",
            "NO_TRADE": "🚫",
        }.get(sig_type, "📊")

        direction = signal.get("direction", "UNKNOWN")
        symbol = signal.get("symbol", "???")
        confidence = signal.get("confidence", 0)
        score = signal.get("stress_score", 0)
        hypothesis = signal.get("hypothesis", "")

        text = (
            f"{emoji} <b>{sig_type}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📌 Символ: <b>{symbol}</b>\n"
            f"🎯 Направление: <b>{direction}</b>\n"
            f"📈 Стресс: <b>{score:.1f}</b>\n"
            f"🎲 Уверенность: <b>{confidence:.0f}%</b>\n"
            f"📝 {hypothesis[:200]}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🤖 Liquidation Hunter | Paper"
        )
        return await self.send_message(text)

    async def alert_trade_open(self, trade: dict[str, Any]) -> bool:
        """Алерт об открытии paper trade."""
        symbol = trade.get("symbol", "???")
        side = trade.get("side", "???")
        entry = trade.get("price", 0)
        sl = trade.get("stop_loss")
        tp = trade.get("take_profit")

        if not self._should_send(f"trade_open_{symbol}"):
            return False

        emoji = "🟢" if side == "LONG" else "🔴"
        lines = [
            f"{emoji} <b>PAPER TRADE OPENED</b>",
            f"━━━━━━━━━━━━━━━",
            f"📌 {symbol} | <b>{side}</b>",
            f"💰 Вход: <b>${entry:,.2f}</b>",
        ]
        if sl:
            lines.append(f"🛡 SL: <b>${sl:,.2f}</b>")
        if tp:
            lines.append(f"🎯 TP: <b>${tp:,.2f}</b>")
        lines.append(f"━━━━━━━━━━━━━━━")
        lines.append(f"🤖 Liquidation Hunter | Paper")
        text = "\n".join(lines)
        return await self.send_message(text)

    async def alert_trade_close(self, trade: dict[str, Any]) -> bool:
        """Алерт о закрытии paper trade."""
        symbol = trade.get("symbol", "???")
        side = trade.get("side", "???")
        pnl = trade.get("net_pnl_usdt", 0)
        reason = trade.get("close_reason", "UNKNOWN")

        if not self._should_send(f"trade_close_{symbol}"):
            return False

        emoji = "✅" if pnl > 0 else "❌"
        text = (
            f"{emoji} <b>PAPER TRADE CLOSED</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📌 {symbol} | {side}\n"
            f"💰 PnL: <b>${pnl:+,.2f}</b>\n"
            f"📝 Причина: <b>{reason}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🤖 Liquidation Hunter | Paper"
        )
        return await self.send_message(text)

    async def alert_stress_extreme(self, symbol: str, score: float, level: str) -> bool:
        """Алерт об экстремальном уровне стресса."""
        if not self._should_send(f"stress_{symbol}"):
            return False

        text = (
            f"🔥 <b>EXTREME STRESS</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📌 {symbol}\n"
            f"📈 Score: <b>{score:.1f}</b> ({level})\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🤖 Liquidation Hunter"
        )
        return await self.send_message(text)

    async def alert_kill_switch(self, reason: str = "unknown") -> bool:
        """Алерт о срабатывании Kill Switch."""
        text = (
            f"🚨 <b>KILL SWITCH ACTIVATED</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"⛔️ Все позиции закрыты\n"
            f"📝 Причина: {reason}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🤖 Liquidation Hunter"
        )
        return await self.send_message(text)

    async def alert_source_stale(self, source: str) -> bool:
        """Алерт о shadow disconnect источника (макс 1 раз в час)."""
        now = time.time()
        key = f"stale_{source}"
        last = self._last_sent.get(key, 0.0)
        if now - last < 3600:  # 1 час между stale алертами
            return False
        self._last_sent[key] = now

        text = (
            f"⚠️ <b>SOURCE STALE</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📡 {source.upper()} — connected but no events > 10 min\n"
            f"🔧 Possible shadow disconnect\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🤖 Liquidation Hunter"
        )
        return await self.send_message(text)
