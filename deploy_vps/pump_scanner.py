"""
🎯 Pump Hunter v3 — Dormant Coin Breakout Scanner

Detects crypto that consolidated 1+ month then pumped 50%+.
Adaptive trailing stop to ride the full pump.

Flow:
  Scanner detects pump → Telegram alert (🟡50% / 🟠100% / 🔴200%+)
  → User confirms via ✅ button → Trailing stop position
  → If price returns below consolidation → FALSE BREAKOUT → cancel

Supports: Bybit + MEXC futures
"""

import json
import time
import logging
import signal
import sys
import os
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Optional, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

# Import exchange executor (optional — works without it in paper mode)
try:
    import sys
    # executor is in deploy_vps directory
    _deploy_dir = str(Path(__file__).resolve().parent.parent.parent / "Crypto-Code" / "deploy_vps")
    if _deploy_dir not in sys.path:
        sys.path.insert(0, _deploy_dir)
    from exchange_executor import ExchangeExecutor
except ImportError:
    ExchangeExecutor = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("PumpHunter")

from config import (
    TG_TOKEN, TG_CHAT_ID, TG_THREAD_ID,
    SCAN_INTERVAL_SEC, KLINES_CACHE_TTL_SEC,
    CONSOLIDATION_DAYS, CONSOLIDATION_MAX_RANGE_PCT,
    MIN_PUMP_PCT, MIN_TURNOVER_24H, PREFILTER_24H_CHANGE_PCT,
    ALERT_COOLDOWN_SEC, FALSE_BREAKOUT_COOLDOWN_SEC,
    TIER_EARLY, TIER_CONFIRMED, TIER_MEGA,
    TRAIL_PHASES, ENABLE_BYBIT, ENABLE_MEXC, ENABLE_GATEIO, ENABLE_BITGET,
    AUTO_ENTER, DEMO_BALANCE, DEMO_POSITION_SIZE_PCT,
    DEMO_MAX_POSITIONS, DEMO_STATE_FILE,
)


# ─── Data Classes ───────────────────────────────────────────
@dataclass
class ConsolidationZone:
    high: float
    low: float
    mean: float
    volatility_pct: float
    days: int


@dataclass
class PumpAlert:
    symbol: str
    exchange: str            # "bybit" or "mexc"
    pump_pct: float
    current_price: float
    consolidation: ConsolidationZone
    detected_at: float
    message_id: int = 0
    confirmed: bool = False
    cancelled: bool = False
    # Position tracking
    position_entry: float = 0.0
    position_sl: float = 0.0
    peak_price: float = 0.0
    trailing_stop: float = 0.0
    pnl_pct: float = 0.0
    exited: bool = False
    exit_reason: str = ""


# ─── Telegram Bot ───────────────────────────────────────────
class TelegramBot:
    def __init__(self, token: str, chat_id: str, thread_id: str = ""):
        self.token = token
        self.chat_id = chat_id
        self.thread_id = int(thread_id) if thread_id else None
        self.base = f"https://api.telegram.org/bot{token}"
        self.last_update_id = 0
        self.enabled = bool(token and chat_id)
        if self.enabled:
            topic = f", topic: {thread_id}" if thread_id else ""
            logger.info(f"📱 Telegram ON (chat: {chat_id[:8]}...{topic})")

    def send(self, text: str, reply_markup: dict = None) -> int:
        if not self.enabled:
            return 0
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"}
        if self.thread_id:
            payload["message_thread_id"] = self.thread_id
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        try:
            resp = requests.post(f"{self.base}/sendMessage", json=payload, timeout=15)
            data = resp.json()
            if data.get("ok"):
                return data["result"]["message_id"]
            else:
                logger.warning(f"TG error: {data}")
        except Exception as e:
            logger.warning(f"TG error: {e}")
        return 0

    def send_pump_alert(self, alert: PumpAlert) -> int:
        c = alert.consolidation
        pump = alert.pump_pct
        if pump >= TIER_MEGA:
            tier = "🔴 МЕГА-ПАМП"
        elif pump >= TIER_CONFIRMED:
            tier = "🟠 ПАМП"
        else:
            tier = "🟡 РАННИЙ СИГНАЛ"

        text = (
            f"🚨 *{tier}*\n\n"
            f"🪙 *{alert.symbol}* ({alert.exchange.upper()})\n"
            f"📈 Рост: *+{pump:.0f}%* от проторговки\n"
            f"💰 Цена: `{alert.current_price}`\n\n"
            f"📊 *Проторговка ({c.days}д):*\n"
            f"   High: `{c.high}` | Low: `{c.low}`\n"
            f"   Mean: `{c.mean}` | Range: {c.volatility_pct:.1f}%\n\n"
            f"🛑 SL: `{c.high}` (верх зоны)\n"
            f"📐 Trail: адаптивный 30%→15%\n\n"
            f"_Проверьте график и подтвердите:_"
        )
        kb = {"inline_keyboard": [[
            {"text": "✅ Подтвердить LONG", "callback_data": f"pump_long:{alert.symbol}:{alert.exchange}"},
            {"text": "❌ Пропустить", "callback_data": f"pump_skip:{alert.symbol}"},
        ]]}
        return self.send(text, reply_markup=kb)

    def send_trail_update(self, alert: PumpAlert, current_price: float):
        profit = alert.pnl_pct
        trail_pct = get_trail_pct(profit)
        self.send(
            f"📊 *TRAIL UPDATE* | {alert.symbol}\n"
            f"💰 Price: `{current_price}` | Peak: `{alert.peak_price}`\n"
            f"📈 PnL: *+{profit:.0f}%* | Trail: {trail_pct}%\n"
            f"🛑 Stop: `{alert.trailing_stop:.8g}`"
        )

    def send_exit(self, alert: PumpAlert, current_price: float):
        self.send(
            f"🏁 *EXIT* | {alert.symbol}\n\n"
            f"💰 Entry: `{alert.position_entry}` → Exit: `{current_price}`\n"
            f"📈 PnL: *{alert.pnl_pct:+.1f}%*\n"
            f"🔝 Peak: `{alert.peak_price}` (+{((alert.peak_price/alert.position_entry)-1)*100:.0f}%)\n"
            f"📋 Reason: {alert.exit_reason}"
        )

    def poll_updates(self) -> Tuple[List[dict], List[dict]]:
        """Poll for callbacks AND commands. Returns (callbacks, commands)."""
        if not self.enabled:
            return [], []
        try:
            resp = requests.get(
                f"{self.base}/getUpdates",
                params={"offset": self.last_update_id + 1, "timeout": 1},
                timeout=5,
            )
            callbacks = []
            commands = []
            for upd in resp.json().get("result", []):
                self.last_update_id = upd["update_id"]
                # Inline button press
                cb = upd.get("callback_query")
                if cb:
                    callbacks.append({"id": cb["id"], "data": cb["data"]})
                    requests.post(f"{self.base}/answerCallbackQuery",
                                  json={"callback_query_id": cb["id"]}, timeout=5)
                # Text command
                msg = upd.get("message", {})
                text = msg.get("text", "")
                if text.startswith("/"):
                    cmd = text.split()[0].split("@")[0].lower()
                    commands.append({"cmd": cmd, "chat_id": msg.get("chat", {}).get("id")})
            return callbacks, commands
        except Exception:
            return [], []

    # Keep backward compat
    def poll_callbacks(self) -> List[dict]:
        cbs, _ = self.poll_updates()
        return cbs


