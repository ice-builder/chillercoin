"""
data_lake.py — Parquet-based OHLCV Data Lake

Структура хранилища:
  data/lake/
    catalog.json
    BTCUSDT/1m/2023-01.parquet, 2023-02.parquet, ...
    BTCUSDT/5m/2023.parquet, 2024.parquet, ...
    BTCUSDT/1h/all.parquet
    BTCUSDT/4h/all.parquet
    ...

Стратегия партиционирования:
  1m  → по месяцам (YYYY-MM)
  5m, 15m → по годам (YYYY)
  1h, 4h, D → один файл "all.parquet"
"""

from __future__ import annotations

import json
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

SUPPORTED_COINS: List[str] = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "BNBUSDT",
    "SUIUSDT",
    "TRXUSDT",
    "TONUSDT",
    "SHIB1000USDT",
    "1000PEPEUSDT",
    "AVAXUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "DOTUSDT",
    "POLUSDT",
    "LINKUSDT",
    "UNIUSDT",
    "LTCUSDT",
    "ATOMUSDT",
    "NEARUSDT",
]

SUPPORTED_INTERVALS: List[str] = ["1", "5", "15", "60", "240", "D"]

# Интервал → timedelta
INTERVAL_TO_DELTA = {
    "1":   timedelta(minutes=1),
    "5":   timedelta(minutes=5),
    "15":  timedelta(minutes=15),
    "60":  timedelta(hours=1),
    "240": timedelta(hours=4),
    "D":   timedelta(days=1),
}

# Интервал → стратегия партиционирования
PARTITION_STRATEGY = {
    "1":   "monthly",   # YYYY-MM
    "5":   "yearly",    # YYYY
    "15":  "yearly",
    "60":  "yearly",
    "240": "all",       # one file
    "D":   "all",
}

CATALOG_FILENAME = "catalog.json"
PARQUET_COMPRESSION = "snappy"

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _partition_key(interval: str, dt: datetime) -> str:
    """Возвращает ключ партиции ('2023-01', '2023', 'all') для заданного момента времени."""
    strategy = PARTITION_STRATEGY.get(interval, "yearly")
    if strategy == "monthly":
        return dt.strftime("%Y-%m")
    if strategy == "yearly":
        return dt.strftime("%Y")
    return "all"


def _partition_keys_for_range(interval: str, start: datetime, end: datetime) -> List[str]:
    """Список всех ключей партиций, покрывающих диапазон [start, end]."""
    strategy = PARTITION_STRATEGY.get(interval, "yearly")
    keys: List[str] = []
    current = _ensure_utc(start)
    end_utc = _ensure_utc(end)

    if strategy == "all":
        return ["all"]

    seen: set[str] = set()
    step = timedelta(days=1) if strategy == "yearly" else timedelta(days=1)
    max_iter = 2000  # защита от бесконечного цикла
    i = 0
    while current <= end_utc and i < max_iter:
        key = _partition_key(interval, current)
        if key not in seen:
            seen.add(key)
            keys.append(key)
        if strategy == "monthly":
            # Перейти к следующему месяцу
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1, day=1)
            else:
                current = current.replace(month=current.month + 1, day=1)
        else:
            # Перейти к следующему году
            current = current.replace(year=current.year + 1, month=1, day=1)
        i += 1
    return keys


def _file_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# DataLakeManager
# ---------------------------------------------------------------------------

