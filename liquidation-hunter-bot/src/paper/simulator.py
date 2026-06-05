from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any

from src.storage.database import Database
from src.storage.models import PaperTradeRecord, EquityPointRecord, dumps_json
from src.signals.signal_engine import SignalDecision


@dataclass
class PaperConfig:
    """Конфигурация симулятора бумажной торговли."""

    starting_balance_usdt: float = 10000.0
    fee_rate: float = 0.0004  # 0.04% taker fee
    slippage_bps: int = 8  # 8 bps — реалистичный slippage при высокой волатильности
    max_open_positions_total: int = 3
    max_open_positions_per_symbol: int = 1
    max_risk_per_trade_pct: float = 1.5  # 1.5% от эквити на стоп-лосс
    max_notional_per_trade_pct: float = 20.0  # 20% от эквити на одну позу
    default_stop_loss_pct: float = 2.5  # 2.5% от цены входа — не выбивает шумом
    default_take_profit_pct: float = 4.0  # 4.0% от цены входа — R:R = 1.6
    max_trade_ttl_seconds: int = 3600  # 60 минут — время для bounce
    bot_name: str = "liquidation_hunter"


@dataclass
class PaperPosition:
    """Упрощенная структура для представления открытой позиции в RAM."""

    id: int
    symbol: str
    side: str  # LONG, SHORT
    qty: float
    entry_price: float
    stop_loss: float | None
    take_profit: float | None
    opened_at_ms: int
    max_holding_until_ms: int | None
    liquidation_side: str
    hypothesis_direction: str
    signal_type: str
    entry_reason: str
    stress_score: float
    stress_level: str
    cascade_risk: str
    dominant_side: str


@dataclass
class PaperFill:
    """Информация о виртуальной сделке исполнения."""

    symbol: str
    side: str
    qty: float
    price: float
    fee_usdt: float
    timestamp_ms: int


@dataclass
class PaperExecutionResult:
    """Результат выполнения операции открытия/закрытия."""

    success: bool
    trade_id: int | None = None
    error_message: str | None = None


@dataclass
class PaperPerformance:
    """Агрегированная производительность виртуального аккаунта."""

    bot: str = "liquidation_hunter"
    balance_usdt: float = 10000.0
    equity_usdt: float = 10000.0
    realized_pnl_usdt: float = 0.0
    unrealized_pnl_usdt: float = 0.0
    gross_pnl_usdt: float = 0.0
    fees_usdt: float = 0.0
    net_pnl_usdt: float = 0.0
    net_pnl_pct: float = 0.0
    trades_count: int = 0
    win_rate: float = 0.0
    drawdown_usdt: float = 0.0
    drawdown_pct: float = 0.0
    best_hypothesis: str | None = None
    worst_hypothesis: str | None = None


