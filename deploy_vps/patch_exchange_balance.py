#!/usr/bin/env python3
"""
Patch paper_trader.py to use exchange balance as single source of truth.

Changes:
1. Position sizing uses exchange balance instead of virtual deposit
2. PnL display shows exchange-derived PnL
3. Heartbeat reports exchange balance
4. Drawdown check uses exchange-derived PnL
"""
import re

path = "/home/trader/soldier/paper_trader.py"

with open(path) as f:
    content = f.read()

patches_applied = 0

# ═══════════════════════════════════════════════════════════
# PATCH 1: After executor init, fetch initial exchange balance
#          and store as the "deposit" reference point
# ═══════════════════════════════════════════════════════════
# Find: ex_msg = f"⚡ Exchange connected: {executor} | Balance: ${info.get('balance_usdt', 0):.2f}"
# After this, add: storing initial balance

old_ex_init = '''            ex_msg = f"⚡ Exchange connected: {executor} | Balance: ${info.get('balance_usdt', 0):.2f}"
            logger.info(ex_msg); tg.send(ex_msg)'''

new_ex_init = '''            _ex_balance = info.get('balance_usdt', 0)
            ex_msg = f"⚡ Exchange connected: {executor} | Balance: ${_ex_balance:.2f}"
            logger.info(ex_msg); tg.send(ex_msg)
            # v9: Use exchange balance as single source of truth
            if _ex_balance > 0:
                deposit = _ex_balance
                state.deposit = deposit
                logger.info(f"💰 Using exchange balance as deposit: ${deposit:.2f}")'''

if old_ex_init in content:
    content = content.replace(old_ex_init, new_ex_init, 1)
    patches_applied += 1
    print(f"✅ PATCH 1: Exchange balance as deposit")
else:
    print(f"❌ PATCH 1 FAILED: pattern not found")

# ═══════════════════════════════════════════════════════════
# PATCH 2: Position sizing — use live exchange balance
#          There are 2 places: IIE signal handler + classic signal handler
# ═══════════════════════════════════════════════════════════

# 2a: IIE handler (~line 1225)
old_size_iie = '''                    risk_usdt = deposit * float(params["account_risk_pct"]) / 100.0
                    size = min(risk_usdt / (sl_pct / 100.0), deposit * 0.5)
                    size = size * size_mult'''

new_size_iie = '''                    # v9: Use live exchange balance for sizing
                    _live_bal = executor.get_balance() if executor else deposit
                    if _live_bal <= 0: _live_bal = deposit
                    risk_usdt = _live_bal * float(params["account_risk_pct"]) / 100.0
                    size = min(risk_usdt / (sl_pct / 100.0), _live_bal * 0.5)
                    size = size * size_mult'''

if old_size_iie in content:
    content = content.replace(old_size_iie, new_size_iie, 1)
    patches_applied += 1
    print(f"✅ PATCH 2a: IIE position sizing from exchange balance")
else:
    print(f"❌ PATCH 2a FAILED")

# 2b: Classic signal handler (~line 1481)
old_size_classic = '''                    risk_usdt = deposit * float(params["account_risk_pct"]) / 100.0
                    size = min(risk_usdt / (sig["stop_pct"] / 100.0), deposit * 0.5)'''

new_size_classic = '''                    # v9: Use live exchange balance for sizing
                    _live_bal = executor.get_balance() if executor else deposit
                    if _live_bal <= 0: _live_bal = deposit
                    risk_usdt = _live_bal * float(params["account_risk_pct"]) / 100.0
                    size = min(risk_usdt / (sig["stop_pct"] / 100.0), _live_bal * 0.5)'''

if old_size_classic in content:
    content = content.replace(old_size_classic, new_size_classic, 1)
    patches_applied += 1
    print(f"✅ PATCH 2b: Classic position sizing from exchange balance")
else:
    print(f"❌ PATCH 2b FAILED")

# ═══════════════════════════════════════════════════════════
# PATCH 3: Heartbeat — show exchange balance + exchange PnL
# ═══════════════════════════════════════════════════════════