# ─── Trailing Stop Logic ────────────────────────────────────
def get_trail_pct(profit_pct: float) -> float:
    """Get trailing stop % based on current profit phase."""
    trail = TRAIL_PHASES[0][1]  # default
    for min_profit, pct in TRAIL_PHASES:
        if profit_pct >= min_profit:
            trail = pct
    return trail


def update_trailing_stop(alert: PumpAlert, current_price: float) -> Optional[str]:
    """Update trailing stop. Returns exit reason or None."""
    if not alert.confirmed or alert.position_entry <= 0:
        return None

    profit_pct = (current_price / alert.position_entry - 1.0) * 100
    alert.pnl_pct = profit_pct

    # Update peak
    if current_price > alert.peak_price:
        alert.peak_price = current_price

    # Calculate trailing stop
    trail_pct = get_trail_pct(profit_pct)
    new_stop = alert.peak_price * (1 - trail_pct / 100)

    # Trailing stop only moves UP, never down
    if new_stop > alert.trailing_stop:
        alert.trailing_stop = new_stop

    # Ensure trailing stop is at least at consolidation high (initial SL)
    if alert.trailing_stop < alert.position_sl:
        alert.trailing_stop = alert.position_sl

    # Check stop hit
    if current_price <= alert.trailing_stop:
        return "trailing_stop"

    # Check false breakout (price back in consolidation zone)
    if current_price <= alert.consolidation.high:
        return "false_breakout"

    return None


# ─── Exchange APIs ──────────────────────────────────────────
def fetch_bybit_tickers() -> List[dict]:
    """Fetch all Bybit linear USDT futures tickers."""
    try:
        resp = requests.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear"}, timeout=10,
        )
        data = resp.json()
        if data.get("retCode") != 0:
            return []
        return [
            {"symbol": t["symbol"], "exchange": "bybit",
             "lastPrice": float(t["lastPrice"]),
             "price24hPcnt": float(t.get("price24hPcnt", 0)),
             "turnover24h": float(t.get("turnover24h", 0))}
            for t in data["result"]["list"]
            if t["symbol"].endswith("USDT")
            and float(t.get("turnover24h", 0)) > MIN_TURNOVER_24H
        ]
    except Exception as e:
        logger.warning(f"Bybit tickers error: {e}")
        return []


def fetch_mexc_tickers() -> List[dict]:
    """Fetch all MEXC USDT futures tickers."""
    try:
        resp = requests.get(
            "https://contract.mexc.com/api/v1/contract/ticker",
            timeout=10,
        )
        data = resp.json()
        if not data.get("success"):
            return []
        results = []
        for t in data.get("data", []):
            symbol = t.get("symbol", "")
            if not symbol.endswith("_USDT"):
                continue
            last_price = float(t.get("lastPrice", 0))
            fair_price = float(t.get("fairPrice", 0))
            volume_24h = float(t.get("volume24", 0))
            turnover = last_price * volume_24h
            if turnover < MIN_TURNOVER_24H:
                continue
            change_pct = float(t.get("riseFallRate", 0))
            # Normalize symbol: RAVE_USDT → RAVEUSDT
            norm_symbol = symbol.replace("_", "")
            results.append({
                "symbol": norm_symbol, "exchange": "mexc",
                "lastPrice": last_price,
                "price24hPcnt": change_pct,
                "turnover24h": turnover,
            })
        return results
    except Exception as e:
        logger.warning(f"MEXC tickers error: {e}")
        return []


