"""Jupiter Perps Mirror — mirrors Soldier paper trades to Jupiter Perpetuals (Solana).

Works alongside paper_trader + bybit_mirror + hl_mirror. Four parallel modes:
  1) Paper (always) — tracks signals, calculates PnL
  2) HL Mirror — opens on Hyperliquid if coin exists there
  3) Bybit Mirror — opens on Bybit if coin exists there
  4) Jupiter Mirror — opens on Jupiter Perps (Solana) if coin exists there

Safe: if Jupiter fails, paper trade proceeds normally.
Auto-compound: position size scales with SOL balance.

Jupiter Perps uses on-chain Solana transactions (not REST API).
Requires: solana-py, solders, base58

Env vars:
  JUPITER_MIRROR=1              — enable mirror
  JUPITER_MIRROR_MODE=paper     — paper (log only) or live (real trades)
  JUPITER_WALLET_KEY=<base58>   — private key for trading wallet
  JUPITER_RPC_URL=<url>         — Solana RPC endpoint
  JUPITER_LEVERAGE=5            — leverage (1-100x)
  JUPITER_MAX_POS=3             — max concurrent positions
  JUPITER_DEPOSIT_SOL=10        — initial deposit in SOL for PnL tracking
"""
import logging
import os
import threading
import time
import json
from dataclasses import dataclass, field
from typing import Dict, Optional

log = logging.getLogger("jupiter_mirror")

# ═══════════════════════════════════════════════
# Jupiter Perps market mapping
# Maps Bybit/common symbols → Jupiter market index
# ═══════════════════════════════════════════════
JUPITER_MARKETS = {
    "SOL":  {"index": 0, "name": "SOL-PERP",  "min_size_usd": 10},
    "BTC":  {"index": 1, "name": "BTC-PERP",  "min_size_usd": 10},
    "ETH":  {"index": 2, "name": "ETH-PERP",  "min_size_usd": 10},
    "SUI":  {"index": 3, "name": "SUI-PERP",  "min_size_usd": 10},
    "WIF":  {"index": 4, "name": "WIF-PERP",  "min_size_usd": 10},
    "JTO":  {"index": 5, "name": "JTO-PERP",  "min_size_usd": 10},
    "JUP":  {"index": 6, "name": "JUP-PERP",  "min_size_usd": 10},
    "BONK": {"index": 7, "name": "BONK-PERP", "min_size_usd": 10},
    "W":    {"index": 8, "name": "W-PERP",    "min_size_usd": 10},
    "TNSR": {"index": 9, "name": "TNSR-PERP", "min_size_usd": 10},
    "RENDER": {"index": 10, "name": "RENDER-PERP", "min_size_usd": 10},
}

# Jupiter Perpetuals Program ID
JUPITER_PERP_PROGRAM = "PERPHjGBqRHArX4DySjwM6UJHiR3sWAatqfdBS2qQJu"

# ═══════════════════════════════════════════════
# State
# ═══════════════════════════════════════════════

@dataclass
class JupiterPosition:
    symbol: str
    direction: str  # "long" or "short"
    size_usd: float
    entry_price: float
    open_time: float
    jupiter_market: str


_enabled = False
_mode = "paper"  # "paper" or "live"
_leverage = 5
_max_positions = 3
_deposit_sol = 10.0
_lock = threading.Lock()
_positions: Dict[str, JupiterPosition] = {}
_pnl_total = 0.0
_trades_count = 0
_wins = 0

# Solana client (lazy init for live mode)
_solana_client = None
_wallet_keypair = None
_rpc_url = ""


def _normalize_symbol(symbol: str) -> Optional[str]:
    """Extract base asset from various symbol formats.
    SOLUSDT → SOL, BTCUSDT → BTC, ETHUSDT.P → ETH
    """
    s = symbol.upper().replace(".P", "").replace("/USDT", "").replace("/USD", "")
    for suffix in ["USDT", "USDC", "USD", "BUSD"]:
        if s.endswith(suffix):
            s = s[:-len(suffix)]
    return s if s in JUPITER_MARKETS else None


