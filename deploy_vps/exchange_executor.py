"""
Exchange Executor — Unified Exchange Interface for Trading Bots.

Provides a clean abstraction layer between trading signals and exchange APIs.
Supports paper/demo/live modes via TRADING_MODE env variable.

Usage:
    executor = ExchangeExecutor.from_env()
    result = executor.open_long("BTCUSDT", size_usdt=62.5, leverage=5)
    balance = executor.get_balance()
    executor.close_position("BTCUSDT")
"""
import os
import json
import time
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Optional, Dict, List

try:
    import ccxt
except ImportError:
    ccxt = None

import re as _re

log = logging.getLogger("executor")

# ─── Global 418 Ban Tracker (shared across all instances) ─────
_ban_until = 0.0   # Unix timestamp when IP ban expires
_ban_lock = None
try:
    import threading
    _ban_lock = threading.Lock()
except Exception:
    pass

def _is_banned() -> bool:
    """Check if we're currently IP-banned by the exchange."""
    return time.time() < _ban_until

def _record_ban(error_msg: str):
    """Parse 418 error and record ban expiry timestamp."""
    global _ban_until
    # Extract 'banned until <timestamp>' from Binance error
    m = _re.search(r'banned until (\d+)', str(error_msg))
    if m:
        ban_ts = int(m.group(1)) / 1000  # ms -> seconds
        if _ban_lock:
            with _ban_lock:
                _ban_until = max(_ban_until, ban_ts)
        else:
            _ban_until = max(_ban_until, ban_ts)
        remaining = int(ban_ts - time.time())
        log.warning(f"🚫 IP BANNED for {remaining}s — all API calls suspended until {datetime.fromtimestamp(ban_ts).strftime('%H:%M:%S')}")
    else:
        # Fallback: ban for 2 minutes if can't parse
        fallback = time.time() + 120
        if _ban_lock:
            with _ban_lock:
                _ban_until = max(_ban_until, fallback)
        else:
            _ban_until = max(_ban_until, fallback)
        log.warning(f"🚫 IP BANNED (unknown duration) — suspending API calls for 120s")

def _is_418(error) -> bool:
    """Check if exception is a Binance 418 rate limit ban."""
    err_str = str(error)
    return '418' in err_str or 'teapot' in err_str.lower() or 'too many requests' in err_str.lower()


# ─── Data Classes ────────────────────────────────────────────

@dataclass
class OrderResult:
    """Result of an order execution."""
    success: bool
    order_id: str = ""
    symbol: str = ""
    side: str = ""          # "buy" or "sell"
    direction: str = ""     # "long" or "short"
    fill_price: float = 0.0
    fill_qty: float = 0.0
    fill_cost: float = 0.0  # notional value
    fee: float = 0.0
    timestamp: str = ""
    error: str = ""
    mode: str = "paper"     # paper/demo/live
    verified: bool = False  # True if position confirmed closed on exchange

    def to_dict(self):
        return asdict(self)


@dataclass
class PositionInfo:
    """Current position from exchange."""
    symbol: str
    side: str               # "long" or "short"
    size: float             # in contracts
    notional: float         # in USDT
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: int


# ─── Exchange Executor ───────────────────────────────────────

