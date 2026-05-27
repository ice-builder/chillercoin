#!/usr/bin/env python3
"""
IIE — Background Daemon

PM2-managed process that orchestrates all IIE components:
  - Impulse Collector:   every 5 min
  - Post-Trade Tracker:  every 15 min
  - Market Phase:        every 4 hours
  - Coin Scorer:         every 1 hour  (Phase 3)
  - ML Retrain:          every 24 hours (Phase 4)

Usage:
    python -m iie.iie_daemon
    # or via PM2:
    pm2 start iie_daemon.py --name iie-engine --interpreter venv/bin/python
"""
import sys
import time
import signal
import logging
import os
from pathlib import Path

# Add parent dir for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from iie import config
from iie.impulse_db import ImpulseDB
from iie.impulse_collector import ImpulseCollector, import_completed_trades
from iie.post_trade_tracker import PostTradeTracker
from iie.market_phase import detect_market_phase
from iie.coin_scorer import CoinScorer
from iie.adaptive_manager import AdaptivePositionManager
from iie.report import generate_report, format_telegram_report, send_telegram
from iie.iie_signals import SignalEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("iie.daemon")


def run_daemon():
    logger.info("🧠 IIE Daemon starting...")

    db = ImpulseDB()
    collector = ImpulseCollector(db)
    tracker = PostTradeTracker(db)
    scorer = CoinScorer(db)
    manager = AdaptivePositionManager(db)
    signal_engine = SignalEngine(db, manager)

    # ─── Import historical trades on first run ───
    stats = db.stats()
    if stats.get("trade_outcomes", 0) == 0:
        logger.info("📥 First run — importing historical trades...")
        # Soldier state
        soldier_state = Path(__file__).resolve().parent.parent / ".local_ai" / "paper_trading" / "paper_state_multi.json"
        if soldier_state.exists():
            import_completed_trades(db, str(soldier_state), "soldier")

        # Pump Hunter state
        for ph_path in [
            Path("/home/trader/pump-hunter/demo_state.json"),
            Path("/home/trader/pump_hunter/demo_state.json"),
            Path(__file__).resolve().parent.parent.parent / "oneprop" / "pump_hunter" / "demo_state.json",
        ]:
            if ph_path.exists():
                import_completed_trades(db, str(ph_path), "pump_hunter")
                break

        # Insider state
        insider_state = Path(__file__).resolve().parent.parent / "insider_scanner" / "insider_positions.json"
        if insider_state.exists():
            import_completed_trades(db, str(insider_state), "insider")

        logger.info(f"📥 Import complete: {db.stats()}")

    # Graceful shutdown
    running = True
    def stop(s, f):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    # Timing trackers
    last_collect = 0
    last_track = 0
    last_phase = 0
    last_score = 0
    last_retrain = 0
    last_heartbeat = 0
    last_tg_report = 0
    loop_count = 0

    startup_stats = db.stats()
    logger.info(
        f"🧠 IIE Daemon ready | DB: {startup_stats} | "
        f"Collector: every {config.COLLECTOR_INTERVAL_SEC}s | "
        f"Tracker: every {config.POST_TRACKER_INTERVAL_SEC}s | "
        f"Phase: every {config.MARKET_PHASE_INTERVAL_SEC}s"
    )

    while running:
        now = time.time()
        loop_count += 1

        try:
            # ─── Impulse Collector (every 5 min) ─────
            if now - last_collect >= config.COLLECTOR_INTERVAL_SEC:
                found = collector.run_scan()
                logger.info(f"🔍 Collector scan: {found} new impulses")
                last_collect = now

                # Evaluate new impulses for IIE signals
                try:
                    signals = signal_engine.evaluate_new_impulses()
                    if signals > 0:
                        logger.info(f"🧠 Signal Engine: {signals} signals sent")
                except Exception as e:
                    logger.warning(f"Signal evaluation error: {e}")

            # ─── Post-Trade Tracker (every 15 min) ───
            if now - last_track >= config.POST_TRACKER_INTERVAL_SEC:
                updated = tracker.run_update()
                if updated > 0:
                    logger.info(f"📊 Tracker: updated {updated} outcomes")
                last_track = now

            # ─── Market Phase (every 4 hours) ────────
            if now - last_phase >= config.MARKET_PHASE_INTERVAL_SEC:
                phase = detect_market_phase(db)
                if phase:
                    logger.info(f"🧭 Phase updated: {phase.phase}")
                last_phase = now

            # ─── Coin Scorer (every 1 hour) ──────────
            if now - last_score >= config.COIN_SCORER_INTERVAL_SEC:
                scored = scorer.run_scoring()
                if scored > 0:
                    logger.info(f"🪙 Scorer: updated {scored} coin profiles")
                last_score = now

            # ─── ML Retrain (every 24 hours) ─────────
            if now - last_retrain >= config.PREDICTOR_RETRAIN_INTERVAL_SEC:
                try:
                    trained = manager.retrain_if_needed()
                    if trained:
                        logger.info("🧠 ML model retrained")
                except Exception as e:
                    logger.warning(f"ML retrain error: {e}")
                last_retrain = now

            # ─── Heartbeat (every 1 hour) ────────────
            if now - last_heartbeat >= 3600:
                stats = db.stats()
                current_phase = db.get_current_phase()
                phase_str = current_phase.phase if current_phase else "unknown"
                logger.info(
                    f"📡 IIE Heartbeat #{loop_count} | "
                    f"Impulses: {stats.get('impulses', 0)} | "
                    f"Outcomes: {stats.get('post_impulse_outcomes', 0)} "
                    f"(complete: {_count_complete(db)}) | "
                    f"Profiles: {stats.get('coin_profiles', 0)} | "
                    f"Trades: {stats.get('trade_outcomes', 0)} | "
                    f"Phase: {phase_str}"
                )
                last_heartbeat = now

            # ─── Telegram Report (every 6 hours) ─────
            if now - last_tg_report >= 21600:  # 6h
                try:
                    report = generate_report(db)
                    tg_text = format_telegram_report(report)
                    send_telegram(tg_text)
                    logger.info("📱 TG report sent")
                except Exception as e:
                    logger.warning(f"TG report failed: {e}")
                last_tg_report = now

        except Exception as e:
            logger.error(f"Daemon loop error: {e}", exc_info=True)

        # Sleep 30s between checks
        time.sleep(30)

    logger.info("🛑 IIE Daemon stopped")


def _count_complete(db: ImpulseDB) -> int:
    try:
        with db._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as c FROM post_impulse_outcomes WHERE tracking_complete = 1"
            ).fetchone()
            return row["c"]
    except Exception:
        return 0


if __name__ == "__main__":
    run_daemon()
