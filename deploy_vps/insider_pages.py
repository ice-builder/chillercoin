"""Insider Scanner Dashboard Pages — extracted from monitor.py to avoid freeze.
Provides page_insider() and page_insider_signals() for HQ Dashboard.
"""
import json, time, os
from pathlib import Path
from datetime import datetime, timezone

# ─── Shared imports from monitor ────────────────────
# These will be imported at module level; monitor.py must be importable
import monitor as _m

# Paths — check both insider_scanner and insider-scanner (VPS uses hyphen)
_insider_dir_env = os.getenv("INSIDER_SCANNER_DIR", "")
if _insider_dir_env:
    INSIDER_DIR = Path(_insider_dir_env)
else:
    _d1 = Path(__file__).parent / "insider_scanner"
    _d2 = Path(__file__).parent / "insider-scanner"
    # Also check sibling directories (dashboard may be in /soldier, scanner in /insider-scanner)
    _d3 = Path(__file__).parent.parent / "insider-scanner"
    _d4 = Path("/home/trader/insider-scanner")
    INSIDER_DIR = next((d for d in [_d1, _d2, _d3, _d4] if d.exists()), _d1)
INSIDER_OI_HISTORY = INSIDER_DIR / "oi_history.json"
INSIDER_STATE = INSIDER_DIR / "insider_positions.json"
INSIDER_TRADES = INSIDER_DIR / "insider_trades.json"


def _rj(p):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


def _ago(ts):
    """Human-readable time ago."""
    if not ts:
        return "—"
    try:
        diff = time.time() - float(ts)
    except (ValueError, TypeError):
        return "—"
    if diff < 60:
        return f"{diff:.0f}s ago"
    if diff < 3600:
        return f"{diff/60:.0f}m ago"
    if diff < 86400:
        return f"{diff/3600:.1f}h ago"
    return f"{diff/86400:.1f}d ago"


# ═══════════════════════════════════════════════════════
#  /insider — Main Insider Scanner Dashboard
# ═══════════════════════════════════════════════════════

