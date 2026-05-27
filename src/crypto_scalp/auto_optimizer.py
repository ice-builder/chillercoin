"""
Impulse Scalper — Auto Optimizer (Regime Analysis).
Fetches recent market data, runs backtests with various parameters,
and identifies the best configuration for the current market regime.
"""
import pandas as pd
import numpy as np
import requests
import json
import time
import random
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor

from src.crypto_scalp.strategy_engine import prepare_features, simulate_backtest

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("auto_optimizer")

# --- Configuration ---
OPT_DIR = Path.cwd() / ".local_ai" / "optimizer"
BEST_PARAMS_PATH = Path.cwd() / ".local_ai" / "paper_trading" / "optimized_params.json"
LOOKBACK_DAYS = 7  # Optimize on last 7 days of data
TOP_N = 20        # Number of symbols to include in optimization

PARAM_SPACE = {
    "min_dollar_volume_z": [2.5, 3.0, 3.5, 4.0],
    "min_price_return_z": [2.5, 3.0, 3.5, 4.0],
    "fixed_stop_loss_pct": [0.25, 0.35, 0.45, 0.55],
    "take_profit_rr": [2.5, 3.0, 3.5, 4.0],
    "entry_pullback_pct": [0.3, 0.5, 0.7],
    "breakeven_at_rr": [0.3, 0.5],
}

def fetch_bybit_history(symbol: str, days: int = 7) -> pd.DataFrame:
    """Fetch 5m klines for the last X days from Bybit."""
    all_rows = []
    interval = "5"
    limit = 1000 # Max 1000 per request
    
    # Calculate timestamps
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    
    cursor = start_ts
    while cursor < now:
        url = "https://api.bybit.com/v5/market/kline"
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "start": cursor,
            "limit": limit
        }
        try:
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            if data.get("retCode") != 0: break
            
            rows = data["result"]["list"]
            if not rows: break
            
            all_rows.extend(rows)
            # Bybit returns newest first, so the "earliest" in the list is the last row
            # But we are going forward from start_ts, so the last row in 'list' is actually the earliest?
            # Actually Bybit returns [Newest, ..., Oldest]
            # If we want to move forward, we need to adjust cursor based on the newest one.
            # But it's easier to just fetch and break.
            
            # Update cursor to the timestamp of the newest row in the current batch + 1ms
            newest_ts = int(rows[0][0])
            cursor = newest_ts + 1
            if len(rows) < limit: break
            time.sleep(0.1) # Rate limit
        except Exception as e:
            logger.error(f"Error fetching {symbol}: {e}")
            break
            
    if not all_rows: return pd.DataFrame()
    
    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume", "turnover"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    df["symbol"] = symbol
    return df

def fetch_top_symbols(limit: int = 20) -> List[str]:
    url = "https://api.bybit.com/v5/market/tickers"
    params = {"category": "linear"}
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("retCode") == 0:
            tickers = data["result"]["list"]
            usdt_tickers = [t for t in tickers if t['symbol'].endswith('USDT')]
            sorted_tickers = sorted(usdt_tickers, key=lambda x: float(x.get('turnover24h', 0)), reverse=True)
            return [t['symbol'] for t in sorted_tickers[:limit]]
    except Exception: pass
    return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]

def run_optimization():
    logger.info("🚀 Starting Auto-Optimization (Regime Analysis)")
    
    # 1. Fetch Data
    symbols = fetch_top_symbols(TOP_N)
    logger.info(f"Fetching historical data for {len(symbols)} symbols...")
    
    all_data = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_bybit_history, s, LOOKBACK_DAYS): s for s in symbols}
        for future in futures:
            symbol = futures[future]
            df = future.result()
            if not df.empty:
                all_data[symbol] = df
                logger.info(f"✅ {symbol}: {len(df)} bars")

    if not all_data:
        logger.error("No data fetched. Aborting.")
        return

    # 2. Random Search over Parameter Space
    logger.info("Running parameter search...")
    results = []
    
    # Fixed base params
    base_params = {
        "lookback_bars": 100,
        "trend_ema_period": 50,
        "max_hold_bars": 50,
        "partial_tp_at_be": True,
        "use_dynamic_stop": True
    }

    # Generate 50 random combinations
    trials = 50
    for i in range(trials):
        trial_params = base_params.copy()
        for k, v in PARAM_SPACE.items():
            trial_params[k] = random.choice(v)
            
        # Run backtest on all symbols
        all_trades = []
        for symbol, df in all_data.items():
            frame = prepare_features(df, trial_params)
            trades = simulate_backtest(frame, trial_params)
            all_trades.extend(trades)
            
        if not all_trades:
            continue
            
        # Score the trial
        wins = sum(1 for t in all_trades if t["win"])
        total = len(all_trades)
        wr = wins / total * 100
        pnl = sum(t["pnl"] for t in all_trades)
        avg_pnl = pnl / total
        
        results.append({
            "params": trial_params,
            "wr": wr,
            "pnl": pnl,
            "avg_pnl": avg_pnl,
            "trades": total
        })
        
        if (i+1) % 10 == 0:
            logger.info(f"Trial {i+1}/{trials} completed...")

    # 3. Find Best
    if not results:
        logger.error("No results found.")
        return

    # Filter for minimum number of trades to avoid luck
    min_trades = len(all_data) * 2
    valid_results = [r for r in results if r["trades"] >= min_trades]
    if not valid_results: valid_results = results

    # Best by Total PnL
    best = max(valid_results, key=lambda x: x["pnl"])
    
    logger.info("====================================================")
    logger.info(f"🏆 BEST PARAMETERS FOUND (Trades: {best['trades']})")
    logger.info(f"Win Rate: {best['wr']:.1f}% | Total PnL: {best['pnl']:.3f}%")
    logger.info(f"Params: {json.dumps(best['params'], indent=2)}")
    logger.info("====================================================")

    # 4. Save
    BEST_PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    BEST_PARAMS_PATH.write_text(json.dumps(best['params'], indent=2))
    logger.info(f"✅ Saved to {BEST_PARAMS_PATH}")

if __name__ == "__main__":
    run_optimization()
