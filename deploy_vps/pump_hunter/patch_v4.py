#!/usr/bin/env python3
"""Patch pump_scanner_v2.py to add v4 IIE strategy integration."""
import re

FILE = "/home/trader/pump-hunter/pump_scanner_v2.py"

with open(FILE, "r") as f:
    code = f.read()

# ── PATCH 1: Add iie_v4 import after volume_impulse import ──
old1 = "from volume_impulse import detect_multi_tf, manage_v3_position"
new1 = """from volume_impulse import detect_multi_tf, manage_v3_position

try:
    from iie_v4 import (
        get_iie_signals, mark_signal_done, record_v4_outcome,
        send_v4_close_notification,
    )
    _iie_available = True
except ImportError:
    _iie_available = False"""
code = code.replace(old1, new1, 1)

# ── PATCH 2: Add v4 entry TG notification function after tg_v3_entry ──
old2 = "\n\n# ─── v1 Trailing Stop Logic"
new2 = """

def tg_v4_entry(tg: TelegramBot, pos: Position, signal: dict):
    dir_emoji = "📈" if pos.direction == "long" else "📉"
    dir_label = "LONG" if pos.direction == "long" else "SHORT"
    tg.send(
        f"🧠 *V4 {dir_label}* — IIE Signal\\n"
        f"🪙 *{pos.symbol}* ({pos.exchange.upper()})\\n"
        f"{dir_emoji} Entry: `{pos.entry_price}`\\n"
        f"🛑 SL: `{pos.stop_loss:.8g}` (-{signal.get('sl_pct',1):.1f}%)\\n"
        f"🎯 TP target: +{signal.get('tp_pct',3):.1f}%\\n"
        f"📊 IIE Score: {signal.get('score',0):.0f} | "
        f"Conf: {signal.get('confidence',0):.0f}%\\n"
        f"⏱ Hold: {signal.get('hold_bars',10)} bars | "
        f"Size: {signal.get('size_mult',1):.1f}x\\n"
        f"🧭 Phase: {signal.get('market_phase','?')}"
    )


# ─── v1 Trailing Stop Logic"""
code = code.replace(old2, new2, 1)

# ── PATCH 3: Add v4 position management in _update_positions ──
# Find the v3 management block ending and add v4 after it
old3 = """        # ── v3: Volume-Impulse (LONG + SHORT) ─────────────"""
new3 = """        # ── v4: IIE-driven positions ───────────────────────
        if pos.strategy_version == "v4":
            # Simple trailing stop management using IIE parameters
            if pos.direction == "long":
                pos.pnl_pct = (price / pos.entry_price - 1) * 100
                if price > pos.peak_price:
                    pos.peak_price = price
                # Dynamic trailing: use IIE trail_pct from metadata
                trail_pct = getattr(pos, '_iie_trail_pct', V3_TRAIL_PCT)
                new_trail = pos.peak_price * (1 - trail_pct / 100)
                if new_trail > pos.trailing_stop:
                    pos.trailing_stop = new_trail
                # Check SL/trailing
                if price <= pos.stop_loss:
                    exit_r = "v4_stop_loss"
                elif price <= pos.trailing_stop and pos.pnl_pct > 0.5:
                    exit_r = "v4_trailing_stop"
                else:
                    exit_r = None
            else:  # short
                pos.pnl_pct = (pos.entry_price / price - 1) * 100
                if price < pos.dump_min or pos.dump_min == 0:
                    pos.dump_min = price
                trail_pct = getattr(pos, '_iie_trail_pct', V3_TRAIL_PCT)
                new_trail = pos.dump_min * (1 + trail_pct / 100)
                if pos.trailing_stop == 0 or new_trail < pos.trailing_stop:
                    pos.trailing_stop = new_trail
                if price >= pos.stop_loss:
                    exit_r = "v4_stop_loss"
                elif price >= pos.trailing_stop and pos.pnl_pct > 0.5:
                    exit_r = "v4_trailing_stop"
                else:
                    exit_r = None

            if exit_r:
                pos.exited = True
                pos.exit_reason = exit_r
                tg_exit(tg, pos, price, exit_r)
                _record_trade(completed, pos, price, balance)
                pnl_delta += _calc_balance_pnl(pos, balance)
                if executor:
                    close_fn = executor.close_position_verified
                    close_fn(pos.symbol, pos.direction)
                to_remove.append(key)
                # Record to IIE + send close notification
                if _iie_available:
                    try:
                        record_v4_outcome(
                            symbol=pos.symbol, exchange=pos.exchange,
                            direction=pos.direction,
                            entry_price=pos.entry_price, exit_price=price,
                            pnl_pct=pos.pnl_pct, exit_reason=exit_r,
                            entry_time=pos.detected_at, exit_time=time.time(),
                            impulse_id=getattr(pos, '_iie_impulse_id', None),
                        )
                        send_v4_close_notification(
                            symbol=pos.symbol, direction=pos.direction,
                            entry_price=pos.entry_price, exit_price=price,
                            pnl_pct=pos.pnl_pct, exit_reason=exit_r,
                            peak_price=pos.peak_price,
                        )
                    except Exception as e:
                        log.warning(f"v4 close report error: {e}")
            continue

        # ── v3: Volume-Impulse (LONG + SHORT) ─────────────"""
