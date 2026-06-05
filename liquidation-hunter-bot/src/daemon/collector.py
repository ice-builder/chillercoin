from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import time
from typing import Any

import websockets

from src.config.settings import Settings
from src.parsers.liquidation_event import parse_binance_liquidation, parse_bybit_liquidation
from src.storage.database import Database
from src.storage.models import AuditLogRecord


class EventTracker:
    """Вспомогательный класс для расчета скользящих окон ликвидаций в памяти."""

    def __init__(self, state: dict[str, Any], symbols: list[str]):
        self.state = state
        self.symbols = symbols
        # Список событий за последние 24 часа для вычисления метрик
        self.history: list[dict[str, Any]] = []

    def add_event(self, source: str, event: Any) -> None:
        # Обновляем инфо об источниках в state
        dt = datetime.fromtimestamp(event.event_time_ms / 1000.0, tz=timezone.utc)
        self.state["sources"][source]["last_event_ts"] = dt.isoformat().replace("+00:00", "Z")
        self.state["sources"][source]["last_event_ms"] = event.event_time_ms

        # Добавляем в список последних событий (максимум 20)
        self.state["last_events"].insert(0, event.to_dict())
        if len(self.state["last_events"]) > 20:
            self.state["last_events"] = self.state["last_events"][:20]

        # Добавляем в историю скользящего окна
        self.history.append({
            "timestamp_ms": event.event_time_ms,
            "source": source,
            "symbol": event.symbol,
            "liq_side": event.liquidated_position_side,
            "notional": event.notional_usdt,
        })

        # Пересчитываем статистику
        self.update_stats()

    def update_stats(self) -> None:
        now_ms = int(time.time() * 1000)
        cutoff_24h = now_ms - (24 * 3600 * 1000)

        # Очищаем события старше 24 часов
        self.history = [item for item in self.history if item["timestamp_ms"] >= cutoff_24h]

        cutoff_1h = now_ms - (3600 * 1000)
        cutoff_4h = now_ms - (4 * 3600 * 1000)

        # Считаем количество событий за последний час по источникам
        binance_1h = sum(
            1
            for item in self.history
            if item["source"] == "binance" and item["timestamp_ms"] >= cutoff_1h
        )
        bybit_1h = sum(
            1
            for item in self.history
            if item["source"] == "bybit" and item["timestamp_ms"] >= cutoff_1h
        )

        self.state["sources"]["binance"]["events_1h"] = binance_1h
        self.state["sources"]["bybit"]["events_1h"] = bybit_1h

        # Обновляем показатели по каждому символу
        for symbol in self.symbols:
            sym_items = [item for item in self.history if item["symbol"] == symbol]

            liq_1h = sum(item["notional"] for item in sym_items if item["timestamp_ms"] >= cutoff_1h)
            liq_4h = sum(item["notional"] for item in sym_items if item["timestamp_ms"] >= cutoff_4h)
            liq_24h = sum(item["notional"] for item in sym_items)

            largest_24h = max([item["notional"] for item in sym_items]) if sym_items else 0.0

            long_liq_sum = sum(item["notional"] for item in sym_items if item["liq_side"] == "LONG")
            short_liq_sum = sum(
                item["notional"] for item in sym_items if item["liq_side"] == "SHORT"
            )
            total = long_liq_sum + short_liq_sum

            if total > 0:
                long_liq_ratio = long_liq_sum / total
                short_liq_ratio = short_liq_sum / total
            else:
                long_liq_ratio = 0.0
                short_liq_ratio = 0.0

            if symbol in self.state["symbols"]:
                self.state["symbols"][symbol]["liquidations_1h_usdt"] = round(liq_1h, 4)
                self.state["symbols"][symbol]["liquidations_4h_usdt"] = round(liq_4h, 4)
                self.state["symbols"][symbol]["liquidations_24h_usdt"] = round(liq_24h, 4)
                self.state["symbols"][symbol]["long_liq_ratio"] = round(long_liq_ratio, 4)
                self.state["symbols"][symbol]["short_liq_ratio"] = round(short_liq_ratio, 4)
                self.state["symbols"][symbol]["largest_event_24h_usdt"] = round(largest_24h, 4)

    def restore_from_db(self, db: Database) -> None:
        """Восстанавливает скользящую историю из SQLite при рестарте бота.

        Загружает ликвидации за последние 24 часа, чтобы EventTracker
        имел полный контекст для расчёта acceleration, ratio и volume.
        """
        import logging
        logger = logging.getLogger(__name__)

        now_ms = int(time.time() * 1000)
        since_24h = now_ms - 24 * 3600 * 1000
        total_restored = 0

        for symbol in self.symbols:
            records = db.get_liquidations(symbol=symbol, since_ms=since_24h, limit=100000)
            for r in records:
                self.history.append({
                    "timestamp_ms": r.event_time_ms,
                    "source": r.exchange,
                    "symbol": r.symbol,
                    "liq_side": r.liquidated_position_side,
                    "notional": r.notional_usdt,
                })
                total_restored += 1

        if total_restored > 0:
            self.update_stats()
            logger.info(
                "EventTracker restored %d events from DB (last 24h) for %s",
                total_restored, self.symbols,
            )

    def check_stale_sources(self) -> None:
        """Проверяет shadow disconnect — WS 'connected' но нет событий > 10 мин."""
        now_ms = int(time.time() * 1000)
        stale_threshold_ms = 600_000  # 10 минут

        for source in ["binance", "bybit"]:
            s = self.state["sources"].get(source, {})
            if s.get("ws_status") == "connected" and s.get("events_1h", 0) == 0:
                last_event_ms = s.get("last_event_ms")
                if last_event_ms and (now_ms - last_event_ms) > stale_threshold_ms:
                    s["ws_status"] = "stale"
                elif last_event_ms is None:
                    # Если никогда не получали событий и прошло > 10 мин от старта
                    start_ms = self.state.get("start_time_ms", now_ms)
                    if (now_ms - start_ms) > stale_threshold_ms:
                        s["ws_status"] = "stale"