def page_insider():
    oi_hist = _rj(INSIDER_OI_HISTORY)
    last_updated = oi_hist.get("last_updated", 0)
    snapshots = oi_hist.get("snapshots", {})

    # Count symbols tracked
    total_syms = sum(len(syms) for syms in snapshots.values())
    exchanges_active = [ex for ex in snapshots if snapshots[ex]]

    # Scanner status
    is_online = (time.time() - float(last_updated)) < 600 if last_updated else False
    status_badge = (
        '<span class="badge badge-green">\U0001f7e2 ONLINE</span>'
        if is_online else
        '<span class="badge badge-red">\U0001f534 OFFLINE</span>'
    )

    # ─── Status Card ────────────────────────────
    status_card = f'''<div class="grid">
    <div class="card metric"><span class="label">\U0001f575\ufe0f Scanner Status</span>
    <span class="value">{status_badge}</span>
    <span style="display:block;font-size:11px;color:var(--dim);margin-top:4px">Updated: {_ago(last_updated)}</span></div>
    <div class="card metric"><span class="label">\U0001f4ca Symbols Tracked</span>
    <span class="value">{total_syms}</span></div>
    <div class="card metric"><span class="label">\U0001f310 Exchanges</span>
    <span class="value">{len(exchanges_active)}</span>
    <span style="display:block;font-size:11px;color:var(--dim);margin-top:4px">{', '.join(e.upper() for e in exchanges_active)}</span></div>
    </div>'''

    # ─── Top OI Movers ──────────────────────────
    oi_rows = ""
    oi_movers = []
    now = time.time()
    t_1h = now - 3600

    for exchange, syms in snapshots.items():
        for symbol, entries in syms.items():
            if not entries or len(entries) < 2:
                continue
            current_val = entries[-1][1] if isinstance(entries[-1], list) else entries[-1]
            # Find ~1h ago value
            old_val = None
            for ts_val in entries:
                ts = ts_val[0] if isinstance(ts_val, list) else 0
                if ts <= t_1h + 300:
                    old_val = ts_val[1] if isinstance(ts_val, list) else ts_val
            if old_val and old_val > 0:
                change = ((current_val / old_val) - 1) * 100
                oi_movers.append((symbol, exchange, current_val, change))

    oi_movers.sort(key=lambda x: abs(x[3]), reverse=True)

    for sym, ex, oi_val, change in oi_movers[:20]:
        cls = "pos" if change >= 0 else "neg"
        oi_rows += f'''<tr>
        <td><strong>{sym}</strong></td>
        <td><span class="badge badge-blue">{ex.upper()}</span></td>
        <td>${oi_val:,.0f}</td>
        <td class="{cls}" style="font-weight:700">{change:+.1f}%</td>
        </tr>'''

    if not oi_rows:
        oi_rows = '<tr><td colspan="4" style="color:var(--dim)">No OI data yet — scanner needs to run first</td></tr>'

    oi_table = f'''<div class="card">
    <h3 style="margin-bottom:12px">\U0001f4ca Top OI Movers (1h)</h3>
    <table><thead><tr><th>Symbol</th><th>Exchange</th><th>OI $</th><th>1h Change</th></tr></thead>
    <tbody>{oi_rows}</tbody></table></div>'''

    # ─── Balance & Performance Card ──────────────
    insider_state = _rj(INSIDER_STATE)
    insider_positions = insider_state.get("active_positions", {})
    balance = insider_state.get("balance", 10000)
    initial_balance = 10000
    pnl_usd = balance - initial_balance

    # Load trade history
    trades = []
    if INSIDER_TRADES.exists():
        try:
            trades = json.loads(INSIDER_TRADES.read_text(encoding="utf-8"))
            if not isinstance(trades, list):
                trades = []
        except Exception:
            trades = []

    total_trades = len(trades)
    wins = sum(1 for t in trades if t.get("pnl_pct", 0) > 0)
    losses = total_trades - wins
    wr = wins / max(1, total_trades) * 100
    total_pnl_pct = sum(t.get("pnl_pct", 0) for t in trades)
    avg_win = sum(t.get("pnl_pct", 0) for t in trades if t.get("pnl_pct", 0) > 0) / max(1, wins)
    avg_loss = sum(t.get("pnl_pct", 0) for t in trades if t.get("pnl_pct", 0) <= 0) / max(1, losses)
    best_trade = max((t.get("pnl_pct", 0) for t in trades), default=0)
    worst_trade = min((t.get("pnl_pct", 0) for t in trades), default=0)

    pnl_cls = "pos" if pnl_usd >= 0 else "neg"
    balance_card = f'''<div class="grid" style="grid-template-columns:1fr 1fr 1fr 1fr">
    <div class="card metric"><span class="label">💰 Balance</span>
    <span class="value" style="font-size:28px">${balance:,.0f}</span></div>
    <div class="card metric"><span class="label">📈 PnL</span>
    <span class="value {pnl_cls}" style="font-size:28px">${pnl_usd:+,.0f}</span>
    <span style="display:block;font-size:12px;color:var(--dim)">{total_pnl_pct:+.1f}%</span></div>
    <div class="card metric"><span class="label">🎯 Win Rate</span>
    <span class="value" style="font-size:28px;color:{"var(--green)" if wr >= 50 else "var(--orange)"}">{wr:.0f}%</span>
    <span style="display:block;font-size:12px;color:var(--dim)">{wins}W / {losses}L</span></div>
    <div class="card metric"><span class="label">📊 Trades</span>
    <span class="value" style="font-size:28px">{total_trades}</span></div>
    </div>'''

    # Performance stats
    perf_card = f'''<div class="card">
    <h3 style="margin-bottom:12px">📊 Performance Stats</h3>
    <div class="param-grid">
    <div class="param-item"><span class="key">Avg Win</span><span class="val pos">{avg_win:+.2f}%</span></div>
    <div class="param-item"><span class="key">Avg Loss</span><span class="val neg">{avg_loss:+.2f}%</span></div>
    <div class="param-item"><span class="key">Best Trade</span><span class="val pos">{best_trade:+.2f}%</span></div>
    <div class="param-item"><span class="key">Worst Trade</span><span class="val neg">{worst_trade:+.2f}%</span></div>
    <div class="param-item"><span class="key">Avg Duration</span><span class="val">{sum(t.get("duration_min",0) for t in trades)/max(1,total_trades):.0f}min</span></div>
    <div class="param-item"><span class="key">Active Positions</span><span class="val">{len(insider_positions)}</span></div>
    </div></div>'''

    # ─── Active Positions ─────────────────────────
    pos_rows = ""
    total_upnl = 0.0
    for key, pos in insider_positions.items():
        sym = pos.get("symbol", key.split(":")[0])
        ex = pos.get("exchange", "?")
        entry = float(pos.get("entry_price", 0))
        cur = _m.fetch_price(sym, ex)
        if entry > 0 and cur > 0:
            upnl = ((cur / entry) - 1) * 100
        else:
            upnl = 0
        total_upnl += upnl
        cls = "pos" if upnl >= 0 else "neg"
        score = pos.get("insider_score", "?")
        size = float(pos.get("size_usdt", 0))
        lev = float(pos.get("leverage", 10))
        notional = size * lev
        upnl_d = notional * upnl / 100
        usd_str = f' <span style="font-size:11px">(${upnl_d:+,.0f})</span>' if upnl_d != 0 else ''
        entry_time = str(pos.get("entry_time", ""))[:16]
        # Score breakdown
        bd = pos.get("insider_breakdown", {})
        bd_str = " + ".join(f'{k}({v})' for k, v in bd.items() if v) if bd else "—"
        pos_rows += f'''<tr onclick="location='/insider/position/{key}'" style="cursor:pointer" title="Открыть график">
        <td><strong>{sym}</strong></td>
        <td><span class="badge badge-blue">{ex.upper()}</span></td>
        <td>${entry:.6g}</td><td>${cur:.6g}</td>
        <td class="{cls}" style="font-weight:700">{upnl:+.1f}%{usd_str}</td>
        <td>{score}</td>
        <td style="color:var(--dim);font-size:11px">{bd_str}</td>
        <td style="color:var(--dim)">{entry_time}</td></tr>'''

    if not pos_rows:
        pos_rows = '<tr><td colspan="8" style="color:var(--dim)">No insider positions active</td></tr>'

    upnl_cls = "pos" if total_upnl >= 0 else "neg"
    pos_card = f'''<div class="card">
    <h3 style="margin-bottom:12px">\U0001f4cd Insider Positions ({len(insider_positions)})
    · <span class="{upnl_cls}">uPnL: {total_upnl:+.1f}%</span></h3>
    <table><thead><tr><th>Symbol</th><th>Exchange</th><th>Entry</th><th>Now</th><th>uPnL</th><th>Score</th><th>Breakdown</th><th>Time</th></tr></thead>
    <tbody>{pos_rows}</tbody></table></div>'''

    # ─── Trade History ─────────────────────────────
    trade_rows = ""
    for t in reversed(trades):
        sym = t.get("symbol", "?")
        direction = t.get("direction", "?")
        entry_p = float(t.get("entry_price", 0))
        exit_p = float(t.get("exit_price", 0))
        pnl_t = t.get("pnl_pct", 0)
        pnl_d = t.get("pnl_usd", 0)
        reason = t.get("exit_reason", "?")
        score_t = t.get("insider_score", 0)
        dur = t.get("duration_min", 0)
        entry_time = str(t.get("entry_time", ""))[:16]
        exit_time = str(t.get("exit_time", ""))[:16]
        peak = float(t.get("peak_price", 0))

        cls = "pos" if pnl_t > 0 else "neg"
        icon = "\u2705" if pnl_t > 0 else "\u274c"
        dir_icon = "\u2B06\uFE0F" if direction == "long" else "\u2B07\uFE0F"

        # Reason badge
        reason_colors = {"hard_stop": "badge-red", "time_stop": "badge-orange", "trail_stop": "badge-green", "manual": "badge-blue"}
        reason_cls = reason_colors.get(reason, "badge-dim")

        # Score breakdown
        bd = t.get("insider_breakdown", {})
        tg_conf = "\U0001f4e1" if bd.get("tg_buying_confirmed") or bd.get("tg_activity_detected") else ""
        bd_parts = []
        for k, v in bd.items():
            if v:
                bd_parts.append(f"{k}({v})")
        bd_str = " ".join(bd_parts[:4]) if bd_parts else "—"

        # OI exchanges
        oi_ex = t.get("oi_exchanges", [])
        flow_ex = t.get("flow_exchanges", [])
        exchanges_str = f'OI: {",".join(e[:3].upper() for e in oi_ex)}' if oi_ex else ""
        if flow_ex:
            exchanges_str += f' Flow: {",".join(e[:3].upper() for e in flow_ex)}'

        trade_rows += f'''<tr onclick="location='/insider/trade/{len(trades)-1-trades.index(t)}'" style="cursor:pointer" title="Открыть график">
        <td>{icon}</td>
        <td><strong>{sym}</strong></td>
        <td>{dir_icon}</td>
        <td>${entry_p:.6g}</td><td>${exit_p:.6g}</td>
        <td class="{cls}" style="font-weight:700">{pnl_t:+.1f}%<span style="font-size:10px;color:var(--dim)"> (${pnl_d:+,.0f})</span></td>
        <td><span class="badge {reason_cls}">{reason}</span></td>
        <td>{score_t}{tg_conf}</td>
        <td style="font-size:11px;color:var(--dim)">{dur}m</td>
        <td style="font-size:10px;color:var(--dim)" title="{bd_str}">{exchanges_str}</td>
        <td style="font-size:11px;color:var(--dim)">{entry_time}</td></tr>'''

    if not trade_rows:
        trade_rows = '<tr><td colspan="11" style="color:var(--dim)">No trades yet</td></tr>'

    trade_card = f'''<div class="card">
    <h3 style="margin-bottom:12px">\U0001f4dc Trade History ({total_trades})</h3>
    <div style="overflow-x:auto"><table><thead><tr>
    <th></th><th>Symbol</th><th>Dir</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Reason</th><th>Score</th><th>Dur</th><th>Exchanges</th><th>Time</th>
    </tr></thead>
    <tbody>{trade_rows}</tbody></table></div></div>'''

    # ─── Exchange OI Coverage ───────────────────
    coverage_items = ""
    for ex in ["bybit", "binance", "bitget", "mexc", "gateio"]:
        syms = snapshots.get(ex, {})
        count = len(syms)
        color = "var(--green)" if count > 50 else "var(--orange)" if count > 0 else "var(--dim)"
        coverage_items += f'''<div class="param-item">
        <span class="key">{ex.upper()}</span>
        <span class="val" style="color:{color}">{count} symbols</span></div>'''

    coverage = f'''<div class="card">
    <h3 style="margin-bottom:12px">\U0001f310 Exchange Coverage</h3>
    <div class="param-grid">{coverage_items}</div></div>'''

    # ─── Config Summary ─────────────────────────
    config_card = '''<div class="card">
    <h3 style="margin-bottom:12px">\u2699\ufe0f Scanner Config</h3>
    <div class="param-grid">
    <div class="param-item"><span class="key">Scan Interval</span><span class="val">5 min</span></div>
    <div class="param-item"><span class="key">OI Threshold (1h)</span><span class="val">\u226510%</span></div>
    <div class="param-item"><span class="key">Z-Score Min</span><span class="val">\u22653.0</span></div>
    <div class="param-item"><span class="key">Alert Score</span><span class="val">\u226510</span></div>
    <div class="param-item"><span class="key">Auto-Enter Score</span><span class="val">\u226515</span></div>
    <div class="param-item"><span class="key">Position Size</span><span class="val">5% \u00d7 10x</span></div>
    <div class="param-item"><span class="key">Hard Stop</span><span class="val">-3%</span></div>
    <div class="param-item"><span class="key">Trail Stop</span><span class="val">+5% \u2192 3%</span></div>
    <div class="param-item"><span class="key">Time Stop</span><span class="val">4h flat</span></div>
    <div class="param-item"><span class="key">Max Positions</span><span class="val">3</span></div>
    <div class="param-item"><span class="key">TG Channels</span><span class="val">@cryptoarsenal, @cryptoattack24</span></div>
    <div class="param-item"><span class="key">TG Parse</span><span class="val">Every 2 min</span></div>
    </div></div>'''

    body = (balance_card + status_card + pos_card + perf_card + oi_table + trade_card
            + '<div class="grid" style="grid-template-columns:1fr 1fr">' + coverage + config_card + '</div>')
    return _m.layout("\U0001f575\ufe0f Insider Scanner", body, "/insider")


