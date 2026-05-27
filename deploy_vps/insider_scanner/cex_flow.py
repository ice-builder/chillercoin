"""CEX Flow Tracker — Monitors spot buying pressure across exchanges.
Detects abnormal buy-side flow that precedes pump events.
"""
import time
import json
import logging
import requests
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict

import config

logger = logging.getLogger("insider.flow")


@dataclass
class SpotFlowSignal:
    """Detected spot buying pressure on an exchange."""
    symbol: str
    exchange: str
    turnover_24h: float       # Total 24h turnover in USD
    volume_change_pct: float  # Volume vs baseline
    buy_ratio: float          # Estimated buy/sell ratio
    z_score: float            # Volume z-score
    price: float
    price_change_24h: float


class CEXFlowTracker:
    """Tracks unusual spot buying patterns across exchanges."""

    def __init__(self):
        # Rolling baseline: {exchange: {symbol: [turnover_values]}}
        self.baselines: Dict[str, Dict[str, List[float]]] = {}

    def fetch_spot_tickers(self) -> Dict[str, Dict[str, dict]]:
        """Fetch spot tickers from all enabled exchanges.
        Returns {exchange: {symbol: ticker_data}}."""
        all_tickers = {}

        # Bybit Spot
        if config.EXCHANGES.get("bybit", {}).get("enabled"):
            all_tickers["bybit"] = self._fetch_bybit_spot()

        # Binance Spot
        if config.EXCHANGES.get("binance", {}).get("enabled"):
            all_tickers["binance"] = self._fetch_binance_spot()

        # Bitget Spot
        if config.EXCHANGES.get("bitget", {}).get("enabled"):
            all_tickers["bitget"] = self._fetch_bitget_spot()

        # MEXC Spot
        if config.EXCHANGES.get("mexc", {}).get("enabled"):
            all_tickers["mexc"] = self._fetch_mexc_spot()

        # Gate.io Spot
        if config.EXCHANGES.get("gateio", {}).get("enabled"):
            all_tickers["gateio"] = self._fetch_gateio_spot()

        return all_tickers

    # ─── Exchange Fetchers ─────────────────────────

    def _fetch_bybit_spot(self) -> Dict[str, dict]:
        results = {}
        try:
            resp = requests.get(
                config.EXCHANGES["bybit"]["spot_tickers"],
                timeout=10
            )
            data = resp.json()
            for t in data.get("result", {}).get("list", []):
                sym = t.get("symbol", "")
                if not sym.endswith("USDT"):
                    continue
                turnover = float(t.get("turnover24h", 0) or 0)
                if turnover < config.MIN_SPOT_TURNOVER_24H:
                    continue
                results[sym] = {
                    "turnover_24h": turnover,
                    "volume_24h": float(t.get("volume24h", 0) or 0),
                    "price": float(t.get("lastPrice", 0) or 0),
                    "price_change_24h": float(t.get("price24hPcnt", 0) or 0) * 100,
                    "high_24h": float(t.get("highPrice24h", 0) or 0),
                    "low_24h": float(t.get("lowPrice24h", 0) or 0),
                }
            logger.info(f"Bybit spot: {len(results)} symbols")
        except Exception as e:
            logger.error(f"Bybit spot fetch failed: {e}")
        return results

    def _fetch_binance_spot(self) -> Dict[str, dict]:
        results = {}
        try:
            resp = requests.get(
                config.EXCHANGES["binance"]["spot_tickers"],
                timeout=10
            )
            for t in resp.json():
                sym = t.get("symbol", "")
                if not sym.endswith("USDT"):
                    continue
                turnover = float(t.get("quoteVolume", 0) or 0)
                if turnover < config.MIN_SPOT_TURNOVER_24H:
                    continue
                price = float(t.get("lastPrice", 0) or 0)
                open_price = float(t.get("openPrice", 0) or 0)
                pct_change = ((price / open_price) - 1) * 100 if open_price > 0 else 0
                results[sym] = {
                    "turnover_24h": turnover,
                    "volume_24h": float(t.get("volume", 0) or 0),
                    "price": price,
                    "price_change_24h": round(pct_change, 2),
                    "high_24h": float(t.get("highPrice", 0) or 0),
                    "low_24h": float(t.get("lowPrice", 0) or 0),
                    # Binance gives taker buy volume!
                    "taker_buy_volume": float(t.get("takerBuyBaseAssetVolume", 0) or 0),
                    "taker_buy_quote": float(t.get("takerBuyQuoteAssetVolume", 0) or 0),
                }
            logger.info(f"Binance spot: {len(results)} symbols")
        except Exception as e:
            logger.error(f"Binance spot fetch failed: {e}")
        return results

    def _fetch_bitget_spot(self) -> Dict[str, dict]:
        results = {}
        try:
            resp = requests.get(
                config.EXCHANGES["bitget"]["spot_tickers"],
                timeout=10
            )
            data = resp.json()
            for t in data.get("data", []):
                sym = t.get("symbol", "")
                if not sym.endswith("USDT"):
                    continue
                turnover = float(t.get("quoteVolume", 0) or t.get("usdtVolume", 0) or 0)
                if turnover < config.MIN_SPOT_TURNOVER_24H:
                    continue
                price = float(t.get("lastPr", 0) or t.get("close", 0) or 0)
                open_price = float(t.get("open", 0) or 0)
                pct = ((price / open_price) - 1) * 100 if open_price > 0 else 0
                results[sym] = {
                    "turnover_24h": turnover,
                    "volume_24h": float(t.get("baseVolume", 0) or 0),
                    "price": price,
                    "price_change_24h": round(pct, 2),
                    "high_24h": float(t.get("high24h", 0) or 0),
                    "low_24h": float(t.get("low24h", 0) or 0),
                }
            logger.info(f"Bitget spot: {len(results)} symbols")
        except Exception as e:
            logger.error(f"Bitget spot fetch failed: {e}")
        return results

    def _fetch_mexc_spot(self) -> Dict[str, dict]:
        results = {}
        try:
            resp = requests.get(
                config.EXCHANGES["mexc"]["spot_tickers"],
                timeout=10
            )
            for t in resp.json():
                sym = t.get("symbol", "")
                if not sym.endswith("USDT"):
                    continue
                turnover = float(t.get("quoteVolume", 0) or 0)
                if turnover < config.MIN_SPOT_TURNOVER_24H:
                    continue
                price = float(t.get("lastPrice", 0) or 0)
                open_price = float(t.get("openPrice", 0) or 0)
                pct = ((price / open_price) - 1) * 100 if open_price > 0 else 0
                results[sym] = {
                    "turnover_24h": turnover,
                    "volume_24h": float(t.get("volume", 0) or 0),
                    "price": price,
                    "price_change_24h": round(pct, 2),
                    "high_24h": float(t.get("highPrice", 0) or 0),
                    "low_24h": float(t.get("lowPrice", 0) or 0),
                }
            logger.info(f"MEXC spot: {len(results)} symbols")
        except Exception as e:
            logger.error(f"MEXC spot fetch failed: {e}")
        return results

    def _fetch_gateio_spot(self) -> Dict[str, dict]:
        results = {}
        try:
            resp = requests.get(
                config.EXCHANGES["gateio"]["spot_tickers"],
                timeout=10
            )
            for t in resp.json():
                pair = t.get("currency_pair", "")
                sym = pair.replace("_", "")
                if not sym.endswith("USDT"):
                    continue
                turnover = float(t.get("quote_volume", 0) or 0)
                if turnover < config.MIN_SPOT_TURNOVER_24H:
                    continue
                price = float(t.get("last", 0) or 0)
                pct = float(t.get("change_percentage", 0) or 0)
                results[sym] = {
                    "turnover_24h": turnover,
                    "volume_24h": float(t.get("base_volume", 0) or 0),
                    "price": price,
                    "price_change_24h": round(float(pct), 2),
                    "high_24h": float(t.get("high_24h", 0) or 0),
                    "low_24h": float(t.get("low_24h", 0) or 0),
                }
            logger.info(f"Gate.io spot: {len(results)} symbols")
        except Exception as e:
            logger.error(f"Gate.io spot fetch failed: {e}")
        return results

    # ─── Analysis ─────────────────────────────────

    def detect_buying_pressure(
        self,
        spot_tickers: Dict[str, Dict[str, dict]]
    ) -> List[SpotFlowSignal]:
        """Detect abnormal buying pressure across all exchanges.
        Returns list of SpotFlowSignal sorted by z-score."""

        # Aggregate volume by normalized symbol across exchanges
        symbol_data: Dict[str, List[dict]] = defaultdict(list)
        for exchange, tickers in spot_tickers.items():
            for symbol, data in tickers.items():
                symbol_data[symbol].append({
                    "exchange": exchange,
                    **data
                })

        signals = []
        for symbol, exchanges in symbol_data.items():
            for ex_data in exchanges:
                exchange = ex_data["exchange"]
                turnover = ex_data["turnover_24h"]

                # Estimate buy ratio from Binance taker data, else assume neutral
                buy_ratio = 1.0
                if "taker_buy_quote" in ex_data and turnover > 0:
                    buy_ratio = ex_data["taker_buy_quote"] / turnover * 2
                    # taker_buy is typically ~50% of total; ratio > 1.0 = more buying

                # Update baseline
                if exchange not in self.baselines:
                    self.baselines[exchange] = {}
                if symbol not in self.baselines[exchange]:
                    self.baselines[exchange][symbol] = []
                self.baselines[exchange][symbol].append(turnover)
                # Keep last 50 readings
                self.baselines[exchange][symbol] = self.baselines[exchange][symbol][-50:]

                # Z-score of current volume
                baseline = self.baselines[exchange][symbol]
                z = 0.0
                if len(baseline) >= 5:
                    mean = sum(baseline) / len(baseline)
                    std = (sum((v - mean) ** 2 for v in baseline) / len(baseline)) ** 0.5
                    if std > 0:
                        z = (turnover - mean) / std

                if z >= config.SPOT_VOLUME_Z_MIN or buy_ratio >= config.SPOT_BUY_RATIO_MIN:
                    signals.append(SpotFlowSignal(
                        symbol=symbol,
                        exchange=exchange,
                        turnover_24h=turnover,
                        volume_change_pct=round(z * 100, 1),  # Approximate
                        buy_ratio=round(buy_ratio, 2),
                        z_score=round(z, 2),
                        price=ex_data.get("price", 0),
                        price_change_24h=ex_data.get("price_change_24h", 0),
                    ))

        signals.sort(key=lambda s: s.z_score, reverse=True)
        return signals
