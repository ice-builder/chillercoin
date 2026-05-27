"""
Scalper Pro — Feedback Loop

Orchestrates the complete learning cycle:
  Trade closed → Checkpoints completed → Hypothesis updated → Next trade improved

This module ties together:
  - CheckpointTracker (data collection)
  - HypothesisEngine (learning)
  - ScalperProDB (storage)
"""
import time
import logging
from typing import List, Dict, Optional

from iie_v2.database import ScalperProDB
from iie_v2.hypothesis_engine import HypothesisEngine
from iie_v2.checkpoint_tracker import CheckpointTracker

logger = logging.getLogger("scalper.feedback")


class FeedbackLoop:
    """
    Orchestrates the learning cycle for Scalper Pro.

    Workflow:
      1. Trade opens → create "after_open" checkpoints
      2. Trade closes → create "after_close" checkpoints
      3. Background: CheckpointTracker fills checkpoints as time passes
      4. All checkpoints for a trade complete → trigger hypothesis update
      5. Hypothesis updated → influences next trade with similar conditions
    """

    def __init__(self, db: ScalperProDB):
        self.db = db
        self.hypothesis_engine = HypothesisEngine(db)
        self.checkpoint_tracker = CheckpointTracker(db)
        self._last_analysis_run = 0
        self._analysis_interval = 60  # Check for analyzable trades every 60s

    def on_trade_open(self, trade_id: int, entry_time: float):
        """Called when a new trade is opened. Creates 'after_open' checkpoints."""
        self.db.insert_checkpoints(trade_id, "after_open", entry_time)
        logger.info(f"📋 Checkpoints created for trade #{trade_id} (after_open)")

    def on_trade_close(self, trade_id: int, exit_time: float):
        """Called when a trade is closed. Creates 'after_close' checkpoints."""
        self.db.insert_checkpoints(trade_id, "after_close", exit_time)
        logger.info(f"📋 Checkpoints created for trade #{trade_id} (after_close)")

    def get_signal_recommendation(self, symbol: str, direction: str,
                                    iie_score: float, market_phase: str,
                                    impulse_location: str) -> Optional[dict]:
        """
        Query hypothesis engine for trade parameter optimization.
        Returns recommendation dict or None if no mature hypothesis exists.
        """
        return self.hypothesis_engine.get_recommendation(
            symbol, direction, iie_score, market_phase, impulse_location
        )

    def tick(self):
        """
        Main loop tick — called every cycle from the bot's main loop.
        Processes pending checkpoints and analyzes completed trades.
        """
        # 1. Process pending checkpoints
        if self.checkpoint_tracker.should_run():
            self.checkpoint_tracker.process_pending()

        # 2. Check for trades ready for hypothesis analysis
        now = time.time()
        if now - self._last_analysis_run >= self._analysis_interval:
            self._last_analysis_run = now
            self._analyze_completed_trades()

    def _analyze_completed_trades(self):
        """
        Find closed trades with enough checkpoints for hypothesis analysis.
        
        v2.0: Uses partial checkpoint analysis — doesn't require ALL checkpoints.
        If at least HYPOTHESIS_MIN_CHECKPOINTS (default 4 of 6) are done, we
        analyze the trade. This ensures hypotheses build even if 4h checkpoints
        haven't fired yet.
        """
        import config as cfg
        min_cp = getattr(cfg, 'HYPOTHESIS_MIN_CHECKPOINTS', 4)

        # Method 1: Trades with enough partial checkpoints
        ready_trades = self.db.get_trades_ready_for_hypothesis(min_checkpoints=min_cp)

        for trade in ready_trades:
            trade_id = trade["id"]
            checkpoints = self.db.get_trade_checkpoints(trade_id)

            if not checkpoints:
                continue

            # Get only completed checkpoints for analysis
            completed_cps = [cp for cp in checkpoints if cp["completed"]]
            if len(completed_cps) < min_cp:
                continue

            # Update hypothesis with whatever data we have
            try:
                hyp = self.hypothesis_engine.update_from_trade(trade, completed_cps)
                self.db.mark_trade_analyzed(trade_id)
                logger.info(
                    f"🧠 Trade #{trade_id} analyzed → hypothesis {hyp.id} "
                    f"(N={hyp.sample_count}, WR={hyp.win_rate:.0f}%) "
                    f"[{len(completed_cps)}/{len(checkpoints)} checkpoints]"
                )
            except Exception as e:
                logger.error(f"Failed to analyze trade #{trade_id}: {e}")

    def get_learning_progress(self) -> dict:
        """Get overall learning progress for reports."""
        stats = self.hypothesis_engine.get_stats()
        db_stats = self.db.get_stats()

        return {
            "trades_total": db_stats["trades_total"],
            "trades_closed": db_stats["trades_closed"],
            "pending_checkpoints": db_stats["pending_checkpoints"],
            "hypotheses_total": stats["total"],
            "hypotheses_mature": stats["mature"],
            "avg_win_rate": stats["avg_win_rate"],
            "avg_pnl": stats["avg_pnl"],
            "best_hypothesis": stats.get("best_hypothesis"),
            "worst_hypothesis": stats.get("worst_hypothesis"),
        }

    def get_checkpoint_summary(self, trade_id: int) -> dict:
        """Get checkpoint summary for a specific trade."""
        return self.checkpoint_tracker.get_trade_summary(trade_id)
