#!/usr/bin/env python3
"""Inspect IIE XGBoost model and training progress."""
import pickle, json, sqlite3
from datetime import datetime, timezone

# 1. Load model
with open("iie/data/models/predictor_state.pkl", "rb") as f:
    ms = pickle.load(f)

print("=" * 60)
print("IIE PREDICTOR MODEL INSPECTION")
print("=" * 60)
print(f"State type: {type(ms).__name__}")

if isinstance(ms, dict):
    print(f"Keys: {list(ms.keys())}")
    for k, v in ms.items():
        if isinstance(v, (int, float, str, bool)):
            print(f"  {k}: {v}")
        elif isinstance(v, list):
            print(f"  {k}: list[{len(v)}]")
            if len(v) <= 15 and all(isinstance(x, (int, float, str)) for x in v):
                print(f"    -> {v}")
        elif isinstance(v, dict):
            print(f"  {k}: dict[{len(v)} keys]")
            if len(v) <= 30:
                for kk, vv in list(v.items())[:25]:
                    if isinstance(vv, (int, float)):
                        print(f"    {kk}: {vv}")
                    else:
                        print(f"    {kk}: {type(vv).__name__}")
        elif hasattr(v, "shape"):
            print(f"  {k}: ndarray shape={v.shape}")
        else:
            t = type(v).__name__
            print(f"  {k}: {t}")

# 2. XGBoost model details
try:
    import xgboost as xgb
    for k, v in ms.items():
        if isinstance(v, (xgb.XGBClassifier, xgb.XGBRegressor)):
            print(f"\n{'=' * 60}")
            print(f"XGBOOST MODEL: {k}")
            print(f"{'=' * 60}")
            print(f"  Type: {type(v).__name__}")
            print(f"  n_estimators: {v.n_estimators}")
            print(f"  max_depth: {v.max_depth}")
            print(f"  learning_rate: {v.learning_rate}")
            try:
                booster = v.get_booster()
                imp = booster.get_score(importance_type="gain")
                sorted_imp = sorted(imp.items(), key=lambda x: -x[1])
                print(f"\n  Top 20 features (gain importance):")
                for feat, score in sorted_imp[:20]:
                    print(f"    {feat}: {score:.1f}")
                print(f"\n  Total features used: {len(imp)} / {len(booster.feature_names or [])}")
            except Exception as e:
                print(f"  Feature importance error: {e}")

            try:
                fn = v.get_booster().feature_names
                if fn:
                    print(f"\n  All feature names ({len(fn)}):")
                    for f in fn:
                        print(f"    - {f}")
            except:
                pass
except ImportError:
    print("\n  xgboost not available")

# 3. Training data stats from DB
print(f"\n{'=' * 60}")
print("TRAINING DATA ANALYSIS")
print(f"{'=' * 60}")
db = sqlite3.connect("iie/data/impulses.db")

total = db.execute("SELECT COUNT(*) FROM impulses").fetchone()[0]
print(f"Total impulses: {total}")

# Time range
ts_range = db.execute("SELECT MIN(timestamp), MAX(timestamp) FROM impulses").fetchone()
if ts_range[0]:
    t_min = datetime.fromtimestamp(float(ts_range[0]), tz=timezone.utc)
    t_max = datetime.fromtimestamp(float(ts_range[1]), tz=timezone.utc)
    print(f"Time range: {t_min:%Y-%m-%d %H:%M} to {t_max:%Y-%m-%d %H:%M}")

# By exchange
print("\nBy exchange:")
for row in db.execute("SELECT exchange, COUNT(*) FROM impulses GROUP BY exchange ORDER BY COUNT(*) DESC").fetchall():
    print(f"  {row[0]}: {row[1]}")

# By direction
print("\nBy direction:")
for row in db.execute("SELECT direction, COUNT(*) FROM impulses GROUP BY direction").fetchall():
    print(f"  {row[0]}: {row[1]}")

# Avg scores
print("\nAvg impulse scores:")
for row in db.execute("SELECT AVG(vol_z), AVG(ret_z), AVG(combined_score), AVG(rsi_at_impulse) FROM impulses").fetchall():
    print(f"  vol_z: {row[0]:.2f}, ret_z: {row[1]:.2f}, combined: {row[2]:.2f}, rsi: {row[3]:.1f}")

