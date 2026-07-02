"""
═══════════════════════════════════════════════════════════════
  Drift Executor — Drop-in replacement for ExchangeExecutor
  Trades perpetual futures on Drift Protocol (Solana)
  
  Same interface as exchange_executor.py:
    - open_long / open_short
    - close_position / close_position_verified
    - get_balance / get_equity / get_positions
    - test_connection
═══════════════════════════════════════════════════════════════
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("drift_executor")

# ─── Data Classes (same as exchange_executor) ────────────────

@dataclass
class OrderResult:
    success: bool
    order_id: str = ""
    symbol: str = ""
    direction: str = ""
    size: float = 0.0
    price: float = 0.0
    timestamp: str = ""
    error: str = ""
    tx_signature: str = ""  # Solana TX sig (new for Drift)

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items() if v}


@dataclass
class PositionInfo:
    symbol: str
    side: str               # "long" or "short"
    size: float             # base asset amount
    notional: float         # in USD
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: int
    market_index: int = -1  # Drift market index


# ─── Symbol → Market Index Mapping ──────────────────────────

# Common Drift perp market indices (mainnet)
# Loaded dynamically at connect, these are fallbacks
DEFAULT_MARKET_MAP = {
    "SOL":  0,  "BTC":  1,  "ETH":   2,  "APT":  3,
    "1MBONK": 4, "MATIC": 5, "ARB":   6,  "DOGE": 7,
    "BNB":  8,  "SUI":  9,  "PEPE": 10,  "OP":  11,
    "RENDER": 12, "XRP": 13, "HNT":  14, "INJ":  15,
    "LINK": 16, "RLB":  17, "PYTH": 18, "TIA":  19,
    "JTO":  20, "SEI":  21, "AVAX": 22, "WIF":  23,
    "JUP":  24, "DYM":  25, "TAO":  26, "W":    27,
    "TNSR": 28, "DRIFT": 29, "WLD": 30,
}


def _normalize_to_base(symbol: str) -> str:
    """BTCUSDT → BTC, BTC/USDT:USDT → BTC, ETH-PERP → ETH"""
    s = symbol.upper().replace("-PERP", "").replace("/USDT:USDT", "")
    s = s.replace("/USDT", "").replace("USDT", "").replace("USD", "")
    s = s.replace("1000", "1M")  # 1000PEPE → 1MPEPE
    return s


# ─── Drift Executor ─────────────────────────────────────────

class DriftExecutor:
    """
    Drift Protocol executor with the same API as ExchangeExecutor.
    
    Modes:
      - paper: No connection, simulated fills (for testing)
      - devnet: Drift devnet (virtual funds)
      - mainnet: Real SOL/USDC trading
    """

    def __init__(
        self,
        keypair_path: str = None,
        rpc_url: str = None,
        mode: str = "devnet",
        default_leverage: int = 3,
        sub_account_id: int = 0,
        bot_id: str = "",
    ):
        self.mode = mode
        self.default_leverage = default_leverage
        self.sub_account_id = sub_account_id
        self.bot_id = bot_id
        self.keypair_path = keypair_path
        self.rpc_url = rpc_url or self._default_rpc()
        
        # Caches
        self._balance_cache: float = 0.0
        self._balance_cache_ts: float = 0
        self._positions_cache: List[PositionInfo] = []
        self._positions_cache_ts: float = 0
        self._CACHE_TTL = 30  # seconds
        
        # Market mapping (loaded on connect)
        self._market_map: Dict[str, int] = dict(DEFAULT_MARKET_MAP)
        self._market_names: Dict[int, str] = {v: k for k, v in DEFAULT_MARKET_MAP.items()}
        
        # Drift client (lazy init)
        self._client = None
        self._connection = None
        self._wallet = None
        self._loop = None
        self._connected = False

    def _default_rpc(self) -> str:
        if self.mode == "devnet":
            return "https://api.devnet.solana.com"
        return os.getenv("SOLANA_RPC", "https://api.mainnet-beta.solana.com")

    @classmethod
    def from_env(cls, bot_id: str = "") -> 'DriftExecutor':
        """Create executor from environment variables."""
        mode = os.getenv("DRIFT_MODE", "devnet")
        keypair = os.getenv("DRIFT_KEYPAIR_PATH", 
                           os.path.expanduser("~/.config/solana/id.json"))
        rpc = os.getenv("SOLANA_RPC", None)
        leverage = int(os.getenv("DEFAULT_LEVERAGE", "3"))
        sub = int(os.getenv("DRIFT_SUB_ACCOUNT", "0"))
        
        return cls(
            keypair_path=keypair,
            rpc_url=rpc,
            mode=mode,
            default_leverage=leverage,
            sub_account_id=sub,
            bot_id=bot_id,
        )

    # ─── Async Helpers ───────────────────────────────────────

    def _run(self, coro):
        """Run async coroutine from sync context."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(coro)

    async def _ensure_connected(self):
        """Lazy connect to Drift."""
        if self._connected:
            return
        
        if self.mode == "paper":
            self._connected = True
            log.info("DriftExecutor: paper mode (no connection)")
            return

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
            self._wallet = Wallet(kp)
            
            # Connect
            self._connection = AsyncClient(self.rpc_url)
            env = "devnet" if self.mode == "devnet" else "mainnet"
            self._client = DriftClient(self._connection, self._wallet, env)
            
            await self._client.add_user(self.sub_account_id)
            await self._client.subscribe()
            
            # Load market map dynamically
            try:
                perp_markets = self._client.get_perp_market_accounts()
                for m in perp_markets:
                    name = bytes(m.name).decode('utf-8').strip('\x00').replace('-PERP', '')
                    self._market_map[name] = m.market_index
                    self._market_names[m.market_index] = name
                log.info(f"Loaded {len(perp_markets)} Drift perp markets")
            except Exception as e:
                log.warning(f"Could not load markets dynamically: {e}, using defaults")
            
            self._connected = True
            log.info(f"DriftExecutor connected: {env}, wallet={kp.pubkey()}, "
                     f"markets={len(self._market_map)}")
            
        except ImportError as e:
            raise ImportError(
                f"Drift SDK not installed. Run: pip install driftpy solders anchorpy\n{e}"
            )
        except Exception as e:
            log.error(f"Failed to connect to Drift: {e}")
            raise

    def _get_market_index(self, symbol: str) -> int:
        """Convert symbol to Drift market index."""
        base = _normalize_to_base(symbol)
        idx = self._market_map.get(base)
        if idx is None:
            raise ValueError(
                f"Unknown Drift market: {symbol} (base={base}). "
                f"Available: {list(self._market_map.keys())[:20]}..."
            )
        return idx

    # ─── Public API (same as ExchangeExecutor) ───────────────

    def open_long(self, symbol: str, size_usdt: float,
                  stop_price: float = None, tp_price: float = None,
                  leverage: int = None) -> OrderResult:
        """Open a LONG position on Drift."""
        return self._run(self._open_position(
            symbol, "long", size_usdt, stop_price, tp_price, leverage
        ))

    def open_short(self, symbol: str, size_usdt: float,
                   stop_price: float = None, tp_price: float = None,
                   leverage: int = None) -> OrderResult:
        """Open a SHORT position on Drift."""
        return self._run(self._open_position(
            symbol, "short", size_usdt, stop_price, tp_price, leverage
        ))

    async def _open_position(self, symbol: str, direction: str,
                             size_usdt: float, stop_price: float = None,
                             tp_price: float = None, 
                             leverage: int = None) -> OrderResult:
        """Internal: open position on Drift."""
        ts = datetime.now(timezone.utc).isoformat()
        
        if self.mode == "paper":
            return OrderResult(
                success=True, order_id=f"paper_{int(time.time()*1000)}",
                symbol=symbol, direction=direction, size=size_usdt,
                price=0.0, timestamp=ts
            )

        await self._ensure_connected()
        
        try:
            from driftpy.types import PositionDirection
            from driftpy.constants.numeric_constants import BASE_PRECISION, PRICE_PRECISION
            
            market_index = self._get_market_index(symbol)
            
            # Get oracle price to compute base amount
            oracle_data = self._client.get_oracle_price_data_for_perp_market(market_index)
            oracle_price = oracle_data.price / PRICE_PRECISION
            
            if oracle_price <= 0:
                return OrderResult(success=False, error=f"Invalid oracle price for {symbol}", 
                                   symbol=symbol, timestamp=ts)
            
            # Convert USDT size to base asset amount
            base_amount = size_usdt / oracle_price
            base_amount_scaled = int(base_amount * BASE_PRECISION)
            
            # Direction
            pos_dir = PositionDirection.Long() if direction == "long" else PositionDirection.Short()
            
            # Open position
            tx_sig = await self._client.open_position(
                pos_dir, base_amount_scaled, market_index
            )
            
            log.info(f"✅ Drift {direction.upper()} {symbol} ${size_usdt:.1f} "
                     f"({base_amount:.4f} @ ${oracle_price:.2f}) tx={tx_sig}")
            
            # Invalidate cache
            self._positions_cache_ts = 0
            self._balance_cache_ts = 0
            
            return OrderResult(
                success=True,
                order_id=str(tx_sig)[:16],
                symbol=symbol,
                direction=direction,
                size=base_amount,
                price=oracle_price,
                timestamp=ts,
                tx_signature=str(tx_sig),
            )
            
        except Exception as e:
            log.error(f"❌ Drift open {direction} {symbol} failed: {e}")
            return OrderResult(success=False, error=str(e), symbol=symbol, 
                               direction=direction, timestamp=ts)

    def close_position(self, symbol: str, direction: str = None) -> OrderResult:
        """Close an existing position on Drift."""
        return self._run(self._close_position(symbol, direction))

    async def _close_position(self, symbol: str, direction: str = None) -> OrderResult:
        """Internal: close position."""
        ts = datetime.now(timezone.utc).isoformat()
        
        if self.mode == "paper":
            return OrderResult(success=True, order_id=f"paper_close_{int(time.time()*1000)}",
                               symbol=symbol, timestamp=ts)

        await self._ensure_connected()
        
        try:
            market_index = self._get_market_index(symbol)
            tx_sig = await self._client.close_position(market_index=market_index)
            
            log.info(f"✅ Drift CLOSE {symbol} (market_idx={market_index}) tx={tx_sig}")
            
            self._positions_cache_ts = 0
            self._balance_cache_ts = 0
            
            return OrderResult(
                success=True,
                order_id=str(tx_sig)[:16],
                symbol=symbol,
                direction=direction or "close",
                timestamp=ts,
                tx_signature=str(tx_sig),
            )
        except Exception as e:
            log.error(f"❌ Drift close {symbol} failed: {e}")
            return OrderResult(success=False, error=str(e), symbol=symbol, timestamp=ts)

    def close_position_verified(self, symbol: str, direction: str = None,
                                 max_retries: int = 3) -> OrderResult:
        """Close position with retry logic."""
        for attempt in range(max_retries):
            result = self.close_position(symbol, direction)
            if result.success:
                return result
            log.warning(f"Close attempt {attempt+1}/{max_retries} failed: {result.error}")
            time.sleep(1)
        return result

    def get_balance(self) -> float:
        """Get USDC balance (collateral) in USD."""
        if self.mode == "paper":
            return 0.0
        
        now = time.time()
        if now - self._balance_cache_ts < self._CACHE_TTL:
            return self._balance_cache
        
        try:
            self._run(self._ensure_connected())
            user = self._client.get_user(self.sub_account_id)
            
            from driftpy.constants.numeric_constants import QUOTE_PRECISION
            
            # Total collateral (free + in positions)
            total_collateral = user.get_total_collateral() / QUOTE_PRECISION
            self._balance_cache = float(total_collateral)
            self._balance_cache_ts = now
            return self._balance_cache
            
        except Exception as e:
            log.error(f"Failed to get Drift balance: {e}")
            return self._balance_cache

    def get_equity(self) -> float:
        """Get total equity (collateral + unrealized PnL)."""
        if self.mode == "paper":
            return 0.0
        return self.get_balance()  # Drift collateral includes unrealized PnL

    def get_positions(self) -> List[PositionInfo]:
        """Get all open perp positions."""
        if self.mode == "paper":
            return []
        
        now = time.time()
        if now - self._positions_cache_ts < self._CACHE_TTL:
            return self._positions_cache
        
        try:
            self._run(self._ensure_connected())
            user = self._client.get_user(self.sub_account_id)
            
            from driftpy.constants.numeric_constants import (
                BASE_PRECISION, PRICE_PRECISION, QUOTE_PRECISION
            )
            
            result = []
            perp_positions = user.get_active_perp_positions()
            
            for pos in perp_positions:
                base_size = pos.base_asset_amount / BASE_PRECISION
                if abs(base_size) < 1e-9:
                    continue
                
                side = "long" if base_size > 0 else "short"
                abs_size = abs(base_size)
                
                # Get market name
                market_name = self._market_names.get(
                    pos.market_index, f"MARKET_{pos.market_index}"
                )
                
                # Get oracle price for mark
                try:
                    oracle = self._client.get_oracle_price_data_for_perp_market(
                        pos.market_index
                    )
                    mark_price = oracle.price / PRICE_PRECISION
                except:
                    mark_price = 0.0
                
                # Entry price
                if abs_size > 0 and pos.quote_asset_amount != 0:
                    entry = abs(pos.quote_entry_amount / QUOTE_PRECISION) / abs_size
                else:
                    entry = mark_price
                
                # Unrealized PnL
                pnl = pos.quote_asset_amount / QUOTE_PRECISION
                if side == "long":
                    pnl += abs_size * mark_price
                else:
                    pnl -= abs_size * mark_price
                
                result.append(PositionInfo(
                    symbol=f"{market_name}USDT",  # Match CEX format
                    side=side,
                    size=abs_size,
                    notional=abs_size * mark_price,
                    entry_price=entry,
                    mark_price=mark_price,
                    unrealized_pnl=pnl,
                    leverage=self.default_leverage,
                    market_index=pos.market_index,
                ))
            
            self._positions_cache = result
            self._positions_cache_ts = now
            return result
            
        except Exception as e:
            log.error(f"Failed to get Drift positions: {e}")
            return self._positions_cache

    def get_position(self, symbol: str) -> Optional[PositionInfo]:
        """Get position for a specific symbol."""
        positions = self.get_positions()
        base = _normalize_to_base(symbol)
        for p in positions:
            if _normalize_to_base(p.symbol) == base:
                return p
        return None

    def test_connection(self) -> Dict:
        """Test Drift connection and return account info."""
        if self.mode == "paper":
            return {"mode": "paper", "connected": True, "balance": 0, "exchange": "drift"}

        try:
            self._run(self._ensure_connected())
            balance = self.get_balance()
            positions = self.get_positions()
            
            return {
                "mode": self.mode,
                "exchange": "drift",
                "connected": True,
                "balance_usd": balance,
                "open_positions": len(positions),
                "wallet": str(self._wallet.payer.pubkey()) if self._wallet else "N/A",
                "rpc": self.rpc_url[:40] + "...",
                "markets": len(self._market_map),
                "positions": [
                    {"symbol": p.symbol, "side": p.side, "pnl": p.unrealized_pnl}
                    for p in positions
                ],
            }
        except Exception as e:
            return {
                "mode": self.mode,
                "exchange": "drift",
                "connected": False,
                "error": str(e),
            }

    def _normalize_symbol(self, symbol: str) -> str:
        """Compatibility method — maps to Drift format."""
        return symbol

    def __repr__(self):
        env = "devnet" if self.mode == "devnet" else "mainnet"
        bot = f", bot={self.bot_id}" if self.bot_id else ""
        return (f"DriftExecutor(mode={self.mode}, env={env}, "
                f"leverage={self.default_leverage}x{bot})")

    def __del__(self):
        """Cleanup async resources."""
        if self._connection and self._loop and not self._loop.is_closed():
            try:
                self._loop.run_until_complete(self._connection.close())
            except:
                pass


# ─── Alias for drop-in replacement ──────────────────────────
ExchangeExecutor = DriftExecutor


# ─── CLI Test ────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "paper"
    
    print(f"═══ Drift Executor Test (mode={mode}) ═══\n")
    
    if mode == "paper":
        ex = DriftExecutor(mode="paper")
    else:
        ex = DriftExecutor.from_env()
    
    info = ex.test_connection()
    print(f"Connection: {info}\n")
    
    if mode == "paper":
        # Test paper trades
        r1 = ex.open_long("BTCUSDT", 100)
        print(f"Open long: {r1.to_dict()}")
        
        r2 = ex.open_short("ETHUSDT", 50)
        print(f"Open short: {r2.to_dict()}")
        
        r3 = ex.close_position("BTCUSDT")
        print(f"Close: {r3.to_dict()}")
        
        print(f"\nBalance: ${ex.get_balance()}")
        print(f"Positions: {ex.get_positions()}")
    
    print(f"\n{ex}")
