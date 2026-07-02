"""
$CHILLER — Exchange Adapter Layer
Unified interface for trading bots. Swap between Drift/Bybit with one line.

Usage:
    from exchange_adapter import DriftAdapter, BybitAdapter

    # Phase 1: Drift (on-chain, no CEX)
    exchange = DriftAdapter(keypair_path="~/.config/solana/id.json")

    # Phase 2: Bybit fallback
    # exchange = BybitAdapter(api_key="...", api_secret="...")

    await exchange.connect()
    await exchange.open_long("SOL-PERP", size=10.0, leverage=5)
    pos = await exchange.get_position("SOL-PERP")
    await exchange.close_position("SOL-PERP")
    balance = await exchange.get_balance()
"""

import asyncio
import json
import os
import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

log = logging.getLogger("EXCHANGE")
log.setLevel(logging.INFO)
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(h)

# ═══════════════════════════════════════════════
# Common Types
# ═══════════════════════════════════════════════

class Side(Enum):
    LONG = "LONG"
    SHORT = "SHORT"

@dataclass
class Position:
    pair: str
    side: Side
    size: float          # in base asset (e.g., SOL)
    entry_price: float   # USD
    unrealized_pnl: float  # USD
    leverage: float
    liquidation_price: float = 0.0
    timestamp: float = 0.0

    def to_dict(self):
        d = asdict(self)
        d["side"] = self.side.value
        return d

@dataclass
class Order:
    pair: str
    side: Side
    size: float
    price: float
    order_type: str = "MARKET"  # MARKET, LIMIT
    order_id: str = ""
    status: str = "NEW"

@dataclass
class Balance:
    total_usd: float
    available_usd: float
    unrealized_pnl: float
    positions_count: int
    assets: dict = field(default_factory=dict)  # {"SOL": 10.5, "USDC": 100.0}

@dataclass
class TradeResult:
    pair: str
    side: Side
    entry_price: float
    exit_price: float
    pnl_usd: float
    pnl_pct: float
    duration_secs: int
    tx_signature: str = ""

# ═══════════════════════════════════════════════
# Abstract Base Adapter
# ═══════════════════════════════════════════════

class ExchangeAdapter(ABC):
    """Unified exchange interface. All bots code against this."""

    name: str = "base"

    @abstractmethod
    async def connect(self):
        """Initialize connection and subscribe to data."""

    @abstractmethod
    async def disconnect(self):
        """Clean shutdown."""

    @abstractmethod
    async def open_long(self, pair: str, size: float, leverage: int = 1, price: float = 0) -> str:
        """Open long position. Returns order/tx ID."""

    @abstractmethod
    async def open_short(self, pair: str, size: float, leverage: int = 1, price: float = 0) -> str:
        """Open short position. Returns order/tx ID."""

    @abstractmethod
    async def close_position(self, pair: str) -> Optional[TradeResult]:
        """Close entire position for pair. Returns trade result."""

    @abstractmethod
    async def get_position(self, pair: str) -> Optional[Position]:
        """Get current position for pair."""

    @abstractmethod
    async def get_all_positions(self) -> list[Position]:
        """Get all open positions."""

    @abstractmethod
    async def get_balance(self) -> Balance:
        """Get account balance."""

    @abstractmethod
    async def get_price(self, pair: str) -> float:
        """Get current price for pair."""

    @abstractmethod
    async def cancel_all_orders(self, pair: str = "") -> int:
        """Cancel all open orders. Returns count cancelled."""

# ═══════════════════════════════════════════════
# Drift Protocol Adapter
# ═══════════════════════════════════════════════

# Market index mapping for Drift
DRIFT_MARKETS = {
    "SOL-PERP": 0,
    "BTC-PERP": 1,
    "ETH-PERP": 2,
    # Add more as needed
    "DOGE-PERP": 3,
    "1KPEPE-PERP": 4,
    "APT-PERP": 5,
    "ARB-PERP": 6,
    "AVAX-PERP": 7,
    "BNB-PERP": 8,
    "BONK-PERP": 9,
    "FTM-PERP": 10,
    "JUP-PERP": 11,
    "LINK-PERP": 12,
    "MATIC-PERP": 13,
    "OP-PERP": 14,
    "RNDR-PERP": 15,
    "SUI-PERP": 16,
    "WIF-PERP": 17,
    "JTO-PERP": 18,
    "PYTH-PERP": 19,
}

