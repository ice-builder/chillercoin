"""
IIE — Post-Trade Tracker

Background process that fills in post_impulse_outcomes:
- What happened to price after each impulse?
- Max favorable/adverse moves
- Was it a stop hunt? (reversal >50% within 3 bars)
- Did it make a new extremum?
- How many continuation impulses followed?
"""
import time
import logging
from typing import Optional, Dict, List

from . import config
from .impulse_db import ImpulseDB
from .impulse_collector import fetch_klines_bybit

logger = logging.getLogger("iie.tracker")


class PostTradeTracker:
    """Updates post_impulse_outcomes for all pending impulses."""

    def __init__(self, db: ImpulseDB):
        self.db = db
        self._price_cache: Dict[str, float] = {}
        self._cache_ts: float = 0

    def run_update(self) -> int:
        """Run one update cycle. Returns number of outcomes updated."""
        batch_limit = getattr(config, "POST_TRACKER_BATCH_LIMIT", 2000)
        pending = self.db.get_pending_outcomes(limit=batch_limit)
        if not pending:
            return 0

        # Collect all unique symbols we need prices for
        symbols_needed = set()
        for outcome in pending:
            sym = outcome.get("symbol", "")
            if sym and outcome.get("price_at_impulse", 0) > 0:
                symbols_needed.add(sym)

        # Batch-fetch all prices in ONE API call
        self._refresh_prices_batch(symbols_needed)

        updated = 0
        now = time.time()

        for outcome in pending:
            impulse_ts = outcome.get("impulse_ts", 0)
            impulse_id = outcome.get("impulse_id", 0)
            symbol = outcome.get("symbol", "")
            direction = outcome.get("direction", "long")
            price_at = outcome.get("price_at_impulse", 0)
            timeframe = outcome.get("timeframe", "5")

            if not symbol or price_at <= 0:
                continue

            age_sec = now - impulse_ts
            updates: Dict = {}

            # Get current price from batch cache
            current_price = self._price_cache.get(symbol)
            if current_price is None or current_price <= 0:
                continue

            # Fill time-based checkpoints
            for label, checkpoint_sec in config.POST_TRACKER_CHECKPOINTS:
                col = f"price_after_{label}"
                if outcome.get(col, 0) == 0 and age_sec >= checkpoint_sec:
                    updates[col] = current_price

            # Calculate max favorable / adverse moves
            if price_at > 0:
                if direction == "long":
                    favorable_pct = (current_price / price_at - 1.0) * 100
                    adverse_pct = max(0, (1.0 - current_price / price_at) * 100)
                else:
                    favorable_pct = (1.0 - current_price / price_at) * 100
                    adverse_pct = max(0, (current_price / price_at - 1.0) * 100)

                # Update max favorable if current is better
                if favorable_pct > outcome.get("max_favorable_pct", 0):
                    updates["max_favorable_pct"] = round(favorable_pct, 4)
                if adverse_pct > outcome.get("max_adverse_pct", 0):
                    updates["max_adverse_pct"] = round(adverse_pct, 4)

            # Stop hunt detection: big adverse move early, then reversal
            if (age_sec > 900 and age_sec < 7200  # Between 15min and 2h
                    and not outcome.get("was_stop_hunt", False)):
                max_adv = max(outcome.get("max_adverse_pct", 0),
                              updates.get("max_adverse_pct", 0))
                max_fav = max(outcome.get("max_favorable_pct", 0),
                              updates.get("max_favorable_pct", 0))
                # Stop hunt pattern: went against us significantly, then reversed
                if max_adv > 1.0 and max_fav > max_adv * 0.5:
                    updates["was_stop_hunt"] = 1

            # Reversal calculation
            max_fav = max(outcome.get("max_favorable_pct", 0),
                          updates.get("max_favorable_pct", 0))
            if max_fav > 0 and price_at > 0:
                if direction == "long":
                    peak = price_at * (1 + max_fav / 100)
                    rev = (peak - current_price) / (peak - price_at) * 100 if peak > price_at else 0
                else:
                    trough = price_at * (1 - max_fav / 100)
                    rev = (current_price - trough) / (price_at - trough) * 100 if price_at > trough else 0
                rev = max(0, min(100, rev))
                updates["reversal_pct"] = round(rev, 2)

            # New extremum check (for continuation detection)
            if age_sec > 3600:  # After 1h
                recent_impulses = self.db.get_recent_impulses(
                    symbol=symbol, hours=age_sec / 3600)
                # Count impulses in same direction after this one
                cont_count = sum(
                    1 for ri in recent_impulses
                    if ri.direction == direction
                    and ri.timestamp > impulse_ts
                    and ri.id != impulse_id
                )
                if cont_count != outcome.get("continuation_impulses", 0):
                    updates["continuation_impulses"] = cont_count

                # New extremum: price went beyond impulse candle high/low
                if direction == "long" and current_price > price_at * 1.02:
                    updates["new_extremum"] = 1
                elif direction == "short" and current_price < price_at * 0.98:
                    updates["new_extremum"] = 1

            # Mark as complete after max tracking age
            if age_sec >= config.POST_TRACKER_MAX_AGE_SEC:
                updates["tracking_complete"] = 1
                logger.info(
                    f"✅ Tracking complete: {symbol} imp#{impulse_id} "
                    f"fav={outcome.get('max_favorable_pct', 0):.1f}% "
                    f"adv={outcome.get('max_adverse_pct', 0):.1f}%"
                )

            if updates:
                self.db.update_outcome(impulse_id, updates)
                updated += 1

        return updated

    def _refresh_prices_batch(self, symbols: set):
        """Fetch ALL Bybit linear tickers in one API call and cache them."""
        now = time.time()
        # Skip if cache is still fresh (< 30s old)
        if now - self._cache_ts < 30 and self._price_cache:
            return

        try:
            import requests
            resp = requests.get(
                "https://api.bybit.com/v5/market/tickers",
                params={"category": "linear"},
                timeout=10)
            data = resp.json()
            if data.get("retCode") == 0 and data["result"]["list"]:
                self._price_cache.clear()
                for ticker in data["result"]["list"]:
                    sym = ticker.get("symbol", "")
                    price = float(ticker.get("lastPrice", 0))
                    if sym and price > 0:
                        self._price_cache[sym] = price
                self._cache_ts = now
                logger.debug(
                    f"📡 Batch price refresh: {len(self._price_cache)} tickers loaded"
                )
        except Exception as e:
            logger.warning(f"Batch price fetch failed: {e}")
            # Fall back to individual fetches for missing symbols
            for symbol in symbols:
                if symbol not in self._price_cache:
                    price = self._get_price_single(symbol)
                    if price:
                        self._price_cache[symbol] = price

    def _get_price_single(self, symbol: str) -> Optional[float]:
        """Fallback: fetch a single symbol price."""
        try:
            import requests
            resp = requests.get(
                "https://api.bybit.com/v5/market/tickers",
                params={"category": "linear", "symbol": symbol},
                timeout=5)
            data = resp.json()
            if data.get("retCode") == 0 and data["result"]["list"]:
                return float(data["result"]["list"][0]["lastPrice"])
        except Exception:
            pass
        return None
