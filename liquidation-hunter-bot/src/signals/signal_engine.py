from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any

from src.scoring.stress_score import StressScoreResult
from src.storage.database import Database
from src.storage.models import SignalRecord


@dataclass
class SignalDecision:
    """Безопасный paper-only сигнал/гипотеза."""

    symbol: str
    signal_type: str  # NO_TRADE, WATCH_ONLY, PAPER_OBSERVE, PAPER_LONG_SETUP, PAPER_SHORT_SETUP, PAPER_REVERSION_WATCH, PAPER_CASCADE_RISK
    action: str  # LONG, SHORT, NO_TRADE, WATCH
    confidence: float  # 0..100
    risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL
    stress_level: str
    stress_score: float
    cascade_risk: str
    dominant_side: str
    hypothesis: str
    reasons: list[str]
    invalidation: list[str]
    cooldown_until_ms: int | None
    created_at_ms: int
    expires_at_ms: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["created_at_ts"] = self.ts_to_iso(self.created_at_ms)
        d["expires_at_ts"] = self.ts_to_iso(self.expires_at_ms)
        return d

    @staticmethod
    def ts_to_iso(ts_ms: int) -> str:
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")


class SignalEngine:
    """Аналитический движок для генерации безопасных paper-only сигналов (Signal Engine v0)."""

    SEVERITY_RANK = {
        "NO_TRADE": 0,
        "WATCH_ONLY": 1,
        "PAPER_OBSERVE": 2,
        "PAPER_REVERSION_WATCH": 3,
        "PAPER_CASCADE_RISK": 4,
        "PAPER_LONG_SETUP": 5,
        "PAPER_SHORT_SETUP": 5,
    }

    COOLDOWN_DURATION_MS = 600000  # 10 минут

    def __init__(self, db: Database):
        self.db = db

    def generate_decision(
        self,
        stress_res: StressScoreResult,
        last_decision: SignalDecision | None = None,
    ) -> SignalDecision:
        """
        Превращает результат StressScoreResult в SignalDecision с применением правил фильтрации и cooldown.
        """
        symbol = stress_res.symbol
        created_at_ms = stress_res.computed_at_ms
        score = stress_res.score
        level = stress_res.level
        cascade_risk = stress_res.cascade_risk
        dominant_side = stress_res.dominant_side
        reasons = list(stress_res.reasons)
        invalidation = []

        # 1. Базовые правила генерации типов сигналов
        # Защитные оверрайды
        is_safety_override = (
            level == "NO_TRADE"
            or "kill_switch_active" in reasons
            or "sources_disconnected" in reasons
            or "data_stale" in reasons
        )

        # Инициализация переменных гипотез (используются в расчёте confidence)
        best_hyp = None
        best_met = 0
        best_total = 1  # Avoid division by zero

        if is_safety_override:
            signal_type = "NO_TRADE"
            action = "NO_TRADE"
            hypothesis = "NO_TRADE override active due to security risk filters."
            if "kill_switch_active" in reasons:
                hypothesis = "NO_TRADE: KILL_SWITCH is active."
            elif "sources_disconnected" in reasons:
                hypothesis = "NO_TRADE: Both exchange WS sources are disconnected."
            elif "data_stale" in reasons:
                hypothesis = "NO_TRADE: Database snapshots are stale."
        # Порог минимальной активности
        elif score < 10.0 and stress_res.liquidations_1h_usdt == 0.0:
            signal_type = "NO_TRADE"
            action = "NO_TRADE"
            reasons.append("score_below_minimum")
            reasons.append("no_recent_liquidations")
            hypothesis = "Market is quiet, no significant activity."
        # Высокий стресс / Экстремальный стресс
        elif level in ("HIGH_STRESS", "EXTREME"):
            # Извлекаем компоненты для проверки условий гипотез
            comp = stress_res.components  # dict[str, float|None], значения 0..100

            liq_acc = comp.get("liq_acceleration")
            funding = comp.get("funding_rate")
            oi_chg = comp.get("oi_change")
            vol_spike = comp.get("volume_spike")
            price_mv = comp.get("price_move")

            # ── H1: Long Liquidation Bounce (PAPER_LONG_SETUP) ──
            # Лонги массово ликвидированы, ликвидации замедляются → потенциальный bounce
            h1_conditions_met = 0
            h1_conditions_total = 5
            if dominant_side == "LONGS_LIQUIDATED":
                h1_conditions_met += 1
            if stress_res.long_liq_ratio > 0.75:
                h1_conditions_met += 1
            if liq_acc is not None and liq_acc < 50.0:  # Ускорение замедляется
                h1_conditions_met += 1
            if funding is not None and funding < 50.0:  # Funding не перегрет (медвежий)
                h1_conditions_met += 1
            if "missing_context" not in reasons:
                h1_conditions_met += 1

            # ── H2: Short Squeeze Reversal (PAPER_SHORT_SETUP) ──
            # Шорты массово ликвидированы, funding перегрет → потенциальный откат
            h2_conditions_met = 0
            h2_conditions_total = 5
            if dominant_side == "SHORTS_LIQUIDATED":
                h2_conditions_met += 1
            if stress_res.short_liq_ratio > 0.75:
                h2_conditions_met += 1
            if funding is not None and funding > 50.0:  # Funding перегрет (бычий)
                h2_conditions_met += 1
            if oi_chg is not None and oi_chg > 25.0:  # OI растёт (новые позиции в тренде)
                h2_conditions_met += 1
            if "missing_context" not in reasons:
                h2_conditions_met += 1

            # ── H3: Liquidation Continuation (PAPER_CASCADE_RISK) ──
            # Каскад продолжается, OI растёт, объём подтверждает → тренд
            h3_conditions_met = 0
            h3_conditions_total = 5
            if cascade_risk in ("medium", "high"):
                h3_conditions_met += 1
            if dominant_side != "BALANCED":
                h3_conditions_met += 1
            if oi_chg is not None and oi_chg > 20.0:
                h3_conditions_met += 1
            if vol_spike is not None and vol_spike > 25.0:
                h3_conditions_met += 1
            if liq_acc is not None and liq_acc > 50.0:  # Ускорение высокое
                h3_conditions_met += 1

            # Выбираем лучшую гипотезу (максимум подтверждений, минимум 3)
            best_hyp = None
            best_met = 0
            best_total = 0

            if h1_conditions_met >= 3 and h1_conditions_met > best_met:
                best_hyp = "H1"
                best_met = h1_conditions_met
                best_total = h1_conditions_total

            if h2_conditions_met >= 3 and h2_conditions_met > best_met:
                best_hyp = "H2"
                best_met = h2_conditions_met
                best_total = h2_conditions_total

            if h3_conditions_met >= 3 and h3_conditions_met > best_met:
                best_hyp = "H3"
                best_met = h3_conditions_met
                best_total = h3_conditions_total

            if best_hyp == "H1":
                signal_type = "PAPER_LONG_SETUP"
                action = "LONG"
                hypothesis = (
                    f"H1 Long Liq Bounce: longs cascade ({stress_res.long_liq_ratio:.0%}), "
                    f"acceleration cooling, funding bearish. "
                    f"Conditions: {best_met}/{best_total}. Mean reversion buy setup."
                )
                invalidation.append("stress_score_falls_below_watch")
                invalidation.append("new_liquidations_surge")

            elif best_hyp == "H2":
                signal_type = "PAPER_SHORT_SETUP"
                action = "SHORT"
                hypothesis = (
                    f"H2 Short Squeeze Reversal: shorts squeezed ({stress_res.short_liq_ratio:.0%}), "
                    f"funding overheated, OI rising. "
                    f"Conditions: {best_met}/{best_total}. Mean reversion sell setup."
                )
                invalidation.append("stress_score_falls_below_watch")
                invalidation.append("new_liquidations_surge")

            elif best_hyp == "H3":
                signal_type = "PAPER_CASCADE_RISK"
                action = "SHORT" if dominant_side == "LONGS_LIQUIDATED" else "LONG"
                hypothesis = (
                    f"H3 Liquidation Continuation: cascade_risk={cascade_risk}, "
                    f"dominant={dominant_side}, OI growing, volume confirming. "
                    f"Conditions: {best_met}/{best_total}. Trend continuation."
                )
                invalidation.append("stress_score_drop")
                invalidation.append("dominant_side_balance")

            elif level == "EXTREME" and dominant_side != "BALANCED":
                # Fallback: EXTREME + direction но мало подтверждений → PAPER_REVERSION_WATCH
                signal_type = "PAPER_REVERSION_WATCH"
                action = "WATCH"
                hypothesis = (
                    f"Extreme stress ({score:.1f}) with {dominant_side}, "
                    f"but insufficient hypothesis confirmation "
                    f"(H1:{h1_conditions_met}/{h1_conditions_total}, "
                    f"H2:{h2_conditions_met}/{h2_conditions_total}, "
                    f"H3:{h3_conditions_met}/{h3_conditions_total}). Watching."
                )
                invalidation.append("stress_score_drop")
            else:
                signal_type = "PAPER_OBSERVE"
                action = "WATCH"
                hypothesis = (
                    f"High stress ({score:.1f}), liquidations detected but no strong hypothesis match. "
                    f"H1:{h1_conditions_met}/{h1_conditions_total}, "
                    f"H2:{h2_conditions_met}/{h2_conditions_total}, "
                    f"H3:{h3_conditions_met}/{h3_conditions_total}. Observing."
                )
                invalidation.append("stress_score_drop")
        # WATCH_ONLY
        else:
            signal_type = "WATCH_ONLY"
            action = "WATCH"
            hypothesis = f"Stress score is elevated ({score:.1f}). Watching symbol volatility."
            invalidation.append("stress_score_falls_below_20")


        # 2. Логика Cooldown
        cooldown_until_ms = None
        if last_decision is not None:
            # Проверяем, наложился ли cooldown
            time_since_last = created_at_ms - last_decision.created_at_ms
            is_same_type = last_decision.signal_type == signal_type
            
            # Проверяем приоритеты/серьезность
            curr_rank = self.SEVERITY_RANK.get(signal_type, 0)
            prev_rank = self.SEVERITY_RANK.get(last_decision.signal_type, 0)
            is_more_severe = curr_rank > prev_rank

            # Cooldown применяется только если тип сигнала тот же или менее важный,
            # и таймаут 10 минут еще не истек.
            # Защитный NO_TRADE и базовый WATCH_ONLY никогда не блокируются кулдауном.
            if (
                signal_type not in ("NO_TRADE", "WATCH_ONLY")
                and (is_same_type or not is_more_severe)
                and time_since_last < self.COOLDOWN_DURATION_MS
            ):
                # Накладываем оверрайд cooldown
                reasons.append("cooldown_active")
                cooldown_until_ms = last_decision.created_at_ms + self.COOLDOWN_DURATION_MS
                
                # Откатываем сигнал на безопасный WATCH_ONLY
                signal_type = "WATCH_ONLY"
                action = "WATCH"
                hypothesis = f"Signal generation is throttled by cooldown (active until {last_decision.ts_to_iso(cooldown_until_ms)})."

        # 3. Расчет Confidence — основан на выполнении условий гипотезы
        if is_safety_override:
            confidence = 0.0
        elif best_hyp is not None:
            # Confidence = % выполненных условий × stress weight
            confidence = (best_met / best_total) * 100.0
            # Бонус за EXTREME уровень
            if level == "EXTREME":
                confidence = min(100.0, confidence + 10.0)
        else:
            confidence = score  # Fallback для WATCH/NO_TRADE
            if "missing_context" in reasons:
                confidence -= 20.0
            if "insufficient_data" in reasons:
                confidence -= 20.0

        confidence = min(100.0, max(0.0, confidence))

        # 4. Расчет Risk Level
        if level == "NO_TRADE":
            risk_level = "CRITICAL"
        elif cascade_risk == "high" or level == "EXTREME":
            risk_level = "CRITICAL"
        elif cascade_risk == "medium" or level == "HIGH_STRESS":
            risk_level = "HIGH"
        elif level == "WATCH":
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        # 5. Определение срока действия (Expires)
        if signal_type == "WATCH_ONLY":
            duration_ms = 900000  # 15 минут
        elif signal_type in ("PAPER_LONG_SETUP", "PAPER_SHORT_SETUP", "PAPER_CASCADE_RISK", "PAPER_REVERSION_WATCH"):
            duration_ms = 300000  # 5 минут
        elif signal_type == "PAPER_OBSERVE":
            duration_ms = 600000  # 10 минут
        else:
            duration_ms = 300000  # 5 минут (NO_TRADE)

        expires_at_ms = created_at_ms + duration_ms

        return SignalDecision(
            symbol=symbol,
            signal_type=signal_type,
            action=action,
            confidence=round(confidence, 2),
            risk_level=risk_level,
            stress_level=level,
            stress_score=score,
            cascade_risk=cascade_risk,
            dominant_side=dominant_side,
            hypothesis=hypothesis,
            reasons=reasons,
            invalidation=invalidation,
            cooldown_until_ms=cooldown_until_ms,
            created_at_ms=created_at_ms,
            expires_at_ms=expires_at_ms,
            metadata=stress_res.components,
        )


