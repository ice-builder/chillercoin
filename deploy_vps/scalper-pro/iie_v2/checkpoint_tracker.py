"""
Scalper Pro — Checkpoint Tracker

Background tracker that monitors pending checkpoints and fills them
with verified prices when their target time arrives.

Runs as an async task inside the main bot loop.
"""
import asyncio
import time
import logging
from typing import Optional

import config
from price_verifier import get_verified_price, get_price_fast
from iie_v2.database import ScalperProDB

logger = logging.getLogger("scalper.checkpoints")


class CheckpointTracker:
    """
    Background service that processes pending trade checkpoints.
    Called periodically from the main bot loop.
    """

    def __init__(self, db: ScalperProDB):
        self.db = db
        self._last_run = 0
        self._run_interval = 30  # Check every 30 seconds

    def should_run(self) -> bool:
        """Check if it's time to process checkpoints."""
        return time.time() - self._last_run >= self._run_interval

    def process_pending(self) -> int:
        """
        Process all pending checkpoints whose target time has passed.
        Returns number of checkpoints completed.
        """
        self._last_run = time.time()
        pending = self.db.get_pending_checkpoints()

        if not pending:
            return 0

        completed = 0
        for cp in pending:
            try:
                result = self._process_checkpoint(cp)
                if result:
                    completed += 1
            except Exception as e:
                logger.error(f"Checkpoint {cp['id']} error: {e}")

        if completed > 0:
            logger.info(f"✅ Completed {completed}/{len(pending)} checkpoints")
        return completed

    def _process_checkpoint(self, cp: dict) -> bool:
        """Process a single pending checkpoint."""
        symbol = cp["symbol"]
        entry_price = cp["entry_price"]
        exit_price = cp.get("exit_price", 0)
        direction = cp["direction"]

        # Get verified price
        snap = get_verified_price(symbol)
        if snap.median_price <= 0:
            logger.warning(f"⚠️ No price for {symbol} checkpoint {cp['label']}")
            return False

        price = snap.median_price

        # Calculate PnL vs entry
        if direction == "long":
            pnl_vs_entry = (price / entry_price - 1) * 100
        else:
            pnl_vs_entry = (1 - price / entry_price) * 100

        # Calculate PnL vs exit (for after_close checkpoints)
        pnl_vs_exit = 0
        if cp["phase"] == "after_close" and exit_price > 0:
            if direction == "long":
                pnl_vs_exit = (price / exit_price - 1) * 100
            else:
                pnl_vs_exit = (1 - price / exit_price) * 100

        # Impulse check: is vol_z still elevated?
        # We approximate by checking if price moved further in favorable direction
        impulse_developing = pnl_vs_entry > 0

        self.db.complete_checkpoint(
            cp_id=cp["id"],
            price=price,
            pnl_vs_entry=round(pnl_vs_entry, 4),
            pnl_vs_exit=round(pnl_vs_exit, 4),
            impulse_developing=impulse_developing,
            vol_z=0,  # TODO: get real vol_z from IIE
            bybit=snap.bybit_price,
            binance=snap.binance_price,
            okx=snap.okx_price,
            verified=snap.is_verified,
        )

        icon = "📈" if pnl_vs_entry > 0 else "📉"
        logger.info(
            f"{icon} Checkpoint {cp['phase']}/{cp['label']} for {symbol}: "
            f"price=${price:.6g} pnl_entry={pnl_vs_entry:+.2f}% "
            f"pnl_exit={pnl_vs_exit:+.2f}% "
            f"verified={snap.is_verified}"
        )
        return True

    def get_trade_summary(self, trade_id: int) -> dict:
        """Get checkpoint summary for a trade (for reports)."""
        checkpoints = self.db.get_trade_checkpoints(trade_id)

        after_open = {}
        after_close = {}

        for cp in checkpoints:
            entry = {
                "label": cp["label"],
                "price": cp["price"],
                "pnl_vs_entry": cp["pnl_vs_entry"],
                "pnl_vs_exit": cp["pnl_vs_exit"],
                "impulse": bool(cp["impulse_developing"]),
                "verified": bool(cp["verified"]),
                "completed": bool(cp["completed"]),
            }
            if cp["phase"] == "after_open":
                after_open[cp["label"]] = entry
            else:
                after_close[cp["label"]] = entry

        return {
            "trade_id": trade_id,
            "after_open": after_open,
            "after_close": after_close,
            "all_completed": all(cp["completed"] for cp in checkpoints),
        }
