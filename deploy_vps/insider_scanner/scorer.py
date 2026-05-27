"""Scorer — Combines OI + CEX flow signals into composite insider score."""
import logging
from typing import Dict, List, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

from oi_tracker import OIChange
from cex_flow import SpotFlowSignal
import config

logger = logging.getLogger("insider.scorer")


@dataclass
class InsiderScore:
    """Composite score for a token showing insider activity."""
    symbol: str
    total_score: int = 0
    breakdown: Dict[str, int] = field(default_factory=dict)

    # OI data
    oi_exchanges: List[str] = field(default_factory=list)
    oi_best_change_1h: float = 0.0
    oi_best_z_score: float = 0.0

    # Flow data
    flow_exchanges: List[str] = field(default_factory=list)
    flow_best_z_score: float = 0.0
    flow_best_buy_ratio: float = 0.0

    # Price context
    price: float = 0.0
    price_change_24h: float = 0.0
    total_oi_usd: float = 0.0

    @property
    def is_alert(self) -> bool:
        return self.total_score >= config.ALERT_THRESHOLD

    @property
    def is_auto_enter(self) -> bool:
        return self.total_score >= config.AUTO_ENTER_THRESHOLD


def score_tokens(
    oi_anomalies: List[OIChange],
    flow_signals: List[SpotFlowSignal],
    weekly_trending: Dict[str, int] = None,
    tg_alerts: Dict[str, list] = None,
) -> List[InsiderScore]:
    """Score all tokens based on OI anomalies + CEX flow signals.
    Returns list sorted by total_score descending.

    Args:
        oi_anomalies: OI changes flagged as anomalous
        flow_signals: CEX spot flow signals
        weekly_trending: {symbol: consecutive_growth_days} from weekly tracker
        tg_alerts: {symbol: [alert_dicts]} from TG channel parser (CryptoArsenal etc.)
    """
    weekly_trending = weekly_trending or {}
    tg_alerts = tg_alerts or {}

    # Group by normalized symbol
    oi_by_symbol: Dict[str, List[OIChange]] = defaultdict(list)
    flow_by_symbol: Dict[str, List[SpotFlowSignal]] = defaultdict(list)

    for oi in oi_anomalies:
        oi_by_symbol[oi.symbol].append(oi)
    for flow in flow_signals:
        flow_by_symbol[flow.symbol].append(flow)

    # All unique symbols with any signal (including TG alerts)
    all_symbols = set(oi_by_symbol.keys()) | set(flow_by_symbol.keys()) | set(tg_alerts.keys())

    # ─── Pre-compute syndicate map ────────────────
    # Syndicate = multiple small-cap tokens surging on same exchange simultaneously
    # If 3+ low-OI tokens spike on one exchange → coordinated pump
    exchange_surge_count: Dict[str, int] = defaultdict(int)
    for oi in oi_anomalies:
        # Count only small-cap tokens surging per exchange
        if oi.current_oi < config.LOW_OI_THRESHOLD_USD:
            exchange_surge_count[oi.exchange] += 1
    syndicate_exchanges = {
        ex for ex, cnt in exchange_surge_count.items() if cnt >= config.SYNDICATE_MIN_TOKENS
    }
    if syndicate_exchanges:
        logger.info(
            f"🕸️ Syndicate pattern: {', '.join(syndicate_exchanges)} "
            f"({', '.join(f'{ex}={exchange_surge_count[ex]}' for ex in syndicate_exchanges)})"
        )

    scores = []
    for symbol in all_symbols:
        if symbol in config.BLACKLIST:
            continue

        score = InsiderScore(symbol=symbol)
        oi_list = oi_by_symbol.get(symbol, [])
        flow_list = flow_by_symbol.get(symbol, [])

        # ─── OI Scoring ───────────────────────────
        oi_exchanges = list(set(o.exchange for o in oi_list))
        score.oi_exchanges = oi_exchanges

        if oi_list:
            score.oi_best_change_1h = max(o.change_1h_pct for o in oi_list)
            score.oi_best_z_score = max(o.z_score_1h for o in oi_list)
            score.total_oi_usd = sum(o.current_oi for o in oi_list)

        n_oi_ex = len(oi_exchanges)
        if n_oi_ex >= 3:
            score.breakdown["oi_surge_3_exchanges"] = config.SCORE_WEIGHTS["oi_surge_3_exchanges"]
        elif n_oi_ex >= 2:
            score.breakdown["oi_surge_2_exchanges"] = config.SCORE_WEIGHTS["oi_surge_2_exchanges"]
        elif n_oi_ex >= 1:
            score.breakdown["oi_surge_1_exchange"] = config.SCORE_WEIGHTS["oi_surge_1_exchange"]

        # Bonus: #1 in OI rankings (highest z-score)
        if score.oi_best_z_score >= 5.0:
            score.breakdown["oi_leader_bonus"] = config.SCORE_WEIGHTS["oi_leader_bonus"]

        # Bonus: OI rising on both 1h and 4h
        if oi_list and any(o.change_4h_pct >= config.OI_CHANGE_4H_MIN for o in oi_list):
            score.breakdown["multi_tf_oi"] = config.SCORE_WEIGHTS["multi_tf_oi"]

        # ─── NEW: Bitget/MEXC origin bonus ────────
        # Pump launchpad exchanges (LAB, TAG, RAVE all originated here)
        if oi_list:
            origin_exchanges = set(o.exchange for o in oi_list)
            if origin_exchanges & config.PUMP_ORIGIN_EXCHANGES:
                score.breakdown["bitget_origin_bonus"] = config.SCORE_WEIGHTS["bitget_origin_bonus"]

        # ─── NEW: Small cap bonus ─────────────────
        # Low total OI = micro/small cap → easier to manipulate
        if score.total_oi_usd > 0 and score.total_oi_usd < config.LOW_OI_THRESHOLD_USD:
            score.breakdown["small_cap_bonus"] = config.SCORE_WEIGHTS["small_cap_bonus"]

        # ─── NEW: Weekly OI trend ─────────────────
        # Multi-day OI accumulation (like LAB's 1-month pre-pump pattern)
        if symbol in weekly_trending:
            growth_days = weekly_trending[symbol]
            score.breakdown["weekly_oi_trend"] = config.SCORE_WEIGHTS["weekly_oi_trend"]
            logger.info(f"📈 {symbol}: {growth_days}-day OI accumulation detected")

        # ─── NEW: Syndicate detection ─────────────
        # Multiple small tokens pumping on same exchange = coordinated group
        if oi_list:
            token_exchanges = set(o.exchange for o in oi_list)
            if token_exchanges & syndicate_exchanges:
                score.breakdown["syndicate_bonus"] = config.SCORE_WEIGHTS["syndicate_bonus"]

        # ─── CEX Flow Scoring ─────────────────────
        flow_exchanges = list(set(f.exchange for f in flow_list))
        score.flow_exchanges = flow_exchanges

        if flow_list:
            score.flow_best_z_score = max(f.z_score for f in flow_list)
            score.flow_best_buy_ratio = max(f.buy_ratio for f in flow_list)
            score.price = flow_list[0].price
            score.price_change_24h = flow_list[0].price_change_24h

        n_flow_ex = len(flow_exchanges)
        if n_flow_ex >= 2:
            score.breakdown["spot_flow_2_exchanges"] = config.SCORE_WEIGHTS["spot_flow_2_exchanges"]
        elif n_flow_ex >= 1:
            score.breakdown["spot_flow_1_exchange"] = config.SCORE_WEIGHTS["spot_flow_1_exchange"]

        # ─── Confluence Bonus ─────────────────────
        # OI + Spot buy on the same exchange
        common_exchanges = set(oi_exchanges) & set(flow_exchanges)
        if common_exchanges:
            score.breakdown["confluence_bonus"] = config.SCORE_WEIGHTS["confluence_bonus"]

        # ─── Early Entry Bonus ────────────────────
        # Price still near 24h low (<20% above)
        if score.price_change_24h < 20 and (oi_list or flow_list):
            score.breakdown["early_entry_bonus"] = config.SCORE_WEIGHTS["early_entry_bonus"]

        # ─── TG Signal Bonus ──────────────────────
        # External confirmation from CryptoArsenal/CryptoAttack channels
        if symbol in tg_alerts:
            tg_list = tg_alerts[symbol]
            has_buying = any(a.get("direction") == "buy" for a in tg_list)
            if has_buying:
                score.breakdown["tg_buying_confirmed"] = 3
            elif tg_list:
                score.breakdown["tg_activity_detected"] = 2

        # ─── Total ────────────────────────────────
        score.total_score = sum(score.breakdown.values())
        if score.total_score > 0:
            scores.append(score)

    scores.sort(key=lambda s: s.total_score, reverse=True)
    return scores


