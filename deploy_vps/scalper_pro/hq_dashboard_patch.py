"""
HQ Dashboard — Scalper Pro + IIE v2 Tabs Patch v2

Features added in v2:
  • Trade charts with TradingView Lightweight Charts (entry/exit markers)
  • Real-time unrealized PnL on open positions
  • Trade history with expandable charts
  • Balance tracking after trade closes
"""
import json
import time
import sqlite3
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ── Scalper Pro DB Access ─────────────────────────────────────────────────────

SP_DB_PATH = "/home/trader/scalper-pro/data/scalper_pro.db"
SP_STATE_PATH = "/home/trader/scalper-pro/data/scalper_pro_state.json"

def _sp_query(sql, params=()):
    """Query Scalper Pro SQLite database."""
    try:
        conn = sqlite3.connect(SP_DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []

def _sp_state():
    """Load Scalper Pro state file."""
    try:
        return json.loads(Path(SP_STATE_PATH).read_text())
    except Exception:
        return {}

def _get_price(symbol):
    """Get current price from Bybit."""
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear", "symbol": symbol}, timeout=5
        )
        data = r.json()
        if data.get("retCode") == 0 and data["result"]["list"]:
            return float(data["result"]["list"][0]["lastPrice"])
    except Exception:
        pass
    return 0


# ── Common HTML Helpers ───────────────────────────────────────────────────────

def _pnl_cls(v): return "pos" if v >= 0 else "neg"
def _pnl_badge(v): return f'<span class="badge {"badge-green" if v>0 else "badge-red"}">{v:+.3f}%</span>'
def _verified_badge(v): return '<span class="badge badge-green">✅ verified</span>' if v else '<span class="badge badge-red">⚠️ unverified</span>'

def _exit_reason_label(r):
    labels = {
        "take_profit": "🎯 TP",
        "iie_trailing_stop": "📊 Trail",
        "iie_stop_loss": "🛑 SL",
        "breakeven": "🔄 BE",
        "catastrophic_stop": "🚨 Hard",
        "confirm_stop": "📉 Confirm",
        "trail_stop": "📈 Trail",
        "protect_stop": "🛡️ Protect",
    }
    return labels.get(r, r)


# ── TradingView Lightweight Charts CDN ────────────────────────────────────────

LWCHART_CDN = '<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>'


# ══════════════════════════════════════════════════════════════════════════════
# SCALPER PRO PAGES
# ══════════════════════════════════════════════════════════════════════════════