def compute_and_store_signal(
    db: Database,
    engine: SignalEngine,
    stress_res: StressScoreResult,
    last_decision: SignalDecision | None = None,
) -> SignalDecision:
    """Генерирует SignalDecision и сохраняет его в таблицу signals базы данных SQLite."""
    decision = engine.generate_decision(stress_res, last_decision)

    # Сохраняем в БД, только если это не дубликат с активным cooldown-блокировщиком
    # (если cooldown активен, мы все равно генерируем WATCH_ONLY в RAM, но в БД пишем только оригинальный сигнал)
    if "cooldown_active" not in decision.reasons:
        record = SignalRecord(
            symbol=decision.symbol,
            signal_time_ms=decision.created_at_ms,
            signal_type=decision.signal_type,
            level=decision.risk_level,
            hypothesis=decision.hypothesis,
            direction=decision.action,
            confidence=decision.confidence,
            reason=", ".join(decision.reasons),
            stress_score=decision.stress_score,
            metadata_json={
                "invalidation": decision.invalidation,
                "cooldown_until_ms": decision.cooldown_until_ms,
                "expires_at_ms": decision.expires_at_ms,
                "cascade_risk": decision.cascade_risk,
                "dominant_side": decision.dominant_side,
                "components": decision.metadata,
            },
            created_at_ms=int(time.time() * 1000),
        )
        db.insert_signal(record)

    return decision