# ═══════════════════════════════════════════════════════
#  /insider/signals — Live Signals Feed
# ═══════════════════════════════════════════════════════

def page_insider_signals():
    oi_hist = _rj(INSIDER_OI_HISTORY)
    snapshots = oi_hist.get("snapshots", {})
    last_updated = oi_hist.get("last_updated", 0)

    now = time.time()
    t_1h = now - 3600
    t_4h = now - 14400

    # ─── OI Heatmap: exchange × symbol grid ────
    # Collect all symbols with significant changes
    all_changes = []
    for exchange, syms in snapshots.items():
        for symbol, entries in syms.items():
            if not entries or len(entries) < 2:
                continue
            current_val = entries[-1][1] if isinstance(entries[-1], list) else entries[-1]
            old_val_1h = None
            for ts_val in entries:
                ts = ts_val[0] if isinstance(ts_val, list) else 0
                if ts <= t_1h + 300:
                    old_val_1h = ts_val[1] if isinstance(ts_val, list) else ts_val
            if old_val_1h and old_val_1h > 0:
                change_1h = ((current_val / old_val_1h) - 1) * 100
            else:
                change_1h = 0

            # 4h change
            old_val_4h = None
            for ts_val in entries:
                ts = ts_val[0] if isinstance(ts_val, list) else 0
                if ts <= t_4h + 300:
                    old_val_4h = ts_val[1] if isinstance(ts_val, list) else ts_val
            change_4h = ((current_val / old_val_4h) - 1) * 100 if old_val_4h and old_val_4h > 0 else 0

            if abs(change_1h) >= 5 or abs(change_4h) >= 10:
                all_changes.append({
                    "symbol": symbol, "exchange": exchange,
                    "oi_usd": current_val, "change_1h": change_1h,
                    "change_4h": change_4h,
                })

    all_changes.sort(key=lambda x: abs(x["change_1h"]), reverse=True)

    # ─── Heatmap Table ──────────────────────────
    # Group by symbol, show per-exchange changes
    symbol_data = {}
    for c in all_changes:
        sym = c["symbol"]
        if sym not in symbol_data:
            symbol_data[sym] = {"exchanges": {}, "best_1h": 0}
        symbol_data[sym]["exchanges"][c["exchange"]] = c
        if abs(c["change_1h"]) > abs(symbol_data[sym]["best_1h"]):
            symbol_data[sym]["best_1h"] = c["change_1h"]

    # Sort by best 1h change
    sorted_syms = sorted(symbol_data.items(), key=lambda x: abs(x[1]["best_1h"]), reverse=True)

    exchanges_list = ["bybit", "binance", "bitget", "mexc", "gateio"]
    heatmap_header = "<th>Symbol</th>"
    for ex in exchanges_list:
        heatmap_header += f"<th>{ex.upper()[:3]}</th>"
    heatmap_header += "<th>Best 1h</th>"

    heatmap_rows = ""
    for sym, data in sorted_syms[:30]:
        row = f'<td><strong>{sym}</strong></td>'
        for ex in exchanges_list:
            if ex in data["exchanges"]:
                ch = data["exchanges"][ex]["change_1h"]
                # Color intensity based on change magnitude
                if ch >= 10:
                    bg = "rgba(63,185,80,.3)"
                elif ch >= 5:
                    bg = "rgba(63,185,80,.15)"
                elif ch <= -10:
                    bg = "rgba(248,81,73,.3)"
                elif ch <= -5:
                    bg = "rgba(248,81,73,.15)"
                else:
                    bg = "transparent"
                cls = "pos" if ch >= 0 else "neg"
                row += f'<td style="background:{bg};text-align:center" class="{cls}">{ch:+.0f}%</td>'
            else:
                row += '<td style="text-align:center;color:var(--dim)">—</td>'
        best = data["best_1h"]
        best_cls = "pos" if best >= 0 else "neg"
        row += f'<td class="{best_cls}" style="font-weight:700">{best:+.1f}%</td>'
        heatmap_rows += f"<tr>{row}</tr>"

    if not heatmap_rows:
        heatmap_rows = f'<tr><td colspan="{len(exchanges_list)+2}" style="color:var(--dim)">No significant OI changes detected yet</td></tr>'

    heatmap = f'''<div class="card">
    <h3 style="margin-bottom:12px">\U0001f5fa\ufe0f OI Heatmap — Cross-Exchange (1h changes)</h3>
    <div style="overflow-x:auto"><table><thead><tr>{heatmap_header}</tr></thead>
    <tbody>{heatmap_rows}</tbody></table></div>
    <p style="margin-top:8px;font-size:11px;color:var(--dim)">Shows symbols with \u22655% 1h or \u226510% 4h OI change. Green = OI increasing. Updated: {_ago(last_updated)}</p>
    </div>'''

    # ─── Multi-Exchange Confluence ───────────────
    # Symbols appearing on 2+ exchanges
    confluence_rows = ""
    confluence_count = 0
    for sym, data in sorted_syms:
        n_ex = len(data["exchanges"])
        if n_ex >= 2:
            confluence_count += 1
            ex_list = ", ".join(f'{ex.upper()} ({data["exchanges"][ex]["change_1h"]:+.0f}%)' for ex in sorted(data["exchanges"].keys()))
            best = data["best_1h"]
            cls = "pos" if best >= 0 else "neg"
            total_oi = sum(data["exchanges"][ex]["oi_usd"] for ex in data["exchanges"])
            confluence_rows += f'''<tr>
            <td><strong>{sym}</strong></td>
            <td><span class="badge badge-purple">{n_ex} exchanges</span></td>
            <td style="font-size:12px">{ex_list}</td>
            <td>${total_oi:,.0f}</td>
            <td class="{cls}" style="font-weight:700">{best:+.1f}%</td></tr>'''

    if not confluence_rows:
        confluence_rows = '<tr><td colspan="5" style="color:var(--dim)">No multi-exchange signals yet</td></tr>'

    confluence = f'''<div class="card">
    <h3 style="margin-bottom:12px">\U0001f4a1 Multi-Exchange Confluence ({confluence_count})</h3>
    <p style="color:var(--dim);font-size:12px;margin-bottom:8px">Symbols with OI surges on 2+ exchanges — highest insider probability</p>
    <table><thead><tr><th>Symbol</th><th>Coverage</th><th>Exchanges</th><th>Total OI</th><th>Best 1h</th></tr></thead>
    <tbody>{confluence_rows}</tbody></table></div>'''

    # ─── Signal Stats ───────────────────────────
    n_signals = len(all_changes)
    n_strong = sum(1 for c in all_changes if abs(c["change_1h"]) >= 10)
    n_multi = confluence_count

    stats = f'''<div class="grid">
    <div class="card metric"><span class="label">\U0001f4e1 Active Signals</span><span class="value">{n_signals}</span></div>
    <div class="card metric"><span class="label">\U0001f6a8 Strong (\u226510%)</span><span class="value" style="color:var(--orange)">{n_strong}</span></div>
    <div class="card metric"><span class="label">\U0001f4a1 Multi-Exchange</span><span class="value" style="color:var(--purple)">{n_multi}</span></div>
    <div class="card metric"><span class="label">\u23f1 Last Update</span><span class="value" style="font-size:16px">{_ago(last_updated)}</span></div>
    </div>'''

    body = stats + confluence + heatmap
    return _m.layout("\U0001f4e1 Live Signals", body, "/insider/signals")