# Reverse mapping: Bybit-style pairs → Drift pairs
PAIR_ALIASES = {
    "SOLUSDT": "SOL-PERP",   "BTCUSDT": "BTC-PERP",   "ETHUSDT": "ETH-PERP",
    "DOGEUSDT": "DOGE-PERP", "AVAXUSDT": "AVAX-PERP",  "LINKUSDT": "LINK-PERP",
    "ARBUSDT": "ARB-PERP",   "SUIUSDT": "SUI-PERP",    "APTUSDT": "APT-PERP",
    "BNBUSDT": "BNB-PERP",   "OPUSDT": "OP-PERP",      "JUPUSDT": "JUP-PERP",
    "WIFUSDT": "WIF-PERP",   "JTOUSDT": "JTO-PERP",    "PYTHUSDT": "PYTH-PERP",
    "MATICUSDT": "MATIC-PERP", "RNDRUSDT": "RNDR-PERP", "BONKUSDT": "BONK-PERP",
}


class DriftAdapter(ExchangeAdapter):
    """
    Drift Protocol adapter using driftpy SDK.
    All trading happens on-chain on Solana.
    """

    name = "drift"

    def __init__(self, keypair_path: str = "~/.config/solana/id.json",
                 rpc_url: str = "https://api.mainnet-beta.solana.com",
                 env: str = "mainnet"):
        self.keypair_path = os.path.expanduser(keypair_path)
        self.rpc_url = rpc_url
        self.env = env
        self.client = None
        self._connected = False
        self._position_cache: dict[str, Position] = {}
        self._open_times: dict[str, float] = {}

    def _normalize_pair(self, pair: str) -> str:
        """Convert Bybit-style pair to Drift format."""
        return PAIR_ALIASES.get(pair, pair)

    def _market_index(self, pair: str) -> int:
        drift_pair = self._normalize_pair(pair)
        if drift_pair not in DRIFT_MARKETS:
            raise ValueError(f"Unknown Drift market: {drift_pair} (from {pair})")
        return DRIFT_MARKETS[drift_pair]

    async def connect(self):
        try:
            from solana.rpc.async_api import AsyncClient
            from anchorpy import Wallet
            from solders.keypair import Keypair as SoldersKeypair
            from driftpy.drift_client import DriftClient
            from driftpy.accounts import get_perp_market_account

            # Load keypair
            with open(self.keypair_path) as f:
                secret = json.load(f)
            kp = SoldersKeypair.from_bytes(bytes(secret))
            wallet = Wallet(kp)

            # Connect
            connection = AsyncClient(self.rpc_url)
            self.client = DriftClient(connection, wallet, self.env)
            await self.client.subscribe()

            self._connected = True
            log.info(f"✅ Connected to Drift ({self.env}) | Wallet: {kp.pubkey()}")

        except ImportError:
            log.error("❌ driftpy not installed. Run: pip install driftpy")
            raise
        except Exception as e:
            log.error(f"❌ Drift connection failed: {e}")
            raise

    async def disconnect(self):
        if self.client:
            await self.client.unsubscribe()
            self._connected = False
            log.info("Disconnected from Drift")

    async def open_long(self, pair: str, size: float, leverage: int = 1, price: float = 0) -> str:
        from driftpy.constants.numeric_constants import BASE_PRECISION, PRICE_PRECISION
        from driftpy.types import PositionDirection, OrderType, OrderParams, MarketType

        market_index = self._market_index(pair)
        base_amount = int(size * BASE_PRECISION)

        if price > 0:
            # Limit order
            order_params = OrderParams(
                order_type=OrderType.Limit(),
                market_type=MarketType.Perp(),
                direction=PositionDirection.Long(),
                market_index=market_index,
                base_asset_amount=base_amount,
                price=int(price * PRICE_PRECISION),
            )
            sig = await self.client.place_perp_order(order_params)
        else:
            # Market order
            sig = await self.client.open_position(
                PositionDirection.Long(),
                base_amount,
                market_index,
            )

        self._open_times[pair] = time.time()
        log.info(f"🟢 LONG {pair} size={size} lev={leverage} | TX: {sig}")
        return str(sig)

    async def open_short(self, pair: str, size: float, leverage: int = 1, price: float = 0) -> str:
        from driftpy.constants.numeric_constants import BASE_PRECISION, PRICE_PRECISION
        from driftpy.types import PositionDirection, OrderType, OrderParams, MarketType

        market_index = self._market_index(pair)
        base_amount = int(size * BASE_PRECISION)

        if price > 0:
            order_params = OrderParams(
                order_type=OrderType.Limit(),
                market_type=MarketType.Perp(),
                direction=PositionDirection.Short(),
                market_index=market_index,
                base_asset_amount=base_amount,
                price=int(price * PRICE_PRECISION),
            )
            sig = await self.client.place_perp_order(order_params)
        else:
            sig = await self.client.open_position(
                PositionDirection.Short(),
                base_amount,
                market_index,
            )

        self._open_times[pair] = time.time()
        log.info(f"🔴 SHORT {pair} size={size} lev={leverage} | TX: {sig}")
        return str(sig)

    async def close_position(self, pair: str) -> Optional[TradeResult]:
        from driftpy.constants.numeric_constants import BASE_PRECISION

        market_index = self._market_index(pair)
        pos = await self.get_position(pair)
        if not pos:
            log.warning(f"No position to close for {pair}")
            return None

        sig = await self.client.close_position(market_index)

        exit_price = await self.get_price(pair)
        duration = int(time.time() - self._open_times.get(pair, time.time()))

        if pos.side == Side.LONG:
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
        else:
            pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100

        pnl_usd = pos.size * abs(exit_price - pos.entry_price)
        if pnl_pct < 0:
            pnl_usd = -pnl_usd

        result = TradeResult(
            pair=pair, side=pos.side,
            entry_price=pos.entry_price, exit_price=exit_price,
            pnl_usd=pnl_usd, pnl_pct=pnl_pct,
            duration_secs=duration, tx_signature=str(sig),
        )

        icon = "🟢" if pnl_pct >= 0 else "🔴"
        log.info(f"{icon} CLOSE {pair} {pos.side.value} {pnl_pct:+.2f}% (${pnl_usd:+.2f}) | TX: {sig}")
        self._open_times.pop(pair, None)
        return result

    async def get_position(self, pair: str) -> Optional[Position]:
        from driftpy.constants.numeric_constants import BASE_PRECISION, PRICE_PRECISION

        market_index = self._market_index(pair)
        try:
            user = self.client.get_user()
            perp_pos = user.get_perp_position(market_index)
            if perp_pos is None or perp_pos.base_asset_amount == 0:
                return None

            base = perp_pos.base_asset_amount / BASE_PRECISION
            side = Side.LONG if base > 0 else Side.SHORT
            entry = perp_pos.quote_entry_amount / (abs(perp_pos.base_asset_amount) / BASE_PRECISION) if perp_pos.base_asset_amount != 0 else 0
            current_price = await self.get_price(pair)
            upnl = perp_pos.unrealized_pnl / PRICE_PRECISION if hasattr(perp_pos, 'unrealized_pnl') else 0

            return Position(
                pair=pair, side=side, size=abs(base),
                entry_price=abs(entry) / PRICE_PRECISION,
                unrealized_pnl=upnl,
                leverage=perp_pos.open_orders if hasattr(perp_pos, 'open_orders') else 1,
                timestamp=self._open_times.get(pair, 0),
            )
        except Exception as e:
            log.debug(f"No position for {pair}: {e}")
            return None

    async def get_all_positions(self) -> list[Position]:
        positions = []
        for pair in DRIFT_MARKETS:
            pos = await self.get_position(pair)
            if pos:
                positions.append(pos)
        return positions

    async def get_balance(self) -> Balance:
        from driftpy.constants.numeric_constants import QUOTE_PRECISION

        user = self.client.get_user()
        total_collateral = user.get_total_collateral() / QUOTE_PRECISION
        free_collateral = user.get_free_collateral() / QUOTE_PRECISION
        upnl = user.get_unrealized_pnl(True) / QUOTE_PRECISION
        positions = await self.get_all_positions()

        return Balance(
            total_usd=total_collateral,
            available_usd=free_collateral,
            unrealized_pnl=upnl,
            positions_count=len(positions),
        )

    async def get_price(self, pair: str) -> float:
        from driftpy.constants.numeric_constants import PRICE_PRECISION

        market_index = self._market_index(pair)
        oracle = self.client.get_oracle_price_data_for_perp_market(market_index)
        return oracle.price / PRICE_PRECISION

    async def cancel_all_orders(self, pair: str = "") -> int:
        if pair:
            market_index = self._market_index(pair)
            sig = await self.client.cancel_orders(market_index=market_index)
        else:
            sig = await self.client.cancel_orders()
        log.info(f"Cancelled orders | TX: {sig}")
        return 1  # Drift cancels all in one TX


