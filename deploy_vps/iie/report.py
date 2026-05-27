#!/usr/bin/env python3
"""
IIE — Report Generator

On-demand report showing IIE engine status, impulse stats,
coin profiles, and market phase. Can output to terminal or Telegram.

Usage:
    python -m iie.report              # Terminal report
    python -m iie.report --telegram   # Send to Telegram
    python -m iie.report --json       # JSON export
"""
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from iie import config
from iie.impulse_db import ImpulseDB


def generate_report(db: ImpulseDB) -> dict:
    """Generate full IIE status report."""
    stats = db.stats()
    phase = db.get_current_phase()
    profiles = db.get_all_coin_profiles()

    # Recent impulses breakdown
    recent_1h = db.get_recent_impulses(hours=1)
    recent_24h = db.get_recent_impulses(hours=24)

    # Impulse stats by exchange/timeframe/direction
    impulse_by_tf = {}
    impulse_by_dir = {"long": 0, "short": 0}
    impulse_by_location = {}
    top_scores = []

    for imp in recent_24h:
        tf = imp.timeframe
        impulse_by_tf[tf] = impulse_by_tf.get(tf, 0) + 1
        impulse_by_dir[imp.direction] = impulse_by_dir.get(imp.direction, 0) + 1
        loc = imp.impulse_location
        impulse_by_location[loc] = impulse_by_location.get(loc, 0) + 1
        top_scores.append({
            "symbol": imp.symbol, "tf": imp.timeframe,
            "dir": imp.direction, "score": imp.combined_score,
            "vol_z": imp.vol_z, "ret_z": imp.ret_z,
            "location": imp.impulse_location,
        })

    top_scores.sort(key=lambda x: x["score"], reverse=True)

    # Completed outcomes stats
    completed_outcomes = []
    total_completed = 0
    with db._conn() as conn:
        # Total count of completed outcomes
        total_completed = conn.execute(
            "SELECT COUNT(*) FROM post_impulse_outcomes WHERE tracking_complete = 1"
        ).fetchone()[0]
        # Recent sample for avg calculations
        rows = conn.execute(
            """SELECT i.symbol, i.direction, o.max_favorable_pct, o.max_adverse_pct,
                      o.was_stop_hunt, o.continuation_impulses, o.new_extremum
               FROM post_impulse_outcomes o
               JOIN impulses i ON o.impulse_id = i.id
               WHERE o.tracking_complete = 1
               ORDER BY o.last_updated DESC LIMIT 200"""
        ).fetchall()
        completed_outcomes = [dict(r) for r in rows]

    avg_favorable = 0
    avg_adverse = 0
    stop_hunt_pct = 0
    if completed_outcomes:
        avg_favorable = sum(o["max_favorable_pct"] for o in completed_outcomes) / len(completed_outcomes)
        avg_adverse = sum(o["max_adverse_pct"] for o in completed_outcomes) / len(completed_outcomes)
        stop_hunts = sum(1 for o in completed_outcomes if o["was_stop_hunt"])
        stop_hunt_pct = stop_hunts / len(completed_outcomes) * 100

    # Trade outcomes by bot
    trades_by_bot = {}
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT bot_name, COUNT(*) as cnt, SUM(pnl_pct) as total_pnl, "
            "SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins "
            "FROM trade_outcomes GROUP BY bot_name"
        ).fetchall()
        for r in rows:
            trades_by_bot[r["bot_name"]] = {
                "count": r["cnt"], "total_pnl": round(r["total_pnl"], 3),
                "wins": r["wins"],
                "wr": round(r["wins"] / max(1, r["cnt"]) * 100, 1),
            }

    # Processing speed: outcomes completed in last 1h
    outcomes_per_hour = 0
    try:
        with db._conn() as conn:
            cutoff = time.time() - 3600
            row = conn.execute(
                "SELECT COUNT(*) FROM post_impulse_outcomes WHERE tracking_complete = 1 AND last_updated > ?",
                (cutoff,)
            ).fetchone()
            outcomes_per_hour = row[0] if row else 0
    except Exception:
        pass

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "db_stats": stats,
        "market_phase": {
            "phase": phase.phase if phase else "unknown",
            "btc_price": phase.btc_price if phase else 0,
            "btc_monthly": phase.btc_monthly_change_pct if phase else 0,
            "eth_price": phase.eth_price if phase else 0,
            "alt_correlation": phase.alt_correlation if phase else 0,
        } if phase else None,
        "impulses_last_1h": len(recent_1h),
        "impulses_last_24h": len(recent_24h),
        "impulse_by_tf": impulse_by_tf,
        "impulse_by_dir": impulse_by_dir,
        "impulse_by_location": impulse_by_location,
        "top_10_impulses": top_scores[:10],
        "outcome_stats": {
            "completed": total_completed,
            "avg_favorable_pct": round(avg_favorable, 2),
            "avg_adverse_pct": round(avg_adverse, 2),
            "stop_hunt_pct": round(stop_hunt_pct, 1),
        },
        "trades_by_bot": trades_by_bot,
        "outcomes_per_hour": outcomes_per_hour,
        "coin_profiles_count": len(profiles),
        "top_coins": [
            {"symbol": p.symbol, "quality": p.impulse_quality_score,
             "momentum": p.momentum_persistence, "stop_hunts": p.stop_hunt_frequency,
             "sl_mult": p.recommended_sl_mult}
            for p in profiles[:10]
        ],
    }