class PaperSimulator:
    """Симулятор бумажной торговли и учета кривой эквити."""

    def __init__(self, db: Database, config: PaperConfig | None = None):
        self.db = db
        self.config = config or PaperConfig()

    def get_current_price(self, symbol: str) -> float | None:
        """Получает текущую цену для пары (из снимков или ликвидаций)."""
        # 1. Последний снимок рынка
        snap = self.db.get_latest_market_snapshot(symbol)
        if snap and snap.price is not None:
            return snap.price
        # 2. Последняя ликвидация
        liqs = self.db.get_liquidations(symbol=symbol, limit=1)
        if liqs:
            return liqs[0].price
        return None

    def get_latest_equity(self) -> float:
        """Возвращает последнее эквити из БД или стартовый баланс."""
        latest = self.db.get_latest_equity_point()
        if latest:
            return latest.equity_usdt
        return self.config.starting_balance_usdt

    def check_exits(self, mark_prices: dict[str, float], is_kill_switch: bool, now_ms: int) -> list[PaperFill]:
        """Проверяет и закрывает открытые позиции при срабатывании условий выхода."""
        open_trades = self.db.get_open_paper_trades()
        closed_fills: list[PaperFill] = []

        for trade in open_trades:
            symbol = trade.symbol
            # Если Kill Switch активен, закрываем немедленно по цене входа, если нет mark_price
            mark_price = mark_prices.get(symbol) or self.get_current_price(symbol)
            
            # Если цены нет вообще и активен kill switch, используем цену входа для экстренного закрытия
            if mark_price is None:
                if is_kill_switch:
                    mark_price = trade.entry_price
                else:
                    continue  # Пропускаем без цены

            # Флаги условий закрытия
            hit_sl = False
            hit_tp = False
            ttl_expired = False
            kill_triggered = is_kill_switch

            if not kill_triggered:
                # 1. Stop loss
                if trade.side == "LONG" and trade.stop_loss is not None and mark_price <= trade.stop_loss:
                    hit_sl = True
                elif trade.side == "SHORT" and trade.stop_loss is not None and mark_price >= trade.stop_loss:
                    hit_sl = True

                # 2. Take profit
                if trade.side == "LONG" and trade.take_profit is not None and mark_price >= trade.take_profit:
                    hit_tp = True
                elif trade.side == "SHORT" and trade.take_profit is not None and mark_price <= trade.take_profit:
                    hit_tp = True

                # 3. TTL
                if trade.max_holding_until_ms is not None and now_ms >= trade.max_holding_until_ms:
                    ttl_expired = True

            # Определяем причину
            reason = None
            if kill_triggered:
                reason = "KILL_SWITCH"
            elif hit_sl:
                reason = "STOP_LOSS"
            elif hit_tp:
                reason = "TAKE_PROFIT"
            elif ttl_expired:
                reason = "TTL_EXPIRED"

            if reason is not None:
                fill = self.close_paper_trade(trade, mark_price, reason, now_ms)
                closed_fills.append(fill)

        return closed_fills

    def check_entries(
        self,
        active_signals: dict[str, SignalDecision],
        mark_prices: dict[str, float],
        now_ms: int,
    ) -> list[PaperFill]:
        """Проверяет условия и открывает новые бумажные позиции."""
        entered_fills: list[PaperFill] = []

        for symbol, sig in active_signals.items():
            # Направленный сетап
            if sig.signal_type not in ("PAPER_LONG_SETUP", "PAPER_SHORT_SETUP"):
                continue
            # Уверенность
            if sig.confidence < 50.0:
                continue

            # Проверка лимитов на количество позиций
            all_open = self.db.get_open_paper_trades()
            if len(all_open) >= self.config.max_open_positions_total:
                continue

            # Уже есть поза по символу
            symbol_open = [t for t in all_open if t.symbol == symbol]
            if symbol_open:
                continue

            # Цена
            raw_price = mark_prices.get(symbol) or self.get_current_price(symbol)
            if raw_price is None:
                continue

            # Открываем сделку
            fill = self.open_paper_trade(symbol, sig, raw_price, now_ms)
            if fill:
                entered_fills.append(fill)

        return entered_fills

    def open_paper_trade(self, symbol: str, signal: SignalDecision, raw_price: float, now_ms: int) -> PaperFill | None:
        """Открывает новую виртуальную позицию."""
        # 1. Применяем проскальзывание на вход
        slippage_factor = self.config.slippage_bps / 10000.0
        if signal.action == "LONG":
            entry_price = raw_price * (1.0 + slippage_factor)
        else:
            entry_price = raw_price * (1.0 - slippage_factor)

        # 2. Расчет объема позиции (Sizing)
        latest_equity = self.get_latest_equity()
        risk_cap = latest_equity * (self.config.max_risk_per_trade_pct / 100.0)
        notional_cap = latest_equity * (self.config.max_notional_per_trade_pct / 100.0)

        # SL / TP цены
        sl_pct = self.config.default_stop_loss_pct / 100.0
        tp_pct = self.config.default_take_profit_pct / 100.0

        if signal.action == "LONG":
            stop_loss = entry_price * (1.0 - sl_pct)
            take_profit = entry_price * (1.0 + tp_pct)
        else:
            stop_loss = entry_price * (1.0 + sl_pct)
            take_profit = entry_price * (1.0 - tp_pct)

        stop_distance = abs(entry_price - stop_loss)
        qty_by_risk = risk_cap / stop_distance if stop_distance > 0 else 0.0
        qty_by_notional = notional_cap / entry_price if entry_price > 0 else 0.0
        
        qty = min(qty_by_risk, qty_by_notional)
        notional = entry_price * qty

        if qty <= 0.0 or notional < 5.0:
            return None

        # 3. Комиссия входа
        entry_fee = notional * self.config.fee_rate

        # 4. Сохранение сделки
        # Собираем метаданные
        meta = {
            "bot": self.config.bot_name,
            "symbol": symbol,
            "side": signal.action,
            "status": "OPEN",
            "qty": qty,
            "entry_price": entry_price,
            "exit_price": None,
            "entry_ts": now_ms,
            "exit_ts": None,
            "entry_notional_usdt": notional,
            "exit_notional_usdt": None,
            "entry_fee_usdt": entry_fee,
            "exit_fee_usdt": None,
            "fees_usdt": entry_fee,
            "gross_pnl_usdt": 0.0,
            "net_pnl_usdt": -entry_fee,
            "net_pnl_pct": 0.0,
            "setup_type": "reversion" if (signal.hypothesis and "reversion" in signal.hypothesis.lower()) else "continuation",
            "liquidation_side": signal.dominant_side,
            "hypothesis_direction": signal.action,
            "signal_type": signal.signal_type,
            "entry_reason": ", ".join(signal.reasons) if signal.reasons else "",
            "exit_reason": None,
            "stress_score": signal.stress_score,
            "stress_level": signal.stress_level,
            "cascade_risk": signal.cascade_risk,
            "dominant_side": signal.dominant_side,
        }

        trade = PaperTradeRecord(
            symbol=symbol,
            hypothesis=signal.hypothesis,
            side=signal.action,
            status="OPEN",
            opened_at_ms=now_ms,
            closed_at_ms=None,
            entry_price=entry_price,
            exit_price=None,
            qty=qty,
            notional_usdt=notional,
            realized_pnl_usdt=0.0,
            unrealized_pnl_usdt=-entry_fee - (notional * self.config.fee_rate), # Сразу вычитаем entry fee + estimated exit fee
            fees_usdt=entry_fee,
            net_pnl_usdt=-entry_fee,
            pnl_pct=0.0,
            stop_loss=stop_loss,
            take_profit=take_profit,
            max_holding_until_ms=now_ms + self.config.max_trade_ttl_seconds * 1000,
            close_reason=None,
            metadata_json=meta,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
        )

        self.db.insert_paper_trade(trade)
        
        return PaperFill(
            symbol=symbol,
            side=signal.action,
            qty=qty,
            price=entry_price,
            fee_usdt=entry_fee,
            timestamp_ms=now_ms,
        )

    def close_paper_trade(self, trade: PaperTradeRecord, raw_price: float, reason: str, now_ms: int) -> PaperFill:
        """Закрывает открытую виртуальную позицию."""
        # 1. Применяем проскальзывание на выход
        slippage_factor = self.config.slippage_bps / 10000.0
        if trade.side == "LONG":
            exit_price = raw_price * (1.0 - slippage_factor)
        else:
            exit_price = raw_price * (1.0 + slippage_factor)

        # 2. Расчет комиссий и PnL
        entry_fee = trade.fees_usdt  # Сохраненная комиссия входа
        exit_fee = exit_price * trade.qty * self.config.fee_rate
        total_fees = entry_fee + exit_fee

        if trade.side == "LONG":
            gross_pnl = (exit_price - trade.entry_price) * trade.qty
        else:
            gross_pnl = (trade.entry_price - exit_price) * trade.qty

        net_pnl = gross_pnl - total_fees
        net_pnl_pct = (net_pnl / trade.notional_usdt) * 100.0 if trade.notional_usdt > 0 else 0.0

        # 3. Обновляем метаданные сделки
        meta = trade.metadata_json
        if isinstance(meta, str):
            import json
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        
        meta["status"] = "CLOSED"
        meta["exit_price"] = exit_price
        meta["exit_ts"] = now_ms
        meta["exit_notional_usdt"] = exit_price * trade.qty
        meta["exit_fee_usdt"] = exit_fee
        meta["fees_usdt"] = total_fees
        meta["gross_pnl_usdt"] = gross_pnl
        meta["net_pnl_usdt"] = net_pnl
        meta["net_pnl_pct"] = round(net_pnl_pct, 4)
        meta["exit_reason"] = reason

        self.db.update_paper_trade(
            trade_id=trade.id,
            status="CLOSED",
            closed_at_ms=now_ms,
            exit_price=exit_price,
            realized_pnl_usdt=gross_pnl,  # В БД пишем gross PnL в realized_pnl
            unrealized_pnl_usdt=0.0,
            fees_usdt=total_fees,
            net_pnl_usdt=net_pnl,
            pnl_pct=round(net_pnl_pct, 4),
            close_reason=reason,
            metadata_json=meta,
            updated_at_ms=now_ms,
        )

        return PaperFill(
            symbol=trade.symbol,
            side="LONG" if trade.side == "SHORT" else "SHORT",  # Направление закрытия противоположно входу
            qty=trade.qty,
            price=exit_price,
            fee_usdt=exit_fee,
            timestamp_ms=now_ms,
        )

    def calculate_equity_point(self, now_ms: int) -> EquityPointRecord:
        """Считает текущие показатели баланса и эквити и записывает точку эквити."""
        # 1. Получаем реализованные показатели из закрытых сделок
        perf_db = self.db.get_paper_performance()
        realized_pnl = perf_db["realized_pnl_usdt"]
        realized_fees = perf_db["fees_usdt"]
        realized_net_pnl = perf_db["net_pnl_usdt"]
        trades_count = perf_db["total_trades"]
        win_rate = perf_db["win_rate"]

        # Наш виртуальный баланс = стартовый баланс + realized net PnL
        balance = self.config.starting_balance_usdt + realized_net_pnl

        # 2. Считаем нереализованный PnL и комиссии для всех открытых позиций
        open_trades = self.db.get_open_paper_trades()
        unrealized_gross = 0.0
        unrealized_fees = 0.0
        unrealized_net = 0.0
        unrealized_net_for_equity = 0.0

        for trade in open_trades:
            mark_price = self.get_current_price(trade.symbol)
            if mark_price is None:
                mark_price = trade.entry_price

            if trade.side == "LONG":
                u_gross = (mark_price - trade.entry_price) * trade.qty
            else:
                u_gross = (trade.entry_price - mark_price) * trade.qty

            # Оценка комиссии закрытия по текущей цене
            est_exit_fee = mark_price * trade.qty * self.config.fee_rate
            
            unrealized_gross += u_gross
            unrealized_fees += est_exit_fee
            # Нереализованный чистый PnL по открытой сделке: u_gross - entry_fee - est_exit_fee
            unrealized_net += (u_gross - trade.fees_usdt - est_exit_fee)
            unrealized_net_for_equity += (u_gross - est_exit_fee)

            # Обновим unrealized в БД для отображения в API
            self.db.update_paper_trade(
                trade_id=trade.id,
                unrealized_pnl_usdt=round(u_gross - trade.fees_usdt - est_exit_fee, 4),
            )

        equity = balance + unrealized_net_for_equity
        total_fees = realized_fees + unrealized_fees
        total_net_pnl = (equity - self.config.starting_balance_usdt)
        total_net_pnl_pct = (total_net_pnl / self.config.starting_balance_usdt) * 100.0

        # Вычисляем drawdown
        all_points = self.db.get_equity_curve()
        equity_values = [p.equity_usdt for p in all_points] + [equity]
        peak_equity = max(equity_values)
        drawdown_usdt = peak_equity - equity
        drawdown_pct = (drawdown_usdt / peak_equity * 100.0) if peak_equity > 0.0 else 0.0

        # 3. Сохраняем точку в БД
        record = EquityPointRecord(
            ts_ms=now_ms,
            bot=self.config.bot_name,
            balance_usdt=round(balance, 4),
            equity_usdt=round(equity, 4),
            available_balance_usdt=round(balance - sum(t.notional_usdt for t in open_trades), 4),
            realized_pnl_usdt=round(realized_pnl, 4),
            unrealized_pnl_usdt=round(unrealized_net, 4),
            fees_usdt=round(total_fees, 4),
            net_pnl_usdt=round(total_net_pnl, 4),
            net_pnl_pct=round(total_net_pnl_pct, 4),
            open_positions=len(open_trades),
            trades_count=trades_count,
            win_rate=win_rate,
            drawdown_usdt=round(drawdown_usdt, 4),
            drawdown_pct=round(drawdown_pct, 4),
            created_at_ms=int(time.time() * 1000),
        )

        self.db.insert_equity_point(record)
        return record

    def get_performance(self) -> PaperPerformance:
        """Собирает сводную производительность симулятора с учетом лучшей/худшей гипотезы."""
        perf_db = self.db.get_paper_performance()
        latest_equity = self.get_latest_equity()
        open_trades = self.db.get_open_paper_trades()

        # Нереализованный нетто-PnL
        unrealized_net = 0.0
        unrealized_net_for_equity = 0.0
        for trade in open_trades:
            mark_price = self.get_current_price(trade.symbol)
            if mark_price is None:
                mark_price = trade.entry_price

            if trade.side == "LONG":
                u_gross = (mark_price - trade.entry_price) * trade.qty
            else:
                u_gross = (trade.entry_price - mark_price) * trade.qty

            est_exit_fee = mark_price * trade.qty * self.config.fee_rate
            unrealized_net += (u_gross - trade.fees_usdt - est_exit_fee)
            unrealized_net_for_equity += (u_gross - est_exit_fee)

        realized_net_pnl = perf_db["net_pnl_usdt"]
        balance = self.config.starting_balance_usdt + realized_net_pnl
        equity = balance + unrealized_net_for_equity

        total_net_pnl = equity - self.config.starting_balance_usdt
        total_net_pnl_pct = (total_net_pnl / self.config.starting_balance_usdt) * 100.0

        # Вычисляем drawdown
        all_points = self.db.get_equity_curve()
        equity_values = [p.equity_usdt for p in all_points] + [equity]
        peak_equity = max(equity_values)
        drawdown_usdt = peak_equity - equity
        drawdown_pct = (drawdown_usdt / peak_equity * 100.0) if peak_equity > 0.0 else 0.0

        # Вычисление лучшей и худшей гипотезы
        recent_trades = self.db.get_recent_paper_trades(limit=1000)
        closed_trades = [t for t in recent_trades if t.status == "CLOSED"]
        hyp_pnl = {}
        for t in closed_trades:
            if t.hypothesis:
                hyp_pnl[t.hypothesis] = hyp_pnl.get(t.hypothesis, 0.0) + t.net_pnl_usdt

        best_hypothesis = None
        worst_hypothesis = None
        if hyp_pnl:
            best_hypothesis = max(hyp_pnl, key=hyp_pnl.get)
            worst_hypothesis = min(hyp_pnl, key=hyp_pnl.get)

        return PaperPerformance(
            bot=self.config.bot_name,
            balance_usdt=round(balance, 4),
            equity_usdt=round(equity, 4),
            realized_pnl_usdt=round(perf_db["realized_pnl_usdt"], 4),
            unrealized_pnl_usdt=round(unrealized_net, 4),
            gross_pnl_usdt=round(perf_db["realized_pnl_usdt"] + unrealized_net + perf_db["fees_usdt"], 4),
            fees_usdt=round(perf_db["fees_usdt"] + sum(self.get_current_price(t.symbol) * t.qty * self.config.fee_rate for t in open_trades if self.get_current_price(t.symbol)), 4),
            net_pnl_usdt=round(total_net_pnl, 4),
            net_pnl_pct=round(total_net_pnl_pct, 4),
            trades_count=perf_db["total_trades"],
            win_rate=perf_db["win_rate"],
            drawdown_usdt=round(drawdown_usdt, 4),
            drawdown_pct=round(drawdown_pct, 4),
            best_hypothesis=best_hypothesis,
            worst_hypothesis=worst_hypothesis,
        )
