#!/usr/bin/env python3
"""
Add automatic testnet symbol validation to paper_trader.py.
On startup, filter out symbols not available on the exchange.
"""

path = "/home/trader/soldier/paper_trader.py"

with open(path) as f:
    content = f.read()

# Insert symbol validation right after exchange sync
# Find the end of exchange sync block
old_marker = '    # ─── v9.1: Cached exchange balance'

new_block = '''    # ─── v9.2: Filter symbols to exchange-available only ─────
    if executor and executor._exchange:
        try:
            exchange_markets = executor._exchange.load_markets()
            available = set()
            for s in exchange_markets:
                raw = s.replace("/USDT:USDT", "USDT").replace("/", "")
                available.add(raw)
            before_count = len(symbols)
            symbols = [s for s in symbols if s in available]
            state.symbols = symbols
            removed = before_count - len(symbols)
            if removed > 0:
                logger.info(f"🔍 Filtered {removed} symbols not on exchange. Active: {len(symbols)}")
            else:
                logger.info(f"🔍 All {len(symbols)} symbols available on exchange")
        except Exception as e:
            logger.warning(f"⚠️ Symbol filter failed: {e}")
    # ──────────────────────────────────────────────────────────

    # ─── v9.1: Cached exchange balance'''

if old_marker in content:
    content = content.replace(old_marker, new_block, 1)
    with open(path, "w") as f:
        f.write(content)
    print("✅ Added symbol validation filter")
else:
    print("❌ Marker not found")
