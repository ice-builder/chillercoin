"""Test SL/TP order types on Binance Testnet."""
import ccxt, os, json, time, hmac, hashlib
import requests as req
from urllib.parse import urlencode

api_key = os.getenv("EXCHANGE_API_KEY", "")
api_secret = os.getenv("EXCHANGE_API_SECRET", "")

if not api_key:
    # Try loading from .env file manually
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()
        api_key = os.getenv("EXCHANGE_API_KEY", "")
        api_secret = os.getenv("EXCHANGE_API_SECRET", "")

ex = ccxt.binanceusdm({
    "apiKey": api_key,
    "secret": api_secret,
    "options": {"defaultType": "swap", "adjustForTimeDifference": True},
})
ex.set_sandbox_mode(True)
ex.load_markets()

# Show positions
print("=== Open Positions ===")
positions = ex.fetch_positions()
for p in positions:
    c = float(p.get("contracts", 0) or 0)
    if c > 0:
        print(f"  {p['symbol']} {p['side']} qty={c} entry={p['entryPrice']}")

# Test 1: Direct /fapi/v1/algoOrder
print("\n=== Test 1: /fapi/v1/algoOrder ===")
base_url = "https://testnet.binancefuture.com"
endpoint = "/fapi/v1/algoOrder"
params = {
    "algoType": "CONDITIONAL",
    "symbol": "BTCUSDT",
    "side": "SELL",
    "type": "STOP_MARKET",
    "triggerPrice": "70000",
    "quantity": "0.001",
    "reduceOnly": "true",
    "workingType": "MARK_PRICE",
    "timestamp": str(int(time.time() * 1000)),
    "recvWindow": "5000",
}
qs = urlencode(params)
sig = hmac.new(api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
params["signature"] = sig
headers = {"X-MBX-APIKEY": api_key}
resp = req.post(f"{base_url}{endpoint}", params=params, headers=headers, timeout=10)
print(f"  HTTP {resp.status_code}: {resp.text[:300]}")

# Test 2: STOP (limit) type — via ccxt
print("\n=== Test 2: STOP (limit) type ===")
try:
    result = ex.create_order(
        "BTC/USDT:USDT", "STOP", "sell", 0.001, 79000.0,
        params={"stopPrice": 79100.0, "reduceOnly": True}
    )
    print(f"  ✅ STOP order OK: id={result['id']}")
    ex.cancel_order(result["id"], "BTC/USDT:USDT")
    print("  Cancelled")
except Exception as e:
    print(f"  ❌ STOP error: {e}")

# Test 3: TAKE_PROFIT (limit) type — via ccxt
print("\n=== Test 3: TAKE_PROFIT (limit) type ===")
try:
    result = ex.create_order(
        "BTC/USDT:USDT", "TAKE_PROFIT", "sell", 0.001, 90000.0,
        params={"stopPrice": 89000.0, "reduceOnly": True}
    )
    print(f"  ✅ TP order OK: id={result['id']}")
    ex.cancel_order(result["id"], "BTC/USDT:USDT")
    print("  Cancelled")
except Exception as e:
    print(f"  ❌ TP error: {e}")

# Test 4: STOP_MARKET — via ccxt (the one that fails)
print("\n=== Test 4: STOP_MARKET via ccxt ===")
try:
    result = ex.create_order(
        "BTC/USDT:USDT", "STOP_MARKET", "sell", 0.001,
        params={"stopPrice": 79100.0, "reduceOnly": True}
    )
    print(f"  ✅ STOP_MARKET OK: id={result['id']}")
    ex.cancel_order(result["id"], "BTC/USDT:USDT")
    print("  Cancelled")
except Exception as e:
    print(f"  ❌ STOP_MARKET error: {e}")

# Test 5: TAKE_PROFIT_MARKET — via ccxt
print("\n=== Test 5: TAKE_PROFIT_MARKET via ccxt ===")
try:
    result = ex.create_order(
        "BTC/USDT:USDT", "TAKE_PROFIT_MARKET", "sell", 0.001,
        params={"stopPrice": 89000.0, "reduceOnly": True}
    )
    print(f"  ✅ TAKE_PROFIT_MARKET OK: id={result['id']}")
    ex.cancel_order(result["id"], "BTC/USDT:USDT")
    print("  Cancelled")
except Exception as e:
    print(f"  ❌ TAKE_PROFIT_MARKET error: {e}")

print("\n=== DONE ===")
