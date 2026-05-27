"""
HQ Dashboard — Scalper Pro + IIE v2 Tabs Patch

This file adds two new sections to the HQ Dashboard:
  1. Scalper Pro tab (positions, trades, checkpoints, PnL curve)
  2. IIE v2 tab (hypotheses, feedback loop progress, checkpoint analytics)

Apply by importing and calling patch_monitor() AFTER the main monitor.py loads.
"""
import json
import time
import sqlite3
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


# ── Common HTML Helpers ───────────────────────────────────────────────────────

def _pnl_cls(v): return "pos" if v >= 0 else "neg"
def _pnl_badge(v): return f'<span class="badge {"badge-green" if v>0 else "badge-red"}">{v:+.3f}%</span>'
def _verified_badge(v): return '<span class="badge badge-green">✅ verified</span>' if v else '<span class="badge badge-red">⚠️ unverified</span>'


# ══════════════════════════════════════════════════════════════════════════════
# SCALPER PRO PAGES
# ══════════════════════════════════════════════════════════════════════════════

def page_scalper_pro():
    """Scalper Pro main dashboard — balance, PnL, active positions, checkpoints."""
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
        <div class="card metric"><span class="label">📋 Чекпоинты</span><span class="value">{cp_pending}</span><span class="label">ожидают</span></div>
    </div>
    '''

    # Active positions table
    pos_rows = ""
    for sym, pos in active.items():
        entry = pos.get("entry_price", 0)
        direction = pos.get("direction", "?")
        score = pos.get("iie_score", 0)
        verified = pos.get("entry_verified", False)
        dir_icon = "🟢" if direction == "long" else "🔴"
        pos_rows += f'''<tr>
            <td>{dir_icon} <b>{sym}</b></td>
            <td>{direction.upper()}</td>
            <td>${entry:.6g}</td>
            <td>{score:.0f}</td>
            <td>{_verified_badge(verified)}</td>
            <td>{pos.get("bars_held", 0)} bars</td>
        </tr>'''

    pos_table = f'''
    <div class="card">
        <h3>🎯 Активные позиции ({len(active)})</h3>
        <table><thead><tr>
            <th>Монета</th><th>Направление</th><th>Вход</th><th>Score</th><th>Верификация</th><th>Время</th>
        </tr></thead><tbody>{pos_rows if pos_rows else '<tr><td colspan="6" style="text-align:center;color:var(--dim)">Нет активных позиций</td></tr>'}</tbody></table>
    </div>
    '''

    # Recent trades with checkpoints
    recent = _sp_query("""
        SELECT * FROM pro_trades
        WHERE status IN ('closed', 'analyzed')
        ORDER BY exit_time DESC LIMIT 10
    """)
    trade_rows = ""
    for t in recent:
        icon = "✅" if t["pnl_pct_after_commission"] > 0 else "❌"
        ver = _verified_badge(t.get("entry_verified", 0))

        # Get checkpoints
        cps = _sp_query(
            "SELECT * FROM trade_checkpoints WHERE trade_id=? AND phase='after_open' AND completed=1 ORDER BY label",
            (t["id"],)
        )
        cp_cells = ""
        for label in ["15m", "1h", "4h"]:
            cp = next((c for c in cps if c["label"] == label), None)
            if cp:
                cp_icon = "📈" if cp["pnl_vs_entry"] > 0 else "📉"
                cp_cells += f'<td>{cp_icon} {cp["pnl_vs_entry"]:+.2f}%</td>'
            else:
                cp_cells += '<td style="color:var(--dim)">⏳</td>'

        trade_rows += f'''<tr>
            <td>{icon} {t["symbol"]}</td>
            <td>{t["direction"].upper()}</td>
            <td>{_pnl_badge(t["pnl_pct_after_commission"])}</td>
            <td>{t["exit_reason"]}</td>
            <td>{ver}</td>
            {cp_cells}
        </tr>'''

    trades_table = f'''
    <div class="card">
        <h3>📋 Последние сделки с чекпоинтами</h3>
        <table><thead><tr>
            <th>Монета</th><th>Dir</th><th>PnL</th><th>Причина</th><th>Вериф.</th>
            <th>+15м</th><th>+1ч</th><th>+4ч</th>
        </tr></thead><tbody>{trade_rows if trade_rows else '<tr><td colspan="8" style="text-align:center;color:var(--dim)">Сделок пока нет</td></tr>'}</tbody></table>
    </div>
    '''

    # PnL curve (daily metrics)
    daily = _sp_query("SELECT * FROM daily_metrics ORDER BY date DESC LIMIT 30")
    pnl_data = json.dumps([{"date": d["date"], "pnl": d["total_pnl"], "balance": d["balance"]} for d in reversed(daily)]) if daily else "[]"

    pnl_curve = f'''
    <div class="card">
        <h3>📈 PnL Curve</h3>
        <canvas id="spPnlChart" height="200"></canvas>
        <script>
        (function() {{
            const data = {pnl_data};
            if (data.length === 0) return;
            const ctx = document.getElementById('spPnlChart').getContext('2d');
            const labels = data.map(d => d.date);
            const values = data.map(d => d.pnl);
            // Simple line chart via canvas
            const w = ctx.canvas.width, h = ctx.canvas.height;
            const maxV = Math.max(...values, 1), minV = Math.min(...values, -1);
            const range = maxV - minV || 1;
            ctx.strokeStyle = values[values.length-1] >= 0 ? '#3fb950' : '#f85149';
            ctx.lineWidth = 2;
            ctx.beginPath();
            values.forEach((v, i) => {{
                const x = (i / (values.length - 1)) * w;
                const y = h - ((v - minV) / range) * h * 0.8 - h * 0.1;
                i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
            }});
            ctx.stroke();
            // Zero line
            const zeroY = h - ((0 - minV) / range) * h * 0.8 - h * 0.1;
            ctx.strokeStyle = 'rgba(255,255,255,0.15)';
            ctx.setLineDash([4,4]);
            ctx.beginPath(); ctx.moveTo(0, zeroY); ctx.lineTo(w, zeroY); ctx.stroke();
            ctx.setLineDash([]);
        }})();
        </script>
    </div>
    '''

    return cards + pos_table + trades_table + pnl_curve


# ══════════════════════════════════════════════════════════════════════════════
# IIE v2 PAGES
# ══════════════════════════════════════════════════════════════════════════════

def page_iie_v2():
    """IIE v2 overview — hypotheses, feedback loop progress, checkpoint analytics."""
    hyps = _sp_query("SELECT * FROM hypotheses ORDER BY sample_count DESC")
    mature = [h for h in hyps if h.get("is_mature")]

    # Summary stats
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

    # KPI cards
    cards = f'''
    <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:20px">
        <div class="card metric"><span class="label">🧠 Гипотезы</span><span class="value">{len(hyps)}</span><span class="label">зрелых: {len(mature)}</span></div>
        <div class="card metric"><span class="label">📊 Avg WR</span><span class="value {_pnl_cls(avg_wr - 50)}">{avg_wr:.1f}%</span></div>
        <div class="card metric"><span class="label">💰 Avg PnL</span><span class="value {_pnl_cls(avg_pnl)}">{avg_pnl:+.3f}%</span></div>
        <div class="card metric"><span class="label">📋 Сделок</span><span class="value">{total_trades}</span><span class="label">проанализировано: {analyzed}</span></div>
        <div class="card metric"><span class="label">✅ Чекпоинты</span><span class="value">{cp_done}/{cp_total}</span><span class="label">ожидают: {cp_pending}</span></div>
    </div>
    '''

    # Hypotheses table
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

    # Checkpoint analytics
    cp_analysis = _sp_query("""
        SELECT
            label,
            COUNT(*) as cnt,
            AVG(CASE WHEN pnl_vs_entry > 0 THEN 1.0 ELSE 0.0 END) * 100 as pct_profitable,
            AVG(pnl_vs_entry) as avg_pnl
        FROM trade_checkpoints
        WHERE completed = 1 AND phase = 'after_open'
        GROUP BY label
    """)
    cp_rows = ""
    for cp in cp_analysis:
        cp_rows += f'''<tr>
            <td><b>+{cp["label"]}</b></td>
            <td>{cp["cnt"]}</td>
            <td class="{_pnl_cls(cp['pct_profitable'] - 50)}">{cp["pct_profitable"]:.0f}%</td>
            <td>{_pnl_badge(cp["avg_pnl"])}</td>
        </tr>'''

    close_analysis = _sp_query("""
        SELECT
            label,
            COUNT(*) as cnt,
            AVG(CASE WHEN pnl_vs_exit > 0 THEN 1.0 ELSE 0.0 END) * 100 as pct_continued,
            AVG(pnl_vs_exit) as avg_pnl_after
        FROM trade_checkpoints
        WHERE completed = 1 AND phase = 'after_close'
        GROUP BY label
    """)
    close_rows = ""
    for cp in close_analysis:
        close_rows += f'''<tr>
            <td><b>+{cp["label"]}</b></td>
            <td>{cp["cnt"]}</td>
            <td>{cp["pct_continued"]:.0f}%</td>
            <td>{_pnl_badge(cp["avg_pnl_after"])}</td>
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

    # Top/Worst performers
    performers = ""
    if mature:
        best = sorted(mature, key=lambda h: h["avg_pnl"], reverse=True)[:5]
        worst = sorted(mature, key=lambda h: h["avg_pnl"])[:5]

        best_rows = ""
        for h in best:
            best_rows += f'<tr><td>🏆 {h["symbol"]} {h["direction"]}</td><td>{h["win_rate"]:.0f}%</td><td>{_pnl_badge(h["avg_pnl"])}</td><td>{h["sample_count"]}</td></tr>'

        worst_rows = ""
        for h in worst:
            worst_rows += f'<tr><td>💀 {h["symbol"]} {h["direction"]}</td><td>{h["win_rate"]:.0f}%</td><td>{_pnl_badge(h["avg_pnl"])}</td><td>{h["sample_count"]}</td></tr>'

        performers = f'''
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
            <div class="card">
                <h3>🏆 Лучшие гипотезы</h3>
                <table><thead><tr><th>Гипотеза</th><th>WR</th><th>Avg PnL</th><th>N</th></tr></thead>
                <tbody>{best_rows}</tbody></table>
            </div>
            <div class="card">
                <h3>💀 Худшие гипотезы</h3>
                <table><thead><tr><th>Гипотеза</th><th>WR</th><th>Avg PnL</th><th>N</th></tr></thead>
                <tbody>{worst_rows}</tbody></table>
            </div>
        </div>
        '''

    return cards + hyp_table + checkpoint_card + performers


def page_compare():
    """Compare Scalper Pro vs Soldier performance."""
    # Soldier state
    try:
        soldier = json.loads(Path("/home/trader/soldier/paper_state_multi.json").read_text())
    except Exception:
        soldier = {}

    # Scalper Pro state
    sp = _sp_state()

    s_wins = soldier.get("wins", 0)
    s_losses = soldier.get("losses", 0)
    s_pnl = soldier.get("total_pnl_pct", 0)
    s_wr = s_wins / max(1, s_wins + s_losses) * 100

    p_wins = sp.get("wins", 0)
    p_losses = sp.get("losses", 0)
    p_pnl = sp.get("total_pnl_pct", 0)
    p_wr = p_wins / max(1, p_wins + p_losses) * 100

    # Who's better
    pnl_winner = "Scalper Pro" if p_pnl > s_pnl else "Soldier"
    wr_winner = "Scalper Pro" if p_wr > s_wr else "Soldier"

    return f'''
    <div class="card" style="text-align:center;padding:30px">
        <h2>⚔️ Сравнение стратегий</h2>
    </div>
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
