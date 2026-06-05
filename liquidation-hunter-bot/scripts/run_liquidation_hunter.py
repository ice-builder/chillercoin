#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Добавляем корень проекта в путь поиска
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import Settings
from src.daemon.collector import BinanceCollector, BybitCollector, EventTracker
from src.scoring.stress_score import StressScoreEngine, compute_and_store_stress_score
from src.signals.signal_engine import SignalEngine, SignalDecision, compute_and_store_signal
from src.state.state_writer import StateWriter, create_default_state
from src.storage.database import Database
from src.paper import PaperSimulator, PaperConfig
from src.collectors.market_data_collector import MarketDataCollector
from src.alerts.telegram_alert import TelegramAlertSender


async def main_async(args: argparse.Namespace) -> None:
    # 1. Загрузка настроек и оверрайд из аргументов командной строки
    settings = Settings.load()
    if args.symbols:
        settings.symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.db_path:
        settings.db_path = Path(args.db_path)
    if args.state_path:
        settings.state_path = Path(args.state_path)
    if args.api_port:
        settings.api_port = args.api_port

    # Проверка хоста API по соображениям безопасности
    if settings.api_host != "127.0.0.1":
        raise ValueError("API host must be 127.0.0.1 for security reasons.")

    print(f"🚀 Запуск Liquidation Hunter Bot (Mode: {settings.mode})")
    print(f"   Символы: {', '.join(settings.symbols)}")
    print(f"   БД:      {settings.db_path}")
    print(f"   State:   {settings.state_path}")
    print(f"   API:     {settings.api_host}:{settings.api_port} (Localhost only)")

    # 2. Инициализация базы данных
    db = Database(settings.db_path)

    # 3. Инициализация состояния
    state = create_default_state(settings)
    writer = StateWriter(settings.state_path)

    # 4. Проверка Kill Switch на старте
    is_kill = settings.kill_switch_path.exists()
    if is_kill:
        print("⚠️ Обнаружен активный файл KILL_SWITCH! Сборщики отключены.")
        state["status"] = "kill_switch"
        state["risk"]["kill_switch"] = True
        state["risk"]["status"] = "KILL_SWITCH_ACTIVE"
    else:
        state["status"] = "online"

    # Записываем первое состояние
    writer.write(state)

    # 5. Инициализация трекера, сборщиков и скоринг-движка
    tracker = EventTracker(state, settings.symbols)

    # Восстанавливаем историю из БД при рестарте
    tracker.restore_from_db(db)
    state["start_time_ms"] = int(time.time() * 1000)

    binance = BinanceCollector(settings, db, state, tracker)
    bybit = BybitCollector(settings, db, state, tracker)
    scoring_engine = StressScoreEngine(db)
    signal_engine = SignalEngine(db)
    last_decisions: dict[str, SignalDecision] = {}
    
    paper_config = PaperConfig(
        bot_name=settings.bot_name,
        starting_balance_usdt=10000.0,
        fee_rate=0.0004,
    )
    paper_simulator = PaperSimulator(db, paper_config)

    # Market Data Collector — REST polling price, OI, funding, volume
    market_collector = MarketDataCollector(
        db=db, state=state, symbols=settings.symbols, poll_interval_seconds=60.0,
    )

    # Telegram Alert Sender
    tg_alert = TelegramAlertSender()
    if tg_alert.enabled:
        print(f"✅ Telegram алерты включены (chat_id={tg_alert.chat_id})")

    if not is_kill:
        binance.start()
        bybit.start()
        market_collector.start()
        print("✅ MarketDataCollector запущен (polling каждые 60s)")

    # 6. Запуск FastAPI
    server = None
    if not args.no_api:
        import uvicorn
        from src.daemon.app import create_app

        app = create_app(settings, db, state)
        config = uvicorn.Config(
            app, host=settings.api_host, port=settings.api_port, log_level="warning"
        )
        server = uvicorn.Server(config)
        # Запуск uvicorn в фоновой задаче того же asyncio event loop
        asyncio.create_task(server.serve())
        print(f"✅ API запущен на http://{settings.api_host}:{settings.api_port}")

    # 7. Основной бесконечный цикл демона
    start_time = time.monotonic()
    loop = asyncio.get_running_loop()

    # Обработка сигналов выключения
    def handle_exit_signal() -> None:
        print("\n⛔ Получен сигнал завершения. Останавливаем демон...")
        # Выбрасываем CancelledError для выхода из бесконечного цикла
        for task in asyncio.all_tasks(loop):
            if task is not asyncio.current_task(loop):
                task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_exit_signal)
        except NotImplementedError:
            # Для сред, где signal_handler не поддерживается
            pass

    try:
        while True:
            # Проверяем лимит длительности работы (для автотестов)
            if args.duration and (time.monotonic() - start_time) >= args.duration:
                print(f"⏱ Достигнут лимит времени работы ({args.duration}s). Выключение...")
                break

            # Периодическая проверка Kill Switch
            is_kill = settings.kill_switch_path.exists()
            if is_kill:
                if not state["risk"]["kill_switch"]:
                    print("⚠️ Замечен файл KILL_SWITCH! Останавливаем сборщики...")
                    state["status"] = "kill_switch"
                    state["risk"]["kill_switch"] = True
                    state["risk"]["status"] = "KILL_SWITCH_ACTIVE"
                    binance.stop()
                    bybit.stop()
                    market_collector.stop()
                    # TG alert о Kill Switch
                    if tg_alert.enabled:
                        asyncio.create_task(tg_alert.alert_kill_switch("KILL_SWITCH file detected"))
            else:
                if state["risk"]["kill_switch"]:
                    print("✅ Файл KILL_SWITCH удален. Запускаем сборщики...")
                    state["status"] = "online"
                    state["risk"]["kill_switch"] = False
                    state["risk"]["status"] = "OK"
                    binance.start()
                    bybit.start()
                    market_collector.start()

            # Обновляем Heartbeat
            now = datetime.now(timezone.utc)
            state["heartbeat_ts"] = now.isoformat().replace("+00:00", "Z")
            state["heartbeat_ms"] = int(now.timestamp() * 1000)

            # 7.1. Расчет Stress Score для каждого символа и обновление состояния
            computed_scores = []
            kill_active = state["risk"]["kill_switch"]
            binance_status = state["sources"]["binance"]["ws_status"] == "connected"
            bybit_status = state["sources"]["bybit"]["ws_status"] == "connected"
            now_ms = int(now.timestamp() * 1000)

            for symbol in settings.symbols:
                try:
                    res = compute_and_store_stress_score(
                        db=db,
                        engine=scoring_engine,
                        symbol=symbol,
                        computed_at_ms=now_ms,
                        kill_switch_active=kill_active,
                        binance_connected=binance_status,
                        bybit_connected=bybit_status,
                    )
                    computed_scores.append(res)
                    
                    if symbol in state["symbols"]:
                        state["symbols"][symbol].update({
                            "stress_score": res.score,
                            "stress_level": res.level,
                            "liquidations_1h_usdt": res.liquidations_1h_usdt,
                            "liquidations_4h_usdt": res.liquidations_4h_usdt,
                            "liquidations_24h_usdt": res.liquidations_24h_usdt,
                            "long_liq_ratio": res.long_liq_ratio,
                            "short_liq_ratio": res.short_liq_ratio,
                            "largest_event_24h_usdt": res.largest_event_24h_usdt,
                        })

                    # Генерируем и сохраняем сигнал
                    decision = compute_and_store_signal(
                        db=db,
                        engine=signal_engine,
                        stress_res=res,
                        last_decision=last_decisions.get(symbol),
                    )
                    last_decisions[symbol] = decision

                    if symbol in state["symbols"]:
                        state["symbols"][symbol]["last_signal"] = decision.signal_type

                    if "cooldown_active" not in decision.reasons:
                        state["last_signals"].insert(0, decision.to_dict())
                        if len(state["last_signals"]) > 20:
                            state["last_signals"] = state["last_signals"][:20]

                        # TG алерт о торговых сигналах
                        if tg_alert.enabled and decision.signal_type in (
                            "PAPER_LONG_SETUP", "PAPER_SHORT_SETUP",
                            "PAPER_CASCADE_RISK", "PAPER_REVERSION_WATCH",
                        ):
                            asyncio.create_task(tg_alert.alert_signal(decision.to_dict()))

                    # TG алерт об экстремальном стрессе
                    if tg_alert.enabled and res.level in ("EXTREME", "NO_TRADE"):
                        asyncio.create_task(
                            tg_alert.alert_stress_extreme(symbol, res.score, res.level)
                        )
                except Exception as exc:
                    print(f"⚠️ Ошибка вычисления стресса/сигнала для {symbol}: {exc}")

            # Обновление глобального стресса
            if computed_scores:
                max_res = max(computed_scores, key=lambda r: r.score)
                
                priority = {"INFO": 0, "WATCH": 1, "HIGH_STRESS": 2, "EXTREME": 3, "NO_TRADE": 4}
                max_level_res = max(computed_scores, key=lambda r: priority.get(r.level, 0))
                
                risk_priority = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
                max_risk_res = max(computed_scores, key=lambda r: risk_priority.get(r.cascade_risk, 0))

                state["market_stress"].update({
                    "global_score": max_res.score,
                    "global_level": max_level_res.level,
                    "cascade_risk": max_risk_res.cascade_risk,
                    "dominant_side": max_res.dominant_side if max_res.score > 0 else "BALANCED",
                })
                
                if max_res.score >= 50.0:
                    state["market_stress"]["last_spike_ts"] = now.isoformat().replace("+00:00", "Z")

            # Интеграция Paper Simulator
            try:
                # 1. Собираем цены разметки
                mark_prices = {}
                for symbol in settings.symbols:
                    price = paper_simulator.get_current_price(symbol)
                    if price is not None:
                        mark_prices[symbol] = price
                
                # 2. Проверяем выходы
                paper_simulator.check_exits(mark_prices, kill_active, now_ms)
                
                # 3. Проверяем входы
                paper_simulator.check_entries(last_decisions, mark_prices, now_ms)
                
                # 4. Считаем и пишем точку эквити в БД
                paper_simulator.calculate_equity_point(now_ms)
                
                # 5. Получаем агрегированную производительность
                perf = paper_simulator.get_performance()
                open_trades = db.get_open_paper_trades()
                
                open_positions_list = []
                for t in open_trades:
                    m_price = mark_prices.get(t.symbol) or t.entry_price
                    ttl_rem = max(0, int((t.max_holding_until_ms - now_ms) / 1000.0)) if t.max_holding_until_ms else 0
                    open_positions_list.append({
                        "symbol": t.symbol,
                        "side": t.side,
                        "qty": t.qty,
                        "entry_price": t.entry_price,
                        "mark_price": m_price,
                        "unrealized_net_pnl_usdt": t.unrealized_pnl_usdt,
                        "net_pnl_pct": t.pnl_pct,
                        "liquidation_side": t.metadata_json.get("liquidation_side") if isinstance(t.metadata_json, dict) else None,
                        "hypothesis_direction": t.metadata_json.get("hypothesis_direction") if isinstance(t.metadata_json, dict) else None,
                        "signal_type": t.metadata_json.get("signal_type") if isinstance(t.metadata_json, dict) else None,
                        "opened_at_ts": datetime.fromtimestamp(t.opened_at_ms / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                        "ttl_seconds_remaining": ttl_rem,
                    })

                state["paper"].update({
                    "balance_usdt": perf.balance_usdt,
                    "deposit_usdt": 10000.0,
                    "equity_usdt": perf.equity_usdt,
                    "available_balance_usdt": round(perf.balance_usdt - sum(t.notional_usdt for t in open_trades), 4),
                    "active_positions": len(open_positions_list),
                    "trades_today": perf.trades_count,
                    "win_rate": perf.win_rate,
                    "realized_pnl_usdt": perf.realized_pnl_usdt,
                    "unrealized_pnl_usdt": perf.unrealized_pnl_usdt,
                    "fees_usdt": perf.fees_usdt,
                    "net_pnl_usdt": perf.net_pnl_usdt,
                    "net_pnl_pct": perf.net_pnl_pct,
                    "drawdown_usdt": perf.drawdown_usdt,
                    "drawdown_pct": perf.drawdown_pct,
                    "best_hypothesis": perf.best_hypothesis,
                    "worst_hypothesis": perf.worst_hypothesis,
                    "open_positions": open_positions_list,
                })

                latest_eq = db.get_latest_equity_point()
                if latest_eq:
                    state["equity"].update({
                        "bot": "liquidation_hunter",
                        "latest_point_ms": latest_eq.ts_ms,
                        "has_equity_curve": True,
                    })
            except Exception as sim_exc:
                print(f"⚠️ Ошибка симулятора бумажной торговли: {sim_exc}")

            # Stale detection — проверяем shadow disconnect
            tracker.check_stale_sources()
            for source in ["binance", "bybit"]:
                if state["sources"][source]["ws_status"] == "stale" and tg_alert.enabled:
                    asyncio.create_task(tg_alert.alert_source_stale(source))

            # Атомарная запись состояния
            writer.write(state)

            await asyncio.sleep(settings.state_write_interval_seconds)

    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        print("🛑 Завершение работы демона...")
        # Устанавливаем статус завершения
        state["status"] = "stopping"
        try:
            writer.write(state)
        except Exception:
            pass

        # Останавливаем сборщики
        binance.stop()
        bybit.stop()
        market_collector.stop()
        if tg_alert.enabled:
            asyncio.get_event_loop().run_until_complete(tg_alert.close())

        if server:
            server.should_exit = True

        db.close()
        print("👋 Демон успешно остановлен.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Запуск Liquidation Hunter Bot Daemon.")
    parser.add_argument("--symbols", type=str, help="Символы через запятую (напр. BTCUSDT,ETHUSDT)")
    parser.add_argument("--db-path", type=str, help="Путь к SQLite БД")
    parser.add_argument("--state-path", type=str, help="Путь к файлу состояния JSON")
    parser.add_argument("--api-port", type=int, help="Порт API (localhost-only)")
    parser.add_argument("--duration", type=int, help="Длительность работы в секундах (для тестов)")
    parser.add_argument("--no-api", action="store_true", help="Не запускать FastAPI сервер")

    args = parser.parse_args()

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n👋 Выход.")
        sys.exit(0)


if __name__ == "__main__":
    main()
