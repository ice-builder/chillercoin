from __future__ import annotations

from pathlib import Path
import json

import numpy as np
import pandas as pd
import torch

from .config import RunConfig
from .data import load_ohlcv_csv
from .features import build_dataset
from .model import load_model


CLASS_TO_POSITION = {0: -1, 1: 0, 2: 1}


def run_backtest(data_path: Path, artifacts_dir: Path, config: RunConfig) -> dict:
    raw = load_ohlcv_csv(data_path)
    prepared = build_dataset(raw, config.feature, config.label)
    frame = prepared.frame.copy()

    model, meta = load_model(
        artifacts_dir / "model.pt",
        artifacts_dir / "model_meta.json",
        hidden_dim=config.train.hidden_dim,
    )

    features = meta["feature_columns"]
    means = np.array(meta["means"], dtype=np.float32)
    stds = np.array(meta["stds"], dtype=np.float32)
    x = frame[features].to_numpy(dtype=np.float32)
    x = (x - means) / stds

    with torch.no_grad():
        logits = model(torch.from_numpy(x))
        probs = torch.softmax(logits, dim=1).numpy()

    frame["prob_short"] = probs[:, 0]
    frame["prob_flat"] = probs[:, 1]
    frame["prob_long"] = probs[:, 2]
    frame["signal"] = decide_signal(
        probs,
        threshold=config.backtest.decision_threshold,
        cooldown_bars=config.backtest.cooldown_bars,
    )

    frame["ret_1"] = frame["close"].pct_change().fillna(0.0)
    frame["trade_flag"] = frame["signal"].diff().fillna(frame["signal"]).ne(0).astype(int)
    fee = config.backtest.fee_bps / 10_000
    slippage = config.backtest.slippage_bps / 10_000
    frame["cost"] = frame["trade_flag"] * (fee + slippage)
    frame["strategy_ret"] = frame["signal"].shift(1).fillna(0) * frame["ret_1"] - frame["cost"]
    frame["equity"] = (1 + frame["strategy_ret"]).cumprod()

    summary = {
        "bars": int(len(frame)),
        "trades": int(frame["trade_flag"].sum()),
        "avg_position": float(np.abs(frame["signal"]).mean()),
        "hit_rate": hit_rate(frame),
        "total_return": float(frame["equity"].iloc[-1] - 1.0),
        "max_drawdown": max_drawdown(frame["equity"]),
        "sharpe_like": sharpe_like(frame["strategy_ret"]),
    }

    (artifacts_dir / "backtest_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    frame.to_csv(artifacts_dir / "backtest_predictions.csv", index=False)
    return summary


def decide_signal(probs: np.ndarray, threshold: float, cooldown_bars: int) -> np.ndarray:
    signal = np.zeros(len(probs), dtype=np.int8)
    cooldown = 0
    for idx, row in enumerate(probs):
        if cooldown > 0:
            signal[idx] = signal[idx - 1] if idx > 0 else 0
            cooldown -= 1
            continue

        short_p, flat_p, long_p = row
        next_signal = 0
        if long_p >= threshold and long_p > short_p and long_p > flat_p:
            next_signal = 1
        elif short_p >= threshold and short_p > long_p and short_p > flat_p:
            next_signal = -1

        prev_signal = signal[idx - 1] if idx > 0 else 0
        signal[idx] = next_signal
        if next_signal != prev_signal:
            cooldown = cooldown_bars
    return signal


def hit_rate(frame: pd.DataFrame) -> float:
    active = frame["signal"].shift(1).fillna(0) != 0
    if active.sum() == 0:
        return 0.0
    wins = frame.loc[active, "strategy_ret"] > 0
    return float(wins.mean())


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    return float(drawdown.min())


def sharpe_like(returns: pd.Series) -> float:
    std = returns.std()
    if std == 0 or np.isnan(std):
        return 0.0
    return float((returns.mean() / std) * np.sqrt(365 * 24 * 60))
