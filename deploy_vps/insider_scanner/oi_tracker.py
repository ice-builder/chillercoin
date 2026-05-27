"""OI Tracker — Monitors Open Interest across Bybit, Binance, Bitget, MEXC, Gate.io
Detects abnormal OI surges that precede insider pump events.
"""
import time
import json
import logging
import requests
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from collections import defaultdict

import config

logger = logging.getLogger("insider.oi")


@dataclass
class OISnapshot:
    """Single OI reading for a symbol on an exchange."""
    symbol: str
    exchange: str
    oi_value: float        # OI in contracts or USD
    oi_usd: float          # OI in USD (normalized)
    timestamp: float       # Unix timestamp

@dataclass
class OIChange:
    """Calculated OI change for a symbol across time windows."""
    symbol: str
    exchange: str
    current_oi: float
    change_1h_pct: float = 0.0
    change_4h_pct: float = 0.0
    change_24h_pct: float = 0.0
    z_score_1h: float = 0.0
    price: float = 0.0
    price_change_24h_pct: float = 0.0


class OITracker:
    """Tracks Open Interest changes across multiple exchanges."""

    def __init__(self, history_path: Optional[Path] = None):
        self.history_path = history_path or Path(config.OI_HISTORY_FILE)
        # {exchange: {symbol: [oi_value, oi_value, ...]}} — rolling window
        self.snapshots: Dict[str, Dict[str, List[Tuple[float, float]]]] = {}
        self._load_history()

    def _load_history(self):
        """Load OI history from disk."""
        if self.history_path.exists():
            try:
                data = json.loads(self.history_path.read_text())
                self.snapshots = data.get("snapshots", {})
                logger.info(f"Loaded OI history: {sum(len(syms) for syms in self.snapshots.values())} symbols")
            except Exception as e:
                logger.warning(f"Failed to load OI history: {e}")
                self.snapshots = {}

    def _save_history(self):
        """Persist OI history to disk (trimmed to retention window)."""
        # Trim old snapshots
        cutoff = time.time() - (config.OI_SNAPSHOT_RETENTION * config.SCAN_INTERVAL_SEC)
        for exchange in self.snapshots:
            for symbol in list(self.snapshots[exchange].keys()):
                entries = self.snapshots[exchange][symbol]
                self.snapshots[exchange][symbol] = [
                    (ts, val) for ts, val in entries if ts > cutoff
                ]
                if not self.snapshots[exchange][symbol]:
                    del self.snapshots[exchange][symbol]

        self.history_path.write_text(
            json.dumps({"snapshots": self.snapshots, "last_updated": time.time()},
                       default=str),
            encoding="utf-8"
        )

    # ─── Exchange-specific fetchers ────────────────────────

    def fetch_bybit_oi(self) -> Dict[str, OISnapshot]:
        """Fetch OI for all Bybit USDT perps via tickers endpoint (includes openInterest)."""
        results = {}
        try:
            resp = requests.get(
                config.EXCHANGES["bybit"]["futures_tickers"],
                timeout=10
            )
            data = resp.json()
            if data.get("retCode") != 0:
                logger.warning(f"Bybit tickers error: {data.get('retMsg')}")
                return results

            now = time.time()
            for t in data["result"]["list"]:
                symbol = t["symbol"]
                if not symbol.endswith("USDT"):
                    continue
                oi_value = float(t.get("openInterest", 0) or 0)
                price = float(t.get("lastPrice", 0) or 0)
                oi_usd = oi_value * price  # OI in contracts × price = USD
                if oi_usd < 100_000:  # Skip tiny OI
                    continue
                results[symbol] = OISnapshot(
                    symbol=symbol, exchange="bybit",
                    oi_value=oi_value, oi_usd=oi_usd, timestamp=now
                )
            logger.info(f"Bybit: fetched OI for {len(results)} symbols")
        except Exception as e:
            logger.error(f"Bybit OI fetch failed: {e}")
        return results

    def fetch_binance_oi(self) -> Dict[str, OISnapshot]:
        """Fetch OI for all Binance USDT-M futures via 24hr tickers."""
        results = {}
        try:
            # Get all tickers (includes volume/price)
            resp = requests.get(
                config.EXCHANGES["binance"]["futures_tickers"],
                timeout=10
            )
            tickers = {t["symbol"]: t for t in resp.json() if t["symbol"].endswith("USDT")}

            # Fetch OI for top symbols (Binance requires per-symbol OI calls,
            # but we can batch by using the futures data endpoint)
            resp2 = requests.get(
                "https://fapi.binance.com/fapi/v1/openInterest",
                params={"symbol": "BTCUSDT"},
                timeout=10
            )  # Test connectivity

            # Alternative: iterate top 100 by volume
            sorted_tickers = sorted(
                tickers.values(),
                key=lambda x: float(x.get("quoteVolume", 0)),
                reverse=True
            )[:100]

            now = time.time()
            for t in sorted_tickers:
                sym = t["symbol"]
                try:
                    oi_resp = requests.get(
                        config.EXCHANGES["binance"]["oi_endpoint"],
                        params={"symbol": sym},
                        timeout=5
                    )
                    oi_data = oi_resp.json()
                    oi_value = float(oi_data.get("openInterest", 0))
                    price = float(t.get("lastPrice", 0))
                    oi_usd = oi_value * price
                    if oi_usd < 100_000:
                        continue
                    results[sym] = OISnapshot(
                        symbol=sym, exchange="binance",
                        oi_value=oi_value, oi_usd=oi_usd, timestamp=now
                    )
                except Exception:
                    continue
                # Rate limiting: ~10 req/s for Binance
                time.sleep(0.1)
            logger.info(f"Binance: fetched OI for {len(results)} symbols")
        except Exception as e:
            logger.error(f"Binance OI fetch failed: {e}")
        return results

    def fetch_bitget_oi(self) -> Dict[str, OISnapshot]:
        """Fetch OI for all Bitget USDT-FUTURES via tickers (includes holdingAmount)."""
        results = {}
        try:
            # Use tickers endpoint — includes holdingAmount (OI) + price in one call
            resp = requests.get(
                config.EXCHANGES["bitget"]["futures_tickers"],
                timeout=10
            )
            data = resp.json()
            items = data.get("data", [])
            if items is None:
                items = []
            now = time.time()
            for t in items:
                symbol = t.get("symbol", "")
                if not symbol.endswith("USDT"):
                    continue
                # Bitget: holdingAmount = OI in base currency, lastPr = last price
                oi_value = float(t.get("holdingAmount", 0) or 0)
                price = float(t.get("lastPr", 0) or t.get("last", 0) or 0)
                oi_usd = oi_value * price
                if oi_usd < 100_000:
                    continue
                results[symbol] = OISnapshot(
                    symbol=symbol, exchange="bitget",
                    oi_value=oi_value, oi_usd=oi_usd, timestamp=now
                )
            logger.info(f"Bitget: fetched OI for {len(results)} symbols")
        except Exception as e:
            logger.error(f"Bitget OI fetch failed: {e}")
        return results

    def fetch_mexc_oi(self) -> Dict[str, OISnapshot]:
        """Fetch OI from MEXC futures."""
        results = {}
        try:
            resp = requests.get(
                config.EXCHANGES["mexc"]["futures_tickers"],
                timeout=10
            )
            data = resp.json()
            items = data.get("data", [])
            now = time.time()
            for t in items:
                symbol = t.get("symbol", "")
                # MEXC uses e.g. "LAB_USDT" format — normalize
                normalized = symbol.replace("_", "")
                if not normalized.endswith("USDT"):
                    continue
                oi_value = float(t.get("holdVol", 0) or 0)
                price = float(t.get("lastPrice", 0) or 0)
                oi_usd = oi_value * price
                if oi_usd < 50_000:
                    continue
                results[normalized] = OISnapshot(
                    symbol=normalized, exchange="mexc",
                    oi_value=oi_value, oi_usd=oi_usd, timestamp=now
                )
            logger.info(f"MEXC: fetched OI for {len(results)} symbols")
        except Exception as e:
            logger.error(f"MEXC OI fetch failed: {e}")
        return results

    def fetch_gateio_oi(self) -> Dict[str, OISnapshot]:
        """Fetch OI from Gate.io futures."""
        results = {}
        try:
            resp = requests.get(
                config.EXCHANGES["gateio"]["futures_tickers"],
                timeout=10
            )
            items = resp.json()
            now = time.time()
            for t in items:
                contract = t.get("contract", "")
                # Gate uses "LAB_USDT" format
                normalized = contract.replace("_", "")
                if not normalized.endswith("USDT"):
                    continue
                # Gate provides total_size (contracts) and last price
                oi_value = float(t.get("total_size", 0) or 0)
                price = float(t.get("last", 0) or 0)
                quanto_mult = float(t.get("quanto_multiplier", 1) or 1)
                oi_usd = oi_value * price * quanto_mult
                if oi_usd < 50_000:
                    continue
                results[normalized] = OISnapshot(
                    symbol=normalized, exchange="gateio",
                    oi_value=oi_value, oi_usd=oi_usd, timestamp=now
                )
            logger.info(f"Gate.io: fetched OI for {len(results)} symbols")
        except Exception as e:
            logger.error(f"Gate.io OI fetch failed: {e}")
        return results

    # ─── Core Logic ────────────────────────────────────

    def fetch_all_oi(self) -> Dict[str, Dict[str, OISnapshot]]:
        """Fetch OI from all enabled exchanges. Returns {exchange: {symbol: snapshot}}."""
        all_oi = {}
        fetchers = {
            "bybit": self.fetch_bybit_oi,
            "binance": self.fetch_binance_oi,
            "bitget": self.fetch_bitget_oi,
            "mexc": self.fetch_mexc_oi,
            "gateio": self.fetch_gateio_oi,
        }
        for exchange, fetcher in fetchers.items():
            if config.EXCHANGES.get(exchange, {}).get("enabled", False):
                try:
                    all_oi[exchange] = fetcher()
                except Exception as e:
                    logger.error(f"Failed to fetch OI from {exchange}: {e}")
                    all_oi[exchange] = {}
        return all_oi

    def record_snapshots(self, all_oi: Dict[str, Dict[str, OISnapshot]]):
        """Store current OI values in rolling history."""
        now = time.time()
        for exchange, symbols in all_oi.items():
            if exchange not in self.snapshots:
                self.snapshots[exchange] = {}
            for symbol, snapshot in symbols.items():
                if symbol not in self.snapshots[exchange]:
                    self.snapshots[exchange][symbol] = []
                self.snapshots[exchange][symbol].append((now, snapshot.oi_usd))
        self._save_history()

    def calculate_changes(self, all_oi: Dict[str, Dict[str, OISnapshot]]) -> List[OIChange]:
        """Calculate OI changes for all symbols across all exchanges.
        Returns list sorted by 1h change magnitude."""
        changes = []
        now = time.time()
        t_1h = now - 3600
        t_4h = now - 14400
        t_24h = now - 86400

        for exchange, symbols in all_oi.items():
            history = self.snapshots.get(exchange, {})
            for symbol, snapshot in symbols.items():
                hist = history.get(symbol, [])
                if not hist:
                    continue

                current_oi = snapshot.oi_usd

                # Find closest historical OI values
                oi_1h = self._find_closest_oi(hist, t_1h)
                oi_4h = self._find_closest_oi(hist, t_4h)
                oi_24h = self._find_closest_oi(hist, t_24h)

                change_1h = ((current_oi / oi_1h) - 1) * 100 if oi_1h and oi_1h > 0 else 0
                change_4h = ((current_oi / oi_4h) - 1) * 100 if oi_4h and oi_4h > 0 else 0
                change_24h = ((current_oi / oi_24h) - 1) * 100 if oi_24h and oi_24h > 0 else 0

                # Z-score: how unusual is this 1h change?
                z = self._compute_z_score(hist, change_1h)

                changes.append(OIChange(
                    symbol=symbol,
                    exchange=exchange,
                    current_oi=current_oi,
                    change_1h_pct=round(change_1h, 2),
                    change_4h_pct=round(change_4h, 2),
                    change_24h_pct=round(change_24h, 2),
                    z_score_1h=round(z, 2),
                ))

        # Sort by absolute 1h change
        changes.sort(key=lambda c: abs(c.change_1h_pct), reverse=True)
        return changes

    def detect_anomalies(self, changes: List[OIChange]) -> List[OIChange]:
        """Filter to only anomalous OI changes."""
        anomalies = []
        for c in changes:
            if (abs(c.change_1h_pct) >= config.OI_CHANGE_1H_MIN
                    or abs(c.z_score_1h) >= config.OI_Z_SCORE_MIN):
                anomalies.append(c)
        return anomalies

    def get_top_movers(self, changes: List[OIChange], top_n: int = None) -> List[OIChange]:
        """Return top N OI movers (by 1h change)."""
        n = top_n or config.OI_TOP_N
        return changes[:n]

    # ─── Helpers ───────────────────────────────────────

    @staticmethod
    def _find_closest_oi(hist: List[Tuple[float, float]], target_ts: float) -> Optional[float]:
        """Find OI value closest to target timestamp."""
        if not hist:
            return None
        # Filter to entries before or at target
        candidates = [(ts, val) for ts, val in hist if ts <= target_ts + 300]
        if not candidates:
            return hist[0][1] if hist else None  # Oldest available
        # Return closest to target
        candidates.sort(key=lambda x: abs(x[0] - target_ts))
        return candidates[0][1]

    @staticmethod
    def _compute_z_score(hist: List[Tuple[float, float]], current_change: float) -> float:
        """Compute z-score of current OI change relative to historical changes."""
        if len(hist) < 12:  # Need at least 1h of 5-min data
            return 0.0

        # Calculate rolling 1h changes from history
        changes = []
        for i in range(12, len(hist)):
            old_val = hist[i - 12][1]  # 12 × 5min = 1h ago
            new_val = hist[i][1]
            if old_val > 0:
                ch = ((new_val / old_val) - 1) * 100
                changes.append(ch)

        if not changes:
            return 0.0

        mean = sum(changes) / len(changes)
        variance = sum((c - mean) ** 2 for c in changes) / len(changes)
        std = variance ** 0.5

        if std < 0.001:
            return 0.0
        return (current_change - mean) / std

    # ─── Weekly OI Trend Tracking ─────────────────────

    def update_weekly_trends(self, all_oi: Dict[str, Dict[str, OISnapshot]]):
        """Record daily OI snapshots for multi-day trend detection.
        Tracks if a token's OI has been growing consistently over days
        (accumulation phase before pump, like LAB case).
        """
        import json as _json
        trend_path = Path(config.WEEKLY_TREND_FILE)

        # Load existing trend data
        trends = {}
        if trend_path.exists():
            try:
                trends = _json.loads(trend_path.read_text(encoding="utf-8"))
            except Exception:
                trends = {}

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Record today's OI for each symbol (aggregate across exchanges)
        symbol_oi_today: Dict[str, float] = {}
        for exchange, symbols in all_oi.items():
            for symbol, snap in symbols.items():
                symbol_oi_today[symbol] = symbol_oi_today.get(symbol, 0) + snap.oi_usd

        for symbol, total_oi in symbol_oi_today.items():
            if symbol not in trends:
                trends[symbol] = {}
            trends[symbol][today] = round(total_oi, 2)

            # Trim: keep only last 14 days
            dates = sorted(trends[symbol].keys())
            if len(dates) > 14:
                for old_date in dates[:-14]:
                    del trends[symbol][old_date]

        # Remove symbols with no recent data (> 3 days old)
        stale = []
        for sym in trends:
            dates = sorted(trends[sym].keys())
            if not dates or dates[-1] < (datetime.now(timezone.utc).strftime("%Y-%m-%d")):
                pass  # OK, might update later today
            if len(dates) == 0:
                stale.append(sym)
        for sym in stale:
            del trends[sym]

        trend_path.write_text(
            _json.dumps(trends, default=str), encoding="utf-8"
        )

    def get_weekly_trending(self) -> Dict[str, int]:
        """Get symbols with consecutive daily OI growth.
        Returns {symbol: consecutive_growth_days}.
        """
        import json as _json
        trend_path = Path(config.WEEKLY_TREND_FILE)
        if not trend_path.exists():
            return {}

        try:
            trends = _json.loads(trend_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        result = {}
        for symbol, daily_data in trends.items():
            dates = sorted(daily_data.keys())
            if len(dates) < 2:
                continue

            # Count consecutive days of OI growth (from most recent)
            growth_days = 0
            for i in range(len(dates) - 1, 0, -1):
                cur = daily_data[dates[i]]
                prev = daily_data[dates[i - 1]]
                if prev > 0 and cur > prev * 1.02:  # >2% daily growth
                    growth_days += 1
                else:
                    break

            if growth_days >= config.WEEKLY_TREND_MIN_DAYS:
                result[symbol] = growth_days

        return result