old_heartbeat = '''            heartbeat_msg = (
                f"📡 *ПУЛЬС — Солдат активен*\\n"
                f"⏱ Аптайм: {uptime_h}ч {uptime_m}м\\n"
                f"📊 Сессия: П{state.wins}/У{state.losses} | Винрейт: {wr:.0f}%\\n"
                f"💰 PnL: {state.total_pnl_pct:+.3f}%\\n"'''

new_heartbeat = '''            # v9: Show exchange balance in heartbeat
            _hb_bal = executor.get_balance() if executor else deposit
            _hb_pnl = (_hb_bal / state.deposit - 1) * 100 if state.deposit > 0 and _hb_bal > 0 else state.total_pnl_pct
            heartbeat_msg = (
                f"📡 *ПУЛЬС — Солдат активен*\\n"
                f"⏱ Аптайм: {uptime_h}ч {uptime_m}м\\n"
                f"📊 Сессия: П{state.wins}/У{state.losses} | Винрейт: {wr:.0f}%\\n"
                f"💰 Баланс: ${_hb_bal:,.2f} | PnL: {_hb_pnl:+.2f}%\\n"'''

if old_heartbeat in content:
    content = content.replace(old_heartbeat, new_heartbeat, 1)
    patches_applied += 1
    print(f"✅ PATCH 3: Heartbeat with exchange balance")
else:
    print(f"❌ PATCH 3 FAILED")

# ═══════════════════════════════════════════════════════════
# PATCH 4: Tick log — show exchange balance
# ═══════════════════════════════════════════════════════════

old_tick_log = '''        logger.info(f"[{datetime.now().strftime(\'%H:%M:%S\')}] Active: {len(state.active_positions)} | PnL: {state.total_pnl_pct:+.3f}% | WR: {wr:.0f}%{strat_info}")'''

new_tick_log = '''        # v9: Log exchange balance
        _tick_bal = executor.get_balance() if executor else 0
        _tick_pnl = (_tick_bal / state.deposit - 1) * 100 if state.deposit > 0 and _tick_bal > 0 else state.total_pnl_pct
        logger.info(f"[{datetime.now().strftime(\'%H:%M:%S\')}] Active: {len(state.active_positions)} | Bal: ${_tick_bal:,.2f} | PnL: {_tick_pnl:+.2f}% | WR: {wr:.0f}%{strat_info}")'''

if old_tick_log in content:
    content = content.replace(old_tick_log, new_tick_log, 1)
    patches_applied += 1
    print(f"✅ PATCH 4: Tick log with exchange balance")
else:
    print(f"❌ PATCH 4 FAILED")

# ═══════════════════════════════════════════════════════════
# PATCH 5: Drawdown check — use exchange PnL instead of paper PnL
# ═══════════════════════════════════════════════════════════

old_dd = '''        if state.total_pnl_pct <= MAX_SESSION_LOSS_PCT and not kill_switch_path.exists():
            dd_msg = (
                f"🚨 *AUTO DRAWDOWN STOP*\\n"
                f"Session PnL reached `{state.total_pnl_pct:+.3f}%` "'''

new_dd = '''        # v9: Drawdown check from exchange balance
        _dd_bal = executor.get_balance() if executor else 0
        _dd_pnl = (_dd_bal / state.deposit - 1) * 100 if state.deposit > 0 and _dd_bal > 0 else state.total_pnl_pct
        if _dd_pnl <= MAX_SESSION_LOSS_PCT and not kill_switch_path.exists():
            dd_msg = (
                f"🚨 *AUTO DRAWDOWN STOP*\\n"
                f"Exchange PnL reached `{_dd_pnl:+.3f}%` (bal: ${_dd_bal:,.2f}) "'''

if old_dd in content:
    content = content.replace(old_dd, new_dd, 1)
    patches_applied += 1
    print(f"✅ PATCH 5: Drawdown from exchange PnL")
else:
    print(f"❌ PATCH 5 FAILED")

# ═══════════════════════════════════════════════════════════
# Save
# ═══════════════════════════════════════════════════════════

with open(path, "w") as f:
    f.write(content)

print(f"\n{'='*50}")
print(f"Applied {patches_applied}/6 patches")
if patches_applied >= 5:
    print("🎉 All critical patches applied!")
else:
    print("⚠️ Some patches failed — review manually")