def format_score_report(score: InsiderScore) -> str:
    """Format a human-readable score report for TG."""
    tier = "🔴 АВТО-ВХОД" if score.is_auto_enter else "🟡 СИГНАЛ"

    oi_info = ""
    if score.oi_exchanges:
        oi_info = (
            f"📊 OI рост: {', '.join(score.oi_exchanges)}\n"
            f"   Лучший за 1ч: +{score.oi_best_change_1h:.1f}% "
            f"(z={score.oi_best_z_score:.1f})\n"
            f"   Общий OI: ${score.total_oi_usd:,.0f}\n"
        )

    flow_info = ""
    if score.flow_exchanges:
        flow_info = (
            f"💰 Покупки (spot): {', '.join(score.flow_exchanges)}\n"
            f"   Z-скор объёма: {score.flow_best_z_score:.1f} "
            f"| Доля покупок: {score.flow_best_buy_ratio:.2f}\n"
        )

    breakdown = " + ".join(f"{k}({v})" for k, v in score.breakdown.items())

    return (
        f"{tier} *#{score.symbol}*\n"
        f"Скор: *{score.total_score}* / {config.AUTO_ENTER_THRESHOLD}\n"
        f"\n"
        f"{oi_info}"
        f"{flow_info}"
        f"💵 Цена: ${score.price:.6g} ({score.price_change_24h:+.1f}% 24ч)\n"
        f"\n"
        f"📋 {breakdown}"
    )
