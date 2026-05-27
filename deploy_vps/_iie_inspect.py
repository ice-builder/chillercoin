#!/usr/bin/env python3
"""IIE Deep Inspection — database, models, signal quality."""
import sqlite3, json, os
from datetime import datetime
from pathlib import Path

DB = "iie/data/impulses.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# === SCHEMA ===
print("=" * 70)
print("  IIE DATABASE SCHEMA & STATS")
print("=" * 70)

tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
for tbl in tables:
    count = conn.execute(f"SELECT COUNT(*) FROM [{tbl}]").fetchone()[0]
    cols = conn.execute(f"PRAGMA table_info([{tbl}])").fetchall()
    col_names = [c[1] for c in cols]
    print(f"\n  {tbl} ({count:,} rows)")
    print(f"    Columns: {', '.join(col_names)}")

# === IMPULSES ===
print(f"\n{'='*70}")
print("  IMPULSE COLLECTION")
print("=" * 70)

# Find the timestamp column
imp_cols = [c[1] for c in conn.execute("PRAGMA table_info(impulses)").fetchall()]
ts_col = "timestamp" if "timestamp" in imp_cols else "ts" if "ts" in imp_cols else imp_cols[-1]
print(f"  Timestamp column: {ts_col}")

total = conn.execute("SELECT COUNT(*) FROM impulses").fetchone()[0]
print(f"  Total impulses: {total:,}")

# By timeframe
if "timeframe" in imp_cols:
    print(f"\n  By timeframe:")
    for r in conn.execute("SELECT timeframe, COUNT(*) as cnt FROM impulses GROUP BY timeframe ORDER BY cnt DESC").fetchall():
        print(f"    {r[0]:>5}: {r[1]:>6,} impulses")

# By direction
if "direction" in imp_cols:
    print(f"\n  By direction:")
    for r in conn.execute("SELECT direction, COUNT(*) as cnt FROM impulses GROUP BY direction ORDER BY cnt DESC").fetchall():
        print(f"    {r[0]:>5}: {r[1]:>6,}")

# Recent impulses
print(f"\n  Last 10 impulses:")
recent = conn.execute(f"SELECT * FROM impulses ORDER BY id DESC LIMIT 10").fetchall()
for r in recent:
    d = dict(r)
    sym = d.get("symbol", "?")
    tf = d.get("timeframe", "?")
    direc = d.get("direction", "?")
    vol_z = d.get("vol_z", d.get("volume_z", 0))
    ret_z = d.get("ret_z", d.get("return_z", 0))
    score = d.get("combined_score", d.get("score", 0))
    print(f"    #{d.get('id',0):>6} {sym:14} {tf:>4} {direc:>5}  vol_z={vol_z:>6.1f}  ret_z={ret_z:>5.1f}  score={score:>6.1f}")

# === POST-IMPULSE OUTCOMES ===
print(f"\n{'='*70}")
print("  POST-IMPULSE OUTCOMES (what happens after impulse)")
print("=" * 70)

pio_cols = [c[1] for c in conn.execute("PRAGMA table_info(post_impulse_outcomes)").fetchall()]
total_pio = conn.execute("SELECT COUNT(*) FROM post_impulse_outcomes").fetchone()[0]
tracked = conn.execute("SELECT COUNT(*) FROM post_impulse_outcomes WHERE favorable_pct > 0 OR adverse_pct > 0").fetchone()[0]
print(f"  Total: {total_pio:,}")
print(f"  With outcomes: {tracked:,}")

if "favorable_pct" in pio_cols and "adverse_pct" in pio_cols:
    avg = conn.execute("""
        SELECT AVG(favorable_pct) as avg_fav, AVG(adverse_pct) as avg_adv,
               MAX(favorable_pct) as max_fav, MAX(adverse_pct) as max_adv
        FROM post_impulse_outcomes
        WHERE favorable_pct > 0 OR adverse_pct > 0
    """).fetchone()
    if avg and avg[0]:
        print(f"  Avg favorable: {avg[0]:+.2f}%")
        print(f"  Avg adverse:   {avg[1]:+.2f}%")
        print(f"  Max favorable: {avg[2]:+.2f}%")
        print(f"  Max adverse:   {avg[3]:+.2f}%")

# === TRADE OUTCOMES ===
print(f"\n{'='*70}")
print("  TRADE OUTCOMES (ML training data)")
print("=" * 70)

to_cols = [c[1] for c in conn.execute("PRAGMA table_info(trade_outcomes)").fetchall()]
total_to = conn.execute("SELECT COUNT(*) FROM trade_outcomes").fetchone()[0]
print(f"  Total: {total_to}")

if "exit_reason" in to_cols and "pnl_pct" in to_cols:
    print(f"\n  By exit reason:")
    for r in conn.execute("""
        SELECT exit_reason, COUNT(*) as cnt, AVG(pnl_pct) as avg, SUM(pnl_pct) as total
        FROM trade_outcomes GROUP BY exit_reason ORDER BY cnt DESC
    """).fetchall():
        print(f"    {r[0]:25} x{r[1]:>3}  avg={r[2]:+.3f}%  total={r[3]:+.2f}%")