class ExchangeExecutor:
    """
    Unified exchange executor with paper/demo/live modes.
    
    Modes:
      - paper: No exchange connection, returns simulated fills
      - demo:  Connects to exchange Testnet, real order execution with virtual funds
      - live:  Connects to exchange Mainnet, REAL money (requires explicit confirmation)
    
    Supported exchanges: bybit, binance
    """

    # Exchange-specific config
    EXCHANGE_CONFIGS = {
        'bybit': {
            'class': 'bybit',
            'options': {'defaultType': 'swap', 'adjustForTimeDifference': True},
        },
        'binance': {
            'class': 'binanceusdm',
            'options': {'defaultType': 'swap', 'adjustForTimeDifference': True},
        },
    }

    def __init__(self, api_key: str, api_secret: str, 
                 exchange_id: str = "bybit",
                 testnet: bool = True, mode: str = "paper",
                 default_leverage: int = 5,
                 bot_id: str = ""):
        self.mode = mode
        self.default_leverage = default_leverage
        self.testnet = testnet
        self.exchange_id = exchange_id.lower()
        self.bot_id = bot_id
        self._exchange = None
        self._leverage_set = set()  # track which symbols had leverage set
        self._registry = None

        # Initialize position registry if bot_id is provided
        if bot_id:
            try:
                from position_registry import PositionRegistry
                self._registry = PositionRegistry(bot_id)
                log.info(f"📋 Position registry active for bot '{bot_id}'")
            except ImportError:
                log.warning("position_registry.py not found — registry disabled")

        if mode == "paper":
            log.info("ExchangeExecutor: PAPER mode — no exchange connection")
            return

        if ccxt is None:
            raise ImportError("ccxt is required for demo/live modes: pip install ccxt")

        if mode == "live" and testnet:
            raise ValueError("Live mode must NOT use testnet. Set EXCHANGE_TESTNET=false explicitly.")

        config = self.EXCHANGE_CONFIGS.get(self.exchange_id)
        if not config:
            raise ValueError(f"Unsupported exchange: {self.exchange_id}. Use: {list(self.EXCHANGE_CONFIGS.keys())}")

        exchange_class = getattr(ccxt, config['class'])
        self._exchange = exchange_class({
            'apiKey': api_key,
            'secret': api_secret,
            'options': config['options'].copy(),
        })

        if testnet:
            self._exchange.set_sandbox_mode(True)

        env_label = "TESTNET" if testnet else "🔴 MAINNET"
        log.info(f"ExchangeExecutor: {mode.upper()} mode on {self.exchange_id.upper()} {env_label}")

    @classmethod
    def from_env(cls, bot_id: str = "") -> 'ExchangeExecutor':
        """Create executor from environment variables."""
        mode = os.getenv("TRADING_MODE", "paper").lower()
        exchange_id = os.getenv("EXCHANGE_ID", "bybit").lower()
        
        # Exchange-agnostic keys with fallback to legacy BYBIT_* vars
        api_key = os.getenv("EXCHANGE_API_KEY") or os.getenv("BYBIT_API_KEY", "")
        api_secret = os.getenv("EXCHANGE_API_SECRET") or os.getenv("BYBIT_API_SECRET", "")
        testnet = os.getenv("EXCHANGE_TESTNET", os.getenv("BYBIT_TESTNET", "true")).lower() in ("true", "1", "yes")
        leverage = int(os.getenv("DEFAULT_LEVERAGE", "5"))

        if mode in ("demo", "live") and (not api_key or not api_secret):
            log.warning(f"TRADING_MODE={mode} but no API keys found. Falling back to paper.")
            mode = "paper"

        return cls(api_key, api_secret, exchange_id=exchange_id,
                   testnet=testnet, mode=mode, default_leverage=leverage,
                   bot_id=bot_id)

    # ─── Symbol Normalization ────────────────────────────────

    def _normalize_symbol(self, symbol: str) -> str:
        """Convert exchange-native symbol to ccxt unified format.
        
        BTCUSDT   → BTC/USDT:USDT   (futures)
        BTC/USDT  → BTC/USDT:USDT   (add settle currency)
        BTC/USDT:USDT → BTC/USDT:USDT (already correct)
        """
        if '/' in symbol and ':' in symbol:
            return symbol  # Already full ccxt format
        if '/' in symbol:
            # BTC/USDT → BTC/USDT:USDT
            return f"{symbol}:USDT"
        # Raw format: BTCUSDT → BTC/USDT:USDT
        if symbol.endswith('USDT'):
            base = symbol[:-4]
            return f"{base}/USDT:USDT"
        if symbol.endswith('USDC'):
            base = symbol[:-4]
            return f"{base}/USDC:USDC"
        return symbol

    # ─── Core Trading Methods ────────────────────────────────

    def _ensure_leverage(self, symbol: str, leverage: int = None):
        """Set leverage for symbol if not already set."""
        symbol = self._normalize_symbol(symbol)
        lev = leverage or self.default_leverage
        if symbol not in self._leverage_set and self._exchange:
            try:
                self._exchange.set_leverage(lev, symbol)
                self._leverage_set.add(symbol)
                log.info(f"Set leverage {lev}x for {symbol}")
            except Exception as e:
                # Some symbols may not support leverage change
                log.warning(f"Could not set leverage for {symbol}: {e}")

    def open_long(self, symbol: str, size_usdt: float, 
                  stop_price: float = None, tp_price: float = None,
                  leverage: int = None) -> OrderResult:
        """Open a LONG position."""
        return self._open_position(symbol, "long", "buy", size_usdt, 
                                   stop_price, tp_price, leverage)

    def open_short(self, symbol: str, size_usdt: float,
                   stop_price: float = None, tp_price: float = None,
                   leverage: int = None) -> OrderResult:
        """Open a SHORT position."""
        return self._open_position(symbol, "short", "sell", size_usdt,
                                   stop_price, tp_price, leverage)

    def _open_position(self, symbol: str, direction: str, side: str,
                       size_usdt: float, stop_price: float = None, 
                       tp_price: float = None, leverage: int = None) -> OrderResult:
        """Internal: open a position with optional SL/TP."""
        symbol = self._normalize_symbol(symbol)
        ts = datetime.now(timezone.utc).isoformat()

        # ─── Registry: check if symbol is available ───────────
        if self._registry:
            # Convert ccxt symbol back to raw for registry (BTC/USDT:USDT → BTCUSDT)
            raw_sym = symbol.split('/')[0] + symbol.split('/')[1].split(':')[0] if '/' in symbol else symbol
            if not self._registry.claim(raw_sym):
                owner = self._registry.owner(raw_sym)
                log.warning(f"🚫 {symbol}: blocked by registry — owned by '{owner}'")
                return OrderResult(
                    success=False, symbol=symbol, side=side, direction=direction,
                    error=f"Symbol owned by bot '{owner}'", timestamp=ts, mode=self.mode
                )
        # ──────────────────────────────────────────────────────

        if self.mode == "paper":
            return OrderResult(
                success=True, order_id=f"paper_{int(time.time())}",
                symbol=symbol, side=side, direction=direction,
                fill_price=0, fill_qty=0, fill_cost=size_usdt,
                timestamp=ts, mode="paper"
            )

        try:
            self._ensure_leverage(symbol, leverage)

            # Fetch current price to calculate qty
            ticker = self._exchange.fetch_ticker(symbol)
            price = ticker['last']
            lev = leverage or self.default_leverage
            qty = (size_usdt * lev) / price

            # Round qty to exchange precision
            market = self._exchange.market(symbol)
            qty = self._exchange.amount_to_precision(symbol, qty)
            qty = float(qty)

            log.info(f"Opening {direction.upper()} {symbol}: "
                     f"${size_usdt} x {lev}x = {qty} contracts @ ~{price}")

            # Place market order
            params = {}
            # SL/TP inline only supported on Bybit
            if self.exchange_id == 'bybit':
                if stop_price:
                    params['stopLoss'] = {'triggerPrice': stop_price, 'type': 'market'}
                if tp_price:
                    params['takeProfit'] = {'triggerPrice': tp_price, 'type': 'market'}

            order = self._exchange.create_order(
                symbol=symbol,
                type='market',
                side=side,
                amount=qty,
                params=params
            )

            fill_price = order.get('average') or order.get('price') or price
            fill_qty = order.get('filled') or qty

            result = OrderResult(
                success=True,
                order_id=str(order.get('id', '')),
                symbol=symbol,
                side=side,
                direction=direction,
                fill_price=float(fill_price) if fill_price else price,
                fill_qty=float(fill_qty),
                fill_cost=float(fill_price or price) * float(fill_qty) / lev,
                fee=float((order.get('fee') or {}).get('cost', 0) or 0),
                timestamp=ts,
                mode=self.mode
            )

            log.info(f"✅ {direction.upper()} {symbol} filled @ {result.fill_price} "
                     f"(qty={result.fill_qty}, order_id={result.order_id})")

            # ─── Server-side SL/TP for Binance (direct Algo Order API) ──
            # Binance migrated conditional orders to /fapi/v1/algoOrder.
            # ccxt 4.x does NOT route to this endpoint, so we call it
            # directly using signed HTTP requests. Verified working.
            if self.exchange_id == 'binance' and (stop_price or tp_price):
                close_side = 'SELL' if side == 'buy' else 'BUY'
                raw_sym = symbol.replace('/USDT:USDT', 'USDT').replace('/USDT', 'USDT').replace('/', '')

                def _place_algo_order(trigger_px: float, order_type: str, label: str):
                    """Place SL/TP via direct /fapi/v1/algoOrder HTTP call."""
                    import time as _t, hmac as _hmac, hashlib as _hl
                    from urllib.parse import urlencode as _ue
                    try:
                        import requests as _req
                    except ImportError:
                        log.warning(f"   ⚠️ {label}: requests lib not available")
                        return
                    try:
                        # Determine base URL (testnet vs mainnet)
                        if self.testnet:
                            base = 'https://testnet.binancefuture.com'
                        else:
                            base = 'https://fapi.binance.com'

                        # v3.5: Round prices and qty to exchange precision
                        # Fixes Binance -1111 "Precision is over the maximum"
                        rounded_price = self._exchange.price_to_precision(symbol, trigger_px)
                        rounded_qty = self._exchange.amount_to_precision(symbol, fill_qty)

                        params = {
                            'algoType':     'CONDITIONAL',
                            'symbol':       raw_sym,
                            'side':         close_side,
                            'type':         order_type,
                            'triggerPrice': str(rounded_price),
                            'quantity':     str(rounded_qty),
                            'reduceOnly':   'true',
                            'workingType':  'MARK_PRICE',
                            'timestamp':    str(int(_t.time() * 1000)),
                            'recvWindow':   '5000',
                        }
                        qs = _ue(params)
                        sig = _hmac.new(
                            self._exchange.secret.encode(),
                            qs.encode(),
                            _hl.sha256
                        ).hexdigest()
                        params['signature'] = sig
                        headers = {'X-MBX-APIKEY': self._exchange.apiKey}

                        resp = _req.post(
                            f'{base}/fapi/v1/algoOrder',
                            params=params,
                            headers=headers,
                            timeout=10
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            algo_id = data.get('algoId', '?')
                            log.info(f"   {label} @ {trigger_px} (algoId={algo_id})")
                        else:
                            log.warning(f"   ⚠️ {label} HTTP {resp.status_code}: {resp.text[:200]}")
                    except Exception as e:
                        log.warning(f"   ⚠️ {label} failed: {e}")

                if stop_price:
                    _place_algo_order(stop_price, 'STOP_MARKET',        '🛑 SL placed')
                if tp_price:
                    _place_algo_order(tp_price,   'TAKE_PROFIT_MARKET', '🎯 TP placed')
            # ──────────────────────────────────────────────────

            return result

        except Exception as e:
            log.error(f"❌ Failed to open {direction} {symbol}: {e}")
            return OrderResult(
                success=False, symbol=symbol, side=side, direction=direction,
                error=str(e), timestamp=ts, mode=self.mode
            )

    def close_position(self, symbol: str, direction: str = None) -> OrderResult:
        """Close an existing position. Auto-detects direction if not specified."""
        symbol = self._normalize_symbol(symbol)
        ts = datetime.now(timezone.utc).isoformat()

        if self.mode == "paper":
            # Release registry on paper close
            if self._registry:
                raw_sym = symbol.split('/')[0] + symbol.split('/')[1].split(':')[0] if '/' in symbol else symbol
                self._registry.release(raw_sym)
            return OrderResult(
                success=True, order_id=f"paper_close_{int(time.time())}",
                symbol=symbol, side="sell" if direction == "long" else "buy",
                direction=direction or "unknown", timestamp=ts, mode="paper",
                verified=True
            )

        try:
            # Get current position to determine size and direction
            positions = self._exchange.fetch_positions([symbol])
            pos = None
            for p in positions:
                if p['symbol'] == symbol and p['contracts'] and float(p['contracts']) > 0:
                    pos = p
                    break

            if not pos:
                log.warning(f"No open position found for {symbol}")
                # Position already closed — release registry
                if self._registry:
                    raw_sym = symbol.split('/')[0] + symbol.split('/')[1].split(':')[0] if '/' in symbol else symbol
                    self._registry.release(raw_sym)
                return OrderResult(success=False, symbol=symbol,
                                   error="No open position", timestamp=ts, mode=self.mode)

            side = pos.get('side', '')
            close_side = 'sell' if side == 'long' else 'buy'
            qty = float(pos['contracts'])

            log.info(f"Closing {side.upper()} {symbol}: {qty} contracts")

            # Cancel any pending SL/TP orders first (regular + algo)
            # Prevents orphaned stop orders from triggering after position is closed
            if self.exchange_id == 'binance':
                raw_sym = symbol.replace('/USDT:USDT', 'USDT').replace('/USDT', 'USDT').replace('/', '')
                # Cancel regular orders
                try:
                    self._exchange.cancel_all_orders(symbol)
                    log.info(f"   🗑️ Cancelled regular orders for {symbol}")
                except Exception as e:
                    log.debug(f"   cancel regular orders: {e}")
                # Cancel algo/conditional orders (SL/TP placed via /fapi/v1/algoOrder)
                try:
                    import time as _t, hmac as _hmac, hashlib as _hl, requests as _req
                    from urllib.parse import urlencode as _ue
                    base = 'https://testnet.binancefuture.com' if self.testnet else 'https://fapi.binance.com'

                    # Fetch open algo orders
                    q_params = {
                        'symbol': raw_sym,
                        'timestamp': str(int(_t.time() * 1000)),
                        'recvWindow': '5000',
                    }
                    qs = _ue(q_params)
                    sig = _hmac.new(self._exchange.secret.encode(), qs.encode(), _hl.sha256).hexdigest()
                    q_params['signature'] = sig
                    headers = {'X-MBX-APIKEY': self._exchange.apiKey}
                    resp = _req.get(f'{base}/fapi/v1/openAlgoOrders', params=q_params, headers=headers, timeout=10)

                    if resp.status_code == 200:
                        open_algos = resp.json() if isinstance(resp.json(), list) else resp.json().get('orders', [])
                        cancelled = 0
                        for algo in open_algos:
                            algo_id = algo.get('algoId')
                            if algo_id:
                                c_params = {
                                    'algoId': str(algo_id),
                                    'timestamp': str(int(_t.time() * 1000)),
                                    'recvWindow': '5000',
                                }
                                c_qs = _ue(c_params)
                                c_sig = _hmac.new(self._exchange.secret.encode(), c_qs.encode(), _hl.sha256).hexdigest()
                                c_params['signature'] = c_sig
                                _req.delete(f'{base}/fapi/v1/algoOrder', params=c_params, headers=headers, timeout=10)
                                cancelled += 1
                        if cancelled:
                            log.info(f"   🗑️ Cancelled {cancelled} algo SL/TP for {symbol}")
                except Exception as e:
                    log.debug(f"   cancel algo orders: {e}")

            order = self._exchange.create_order(
                symbol=symbol,
                type='market',
                side=close_side,
                amount=qty,
                params={'reduceOnly': True}
            )


            fill_price = order.get('average') or order.get('price') or 0

            result = OrderResult(
                success=True,
                order_id=str(order.get('id', '')),
                symbol=symbol,
                side=close_side,
                direction=side,
                fill_price=float(fill_price) if fill_price else 0,
                fill_qty=qty,
                timestamp=ts,
                mode=self.mode
            )

            log.info(f"✅ Closed {side.upper()} {symbol} @ {result.fill_price}")

            # Release registry after successful close
            if self._registry:
                raw_sym = symbol.split('/')[0] + symbol.split('/')[1].split(':')[0] if '/' in symbol else symbol
                self._registry.release(raw_sym)

            return result

        except Exception as e:
            log.error(f"❌ Failed to close {symbol}: {e}")
            return OrderResult(
                success=False, symbol=symbol, error=str(e),
                timestamp=ts, mode=self.mode
            )

    def close_position_verified(self, symbol: str, direction: str = None,
                                max_retries: int = 3, verify_delay: float = 2.0) -> OrderResult:
        """
        Close position with verification — confirms the position is actually
        closed on the exchange. Retries up to max_retries times.
        
        Returns OrderResult with verified=True if position confirmed closed.
        """
        symbol = self._normalize_symbol(symbol)

        if self.mode == "paper":
            return self.close_position(symbol, direction)

        last_result = None
        for attempt in range(1, max_retries + 1):
            log.info(f"🔄 Close attempt {attempt}/{max_retries} for {symbol}")
            result = self.close_position(symbol, direction)
            last_result = result

            if not result.success:
                err = result.error or ""
                # If symbol doesn't exist on this exchange — treat as already closed
                if "does not have market symbol" in err or "symbol" in err.lower() and "not" in err.lower():
                    result.verified = True
                    result.success = True
                    log.warning(f"⚠️ {symbol}: not listed on this exchange — treating as closed (paper-only position)")
                    # Release registry
                    if self._registry:
                        raw_sym = symbol.split('/')[0] + symbol.split('/')[1].split(':')[0] if '/' in symbol else symbol
                        self._registry.release(raw_sym)
                    return result
                # If already no open position — it's closed
                if "No open position" in err:
                    result.verified = True
                    result.success = True
                    log.info(f"✅ {symbol}: already closed (verified)")
                    return result
                log.warning(f"⚠️ Close attempt {attempt} failed: {result.error}")
                # If 418 banned — stop retrying immediately
                if _is_418(err):
                    _record_ban(err)
                    log.error(f"🚫 {symbol}: IP banned — aborting close retries")
                    return result
                if attempt < max_retries:
                    time.sleep(verify_delay)
                continue

            # Verify: check that position is actually gone
            time.sleep(verify_delay)
            try:
                positions = self._exchange.fetch_positions([symbol])
                still_open = False
                for p in positions:
                    if p['symbol'] == symbol and p.get('contracts') and float(p['contracts']) > 0:
                        still_open = True
                        remaining = float(p['contracts'])
                        log.warning(f"⚠️ Position still open after close: {symbol} = {remaining} contracts")
                        break

                if not still_open:
                    result.verified = True
                    log.info(f"✅ {symbol}: VERIFIED closed on exchange")
                    return result
                else:
                    log.warning(f"⚠️ Partial fill or close failed — retrying ({attempt}/{max_retries})")

            except Exception as e:
                log.warning(f"⚠️ Verification check failed: {e}")

        # Exhausted retries
        if last_result:
            last_result.verified = False
            log.error(f"❌ {symbol}: close NOT VERIFIED after {max_retries} attempts")
        return last_result or OrderResult(
            success=False, symbol=symbol, error="Close verification failed",
            timestamp=datetime.now(timezone.utc).isoformat(), mode=self.mode,
            verified=False
        )

    # ─── Account Info (with caching + 418 protection) ────────

    _balance_cache: float = 0.0
    _balance_cache_ts: float = 0
    _positions_cache: list = []
    _positions_cache_ts: float = 0
    _ACCOUNT_CACHE_TTL = 60  # 60s cache for balance/positions

    def get_balance(self) -> float:
        """Get USDT balance (cached 60s, 418-safe)."""
        if self.mode == "paper":
            return 0.0

        now = time.time()
        # Return cached if fresh
        if now - self._balance_cache_ts < self._ACCOUNT_CACHE_TTL:
            return self._balance_cache

        # If banned, return last cached value
        if _is_banned():
            return self._balance_cache

        try:
            balance = self._exchange.fetch_balance({'type': 'swap'})
            usdt = balance.get('USDT', {})
            total = usdt.get('total', 0) or 0
            self._balance_cache = float(total)
            self._balance_cache_ts = now
            return self._balance_cache
        except Exception as e:
            if _is_418(e):
                _record_ban(e)
                return self._balance_cache  # return last known
            log.error(f"Failed to fetch balance: {e}")
            return self._balance_cache if self._balance_cache > 0 else 0.0

    def get_equity(self) -> float:
        """Get total equity (balance + unrealized PnL)."""
        if self.mode == "paper":
            return 0.0
        # Reuse cached balance to avoid extra API call
        return self.get_balance()

    def get_positions(self) -> List[PositionInfo]:
        """Get all open positions (cached 60s, 418-safe)."""
        if self.mode == "paper":
            return []

        now = time.time()
        # Return cached if fresh
        if now - self._positions_cache_ts < self._ACCOUNT_CACHE_TTL:
            return self._positions_cache

        # If banned, return last cached value
        if _is_banned():
            return self._positions_cache

        try:
            positions = self._exchange.fetch_positions()
            result = []
            for p in positions:
                contracts = float(p.get('contracts', 0) or 0)
                if contracts > 0:
                    result.append(PositionInfo(
                        symbol=p['symbol'],
                        side=p.get('side', 'long'),
                        size=contracts,
                        notional=float(p.get('notional', 0) or 0),
                        entry_price=float(p.get('entryPrice', 0) or 0),
                        mark_price=float(p.get('markPrice', 0) or 0),
                        unrealized_pnl=float(p.get('unrealizedPnl', 0) or 0),
                        leverage=int(p.get('leverage', 1) or 1)
                    ))
            self._positions_cache = result
            self._positions_cache_ts = now
            return result
        except Exception as e:
            if _is_418(e):
                _record_ban(e)
                return self._positions_cache  # return last known
            log.error(f"Failed to fetch positions: {e}")
            return self._positions_cache

    def get_position(self, symbol: str) -> Optional[PositionInfo]:
        """Get position for a specific symbol."""
        symbol = self._normalize_symbol(symbol)
        positions = self.get_positions()
        for p in positions:
            if p.symbol == symbol:
                return p
        return None

    # ─── Trade History ───────────────────────────────────────

    _trade_cache: Dict = {}
    _trade_cache_ts: float = 0
    _income_cache: List = []
    _income_cache_ts: float = 0
    _HISTORY_CACHE_TTL = 60  # seconds

    def get_trade_history(self, symbol: str = None, since: int = None,
                          limit: int = 500) -> List[Dict]:
        """
        Fetch all trades from exchange via ccxt.
        Returns list of dicts: symbol, side, price, amount, cost, fee, datetime, order_id.
        Results are cached for 60s.
        """
        if symbol:
            symbol = self._normalize_symbol(symbol)

        if self.mode == "paper" or not self._exchange:
            return []

        now = time.time()
        cache_key = f"{symbol or 'all'}:{since}:{limit}"
        if (now - self._trade_cache_ts < self._HISTORY_CACHE_TTL
                and cache_key in self._trade_cache):
            return self._trade_cache[cache_key]

        # If banned, return cached
        if _is_banned():
            return self._trade_cache.get(cache_key, [])

        try:
            # For Binance futures: fetch_my_trades
            params = {}
            all_trades = []

            if symbol:
                trades = self._exchange.fetch_my_trades(
                    symbol, since=since, limit=limit, params=params)
                all_trades = trades
            else:
                # Fetch for common symbols if no symbol specified
                # First try fetching recent orders to find traded symbols
                try:
                    orders = self._exchange.fetch_closed_orders(
                        symbol=None, since=since, limit=limit)
                    symbols_traded = list(set(o['symbol'] for o in orders if o.get('symbol')))
                except Exception:
                    symbols_traded = []

                # Fallback: fetch from known positions + common pairs
                if not symbols_traded:
                    try:
                        positions = self._exchange.fetch_positions()
                        symbols_traded = list(set(
                            p['symbol'] for p in positions
                            if p.get('symbol')
                        ))
                    except Exception:
                        symbols_traded = []

                # Add common pairs as fallback
                for default_sym in ['BTC/USDT:USDT', 'ETH/USDT:USDT']:
                    if default_sym not in symbols_traded:
                        symbols_traded.append(default_sym)

                for sym in symbols_traded:
                    try:
                        trades = self._exchange.fetch_my_trades(
                            sym, since=since, limit=min(limit, 200), params=params)
                        all_trades.extend(trades)
                    except Exception as e:
                        log.debug(f"No trades for {sym}: {e}")

            # Normalize and sort
            result = []
            for t in all_trades:
                fee_info = t.get('fee') or {}
                result.append({
                    'id': t.get('id', ''),
                    'order_id': t.get('order', ''),
                    'symbol': t.get('symbol', ''),
                    'side': t.get('side', ''),
                    'price': float(t.get('price', 0) or 0),
                    'amount': float(t.get('amount', 0) or 0),
                    'cost': float(t.get('cost', 0) or 0),
                    'fee': float(fee_info.get('cost', 0) or 0),
                    'fee_currency': fee_info.get('currency', 'USDT'),
                    'datetime': t.get('datetime', ''),
                    'timestamp': t.get('timestamp', 0),
                    'taker_or_maker': t.get('takerOrMaker', ''),
                    'type': t.get('type', ''),
                    'info': t.get('info', {}),
                })

            result.sort(key=lambda x: x.get('timestamp', 0))
            self._trade_cache[cache_key] = result
            self._trade_cache_ts = now
            log.info(f"Fetched {len(result)} trades from exchange")
            return result

        except Exception as e:
            if _is_418(e):
                _record_ban(e)
                return self._trade_cache.get(cache_key, [])
            log.error(f"Failed to fetch trade history: {e}")
            return []

    def get_income_history(self, income_type: str = None,
                           since: int = None, limit: int = 500) -> List[Dict]:
        """
        Fetch income/PnL history from Binance Futures.
        Types: REALIZED_PNL, COMMISSION, FUNDING_FEE, TRANSFER, etc.
        Returns list of dicts: symbol, income_type, income, asset, time.
        Cached for 60s.
        """
        if self.mode == "paper" or not self._exchange:
            return []

        now = time.time()
        if (now - self._income_cache_ts < self._HISTORY_CACHE_TTL
                and self._income_cache):
            filtered = self._income_cache
            if income_type:
                filtered = [i for i in filtered if i.get('income_type') == income_type]
            return filtered

        # If banned, return cached
        if _is_banned():
            filtered = self._income_cache
            if income_type:
                filtered = [i for i in filtered if i.get('income_type') == income_type]
            return filtered

        try:
            # Binance-specific: fapiPrivateGetIncome
            params = {'limit': limit}
            if income_type:
                params['incomeType'] = income_type
            if since:
                params['startTime'] = since

            if self.exchange_id == 'binance' and hasattr(self._exchange, 'fapiPrivateGetIncome'):
                raw = self._exchange.fapiPrivateGetIncome(params)
            else:
                # Fallback for other exchanges: try fetch_ledger
                try:
                    ledger = self._exchange.fetch_ledger(params=params)
                    raw = []
                    for entry in ledger:
                        raw.append({
                            'symbol': entry.get('info', {}).get('symbol', ''),
                            'incomeType': entry.get('type', ''),
                            'income': str(entry.get('amount', 0)),
                            'asset': entry.get('currency', 'USDT'),
                            'time': entry.get('timestamp', 0),
                            'info': str(entry.get('info', '')),
                        })
                except Exception:
                    raw = []

            result = []
            for item in raw:
                income_val = float(item.get('income', 0) or 0)
                result.append({
                    'symbol': item.get('symbol', ''),
                    'income_type': item.get('incomeType', ''),
                    'income': income_val,
                    'asset': item.get('asset', 'USDT'),
                    'time': int(item.get('time', 0) or 0),
                    'datetime': datetime.fromtimestamp(
                        int(item.get('time', 0) or 0) / 1000,
                        tz=timezone.utc
                    ).isoformat() if item.get('time') else '',
                    'info': item.get('info', ''),
                })

            result.sort(key=lambda x: x.get('time', 0))
            self._income_cache = result
            self._income_cache_ts = now
            log.info(f"Fetched {len(result)} income records from exchange")

            if income_type:
                return [i for i in result if i.get('income_type') == income_type]
            return result

        except Exception as e:
            if _is_418(e):
                _record_ban(e)
                return self._income_cache
            log.error(f"Failed to fetch income history: {e}")
            return []

    def get_closed_orders(self, symbol: str = None, since: int = None,
                          limit: int = 100) -> List[Dict]:
        """Fetch closed/filled orders from exchange."""
        if self.mode == "paper" or not self._exchange:
            return []
        try:
            orders = self._exchange.fetch_closed_orders(
                symbol=symbol, since=since, limit=limit)
            result = []
            for o in orders:
                fee_info = o.get('fee') or {}
                result.append({
                    'id': o.get('id', ''),
                    'symbol': o.get('symbol', ''),
                    'side': o.get('side', ''),
                    'type': o.get('type', ''),
                    'price': float(o.get('average') or o.get('price', 0) or 0),
                    'amount': float(o.get('filled') or o.get('amount', 0) or 0),
                    'cost': float(o.get('cost', 0) or 0),
                    'fee': float(fee_info.get('cost', 0) or 0),
                    'status': o.get('status', ''),
                    'datetime': o.get('datetime', ''),
                    'timestamp': o.get('timestamp', 0),
                    'reduce_only': o.get('reduceOnly', False),
                    'info': o.get('info', {}),
                })
            result.sort(key=lambda x: x.get('timestamp', 0))
            return result
        except Exception as e:
            log.error(f"Failed to fetch closed orders: {e}")
            return []

    # ─── Utilities ───────────────────────────────────────────

    def test_connection(self) -> Dict:
        """Test exchange connection and return account info."""
        if self.mode == "paper":
            return {"mode": "paper", "connected": True, "balance": 0}

        try:
            balance = self.get_balance()
            positions = self.get_positions()
            return {
                "mode": self.mode,
                "testnet": self.testnet,
                "connected": True,
                "balance_usdt": balance,
                "open_positions": len(positions),
                "positions": [
                    {"symbol": p.symbol, "side": p.side, "pnl": p.unrealized_pnl}
                    for p in positions
                ]
            }
        except Exception as e:
            return {
                "mode": self.mode,
                "connected": False,
                "error": str(e)
            }

    def __repr__(self):
        env = "testnet" if self.testnet else "mainnet"
        bot = f", bot={self.bot_id}" if self.bot_id else ""
        return f"ExchangeExecutor(mode={self.mode}, exchange={self.exchange_id}, env={env}, leverage={self.default_leverage}x{bot})"


# ─── CLI Test ────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    executor = ExchangeExecutor.from_env()
    print(f"\n{executor}")

    info = executor.test_connection()
    print(f"\nConnection test:")
    for k, v in info.items():
        print(f"  {k}: {v}")

    if len(sys.argv) > 1 and sys.argv[1] == "test-trade":
        symbol = sys.argv[2] if len(sys.argv) > 2 else "BTC/USDT:USDT"
        size = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0

        print(f"\n--- Test Trade: LONG {symbol} ${size} ---")
        result = executor.open_long(symbol, size)
        print(f"Open result: {result}")

        if result.success:
            input("\nPress Enter to close position...")
            close = executor.close_position(symbol)
            print(f"Close result: {close}")
"""
Usage:

  .env config:
    EXCHANGE_ID=binance          # bybit or binance
    EXCHANGE_API_KEY=your_key
    EXCHANGE_API_SECRET=your_secret
    EXCHANGE_TESTNET=true
    TRADING_MODE=demo            # paper / demo / live
    DEFAULT_LEVERAGE=5

  Legacy Bybit vars also supported:
    BYBIT_API_KEY, BYBIT_API_SECRET, BYBIT_TESTNET

  Test connection:
    python3 exchange_executor.py

  Test trade:
    python3 exchange_executor.py test-trade BTC/USDT:USDT 10

  Binance Testnet: https://testnet.binancefuture.com
  Bybit Testnet:   https://testnet.bybit.com
"""

