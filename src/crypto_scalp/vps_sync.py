import json
import os
import subprocess
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

import pandas as pd
import requests

logger = logging.getLogger("vps_sync")

class VPSSyncManager:
    def __init__(self, 
                 host: str = "185.207.67.130", 
                 port: int = 48113, 
                 user: str = "trader", 
                 key_path: str = "~/.ssh/id_ed25519",
                 remote_path: str = "/home/trader/impulse-scalper/.local_ai/paper_trading/paper_state_multi.json",
                 local_cache_dir: str = "data/vps_sync"):
        self.host = host
        self.port = port
        self.user = user
        self.key_path = os.path.expanduser(key_path)
        self.remote_path = remote_path
        self.local_cache_dir = Path(local_cache_dir)
        self.local_cache_dir.mkdir(parents=True, exist_ok=True)
        self.local_json = self.local_cache_dir / "paper_state_multi.json"

    def sync_from_vps(self) -> bool:
        """Downloads the latest paper state from the VPS using SCP."""
        logger.info(f"🔄 Syncing data from VPS {self.host}...")
        cmd = [
            "scp",
            "-i", self.key_path,
            "-P", str(self.port),
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=no",
            f"{self.user}@{self.host}:{self.remote_path}",
            str(self.local_json)
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                logger.info("✅ Sync successful.")
                return True
            else:
                logger.error(f"❌ Sync failed: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"❌ SCP error: {e}")
            return False

    def load_state(self) -> Dict:
        if not self.local_json.exists():
            return {}
        try:
            return json.loads(self.local_json.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Error reading local state: {e}")
            return {}

    def fetch_historical_follow_up(self, symbol: str, start_time_iso: str, duration_hours: int = 2) -> pd.DataFrame:
        """Fetches klines from Bybit for the period AFTER a trade exit."""
        start_dt = datetime.fromisoformat(start_time_iso.replace("Z", "+00:00"))
        end_dt = start_dt + timedelta(hours=duration_hours)
        
        url = "https://api.bybit.com/v5/market/kline"
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": "1", # 1m for precise analysis
            "start": int(start_dt.timestamp() * 1000),
            "end": int(end_dt.timestamp() * 1000),
            "limit": 200
        }
        try:
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            if data.get("retCode") != 0: return pd.DataFrame()
            rows = data["result"]["list"]
            rows.reverse()
            df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            return df
        except Exception:
            return pd.DataFrame()

    def perform_regret_analysis(self, trades: List[Dict]) -> List[Dict]:
        """Analyzes trades to see if we left money on the table."""
        analysis_results = []
        logger.info(f"🧐 Analyzing {len(trades)} completed trades for regret...")
        
        for trade in trades:
            symbol = trade.get("symbol")
            exit_time = trade.get("exit_time")
            exit_price = trade.get("exit_price")
            direction = trade.get("direction")
            realized_pnl = trade.get("realized_pnl_pct", 0)
            
            # Robustness check: if PnL is positive but prices suggest otherwise, flip direction
            if realized_pnl > 0:
                if exit_price > trade.get("entry_price", 0) and direction == "short":
                    direction = "long" # Correction
                elif exit_price < trade.get("entry_price", 0) and direction == "long":
                    direction = "short" # Correction
            
            if not all([symbol, exit_time, exit_price, direction]):
                continue
                
            follow_df = self.fetch_historical_follow_up(symbol, exit_time)
            if follow_df.empty:
                continue
                
            # Calculate max favorable move after exit
            if direction == "long":
                max_price = follow_df["high"].max()
                potential_pnl = (max_price / trade["entry_price"] - 1.0) * 100
            else:
                min_price = follow_df["low"].min()
                potential_pnl = (1.0 - min_price / trade["entry_price"]) * 100
                
            regret = max(0, potential_pnl - realized_pnl)
            
            analysis_results.append({
                "symbol": symbol,
                "exit_time": exit_time,
                "entry_time": trade.get("entry_time", exit_time),
                "entry_price": trade.get("entry_price", 0),
                "exit_price": exit_price,
                "direction": direction,
                "realized_pnl": realized_pnl,
                "potential_pnl": potential_pnl,
                "regret": regret,
                "exit_reason": trade.get("exit_reason")
            })
            
        return analysis_results

    def generate_recommendations(self, analysis: List[Dict]) -> Dict:
        """Suggests parameter changes based on analysis."""
        if not analysis:
            return {"status": "No data", "recommendations": []}
            
        avg_regret = sum(a["regret"] for a in analysis) / len(analysis)
        tp_exits = [a for a in analysis if a["exit_reason"] == "take_profit"]
        
        recs = []
        if len(tp_exits) > 0:
            avg_tp_regret = sum(a["regret"] for a in tp_exits) / len(tp_exits)
            if avg_tp_regret > 0.5:
                recs.append({
                    "parameter": "take_profit_rr",
                    "action": "increase",
                    "suggested_value": 3.5, # Example: increase from 3.0 to 3.5
                    "reason": f"Average regret on Take Profit is {avg_tp_regret:.2f}%. You are leaving significant money on the table."
                })
        
        # Check for early exits due to fixed stop vs move
        stop_exits = [a for a in analysis if a["exit_reason"] == "fixed_stop"]
        if len(stop_exits) > 5:
            recs.append({
                "parameter": "fixed_stop_loss_pct",
                "action": "review",
                "suggested_value": 0.45,
                "reason": "Many trades hit fixed stop. Consider if stops are too tight for current volatility."
            })

        # NEW: Check for Breakeven "frights"
        be_exits = [a for a in analysis if a["exit_reason"] == "breakeven"]
        if be_exits:
            avg_be_regret = sum(a["regret"] for a in be_exits) / len(be_exits)
            if avg_be_regret > 1.0: # If we lose more than 1% potential on average after BE
                recs.append({
                    "parameter": "breakeven_activation_rr",
                    "action": "increase",
                    "suggested_value": 1.5, # Suggest moving it higher
                    "reason": f"Trades are hitting Breakeven too early and then moving significantly further (Avg regret: {avg_be_regret:.2f}%). Increase activation distance."
                })
            
        return {
            "avg_regret": avg_regret,
            "total_trades_analyzed": len(analysis),
            "recommendations": recs
        }

    def fetch_remote_params(self) -> Dict:
        """Downloads and reads optimized_params.json from the VPS."""
        remote_json = os.path.join(os.path.dirname(self.remote_path), "optimized_params.json")
        logger.info(f"📥 Fetching current parameters from VPS...")
        
        ssh_cmd = [
            "ssh",
            "-i", self.key_path,
            "-p", str(self.port),
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=no",
            f"{self.user}@{self.host}",
            f"cat {remote_json}"
        ]
        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True)
            if result.returncode == 0:
                return json.loads(result.stdout)
            else:
                logger.error(f"❌ Failed to fetch params: {result.stderr}")
                return {}
        except Exception as e:
            logger.error(f"❌ SSH error during fetch: {e}")
            return {}

    def apply_remote_parameter(self, parameter: str, value: float) -> bool:
        """Updates a parameter on the VPS by modifying optimized_params.json."""
        remote_json = os.path.join(os.path.dirname(self.remote_path), "optimized_params.json")
        logger.info(f"📤 Updating {parameter} to {value} on VPS...")
        
        # Python command to update the JSON file on the VPS
        py_cmd = f"""
import json, os
p_path = '{remote_json}'
params = {{}}
if os.path.exists(p_path):
    with open(p_path, 'r') as f: params = json.load(f)
params['{parameter}'] = {value}
with open(p_path, 'w') as f: json.dump(params, f, indent=2)
print('Updated')
"""
        ssh_cmd = [
            "ssh",
            "-i", self.key_path,
            "-p", str(self.port),
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=no",
            f"{self.user}@{self.host}",
            f"python3 -c \"{py_cmd}\""
        ]
        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True)
            if "Updated" in result.stdout:
                logger.info(f"✅ Parameter {parameter} updated on VPS.")
                return True
            else:
                logger.error(f"❌ Update failed: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"❌ SSH error: {e}")
            return False

    def sync_strategy_pack_to_vps(self, local_pack_path: str = ".local_ai/strategy_packs/strategy_pack.json") -> bool:
        """Uploads strategy_pack.json to VPS for the Paper Trader to pick up."""
        local_path = Path(local_pack_path)
        if not local_path.exists():
            logger.error(f"❌ Strategy pack not found: {local_path}")
            return False

        remote_dir = os.path.dirname(self.remote_path)
        remote_pack = f"{remote_dir}/strategy_pack.json"

        logger.info(f"📦 Uploading strategy pack to VPS: {remote_pack}")
        cmd = [
            "scp",
            "-i", self.key_path,
            "-P", str(self.port),
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=no",
            str(local_path),
            f"{self.user}@{self.host}:{remote_pack}",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                logger.info("✅ Strategy pack uploaded to VPS.")
                return True
            else:
                logger.error(f"❌ Upload failed: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"❌ SCP error: {e}")
            return False

if __name__ == "__main__":
    # Test sync
    sync = VPSSyncManager()
    if sync.sync_from_vps():
        state = sync.load_state()
        trades = state.get("completed_trades", [])
        if trades:
            results = sync.perform_regret_analysis(trades[-10:])
            recs = sync.generate_recommendations(results)
            print(json.dumps(recs, indent=2))