if "bot_name" in to_cols:
    print(f"\n  By bot:")
    for r in conn.execute("""
        SELECT bot_name, COUNT(*) as cnt, AVG(pnl_pct) as avg
        FROM trade_outcomes GROUP BY bot_name ORDER BY cnt DESC
    """).fetchall():
        print(f"    {r[0]:15} x{r[1]:>3}  avg={r[2]:+.3f}%")

# === PENDING SIGNALS ===
print(f"\n{'='*70}")
print("  PENDING SIGNALS (IIE → Soldier pipeline)")
print("=" * 70)

ps_cols = [c[1] for c in conn.execute("PRAGMA table_info(pending_signals)").fetchall()]
total_ps = conn.execute("SELECT COUNT(*) FROM pending_signals").fetchone()[0]
print(f"  Total generated: {total_ps:,}")

import time as _t
recent_1h = conn.execute("SELECT COUNT(*) FROM pending_signals WHERE created_at > ?", (_t.time() - 3600,)).fetchone()[0]
recent_24h = conn.execute("SELECT COUNT(*) FROM pending_signals WHERE created_at > ?", (_t.time() - 86400,)).fetchone()[0]
print(f"  Last 1h: {recent_1h}")
print(f"  Last 24h: {recent_24h}")

# Score distribution
if "score" in ps_cols:
    print(f"\n  Score distribution (all time):")
    for r in conn.execute("""
        SELECT
            CASE
                WHEN score >= 90 THEN '90+'
                WHEN score >= 80 THEN '80-89'
                WHEN score >= 70 THEN '70-79'
                WHEN score >= 60 THEN '60-69'
                ELSE '<60'
            END as bucket, COUNT(*) as cnt
        FROM pending_signals GROUP BY bucket ORDER BY bucket DESC
    """).fetchall():
        print(f"    Score {r[0]:>5}: {r[1]:>5} signals")

# Recent 10 signals
print(f"\n  Last 10 signals:")
for r in conn.execute("""
    SELECT symbol, direction, score, confidence, sl_pct, tp_pct, trail_pct,
           hold_bars, size_mult, market_phase, reason, created_at
    FROM pending_signals ORDER BY created_at DESC LIMIT 10
""").fetchall():
    ts = datetime.fromtimestamp(r["created_at"]).strftime("%H:%M")
    print(f"    {ts} {r['symbol']:14} {r['direction']:5} s={r['score']:>3.0f} c={r['confidence']:.0f}% "
          f"SL={r['sl_pct']:.1f}% TP={r['tp_pct']:.1f}% trail={r['trail_pct']:.2f}% "
          f"hold={r['hold_bars']} size={r['size_mult']:.1f}x phase={r['market_phase']}")

# === COIN PROFILES ===
print(f"\n{'='*70}")
print("  COIN PROFILES (quality scoring)")
print("=" * 70)

cp_cols = [c[1] for c in conn.execute("PRAGMA table_info(coin_profiles)").fetchall()]
total_cp = conn.execute("SELECT COUNT(*) FROM coin_profiles").fetchone()[0]
print(f"  Total profiled coins: {total_cp}")

if "quality_score" in cp_cols:
    # Top and bottom
    print(f"\n  Top 10 quality coins:")
    for r in conn.execute("""
        SELECT symbol, quality_score, avg_pnl, total_trades, win_rate, avg_volume_z
        FROM coin_profiles WHERE total_trades >= 3
        ORDER BY quality_score DESC LIMIT 10
    """).fetchall():
        d = dict(r)
        print(f"    {d['symbol']:14} Q={d['quality_score']:>5.1f}  pnl={d.get('avg_pnl',0):+.2f}%  "
              f"trades={d.get('total_trades',0)}  WR={d.get('win_rate',0):.0f}%  vol_z={d.get('avg_volume_z',0):.1f}")

    print(f"\n  Bottom 5 quality coins:")
    for r in conn.execute("""
        SELECT symbol, quality_score, avg_pnl, total_trades, win_rate
        FROM coin_profiles WHERE total_trades >= 3
        ORDER BY quality_score ASC LIMIT 5
    """).fetchall():
        d = dict(r)
        print(f"    {d['symbol']:14} Q={d['quality_score']:>5.1f}  pnl={d.get('avg_pnl',0):+.2f}%  trades={d.get('total_trades',0)}  WR={d.get('win_rate',0):.0f}%")

# === MARKET PHASE ===
print(f"\n{'='*70}")
print("  MARKET PHASE HISTORY")
print("=" * 70)

mp_cols = [c[1] for c in conn.execute("PRAGMA table_info(market_phases)").fetchall()]
for r in conn.execute("SELECT * FROM market_phases ORDER BY rowid DESC LIMIT 5").fetchall():
    d = dict(r)
    print(f"  {d}")

# === ML MODELS ===
print(f"\n{'='*70}")
print("  ML MODELS")
print("=" * 70)

models_dir = Path("iie/data/models")
if models_dir.exists():
    for f in sorted(models_dir.iterdir()):
        size = f.stat().st_size
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  {f.name:30} {size/1024:>8.1f} KB  ({mtime})")
else:
    print("  No models directory")

conn.close()
print(f"\n{'='*70}")
print("  IIE INSPECTION COMPLETE")
print("=" * 70)
