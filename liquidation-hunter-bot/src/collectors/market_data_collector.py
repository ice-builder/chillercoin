"""
Market Data Collector — Binance Futures REST API polling.

Собирает price, funding rate, open interest, volume каждые 60 секунд.
Все эндпоинты публичные — API ключи не нужны.
"""

from __future__ import annotations

import asyncio
import time
import logging
from typing import Any

import aiohttp

from src.storage.database import Database
from src.storage.models import MarketSnapshot

logger = logging.getLogger(__name__)

# Binance Futures REST base URL
BINANCE_FAPI = "https://fapi.binance.com"


class MarketDataCollector:
    """Асинхронный сборщик рыночных данных с Binance Futures REST API."""

    def __init__(
        self,
        db: Database,
        state: dict[str, Any],
        symbols: list[str],
        poll_interval_seconds: float = 60.0,
    ):
        self.db = db
        self.state = state
        self.symbols = symbols
        self.poll_interval = poll_interval_seconds
        self._shutdown = False
        self._task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None

    def start(self) -> None:
        self._shutdown = False
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        self._shutdown = True
        if self._task:
            self._task.cancel()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _close_session(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _loop(self) -> None:
        """Главный цикл polling — опрашивает Binance каждые poll_interval секунд."""
        logger.info(
            "MarketDataCollector started: symbols=%s, interval=%.0fs",
            self.symbols, self.poll_interval,
        )

        while not self._shutdown:
            try:
                session = await self._ensure_session()

                for symbol in self.symbols:
                    if self._shutdown:
                        break
                    try:
                        snapshot = await self._fetch_snapshot(session, symbol)
                        if snapshot:
                            self.db.insert_market_snapshot(snapshot)
                            logger.debug(
                                "Market snapshot saved: %s price=%.2f OI=%.0f funding=%.6f vol_1h=%.0f",
                                symbol,
                                snapshot.price or 0,
                                snapshot.open_interest or 0,
                                snapshot.funding_rate or 0,
                                snapshot.volume_24h or 0,
                            )
                    except Exception as exc:
                        logger.warning("Failed to fetch snapshot for %s: %s", symbol, exc)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("MarketDataCollector cycle error: %s", exc)

            # Ждём следующий цикл
            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break

        await self._close_session()
        logger.info("MarketDataCollector stopped")

    async def _fetch_snapshot(
        self, session: aiohttp.ClientSession, symbol: str
    ) -> MarketSnapshot | None:
        """Собирает все метрики для одного символа в один MarketSnapshot."""
        now_ms = int(time.time() * 1000)

        # 1. Premium Index — mark price + funding rate
        price: float | None = None
        funding_rate: float | None = None
        try:
            url = f"{BINANCE_FAPI}/fapi/v1/premiumIndex"
            async with session.get(url, params={"symbol": symbol}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = float(data.get("markPrice", 0))
                    funding_rate = float(data.get("lastFundingRate", 0))
                    if price <= 0:
                        price = None
        except Exception as exc:
            logger.debug("premiumIndex error for %s: %s", symbol, exc)

        # 2. Open Interest
        open_interest: float | None = None
        try:
            url = f"{BINANCE_FAPI}/fapi/v1/openInterest"
            async with session.get(url, params={"symbol": symbol}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    open_interest = float(data.get("openInterest", 0))
                    if open_interest <= 0:
                        open_interest = None
        except Exception as exc:
            logger.debug("openInterest error for %s: %s", symbol, exc)

        # 3. Volume — из последней 1h kline
        volume_1h: float | None = None
        try:
            url = f"{BINANCE_FAPI}/fapi/v1/klines"
            params = {"symbol": symbol, "interval": "1h", "limit": 2}
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    klines = await resp.json()
                    if klines and len(klines) >= 2:
                        # Предпоследняя свеча (закрытая) — более точная
                        # [0]=open_time, [5]=volume, [7]=quote_asset_volume
                        volume_1h = float(klines[-2][7])  # Quote volume in USDT
                    elif klines:
                        volume_1h = float(klines[-1][7])
        except Exception as exc:
            logger.debug("klines error for %s: %s", symbol, exc)

        # 4. Long/Short ratio (global accounts)
        long_short_ratio: float | None = None
        try:
            url = f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio"
            params = {"symbol": symbol, "period": "1h", "limit": 1}
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data:
                        long_short_ratio = float(data[0].get("longShortRatio", 0))
        except Exception as exc:
            logger.debug("longShortRatio error for %s: %s", symbol, exc)

        # Если совсем ничего не собрали — пропускаем
        if price is None and open_interest is None:
            return None

        raw = {
            "source": "binance_rest",
            "symbol": symbol,
            "price": price,
            "open_interest": open_interest,
            "funding_rate": funding_rate,
            "volume_1h": volume_1h,
            "long_short_ratio": long_short_ratio,
        }

        return MarketSnapshot(
            exchange="binance",
            symbol=symbol,
            snapshot_time_ms=now_ms,
            price=price,
            open_interest=open_interest,
            funding_rate=funding_rate,
            long_short_ratio=long_short_ratio,
            volume_24h=volume_1h,  # Используем поле volume_24h для хранения 1h volume
            raw_json=raw,
            created_at_ms=now_ms,
        )