code = code.replace(old3, new3, 1)

# ── PATCH 4: Add v4 signal consumption in main loop after v3 block ──
# Find the line after v3 position opening block
old4 = """        _save_state(positions, completed, scan_count, start_ts, balance, executor)

        active = sum(1 for p in positions.values() if p.confirmed and not p.exited)
        v1_a = sum(1 for p in positions.values() if p.confirmed and not p.exited and p.strategy_version == "v1")
        v2_a = sum(1 for p in positions.values() if p.confirmed and not p.exited and p.strategy_version == "v2")
        v3_a = sum(1 for p in positions.values() if p.confirmed and not p.exited and p.strategy_version == "v3")
        log.info(f"Active: {active} (v1:{v1_a} v2:{v2_a} v3:{v3_a}) | "
                 f"Completed: {len(completed)} | Balance: ${balance:,.0f}")"""
new4 = """        # ── v4: IIE Signal Consumption ─────────────────────
        if _iie_available and active_count < MAX_CONCURRENT_POSITIONS:
            try:
                iie_signals = get_iie_signals()
                for sig in iie_signals:
                    sym = sig["symbol"]
                    exch = sig.get("exchange", "bybit")
                    direction = sig["direction"]
                    k_v4 = f"{sym}:{exch}:v4"
                    if direction == "short":
                        k_v4 += ":short"

                    if k_v4 in positions:
                        mark_signal_done(sig["id"])
                        continue

                    if active_count >= MAX_CONCURRENT_POSITIONS:
                        break

                    cur_price = _get_current_price(sym, exch) or sig.get("price", 0)
                    if cur_price <= 0:
                        mark_signal_done(sig["id"])
                        continue

                    # Calculate SL price from IIE recommendation
                    sl_pct = sig.get("sl_pct", 1.0)
                    size_mult = sig.get("size_mult", 1.0)
                    if direction == "long":
                        sl_price = cur_price * (1 - sl_pct / 100)
                    else:
                        sl_price = cur_price * (1 + sl_pct / 100)

                    pos_v4 = Position(
                        symbol=sym, exchange=exch,
                        strategy_version="v4",
                        pump_pct=0,
                        p_consolidation=sl_price,
                        detected_at=time.time(),
                    )
                    pos_v4.phase = 2
                    pos_v4.direction = direction
                    pos_v4.entry_price = cur_price
                    pos_v4.peak_price = cur_price
                    pos_v4.dump_min = cur_price if direction == "short" else 0
                    pos_v4.size_pct = V3_SIZE_PCT * size_mult
                    pos_v4.leverage = V3_LEVERAGE
                    pos_v4.stop_loss = sl_price
                    pos_v4.trailing_stop = sl_price
                    pos_v4.confirmed = True
                    # Store IIE metadata for position management
                    pos_v4._iie_trail_pct = sig.get("trail_pct", V3_TRAIL_PCT)
                    pos_v4._iie_impulse_id = sig.get("impulse_id")

                    positions[k_v4] = pos_v4
                    active_count += 1
                    mark_signal_done(sig["id"])
                    tg_v4_entry(tg, pos_v4, sig)
                    log.info(
                        f"🧠 V4 {direction.upper()} {sym} @ {cur_price} "
                        f"SL={sl_price:.8g} score={sig.get('score',0):.0f}"
                    )

                    if executor:
                        open_fn = executor.open_short if direction == "short" else executor.open_long
                        r = open_fn(
                            sym,
                            balance * pos_v4.size_pct / 100,
                            stop_price=sl_price,
                            leverage=V3_LEVERAGE,
                        )
                        if r.success and r.fill_price > 0:
                            pos_v4.entry_price = r.fill_price
            except Exception as e:
                log.warning(f"v4 signal processing error: {e}")

        _save_state(positions, completed, scan_count, start_ts, balance, executor)

        active = sum(1 for p in positions.values() if p.confirmed and not p.exited)
        v1_a = sum(1 for p in positions.values() if p.confirmed and not p.exited and p.strategy_version == "v1")
        v2_a = sum(1 for p in positions.values() if p.confirmed and not p.exited and p.strategy_version == "v2")
        v3_a = sum(1 for p in positions.values() if p.confirmed and not p.exited and p.strategy_version == "v3")
        v4_a = sum(1 for p in positions.values() if p.confirmed and not p.exited and p.strategy_version == "v4")
        log.info(f"Active: {active} (v1:{v1_a} v2:{v2_a} v3:{v3_a} v4:{v4_a}) | "
                 f"Completed: {len(completed)} | Balance: ${balance:,.0f}")"""
code = code.replace(old4, new4, 1)

# ── Write patched file ──
with open(FILE, "w") as f:
    f.write(code)

print("✅ pump_scanner_v2.py patched with v4 IIE strategy")
print(f"   Total lines: {len(code.splitlines())}")
