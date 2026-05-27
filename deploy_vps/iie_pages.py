"""
IIE Dashboard Pages for HQ Monitor

Provides:
  /iie              — Full engine overview (impulses, phase, outcomes, ML status)
  /iie/config       — View & edit all IIE config parameters per layer
  /iie/impulses     — Live impulse feed with filtering
  /iie/coins        — Coin profiles table
  /iie/api/config   — POST endpoint to update config values at runtime
"""
import json
import time
import sys
from pathlib import Path
from datetime import datetime, timezone

# IIE imports
sys.path.insert(0, str(Path(__file__).parent))
try:
    from iie.impulse_db import ImpulseDB
    from iie.report import generate_report
    from iie import config as iie_config
    _iie_db = ImpulseDB()
    _iie_available = True
except Exception as e:
    _iie_available = False
    _iie_db = None
    print(f"⚠️ IIE not available: {e}")


def _layout_ref():
    """Get layout/nav/pnl_cls from monitor module at runtime."""
    import monitor
    return monitor.layout, monitor.nav, monitor.pnl_cls, monitor.pnl_badge


# ─── /iie — Main Dashboard ──────────────────────────────────

def page_iie():
    layout, nav, pnl_cls, pnl_badge = _layout_ref()

    if not _iie_available:
        return layout("🧠 IIE Engine", '<div class="card"><p>IIE module not loaded.</p></div>', "/iie")

    report = generate_report(_iie_db)
    stats = report["db_stats"]
    mp = report.get("market_phase") or {}
    os_ = report.get("outcome_stats", {})
    tbb = report.get("trades_by_bot", {})

    # Phase badge
    phase = mp.get("phase", "unknown")
    phase_colors = {
        "trending_up": ("var(--green)", "rgba(63,185,80,.15)"),
        "trending_down": ("var(--red)", "rgba(248,81,73,.15)"),
        "sideways": ("var(--orange)", "rgba(240,136,62,.15)"),
        "volatile": ("var(--purple)", "rgba(188,140,255,.15)"),
    }
    pc, pbg = phase_colors.get(phase, ("var(--dim)", "rgba(139,148,158,.15)"))
    phase_badge = f'<span class="badge" style="background:{pbg};color:{pc};font-size:14px;padding:4px 12px">{phase.upper()}</span>'

    # ML model status
    ml_status = "⏳ Waiting for data"
    ml_badge_style = "background:rgba(139,148,158,.15);color:var(--dim)"
    ml_train_samples = 0
    next_retrain = "—"
    try:
        from iie.impulse_predictor import ImpulsePredictor
        predictor = ImpulsePredictor(_iie_db)
        ml_info = predictor.get_model_info()
        ml_train_samples = ml_info["train_samples"]
        if ml_info["trained"]:
            ml_status = f'✅ Trained'
            ml_badge_style = "background:rgba(63,185,80,.15);color:var(--green)"
            # Calculate next retrain time
            import time as _time
            last_t = ml_info.get("last_train", 0)
            if last_t > 0:
                next_ts = last_t + iie_config.PREDICTOR_RETRAIN_INTERVAL_SEC
                remaining = next_ts - _time.time()
                if remaining > 0:
                    hrs = int(remaining // 3600)
                    mins = int((remaining % 3600) // 60)
                    next_retrain = f"in {hrs}h {mins}m"
                else:
                    next_retrain = "pending"
        else:
            ml_status = f'⏳ Need {iie_config.PREDICTOR_MIN_SAMPLES}+ outcomes'
    except Exception:
        pass

    # Pending/complete outcomes
    pending = stats.get("post_impulse_outcomes", 0) - os_.get("completed", 0)

    # ML progress metrics
    ml_outcomes = os_.get("completed", 0)
    ml_min = iie_config.PREDICTOR_MIN_SAMPLES
    ml_pct = min(100, ml_outcomes / max(1, ml_min) * 100)
    if ml_pct >= 100:
        ml_bar_color = "var(--green)"
    elif ml_pct >= 50:
        ml_bar_color = "var(--orange)"
    else:
        ml_bar_color = "var(--blue)"

    # Processing speed (outcomes processed per hour)
    proc_speed = report.get("outcomes_per_hour", "—")
    backlog_count = max(0, pending)

    # Metrics row
    metrics = f'''<div class="grid" style="grid-template-columns:repeat(4,1fr)">
    <div class="card metric"><span class="label">⚡ Impulses (total)</span><span class="value">{stats.get("impulses",0)}</span>
      <span style="display:block;font-size:11px;color:var(--dim);margin-top:4px">Last 1h: {report.get("impulses_last_1h",0)} | 24h: {report.get("impulses_last_24h",0)}</span></div>
    <div class="card metric"><span class="label">📊 Outcomes</span><span class="value">{os_.get("completed",0)}</span>
      <span style="display:block;font-size:11px;color:var(--dim);margin-top:4px">Pending: {pending}</span></div>
    <div class="card metric"><span class="label">🪙 Coin Profiles</span><span class="value">{stats.get("coin_profiles",0)}</span></div>
    <div class="card metric"><span class="label">📝 Trades Imported</span><span class="value">{stats.get("trade_outcomes",0)}</span></div>
    </div>'''

    # Phase + ML status
    status_row = f'''<div class="grid" style="grid-template-columns:1fr 1fr">
    <div class="card">
      <h3 style="margin-bottom:12px">🧭 Market Phase</h3>
      <div style="text-align:center;margin-bottom:16px">{phase_badge}</div>
      <div class="param-grid" style="grid-template-columns:1fr 1fr">
        <div class="param-item"><span class="key">BTC Price</span><span class="val">${mp.get("btc_price",0):,.0f}</span></div>
        <div class="param-item"><span class="key">BTC Monthly</span><span class="val {pnl_cls(mp.get("btc_monthly",0))}">{mp.get("btc_monthly",0):+.1f}%</span></div>
        <div class="param-item"><span class="key">ETH Price</span><span class="val">${mp.get("eth_price",0):,.0f}</span></div>
        <div class="param-item"><span class="key">Alt Correlation</span><span class="val">{mp.get("alt_correlation",0):.2f}</span></div>
      </div>
    </div>
    <div class="card">
      <h3 style="margin-bottom:12px">🧠 ML Predictor</h3>
      <div style="text-align:center;margin-bottom:16px"><span class="badge" style="{ml_badge_style};font-size:14px;padding:4px 12px">{ml_status}</span></div>
      <div style="margin-bottom:12px">
        <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--dim);margin-bottom:4px">
          <span>Training Progress</span>
          <span>{ml_pct:.0f}%</span>
        </div>
        <div style="background:rgba(139,148,158,.15);border-radius:6px;height:10px;overflow:hidden">
          <div style="background:{ml_bar_color};height:100%;width:{ml_pct:.0f}%;border-radius:6px;transition:width .3s"></div>
        </div>
      </div>
      <div class="param-grid" style="grid-template-columns:1fr 1fr">
        <div class="param-item"><span class="key">Outcomes</span><span class="val">{ml_outcomes}</span></div>
        <div class="param-item"><span class="key">Trained On</span><span class="val">{ml_train_samples}</span></div>
        <div class="param-item"><span class="key">Processing</span><span class="val">{proc_speed}/hr</span></div>
        <div class="param-item"><span class="key">Backlog</span><span class="val">{backlog_count}</span></div>
        <div class="param-item"><span class="key">Retrain</span><span class="val">Every 24h</span></div>
        <div class="param-item"><span class="key">Next Retrain</span><span class="val">{next_retrain}</span></div>
      </div>
    </div>
    </div>'''

    # Outcome analysis
    outcome_html = ""
    if os_.get("completed", 0) > 0:
        outcome_html = f'''<div class="card">
        <h3 style="margin-bottom:12px">📊 Outcome Analysis ({os_["completed"]} completed impulses)</h3>
        <div class="grid" style="grid-template-columns:repeat(4,1fr)">
          <div class="card metric" style="padding:12px"><span class="label">Avg Favorable</span><span class="value pos">+{os_["avg_favorable_pct"]:.2f}%</span></div>
          <div class="card metric" style="padding:12px"><span class="label">Avg Adverse</span><span class="value neg">-{os_["avg_adverse_pct"]:.2f}%</span></div>
          <div class="card metric" style="padding:12px"><span class="label">Stop Hunts</span><span class="value">{os_["stop_hunt_pct"]:.0f}%</span></div>
          <div class="card metric" style="padding:12px"><span class="label">R:R Ratio</span><span class="value">{os_["avg_favorable_pct"]/max(0.01,os_["avg_adverse_pct"]):.2f}</span></div>
        </div></div>'''

    # Top impulses table
    top = report.get("top_10_impulses", [])
    seen = set()
    top_dedup = []
    for t in top:
        key = f'{t["symbol"]}:{t["tf"]}'
        if key not in seen:
            seen.add(key)
            top_dedup.append(t)
        if len(top_dedup) >= 10:
            break

    imp_rows = ""
    for t in top_dedup:
        dc = "badge-green" if t["dir"] == "long" else "badge-red"
        imp_rows += f'''<tr>
          <td><strong>{t["symbol"]}</strong></td>
          <td><span class="badge {dc}">{t["dir"].upper()}</span></td>
          <td>{t["tf"]}m</td>
          <td style="font-weight:700">{t["score"]:.1f}</td>
          <td>{t["vol_z"]:.1f}</td>
          <td>{t["ret_z"]:.1f}</td>
          <td>{t["location"]}</td>
        </tr>'''

    impulse_table = f'''<div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <h3>⚡ Top Impulses (24h)</h3>
      <a href="/iie/impulses" class="hist-btn">View All →</a>
    </div>
    <table><thead><tr><th>Symbol</th><th>Dir</th><th>TF</th><th>Score</th><th>Vol Z</th><th>Ret Z</th><th>Location</th></tr></thead>
    <tbody>{imp_rows}</tbody></table></div>'''

    # Trades by bot
    bot_rows = ""
    for bot, info in tbb.items():
        bc = "pos" if info["total_pnl"] >= 0 else "neg"
        bot_rows += f'''<tr>
          <td><strong>{bot}</strong></td>
          <td>{info["count"]}</td>
          <td>{info["wr"]:.0f}%</td>
          <td class="{bc}" style="font-weight:700">{info["total_pnl"]:+.3f}%</td>
        </tr>'''
    bot_table = f'''<div class="card">
    <h3 style="margin-bottom:12px">🤖 Performance by Bot</h3>
    <table><thead><tr><th>Bot</th><th>Trades</th><th>Win Rate</th><th>Total PnL</th></tr></thead>
    <tbody>{bot_rows}</tbody></table></div>''' if bot_rows else ""

    # Impulse distribution
    by_tf = report.get("impulse_by_tf", {})
    by_dir = report.get("impulse_by_dir", {})
    by_loc = report.get("impulse_by_location", {})

    dist_items = ""
    for tf, cnt in sorted(by_tf.items()):
        dist_items += f'<div class="param-item"><span class="key">{tf}m</span><span class="val">{cnt}</span></div>'
    dist_items += f'<div class="param-item"><span class="key">🟢 Long</span><span class="val">{by_dir.get("long",0)}</span></div>'
    dist_items += f'<div class="param-item"><span class="key">🔴 Short</span><span class="val">{by_dir.get("short",0)}</span></div>'
    for loc, cnt in by_loc.items():
        dist_items += f'<div class="param-item"><span class="key">{loc}</span><span class="val">{cnt}</span></div>'

    dist_html = f'''<div class="card">
    <h3 style="margin-bottom:12px">📐 Impulse Distribution (24h)</h3>
    <div class="param-grid">{dist_items}</div></div>'''

    # Navigation buttons
    nav_buttons = '''<div style="display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap">
    <a href="/iie" class="hist-btn" style="background:rgba(63,185,80,.15);border-color:var(--green);color:var(--green)">🧠 Overview</a>
    <a href="/iie/impulses" class="hist-btn">⚡ Impulses</a>
    <a href="/iie/coins" class="hist-btn">🪙 Coin Profiles</a>
    <a href="/iie/config" class="hist-btn">⚙️ Config</a>
    </div>'''

    body = nav_buttons + metrics + status_row + outcome_html + f'<div class="grid" style="grid-template-columns:2fr 1fr">{impulse_table}{dist_html}</div>' + bot_table

    return layout("🧠 IIE — Impulse Intelligence Engine", body, "/iie")


# ─── /iie/impulses — Live Feed ───────────────────────────────

def page_iie_impulses():
    layout, nav, pnl_cls, pnl_badge = _layout_ref()

    if not _iie_available:
        return layout("⚡ Impulses", '<div class="card"><p>IIE not available.</p></div>', "/iie/impulses")

    impulses = _iie_db.get_recent_impulses(hours=24, limit=100)

    rows = ""
    for imp in impulses:
        dc = "badge-green" if imp.direction == "long" else "badge-red"
        ts = datetime.fromtimestamp(imp.timestamp, tz=timezone.utc).strftime("%H:%M:%S")
        outcome = _iie_db.get_outcome(imp.id)
        fav = outcome.get("max_favorable_pct", 0) if outcome else 0
        adv = outcome.get("max_adverse_pct", 0) if outcome else 0
        complete = "✅" if outcome and outcome.get("tracking_complete") else "⏳"

        rows += f'''<tr>
          <td style="color:var(--dim)">{ts}</td>
          <td><strong>{imp.symbol}</strong></td>
          <td><span class="badge {dc}">{imp.direction.upper()}</span></td>
          <td>{imp.timeframe}m</td>
          <td style="font-weight:700">{imp.combined_score:.1f}</td>
          <td>{imp.vol_z:.1f}</td>
          <td>{imp.ret_z:.1f}</td>
          <td>{imp.rsi_at_impulse:.0f}</td>
          <td>{imp.impulse_location}</td>
          <td class="pos">+{fav:.2f}%</td>
          <td class="neg">-{adv:.2f}%</td>
          <td>{complete}</td>
        </tr>'''

    nav_buttons = '''<div style="display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap">
    <a href="/iie" class="hist-btn">🧠 Overview</a>
    <a href="/iie/impulses" class="hist-btn" style="background:rgba(63,185,80,.15);border-color:var(--green);color:var(--green)">⚡ Impulses</a>
    <a href="/iie/coins" class="hist-btn">🪙 Coin Profiles</a>
    <a href="/iie/config" class="hist-btn">⚙️ Config</a>
    </div>'''

    info = f'<div class="info-box">Showing {len(impulses)} impulses from last 24 hours. Auto-refreshes every 30s.</div>'

    tbl = f'''<div class="card" style="overflow-x:auto"><table>
    <thead><tr><th>Time</th><th>Symbol</th><th>Dir</th><th>TF</th><th>Score</th><th>Vol Z</th><th>Ret Z</th><th>RSI</th><th>Location</th><th>Max Fav</th><th>Max Adv</th><th>Track</th></tr></thead>
    <tbody>{rows}</tbody></table></div>'''

    return layout("⚡ Live Impulses", nav_buttons + info + tbl, "/iie/impulses")


# ─── /iie/coins — Coin Profiles ──────────────────────────────

def page_iie_coins():
    layout, nav, pnl_cls, pnl_badge = _layout_ref()

    if not _iie_available:
        return layout("🪙 Coin Profiles", '<div class="card"><p>IIE not available.</p></div>', "/iie/coins")

    profiles = _iie_db.get_all_coin_profiles()

    rows = ""
    for p in profiles:
        q_cls = "pos" if p.impulse_quality_score >= 60 else "neg" if p.impulse_quality_score < 40 else ""
        m_cls = "pos" if p.momentum_persistence >= 60 else "neg" if p.momentum_persistence < 40 else ""
        h_cls = "neg" if p.stop_hunt_frequency > 40 else "pos" if p.stop_hunt_frequency < 20 else ""

        rows += f'''<tr>
          <td><strong>{p.symbol}</strong></td>
          <td>{p.impulse_count}</td>
          <td class="{q_cls}" style="font-weight:700">{p.impulse_quality_score:.0f}</td>
          <td class="{m_cls}">{p.momentum_persistence:.0f}</td>
          <td class="{h_cls}">{p.stop_hunt_frequency:.0f}%</td>
          <td>{p.predictability_score:.0f}</td>
          <td>{p.volatility_regime}</td>
          <td>{p.best_tf}m</td>
          <td>{p.recommended_sl_mult:.1f}x</td>
          <td>{p.recommended_hold_bars}</td>
          <td>+{p.avg_continuation_pct:.2f}%</td>
          <td>-{p.avg_retracement_pct:.2f}%</td>
        </tr>'''

    if not rows:
        rows = '<tr><td colspan="12" style="color:var(--dim)">No profiles yet. Need 10+ completed impulse outcomes per coin.</td></tr>'

    nav_buttons = '''<div style="display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap">
    <a href="/iie" class="hist-btn">🧠 Overview</a>
    <a href="/iie/impulses" class="hist-btn">⚡ Impulses</a>
    <a href="/iie/coins" class="hist-btn" style="background:rgba(63,185,80,.15);border-color:var(--green);color:var(--green)">🪙 Coin Profiles</a>
    <a href="/iie/config" class="hist-btn">⚙️ Config</a>
    </div>'''

    tbl = f'''<div class="card" style="overflow-x:auto"><table>
    <thead><tr><th>Symbol</th><th>Impulses</th><th>Quality</th><th>Momentum</th><th>Stop Hunts</th><th>Predict.</th><th>Vol Regime</th><th>Best TF</th><th>SL Mult</th><th>Hold Bars</th><th>Avg Cont</th><th>Avg Retr</th></tr></thead>
    <tbody>{rows}</tbody></table></div>'''

    return layout("🪙 Coin Profiles", nav_buttons + tbl, "/iie/coins")


# ─── /iie/config — Config Editor ─────────────────────────────

def page_iie_config():
    layout, nav, pnl_cls, pnl_badge = _layout_ref()

    if not _iie_available:
        return layout("⚙️ IIE Config", '<div class="card"><p>IIE not available.</p></div>', "/iie/config")

    # Build config sections from actual config values
    sections = [
        ("⚡ Impulse Collector", [
            ("COLLECTOR_INTERVAL_SEC", iie_config.COLLECTOR_INTERVAL_SEC, "Scan interval (seconds)", "number"),
            ("COLLECTOR_TOP_COINS", iie_config.COLLECTOR_TOP_COINS, "Number of top coins to scan", "number"),
            ("COLLECTOR_MIN_TURNOVER_24H", iie_config.COLLECTOR_MIN_TURNOVER_24H, "Min 24h turnover ($)", "number"),
            ("IMPULSE_MIN_VOL_Z", iie_config.IMPULSE_MIN_VOL_Z, "Min volume Z-score", "number"),
            ("IMPULSE_MIN_RET_Z", iie_config.IMPULSE_MIN_RET_Z, "Min return Z-score", "number"),
        ]),
        ("📊 Post-Trade Tracker", [
            ("POST_TRACKER_INTERVAL_SEC", iie_config.POST_TRACKER_INTERVAL_SEC, "Update interval (seconds)", "number"),
            ("POST_TRACKER_MAX_AGE_SEC", iie_config.POST_TRACKER_MAX_AGE_SEC, "Max tracking age (seconds)", "number"),
            ("STOP_HUNT_REVERSAL_PCT", iie_config.STOP_HUNT_REVERSAL_PCT, "Stop hunt reversal threshold (%)", "number"),
        ]),
        ("🧭 Market Phase Detector", [
            ("MARKET_PHASE_INTERVAL_SEC", iie_config.MARKET_PHASE_INTERVAL_SEC, "Detection interval (seconds)", "number"),
            ("MARKET_PHASE_TRENDING_THRESHOLD", iie_config.MARKET_PHASE_TRENDING_THRESHOLD, "Trending threshold (%)", "number"),
            ("MARKET_PHASE_EMA_FAST", iie_config.MARKET_PHASE_EMA_FAST, "Fast EMA period", "number"),
            ("MARKET_PHASE_EMA_SLOW", iie_config.MARKET_PHASE_EMA_SLOW, "Slow EMA period", "number"),
        ]),
        ("🪙 Coin Scorer", [
            ("COIN_SCORER_INTERVAL_SEC", iie_config.COIN_SCORER_INTERVAL_SEC, "Scoring interval (seconds)", "number"),
            ("COIN_SCORER_MIN_IMPULSES", iie_config.COIN_SCORER_MIN_IMPULSES, "Min impulses for profile", "number"),
        ]),
        ("🧠 ML Predictor", [
            ("PREDICTOR_RETRAIN_INTERVAL_SEC", iie_config.PREDICTOR_RETRAIN_INTERVAL_SEC, "Retrain interval (seconds)", "number"),
            ("PREDICTOR_MIN_SAMPLES", iie_config.PREDICTOR_MIN_SAMPLES, "Min samples for training", "number"),
        ]),
    ]

    sections_html = ""
    for title, params in sections:
        items = ""
        for key, value, desc, input_type in params:
            step = "0.1" if isinstance(value, float) else "1"
            items += f'''<div class="param-item" style="flex-direction:column;align-items:stretch;gap:8px">
              <div style="display:flex;justify-content:space-between;align-items:center">
                <span class="key">{key}</span>
                <span style="font-size:11px;color:var(--dim)">{desc}</span>
              </div>
              <div style="display:flex;gap:8px;align-items:center">
                <input type="{input_type}" name="{key}" value="{value}" step="{step}"
                  style="flex:1;background:#0d1117;border:1px solid var(--border);color:var(--text);padding:8px;border-radius:6px;font-size:14px"
                  data-original="{value}" onchange="this.style.borderColor=this.value!=this.dataset.original?'var(--accent)':'var(--border)'">
                <span class="badge" style="background:rgba(139,148,158,.15);color:var(--dim);min-width:60px;text-align:center">{value}</span>
              </div>
            </div>'''

        sections_html += f'''<div class="ctrl-section">
          <h3>{title}</h3>
          <div class="param-grid" style="grid-template-columns:1fr">{items}</div>
        </div>'''

    nav_buttons = '''<div style="display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap">
    <a href="/iie" class="hist-btn">🧠 Overview</a>
    <a href="/iie/impulses" class="hist-btn">⚡ Impulses</a>
    <a href="/iie/coins" class="hist-btn">🪙 Coin Profiles</a>
    <a href="/iie/config" class="hist-btn" style="background:rgba(63,185,80,.15);border-color:var(--green);color:var(--green)">⚙️ Config</a>
    </div>'''

    save_js = '''<script>
    function saveConfig() {
      const inputs = document.querySelectorAll('input[name]');
      const changes = {};
      let hasChanges = false;
      inputs.forEach(inp => {
        if (inp.value != inp.dataset.original) {
          changes[inp.name] = parseFloat(inp.value) || inp.value;
          hasChanges = true;
        }
      });
      if (!hasChanges) { alert('No changes to save'); return; }

      const confirmMsg = 'Apply changes?\\n\\n' + Object.entries(changes).map(([k,v]) => k + ': ' + v).join('\\n');
      if (!confirm(confirmMsg)) return;

      fetch('/iie/api/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(changes)
      }).then(r => r.json()).then(data => {
        if (data.ok) {
          alert('✅ Config updated! Changes apply on next cycle.');
          location.reload();
        } else {
          alert('❌ Error: ' + (data.error || 'Unknown'));
        }
      }).catch(e => alert('❌ Network error: ' + e));
    }
    </script>'''

    save_btn = '''<div style="text-align:center;margin-top:20px">
    <button class="btn btn-green" onclick="saveConfig()" style="font-size:16px;padding:14px 40px">💾 Save Changes</button>
    <p style="color:var(--dim);font-size:12px;margin-top:8px">Changes apply immediately to the running daemon (next cycle)</p>
    </div>'''

    body = nav_buttons + '<div class="info-box">Edit any value and click Save. Changes are applied at runtime without restart.</div>' + sections_html + save_btn + save_js

    return layout("⚙️ IIE Config Editor", body, "/iie/config")


# ─── /iie/api/config — POST endpoint ────────────────────────

def handle_iie_config_update(post_data: bytes) -> str:
    """Handle POST to /iie/api/config. Returns JSON response."""
    try:
        changes = json.loads(post_data)
        applied = {}

        for key, value in changes.items():
            if hasattr(iie_config, key):
                old_val = getattr(iie_config, key)
                # Type-cast to match original
                if isinstance(old_val, int):
                    value = int(float(value))
                elif isinstance(old_val, float):
                    value = float(value)
                setattr(iie_config, key, value)
                applied[key] = {"old": old_val, "new": value}

        return json.dumps({"ok": True, "applied": applied})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})
