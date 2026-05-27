"""
Paper Trading Runner for Impulse Scalper Strategy.

Connects to Bybit via REST API, fetches real-time 5m candles,
detects impulse patterns using the same z-score logic as the backtester,
and simulates trades with full logging.

Usage:
    python -m crypto_scalp.paper_trader --symbol XRPUSDT --deposit 1000
"""
import json
import time
import logging
import signal
import sys
import os
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
import os

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("PaperTrader")

from src.crypto_scalp.strategy_engine import prepare_features, detect_live_signal


# ─── Telegram Notifications ──────────────────────────────────
class TelegramNotifier:
    """Sends trade notifications via Telegram Bot API (no extra deps)."""

    def __init__(self, token: str = "", chat_id: str = ""):
        self.token = token or os.getenv("TELEGRAM_SCALPER_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.token and self.chat_id)
        if self.enabled:
            logger.info(f"📱 Telegram notifications ON (chat: {self.chat_id[:6]}...)")
        else:
            logger.info("📱 Telegram notifications OFF (no token/chat_id)")

    def send(self, text: str):
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            requests.post(
                url,
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")

# ─── Default Strategy Params (best config from optimization) ───
DEFAULT_PARAMS = {
    "lookback_bars": 80,
    "min_dollar_volume_z": 3.0,
    "min_price_return_z": 2.0,
    "min_sequence_bars": 2,
    "max_sequence_bars": 8,
    "entry_after_bars": 1,
    "fixed_stop_loss_pct": 0.35,
    "take_profit_rr": 3.0,
    "max_hold_bars": 20,
    "cancel_if_no_follow_bars": 3,
    "cancel_min_follow_pct": 0.12,
    "account_risk_pct": 0.10,
    "paper_win_rate_threshold": 0.60,
    "use_dynamic_stop": True,
    "breakeven_at_rr": 0.3,
    "partial_tp_at_be": True,
    "entry_pullback_pct": 0.5,
    "trend_ema_period": 50,
}

# Bybit Linear Futures: Taker 0.055% per side → 0.11% round trip
COMMISSION_PCT = 0.11


@dataclass
class PaperPosition:
    symbol: str
    direction: str  # "long" or "short"
    entry_price: float
    entry_time: str
    stop_price: float
    tp_price: float
    stop_pct: float
    tp_pct: float
    size_usdt: float
    breakeven_activated: bool = False
    partial_taken: bool = False
    realized_pnl_pct: float = 0.0
    exit_price: float = 0.0
    exit_time: str = ""
    exit_reason: str = ""
    bars_held: int = 0


@dataclass
class PaperTraderState:
    symbols: List[str]
    deposit: float
    active_positions: Dict[str, PaperPosition] = field(default_factory=dict)
    completed_trades: List[Dict] = field(default_factory=list)
    total_pnl_pct: float = 0.0
    wins: int = 0
    losses: int = 0
    signals_seen: int = 0
    max_positions: int = 5


# ─── Bybit Data Feed ──────────────────────────────────────────
def fetch_bybit_klines(symbol: str, interval: str = "5", limit: int = 200) -> pd.DataFrame:
    """Fetch klines from Bybit v5 API (linear/perpetual)."""
    url = "https://api.bybit.com/v5/market/kline"
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("retCode") != 0:
            return pd.DataFrame()

        rows = data["result"]["list"]
        rows.reverse()

        df = pd.DataFrame(rows, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "turnover"
        ])
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna().reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def discover_hot_symbols(limit: int = 20) -> List[str]:
    """
    Scans the market for symbols with the highest Volume Z-Score.
    1. Get all USDT linear tickers.
    2. Filter by minimum turnover ($5M+) to ensure liquidity.
    3. For top 50 candidates, fetch 1h candles to calc volume z-score.
    4. Return top N by Z-score.
    """
    logger.info("🔍 Scanning market for high Volume Z-Score symbols...")
    url = "https://api.bybit.com/v5/market/tickers"
    params = {"category": "linear"}
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("retCode") != 0: return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
        
        tickers = data["result"]["list"]
        # Filter for USDT pairs with > $5M turnover to avoid illiquid coins
        candidates = [
            t for t in tickers 
            if t['symbol'].endswith('USDT') and float(t.get('turnover24h', 0)) > 5_000_000
        ]
        
        # Sort by turnover and take top 50 to investigate Z-score
        candidates = sorted(candidates, key=lambda x: float(x.get('turnover24h', 0)), reverse=True)[:50]
        
        hot_scores = []
        def get_z_score(sym):
            df = fetch_bybit_klines(sym, interval="60", limit=24) # Last 24 hours
            if len(df) < 10: return None
            # Z-score of the last hour's volume vs last 24h
            mean_vol = df["volume"].mean()
            std_vol = df["volume"].std()
            if std_vol == 0: return 0
            z = (df["volume"].iloc[-1] - mean_vol) / std_vol
            return (sym, z)

        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(get_z_score, [c['symbol'] for c in candidates]))
            hot_scores = [r for r in results if r is not None]
            
        # Sort by Z-Score descending
        hot_scores = sorted(hot_scores, key=lambda x: x[1], reverse=True)
        top_symbols = [s[0] for s in hot_scores[:limit]]
        
        logger.info(f"🔥 Found hot symbols by Z-Score: {', '.join(top_symbols[:10])}...")
        return top_symbols
    except Exception as e:
        logger.error(f"Symbol discovery failed: {e}")
    return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]



