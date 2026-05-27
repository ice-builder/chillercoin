#!/usr/bin/env python3
"""Quick analysis of soldier state + IIE model."""
import json, os, sqlite3
from datetime import datetime

# --- Soldier State ---
state_path = os.path.expanduser("~/.local_ai/paper_trading/paper_state_multi.json")
if not os.path.exists(state_path):
    state_path = ".local_ai/paper_trading/paper_state_multi.json"

with open(state_path) as f:
    state = json.load(f)

balance = state.get("balance", 0)
active = state.get("active_positions", [])
closed = state.get("closed_trades", [])
print(f"=== SOLDIER BOT ===")
print(f"Balance: ${balance:.2f}")
print(f"Active positions: {len(active)}")
print(f"Closed trades: {len(closed)}")
print(f"Last updated: {state.get('last_updated', 'unknown')}")

for p in active:
    sym = p.get("symbol","?")
    d = p.get("direction","?")
    ep = p.get("entry_price",0)
    sl = p.get("stop_price",0)
    tp = p.get("tp_price",0)
    sn = p.get("strategy_name","?")
    print(f"  ACTIVE: {sym} {d} @ {ep} | SL: {sl} | TP: {tp} | {sn}")

if closed:
    wins = sum(1 for t in closed if t.get("realized_pnl_pct", 0) > 0)
    losses = len(closed) - wins
    avg = sum(t.get("realized_pnl_pct", 0) for t in closed) / len(closed)
    total_pnl = sum(t.get("realized_pnl_pct", 0) for t in closed)
    print(f"\nOverall: {len(closed)} trades, {wins}W/{losses}L, WR={wins/len(closed)*100:.0f}%, avg={avg:.3f}%, total={total_pnl:.3f}%")
    
    # By strategy
    strats = {}
    for t in closed:
        sn = t.get("strategy_name", "unknown")
        if sn not in strats:
            strats[sn] = {"w": 0, "l": 0, "pnl": 0}
        if t.get("realized_pnl_pct", 0) > 0:
            strats[sn]["w"] += 1
        else:
            strats[sn]["l"] += 1
        strats[sn]["pnl"] += t.get("realized_pnl_pct", 0)
    print("\n  By Strategy:")
    for s, v in sorted(strats.items(), key=lambda x: -(x[1]["w"]+x[1]["l"])):
        tt = v["w"] + v["l"]
        wr = v["w"]/tt*100 if tt > 0 else 0
        print(f"    {s}: {tt} trades, {v['w']}W/{v['l']}L, WR={wr:.0f}%, PnL={v['pnl']:.3f}%")
    
    # By exit reason
    reasons = {}
    for t in closed:
        er = t.get("exit_reason", "unknown")
        if er not in reasons:
            reasons[er] = {"count": 0, "pnl": 0}
        reasons[er]["count"] += 1
        reasons[er]["pnl"] += t.get("realized_pnl_pct", 0)
    print("\n  By Exit Reason:")
    for r, v in sorted(reasons.items(), key=lambda x: -x[1]["count"]):
        print(f"    {r}: {v['count']} trades, total PnL={v['pnl']:.3f}%")
    
    # Last 10 trades
    print("\n  Last 10 trades:")
    for t in closed[-10:]:
        et = str(t.get("exit_time","?"))[:16]
        sym = t.get("symbol","?")
        d = t.get("direction","?")
        pnl = t.get("realized_pnl_pct",0)
        er = t.get("exit_reason","?")
        sn = t.get("strategy_name","?")
        bh = t.get("bars_held","?")
        print(f"    {et} | {sym} {d} | {pnl:+.3f}% | {er} | {sn} | {bh} bars")

# --- IIE Model Analysis ---
print("\n\n=== IIE PREDICTOR MODEL ===")
try:
    import pickle
    model_path = "iie/data/models/predictor_state.pkl"
    with open(model_path, "rb") as f:
        model_state = pickle.load(f)
    
    if isinstance(model_state, dict):
        for k, v in model_state.items():
            if isinstance(v, (int, float, str, bool)):
                print(f"  {k}: {v}")
            elif isinstance(v, list):
                print(f"  {k}: list[{len(v)}]")
            elif isinstance(v, dict):
                print(f"  {k}: dict[{len(v)} keys]")
            else:
                print(f"  {k}: {type(v).__name__}")
    else:
        print(f"  Model type: {type(model_state).__name__}")
        if hasattr(model_state, '__dict__'):
            for k, v in model_state.__dict__.items():
                if isinstance(v, (int, float, str, bool)):
                    print(f"  .{k}: {v}")
                elif isinstance(v, (list, tuple)):
                    print(f"  .{k}: {type(v).__name__}[{len(v)}]")
                elif isinstance(v, dict):
                    print(f"  .{k}: dict[{len(v)} keys]")
                elif hasattr(v, 'shape'):
                    print(f"  .{k}: {type(v).__name__} shape={v.shape}")
                else:
                    print(f"  .{k}: {type(v).__name__}")
except Exception as e:
    print(f"  Error loading model: {e}")

# --- IIE DB deeper analysis ---
print("\n\n=== IIE SIGNAL QUALITY ===")
try:
    db = sqlite3.connect("iie/data/impulses.db")
    
    # Recent impulse stats
    row = db.execute("SELECT COUNT(*), AVG(volume_z), AVG(return_z) FROM impulses WHERE ts > strftime('%s','now') - 86400").fetchone()
    print(f"  Last 24h impulses: {row[0]}")
    if row[1]:
        print(f"  Avg volume_z: {row[1]:.2f}, avg return_z: {row[2]:.2f}")
    
    # Check impulse table columns
    cols = [c[1] for c in db.execute("PRAGMA table_info(impulses)").fetchall()]
    print(f"  Impulse columns: {cols}")
    
    # Recent processed signals and their outcomes
    processed = db.execute("""
        SELECT s.symbol, s.score, s.direction, s.will_continue_prob, s.coin_quality,
               t.pnl_pct, t.exit_reason
        FROM pending_signals s
        LEFT JOIN trade_outcomes t ON t.symbol = s.symbol
        WHERE s.processed = 1
        ORDER BY s.created_at DESC LIMIT 15
    """).fetchall()
    
    if processed:
        print("\n  Recent processed signals -> outcomes:")
        for p in processed:
            outcome = f"PnL={p[5]:+.2f}% ({p[6]})" if p[5] is not None else "no outcome"
            print(f"    {p[0]} score={p[1]:.0f} {p[2]} cont={p[3]:.3f} qual={p[4]:.0f} -> {outcome}")
    
    db.close()
except Exception as e:
    print(f"  Error: {e}")
