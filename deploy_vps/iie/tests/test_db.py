#!/usr/bin/env python3
"""
Quick smoke test for IIE Phase 1+2.
Tests DB creation, CRUD operations, and impulse detection.
"""
import sys
import time
import tempfile
from pathlib import Path

# Add parent dirs
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from iie.impulse_db import ImpulseDB, Impulse, CoinProfile, MarketPhase, TradeOutcome


def test_db_operations():
    """Test all DB CRUD operations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = ImpulseDB(db_path)

        # ─── Test impulse insert/read ────────────
        imp = Impulse(
            symbol="BTCUSDT", exchange="bybit", timeframe="5",
            timestamp=time.time(), direction="long",
            vol_z=5.2, ret_z=3.8, combined_score=9.0,
            rsi_at_impulse=62.5, ema_deviation_pct=1.3,
            price_at_impulse=103500.0,
            candle_body_pct=75.0, wick_ratio_top=10.0, wick_ratio_bottom=15.0,
            impulse_location="mid_range", atr_at_impulse=850.0,
            source="collector",
        )
        imp_id = db.insert_impulse(imp)
        assert imp_id > 0, f"Expected positive ID, got {imp_id}"

        # Read back
        loaded = db.get_impulse(imp_id)
        assert loaded is not None
        assert loaded.symbol == "BTCUSDT"
        assert loaded.vol_z == 5.2
        assert loaded.direction == "long"
        print(f"  ✅ Impulse CRUD: OK (id={imp_id})")

        # ─── Test outcome auto-creation ──────────
        outcome = db.get_outcome(imp_id)
        assert outcome is not None, "Outcome should be auto-created"
        assert outcome["impulse_id"] == imp_id
        print(f"  ✅ Outcome auto-created: OK")

        # Update outcome
        db.update_outcome(imp_id, {
            "price_after_5m": 103600.0,
            "max_favorable_pct": 0.97,
            "was_stop_hunt": 0,
        })
        outcome2 = db.get_outcome(imp_id)
        assert outcome2["price_after_5m"] == 103600.0
        assert outcome2["max_favorable_pct"] == 0.97
        print(f"  ✅ Outcome update: OK")

        # ─── Test dedup ──────────────────────────
        assert db.has_recent_impulse("BTCUSDT", "bybit", "5", within_sec=300)
        assert not db.has_recent_impulse("ETHUSDT", "bybit", "5", within_sec=300)
        print(f"  ✅ Dedup check: OK")

        # ─── Test coin profile ───────────────────
        profile = CoinProfile(
            symbol="BTCUSDT",
            impulse_count=42,
            avg_continuation_pct=3.5,
            stop_hunt_frequency=22.0,
            impulse_quality_score=78.0,
            momentum_persistence=65.0,
            recommended_sl_mult=1.8,
            last_updated=time.time(),
        )
        db.upsert_coin_profile(profile)
        loaded_p = db.get_coin_profile("BTCUSDT")
        assert loaded_p is not None
        assert loaded_p.impulse_count == 42
        assert loaded_p.impulse_quality_score == 78.0
        print(f"  ✅ Coin profile: OK")

        # Upsert (update existing)
        profile.impulse_count = 43
        db.upsert_coin_profile(profile)
        loaded_p2 = db.get_coin_profile("BTCUSDT")
        assert loaded_p2.impulse_count == 43
        print(f"  ✅ Coin profile upsert: OK")

        # ─── Test market phase ───────────────────
        mp = MarketPhase(
            timestamp=time.time(),
            btc_price=103500.0, eth_price=2450.0,
            btc_monthly_change_pct=12.5, eth_monthly_change_pct=8.3,
            btc_weekly_change_pct=3.2,
            btc_ema_fast=103000.0, btc_ema_slow=101500.0,
            btc_atr_daily=850.0,
            phase="trending_up", alt_correlation=0.72,
        )
        mp_id = db.insert_market_phase(mp)
        assert mp_id > 0
        current = db.get_current_phase()
        assert current is not None
        assert current.phase == "trending_up"
        print(f"  ✅ Market phase: OK")

        # ─── Test trade outcome ──────────────────
        trade = TradeOutcome(
            symbol="SOLUSDT", exchange="bybit", direction="long",
            entry_price=175.5, exit_price=178.2, pnl_pct=1.54,
            exit_reason="take_profit", strategy_name="retest_5m",
            bot_name="soldier", entry_time=time.time() - 3600,
            exit_time=time.time(),
        )
        trade_id = db.insert_trade(trade)
        assert trade_id > 0
        trades = db.get_trades_by_symbol("SOLUSDT")
        assert len(trades) == 1
        assert trades[0]["pnl_pct"] == 1.54
        print(f"  ✅ Trade outcome: OK")

        # ─── Test stats ──────────────────────────
        stats = db.stats()
        assert stats["impulses"] == 1
        assert stats["coin_profiles"] == 1
        assert stats["market_phases"] == 1
        assert stats["trade_outcomes"] == 1
        print(f"  ✅ Stats: {stats}")

        # ─── Test recent impulses query ──────────
        recent = db.get_recent_impulses(hours=1)
        assert len(recent) == 1
        recent_btc = db.get_recent_impulses(symbol="BTCUSDT", hours=1)
        assert len(recent_btc) == 1
        print(f"  ✅ Recent impulses query: OK")

        print(f"\n🎉 All tests passed!")


if __name__ == "__main__":
    print("🧪 IIE Phase 1+2 Smoke Test\n")
    test_db_operations()
