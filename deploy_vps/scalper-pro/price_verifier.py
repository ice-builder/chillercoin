"""
Scalper Pro — Multi-Exchange Price Verifier

Fetches prices from Bybit, Binance mainnet, and OKX simultaneously.
Ensures trade execution uses REAL verified prices, not fictional data.

Verification:
  1. Get price from all 3 exchanges
  2. Check divergence < 0.5%
  3. Use median price for trade execution
  4. Store all 3 prices as proof in DB
"""
import time
import logging
import threading
from typing import Optional, Dict, Tuple
from dataclasses import dataclass, field

import requests

import config

logger = logging.getLogger("scalper.price")

# ── Price Snapshot ─────────────────────────────────────────────────────────────

@dataclass
class PriceSnapshot:
    """Verified price from multiple exchanges at a point in time."""
    symbol: str
    timestamp: float
    bybit_price: float = 0.0
    binance_price: float = 0.0
    okx_price: float = 0.0
    median_price: float = 0.0
    divergence_pct: float = 0.0
    sources_count: int = 0
    is_verified: bool = False       # True if >= 2 sources agree within threshold

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "bybit": self.bybit_price,
            "binance": self.binance_price,
            "okx": self.okx_price,
            "median": self.median_price,
            "divergence_pct": self.divergence_pct,
            "sources": self.sources_count,
            "verified": self.is_verified,
        }


# ── Rate Limiter ──────────────────────────────────────────────────────────────

_last_call: Dict[str, float] = {}
_MIN_INTERVAL = 0.5  # 500ms between calls to same exchange

def _rate_limit(exchange: str):
    now = time.time()
    last = _last_call.get(exchange, 0)
    wait = _MIN_INTERVAL - (now - last)
    if wait > 0:
        time.sleep(wait)
    _last_call[exchange] = time.time()


# ── Batch Price Cache (all tickers) ───────────────────────────────────────────

_batch_cache: Dict[str, float] = {}  # symbol -> price
_batch_ts: float = 0
_batch_lock = threading.Lock()
_BATCH_TTL = 10  # Refresh all tickers every 10s


def _refresh_batch_prices():
    """Fetch ALL Bybit linear tickers in one API call."""
    global _batch_cache, _batch_ts
    now = time.time()
    if now - _batch_ts < _BATCH_TTL:
        return

    try:
        _rate_limit("bybit_batch")
        resp = requests.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear"},
            timeout=10,
        )
        data = resp.json()
        if data.get("retCode") == 0 and data.get("result", {}).get("list"):
            with _batch_lock:
                _batch_cache.clear()
                for ticker in data["result"]["list"]:
                    sym = ticker.get("symbol", "")
                    price = float(ticker.get("lastPrice", 0))
                    if sym and price > 0:
                        _batch_cache[sym] = price
                _batch_ts = now
            logger.debug(f"📡 Batch prices: {len(_batch_cache)} tickers")
    except Exception as e:
        logger.warning(f"Batch price fetch failed: {e}")


def get_price_fast(symbol: str) -> float:
    """Get price from batch cache (fastest, single exchange)."""
    _refresh_batch_prices()
    with _batch_lock:
        return _batch_cache.get(symbol, 0)


# ── Individual Exchange Fetchers ──────────────────────────────────────────────

def _get_bybit_price(symbol: str) -> float:
    """Bybit linear futures price."""
    # First try batch cache
    _refresh_batch_prices()
    with _batch_lock:
        cached = _batch_cache.get(symbol, 0)
    if cached > 0:
        return cached

    try:
        _rate_limit("bybit")
        resp = requests.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear", "symbol": symbol},
            timeout=5,
        )
        data = resp.json()
        if data.get("retCode") == 0 and data.get("result", {}).get("list"):
            return float(data["result"]["list"][0]["lastPrice"])
    except Exception:
        pass
    return 0


def _get_binance_price(symbol: str) -> float:
    """Binance futures MAINNET price (not testnet)."""
    try:
        _rate_limit("binance")
        resp = requests.get(
            "https://fapi.binance.com/fapi/v1/ticker/price",
            params={"symbol": symbol},
            timeout=5,
        )
        data = resp.json()
        return float(data.get("price", 0))
    except Exception:
        return 0


def _get_okx_price(symbol: str) -> float:
    """OKX perpetual swap price."""
    try:
        base = symbol.replace("USDT", "")
        inst_id = f"{base}-USDT-SWAP"
        _rate_limit("okx")
        resp = requests.get(
            "https://www.okx.com/api/v5/market/ticker",
            params={"instId": inst_id},
            timeout=5,
        )
        data = resp.json()
        if data.get("code") == "0" and data.get("data"):
            return float(data["data"][0]["last"])
    except Exception:
        pass
    return 0


# ── Verified Price ────────────────────────────────────────────────────────────

def get_verified_price(symbol: str) -> PriceSnapshot:
    """
    Get price from multiple exchanges and verify consistency.

    Returns PriceSnapshot with:
      - median_price: median of available prices (trade execution price)
      - is_verified: True if >=2 sources within divergence threshold
      - Individual prices from each exchange (for audit trail)
    """
    snap = PriceSnapshot(symbol=symbol, timestamp=time.time())

    # Fetch from all exchanges
    snap.bybit_price = _get_bybit_price(symbol)
    snap.binance_price = _get_binance_price(symbol)
    snap.okx_price = _get_okx_price(symbol)

    # Collect non-zero prices
    prices = []
    if snap.bybit_price > 0:
        prices.append(snap.bybit_price)
    if snap.binance_price > 0:
        prices.append(snap.binance_price)
    if snap.okx_price > 0:
        prices.append(snap.okx_price)

    snap.sources_count = len(prices)

    if not prices:
        logger.warning(f"⚠️ No prices for {symbol} from any exchange")
        return snap

    # Calculate median
    prices.sort()
    if len(prices) % 2 == 1:
        snap.median_price = prices[len(prices) // 2]
    else:
        mid = len(prices) // 2
        snap.median_price = (prices[mid - 1] + prices[mid]) / 2

    # Calculate max divergence
    if len(prices) >= 2:
        max_div = max(
            abs(p - snap.median_price) / snap.median_price * 100
            for p in prices
        )
        snap.divergence_pct = round(max_div, 4)
        snap.is_verified = (
            snap.divergence_pct < config.PRICE_DIVERGENCE_THRESHOLD
        )
    elif len(prices) == 1:
        snap.median_price = prices[0]
        snap.is_verified = False  # Single source — not verified
        snap.divergence_pct = 0

    if snap.is_verified:
        logger.debug(
            f"✅ {symbol} verified: ${snap.median_price:.6g} "
            f"({snap.sources_count} sources, div={snap.divergence_pct:.3f}%)"
        )
    else:
        logger.warning(
            f"⚠️ {symbol} NOT verified: div={snap.divergence_pct:.3f}% "
            f"(threshold={config.PRICE_DIVERGENCE_THRESHOLD}%) "
            f"bybit={snap.bybit_price} binance={snap.binance_price} okx={snap.okx_price}"
        )

    return snap


def get_multiple_verified(symbols: list[str]) -> Dict[str, PriceSnapshot]:
    """Get verified prices for multiple symbols efficiently."""
    _refresh_batch_prices()  # Pre-warm cache
    result = {}
    for sym in symbols:
        result[sym] = get_verified_price(sym)
        time.sleep(0.1)  # Tiny delay between symbols
    return result
