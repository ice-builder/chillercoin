from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd


REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def load_ohlcv_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    out = df[REQUIRED_COLUMNS].copy()
    out["timestamp"] = parse_timestamp(out["timestamp"])
    out = out.sort_values("timestamp").drop_duplicates("timestamp")

    for col in REQUIRED_COLUMNS[1:]:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out.dropna().reset_index(drop=True)
    return out


def parse_timestamp(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().all():
        return pd.to_datetime(series.astype("int64"), unit="ms", utc=True)
    return pd.to_datetime(series, utc=True)


def make_demo_data(rows: int = 5000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    timestamps = pd.date_range("2024-01-01", periods=rows, freq="min", tz="UTC")

    drift = rng.normal(0.0, 0.0004, rows)
    shock = rng.normal(0.0, 0.002, rows)
    impulse = (rng.random(rows) > 0.97) * rng.normal(0.0, 0.01, rows)
    returns = drift + shock + impulse

    close = 42000 * np.exp(np.cumsum(returns))
    open_ = np.concatenate([[close[0]], close[:-1]])
    spread = np.maximum(np.abs(close - open_), close * 0.0005)
    high = np.maximum(open_, close) + spread * rng.uniform(0.2, 1.0, rows)
    low = np.minimum(open_, close) - spread * rng.uniform(0.2, 1.0, rows)
    volume = rng.lognormal(mean=10.5, sigma=0.35, size=rows)
    volume *= 1 + (np.abs(impulse) > 0) * rng.uniform(1.5, 3.0, rows)

    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )
