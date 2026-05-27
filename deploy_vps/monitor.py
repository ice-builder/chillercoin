"""
HQ Dashboard — Multi-page trading command center.
Routes: / (dashboard), /trades, /history, /analyze, /control
"""
import json, os, time, http.server, socketserver, urllib.parse
from pathlib import Path
from datetime import datetime, timezone
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
try:
    import requests as req
except ImportError:
    req = None
try:
    from insider_pages import page_insider, page_insider_signals, page_insider_trade, page_insider_position
except ImportError:
    page_insider = page_insider_signals = page_insider_trade = page_insider_position = None
try:
    from iie_pages import page_iie, page_iie_impulses, page_iie_coins, page_iie_config, handle_iie_config_update
except ImportError:
    page_iie = page_iie_impulses = page_iie_coins = page_iie_config = handle_iie_config_update = None

PORT = int(os.getenv("MONITOR_PORT", "8585"))
SD = Path(__file__).parent / ".local_ai" / "paper_trading"
PH_STATE = Path(os.getenv("PUMP_HUNTER_STATE", "/home/trader/pump_hunter/demo_state.json"))
_insider_dir_env = os.getenv("INSIDER_SCANNER_DIR", "")
if _insider_dir_env:
    INSIDER_DIR = Path(_insider_dir_env)
else:
    _id1 = Path(__file__).parent / "insider_scanner"
    _id2 = Path(__file__).parent / "insider-scanner"
    _id3 = Path(__file__).parent.parent / "insider-scanner"
    _id4 = Path("/home/trader/insider-scanner")
    INSIDER_DIR = next((d for d in [_id1, _id2, _id3, _id4] if d.exists()), _id1)
INSIDER_OI_HISTORY = INSIDER_DIR / "oi_history.json"
INSIDER_STATE = INSIDER_DIR / "insider_positions.json"
INSIDER_TRADES = INSIDER_DIR / "insider_trades.json"

# ─── Exchange Executor (for real balance display) ────────────
TRADING_MODE = os.getenv("TRADING_MODE", "paper").lower()
_executor = None
try:
    from exchange_executor import ExchangeExecutor
    if TRADING_MODE != "paper":
        import threading
        _exec_result = [None]
        def _init_executor():
            try:
                _exec_result[0] = ExchangeExecutor.from_env()
            except Exception:
                pass
        t = threading.Thread(target=_init_executor, daemon=True)
        t.start()
        t.join(timeout=10)  # Max 10s for exchange init
        _executor = _exec_result[0]
except Exception:
    _executor = None

# ─── Position Registry (for bot identification) ─────────────
_registry = None
try:
    from position_registry import PositionRegistry
    _registry = PositionRegistry("dashboard")
except Exception:
    pass

# ─── Exchange Data Cache (prevents Binance rate-limit bans) ──
# All exchange pages read from this cache instead of calling API directly.
# Cache is refreshed at most once per _EX_CACHE_TTL seconds.
_EX_CACHE_TTL = 180  # seconds — generous TTL to avoid Binance rate-limit bans
_ex_cache = {}      # key -> value
_ex_cache_ts = {}   # key -> last_fetch_timestamp
import threading
_ex_cache_lock = threading.Lock()

def _ex_cached(key: str, fetch_fn, ttl: int = None):
    """Get cached exchange data or fetch if stale.
    On API error, caches the failure for TTL to avoid hammering a banned endpoint.
    """
    _ttl = ttl or _EX_CACHE_TTL
    now = time.time()
    if key in _ex_cache_ts and (now - _ex_cache_ts[key]) < _ttl:
        return _ex_cache.get(key)
    with _ex_cache_lock:
        # Double-check after acquiring lock
        if key in _ex_cache_ts and (time.time() - _ex_cache_ts[key]) < _ttl:
            return _ex_cache.get(key)
        try:
            val = fetch_fn()
            _ex_cache[key] = val
            _ex_cache_ts[key] = time.time()
            return val
        except Exception as e:
            logging.warning(f"Exchange cache fetch '{key}' failed: {e}")
            # IMPORTANT: Still update timestamp to prevent re-hitting banned API
            _ex_cache_ts[key] = time.time()
            # Return last known good value, or sensible default
            return _ex_cache.get(key)

def ex_balance() -> float:
    """Cached exchange balance (90s TTL)."""
    if not _executor: return 0.0
    return _ex_cached('balance', _executor.get_balance) or 0.0

def ex_positions() -> list:
    """Cached exchange positions (90s TTL)."""
    if not _executor: return []
    return _ex_cached('positions', _executor.get_positions) or []

def ex_income() -> list:
    """Cached income history (90s TTL)."""
    if not _executor: return []
    return _ex_cached('income', _executor.get_income_history) or []

def ex_trades() -> list:
    """Cached trade history (90s TTL)."""
    if not _executor: return []
    return _ex_cached('trades', _executor.get_trade_history) or []

# ─── Bybit Price API ─────────────────────────────────────────
_price_cache = {}  # symbol -> (price, timestamp)
_CACHE_TTL = 10  # seconds

def fetch_price(symbol: str, exchange: str = "bybit") -> float:
    """Get latest price from exchange API with caching."""
    cache_key = f"{symbol}:{exchange}"
    now = time.time()
    if cache_key in _price_cache:
        price, ts = _price_cache[cache_key]
        if now - ts < _CACHE_TTL:
            return price
    if req is None:
        return 0.0
    try:
        if exchange == "mexc":
            mexc_sym = symbol.replace("USDT", "_USDT")
            r = req.get("https://contract.mexc.com/api/v1/contract/ticker", timeout=5)
            for t in r.json().get("data", []):
                if t.get("symbol") == mexc_sym:
                    price = float(t["lastPrice"])
                    _price_cache[cache_key] = (price, now)
                    return price
        elif exchange == "gateio":
            gate_sym = symbol.replace("USDT", "_USDT")
            r = req.get(f"https://api.gateio.ws/api/v4/futures/usdt/tickers?contract={gate_sym}", timeout=5)
            data = r.json()
            if isinstance(data, list) and data:
                price = float(data[0].get("last", 0))
                _price_cache[cache_key] = (price, now)
                return price
        elif exchange == "bitget":
            r = req.get(f"https://api.bitget.com/api/v2/mix/market/ticker?productType=USDT-FUTURES&symbol={symbol}", timeout=5)
            data = r.json()
            if data.get("data"):
                price = float(data["data"][0].get("lastPr", 0))
                _price_cache[cache_key] = (price, now)
                return price
        else:  # bybit
            r = req.get(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}", timeout=5)
            data = r.json()
            price = float(data["result"]["list"][0]["lastPrice"])
            _price_cache[cache_key] = (price, now)
            return price
    except Exception:
        return _price_cache.get(cache_key, (0.0, 0))[0]
    return 0.0

def calc_upnl(pos: dict, current_price: float) -> float:
    """Calculate unrealized P/L % for a position."""
    entry = float(pos.get("entry_price", 0))
    if entry == 0 or current_price == 0:
        return 0.0
    if pos.get("direction") == "long":
        return (current_price - entry) / entry * 100
    else:
        return (entry - current_price) / entry * 100

def rj(p):
    try: return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except: return {}

def state(): return rj(SD / "paper_state_multi.json")
def history(): return rj(SD / "strategy_history.json")
def archive():
    all_archived = []
    for fname in ["v1_trades_archive.json", "v2_trades_archive.json"]:
        a = rj(SD / fname)
        if a:
            all_archived.extend(a.get("completed_trades", []))
    return all_archived

def kill_active(): return (SD / ".kill_switch").exists()

def ph_state():
    """Read pump hunter state file."""
    return rj(PH_STATE)

def insider_oi_history():
    """Read insider scanner OI history."""
    return rj(INSIDER_OI_HISTORY)

def insider_state():
    """Read insider scanner state file."""
    return rj(INSIDER_STATE)

# ─── CSS ───# ─── Scalper Pages ──────────────────────────────────
CSS = """
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0a0e17;--card:#161b22;--border:#21262d;--text:#e0e6ed;--dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--orange:#f0883e;--purple:#bc8cff}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;display:flex}
a{color:var(--accent);text-decoration:none}
.sidebar{width:220px;background:var(--card);border-right:1px solid var(--border);padding:20px 0;position:fixed;top:0;left:0;height:100vh;display:flex;flex-direction:column}
.sidebar .logo{padding:16px 20px;font-size:18px;font-weight:700;background:linear-gradient(90deg,var(--accent),var(--green));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.sidebar a{display:flex;align-items:center;gap:10px;padding:12px 20px;color:var(--dim);font-size:14px;border-left:3px solid transparent;transition:.2s}
.sidebar a:hover,.sidebar a.active{color:var(--text);background:rgba(88,166,255,.08);border-left-color:var(--accent)}
.main{margin-left:220px;flex:1;padding:24px;min-height:100vh}
.page-title{font-size:24px;margin-bottom:20px;display:flex;align-items:center;gap:10px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:24px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px}
.metric{text-align:center}.metric .label{display:block;color:var(--dim);font-size:12px;margin-bottom:6px}.metric .value{font-size:24px;font-weight:700}
.pos{color:var(--green)}.neg{color:var(--red)}
table{width:100%;border-collapse:collapse;margin-top:12px}
th{background:#0d1117;color:var(--dim);padding:10px;text-align:left;font-size:13px;position:sticky;top:0}
td{padding:10px;border-bottom:1px solid var(--border);font-size:14px}
tr:hover{background:rgba(88,166,255,.04)}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.badge-green{background:rgba(63,185,80,.15);color:var(--green)}
.badge-red{background:rgba(248,81,73,.15);color:var(--red)}
.badge-blue{background:rgba(88,166,255,.15);color:var(--accent)}
.badge-purple{background:rgba(188,140,255,.15);color:var(--purple)}
.ver-timeline{border-left:2px solid var(--border);padding-left:20px;margin:20px 0}
.ver-item{position:relative;margin-bottom:24px;padding:16px;background:var(--card);border:1px solid var(--border);border-radius:8px}
.ver-item::before{content:'';position:absolute;left:-27px;top:20px;width:12px;height:12px;border-radius:50%;background:var(--border)}
.ver-item.active::before{background:var(--green);box-shadow:0 0 8px var(--green)}
.ver-item h3{margin-bottom:8px}
.param-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;margin-top:16px}
.param-item{background:#0d1117;padding:12px;border-radius:6px;display:flex;justify-content:space-between;align-items:center}
.param-item .key{color:var(--dim);font-size:13px}.param-item .val{font-weight:600;font-size:14px}
.ctrl-section{margin-bottom:24px;padding:20px;background:var(--card);border:1px solid var(--border);border-radius:12px}
.ctrl-section h3{margin-bottom:12px;color:var(--accent)}
.btn{display:inline-block;padding:10px 20px;border-radius:8px;font-weight:600;font-size:14px;cursor:pointer;border:none;color:#fff;text-decoration:none;margin:4px}
.btn-red{background:var(--red)}.btn-green{background:var(--green);color:#000}.btn-blue{background:var(--accent)}
.equity-bar{display:flex;align-items:flex-end;gap:2px;height:80px;margin-top:12px}
.equity-bar .bar{flex:1;min-width:4px;border-radius:2px 2px 0 0;transition:.2s}
.info-box{padding:12px 16px;background:rgba(88,166,255,.08);border:1px solid rgba(88,166,255,.2);border-radius:8px;margin-bottom:16px;font-size:13px;color:var(--accent)}
.refresh{color:var(--dim);font-size:12px;margin-top:20px;text-align:center}
.balance-card{cursor:pointer;transition:all .3s;border:2px solid transparent;position:relative;overflow:hidden}
.balance-card:hover{border-color:var(--green);box-shadow:0 0 20px rgba(63,185,80,.15)}
.balance-card:hover::after{content:'→ история';position:absolute;bottom:6px;right:10px;font-size:10px;color:var(--dim);opacity:.8}
.balance-card .value{font-size:28px;background:linear-gradient(135deg,var(--green),var(--accent));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.balance-card.neg-balance .value{background:linear-gradient(135deg,var(--red),var(--orange));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.svg-chart{width:100%;background:var(--card);border-radius:8px;overflow:visible}
.svg-chart text{font-family:-apple-system,sans-serif}
.filter-bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;align-items:center}
.filter-bar select,.filter-bar input{background:#0d1117;border:1px solid var(--border);color:var(--text);padding:8px 12px;border-radius:8px;font-size:13px}
.filter-bar label{color:var(--dim);font-size:13px}
.mini-spark{display:inline-block;vertical-align:middle;margin-left:8px}
.hist-btn{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;border-radius:8px;font-size:12px;font-weight:600;background:rgba(88,166,255,.12);color:var(--accent);border:1px solid rgba(88,166,255,.25);cursor:pointer;transition:all .2s;text-decoration:none}
.hist-btn:hover{background:rgba(88,166,255,.22);border-color:var(--accent);box-shadow:0 0 12px rgba(88,166,255,.2)}
.income-pos{color:var(--green);font-weight:600}
.income-neg{color:var(--red);font-weight:600}
.day-row:hover{background:rgba(88,166,255,.06)}
.pos-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;position:relative;transition:all .3s}
.pos-card:hover{border-color:var(--accent);box-shadow:0 0 20px rgba(88,166,255,.1)}
.pos-card .pos-sym{font-size:18px;font-weight:700;margin-bottom:8px}
.pos-card .pos-side{display:inline-block;padding:3px 10px;border-radius:6px;font-size:11px;font-weight:700;text-transform:uppercase}
.pos-card .pos-side.long{background:rgba(63,185,80,.15);color:var(--green)}
.pos-card .pos-side.short{background:rgba(248,81,73,.15);color:var(--red)}
.pos-detail{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:12px;font-size:13px}
.pos-detail .lbl{color:var(--dim)}
.pos-detail .val{text-align:right;font-weight:600}
.btn-close-pos{display:block;margin-top:14px;padding:8px;text-align:center;border-radius:8px;font-size:13px;font-weight:700;background:rgba(248,81,73,.12);color:var(--red);border:1px solid rgba(248,81,73,.3);cursor:pointer;transition:all .2s;text-decoration:none}
.btn-close-pos:hover{background:rgba(248,81,73,.25);border-color:var(--red);box-shadow:0 0 12px rgba(248,81,73,.3)}
.pos-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;margin-top:16px}
.close-modal{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.8);z-index:1000;align-items:center;justify-content:center}
.close-modal.show{display:flex}
.close-modal-box{background:var(--card);border:2px solid var(--red);border-radius:16px;padding:32px;text-align:center;max-width:400px;width:90%}
.close-modal-box h2{color:var(--red);margin-bottom:16px}
.countdown{font-size:48px;font-weight:700;color:var(--red);margin:16px 0}
.close-modal .btn{margin:8px;min-width:120px}
"""

def nav(active):
    def item(url, icon, text, indent=False):
        cls = 'active' if url == active else ''
        style = 'padding-left:36px;font-size:13px' if indent else ''
        return f'<a href="{url}" class="{cls}" style="{style}">{icon} {text}</a>'
    def section(title):
        return f'<div style="padding:8px 20px;font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:1.5px;margin-top:14px;border-top:1px solid var(--border)">{title}</div>'
    links = item("/", "\U0001f3e0", "HQ Overview")
    links += section("\u2694\ufe0f Soldier")
    links += item("/scalper", "\U0001f4ca", "Dashboard")
    links += item("/scalper/trades", "\U0001f4cb", "Trades", True)
    links += item("/scalper/history", "\U0001f4dc", "History", True)
    links += item("/scalper/analyze", "\U0001f52c", "Analysis", True)
    links += item("/scalper/control", "\u2699\ufe0f", "Control", True)
    links += section("\U0001f3af Pump Hunter")
    links += item("/pumps", "\U0001f4ca", "Dashboard")
    links += item("/pumps/trades", "\U0001f4cb", "Trades", True)
    links += item("/pumps/analyze", "\U0001f52c", "Analysis", True)
    links += section("\U0001f575\ufe0f Insider Scanner")
    links += item("/insider", "\U0001f4ca", "Dashboard")
    links += item("/insider/signals", "\U0001f4e1", "Live Signals", True)
    links += section("\U0001f9e0 IIE Engine")
    links += item("/iie", "\U0001f4ca", "Overview")
    links += item("/iie/impulses", "\u26a1", "Impulses", True)
    links += item("/iie/coins", "\U0001fa99", "Coin Profiles", True)
    links += item("/iie/config", "\u2699\ufe0f", "Config", True)
    links += section("\U0001f4b9 Exchange")
    links += item("/exchange", "\U0001f4ca", "Dashboard")
    links += item("/exchange/positions", "\U0001f4cd", "Positions", True)
    links += item("/exchange/history", "\U0001f4dc", "Trade History", True)
    links += item("/exchange/equity", "\U0001f4c8", "PnL Curve", True)
    return f'<div class="sidebar"><div class="logo">\U0001f3af HQ Command</div>{links}<div style="flex:1"></div><div style="padding:12px 20px;font-size:11px;color:var(--dim)">Auto-refresh: 60s</div></div>'