# ═══════════════════════════════════════════════
# Bybit Adapter (Phase 2 — placeholder)
# ═══════════════════════════════════════════════

class BybitAdapter(ExchangeAdapter):
    """
    Bybit CEX adapter. Wraps pybit REST API.
    Used for Phase 2 exotic altcoin trading.
    """

    name = "bybit"

    def __init__(self, api_key: str = "", api_secret: str = "", testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.session = None
        self._connected = False
        self._open_times: dict[str, float] = {}

    async def connect(self):
        try:
            from pybit.unified_trading import HTTP
            self.session = HTTP(
                api_key=self.api_key,
                api_secret=self.api_secret,
                testnet=self.testnet,
            )
            self._connected = True
            log.info(f"✅ Connected to Bybit {'testnet' if self.testnet else 'mainnet'}")
        except ImportError:
            log.error("❌ pybit not installed. Run: pip install pybit")
            raise

    async def disconnect(self):
        self._connected = False
        log.info("Disconnected from Bybit")

    async def open_long(self, pair: str, size: float, leverage: int = 1, price: float = 0) -> str:
        self.session.set_leverage(category="linear", symbol=pair, buyLeverage=str(leverage), sellLeverage=str(leverage))
        order = self.session.place_order(
            category="linear", symbol=pair, side="Buy", orderType="Market",
            qty=str(size), timeInForce="IOC",
        )
        oid = order["result"]["orderId"]
        self._open_times[pair] = time.time()
        log.info(f"🟢 LONG {pair} size={size} lev={leverage} | ID: {oid}")
        return oid

    async def open_short(self, pair: str, size: float, leverage: int = 1, price: float = 0) -> str:
        self.session.set_leverage(category="linear", symbol=pair, buyLeverage=str(leverage), sellLeverage=str(leverage))
        order = self.session.place_order(
            category="linear", symbol=pair, side="Sell", orderType="Market",
            qty=str(size), timeInForce="IOC",
        )
        oid = order["result"]["orderId"]
        self._open_times[pair] = time.time()
        log.info(f"🔴 SHORT {pair} size={size} lev={leverage} | ID: {oid}")
        return oid

    async def close_position(self, pair: str) -> Optional[TradeResult]:
        pos_data = self.session.get_positions(category="linear", symbol=pair)
        positions = pos_data["result"]["list"]
        if not positions or float(positions[0]["size"]) == 0:
            return None

        p = positions[0]
        close_side = "Sell" if p["side"] == "Buy" else "Buy"
        self.session.place_order(
            category="linear", symbol=pair, side=close_side,
            orderType="Market", qty=p["size"], timeInForce="IOC",
            reduceOnly=True,
        )

        entry = float(p["avgPrice"])
        current = float(p["markPrice"])
        side = Side.LONG if p["side"] == "Buy" else Side.SHORT
        pnl_usd = float(p["unrealisedPnl"])
        pnl_pct = (pnl_usd / (entry * float(p["size"]))) * 100 if entry else 0
        duration = int(time.time() - self._open_times.get(pair, time.time()))

        result = TradeResult(
            pair=pair, side=side, entry_price=entry, exit_price=current,
            pnl_usd=pnl_usd, pnl_pct=pnl_pct, duration_secs=duration,
        )
        log.info(f"{'🟢' if pnl_pct >= 0 else '🔴'} CLOSE {pair} {side.value} {pnl_pct:+.2f}%")
        return result

    async def get_position(self, pair: str) -> Optional[Position]:
        pos_data = self.session.get_positions(category="linear", symbol=pair)
        positions = pos_data["result"]["list"]
        if not positions or float(positions[0]["size"]) == 0:
            return None
        p = positions[0]
        return Position(
            pair=pair,
            side=Side.LONG if p["side"] == "Buy" else Side.SHORT,
            size=float(p["size"]),
            entry_price=float(p["avgPrice"]),
            unrealized_pnl=float(p["unrealisedPnl"]),
            leverage=float(p["leverage"]),
            liquidation_price=float(p.get("liqPrice", 0) or 0),
        )

    async def get_all_positions(self) -> list[Position]:
        pos_data = self.session.get_positions(category="linear", settleCoin="USDT")
        positions = []
        for p in pos_data["result"]["list"]:
            if float(p["size"]) > 0:
                positions.append(Position(
                    pair=p["symbol"],
                    side=Side.LONG if p["side"] == "Buy" else Side.SHORT,
                    size=float(p["size"]),
                    entry_price=float(p["avgPrice"]),
                    unrealized_pnl=float(p["unrealisedPnl"]),
                    leverage=float(p["leverage"]),
                ))
        return positions

    async def get_balance(self) -> Balance:
        bal = self.session.get_wallet_balance(accountType="UNIFIED")
        coins = bal["result"]["list"][0]
        total = float(coins["totalEquity"])
        available = float(coins["availableBalance"])
        upnl = float(coins.get("totalUnrealisedPnl", 0) or 0)
        positions = await self.get_all_positions()
        return Balance(total_usd=total, available_usd=available,
                       unrealized_pnl=upnl, positions_count=len(positions))

    async def get_price(self, pair: str) -> float:
        ticker = self.session.get_tickers(category="linear", symbol=pair)
        return float(ticker["result"]["list"][0]["lastPrice"])

    async def cancel_all_orders(self, pair: str = "") -> int:
        if pair:
            self.session.cancel_all_orders(category="linear", symbol=pair)
        else:
            self.session.cancel_all_orders(category="linear")
        return 1


# ═══════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════

def create_exchange(name: str = "drift", **kwargs) -> ExchangeAdapter:
    """Factory: create exchange adapter by name."""
    if name == "drift":
        return DriftAdapter(**kwargs)
    elif name == "bybit":
        return BybitAdapter(**kwargs)
    else:
        raise ValueError(f"Unknown exchange: {name}. Use 'drift' or 'bybit'.")


# ═══════════════════════════════════════════════
# CLI test
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    async def main():
        adapter_name = sys.argv[1] if len(sys.argv) > 1 else "drift"
        print(f"\n🧊 Exchange Adapter Test: {adapter_name}")
        print("=" * 50)

        exchange = create_exchange(adapter_name)
        print(f"  Adapter: {exchange.name}")
        print(f"  Type:    {type(exchange).__name__}")

        # Show available Drift markets
        if adapter_name == "drift":
            print(f"\n  Available Drift markets ({len(DRIFT_MARKETS)}):")
            for pair, idx in sorted(DRIFT_MARKETS.items(), key=lambda x: x[1]):
                alias = [k for k, v in PAIR_ALIASES.items() if v == pair]
                alias_str = f" (aka {alias[0]})" if alias else ""
                print(f"    [{idx:2d}] {pair}{alias_str}")

        # Show pair translation
        print(f"\n  Pair translation examples:")
        for bybit_pair in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
            drift_pair = PAIR_ALIASES.get(bybit_pair, "?")
            idx = DRIFT_MARKETS.get(drift_pair, -1)
            print(f"    {bybit_pair} → {drift_pair} (index {idx})")

        print(f"\n✅ Adapter ready. Connect with: await exchange.connect()")
        print()

    asyncio.run(main())
