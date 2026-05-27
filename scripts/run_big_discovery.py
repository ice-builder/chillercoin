#!/usr/bin/env python3
"""
run_big_discovery.py — Запуск auto_discover на Data Lake с 3-летней историей
и автоматическая валидация + промоция стратегий.

Использование:
    python scripts/run_big_discovery.py --coins BTC ETH SOL --intervals 5 15
    python scripts/run_big_discovery.py --all                # все 20 монет × 2 TF
    python scripts/run_big_discovery.py --all --validate     # + валидация + промоция
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Добавляем src/ в PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
import pandas as pd

from crypto_scalp.data_lake import SUPPORTED_COINS
from crypto_scalp.data_manager import HistoryManager
from crypto_scalp.hypothesis_vault import (
    DEFAULT_DB_PATH,
    HypothesisRecord,
    add_hypothesis,
    get_hypothesis,
    list_hypotheses_full,
    update_hypothesis,
)
from crypto_scalp.impulse_detector import ImpulseDetectorConfig, detect_impulses
from crypto_scalp.quant_brick import QuantBrick, build_features_df
from crypto_scalp.strategy_pack import (
    auto_promote_hypothesis,
    export_strategy_pack,
    MIN_TRADES,
    MIN_WIN_RATE,
)
from crypto_scalp.confluence_strategy import (
    optimize_confluence,
    validate_confluence,
    CONFLUENCE_BASE,
    CONFLUENCE_GRID,
)


# ─── Комиссии ─────────────────────────────────────────────────
# Bybit Linear Futures: Taker 0.055% per side → 0.11% round trip
COMMISSION_PCT = 0.11  # round-trip commission in %

# Параметры валидации (базовые)
DISCOVERY_PARAMS = {
    "lookback_bars": 80,
    "min_dollar_volume_z": 3.0,
    "min_price_return_z": 2.0,
    "min_sequence_bars": 2,
    "max_sequence_bars": 8,
    "entry_after_bars": 1,
    "max_hold_bars": 30,
    "fixed_stop_loss_pct": 0.50,
    "take_profit_rr": 1.0,
    "cancel_if_no_follow_bars": 5,
    "cancel_min_follow_pct": 0.08,
    "paper_win_rate_threshold": 0.70,
    "account_risk_pct": 0.10,
    "trend_ema_period": 50,
    "strategy_mode": "reversal",
}

# Сетка для поиска стратегий с WR≥70% и прибыльностью ПОСЛЕ комиссий
# Reversal-only (mean-reversion после сильных импульсов)
# 1 × 3 × 3 × 4 × 5 = 180 комбинаций (быстро)
PARAM_GRID = {
    "strategy_mode": ["reversal"],
    "min_dollar_volume_z": [3.0, 3.5, 4.0],
    "min_price_return_z": [2.0, 2.5, 3.0],
    "fixed_stop_loss_pct": [0.3, 0.5, 0.75, 1.0],
    "take_profit_rr": [0.5, 0.8, 1.0, 1.5, 2.0],
}


def rolling_zscore(series: pd.Series, lookback: int) -> pd.Series:
    baseline = series.shift(1)
    mean = baseline.rolling(lookback, min_periods=max(10, lookback // 4)).mean()
    std = baseline.rolling(lookback, min_periods=max(10, lookback // 4)).std().replace(0, np.nan)
    return ((series - mean) / std).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def find_impulse_events(frame: pd.DataFrame, params: dict) -> list:
    """Находит все импульсные события в DataFrame."""
    min_volume_z = float(params["min_dollar_volume_z"])
    min_ret_z = float(params["min_price_return_z"])
    min_seq = int(params["min_sequence_bars"])
    max_seq = int(params["max_sequence_bars"])

    triggered = frame[
        (frame["dollar_volume_z"] >= min_volume_z)
        & (frame["abs_ret_z"] >= min_ret_z)
        & (frame["direction"] != 0)
    ]

    if triggered.empty:
        return []

    events = []
    group_start = None
    group_indices = []

    for idx in triggered.index:
        if group_start is None:
            group_start = idx
            group_indices = [idx]
        elif idx - group_indices[-1] <= 2:  # допуск на gap
            group_indices.append(idx)
        else:
            if len(group_indices) >= min_seq:
                events.append({
                    "start_idx": group_indices[0],
                    "end_idx": group_indices[-1],
                    "indices": group_indices[:max_seq],
                })
            group_start = idx
            group_indices = [idx]

    if group_indices and len(group_indices) >= min_seq:
        events.append({
            "start_idx": group_indices[0],
            "end_idx": group_indices[-1],
            "indices": group_indices[:max_seq],
        })

    return events


def simulate_trade(frame: pd.DataFrame, event: dict, params: dict) -> dict:
    """Симулирует сделку по импульсному событию."""
    entry_after = int(params.get("entry_after_bars", 1))
    max_hold = int(params.get("max_hold_bars", 30))
    stop_pct = float(params["fixed_stop_loss_pct"])
    tp_rr = float(params["take_profit_rr"])
    tp_pct = stop_pct * tp_rr
    cancel_bars = int(params.get("cancel_if_no_follow_bars", 5))
    cancel_min = float(params.get("cancel_min_follow_pct", 0.08))
    mode = params.get("strategy_mode", "continuation")

    trigger_idx = event["end_idx"]
    entry_idx = trigger_idx + entry_after

    if entry_idx >= len(frame):
        return {}

    # Direction from impulse
    impulse_rows = frame.iloc[event["indices"]]
    direction_sum = impulse_rows["direction"].sum()
    impulse_dir = 1 if direction_sum > 0 else -1

    # In reversal mode, trade AGAINST the impulse (mean reversion)
    if mode == "reversal":
        direction = -impulse_dir
    else:
        direction = impulse_dir

    entry_price = float(frame.iloc[entry_idx]["close"])
    if entry_price <= 0:
        return {}

    # Simulate forward
    best_favorable = 0.0
    for hold_bar in range(1, max_hold + 1):
        bar_idx = entry_idx + hold_bar
        if bar_idx >= len(frame):
            break

        bar = frame.iloc[bar_idx]
        if direction > 0:
            move = (float(bar["close"]) / entry_price - 1.0) * 100
            adverse = (1.0 - float(bar["low"]) / entry_price) * 100
        else:
            move = (1.0 - float(bar["close"]) / entry_price) * 100
            adverse = (float(bar["high"]) / entry_price - 1.0) * 100

        best_favorable = max(best_favorable, move)

        # Check stop
        if adverse >= stop_pct:
            net_pnl = -stop_pct - COMMISSION_PCT
            return {
                "entry_idx": entry_idx,
                "entry_ts": str(frame.iloc[entry_idx]["timestamp"]),
                "entry_price": entry_price,
                "exit_price": float(bar["close"]),
                "direction": "long" if direction > 0 else "short",
                "realized_move_pct": net_pnl,
                "adverse_move_pct": adverse,
                "bars_held": hold_bar,
                "exit_reason": "fixed_stop",
                "is_profitable": False,
            }

        # Check TP
        if move >= tp_pct:
            net_pnl = tp_pct - COMMISSION_PCT
            return {
                "entry_idx": entry_idx,
                "entry_ts": str(frame.iloc[entry_idx]["timestamp"]),
                "entry_price": entry_price,
                "exit_price": float(bar["close"]),
                "direction": "long" if direction > 0 else "short",
                "realized_move_pct": net_pnl,
                "adverse_move_pct": 0.0,
                "bars_held": hold_bar,
                "exit_reason": "take_profit",
                "is_profitable": net_pnl > 0,
            }

        # Check cancel
        if hold_bar >= cancel_bars and best_favorable < cancel_min:
            net_pnl = move - COMMISSION_PCT
            return {
                "entry_idx": entry_idx,
                "entry_ts": str(frame.iloc[entry_idx]["timestamp"]),
                "entry_price": entry_price,
                "exit_price": float(bar["close"]),
                "direction": "long" if direction > 0 else "short",
                "realized_move_pct": net_pnl,
                "adverse_move_pct": 0.0,
                "bars_held": hold_bar,
                "exit_reason": "cancel_no_follow",
                "is_profitable": net_pnl > 0,
            }

    # Time exit
    if entry_idx + max_hold < len(frame):
        bar = frame.iloc[entry_idx + max_hold]
        if direction > 0:
            move = (float(bar["close"]) / entry_price - 1.0) * 100
        else:
            move = (1.0 - float(bar["close"]) / entry_price) * 100
        net_pnl = move - COMMISSION_PCT
        return {
            "entry_idx": entry_idx,
            "entry_ts": str(frame.iloc[entry_idx]["timestamp"]),
            "entry_price": entry_price,
            "exit_price": float(bar["close"]),
            "direction": "long" if direction > 0 else "short",
            "realized_move_pct": net_pnl,
            "adverse_move_pct": 0.0,
            "bars_held": max_hold,
            "exit_reason": "time_exit",
            "is_profitable": net_pnl > 0,
        }

    return {}


def validate_on_data(frame: pd.DataFrame, params: dict) -> dict:
    """Полная валидация стратегии на данных."""
    lookback = int(params["lookback_bars"])
    
    # Подготовка фич
    frame = frame.copy()
    frame["dollar_volume"] = frame["close"] * frame["volume"]
    frame["ret_pct"] = frame["close"].pct_change().fillna(0.0) * 100
    frame["dollar_volume_z"] = rolling_zscore(frame["dollar_volume"], lookback)
    frame["abs_ret_z"] = rolling_zscore(frame["ret_pct"].abs(), lookback)
    frame["direction"] = np.sign(frame["ret_pct"]).astype(int)

    ema_period = int(params.get("trend_ema_period", 0))
    if ema_period > 0:
        frame["trend_ema"] = frame["close"].ewm(span=ema_period, adjust=False).mean()

    events = find_impulse_events(frame, params)
    outcomes = [simulate_trade(frame, event, params) for event in events]
    outcomes = [o for o in outcomes if o]

    wins = [o for o in outcomes if o.get("is_profitable")]
    win_rate = len(wins) / len(outcomes) if outcomes else 0.0
    avg_pnl = np.mean([o["realized_move_pct"] for o in outcomes]) if outcomes else 0.0
    total_pnl = sum(o["realized_move_pct"] for o in outcomes)

    exit_reasons = {}
    for o in outcomes:
        r = o.get("exit_reason", "?")
        exit_reasons[r] = exit_reasons.get(r, 0) + 1

    return {
        "trades": len(outcomes),
        "win_rate": win_rate,
        "avg_realized_move_pct": float(avg_pnl),
        "total_pnl_pct": float(total_pnl),
        "exit_reasons": exit_reasons,
        "params": params,
    }


def optimize_params(frame: pd.DataFrame, base_params: dict) -> dict:
    """Grid search по параметрам — тестирует continuation И reversal."""
    best_result = None
    best_params = None
    best_score = -999
    combos_tested = 0

    # Grid search: both modes × all param combos
    for mode in PARAM_GRID["strategy_mode"]:
        for z_vol in PARAM_GRID["min_dollar_volume_z"]:
            for z_ret in PARAM_GRID["min_price_return_z"]:
                for sl in PARAM_GRID["fixed_stop_loss_pct"]:
                    for rr in PARAM_GRID["take_profit_rr"]:
                        test_params = dict(base_params)
                        test_params["strategy_mode"] = mode
                        test_params["min_dollar_volume_z"] = z_vol
                        test_params["min_price_return_z"] = z_ret
                        test_params["fixed_stop_loss_pct"] = sl
                        test_params["take_profit_rr"] = rr
                        combos_tested += 1

                        result = validate_on_data(frame, test_params)

                        if result["trades"] < MIN_TRADES:
                            continue
                        if result["win_rate"] < MIN_WIN_RATE:
                            continue

                        # Score: total_pnl (actual profit) is king,
                        # with WR as tiebreaker
                        total_pnl = result["total_pnl_pct"]
                        if total_pnl <= 0:
                            continue  # не прибыльно — отбрасываем
                        score = total_pnl + result["win_rate"] * 10
                        if score > best_score:
                            best_score = score
                            best_result = result
                            best_params = dict(test_params)

    # Fallback: run base params if nothing found
    if best_result is None:
        base_result = validate_on_data(frame, base_params)
        # Also try reversal mode with base params
        rev_params = dict(base_params)
        rev_params["strategy_mode"] = "reversal"
        rev_result = validate_on_data(frame, rev_params)
        if rev_result["win_rate"] > base_result["win_rate"]:
            base_result = rev_result
            base_params = rev_params
        base_result["combos_tested"] = combos_tested
        return base_result

    best_result["best_params"] = best_params
    best_result["combos_tested"] = combos_tested
    return best_result


def run_discovery_for_coin(
    symbol: str,
    interval: str,
    hm: HistoryManager,
    validate: bool = True,
    optimize: bool = True,
) -> dict:
    """Запускает discovery + validation для одной монеты/TF."""
    
    print(f"\n{'='*60}")
    print(f"  {symbol} / {interval}")
    print(f"{'='*60}")

    t0 = time.time()
    df = hm.load(symbol, interval)

    if df.empty:
        print(f"  ⚠️ No data for {symbol}/{interval}")
        return {"symbol": symbol, "interval": interval, "status": "no_data"}

    n_candles = len(df)
    print(f"  📊 Loaded {n_candles:,} candles")

    # Validate with grid search
    params = dict(DISCOVERY_PARAMS)
    if optimize:
        total_combos = 1
        for v in PARAM_GRID.values():
            total_combos *= len(v)
        print(f"  🔍 Running grid search optimization ({total_combos} combinations, cont+rev)...")
        result = optimize_params(df, params)
    else:
        result = validate_on_data(df, params)

    elapsed = time.time() - t0

    trades = result.get("trades", 0)
    win_rate = result.get("win_rate", 0)
    avg_pnl = result.get("avg_realized_move_pct", 0)
    total_pnl = result.get("total_pnl_pct", 0)
    exit_reasons = result.get("exit_reasons", {})
    best_params = result.get("best_params", params)

    print(f"  📈 Trades: {trades} | WR: {win_rate*100:.1f}% | Avg PnL: {avg_pnl:.3f}% | Total: {total_pnl:.2f}%")
    print(f"  📋 Exits: {exit_reasons}")
    print(f"  ⏱️ {elapsed:.1f}s")

    # Determine direction from data
    # Determine direction
    mode = best_params.get("strategy_mode", "continuation")
    mode_label = "REV" if mode == "reversal" else "CONT"
    direction = "BOTH"  # Direction is handled by the mode

    # Save to Hypothesis Vault
    if trades > 0 and validate:
        tf_label = {"1": "1m", "5": "5m", "15": "15m", "60": "1h", "240": "4h", "D": "1D"}.get(interval, f"{interval}m")

        record = HypothesisRecord(
            title=f"BigData: {symbol} {tf_label} {mode_label} | WR{win_rate*100:.0f}% T{trades}",
            thesis=f"Impulse z-score {mode} strategy on 3yr data. {trades} trades, {win_rate*100:.1f}% WR, {total_pnl:.1f}% total PnL. Best params: z_vol={best_params.get('min_dollar_volume_z')}, z_ret={best_params.get('min_price_return_z')}, SL={best_params.get('fixed_stop_loss_pct')}%, RR={best_params.get('take_profit_rr')}.",
            evidence=json.dumps({
                "candles": n_candles,
                "direction": direction,
                "source": "big_data_discovery",
            }),
            tags="auto-generated, big-data, 3yr",
            symbol=symbol,
            timeframe=tf_label,
            status="testing",
            score=win_rate,
            data_path=f"data_lake://{symbol}/{interval}/3yr",
            strategy_params=json.dumps(best_params, ensure_ascii=False),
            validation_result=json.dumps(result, ensure_ascii=False, default=str),
        )

        hyp_id = add_hypothesis(record=record, db_path=DEFAULT_DB_PATH)
        print(f"  💾 Saved as Hypothesis #{hyp_id}")

        # Auto-promote if qualifies
        promoted = auto_promote_hypothesis(
            hypothesis_id=hyp_id,
            validation_result=result,
            min_win_rate=MIN_WIN_RATE,
            min_trades=MIN_TRADES,
        )

        if promoted:
            print(f"  🚀 AUTO-PROMOTED to paper_ready!")
        else:
            print(f"  ⏳ Not promoted (WR={win_rate*100:.0f}%, trades={trades})")

        return {
            "symbol": symbol,
            "interval": interval,
            "hypothesis_id": hyp_id,
            "trades": trades,
            "win_rate": win_rate,
            "avg_pnl": avg_pnl,
            "total_pnl": total_pnl,
            "promoted": promoted,
            "best_params": best_params,
            "elapsed": elapsed,
        }

    return {
        "symbol": symbol,
        "interval": interval,
        "status": "no_trades" if trades == 0 else "not_validated",
        "trades": trades,
        "win_rate": win_rate,
        "elapsed": elapsed,
    }


def run_discovery_confluence(
    symbol: str,
    interval: str,
    hm: HistoryManager,
    validate: bool = True,
) -> dict:
    """Запускает confluence strategy discovery для одной монеты/TF."""
    print(f"\n{'='*60}")
    print(f"  {symbol} / {interval} [CONFLUENCE]")
    print(f"{'='*60}")

    t0 = time.time()
    df = hm.load(symbol, interval)

    if df.empty:
        print(f"  No data for {symbol}/{interval}")
        return {"symbol": symbol, "interval": interval, "status": "no_data"}

    n_candles = len(df)
    print(f"  Loaded {n_candles:,} candles")

    total_combos = 1
    for v in CONFLUENCE_GRID.values():
        total_combos *= len(v)
    print(f"  Running CONFLUENCE grid search (~{total_combos} combos)...")

    result = optimize_confluence(df, min_trades=MIN_TRADES, min_wr=MIN_WIN_RATE)
    elapsed = time.time() - t0

    trades = result.get("trades", 0)
    win_rate = result.get("win_rate", 0)
    avg_pnl = result.get("avg_realized_move_pct", 0)
    total_pnl = result.get("total_pnl_pct", 0)
    exit_reasons = result.get("exit_reasons", {})
    best_params = result.get("best_params", result.get("params", {}))
    combos_tested = result.get("combos_tested", 0)

    print(f"  Trades: {trades} | WR: {win_rate*100:.1f}% | Avg PnL: {avg_pnl:.3f}% | Total: {total_pnl:.2f}%")
    print(f"  Exits: {exit_reasons}")
    print(f"  Combos tested: {combos_tested}")
    print(f"  Time: {elapsed:.1f}s")

    promoted = False
    if trades > 0 and validate:
        tf_label = {"1": "1m", "5": "5m", "15": "15m", "60": "1h", "240": "4h", "D": "1D"}.get(interval, f"{interval}m")

        record = HypothesisRecord(
            title=f"Confluence: {symbol} {tf_label} | WR{win_rate*100:.0f}% T{trades}",
            thesis=(
                f"Multi-factor confluence mean-reversion on 3yr data. "
                f"{trades} trades, {win_rate*100:.1f}% WR, {total_pnl:.1f}% total PnL (net of 0.11% commission). "
                f"RSI<={best_params.get('rsi_oversold', '?')}/>{best_params.get('rsi_overbought', '?')} "
                f"+ BB({best_params.get('bb_std', '?')}s) + VolZ>{best_params.get('volume_z_min', '?')} "
                f"+ EMA dev>{best_params.get('min_ema_deviation_pct', '?')}%. "
                f"Min {best_params.get('min_confluence', '?')}/4 indicators. Dynamic TP at BB middle."
            ),
            evidence=json.dumps({
                "candles": n_candles,
                "strategy_type": "confluence",
                "source": "confluence_discovery",
            }),
            tags="auto-generated, confluence, mean-reversion",
            symbol=symbol,
            timeframe=tf_label,
            status="testing",
            score=win_rate,
            data_path=f"data_lake://{symbol}/{interval}/3yr",
            strategy_params=json.dumps({**best_params, "strategy_type": "confluence"}, ensure_ascii=False),
            validation_result=json.dumps(result, ensure_ascii=False, default=str),
        )

        hyp_id = add_hypothesis(record=record, db_path=DEFAULT_DB_PATH)
        print(f"  Saved as Hypothesis #{hyp_id}")

        promoted = auto_promote_hypothesis(
            hypothesis_id=hyp_id,
            validation_result=result,
            min_win_rate=MIN_WIN_RATE,
            min_trades=MIN_TRADES,
        )

        if promoted:
            print(f"  AUTO-PROMOTED to paper_ready!")
        else:
            print(f"  Not promoted (WR={win_rate*100:.0f}%, trades={trades})")

        return {
            "symbol": symbol,
            "interval": interval,
            "hypothesis_id": hyp_id,
            "trades": trades,
            "win_rate": win_rate,
            "avg_pnl": avg_pnl,
            "total_pnl": total_pnl,
            "promoted": promoted,
            "best_params": best_params,
            "elapsed": elapsed,
        }

    return {
        "symbol": symbol,
        "interval": interval,
        "status": "no_trades" if trades == 0 else "not_validated",
        "trades": trades,
        "win_rate": win_rate,
        "elapsed": elapsed,
    }


def main():
    parser = argparse.ArgumentParser(description="Big Data Discovery on Data Lake")
    parser.add_argument("--coins", nargs="+", default=None, help="Symbols (e.g. BTCUSDT ETHUSDT)")
    parser.add_argument("--intervals", nargs="+", default=["5", "15"], help="Intervals (e.g. 5 15 60)")
    parser.add_argument("--all", action="store_true", help="All 20 coins")
    parser.add_argument("--validate", action="store_true", default=True, help="Validate + promote")
    parser.add_argument("--no-optimize", action="store_true", help="Skip grid search")
    parser.add_argument("--export-pack", action="store_true", help="Export strategy pack after")
    parser.add_argument("--strategy", choices=["impulse", "confluence"], default="confluence",
                       help="Strategy type: impulse (z-score) or confluence (multi-factor)")
    args = parser.parse_args()

    if args.all:
        coins = list(SUPPORTED_COINS)
    elif args.coins:
        coins = [c.upper() if not c.endswith("USDT") else c for c in args.coins]
    else:
        # Default: top 5 by volume
        coins = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]

    print(f"\n{'='*60}")
    print(f"  🔬 BIG DATA DISCOVERY [{args.strategy.upper()}]")
    print(f"  Coins: {len(coins)} | Intervals: {args.intervals}")
    print(f"  Validate: {args.validate} | Strategy: {args.strategy}")
    print(f"{'='*60}")

    hm = HistoryManager()

    results = []
    for symbol in coins:
        for interval in args.intervals:
            try:
                if args.strategy == "confluence":
                    r = run_discovery_confluence(
                        symbol=symbol,
                        interval=interval,
                        hm=hm,
                        validate=args.validate,
                    )
                else:
                    r = run_discovery_for_coin(
                        symbol=symbol,
                        interval=interval,
                        hm=hm,
                        validate=args.validate,
                        optimize=not args.no_optimize,
                    )
                results.append(r)
            except Exception as e:
                print(f"  ❌ Error processing {symbol}/{interval}: {e}")
                import traceback; traceback.print_exc()
                results.append({"symbol": symbol, "interval": interval, "error": str(e)})

    # Summary
    print(f"\n\n{'='*60}")
    print(f"  📊 DISCOVERY RESULTS SUMMARY")
    print(f"{'='*60}\n")

    promoted = [r for r in results if r.get("promoted")]
    tested = [r for r in results if r.get("trades", 0) > 0]

    print(f"  Total combinations tested: {len(results)}")
    print(f"  With trades: {len(tested)}")
    print(f"  Promoted to paper_ready: {len(promoted)}")
    print()

    if tested:
        print(f"  {'Symbol':<14} {'TF':<5} {'Trades':>7} {'WR':>7} {'AvgPnL':>8} {'TotalPnL':>10} {'Status':<12}")
        print(f"  {'-'*14} {'-'*5} {'-'*7} {'-'*7} {'-'*8} {'-'*10} {'-'*12}")
        for r in sorted(tested, key=lambda x: x.get("win_rate", 0), reverse=True):
            status = "🚀 READY" if r.get("promoted") else "⏳ testing"
            print(
                f"  {r['symbol']:<14} {r['interval']:<5} "
                f"{r['trades']:>7} {r['win_rate']*100:>6.1f}% {r.get('avg_pnl', 0):>7.3f}% "
                f"{r.get('total_pnl', 0):>9.2f}% {status}"
            )

    # Export pack if requested or if any promoted
    if args.export_pack or promoted:
        print(f"\n  📦 Exporting strategy pack...")
        pack = export_strategy_pack()
        print(f"  ✅ Pack exported: {pack['total_strategies']} strategies")

    print(f"\n{'='*60}")
    total_time = sum(r.get("elapsed", 0) for r in results)
    print(f"  Total time: {total_time:.1f}s")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
