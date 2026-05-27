#!/usr/bin/env python3
"""Deep audit using bot's own exchange_executor + state analysis."""
import os, sys, json
sys.path.insert(0, "/home/trader/soldier")
os.chdir("/home/trader/soldier")

from dotenv import load_dotenv
load_dotenv("/home/trader/soldier/.env")

# Part 1: Use bot's own exchange executor
from exchange_executor import ExchangeExecutor

try:
    exe = ExchangeExecutor.from_env()
    ex = exe._exchange
    
    print("=" * 80)
    print("  BINANCE TESTNET — REAL ACCOUNT STATE")
    print("=" * 80)
    
    # Futures balance via direct fapi call
    try:
        account = ex.fapiPrivateV2GetAccount()
        total_bal = float(account.get('totalWalletBalance', 0))
        avail_bal = float(account.get('availableBalance', 0))
        total_upnl = float(account.get('totalUnrealizedProfit', 0))
        total_margin = float(account.get('totalInitialMargin', 0))
        
        print(f"  Wallet Balance:    ${total_bal:,.4f}")
        print(f"  Available Balance: ${avail_bal:,.4f}")
        print(f"  Unrealized PnL:    ${total_upnl:+,.4f}")
        print(f"  Used Margin:       ${total_margin:,.4f}")
        print(f"  Starting Balance:  $5,000.00")
        print(f"  Net P&L:           ${total_bal - 5000:+,.4f}  ({(total_bal-5000)/5000*100:+.2f}%)")
        
        # Open positions
        positions = [p for p in account.get('positions', []) 
                    if abs(float(p.get('positionAmt', 0))) > 0]
        print(f"\n  Open positions: {len(positions)}")
        for p in positions:
            sym = p['symbol']
            amt = float(p['positionAmt'])
            entry = float(p.get('entryPrice', 0))
            upnl = float(p.get('unrealizedProfit', 0))
            lev = p.get('leverage', '?')
            side = "LONG" if amt > 0 else "SHORT"
            notional = abs(amt * entry)
            print(f"    {sym:18} {side:5} qty={abs(amt):<12} entry={entry:.6f}  uPnL=${upnl:+.4f}  lev={lev}x  notional=${notional:.2f}")
        
    except Exception as e:
        print(f"  Error fetching account: {e}")
        # Try simpler approach
        try:
            bal = ex.fetch_balance()
            usdt = bal.get('USDT', {})
            print(f"  USDT Total: ${float(usdt.get('total', 0)):,.4f}")
        except Exception as e2:
            print(f"  Also failed: {e2}")

    # Check available markets on testnet
    print(f"\n{'='*80}")
    print("  TESTNET SYMBOL AVAILABILITY CHECK")
    print("=" * 80)
    
    markets = ex.load_markets()
    testnet_futures = set()
    for sym, mkt in markets.items():
        if mkt.get('type') == 'swap' or ':USDT' in sym:
            testnet_futures.add(sym)
    
    print(f"  Total futures on testnet: {len(testnet_futures)}")

except Exception as e:
    print(f"Exchange error: {e}")
    testnet_futures = set()

# Part 2: State file analysis (works regardless of exchange)
print(f"\n{'='*80}")
print("  COMMISSION & PROFITABILITY ANALYSIS")
print("=" * 80)

with open("/home/trader/soldier/.local_ai/paper_trading/paper_state_multi.json") as f:
    state = json.load(f)

trades = state.get("completed_trades", [])
TAKER_FEE = 0.00045  # 0.045% per side on Binance futures
ROUND_TRIP = TAKER_FEE * 2  # 0.09% round trip

print(f"\n  Fee model: {TAKER_FEE*100:.3f}% per side = {ROUND_TRIP*100:.3f}% round trip")
print(f"  Analyzing {len(trades)} completed trades...\n")

total_notional = 0
total_commission = 0
gross_pnl_usd = 0
net_pnl_usd = 0
wins = losses = 0
win_pnl = loss_pnl = 0
small_wins_eaten = 0
trade_details = []

for t in trades:
    size = t.get("size_usdt", 0)
    pnl_pct = t.get("realized_pnl_pct", 0)
    leverage = 5  # from .env DEFAULT_LEVERAGE
    
    # Notional = position size (already leveraged in size_usdt for some versions)
    # size_usdt is the margin, notional = margin * leverage
    notional = size  # size_usdt appears to be the actual position size
    
    commission = notional * ROUND_TRIP
    profit_usd = size * pnl_pct / 100
    net_usd = profit_usd - commission
    
    total_notional += notional
    total_commission += commission
    gross_pnl_usd += profit_usd
    net_pnl_usd += net_usd
    
    if pnl_pct > 0:
        wins += 1
        win_pnl += pnl_pct
        if net_usd < 0:
            small_wins_eaten += 1
    else:
        losses += 1
        loss_pnl += pnl_pct
    
    trade_details.append({
        "sym": t.get("symbol", "?"),
        "dir": t.get("direction", "?"),
        "pnl_pct": pnl_pct,
        "gross_usd": profit_usd,
        "commission": commission,
        "net_usd": net_usd,
        "size": size,
        "reason": t.get("exit_reason", "?"),
    })

