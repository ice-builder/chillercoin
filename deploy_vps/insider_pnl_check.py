#!/usr/bin/env python3
"""Deep analysis of insider scanner trades with simulations."""
import re, requests

log_path = '/home/trader/.pm2/logs/insider-scanner-error.log'
with open(log_path) as f:
    lines = f.read().split('\n')

entries = []
i = 0
while i < len(lines):
    if 'INSIDER AUTO-ENTER' in lines[i]:
        ts = lines[i][:19]
        symbol = exchange = score = entry_price = size = oi_info = ''
        for j in range(i+1, min(i+10, len(lines))):
            l = lines[j]
            m = re.search(r'LONG \*(\w+)\* on (\w+)', l)
            if m: symbol, exchange = m.group(1), m.group(2)
            m = re.search(r'Score: \*(\d+)\*', l)
            if m: score = m.group(1)
            m = re.search(r'Entry: .([0-9.]+)', l)
            if m: entry_price = m.group(1)
            m = re.search(r'Size: .([0-9,]+)', l)
            if m: size = m.group(1)
            if 'OI:' in l: oi_info = l.strip()
        if symbol and entry_price:
            entries.append(dict(ts=ts, symbol=symbol, exchange=exchange,
                              score=int(score or 0), entry=float(entry_price),
                              size=float(size.replace(',','') if size else '0'), oi=oi_info))
    i += 1

for e in entries:
    try:
        r = requests.get(
            f'https://api.bybit.com/v5/market/tickers?category=linear&symbol={e["symbol"]}',
            timeout=5)
        d = r.json()
        if d.get('result',{}).get('list'):
            e['cur'] = float(d['result']['list'][0]['lastPrice'])
        else:
            e['cur'] = 0
    except:
        e['cur'] = 0
    e['pnl'] = ((e['cur']/e['entry'])-1)*100 if e['entry'] > 0 and e['cur'] > 0 else 0
    e['pnl_usd'] = e['size']*20*e['pnl']/100

print('=== BY SCORE ===')
for sc in sorted(set(e['score'] for e in entries)):
    grp = [e for e in entries if e['score']==sc]
    avg = sum(e['pnl'] for e in grp)/len(grp)
    total = sum(e['pnl_usd'] for e in grp)
    wins = sum(1 for e in grp if e['pnl']>0)
    print(f'  Score {sc}: {len(grp)} trades, W{wins}/L{len(grp)-wins}, avg={avg:+.1f}%, total=${total:+,.0f}')

print('\n=== BY EXCHANGE ===')
for ex in sorted(set(e['exchange'] for e in entries)):
    grp = [e for e in entries if e['exchange']==ex]
    avg = sum(e['pnl'] for e in grp)/len(grp)
    total = sum(e['pnl_usd'] for e in grp)
    wins = sum(1 for e in grp if e['pnl']>0)
    print(f'  {ex:8s}: {len(grp)} trades, W{wins}/L{len(grp)-wins}, avg={avg:+.1f}%, total=${total:+,.0f}')

print('\n=== TOP 5 WINNERS ===')
for e in sorted(entries, key=lambda x: x['pnl_usd'], reverse=True)[:5]:
    print(f'  {e["symbol"]:18s} {e["pnl"]:+.1f}% ${e["pnl_usd"]:+,.0f} score={e["score"]} {e["exchange"]}')

print('\n=== TOP 5 LOSERS ===')
for e in sorted(entries, key=lambda x: x['pnl_usd'])[:5]:
    print(f'  {e["symbol"]:18s} {e["pnl"]:+.1f}% ${e["pnl_usd"]:+,.0f} score={e["score"]} {e["exchange"]}')

broken = [e for e in entries if e['entry'] == 0]
if broken:
    print(f'\n=== BROKEN ENTRIES (price=0): {len(broken)} ===')
    for e in broken:
        print(f'  {e["symbol"]} {e["exchange"]} {e["ts"]}')

# Simulations
actual = sum(e['pnl_usd'] for e in entries)

print(f'\n{"="*60}')
print(f'ACTUAL: ${actual:+,.0f}')

for stop in [-3, -5, -7, -10]:
    total = sum(e['size']*20*max(e['pnl'], stop)/100 for e in entries)
    print(f'  With {stop}% hard stop: ${total:+,.0f}')

print()
for min_sc in [15, 16, 17, 18]:
    grp = [e for e in entries if e['score'] >= min_sc]
    total = sum(e['pnl_usd'] for e in grp)
    wins = sum(1 for e in grp if e['pnl']>0)
    print(f'  Score >= {min_sc}: {len(grp)} trades, W{wins}/L{len(grp)-wins}, P&L=${total:+,.0f}')

print()
for min_sc in [15, 16, 17, 18]:
    for stop in [-3, -5]:
        grp = [e for e in entries if e['score'] >= min_sc]
        total = sum(e['size']*20*max(e['pnl'], stop)/100 for e in grp)
        wins = sum(1 for e in grp if e['pnl']>0)
        print(f'  Score >= {min_sc} + {stop}% stop: {len(grp)} trades, P&L=${total:+,.0f}')

# Leverage sim
print('\n=== LEVERAGE SIMULATION (all trades, -5% stop) ===')
for lev in [5, 10, 15, 20]:
    total = sum(e['size']*lev*max(e['pnl'], -5)/100 for e in entries)
    print(f'  {lev}x leverage: P&L=${total:+,.0f}')