# ─── Position Management ──────────────────────────────────────
def check_exit(pos: PaperPosition, high: float, low: float, close: float, params: dict) -> Optional[str]:
    """Simplified exit check (Matches simulate_impulse_trade logic)."""
    # Bars held
    pos.bars_held += 1
    
    # Calculate current moves
    if pos.direction == "long":
        favorable = (high / pos.entry_price - 1.0) * 100
        adverse = (1.0 - low / pos.entry_price) * 100
        current_pnl = (close / pos.entry_price - 1.0) * 100
    else:
        favorable = (1.0 - low / pos.entry_price) * 100
        adverse = (high / pos.entry_price - 1.0) * 100
        current_pnl = (1.0 - close / pos.entry_price) * 100

    # Breakeven Activation
    be_rr = float(params.get("breakeven_at_rr", 0.3))
    be_pct = pos.stop_pct * be_rr
    if favorable >= be_pct and not pos.breakeven_activated:
        pos.breakeven_activated = True
        # Partial TP at BE?
        if params.get("partial_tp_at_be", True) and not pos.partial_taken:
            pos.partial_taken = True
            pos.realized_pnl_pct += be_pct * 0.5 # Conceptual 50% TP

    # Current effective stop
    effective_stop = pos.stop_pct if not pos.breakeven_activated else -0.05
    
    # 1. TP Hit
    if favorable >= pos.tp_pct:
        # If partial taken, we only get profit on remaining 50%
        pnl = pos.tp_pct * (0.5 if pos.partial_taken else 1.0)
        pos.realized_pnl_pct += pnl
        pos.realized_pnl_pct -= COMMISSION_PCT  # round-trip commission
        return "take_profit"
        
    # 2. SL Hit
    if adverse >= effective_stop:
        # If partial taken, we lose on remaining 50%
        pnl = -effective_stop * (0.5 if pos.partial_taken else 1.0)
        pos.realized_pnl_pct += pnl
        pos.realized_pnl_pct -= COMMISSION_PCT  # round-trip commission
        return "breakeven" if pos.breakeven_activated else "fixed_stop"
        
    # 3. Max Bars
    if pos.bars_held >= int(params["max_hold_bars"]):
        pnl = current_pnl * (0.5 if pos.partial_taken else 1.0)
        pos.realized_pnl_pct += pnl
        pos.realized_pnl_pct -= COMMISSION_PCT  # round-trip commission
        return "time_exit"

    return None


