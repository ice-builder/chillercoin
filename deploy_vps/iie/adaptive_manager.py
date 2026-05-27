"""
IIE — Adaptive Position Manager

Central decision engine used by all bots. Provides:
  1. Signal quality gate (should we enter?)
  2. Adaptive SL/TP based on coin profile
  3. Adaptive position sizing (5-20% based on confidence)
  4. Hold time recommendations
  5. Post-exit analysis scheduling
  6. v7.1: Anomaly Multiplier — extreme impulses (combined_score 200+)
     get wider TP, larger size, and wider trail to capture full move

Uses coin_profiles + impulse_predictor + market_phase to make decisions.
"""
import time
import math
import logging
from typing import Optional, Dict, Tuple
from dataclasses import dataclass, field

from . import config
from .impulse_db import ImpulseDB, Impulse, CoinProfile, TradeOutcome
from .impulse_predictor import ImpulsePredictor

logger = logging.getLogger("iie.adaptive")

# ─── v7.1: Anomaly Tiers ──────────────────────────────────
# Defines how extreme combined_score values affect trading parameters.
# Historical data: ALCH (435) → +300%, API3 (282) → +150%, AIGENSYN (257) → TBD
# Stronger anomalies = stronger continuation probability = bigger opportunity.
ANOMALY_TIERS = {
    # (min_combined, max_combined): (size_mult, tp_mult, trail_mult, label)
    "normal":    (0,    30,   1.0, 1.0, 1.0, "Normal"),
    "strong":    (30,   100,  1.3, 1.5, 1.2, "Strong"),
    "extreme":   (100,  200,  1.5, 2.0, 1.5, "Extreme"),
    "anomaly":   (200,  9999, 2.0, 3.0, 2.0, "🔥 ANOMALY"),
}


@dataclass
class TradeRecommendation:
    """IIE recommendation for a trade signal."""
    should_enter: bool = False
    confidence: float = 0.0       # 0-100
    score: float = 0.0            # composite score
    reason: str = ""              # why enter/reject

    # Adaptive parameters
    recommended_sl_pct: float = 1.0
    recommended_tp_pct: float = 3.0
    recommended_hold_bars: int = 10
    recommended_trail_pct: float = 0.15

    # Position sizing (multiplier: 0.5 = half, 1.0 = normal, 2.0 = double)
    position_size_mult: float = 1.0
    position_size_reason: str = ""

    # ML predictions
    will_continue_prob: float = 0.5
    predicted_favorable_pct: float = 0.0
    stop_hunt_prob: float = 0.5

    # Context
    market_phase: str = ""
    coin_quality: float = 50.0

    # v7.1: Anomaly tier info
    anomaly_tier: str = "normal"
    combined_score: float = 0.0


