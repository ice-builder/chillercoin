#!/usr/bin/env python3
"""Full audit of Soldier + Pump Hunter bots."""
import json
from pathlib import Path
from datetime import datetime

def fmt(ts):
    if not ts: return "N/A             "
    s = str(ts)
    return s[:16]

# ===================== SOLDIER =====================
print("=" * 80)
print("  SOLDIER BOT — Full Audit")
print("=" * 80)
with open("/home/trader/soldier/.local_ai/paper_trading/paper_state_multi.json") as f:
    s = json.load(f)

print(f"Deposit:       ${s['deposit']:,.2f}")
print(f"Total PnL:     {s['total_pnl_pct']:+.2f}%")
print(f"Wins/Losses:   {s['wins']}/{s['losses']}  (WR: {s['win_rate']:.1f}%)")
print(f"Signals seen:  {s['signals_seen']}")
print(f"Last updated:  {s['last_updated']}")

# Active positions
act = s.get("active_positions", {})
print(f"\nActive positions: {len(act)}")
for sym, p in act.items():
    d = p.get("direction", "?")
    ep = p.get("entry_price", 0)
    sl = p.get("stop_price", 0)
    tp = p.get("tp_price", 0)
    sz = p.get("size_usdt", 0)
    st = p.get("strategy_name", "?")
    print(f"  {sym:14} {d:5} entry={ep:.6f} SL={sl:.6f} TP={tp:.6f} ${sz:.2f} [{st}]")

# Completed trades - recent 30
trades = s.get("completed_trades", [])
print(f"\nTotal completed: {len(trades)}")

# Sort by exit_time desc
recent = sorted(trades, key=lambda t: str(t.get("exit_time", "")), reverse=True)[:30]
print(f"\n{'='*80}")
print(f"  Last 30 Closed Trades")
print(f"{'='*80}")
print(f"  {'Time':16} | {'Symbol':14} | {'Dir':5} | {'PnL%':>8} | {'$Size':>8} | {'Reason':20} | Strategy")
print(f"  {'-'*16}-+-{'-'*14}-+-{'-'*5}-+-{'-'*8}-+-{'-'*8}-+-{'-'*20}-+-{'-'*20}")

batch_pnl_pct = 0
batch_wins = 0
batch_losses = 0
for t in recent:
    ct = fmt(t.get("exit_time", ""))
    sym = t.get("symbol", "?")
    d = t.get("direction", "?")
    pnl_pct = t.get("realized_pnl_pct", 0)
    batch_pnl_pct += pnl_pct
    if pnl_pct > 0: batch_wins += 1
    else: batch_losses += 1
    sz = t.get("size_usdt", 0)
    reason = t.get("exit_reason", "?")
    strat = t.get("strategy_name", t.get("config_version", "?"))
    print(f"  {ct} | {sym:14} | {d:5} | {pnl_pct:+7.2f}% | ${sz:>7.2f} | {reason:20} | {strat}")

n = batch_wins + batch_losses
print(f"\n  Batch: W={batch_wins} L={batch_losses} WR={batch_wins/max(n,1)*100:.0f}%  Σ PnL%={batch_pnl_pct:+.2f}%")

# Strategy breakdown (all time)
strat_stats = {}
for t in trades:
    st = t.get("strategy_name", t.get("config_version", "unknown"))
    pnl = t.get("realized_pnl_pct", 0)
    sz = t.get("size_usdt", 0)
    if st not in strat_stats:
        strat_stats[st] = {"w": 0, "l": 0, "pnl_pct": 0, "total_usd": 0, "count": 0}
    strat_stats[st]["count"] += 1
    strat_stats[st]["pnl_pct"] += pnl
    strat_stats[st]["total_usd"] += sz
    if pnl > 0: strat_stats[st]["w"] += 1
    else: strat_stats[st]["l"] += 1

print(f"\n{'='*80}")
print(f"  Strategy Breakdown (all {len(trades)} trades)")
print(f"{'='*80}")
print(f"  {'Strategy':30} | {'#':>4} | {'W':>3} | {'L':>3} | {'WR%':>6} | {'Σ PnL%':>10} | {'Avg PnL%':>9}")
print(f"  {'-'*30}-+-{'-'*4}-+-{'-'*3}-+-{'-'*3}-+-{'-'*6}-+-{'-'*10}-+-{'-'*9}")
for st, d in sorted(strat_stats.items(), key=lambda x: x[1]["pnl_pct"], reverse=True):
    n = d["w"]+d["l"]
    avg = d["pnl_pct"]/max(n,1)
    print(f"  {st:30} | {n:>4} | {d['w']:>3} | {d['l']:>3} | {d['w']/max(n,1)*100:>5.1f}% | {d['pnl_pct']:>+9.2f}% | {avg:>+8.3f}%")

