#!/usr/bin/env python3
"""Insider Pump Scanner — Main entry point.
Monitors OI + CEX flow → scores → TG alerts → auto-enters via Pump Hunter.

Usage:
    python insider_scanner.py
"""
import os
import sys
import json
import time
import signal
import logging
import requests
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Set

# Add parent dir for imports
sys.path.insert(0, str(Path(__file__).parent))

import config
from oi_tracker import OITracker
from cex_flow import CEXFlowTracker
from scorer import score_tokens, format_score_report, InsiderScore
from position_manager import InsiderPositionManager
from tg_parser import TGWebParser

# ─── Logging ────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("insider")


# ─── Telegram ───────────────────────────────────
class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, thread_id: str = ""):
        self.token = token
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.enabled = bool(token and chat_id)

    def send(self, text: str):
        if not self.enabled:
            return
        try:
            data = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
            if self.thread_id:
                data["message_thread_id"] = int(self.thread_id)
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json=data, timeout=10
            )
        except Exception as e:
            logger.warning(f"TG send failed: {e}")


# ─── [REMOVED] Old Pump Hunter integration replaced by InsiderPositionManager ─


# ─── Main Scanner Loop ─────────────────────────
def run_scanner():
    """Main scanner loop."""
    logger.info("🕵️ Insider Pump Scanner starting...")

    tg = TelegramNotifier(config.TG_TOKEN, config.TG_CHAT_ID, config.TG_THREAD_ID)
    oi_tracker = OITracker(Path(config.OI_HISTORY_FILE))
    flow_tracker = CEXFlowTracker()
    pos_mgr = InsiderPositionManager(tg_notifier=tg)
    tg_parser = TGWebParser()

    # Cooldowns: {symbol: expiry_timestamp}
    alert_cooldowns: Dict[str, float] = {}
    tg_alert_count = 0

    # Graceful shutdown
    running = True
    def stop(s, f):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    startup_msg = (
        f"🕵️ *INSIDER SCANNER STARTED*\n"
        f"Exchanges: {', '.join(k for k, v in config.EXCHANGES.items() if v.get('enabled'))}\n"
        f"Scan interval: {config.SCAN_INTERVAL_SEC}s\n"
        f"Alert threshold: {config.ALERT_THRESHOLD}\n"
        f"Auto-enter threshold: {config.AUTO_ENTER_THRESHOLD}\n"
        f"Position size: {config.POSITION_SIZE_PCT}% × {config.LEVERAGE}x\n"
        f"Hard stop: {config.HARD_STOP_PCT}% | Trail: +{config.TRAIL_ACTIVATION_PCT}% → {config.TRAIL_STOP_PCT}%\n"
        f"Balance: ${pos_mgr.get_balance():,.0f}"
    )
    logger.info(startup_msg)
    tg.send(startup_msg)

    scan_count = 0
    last_heartbeat = 0

    while running:
        loop_start = time.time()
        scan_count += 1

        try:
            # ─── Step 1: Fetch OI from all exchanges ───────
            logger.info(f"━━━ Scan #{scan_count} ━━━")
            all_oi = oi_tracker.fetch_all_oi()
            total_symbols = sum(len(v) for v in all_oi.values())
            logger.info(f"📊 OI: {total_symbols} symbols across {len(all_oi)} exchanges")

            # ─── Step 2: Record + calculate changes ────────
            oi_tracker.record_snapshots(all_oi)
            oi_changes = oi_tracker.calculate_changes(all_oi)
            oi_anomalies = oi_tracker.detect_anomalies(oi_changes)
            top_movers = oi_tracker.get_top_movers(oi_changes)

            if oi_anomalies:
                logger.info(f"🚨 OI anomalies: {len(oi_anomalies)} symbols")
                for a in oi_anomalies[:5]:
                    logger.info(f"  {a.symbol} [{a.exchange}]: +{a.change_1h_pct:.1f}% 1h (z={a.z_score_1h:.1f})")

            # ─── Step 2b: Weekly OI trend tracking ────────
            oi_tracker.update_weekly_trends(all_oi)
            weekly_trending = oi_tracker.get_weekly_trending()
            if weekly_trending:
                logger.info(f"📈 Weekly trending: {', '.join(f'{s}({d}d)' for s, d in weekly_trending.items())}")

            # ─── Step 3: Fetch spot flow ───────────────────
            spot_tickers = flow_tracker.fetch_spot_tickers()
            flow_signals = flow_tracker.detect_buying_pressure(spot_tickers)

            if flow_signals:
                logger.info(f"💰 Flow signals: {len(flow_signals)} symbols")
                for f in flow_signals[:5]:
                    logger.info(f"  {f.symbol} [{f.exchange}]: z={f.z_score:.1f} buy_ratio={f.buy_ratio:.2f}")

            # ─── Step 3b: TG Channel Signals ──────────────
            tg_alerts_by_sym: Dict[str, list] = {}
            try:
                tg_oi, tg_flow, tg_msgs = tg_parser.scan()
                if tg_oi or tg_flow:
                    tg_alert_count += len(tg_oi) + len(tg_flow)
                    logger.info(f"📡 TG channels: {len(tg_oi)} OI + {len(tg_flow)} flow alerts")
                    # Group by normalized symbol
                    for alert in tg_oi:
                        sym = alert.symbol if alert.symbol.endswith("USDT") else alert.symbol + "USDT"
                        tg_alerts_by_sym.setdefault(sym, []).append({
                            "type": "oi", "exchange": alert.exchange,
                            "change": alert.oi_change_1h, "source": alert.source,
                        })
                    for alert in tg_flow:
                        sym = alert.symbol if alert.symbol.endswith("USDT") else alert.symbol + "USDT"
                        tg_alerts_by_sym.setdefault(sym, []).append({
                            "type": "flow", "direction": alert.direction,
                            "amount_usdt": alert.amount_usdt, "source": alert.source,
                        })
                    for sym, alerts in list(tg_alerts_by_sym.items())[:3]:
                        logger.info(f"  📡 TG {sym}: {len(alerts)} alerts")
                else:
                    logger.info(f"📡 TG: {len(tg_msgs)} new msgs, 0 alerts (seen={len(tg_parser.seen_posts)})")
            except Exception as e:
                logger.warning(f"TG parser error: {e}")

            # ─── Step 4: Score (with weekly trends + TG) ───
            scores = score_tokens(oi_anomalies, flow_signals, weekly_trending,
                                  tg_alerts=tg_alerts_by_sym)

            # ─── Step 5: Alert + Auto-enter ────────────────
            now = time.time()
            for score in scores:
                # Cooldown check
                if score.symbol in alert_cooldowns and now < alert_cooldowns[score.symbol]:
                    continue

                if score.is_auto_enter:
                    # Auto-enter via position manager (own state + stops)
                    report = format_score_report(score)
                    tg.send(report)
                    pos_mgr.open_position(score)
                    alert_cooldowns[score.symbol] = now + config.ALERT_COOLDOWN_SEC

                elif score.is_alert:
                    # Alert only
                    report = format_score_report(score)
                    logger.info(f"🟡 ALERT: {score.symbol} score={score.total_score}")
                    tg.send(report)
                    alert_cooldowns[score.symbol] = now + config.ALERT_COOLDOWN_SEC

            # ─── Step 6: Log summary ───────────────────────
            n_alerts = sum(1 for s in scores if s.is_alert)
            n_auto = sum(1 for s in scores if s.is_auto_enter)
            logger.info(
                f"📋 Scan #{scan_count}: "
                f"{total_symbols} symbols | "
                f"{len(oi_anomalies)} OI anomalies | "
                f"{len(flow_signals)} flow signals | "
                f"{n_alerts} alerts | {n_auto} auto-enters"
            )

            # Top OI movers log
            if top_movers:
                top_str = ", ".join(
                    f"{m.symbol}({m.exchange}):+{m.change_1h_pct:.1f}%"
                    for m in top_movers[:5]
                )
                logger.info(f"🏆 Top OI: {top_str}")

            # ─── Step 7: Check exits on open positions ────
            pos_mgr.check_exits()

            # ─── Heartbeat ─────────────────────────────────
            if time.time() - last_heartbeat > 3600:
                hb = (
                    f"📡 *INSIDER SCANNER HEARTBEAT*\n"
                    f"⏱ Scan #{scan_count}\n"
                    f"📊 Tracking: {total_symbols} symbols\n"
                    f"🚨 OI anomalies: {len(oi_anomalies)}\n"
                    f"💰 Flow signals: {len(flow_signals)}\n"
                    f"📡 TG alerts (total): {tg_alert_count}\n"
                    f"📍 Open positions: {pos_mgr.get_active_count()}\n"
                    f"💵 Balance: ${pos_mgr.get_balance():,.0f}\n"
                    f"🔔 Active cooldowns: {len(alert_cooldowns)}"
                )
                tg.send(hb)
                last_heartbeat = time.time()

        except Exception as e:
            logger.error(f"Scan error: {e}", exc_info=True)

        # Sleep until next scan
        elapsed = time.time() - loop_start
        sleep_time = max(1, config.SCAN_INTERVAL_SEC - elapsed)
        logger.info(f"⏳ Next scan in {sleep_time:.0f}s")
        time.sleep(sleep_time)

    logger.info("🛑 Insider Scanner stopped.")
    tg.send("🛑 *INSIDER SCANNER STOPPED*")


if __name__ == "__main__":
    run_scanner()