def page_scalper_pro():
    """Scalper Pro main dashboard — balance, PnL, active positions with charts, trade history."""
    s = _sp_state()
    bal = s.get("balance", 5000)
    pnl = s.get("total_pnl_pct", 0)
    wins = s.get("wins", 0)
    losses = s.get("losses", 0)
    wr = wins / max(1, wins + losses) * 100
    active = s.get("active_positions", {})

    # Stats from DB
    stats = _sp_query("SELECT COUNT(*) as cnt FROM pro_trades")
    total_trades = stats[0]["cnt"] if stats else 0
    closed_trades = _sp_query("SELECT COUNT(*) as cnt FROM pro_trades WHERE status IN ('closed','analyzed')")
    n_closed = closed_trades[0]["cnt"] if closed_trades else 0
    hyp_stats = _sp_query("SELECT COUNT(*) as total, SUM(CASE WHEN is_mature=1 THEN 1 ELSE 0 END) as mature FROM hypotheses")
    hyp_total = hyp_stats[0]["total"] if hyp_stats else 0
    hyp_mature = hyp_stats[0]["mature"] if hyp_stats else 0
    pending_cp = _sp_query("SELECT COUNT(*) as cnt FROM trade_checkpoints WHERE completed=0")
    cp_pending = pending_cp[0]["cnt"] if pending_cp else 0

    # KPI cards
    cards = f'''
    <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:20px">
        <div class="card metric"><span class="label">💰 Баланс</span><span class="value">${bal:,.2f}</span></div>
        <div class="card metric"><span class="label">📈 PnL</span><span class="value {_pnl_cls(pnl)}">{pnl:+.3f}%</span></div>
        <div class="card metric"><span class="label">📊 Win Rate</span><span class="value">{wr:.0f}%</span><span class="label">W{wins}/L{losses}</span></div>
        <div class="card metric"><span class="label">🧠 Гипотезы</span><span class="value">{hyp_mature}/{hyp_total}</span><span class="label">зрелых</span></div>
        <div class="card metric"><span class="label">📋 Сделок</span><span class="value">{n_closed}</span><span class="label">чекпоинтов ожидают: {cp_pending}</span></div>
    </div>
    '''

    # ── Active positions with real-time PnL ──────────────────────────
    pos_rows = ""
    pos_charts_js = ""
    for i, (sym, pos) in enumerate(active.items()):
        entry = pos.get("entry_price", 0)
        direction = pos.get("direction", "?")
        score = pos.get("iie_score", 0)
        verified = pos.get("entry_verified", False)
        dir_icon = "🟢" if direction == "long" else "🔴"

        # Get current price for unrealized PnL
        current_price = _get_price(sym)
        if current_price and entry:
            if direction == "long":
                upnl = (current_price / entry - 1) * 100
            else:
                upnl = (entry / current_price - 1) * 100
        else:
            upnl = 0

        upnl_cls = "pos" if upnl >= 0 else "neg"
        chart_id = f"pos_chart_{i}"

        pos_rows += f'''<tr onclick="var w=document.getElementById('{chart_id}_wrap');w.style.display=w.style.display==='none'?'':'none'" style="cursor:pointer">
            <td>{dir_icon} <b>{sym}</b></td>
            <td>{direction.upper()}</td>
            <td>${entry:.6g}</td>
            <td>${current_price:.6g}</td>
            <td class="{upnl_cls}"><b>{upnl:+.2f}%</b></td>
            <td>{score:.0f}</td>
            <td>{_verified_badge(verified)}</td>
            <td>{pos.get("bars_held", 0)} bars</td>
        </tr>
        <tr id="{chart_id}_wrap" style="display:none"><td colspan="8" style="padding:0">
            <div id="{chart_id}" style="height:300px;width:100%"></div>
        </td></tr>'''

        # Prepare chart JS for each position
        stop = pos.get("stop_price", pos.get("current_stop", 0))
        tp = pos.get("tp_price", pos.get("take_profit", 0))
        pos_charts_js += f'''
        (function() {{
            const wrap = document.getElementById('{chart_id}_wrap');
            const el = document.getElementById('{chart_id}');
            if (!el || !wrap) return;
            const obs = new MutationObserver(function() {{
                if (wrap.style.display !== 'none' && !el.dataset.loaded) {{
                    el.dataset.loaded = '1';
                    loadPosChart('{chart_id}', '{sym}', {entry}, '{direction}', {stop or 0}, {tp or 0});
                }}
            }});
            obs.observe(wrap, {{attributes: true, attributeFilter: ['style']}});
        }})();
        '''

    pos_table = f'''
    <div class="card">
        <h3>🎯 Активные позиции ({len(active)}) <span style="font-size:12px;color:var(--dim)">— кликните для графика</span></h3>
        <table><thead><tr>
            <th>Монета</th><th>Dir</th><th>Вход</th><th>Текущая</th><th>uPnL</th><th>Score</th><th>Вериф.</th><th>Время</th>
        </tr></thead><tbody>{pos_rows if pos_rows else '<tr><td colspan="8" style="text-align:center;color:var(--dim)">Нет активных позиций</td></tr>'}</tbody></table>
    </div>
    '''

    # ── Trade History with expandable charts ──────────────────────────
    recent = _sp_query("""
        SELECT * FROM pro_trades
        WHERE status IN ('closed', 'analyzed')
        ORDER BY exit_time DESC LIMIT 20
    """)
    trade_rows = ""
    trade_charts_js = ""

    for t in recent:
        tid = t["id"]
        icon = "✅" if t["pnl_pct_after_commission"] > 0 else "❌"
        ver = _verified_badge(t.get("entry_verified", 0))
        reason = _exit_reason_label(t.get("exit_reason", ""))
        chart_id = f"trade_chart_{tid}"

        # Get checkpoints
        cps = _sp_query(
            "SELECT * FROM trade_checkpoints WHERE trade_id=? AND phase='after_open' AND completed=1 ORDER BY label",
            (tid,)
        )
        cp_cells = ""
        for label in ["15m", "1h", "4h"]:
            cp = next((c for c in cps if c["label"] == label), None)
            if cp:
                cp_icon = "📈" if cp["pnl_vs_entry"] > 0 else "📉"
                cp_cells += f'<td>{cp_icon} {cp["pnl_vs_entry"]:+.2f}%</td>'
            else:
                cp_cells += '<td style="color:var(--dim)">⏳</td>'

        # Format time
        try:
            exit_dt = datetime.fromtimestamp(t["exit_time"], tz=timezone.utc)
            time_str = exit_dt.strftime("%d.%m %H:%M")
        except Exception:
            time_str = "—"

        # Max favorable / adverse
        mfe = t.get("max_favorable_pct", 0)
        mae = t.get("max_adverse_pct", 0)

        trade_rows += f'''<tr onclick="var w=document.getElementById('{chart_id}_wrap');w.style.display=w.style.display==='none'?'':'none'" style="cursor:pointer">
            <td>{icon} <b>{t["symbol"]}</b></td>
            <td>{t["direction"].upper()}</td>
            <td>${t["entry_price"]:.6g}</td>
            <td>${t["exit_price"]:.6g}</td>
            <td>{_pnl_badge(t["pnl_pct_after_commission"])}</td>
            <td>{reason}</td>
            <td style="font-size:11px">{time_str}</td>
            <td style="color:var(--green);font-size:11px">+{mfe:.2f}%</td>
            <td style="color:var(--red);font-size:11px">-{mae:.2f}%</td>
            {cp_cells}
        </tr>
        <tr id="{chart_id}_wrap" style="display:none"><td colspan="12" style="padding:0">
            <div id="{chart_id}" style="height:350px;width:100%"></div>
        </td></tr>'''

        # Prepare chart JS
        entry_ts = int(t["entry_time"])
        exit_ts = int(t["exit_time"])
        stop_phase = t.get("stop_phase", "")

        trade_charts_js += f'''
        (function() {{
            const wrap = document.getElementById('{chart_id}_wrap');
            const el = document.getElementById('{chart_id}');
            if (!el || !wrap) return;
            const obs = new MutationObserver(function() {{
                if (wrap.style.display !== 'none' && !el.dataset.loaded) {{
                    el.dataset.loaded = '1';
                    loadTradeChart('{chart_id}', '{t["symbol"]}', {t["entry_price"]}, {t["exit_price"]}, '{t["direction"]}', {entry_ts}, {exit_ts}, '{t.get("exit_reason","")}', '{stop_phase}');
                }}
            }});
            obs.observe(wrap, {{attributes: true, attributeFilter: ['style']}});
        }})();
        '''

    trades_table = f'''
    <div class="card">
        <h3>📋 История сделок <span style="font-size:12px;color:var(--dim)">— кликните для графика с входом/выходом</span></h3>
        <table><thead><tr>
            <th>Монета</th><th>Dir</th><th>Вход</th><th>Выход</th><th>PnL</th><th>Причина</th><th>Время</th>
            <th>MFE</th><th>MAE</th><th>+15м</th><th>+1ч</th><th>+4ч</th>
        </tr></thead><tbody>{trade_rows if trade_rows else '<tr><td colspan="12" style="text-align:center;color:var(--dim)">Сделок пока нет</td></tr>'}</tbody></table>
    </div>
    '''

    # ── PnL curve ──
    daily = _sp_query("SELECT * FROM daily_metrics ORDER BY date DESC LIMIT 30")
    pnl_data = json.dumps([{"date": d["date"], "pnl": d["total_pnl"], "balance": d["balance"]} for d in reversed(daily)]) if daily else "[]"

    pnl_curve = f'''
    <div class="card">
        <h3>📈 PnL Curve & Баланс</h3>
        <canvas id="spPnlChart" height="200"></canvas>
        <script>
        (function() {{
            const data = {pnl_data};
            if (data.length === 0) return;
            const ctx = document.getElementById('spPnlChart').getContext('2d');
            const w = ctx.canvas.width, h = ctx.canvas.height;

            // Draw balance line
            const balances = data.map(d => d.balance);
            const bMax = Math.max(...balances), bMin = Math.min(...balances);
            const bRange = bMax - bMin || 1;
            ctx.strokeStyle = 'rgba(88,166,255,0.5)';
            ctx.lineWidth = 1;
            ctx.setLineDash([4,4]);
            ctx.beginPath();
            balances.forEach((v, i) => {{
                const x = (i / (balances.length - 1)) * w;
                const y = h - ((v - bMin) / bRange) * h * 0.7 - h * 0.15;
                i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
            }});
            ctx.stroke();
            ctx.setLineDash([]);

            // Draw PnL line
            const values = data.map(d => d.pnl);
            const maxV = Math.max(...values, 1), minV = Math.min(...values, -1);
            const range = maxV - minV || 1;
            ctx.strokeStyle = values[values.length-1] >= 0 ? '#3fb950' : '#f85149';
            ctx.lineWidth = 2;
            ctx.beginPath();
            values.forEach((v, i) => {{
                const x = (i / (values.length - 1)) * w;
                const y = h - ((v - minV) / range) * h * 0.7 - h * 0.15;
                i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
            }});
            ctx.stroke();

            // Zero line
            const zeroY = h - ((0 - minV) / range) * h * 0.7 - h * 0.15;
            ctx.strokeStyle = 'rgba(255,255,255,0.15)';
            ctx.setLineDash([4,4]);
            ctx.beginPath(); ctx.moveTo(0, zeroY); ctx.lineTo(w, zeroY); ctx.stroke();
            ctx.setLineDash([]);

            // Labels
            ctx.fillStyle = 'rgba(255,255,255,0.3)';
            ctx.font = '10px system-ui';
            if (data.length > 0) {{
                ctx.fillText(data[0].date, 4, h - 4);
                ctx.fillText(data[data.length-1].date, w - 60, h - 4);
                ctx.fillText('$' + balances[balances.length-1].toFixed(0), w - 60, 12);
            }}
        }})();
        </script>
    </div>
    '''

    # ── Chart loading JS ──
    chart_js = f'''
    {LWCHART_CDN}
    <script>
    function fetchKlines(symbol, startTs, endTs, interval) {{
        interval = interval || '5';
        const startMs = startTs * 1000;
        const endMs = endTs * 1000;
        return fetch('/api/sp/klines?symbol=' + symbol +
            '&interval=' + interval + '&start=' + startMs + '&end=' + endMs)
            .then(r => r.json())
            .then(d => {{
                if (d.retCode !== 0) return [];
                return d.result.list.map(k => ({{
                    time: parseInt(k[0]) / 1000,
                    open: parseFloat(k[1]),
                    high: parseFloat(k[2]),
                    low: parseFloat(k[3]),
                    close: parseFloat(k[4]),
                    volume: parseFloat(k[5])
                }})).reverse();
            }}).catch(() => []);
    }}

    function loadPosChart(containerId, symbol, entryPrice, direction, stop, tp) {{
        const now = Math.floor(Date.now() / 1000);
        const start = now - 4 * 3600;  // 4 hours back
        fetchKlines(symbol, start, now, '1').then(klines => {{
            if (!klines.length) return;
            const el = document.getElementById(containerId);
            el.innerHTML = '';
            const chart = LightweightCharts.createChart(el, {{
                width: el.clientWidth,
                height: 300,
                layout: {{ background: {{ color: '#0d1117' }}, textColor: '#8b949e' }},
                grid: {{ vertLines: {{ color: 'rgba(255,255,255,0.03)' }}, horzLines: {{ color: 'rgba(255,255,255,0.03)' }} }},
                crosshair: {{ mode: 0 }},
                timeScale: {{ timeVisible: true, secondsVisible: false }},
            }});
            const series = chart.addCandlestickSeries({{
                upColor: '#3fb950', downColor: '#f85149',
                borderUpColor: '#3fb950', borderDownColor: '#f85149',
                wickUpColor: '#3fb950', wickDownColor: '#f85149',
            }});
            series.setData(klines);

            // Entry line
            series.createPriceLine({{ price: entryPrice, color: '#58a6ff', lineWidth: 2, lineStyle: 0, title: '▶ Entry ' + entryPrice }});

            // Stop line
            if (stop) series.createPriceLine({{ price: stop, color: '#f85149', lineWidth: 1, lineStyle: 2, title: '🛑 Stop ' + stop.toFixed(6) }});

            // TP line
            if (tp) series.createPriceLine({{ price: tp, color: '#3fb950', lineWidth: 1, lineStyle: 2, title: '🎯 TP ' + tp.toFixed(6) }});

            chart.timeScale().fitContent();

            // Auto-refresh every 15s
            setInterval(() => {{
                const nowTs = Math.floor(Date.now() / 1000);
                fetchKlines(symbol, nowTs - 120, nowTs, '1').then(newK => {{
                    if (newK.length) series.update(newK[newK.length - 1]);
                }});
            }}, 15000);
        }});
    }}

    function loadTradeChart(containerId, symbol, entryPrice, exitPrice, direction, entryTs, exitTs, exitReason, stopPhase) {{
        const before = 30 * 60;
        const after = 30 * 60;
        const start = entryTs - before;
        const end = exitTs + after;
        fetchKlines(symbol, start, end, '5').then(klines => {{
            if (!klines.length) return;
            const el = document.getElementById(containerId);
            el.innerHTML = '';
            const chart = LightweightCharts.createChart(el, {{
                width: el.clientWidth,
                height: 350,
                layout: {{ background: {{ color: '#0d1117' }}, textColor: '#8b949e' }},
                grid: {{ vertLines: {{ color: 'rgba(255,255,255,0.03)' }}, horzLines: {{ color: 'rgba(255,255,255,0.03)' }} }},
                crosshair: {{ mode: 0 }},
                timeScale: {{ timeVisible: true, secondsVisible: false }},
            }});
            const series = chart.addCandlestickSeries({{
                upColor: '#3fb950', downColor: '#f85149',
                borderUpColor: '#3fb950', borderDownColor: '#f85149',
                wickUpColor: '#3fb950', wickDownColor: '#f85149',
            }});
            series.setData(klines);

            // Entry line
            series.createPriceLine({{ price: entryPrice, color: '#58a6ff', lineWidth: 2, lineStyle: 0, title: '▶ Entry ' + entryPrice }});

            // Exit line
            const exitColor = exitPrice > entryPrice === (direction === 'long') ? '#3fb950' : '#f85149';
            const reasonLabels = {{
                'take_profit': '🎯 TP', 'iie_trailing_stop': '📊 Trail',
                'iie_stop_loss': '🛑 SL', 'breakeven': '🔄 BE',
                'catastrophic_stop': '🚨 Hard', 'confirm_stop': '📉 Confirm',
                'trail_stop': '📈 Trail', 'protect_stop': '🛡️ Protect'
            }};
            const label = reasonLabels[exitReason] || exitReason;
            series.createPriceLine({{ price: exitPrice, color: exitColor, lineWidth: 2, lineStyle: 0, title: label + ' ' + exitPrice }});

            // Entry/Exit markers
            series.setMarkers([
                {{ time: entryTs, position: direction === 'long' ? 'belowBar' : 'aboveBar', color: '#58a6ff', shape: direction === 'long' ? 'arrowUp' : 'arrowDown', text: 'ENTRY' }},
                {{ time: exitTs, position: direction === 'long' ? 'aboveBar' : 'belowBar', color: exitColor, shape: 'circle', text: label }}
            ]);

            chart.timeScale().fitContent();
        }});
    }}

    {pos_charts_js}
    {trade_charts_js}
    </script>
    '''

    return cards + pos_table + trades_table + pnl_curve + chart_js