def print_terminal_report(report: dict):
    """Pretty-print report to terminal."""
    G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"
    C = "\033[96m"; B = "\033[1m"; D = "\033[2m"; X = "\033[0m"

    print(f"\n{C}{'═'*60}{X}")
    print(f"  {B}{C}🧠 IIE — IMPULSE INTELLIGENCE ENGINE REPORT{X}")
    print(f"{C}{'═'*60}{X}")
    print(f"  {D}{report['timestamp']}{X}\n")

    # DB Stats
    s = report["db_stats"]
    print(f"  {B}📦 Database:{X}")
    print(f"    Impulses: {s['impulses']} | Outcomes: {s['post_impulse_outcomes']}")
    print(f"    Profiles: {s['coin_profiles']} | Trades: {s['trade_outcomes']}")
    print(f"    Phases:   {s['market_phases']}")

    # Market Phase
    mp = report.get("market_phase")
    if mp:
        phase = mp["phase"]
        pc = G if "up" in phase else R if "down" in phase else Y
        print(f"\n  {B}🧭 Market Phase: {pc}{phase.upper()}{X}")
        print(f"    BTC: ${mp['btc_price']:,.0f} ({mp['btc_monthly']:+.1f}% mo)")
        print(f"    ETH: ${mp['eth_price']:,.0f}")
        print(f"    Alt correlation: {mp['alt_correlation']:.2f}")

    # Impulse Activity
    print(f"\n  {B}⚡ Impulses:{X}")
    print(f"    Last 1h:  {report['impulses_last_1h']}")
    print(f"    Last 24h: {report['impulses_last_24h']}")
    by_tf = report.get("impulse_by_tf", {})
    if by_tf:
        tf_str = " | ".join(f"{tf}m:{cnt}" for tf, cnt in sorted(by_tf.items()))
        print(f"    By TF: {tf_str}")
    by_dir = report.get("impulse_by_dir", {})
    if by_dir:
        print(f"    Long: {by_dir.get('long', 0)} | Short: {by_dir.get('short', 0)}")
    by_loc = report.get("impulse_by_location", {})
    if by_loc:
        loc_str = " | ".join(f"{loc}:{cnt}" for loc, cnt in by_loc.items())
        print(f"    Location: {loc_str}")

    # Top Impulses
    top = report.get("top_10_impulses", [])
    if top:
        print(f"\n  {B}🏆 Top Impulses (24h):{X}")
        for i, t in enumerate(top[:10], 1):
            dc = G if t["dir"] == "long" else R
            print(f"    {i:2d}. {t['symbol']:12s} [{t['tf']}] "
                  f"{dc}{t['dir'].upper():5s}{X} "
                  f"score={t['score']:.1f} (v={t['vol_z']:.1f} r={t['ret_z']:.1f}) "
                  f"{t['location']}")

    # Outcome Stats
    os_ = report.get("outcome_stats", {})
    if os_.get("completed", 0) > 0:
        print(f"\n  {B}📊 Outcome Analysis ({os_['completed']} completed):{X}")
        print(f"    Avg favorable: {G}+{os_['avg_favorable_pct']:.2f}%{X}")
        print(f"    Avg adverse:   {R}-{os_['avg_adverse_pct']:.2f}%{X}")
        print(f"    Stop hunts:    {os_['stop_hunt_pct']:.1f}%")

    # Trades by Bot
    tbb = report.get("trades_by_bot", {})
    if tbb:
        print(f"\n  {B}🤖 Trades by Bot:{X}")
        for bot, info in tbb.items():
            pc = G if info["total_pnl"] >= 0 else R
            print(f"    {bot:15s} {info['count']:3d} trades | "
                  f"WR: {info['wr']:.0f}% | "
                  f"PnL: {pc}{info['total_pnl']:+.3f}%{X}")

    # Coin Profiles
    coins = report.get("top_coins", [])
    if coins:
        print(f"\n  {B}🪙 Top Coin Profiles:{X}")
        for c in coins:
            print(f"    {c['symbol']:12s} quality={c['quality']:.0f} "
                  f"momentum={c['momentum']:.0f} "
                  f"stop_hunts={c['stop_hunts']:.0f}% "
                  f"sl×{c['sl_mult']:.1f}")

    print(f"\n{C}{'═'*60}{X}\n")


