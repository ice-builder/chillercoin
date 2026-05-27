"""
IIE — Coin Scoring Engine

Builds per-coin profiles from accumulated impulse + outcome data.
Recalculates every hour.

Each coin gets a profile with:
  - impulse_quality_score (0-100): how often impulses continue
  - predictability_score (0-100): consistency of behavior
  - momentum_persistence: does price continue or stop after 1-2 bars?
  - stop_hunt_frequency: % of impulses that were stop hunts
  - recommended_sl_mult: optimal SL multiplier based on historical outcomes
  - recommended_hold_bars: optimal holding time
  - recommended_position_size: scaling factor (0.5-2.0) for position sizing
"""
import time
import logging
import numpy as np
from typing import List, Dict, Optional

from . import config
from .impulse_db import ImpulseDB, CoinProfile

logger = logging.getLogger("iie.scorer")


class CoinScorer:
    """Builds and updates coin profiles from impulse data."""

    def __init__(self, db: ImpulseDB):
        self.db = db

    def run_scoring(self) -> int:
        """Score all coins with enough data. Returns number of profiles updated."""
        symbols = self.db.get_unique_symbols_with_impulses()
        updated = 0

        for symbol in symbols:
            data = self.db.get_impulses_for_coin(symbol, limit=500)
            completed = [d for d in data if d.get("tracking_complete")]

            if len(completed) < config.COIN_SCORER_MIN_IMPULSES:
                continue

            profile = self._build_profile(symbol, completed)
            if profile:
                self.db.upsert_coin_profile(profile)
                updated += 1

        if updated > 0:
            logger.info(f"🪙 Coin Scorer: updated {updated} profiles")
        return updated

    def _build_profile(self, symbol: str, data: List[dict]) -> Optional[CoinProfile]:
        """Build a CoinProfile from completed impulse+outcome data."""
        if not data:
            return None

        n = len(data)
        now = time.time()

        # ─── Continuation analysis ───────────────
        # How often does the impulse continue vs reverse?
        favorable_moves = [d.get("max_favorable_pct", 0) for d in data]
        adverse_moves = [d.get("max_adverse_pct", 0) for d in data]

        avg_continuation = float(np.mean(favorable_moves)) if favorable_moves else 0
        avg_retracement = float(np.mean(adverse_moves)) if adverse_moves else 0

        # ─── Stop hunt frequency ─────────────────
        stop_hunts = sum(1 for d in data if d.get("was_stop_hunt"))
        stop_hunt_freq = (stop_hunts / n * 100) if n > 0 else 0

        # ─── Momentum persistence ────────────────
        # How many impulses resulted in favorable > adverse?
        momentum_wins = sum(
            1 for d in data
            if d.get("max_favorable_pct", 0) > d.get("max_adverse_pct", 0)
        )
        momentum_persistence = (momentum_wins / n * 100) if n > 0 else 50

        # ─── Impulse quality score (0-100) ───────
        # Weighted combination of continuation, stop hunt avoidance, consistency
        quality = 0.0

        # Factor 1: Average risk-reward of impulses (30% weight)
        if avg_retracement > 0:
            rr_ratio = avg_continuation / avg_retracement
            quality += min(30, rr_ratio * 10)
        elif avg_continuation > 0:
            quality += 30  # No adverse = perfect

        # Factor 2: Low stop hunt rate (25% weight)
        quality += max(0, 25 - stop_hunt_freq * 0.5)

        # Factor 3: Momentum persistence (25% weight)
        quality += momentum_persistence * 0.25

        # Factor 4: New extremum rate (20% weight)
        new_extremum_count = sum(1 for d in data if d.get("new_extremum"))
        extremum_rate = (new_extremum_count / n * 100) if n > 0 else 0
        quality += min(20, extremum_rate * 0.4)

        quality = max(0, min(100, quality))

        # ─── Predictability score ────────────────
        # Low std = predictable behavior
        if len(favorable_moves) >= 5:
            std_fav = float(np.std(favorable_moves))
            mean_fav = float(np.mean(favorable_moves)) if float(np.mean(favorable_moves)) > 0 else 1
            cv = std_fav / mean_fav  # Coefficient of variation
            predictability = max(0, min(100, 100 - cv * 30))
        else:
            predictability = 50

        # ─── Optimal hold time ───────────────────
        # Find checkpoint where max_favorable peaks
        checkpoints = {
            "price_after_1h": 1,
            "price_after_4h": 4,
            "price_after_24h": 24,
        }
        best_hold_hours = 4  # default
        best_hold_gain = 0

        for col, hours in checkpoints.items():
            prices = [d.get(col, 0) for d in data if d.get(col, 0) > 0]
            entry_prices = [d.get("price_at_impulse", 0) for d in data if d.get(col, 0) > 0]
            if not prices or not entry_prices:
                continue

            gains = []
            for i, (p, e) in enumerate(zip(prices, entry_prices)):
                if e > 0:
                    direction = data[i].get("direction", "long")
                    if direction == "long":
                        gains.append((p / e - 1.0) * 100)
                    else:
                        gains.append((1.0 - p / e) * 100)

            if gains:
                avg_gain = float(np.mean(gains))
                if avg_gain > best_hold_gain:
                    best_hold_gain = avg_gain
                    best_hold_hours = hours

        # Convert to bars (approximate: 5m bars)
        recommended_hold = best_hold_hours * 12  # 12 five-min bars per hour

        # ─── Optimal SL multiplier ───────────────
        # Based on adverse moves distribution
        if adverse_moves:
            # SL should survive 80% of adverse moves
            p80 = float(np.percentile(adverse_moves, 80))
            # Convert to ATR multiplier (rough: avg adverse / 1.0 = baseline)
            sl_mult = max(0.8, min(3.0, p80 / max(0.5, avg_retracement) * 1.5))
        else:
            sl_mult = 1.5

        # ─── Best timeframe ─────────────────────
        tf_scores = {}
        for d in data:
            tf = d.get("timeframe", "5")
            fav = d.get("max_favorable_pct", 0)
            adv = d.get("max_adverse_pct", 0)
            if tf not in tf_scores:
                tf_scores[tf] = {"total_rr": 0, "count": 0}
            rr = fav / max(0.1, adv)
            tf_scores[tf]["total_rr"] += rr
            tf_scores[tf]["count"] += 1

        best_tf = "5"
        best_avg_rr = 0
        for tf, info in tf_scores.items():
            avg_rr = info["total_rr"] / max(1, info["count"])
            if avg_rr > best_avg_rr:
                best_avg_rr = avg_rr
                best_tf = tf

        # ─── Impulse regularity ──────────────────
        # How regularly do impulses occur?
        timestamps = sorted([d.get("timestamp", 0) for d in data if d.get("timestamp", 0) > 0])
        if len(timestamps) >= 3:
            intervals = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
            avg_interval = float(np.mean(intervals))
            std_interval = float(np.std(intervals))
            regularity = max(0, min(100, 100 - (std_interval / max(1, avg_interval)) * 50))
        else:
            regularity = 50

        # ─── Volatility regime ───────────────────
        avg_combined = float(np.mean([d.get("combined_score", 0) for d in data]))
        if avg_combined > 15:
            vol_regime = "extreme"
        elif avg_combined > 10:
            vol_regime = "high"
        elif avg_combined > 6:
            vol_regime = "medium"
        else:
            vol_regime = "low"

        # ─── Listing age ─────────────────────────
        if timestamps:
            first_seen = min(timestamps)
            listing_age = int((now - first_seen) / 86400)
        else:
            listing_age = 0

        profile = CoinProfile(
            symbol=symbol,
            impulse_count=n,
            avg_continuation_pct=round(avg_continuation, 3),
            avg_retracement_pct=round(avg_retracement, 3),
            stop_hunt_frequency=round(stop_hunt_freq, 1),
            avg_time_to_extremum=best_hold_hours,
            level_respect_score=round(extremum_rate, 1),
            volatility_regime=vol_regime,
            listing_age_days=listing_age,
            impulse_regularity=round(regularity, 1),
            best_tf=best_tf,
            recommended_sl_mult=round(sl_mult, 2),
            recommended_hold_bars=recommended_hold,
            momentum_persistence=round(momentum_persistence, 1),
            impulse_quality_score=round(quality, 1),
            predictability_score=round(predictability, 1),
            last_updated=now,
        )

        logger.debug(
            f"  {symbol}: quality={quality:.0f} momentum={momentum_persistence:.0f}% "
            f"hunts={stop_hunt_freq:.0f}% sl×{sl_mult:.1f} hold={recommended_hold}bars"
        )

        return profile
