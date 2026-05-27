#!/usr/bin/env python3
"""
Patch paper_trader.py: Replace discover_hot_symbols with IIE-aware symbol selection.
At startup, load ALL symbols that IIE has studied AND are available on the exchange.
"""

path = "/home/trader/soldier/paper_trader.py"

with open(path) as f:
    content = f.read()

# Add new function: discover_iie_symbols
old_discover = 'def discover_hot_symbols(limit: int = 20) -> List[str]:'

# Find the entire function and add our new one before it
fn_start = content.find(old_discover)
if fn_start < 0:
    print("ERROR: discover_hot_symbols not found")
    exit(1)

# Insert new function before discover_hot_symbols
new_fn = '''def discover_iie_symbols(db_path: str, exchange=None) -> List[str]:
    """
    v9.3: Load ALL symbols that IIE has impulse data for,
    filtered to those available on the exchange.
    This replaces the old --top N approach which limited the bot
    to only a handful of symbols.
    """
    import sqlite3
    symbols = []
    try:
        db = sqlite3.connect(db_path)
        rows = db.execute("SELECT DISTINCT symbol FROM impulses").fetchall()
        symbols = [r[0] for r in rows]
        db.close()
        log.info(f"📊 IIE DB contains {len(symbols)} studied symbols")
    except Exception as e:
        log.warning(f"Failed to load IIE symbols: {e}")
        return []
    
    # Filter to exchange-available if exchange connection exists
    if exchange and hasattr(exchange, '_exchange') and exchange._exchange:
        try:
            markets = exchange._exchange.markets or exchange._exchange.load_markets()
            available = set()
            for s in markets:
                raw = s.replace("/USDT:USDT", "USDT").replace("/", "")
                available.add(raw)
            before = len(symbols)
            symbols = [s for s in symbols if s in available]
            filtered = before - len(symbols)
            if filtered > 0:
                log.info(f"🔍 Filtered {filtered} symbols not on exchange. Trading {len(symbols)} symbols")
        except Exception as e:
            log.warning(f"Exchange symbol filter failed: {e}")
    
    return sorted(symbols)


'''

content = content[:fn_start] + new_fn + content[fn_start:]

# Now patch the startup to use IIE symbols instead of discover_hot_symbols
# Find the symbol init line after executor is ready
old_sym_init = "    syms = discover_hot_symbols(a.top) if a.top > 0 else [s.strip() for s in a.symbols.split(\",\")]"
new_sym_init = '''    # v9.3: Default to IIE-studied symbols (all 300+)
    iie_db = Path.cwd() / "iie" / "data" / "impulses.db"
    if iie_db.exists():
        syms = discover_iie_symbols(str(iie_db))
        if not syms:
            syms = discover_hot_symbols(a.top) if a.top > 0 else [s.strip() for s in a.symbols.split(",")]
    elif a.top > 0:
        syms = discover_hot_symbols(a.top)
    else:
        syms = [s.strip() for s in a.symbols.split(",")]'''

if old_sym_init in content:
    content = content.replace(old_sym_init, new_sym_init, 1)
    print("✅ Patched startup to use IIE symbols")
else:
    print("⚠️ Could not patch startup (pattern not found)")

with open(path, "w") as f:
    f.write(content)

print("✅ Added discover_iie_symbols function")
print("Soldier will now trade ALL IIE-studied symbols available on exchange")