def fetch_gateio_tickers() -> List[dict]:
    """Fetch all Gate.io USDT futures tickers."""
    try:
        resp = requests.get(
            "https://api.gateio.ws/api/v4/futures/usdt/tickers",
            timeout=10,
        )
        data = resp.json()
        if not isinstance(data, list):
            return []
        results = []
        for t in data:
            contract = t.get("contract", "")
            if not contract.endswith("_USDT"):
                continue
            last_price = float(t.get("last", 0))
            change_pct = float(t.get("change_percentage", 0)) / 100  # Gate gives % as number
            volume = float(t.get("volume_24h_quote", 0))
            if volume < MIN_TURNOVER_24H:
                continue
            norm_symbol = contract.replace("_", "")
            results.append({
                "symbol": norm_symbol, "exchange": "gateio",
                "lastPrice": last_price,
                "price24hPcnt": change_pct,
                "turnover24h": volume,
            })
        return results
    except Exception as e:
        logger.warning(f"Gate.io tickers error: {e}")
        return []


def fetch_bitget_tickers() -> List[dict]:
    """Fetch all Bitget USDT-FUTURES tickers."""
    try:
        resp = requests.get(
            "https://api.bitget.com/api/v2/mix/market/tickers",
            params={"productType": "USDT-FUTURES"},
            timeout=10,
        )
        data = resp.json()
        results = []
        for t in data.get("data", []):
            symbol = t.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            last_price = float(t.get("lastPr", 0))
            open_price = float(t.get("open24h", 0))
            change_pct = (last_price / open_price - 1) if open_price > 0 else 0
            turnover = float(t.get("quoteVolume", 0))
            if turnover < MIN_TURNOVER_24H:
                continue
            results.append({
                "symbol": symbol, "exchange": "bitget",
                "lastPrice": last_price,
                "price24hPcnt": change_pct,
                "turnover24h": turnover,
            })
        return results
    except Exception as e:
        logger.warning(f"Bitget tickers error: {e}")
        return []


def fetch_klines(symbol: str, exchange: str = "bybit",
                 interval: str = "D", limit: int = 50) -> pd.DataFrame:
    """Fetch daily OHLCV klines from exchange."""
    fetchers = {
        "mexc": _fetch_mexc_klines,
        "gateio": _fetch_gateio_klines,
        "bitget": _fetch_bitget_klines,
    }
    return fetchers.get(exchange, _fetch_bybit_klines)(symbol, interval, limit)


