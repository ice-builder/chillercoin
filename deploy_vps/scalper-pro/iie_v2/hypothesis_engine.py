"""
Scalper Pro — IIE v2 Hypothesis Engine

Each closed trade with completed checkpoints becomes a "realized hypothesis".
The engine builds and updates trading hypotheses per (symbol, direction, score_bin,
market_phase, impulse_location).

Learning algorithm:
  1. Trade closes → Post-Trade Analyzer collects 4 checkpoints (4h)
  2. All data collected → hypothesis_engine.update(trade, checkpoints)
  3. Find matching hypothesis by (symbol, direction, score_bin, phase, location)
  4. Update rolling statistics (window=50)
  5. Recalculate optimal SL/TP/hold/trail
  6. If sample_count >= 10 → hypothesis is "mature", used for next trades
"""
import time
import math
import logging
from typing import Optional, List, Dict

import config
from iie_v2.database import ScalperProDB, Hypothesis

logger = logging.getLogger("scalper.hypothesis")


def _score_to_bin(score: float) -> str:
    """Map IIE score to bin label."""
    for lo, hi, label in config.HYPOTHESIS_SCORE_BINS:
        if lo <= score < hi:
            return label
    return "extreme"


def _make_hypothesis_id(symbol: str, direction: str, score_bin: str,
                         market_phase: str, impulse_location: str) -> str:
    """Create deterministic hypothesis ID."""
    return f"{symbol}_{direction}_{score_bin}_{market_phase}_{impulse_location}"