# ═══════════════════════════════════════════════════════
#  /insider/trade/N — Trade Detail with TradingView Chart
# ═══════════════════════════════════════════════════════

def page_insider_trade(trade_idx: int):
    """Insider trade detail page with TradingView chart."""
    trades_data = _rj(INSIDER_TRADES)
    trades = trades_data if isinstance(trades_data, list) else trades_data.get("trades", [])

    if trade_idx < 0 or trade_idx >= len(trades):
        return _m.layout("Trade", '<div class="card"><p>Сделка не найдена.</p><a href="/insider">← Назад</a></div>', "/insider")

    t = trades[trade_idx]
    symbol = t.get("symbol", "?")
    direction = t.get("direction", "long")
    entry_price = float(t.get("entry_price", 0))
    exit_price = float(t.get("exit_price", 0))
    pnl_pct = t.get("pnl_pct", 0)
    pnl_usd = t.get("pnl_usd", 0)
    entry_time = str(t.get("entry_time", ""))[:19]
    exit_time = str(t.get("exit_time", ""))[:19]
    exit_reason = t.get("exit_reason", "?")
    score = t.get("insider_score", 0)
    dur = t.get("duration_min", 0)
    peak_price = float(t.get("peak_price", 0))
    oi_exchanges = t.get("oi_exchanges", [])
    flow_exchanges = t.get("flow_exchanges", [])
    breakdown = t.get("insider_breakdown", {})

    di_icon = "🟢 LONG" if direction == "long" else "🔴 SHORT"
    pnl_color = "var(--green)" if pnl_pct > 0 else "var(--red)"
    pnl_bg = "rgba(63,185,80,.15)" if pnl_pct > 0 else "rgba(248,81,73,.15)"

    # TradingView symbol — try Bybit first, fallback by exchange
    oi_ex_list = oi_exchanges or ["bybit"]
    tv_map = {"bybit": "BYBIT", "binance": "BINANCE", "bitget": "BITGET", "mexc": "MEXC", "gateio": "GATEIO"}
    tv_exchange = tv_map.get(oi_ex_list[0], "BYBIT")
    tv_symbol = f"{tv_exchange}:{symbol}.P"

    # Price ladder
    prices = [p for p in [entry_price, exit_price, peak_price] if p > 0]
    if prices:
        p_min = min(prices) * 0.997
        p_max = max(prices) * 1.003
        p_range = p_max - p_min if p_max != p_min else 0.001
        def pct_pos(price):
            return max(0, min(100, (price - p_min) / p_range * 100))
        entry_y = pct_pos(entry_price)
        exit_y = pct_pos(exit_price) if exit_price else 50
        peak_y = pct_pos(peak_price) if peak_price else 100
    else:
        entry_y = exit_y = peak_y = 50

    # Score breakdown
    bd_parts = [f"{k}({v})" for k, v in breakdown.items() if v]
    bd_str = " + ".join(bd_parts) if bd_parts else "—"

    # Duration display
    dur_h = dur // 60
    dur_m = dur % 60
    dur_str = f"{dur_h}ч {dur_m}м" if dur_h > 0 else f"{dur_m}м"

    # Reason badge
    reason_colors = {"hard_stop": "badge-red", "time_stop": "badge-orange",
                     "trail_stop": "badge-green", "manual": "badge-blue"}
    reason_cls = reason_colors.get(exit_reason, "badge-dim")

    body = f'''
    <style>
    .trade-ladder{{position:relative;width:60px;height:500px;background:linear-gradient(180deg,rgba(63,185,80,.05),rgba(248,81,73,.05));border-radius:8px;border:1px solid var(--border)}}
    .tl-line{{position:absolute;left:0;right:0;height:0;display:flex;align-items:center}}
    .tl-line .tl-tag{{position:absolute;right:-95px;white-space:nowrap;font-size:11px;font-weight:600;padding:2px 6px;border-radius:4px}}
    .tl-line::before{{content:'';position:absolute;left:0;right:0}}
    .tl-entry::before{{border-top:2px solid var(--accent)}}
    .tl-exit::before{{border-top:2px solid var(--orange)}}
    .tl-peak::before{{border-top:2px dashed var(--green)}}
    .result-badge{{display:inline-block;padding:8px 20px;border-radius:12px;font-size:24px;font-weight:700;margin:8px 0}}
    </style>

    <div style="margin-bottom:16px"><a href="/insider" style="font-size:14px">← Назад к Insider Scanner</a></div>

    <div class="grid" style="grid-template-columns:repeat(5,1fr);margin-bottom:16px">
    <div class="card metric"><span class="label">Результат</span><span class="value" style="color:{pnl_color}">{pnl_pct:+.1f}%</span></div>
    <div class="card metric"><span class="label">PnL $</span><span class="value" style="color:{pnl_color}">${pnl_usd:+,.0f}</span></div>
    <div class="card metric"><span class="label">Причина</span><span class="value" style="font-size:14px"><span class="badge {reason_cls}">{exit_reason}</span></span></div>
    <div class="card metric"><span class="label">Длительность</span><span class="value">{dur_str}</span></div>
    <div class="card metric"><span class="label">Скор</span><span class="value">{score}</span></div>
    </div>

    <div class="grid" style="grid-template-columns:80px 1fr 300px;gap:12px">

    <!-- Price Ladder -->
    <div class="card" style="padding:12px 8px;display:flex;flex-direction:column;align-items:center">
    <div style="font-size:11px;color:var(--dim);margin-bottom:8px;text-align:center">Уровни</div>
    <div class="trade-ladder">
    <div class="tl-line tl-peak" style="bottom:{peak_y}%"><span class="tl-tag" style="background:rgba(63,185,80,.15);color:var(--green)">ПИК {peak_price:.6g}</span></div>
    <div class="tl-line tl-entry" style="bottom:{entry_y}%"><span class="tl-tag" style="background:rgba(88,166,255,.15);color:var(--accent)">ВХОД {entry_price:.6g}</span></div>
    <div class="tl-line tl-exit" style="bottom:{exit_y}%"><span class="tl-tag" style="background:rgba(240,136,62,.2);color:var(--orange)">ВЫХОД {exit_price:.6g}</span></div>
    </div>
    </div>

    <!-- TradingView Chart -->
    <div class="card" style="padding:0;overflow:hidden;border-radius:12px;min-height:500px">
    <div id="tv_chart" style="height:500px"></div>
    <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
    <script type="text/javascript">
    new TradingView.widget({{"autosize":true,"symbol":"{tv_symbol}","interval":"5","timezone":"Etc/UTC",
    "theme":"dark","style":"1","locale":"ru","toolbar_bg":"#0a0e17","enable_publishing":false,
    "hide_side_toolbar":false,"allow_symbol_change":true,"container_id":"tv_chart",
    "studies":["Volume@tv-basicstudies"],
    "width":"100%","height":"500"}});
    </script>
    </div>

    <!-- Trade Details -->
    <div>
    <div class="card" style="margin-bottom:16px;text-align:center">
    <div class="result-badge" style="background:{pnl_bg};color:{pnl_color}">{pnl_pct:+.1f}%</div>
    <h3>{di_icon} {symbol}</h3>
    </div>

    <div class="card" style="margin-bottom:16px">
    <h3 style="margin-bottom:8px;color:var(--accent)">📥 Вход</h3>
    <div class="param-grid" style="grid-template-columns:1fr">
    <div class="param-item"><span class="key">Цена</span><span class="val" style="color:var(--accent)">{entry_price:.6g}</span></div>
    <div class="param-item"><span class="key">Время</span><span class="val" style="font-size:12px">{entry_time}</span></div>
    <div class="param-item"><span class="key">Направление</span><span class="val">{direction.upper()}</span></div>
    </div></div>

    <div class="card" style="margin-bottom:16px">
    <h3 style="margin-bottom:8px;color:var(--orange)">📤 Выход</h3>
    <div class="param-grid" style="grid-template-columns:1fr">
    <div class="param-item"><span class="key">Цена</span><span class="val" style="color:var(--orange)">{exit_price:.6g}</span></div>
    <div class="param-item"><span class="key">Время</span><span class="val" style="font-size:12px">{exit_time}</span></div>
    <div class="param-item"><span class="key">Причина</span><span class="val"><span class="badge {reason_cls}">{exit_reason}</span></span></div>
    <div class="param-item"><span class="key">Пик</span><span class="val" style="color:var(--green)">{peak_price:.6g}</span></div>
    </div></div>

    <div class="card">
    <h3 style="margin-bottom:8px;color:var(--purple)">📋 Breakdown</h3>
    <div style="font-size:11px;color:var(--dim);word-break:break-all">{bd_str}</div>
    <div style="margin-top:8px;font-size:12px">
    <div>OI: {", ".join(e.upper() for e in oi_exchanges) if oi_exchanges else "—"}</div>
    <div>Flow: {", ".join(e.upper() for e in flow_exchanges) if flow_exchanges else "—"}</div>
    </div></div>
    </div>

    </div>'''

    return _m.layout(f"📊 {symbol} — Insider Trade", body, "/insider")


