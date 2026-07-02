"""
$CHILLER Vault Bridge — Connects Trading Bots ↔ Solana Vault

Uses direct JSON-RPC calls to Solana (no wrapper library version issues).

Usage:
  python3 vault_bridge.py status                    # Print vault state
  python3 vault_bridge.py log-trade --pair ETHUSDT --side LONG --entry 3500 --exit 3550 --pnl-bps 143 --pnl-usdt 71.50 --duration 3600
  python3 vault_bridge.py update-nav 600.00         # Set total assets = $600
  python3 vault_bridge.py pause                     # Emergency pause
  python3 vault_bridge.py unpause                   # Resume
  python3 vault_bridge.py json                      # JSON for dashboard
  python3 vault_bridge.py daemon                    # Run daemon loop
"""

import os
import sys
import stat
import json
import time
import struct
import hashlib
import logging
import argparse
import base64
import urllib.request
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, List

from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.system_program import ID as SYS_PROGRAM_ID
from solders.transaction import Transaction
from solders.instruction import Instruction, AccountMeta
from solders.message import Message
from solders.hash import Hash

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [VAULT] %(levelname)s %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("vault_bridge")

# ═══════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════

PROGRAM_ID = Pubkey.from_string("7ayYqgiiBtXdk13f9DBFTxJoYKkZyr3AaaLt2f2TPDoH")
TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
VAULT_SEED = b"vault"
MINT_SEED = b"chiller-mint"
NAV_UPDATE_INTERVAL = 3600
TRADE_LOG_FILE = "trade_log.jsonl"
TRADE_LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB max
TRADE_LOG_KEEP_LINES = 1000

CLUSTERS = {
    "localnet": "http://127.0.0.1:8899",
    "devnet": "https://api.devnet.solana.com",
    "mainnet": "https://api.mainnet-beta.solana.com",
}


def sighash(name: str) -> bytes:
    """Anchor discriminator: sha256('global:<name>')[:8]"""
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]


# ═══════════════════════════════════════════════
# Solana RPC (direct, sync, no wrapper)
# ═══════════════════════════════════════════════