def _fetch_bybit_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    try:
        resp = requests.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "linear", "symbol": symbol,
                    "interval": interval, "limit": limit},
            timeout=10,
        )
        data = resp.json()
        if data.get("retCode") != 0:
            return pd.DataFrame()
        rows = data["result"]["list"]
        rows.reverse()
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna().reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def _fetch_mexc_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    mexc_intervals = {"D": "Day1", "60": "Hour1", "30": "Min30"}
    mexc_interval = mexc_intervals.get(interval, "Day1")
    mexc_sym = symbol.replace("USDT", "_USDT")
    try:
        resp = requests.get(
            f"https://contract.mexc.com/api/v1/contract/kline/{mexc_sym}",
            params={"interval": mexc_interval, "limit": limit},
            timeout=10,
        )
        data = resp.json()
        if not data.get("success"):
            return pd.DataFrame()
        rows = data.get("data", {}).get("time", [])
        opens = data.get("data", {}).get("open", [])
        highs = data.get("data", {}).get("high", [])
        lows = data.get("data", {}).get("low", [])
        closes = data.get("data", {}).get("close", [])
        vols = data.get("data", {}).get("vol", [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame({
            "timestamp": pd.to_datetime(rows, unit="s", utc=True),
            "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": vols,
        })
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna().reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def _fetch_gateio_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    gate_intervals = {"D": "1d", "60": "1h", "30": "30m"}
    gate_interval = gate_intervals.get(interval, "1d")
    gate_sym = symbol.replace("USDT", "_USDT")
    try:
        resp = requests.get(
            f"https://api.gateio.ws/api/v4/futures/usdt/candlesticks",
            params={"contract": gate_sym, "interval": gate_interval, "limit": limit},
            timeout=10,
        )
        rows = resp.json()
        if not rows or not isinstance(rows, list):
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["t", "volume", "close", "high", "low", "open"])
        df["timestamp"] = pd.to_datetime(df["t"].astype(int), unit="s", utc=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df[["timestamp", "open", "high", "low", "close", "volume"]].dropna().reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def _fetch_bitget_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    bg_intervals = {"D": "1D", "60": "1H", "30": "30m"}
    bg_interval = bg_intervals.get(interval, "1D")
    try:
        resp = requests.get(
            "https://api.bitget.com/api/v2/mix/market/candles",
            params={"productType": "USDT-FUTURES", "symbol": symbol,
                    "granularity": bg_interval, "limit": str(limit)},
            timeout=10,
        )
        data = resp.json()
        rows = data.get("data", [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna().sort_values("timestamp").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


# ─── Analysis ───────────────────────────────────────────────
def analyze_for_pump(daily_df: pd.DataFrame) -> Optional[Tuple[ConsolidationZone, float, float]]:
    """Check if coin was dormant 30+ days then pumped 50%+."""
    if len(daily_df) < 10:
        return None

    split = max(5, len(daily_df) - 3)
    consolidation_df = daily_df.iloc[:split]
    current_price = float(daily_df["close"].iloc[-1])

    if len(consolidation_df) < max(10, CONSOLIDATION_DAYS // 2):
        return None

    cons_high = float(consolidation_df["high"].max())
    cons_low = float(consolidation_df["low"].min())
    cons_mean = float(consolidation_df["close"].mean())

    if cons_mean <= 0:
        return None

    range_pct = (cons_high - cons_low) / cons_mean * 100
    if range_pct > CONSOLIDATION_MAX_RANGE_PCT:
        return None

    pump_pct = (current_price / cons_mean - 1.0) * 100
    if pump_pct < MIN_PUMP_PCT:
        return None

    zone = ConsolidationZone(
        high=round(cons_high, 8), low=round(cons_low, 8),
        mean=round(cons_mean, 8), volatility_pct=round(range_pct, 1),
        days=len(consolidation_df),
    )
    return zone, pump_pct, current_price


# ─── Main Scanner ───────────────────────────────────────────
def _load_demo_state() -> dict:
    """Load persistent demo state from disk."""
    state_path = Path(__file__).parent / DEMO_STATE_FILE
    try:
        if state_path.exists():
            data = json.loads(state_path.read_text(encoding="utf-8"))
            logger.info(f"📂 Loaded state: {len(data.get('completed_trades',[]))} completed, {len(data.get('active_positions',[]))} active")
            return data
    except Exception as e:
        logger.warning(f"Failed to load state: {e}")
    return {}


def _save_demo_state(active_alerts: Dict[str, 'PumpAlert'], demo_completed: list,
                     scan_count: int, start_ts: float, demo_balance: float,
                     executor=None):
    """Save current state to disk for HQ monitor."""
    state_path = Path(__file__).parent / DEMO_STATE_FILE
    now = time.time()
    uptime = int(now - start_ts) if start_ts else 0

    # Build active positions dict
    active_positions = {}
    for key, alert in active_alerts.items():
        if alert.confirmed and not alert.exited:
            active_positions[alert.symbol] = {
                "symbol": alert.symbol,
                "exchange": alert.exchange,
                "direction": "long",
                "entry_price": alert.position_entry,
                "stop_price": alert.position_sl,
                "trailing_stop": alert.trailing_stop,
                "peak_price": alert.peak_price,
                "pnl_pct": alert.pnl_pct,
                "pump_pct": alert.pump_pct,
                "consolidation": asdict(alert.consolidation),
                "detected_at": alert.detected_at,
                "strategy_name": "pump_hunter",
            }

    # Stats
    wins = sum(1 for t in demo_completed if t.get("pnl_pct", 0) > 0)
    losses = len(demo_completed) - wins
    total_pnl = sum(t.get("pnl_pct", 0) for t in demo_completed)

    # Exchange balance (if executor available)
    trading_mode = os.getenv("TRADING_MODE", "paper").lower()
    exchange_balance = 0.0
    if executor and trading_mode != "paper":
        try:
            exchange_balance = executor.get_balance()
        except Exception:
            pass

    state = {
        "scanner": "pump_hunter_v3",
        "scan_count": scan_count,
        "uptime_sec": uptime,
        "start_ts": start_ts,
        "demo_balance": demo_balance,
        "wins": wins,
        "losses": losses,
        "total_pnl_pct": round(total_pnl, 4),
        "trading_mode": trading_mode,
        "exchange_balance": exchange_balance,
        "active_positions": active_positions,
        "completed_trades": demo_completed,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }

    try:
        state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to save state: {e}")


def run_scanner():
    tg = TelegramBot(TG_TOKEN, TG_CHAT_ID, TG_THREAD_ID)
    active_alerts: Dict[str, PumpAlert] = {}
    cooldowns: Dict[str, float] = {}
    klines_cache: Dict[str, Tuple[pd.DataFrame, float]] = {}

    running = True
    def stop_handler(sig, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    exchanges = []
    if ENABLE_BYBIT:
        exchanges.append("Bybit")
    if ENABLE_MEXC:
        exchanges.append("MEXC")
    if ENABLE_GATEIO:
        exchanges.append("Gate.io")
    if ENABLE_BITGET:
        exchanges.append("Bitget")

    # Demo trading state — load persisted data
    saved = _load_demo_state()
    demo_balance = saved.get("demo_balance", DEMO_BALANCE)
    demo_completed = saved.get("completed_trades", [])
    demo_total_pnl = sum(t.get("pnl_pct", 0) for t in demo_completed)

    # Restore active positions from saved state
    saved_positions = saved.get("active_positions", {})
    for sym, pos_data in saved_positions.items():
        try:
            cons_data = pos_data.get("consolidation", {})
            zone = ConsolidationZone(
                high=float(cons_data.get("high", 0)),
                low=float(cons_data.get("low", 0)),
                mean=float(cons_data.get("mean", 0)),
                volatility_pct=float(cons_data.get("volatility_pct", 0)),
                days=int(cons_data.get("days", 0)),
            )
            exchange = pos_data.get("exchange", "bybit")
            alert = PumpAlert(
                symbol=sym,
                exchange=exchange,
                pump_pct=float(pos_data.get("pump_pct", 0)),
                current_price=float(pos_data.get("entry_price", 0)),
                consolidation=zone,
                detected_at=float(pos_data.get("detected_at", time.time())),
                confirmed=True,
                position_entry=float(pos_data.get("entry_price", 0)),
                position_sl=float(pos_data.get("stop_price", 0)),
                peak_price=float(pos_data.get("peak_price", 0)),
                trailing_stop=float(pos_data.get("trailing_stop", 0)),
                pnl_pct=float(pos_data.get("pnl_pct", 0)),
            )
            key = f"{sym}:{exchange}"
            active_alerts[key] = alert
            logger.info(f"📂 Restored position: {sym} ({exchange}) LONG @ {alert.position_entry} (pnl: {alert.pnl_pct:+.1f}%)")
        except Exception as e:
            logger.warning(f"⚠️ Failed to restore position {sym}: {e}")
    if active_alerts:
        logger.info(f"📂 Restored {len(active_alerts)} active positions from state")

    startup = (
        f"🎯 *PUMP HUNTER v3 STARTED*\n"
        f"📊 Detection: +{MIN_PUMP_PCT}%+ | Cons: {CONSOLIDATION_DAYS}d\n"
        f"📐 Trail: adaptive {TRAIL_PHASES[0][1]}%→{TRAIL_PHASES[-1][1]}%\n"
        f"🏦 Exchanges: {', '.join(exchanges)}\n"
        f"🔄 Scan every {SCAN_INTERVAL_SEC // 60} min\n"
        f"{'🤖 AUTO-ENTER: ON' if AUTO_ENTER else '👋 Manual confirm'}\n"
        f"💰 Demo balance: ${demo_balance:,.0f}\n"
        f"📂 History: {len(demo_completed)} past trades | Active: {len(active_alerts)}"
    )
    logger.info(startup)
    tg.send(startup)

    scan_count = saved.get("scan_count", 0)
    last_trail_report = 0
    start_ts = time.time()

    # ─── Exchange Executor ─────────────────────────────────
    executor = None
    trading_mode = os.getenv("TRADING_MODE", "paper").lower()
    if ExchangeExecutor and trading_mode != "paper":
        try:
            executor = ExchangeExecutor.from_env(bot_id="pump_hunter")
            info = executor.test_connection()
            ex_msg = f"⚡ Exchange connected: {executor} | Balance: ${info.get('balance_usdt', 0):.2f}"
            logger.info(ex_msg); tg.send(ex_msg)
        except Exception as e:
            logger.error(f"⚠️ Exchange init failed: {e} — paper mode")
            executor = None
    else:
        logger.info("📝 Pump Hunter running in PAPER mode")

    while running:
        loop_start = time.time()
        scan_count += 1
        now = time.time()

        # ── 1. Process Telegram callbacks ──────────────────────
        _process_callbacks(tg, active_alerts, now, scan_count, start_ts)

        # ── 2. Update trailing stops for confirmed positions ───
        _update_positions(tg, active_alerts, cooldowns, now,
                          demo_completed=demo_completed, executor=executor)

        # ── 3. Scan for new pumps ──────────────────────────────
        all_tickers = []
        if ENABLE_BYBIT:
            bybit_t = fetch_bybit_tickers()
            all_tickers.extend(bybit_t)
            logger.info(f"   Bybit: {len(bybit_t)} tickers")
        if ENABLE_MEXC:
            mexc_t = fetch_mexc_tickers()
            all_tickers.extend(mexc_t)
            logger.info(f"   MEXC: {len(mexc_t)} tickers")
        if ENABLE_GATEIO:
            gate_t = fetch_gateio_tickers()
            all_tickers.extend(gate_t)
            logger.info(f"   Gate.io: {len(gate_t)} tickers")
        if ENABLE_BITGET:
            bg_t = fetch_bitget_tickers()
            all_tickers.extend(bg_t)
            logger.info(f"   Bitget: {len(bg_t)} tickers")

        logger.info(f"🔍 Scan #{scan_count} — {len(all_tickers)} total tickers")

        # Pre-filter by 24h change
        candidates = []
        for t in all_tickers:
            sym = t["symbol"]
            key = f"{sym}:{t['exchange']}"
            if key in active_alerts:
                continue
            if sym in cooldowns and now < cooldowns[sym]:
                continue
            if abs(t["price24hPcnt"]) * 100 >= PREFILTER_24H_CHANGE_PCT:
                candidates.append(t)

        logger.info(f"   Candidates: {len(candidates)} (24h >= {PREFILTER_24H_CHANGE_PCT}%)")

        # Fetch daily klines and analyze
        pump_found = 0
        confirmed_count = sum(1 for a in active_alerts.values() if a.confirmed and not a.exited)

        for t in candidates[:40]:  # Limit API calls
            sym = t["symbol"]
            exch = t["exchange"]
            cache_key = f"{sym}:{exch}"

            # Use cache
            if cache_key in klines_cache:
                df, fetched_at = klines_cache[cache_key]
                if now - fetched_at < KLINES_CACHE_TTL_SEC:
                    pass
                else:
                    df = fetch_klines(sym, exch, "D", 50)
                    if not df.empty:
                        klines_cache[cache_key] = (df, now)
            else:
                df = fetch_klines(sym, exch, "D", 50)
                if not df.empty:
                    klines_cache[cache_key] = (df, now)
                else:
                    continue

            result = analyze_for_pump(df)
            if result is None:
                continue

            zone, pump_pct, price = result
            pump_found += 1
            logger.info(f"🚨 PUMP: {sym} ({exch}) +{pump_pct:.0f}% | range: {zone.volatility_pct:.1f}%")

            alert = PumpAlert(
                symbol=sym, exchange=exch, pump_pct=pump_pct,
                current_price=price, consolidation=zone, detected_at=now,
            )

            # AUTO-ENTER: skip manual confirmation, enter immediately
            if AUTO_ENTER and confirmed_count < DEMO_MAX_POSITIONS:
                alert.confirmed = True
                alert.position_entry = price
                alert.peak_price = price
                alert.position_sl = zone.high
                alert.trailing_stop = zone.high
                pos_size = demo_balance * DEMO_POSITION_SIZE_PCT / 100
                confirmed_count += 1
                # Send combined alert + entry
                tg.send(
                    f"🚨🤖 *AUTO-ENTRY*\n\n"
                    f"🪙 *{sym}* ({exch.upper()})\n"
                    f"📈 Памп: *+{pump_pct:.0f}%* от проторговки\n"
                    f"💰 Entry: `{price}` | Size: ${pos_size:.0f}\n"
                    f"🛑 SL: `{zone.high}` (верх зоны)\n"
                    f"📐 Trail: adaptive {TRAIL_PHASES[0][1]}%→{TRAIL_PHASES[-1][1]}%\n"
                    f"📊 Зона: {zone.days}д | Range: {zone.volatility_pct:.1f}%"
                )
                logger.info(f"🤖 AUTO-ENTER: {sym} LONG @ {price}")

                # ─── Exchange: open position ───
                if executor:
                    ex_result = executor.open_long(sym, pos_size)
                    if ex_result.success and ex_result.fill_price > 0:
                        alert.position_entry = ex_result.fill_price
                        logger.info(f"⚡ Exchange open: LONG {sym} @ {ex_result.fill_price}")
                    elif not ex_result.success:
                        logger.error(f"⚠️ Exchange open failed: {ex_result.error}")
                # ───────────────────────────────
            else:
                msg_id = tg.send_pump_alert(alert)
                alert.message_id = msg_id

            active_alerts[cache_key] = alert
            cooldowns[sym] = now + ALERT_COOLDOWN_SEC

        # ── Save state to disk ─────────────────────────────────
        _save_demo_state(active_alerts, demo_completed, scan_count, start_ts, demo_balance, executor=executor)

        # ── 4. Status ──────────────────────────────────────────
        confirmed = sum(1 for a in active_alerts.values() if a.confirmed and not a.exited)
        pending = sum(1 for a in active_alerts.values() if not a.confirmed and not a.cancelled)

        # Calculate demo stats
        demo_total_pnl = sum(t.get("pnl_pct", 0) for t in demo_completed)
        demo_wins = sum(1 for t in demo_completed if t.get("pnl_pct", 0) > 0)
        demo_losses = len(demo_completed) - demo_wins

        logger.info(
            f"[{datetime.now().strftime('%H:%M:%S')}] "
            f"New: {pump_found} | Pos: {confirmed} | Pending: {pending} | "
            f"Demo: {len(demo_completed)} trades, PnL: {demo_total_pnl:+.1f}%"
        )

        # Trailing stop report
        if now - last_trail_report > 1800:
            for key, alert in active_alerts.items():
                if alert.confirmed and not alert.exited:
                    logger.info(
                        f"   📊 {alert.symbol}: entry={alert.position_entry} "
                        f"peak={alert.peak_price} trail={alert.trailing_stop:.8g} "
                        f"pnl={alert.pnl_pct:+.1f}%"
                    )
            last_trail_report = now

        # ── 5. Sleep with callback polling ─────────────────────
        elapsed = time.time() - loop_start
        sleep_time = max(10, SCAN_INTERVAL_SEC - elapsed)
        logger.info(f"💤 Next scan in {sleep_time / 60:.0f} min")

        sleep_end = time.time() + sleep_time
        while running and time.time() < sleep_end:
            _process_callbacks(tg, active_alerts, time.time(), scan_count, start_ts)
            if any(a.confirmed and not a.exited for a in active_alerts.values()):
                _update_positions(tg, active_alerts, cooldowns, time.time(),
                                  demo_completed=demo_completed, executor=executor)
                _save_demo_state(active_alerts, demo_completed, scan_count, start_ts, demo_balance, executor=executor)
            time.sleep(5)

    logger.info("Stopped.")
    tg.send("🛑 *Pump Hunter STOPPED*")


def _process_callbacks(tg: TelegramBot, active_alerts: Dict, now: float,
                       scan_count: int = 0, start_ts: float = 0):
    """Handle Telegram button presses AND text commands."""
    callbacks, commands = tg.poll_updates()

    # Handle callbacks (inline button presses)
    for cb in callbacks:
        data = cb["data"]
        if data.startswith("pump_long:"):
            parts = data.split(":")
            symbol = parts[1]
            exchange = parts[2] if len(parts) > 2 else "bybit"
            key = f"{symbol}:{exchange}"
            if key in active_alerts and not active_alerts[key].confirmed:
                alert = active_alerts[key]
                price = _get_current_price(symbol, exchange)
                if price:
                    alert.confirmed = True
                    alert.position_entry = price
                    alert.peak_price = price
                    alert.position_sl = alert.consolidation.high
                    alert.trailing_stop = alert.consolidation.high
                    tg.send_entry(alert)
                    logger.info(f"✅ CONFIRMED: {symbol} LONG @ {price}")

        elif data.startswith("pump_skip:"):
            symbol = data.split(":")[1]
            for key, alert in list(active_alerts.items()):
                if alert.symbol == symbol:
                    alert.cancelled = True
                    tg.send(f"❌ {symbol} — пропущен")

    # Handle text commands
    for cmd_data in commands:
        cmd = cmd_data["cmd"]

        if cmd == "/start":
            tg.send(
                f"🎯 *Pump Hunter v2*\n\n"
                f"Сканер аномальных движений на криптофьючерсах.\n"
                f"Ищет монеты которые стояли flat 30+ дней и пампнули 50%+.\n\n"
                f"📋 *Команды:*\n"
                f"/status — статус сканера\n"
                f"/alerts — активные алерты\n"
                f"/positions — открытые позиции\n"
                f"/settings — текущие настройки\n"
                f"/help — помощь"
            )

        elif cmd == "/status":
            uptime = int(now - start_ts) if start_ts else 0
            h, m = uptime // 3600, (uptime % 3600) // 60
            confirmed = sum(1 for a in active_alerts.values() if a.confirmed and not a.exited)
            pending = sum(1 for a in active_alerts.values() if not a.confirmed and not a.cancelled)
            tg.send(
                f"📊 *СТАТУС СКАНЕРА*\n\n"
                f"⏱ Uptime: {h}h {m}m\n"
                f"🔍 Сканов: {scan_count}\n"
                f"🚨 Алертов: {pending} pending | {confirmed} confirmed\n"
                f"🏦 Биржи: {'Bybit' if ENABLE_BYBIT else ''} {'MEXC' if ENABLE_MEXC else ''}\n"
                f"📐 Trail: {TRAIL_PHASES[0][1]}%→{TRAIL_PHASES[-1][1]}%\n"
                f"🔄 Интервал: {SCAN_INTERVAL_SEC // 60} мин"
            )

        elif cmd == "/alerts":
            pending = [a for a in active_alerts.values() if not a.confirmed and not a.cancelled]
            if not pending:
                tg.send("📭 Нет активных алертов")
            else:
                lines = ["🚨 *Активные алерты:*\n"]
                for a in pending:
                    lines.append(
                        f"• {a.symbol} ({a.exchange}) — +{a.pump_pct:.0f}% | "
                        f"цена: {a.current_price}"
                    )
                tg.send("\n".join(lines))

        elif cmd == "/positions":
            confirmed = [a for a in active_alerts.values() if a.confirmed and not a.exited]
            if not confirmed:
                tg.send("📭 Нет открытых позиций")
            else:
                lines = ["💰 *Открытые позиции:*\n"]
                for a in confirmed:
                    trail_pct = get_trail_pct(a.pnl_pct)
                    lines.append(
                        f"• *{a.symbol}* LONG\n"
                        f"  Entry: `{a.position_entry}` | Peak: `{a.peak_price}`\n"
                        f"  PnL: *{a.pnl_pct:+.1f}%* | Trail: {trail_pct}%\n"
                        f"  Stop: `{a.trailing_stop:.8g}`"
                    )
                tg.send("\n".join(lines))

        elif cmd == "/settings":
            tg.send(
                f"⚙️ *Настройки:*\n\n"
                f"📊 Min pump: {MIN_PUMP_PCT}%\n"
                f"📅 Consolidation: {CONSOLIDATION_DAYS}d\n"
                f"📏 Max range: {CONSOLIDATION_MAX_RANGE_PCT}%\n"
                f"💰 Min turnover: ${MIN_TURNOVER_24H/1e6:.1f}M\n"
                f"📐 Trail phases: {TRAIL_PHASES}\n"
                f"⏱ Scan interval: {SCAN_INTERVAL_SEC}s\n"
                f"🏦 Bybit: {'✅' if ENABLE_BYBIT else '❌'} | MEXC: {'✅' if ENABLE_MEXC else '❌'}"
            )

        elif cmd == "/help":
            tg.send(
                f"❓ *Pump Hunter — Помощь*\n\n"
                f"*Как работает:*\n"
                f"1️⃣ Каждые {SCAN_INTERVAL_SEC//60} мин сканирует Bybit + MEXC\n"
                f"2️⃣ Ищет монеты: flat {CONSOLIDATION_DAYS}d → pump {MIN_PUMP_PCT}%+\n"
                f"3️⃣ Шлёт алерт с кнопками ✅/❌\n"
                f"4️⃣ После подтверждения — адаптивный trailing stop\n"
                f"5️⃣ Если цена вернулась в зону — ложный вынос → отмена\n\n"
                f"*Тир алертов:*\n"
                f"🟡 +{TIER_EARLY}% — ранний сигнал\n"
                f"🟠 +{TIER_CONFIRMED}% — подтверждённый памп\n"
                f"🔴 +{TIER_MEGA}%+ — мега-памп"
            )

    # Clean cancelled
    for key in [k for k, a in active_alerts.items() if a.cancelled]:
        del active_alerts[key]


def _update_positions(tg: TelegramBot, active_alerts: Dict,
                      cooldowns: Dict, now: float, demo_completed: list = None,
                      executor=None):
    """Update trailing stops and check exits."""
    if demo_completed is None:
        demo_completed = []
    to_remove = []
    for key, alert in active_alerts.items():
        if not alert.confirmed or alert.exited:
            continue

        price = _get_current_price(alert.symbol, alert.exchange)
        if price is None:
            continue

        exit_reason = update_trailing_stop(alert, price)
        if exit_reason:
            alert.exited = True
            alert.exit_reason = exit_reason
            tg.send_exit(alert, price)
            cooldowns[alert.symbol] = now + FALSE_BREAKOUT_COOLDOWN_SEC
            logger.info(
                f"🏁 EXIT: {alert.symbol} | reason={exit_reason} "
                f"| entry={alert.position_entry} | pnl={alert.pnl_pct:+.1f}%"
            )
            # Save to demo completed trades
            demo_completed.append({
                "symbol": alert.symbol,
                "exchange": alert.exchange,
                "entry": alert.position_entry,
                "exit": price,
                "peak": alert.peak_price,
                "pnl_pct": alert.pnl_pct,
                "exit_reason": exit_reason,
                "time": datetime.now(timezone.utc).isoformat(),
            })

            # ─── Exchange: close position (verified) ───
            if executor:
                ex_result = executor.close_position_verified(alert.symbol, "long")
                if ex_result.success:
                    if ex_result.verified:
                        logger.info(f"⚡ Exchange close VERIFIED: {alert.symbol} @ {ex_result.fill_price}")
                    else:
                        warn_msg = f"⚠️ *CLOSE NOT VERIFIED*: {alert.symbol} — check exchange!"
                        logger.warning(warn_msg); tg.send(warn_msg)
                else:
                    err_msg = f"⚠️ Exchange close failed: {ex_result.error}"
                    logger.error(err_msg); tg.send(err_msg)
            # ───────────────────────────────

            to_remove.append(key)

    for key in to_remove:
        del active_alerts[key]


def _get_current_price(symbol: str, exchange: str) -> Optional[float]:
    """Quick price fetch — supports Bybit, MEXC, Gate.io, Bitget."""
    try:
        if exchange == "mexc":
            mexc_sym = symbol.replace("USDT", "_USDT")
            resp = requests.get(
                "https://contract.mexc.com/api/v1/contract/ticker", timeout=5)
            for t in resp.json().get("data", []):
                if t.get("symbol") == mexc_sym:
                    return float(t["lastPrice"])

        elif exchange == "gateio":
            gate_sym = symbol.replace("USDT", "_USDT")
            resp = requests.get(
                "https://api.gateio.ws/api/v4/futures/usdt/tickers",
                params={"contract": gate_sym}, timeout=5)
            data = resp.json()
            if isinstance(data, list) and data:
                return float(data[0].get("last", 0))

        elif exchange == "bitget":
            resp = requests.get(
                "https://api.bitget.com/api/v2/mix/market/ticker",
                params={"productType": "USDT-FUTURES", "symbol": symbol},
                timeout=5)
            data = resp.json()
            if data.get("data"):
                return float(data["data"][0].get("lastPr", 0))

        else:  # bybit
            resp = requests.get(
                "https://api.bybit.com/v5/market/tickers",
                params={"category": "linear", "symbol": symbol}, timeout=5)
            data = resp.json()
            if data.get("retCode") == 0 and data["result"]["list"]:
                return float(data["result"]["list"][0]["lastPrice"])
    except Exception:
        pass
    return None


def send_entry(self, alert):
    """Alias for TelegramBot — called from _process_callbacks."""
    self.send(
        f"✅ *ВХОД ПОДТВЕРЖДЁН*\n\n"
        f"🪙 {alert.symbol} ({alert.exchange.upper()}) LONG\n"
        f"💰 Entry: `{alert.position_entry}`\n"
        f"🛑 SL: `{alert.position_sl}` (зона проторговки)\n"
        f"📐 Trail: adaptive {TRAIL_PHASES[0][1]}%→{TRAIL_PHASES[-1][1]}%\n"
        f"📈 Памп: +{alert.pump_pct:.0f}%"
    )

# Attach send_entry to TelegramBot
TelegramBot.send_entry = send_entry


if __name__ == "__main__":
    run_scanner()