# ═══════════════════════════════════════════════════════
#  /insider/position/KEY — Live Position with Chart
# ═══════════════════════════════════════════════════════

def page_insider_position(pos_key: str):
    """Live insider position detail with TradingView chart."""
    insider_state = _rj(INSIDER_STATE)
    positions = insider_state.get("active_positions", {})

    if pos_key not in positions:
        return _m.layout("Позиция", '<div class="card"><p>Позиция не найдена.</p><a href="/insider">← Назад</a></div>', "/insider")

    pos = positions[pos_key]
    symbol = pos.get("symbol", pos_key.split(":")[0])
    exchange = pos.get("exchange", "bybit")
    direction = pos.get("direction", "long")
    entry_price = float(pos.get("entry_price", 0))
    score = pos.get("insider_score", 0)
    size_usdt = float(pos.get("size_usdt", 0))
    leverage = float(pos.get("leverage", 10))
    entry_time = str(pos.get("entry_time", ""))[:19]
    breakdown = pos.get("insider_breakdown", {})
    oi_exchanges = pos.get("oi_exchanges", [])
    flow_exchanges = pos.get("flow_exchanges", [])

    # Live price
    cur_price = _m.fetch_price(symbol, exchange)
    if entry_price > 0 and cur_price > 0:
        if direction == "long":
            upnl_pct = ((cur_price / entry_price) - 1) * 100
        else:
            upnl_pct = ((entry_price / cur_price) - 1) * 100
    else:
        upnl_pct = 0

    notional = size_usdt * leverage
    upnl_usd = notional * upnl_pct / 100

    pnl_color = "var(--green)" if upnl_pct >= 0 else "var(--red)"
    pnl_bg = "rgba(63,185,80,.15)" if upnl_pct >= 0 else "rgba(248,81,73,.15)"
    di_icon = "🟢 LONG" if direction == "long" else "🔴 SHORT"

    # TradingView
    tv_map = {"bybit": "BYBIT", "binance": "BINANCE", "bitget": "BITGET", "mexc": "MEXC", "gateio": "GATEIO"}
    tv_exchange = tv_map.get(exchange, "BYBIT")
    tv_symbol = f"{tv_exchange}:{symbol}.P"

    # Hard stop & trail from config
    hard_stop_pct = 3.0
    trail_act_pct = 5.0
    if direction == "long":
        sl_price = entry_price * (1 - hard_stop_pct / 100)
        trail_price = entry_price * (1 + trail_act_pct / 100)
    else:
        sl_price = entry_price * (1 + hard_stop_pct / 100)
        trail_price = entry_price * (1 - trail_act_pct / 100)

    # Price ladder
    prices = [p for p in [sl_price, entry_price, cur_price, trail_price] if p > 0]
    p_min = min(prices) * 0.997
    p_max = max(prices) * 1.003
    p_range = p_max - p_min if p_max != p_min else 0.001
    def pct_pos(price):
        return max(0, min(100, (price - p_min) / p_range * 100))
    entry_y = pct_pos(entry_price)
    cur_y = pct_pos(cur_price)
    sl_y = pct_pos(sl_price)
    trail_y = pct_pos(trail_price)

    # Duration
    try:
        dur_sec = time.time() - float(pos.get("entry_ts", time.time()))
        dur_h = int(dur_sec // 3600)
        dur_m = int((dur_sec % 3600) // 60)
        dur_str = f"{dur_h}ч {dur_m}м" if dur_h > 0 else f"{dur_m}м"
    except Exception:
        dur_str = "—"

    # Breakdown
    bd_parts = [f"{k}({v})" for k, v in breakdown.items() if v]
    bd_str = " + ".join(bd_parts) if bd_parts else "—"

    body = f'''
    <meta http-equiv="refresh" content="30">
    <style>
    .trade-ladder{{position:relative;width:60px;height:500px;background:linear-gradient(180deg,rgba(63,185,80,.05),rgba(248,81,73,.05));border-radius:8px;border:1px solid var(--border)}}
    .tl-line{{position:absolute;left:0;right:0;height:0;display:flex;align-items:center}}
    .tl-line .tl-tag{{position:absolute;right:-105px;white-space:nowrap;font-size:11px;font-weight:600;padding:2px 6px;border-radius:4px}}
    .tl-line::before{{content:'';position:absolute;left:0;right:0}}
    .tl-entry::before{{border-top:2px solid var(--accent)}}
    .tl-cur::before{{border-top:2px solid var(--orange)}}
    .tl-stop::before{{border-top:2px solid var(--red)}}
    .tl-trail::before{{border-top:2px dashed var(--green)}}
    .result-badge{{display:inline-block;padding:8px 20px;border-radius:12px;font-size:24px;font-weight:700;margin:8px 0}}
    .live-dot{{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--green);margin-right:6px;animation:pulse 1.5s infinite}}
    @keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
    </style>

    <div style="margin-bottom:16px"><a href="/insider" style="font-size:14px">← Назад к Insider Scanner</a></div>

    <div class="grid" style="grid-template-columns:repeat(5,1fr);margin-bottom:16px">
    <div class="card metric"><span class="label"><span class="live-dot"></span>uPnL</span><span class="value" style="color:{pnl_color}">{upnl_pct:+.2f}%</span></div>
    <div class="card metric"><span class="label">PnL $</span><span class="value" style="color:{pnl_color}">${upnl_usd:+,.0f}</span></div>
    <div class="card metric"><span class="label">Скор</span><span class="value">{score}</span></div>
    <div class="card metric"><span class="label">Открыта</span><span class="value" style="font-size:18px">{dur_str}</span></div>
    <div class="card metric"><span class="label">Размер</span><span class="value" style="font-size:16px">${size_usdt:,.0f} × {leverage:.0f}x</span></div>
    </div>

    <div class="grid" style="grid-template-columns:80px 1fr 300px;gap:12px">

    <!-- Price Ladder -->
    <div class="card" style="padding:12px 8px;display:flex;flex-direction:column;align-items:center">
    <div style="font-size:11px;color:var(--dim);margin-bottom:8px;text-align:center">Уровни</div>
    <div class="trade-ladder">
    <div class="tl-line tl-trail" style="bottom:{trail_y}%"><span class="tl-tag" style="background:rgba(63,185,80,.15);color:var(--green)">ТРЕЙЛ {trail_price:.6g}</span></div>
    <div class="tl-line tl-entry" style="bottom:{entry_y}%"><span class="tl-tag" style="background:rgba(88,166,255,.15);color:var(--accent)">ВХОД {entry_price:.6g}</span></div>
    <div class="tl-line tl-cur" style="bottom:{cur_y}%"><span class="tl-tag" style="background:rgba(240,136,62,.2);color:var(--orange)">СЕЙЧАС {cur_price:.6g}</span></div>
    <div class="tl-line tl-stop" style="bottom:{sl_y}%"><span class="tl-tag" style="background:rgba(248,81,73,.15);color:var(--red)">СТОП {sl_price:.6g}</span></div>
    </div>
    </div>

    <!-- TradingView Chart -->
    <div class="card" style="padding:0;overflow:hidden;border-radius:12px;min-height:500px">
    <div id="tv_chart" style="height:500px"></div>
    <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
    <script type="text/javascript">
    new TradingView.widget({{"autosize":true,"symbol":"{tv_symbol}","interval":"5","timezone":"Etc/UTC",
    "theme":"dark","style":"1","locale":"ru","toolbar_bg":"#0a0e17","enable_publishing":false,
    "hide_side_toolbar":false,"allow_symbol_change":true,"container_id":"tv_chart",
    "studies":["Volume@tv-basicstudies"],
    "width":"100%","height":"500"}});
    </script>
    </div>

    <!-- Position Details -->
    <div>
    <div class="card" style="margin-bottom:16px;text-align:center">
    <div class="result-badge" style="background:{pnl_bg};color:{pnl_color}">{upnl_pct:+.2f}%</div>
    <h3>{di_icon} {symbol}</h3>
    <div style="font-size:12px;color:var(--dim);margin-top:4px"><span class="badge badge-blue">{exchange.upper()}</span></div>
    </div>

    <div class="card" style="margin-bottom:16px">
    <h3 style="margin-bottom:8px;color:var(--accent)">📥 Позиция</h3>
    <div class="param-grid" style="grid-template-columns:1fr">
    <div class="param-item"><span class="key">Вход</span><span class="val" style="color:var(--accent)">{entry_price:.6g}</span></div>
    <div class="param-item"><span class="key">Сейчас</span><span class="val" style="color:{pnl_color}">{cur_price:.6g}</span></div>
    <div class="param-item"><span class="key">Стоп (-{hard_stop_pct}%)</span><span class="val" style="color:var(--red)">{sl_price:.6g}</span></div>
    <div class="param-item"><span class="key">Трейл (+{trail_act_pct}%)</span><span class="val" style="color:var(--green)">{trail_price:.6g}</span></div>
    <div class="param-item"><span class="key">Открыта</span><span class="val">{entry_time}</span></div>
    </div></div>

    <div class="card">
    <h3 style="margin-bottom:8px;color:var(--purple)">📋 Breakdown</h3>
    <div style="font-size:11px;color:var(--dim);word-break:break-all">{bd_str}</div>
    <div style="margin-top:8px;font-size:12px">
    <div>OI: {", ".join(e.upper() for e in oi_exchanges) if oi_exchanges else "—"}</div>
    <div>Flow: {", ".join(e.upper() for e in flow_exchanges) if flow_exchanges else "—"}</div>
    </div></div>
    </div>

    </div>'''

    return _m.layout(f"📍 {symbol} — Live Position", body, "/insider")
