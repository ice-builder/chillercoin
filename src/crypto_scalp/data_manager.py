"""
data_manager.py — Центральный менеджер исторических данных

Тонкий фасад поверх DataLakeManager + Bybit downloader.
Предоставляет простой API для работы с данными в Research App и Auto-Discover.

Использование:
    from crypto_scalp.data_manager import HistoryManager

    hm = HistoryManager()
    df = hm.load("BTCUSDT", "5")           # быстрое чтение из Parquet
    hm.update("ETHUSDT", "15")             # инкрементальное обновление
    hm.download("SOLUSDT", "60", years=3)  # полная загрузка
    print(hm.list_available())             # таблица доступных данных
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .data_lake import DataLakeManager, SUPPORTED_COINS, SUPPORTED_INTERVALS
from .bulk_downloader import BulkDownloadJob, run_bulk_download, build_default_jobs

logger = logging.getLogger(__name__)

# Псевдонимы для удобного использования
TF_LABELS = {"1": "1m", "5": "5m", "15": "15m", "60": "1h", "240": "4h", "D": "1D"}


class HistoryManager:
    """
    Высокоуровневый менеджер исторических данных.

    Хранилище: data/lake/{SYMBOL}/{INTERVAL}/*.parquet
    Все чтения — из Parquet (быстро, ~50ms на 300k строк).
    Все записи — через DataLakeManager (инкрементально, без дублей).
    """

    def __init__(self, root: Optional[Path] = None):
        self._lake = DataLakeManager(root=root)

    # ------------------------------------------------------------------
    # Чтение
    # ------------------------------------------------------------------

    def load(
        self,
        symbol: str,
        interval: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """
        Загрузить OHLCV данные из локального Parquet.

        Parameters
        ----------
        symbol : str  (напр. "BTCUSDT")
        interval : str  (напр. "5" = 5m, "60" = 1h, "D" = 1D)
        start : datetime | None  — если None, берёт всё что есть
        end   : datetime | None  — если None, берёт до сегодня

        Returns
        -------
        pd.DataFrame с колонками: timestamp, open, high, low, close, volume, turnover
        Пустой DataFrame если данных нет.
        """
        end = end or datetime.now(timezone.utc)
        if start is None:
            cov = self._lake.get_coverage(symbol, interval)
            if cov is None:
                logger.warning("No data for %s/%s in lake. Run download() first.", symbol, interval)
                return pd.DataFrame()
            start = cov[0]

        return self._lake.read_range(symbol, interval, start, end)

    def load_latest(self, symbol: str, interval: str, n_rows: int = 1000) -> pd.DataFrame:
        """Последние N свечей из lake."""
        return self._lake.read_latest(symbol, interval, n_rows=n_rows)

    # ------------------------------------------------------------------
    # Скачка
    # ------------------------------------------------------------------

    def download(
        self,
        symbol: str,
        interval: str,
        years: float = 3.0,
        workers: int = 1,
    ) -> int:
        """
        Полная загрузка истории для одной монеты/таймфрейма.

        Returns
        -------
        int — количество скачанных строк
        """
        jobs = [BulkDownloadJob(
            symbol=symbol,
            interval=interval,
            start=datetime.now(timezone.utc) - timedelta(days=int(365 * years)),
            end=datetime.now(timezone.utc),
        )]
        results = run_bulk_download(jobs=jobs, lake=self._lake, workers=workers)
        total = sum(r.rows_written for r in results)
        logger.info("download(%s/%s): %d rows written", symbol, interval, total)
        return total

    def download_many(
        self,
        symbols: Optional[List[str]] = None,
        intervals: Optional[List[str]] = None,
        years: float = 3.0,
        workers: int = 4,
    ) -> Dict[str, int]:
        """
        Параллельная загрузка для нескольких монет/таймфреймов.

        Returns
        -------
        dict {symbol/interval: rows_written}
        """
        jobs = build_default_jobs(
            symbols=symbols or SUPPORTED_COINS,
            intervals=intervals or SUPPORTED_INTERVALS,
            years=years,
        )
        results = run_bulk_download(jobs=jobs, lake=self._lake, workers=workers)
        return {
            f"{r.job.symbol}/{r.job.interval}": r.rows_written
            for r in results
        }

    def update(
        self,
        symbol: str,
        interval: str,
        workers: int = 1,
    ) -> int:
        """
        Инкрементальное обновление — докачать только новые свечи.
        Логика встроена в bulk_downloader (get_missing_ranges).

        Returns
        -------
        int — количество новых строк
        """
        return self.download(symbol, interval, years=3.0, workers=workers)

    def update_all(
        self,
        symbols: Optional[List[str]] = None,
        intervals: Optional[List[str]] = None,
        workers: int = 4,
    ) -> Dict[str, int]:
        """Инкрементальное обновление всех монет/TF."""
        return self.download_many(
            symbols=symbols,
            intervals=intervals,
            years=3.0,
            workers=workers,
        )

    # ------------------------------------------------------------------
    # Coverage & listing
    # ------------------------------------------------------------------

    def coverage(
        self, symbol: str, interval: str
    ) -> Optional[Tuple[datetime, datetime, int]]:
        """
        Возвращает (start_date, end_date, n_candles) или None если нет данных.
        """
        return self._lake.get_coverage(symbol, interval)

    def list_available(self) -> pd.DataFrame:
        """
        Таблица всех доступных данных.

        Returns
        -------
        pd.DataFrame с колонками:
            symbol, interval, tf_label, min_date, max_date,
            rows, size_mb, coverage_pct
        """
        summary = self._lake.get_catalog_summary()
        summary = summary[summary["coverage_pct"] > 0].copy()
        summary["tf_label"] = summary["interval"].map(TF_LABELS)
        return summary.sort_values(["symbol", "interval"]).reset_index(drop=True)

    def is_available(self, symbol: str, interval: str) -> bool:
        """Проверить что данные есть в lake."""
        return self._lake.get_coverage(symbol, interval) is not None

    def get_lake_size_mb(self) -> float:
        """Суммарный размер lake в MB."""
        return self._lake.get_lake_size_bytes() / 1_048_576

    def get_lake_manager(self) -> DataLakeManager:
        """Прямой доступ к DataLakeManager для расширенных операций."""
        return self._lake

    # ------------------------------------------------------------------
    # Chunked read (для больших диапазонов)
    # ------------------------------------------------------------------

    def iter_chunks(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        chunk_rows: int = 50_000,
        overlap_rows: int = 200,
    ):
        """
        Итератор по чанкам данных — для RAM-efficient обработки больших диапазонов.

        Каждый чанк перекрывается с предыдущим на `overlap_rows` строк для
        корректности rolling z-scores на границах.

        Usage:
            for chunk_df in hm.iter_chunks("BTCUSDT", "5", start, end):
                features = build_features_df(chunk_df)
                impulses = detect_impulses_from_df(features, config)

        Yields
        ------
        pd.DataFrame — очередной чанк (включая overlap с предыдущим)
        """
        df = self.load(symbol, interval, start, end)
        if df.empty:
            return

        n = len(df)
        pos = 0
        while pos < n:
            chunk_start = max(0, pos - overlap_rows)
            chunk_end = min(n, pos + chunk_rows)
            chunk = df.iloc[chunk_start:chunk_end].reset_index(drop=True)
            yield chunk
            pos = chunk_end
            if chunk_end >= n:
                break

    def print_status(self) -> None:
        """Печатает статус lake в консоль."""
        avail = self.list_available()
        size_mb = self.get_lake_size_mb()
        print(f"\n{'='*60}")
        print(f"  Data Lake Status")
        print(f"  Size: {size_mb:.1f} MB | Root: {self._lake.root}")
        print(f"{'='*60}")
        if avail.empty:
            print("  No data downloaded yet.")
        else:
            print(avail[["symbol", "tf_label", "min_date", "max_date", "rows", "size_mb", "coverage_pct"]].to_string(index=False))
        print(f"{'='*60}\n")