class HypothesisEngine:
    """
    Builds, updates, and queries trading hypotheses from realized trades.
    """

    def __init__(self, db: ScalperProDB):
        self.db = db

    def get_recommendation(self, symbol: str, direction: str,
                           iie_score: float, market_phase: str,
                           impulse_location: str) -> Optional[dict]:
        """
        Query best matching mature hypothesis for a new signal.
        Returns dict with optimal parameters or None if no mature hypothesis.
        """
        score_bin = _score_to_bin(iie_score)
        hyp = self.db.get_matching_hypothesis(
            symbol, direction, score_bin, market_phase, impulse_location
        )
        if not hyp:
            return None

        return {
            "hypothesis_id": hyp["id"],
            "sample_count": hyp["sample_count"],
            "win_rate": hyp["win_rate"],
            "avg_pnl": hyp["avg_pnl"],
            "optimal_sl_pct": hyp["optimal_sl_pct"],
            "optimal_tp_pct": hyp["optimal_tp_pct"],
            "optimal_hold_bars": hyp["optimal_hold_bars"],
            "optimal_trail_pct": hyp["optimal_trail_pct"],
            "should_scale_in": bool(hyp["should_scale_in"]),
            "scale_in_trigger": hyp["scale_in_trigger"],
            "should_cut_early": bool(hyp["should_cut_early"]),
            "pct_profitable_15m": hyp["pct_profitable_15m"],
            "pct_profitable_1h": hyp["pct_profitable_1h"],
            "pct_profitable_4h": hyp["pct_profitable_4h"],
            "is_mature": bool(hyp["is_mature"]),
            "match_type": "exact" if hyp["symbol"] == symbol else "fallback",
        }

    def update_from_trade(self, trade: dict, checkpoints: List[dict]):
        """
        Update hypothesis from a closed trade with completed checkpoints.

        Args:
            trade: dict from pro_trades table
            checkpoints: list of dicts from trade_checkpoints table
        """
        symbol = trade["symbol"]
        direction = trade["direction"]
        score_bin = _score_to_bin(trade.get("iie_score", 0))
        market_phase = trade.get("market_phase", "unknown")
        impulse_location = trade.get("impulse_location", "mid_range")

        hyp_id = _make_hypothesis_id(
            symbol, direction, score_bin, market_phase, impulse_location
        )

        # Load or create hypothesis
        existing = self.db.get_hypothesis(hyp_id)
        if existing:
            hyp = Hypothesis(**{k: v for k, v in existing.items()
                               if k in Hypothesis.__dataclass_fields__})
        else:
            hyp = Hypothesis(
                id=hyp_id,
                symbol=symbol,
                direction=direction,
                score_bin=score_bin,
                market_phase=market_phase,
                impulse_location=impulse_location,
                created_at=time.time(),
            )

        # Update rolling stats
        pnl = trade.get("pnl_pct_after_commission", 0)
        is_win = pnl > 0
        max_fav = trade.get("max_favorable_pct", 0)
        max_adv = trade.get("max_adverse_pct", 0)

        n = hyp.sample_count
        w = config.HYPOTHESIS_ROLLING_WINDOW

        if n < w:
            # Growing window: simple running average
            hyp.sample_count += 1
            hyp.win_count += int(is_win)
            hyp.total_pnl += pnl
            hyp.avg_pnl = hyp.total_pnl / hyp.sample_count
            hyp.avg_max_favorable = (
                (hyp.avg_max_favorable * n + max_fav) / hyp.sample_count
            )
            hyp.avg_max_adverse = (
                (hyp.avg_max_adverse * n + max_adv) / hyp.sample_count
            )
        else:
            # Rolling window: exponential moving average (alpha = 2/(w+1))
            alpha = 2.0 / (w + 1)
            hyp.sample_count += 1
            hyp.win_count += int(is_win)
            hyp.total_pnl += pnl
            hyp.avg_pnl = hyp.avg_pnl * (1 - alpha) + pnl * alpha
            hyp.avg_max_favorable = (
                hyp.avg_max_favorable * (1 - alpha) + max_fav * alpha
            )
            hyp.avg_max_adverse = (
                hyp.avg_max_adverse * (1 - alpha) + max_adv * alpha
            )

        hyp.win_rate = (hyp.win_count / hyp.sample_count * 100
                        if hyp.sample_count > 0 else 0)

        # ── Analyze checkpoints ───────────────────────────────────────────
        after_open = [c for c in checkpoints if c["phase"] == "after_open" and c["completed"]]
        after_close = [c for c in checkpoints if c["phase"] == "after_close" and c["completed"]]

        # Checkpoint profitability (after open)
        for cp in after_open:
            label = cp["label"]
            profitable = cp["pnl_vs_entry"] > 0
            if label == "15m":
                hyp.pct_profitable_15m = self._update_pct(
                    hyp.pct_profitable_15m, profitable, hyp.sample_count
                )
            elif label == "1h":
                hyp.pct_profitable_1h = self._update_pct(
                    hyp.pct_profitable_1h, profitable, hyp.sample_count
                )
            elif label == "4h":
                hyp.pct_profitable_4h = self._update_pct(
                    hyp.pct_profitable_4h, profitable, hyp.sample_count
                )

        # Close miss: how much price moved favorably AFTER we exited
        if after_close:
            max_favorable_after_exit = max(
                (cp["pnl_vs_exit"] for cp in after_close
                 if cp["completed"] and cp["pnl_vs_exit"] > 0),
                default=0,
            )
            alpha_close = 2.0 / (min(hyp.sample_count, w) + 1)
            hyp.avg_close_miss_pct = (
                hyp.avg_close_miss_pct * (1 - alpha_close)
                + max_favorable_after_exit * alpha_close
            )

        # ── Optimize parameters ───────────────────────────────────────────
        self._optimize_parameters(hyp, trade, checkpoints)

        # ── Maturity check ────────────────────────────────────────────────
        hyp.is_mature = hyp.sample_count >= config.HYPOTHESIS_MIN_SAMPLES
        hyp.updated_at = time.time()

        # Save
        self.db.upsert_hypothesis(hyp)

        logger.info(
            f"🧠 Hypothesis updated: {hyp_id} "
            f"N={hyp.sample_count} WR={hyp.win_rate:.0f}% "
            f"avg_pnl={hyp.avg_pnl:+.2f}% "
            f"{'✅ MATURE' if hyp.is_mature else '⏳ growing'}"
        )

        return hyp

    def _update_pct(self, current_pct: float, is_true: bool, n: int) -> float:
        """Update a running percentage with a new boolean observation."""
        if n <= 1:
            return 100.0 if is_true else 0.0
        # Running average
        return current_pct + (100.0 * int(is_true) - current_pct) / n

    def _optimize_parameters(self, hyp: Hypothesis, trade: dict,
                              checkpoints: List[dict]):
        """
        Optimize SL/TP/trail based on accumulated data.
        Uses the observation that the best SL should be slightly wider
        than avg_max_adverse, and best TP near avg_max_favorable.
        """
        if hyp.sample_count < 5:
            return  # Not enough data to optimize

        # Optimal SL: avg_max_adverse × 1.3 (buffer for noise)
        # But never less than 0.3% (commission protection)
        if hyp.avg_max_adverse > 0:
            optimal_sl = hyp.avg_max_adverse * 1.3
            optimal_sl = max(optimal_sl, 0.3)
            optimal_sl = min(optimal_sl, config.EMERGENCY_STOP_PCT * 0.8)
            hyp.optimal_sl_pct = round(optimal_sl, 2)

        # Optimal TP: avg_max_favorable × 0.85 (take before reversal)
        if hyp.avg_max_favorable > 0:
            optimal_tp = hyp.avg_max_favorable * 0.85
            optimal_tp = max(optimal_tp, hyp.optimal_sl_pct * 1.5)  # Min RR = 1.5
            hyp.optimal_tp_pct = round(optimal_tp, 2)

        # Optimal trail: fraction of SL (v2.0: 50% of SL, min 0.5% — crypto needs room)
        hyp.optimal_trail_pct = round(max(0.5, hyp.optimal_sl_pct * 0.5), 3)

        # Scale-in recommendation:
        # If >70% profitable at 15m AND avg_max_favorable > 1.5%, recommend scale-in
        if hyp.pct_profitable_15m > 70 and hyp.avg_max_favorable > 1.5:
            hyp.should_scale_in = True
            hyp.scale_in_trigger = round(hyp.optimal_sl_pct * 0.3, 2)
        else:
            hyp.should_scale_in = False

        # Cut-early recommendation:
        # If win_rate < 40% AND avg_close_miss < 0.3%, exits are usually correct
        if hyp.win_rate < 40 and hyp.avg_close_miss_pct < 0.3:
            hyp.should_cut_early = True
        else:
            hyp.should_cut_early = False

    def get_stats(self) -> dict:
        """Get overall hypothesis engine statistics."""
        all_hyps = self.db.get_all_hypotheses()
        mature = [h for h in all_hyps if h["is_mature"]]
        if not mature:
            return {
                "total": len(all_hyps),
                "mature": 0,
                "avg_win_rate": 0,
                "avg_pnl": 0,
                "best_hypothesis": None,
                "worst_hypothesis": None,
            }

        avg_wr = sum(h["win_rate"] for h in mature) / len(mature)
        avg_pnl = sum(h["avg_pnl"] for h in mature) / len(mature)
        best = max(mature, key=lambda h: h["avg_pnl"])
        worst = min(mature, key=lambda h: h["avg_pnl"])

        return {
            "total": len(all_hyps),
            "mature": len(mature),
            "avg_win_rate": round(avg_wr, 1),
            "avg_pnl": round(avg_pnl, 3),
            "best_hypothesis": {
                "id": best["id"],
                "win_rate": best["win_rate"],
                "avg_pnl": best["avg_pnl"],
                "sample_count": best["sample_count"],
            },
            "worst_hypothesis": {
                "id": worst["id"],
                "win_rate": worst["win_rate"],
                "avg_pnl": worst["avg_pnl"],
                "sample_count": worst["sample_count"],
            },
        }
