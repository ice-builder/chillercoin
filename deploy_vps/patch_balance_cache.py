#!/usr/bin/env python3
"""
Fix: Add balance caching to paper_trader.py to prevent Binance rate-limit bans.
Instead of calling executor.get_balance() multiple times per tick,
cache it once per tick cycle with a 60s TTL.
"""

path = "/home/trader/soldier/paper_trader.py"

with open(path) as f:
    content = f.read()

patches = 0

# ═══════════════════════════════════════════════════════════
# PATCH A: Add cached balance function after executor init
# ═══════════════════════════════════════════════════════════

# Insert balance cache right after the exchange sync block
old_sync_end = '    # ─── Exchange Position Sync ────────────────────────────'

new_cache_block = '''    # ─── v9.1: Cached exchange balance (avoids rate-limit bans) ──
    _cached_balance = 0.0
    _cached_balance_ts = 0
    _BALANCE_CACHE_TTL = 120  # seconds — refresh at most once per 2 min
    import time as _time_module

    def get_cached_balance():
        nonlocal _cached_balance, _cached_balance_ts
        now = _time_module.time()
        if now - _cached_balance_ts < _BALANCE_CACHE_TTL and _cached_balance > 0:
            return _cached_balance
        if executor:
            try:
                bal = executor.get_balance()
                if bal > 0:
                    _cached_balance = bal
                    _cached_balance_ts = now
                    return bal
            except Exception as e:
                logger.debug(f"Balance fetch failed: {e}")
        return _cached_balance if _cached_balance > 0 else deposit
    # ──────────────────────────────────────────────────────────

    # ─── Exchange Position Sync ────────────────────────────'''

if old_sync_end in content:
    content = content.replace(old_sync_end, new_cache_block, 1)
    patches += 1
    print("✅ PATCH A: Added get_cached_balance()")
else:
    print("❌ PATCH A FAILED")

# ═══════════════════════════════════════════════════════════
# PATCH B: Replace all executor.get_balance() calls with get_cached_balance()
# ═══════════════════════════════════════════════════════════

replacements = [
    # Position sizing (IIE)
    ("_live_bal = executor.get_balance() if executor else deposit",
     "_live_bal = get_cached_balance()"),
    # Heartbeat
    ("_hb_bal = executor.get_balance() if executor else deposit",
     "_hb_bal = get_cached_balance()"),
    # Tick log
    ("_tick_bal = executor.get_balance() if executor else 0",
     "_tick_bal = get_cached_balance()"),
    # Drawdown
    ("_dd_bal = executor.get_balance() if executor else 0",
     "_dd_bal = get_cached_balance()"),
]

for old, new in replacements:
    if old in content:
        content = content.replace(old, new)
        patches += 1
        print(f"✅ Replaced: {old[:50]}...")
    else:
        print(f"⚠️ Not found: {old[:50]}...")

with open(path, "w") as f:
    f.write(content)

print(f"\nApplied {patches} patches")
