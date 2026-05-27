from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, List, Optional

import pandas as pd
import requests


BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
SUPPORTED_INTERVALS = ["1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D", "W"]
INTERVAL_TO_DELTA = {
    "1": timedelta(minutes=1),
    "3": timedelta(minutes=3),
    "5": timedelta(minutes=5),
    "15": timedelta(minutes=15),
    "30": timedelta(minutes=30),
    "60": timedelta(hours=1),
    "120": timedelta(hours=2),
    "240": timedelta(hours=4),
    "360": timedelta(hours=6),
    "720": timedelta(hours=12),
    "D": timedelta(days=1),
    "W": timedelta(days=7),
}


@dataclass
class BybitDownloadConfig:
    symbol: str = "BTCUSDT"
    interval: str = "1"
    category: str = "linear"
    limit: int = 1000


@dataclass
class DownloadProgress:
    current_chunk: int
    total_chunks: int
    current_start: datetime
    current_end: datetime
    rows_downloaded: int


@dataclass
class CacheHit:
    path: Path
    coverage_start: datetime
    coverage_end: datetime
    requested_rows: int
    expected_rows: int
    requested_start: datetime
    requested_end: datetime
    effective_end: datetime
    missing_rows: int = 0


def download_bybit_klines(
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    category: str = "linear",
    limit: int = 1000,
    session: Optional[requests.Session] = None,
    progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
) -> pd.DataFrame:
    if interval not in INTERVAL_TO_DELTA:
        raise ValueError(f"Unsupported interval: {interval}")
    if start >= end:
        raise ValueError("start must be earlier than end")

    client = session or requests.Session()
    current = ensure_utc(start)
    end = ensure_utc(end)
    step = INTERVAL_TO_DELTA[interval] * (limit - 1)
    total_chunks = max(1, int(((end - current) // step) + 1))

    chunks: List[pd.DataFrame] = []
    chunk_index = 0
    while current < end:
        chunk_index += 1
        chunk_end = min(current + step, end)
        params = {
            "category": category,
            "symbol": symbol.upper(),
            "interval": interval,
            "start": to_ms(current),
            "end": to_ms(chunk_end),
            "limit": limit,
        }
        response = client.get(BYBIT_KLINE_URL, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if payload.get("retCode") != 0:
            raise ValueError(f"Bybit API error: {payload}")

        raw_list = payload.get("result", {}).get("list", [])
        if not raw_list:
            current = chunk_end + INTERVAL_TO_DELTA[interval]
            continue

        frame = parse_kline_rows(raw_list)
        chunks.append(frame)
        if progress_callback is not None:
            progress_callback(
                DownloadProgress(
                    current_chunk=chunk_index,
                    total_chunks=total_chunks,
                    current_start=current,
                    current_end=chunk_end,
                    rows_downloaded=sum(len(item) for item in chunks),
                )
            )
        last_ts = frame["timestamp"].max().to_pydatetime().replace(tzinfo=timezone.utc)
        current = last_ts + INTERVAL_TO_DELTA[interval]

    if not chunks:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])

    out = pd.concat(chunks, ignore_index=True)
    out = out.sort_values("timestamp").drop_duplicates("timestamp")
    out = out[(out["timestamp"] >= ensure_utc(start)) & (out["timestamp"] <= ensure_utc(end))].reset_index(drop=True)
    return out


def save_bybit_klines_csv(
    output_path: Path,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    category: str = "linear",
    progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame = download_bybit_klines(
        symbol=symbol,
        interval=interval,
        start=start,
        end=end,
        category=category,
        progress_callback=progress_callback,
    )
    frame.to_csv(output_path, index=False)
    return output_path


def default_output_path(root: Path, symbol: str, interval: str, start: datetime, end: datetime) -> Path:
    return root / "data" / f"bybit_{symbol.lower()}_{interval}_{start:%Y%m%d}_{end:%Y%m%d}.csv"


def find_reusable_bybit_csv(
    root: Path,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
) -> Optional[CacheHit]:
    requested_start = ensure_utc(start)
    requested_end = ensure_utc(end)
    symbol_key = symbol.lower()

    candidates = sorted((root / "data").glob(f"bybit_{symbol_key}_{interval}_*.csv"))
    for path in candidates:
        hit = inspect_csv_cache(path=path, interval=interval, start=requested_start, end=requested_end)
        if hit is not None:
            return hit
    return None


def inspect_csv_cache(
    path: Path,
    interval: str,
    start: datetime,
    end: datetime,
) -> Optional[CacheHit]:
    if not path.exists():
        return None

    frame = pd.read_csv(path, usecols=["timestamp"])
    if frame.empty:
        return None

    timestamps = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce").dropna().sort_values().reset_index(drop=True)
    if timestamps.empty:
        return None

    requested_start = ensure_utc(start)
    requested_end = ensure_utc(end)
    delta = pd.Timedelta(INTERVAL_TO_DELTA[interval])
    first_ts = timestamps.iloc[0].to_pydatetime()
    last_ts = timestamps.iloc[-1].to_pydatetime()
    if first_ts > requested_start + INTERVAL_TO_DELTA[interval]:
        return None
    effective_end = effective_cache_end(requested_end, interval)
    if last_ts < effective_end:
        current = datetime.now(timezone.utc)
        if requested_end.date() >= current.date() and last_ts.date() == current.date():
            effective_end = last_ts
        else:
            return None

    effective_start = max(requested_start, first_ts)
    mask = (timestamps >= pd.Timestamp(effective_start)) & (timestamps <= pd.Timestamp(effective_end))
    subset = timestamps.loc[mask].reset_index(drop=True)
    expected_rows = expected_candle_count(effective_start, effective_end, interval)
    missing_rows = expected_rows - len(subset)
    missing_tolerance = max(2, int(expected_rows * 0.002))
    if missing_rows < 0 or missing_rows > missing_tolerance:
        return None

    if len(subset) > 1 and not (subset.diff().iloc[1:] == delta).all():
        gap_rows = int(((subset.diff().iloc[1:] / delta) - 1).clip(lower=0).sum())
        if gap_rows > missing_tolerance:
            return None
        missing_rows = max(missing_rows, gap_rows)

    return CacheHit(
        path=path,
        coverage_start=first_ts,
        coverage_end=last_ts,
        requested_rows=len(subset),
        expected_rows=expected_rows,
        requested_start=requested_start,
        requested_end=requested_end,
        effective_end=effective_end,
        missing_rows=int(missing_rows),
    )


def parse_kline_rows(rows: Iterable[list[str]]) -> pd.DataFrame:
    frame = pd.DataFrame(
        rows,
        columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"],
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"].astype("int64"), unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume", "turnover"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_ms(dt: datetime) -> int:
    return int(ensure_utc(dt).timestamp() * 1000)


def expected_candle_count(start: datetime, end: datetime, interval: str) -> int:
    delta = INTERVAL_TO_DELTA[interval]
    span = ensure_utc(end) - ensure_utc(start)
    return int(span // delta) + 1


def effective_cache_end(end: datetime, interval: str, now: Optional[datetime] = None) -> datetime:
    """Clamp moving "today" requests so a fresh local file is reusable during the day."""
    requested_end = ensure_utc(end)
    current = ensure_utc(now or datetime.now(timezone.utc))
    delta = INTERVAL_TO_DELTA[interval]
    if requested_end >= current:
        return min(requested_end, floor_interval_start(current, interval))
    freshness_window = max(delta * 5, timedelta(minutes=10))
    moving_end = current - freshness_window
    if moving_end <= requested_end:
        return max(requested_end.replace(hour=0, minute=0, second=0, microsecond=0), moving_end)
    return requested_end


def floor_interval_start(dt: datetime, interval: str) -> datetime:
    dt = ensure_utc(dt).replace(second=0, microsecond=0)
    if interval == "W":
        week_start = dt - timedelta(days=dt.weekday())
        return week_start.replace(hour=0, minute=0)
    if interval == "D":
        return dt.replace(hour=0, minute=0)

    delta = INTERVAL_TO_DELTA[interval]
    if delta >= timedelta(days=1):
        return dt.replace(hour=0, minute=0)
    midnight = dt.replace(hour=0, minute=0)
    elapsed = dt - midnight
    intervals = int(elapsed // delta)
    return midnight + intervals * delta