# ══════════════════════════════════════════════════════════════════════════════
# IIE v2 PAGES (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

def page_iie_v2():
    """IIE v2 overview — hypotheses, feedback loop progress, checkpoint analytics."""
    hyps = _sp_query("SELECT * FROM hypotheses ORDER BY sample_count DESC")
    mature = [h for h in hyps if h.get("is_mature")]

    avg_wr = sum(h["win_rate"] for h in mature) / len(mature) if mature else 0
    avg_pnl = sum(h["avg_pnl"] for h in mature) / len(mature) if mature else 0

    stats = _sp_query("SELECT COUNT(*) as total, SUM(CASE WHEN status='analyzed' THEN 1 ELSE 0 END) as analyzed FROM pro_trades")
    total_trades = stats[0]["total"] if stats else 0
    analyzed = stats[0]["analyzed"] if stats else 0

    cp_stats = _sp_query("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN completed=1 THEN 1 ELSE 0 END) as done,
            SUM(CASE WHEN completed=0 THEN 1 ELSE 0 END) as pending
        FROM trade_checkpoints
    """)
    cp_total = cp_stats[0]["total"] if cp_stats else 0
    cp_done = cp_stats[0]["done"] if cp_stats else 0
    cp_pending = cp_stats[0]["pending"] if cp_stats else 0

    cards = f'''
    <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:20px">
        <div class="card metric"><span class="label">🧠 Гипотезы</span><span class="value">{len(hyps)}</span><span class="label">зрелых: {len(mature)}</span></div>
        <div class="card metric"><span class="label">📊 Avg WR</span><span class="value {_pnl_cls(avg_wr - 50)}">{avg_wr:.1f}%</span></div>
        <div class="card metric"><span class="label">💰 Avg PnL</span><span class="value {_pnl_cls(avg_pnl)}">{avg_pnl:+.3f}%</span></div>
        <div class="card metric"><span class="label">📋 Сделок</span><span class="value">{total_trades}</span><span class="label">проанализировано: {analyzed}</span></div>
        <div class="card metric"><span class="label">✅ Чекпоинты</span><span class="value">{cp_done}/{cp_total}</span><span class="label">ожидают: {cp_pending}</span></div>
    </div>
    '''

    hyp_rows = ""
    for h in hyps[:30]:
        maturity = '<span class="badge badge-green">✅ mature</span>' if h["is_mature"] else '<span class="badge" style="background:var(--dim)">⏳ growing</span>'
        scale_in = "🔼" if h.get("should_scale_in") else "—"
        cut_early = "✂️" if h.get("should_cut_early") else "—"
        wr_cls = _pnl_cls(h["win_rate"] - 50)

        hyp_rows += f'''<tr>
            <td><b>{h["symbol"]}</b></td>
            <td>{h["direction"].upper()}</td>
            <td>{h["score_bin"]}</td>
            <td class="{wr_cls}">{h["win_rate"]:.0f}%</td>
            <td>{_pnl_badge(h["avg_pnl"])}</td>
            <td>{h["sample_count"]}</td>
            <td>SL={h["optimal_sl_pct"]:.2f}% TP={h["optimal_tp_pct"]:.2f}%</td>
            <td>{scale_in} {cut_early}</td>
            <td>{maturity}</td>
        </tr>'''

    hyp_table = f'''
    <div class="card">
        <h3>🧠 Гипотезы IIE v2</h3>
        <table><thead><tr>
            <th>Монета</th><th>Dir</th><th>Score Bin</th><th>WR</th><th>Avg PnL</th>
            <th>Samples</th><th>Оптимальные</th><th>Scale/Cut</th><th>Статус</th>
        </tr></thead><tbody>{hyp_rows if hyp_rows else '<tr><td colspan="9" style="text-align:center;color:var(--dim)">Гипотезы появятся после 10+ сделок</td></tr>'}</tbody></table>
    </div>
    '''

    cp_analysis = _sp_query("""
        SELECT label, COUNT(*) as cnt,
            AVG(CASE WHEN pnl_vs_entry > 0 THEN 1.0 ELSE 0.0 END) * 100 as pct_profitable,
            AVG(pnl_vs_entry) as avg_pnl
        FROM trade_checkpoints WHERE completed = 1 AND phase = 'after_open' GROUP BY label
    """)
    cp_rows = ""
    for cp in cp_analysis:
        cp_rows += f'''<tr>
            <td><b>+{cp["label"]}</b></td><td>{cp["cnt"]}</td>
            <td class="{_pnl_cls(cp['pct_profitable'] - 50)}">{cp["pct_profitable"]:.0f}%</td>
            <td>{_pnl_badge(cp["avg_pnl"])}</td>
        </tr>'''

    close_analysis = _sp_query("""
        SELECT label, COUNT(*) as cnt,
            AVG(CASE WHEN pnl_vs_exit > 0 THEN 1.0 ELSE 0.0 END) * 100 as pct_continued,
            AVG(pnl_vs_exit) as avg_pnl_after
        FROM trade_checkpoints WHERE completed = 1 AND phase = 'after_close' GROUP BY label
    """)
    close_rows = ""
    for cp in close_analysis:
        close_rows += f'''<tr>
            <td><b>+{cp["label"]}</b></td><td>{cp["cnt"]}</td>
            <td>{cp["pct_continued"]:.0f}%</td><td>{_pnl_badge(cp["avg_pnl_after"])}</td>
        </tr>'''

    checkpoint_card = f'''
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        <div class="card">
            <h3>📊 Чекпоинты после ОТКРЫТИЯ</h3>
            <p style="color:var(--dim);font-size:12px">% сделок в прибыли через N времени после входа</p>
            <table><thead><tr><th>Время</th><th>Сделок</th><th>% в +</th><th>Avg PnL</th></tr></thead>
            <tbody>{cp_rows if cp_rows else '<tr><td colspan="4" style="text-align:center;color:var(--dim)">Данные появятся после первых сделок</td></tr>'}</tbody></table>
        </div>
        <div class="card">
            <h3>📊 Чекпоинты после ЗАКРЫТИЯ</h3>
            <p style="color:var(--dim);font-size:12px">% сделок где цена продолжила движение в нашу сторону после выхода</p>
            <table><thead><tr><th>Время</th><th>Сделок</th><th>% продолжили</th><th>Avg +/- после</th></tr></thead>
            <tbody>{close_rows if close_rows else '<tr><td colspan="4" style="text-align:center;color:var(--dim)">Данные появятся после первых сделок</td></tr>'}</tbody></table>
        </div>
    </div>
    '''

    performers = ""
    if mature:
        best = sorted(mature, key=lambda h: h["avg_pnl"], reverse=True)[:5]
        worst = sorted(mature, key=lambda h: h["avg_pnl"])[:5]
        best_rows = "".join(f'<tr><td>🏆 {h["symbol"]} {h["direction"]}</td><td>{h["win_rate"]:.0f}%</td><td>{_pnl_badge(h["avg_pnl"])}</td><td>{h["sample_count"]}</td></tr>' for h in best)
        worst_rows = "".join(f'<tr><td>💀 {h["symbol"]} {h["direction"]}</td><td>{h["win_rate"]:.0f}%</td><td>{_pnl_badge(h["avg_pnl"])}</td><td>{h["sample_count"]}</td></tr>' for h in worst)
        performers = f'''
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
            <div class="card"><h3>🏆 Лучшие гипотезы</h3>
                <table><thead><tr><th>Гипотеза</th><th>WR</th><th>Avg PnL</th><th>N</th></tr></thead>
                <tbody>{best_rows}</tbody></table></div>
            <div class="card"><h3>💀 Худшие гипотезы</h3>
                <table><thead><tr><th>Гипотеза</th><th>WR</th><th>Avg PnL</th><th>N</th></tr></thead>
                <tbody>{worst_rows}</tbody></table></div>
        </div>'''

    return cards + hyp_table + checkpoint_card + performers


def page_compare():
    """Compare Scalper Pro vs Soldier performance."""
    try:
        soldier = json.loads(Path("/home/trader/soldier/paper_state_multi.json").read_text())
    except Exception:
        soldier = {}
    sp = _sp_state()

    s_wins = soldier.get("wins", 0)
    s_losses = soldier.get("losses", 0)
    s_pnl = soldier.get("total_pnl_pct", 0)
    s_wr = s_wins / max(1, s_wins + s_losses) * 100

    p_wins = sp.get("wins", 0)
    p_losses = sp.get("losses", 0)
    p_pnl = sp.get("total_pnl_pct", 0)
    p_wr = p_wins / max(1, p_wins + p_losses) * 100

    pnl_winner = "Scalper Pro" if p_pnl > s_pnl else "Soldier"
    wr_winner = "Scalper Pro" if p_wr > s_wr else "Soldier"

    return f'''
    <div class="card" style="text-align:center;padding:30px"><h2>⚔️ Сравнение стратегий</h2></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:16px">
        <div class="card" style="border-left:3px solid var(--accent)">
            <h3>⚔️ Soldier</h3>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px">
                <div class="metric"><span class="label">PnL</span><span class="value {_pnl_cls(s_pnl)}">{s_pnl:+.3f}%</span></div>
                <div class="metric"><span class="label">Win Rate</span><span class="value">{s_wr:.0f}%</span></div>
                <div class="metric"><span class="label">Wins</span><span class="value">{s_wins}</span></div>
                <div class="metric"><span class="label">Losses</span><span class="value">{s_losses}</span></div>
            </div>
        </div>
        <div class="card" style="border-left:3px solid var(--green)">
            <h3>🧪 Scalper Pro</h3>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px">
                <div class="metric"><span class="label">PnL</span><span class="value {_pnl_cls(p_pnl)}">{p_pnl:+.3f}%</span></div>
                <div class="metric"><span class="label">Win Rate</span><span class="value">{p_wr:.0f}%</span></div>
                <div class="metric"><span class="label">Wins</span><span class="value">{p_wins}</span></div>
                <div class="metric"><span class="label">Losses</span><span class="value">{p_losses}</span></div>
            </div>
        </div>
    </div>
    <div class="card" style="text-align:center;margin-top:16px;padding:20px">
        <h3>🏆 Результат</h3>
        <p>По PnL: <b>{pnl_winner}</b> ({max(s_pnl, p_pnl):+.3f}%)</p>
        <p>По Win Rate: <b>{wr_winner}</b> ({max(s_wr, p_wr):.0f}%)</p>
    </div>
    '''
