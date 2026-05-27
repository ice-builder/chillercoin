"""
Functional Test: SL/TP Algo Order Placement on Binance Testnet.
Opens a small test position and verifies SL/TP are placed via /fapi/v1/algoOrder.
"""
import os, json, time, hmac, hashlib
import requests as req
from urllib.parse import urlencode

# Load env
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    for line in open(env_path):
        l = line.strip()
        if l and not l.startswith("#") and "=" in l:
            k, v = l.split("=", 1)
            os.environ[k.strip()] = v.strip()

import ccxt
api_key = os.getenv("EXCHANGE_API_KEY")
api_secret = os.getenv("EXCHANGE_API_SECRET")
base_url = "https://testnet.binancefuture.com"

ex = ccxt.binanceusdm({
    "apiKey": api_key,
    "secret": api_secret,
    "options": {"defaultType": "swap", "adjustForTimeDifference": True},
})
ex.set_sandbox_mode(True)
ex.load_markets()

def signed_request(method, endpoint, params):
    """Make a signed request to Binance Futures API."""
    params["timestamp"] = str(int(time.time() * 1000))
    params["recvWindow"] = "5000"
    qs = urlencode(params)
    sig = hmac.new(api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    params["signature"] = sig
    headers = {"X-MBX-APIKEY": api_key}
    if method == "POST":
        return req.post(f"{base_url}{endpoint}", params=params, headers=headers, timeout=10)
    elif method == "GET":
        return req.get(f"{base_url}{endpoint}", params=params, headers=headers, timeout=10)
    elif method == "DELETE":
        return req.delete(f"{base_url}{endpoint}", params=params, headers=headers, timeout=10)


print("=" * 60)
print("FUNCTIONAL TEST: SL/TP via Algo Order API")
print("=" * 60)

# Step 1: Open small test LONG on BTC
print("\n[1] Opening test LONG BTC/USDT...")
try:
    ex.set_leverage(5, "BTC/USDT:USDT")
    order = ex.create_order("BTC/USDT:USDT", "market", "buy", 0.001)
    fill_price = float(order.get("average") or order.get("price") or 0)
    fill_qty = float(order.get("filled") or 0.001)
    print(f"    ✅ Opened LONG @ {fill_price}, qty={fill_qty}")
except Exception as e:
    print(f"    ❌ Failed to open: {e}")
    exit(1)

# Step 2: Place SL via algoOrder
sl_price = round(fill_price * 0.995, 2)  # 0.5% below
tp_price = round(fill_price * 1.010, 2)  # 1.0% above

print(f"\n[2] Placing SL @ {sl_price} via /fapi/v1/algoOrder...")
resp = signed_request("POST", "/fapi/v1/algoOrder", {
    "algoType": "CONDITIONAL",
    "symbol": "BTCUSDT",
    "side": "SELL",
    "type": "STOP_MARKET",
    "triggerPrice": str(sl_price),
    "quantity": str(fill_qty),
    "reduceOnly": "true",
    "workingType": "MARK_PRICE",
})
if resp.status_code == 200:
    data = resp.json()
    sl_algo_id = data.get("algoId")
    print(f"    ✅ SL placed: algoId={sl_algo_id}")
else:
    print(f"    ❌ SL failed: HTTP {resp.status_code}: {resp.text}")
    sl_algo_id = None

# Step 3: Place TP via algoOrder  
print(f"\n[3] Placing TP @ {tp_price} via /fapi/v1/algoOrder...")
resp = signed_request("POST", "/fapi/v1/algoOrder", {
    "algoType": "CONDITIONAL",
    "symbol": "BTCUSDT",
    "side": "SELL",
    "type": "TAKE_PROFIT_MARKET",
    "triggerPrice": str(tp_price),
    "quantity": str(fill_qty),
    "reduceOnly": "true",
    "workingType": "MARK_PRICE",
})
if resp.status_code == 200:
    data = resp.json()
    tp_algo_id = data.get("algoId")
    print(f"    ✅ TP placed: algoId={tp_algo_id}")
else:
    print(f"    ❌ TP failed: HTTP {resp.status_code}: {resp.text}")
    tp_algo_id = None

# Step 4: Verify open algo orders
print("\n[4] Verifying open algo orders...")
resp = signed_request("GET", "/fapi/v1/algoOrder/openOrders", {"symbol": "BTCUSDT"})
if resp.status_code == 200:
    orders = resp.json()
    if isinstance(orders, dict):
        orders = orders.get("orders", [])
    print(f"    Found {len(orders)} open algo orders:")
    for o in orders:
        print(f"      - {o.get('orderType')} {o.get('side')} triggerPrice={o.get('triggerPrice')} algoId={o.get('algoId')}")
else:
    print(f"    ❌ Query failed: {resp.text}")

# Step 5: Cancel SL/TP and close position
print("\n[5] Cleaning up: cancelling orders and closing position...")
for algo_id in [sl_algo_id, tp_algo_id]:
    if algo_id:
        resp = signed_request("DELETE", "/fapi/v1/algoOrder", {"algoId": str(algo_id)})
        if resp.status_code == 200:
            print(f"    ✅ Cancelled algoId={algo_id}")
        else:
            print(f"    ⚠️ Cancel algoId={algo_id}: {resp.text[:100]}")

try:
    close_order = ex.create_order("BTC/USDT:USDT", "market", "sell", fill_qty, params={"reduceOnly": True})
    print(f"    ✅ Closed position @ {close_order.get('average')}")
except Exception as e:
    print(f"    ⚠️ Close failed: {e}")

print("\n" + "=" * 60)
print("TEST COMPLETE")
print("=" * 60)
