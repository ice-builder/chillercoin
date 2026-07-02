"""Patch: Add Jupiter Mirror page to monitor.py dashboard"""

with open("/home/trader/soldier/monitor.py", "r") as f:
    content = f.read()

changes = 0

# 1. Add page_jupiter function before ROUTES
jupiter_page = '''

# ═══════════════════════════════════════════════
# Jupiter Mirror Dashboard
# ═══════════════════════════════════════════════

def page_jupiter():
    try:
        from jupiter_mirror import get_jupiter_status
        s = get_jupiter_status()
    except ImportError:
        return layout("Jupiter", '<div class="card"><p>Jupiter Mirror not installed</p></div>', "/jupiter")

    if not s.get("enabled"):
        return layout("Jupiter", '<div class="card"><p>Jupiter Mirror disabled. Set <code>JUPITER_MIRROR=1</code></p></div>', "/jupiter")

    mode = s["mode"].upper()
    pnl = s["pnl_pct"]
    trades = s["trades"]
    wins = s["wins"]
    wr = s["win_rate"]
    pos_count = s["positions"]
    max_pos = s["max_positions"]
    leverage = s["leverage"]

    # Stats cards
    stats = f"""
    <div class="stats-row">
      <div class="stat-card">
        <div class="stat-label">Mode</div>
        <div class="stat-value">🪐 {mode}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">PnL</div>
        <div class="stat-value {pnl_cls(pnl)}">{pnl:+.2f}%</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Trades</div>
        <div class="stat-value">{trades}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Win Rate</div>
        <div class="stat-value">{wr:.1f}%</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Leverage</div>
        <div class="stat-value">{leverage}x</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Positions</div>
        <div class="stat-value">{pos_count}/{max_pos}</div>
      </div>
    </div>
    """

    # Open positions table
    positions_html = ""
    open_pos = s.get("open_positions", [])
    if open_pos:
        rows = ""
        for p in open_pos:
            dir_cls = "pos" if p["direction"] == "long" else "neg"
            dir_icon = "🟢" if p["direction"] == "long" else "🔴"
            rows += f"""
            <tr>
              <td>{dir_icon} {p['market']}</td>
              <td class="{dir_cls}">{p['direction'].upper()}</td>
              <td>${p['size_usd']:.0f}</td>
              <td>${p['entry_price']:.2f}</td>
              <td>{p['duration_min']:.0f}m</td>
            </tr>"""
        positions_html = f"""
        <div class="card">
          <h3>🪐 Open Positions</h3>
          <table>
            <thead><tr><th>Market</th><th>Dir</th><th>Size</th><th>Entry</th><th>Duration</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>"""
    else:
        positions_html = '<div class="card"><p>No open positions</p></div>'

    # Supported markets
    markets = s.get("supported_markets", [])
    markets_html = " ".join(f'<span class="badge badge-blue">{m}</span>' for m in markets)

    body = f"""
    <h2>🪐 Jupiter Perps Mirror</h2>
    {stats}
    {positions_html}
    <div class="card">
      <h3>Supported Markets</h3>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">{markets_html}</div>
    </div>
    """
    return layout("Jupiter Mirror", body, "/jupiter")

'''

if "page_jupiter" not in content:
    # Insert before ROUTES line
    content = content.replace('\nROUTES = {', jupiter_page + '\nROUTES = {', 1)
    changes += 1
    print("  + Added page_jupiter")

# 2. Add route
old_routes_end = '"/exchange/positions": page_exchange_positions}'
new_routes_end = '"/exchange/positions": page_exchange_positions, "/jupiter": page_jupiter}'

if '"/jupiter"' not in content:
    content = content.replace(old_routes_end, new_routes_end, 1)
    changes += 1
    print("  + Added /jupiter route")

# 3. Add nav link - find existing nav items
old_nav = '"/exchange": "💱 Exchange"'
new_nav = '"/exchange": "💱 Exchange", "/jupiter": "🪐 Jupiter"'

if '"/jupiter": "🪐 Jupiter"' not in content:
    content = content.replace(old_nav, new_nav, 1)
    changes += 1
    print("  + Added Jupiter nav link")

with open("/home/trader/soldier/monitor.py", "w") as f:
    f.write(content)

print(f"✅ {changes} changes, verifying syntax...")
compile(content, "monitor.py", "exec")
print("✅ Syntax OK")
