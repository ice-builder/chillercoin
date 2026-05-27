"""
Position Registry — Shared position ownership tracker for multi-bot environments.

Prevents two bots from opening positions on the same symbol on a shared
exchange account. Uses file-based JSON with fcntl locking for atomicity.

Usage:
    registry = PositionRegistry(bot_id="soldier")
    if registry.claim("BTCUSDT"):
        # Safe to open position
        ...
    registry.release("BTCUSDT")
"""

import json
import os
import fcntl
import logging
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict

log = logging.getLogger("registry")

# Default registry path (shared between bots)
DEFAULT_REGISTRY_PATH = os.getenv(
    "POSITION_REGISTRY_PATH",
    str(Path(__file__).parent / ".local_ai" / "position_registry.json")
)


class PositionRegistry:
    """
    File-based position ownership registry with atomic operations.
    
    Each bot identifies itself with a bot_id. Before opening a position
    on a symbol, it must claim() that symbol. If another bot already owns
    it, the claim is rejected. After closing, the bot must release().
    """

    def __init__(self, bot_id: str, registry_path: str = None):
        self.bot_id = bot_id
        self.path = Path(registry_path or DEFAULT_REGISTRY_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Initialize empty registry file if it doesn't exist
        if not self.path.exists():
            self._write({})
        log.info(f"📋 PositionRegistry: bot_id={bot_id}, path={self.path}")

    def _read(self) -> Dict:
        """Read registry with shared lock."""
        try:
            with open(self.path, 'r') as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    data = {}
                fcntl.flock(f, fcntl.LOCK_UN)
                return data
        except FileNotFoundError:
            return {}

    def _write(self, data: Dict):
        """Write registry with exclusive lock."""
        with open(self.path, 'w') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            json.dump(data, f, indent=2, default=str)
            fcntl.flock(f, fcntl.LOCK_UN)

    def _atomic_update(self, func) -> bool:
        """Perform an atomic read-modify-write with exclusive lock."""
        with open(self.path, 'a+') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                content = f.read()
                data = json.loads(content) if content.strip() else {}
                result = func(data)
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=2, default=str)
                return result
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def claim(self, symbol: str) -> bool:
        """
        Try to claim a symbol for this bot.
        
        Returns True if claimed successfully (or already owned by this bot).
        Returns False if another bot owns this symbol.
        """
        def _do_claim(data):
            existing = data.get(symbol)
            if existing:
                owner = existing.get("bot")
                if owner == self.bot_id:
                    log.debug(f"📋 {symbol}: already owned by {self.bot_id}")
                    return True
                else:
                    log.warning(f"📋 {symbol}: BLOCKED — owned by '{owner}' "
                                f"(since {existing.get('opened_at', '?')})")
                    return False
            # Claim it
            data[symbol] = {
                "bot": self.bot_id,
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "claimed_ts": time.time(),
            }
            log.info(f"📋 {symbol}: CLAIMED by {self.bot_id}")
            return True

        return self._atomic_update(_do_claim)

    def release(self, symbol: str) -> bool:
        """
        Release a symbol after closing position.
        
        Only the owning bot can release. Returns True if released.
        """
        def _do_release(data):
            existing = data.get(symbol)
            if not existing:
                log.debug(f"📋 {symbol}: not in registry, nothing to release")
                return True
            owner = existing.get("bot")
            if owner != self.bot_id:
                log.warning(f"📋 {symbol}: cannot release — owned by '{owner}', "
                            f"not '{self.bot_id}'")
                return False
            del data[symbol]
            log.info(f"📋 {symbol}: RELEASED by {self.bot_id}")
            return True

        return self._atomic_update(_do_release)

    def owner(self, symbol: str) -> Optional[str]:
        """Get the bot_id that owns this symbol, or None."""
        data = self._read()
        entry = data.get(symbol)
        return entry.get("bot") if entry else None

    def is_mine(self, symbol: str) -> bool:
        """Check if this bot owns the symbol."""
        return self.owner(symbol) == self.bot_id

    def my_positions(self) -> Dict:
        """Get all positions owned by this bot."""
        data = self._read()
        return {sym: info for sym, info in data.items()
                if info.get("bot") == self.bot_id}

    def all_positions(self) -> Dict:
        """Get entire registry (all bots)."""
        return self._read()

    def force_release(self, symbol: str) -> bool:
        """Force-release a symbol regardless of owner (for admin/cleanup)."""
        def _do_force(data):
            if symbol in data:
                owner = data[symbol].get("bot", "?")
                del data[symbol]
                log.warning(f"📋 {symbol}: FORCE-RELEASED (was owned by '{owner}')")
            return True
        return self._atomic_update(_do_force)

    def sync_with_exchange(self, exchange_symbols: list):
        """
        Cleanup: remove registry entries for symbols that no longer have
        open positions on the exchange. Call periodically to prevent stale entries.
        """
        def _do_sync(data):
            stale = [sym for sym in data if sym not in exchange_symbols]
            for sym in stale:
                owner = data[sym].get("bot", "?")
                log.info(f"📋 SYNC: releasing stale {sym} (was '{owner}')")
                del data[sym]
            if stale:
                log.info(f"📋 SYNC: cleaned {len(stale)} stale entries")
            return len(stale)

        return self._atomic_update(_do_sync)


# ─── CLI ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if len(sys.argv) < 2:
        print("Usage: python position_registry.py <command> [args]")
        print("Commands: list, claim <symbol> <bot_id>, release <symbol> <bot_id>, force-release <symbol>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "list":
        reg = PositionRegistry("cli")
        data = reg.all_positions()
        if not data:
            print("📋 Registry is empty")
        else:
            print(f"📋 Registry ({len(data)} positions):")
            for sym, info in data.items():
                print(f"  {sym}: {info['bot']} (since {info.get('opened_at', '?')})")

    elif cmd == "claim" and len(sys.argv) >= 4:
        reg = PositionRegistry(sys.argv[3])
        ok = reg.claim(sys.argv[2])
        print(f"{'✅ Claimed' if ok else '❌ Blocked'}: {sys.argv[2]} for {sys.argv[3]}")

    elif cmd == "release" and len(sys.argv) >= 4:
        reg = PositionRegistry(sys.argv[3])
        ok = reg.release(sys.argv[2])
        print(f"{'✅ Released' if ok else '❌ Failed'}: {sys.argv[2]}")

    elif cmd == "force-release" and len(sys.argv) >= 3:
        reg = PositionRegistry("admin")
        reg.force_release(sys.argv[2])
        print(f"✅ Force-released: {sys.argv[2]}")

    else:
        print(f"Unknown command: {cmd}")
"""
Usage:
  # List all positions in registry
  python position_registry.py list

  # Claim a symbol for a bot
  python position_registry.py claim BTCUSDT soldier

  # Release a symbol
  python position_registry.py release BTCUSDT soldier

  # Force-release (admin)
  python position_registry.py force-release BTCUSDT
"""