class BinanceCollector:
    """Асинхронный сборщик ликвидаций с Binance Futures WS (публичный поток)."""

    def __init__(self, settings: Settings, db: Database, state: dict[str, Any], tracker: EventTracker):
        self.settings = settings
        self.db = db
        self.state = state
        self.tracker = tracker
        self._shutdown = False
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._shutdown = False
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        self._shutdown = True
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        symbols = [s.lower() for s in self.settings.symbols]
        streams = "/".join(f"{s}@forceOrder" for s in symbols)
        url = f"wss://fstream.binance.com/ws/{streams}"

        attempt = 0
        while not self._shutdown:
            if self.state["risk"]["kill_switch"]:
                self.state["sources"]["binance"]["ws_status"] = "disconnected"
                await asyncio.sleep(1.0)
                continue

            self.state["sources"]["binance"]["ws_status"] = (
                "reconnecting" if attempt > 0 else "starting"
            )
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    self.state["sources"]["binance"]["ws_status"] = "connected"
                    attempt = 0  # Сброс попыток

                    while not self._shutdown:
                        if self.state["risk"]["kill_switch"]:
                            break

                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                            data = json.loads(raw)

                            # Разворачиваем combined stream, если нужно
                            if "stream" in data and "data" in data:
                                data = data["data"]

                            event = parse_binance_liquidation(data)
                            if event:
                                # Сохранение в БД
                                try:
                                    self.db.insert_liquidation_event(event)
                                except Exception as exc:
                                    try:
                                        self.db.insert_audit_log(
                                            AuditLogRecord(
                                                event_time_ms=int(time.time() * 1000),
                                                event_type="DB_ERROR",
                                                severity="ERROR",
                                                message=f"Failed to insert Binance liquidation: {exc}",
                                                payload_json={"error": str(exc)},
                                                created_at_ms=int(time.time() * 1000),
                                            )
                                        )
                                    except Exception:
                                        pass

                                self.tracker.add_event("binance", event)

                        except asyncio.TimeoutError:
                            continue
                        except websockets.ConnectionClosed:
                            self.state["sources"]["binance"]["errors"] += 1
                            break

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.state["sources"]["binance"]["errors"] += 1
                try:
                    self.db.insert_audit_log(
                        AuditLogRecord(
                            event_time_ms=int(time.time() * 1000),
                            event_type="WS_ERROR",
                            severity="ERROR",
                            message=f"Binance WS error: {exc}",
                            payload_json={"error": str(exc)},
                            created_at_ms=int(time.time() * 1000),
                        )
                    )
                except Exception:
                    pass

            if not self._shutdown:
                attempt += 1
                delay = min(30.0, self.settings.ws_reconnect_delay_base**attempt)
                self.state["sources"]["binance"]["ws_status"] = "reconnecting"
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    break


