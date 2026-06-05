from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from src.storage.database import Database
from src.storage.models import StressScoreRecord


@dataclass
class StressScoreResult:
    """Результат расчета уровня рыночного стресса."""

    symbol: str
    computed_at_ms: int
    window: str
    score: float
    level: str  # INFO, WATCH, HIGH_STRESS, EXTREME, NO_TRADE
    cascade_risk: str  # low, medium, high, unknown
    dominant_side: str  # LONGS_LIQUIDATED, SHORTS_LIQUIDATED, BALANCED
    components: dict[str, float | None]  # сырые оценки компонентов (0..100) или None
    liquidations_1h_usdt: float
    liquidations_4h_usdt: float
    liquidations_24h_usdt: float
    long_liq_ratio: float
    short_liq_ratio: float
    largest_event_24h_usdt: float
    reasons: list[str] = field(default_factory=list)


class StressScoreEngine:
    """Аналитический движок для расчета уровня стресса рынка (Stress Score v0)."""

    WEIGHTS = {
        "liq_volume": 0.30,
        "liq_acceleration": 0.20,
        "liq_ratio": 0.15,
        "price_move": 0.10,
        "volume_spike": 0.08,
        "funding_rate": 0.07,
        "oi_change": 0.07,
        "market_direction": 0.03,
    }

    def __init__(self, db: Database):
        self.db = db

    def calculate_stress(
        self,
        symbol: str,
        computed_at_ms: int | None = None,
        kill_switch_active: bool = False,
        binance_connected: bool = True,
        bybit_connected: bool = True,
    ) -> StressScoreResult:
        """
        Вычисляет уровень рыночного стресса для указанного символа.
        """
        if computed_at_ms is None:
            computed_at_ms = int(time.time() * 1000)

        reasons: list[str] = []

        # 1. Сбор ликвидаций за последние 24 часа
        since_24h_ms = computed_at_ms - 24 * 3600 * 1000
        liq_24h = self.db.get_liquidations(symbol=symbol, since_ms=since_24h_ms, limit=100000)

        # Статистика по ликвидациям
        total_liq_usdt = sum(x.notional_usdt for x in liq_24h)
        largest_event_usdt = max((x.notional_usdt for x in liq_24h), default=0.0)

        v_1h = sum(x.notional_usdt for x in liq_24h if x.event_time_ms >= computed_at_ms - 3600 * 1000)
        v_4h = sum(x.notional_usdt for x in liq_24h if x.event_time_ms >= computed_at_ms - 4 * 3600 * 1000)
        v_24h = total_liq_usdt

        long_liq_sum = sum(x.notional_usdt for x in liq_24h if x.liquidated_position_side == "LONG")
        short_liq_sum = sum(x.notional_usdt for x in liq_24h if x.liquidated_position_side == "SHORT")
        
        long_ratio = 0.0
        short_ratio = 0.0
        if total_liq_usdt > 0:
            long_ratio = long_liq_sum / total_liq_usdt
            short_ratio = short_liq_sum / total_liq_usdt

        # Определение dominant_side
        if total_liq_usdt < 1000.0:
            dominant_side = "BALANCED"
        elif long_ratio > 0.7:
            dominant_side = "LONGS_LIQUIDATED"
        elif short_ratio > 0.7:
            dominant_side = "SHORTS_LIQUIDATED"
        else:
            dominant_side = "BALANCED"

        # 2. Сбор снимков рынка за последние 5 часов для рыночного контекста
        since_5h_ms = computed_at_ms - 5 * 3600 * 1000
        snapshots = self.db.get_market_snapshots(symbol=symbol, since_ms=since_5h_ms, limit=1000)

        # 3. Расчет компонентов
        comp_scores: dict[str, float | None] = {}

        # Component 1: Liquidation Volume Score (30%)
        comp_scores["liq_volume"] = self._calc_volume_score(symbol, liq_24h, computed_at_ms)

        # Component 2: Liquidation Acceleration Score (20%)
        comp_scores["liq_acceleration"] = self._calc_acceleration_score(liq_24h, computed_at_ms)

        # Component 3: Liquidation Ratio Score (15%)
        comp_scores["liq_ratio"] = self._calc_ratio_score(long_liq_sum, short_liq_sum)

        # Рыночный контекст (Price, Volume, Funding, OI)
        # Инициализируем переменные для последующего использования в Market Direction
        price_change_1h = None
        oi_change_1h = None

        if not snapshots or len(snapshots) < 2:
            comp_scores["price_move"] = None
            comp_scores["volume_spike"] = None
            comp_scores["funding_rate"] = None
            comp_scores["oi_change"] = None
            comp_scores["market_direction"] = None
            reasons.append("missing_context")
        else:
            # Находим нужные снимки
            snap_now = snapshots[0]
            t_1h_target = snap_now.snapshot_time_ms - 3600 * 1000
            t_4h_target = snap_now.snapshot_time_ms - 4 * 3600 * 1000

            snap_1h = min(snapshots, key=lambda s: abs(s.snapshot_time_ms - t_1h_target))
            snap_4h = min(snapshots, key=lambda s: abs(s.snapshot_time_ms - t_4h_target))

            # Толерантность 30 минут
            if abs(snap_1h.snapshot_time_ms - t_1h_target) > 1800 * 1000:
                snap_1h = None
            if abs(snap_4h.snapshot_time_ms - t_4h_target) > 1800 * 1000:
                snap_4h = None

            # Component 4: Price Move (10%)
            price_score, price_change_1h = self._calc_price_move_score(snap_now, snap_1h, snap_4h)
            comp_scores["price_move"] = price_score

            # Component 5: Volume Spike (8%)
            comp_scores["volume_spike"] = self._calc_volume_spike_score(symbol, computed_at_ms)

            # Component 6: Funding Rate (7%)
            comp_scores["funding_rate"] = self._calc_funding_rate_score(snap_now)

            # Component 7: Open Interest Change (7%)
            oi_score, oi_change_1h = self._calc_oi_change_score(snap_now, snap_1h, snap_4h)
            comp_scores["oi_change"] = oi_score

            # Component 8: Market Direction (3%)
            comp_scores["market_direction"] = self._calc_market_direction_score(
                long_ratio, price_change_1h, oi_change_1h
            )

        # 4. Взвешенный расчет итоговой оценки
        weighted_sum = 0.0
        total_weight = 0.0

        for name, score in comp_scores.items():
            if score is not None:
                weighted_sum += score * self.WEIGHTS[name]
                total_weight += self.WEIGHTS[name]

        if total_weight > 0:
            raw_score = (weighted_sum / total_weight) * 100.0
        else:
            raw_score = 0.0

        raw_score = min(100.0, max(0.0, raw_score))

        # Причины недостатка данных
        if len(liq_24h) < 3:
            reasons.append("insufficient_data")

        # 5. Классификация уровней
        if raw_score >= 75:
            level = "EXTREME"
        elif raw_score >= 50:
            level = "HIGH_STRESS"
        elif raw_score >= 25:
            level = "WATCH"
        else:
            level = "INFO"

        # 6. Расчет риска каскада
        cascade_risk = self._calc_cascade_risk(comp_scores, long_ratio, short_ratio)

        # 7. Проверка оверрайда NO_TRADE
        # Проверяем устаревание данных snapshots
        data_stale = False
        if snapshots:
            latest_snap_age = (computed_at_ms - snapshots[0].snapshot_time_ms) / 1000.0
            if latest_snap_age > 7200.0:  # Старше 2 часов
                data_stale = True

        if kill_switch_active:
            level = "NO_TRADE"
            reasons.append("kill_switch_active")
        elif not binance_connected and not bybit_connected:
            level = "NO_TRADE"
            reasons.append("sources_disconnected")
        elif data_stale:
            level = "NO_TRADE"
            reasons.append("data_stale")

        # Преобразуем компоненты в 0..100 для возврата
        components_result: dict[str, float | None] = {}
        for k, v in comp_scores.items():
            components_result[k] = round(v * 100.0, 1) if v is not None else None

        return StressScoreResult(
            symbol=symbol,
            computed_at_ms=computed_at_ms,
            window="1h",
            score=round(raw_score, 2),
            level=level,
            cascade_risk=cascade_risk,
            dominant_side=dominant_side,
            components=components_result,
            liquidations_1h_usdt=round(v_1h, 4),
            liquidations_4h_usdt=round(v_4h, 4),
            liquidations_24h_usdt=round(v_24h, 4),
            long_liq_ratio=round(long_ratio, 4),
            short_liq_ratio=round(short_ratio, 4),
            largest_event_24h_usdt=round(largest_event_usdt, 4),
            reasons=reasons,
        )

    # ──────────────────────────────────────────────────────────────
    #  Вспомогательные методы расчета компонентов
    # ──────────────────────────────────────────────────────────────

    def _calc_volume_score(self, symbol: str, liq_24h: list[Any], now_ms: int) -> float:
        # Пороги BTC (калиброваны под публичный WS — ~1-5% реальных ликвидаций)
        thr_1h = [50_000, 200_000, 1_000_000]    # $50K, $200K, $1M
        thr_4h = [150_000, 500_000, 2_000_000]   # $150K, $500K, $2M
        thr_24h = [500_000, 2_000_000, 10_000_000]  # $500K, $2M, $10M

        # Для не-BTC пар (например, ETH) уменьшаем пороги
        if "BTC" not in symbol:
            thr_1h = [x * 0.3 for x in thr_1h]
            thr_4h = [x * 0.3 for x in thr_4h]
            thr_24h = [x * 0.3 for x in thr_24h]

        v_1h = sum(x.notional_usdt for x in liq_24h if x.event_time_ms >= now_ms - 3600 * 1000)
        v_4h = sum(x.notional_usdt for x in liq_24h if x.event_time_ms >= now_ms - 4 * 3600 * 1000)
        v_24h = sum(x.notional_usdt for x in liq_24h)

        def get_score(val: float, thr: list[float]) -> float:
            if val < thr[0]:
                return (val / thr[0]) * 0.2
            elif val < thr[1]:
                return 0.2 + ((val - thr[0]) / (thr[1] - thr[0])) * 0.3
            elif val < thr[2]:
                return 0.5 + ((val - thr[1]) / (thr[2] - thr[1])) * 0.3
            else:
                return min(1.0, 0.8 + ((val - thr[2]) / thr[2]) * 0.2)

        s_1h = get_score(v_1h, thr_1h)
        s_4h = get_score(v_4h, thr_4h)
        s_24h = get_score(v_24h, thr_24h)

        return s_1h * 0.5 + s_4h * 0.3 + s_24h * 0.2

    def _calc_acceleration_score(self, liq_24h: list[Any], now_ms: int) -> float:
        recent_15m = sum(x.notional_usdt for x in liq_24h if x.event_time_ms >= now_ms - 900000)
        v_60m = sum(x.notional_usdt for x in liq_24h if x.event_time_ms >= now_ms - 3600000)
        # Исключаем текущий 15-мин интервал из среднего, чтобы сравнивать
        # «последние 15 мин» vs «предыдущие 45 мин в среднем»
        prev_45m = v_60m - recent_15m
        avg_15m = prev_45m / 3.0 if prev_45m > 0 else 0.0

        if avg_15m <= 0:
            # Если предыдущие 45 мин тихо, а сейчас есть ликвидации — это всплеск
            return 0.8 if recent_15m > 0 else 0.0

        accel = recent_15m / avg_15m

        if accel < 1.0:
            return 0.0
        elif accel < 2.0:
            return 0.1 + (accel - 1.0) * 0.2
        elif accel < 5.0:
            return 0.3 + ((accel - 2.0) / 3.0) * 0.4
        elif accel < 10.0:
            return 0.7 + ((accel - 5.0) / 5.0) * 0.2
        else:
            return min(1.0, 0.9 + ((accel - 10.0) / 20.0) * 0.1)

    def _calc_ratio_score(self, long_liq: float, short_liq: float) -> float:
        total = long_liq + short_liq
        if total < 10000.0:  # Минимальный порог значимости
            return 0.0
        long_ratio = long_liq / total
        short_ratio = short_liq / total
        imbalance = abs(long_ratio - short_ratio)
        return min(1.0, imbalance * 1.1)

    def _calc_price_move_score(
        self, snap_now: Any, snap_1h: Any | None, snap_4h: Any | None
    ) -> tuple[float | None, float | None]:
        if snap_now.price is None:
            return None, None

        pct_1h = 0.0
        pct_4h = 0.0
        avail = 0

        s_1h = 0.0
        if snap_1h and snap_1h.price:
            pct_1h = ((snap_now.price - snap_1h.price) / snap_1h.price) * 100.0
            s_1h = min(1.0, abs(pct_1h) / 5.0)
            avail += 1

        s_4h = 0.0
        if snap_4h and snap_4h.price:
            pct_4h = ((snap_now.price - snap_4h.price) / snap_4h.price) * 100.0
            s_4h = min(1.0, abs(pct_4h) / 10.0)
            avail += 1

        if avail == 2:
            return s_1h * 0.6 + s_4h * 0.4, pct_1h
        elif avail == 1:
            return s_1h if snap_1h else s_4h, pct_1h if snap_1h else None
        else:
            return None, None

    def _calc_volume_spike_score(self, symbol: str, now_ms: int) -> float | None:
        history_snaps = self.db.get_market_snapshots(symbol, since_ms=now_ms - 24 * 3600 * 1000, limit=1000)
        if len(history_snaps) < 5:
            return None

        volumes = [s.volume_24h for s in history_snaps if s.volume_24h is not None]
        if len(volumes) < 5:
            return None

        latest_vol = volumes[0]
        avg_vol = sum(volumes) / len(volumes)
        variance = sum((v - avg_vol) ** 2 for v in volumes) / len(volumes)
        std_vol = variance ** 0.5

        if std_vol <= 0:
            return 0.0

        z = (latest_vol - avg_vol) / std_vol
        return min(1.0, max(0.0, (z - 1.0) / 3.0))

    def _calc_funding_rate_score(self, snap_now: Any) -> float | None:
        if snap_now.funding_rate is None:
            return None

        abs_funding = abs(snap_now.funding_rate)

        if abs_funding < 0.0001:
            return 0.0
        elif abs_funding < 0.0005:
            return ((abs_funding - 0.0001) / 0.0004) * 0.4
        elif abs_funding < 0.001:
            return 0.4 + ((abs_funding - 0.0005) / 0.0005) * 0.3
        else:
            return min(1.0, 0.7 + ((abs_funding - 0.001) / 0.002) * 0.3)

    def _calc_oi_change_score(
        self, snap_now: Any, snap_1h: Any | None, snap_4h: Any | None
    ) -> tuple[float | None, float | None]:
        if snap_now.open_interest is None:
            return None, None

        pct_oi_1h = 0.0
        pct_oi_4h = 0.0
        avail = 0

        s_1h = 0.0
        if snap_1h and snap_1h.open_interest:
            pct_oi_1h = ((snap_now.open_interest - snap_1h.open_interest) / snap_1h.open_interest) * 100.0
            s_1h = min(1.0, abs(pct_oi_1h) / 8.0)
            avail += 1

        s_4h = 0.0
        if snap_4h and snap_4h.open_interest:
            pct_oi_4h = ((snap_now.open_interest - snap_4h.open_interest) / snap_4h.open_interest) * 100.0
            s_4h = min(1.0, abs(pct_oi_4h) / 15.0)
            avail += 1

        if avail == 2:
            return s_1h * 0.6 + s_4h * 0.4, pct_oi_1h
        elif avail == 1:
            return s_1h if snap_1h else s_4h, pct_oi_1h if snap_1h else None
        else:
            return None, None

    def _calc_market_direction_score(
        self, long_ratio: float, price_change_1h: float | None, oi_change_1h: float | None
    ) -> float | None:
        if price_change_1h is None:
            return None

        # Направление ликвидаций
        if long_ratio > 0.6:
            liq_direction = "bearish"
        elif long_ratio < 0.4:
            liq_direction = "bullish"
        else:
            liq_direction = "neutral"

        # Направление цены
        if price_change_1h < -1.0:
            price_direction = "bearish"
        elif price_change_1h > 1.0:
            price_direction = "bullish"
        else:
            price_direction = "neutral"

        # Направление OI
        if oi_change_1h is not None:
            if oi_change_1h < -1.0:
                oi_direction = "bearish"
            elif oi_change_1h > 1.0:
                oi_direction = "bullish"
            else:
                oi_direction = "neutral"
        else:
            oi_direction = "neutral"

        if liq_direction == price_direction and liq_direction != "neutral":
            return 0.8
        elif liq_direction != price_direction and liq_direction != "neutral" and price_direction != "neutral":
            return 0.5
        else:
            return 0.2

    def _calc_cascade_risk(self, comp_scores: dict[str, float | None], long_ratio: float, short_ratio: float) -> str:
        vol = comp_scores.get("liq_volume")
        acc = comp_scores.get("liq_acceleration")

        if vol is None or acc is None:
            return "unknown"

        # Условия для каскада
        # vol > 0.6 (высокий объем) и acc > 0.5 (сильное ускорение) и доминирование стороны > 75%
        if vol > 0.6 and acc > 0.6 and (long_ratio > 0.75 or short_ratio > 0.75):
            return "high"
        elif vol > 0.4 or acc > 0.4:
            return "medium"
        else:
            return "low"


def compute_and_store_stress_score(
    db: Database,
    engine: StressScoreEngine,
    symbol: str,
    computed_at_ms: int | None = None,
    kill_switch_active: bool = False,
    binance_connected: bool = True,
    bybit_connected: bool = True,
) -> StressScoreResult:
    """Вычисляет StressScoreResult и сохраняет запись в таблицу stress_scores базы данных."""
    result = engine.calculate_stress(
        symbol=symbol,
        computed_at_ms=computed_at_ms,
        kill_switch_active=kill_switch_active,
        binance_connected=binance_connected,
        bybit_connected=bybit_connected,
    )
    # Преобразование в StressScoreRecord
    record = StressScoreRecord(
        symbol=result.symbol,
        computed_at_ms=result.computed_at_ms,
        window=result.window,
        score=result.score,
        level=result.level,
        components_json=result.components,
        total_liquidations_usdt=result.liquidations_24h_usdt,
        long_liq_ratio=result.long_liq_ratio,
        short_liq_ratio=result.short_liq_ratio,
        cascade_risk=result.cascade_risk,
        created_at_ms=int(time.time() * 1000),
    )
    db.insert_stress_score(record)
    return result