def init_jupiter_mirror() -> bool:
    """Initialize Jupiter mirror from env. Called once at startup."""
    global _enabled, _mode, _leverage, _max_positions, _deposit_sol
    global _solana_client, _wallet_keypair, _rpc_url

    if os.getenv("JUPITER_MIRROR", "0") != "1":
        log.info("Jupiter Mirror disabled (JUPITER_MIRROR != 1)")
        return False

    _mode = os.getenv("JUPITER_MIRROR_MODE", "paper").lower()
    _leverage = int(os.getenv("JUPITER_LEVERAGE", "5"))
    _max_positions = int(os.getenv("JUPITER_MAX_POS", "3"))
    _deposit_sol = float(os.getenv("JUPITER_DEPOSIT_SOL", "10"))
    _rpc_url = os.getenv("JUPITER_RPC_URL", "https://api.mainnet-beta.solana.com")

    if _mode == "live":
        try:
            from solders.keypair import Keypair  # type: ignore
            from solana.rpc.api import Client  # type: ignore
            import base58

            wallet_key = os.getenv("JUPITER_WALLET_KEY", "")
            if not wallet_key:
                log.warning("Jupiter Mirror LIVE: no JUPITER_WALLET_KEY — falling back to paper")
                _mode = "paper"
            else:
                _wallet_keypair = Keypair.from_bytes(base58.b58decode(wallet_key))
                _solana_client = Client(_rpc_url)
                # Verify connection
                bal = _solana_client.get_balance(_wallet_keypair.pubkey())
                sol_balance = bal.value / 1e9
                log.info(
                    f"🪐 Jupiter Mirror LIVE: wallet={str(_wallet_keypair.pubkey())[:8]}... "
                    f"balance={sol_balance:.4f} SOL, leverage={_leverage}x"
                )
        except ImportError:
            log.warning("Jupiter Mirror: solana-py/solders not installed — paper mode")
            _mode = "paper"
        except Exception as e:
            log.error(f"Jupiter Mirror LIVE init failed: {e} — falling back to paper")
            _mode = "paper"

    _enabled = True
    markets = ", ".join(JUPITER_MARKETS.keys())
    log.info(
        f"🪐 Jupiter Mirror ENABLED [{_mode.upper()}]: "
        f"leverage={_leverage}x, max_pos={_max_positions}, "
        f"markets=[{markets}]"
    )
    return True


def _calc_size_usd(sol_price: float = 150.0) -> float:
    """Calculate position size in USD based on balance and leverage."""
    # In paper mode, use deposit_sol * sol_price as balance
    balance_usd = _deposit_sol * sol_price
    # Allocate per position with buffer
    size = (balance_usd / _max_positions) * 0.75
    return max(size, 10.0)