# ─── Logging & Persistence ────────────────────────────────────
def save_state(state: PaperTraderState, path: Path):
    active_pos_dict = {s: asdict(p) for s, p in state.active_positions.items()}
    data = {
        "symbols": state.symbols,
        "deposit": state.deposit,
        "total_pnl_pct": state.total_pnl_pct,
        "wins": state.wins,
        "losses": state.losses,
        "signals_seen": state.signals_seen,
        "win_rate": state.wins / max(1, state.wins + state.losses) * 100,
        "active_positions": active_pos_dict,
        "completed_trades": state.completed_trades[-100:],
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def format_trade_message(pos: PaperPosition, state: PaperTraderState) -> str:
    """Format trade result for logging and Telegram."""
    dir_icon = "🟢 LONG" if pos.direction == "long" else "🔴 SHORT"
    pnl_icon = "✅" if pos.realized_pnl_pct > 0 else "❌"
    wr = state.wins / max(1, state.wins + state.losses) * 100
    return (
        f"{pnl_icon} **Paper Trade #{state.wins + state.losses}**\n"
        f"{dir_icon} {state.symbol}\n"
        f"Entry: {pos.entry_price} → Exit: {pos.exit_price}\n"
        f"PnL: {pos.realized_pnl_pct:+.3f}% | Reason: {pos.exit_reason}\n"
        f"Bars held: {pos.bars_held}\n"
        f"─────────────\n"
        f"Session: W{state.wins}/L{state.losses} | WR: {wr:.0f}% | Total PnL: {state.total_pnl_pct:+.3f}%"
    )


def format_entry_message(signal: dict, state: PaperTraderState) -> str:
    dir_icon = "🟢 LONG" if signal["direction"] == "long" else "🔴 SHORT"
    return (
        f"📡 **New Signal Detected!**\n"
        f"{dir_icon} {state.symbol}\n"
        f"Entry: {signal['entry_price']} | SL: {signal['stop_price']} | TP: {signal['tp_price']}\n"
        f"Risk: {signal['stop_pct']:.2f}% | Reward: {signal['tp_pct']:.2f}%\n"
        f"Vol Z: {signal['max_volume_z']:.1f} | Ret Z: {signal['max_ret_z']:.1f}\n"
        f"Sequence: {signal['sequence_bars']} bars"
    )


# ─── Main Paper Trading Loop ─────────────────────────────────
def run_paper_trader(
    symbols: List[str],
    deposit: float = 1000.0,
    params: Optional[dict] = None,
    interval_seconds: int = 60,
    state_dir: Optional[Path] = None,
    tg_token: str = "",
    tg_chat_id: str = "",
    max_positions: int = 5,
):
    """Main paper trading loop."""
    params = params or dict(DEFAULT_PARAMS)
    state = PaperTraderState(symbols=symbols, deposit=deposit, max_positions=max_positions)
    tg = TelegramNotifier(tg_token, tg_chat_id)

    if state_dir is None:
        state_dir = Path.cwd() / ".local_ai" / "paper_trading"
    state_path = state_dir / "paper_state_multi.json"
    opt_params_path = state_dir / "optimized_params.json"

    # Load previous state if exists
    # Dynamic Reloading Logic
    def load_current_params(base: dict, path: Path) -> dict:
        if path.exists():
            try:
                new_p = json.loads(path.read_text())
                logger.info(f"🔄 Loaded optimized params from {path.name}")
                return {**base, **new_p}
            except: pass
        return base

    params = load_current_params(params, opt_params_path)
    last_params_check = time.time()
    last_discovery_check = 0 # Force discovery on start if top mode active
    
    # We'll use a local copy of symbols that can be updated
    active_symbols = list(symbols)
    is_dynamic = os.getenv("DYNAMIC_SYMBOLS", "0") == "1"

    if state_path.exists():
        try:
            prev = json.loads(state_path.read_text(encoding="utf-8"))
            state.completed_trades = prev.get("completed_trades", [])
            state.wins = prev.get("wins", 0)
            state.losses = prev.get("losses", 0)
            state.total_pnl_pct = prev.get("total_pnl_pct", 0.0)
            state.signals_seen = prev.get("signals_seen", 0)
            logger.info(f"Resumed session: {state.wins}W/{state.losses}L, PnL: {state.total_pnl_pct:+.3f}%")
        except Exception:
            pass

    running = True

    def signal_handler(sig, frame):
        nonlocal running
        logger.info("Shutting down paper trader...")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    startup_msg = (
        f"🚀 *PAPER TRADER STARTED*\n"
        f"Symbols: {len(symbols)} assets\n"
        f"Deposit: ${deposit}\n"
        f"Max Pos: {max_positions}\n"
        f"Interval: {interval_seconds}s\n"
        f"Stop: {params['fixed_stop_loss_pct']}% | TP RR: {params['take_profit_rr']}x\n"
        f"Filters: DynStop={params.get('use_dynamic_stop')}, "
        f"BE@{params.get('breakeven_at_rr')}R, "
        f"PartialTP={params.get('partial_tp_at_be')}, "
        f"Pull={params.get('entry_pullback_pct')}, "
        f"EMA={params.get('trend_ema_period')}"
    )
    logger.info("=" * 60)
    logger.info(startup_msg)
    logger.info("=" * 60)
    tg.send(startup_msg)

    last_signal_ts = ""

    while running:
        loop_start = time.time()
        
        # 1. Periodic Discovery (every 1 hour)
        if is_dynamic and time.time() - last_discovery_check > 3600:
            new_symbols = discover_hot_symbols(limit=len(active_symbols))
            if new_symbols:
                active_symbols = new_symbols
                state.symbols = active_symbols # Update state for monitor
            last_discovery_check = time.time()

        # 2. Periodic params reload (every 1 hour)
        if time.time() - last_params_check > 3600:
            params = load_current_params(params, opt_params_path)
            last_params_check = time.time()
            
        try:
            for symbol in active_symbols:
                # 1. Fetch latest 5m candles
                df = fetch_bybit_klines(symbol, interval="5", limit=200)
                if df.empty:
                    continue

                current_price = float(df["close"].iloc[-1])
                current_ts = str(df["timestamp"].iloc[-1])

                # 2. Add features
                frame = prepare_features(df, params)

                # 3. Manage active position for this symbol
                if symbol in state.active_positions:
                    pos = state.active_positions[symbol]
                    last_bar = frame.iloc[-1]
                    exit_reason = check_exit(
                        pos,
                        float(last_bar["high"]),
                        float(last_bar["low"]),
                        float(last_bar["close"]),
                        params,
                    )
                    if exit_reason:
                        pos.exit_price = current_price
                        pos.exit_time = current_ts
                        pos.exit_reason = exit_reason

                        state.total_pnl_pct += pos.realized_pnl_pct
                        if pos.realized_pnl_pct > 0:
                            state.wins += 1
                        else:
                            state.losses += 1

                        msg = format_trade_message(pos, state)
                        logger.info(f"\n{msg}")
                        tg.send(msg)
                        state.completed_trades.append(asdict(pos))
                        del state.active_positions[symbol]
                        save_state(state, state_path)

                # 4. Detect new signal (only if no active position for this symbol AND under global limit)
                else:
                    if len(state.active_positions) < state.max_positions:
                        signal_data = detect_live_signal(frame, params)
                        if signal_data:
                            # Avoid double signals on same timestamp
                            if current_ts != last_signal_ts:
                                last_signal_ts = current_ts
                                state.signals_seen += 1

                                risk_pct = float(params.get("account_risk_pct", 0.10))
                                size_usdt = deposit * risk_pct / (signal_data["stop_pct"] / 100)
                                size_usdt = min(size_usdt, deposit * 0.5)

                                pos = PaperPosition(
                                    symbol=symbol,
                                    direction=signal_data["direction"],
                                    entry_price=signal_data["entry_price"],
                                    entry_time=current_ts,
                                    stop_price=signal_data["stop_price"],
                                    tp_price=signal_data["tp_price"],
                                    stop_pct=signal_data["stop_pct"],
                                    tp_pct=signal_data["tp_pct"],
                                    size_usdt=round(size_usdt, 2),
                                )
                                state.active_positions[symbol] = pos

                                msg = format_entry_message(signal_data, state)
                                msg = f"🌐 *{symbol}*\n" + msg
                                logger.info(f"\n{msg}")
                                tg.send(msg)
                                save_state(state, state_path)

            # 5. Periodic status
            wr = state.wins / max(1, state.wins + state.losses) * 100
            active_list = ", ".join(state.active_positions.keys())
            logger.info(
                f"[{datetime.now().strftime('%H:%M:%S')}] Active: {len(state.active_positions)}/{state.max_positions} [{active_list}] | "
                f"Trades: {state.wins + state.losses} | WR: {wr:.0f}% | PnL: {state.total_pnl_pct:+.3f}%"
            )

        except Exception as e:
            logger.error(f"Error in paper trading loop: {e}", exc_info=True)

        elapsed = time.time() - loop_start
        wait_time = max(1, interval_seconds - elapsed)
        time.sleep(wait_time)

    # Final save
    save_state(state, state_path)
    logger.info("Paper trader stopped.")


# ─── CLI Entry Point ──────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Impulse Scalper Paper Trader")
    parser.add_argument("--symbols", default="XRPUSDT,SOLUSDT,BTCUSDT,ETHUSDT,DOGEUSDT", help="Trading pairs (comma separated)")
    parser.add_argument("--top", type=int, default=0, help="Fetch top N symbols by volume (overrides --symbols)")
    parser.add_argument("--deposit", type=float, default=1000.0, help="Paper deposit in USDT")
    parser.add_argument("--interval", type=int, default=60, help="Poll interval in seconds")
    parser.add_argument("--max-pos", type=int, default=5, help="Max concurrent positions")
    parser.add_argument("--tg-token", default="", help="Telegram bot token (or set TELEGRAM_SCALPER_BOT_TOKEN env)")
    parser.add_argument("--tg-chat", default="", help="Telegram chat ID (or set TELEGRAM_CHAT_ID env)")
    args = parser.parse_args()

    if args.top > 0:
        symbols_list = discover_hot_symbols(args.top)
        logger.info(f"📊 Auto-discovered Top {args.top} symbols: {', '.join(symbols_list)}")
    else:
        symbols_list = [s.strip() for s in args.symbols.split(",")]

    run_paper_trader(
        symbols=symbols_list,
        deposit=args.deposit,
        interval_seconds=args.interval,
        tg_token=args.tg_token,
        tg_chat_id=args.tg_chat,
        max_positions=args.max_pos,
    )