class AdaptivePositionManager:
    """
    Decision engine that combines coin profiles, ML predictions,
    and market phase to optimize trade parameters.
    """

    def __init__(self, db: Optional[ImpulseDB] = None):
        self.db = db or ImpulseDB()
        self.predictor = ImpulsePredictor(self.db)
        self._phase_cache = None
        self._phase_cache_ts = 0

    def evaluate_signal(
        self,
        symbol: str,
        direction: str,
        vol_z: float = 0,
        ret_z: float = 0,
        rsi: float = 50,
        combined_score: float = 0,
        ema_deviation: float = 0,
        candle_body_pct: float = 50,
        wick_top: float = 0,
        wick_bottom: float = 0,
        impulse_location: str = "mid_range",
        atr: float = 0,
        source: str = "",
        base_sl_pct: float = 1.0,
        base_tp_pct: float = 3.0,
    ) -> TradeRecommendation:
        """
        Evaluate a trading signal and return recommendation.

        Called by bots before entering a trade.
        """
        rec = TradeRecommendation()
        rec.recommended_sl_pct = base_sl_pct
        rec.recommended_tp_pct = base_tp_pct

        # 1. Get coin profile
        profile = self.db.get_coin_profile(symbol)
        if profile:
            rec.coin_quality = profile.impulse_quality_score

        # 2. Get market phase
        phase = self._get_phase()
        rec.market_phase = phase.phase if phase else "unknown"

        # 3. ML prediction (if model is trained)
        imp = Impulse(
            symbol=symbol, direction=direction,
            vol_z=vol_z, ret_z=ret_z, combined_score=combined_score,
            rsi_at_impulse=rsi, ema_deviation_pct=ema_deviation,
            candle_body_pct=candle_body_pct,
            wick_ratio_top=wick_top, wick_ratio_bottom=wick_bottom,
            impulse_location=impulse_location,
            atr_at_impulse=atr, timestamp=time.time(),
        )
        profile_dict = {
            "impulse_quality_score": profile.impulse_quality_score if profile else 50,
            "stop_hunt_frequency": profile.stop_hunt_frequency if profile else 25,
            "momentum_persistence": profile.momentum_persistence if profile else 50,
            "level_respect_score": profile.level_respect_score if profile else 50,
        }
        prediction = self.predictor.predict(imp, profile_dict)
        rec.will_continue_prob = prediction["will_continue_prob"]
        rec.predicted_favorable_pct = prediction["predicted_favorable_pct"]
        rec.stop_hunt_prob = prediction["stop_hunt_prob"]
        rec.confidence = prediction["confidence"]

        # 4. Compute composite score
        score = self._compute_score(
            profile, phase, prediction, combined_score, direction)
        rec.score = score

        # v7.1: Determine anomaly tier and store in recommendation
        tier_name, _, _, _, tier_label = self._get_anomaly_tier(combined_score)
        rec.anomaly_tier = tier_name
        rec.combined_score = combined_score

        # 5. Adapt SL/TP/Hold based on profile + anomaly tier
        self._adapt_parameters(rec, profile, prediction)

        # 6. Adapt position size (with anomaly scaling)
        self._adapt_position_size(rec, profile, prediction, phase, combined_score)

        # 7. Entry decision
        tier_suffix = f" [{tier_label}]" if tier_name != "normal" else ""
        if score >= 60:
            rec.should_enter = True
            rec.reason = f"IIE score {score:.0f}: strong signal{tier_suffix}"
        elif score >= 40:
            rec.should_enter = True
            rec.reason = f"IIE score {score:.0f}: moderate signal{tier_suffix}"
        else:
            rec.should_enter = False
            rec.reason = f"IIE score {score:.0f}: weak — skip"

        # Override: block high stop-hunt probability
        if rec.stop_hunt_prob > 0.7 and rec.confidence > 30:
            rec.should_enter = False
            rec.reason = f"Stop hunt likely ({rec.stop_hunt_prob:.0%})"
            rec.position_size_mult = 0.5

        # Override: block if coin quality is terrible
        if profile and profile.impulse_quality_score < 20 and profile.impulse_count >= 20:
            rec.should_enter = False
            rec.reason = f"Coin quality too low ({profile.impulse_quality_score:.0f})"

        # v7.1: Log anomaly tier signals
        if tier_name != "normal":
            logger.info(
                f"🔥 {tier_label} detected: combined_z={combined_score:.0f} "
                f"→ size×{rec.position_size_mult:.1f} TP={rec.recommended_tp_pct:.1f}% "
                f"trail={rec.recommended_trail_pct:.3f}"
            )

        return rec

    @staticmethod
    def _get_anomaly_tier(combined_z: float) -> Tuple[str, float, float, float, str]:
        """Get anomaly tier for a given combined z-score.
        Returns: (tier_name, size_mult, tp_mult, trail_mult, label)
        """
        for tier_name, (lo, hi, s_mult, tp_mult, tr_mult, label) in ANOMALY_TIERS.items():
            if lo <= combined_z < hi:
                return tier_name, s_mult, tp_mult, tr_mult, label
        return "normal", 1.0, 1.0, 1.0, "Normal"

    def _compute_score(self, profile, phase, prediction, combined_z, direction) -> float:
        """Composite score 0-100.
        v7.1: Uses logarithmic scaling for combined_score instead of linear cap.
        This lets extreme impulses (200+) meaningfully boost the score.
        """
        score = 30.0  # base

        # v7.1: Z-score strength — logarithmic scaling (0-30)
        # Old: min(25, combined_z * 2) — capped at combined=12.5, wasting 200+ scores
        # New: log curve that keeps rising for extreme values
        if combined_z > 0:
            # log2(8)=3→6pts, log2(30)=~5→10pts, log2(100)=~6.6→13pts, log2(257)=~8→16pts
            z_contribution = min(30, math.log2(max(1, combined_z)) * 4)
            score += z_contribution
        else:
            score += 0

        # ML prediction (0-20)
        if prediction.get("confidence", 0) > 10:
            cont_prob = prediction.get("will_continue_prob", 0.5)
            score += (cont_prob - 0.5) * 40  # ±20

        # Coin quality (0-15)
        if profile:
            score += (profile.impulse_quality_score - 50) * 0.3  # ±15

        # Market phase alignment (0-10)
        if phase:
            if phase.phase == "trending_up" and direction == "long":
                score += 10
            elif phase.phase == "trending_down" and direction == "short":
                score += 10
            elif phase.phase == "trending_up" and direction == "short":
                score -= 5
            elif phase.phase == "trending_down" and direction == "long":
                score -= 5

        return max(0, min(100, score))

    def _adapt_parameters(self, rec, profile, prediction):
        """Adapt SL/TP/Hold/Trail based on coin profile + anomaly tier."""
        if not profile or profile.impulse_count < config.COIN_SCORER_MIN_IMPULSES:
            # Even without a profile, apply anomaly tier adjustments
            self._apply_anomaly_adjustments(rec)
            return

        # SL: use coin's recommended multiplier
        rec.recommended_sl_pct = rec.recommended_sl_pct * profile.recommended_sl_mult

        # If stop hunt is likely, widen SL
        if prediction.get("stop_hunt_prob", 0) > 0.5:
            rec.recommended_sl_pct *= 1.3

        # Hold time
        rec.recommended_hold_bars = profile.recommended_hold_bars

        # Trail: tighter for high-momentum coins, wider for erratic ones
        if profile.momentum_persistence > 70:
            rec.recommended_trail_pct = 0.10  # Tight trail
        elif profile.momentum_persistence < 30:
            rec.recommended_trail_pct = 0.25  # Wide trail
        else:
            rec.recommended_trail_pct = 0.15

        # TP: based on predicted favorable
        if prediction.get("predicted_favorable_pct", 0) > 0:
            rec.recommended_tp_pct = prediction["predicted_favorable_pct"] * 0.8

        # v7.1: Apply anomaly tier on top of profile-based params
        self._apply_anomaly_adjustments(rec)

    def _apply_anomaly_adjustments(self, rec: TradeRecommendation):
        """v7.1: Scale TP and trail based on anomaly tier.

        Extreme impulses historically lead to massive moves:
          - ALCHUSDT combined=435 → pumped +300%
          - API3USDT combined=282 → pumped +150%

        We scale TP wider and trail wider to capture more of these moves.
        SL is NOT widened — anomaly doesn't reduce stop-hunt risk.
        """
        tier = rec.anomaly_tier
        if tier == "normal":
            return

        _, _, tp_mult, trail_mult, label = self._get_anomaly_tier(rec.combined_score)

        old_tp = rec.recommended_tp_pct
        old_trail = rec.recommended_trail_pct

        # Scale TP wider for anomalies (ride the move)
        rec.recommended_tp_pct = round(rec.recommended_tp_pct * tp_mult, 2)

        # Scale trail wider for anomalies (don't get shaken out)
        rec.recommended_trail_pct = round(rec.recommended_trail_pct * trail_mult, 3)

        # Extend hold time for anomalies (these moves develop over hours)
        if tier == "anomaly":
            rec.recommended_hold_bars = max(rec.recommended_hold_bars, 96)  # 8h min
        elif tier == "extreme":
            rec.recommended_hold_bars = max(rec.recommended_hold_bars, 72)  # 6h min

        logger.info(
            f"🔥 Anomaly adjustment [{label}] combined={rec.combined_score:.0f}: "
            f"TP {old_tp:.1f}%→{rec.recommended_tp_pct:.1f}% "
            f"trail {old_trail:.3f}→{rec.recommended_trail_pct:.3f} "
            f"hold≥{rec.recommended_hold_bars}bars"
        )

    def _adapt_position_size(self, rec, profile, prediction, phase, combined_z: float = 0):
        """
        Adaptive position sizing (user request: scale based on confidence).
        Multiplier: 0.5 (halved) to 2.0 (doubled) relative to base size.

        v7.1: Anomaly tier now contributes to position sizing.
        """
        mult = 1.0
        reasons = []

        # Factor 1: ML confidence and prediction
        if prediction.get("confidence", 0) > 30:
            cont_prob = prediction.get("will_continue_prob", 0.5)
            if cont_prob > 0.7:
                mult *= 1.3
                reasons.append(f"ML conf high ({cont_prob:.0%})")
            elif cont_prob < 0.35:
                mult *= 0.6
                reasons.append(f"ML conf low ({cont_prob:.0%})")

        # Factor 2: Coin quality
        if profile and profile.impulse_count >= 15:
            if profile.impulse_quality_score > 75:
                mult *= 1.3
                reasons.append(f"quality {profile.impulse_quality_score:.0f}")
            elif profile.impulse_quality_score < 30:
                mult *= 0.6
                reasons.append(f"low quality {profile.impulse_quality_score:.0f}")

        # Factor 3: Stop hunt history
        if profile and profile.stop_hunt_frequency > 50:
            mult *= 0.7
            reasons.append(f"hunts {profile.stop_hunt_frequency:.0f}%")

        # Factor 4: Market phase alignment
        if phase:
            if phase.phase == "volatile":
                mult *= 0.7
                reasons.append("volatile market")
            elif phase.phase in ("trending_up", "trending_down"):
                # Aligned direction gets a bonus
                if ((phase.phase == "trending_up" and rec.recommended_sl_pct > 0) or
                    (phase.phase == "trending_down" and rec.recommended_sl_pct > 0)):
                    mult *= 1.1

        # v7.1 Factor 5: Anomaly tier — extreme impulses get bigger positions
        tier_name, tier_size_mult, _, _, tier_label = self._get_anomaly_tier(combined_z)
        if tier_size_mult > 1.0:
            mult *= tier_size_mult
            reasons.append(f"{tier_label} z={combined_z:.0f}")

        # Clamp to [0.5, 2.5] — v7.1: raised cap from 2.0 to 2.5 for anomalies
        mult = max(0.5, min(2.5, mult))
        rec.position_size_mult = round(mult, 2)
        rec.position_size_reason = " | ".join(reasons) if reasons else "default"

    def _get_phase(self):
        """Get cached market phase."""
        now = time.time()
        if self._phase_cache and now - self._phase_cache_ts < 1800:
            return self._phase_cache
        self._phase_cache = self.db.get_current_phase()
        self._phase_cache_ts = now
        return self._phase_cache

    # ─── Post-Trade Recording ────────────────────

    def record_trade_outcome(
        self,
        symbol: str,
        exchange: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        pnl_pct: float,
        exit_reason: str,
        strategy_name: str,
        bot_name: str,
        entry_time: float = 0,
        exit_time: float = 0,
    ):
        """Record a completed trade into IIE for learning."""
        phase = self._get_phase()
        trade = TradeOutcome(
            symbol=symbol, exchange=exchange, direction=direction,
            entry_price=entry_price, exit_price=exit_price,
            pnl_pct=pnl_pct, exit_reason=exit_reason,
            strategy_name=strategy_name, bot_name=bot_name,
            market_phase_at_entry=phase.phase if phase else "",
            entry_time=entry_time or time.time() - 3600,
            exit_time=exit_time or time.time(),
        )
        self.db.insert_trade(trade)
        logger.info(
            f"📝 Recorded trade: {symbol} {direction} {pnl_pct:+.2f}% ({exit_reason}) [{bot_name}]")

    def retrain_if_needed(self) -> bool:
        """Check if retraining is needed and run it."""
        now = time.time()
        if now - self.predictor.last_train_time < config.PREDICTOR_RETRAIN_INTERVAL_SEC:
            return False
        return self.predictor.train()