class SolanaRPC:
    """Minimal sync Solana JSON-RPC client"""

    def __init__(self, url: str):
        self.url = url
        self._id = 0

    def _call(self, method: str, params: list = None) -> dict:
        self._id += 1
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
            "params": params or [],
        }).encode()
        req = urllib.request.Request(self.url, body, {"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read())
        if "error" in result:
            raise RuntimeError(f"RPC error: {result['error']}")
        return result["result"]

    def get_account_info(self, pubkey: str) -> Optional[bytes]:
        """Get account data as bytes"""
        result = self._call("getAccountInfo", [pubkey, {"encoding": "base64"}])
        if result["value"] is None:
            return None
        data_b64 = result["value"]["data"][0]
        return base64.b64decode(data_b64)

    def get_latest_blockhash(self) -> str:
        result = self._call("getLatestBlockhash")
        return result["value"]["blockhash"]

    def send_transaction(self, tx_bytes: bytes) -> str:
        tx_b64 = base64.b64encode(tx_bytes).decode()
        result = self._call("sendTransaction", [tx_b64, {"encoding": "base64"}])
        return result

    def get_balance(self, pubkey: str) -> int:
        result = self._call("getBalance", [pubkey])
        return result["value"]


# ═══════════════════════════════════════════════
# Vault State
# ═══════════════════════════════════════════════

@dataclass
class VaultState:
    authority: str
    usdt_mint: str
    chiller_mint: str
    vault_usdt_account: str
    team_wallet: str
    total_assets: int
    total_supply: int
    high_water_mark: int
    total_trades: int
    total_wins: int
    cumulative_pnl_bps: int
    performance_fee_bps: int
    management_fee_bps: int
    withdrawal_fee_bps: int
    min_deposit: int
    max_withdrawal_per_epoch: int
    epoch_withdrawals: int
    current_epoch: int
    last_nav_update: int
    is_paused: bool
    bump: int
    chiller_mint_bump: int

    @property
    def nav(self) -> float:
        if self.total_supply == 0:
            return 1.0
        return self.total_assets / self.total_supply

    @property
    def tvl(self) -> float:
        return self.total_assets / 1_000_000

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.total_wins / self.total_trades * 100

    def summary(self) -> str:
        return (
            f"\n🧊 $CHILLER Vault Status\n"
            f"{'═' * 42}\n"
            f"  NAV:           ${self.nav:.4f}\n"
            f"  TVL:           ${self.tvl:,.2f}\n"
            f"  Supply:        {self.total_supply / 1_000_000:,.2f} $CHILLER\n"
            f"  HWM:           ${self.high_water_mark / 1_000_000:,.2f}\n"
            f"  Trades:        {self.total_trades} ({self.win_rate:.1f}% win)\n"
            f"  Cum. PnL:      {self.cumulative_pnl_bps / 100:.2f}%\n"
            f"  Fees:          perf={self.performance_fee_bps/100:.0f}% | "
            f"mgmt={self.management_fee_bps/100:.1f}% | "
            f"wd={self.withdrawal_fee_bps/100:.1f}%\n"
            f"  Paused:        {'🔴 YES' if self.is_paused else '🟢 NO'}\n"
            f"  Last NAV:      {time.strftime('%Y-%m-%d %H:%M', time.gmtime(self.last_nav_update))} UTC\n"
            f"{'═' * 42}\n"
        )

    def to_json(self) -> dict:
        return {
            "nav": round(self.nav, 6),
            "tvl": round(self.tvl, 2),
            "total_supply": round(self.total_supply / 1_000_000, 2),
            "total_trades": self.total_trades,
            "total_wins": self.total_wins,
            "win_rate": round(self.win_rate, 1),
            "cumulative_pnl_pct": round(self.cumulative_pnl_bps / 100, 2),
            "high_water_mark": round(self.high_water_mark / 1_000_000, 2),
            "is_paused": self.is_paused,
            "last_nav_update": self.last_nav_update,
            "perf_fee_pct": self.performance_fee_bps / 100,
            "mgmt_fee_pct": self.management_fee_bps / 100,
            "wd_fee_pct": self.withdrawal_fee_bps / 100,
        }


def decode_vault_state(data: bytes) -> VaultState:
    """Decode VaultState from raw account data (skip 8-byte discriminator)"""
    d = data[8:]
    off = 0

    def pk():
        nonlocal off; p = str(Pubkey.from_bytes(d[off:off+32])); off += 32; return p
    def u64():
        nonlocal off; v = struct.unpack_from("<Q", d, off)[0]; off += 8; return v
    def i64():
        nonlocal off; v = struct.unpack_from("<q", d, off)[0]; off += 8; return v
    def u16():
        nonlocal off; v = struct.unpack_from("<H", d, off)[0]; off += 2; return v
    def b():
        nonlocal off; v = d[off] == 1; off += 1; return v
    def u8():
        nonlocal off; v = d[off]; off += 1; return v

    return VaultState(
        authority=pk(), usdt_mint=pk(), chiller_mint=pk(),
        vault_usdt_account=pk(), team_wallet=pk(),
        total_assets=u64(), total_supply=u64(), high_water_mark=u64(),
        total_trades=u64(), total_wins=u64(), cumulative_pnl_bps=i64(),
        performance_fee_bps=u16(), management_fee_bps=u16(), withdrawal_fee_bps=u16(),
        min_deposit=u64(), max_withdrawal_per_epoch=u64(),
        epoch_withdrawals=u64(), current_epoch=u64(), last_nav_update=i64(),
        is_paused=b(), bump=u8(), chiller_mint_bump=u8(),
    )


# ═══════════════════════════════════════════════
# Vault Bridge
# ═══════════════════════════════════════════════

class VaultBridge:

    def __init__(self, cluster: str = "localnet", keypair_path: str = None):
        self.cluster = cluster
        self.rpc = SolanaRPC(CLUSTERS.get(cluster, cluster))

        # Load keypair (with permission check)
        kp_path = keypair_path or os.path.expanduser("~/.config/solana/id.json")
        kp_stat = os.stat(kp_path)
        kp_mode = stat.S_IMODE(kp_stat.st_mode)
        if kp_mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH):
            log.warning(f"⚠️ Keypair {kp_path} has loose permissions ({oct(kp_mode)}). Fixing to 600...")
            os.chmod(kp_path, 0o600)
        with open(kp_path) as f:
            self.authority = Keypair.from_bytes(bytes(json.load(f)))
        log.info(f"Authority: {self.authority.pubkey()}")
        log.info(f"Cluster:   {cluster}")

        # PDAs
        self.vault_pda, _ = Pubkey.find_program_address([VAULT_SEED], PROGRAM_ID)
        self.mint_pda, _ = Pubkey.find_program_address([MINT_SEED], PROGRAM_ID)
        log.info(f"Vault PDA: {self.vault_pda}")

    # ─── Read ──────────────────────────────────

    def fetch_state(self) -> Optional[VaultState]:
        data = self.rpc.get_account_info(str(self.vault_pda))
        if data is None:
            return None
        return decode_vault_state(data)

    # ─── Send TX ───────────────────────────────

    def _send(self, ix: Instruction) -> str:
        bh = Hash.from_string(self.rpc.get_latest_blockhash())
        msg = Message.new_with_blockhash([ix], self.authority.pubkey(), bh)
        tx = Transaction.new_unsigned(msg)
        tx.sign([self.authority], bh)
        sig = self.rpc.send_transaction(bytes(tx))
        log.info(f"TX: {sig[:24]}...")
        return sig

    # ─── update_nav ────────────────────────────

    def update_nav(self, new_total_usdt: float) -> str:
        state = self.fetch_state()
        if not state:
            raise RuntimeError("Vault not initialized")

        old_nav = state.nav
        raw = int(new_total_usdt * 1_000_000)

        data = sighash("update_nav") + struct.pack("<Q", raw)
        keys = [
            AccountMeta(self.authority.pubkey(), is_signer=True, is_writable=True),
            AccountMeta(self.vault_pda, is_signer=False, is_writable=True),
            AccountMeta(Pubkey.from_string(state.vault_usdt_account), is_signer=False, is_writable=True),
            AccountMeta(Pubkey.from_string(state.team_wallet), is_signer=False, is_writable=True),
            AccountMeta(TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
        ]
        sig = self._send(Instruction(PROGRAM_ID, data, keys))

        new_state = self.fetch_state()
        new_nav = new_state.nav if new_state else 0
        delta = ((new_nav / old_nav) - 1) * 100 if old_nav else 0
        log.info(f"📊 NAV: ${old_nav:.4f} → ${new_nav:.4f} ({delta:+.2f}%) | TVL: ${new_total_usdt:,.2f}")
        return sig

    # ─── log_trade ─────────────────────────────

    def log_trade(self, pair: str, side: str, entry: float, exit: float,
                  pnl_bps: int, pnl_usdt: float, duration: int = 0) -> str:

        # Borsh strings: u32 len + bytes
        pair_b = pair.encode()
        side_b = side.encode()
        pair_buf = struct.pack("<I", len(pair_b)) + pair_b
        side_buf = struct.pack("<I", len(side_b)) + side_b
        nums = struct.pack("<QQiqQ",
            int(entry * 100), int(exit * 100),
            pnl_bps, int(pnl_usdt * 100), duration
        )

        data = sighash("log_trade") + pair_buf + side_buf + nums
        keys = [
            AccountMeta(self.authority.pubkey(), is_signer=True, is_writable=True),
            AccountMeta(self.vault_pda, is_signer=False, is_writable=True),
        ]
        sig = self._send(Instruction(PROGRAM_ID, data, keys))

        emoji = "🟢" if pnl_bps > 0 else "🔴"
        log.info(f"{emoji} {pair} {side} {pnl_bps/100:+.2f}% (${pnl_usdt:+.2f})")

        # Local backup (with rotation)
        with open(TRADE_LOG_FILE, "a") as f:
            f.write(json.dumps({
                "ts": int(time.time()), "pair": pair, "side": side,
                "entry": entry, "exit": exit, "pnl_bps": pnl_bps,
                "pnl_usdt": pnl_usdt, "duration": duration, "sig": sig
            }) + "\n")

        # Log rotation: keep last N lines if file grows too large
        log_path = Path(TRADE_LOG_FILE)
        if log_path.exists() and log_path.stat().st_size > TRADE_LOG_MAX_BYTES:
            lines = log_path.read_text().splitlines()
            log_path.write_text("\n".join(lines[-TRADE_LOG_KEEP_LINES:]) + "\n")
            log.info(f"📝 Trade log rotated: kept last {TRADE_LOG_KEEP_LINES} entries")

        return sig

    # ─── set_paused ────────────────────────────

    def set_paused(self, paused: bool) -> str:
        data = sighash("set_paused") + struct.pack("B", 1 if paused else 0)
        keys = [
            AccountMeta(self.authority.pubkey(), is_signer=True, is_writable=True),
            AccountMeta(self.vault_pda, is_signer=False, is_writable=True),
        ]
        sig = self._send(Instruction(PROGRAM_ID, data, keys))
        log.info(f"{'🔴 PAUSED' if paused else '🟢 UNPAUSED'}")
        return sig

    # ─── Exchange Integration ──────────────────

    def attach_exchange(self, exchange_type: str = "paper"):
        """Attach an exchange adapter for live balance & position tracking."""
        from exchange_adapter import PaperAdapter, DriftAdapter, BybitAdapter

        adapters = {
            "paper": PaperAdapter,
            "drift": DriftAdapter,
            "bybit": BybitAdapter,
        }

        adapter_cls = adapters.get(exchange_type)
        if not adapter_cls:
            raise ValueError(f"Unknown exchange: {exchange_type}. Use: {list(adapters.keys())}")

        config = {
            "rpc_url": CLUSTERS.get(self.cluster, self.cluster),
            "authority": str(self.authority.pubkey()),
            "api_key": os.getenv("EXCHANGE_API_KEY", ""),
            "api_secret": os.getenv("EXCHANGE_API_SECRET", ""),
            "testnet": os.getenv("EXCHANGE_TESTNET", "true").lower() == "true",
            "initial_balance": float(os.getenv("PAPER_BALANCE", "1000")),
        }
        self.exchange = adapter_cls(config)
        log.info(f"📡 Exchange: {self.exchange.name}")
        return self.exchange

    def get_exchange_total(self) -> float:
        """Get total assets from attached exchange"""
        if not hasattr(self, 'exchange') or self.exchange is None:
            log.warning("No exchange attached")
            return 0.0
        try:
            balance = self.exchange.get_balance()
            total = balance.get("total_usd", 0) or balance.get("equity", 0)
            log.info(f"💰 {self.exchange.name}: ${total:,.2f}")
            return total
        except Exception as e:
            log.error(f"Exchange balance error: {e}")
            return 0.0

    def sync_positions(self):
        """Sync open positions from exchange and log closed trades"""
        if not hasattr(self, 'exchange') or self.exchange is None:
            return

        try:
            positions = self.exchange.get_positions()
            closed = [p for p in positions if p.get("status") == "closed" and p.get("pnl_bps")]
            for p in closed:
                self.log_trade(
                    pair=p.get("symbol", "UNKNOWN"),
                    side=p.get("side", "LONG"),
                    entry=p.get("entry_price", 0),
                    exit=p.get("exit_price", 0),
                    pnl_bps=int(p.get("pnl_bps", 0)),
                    pnl_usdt=float(p.get("pnl_usd", 0)),
                    duration=int(p.get("duration", 0)),
                )
            if closed:
                log.info(f"📋 Synced {len(closed)} closed trades")
        except Exception as e:
            log.error(f"Position sync error: {e}")

    # ─── Daemon ────────────────────────────────

    def daemon(self, exchange_type: str = "paper"):
        """Main loop: update NAV every hour, sync positions"""
        log.info("🧊 Vault Bridge daemon starting...")

        self.attach_exchange(exchange_type)
        state = self.fetch_state()
        if state:
            print(state.summary())

        last_update = 0
        while True:
            try:
                now = time.time()

                # NAV update
                if now - last_update >= NAV_UPDATE_INTERVAL:
                    total = self.get_exchange_total()
                    if total > 0:
                        try:
                            self.update_nav(total)
                        except Exception as e:
                            log.error(f"NAV update: {e}")
                    last_update = now

                # Sync positions & trades
                self.sync_positions()

                # Trade signals from bots (file-based)
                self._process_signals()

                # Status log every 5 min
                if int(now) % 300 < 10:
                    s = self.fetch_state()
                    if s:
                        log.info(f"📊 NAV=${s.nav:.4f} TVL=${s.tvl:,.2f} Trades={s.total_trades}")

                time.sleep(10)
            except KeyboardInterrupt:
                log.info("Stopped")
                break
            except Exception as e:
                log.error(f"Error: {e}")
                time.sleep(30)

    def _process_signals(self):
        """Process trade signals from bots (via file)"""
        f = Path("trade_signals.json")
        if not f.exists():
            return
        try:
            signals = json.loads(f.read_text())
            if not signals:
                return
            for s in signals:
                self.log_trade(s["pair"], s["side"], s["entry"], s["exit"],
                             s["pnl_bps"], s["pnl_usdt"], s.get("duration", 0))
            f.write_text("[]")
        except Exception as e:
            log.error(f"Signals: {e}")


# ═══════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(description="$CHILLER Vault Bridge")
    p.add_argument("--cluster", default="localnet", choices=["localnet", "devnet", "mainnet"])
    p.add_argument("--keypair", default=None)
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("status")
    dm = sub.add_parser("daemon")
    dm.add_argument("--exchange", default="paper", choices=["paper", "drift", "bybit"],
                     help="Exchange adapter to use (default: paper)")
    sub.add_parser("json")
    sub.add_parser("pause")
    sub.add_parser("unpause")

    nav = sub.add_parser("update-nav")
    nav.add_argument("total", type=float)

    tr = sub.add_parser("log-trade")
    tr.add_argument("--pair", required=True)
    tr.add_argument("--side", required=True, choices=["LONG", "SHORT"])
    tr.add_argument("--entry", required=True, type=float)
    tr.add_argument("--exit", required=True, type=float)
    tr.add_argument("--pnl-bps", required=True, type=int)
    tr.add_argument("--pnl-usdt", required=True, type=float)
    tr.add_argument("--duration", default=0, type=int)

    args = p.parse_args()
    bridge = VaultBridge(args.cluster, args.keypair)

    if args.cmd == "status" or args.cmd is None:
        s = bridge.fetch_state()
        print(s.summary() if s else "❌ Vault not initialized")

    elif args.cmd == "json":
        s = bridge.fetch_state()
        print(json.dumps(s.to_json() if s else {"error": "not initialized"}, indent=2))

    elif args.cmd == "daemon":
        bridge.daemon(exchange_type=args.exchange)

    elif args.cmd == "update-nav":
        bridge.update_nav(args.total)

    elif args.cmd == "log-trade":
        bridge.log_trade(args.pair, args.side, args.entry, args.exit,
                        args.pnl_bps, args.pnl_usdt, args.duration)

    elif args.cmd == "pause":
        bridge.set_paused(True)

    elif args.cmd == "unpause":
        bridge.set_paused(False)


if __name__ == "__main__":
    main()
