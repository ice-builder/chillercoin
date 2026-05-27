"""Quick analysis of Insider Scanner trades."""
import json
from collections import defaultdict

trades = json.load(open('insider_trades.json'))
pos_data = json.load(open('insider_positions.json'))
balance = pos_data.get('balance', 0)
active = {k: v for k, v in pos_data.items() if k != 'balance'}

print(f"Balance: ${balance:.0f} | Completed: {len(trades)} | Active: {len(active)}")
wins = sum(1 for t in trades if t['pnl_pct'] > 0)
total_pnl = sum(t['pnl_pct'] for t in trades)
print(f"W:{wins} L:{len(trades)-wins} WR:{wins/len(trades)*100:.0f}% PnL:{total_pnl:+.1f}%")

print("\n=== BY DIRECTION ===")
for d in ['long', 'short']:
    dt = [t for t in trades if t['direction'] == d]
    if dt:
        pnl = sum(t['pnl_pct'] for t in dt)
        w = sum(1 for t in dt if t['pnl_pct'] > 0)
        print(f"  {d:6s}: {len(dt):2d}x  W:{w} L:{len(dt)-w}  WR:{w/len(dt)*100:.0f}%  PnL:{pnl:+.1f}%")

print("\n=== BY SYMBOL (worst first) ===")
sym = defaultdict(list)
for t in trades:
    sym[t['symbol']].append(t['pnl_pct'])
for s in sorted(sym, key=lambda x: sum(sym[x])):
    ts = sym[s]
    w = sum(1 for p in ts if p > 0)
    print(f"  {s:15s} {len(ts):2d}x  WR:{w/len(ts)*100:4.0f}%  PnL:{sum(ts):+7.1f}%")

print("\n=== BY EXIT REASON ===")
rx = defaultdict(list)
for t in trades:
    rx[t['exit_reason']].append(t['pnl_pct'])
for r in sorted(rx, key=lambda x: sum(rx[x])):
    ts = rx[r]
    print(f"  {r:20s} {len(ts):2d}x  PnL:{sum(ts):+7.1f}%  avg:{sum(ts)/len(ts):+.1f}%")

print("\n=== TRADE SIZES ===")
winners = [t['pnl_pct'] for t in trades if t['pnl_pct'] > 0]
losers = [t['pnl_pct'] for t in trades if t['pnl_pct'] <= 0]
if winners:
    print(f"  Avg winner:  {sum(winners)/len(winners):+.2f}%  ({len(winners)}x)")
if losers:
    print(f"  Avg loser:   {sum(losers)/len(losers):+.2f}%  ({len(losers)}x)")
print(f"  Best:  {max(t['pnl_pct'] for t in trades):+.2f}%")
print(f"  Worst: {min(t['pnl_pct'] for t in trades):+.2f}%")

print("\n=== INSIDER SCORES ===")
all_sc = [t['insider_score'] for t in trades]
win_sc = [t['insider_score'] for t in trades if t['pnl_pct'] > 0]
lose_sc = [t['insider_score'] for t in trades if t['pnl_pct'] <= 0]
print(f"  All:    {sum(all_sc)/len(all_sc):.0f}  (range {min(all_sc)}-{max(all_sc)})")
if win_sc:
    print(f"  Wins:   {sum(win_sc)/len(win_sc):.0f}")
if lose_sc:
    print(f"  Losses: {sum(lose_sc)/len(lose_sc):.0f}")

# Score brackets
print("\n=== SCORE BRACKETS ===")
for lo, hi in [(0, 15), (15, 25), (25, 35), (35, 100)]:
    bt = [t for t in trades if lo <= t['insider_score'] < hi]
    if bt:
        pnl = sum(t['pnl_pct'] for t in bt)
        w = sum(1 for t in bt if t['pnl_pct'] > 0)
        print(f"  Score {lo:2d}-{hi:2d}: {len(bt):2d}x  WR:{w/len(bt)*100:4.0f}%  PnL:{pnl:+7.1f}%")

print("\n=== LEVERAGE & SIZE ===")
levs = set(t.get('leverage', 0) for t in trades)
sizes = [t.get('size_usdt', 0) for t in trades]
print(f"  Leverage: {levs}")
print(f"  Size: ${min(sizes):.0f}-${max(sizes):.0f}")

print("\n=== ALL 29 TRADES (chronological) ===")
for i, t in enumerate(trades):
    icon = '+' if t['pnl_pct'] > 0 else 'X'
    dur = t.get('duration_min', 0)
    sc = t.get('insider_score', 0)
    lev = t.get('leverage', 0)
    entry = t.get('entry_time', '')[:16]
    print(f"  {icon} {entry} {t['symbol']:15s} {t['pnl_pct']:+7.2f}% (${t['pnl_usd']:+7.0f}) {t['exit_reason']:15s} sc:{sc:2d} {dur:4.0f}m lev:{lev}x")

print("\n=== ACTIVE POSITIONS ===")
for sym, pos in active.items():
    if isinstance(pos, dict):
        print(f"  {sym}: {pos.get('direction','?')} entry:{pos.get('entry_price',0)} sc:{pos.get('insider_score',0)}")
