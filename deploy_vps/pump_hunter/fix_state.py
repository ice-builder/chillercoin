#!/usr/bin/env python3
"""Fix demo_state.json: recalculate balance from closed trades, reset drawdown halt,
fix CGPTUSDT leverage 20→10."""
import json
from pathlib import Path

STATE = Path(__file__).parent / "demo_state.json"

s = json.loads(STATE.read_text())

# 1. Recalculate balance from initial deposit + all closed trade PnL
INITIAL_DEPOSIT = 10_000.0
balance = INITIAL_DEPOSIT
for t in s.get("completed_trades", []):
    pnl_usd = t.get("pnl_usd", 0)
    balance += pnl_usd

print(f"=== STATE FIX ===")
print(f"Old balance: ${s.get('demo_balance', 0):,.2f}")
print(f"Recalculated balance: ${balance:,.2f}")
print(f"Trades: {len(s.get('completed_trades', []))}")
for t in s.get("completed_trades", []):
    print(f"  {t['symbol']}: pnl_usd=${t.get('pnl_usd', 0):+,.2f} ({t.get('exit_reason', '?')})")

# 2. Update balance
s["demo_balance"] = balance

# 3. Set max_balance = current balance (this resets drawdown halt)
s["max_balance"] = balance
print(f"\nmax_balance set to: ${balance:,.2f}")
print(f"Drawdown from HWM: 0.0% (HALT CLEARED)")

# 4. Fix CGPTUSDT leverage if present
for key, pos in s.get("active_positions", {}).items():
    old_lev = pos.get("leverage", 0)
    if old_lev > 10:
        pos["leverage"] = 10
        print(f"\n⚡ Fixed {key}: leverage {old_lev}x → 10x")

# 5. Save
STATE.write_text(json.dumps(s, indent=2, default=str))
print(f"\n✅ State saved to {STATE}")