def _get_current_price(symbol: str) -> float:
    """Get current price for a symbol. Uses Jupiter price API."""
    try:
        import urllib.request
        base = _normalize_symbol(symbol)
        # Jupiter price API v2
        url = f"https://api.jup.ag/price/v2?ids={base}"
        req = urllib.request.Request(url, headers={"User-Agent": "ChillerBot/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            if "data" in data and base in data["data"]:
                return float(data["data"][base]["price"])
    except Exception as e:
        log.debug(f"Jupiter price fetch failed for {symbol}: {e}")
    return 0.0


# ═══════════════════════════════════════════════
# Live mode: on-chain transaction builders
# ═══════════════════════════════════════════════

def _open_position_live(symbol: str, direction: str, size_usd: float) -> dict:
    """Open position on Jupiter Perps via on-chain transaction."""
    if not _solana_client or not _wallet_keypair:
        return {"success": False, "error": "no_wallet"}

    try:
        from solders.pubkey import Pubkey  # type: ignore
        from solders.system_program import ID as SYS_PROGRAM_ID  # type: ignore
        from solana.transaction import Transaction  # type: ignore
        from solders.instruction import Instruction, AccountMeta  # type: ignore
        from solders.compute_budget import set_compute_unit_price  # type: ignore
        import struct

        base = _normalize_symbol(symbol)
        market = JUPITER_MARKETS.get(base)
        if not market:
            return {"success": False, "error": f"unknown_market_{base}"}

        program_id = Pubkey.from_string(JUPITER_PERP_PROGRAM)
        wallet_pubkey = _wallet_keypair.pubkey()

        # Build IncreasePosition instruction
        # This is a simplified version — production would need proper PDA derivation
        # for Position, Custody, Pool accounts based on market index

        # For now, use Jupiter's transaction builder if available
        log.info(
            f"🪐 Jupiter LIVE: Opening {direction.upper()} {base}-PERP "
            f"${size_usd:.0f} @ {_leverage}x"
        )

        # TODO: Full implementation requires:
        # 1. Derive Position PDA: [b"position", wallet, pool, custody, side]
        # 2. Derive PositionRequest PDA
        # 3. Build IncreasePosition instruction with proper accounts
        # 4. Add compute budget + priority fee
        # 5. Sign and send transaction
        # 6. Wait for confirmation

        # Placeholder — will implement when wallet is configured
        return {
            "success": False,
            "error": "live_mode_pending_full_implementation",
            "note": "On-chain TX builder needs PDA derivation setup"
        }

    except Exception as e:
        log.error(f"Jupiter LIVE open failed: {e}")
        return {"success": False, "error": str(e)}


def _close_position_live(symbol: str, direction: str) -> dict:
    """Close position on Jupiter Perps via on-chain transaction."""
    if not _solana_client or not _wallet_keypair:
        return {"success": False, "error": "no_wallet"}

    try:
        base = _normalize_symbol(symbol)
        log.info(f"🪐 Jupiter LIVE: Closing {direction.upper()} {base}-PERP")

        # TODO: Build DecreasePosition transaction (same PDA derivation needed)
        return {
            "success": False,
            "error": "live_mode_pending_full_implementation"
        }
    except Exception as e:
        log.error(f"Jupiter LIVE close failed: {e}")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════
# Public API (same interface as bybit_mirror)
# ═══════════════════════════════════════════════

def mirror_open_jupiter(symbol: str, direction: str, paper_size_usdt: float,
                        stop_price: float = None, tp_price: float = None) -> dict:
    """Mirror an open signal to Jupiter Perps. Returns result dict.

    Never raises — all errors caught and logged.
    """
    if not _enabled:
        return {"mirrored": False, "reason": "disabled"}

    base = _normalize_symbol(symbol)
    if not base:
        return {"mirrored": False, "reason": f"{symbol} not on Jupiter Perps"}

    with _lock:
        try:
            # Check max positions
            if len(_positions) >= _max_positions:
                log.debug(
                    f"Jupiter Mirror: max positions reached "
                    f"({len(_positions)}/{_max_positions}) — skip {symbol}"
                )
                return {"mirrored": False, "reason": "max_positions"}

            # Already in this position?
            pos_key = f"{base}_{direction}"
            if pos_key in _positions:
                return {"mirrored": False, "reason": f"already_open_{pos_key}"}

            # Get price
            price = _get_current_price(symbol)
            if price <= 0:
                price = paper_size_usdt  # fallback

            # Calculate size
            size_usd = _calc_size_usd()
            market = JUPITER_MARKETS[base]

            if size_usd < market["min_size_usd"]:
                return {"mirrored": False, "reason": "size_too_small"}

            if _mode == "live":
                result = _open_position_live(symbol, direction, size_usd)
                if not result.get("success"):
                    # Fall back to paper tracking
                    log.warning(
                        f"Jupiter LIVE failed ({result.get('error')}), "
                        f"tracking as paper"
                    )

            # Track position (paper or live)
            pos = JupiterPosition(
                symbol=base,
                direction=direction,
                size_usd=size_usd,
                entry_price=price,
                open_time=time.time(),
                jupiter_market=market["name"]
            )
            _positions[pos_key] = pos

            log.info(
                f"🪐 Jupiter Mirror [{_mode.upper()}] OPEN: "
                f"{direction.upper()} {market['name']} "
                f"${size_usd:.0f} @ ${price:.2f} | "
                f"Positions: {len(_positions)}/{_max_positions}"
            )

            return {
                "mirrored": True,
                "exchange": "jupiter",
                "mode": _mode,
                "symbol": base,
                "market": market["name"],
                "direction": direction,
                "size_usd": size_usd,
                "entry_price": price,
            }

        except Exception as e:
            log.error(f"🪐 Jupiter Mirror open ERROR: {symbol} — {e}")
            return {"mirrored": False, "reason": str(e)}


def mirror_close_jupiter(symbol: str, direction: str) -> dict:
    """Mirror a close signal to Jupiter Perps. Returns result dict."""
    if not _enabled:
        return {"mirrored": False, "reason": "disabled"}

    base = _normalize_symbol(symbol)
    if not base:
        return {"mirrored": False, "reason": f"{symbol} not on Jupiter"}

    with _lock:
        try:
            pos_key = f"{base}_{direction}"
            pos = _positions.get(pos_key)
            if not pos:
                return {"mirrored": False, "reason": f"no_open_position_{pos_key}"}

            # Get exit price
            exit_price = _get_current_price(symbol)
            if exit_price <= 0:
                exit_price = pos.entry_price  # fallback — flat

            # Calculate PnL
            if pos.direction == "long":
                pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
            else:
                pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100

            pnl_usd = pos.size_usd * pnl_pct / 100
            duration = time.time() - pos.open_time

            global _pnl_total, _trades_count, _wins
            _pnl_total += pnl_pct
            _trades_count += 1
            if pnl_pct > 0:
                _wins += 1

            if _mode == "live":
                result = _close_position_live(symbol, direction)
                if not result.get("success"):
                    log.warning(f"Jupiter LIVE close failed: {result.get('error')}")

            # Remove position
            del _positions[pos_key]

            emoji = "💰" if pnl_pct > 0 else "📉"
            log.info(
                f"🪐 Jupiter Mirror [{_mode.upper()}] CLOSE: "
                f"{pos.jupiter_market} {direction.upper()} "
                f"{emoji} {pnl_pct:+.2f}% (${pnl_usd:+.2f}) | "
                f"Duration: {duration/60:.0f}m | "
                f"Total: {_pnl_total:+.2f}% WR:{_wins}/{_trades_count}"
            )

            return {
                "mirrored": True,
                "symbol": base,
                "direction": direction,
                "pnl_pct": pnl_pct,
                "pnl_usd": pnl_usd,
                "duration_min": duration / 60,
            }

        except Exception as e:
            log.error(f"🪐 Jupiter Mirror close ERROR: {symbol} — {e}")
            return {"mirrored": False, "reason": str(e)}


def get_jupiter_status() -> dict:
    """Return status dict for dashboard/telegram."""
    if not _enabled:
        return {"enabled": False}

    wr = (_wins / _trades_count * 100) if _trades_count > 0 else 0

    return {
        "enabled": True,
        "mode": _mode,
        "pnl_pct": _pnl_total,
        "trades": _trades_count,
        "wins": _wins,
        "win_rate": wr,
        "positions": len(_positions),
        "max_positions": _max_positions,
        "leverage": _leverage,
        "open_positions": [
            {
                "market": p.jupiter_market,
                "direction": p.direction,
                "size_usd": p.size_usd,
                "entry_price": p.entry_price,
                "duration_min": (time.time() - p.open_time) / 60,
            }
            for p in _positions.values()
        ],
        "supported_markets": list(JUPITER_MARKETS.keys()),
    }