# Signal generation stats
print(f"\n{'=' * 60}")
print("IIE SIGNAL GENERATION")
print(f"{'=' * 60}")
total_sig = db.execute("SELECT COUNT(*) FROM pending_signals").fetchone()[0]
processed = db.execute("SELECT COUNT(*) FROM pending_signals WHERE processed=1").fetchone()[0]
print(f"Total signals: {total_sig}, Processed: {processed} ({processed/max(1,total_sig)*100:.1f}%)")

# Score distribution
print("\nScore distribution:")
for lo, hi in [(90,101),(80,90),(70,80),(60,70),(50,60),(0,50)]:
    cnt = db.execute(f"SELECT COUNT(*) FROM pending_signals WHERE score >= {lo} AND score < {hi}").fetchone()[0]
    print(f"  [{lo}-{hi}): {cnt}")

# Direction distribution
print("\nSignal directions:")
for row in db.execute("SELECT direction, COUNT(*), AVG(score) FROM pending_signals GROUP BY direction").fetchall():
    print(f"  {row[0]}: {row[1]} signals, avg score {row[2]:.1f}")

# Market phase distribution
print("\nMarket phases in signals:")
for row in db.execute("SELECT market_phase, COUNT(*) FROM pending_signals GROUP BY market_phase ORDER BY COUNT(*) DESC").fetchall():
    print(f"  {row[0]}: {row[1]}")

# will_continue_prob distribution
print("\nWill-continue probability (processed signals):")
for row in db.execute("""
    SELECT 
        CASE 
            WHEN will_continue_prob >= 0.9 THEN '0.9+'
            WHEN will_continue_prob >= 0.7 THEN '0.7-0.9'
            WHEN will_continue_prob >= 0.5 THEN '0.5-0.7'
            ELSE '<0.5'
        END as bucket,
        COUNT(*)
    FROM pending_signals
    GROUP BY bucket
    ORDER BY bucket DESC
""").fetchall():
    print(f"  {row[0]}: {row[1]}")

# Trade outcomes analysis
print(f"\n{'=' * 60}")
print("TRADE OUTCOMES vs IIE PREDICTIONS")
print(f"{'=' * 60}")
total_outcomes = db.execute("SELECT COUNT(*) FROM trade_outcomes").fetchone()[0]
print(f"Total outcomes tracked: {total_outcomes}")

# By bot
print("\nBy bot:")
for row in db.execute("""
    SELECT bot_name, COUNT(*), 
        SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
        AVG(pnl_pct), SUM(pnl_pct),
        AVG(CASE WHEN pnl_pct > 0 THEN pnl_pct END) as avg_win,
        AVG(CASE WHEN pnl_pct <= 0 THEN pnl_pct END) as avg_loss
    FROM trade_outcomes GROUP BY bot_name
""").fetchall():
    wr = row[2]/row[1]*100 if row[1] else 0
    print(f"  {row[0]}: {row[1]} trades, {row[2]} wins ({wr:.0f}%), avg {row[3]:.2f}%, total {row[4]:.2f}%")
    if row[5] and row[6]:
        print(f"    avg win: {row[5]:+.2f}%, avg loss: {row[6]:+.2f}%, profit factor: {abs(row[5]*row[2]) / abs(row[6]*(row[1]-row[2])):.2f}" if row[1]-row[2] > 0 else "")

# Recent training cycle info
print("\nRecent trade outcomes (last 10):")
for row in db.execute("""
    SELECT symbol, bot_name, pnl_pct, exit_reason, exit_time 
    FROM trade_outcomes ORDER BY exit_time DESC LIMIT 10
""").fetchall():
    dt = datetime.fromtimestamp(float(row[4]), tz=timezone.utc).strftime("%m-%d %H:%M") if row[4] else "?"
    result = "WIN" if row[2] > 0 else "LOSS"
    print(f"  {dt} {row[0]} ({row[1]}) {result} {row[2]:+.2f}% [{row[3]}]")

db.close()
print("\nDone.")