# Direction breakdown
dir_stats = {"long": {"w": 0, "l": 0, "pnl": 0}, "short": {"w": 0, "l": 0, "pnl": 0}}
for t in trades:
    d = t.get("direction", "long")
    pnl = t.get("realized_pnl_pct", 0)
    if d not in dir_stats:
        dir_stats[d] = {"w": 0, "l": 0, "pnl": 0}
    dir_stats[d]["pnl"] += pnl
    if pnl > 0: dir_stats[d]["w"] += 1
    else: dir_stats[d]["l"] += 1

print(f"\n  Direction breakdown:")
for d, st in dir_stats.items():
    n = st["w"]+st["l"]
    if n: print(f"    {d:5}: W={st['w']} L={st['l']} WR={st['w']/n*100:.0f}% PnL={st['pnl']:+.2f}%")

# Exit reason breakdown
reason_stats = {}
for t in trades:
    r = t.get("exit_reason", "unknown")
    pnl = t.get("realized_pnl_pct", 0)
    if r not in reason_stats:
        reason_stats[r] = {"count": 0, "pnl": 0}
    reason_stats[r]["count"] += 1
    reason_stats[r]["pnl"] += pnl

print(f"\n  Exit reason breakdown:")
for r, st in sorted(reason_stats.items(), key=lambda x: x[1]["count"], reverse=True):
    print(f"    {r:25} x{st['count']:>3}  PnL={st['pnl']:+.2f}%")


# ===================== PUMP HUNTER =====================
print(f"\n\n{'='*80}")
print(f"  PUMP HUNTER — Full Audit")
print(f"{'='*80}")

with open("/home/trader/pump-hunter/demo_state.json") as f:
    ph = json.load(f)

print(f"Balance:       ${ph['demo_balance']:,.2f}")
print(f"Total PnL:     {ph.get('total_pnl_pct', 0):+.2f}%")
print(f"Wins/Losses:   {ph.get('wins', 0)}/{ph.get('losses', 0)}")
print(f"Scans done:    {ph.get('scan_count', 0)}")
up_h = ph.get("uptime_sec", 0) / 3600
print(f"Uptime:        {up_h:.1f}h ({up_h/24:.1f}d)")

# Active positions
ph_act = ph.get("active_positions", {})
print(f"\nActive positions: {len(ph_act)}")
for key, p in ph_act.items():
    sym = p.get("symbol", key)
    d = p.get("direction", "?")
    ep = p.get("entry_price", 0)
    pnl = p.get("pnl_pct", 0)
    sl = p.get("stop_loss", 0)
    trail = p.get("trailing_stop", 0)
    lev = p.get("leverage", 0)
    ver = p.get("strategy_version", "?")
    sz_pct = p.get("size_pct", 0)
    print(f"  {sym:14} {d:5} entry={ep:.6f} PnL={pnl:+.2f}% SL={sl:.6f} trail={trail:.6f} lev={lev}x [{ver}]")

# Completed trades
ph_trades = ph.get("completed_trades", [])
print(f"\nTotal completed: {len(ph_trades)}")

print(f"\n{'='*80}")
print(f"  All Pump Hunter Trades")
print(f"{'='*80}")
print(f"  {'Time':16} | {'Symbol':14} | {'Dir':5} | {'PnL%':>8} | {'PnL$':>10} | {'Lev':>4} | {'Reason':20} | Ver")
print(f"  {'-'*16}-+-{'-'*14}-+-{'-'*5}-+-{'-'*8}-+-{'-'*10}-+-{'-'*4}-+-{'-'*20}-+-{'-'*5}")

ph_total_usd = 0
for t in sorted(ph_trades, key=lambda x: str(x.get("time", "")), reverse=True):
    ct = fmt(t.get("time", ""))
    sym = t.get("symbol", "?")
    d = t.get("direction", "?")
    pnl_pct = t.get("pnl_pct", 0)
    pnl_usd = t.get("pnl_usd", 0)
    ph_total_usd += pnl_usd
    lev = t.get("leverage", 0)
    reason = t.get("exit_reason", "?")
    ver = t.get("strategy_version", "?")
    print(f"  {ct} | {sym:14} | {d:5} | {pnl_pct:+7.2f}% | ${pnl_usd:>+9.2f} | {lev:>3}x | {reason:20} | {ver}")

print(f"\n  Total PnL (USD): ${ph_total_usd:+,.2f}")
print(f"  Current balance: ${ph['demo_balance']:,.2f}")

# Version breakdown
ver_stats = {}
for t in ph_trades:
    v = t.get("strategy_version", "?")
    pnl = t.get("pnl_usd", 0)
    if v not in ver_stats:
        ver_stats[v] = {"w": 0, "l": 0, "pnl": 0}
    ver_stats[v]["pnl"] += pnl
    if pnl > 0: ver_stats[v]["w"] += 1
    else: ver_stats[v]["l"] += 1

if ver_stats:
    print(f"\n  Version breakdown:")
    for v, d in sorted(ver_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
        n = d["w"]+d["l"]
        print(f"    {v:10} W={d['w']} L={d['l']} WR={d['w']/max(n,1)*100:.0f}% PnL=${d['pnl']:+,.2f}")

print("\n" + "=" * 80)
print("  AUDIT COMPLETE")
print("=" * 80)
