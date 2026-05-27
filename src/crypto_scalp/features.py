from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from .config import FeatureConfig, LabelConfig


@dataclass
class PreparedDataset:
    frame: pd.DataFrame
    feature_columns: list[str]


def build_dataset(
    df: pd.DataFrame,
    feature_cfg: FeatureConfig,
    label_cfg: LabelConfig,
) -> PreparedDataset:
    frame = df.copy()
    frame["ret_1"] = frame["close"].pct_change()
    frame["ret_fast"] = frame["close"].pct_change(feature_cfg.lookback_fast)
    frame["ret_slow"] = frame["close"].pct_change(feature_cfg.lookback_slow)
    frame["range_pct"] = (frame["high"] - frame["low"]) / frame["close"]
    frame["body_pct"] = (frame["close"] - frame["open"]) / frame["open"]

    frame["volume_z"] = zscore(
        frame["volume"], window=feature_cfg.volume_window, min_periods=feature_cfg.lookback_slow
    )
    frame["volatility"] = frame["ret_1"].rolling(feature_cfg.volatility_window).std()
    frame["volatility_z"] = zscore(
        frame["volatility"], window=feature_cfg.volatility_window, min_periods=feature_cfg.lookback_slow
    )

    ema_fast = frame["close"].ewm(span=feature_cfg.lookback_fast, adjust=False).mean()
    ema_slow = frame["close"].ewm(span=feature_cfg.lookback_slow, adjust=False).mean()
    frame["ema_gap_pct"] = (ema_fast - ema_slow) / frame["close"]
    frame["distance_fast_ema"] = (frame["close"] - ema_fast) / frame["close"]
    frame["breakout_fast"] = frame["close"] / frame["high"].rolling(feature_cfg.lookback_fast).max() - 1.0
    frame["breakout_slow"] = frame["close"] / frame["high"].rolling(feature_cfg.lookback_slow).max() - 1.0
    frame["drawdown_fast"] = frame["close"] / frame["low"].rolling(feature_cfg.lookback_fast).min() - 1.0

    future_return = frame["close"].shift(-label_cfg.horizon) / frame["close"] - 1.0
    frame["target"] = 1
    frame.loc[future_return >= label_cfg.min_move_pct, "target"] = 2
    frame.loc[future_return <= -label_cfg.min_move_pct, "target"] = 0

    feature_columns = [
        "ret_1",
        "ret_fast",
        "ret_slow",
        "range_pct",
        "body_pct",
        "volume_z",
        "volatility",
        "volatility_z",
        "ema_gap_pct",
        "distance_fast_ema",
        "breakout_fast",
        "breakout_slow",
        "drawdown_fast",
    ]

    frame = frame.replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)
    return PreparedDataset(frame=frame, feature_columns=feature_columns)


def zscore(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    rolling_mean = series.rolling(window=window, min_periods=min_periods).mean()
    rolling_std = series.rolling(window=window, min_periods=min_periods).std().replace(0, np.nan)
    return (series - rolling_mean) / rolling_std