def format_telegram_report(report: dict) -> str:
    """Format compact report for Telegram (HTML mode — no markdown issues)."""
    s = report["db_stats"]
    mp = report.get("market_phase", {}) or {}
    phase = mp.get("phase", "unknown")
    os_ = report.get("outcome_stats", {})
    tbb = report.get("trades_by_bot", {})

    phase_emoji = {"trending_up": "📈", "trending_down": "📉",
                   "sideways": "➡️", "volatile": "🌊"}.get(phase, "❓")

    lines = [
        f"🧠 <b>ИИ ОТЧЁТ</b>",
        f"{'━'*24}",
        f"",
        f"📦 БД: {s['impulses']} импульсов | {s['trade_outcomes']} сделок",
        f"{phase_emoji} Фаза: <b>{phase.upper()}</b>",
    ]

    if mp.get("btc_price"):
        btc_p = int(mp['btc_price'])
        lines.append(
            f"₿ BTC: {btc_p:,} ({mp['btc_monthly']:+.1f}% мес) "
            f"| Корр. альтов: {mp['alt_correlation']:.2f}"
        )

    lines.append(f"")
    lines.append(
        f"⚡ Импульсы: {report['impulses_last_1h']} (1ч) / "
        f"{report['impulses_last_24h']} (24ч)"
    )

    by_dir = report.get("impulse_by_dir", {})
    lines.append(f"   Лонг: {by_dir.get('long', 0)} | Шорт: {by_dir.get('short', 0)}")

    # Top 5 impulses (dedup by symbol)
    top = report.get("top_10_impulses", [])
    seen = set()
    top_dedup = []
    for t in top:
        if t["symbol"] not in seen:
            seen.add(t["symbol"])
            top_dedup.append(t)
        if len(top_dedup) >= 5:
            break

    if top_dedup:
        lines.append(f"")
        lines.append(f"🏆 <b>Топ импульсы:</b>")
        for t in top_dedup:
            d = "🟢" if t["dir"] == "long" else "🔴"
            lines.append(
                f"  {d} <code>{t['symbol']}</code> [{t['tf']}] "
                f"скор={t['score']:.1f}"
            )

    if os_.get("completed", 0) > 0:
        lines.append(f"")
        lines.append(
            f"📊 Исходы ({os_['completed']}): "
            f"+{os_['avg_favorable_pct']:.1f}% / -{os_['avg_adverse_pct']:.1f}% "
            f"| Стоп-хант: {os_['stop_hunt_pct']:.0f}%"
        )

    if tbb:
        lines.append(f"")
        lines.append(f"🤖 <b>Боты:</b>")
        for bot, info in tbb.items():
            e = "✅" if info["total_pnl"] >= 0 else "❌"
            lines.append(
                f"  {e} {bot}: {info['count']} | "
                f"ВР {info['wr']:.0f}% | <code>{info['total_pnl']:+.3f}%</code>"
            )

    return "\n".join(lines)


def send_telegram(text: str):
    """Send report to Telegram."""
    import requests
    if not config.TG_TOKEN or not config.TG_CHAT_ID:
        print("❌ TG_TOKEN or TG_CHAT_ID not set")
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{config.TG_TOKEN}/sendMessage",
            json={"chat_id": config.TG_CHAT_ID, "text": text,
                  "parse_mode": "HTML"},
            timeout=15)
        if resp.status_code == 200:
            print("✅ Report sent to Telegram")
        else:
            print(f"❌ TG error: {resp.status_code}")
    except Exception as e:
        print(f"❌ TG send failed: {e}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="🧠 IIE Report")
    parser.add_argument("--telegram", action="store_true", help="Send to Telegram")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    db = ImpulseDB()
    report = generate_report(db)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_terminal_report(report)

    if args.telegram:
        tg_text = format_telegram_report(report)
        send_telegram(tg_text)