class BybitCollector:
    """Асинхронный сборщик ликвидаций с Bybit V5 WS (публичный поток)."""

    def __init__(self, settings: Settings, db: Database, state: dict[str, Any], tracker: EventTracker):
        self.settings = settings
        self.db = db
        self.state = state
        self.tracker = tracker
        self._shutdown = False
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._shutdown = False
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        self._shutdown = True
        if self._task:
            self._task.cancel()

    async def _heartbeat(self, ws: Any) -> None:
        while not self._shutdown and not self.state["risk"]["kill_switch"]:
            try:
                await ws.send(json.dumps({"op": "ping"}))
                await asyncio.sleep(20.0)
            except Exception:
                break

    async def _loop(self) -> None:
        url = "wss://stream.bybit.com/v5/public/linear"
        attempt = 0

        while not self._shutdown:
            if self.state["risk"]["kill_switch"]:
                self.state["sources"]["bybit"]["ws_status"] = "disconnected"
                await asyncio.sleep(1.0)
                continue

            self.state["sources"]["bybit"]["ws_status"] = (
                "reconnecting" if attempt > 0 else "starting"
            )
            try:
                async with websockets.connect(url, ping_interval=None) as ws:
                    # Подписка
                    topics = [f"allLiquidation.{s}" for s in self.settings.symbols]
                    sub_msg = {"op": "subscribe", "args": topics}
                    await ws.send(json.dumps(sub_msg))

                    # Ожидание подтверждения подписки (неблокирующее)
                    try:
                        await asyncio.wait_for(ws.recv(), timeout=5.0)
                    except asyncio.TimeoutError:
                        pass

                    self.state["sources"]["bybit"]["ws_status"] = "connected"
                    attempt = 0

                    # Фоновый heartbeat
                    hb_task = asyncio.create_task(self._heartbeat(ws))

                    while not self._shutdown:
                        if self.state["risk"]["kill_switch"]:
                            break

                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                            data = json.loads(raw)

                            if data.get("op") in ("pong", "subscribe"):
                                continue

                            events = parse_bybit_liquidation(data)
                            for event in events:
                                # Сохранение в БД
                                try:
                                    self.db.insert_liquidation_event(event)
                                except Exception as exc:
                                    try:
                                        self.db.insert_audit_log(
                                            AuditLogRecord(
                                                event_time_ms=int(time.time() * 1000),
                                                event_type="DB_ERROR",
                                                severity="ERROR",
                                                message=f"Failed to insert Bybit liquidation: {exc}",
                                                payload_json={"error": str(exc)},
                                                created_at_ms=int(time.time() * 1000),
                                            )
                                        )
                                    except Exception:
                                        pass

                                self.tracker.add_event("bybit", event)

                        except asyncio.TimeoutError:
                            continue
                        except websockets.ConnectionClosed:
                            self.state["sources"]["bybit"]["errors"] += 1
                            break

                    hb_task.cancel()
                    try:
                        await hb_task
                    except asyncio.CancelledError:
                        pass

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.state["sources"]["bybit"]["errors"] += 1
                try:
                    self.db.insert_audit_log(
                        AuditLogRecord(
                            event_time_ms=int(time.time() * 1000),
                            event_type="WS_ERROR",
                            severity="ERROR",
                            message=f"Bybit WS error: {exc}",
                            payload_json={"error": str(exc)},
                            created_at_ms=int(time.time() * 1000),
                        )
                    )
                except Exception:
                    pass

            if not self._shutdown:
                attempt += 1
                delay = min(30.0, self.settings.ws_reconnect_delay_base**attempt)
                self.state["sources"]["bybit"]["ws_status"] = "reconnecting"
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    break