n = wins + losses
avg_win = win_pnl / max(wins, 1)
avg_loss = loss_pnl / max(losses, 1)

print(f"  Total trades:           {n}")
print(f"  Wins / Losses:          {wins} / {losses}")
print(f"  Win Rate:               {wins/max(n,1)*100:.1f}%")
print(f"  Avg Win%:               {avg_win:+.3f}%")
print(f"  Avg Loss%:              {avg_loss:+.3f}%")
print(f"  Profit Factor:          {abs(win_pnl)/max(abs(loss_pnl),0.01):.2f}")
print(f"")
print(f"  Total Notional Volume:  ${total_notional:,.2f}")
print(f"  Total Commission Est:   ${total_commission:,.2f}")
print(f"  Gross PnL (paper):      ${gross_pnl_usd:+,.2f}")
print(f"  Net PnL (after comm):   ${net_pnl_usd:+,.2f}")
print(f"  Commission % of gross:  {total_commission/max(abs(gross_pnl_usd),0.01)*100:.1f}%")
print(f"")
print(f"  Wins eaten by commission: {small_wins_eaten} / {wins} ({small_wins_eaten/max(wins,1)*100:.0f}%)")

# Check which traded symbols exist on testnet
if testnet_futures:
    print(f"\n{'='*80}")
    print("  SYMBOL AVAILABILITY ANALYSIS")
    print("=" * 80)
    
    traded_syms = set(t.get("symbol", "") for t in trades)
    missing_syms = []
    available_syms = []
    
    for s in traded_syms:
        base = s.replace("USDT", "")
        ccxt_sym = f"{base}/USDT:USDT"
        if ccxt_sym in testnet_futures:
            available_syms.append(s)
        else:
            missing_syms.append(s)
    
    print(f"  Traded symbols:       {len(traded_syms)}")
    print(f"  Available on testnet: {len(available_syms)}")
    print(f"  MISSING on testnet:   {len(missing_syms)}")
    
    if missing_syms:
        print(f"\n  ⚠️  MISSING SYMBOLS (trades may have FAILED on exchange):")
        for m in sorted(missing_syms):
            sym_trades = [t for t in trades if t.get("symbol") == m]
            count = len(sym_trades)
            pnl = sum(t.get("realized_pnl_pct", 0) for t in sym_trades)
            vol = sum(t.get("size_usdt", 0) for t in sym_trades)
            print(f"    {m:18} — {count:2} trades  PnL={pnl:+.2f}%  vol=${vol:.2f}")
        
        missing_count = sum(1 for t in trades if t.get("symbol") in missing_syms)
        missing_pnl = sum(t.get("realized_pnl_pct", 0) for t in trades if t.get("symbol") in missing_syms)
        print(f"\n  Total trades on missing symbols: {missing_count}")
        print(f"  PnL on missing symbols: {missing_pnl:+.2f}%")

# Exit reason analysis  
print(f"\n{'='*80}")
print("  EXIT REASON PROFITABILITY (net of commission)")
print("=" * 80)

reason_stats = {}
for td in trade_details:
    r = td["reason"]
    if r not in reason_stats:
        reason_stats[r] = {"count": 0, "gross": 0, "comm": 0, "net": 0, "wins": 0, "pnl_pct": 0}
    reason_stats[r]["count"] += 1
    reason_stats[r]["gross"] += td["gross_usd"]
    reason_stats[r]["comm"] += td["commission"]
    reason_stats[r]["net"] += td["net_usd"]
    reason_stats[r]["pnl_pct"] += td["pnl_pct"]
    if td["pnl_pct"] > 0:
        reason_stats[r]["wins"] += 1

print(f"  {'Reason':25} | {'#':>4} | {'WR%':>6} | {'Gross$':>9} | {'Comm$':>8} | {'Net$':>9} | {'PnL%':>8}")
print(f"  {'-'*25}-+-{'-'*4}-+-{'-'*6}-+-{'-'*9}-+-{'-'*8}-+-{'-'*9}-+-{'-'*8}")
for r, st in sorted(reason_stats.items(), key=lambda x: x[1]["net"], reverse=True):
    wr = st["wins"]/max(st["count"],1)*100
    print(f"  {r:25} | {st['count']:>4} | {wr:>5.1f}% | ${st['gross']:>+8.2f} | ${st['comm']:>7.2f} | ${st['net']:>+8.2f} | {st['pnl_pct']:>+7.2f}%")

print(f"\n{'='*80}")
print("  DEEP AUDIT COMPLETE")
print("=" * 80)