def layout(title, body, active="/"):
    refresh_js = '<script>window._modalOpen=false;let _r=setInterval(()=>{if(!document.hidden&&!window._modalOpen)location.reload()},60000);window.addEventListener("beforeunload",()=>clearInterval(_r));document.addEventListener("visibilitychange",()=>{if(document.hidden)clearInterval(_r);else _r=setInterval(()=>{if(!window._modalOpen)location.reload()},60000)})</script>'
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>HQ — {title}</title><style>{CSS}</style></head>
<body>{nav(active)}<div class="main"><div class="page-title">{title}</div>{body}</div>{refresh_js}</body></html>"""

def pnl_cls(v): return "pos" if v >= 0 else "neg"
def pnl_badge(v): return f'<span class="badge {"badge-green" if v>0 else "badge-red"}">{v:+.3f}%</span>'

# ─── Pages ─# ─── Scalper Pages ──────────────────────────────────

def page_home():
    """Combined HQ Overview showing both bots."""
    s = state()
    # Recalculate wins/losses from actual trades (counters in state may be stale)
    s_trades = [t for t in s.get("completed_trades", []) if t.get("strategy_name") != "exchange_sync" and t.get("config_version") != "sync"]
    sw = sum(1 for t in s_trades if t.get("realized_pnl_pct", 0) > 0)
    sl = len(s_trades) - sw
    s_tot = sw+sl; s_wr = sw/max(1,s_tot)*100
    # v10: Soldier PnL = sum of actual trade PnLs (no fake deposit)
    s_pnl = sum(t.get('realized_pnl_pct', 0) for t in s_trades)
    s_active = s.get("active_positions", {})
    ks = kill_active()

    # Soldier balance = exchange balance (single source of truth)
    exchange_balance = ex_balance() if _executor else 0.0
    s_balance = exchange_balance if exchange_balance > 0 else s.get('exchange_balance', 0)

    ph = ph_state()
    # Recalculate PnL from actual completed trades (not stale total_pnl_pct)
    p_pnl = sum(t.get('pnl_pct', 0) for t in ph.get('completed_trades', [])) if ph else 0
    p_wins = ph.get("wins", 0) if ph else 0
    p_losses = ph.get("losses", 0) if ph else 0
    p_tot = p_wins + p_losses
    p_wr = p_wins / max(1, p_tot) * 100
    p_deposit = 10000  # Initial deposit
    p_balance = ph.get("demo_balance", 10000) if ph else 10000
    p_active = ph.get("active_positions", {}) if ph else {}

    # Total portfolio = exchange balance (single account for Soldier)
    # Pump Hunter has its own paper balance
    total_balance = exchange_balance if exchange_balance > 0 else s_balance
    # Combined PnL from exchange
    c_bal_cls = '' if s_balance > 0 else ' neg-balance'
    s_bal_cls = '' if s_pnl >= 0 else ' neg-balance'
    p_bal_cls = '' if p_pnl >= 0 else ' neg-balance'

    # Trading mode badges (per-bot)
    mode_badges = {
        'paper': '<span class="badge" style="background:rgba(139,148,158,.15);color:var(--dim)">📝 PAPER</span>',
        'demo': '<span class="badge badge-purple">🧪 DEMO</span>',
        'live': '<span class="badge badge-red">🔴 LIVE</span>',
    }
    s_mode = TRADING_MODE
    p_mode = ph.get("trading_mode", "paper") if ph else "paper"
    s_mode_badge = mode_badges.get(s_mode, mode_badges['paper'])
    p_mode_badge = mode_badges.get(p_mode, mode_badges['paper'])

    # Combined mode
    combined_mode = 'live' if 'live' in (s_mode, p_mode) else ('demo' if 'demo' in (s_mode, p_mode) else 'paper')
    combined_badge = mode_badges.get(combined_mode, mode_badges['paper'])

    # Info line
    ex_line = f' | Binance Testnet: ${exchange_balance:,.2f}' if exchange_balance > 0 else ''
    sol_pnl_line = f'Soldier: {s_pnl:+.2f}%'
    pump_pnl_line = f'Pump: {p_pnl:+.1f}%'

    # Mini equity sparkline from income history
    mini_spark = ''
    if _executor:
        try:
            income = [i for i in ex_income() if i.get('income_type') == 'REALIZED_PNL']
            if income:
                vals = [i['income'] for i in income[-30:]]
                running = []
                s_run = 0
                for v in vals:
                    s_run += v
                    running.append(s_run)
                if running:
                    mn_s = min(running); mx_s = max(running)
                    rng_s = mx_s - mn_s if mx_s != mn_s else 1
                    pts = []
                    for idx_s, rv in enumerate(running):
                        x = idx_s / max(1, len(running)-1) * 120
                        y = 24 - (rv - mn_s) / rng_s * 20
                        pts.append(f'{x:.0f},{y:.1f}')
                    last_c = 'var(--green)' if running[-1] >= 0 else 'var(--red)'
                    mini_spark = f'<span class="mini-spark"><svg width="124" height="28" viewBox="0 0 124 28"><polyline fill="none" stroke="{last_c}" stroke-width="1.5" points="{" ".join(pts)}"/></svg></span>'
        except Exception:
            pass

    # Fetch exchange open positions for overview
    ex_pos_list = ex_positions()
    ex_upnl = sum(p.unrealized_pnl for p in ex_pos_list)
    ex_pos_count = len(ex_pos_list)
    ex_upnl_c = 'var(--green)' if ex_upnl >= 0 else 'var(--red)'
    ex_pos_info = f'<span style="display:block;font-size:13px;margin-top:6px;color:{ex_upnl_c};font-weight:600">\U0001f4cd {ex_pos_count} open positions · uPnL: ${ex_upnl:+,.2f}</span>' if ex_pos_count > 0 else ''

    combined = f'''<div class="grid" style="grid-template-columns:1fr">
    <div class="card metric balance-card{c_bal_cls}" style="padding:28px">
    <span class="label">\U0001f4b0 Total Portfolio {combined_badge}</span>
    <span class="value" style="font-size:36px">${total_balance:,.2f}</span>{mini_spark}
    <span style="display:block;font-size:12px;color:var(--dim);margin-top:6px">{sol_pnl_line} · {pump_pnl_line}{ex_line}</span>
    {ex_pos_info}
    <div style="margin-top:12px;display:flex;gap:8px;justify-content:center">
    <a href="/exchange/positions" class="hist-btn">\U0001f4cd Позиции ({ex_pos_count})</a>
    <a href="/exchange/history" class="hist-btn">\U0001f4dc История сделок</a>
    <a href="/exchange/equity" class="hist-btn">\U0001f4c8 Кривая PnL</a>
    </div>
    </div></div>'''

    # Soldier positions
    s_status = '<span class="badge badge-red">\U0001f6d1 STOP</span>' if ks else '<span class="badge badge-green">\U0001f7e2 LIVE</span>'
    s_pos_rows = ""
    s_upnl = 0.0
    s_upnl_usd = 0.0
    for sym, p in s_active.items():
        di = "\U0001f7e2" if p.get("direction")=="long" else "\U0001f534"
        cur = fetch_price(sym)
        upnl = calc_upnl(p, cur) if cur else 0
        size_u = float(p.get("size_usdt", 0))
        upnl_d = size_u * upnl / 100 if size_u else 0
        s_upnl += upnl
        s_upnl_usd += upnl_d
        cls = "pos" if upnl >= 0 else "neg"
        usd_str = f' <span style="font-size:12px">(${upnl_d:+,.1f})</span>' if size_u else ''
        s_pos_rows += f'<tr><td><strong>{sym}</strong></td><td>{di} {p.get("direction","?").upper()}</td><td class="{cls}" style="font-weight:700">{upnl:+.2f}%{usd_str}</td></tr>'
    if not s_pos_rows:
        s_pos_rows = '<tr><td colspan="3" style="color:var(--dim)">No open positions</td></tr>'
    s_upnl_cls = "pos" if s_upnl >= 0 else "neg"
    s_usd_info = f' (${s_upnl_usd:+,.1f})' if s_upnl_usd != 0 else ''

    soldier = f'''<div class="card" style="cursor:pointer" onclick="window.location='/scalper'">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
    <h3>\u2694\ufe0f Soldier (Impulse Scalper)</h3><div>{s_mode_badge} {s_status}</div>
    </div>
    <div class="grid" style="grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px">
    <div class="card metric" style="padding:12px" onclick="event.stopPropagation();window.location='/scalper/trades'"><span class="label">📈 PnL</span><span class="value {pnl_cls(s_pnl)}" style="font-size:20px">{s_pnl:+.2f}%</span></div>
    <div class="card metric" style="padding:12px"><span class="label">Win Rate</span><span class="value" style="font-size:20px">{s_wr:.0f}%</span></div>
    <div class="card metric" style="padding:12px"><span class="label">Trades</span><span class="value" style="font-size:20px">{s_tot} <small style="color:var(--dim)">W{sw}/L{sl}</small></span></div>
    </div>
    <table><thead><tr><th>Symbol</th><th>Direction</th><th>uPnL</th></tr></thead><tbody>{s_pos_rows}</tbody></table>
    <p style="margin-top:8px;font-size:12px;color:var(--dim)">Active: {len(s_active)}/5 \u00b7 <span class="{s_upnl_cls}">uPnL: {s_upnl:+.2f}%{s_usd_info}</span> \u00b7 Click for details \u2192</p>
    </div>'''

    # Pump Hunter positions
    p_pos_rows = ""
    p_upnl = 0.0
    p_upnl_usd = 0.0
    for sk, p in p_active.items():
        real_sym = p.get("symbol", sk.split(":")[0])
        exch = p.get("exchange", "?")
        entry = float(p.get("entry_price", 0))
        direction = p.get("direction", "long")
        strat_ver = p.get("strategy_version", "v2")
        cur = fetch_price(real_sym, exch)
        if direction == "long":
            upnl = ((cur / entry) - 1) * 100 if entry > 0 and cur > 0 else p.get("pnl_pct", 0)
        else:
            upnl = ((entry / cur) - 1) * 100 if entry > 0 and cur > 0 else p.get("pnl_pct", 0)
        # Calculate position notional and dollar uPnL
        lev = float(p.get("leverage", 20))
        size_u = float(p.get("size_usdt", 0) or 0)
        if size_u == 0:
            sz_pct = float(p.get("size_pct", 20))
            size_u = p_balance * sz_pct / 100
        notional = size_u * lev
        upnl_d = notional * upnl / 100
        p_upnl += upnl
        p_upnl_usd += upnl_d
        cls = "pos" if upnl >= 0 else "neg"
        pump = p.get("pump_pct", 0)
        usd_str = f' <span style="font-size:11px">(${upnl_d:+,.0f})</span>' if upnl_d != 0 else ''
        ver_badge = (
            f'<span class="badge" style="background:rgba(63,185,80,.15);color:var(--green)">{strat_ver.upper()}</span>' if strat_ver == 'v3'
            else f'<span class="badge badge-purple">{strat_ver.upper()}</span>' if strat_ver == 'v2'
            else f'<span class="badge" style="background:rgba(139,148,158,.15);color:var(--dim)">{strat_ver.upper()}</span>'
        )
        p_pos_rows += f'<tr><td><strong>{real_sym}</strong></td><td>{ver_badge}</td><td><span class="badge badge-blue">{exch.upper()}</span></td><td>+{pump:.0f}%</td><td style="color:var(--dim)">${notional:,.0f}</td><td class="{cls}" style="font-weight:700">{upnl:+.1f}%{usd_str}</td></tr>'
    if not p_pos_rows:
        p_pos_rows = '<tr><td colspan="6" style="color:var(--dim)">Scanner hunting...</td></tr>'
    p_upnl_cls = "pos" if p_upnl >= 0 else "neg"
    p_usd_info = f' (${p_upnl_usd:+,.0f})' if p_upnl_usd != 0 else ''
    ph_status = '<span class="badge badge-green">\U0001f7e2 SCANNING</span>' if ph else '<span class="badge" style="background:rgba(139,148,158,.15);color:var(--dim)">\u23f3 OFFLINE</span>'

    p_bal_pnl = (p_balance / p_deposit - 1) * 100 if p_deposit else 0
    p_bal_cls = "pos" if p_bal_pnl >= 0 else "neg"

    pump_card = f'''<div class="card" style="cursor:pointer" onclick="window.location='/pumps'">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
    <h3>\U0001f3af Pump Hunter</h3><div>{p_mode_badge} {ph_status}</div>
    </div>
    <div class="card metric balance-card{" neg-balance" if p_bal_pnl < 0 else ""}" style="padding:16px;margin-bottom:16px;text-align:center" onclick="event.stopPropagation()">
    <span class="label">\U0001f4b0 Balance</span>
    <span class="value" style="font-size:24px">${p_balance:,.2f}</span>
    <span style="display:block;font-size:11px;color:var(--dim);margin-top:4px">Deposit: ${p_deposit:,.0f} | PnL: <span class="{p_bal_cls}">{p_bal_pnl:+.1f}%</span></span>
    </div>
    <div class="grid" style="grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px">
    <div class="card metric" style="padding:12px" onclick="event.stopPropagation();window.location='/pumps/trades'"><span class="label">\U0001f4c8 PnL</span><span class="value {pnl_cls(p_pnl)}" style="font-size:20px">{p_pnl:+.1f}%</span></div>
    <div class="card metric" style="padding:12px"><span class="label">Win Rate</span><span class="value" style="font-size:20px">{p_wr:.0f}%</span></div>
    <div class="card metric" style="padding:12px"><span class="label">Trades</span><span class="value" style="font-size:20px">{p_tot} <small style="color:var(--dim)">W{p_wins}/L{p_losses}</small></span></div>
    </div>
    <table><thead><tr><th>Symbol</th><th>Strategy</th><th>Exchange</th><th>Pump</th><th>Size $</th><th>uPnL</th></tr></thead><tbody>{p_pos_rows}</tbody></table>
    <p style="margin-top:8px;font-size:12px;color:var(--dim)">Active: {len(p_active)} \u00b7 <span class="{p_upnl_cls}">uPnL: {p_upnl:+.1f}%{p_usd_info}</span> \u00b7 Click for details \u2192</p>
    </div>'''

    # ─── Insider Scanner Card ─────────────────────
    ins = insider_state()
    ins_balance = ins.get("balance", 10000) if ins else 10000
    ins_deposit = 10000
    ins_pnl_usd = ins_balance - ins_deposit
    ins_pnl_pct = (ins_balance / ins_deposit - 1) * 100 if ins_deposit else 0
    ins_active = ins.get("active_positions", {}) if ins else {}

    # Load insider trades
    ins_trades = []
    try:
        ins_trades_path = INSIDER_TRADES
        if ins_trades_path.exists():
            ins_trades = json.loads(ins_trades_path.read_text(encoding="utf-8"))
            if not isinstance(ins_trades, list):
                ins_trades = []
    except Exception:
        ins_trades = []

    ins_total = len(ins_trades)
    ins_wins = sum(1 for t in ins_trades if t.get("pnl_pct", 0) > 0)
    ins_losses = ins_total - ins_wins
    ins_wr = ins_wins / max(1, ins_total) * 100
    ins_sum_pnl = sum(t.get("pnl_pct", 0) for t in ins_trades)
    ins_bal_cls = "pos" if ins_pnl_pct >= 0 else "neg"

    # Insider positions rows
    ins_pos_rows = ""
    ins_upnl = 0.0
    ins_upnl_usd = 0.0
    for key, pos in ins_active.items():
        sym = pos.get("symbol", key.split(":")[0])
        ex = pos.get("exchange", "?")
        entry = float(pos.get("entry_price", 0))
        cur = fetch_price(sym, ex)
        if entry > 0 and cur > 0:
            upnl = ((cur / entry) - 1) * 100
        else:
            upnl = 0
        size_u = float(pos.get("size_usdt", 0))
        lev = float(pos.get("leverage", 10))
        notional = size_u * lev
        upnl_d = notional * upnl / 100
        ins_upnl += upnl
        ins_upnl_usd += upnl_d
        cls = "pos" if upnl >= 0 else "neg"
        score = pos.get("insider_score", "?")
        usd_str = f' <span style="font-size:11px">(${upnl_d:+,.0f})</span>' if upnl_d != 0 else ''
        ins_pos_rows += f'<tr><td><strong>{sym}</strong></td><td><span class="badge badge-blue">{ex.upper()}</span></td><td>{score}</td><td class="{cls}" style="font-weight:700">{upnl:+.1f}%{usd_str}</td></tr>'
    if not ins_pos_rows:
        ins_pos_rows = '<tr><td colspan="4" style="color:var(--dim)">Scanner hunting...</td></tr>'
    ins_upnl_cls = "pos" if ins_upnl >= 0 else "neg"
    ins_usd_info = f' (${ins_upnl_usd:+,.0f})' if ins_upnl_usd != 0 else ''

    # Scanner status
    ins_oi = insider_oi_history()
    ins_last_updated = ins_oi.get("last_updated", 0) if ins_oi else 0
    ins_online = (time.time() - float(ins_last_updated)) < 600 if ins_last_updated else False
    ins_status = '<span class="badge badge-green">\U0001f7e2 SCANNING</span>' if ins_online else '<span class="badge" style="background:rgba(139,148,158,.15);color:var(--dim)">\u23f3 OFFLINE</span>'

    insider_card = f'''<div class="card" style="cursor:pointer" onclick="window.location='/insider'">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
    <h3>\U0001f575\ufe0f Insider Scanner</h3><div>{ins_status}</div>
    </div>
    <div class="card metric balance-card{" neg-balance" if ins_pnl_pct < 0 else ""}" style="padding:16px;margin-bottom:16px;text-align:center" onclick="event.stopPropagation()">
    <span class="label">\U0001f4b0 Balance</span>
    <span class="value" style="font-size:24px">${ins_balance:,.0f}</span>
    <span style="display:block;font-size:11px;color:var(--dim);margin-top:4px">Deposit: ${ins_deposit:,.0f} | PnL: <span class="{ins_bal_cls}">{ins_pnl_pct:+.1f}%</span></span>
    </div>
    <div class="grid" style="grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px">
    <div class="card metric" style="padding:12px"><span class="label">\U0001f4c8 PnL</span><span class="value {pnl_cls(ins_sum_pnl)}" style="font-size:20px">{ins_sum_pnl:+.1f}%</span></div>
    <div class="card metric" style="padding:12px"><span class="label">Win Rate</span><span class="value" style="font-size:20px;color:{"var(--green)" if ins_wr >= 50 else "var(--orange)"}">{ins_wr:.0f}%</span></div>
    <div class="card metric" style="padding:12px"><span class="label">Trades</span><span class="value" style="font-size:20px">{ins_total} <small style="color:var(--dim)">W{ins_wins}/L{ins_losses}</small></span></div>
    </div>
    <table><thead><tr><th>Symbol</th><th>Exchange</th><th>Score</th><th>uPnL</th></tr></thead><tbody>{ins_pos_rows}</tbody></table>
    <p style="margin-top:8px;font-size:12px;color:var(--dim)">Active: {len(ins_active)} \u00b7 <span class="{ins_upnl_cls}">uPnL: {ins_upnl:+.1f}%{ins_usd_info}</span> \u00b7 Click for details \u2192</p>
    </div>'''

    return layout("\U0001f3e0 HQ Overview", combined + '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px">' + soldier + pump_card + insider_card + '</div>', "/")

# ─── Scalper Pages ──────────────────────────────────

def page_scalper():
    s = state()
    # Recalculate from actual trades
    s_trades = [t for t in s.get("completed_trades", []) if t.get("strategy_name") != "exchange_sync" and t.get("config_version") != "sync"]
    w = sum(1 for t in s_trades if t.get("realized_pnl_pct", 0) > 0)
    l = len(s_trades) - w
    tot = w+l; wr = w/max(1,tot)*100
    pnl = sum(t.get('realized_pnl_pct', 0) for t in s_trades)
    sig = s.get("signals_seen",0)
    up = str(s.get("last_updated",""))[:19]
    h = history(); ver = h.get("current_version","?")
    ks = kill_active()
    active = s.get("active_positions",{})
    # v10: Balance = exchange balance (no fake deposit)
    balance = ex_balance() if (_executor and TRADING_MODE != 'paper') else s.get('exchange_balance', 0)
    bal_cls = '' if pnl >= 0 else ' neg-balance'

    status = '<span class="badge badge-red">🛑 STOPPED</span>' if ks else '<span class="badge badge-green">🟢 ACTIVE</span>'

    # Mode badge
    s_mode_badges = {
        'paper': '<span class="badge" style="background:rgba(139,148,158,.15);color:var(--dim)">📝 PAPER</span>',
        'demo': '<span class="badge badge-purple">🧪 DEMO</span>',
        'live': '<span class="badge badge-red">🔴 LIVE</span>',
    }
    s_mode_badge = s_mode_badges.get(TRADING_MODE, s_mode_badges['paper'])

    # Metrics
    bal_display = f'${balance:,.2f}' if balance > 0 else '—'
    metrics = f"""<div class="grid">
    <div class="card metric balance-card{bal_cls}" onclick="window.location='/trades'" title="Нажми для истории сделок"><span class="label">💰 Exchange Balance</span><span class="value">{bal_display}</span><span style="display:block;font-size:11px;color:var(--dim);margin-top:4px">Trades PnL: {pnl:+.3f}%</span></div>
    <div class="card metric"><span class="label">Status</span><span class="value">{status} {s_mode_badge}</span></div>
    <div class="card metric"><span class="label">Total PnL</span><span class="value {pnl_cls(pnl)}">{pnl:+.3f}%</span></div>
    <div class="card metric"><span class="label">Win Rate</span><span class="value">{wr:.1f}%</span></div>
    <div class="card metric"><span class="label">Trades</span><span class="value">{tot} <small style="color:var(--dim)">W{w}/L{l}</small></span></div>
    <div class="card metric"><span class="label">Config</span><span class="value"><span class="badge badge-purple">{ver}</span></span></div>
    </div>"""

    # Active positions with unrealized P/L
    pos_html = ""
    if active:
        rows = ""
        total_upnl = 0.0
        total_upnl_usd = 0.0
        for sym, p in active.items():
            di = "🟢 LONG" if p.get("direction")=="long" else "🔴 SHORT"
            cur_price = fetch_price(sym)
            upnl = calc_upnl(p, cur_price) if cur_price else 0
            size_u = float(p.get("size_usdt", 0) or 0)
            upnl_d = size_u * upnl / 100 if size_u else 0
            total_upnl += upnl
            total_upnl_usd += upnl_d
            upnl_cls = "pos" if upnl >= 0 else "neg"
            price_display = f"{cur_price:.6g}" if cur_price else "—"
            entry_price = p.get('entry_price', '?')
            stop_price = p.get('stop_price', '?')
            tp_price = p.get('tp_price', '?')
            size_display = f"${size_u:,.0f}" if size_u else "—"
            usd_str = f' <span style="font-size:12px">(${upnl_d:+,.1f})</span>' if size_u else ''
            rows += f'''<tr style="cursor:pointer" onclick="window.location='/scalper/position/{sym}'">
            <td><a href="/scalper/position/{sym}" style="font-weight:700">{sym}</a></td>
            <td>{di}</td>
            <td>{entry_price}</td>
            <td>{price_display}</td>
            <td style="color:var(--dim)">{size_display}</td>
            <td class="{upnl_cls}" style="font-weight:700">{upnl:+.3f}%{usd_str}</td>
            <td>{p.get("strategy_name","?")}</td>
            <td><span class="badge badge-purple">{p.get("config_version","?")}</span></td>
            </tr>'''
        total_cls = "pos" if total_upnl >= 0 else "neg"
        usd_total = f' · ${total_upnl_usd:+,.1f}' if total_upnl_usd != 0 else ''
        pos_html = f'''<div class="card"><h3 style="margin-bottom:12px">📡 Active Positions ({len(active)}) · <span class="{total_cls}">uPnL: {total_upnl:+.3f}%{usd_total}</span></h3>
        <table><thead><tr><th>Symbol</th><th>Dir</th><th>Entry</th><th>Now</th><th>Size $</th><th>uPnL</th><th>Strategy</th><th>Version</th></tr></thead>
        <tbody>{rows}</tbody></table>
        <p style="margin-top:8px;color:var(--dim);font-size:12px">Click a row to view live chart →</p></div>'''
    else:
        pos_html = '<div class="card"><h3>📡 Active Positions</h3><p style="color:var(--dim);margin-top:8px">No open positions</p></div>'

    # Equity curve (exclude exchange_sync trades)
    trades = s.get("completed_trades",[])
    bot_trades = [t for t in trades if t.get("strategy_name") != "exchange_sync" and t.get("config_version") != "sync"]
    eq_html = ""
    if bot_trades:
        running = 0; curve = []
        for t in bot_trades:
            running += t.get("realized_pnl_pct",0)
            curve.append(running)
        mn = min(curve); mx = max(curve); rng = mx - mn if mx != mn else 0.1
        bars = ""
        for v in curve:
            pct = max(5, (v - mn) / rng * 100)
            c = "var(--green)" if v >= 0 else "var(--red)"
            bars += f'<div class="bar" style="height:{pct}%;background:{c}" title="{v:+.3f}%"></div>'
        eq_html = f'<div class="card"><h3>📈 Equity Curve</h3><div class="equity-bar">{bars}</div></div>'

    # Recent trades (exclude sync)
    recent = ""
    if bot_trades:
        rows = ""
        for t in bot_trades[-5:][::-1]:
            p = t.get("realized_pnl_pct",0)
            di = "🟢" if t.get("direction")=="long" else "🔴"
            rows += f'<tr><td>{di} {t.get("symbol","?")}</td><td>{pnl_badge(p)}</td><td>{t.get("exit_reason","?")}</td><td>{t.get("strategy_name","?")}</td></tr>'
        recent = f'<div class="card"><h3>🕐 Recent Trades</h3><table><thead><tr><th>Symbol</th><th>PnL</th><th>Exit</th><th>Strategy</th></tr></thead><tbody>{rows}</tbody></table><p style="margin-top:8px"><a href="/scalper/trades">View all →</a></p></div>'

    return layout("📊 Dashboard", metrics + pos_html + eq_html + recent, "/scalper")


def page_trades():
    s = state()
    trades = s.get("completed_trades",[])
    archived = archive()
    all_trades = archived + trades

    if not all_trades:
        return layout("📋 Trades", '<div class="card"><p>No trades yet.</p></div>', "/scalper/trades")

    # Separate real bot trades from exchange_sync artifacts
    bot_trades = [t for t in all_trades if t.get("strategy_name") != "exchange_sync" and t.get("config_version") != "sync"]
    sync_trades = [t for t in all_trades if t.get("strategy_name") == "exchange_sync" or t.get("config_version") == "sync"]

    rows = ""
    for i, t in enumerate(reversed(bot_trades), 1):
        p = t.get("realized_pnl_pct",0)
        di = "🟢" if t.get("direction")=="long" else "🔴"
        ver = t.get("config_version","v1")
        rows += f"""<tr>
        <td>{i}</td><td><strong>{t.get("symbol","?")}</strong></td>
        <td>{di} {t.get("direction","?").upper()}</td>
        <td>{t.get("entry_price","?")}</td><td>{t.get("exit_price","?")}</td>
        <td>{pnl_badge(p)}</td><td>{t.get("exit_reason","?")}</td>
        <td>{t.get("bars_held","?")}</td>
        <td><span class="badge badge-blue">{t.get("strategy_name","?")}</span></td>
        <td><span class="badge badge-purple">{ver}</span></td>
        <td>{str(t.get("entry_time",""))[:16]}</td></tr>"""

    # Stats from bot trades only (excluding sync artifacts)
    total_pnl = sum(t.get("realized_pnl_pct",0) for t in bot_trades)
    wins = sum(1 for t in bot_trades if t.get("realized_pnl_pct",0)>0)
    info = f'<div class="info-box">Total: {len(bot_trades)} trades | W{wins}/L{len(bot_trades)-wins} | PnL: {total_pnl:+.3f}%</div>'

    # Sync trades notice
    sync_note = ''
    if sync_trades:
        sync_pnl = sum(t.get("realized_pnl_pct",0) for t in sync_trades)
        sync_note = f'<div style="padding:8px 16px;background:rgba(240,136,62,.08);border:1px solid rgba(240,136,62,.2);border-radius:8px;margin-bottom:12px;font-size:13px;color:var(--orange)">⚠️ {len(sync_trades)} exchange_sync trades hidden (inherited positions from restart, PnL: {sync_pnl:+.3f}%)</div>'

    tbl = f"""{info}{sync_note}<div class="card" style="overflow-x:auto"><table><thead><tr>
    <th>#</th><th>Symbol</th><th>Dir</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Exit Reason</th><th>Bars</th><th>Strategy</th><th>Version</th><th>Time</th>
    </tr></thead><tbody>{rows}</tbody></table></div>"""

    return layout("📋 Trade History", tbl, "/scalper/trades")


def page_history():
    h = history()
    versions = h.get("versions",[])
    current = h.get("current_version","?")

    if not versions:
        return layout("📜 History", '<div class="card"><p>No version history.</p></div>', "/scalper/history")

    timeline = '<div class="ver-timeline">'
    for v in reversed(versions):
        ver = v.get("version","?")
        act = "active" if ver == current else ""
        desc = v.get("description","")
        ts = str(v.get("timestamp",""))[:19]
        verdict = v.get("verdict","")
        perf = v.get("performance",{})
        tr_count = perf.get("trades",0)
        pnl_val = perf.get("total_pnl_pct",0)

        perf_html = f'<span class="{pnl_cls(pnl_val)}">{pnl_val:+.3f}%</span> | {tr_count} trades' if tr_count > 0 else '<span style="color:var(--dim)">⏳ Awaiting results</span>'

        # Changes
        changes_html = ""
        for ch in v.get("changes",[]):
            changes_html += f'<div class="param-item"><span class="key">🔧 {ch.get("param","")}</span><span>{ch.get("old","")} → <strong>{ch.get("new","")}</strong></span></div>'

        # Params
        params_html = ""
        params = v.get("params",{})
        if params:
            items = "".join(f'<div class="param-item"><span class="key">{k}</span><span class="val">{val}</span></div>' for k,val in params.items())
            params_html = f'<details style="margin-top:12px"><summary style="cursor:pointer;color:var(--accent)">📋 Parameters</summary><div class="param-grid">{items}</div></details>'

        timeline += f"""<div class="ver-item {act}">
        <h3><span class="badge {"badge-green" if act else "badge-blue"}">{ver}</span> {" ← ACTIVE" if act else ""}</h3>
        <p style="color:var(--dim);font-size:13px">{ts} — {desc}</p>
        <p style="margin-top:8px">{perf_html}</p>
        <p style="margin-top:4px;font-size:13px">{verdict}</p>
        {f'<div class="param-grid" style="margin-top:8px">{changes_html}</div>' if changes_html else ""}
        {params_html}</div>"""

    timeline += "</div>"

    # Live comparison
    s = state()
    ct = s.get("completed_trades",[])
    comp = ""
    if ct:
        by_ver = {}
        for t in ct:
            vv = t.get("config_version","v1")
            by_ver.setdefault(vv,[]).append(t.get("realized_pnl_pct",0))
        if by_ver:
            rows = ""
            for vv, pnls in sorted(by_ver.items()):
                total = sum(pnls); wins = sum(1 for p in pnls if p>0); wr = wins/max(1,len(pnls))*100
                rows += f'<tr><td><span class="badge badge-purple">{vv}</span></td><td>{len(pnls)}</td><td>{pnl_badge(total)}</td><td>{wr:.0f}%</td></tr>'
            comp = f'<div class="card"><h3>📊 Live Comparison</h3><table><thead><tr><th>Version</th><th>Trades</th><th>PnL</th><th>WR</th></tr></thead><tbody>{rows}</tbody></table></div>'

    return layout("📜 Strategy History", comp + timeline, "/scalper/history")


def page_analyze():
    s = state()
    trades = s.get("completed_trades",[])
    archived = archive()
    all_trades = archived + trades

    if not all_trades:
        return layout("🔬 Analysis", '<div class="card"><p>No trades to analyze.</p></div>', "/scalper/analyze")

    total = len(all_trades)
    wins = [t for t in all_trades if t.get("realized_pnl_pct",0)>0]
    losses = [t for t in all_trades if t.get("realized_pnl_pct",0)<=0]
    total_pnl = sum(t.get("realized_pnl_pct",0) for t in all_trades)
    wr = len(wins)/max(1,total)*100
    avg_win = sum(t.get("realized_pnl_pct",0) for t in wins)/max(1,len(wins))
    avg_loss = sum(t.get("realized_pnl_pct",0) for t in losses)/max(1,len(losses))
    gp = sum(t.get("realized_pnl_pct",0) for t in wins)
    gl = abs(sum(t.get("realized_pnl_pct",0) for t in losses))
    pf = gp/max(0.001,gl)
    best = max(all_trades, key=lambda t: t.get("realized_pnl_pct",0))
    worst = min(all_trades, key=lambda t: t.get("realized_pnl_pct",0))

    summary = f"""<div class="grid">
    <div class="card metric"><span class="label">Total PnL</span><span class="value {pnl_cls(total_pnl)}">{total_pnl:+.3f}%</span></div>
    <div class="card metric"><span class="label">Win Rate</span><span class="value">{wr:.1f}%</span></div>
    <div class="card metric"><span class="label">Avg Win</span><span class="value pos">{avg_win:+.3f}%</span></div>
    <div class="card metric"><span class="label">Avg Loss</span><span class="value neg">{avg_loss:+.3f}%</span></div>
    <div class="card metric"><span class="label">Profit Factor</span><span class="value">{pf:.2f}</span></div>
    <div class="card metric"><span class="label">Trades</span><span class="value">{total}</span></div>
    </div>"""

    # ── Strategy Cards ────────────────────────────────────────
    strat_groups = {}
    for t in all_trades:
        sn = t.get("strategy_name","unknown")
        strat_groups.setdefault(sn,[]).append(t)

    strat_cards = '<h2 style="margin:20px 0 12px;font-size:18px">🧠 Strategy Breakdown</h2><div class="grid" style="grid-template-columns:1fr 1fr">'
    for sname, st in sorted(strat_groups.items(), key=lambda x: sum(t.get("realized_pnl_pct",0) for t in x[1]), reverse=True):
        s_total = len(st)
        s_longs = [t for t in st if t.get("direction")=="long"]
        s_shorts = [t for t in st if t.get("direction")=="short"]
        s_wins = [t for t in st if t.get("realized_pnl_pct",0)>0]
        s_pnl = sum(t.get("realized_pnl_pct",0) for t in st)
        s_wr = len(s_wins)/max(1,s_total)*100
        s_long_pnl = sum(t.get("realized_pnl_pct",0) for t in s_longs)
        s_short_pnl = sum(t.get("realized_pnl_pct",0) for t in s_shorts)
        s_gp = sum(t.get("realized_pnl_pct",0) for t in s_wins)
        s_gl = abs(sum(t.get("realized_pnl_pct",0) for t in st if t.get("realized_pnl_pct",0)<=0))
        s_pf = s_gp/max(0.001,s_gl)
        s_best = max(st, key=lambda t: t.get("realized_pnl_pct",0))
        s_worst = min(st, key=lambda t: t.get("realized_pnl_pct",0))
        s_best_v = s_best.get("realized_pnl_pct",0)
        s_worst_v = s_worst.get("realized_pnl_pct",0)

        long_wr = sum(1 for t in s_longs if t.get("realized_pnl_pct",0)>0)/max(1,len(s_longs))*100 if s_longs else 0
        short_wr = sum(1 for t in s_shorts if t.get("realized_pnl_pct",0)>0)/max(1,len(s_shorts))*100 if s_shorts else 0

        # Mini bar showing win/loss ratio
        bar_w = max(5, s_wr)
        bar_l = 100 - bar_w

        strat_cards += f'''<div class="card">
        <h3 style="margin-bottom:8px">🧠 {sname}</h3>
        <div style="display:flex;gap:4px;height:6px;border-radius:3px;overflow:hidden;margin-bottom:12px">
        <div style="width:{bar_w}%;background:var(--green);border-radius:3px"></div>
        <div style="width:{bar_l}%;background:var(--red);border-radius:3px"></div>
        </div>
        <div class="param-grid" style="grid-template-columns:1fr 1fr">
        <div class="param-item"><span class="key">Trades</span><span class="val">{s_total}</span></div>
        <div class="param-item"><span class="key">Win Rate</span><span class="val">{s_wr:.0f}%</span></div>
        <div class="param-item"><span class="key">🟢 Longs</span><span class="val">{len(s_longs)} ({long_wr:.0f}% WR)</span></div>
        <div class="param-item"><span class="key">🔴 Shorts</span><span class="val">{len(s_shorts)} ({short_wr:.0f}% WR)</span></div>
        <div class="param-item"><span class="key">Total PnL</span><span class="val {pnl_cls(s_pnl)}">{s_pnl:+.3f}%</span></div>
        <div class="param-item"><span class="key">Profit Factor</span><span class="val">{s_pf:.2f}</span></div>
        <div class="param-item"><span class="key">Long PnL</span><span class="val {pnl_cls(s_long_pnl)}">{s_long_pnl:+.3f}%</span></div>
        <div class="param-item"><span class="key">Short PnL</span><span class="val {pnl_cls(s_short_pnl)}">{s_short_pnl:+.3f}%</span></div>
        <div class="param-item"><span class="key">Best Trade</span><span class="val pos">{s_best_v:+.3f}% ({s_best.get("symbol","?")})</span></div>
        <div class="param-item"><span class="key">Worst Trade</span><span class="val neg">{s_worst_v:+.3f}% ({s_worst.get("symbol","?")})</span></div>
        </div>
        </div>'''
    strat_cards += '</div>'

    # ── Enriched breakdown tables ─────────────────────────────
    def breakdown_table(key, title, icon):
        groups = {}
        for t in all_trades:
            k = t.get(key,"unknown")
            groups.setdefault(k,[]).append(t)
        rows = ""
        for name, grp in sorted(groups.items(), key=lambda x: sum(t.get("realized_pnl_pct",0) for t in x[1]), reverse=True):
            pnls = [t.get("realized_pnl_pct",0) for t in grp]
            sp = sum(pnls); sw = sum(1 for p in pnls if p>0); swr = sw/max(1,len(pnls))*100
            longs = sum(1 for t in grp if t.get("direction")=="long")
            shorts = len(grp) - longs
            rows += f'<tr><td><strong>{name}</strong></td><td>{len(pnls)}</td><td>{longs}L / {shorts}S</td><td>{pnl_badge(sp)}</td><td>{swr:.0f}%</td></tr>'
        return f'<div class="card"><h3>{icon} {title}</h3><table><thead><tr><th>Name</th><th>Trades</th><th>L/S</th><th>PnL</th><th>WR</th></tr></thead><tbody>{rows}</tbody></table></div>'

    sym_tbl = breakdown_table("symbol","By Symbol","🪙")
    reason_tbl = breakdown_table("exit_reason","By Exit Reason","🚪")
    dir_tbl = breakdown_table("direction","By Direction","📐")
    ver_tbl = breakdown_table("config_version","By Version","🏷️")

    breakdown = f'<div class="grid" style="grid-template-columns:1fr 1fr">{sym_tbl}{reason_tbl}</div><div class="grid" style="grid-template-columns:1fr 1fr">{dir_tbl}{ver_tbl}</div>'

    # ── Full Trade Table (clickable) ──────────────────────────
    trade_rows = ""
    for i, t in enumerate(reversed(all_trades)):
        idx = len(all_trades) - 1 - i
        p = t.get("realized_pnl_pct",0)
        di = "🟢" if t.get("direction")=="long" else "🔴"
        entry_t = str(t.get("entry_time",""))[:16]
        exit_t = str(t.get("exit_time",""))[:16]
        trade_rows += f'''<tr style="cursor:pointer" onclick="window.location='/scalper/trade/{idx}'">
        <td>{i+1}</td>
        <td><a href="/scalper/trade/{idx}" style="font-weight:700">{t.get("symbol","?")}</a></td>
        <td>{di} {t.get("direction","?").upper()}</td>
        <td>{t.get("entry_price","?")}</td><td>{t.get("exit_price","?")}</td>
        <td class="{pnl_cls(p)}" style="font-weight:700">{p:+.3f}%</td>
        <td>{t.get("exit_reason","?")}</td>
        <td>{t.get("strategy_name","?")}</td>
        <td><span class="badge badge-purple">{t.get("config_version","?")}</span></td>
        <td style="font-size:11px;color:var(--dim)">{entry_t}</td>
        </tr>'''
    trade_table = f'''<div class="card" style="margin-top:20px">
    <h3 style="margin-bottom:12px">📋 All Trades ({total}) — click to inspect</h3>
    <div style="overflow-x:auto"><table><thead><tr>
    <th>#</th><th>Symbol</th><th>Dir</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Exit Reason</th><th>Strategy</th><th>Ver</th><th>Time</th>
    </tr></thead><tbody>{trade_rows}</tbody></table></div></div>'''

    return layout("🔬 Strategy Analysis", summary + strat_cards + breakdown + trade_table, "/scalper/analyze")


def page_trade(trade_idx: int):
    """Trade detail page with TradingView chart."""
    s = state()
    trades = s.get("completed_trades",[])
    archived = archive()
    all_trades = archived + trades

    if trade_idx < 0 or trade_idx >= len(all_trades):
        return layout("Trade", '<div class="card"><p>Trade not found.</p><a href="/analyze">← Back</a></div>', "/scalper/analyze")

    t = all_trades[trade_idx]
    symbol = t.get("symbol","?")
    direction = t.get("direction","?")
    entry_price = t.get("entry_price",0)
    exit_price = t.get("exit_price",0)
    pnl = t.get("realized_pnl_pct",0)
    entry_time = str(t.get("entry_time",""))[:19]
    exit_time = str(t.get("exit_time",""))[:19]
    stop_price = t.get("stop_price",0)
    tp_price = t.get("tp_price",0)
    exit_reason = t.get("exit_reason","?")
    strat = t.get("strategy_name","?")
    ver = t.get("config_version","?")
    bars_held = t.get("bars_held","?")
    be = t.get("breakeven_activated", False)

    di_icon = "🟢 LONG" if direction == "long" else "🔴 SHORT"
    tv_symbol = f"BYBIT:{symbol}.P"

    # Calculate R multiple if possible
    entry_f = float(entry_price) if entry_price else 0
    exit_f = float(exit_price) if exit_price else 0
    stop_f = float(stop_price) if stop_price else 0
    risk = abs(entry_f - stop_f) if stop_f else 0
    reward = abs(exit_f - entry_f) if exit_f else 0
    r_multiple = reward / risk if risk > 0 else 0

    # Price levels for visual
    prices = [p for p in [stop_f, entry_f, exit_f, float(tp_price) if tp_price else 0] if p > 0]
    if prices:
        p_min = min(prices) * 0.998; p_max = max(prices) * 1.002
        p_range = p_max - p_min if p_max != p_min else 0.001
        def pct_pos(price): return max(0, min(100, (price - p_min) / p_range * 100))
        entry_y = pct_pos(entry_f)
        exit_y = pct_pos(exit_f) if exit_f else 50
        stop_y = pct_pos(stop_f) if stop_f else 0
        tp_y = pct_pos(float(tp_price)) if tp_price else 100
    else:
        entry_y = exit_y = stop_y = tp_y = 50

    tp_display = f"{float(tp_price):.6g}" if tp_price else "—"

    body = f'''
    <style>
    .trade-ladder{{position:relative;width:60px;height:500px;background:linear-gradient(180deg,rgba(63,185,80,.05),rgba(248,81,73,.05));border-radius:8px;border:1px solid var(--border)}}
    .tl-line{{position:absolute;left:0;right:0;height:0;display:flex;align-items:center}}
    .tl-line .tl-tag{{position:absolute;right:-95px;white-space:nowrap;font-size:11px;font-weight:600;padding:2px 6px;border-radius:4px}}
    .tl-line::before{{content:'';position:absolute;left:0;right:0}}
    .tl-entry::before{{border-top:2px solid var(--accent)}}
    .tl-exit::before{{border-top:2px solid var(--orange)}}
    .tl-stop::before{{border-top:2px solid var(--red)}}
    .tl-tp::before{{border-top:2px dashed var(--green)}}
    .result-badge{{display:inline-block;padding:8px 20px;border-radius:12px;font-size:24px;font-weight:700;margin:8px 0}}
    </style>

    <div style="margin-bottom:16px"><a href="/analyze" style="font-size:14px">← Back to Analysis</a></div>

    <div class="grid" style="grid-template-columns:repeat(5,1fr);margin-bottom:16px">
    <div class="card metric"><span class="label">Result</span><span class="value {pnl_cls(pnl)}">{pnl:+.3f}%</span></div>
    <div class="card metric"><span class="label">R Multiple</span><span class="value">{r_multiple:.1f}R</span></div>
    <div class="card metric"><span class="label">Exit Reason</span><span class="value" style="font-size:14px">{exit_reason}</span></div>
    <div class="card metric"><span class="label">Bars Held</span><span class="value">{bars_held}</span></div>
    <div class="card metric"><span class="label">Strategy</span><span class="value" style="font-size:13px">{strat}</span></div>
    </div>

    <div class="grid" style="grid-template-columns:80px 1fr 300px;gap:12px">

    <!-- Trade Price Ladder -->
    <div class="card" style="padding:12px 8px;display:flex;flex-direction:column;align-items:center">
    <div style="font-size:11px;color:var(--dim);margin-bottom:8px;text-align:center">Levels</div>
    <div class="trade-ladder">
    <div class="tl-line tl-tp" style="bottom:{tp_y}%"><span class="tl-tag" style="background:rgba(63,185,80,.15);color:var(--green)">TP {tp_display}</span></div>
    <div class="tl-line tl-entry" style="bottom:{entry_y}%"><span class="tl-tag" style="background:rgba(88,166,255,.15);color:var(--accent)">EN {entry_f:.6g}</span></div>
    <div class="tl-line tl-exit" style="bottom:{exit_y}%"><span class="tl-tag" style="background:rgba(240,136,62,.2);color:var(--orange)">EX {exit_f:.6g}</span></div>
    <div class="tl-line tl-stop" style="bottom:{stop_y}%"><span class="tl-tag" style="background:rgba(248,81,73,.15);color:var(--red)">SL {stop_f:.6g}</span></div>
    </div>
    </div>

    <!-- TradingView Chart -->
    <div class="card" style="padding:0;overflow:hidden;border-radius:12px;min-height:500px">
    <div id="tv_chart" style="height:500px"></div>
    <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
    <script type="text/javascript">
    new TradingView.widget({{"autosize":true,"symbol":"{tv_symbol}","interval":"5","timezone":"Etc/UTC",
    "theme":"dark","style":"1","locale":"en","toolbar_bg":"#0a0e17","enable_publishing":false,
    "hide_side_toolbar":false,"allow_symbol_change":true,"container_id":"tv_chart",
    "studies":["Volume@tv-basicstudies"],
    "width":"100%","height":"500"}});
    </script>
    </div>

    <!-- Trade Details -->
    <div>
    <div class="card" style="margin-bottom:16px;text-align:center">
    <div class="result-badge" style="background:{'rgba(63,185,80,.15)' if pnl>0 else 'rgba(248,81,73,.15)'};color:{'var(--green)' if pnl>0 else 'var(--red)'}">{pnl:+.3f}%</div>
    <h3>{di_icon} {symbol}</h3>
    </div>

    <div class="card" style="margin-bottom:16px">
    <h3 style="margin-bottom:8px;color:var(--accent)">📥 Entry</h3>
    <div class="param-grid" style="grid-template-columns:1fr">
    <div class="param-item"><span class="key">Price</span><span class="val" style="color:var(--accent)">{entry_f:.6g}</span></div>
    <div class="param-item"><span class="key">Time</span><span class="val" style="font-size:12px">{entry_time}</span></div>
    <div class="param-item"><span class="key">Stop Loss</span><span class="val" style="color:var(--red)">{stop_f:.6g}</span></div>
    <div class="param-item"><span class="key">Take Profit</span><span class="val" style="color:var(--green)">{tp_display}</span></div>
    </div>
    </div>

    <div class="card" style="margin-bottom:16px">
    <h3 style="margin-bottom:8px;color:var(--orange)">📤 Exit</h3>
    <div class="param-grid" style="grid-template-columns:1fr">
    <div class="param-item"><span class="key">Price</span><span class="val" style="color:var(--orange)">{exit_f:.6g}</span></div>
    <div class="param-item"><span class="key">Time</span><span class="val" style="font-size:12px">{exit_time}</span></div>
    <div class="param-item"><span class="key">Reason</span><span class="val">{exit_reason}</span></div>
    <div class="param-item"><span class="key">Breakeven</span><span class="val">{'✅ Yes' if be else 'No'}</span></div>
    </div>
    </div>

    <div class="card">
    <div class="param-grid" style="grid-template-columns:1fr">
    <div class="param-item"><span class="key">Version</span><span class="val"><span class="badge badge-purple">{ver}</span></span></div>
    <div class="param-item"><span class="key">Bars Held</span><span class="val">{bars_held}</span></div>
    <div class="param-item"><span class="key">R Multiple</span><span class="val">{r_multiple:.2f}R</span></div>
    </div>
    </div>
    </div>
    </div>
    '''

    return layout(f"🔍 Trade #{len(all_trades)-trade_idx} · {di_icon} {symbol} · <span class='{pnl_cls(pnl)}'>{pnl:+.3f}%</span>", body, "/scalper/analyze")


def page_control():
    ks = kill_active()
    s = state()
    h = history()
    ver = h.get("current_version","?")

    # Current params
    params = {}
    for v in h.get("versions",[]):
        if v.get("version") == ver:
            params = v.get("params",{})

    ks_html = f"""<div class="ctrl-section">
    <h3>🛑 Kill Switch</h3>
    <p style="margin:8px 0">Status: <strong class="{"neg" if ks else "pos"}">{"🛑 ACTIVE — Trading halted" if ks else "✅ OFF — Trading active"}</strong></p>
    <p style="color:var(--dim);font-size:13px">Control via Telegram: send /stop or /resume</p></div>"""

    params_html = ""
    if params:
        items = "".join(f'<div class="param-item"><span class="key">{k}</span><span class="val">{v}</span></div>' for k,v in params.items())
        params_html = f'<div class="ctrl-section"><h3>⚙️ Current Strategy Params ({ver})</h3><div class="param-grid">{items}</div><p style="margin-top:12px;color:var(--dim);font-size:13px">Edit via Telegram: /rollback vN to switch versions</p></div>'

    # Process info
    active = s.get("active_positions",{})
    symbols = s.get("symbols",[])
    info = f"""<div class="ctrl-section"><h3>📡 Soldier Info</h3>
    <div class="param-grid">
    <div class="param-item"><span class="key">Symbols</span><span class="val">{len(symbols)}</span></div>
    <div class="param-item"><span class="key">Active Positions</span><span class="val">{len(active)}</span></div>
    <div class="param-item"><span class="key">Config Version</span><span class="val">{ver}</span></div>
    <div class="param-item"><span class="key">Kill Switch</span><span class="val">{"ON" if ks else "OFF"}</span></div>
    </div></div>"""

    tg_cmds = """<div class="ctrl-section"><h3>📱 Telegram Commands</h3>
    <table><thead><tr><th>Command</th><th>Action</th></tr></thead><tbody>
    <tr><td><code>/stop</code></td><td>🛑 Emergency stop</td></tr>
    <tr><td><code>/resume</code></td><td>▶️ Resume trading</td></tr>
    <tr><td><code>/status</code></td><td>📊 Current status</td></tr>
    <tr><td><code>/analyze</code></td><td>🔬 Trade analysis</td></tr>
    <tr><td><code>/history</code></td><td>📜 Version history</td></tr>
    <tr><td><code>/rollback v1</code></td><td>🔄 Rollback to version</td></tr>
    <tr><td><code>/closeall</code></td><td>💀 Force close all</td></tr>
    </tbody></table></div>"""

    return layout("⚙️ Control Panel", ks_html + params_html + info + tg_cmds, "/scalper/control")


# ─── Position Detail Page ────────────────────────────────────

def page_position(symbol: str):
    """Live position detail with TradingView chart and manual close."""
    s = state()
    active = s.get("active_positions", {})
    pos = active.get(symbol)

    if not pos:
        return layout(f"Position: {symbol}", f'<div class="card"><p>No active position for {symbol}</p><p><a href="/scalper">← Back</a></p></div>', "/scalper")

    direction = pos.get("direction", "?")
    entry = float(pos.get("entry_price", 0))
    stop = float(pos.get("stop_price", 0))
    tp = float(pos.get("tp_price", 0))
    stop_pct = float(pos.get("stop_pct", 0))
    tp_pct = float(pos.get("tp_pct", 0))
    size = pos.get("size_usdt", 0)
    strat = pos.get("strategy_name", "?")
    ver = pos.get("config_version", "?")
    entry_time = pos.get("entry_time", "")
    be = pos.get("breakeven_activated", False)

    cur_price = fetch_price(symbol)

    # Calculate SL/TP from percentages if absolute prices missing
    if entry > 0 and stop == 0 and stop_pct > 0:
        if direction == "long":
            stop = entry * (1 - stop_pct / 100)
        else:
            stop = entry * (1 + stop_pct / 100)
    if entry > 0 and tp == 0 and tp_pct > 0:
        if direction == "long":
            tp = entry * (1 + tp_pct / 100)
        else:
            tp = entry * (1 - tp_pct / 100)

    upnl = calc_upnl(pos, cur_price) if cur_price else 0
    upnl_cls = "pos" if upnl >= 0 else "neg"
    price_display = f"{cur_price:.6g}" if cur_price else "\u2014"

    # Dollar PnL calculation
    s_leverage = float(pos.get("leverage", 20))
    s_size_usdt = float(pos.get("size_usdt", 0) or size)
    s_notional = s_size_usdt * s_leverage
    upnl_usd = s_notional * upnl / 100
    upnl_usd_str = f' (${upnl_usd:+,.0f})' if upnl_usd != 0 else ''

    # Distance to stop/tp from current price
    if cur_price and entry:
        if direction == "long":
            dist_stop = (cur_price - stop) / cur_price * 100 if stop else 0
            dist_tp = (tp - cur_price) / cur_price * 100 if tp else 0
        else:
            dist_stop = (stop - cur_price) / cur_price * 100 if stop else 0
            dist_tp = (cur_price - tp) / cur_price * 100 if tp else 0
    else:
        dist_stop = dist_tp = 0

    di_icon = "🟢 LONG" if direction == "long" else "🔴 SHORT"
    be_badge = '<span class="badge badge-green">✅ BE Active</span>' if be else '<span class="badge" style="background:rgba(139,148,158,.15);color:var(--dim)">Not yet</span>'

    # TradingView symbol format
    tv_symbol = f"BYBIT:{symbol}.P"

    # Price levels visual: calculate % positions on a vertical scale
    all_prices = [p for p in [stop, entry, cur_price, tp] if p > 0]
    if all_prices:
        p_min = min(all_prices) * 0.998
        p_max = max(all_prices) * 1.002
        p_range = p_max - p_min if p_max != p_min else 0.001
        def pct_pos(price):
            return max(0, min(100, (price - p_min) / p_range * 100))
        entry_y = pct_pos(entry)
        stop_y = pct_pos(stop)
        tp_y = pct_pos(tp)
        cur_y = pct_pos(cur_price) if cur_price else 50
    else:
        entry_y = stop_y = tp_y = cur_y = 50

    body = f'''
    <style>
    .price-ladder{{position:relative;width:60px;height:500px;background:linear-gradient(180deg,rgba(63,185,80,.05),rgba(248,81,73,.05));border-radius:8px;border:1px solid var(--border)}}
    .pl-line{{position:absolute;left:0;right:0;height:0;display:flex;align-items:center}}
    .pl-line .pl-tag{{position:absolute;right:-90px;white-space:nowrap;font-size:11px;font-weight:600;padding:2px 6px;border-radius:4px}}
    .pl-line::before{{content:'';position:absolute;left:0;right:0}}
    .pl-entry::before{{border-top:2px solid var(--accent)}}
    .pl-stop::before{{border-top:2px solid var(--red)}}
    .pl-tp::before{{border-top:2px dashed var(--green)}}
    .pl-cur::before{{border-top:3px solid var(--orange)}}
    .pl-cur .pl-dot{{position:absolute;left:50%;transform:translateX(-50%);width:12px;height:12px;border-radius:50%;background:var(--orange);box-shadow:0 0 8px var(--orange);animation:pulse 2s infinite}}
    @keyframes pulse{{0%,100%{{opacity:1;transform:translateX(-50%) scale(1)}}50%{{opacity:.6;transform:translateX(-50%) scale(1.3)}}}}
    .close-modal{{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.8);z-index:1000;align-items:center;justify-content:center}}
    .close-modal.show{{display:flex}}
    .close-modal-box{{background:var(--card);border:2px solid var(--red);border-radius:16px;padding:32px;text-align:center;max-width:400px;width:90%}}
    .close-modal-box h2{{color:var(--red);margin-bottom:16px}}
    .countdown{{font-size:48px;font-weight:700;color:var(--red);margin:16px 0}}
    .close-modal .btn{{margin:8px;min-width:120px}}
    </style>

    <div style="margin-bottom:16px"><a href="/scalper" style="font-size:14px">\u2190 Back to Dashboard</a></div>

    <div class="grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:16px">
    <div class="card metric"><span class="label">Current Price</span><span class="value">{price_display}</span></div>
    <div class="card metric"><span class="label">Unrealized P/L</span><span class="value {upnl_cls}">{upnl:+.3f}%<span style="display:block;font-size:14px;margin-top:2px">${upnl_usd:+,.2f}</span></span></div>
    <div class="card metric" {'style="border:2px solid var(--red);animation:pulse 1s infinite"' if dist_stop < 0 else ''}><span class="label">To Stop</span><span class="value {'pos' if dist_stop > 0 else 'neg'}">{dist_stop:+.2f}%</span></div>
    <div class="card metric" {'style="border:2px solid var(--green);animation:pulse 1s infinite"' if dist_tp < 0 else ''}><span class="label">To TP</span><span class="value {'neg' if dist_tp > 0 else 'pos'}">{dist_tp:+.2f}%</span></div>
    </div>

    {'<div class="card" style="background:rgba(248,81,73,.1);border:2px solid var(--red);margin-bottom:16px;text-align:center"><h3 style="color:var(--red)">⚠️ SL BREACHED!</h3><p style="color:var(--dim)">Price passed Stop Loss level. Bot may not be running — close manually!</p></div>' if dist_stop < 0 else ''}
    {'<div class="card" style="background:rgba(63,185,80,.1);border:2px solid var(--green);margin-bottom:16px;text-align:center"><h3 style="color:var(--green)">🎯 TP REACHED!</h3><p style="color:var(--dim)">Price passed Take Profit level. Bot may not be running — consider closing!</p></div>' if dist_tp < 0 else ''}

    <div class="grid" style="grid-template-columns:80px 1fr 300px;gap:12px">

    <!-- Price Levels Ladder -->
    <div class="card" style="padding:12px 8px;display:flex;flex-direction:column;align-items:center">
    <div style="font-size:11px;color:var(--dim);margin-bottom:8px;text-align:center">Levels</div>
    <div class="price-ladder">
    <div class="pl-line pl-tp" style="bottom:{tp_y}%"><span class="pl-tag" style="background:rgba(63,185,80,.15);color:var(--green)">TP {tp:.6g}</span></div>
    <div class="pl-line pl-entry" style="bottom:{entry_y}%"><span class="pl-tag" style="background:rgba(88,166,255,.15);color:var(--accent)">EN {entry:.6g}</span></div>
    <div class="pl-line pl-cur" style="bottom:{cur_y}%"><div class="pl-dot"></div><span class="pl-tag" style="background:rgba(240,136,62,.2);color:var(--orange)">NOW {price_display}</span></div>
    <div class="pl-line pl-stop" style="bottom:{stop_y}%"><span class="pl-tag" style="background:rgba(248,81,73,.15);color:var(--red)">SL {stop:.6g}</span></div>
    </div>
    </div>

    <!-- TradingView Chart with position lines -->
    <div class="card" style="padding:0;overflow:hidden;border-radius:12px;min-height:500px">
    <div id="tv_chart" style="height:500px"></div>
    <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
    <script type="text/javascript">
    var tvWidget = new TradingView.widget({{"autosize":true,"symbol":"{tv_symbol}","interval":"5","timezone":"Etc/UTC",
    "theme":"dark","style":"1","locale":"en","toolbar_bg":"#0a0e17","enable_publishing":false,
    "hide_side_toolbar":false,"allow_symbol_change":true,"container_id":"tv_chart",
    "studies":["Volume@tv-basicstudies"],
    "width":"100%","height":"500"}});
    tvWidget.onChartReady(function() {{
        var chart = tvWidget.activeChart();
        // Entry line (blue dashed)
        if ({entry} > 0) {{
            chart.createShape({{price: {entry}}}, {{shape: 'horizontal_line', lock: true, disableSelection: true, overrides: {{linecolor: '#58a6ff', linestyle: 2, linewidth: 1, showLabel: true, text: 'ENTRY {entry:.6g}'}}}});
        }}
        // Stop Loss line (red dashed)
        if ({stop} > 0) {{
            chart.createShape({{price: {stop}}}, {{shape: 'horizontal_line', lock: true, disableSelection: true, overrides: {{linecolor: '#f85149', linestyle: 2, linewidth: 1, showLabel: true, text: 'SL {stop:.6g}'}}}});
        }}
        // Take Profit line (green dashed)
        if ({tp} > 0) {{
            chart.createShape({{price: {tp}}}, {{shape: 'horizontal_line', lock: true, disableSelection: true, overrides: {{linecolor: '#3fb950', linestyle: 2, linewidth: 1, showLabel: true, text: 'TP {tp:.6g}'}}}});
        }}
    }});
    </script>
    </div>

    <!-- Position Info + Close -->
    <div>
    <div class="card" style="margin-bottom:16px">
    <h3 style="margin-bottom:12px">{di_icon} {symbol}</h3>
    <div class="param-grid" style="grid-template-columns:1fr">
    <div class="param-item"><span class="key">Entry</span><span class="val" style="color:var(--accent)">{entry:.6g}</span></div>
    <div class="param-item"><span class="key">Stop Loss</span><span class="val" style="color:var(--red)">{stop:.6g} ({stop_pct:.2f}%)</span></div>
    <div class="param-item"><span class="key">Take Profit</span><span class="val" style="color:var(--green)">{tp:.6g} ({tp_pct:.2f}%)</span></div>
    <div class="param-item"><span class="key">Size</span><span class="val">${size} <span style="font-size:11px;color:var(--dim)">({upnl:+.2f}% = ${size * upnl / 100:+.2f})</span></span></div>
    <div class="param-item"><span class="key">Strategy</span><span class="val">{strat}</span></div>
    <div class="param-item"><span class="key">Version</span><span class="val"><span class="badge badge-purple">{ver}</span></span></div>
    <div class="param-item"><span class="key">Breakeven</span><span class="val">{be_badge}</span></div>
    <div class="param-item"><span class="key">Opened</span><span class="val" style="font-size:12px">{str(entry_time)[:19]}</span></div>
    </div>
    </div>

    <div class="card" style="text-align:center">
    <h3 style="margin-bottom:12px;color:var(--red)">⚠️ Manual Control</h3>
    <button onclick="showCloseModal()" class="btn btn-red" style="width:100%;padding:14px;font-size:16px">💀 CLOSE {symbol}</button>
    <p style="margin-top:8px;color:var(--dim);font-size:12px">Force-close with 7s confirmation</p>
    </div>
    </div>
    </div>

    <!-- Close Confirmation Modal -->
    <div id="closeModal" class="close-modal">
    <div class="close-modal-box">
    <h2>💀 CLOSE {symbol}?</h2>
    <p style="color:var(--dim);margin-bottom:8px">{di_icon} @ {entry:.6g}</p>
    <p>Current uPnL: <strong class="{upnl_cls}" style="font-size:20px">{upnl:+.3f}%</strong></p>
    <div class="countdown" id="countdown">7</div>
    <p style="color:var(--dim);font-size:13px;margin-bottom:20px">Confirm within countdown or cancel</p>
    <div style="display:flex;align-items:center;justify-content:center;gap:16px">
    <form method="POST" action="/scalper/close/{symbol}" id="closeForm" style="margin:0;padding:0;display:block">
    <button type="submit" class="btn btn-red" id="confirmBtn" disabled style="opacity:0.5;width:200px;padding:14px 0;font-size:15px;font-weight:700">💀 CONFIRM CLOSE</button>
    </form>
    <button onclick="hideCloseModal()" class="btn" style="background:var(--border);width:140px;padding:14px 0;font-size:15px;font-weight:600">❌ CANCEL</button>
    </div>
    </div>
    </div>

    <style>
    @keyframes btnGlow {{
        0%, 100% {{ box-shadow: 0 0 8px rgba(248,81,73,.4); }}
        50% {{ box-shadow: 0 0 20px rgba(248,81,73,.8), 0 0 40px rgba(248,81,73,.3); }}
    }}
    .btn-ready {{ animation: btnGlow 1s ease-in-out infinite !important; opacity: 1 !important; }}
    </style>

    <script>
    let countdownTimer = null;
    function showCloseModal() {{
        window._modalOpen = true;
        const modal = document.getElementById('closeModal');
        const btn = document.getElementById('confirmBtn');
        const cd = document.getElementById('countdown');
        btn.disabled = true;
        btn.style.opacity = '0.5';
        btn.classList.remove('btn-ready');
        modal.classList.add('show');
        let sec = 7;
        cd.textContent = sec;
        countdownTimer = setInterval(() => {{
            sec--;
            cd.textContent = sec;
            if (sec <= 0) {{
                clearInterval(countdownTimer);
                cd.textContent = '\u2705';
                btn.disabled = false;
                btn.classList.add('btn-ready');
            }}
        }}, 1000);
    }}
    function hideCloseModal() {{
        clearInterval(countdownTimer);
        const btn = document.getElementById('confirmBtn');
        btn.classList.remove('btn-ready');
        document.getElementById('closeModal').classList.remove('show');
        window._modalOpen = false;
    }}
    </script>
    '''

    return layout(f"📡 {di_icon} {symbol} · <span class='{upnl_cls}'>{upnl:+.3f}%{upnl_usd_str}</span>", body, "/scalper")


def handle_close_position(symbol: str) -> str:
    """Force-close a position via POST request."""
    s = state()
    active = s.get("active_positions", {})
    pos = active.get(symbol)
    if not pos:
        return layout("Close", f'<div class="card"><p>No position for {symbol}</p><a href="/">← Back</a></div>', "/scalper")

    cur_price = fetch_price(symbol)
    upnl = calc_upnl(pos, cur_price) if cur_price else 0

    # Close on exchange if available
    ex_msg = ""
    if _executor:
        try:
            result = _executor.close_position_verified(symbol)
            ex_msg = f" | Exchange: ✅ closed"
        except Exception as e:
            ex_msg = f" | Exchange: ⚠️ {e}"

    pos["exit_reason"] = "manual_close_hq"
    pos["exit_time"] = datetime.now(timezone.utc).isoformat()
    pos["exit_price"] = cur_price
    pos["realized_pnl_pct"] = round(upnl, 4)

    trades = s.get("completed_trades", [])
    trades.append(pos)
    s["completed_trades"] = trades
    del active[symbol]
    s["active_positions"] = active

    if upnl > 0:
        s["wins"] = s.get("wins", 0) + 1
    else:
        s["losses"] = s.get("losses", 0) + 1
    s["total_pnl_pct"] = s.get("total_pnl_pct", 0) + upnl
    s["last_updated"] = datetime.now(timezone.utc).isoformat()

    (SD / "paper_state_multi.json").write_text(json.dumps(s, indent=2, default=str), encoding="utf-8")

    return layout("✅ Position Closed", f'''
    <div class="card" style="text-align:center">
    <h2 style="margin-bottom:16px">✅ {symbol} Closed</h2>
    <p class="{"pos" if upnl >= 0 else "neg"}" style="font-size:28px;font-weight:700">{upnl:+.3f}%</p>
    <p style="margin-top:8px;color:var(--dim)">Exit price: {cur_price:.6g} | Reason: manual_close_hq</p>
    <a href="/scalper" class="btn btn-blue" style="margin-top:20px">← Dashboard</a>
    </div>
    ''', "/")


# ─── Pump Hunter Position Detail ─────────────────────────────

def page_pump_position(state_key: str):
    """Live pump position detail with TradingView chart, price ladder, and manual close."""
    ph = ph_state()
    if not ph:
        return layout("Pump Position", '<div class="card"><p>No pump hunter data.</p><a href="/pumps">← Back</a></div>', "/pumps")

    active = ph.get("active_positions", {})
    pos = active.get(state_key)
    if not pos:
        return layout(f"Pump Position: {state_key}", f'<div class="card"><p>No active position for <code>{state_key}</code></p><p><a href="/pumps">← Back</a></p></div>', "/pumps")

    symbol = pos.get("symbol", state_key.split(":")[0])
    exchange = pos.get("exchange", "bybit")
    direction = pos.get("direction", "long")
    entry = float(pos.get("entry_price", 0))
    stop = float(pos.get("stop_loss", pos.get("trailing_stop", 0)))
    trail = float(pos.get("trailing_stop", 0))
    peak = float(pos.get("peak_price", 0))
    pump_pct = pos.get("pump_pct", 0)
    strat_ver = pos.get("strategy_version", "v2")
    phase = pos.get("phase", "?")
    rev_score = pos.get("reversal_score", 0)
    addon_done = pos.get("addon_done", False)
    leverage = pos.get("leverage", 20)
    size_pct = pos.get("size_pct", 20)
    p_cons = pos.get("p_consolidation", 0)
    detected_at = pos.get("detected_at", 0)

    cur_price = fetch_price(symbol, exchange)

    if direction == "long":
        upnl = ((cur_price / entry) - 1) * 100 if entry > 0 and cur_price > 0 else pos.get("pnl_pct", 0)
    else:
        upnl = ((entry / cur_price) - 1) * 100 if entry > 0 and cur_price > 0 else pos.get("pnl_pct", 0)
    upnl_cls = "pos" if upnl >= 0 else "neg"
    price_display = f"{cur_price:.6g}" if cur_price else "\u2014"

    # Dollar PnL calculation
    demo_balance = ph.get("demo_balance", 10000)
    p_size_u = float(pos.get("size_usdt", 0) or 0)
    if p_size_u == 0:
        p_size_u = demo_balance * float(size_pct) / 100
    p_notional = p_size_u * float(leverage)
    upnl_usd = p_notional * upnl / 100
    upnl_usd_str = f' (${upnl_usd:+,.0f})' if upnl_usd != 0 else ''

    # Distance to trailing stop
    if cur_price and trail > 0:
        if direction == "long":
            dist_stop = (cur_price - trail) / cur_price * 100
        else:
            dist_stop = (trail - cur_price) / cur_price * 100
    else:
        dist_stop = 0

    di_icon = "\U0001f7e2 LONG" if direction == "long" else "\U0001f534 SHORT"
    ver_badge = f'<span class="badge badge-purple">{strat_ver.upper()}</span>' if strat_ver == 'v2' else f'<span class="badge" style="background:rgba(139,148,158,.15);color:var(--dim)">{strat_ver.upper()}</span>'

    # TradingView symbol — use Bybit perp as primary (most reliable on TV)
    # MEXC/GateIO/Bitget perps often missing from TradingView
    tv_exchange_map = {"bybit": "BYBIT", "mexc": "MEXC", "gateio": "GATEIO", "bitget": "BITGET"}
    tv_exchange = tv_exchange_map.get(exchange, "BYBIT")
    # Primary: try native exchange perp, fallback: Bybit perp, then Binance spot
    tv_symbol = f"{tv_exchange}:{symbol}.P"
    tv_fallback1 = f"BYBIT:{symbol}.P"
    tv_fallback2 = f"BINANCE:{symbol}"

    # Price ladder
    all_prices = [p for p in [trail, entry, cur_price, peak] if p > 0]
    if all_prices:
        p_min = min(all_prices) * 0.998
        p_max = max(all_prices) * 1.002
        p_range = p_max - p_min if p_max != p_min else 0.001
        def pct_pos(price):
            return max(0, min(100, (price - p_min) / p_range * 100))
        entry_y = pct_pos(entry)
        stop_y = pct_pos(trail)
        peak_y = pct_pos(peak)
        cur_y = pct_pos(cur_price) if cur_price else 50
    else:
        entry_y = stop_y = peak_y = cur_y = 50

    # Strategy-specific info
    addon_text = "\u2705 Done" if addon_done else "Pending"
    if strat_ver == "v2":
        strat_info = f'''
        <div class="param-item"><span class="key">Phase</span><span class="val"><span class="badge badge-purple">Phase {phase}</span></span></div>
        <div class="param-item"><span class="key">Reversal Score</span><span class="val">{rev_score}/12</span></div>
        <div class="param-item"><span class="key">Addon</span><span class="val">{addon_text}</span></div>
        <div class="param-item"><span class="key">Leverage</span><span class="val">{leverage}x</span></div>
        <div class="param-item"><span class="key">Size</span><span class="val">{size_pct}%</span></div>'''
    else:
        strat_info = f'''
        <div class="param-item"><span class="key">Trail Mode</span><span class="val">Adaptive 30%\u219215%</span></div>
        <div class="param-item"><span class="key">Leverage</span><span class="val">{leverage}x</span></div>
        <div class="param-item"><span class="key">Size</span><span class="val">{size_pct}%</span></div>'''

    url_key = urllib.parse.quote(state_key, safe='')

    # Pre-compute strings that would need backslashes in f-string
    back_link = '\u2190 Back to Pump Hunter'
    warn_icon = '\u26a0\ufe0f'
    skull = '\U0001f480'
    check = '\u2705'
    cancel_icon = '\u274c'
    trail_breach_html = f'<div class="card" style="background:rgba(248,81,73,.1);border:2px solid var(--red);margin-bottom:16px;text-align:center"><h3 style="color:var(--red)">{warn_icon} TRAIL STOP BREACHED!</h3><p style="color:var(--dim)">Price passed trailing stop level.</p></div>' if dist_stop < 0 else ''

    body = f'''
    <style>
    .price-ladder{{position:relative;width:60px;height:500px;background:linear-gradient(180deg,rgba(63,185,80,.05),rgba(248,81,73,.05));border-radius:8px;border:1px solid var(--border)}}
    .pl-line{{position:absolute;left:0;right:0;height:0;display:flex;align-items:center}}
    .pl-line .pl-tag{{position:absolute;right:-90px;white-space:nowrap;font-size:11px;font-weight:600;padding:2px 6px;border-radius:4px}}
    .pl-line::before{{content:'';position:absolute;left:0;right:0}}
    .pl-entry::before{{border-top:2px solid var(--accent)}}
    .pl-stop::before{{border-top:2px solid var(--red)}}
    .pl-peak::before{{border-top:2px dashed var(--green)}}
    .pl-cur::before{{border-top:3px solid var(--orange)}}
    .pl-cur .pl-dot{{position:absolute;left:50%;transform:translateX(-50%);width:12px;height:12px;border-radius:50%;background:var(--orange);box-shadow:0 0 8px var(--orange);animation:pulse 2s infinite}}
    @keyframes pulse{{0%,100%{{opacity:1;transform:translateX(-50%) scale(1)}}50%{{opacity:.6;transform:translateX(-50%) scale(1.3)}}}}
    .close-modal{{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.8);z-index:1000;align-items:center;justify-content:center}}
    .close-modal.show{{display:flex}}
    .close-modal-box{{background:var(--card);border:2px solid var(--red);border-radius:16px;padding:32px;text-align:center;max-width:400px;width:90%}}
    .close-modal-box h2{{color:var(--red);margin-bottom:16px}}
    .countdown{{font-size:48px;font-weight:700;color:var(--red);margin:16px 0}}
    .close-modal .btn{{margin:8px;min-width:120px}}
    @keyframes btnGlow {{
        0%, 100% {{ box-shadow: 0 0 8px rgba(248,81,73,.4); }}
        50% {{ box-shadow: 0 0 20px rgba(248,81,73,.8), 0 0 40px rgba(248,81,73,.3); }}
    }}
    .btn-ready {{ animation: btnGlow 1s ease-in-out infinite !important; opacity: 1 !important; }}
    </style>

    <div style="margin-bottom:16px"><a href="/pumps" style="font-size:14px">{back_link}</a></div>

    <div class="grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:16px">
    <div class="card metric"><span class="label">Current Price</span><span class="value">{price_display}</span></div>
    <div class="card metric"><span class="label">Unrealized P/L</span><span class="value {upnl_cls}">{upnl:+.2f}%<span style="display:block;font-size:14px;margin-top:2px">${upnl_usd:+,.2f}</span></span></div>
    <div class="card metric" {'style="border:2px solid var(--red);animation:pulse 1s infinite"' if dist_stop < 0 else ''}><span class="label">To Trail Stop</span><span class="value {'pos' if dist_stop > 0 else 'neg'}">{dist_stop:+.2f}%</span></div>
    <div class="card metric"><span class="label">Peak</span><span class="value pos">{peak:.6g}</span></div>
    </div>

    {'<div class="card" style="background:rgba(248,81,73,.1);border:2px solid var(--red);margin-bottom:16px;text-align:center"><h3 style="color:var(--red)">{warn_icon} TRAIL STOP BREACHED!</h3><p style="color:var(--dim)">Price passed trailing stop level.</p></div>' if dist_stop < 0 else ''}

    <div class="grid" style="grid-template-columns:80px 1fr 300px;gap:12px">

    <!-- Price Levels Ladder -->
    <div class="card" style="padding:12px 8px;display:flex;flex-direction:column;align-items:center">
    <div style="font-size:11px;color:var(--dim);margin-bottom:8px;text-align:center">Levels</div>
    <div class="price-ladder">
    <div class="pl-line pl-peak" style="bottom:{peak_y}%"><span class="pl-tag" style="background:rgba(63,185,80,.15);color:var(--green)">PK {peak:.6g}</span></div>
    <div class="pl-line pl-entry" style="bottom:{entry_y}%"><span class="pl-tag" style="background:rgba(88,166,255,.15);color:var(--accent)">EN {entry:.6g}</span></div>
    <div class="pl-line pl-cur" style="bottom:{cur_y}%"><div class="pl-dot"></div><span class="pl-tag" style="background:rgba(240,136,62,.2);color:var(--orange)">NOW {price_display}</span></div>
    <div class="pl-line pl-stop" style="bottom:{stop_y}%"><span class="pl-tag" style="background:rgba(248,81,73,.15);color:var(--red)">TS {trail:.6g}</span></div>
    </div>
    </div>

    <!-- TradingView Chart -->
    <div class="card" style="padding:0;overflow:hidden;border-radius:12px;min-height:500px;position:relative">
    <div id="tv_chart" style="height:500px"></div>
    <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
    <script type="text/javascript">
    // Build candidate list: native exchange perp → major exchanges perp → spot
    var candidates = [
        "{tv_symbol}",
        "BINANCE:{symbol}.P",
        "OKX:{symbol}.P",
        "BYBIT:{symbol}.P",
        "BINANCE:{symbol}",
        "OKX:{symbol}",
        "BYBIT:{symbol}",
        "MEXC:{symbol}",
        "BITGET:{symbol}"
    ];
    // Remove duplicates preserving order
    var seen = {{}};
    var tvSymbols = [];
    for (var i = 0; i < candidates.length; i++) {{
        if (!seen[candidates[i]]) {{
            seen[candidates[i]] = true;
            tvSymbols.push(candidates[i]);
        }}
    }}
    // Use first candidate (best guess). TradingView widget handles unknown symbols gracefully
    // and allow_symbol_change=true lets user search manually if needed
    var bestSym = tvSymbols[0];
    // For major coins, prefer Binance perp (most reliable on TV)
    var majorCoins = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","DOGEUSDT","ADAUSDT","TONUSDT","DOTUSDT","LTCUSDT","LINKUSDT","AVAXUSDT","MATICUSDT","UNIUSDT","APTUSDT","NEARUSDT","ARBUSDT","OPUSDT","SUIUSDT","SEIUSDT","JUPUSDT","WIFUSDT","PEPEUSDT","BONKUSDT","FLOKIUSDT"];
    if (majorCoins.indexOf("{symbol}") >= 0) {{
        bestSym = "BINANCE:{symbol}.P";
    }}
    new TradingView.widget({{"autosize":true,"symbol":bestSym,"interval":"60","timezone":"Etc/UTC",
    "theme":"dark","style":"1","locale":"en","toolbar_bg":"#0a0e17","enable_publishing":false,
    "hide_side_toolbar":false,"allow_symbol_change":true,"container_id":"tv_chart",
    "studies":["Volume@tv-basicstudies"],
    "width":"100%","height":"500"}});
    </script>
    </div>

    <!-- Position Info + Close -->
    <div>
    <div class="card" style="margin-bottom:16px">
    <h3 style="margin-bottom:12px">{di_icon} {symbol} {ver_badge}</h3>
    <div class="param-grid" style="grid-template-columns:1fr">
    <div class="param-item"><span class="key">Entry</span><span class="val" style="color:var(--accent)">{entry:.6g}</span></div>
    <div class="param-item"><span class="key">Trail Stop</span><span class="val" style="color:var(--red)">{trail:.6g}</span></div>
    <div class="param-item"><span class="key">Peak</span><span class="val" style="color:var(--green)">{peak:.6g}</span></div>
    <div class="param-item"><span class="key">Pump</span><span class="val">+{pump_pct:.0f}%</span></div>
    <div class="param-item"><span class="key">Consolidation</span><span class="val">{p_cons:.6g}</span></div>
    <div class="param-item"><span class="key">Exchange</span><span class="val"><span class="badge badge-blue">{exchange.upper()}</span></span></div>
    {strat_info}
    </div>
    </div>

    <div class="card" style="text-align:center">
    <h3 style="margin-bottom:12px;color:var(--red)">{warn_icon} Manual Control</h3>
    <button onclick="showCloseModal()" class="btn btn-red" style="width:100%;padding:14px;font-size:16px">{skull} CLOSE {symbol}</button>
    <p style="margin-top:8px;color:var(--dim);font-size:12px">Force-close with 5s confirmation</p>
    </div>
    </div>
    </div>

    <!-- Close Confirmation Modal -->
    <div id="closeModal" class="close-modal">
    <div class="close-modal-box">
    <h2>{skull} CLOSE {symbol}?</h2>
    <p style="color:var(--dim);margin-bottom:8px">{di_icon} @ {entry:.6g} on {exchange.upper()}</p>
    <p>Current uPnL: <strong class="{upnl_cls}" style="font-size:20px">{upnl:+.2f}%</strong></p>
    <div class="countdown" id="countdown">5</div>
    <p style="color:var(--dim);font-size:13px;margin-bottom:20px">Confirm within countdown or cancel</p>
    <div style="display:flex;align-items:center;justify-content:center;gap:16px">
    <form method="POST" action="/pumps/close/{url_key}" id="closeForm" style="margin:0;padding:0;display:block">
    <button type="submit" class="btn btn-red" id="confirmBtn" disabled style="opacity:0.5;width:200px;padding:14px 0;font-size:15px;font-weight:700">{skull} CONFIRM CLOSE</button>
    </form>
    <button onclick="hideCloseModal()" class="btn" style="background:var(--border);width:140px;padding:14px 0;font-size:15px;font-weight:600">{cancel_icon} CANCEL</button>
    </div></div></div>

    <script>
    let countdownTimer = null;
    function showCloseModal() {{
        window._modalOpen = true;
        const modal = document.getElementById('closeModal');
        const btn = document.getElementById('confirmBtn');
        const cd = document.getElementById('countdown');
        btn.disabled = true; btn.style.opacity = '0.5';
        btn.classList.remove('btn-ready');
        modal.classList.add('show');
        let sec = 5; cd.textContent = sec;
        countdownTimer = setInterval(() => {{
            sec--; cd.textContent = sec;
            if (sec <= 0) {{
                clearInterval(countdownTimer);
                cd.textContent = '{check}';
                btn.disabled = false;
                btn.classList.add('btn-ready');
            }}
        }}, 1000);
    }}
    function hideCloseModal() {{
        clearInterval(countdownTimer);
        document.getElementById('confirmBtn').classList.remove('btn-ready');
        document.getElementById('closeModal').classList.remove('show');
        window._modalOpen = false;
    }}
    </script>
    '''

    return layout(f"📡 {di_icon} {symbol} [{strat_ver.upper()}] · <span class='{upnl_cls}'>{upnl:+.2f}%{upnl_usd_str}</span>", body, "/pumps")


# ─── Pump Hunter Page ────────────────────────────────────────

def page_pumps():
    """Pump Hunter dashboard — active positions, completed trades, scanner stats."""
    ph = ph_state()
    if not ph:
        return layout("🎯 Pump Hunter", '<div class="card"><p style="color:var(--dim)">⏳ No pump hunter data available. Scanner may not be running or no state file found.</p><p style="margin-top:8px;font-size:13px;color:var(--dim)">Expected state file: <code>' + str(PH_STATE) + '</code></p></div>', "/pumps")

    scan_count = ph.get("scan_count", 0)
    wins = ph.get("wins", 0)
    losses = ph.get("losses", 0)
    completed = ph.get("completed_trades", [])
    # Recalculate PnL from actual completed trades
    total_pnl = sum(t.get('pnl_pct', 0) for t in completed)
    demo_balance = ph.get("demo_balance", 10000)
    uptime = ph.get("uptime_sec", 0)
    active = ph.get("active_positions", {})
    last_updated = ph.get("last_updated", "")
    tot = wins + losses
    wr = wins / max(1, tot) * 100

    # Exchange balance override
    ex_balance = ph.get("exchange_balance", 0)
    ph_mode = ph.get("trading_mode", "paper")

    # Uptime format
    up_h = uptime // 3600
    up_m = (uptime % 3600) // 60

    # Metrics — demo_balance already = initial_deposit + sum(pnl_usd)
    current_balance = demo_balance
    if ex_balance > 0:
        current_balance = ex_balance
    bal_cls = '' if total_pnl >= 0 else ' neg-balance'

    # Mode badge
    ph_mode_badges = {
        'paper': '<span class="badge" style="background:rgba(139,148,158,.15);color:var(--dim)">📝 PAPER</span>',
        'demo': '<span class="badge badge-purple">🧪 DEMO</span>',
        'live': '<span class="badge badge-red">🔴 LIVE</span>',
    }
    ph_mode_badge = ph_mode_badges.get(ph_mode, ph_mode_badges['paper'])
    balance_info = f'Deposit: ${demo_balance:,.0f} | PnL: {total_pnl:+.1f}%'
    if ex_balance > 0:
        balance_info += f' | Exchange: ${ex_balance:,.2f}'

    metrics = f'''<div class="grid">
    <div class="card metric balance-card{bal_cls}" onclick="var el=document.getElementById('ph-trades');if(el)el.scrollIntoView({{behavior:'smooth'}})" title="Click for trade history"><span class="label">💰 Balance</span><span class="value">${current_balance:,.2f}</span><span style="display:block;font-size:11px;color:var(--dim);margin-top:4px">{balance_info}</span></div>
    <div class="card metric"><span class="label">Scanner</span><span class="value"><span class="badge badge-green">🟢 ACTIVE</span> {ph_mode_badge}</span></div>
    <div class="card metric"><span class="label">Win Rate</span><span class="value">{wr:.0f}%</span></div>
    <div class="card metric"><span class="label">Trades</span><span class="value">{tot} <small style="color:var(--dim)">W{wins}/L{losses}</small></span></div>
    <div class="card metric"><span class="label">Scans</span><span class="value">{scan_count}</span></div>
    <div class="card metric"><span class="label">Uptime</span><span class="value">{up_h}h {up_m}m</span></div>
    </div>'''


    # Active positions with live prices
    pos_html = ""
    if active:
        rows = ""
        total_upnl = 0.0
        for state_key, p in active.items():
            real_sym = p.get("symbol", state_key.split(":")[0])
            exchange = p.get("exchange", "bybit")
            entry = float(p.get("entry_price", 0))
            direction = p.get("direction", "long")
            strat_ver = p.get("strategy_version", p.get("strategy_name", "v2").replace("pump_hunter_", ""))
            cur_price = fetch_price(real_sym, exchange)
            if direction == "long":
                upnl = ((cur_price / entry) - 1) * 100 if entry > 0 and cur_price > 0 else p.get("pnl_pct", 0)
            else:
                upnl = ((entry / cur_price) - 1) * 100 if entry > 0 and cur_price > 0 else p.get("pnl_pct", 0)
            total_upnl += upnl
            upnl_c = "pos" if upnl >= 0 else "neg"
            price_display = f"{cur_price:.6g}" if cur_price else "\u2014"
            pump_pct = p.get("pump_pct", 0)
            trail = p.get("trailing_stop", 0)
            peak = p.get("peak_price", 0)
            url_key = urllib.parse.quote(state_key, safe='')

            # Tier badge
            if pump_pct >= 200:
                tier = '<span class="badge badge-red">\U0001f534 MEGA</span>'
            elif pump_pct >= 100:
                tier = '<span class="badge" style="background:rgba(240,136,62,.15);color:var(--orange)">\U0001f7e0 PUMP</span>'
            else:
                tier = '<span class="badge" style="background:rgba(255,215,0,.15);color:#ffd700">\U0001f7e1 EARLY</span>'

            # Strategy version badge
            ver_badge = f'<span class="badge badge-purple">{strat_ver.upper()}</span>' if strat_ver == 'v2' else f'<span class="badge" style="background:rgba(139,148,158,.15);color:var(--dim)">{strat_ver.upper()}</span>'
            # Direction
            dir_icon = '\U0001f7e2 LONG' if direction == 'long' else '\U0001f534 SHORT'

            # Calculate position notional and dollar uPnL
            p_lev = float(p.get("leverage", 20))
            p_size_u = float(p.get("size_usdt", 0) or 0)
            if p_size_u == 0:
                p_sz_pct = float(p.get("size_pct", 20))
                p_size_u = demo_balance * p_sz_pct / 100
            p_notional = p_size_u * p_lev
            upnl_d = p_notional * upnl / 100
            usd_str = f' <span style="font-size:11px">(${upnl_d:+,.0f})</span>' if upnl_d != 0 else ''

            rows += f'''<tr style="cursor:pointer" onclick="window.location='/pumps/position/{url_key}'">
            <td><strong>{real_sym}</strong></td>
            <td>{ver_badge}</td>
            <td><span class="badge badge-blue">{exchange.upper()}</span></td>
            <td>{dir_icon}</td>
            <td>{tier}</td>
            <td>{entry:.6g}</td>
            <td>{price_display}</td>
            <td>{peak:.6g}</td>
            <td style="color:var(--dim)">${p_notional:,.0f}</td>
            <td class="{upnl_c}" style="font-weight:700">{upnl:+.1f}%{usd_str}</td>
            <td style="color:var(--red)">{trail:.6g}</td>
            <td>+{pump_pct:.0f}%</td>
            <td><button onclick="event.stopPropagation();showPumpClose('{state_key}','{exchange}',{upnl})" class="btn btn-red" style="padding:4px 10px;font-size:11px">\U0001f480 Close</button></td>
            </tr>'''

        total_cls = "pos" if total_upnl >= 0 else "neg"
        # Calculate total dollar uPnL for all positions
        total_upnl_usd = 0.0
        for _sk, _p in active.items():
            _lev = float(_p.get("leverage", 20))
            _size_u = float(_p.get("size_usdt", 0) or 0)
            if _size_u == 0:
                _sz_pct = float(_p.get("size_pct", 20))
                _size_u = demo_balance * _sz_pct / 100
            _notional = _size_u * _lev
            _entry = float(_p.get("entry_price", 0))
            _dir = _p.get("direction", "long")
            _cur = fetch_price(_p.get("symbol", _sk.split(":")[0]), _p.get("exchange", "bybit"))
            if _dir == "long":
                _upnl = ((_cur / _entry) - 1) * 100 if _entry > 0 and _cur > 0 else 0
            else:
                _upnl = ((_entry / _cur) - 1) * 100 if _entry > 0 and _cur > 0 else 0
            total_upnl_usd += _notional * _upnl / 100
        t_usd_cls = "pos" if total_upnl_usd >= 0 else "neg"
        usd_total_str = f' · <span class="{t_usd_cls}">${total_upnl_usd:+,.0f}</span>' if total_upnl_usd != 0 else ''
        pos_html = f'''<div class="card"><h3 style="margin-bottom:12px">\U0001f4e1 Active Pump Positions ({len(active)}) \u00b7 <span class="{total_cls}">uPnL: {total_upnl:+.1f}%</span>{usd_total_str}</h3>
        <div style="overflow-x:auto"><table><thead><tr>
        <th>Symbol</th><th>Strategy</th><th>Exchange</th><th>Dir</th><th>Tier</th><th>Entry</th><th>Now</th><th>Peak</th><th>Size $</th><th>uPnL</th><th>Trail Stop</th><th>Pump</th><th>Action</th>
        </tr></thead><tbody>{rows}</tbody></table></div></div>'''
    else:
        pos_html = '<div class="card"><h3>\U0001f4e1 Active Positions</h3><p style="color:var(--dim);margin-top:8px">No open pump positions \u2014 scanner is hunting...</p></div>'

    # Completed trades
    trades_html = ""
    if completed:
        rows = ""
        for i, t in enumerate(reversed(completed), 1):
            pnl_v = t.get("pnl_pct", 0)
            pnl_usd = t.get("pnl_usd", 0)
            exchange = t.get("exchange", "?")
            reason = t.get("exit_reason", "?")
            entry_p = t.get("entry", "?")
            exit_p = t.get("exit", "?")
            peak_p = t.get("peak", "?")
            direction = t.get("direction", "long")
            strat = t.get("strategy_version", "?")
            ts = str(t.get("time", ""))[:16]

            reason_badge = {
                "trailing_stop": '<span class="badge badge-green">\U0001f4d0 Trail</span>',
                "false_breakout": '<span class="badge badge-red">\u26a0\ufe0f False</span>',
                "manual_close_hq": '<span class="badge" style="background:rgba(88,166,255,.15);color:var(--accent)">\U0001f3ae HQ</span>',
            }.get(reason, f'<span class="badge" style="background:rgba(139,148,158,.15);color:var(--dim)">{reason}</span>')

            ver_badge = f'<span class="badge badge-purple">{strat.upper()}</span>' if strat == 'v2' else f'<span class="badge" style="background:rgba(139,148,158,.15);color:var(--dim)">{strat.upper()}</span>'
            dir_icon = '\U0001f7e2' if direction == 'long' else '\U0001f534'
            pnl_usd_cls = "pos" if pnl_usd >= 0 else "neg"

            rows += f'''<tr>
            <td>{i}</td>
            <td><strong>{t.get("symbol","?")}</strong></td>
            <td>{ver_badge}</td>
            <td>{dir_icon} {direction.upper()}</td>
            <td><span class="badge badge-blue">{exchange.upper()}</span></td>
            <td>{entry_p}</td><td>{exit_p}</td><td>{peak_p}</td>
            <td>{pnl_badge(pnl_v)}</td>
            <td class="{pnl_usd_cls}" style="font-weight:600">${pnl_usd:+,.0f}</td>
            <td>{reason_badge}</td>
            <td style="font-size:11px;color:var(--dim)">{ts}</td>
            </tr>'''

        trades_html = f'''<div id="ph-trades" class="card" style="margin-top:16px"><h3 style="margin-bottom:12px">\U0001f4cb Completed Trades ({len(completed)})</h3>
        <div style="overflow-x:auto"><table><thead><tr>
        <th>#</th><th>Symbol</th><th>Strategy</th><th>Dir</th><th>Exchange</th><th>Entry</th><th>Exit</th><th>Peak</th><th>PnL</th><th>$</th><th>Reason</th><th>Time</th>
        </tr></thead><tbody>{rows}</tbody></table></div></div>'''

    # V1 / V2 / V3 Comparison
    v1_trades = [t for t in completed if t.get("strategy_version") == "v1"]
    v2_trades = [t for t in completed if t.get("strategy_version") == "v2"]
    v3_trades = [t for t in completed if t.get("strategy_version") == "v3"]

    def strat_stats(trades):
        if not trades:
            return 0, 0, 0, 0, 0
        w = sum(1 for t in trades if t.get("pnl_pct", 0) > 0)
        l = len(trades) - w
        pnl = sum(t.get("pnl_pct", 0) for t in trades)
        usd = sum(t.get("pnl_usd", 0) for t in trades)
        return len(trades), w, l, pnl, usd

    v1_tot, v1_w, v1_l, v1_pnl, v1_usd = strat_stats(v1_trades)
    v2_tot, v2_w, v2_l, v2_pnl, v2_usd = strat_stats(v2_trades)
    v3_tot, v3_w, v3_l, v3_pnl, v3_usd = strat_stats(v3_trades)
    v1_wr = v1_w / v1_tot * 100 if v1_tot else 0
    v2_wr = v2_w / v2_tot * 100 if v2_tot else 0
    v3_wr = v3_w / v3_tot * 100 if v3_tot else 0

    comparison_html = f'''<div class="card" style="margin-top:16px">
    <h3 style="margin-bottom:12px">\U0001f4ca Strategy Comparison (V1 / V2 / V3)</h3>
    <div style="overflow-x:auto"><table><thead><tr>
    <th>Metric</th><th>V1 (Trailing)</th><th>V2 (6-Phase)</th><th>V3 (Impulse)</th>
    </tr></thead><tbody>
    <tr><td>Trades</td><td>{v1_tot}</td><td>{v2_tot}</td><td>{v3_tot}</td></tr>
    <tr><td>Win Rate</td><td>{v1_wr:.0f}%</td><td>{v2_wr:.0f}%</td><td>{v3_wr:.0f}%</td></tr>
    <tr><td>Total PnL</td><td class="{pnl_cls(v1_pnl)}">{v1_pnl:+.2f}%</td><td class="{pnl_cls(v2_pnl)}">{v2_pnl:+.2f}%</td><td class="{pnl_cls(v3_pnl)}">{v3_pnl:+.2f}%</td></tr>
    <tr><td>USD PnL</td><td class="{pnl_cls(v1_usd)}">${v1_usd:+,.0f}</td><td class="{pnl_cls(v2_usd)}">${v2_usd:+,.0f}</td><td class="{pnl_cls(v3_usd)}">${v3_usd:+,.0f}</td></tr>
    <tr><td>W/L</td><td>{v1_w}/{v1_l}</td><td>{v2_w}/{v2_l}</td><td>{v3_w}/{v3_l}</td></tr>
    </tbody></table></div>
    </div>'''

    # Equity curve
    eq_html = ""
    if completed:
        running = 0
        curve = []
        for t in completed:
            running += t.get("pnl_pct", 0)
            curve.append(running)
        mn = min(curve)
        mx = max(curve)
        rng = mx - mn if mx != mn else 0.1
        bars = ""
        for v in curve:
            pct = max(5, (v - mn) / rng * 100)
            c = "var(--green)" if v >= 0 else "var(--red)"
            bars += f'<div class="bar" style="height:{pct}%;background:{c}" title="{v:+.1f}%"></div>'
        eq_html = f'<div class="card" style="margin-top:16px"><h3>\U0001f4c8 Pump Hunter Equity Curve</h3><div class="equity-bar">{bars}</div></div>'

    # Scanner info
    info_html = f'''<div class="card" style="margin-top:16px">
    <h3 style="margin-bottom:12px">⚙️ Scanner Settings</h3>
    <div class="param-grid">
    <div class="param-item"><span class="key">Mode</span><span class="val">{'🤖 Auto-Enter' if True else '👋 Manual'}</span></div>
    <div class="param-item"><span class="key">Demo Balance</span><span class="val">${demo_balance:,.0f}</span></div>
    <div class="param-item"><span class="key">Position Size</span><span class="val">5% (${demo_balance*0.05:,.0f})</span></div>
    <div class="param-item"><span class="key">Max Positions</span><span class="val">5</span></div>
    <div class="param-item"><span class="key">Exchanges</span><span class="val">Bybit · MEXC · Gate.io · Bitget</span></div>
    <div class="param-item"><span class="key">Detection</span><span class="val">+50% pump / 30d flat</span></div>
    <div class="param-item"><span class="key">Trail</span><span class="val">Adaptive 30%→15%</span></div>
    <div class="param-item"><span class="key">Last Update</span><span class="val" style="font-size:12px">{str(last_updated)[:19]}</span></div>
    </div>
    </div>'''

    pump_close_modal = """
    <!-- Pump Close Modal -->
    <div id="pumpCloseModal" class="close-modal">
    <div class="close-modal-box">
    <h2>💀 Close Pump Position</h2>
    <p id="pumpCloseInfo" style="color:var(--dim);margin-bottom:8px"></p>
    <p>uPnL: <strong id="pumpCloseUpnl" style="font-size:20px"></strong></p>
    <div class="countdown" id="pumpCountdown">5</div>
    <p style="color:var(--dim);font-size:13px;margin-bottom:20px">Confirm within countdown</p>
    <div style="display:flex;align-items:center;justify-content:center;gap:16px">
    <form method="POST" id="pumpCloseForm" style="margin:0;padding:0;display:block">
    <button type="submit" class="btn btn-red" id="pumpConfirmBtn" disabled style="opacity:0.5;width:200px;padding:14px 0;font-size:15px;font-weight:700">💀 CONFIRM CLOSE</button>
    </form>
    <button onclick="hidePumpClose()" class="btn" style="background:var(--border);width:140px;padding:14px 0;font-size:15px;font-weight:600">❌ CANCEL</button>
    </div></div></div>

    <style>
    @keyframes btnGlow {{
        0%, 100% {{ box-shadow: 0 0 8px rgba(248,81,73,.4); }}
        50% {{ box-shadow: 0 0 20px rgba(248,81,73,.8), 0 0 40px rgba(248,81,73,.3); }}
    }}
    .btn-ready {{ animation: btnGlow 1s ease-in-out infinite !important; opacity: 1 !important; }}
    </style>

    <script>
    let pumpTimer = null;
    function showPumpClose(sym, exch, upnl) {{
        window._modalOpen = true;
        const modal = document.getElementById('pumpCloseModal');
        document.getElementById('pumpCloseInfo').textContent = sym + ' on ' + exch.toUpperCase();
        const upnlEl = document.getElementById('pumpCloseUpnl');
        upnlEl.textContent = (upnl >= 0 ? '+' : '') + upnl.toFixed(1) + '%';
        upnlEl.className = upnl >= 0 ? 'pos' : 'neg';
        document.getElementById('pumpCloseForm').action = '/pumps/close/' + sym;
        const btn = document.getElementById('pumpConfirmBtn');
        const cd = document.getElementById('pumpCountdown');
        btn.disabled = true; btn.style.opacity = '0.5';
        btn.classList.remove('btn-ready');
        modal.classList.add('show');
        let sec = 5; cd.textContent = sec;
        pumpTimer = setInterval(() => {{
            sec--; cd.textContent = sec;
            if (sec <= 0) {{
                clearInterval(pumpTimer);
                cd.textContent = '✅';
                btn.disabled = false;
                btn.classList.add('btn-ready');
            }}
        }}, 1000);
    }}
    function hidePumpClose() {{
        clearInterval(pumpTimer);
        const btn = document.getElementById('pumpConfirmBtn');
        btn.classList.remove('btn-ready');
        document.getElementById('pumpCloseModal').classList.remove('show');
        window._modalOpen = false;
    }}
    </script>""" if active else ""
    return layout("\U0001f3af Pump Hunter", metrics + pos_html + pump_close_modal + trades_html + comparison_html + eq_html + info_html, "/pumps")



def page_pumps_trades():
    """Pump Hunter trades list."""
    ph = ph_state()
    if not ph:
        return layout("\U0001f4cb Pump Trades", '<div class="card"><p style="color:var(--dim)">No pump hunter data.</p></div>', "/pumps/trades")
    completed = ph.get("completed_trades", [])
    total_pnl = sum(t.get('pnl_pct', 0) for t in completed)
    demo_balance = ph.get("demo_balance", 10000)
    balance = demo_balance  # already = initial_deposit + sum(pnl_usd)
    bal_cls = '' if total_pnl >= 0 else ' neg-balance'

    header = f'''<div class="grid" style="grid-template-columns:1fr 1fr 1fr">
    <div class="card metric balance-card{bal_cls}"><span class="label">\U0001f4b0 Balance</span><span class="value">${balance:,.2f}</span></div>
    <div class="card metric"><span class="label">Total PnL</span><span class="value {pnl_cls(total_pnl)}">{total_pnl:+.2f}%</span></div>
    <div class="card metric"><span class="label">Trades</span><span class="value">{len(completed)}</span></div>
    </div>'''

    if not completed:
        return layout("\U0001f4cb Pump Trades", header + '<div class="card"><p style="color:var(--dim)">No completed trades yet.</p></div>', "/pumps/trades")

    rows = ""
    running_pnl = 0
    for i, t in enumerate(completed, 1):
        pnl_v = t.get("pnl_pct", 0)
        running_pnl += pnl_v
        exchange = t.get("exchange", "?")
        reason = t.get("exit_reason", "?")
        rows += f'<tr><td>{i}</td><td><strong>{t.get("symbol","?")}</strong></td><td><span class="badge badge-blue">{exchange.upper()}</span></td><td>{t.get("entry","?")}</td><td>{t.get("exit","?")}</td><td>{pnl_badge(pnl_v)}</td><td>{reason}</td><td class="{pnl_cls(running_pnl)}">{running_pnl:+.2f}%</td></tr>'

    table = f'''<div class="card"><h3>\U0001f4cb All Pump Trades ({len(completed)})</h3>
    <div style="overflow-x:auto"><table><thead><tr>
    <th>#</th><th>Symbol</th><th>Exchange</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Reason</th><th>Running</th>
    </tr></thead><tbody>{rows}</tbody></table></div></div>'''

    # Equity curve
    running = 0; curve = []
    for t in completed:
        running += t.get("pnl_pct", 0)
        curve.append(running)
    mn = min(curve); mx = max(curve); rng = mx - mn if mx != mn else 0.1
    bars = ""
    for v in curve:
        pct = max(5, (v - mn) / rng * 100)
        c = "var(--green)" if v >= 0 else "var(--red)"
        bars += f'<div class="bar" style="height:{pct}%;background:{c}" title="{v:+.1f}%"></div>'
    eq = f'<div class="card" style="margin-top:16px"><h3>\U0001f4c8 Equity Curve</h3><div class="equity-bar">{bars}</div></div>'

    return layout("\U0001f4cb Pump Trades", header + table + eq, "/pumps/trades")


def page_pumps_analyze():
    """Pump Hunter analysis."""
    ph = ph_state()
    if not ph:
        return layout("\U0001f52c Pump Analysis", '<div class="card"><p style="color:var(--dim)">No data.</p></div>', "/pumps/analyze")
    completed = ph.get("completed_trades", [])
    scan_count = ph.get("scan_count", 0)
    total_pnl = sum(t.get('pnl_pct', 0) for t in completed)
    wins = ph.get("wins", 0)
    losses = ph.get("losses", 0)
    tot = wins + losses
    wr = wins / max(1, tot) * 100
    active = ph.get("active_positions", {})

    by_exch = {}
    for t in completed:
        e = t.get("exchange", "unknown")
        by_exch.setdefault(e, []).append(t.get("pnl_pct", 0))
    exch_rows = ""
    for e, pnls in sorted(by_exch.items(), key=lambda x: sum(x[1]), reverse=True):
        ep = sum(pnls); ew = sum(1 for p in pnls if p > 0)
        ewr = ew / max(1, len(pnls)) * 100
        exch_rows += f'<tr><td><span class="badge badge-blue">{e.upper()}</span></td><td>{len(pnls)}</td><td class="{pnl_cls(ep)}">{ep:+.2f}%</td><td>{ewr:.0f}%</td></tr>'

    by_reason = {}
    for t in completed:
        r = t.get("exit_reason", "?")
        by_reason.setdefault(r, []).append(t.get("pnl_pct", 0))
    reason_rows = ""
    for r, pnls in sorted(by_reason.items(), key=lambda x: sum(x[1]), reverse=True):
        rp = sum(pnls)
        reason_rows += f'<tr><td>{r}</td><td>{len(pnls)}</td><td class="{pnl_cls(rp)}">{rp:+.2f}%</td></tr>'

    verdict = "\u2705 \u0412 \u043f\u043b\u044e\u0441\u0435" if total_pnl > 0 else "\U0001f6a8 \u0421\u043b\u0438\u0432\u0430\u0435\u0442" if total_pnl < -1 else "\u26a0\ufe0f \u041e\u043a\u043e\u043b\u043e \u043d\u0443\u043b\u044f"

    body = f'''<div class="card" style="margin-bottom:16px">
    <h3>\U0001f52c Pump Hunter \u2014 Analysis</h3>
    <p style="font-size:20px;font-weight:700;margin:12px 0" class="{pnl_cls(total_pnl)}">{verdict} ({total_pnl:+.2f}%)</p>
    <div class="param-grid">
    <div class="param-item"><span class="key">Win Rate</span><span class="val">{wr:.1f}%</span></div>
    <div class="param-item"><span class="key">Trades</span><span class="val">{tot} (W{wins}/L{losses})</span></div>
    <div class="param-item"><span class="key">Scans</span><span class="val">{scan_count}</span></div>
    <div class="param-item"><span class="key">Active Now</span><span class="val">{len(active)}</span></div>
    </div></div>
    <div class="card" style="margin-bottom:16px"><h3>By Exchange</h3>
    <table><thead><tr><th>Exchange</th><th>Trades</th><th>PnL</th><th>WR</th></tr></thead>
    <tbody>{exch_rows if exch_rows else '<tr><td colspan="4" style="color:var(--dim)">No data</td></tr>'}</tbody></table></div>
    <div class="card"><h3>By Exit Reason</h3>
    <table><thead><tr><th>Reason</th><th>Count</th><th>PnL</th></tr></thead>
    <tbody>{reason_rows if reason_rows else '<tr><td colspan="3" style="color:var(--dim)">No data</td></tr>'}</tbody></table></div>'''

    return layout("\U0001f52c Pump Analysis", body, "/pumps/analyze")


def handle_pump_close(symbol: str) -> str:
    """Force-close a pump hunter position."""
    ph = ph_state()
    if not ph:
        return layout("Error", '<div class="card"><p>No pump state.</p><a href="/pumps" class="btn btn-blue">← Back</a></div>', "/pumps")
    active = ph.get("active_positions", {})
    if symbol not in active:
        return layout("Error", f'<div class="card"><p>No pump position for {symbol}</p><a href="/pumps" class="btn btn-blue">← Back</a></div>', "/pumps")

    pos = active[symbol]
    real_sym = pos.get("symbol", symbol.split(":")[0])
    exchange = pos.get("exchange", "bybit")
    direction = pos.get("direction", "long")
    entry = float(pos.get("entry_price", 0))
    size_pct = pos.get("size_pct", 20)
    leverage = pos.get("leverage", 20)
    cur_price = fetch_price(real_sym, exchange)
    if direction == "long":
        upnl = ((cur_price / entry) - 1) * 100 if entry > 0 and cur_price > 0 else 0
    else:
        upnl = ((entry / cur_price) - 1) * 100 if entry > 0 and cur_price > 0 else 0

    # Calculate balance impact — use stored size_usdt from position if available
    old_balance = ph.get("demo_balance", 10000)
    position_size = float(pos.get("size_usdt", 0) or 0)
    if position_size == 0:
        # Fallback: calculate from current balance (legacy positions without size_usdt)
        position_size = old_balance * float(size_pct) / 100
    pnl_usd = position_size * float(leverage) * upnl / 100
    new_balance = old_balance + pnl_usd

    # Move to completed
    completed = ph.get("completed_trades", [])
    completed.append({
        "symbol": real_sym,
        "exchange": exchange,
        "direction": direction,
        "strategy_version": pos.get("strategy_version", "?"),
        "entry": entry,
        "exit": cur_price,
        "peak": pos.get("peak_price", entry),
        "pnl_pct": round(upnl, 2),
        "pnl_usd": round(pnl_usd, 2),
        "size_usdt": round(position_size, 2),
        "leverage": leverage,
        "exit_reason": "manual_close_hq",
        "pump_pct": pos.get("pump_pct", 0),
        "time": datetime.now(timezone.utc).isoformat(),
    })
    del active[symbol]
    ph["active_positions"] = active
    ph["completed_trades"] = completed
    if upnl > 0:
        ph["wins"] = ph.get("wins", 0) + 1
    else:
        ph["losses"] = ph.get("losses", 0) + 1
    ph["total_pnl_pct"] = sum(t.get("pnl_pct", 0) for t in completed)
    ph["demo_balance"] = new_balance
    ph["last_updated"] = datetime.now(timezone.utc).isoformat()

    PH_STATE.write_text(json.dumps(ph, indent=2, default=str), encoding="utf-8")

    upnl_cls = "pos" if upnl >= 0 else "neg"
    return layout("✅ Pump Position Closed", f'''
    <div class="card" style="text-align:center">
    <h2 style="margin-bottom:16px">✅ {symbol} Closed</h2>
    <p class="{upnl_cls}" style="font-size:28px;font-weight:700">{upnl:+.2f}%</p>
    <p class="{upnl_cls}" style="font-size:20px;font-weight:600;margin-top:4px">${pnl_usd:+,.2f}</p>
    <p style="margin-top:8px;color:var(--dim)">Exit: {cur_price:.6g} on {exchange.upper()} | manual_close_hq</p>
    <p style="margin-top:4px;color:var(--dim)">Size: ${position_size:,.0f} × {leverage}x = ${position_size * leverage:,.0f} notional</p>
    <p style="margin-top:4px;color:var(--dim)">New Balance: ${new_balance:,.2f}</p>
    <a href="/pumps" class="btn btn-blue" style="margin-top:20px">← Pump Hunter</a>
    </div>
    ''', "/pumps")


# ─── Exchange History Pages ──────────────────────────────────

def page_exchange_dashboard():
    """Exchange Dashboard — balance, positions, PnL from Binance Testnet API."""
    if not _executor:
        return layout("\U0001f4b9 Exchange Dashboard",
            '<div class="card"><p style="color:var(--dim)">⚠️ Exchange not connected. Set TRADING_MODE=demo and configure API keys.</p></div>',
            "/exchange")

    # ─── Fetch data from exchange API ─────────────
    balance = ex_balance()
    positions = ex_positions()
    income = ex_income()
    trades = ex_trades()

    total_upnl = sum(p.unrealized_pnl for p in positions)
    total_notional = sum(p.notional for p in positions)
    equity = balance + total_upnl

    # PnL from income history
    total_pnl = sum(i['income'] for i in income if i['income_type'] == 'REALIZED_PNL')
    total_fees = sum(i['income'] for i in income if i['income_type'] == 'COMMISSION')
    total_funding = sum(i['income'] for i in income if i['income_type'] == 'FUNDING_FEE')
    net_profit = total_pnl + total_fees + total_funding

    # Win/loss from PnL items
    pnl_items = [i for i in income if i['income_type'] == 'REALIZED_PNL']
    wins = sum(1 for i in pnl_items if i['income'] > 0)
    losses = sum(1 for i in pnl_items if i['income'] < 0)
    total_trades_pnl = wins + losses
    wr = wins / max(1, total_trades_pnl) * 100

    # Daily PnL
    daily = {}
    for item in income:
        if item['income_type'] in ('REALIZED_PNL', 'COMMISSION', 'FUNDING_FEE'):
            day = str(item.get('datetime', ''))[:10]
            if day:
                daily.setdefault(day, 0)
                daily[day] += item['income']
    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    today_pnl = daily.get(today_str, 0)

    # Max drawdown
    peak_val = 0.0
    max_dd = 0.0
    running_dd = 0.0
    all_sorted = sorted(
        [i for i in income if i['income_type'] in ('REALIZED_PNL', 'COMMISSION', 'FUNDING_FEE')],
        key=lambda x: x.get('time', 0)
    )
    for item in all_sorted:
        running_dd += item['income']
        if running_dd > peak_val:
            peak_val = running_dd
        dd = peak_val - running_dd
        if dd > max_dd:
            max_dd = dd

    # ─── Balance card (hero) ──────────────────────
    bal_cls = '' if net_profit >= 0 else ' neg-balance'
    eq_cls = 'pos' if total_upnl >= 0 else 'neg'
    net_cls = 'pos' if net_profit >= 0 else 'neg'
    today_cls = 'pos' if today_pnl >= 0 else 'neg'
    mode_badge = '<span class="badge badge-purple">🧪 TESTNET</span>' if _executor.testnet else '<span class="badge badge-red">🔴 MAINNET</span>'
    exchange_badge = f'<span class="badge badge-blue">{_executor.exchange_id.upper()}</span>'

    # Mini equity sparkline
    mini_spark = ''
    if all_sorted:
        vals = []
        s_run = 0
        for v in all_sorted[-40:]:
            s_run += v['income']
            vals.append(s_run)
        if vals:
            mn_s = min(vals); mx_s = max(vals)
            rng_s = mx_s - mn_s if mx_s != mn_s else 1
            pts = []
            for idx_s, rv in enumerate(vals):
                x = idx_s / max(1, len(vals)-1) * 200
                y = 36 - (rv - mn_s) / rng_s * 30
                pts.append(f'{x:.0f},{y:.1f}')
            last_c = 'var(--green)' if vals[-1] >= 0 else 'var(--red)'
            mini_spark = f'<span class="mini-spark" style="display:block;margin-top:8px"><svg width="204" height="40" viewBox="0 0 204 40"><polyline fill="none" stroke="{last_c}" stroke-width="1.5" points="{" ".join(pts)}"/></svg></span>'

    hero = f'''<div class="grid" style="grid-template-columns:1fr">
    <div class="card metric balance-card{bal_cls}" style="padding:28px">
    <div style="display:flex;justify-content:center;gap:10px;margin-bottom:12px">{exchange_badge} {mode_badge}</div>
    <span class="label">💰 Exchange Balance</span>
    <span class="value" style="font-size:36px">${balance:,.2f}</span>
    <span style="display:block;font-size:13px;color:var(--dim);margin-top:6px">Equity: ${equity:,.2f} · uPnL: <span class="{eq_cls}">${total_upnl:+,.2f}</span></span>
    {mini_spark}
    <div style="margin-top:12px;display:flex;gap:8px;justify-content:center">
    <a href="/exchange/positions" class="hist-btn">📍 Позиции ({len(positions)})</a>
    <a href="/exchange/history" class="hist-btn">📜 История сделок</a>
    <a href="/exchange/equity" class="hist-btn">📈 Кривая PnL</a>
    </div>
    </div></div>'''

    # ─── Metrics grid ─────────────────────────────
    metrics = f'''<div class="grid" style="grid-template-columns:repeat(4,1fr)">
    <div class="card metric"><span class="label">📈 Net Profit</span><span class="value {net_cls}" style="font-size:24px">${net_profit:+,.2f}</span></div>
    <div class="card metric"><span class="label">📊 Realized PnL</span><span class="value {"pos" if total_pnl>=0 else "neg"}">${total_pnl:+,.2f}</span></div>
    <div class="card metric"><span class="label">📅 Today PnL</span><span class="value {today_cls}" style="font-size:20px">${today_pnl:+,.2f}</span></div>
    <div class="card metric"><span class="label">Win Rate</span><span class="value" style="font-size:20px">{wr:.0f}% <small style="color:var(--dim)">W{wins}/L{losses}</small></span></div>
    </div>
    <div class="grid" style="grid-template-columns:repeat(4,1fr)">
    <div class="card metric"><span class="label">💳 Commissions</span><span class="value neg">${total_fees:,.2f}</span></div>
    <div class="card metric"><span class="label">🔄 Funding</span><span class="value {"pos" if total_funding>=0 else "neg"}">${total_funding:+,.2f}</span></div>
    <div class="card metric"><span class="label">📉 Max Drawdown</span><span class="value neg">${max_dd:,.2f}</span></div>
    <div class="card metric"><span class="label">📋 Total Fills</span><span class="value">{len(trades)}</span></div>
    </div>'''

    # ─── Open positions table ─────────────────────
    pos_html = ''
    if positions:
        pos_rows = ''
        for p in sorted(positions, key=lambda x: abs(x.unrealized_pnl), reverse=True):
            side_icon = '🟢' if p.side == 'long' else '🔴'
            upnl_cls = 'pos' if p.unrealized_pnl >= 0 else 'neg'
            pnl_pct = (p.unrealized_pnl / p.notional * 100) if p.notional else 0
            sym_url = p.symbol.replace('/', '_').replace(':', '_')
            # Bot owner badge
            bot_badge = ''
            if _registry:
                raw_sym = p.symbol.split('/')[0] + p.symbol.split('/')[1].split(':')[0] if '/' in p.symbol else p.symbol
                owner = _registry.owner(raw_sym)
                if owner == 'soldier':
                    bot_badge = '<span class="badge badge-blue" style="margin-left:4px">⚔️</span>'
                elif owner == 'pump_hunter':
                    bot_badge = '<span class="badge badge-purple" style="margin-left:4px">🎯</span>'
            pos_rows += f'''<tr style="cursor:pointer" onclick="location.href='/exchange/position/{sym_url}'">
            <td><strong>{p.symbol}</strong>{bot_badge}</td>
            <td>{side_icon} {p.side.upper()}</td>
            <td>{p.leverage}x</td>
            <td>${p.entry_price:,.6g}</td>
            <td>${p.mark_price:,.6g}</td>
            <td style="color:var(--dim)">${p.notional:,.2f}</td>
            <td class="{upnl_cls}" style="font-weight:700">${p.unrealized_pnl:+,.4f} ({pnl_pct:+.2f}%)</td>
            </tr>'''
        upnl_total_cls = 'pos' if total_upnl >= 0 else 'neg'
        pos_html = f'''<div class="card">
        <h3 style="margin-bottom:12px">📍 Open Positions ({len(positions)}) · <span class="{upnl_total_cls}">uPnL: ${total_upnl:+,.2f}</span></h3>
        <table><thead><tr>
        <th>Symbol</th><th>Side</th><th>Lev</th><th>Entry</th><th>Mark</th><th>Notional</th><th>uPnL</th>
        </tr></thead><tbody>{pos_rows}</tbody></table>
        <p style="margin-top:8px;font-size:12px;color:var(--dim)">Click row for detail → | <a href="/exchange/positions">Full view →</a></p></div>'''
    else:
        pos_html = '<div class="card"><h3>📍 Open Positions</h3><p style="color:var(--dim);margin-top:8px">No open positions on the exchange</p></div>'

    # ─── Recent PnL (last 10) ─────────────────────
    recent_html = ''
    if pnl_items:
        recent_rows = ''
        for i, inc in enumerate(reversed(pnl_items[-10:])):
            val = inc['income']
            cls = 'pos' if val >= 0 else 'neg'
            dt = str(inc.get('datetime', ''))[:16]
            sym = inc.get('symbol', '—')
            icon = '✅' if val >= 0 else '❌'
            recent_rows += f'<tr><td>{icon} <strong>{sym}</strong></td><td class="{cls}" style="font-weight:700">${val:+,.4f}</td><td style="font-size:11px;color:var(--dim)">{dt}</td></tr>'
        recent_html = f'''<div class="card">
        <h3 style="margin-bottom:12px">🕐 Recent Realized PnL</h3>
        <table><thead><tr><th>Symbol</th><th>PnL</th><th>Time</th></tr></thead>
        <tbody>{recent_rows}</tbody></table>
        <p style="margin-top:8px"><a href="/exchange/history">View all →</a></p></div>'''

    # ─── Daily PnL mini chart (bar chart) ─────────
    daily_chart = ''
    if daily:
        sorted_days = sorted(daily.keys())[-14:]  # Last 14 days
        if sorted_days:
            max_abs = max(abs(daily[d]) for d in sorted_days) or 1
            bars = ''
            for d in sorted_days:
                dv = daily[d]
                h = abs(dv) / max_abs * 60
                h = max(h, 3)
                c = 'var(--green)' if dv >= 0 else 'var(--red)'
                label = d[5:]  # MM-DD
                bars += f'''<div style="display:flex;flex-direction:column;align-items:center;flex:1;min-width:30px">
                <div style="height:65px;display:flex;align-items:flex-end">
                <div style="width:100%;max-width:28px;height:{h:.0f}px;background:{c};border-radius:3px 3px 0 0;margin:0 2px" title="{d}: ${dv:+,.2f}"></div>
                </div>
                <span style="font-size:9px;color:var(--dim);margin-top:4px">{label}</span>
                <span style="font-size:10px;color:{c};font-weight:600">${dv:+,.0f}</span>
                </div>'''
            daily_chart = f'''<div class="card">
            <h3 style="margin-bottom:12px">📅 Daily PnL (last 14 days)</h3>
            <div style="display:flex;gap:2px;align-items:flex-end">{bars}</div></div>'''

    # ─── Layout ───────────────────────────────────
    body = hero + metrics
    # Two-column layout for positions + recent PnL
    body += f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">{pos_html}{recent_html}</div>'
    body += daily_chart

    return layout("\U0001f4b9 Exchange Dashboard", body, "/exchange")


def page_exchange_history():
    """All trades from exchange API — full picture with entry/exit/fees."""
    if not _executor:
        return layout("\U0001f4dc Exchange History",
            '<div class="card"><p style="color:var(--dim)">⚠️ Exchange not connected. Set TRADING_MODE=demo and configure API keys.</p></div>',
            "/exchange/history")

    trades = ex_trades()
    income = ex_income()

    # Build income lookup by symbol for PnL
    pnl_by_sym = {}
    fee_by_sym = {}
    for inc in income:
        sym = inc.get('symbol', '')
        itype = inc.get('income_type', '')
        val = inc.get('income', 0)
        if itype == 'REALIZED_PNL':
            pnl_by_sym[sym] = pnl_by_sym.get(sym, 0) + val
        elif itype == 'COMMISSION':
            fee_by_sym[sym] = fee_by_sym.get(sym, 0) + val

    # Summary metrics
    total_pnl = sum(i['income'] for i in income if i['income_type'] == 'REALIZED_PNL')
    total_fees = sum(i['income'] for i in income if i['income_type'] == 'COMMISSION')
    total_funding = sum(i['income'] for i in income if i['income_type'] == 'FUNDING_FEE')
    net_profit = total_pnl + total_fees + total_funding
    trade_count = len(trades)

    # Get open positions from exchange
    open_positions = ex_positions()
    total_upnl = sum(p.unrealized_pnl for p in open_positions)
    open_count = len(open_positions)

    pnl_c = 'pos' if total_pnl >= 0 else 'neg'
    net_c = 'pos' if net_profit >= 0 else 'neg'
    upnl_c = 'pos' if total_upnl >= 0 else 'neg'

    metrics = f'''<div class="grid" style="grid-template-columns:repeat(4,1fr)">
    <div class="card metric"><span class="label">Realized PnL</span><span class="value {pnl_c}">${total_pnl:+,.2f}</span></div>
    <div class="card metric"><span class="label">Net Profit</span><span class="value {net_c}" style="font-size:28px">${net_profit:+,.2f}</span></div>
    <div class="card metric"><span class="label">Commissions</span><span class="value neg">${total_fees:,.2f}</span></div>
    <div class="card metric"><span class="label">Fills</span><span class="value">{trade_count}</span></div>
    </div>
    <div class="grid" style="grid-template-columns:repeat(4,1fr)">
    <div class="card metric" style="cursor:pointer;border:1px solid rgba(88,166,255,.3)" onclick="location.href='/exchange/positions'"><span class="label">\U0001f4cd Open Positions</span><span class="value" style="font-size:28px">{open_count}</span></div>
    <div class="card metric"><span class="label">Unrealized PnL</span><span class="value {upnl_c}" style="font-size:24px">${total_upnl:+,.2f}</span></div>
    <div class="card metric"><span class="label">Funding</span><span class="value {'pos' if total_funding>=0 else 'neg'}">${total_funding:+,.2f}</span></div>
    <div class="card metric"><span class="label">Balance</span><span class="value pos">${ex_balance():,.2f}</span></div>
    </div>'''

    # Group trades by order_id to show position pairs
    # Build trade table
    rows = ""
    if trades:
        # Collect unique symbols for filter
        symbols = sorted(set(t['symbol'] for t in trades))
        sym_opts = ''.join(f'<option value="{s}">{s}</option>' for s in symbols)

        filter_html = f'''<div class="filter-bar">
        <label>Symbol:</label>
        <select id="symFilter" onchange="filterTrades()"><option value="">All</option>{sym_opts}</select>
        <label>Side:</label>
        <select id="sideFilter" onchange="filterTrades()"><option value="">All</option><option value="buy">Buy</option><option value="sell">Sell</option></select>
        </div>'''

        for i, t in enumerate(reversed(trades)):
            sym = t['symbol']
            side = t['side']
            side_icon = "\U0001f7e2" if side == "buy" else "\U0001f534"
            price = t['price']
            amount = t['amount']
            cost = t['cost']
            fee = t['fee']
            fee_cur = t.get('fee_currency', 'USDT')
            dt = str(t.get('datetime', ''))[:19]
            tm = t.get('taker_or_maker', '')

            rows += f'''<tr class="trade-row" data-sym="{sym}" data-side="{side}">
            <td>{i+1}</td>
            <td><strong>{sym}</strong></td>
            <td>{side_icon} {side.upper()}</td>
            <td>{price:.6g}</td>
            <td>{amount:.4g}</td>
            <td>${cost:.2f}</td>
            <td class="neg">${fee:.4f} {fee_cur}</td>
            <td><span class="badge {'badge-blue' if tm=='maker' else 'badge-purple'}">{tm or '?'}</span></td>
            <td style="font-size:11px;color:var(--dim)">{dt}</td>
            </tr>'''
    else:
        filter_html = ''
        rows = '<tr><td colspan="9" style="color:var(--dim)">No trades found on exchange</td></tr>'

    trade_table = f'''{filter_html}<div class="card" style="overflow-x:auto">
    <h3 style="margin-bottom:12px">\U0001f4cb All Exchange Fills ({trade_count})</h3>
    <table id="tradesTable"><thead><tr>
    <th>#</th><th>Symbol</th><th>Side</th><th>Price</th><th>Qty</th><th>Cost</th><th>Fee</th><th>Type</th><th>Time</th>
    </tr></thead><tbody>{rows}</tbody></table></div>'''

    # Income breakdown table
    income_rows = ""
    pnl_items = [i for i in income if i['income_type'] == 'REALIZED_PNL']
    for i, inc in enumerate(reversed(pnl_items[-50:])):
        val = inc['income']
        cls = 'income-pos' if val >= 0 else 'income-neg'
        dt = str(inc.get('datetime', ''))[:19]
        sym = inc.get('symbol', '—')
        income_rows += f'<tr><td>{i+1}</td><td><strong>{sym}</strong></td><td class="{cls}">${val:+.4f}</td><td style="font-size:11px;color:var(--dim)">{dt}</td></tr>'

    income_table = ''
    if income_rows:
        income_table = f'''<div class="card" style="margin-top:16px;overflow-x:auto">
        <h3 style="margin-bottom:12px">\U0001f4b0 Realized PnL History (last 50)</h3>
        <table><thead><tr><th>#</th><th>Symbol</th><th>PnL</th><th>Time</th></tr></thead>
        <tbody>{income_rows}</tbody></table></div>'''

    # PnL by symbol card
    sym_cards = ''
    if pnl_by_sym:
        sym_items = ''
        for sym_k in sorted(pnl_by_sym, key=lambda x: pnl_by_sym[x], reverse=True):
            pv = pnl_by_sym[sym_k]
            fv = fee_by_sym.get(sym_k, 0)
            net_v = pv + fv
            cls = 'pos' if net_v >= 0 else 'neg'
            sym_items += f'<div class="param-item"><span class="key">{sym_k}</span><span class="val {cls}">PnL: ${pv:+.2f} | Fee: ${fv:.2f} | Net: ${net_v:+.2f}</span></div>'
        sym_cards = f'<div class="card" style="margin-top:16px"><h3 style="margin-bottom:12px">\U0001f4ca PnL by Symbol</h3><div class="param-grid">{sym_items}</div></div>'

    # Filter JS
    filter_js = '''<script>
    function filterTrades(){
        var sym=document.getElementById('symFilter').value;
        var side=document.getElementById('sideFilter').value;
        var rows=document.querySelectorAll('.trade-row');
        rows.forEach(function(r){
            var show=true;
            if(sym&&r.dataset.sym!==sym)show=false;
            if(side&&r.dataset.side!==side)show=false;
            r.style.display=show?'':'none';
        });
    }
    </script>'''

    return layout("\U0001f4dc Exchange Trade History",
                   metrics + trade_table + income_table + sym_cards + filter_js,
                   "/exchange/history")


def page_exchange_equity():
    """PnL equity curve over time — SVG chart with metrics."""
    if not _executor:
        return layout("\U0001f4c8 PnL Curve",
            '<div class="card"><p style="color:var(--dim)">⚠️ Exchange not connected.</p></div>',
            "/exchange/equity")

    income = ex_income()
    pnl_items = [i for i in income if i['income_type'] == 'REALIZED_PNL']
    fee_items = [i for i in income if i['income_type'] == 'COMMISSION']
    funding_items = [i for i in income if i['income_type'] == 'FUNDING_FEE']

    if not pnl_items and not fee_items:
        return layout("\U0001f4c8 PnL Curve",
            '<div class="card"><p style="color:var(--dim)">No PnL data yet. Make some trades first!</p></div>',
            "/exchange/equity")

    # Build cumulative PnL curve
    all_income = sorted(pnl_items + fee_items + funding_items, key=lambda x: x.get('time', 0))
    running = 0.0
    curve = []  # (datetime_str, cumulative_pnl)
    for item in all_income:
        running += item['income']
        dt = str(item.get('datetime', ''))[:16]
        curve.append((dt, running))

    if not curve:
        return layout("\U0001f4c8 PnL Curve",
            '<div class="card"><p style="color:var(--dim)">No data to plot.</p></div>',
            "/exchange/equity")

    # Metrics
    total_pnl = sum(i['income'] for i in pnl_items)
    total_fees = sum(i['income'] for i in fee_items)
    total_funding = sum(i['income'] for i in funding_items)
    net = total_pnl + total_fees + total_funding

    # Max drawdown
    peak_val = 0.0
    max_dd = 0.0
    run_dd = 0.0
    for _, cv in curve:
        if cv > peak_val:
            peak_val = cv
        dd = peak_val - cv
        if dd > max_dd:
            max_dd = dd

    # Daily breakdown
    daily = {}
    for item in all_income:
        day = str(item.get('datetime', ''))[:10]
        if day:
            daily.setdefault(day, 0)
            daily[day] += item['income']
    best_day = max(daily.values()) if daily else 0
    worst_day = min(daily.values()) if daily else 0
    win_days = sum(1 for v in daily.values() if v > 0)
    loss_days = sum(1 for v in daily.values() if v < 0)

    net_c = 'pos' if net >= 0 else 'neg'
    pnl_c = 'pos' if total_pnl >= 0 else 'neg'

    metrics = f'''<div class="grid" style="grid-template-columns:repeat(4,1fr)">
    <div class="card metric"><span class="label">Net Profit</span><span class="value {net_c}" style="font-size:28px">${net:+,.2f}</span></div>
    <div class="card metric"><span class="label">Realized PnL</span><span class="value {pnl_c}">${total_pnl:+,.2f}</span></div>
    <div class="card metric"><span class="label">Total Fees</span><span class="value neg">${total_fees:,.2f}</span></div>
    <div class="card metric"><span class="label">Max Drawdown</span><span class="value neg">${max_dd:,.2f}</span></div>
    </div>
    <div class="grid" style="grid-template-columns:repeat(4,1fr)">
    <div class="card metric"><span class="label">Funding</span><span class="value {'pos' if total_funding>=0 else 'neg'}">${total_funding:+,.2f}</span></div>
    <div class="card metric"><span class="label">Best Day</span><span class="value pos">${best_day:+,.2f}</span></div>
    <div class="card metric"><span class="label">Worst Day</span><span class="value neg">${worst_day:+,.2f}</span></div>
    <div class="card metric"><span class="label">Days W/L</span><span class="value">{win_days}W / {loss_days}L</span></div>
    </div>'''

    # SVG Equity Curve
    W, H = 900, 320
    PAD_L, PAD_R, PAD_T, PAD_B = 70, 20, 20, 40
    chart_w = W - PAD_L - PAD_R
    chart_h = H - PAD_T - PAD_B

    vals = [c[1] for c in curve]
    mn_v = min(vals + [0])
    mx_v = max(vals + [0])
    rng_v = mx_v - mn_v if mx_v != mn_v else 1

    def sx(i):
        return PAD_L + (i / max(1, len(vals)-1)) * chart_w
    def sy(v):
        return PAD_T + chart_h - ((v - mn_v) / rng_v * chart_h)

    # Zero line
    zero_y = sy(0)

    # Build polyline points
    pts = ' '.join(f'{sx(i):.1f},{sy(v):.1f}' for i, v in enumerate(vals))

    # Fill area (gradient under curve)
    fill_pts = f'{sx(0):.1f},{zero_y:.1f} ' + pts + f' {sx(len(vals)-1):.1f},{zero_y:.1f}'

    # Y-axis labels
    y_labels = ''
    steps = 5
    for i in range(steps + 1):
        v = mn_v + (rng_v * i / steps)
        y = sy(v)
        y_labels += f'<text x="{PAD_L-8}" y="{y+4}" text-anchor="end" fill="#8b949e" font-size="11">${v:,.0f}</text>'
        y_labels += f'<line x1="{PAD_L}" y1="{y}" x2="{W-PAD_R}" y2="{y}" stroke="#21262d" stroke-width="0.5"/>'

    # X-axis labels (show ~6 dates)
    x_labels = ''
    if len(curve) > 1:
        step_x = max(1, len(curve) // 6)
        for i in range(0, len(curve), step_x):
            dt_str = curve[i][0][5:16]  # MM-DD HH:MM
            x = sx(i)
            x_labels += f'<text x="{x}" y="{H-5}" text-anchor="middle" fill="#8b949e" font-size="10">{dt_str}</text>'

    last_v = vals[-1]
    line_color = '#3fb950' if last_v >= 0 else '#f85149'
    fill_color = 'rgba(63,185,80,.08)' if last_v >= 0 else 'rgba(248,81,73,.08)'

    # Dot on last point
    last_x, last_y_pt = sx(len(vals)-1), sy(last_v)

    svg = f'''<div class="card" style="padding:16px">
    <h3 style="margin-bottom:12px">\U0001f4c8 Equity Curve (Net PnL over time)</h3>
    <svg class="svg-chart" viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet" style="height:340px">
    <defs>
    <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="{line_color}" stop-opacity="0.3"/>
    <stop offset="100%" stop-color="{line_color}" stop-opacity="0.02"/>
    </linearGradient>
    </defs>
    {y_labels}{x_labels}
    <line x1="{PAD_L}" y1="{zero_y}" x2="{W-PAD_R}" y2="{zero_y}" stroke="#58a6ff" stroke-width="0.8" stroke-dasharray="4,3" opacity="0.5"/>
    <text x="{PAD_L-8}" y="{zero_y+4}" text-anchor="end" fill="#58a6ff" font-size="11" font-weight="600">$0</text>
    <polygon points="{fill_pts}" fill="url(#eqGrad)"/>
    <polyline points="{pts}" fill="none" stroke="{line_color}" stroke-width="2" stroke-linejoin="round"/>
    <circle cx="{last_x}" cy="{last_y_pt}" r="4" fill="{line_color}" stroke="#0a0e17" stroke-width="2"/>
    <text x="{last_x+8}" y="{last_y_pt+4}" fill="{line_color}" font-size="12" font-weight="700">${last_v:+,.2f}</text>
    </svg></div>'''

    # Daily PnL table
    day_rows = ''
    running_day = 0
    for day_str in sorted(daily.keys()):
        dv = daily[day_str]
        running_day += dv
        cls = 'pos' if dv >= 0 else 'neg'
        r_cls = 'pos' if running_day >= 0 else 'neg'
        day_rows += f'<tr class="day-row"><td>{day_str}</td><td class="{cls}" style="font-weight:700">${dv:+,.4f}</td><td class="{r_cls}">${running_day:+,.2f}</td></tr>'

    day_table = ''
    if day_rows:
        day_table = f'''<div class="card" style="margin-top:16px">
        <h3 style="margin-bottom:12px">\U0001f4c5 Daily PnL Breakdown</h3>
        <table><thead><tr><th>Date</th><th>Day PnL</th><th>Cumulative</th></tr></thead>
        <tbody>{day_rows}</tbody></table></div>'''

    # Income type breakdown
    type_counts = {}
    for inc in income:
        t = inc.get('income_type', '?')
        type_counts.setdefault(t, {'count': 0, 'total': 0})
        type_counts[t]['count'] += 1
        type_counts[t]['total'] += inc.get('income', 0)

    type_rows = ''
    for t_name in sorted(type_counts, key=lambda x: abs(type_counts[x]['total']), reverse=True):
        tv = type_counts[t_name]
        cls = 'pos' if tv['total'] >= 0 else 'neg'
        icon = {'REALIZED_PNL': '\U0001f4b0', 'COMMISSION': '\U0001f4b3', 'FUNDING_FEE': '\U0001f504', 'TRANSFER': '\U0001f4e5'}.get(t_name, '\U0001f4cc')
        type_rows += f'<tr><td>{icon} {t_name}</td><td>{tv["count"]}</td><td class="{cls}" style="font-weight:700">${tv["total"]:+,.4f}</td></tr>'

    type_table = ''
    if type_rows:
        type_table = f'''<div class="card" style="margin-top:16px">
        <h3 style="margin-bottom:12px">\U0001f4ca Income Breakdown by Type</h3>
        <table><thead><tr><th>Type</th><th>Count</th><th>Total</th></tr></thead>
        <tbody>{type_rows}</tbody></table></div>'''

    return layout("\U0001f4c8 PnL Equity Curve", metrics + svg + day_table + type_table, "/exchange/equity")


# ─── Exchange Positions ─────────────────────────

def page_exchange_positions():
    """Open positions from exchange — real-time grid view."""
    if not _executor:
        return layout("\U0001f4cd Exchange Positions", '<div class="card"><p>Exchange executor not configured</p></div>', "/exchange/positions")

    positions = ex_positions()
    balance = ex_balance()
    total_upnl = sum(p.unrealized_pnl for p in positions)
    total_notional = sum(p.notional for p in positions)
    upnl_c = 'pos' if total_upnl >= 0 else 'neg'

    # Metrics row
    metrics = f'''<div class="grid" style="grid-template-columns:repeat(4,1fr)">
    <div class="card metric"><span class="label">\U0001f4cd Open Positions</span><span class="value" style="font-size:32px">{len(positions)}</span></div>
    <div class="card metric"><span class="label">Total Unrealized PnL</span><span class="value {upnl_c}" style="font-size:28px">${total_upnl:+,.2f}</span></div>
    <div class="card metric"><span class="label">Total Notional</span><span class="value">${total_notional:,.2f}</span></div>
    <div class="card metric"><span class="label">Balance</span><span class="value pos">${balance:,.2f}</span></div>
    </div>'''

    if not positions:
        empty = '<div class="card" style="text-align:center;padding:40px"><p style="font-size:18px;color:var(--dim)">\U0001f4ad No open positions on the exchange</p></div>'
        return layout("\U0001f4cd Exchange Positions", metrics + empty, "/exchange/positions")

    # Position cards grid
    cards = ''
    for p in sorted(positions, key=lambda x: abs(x.unrealized_pnl), reverse=True):
        side_cls = 'long' if p.side == 'long' else 'short'
        side_icon = '\U0001f7e2' if p.side == 'long' else '\U0001f534'
        upnl_cls = 'pos' if p.unrealized_pnl >= 0 else 'neg'
        pnl_pct = (p.unrealized_pnl / p.notional * 100) if p.notional else 0
        pnl_pct_cls = 'pos' if pnl_pct >= 0 else 'neg'
        # Clean symbol for URL (replace / with _)
        sym_url = p.symbol.replace('/', '_').replace(':', '_')

        # Bot owner badge from registry
        bot_badge = ''
        if _registry:
            raw_sym = p.symbol.split('/')[0] + p.symbol.split('/')[1].split(':')[0] if '/' in p.symbol else p.symbol
            owner = _registry.owner(raw_sym)
            if owner == 'soldier':
                bot_badge = '<span class="badge badge-blue" style="margin-left:6px">⚔️ Soldier</span>'
            elif owner == 'pump_hunter':
                bot_badge = '<span class="badge badge-purple" style="margin-left:6px">🎯 Pump Hunter</span>'
            elif owner:
                bot_badge = f'<span class="badge" style="background:rgba(139,148,158,.15);color:var(--dim);margin-left:6px">{owner}</span>'

        cards += f'''<div class="pos-card">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">
            <span class="pos-sym">{p.symbol}</span>
            <span class="pos-side {side_cls}">{side_icon} {p.side.upper()}</span>{bot_badge}
            <span style="margin-left:auto;font-size:11px;color:var(--dim)">{p.leverage}x</span>
        </div>
        <div class="pos-detail">
            <span class="lbl">Entry Price</span><span class="val">${p.entry_price:,.6g}</span>
            <span class="lbl">Mark Price</span><span class="val">${p.mark_price:,.6g}</span>
            <span class="lbl">Size</span><span class="val">{p.size:,.4g}</span>
            <span class="lbl">Notional</span><span class="val">${p.notional:,.2f}</span>
            <span class="lbl">Unrealized PnL</span><span class="val {upnl_cls}">${p.unrealized_pnl:+,.4f}</span>
            <span class="lbl">PnL %</span><span class="val {pnl_pct_cls}">{pnl_pct:+.2f}%</span>
        </div>
        <div style="display:flex;gap:8px;margin-top:14px">
            <a href="/exchange/position/{sym_url}" class="hist-btn" style="flex:1;text-align:center">\U0001f50d Detail</a>
            <a href="#" onclick="showExClose('{p.symbol}','{p.side.upper()}','{sym_url}',{p.unrealized_pnl},{p.notional});return false" class="btn-close-pos" style="flex:1">\u2716 Close</a>
        </div>
        </div>'''

    grid = f'<div class="pos-grid">{cards}</div>'

    # Exchange Close Modal (same pattern as scalper/pump modals)
    ex_close_modal = """
    <!-- Exchange Close Modal -->
    <div id="exCloseModal" class="close-modal">
    <div class="close-modal-box">
    <h2>⚠️ Close Exchange Position</h2>
    <p id="exCloseInfo" style="color:var(--dim);margin-bottom:8px"></p>
    <p>uPnL: <strong id="exCloseUpnl" style="font-size:20px"></strong></p>
    <div class="countdown" id="exCountdown">5</div>
    <p style="color:var(--dim);font-size:13px;margin-bottom:20px">Confirm within countdown</p>
    <div style="display:flex;align-items:center;justify-content:center;gap:16px">
    <form method="POST" id="exCloseForm" style="margin:0;padding:0;display:block">
    <button type="submit" class="btn btn-red" id="exConfirmBtn" disabled style="opacity:0.5;width:200px;padding:14px 0;font-size:15px;font-weight:700">⚠️ CONFIRM CLOSE</button>
    </form>
    <button onclick="hideExClose()" class="btn" style="background:var(--border);width:140px;padding:14px 0;font-size:15px;font-weight:600">❌ CANCEL</button>
    </div></div></div>

    <style>
    @keyframes exBtnGlow {
        0%, 100% { box-shadow: 0 0 8px rgba(248,81,73,.4); }
        50% { box-shadow: 0 0 20px rgba(248,81,73,.8), 0 0 40px rgba(248,81,73,.3); }
    }
    #exConfirmBtn.btn-ready { animation: exBtnGlow 1s ease-in-out infinite !important; opacity: 1 !important; }
    </style>

    <script>
    let exTimer = null;
    function showExClose(sym, side, symUrl, upnl, notional) {
        window._modalOpen = true;
        const modal = document.getElementById('exCloseModal');
        document.getElementById('exCloseInfo').textContent = sym + ' ' + side;
        const upnlEl = document.getElementById('exCloseUpnl');
        const pct = notional > 0 ? (upnl / notional * 100) : 0;
        upnlEl.textContent = '$' + upnl.toFixed(4) + ' (' + pct.toFixed(2) + '%)';
        upnlEl.className = upnl >= 0 ? 'pos' : 'neg';
        document.getElementById('exCloseForm').action = '/exchange/close/' + symUrl;
        const btn = document.getElementById('exConfirmBtn');
        const cd = document.getElementById('exCountdown');
        btn.disabled = true; btn.style.opacity = '0.5';
        btn.classList.remove('btn-ready');
        modal.classList.add('show');
        let sec = 5; cd.textContent = sec;
        if (exTimer) clearInterval(exTimer);
        exTimer = setInterval(() => {
            sec--; cd.textContent = sec;
            if (sec <= 0) {
                clearInterval(exTimer);
                cd.textContent = '✅';
                btn.disabled = false;
                btn.classList.add('btn-ready');
            }
        }, 1000);
    }
    function hideExClose() {
        clearInterval(exTimer);
        const btn = document.getElementById('exConfirmBtn');
        btn.classList.remove('btn-ready');
        document.getElementById('exCloseModal').classList.remove('show');
        window._modalOpen = false;
    }
    </script>"""

    return layout("\U0001f4cd Exchange Positions", metrics + grid + ex_close_modal, "/exchange/positions")


def page_exchange_position_detail(symbol_url):
    """Detailed view of a single exchange position."""
    if not _executor:
        return layout("\U0001f4cd Position Detail", '<div class="card"><p>Exchange executor not configured</p></div>', "/exchange/positions")

    # Convert URL symbol back: BTC_USDT_USDT -> BTC/USDT:USDT
    parts = symbol_url.split('_')
    if len(parts) >= 3:
        symbol = f"{parts[0]}/{parts[1]}:{parts[2]}"
    elif len(parts) == 2:
        symbol = f"{parts[0]}/{parts[1]}"
    else:
        symbol = symbol_url

    position = _executor.get_position(symbol)
    if not position:
        return layout("\U0001f4cd Position Detail", f'<div class="card"><p>Position not found: {symbol}</p><a href="/exchange/positions" class="hist-btn">\u2190 Back</a></div>', "/exchange/positions")

    p = position
    side_cls = 'long' if p.side == 'long' else 'short'
    side_icon = '\U0001f7e2' if p.side == 'long' else '\U0001f534'
    upnl_cls = 'pos' if p.unrealized_pnl >= 0 else 'neg'
    pnl_pct = (p.unrealized_pnl / p.notional * 100) if p.notional else 0
    pnl_pct_cls = 'pos' if pnl_pct >= 0 else 'neg'

    # TradingView symbol: determine exchange prefix
    ex_id = getattr(_executor, 'exchange_id', 'binance').upper()
    # Map ccxt exchange_id to TradingView prefix
    tv_prefix_map = {'binance': 'BINANCE', 'bybit': 'BYBIT', 'mexc': 'MEXC', 'gateio': 'GATEIO', 'bitget': 'BITGET'}
    tv_prefix = tv_prefix_map.get(ex_id.lower(), ex_id)
    # Build clean symbol: BTC/USDT:USDT -> BTCUSDT
    base_sym = p.symbol.split(':')[0].replace('/', '')
    tv_symbol = f"{tv_prefix}:{base_sym}.P"

    # ─── Look up SL/TP from bot state files ───────────────
    stop_price = 0.0
    tp_price = 0.0
    bot_source = ""
    # Try Soldier state
    try:
        s_state = rj(SD / "paper_state_multi.json")
        s_active = s_state.get("active_positions", {})
        if base_sym in s_active:
            stop_price = float(s_active[base_sym].get("stop_price", 0))
            tp_price = float(s_active[base_sym].get("tp_price", 0))
            bot_source = "Soldier"
    except Exception:
        pass
    # Try Pump Hunter state
    if not stop_price:
        try:
            p_state = ph_state()
            p_active = p_state.get("active_positions", {})
            if base_sym in p_active:
                stop_price = float(p_active[base_sym].get("stop_price", 0))
                tp_price = float(p_active[base_sym].get("tp_price", 0))
                bot_source = "Pump Hunter"
        except Exception:
            pass

    # Calculate distances to SL/TP
    dist_sl = ""
    dist_tp = ""
    if stop_price > 0 and p.mark_price > 0:
        if p.side == 'long':
            dist_sl_val = (p.mark_price / stop_price - 1) * 100
        else:
            dist_sl_val = (1 - p.mark_price / stop_price) * 100
        sl_style = 'style="border:2px solid var(--red)"' if dist_sl_val < 0.5 else ''
        sl_cls = 'pos' if dist_sl_val > 0 else 'neg'
        dist_sl = f'<div class="card metric" {sl_style}><span class="label">To SL</span><span class="value {sl_cls}">{dist_sl_val:+.2f}%</span></div>'
    if tp_price > 0 and p.mark_price > 0:
        if p.side == 'long':
            dist_tp_val = (tp_price / p.mark_price - 1) * 100
        else:
            dist_tp_val = (1 - tp_price / p.mark_price) * 100
        tp_style = 'style="border:2px solid var(--green)"' if dist_tp_val < 0.5 else ''
        tp_cls = 'neg' if dist_tp_val > 0 else 'pos'
        dist_tp = f'<div class="card metric" {tp_style}><span class="label">To TP</span><span class="value {tp_cls}">{dist_tp_val:+.2f}%</span></div>'

    # Bot source badge
    bot_badge = f'<span class="badge badge-blue">{bot_source}</span>' if bot_source else '<span class="badge" style="background:rgba(139,148,158,.15);color:var(--dim)">Manual</span>'

    # SL/TP display strings
    sl_display = f"${stop_price:,.6g}" if stop_price > 0 else "—"
    tp_display = f"${tp_price:,.6g}" if tp_price > 0 else "—"

    # Fetch recent trades for this symbol
    trade_rows = ''
    try:
        sym_trades = _executor.get_trade_history(symbol=symbol)
        for i, t in enumerate(reversed(sym_trades[-20:]), 1):
            s_cls = 'pos' if t['side'] == 'buy' else 'neg'
            s_icon = '\U0001f7e2' if t['side'] == 'buy' else '\U0001f534'
            fee = t.get('fee', 0) or 0
            trade_rows += f'<tr><td>{i}</td><td>{s_icon} <span class="{s_cls}">{t["side"].upper()}</span></td><td>${t["price"]:,.6g}</td><td>{t["amount"]:,.4g}</td><td>${t["cost"]:,.2f}</td><td class="neg">${fee:,.4f}</td><td style="font-size:11px;color:var(--dim)">{t.get("datetime","")[:19]}</td></tr>'
    except Exception:
        pass

    trade_table = ''
    if trade_rows:
        trade_table = f'''<div class="card" style="margin-top:16px">
        <h3 style="margin-bottom:12px">\U0001f4cb Trade History for {symbol}</h3>
        <table><thead><tr><th>#</th><th>Side</th><th>Price</th><th>Qty</th><th>Cost</th><th>Fee</th><th>Time</th></tr></thead>
        <tbody>{trade_rows}</tbody></table></div>'''

    sym_url = p.symbol.replace('/', '_').replace(':', '_')

    detail = f'''
    <div class="card" style="padding:24px">
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
            <span style="font-size:24px;font-weight:800">{p.symbol}</span>
            <span class="pos-side {side_cls}" style="font-size:13px;padding:4px 14px">{side_icon} {p.side.upper()}</span>
            <span style="font-size:13px;color:var(--dim)">{p.leverage}x leverage</span>
            {bot_badge}
            <a href="/exchange/positions" class="hist-btn" style="margin-left:auto">\u2190 All Positions</a>
        </div>
        <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px">
            <div class="card metric"><span class="label">Entry Price</span><span class="value">${p.entry_price:,.6g}</span></div>
            <div class="card metric"><span class="label">Mark Price</span><span class="value">${p.mark_price:,.6g}</span></div>
            <div class="card metric"><span class="label">Stop Loss</span><span class="value" style="color:var(--red)">{sl_display}</span></div>
            <div class="card metric"><span class="label">Take Profit</span><span class="value" style="color:var(--green)">{tp_display}</span></div>
            <div class="card metric"><span class="label">Size</span><span class="value">{p.size:,.4g}</span></div>
            <div class="card metric"><span class="label">Notional</span><span class="value">${p.notional:,.2f}</span></div>
            <div class="card metric"><span class="label">Unrealized PnL</span><span class="value {upnl_cls}" style="font-size:20px">${p.unrealized_pnl:+,.4f}</span></div>
            <div class="card metric"><span class="label">PnL %</span><span class="value {pnl_pct_cls}" style="font-size:20px">{pnl_pct:+.2f}%</span></div>
            {dist_sl}
            {dist_tp}
        </div>
    </div>

    <!-- TradingView Chart with position lines -->
    <div class="card" style="padding:0;overflow:hidden;border-radius:12px;margin-top:16px;min-height:500px">
    <div id="tv_chart_ex" style="height:500px"></div>
    <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
    <script type="text/javascript">
    var tvWidget = new TradingView.widget({{"autosize":true,"symbol":"{tv_symbol}","interval":"15","timezone":"Etc/UTC",
    "theme":"dark","style":"1","locale":"en","toolbar_bg":"#0a0e17","enable_publishing":false,
    "hide_side_toolbar":false,"allow_symbol_change":true,"container_id":"tv_chart_ex",
    "studies":["Volume@tv-basicstudies"],
    "width":"100%","height":"500"}});
    tvWidget.onChartReady(function() {{
        var chart = tvWidget.activeChart();
        // Entry price line (blue)
        if ({p.entry_price} > 0) {{
            chart.createShape({{price: {p.entry_price}}}, {{shape: 'horizontal_line', lock: true, disableSelection: true, overrides: {{linecolor: '#58a6ff', linestyle: 2, linewidth: 2, showLabel: true, text: 'ENTRY {p.entry_price:,.6g}'}}}});
        }}
        // Stop Loss line (red)
        if ({stop_price} > 0) {{
            chart.createShape({{price: {stop_price}}}, {{shape: 'horizontal_line', lock: true, disableSelection: true, overrides: {{linecolor: '#f85149', linestyle: 2, linewidth: 1, showLabel: true, text: 'SL {sl_display}'}}}});
        }}
        // Take Profit line (green)
        if ({tp_price} > 0) {{
            chart.createShape({{price: {tp_price}}}, {{shape: 'horizontal_line', lock: true, disableSelection: true, overrides: {{linecolor: '#3fb950', linestyle: 2, linewidth: 1, showLabel: true, text: 'TP {tp_display}'}}}});
        }}
    }});
    </script>
    </div>

    <!-- Close Button -->
    <div style="margin-top:16px;text-align:center">
        <a href="#" onclick="showExDetailClose();return false" class="btn-close-pos" style="display:inline-block;padding:12px 40px;font-size:16px">\u2716 Close Position</a>
    </div>

    <!-- Exchange Detail Close Modal -->
    <div id="exDetailCloseModal" class="close-modal">
    <div class="close-modal-box">
    <h2>⚠️ CLOSE {p.symbol}?</h2>
    <p style="color:var(--dim);margin-bottom:8px">{p.side.upper()} x{p.leverage} · {p.size:,.4g} contracts</p>
    <p>uPnL: <strong class="{upnl_cls}" style="font-size:20px">${p.unrealized_pnl:+,.4f} ({pnl_pct:+.2f}%)</strong></p>
    <div class="countdown" id="exdCountdown">5</div>
    <p style="color:var(--dim);font-size:13px;margin-bottom:20px">Confirm within countdown</p>
    <div style="display:flex;align-items:center;justify-content:center;gap:16px">
    <form method="POST" action="/exchange/close/{sym_url}" id="exdCloseForm" style="margin:0;padding:0;display:block">
    <button type="submit" class="btn btn-red" id="exdConfirmBtn" disabled style="opacity:0.5;width:200px;padding:14px 0;font-size:15px;font-weight:700">⚠️ CONFIRM CLOSE</button>
    </form>
    <button onclick="hideExDetailClose()" class="btn" style="background:var(--border);width:140px;padding:14px 0;font-size:15px;font-weight:600">❌ CANCEL</button>
    </div></div></div>
    <style>
    @keyframes exdBtnGlow {{
        0%, 100% {{ box-shadow: 0 0 8px rgba(248,81,73,.4); }}
        50% {{ box-shadow: 0 0 20px rgba(248,81,73,.8), 0 0 40px rgba(248,81,73,.3); }}
    }}
    #exdConfirmBtn.btn-ready {{ animation: exdBtnGlow 1s ease-in-out infinite !important; opacity: 1 !important; }}
    </style>
    <script>
    let exdTimer = null;
    function showExDetailClose() {{
        window._modalOpen = true;
        const modal = document.getElementById('exDetailCloseModal');
        const btn = document.getElementById('exdConfirmBtn');
        const cd = document.getElementById('exdCountdown');
        btn.disabled = true; btn.style.opacity = '0.5';
        btn.classList.remove('btn-ready');
        modal.classList.add('show');
        let sec = 5; cd.textContent = sec;
        if (exdTimer) clearInterval(exdTimer);
        exdTimer = setInterval(() => {{
            sec--; cd.textContent = sec;
            if (sec <= 0) {{
                clearInterval(exdTimer);
                cd.textContent = '✅';
                btn.disabled = false;
                btn.classList.add('btn-ready');
            }}
        }}, 1000);
    }}
    function hideExDetailClose() {{
        clearInterval(exdTimer);
        const btn = document.getElementById('exdConfirmBtn');
        btn.classList.remove('btn-ready');
        document.getElementById('exDetailCloseModal').classList.remove('show');
        window._modalOpen = false;
    }}
    </script>
    {trade_table}'''

    return layout(f"\U0001f4cd {p.symbol} Position", detail, "/exchange/positions")


def handle_exchange_close(symbol_url):
    """Close an exchange position via market order."""
    if not _executor:
        return layout("\u274c Error", '<div class="card"><p>Exchange executor not configured</p></div>', "/exchange/positions")

    # Convert URL symbol back
    parts = symbol_url.split('_')
    if len(parts) >= 3:
        symbol = f"{parts[0]}/{parts[1]}:{parts[2]}"
    elif len(parts) == 2:
        symbol = f"{parts[0]}/{parts[1]}"
    else:
        symbol = symbol_url

    try:
        result = _executor.close_position(symbol)
        if result.success:
            msg = f'''<div class="card" style="padding:24px;text-align:center">
            <h2 style="color:var(--green);margin-bottom:12px">\u2705 Position Closed</h2>
            <p style="font-size:16px;margin-bottom:8px"><strong>{symbol}</strong></p>
            <p>Fill Price: <strong>${result.fill_price:,.6g}</strong></p>
            <p>Quantity: <strong>{result.fill_qty:,.4g}</strong></p>
            <p>Fee: <span class="neg">${result.fee:,.4f}</span></p>
            <p style="margin-top:16px"><a href="/exchange/positions" class="hist-btn">\u2190 Back to Positions</a></p>
            </div>'''
        else:
            msg = f'''<div class="card" style="padding:24px;text-align:center">
            <h2 style="color:var(--red);margin-bottom:12px">\u274c Close Failed</h2>
            <p>{result.error}</p>
            <p style="margin-top:16px"><a href="/exchange/positions" class="hist-btn">\u2190 Back</a></p>
            </div>'''
    except Exception as e:
        msg = f'''<div class="card" style="padding:24px;text-align:center">
        <h2 style="color:var(--red);margin-bottom:12px">\u274c Error</h2>
        <p>{str(e)}</p>
        <p style="margin-top:16px"><a href="/exchange/positions" class="hist-btn">\u2190 Back</a></p>
        </div>'''

    return layout("\U0001f4cd Close Position", msg, "/exchange/positions")


# ─── Server ──────────────────────────────────
ROUTES = {"/": page_home, "/scalper": page_scalper, "/scalper/trades": page_trades, "/scalper/history": page_history, "/scalper/analyze": page_analyze, "/scalper/control": page_control, "/pumps": page_pumps, "/pumps/trades": page_pumps_trades, "/pumps/analyze": page_pumps_analyze, "/exchange": page_exchange_dashboard, "/exchange/history": page_exchange_history, "/exchange/equity": page_exchange_equity, "/exchange/positions": page_exchange_positions}
if page_insider:
    ROUTES["/insider"] = page_insider
if page_insider_signals:
    ROUTES["/insider/signals"] = page_insider_signals
if page_iie:
    ROUTES["/iie"] = page_iie
if page_iie_impulses:
    ROUTES["/iie/impulses"] = page_iie_impulses
if page_iie_coins:
    ROUTES["/iie/coins"] = page_iie_coins
if page_iie_config:
    ROUTES["/iie/config"] = page_iie_config

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # Dynamic route: /position/SYMBOL
        if path.startswith("/scalper/position/"):
            symbol = path.split("/scalper/position/")[1]
            html = page_position(symbol)
        elif path.startswith("/scalper/trade/"):
            try:
                idx = int(path.split("/scalper/trade/")[1])
            except ValueError:
                idx = -1
            html = page_trade(idx)
        elif path.startswith("/exchange/position/"):
            sym_url = path.split("/exchange/position/")[1]
            html = page_exchange_position_detail(sym_url)
        elif path.startswith("/pumps/position/"):
            key_url = urllib.parse.unquote(path.split("/pumps/position/")[1])
            html = page_pump_position(key_url)
        elif path.startswith("/insider/trade/") and page_insider_trade:
            try:
                idx = int(path.split("/insider/trade/")[1])
            except ValueError:
                idx = -1
            html = page_insider_trade(idx)
        elif path.startswith("/insider/position/") and page_insider_position:
            pos_key = urllib.parse.unquote(path.split("/insider/position/")[1])
            html = page_insider_position(pos_key)
        else:
            handler = ROUTES.get(path, page_home)
            html = handler()

        try:
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client disconnected (auto-refresh race), safe to ignore

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path.startswith("/scalper/close/"):
            symbol = path.split("/scalper/close/")[1]
            html = handle_close_position(symbol)
        elif path.startswith("/pumps/close/"):
            symbol = urllib.parse.unquote(path.split("/pumps/close/")[1])
            html = handle_pump_close(symbol)
        elif path.startswith("/exchange/close/"):
            sym_url = path.split("/exchange/close/")[1]
            html = handle_exchange_close(sym_url)
        elif path == "/iie/api/config" and handle_iie_config_update:
            content_len = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_len)
            result = handle_iie_config_update(post_data)
            try:
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(result.encode('utf-8'))
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        else:
            html = layout("Error", '<div class="card"><p>Unknown action</p></div>', "/")

        try:
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client disconnected, safe to ignore

    def log_message(self, fmt, *a): pass

if __name__ == "__main__":
    import argparse, socket, time as _time, signal
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--bind", default="127.0.0.1", help="Bind address (default: 127.0.0.1, SSH tunnel only)")
    args = p.parse_args()
    print(f"📊 HQ Dashboard on http://{args.bind}:{args.port}")
    print(f"🔒 Access via SSH tunnel: ssh -i ~/.ssh/profitrade_trader.pem -p 48113 -N -L {args.port}:localhost:{args.port} trader@185.207.67.130")

    # Global server ref for clean shutdown
    _server_instance = None
    def _shutdown_handler(signum, frame):
        print(f"\n🛑 Received signal {signum}, shutting down...")
        if _server_instance:
            _server_instance.shutdown()
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    # Custom server class with socket reuse forced at creation time
    class ReusableTCPServer(socketserver.TCPServer):
        allow_reuse_address = True
        def server_bind(self):
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                # SO_REUSEPORT allows multiple processes; helps with fast restart
                if hasattr(socket, 'SO_REUSEPORT'):
                    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
            super().server_bind()

    # Retry loop: wait up to 30s for port to free after PM2 restart
    max_attempts = 10
    for attempt in range(1, max_attempts + 1):
        try:
            with ReusableTCPServer((args.bind, args.port), Handler) as h:
                _server_instance = h
                print(f"✅ Listening on {args.bind}:{args.port} (attempt {attempt})")
                try:
                    h.serve_forever()
                except KeyboardInterrupt:
                    print("Stopped.")
                finally:
                    h.server_close()
                    _server_instance = None
            break
        except OSError as e:
            if "Address already in use" in str(e) and attempt < max_attempts:
                print(f"⚠️ Port {args.port} busy, retrying in 3s... ({attempt}/{max_attempts})")
                _time.sleep(3)
            else:
                raise