class DataLakeManager:
    """Менеджер Parquet Data Lake для OHLCV данных."""

    def __init__(self, root: Optional[Path] = None):
        if root is None:
            # Поиск корня проекта
            here = Path(__file__).resolve().parent
            project_root = here.parent.parent
            root = project_root / "data" / "lake"
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._catalog: Dict = self._load_catalog()

    # ------------------------------------------------------------------
    # Catalog
    # ------------------------------------------------------------------

    def _catalog_path(self) -> Path:
        return self.root / CATALOG_FILENAME

    def _load_catalog(self) -> Dict:
        p = self._catalog_path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_catalog(self) -> None:
        self._catalog_path().write_text(
            json.dumps(self._catalog, indent=2, default=str),
            encoding="utf-8",
        )

    def _catalog_key(self, symbol: str, interval: str, partition: str) -> str:
        return f"{symbol}/{interval}/{partition}"

    def _update_catalog_entry(self, symbol: str, interval: str, partition: str, path: Path, df: pd.DataFrame) -> None:
        key = self._catalog_key(symbol, interval, partition)
        ts = df["timestamp"]
        self._catalog[key] = {
            "symbol": symbol,
            "interval": interval,
            "partition": partition,
            "path": str(path.relative_to(self.root)),
            "rows": len(df),
            "min_ts": str(ts.min()),
            "max_ts": str(ts.max()),
            "size_bytes": path.stat().st_size if path.exists() else 0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_catalog()

    # ------------------------------------------------------------------
    # Запись
    # ------------------------------------------------------------------

    def write_partition(
        self,
        symbol: str,
        interval: str,
        df: pd.DataFrame,
        partition_key: Optional[str] = None,
    ) -> Path:
        """Записать DataFrame в Parquet-партицию."""
        if df.empty:
            raise ValueError("Cannot write empty DataFrame")

        df = df.copy()
        # Убедимся что timestamp timezone-aware
        if not hasattr(df["timestamp"].dtype, "tz") or df["timestamp"].dt.tz is None:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

        if partition_key is None:
            partition_key = _partition_key(interval, df["timestamp"].iloc[0].to_pydatetime())

        part_dir = self.root / symbol / interval
        part_dir.mkdir(parents=True, exist_ok=True)
        out_path = part_dir / f"{partition_key}.parquet"

        # Если файл уже существует — мержим
        if out_path.exists():
            existing = pd.read_parquet(out_path)
            # Унификация timezone
            if existing["timestamp"].dt.tz is None:
                existing["timestamp"] = pd.to_datetime(existing["timestamp"], utc=True)
            df = pd.concat([existing, df], ignore_index=True)
            df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

        df.to_parquet(out_path, index=False, compression=PARQUET_COMPRESSION)
        self._update_catalog_entry(symbol, interval, partition_key, out_path, df)
        logger.debug("Written %d rows → %s", len(df), out_path)
        return out_path

    def write_dataframe(self, symbol: str, interval: str, df: pd.DataFrame) -> List[Path]:
        """Разбить DataFrame на партиции и записать каждую."""
        if df.empty:
            return []

        df = df.copy()
        if not hasattr(df["timestamp"].dtype, "tz") or df["timestamp"].dt.tz is None:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

        strategy = PARTITION_STRATEGY.get(interval, "yearly")
        if strategy == "all":
            return [self.write_partition(symbol, interval, df, "all")]

        paths = []
        if strategy == "monthly":
            df["_pk"] = df["timestamp"].dt.strftime("%Y-%m")
        else:
            df["_pk"] = df["timestamp"].dt.strftime("%Y")

        for pk, group in df.groupby("_pk"):
            group = group.drop(columns=["_pk"])
            paths.append(self.write_partition(symbol, interval, group, str(pk)))
        return paths

    # ------------------------------------------------------------------
    # Чтение
    # ------------------------------------------------------------------

    def read_range(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Читать OHLCV данные для символа/интервала в диапазоне [start, end]."""
        start = _ensure_utc(start)
        end = _ensure_utc(end)

        keys = _partition_keys_for_range(interval, start, end)
        part_dir = self.root / symbol / interval

        frames: List[pd.DataFrame] = []
        for key in keys:
            path = part_dir / f"{key}.parquet"
            if path.exists():
                try:
                    df = pd.read_parquet(path)
                    if df["timestamp"].dt.tz is None:
                        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                    frames.append(df)
                except Exception as e:
                    logger.warning("Failed to read %s: %s", path, e)

        if not frames:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])

        out = pd.concat(frames, ignore_index=True)
        out = out.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
        mask = (out["timestamp"] >= pd.Timestamp(start)) & (out["timestamp"] <= pd.Timestamp(end))
        return out.loc[mask].reset_index(drop=True)

    def read_latest(self, symbol: str, interval: str, n_rows: int = 1000) -> pd.DataFrame:
        """Читать последние N свечей."""
        end = datetime.now(timezone.utc)
        start = end - INTERVAL_TO_DELTA.get(interval, timedelta(minutes=1)) * n_rows * 2
        df = self.read_range(symbol, interval, start, end)
        return df.tail(n_rows).reset_index(drop=True)

    # ------------------------------------------------------------------
    # Coverage / Catalog queries
    # ------------------------------------------------------------------

    def get_coverage(self, symbol: str, interval: str) -> Optional[Tuple[datetime, datetime, int]]:
        """Возвращает (min_ts, max_ts, total_rows) или None если нет данных."""
        prefix = f"{symbol}/{interval}/"
        entries = [v for k, v in self._catalog.items() if k.startswith(prefix)]
        if not entries:
            return None
        min_ts = min(e["min_ts"] for e in entries)
        max_ts = max(e["max_ts"] for e in entries)
        total_rows = sum(e["rows"] for e in entries)
        try:
            mn = datetime.fromisoformat(str(min_ts))
            mx = datetime.fromisoformat(str(max_ts))
        except Exception:
            mn = pd.Timestamp(min_ts).to_pydatetime()
            mx = pd.Timestamp(max_ts).to_pydatetime()
        return mn, mx, total_rows

    def get_catalog_summary(self) -> pd.DataFrame:
        """DataFrame с покрытием всех монет × интервалов."""
        rows = []
        for symbol in SUPPORTED_COINS:
            for interval in SUPPORTED_INTERVALS:
                cov = self.get_coverage(symbol, interval)
                if cov:
                    mn, mx, total = cov
                    # Размер в MB
                    prefix = f"{symbol}/{interval}/"
                    size_bytes = sum(
                        e.get("size_bytes", 0)
                        for k, e in self._catalog.items()
                        if k.startswith(prefix)
                    )
                    # Процент от 3 лет
                    three_years_ago = datetime.now(timezone.utc) - timedelta(days=365 * 3)
                    expected_days = (datetime.now(timezone.utc) - three_years_ago).days
                    if mn.tzinfo is None:
                        mn = mn.replace(tzinfo=timezone.utc)
                    if mx.tzinfo is None:
                        mx = mx.replace(tzinfo=timezone.utc)
                    actual_days = (mx - mn).days
                    coverage_pct = min(100.0, round(actual_days / expected_days * 100, 1))
                    rows.append({
                        "symbol": symbol,
                        "interval": interval,
                        "min_date": mn.strftime("%Y-%m-%d"),
                        "max_date": mx.strftime("%Y-%m-%d"),
                        "rows": total,
                        "size_mb": round(size_bytes / 1_048_576, 1),
                        "coverage_pct": coverage_pct,
                    })
                else:
                    rows.append({
                        "symbol": symbol,
                        "interval": interval,
                        "min_date": None,
                        "max_date": None,
                        "rows": 0,
                        "size_mb": 0.0,
                        "coverage_pct": 0.0,
                    })
        return pd.DataFrame(rows)

    def get_missing_ranges(
        self,
        symbol: str,
        interval: str,
        desired_start: datetime,
        desired_end: datetime,
    ) -> List[Tuple[datetime, datetime]]:
        """Возвращает список (start, end) отрезков, которых не хватает в lake."""
        desired_start = _ensure_utc(desired_start)
        desired_end = _ensure_utc(desired_end)
        cov = self.get_coverage(symbol, interval)
        if cov is None:
            return [(desired_start, desired_end)]

        mn, mx, _ = cov
        mn = _ensure_utc(mn)
        mx = _ensure_utc(mx)
        missing = []
        if desired_start < mn - INTERVAL_TO_DELTA.get(interval, timedelta(minutes=1)):
            missing.append((desired_start, mn - INTERVAL_TO_DELTA.get(interval, timedelta(minutes=1))))
        if mx < desired_end - INTERVAL_TO_DELTA.get(interval, timedelta(minutes=1)):
            missing.append((mx + INTERVAL_TO_DELTA.get(interval, timedelta(minutes=1)), desired_end))
        return missing

    def get_lake_size_bytes(self) -> int:
        """Суммарный размер всех файлов lake."""
        return sum(p.stat().st_size for p in self.root.rglob("*.parquet") if p.is_file())

    def refresh_catalog(self) -> None:
        """Пересканировать lake и обновить catalog.json."""
        self._catalog = {}
        for parquet_path in sorted(self.root.rglob("*.parquet")):
            try:
                parts = parquet_path.relative_to(self.root).parts
                if len(parts) != 3:
                    continue
                symbol, interval, fname = parts
                partition_key = fname.replace(".parquet", "")
                df = pd.read_parquet(parquet_path, columns=["timestamp"])
                if df["timestamp"].dt.tz is None:
                    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                self._catalog[self._catalog_key(symbol, interval, partition_key)] = {
                    "symbol": symbol,
                    "interval": interval,
                    "partition": partition_key,
                    "path": str(parquet_path.relative_to(self.root)),
                    "rows": len(df),
                    "min_ts": str(df["timestamp"].min()),
                    "max_ts": str(df["timestamp"].max()),
                    "size_bytes": parquet_path.stat().st_size,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as e:
                logger.warning("Skipping %s: %s", parquet_path, e)
        self._save_catalog()
        logger.info("Catalog refreshed: %d entries", len(self._catalog))
