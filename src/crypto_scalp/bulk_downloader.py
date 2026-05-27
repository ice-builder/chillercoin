"""
bulk_downloader.py — Параллельный загрузчик истории с Bybit

Функции:
- BulkDownloadJob: задание на скачку (symbol, interval, start, end)
- run_bulk_download(): запускает N потоков, пишет данные в DataLakeManager
- Retry с exponential backoff
- Rate-limit safe (sleep между запросами)
- Инкрементальная логика: пропускает уже скачанные куски
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

import requests

from .bybit import download_bybit_klines, INTERVAL_TO_DELTA
from .data_lake import DataLakeManager, SUPPORTED_COINS, SUPPORTED_INTERVALS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

DEFAULT_CHUNK_DAYS = {
    "1":   7,    # 1m  → 7-дневные куски (~10 080 свечей)
    "5":   30,   # 5m  → 30 дней
    "15":  60,   # 15m → 60 дней
    "60":  180,  # 1h  → полгода
    "240": 365,  # 4h  → год
    "D":   365,  # 1D  → год
}

REQUEST_DELAY_SEC = 0.25   # задержка между запросами к API
MAX_RETRIES = 3            # попыток на чанк
RETRY_BASE_SEC = 2.0       # базовая задержка для backoff


# ---------------------------------------------------------------------------
# Структуры данных
# ---------------------------------------------------------------------------

@dataclass
class BulkDownloadJob:
    symbol: str
    interval: str
    start: datetime
    end: datetime
    category: str = "linear"


@dataclass
class DownloadResult:
    job: BulkDownloadJob
    success: bool
    rows_written: int = 0
    error: Optional[str] = None
    duration_sec: float = 0.0


@dataclass
class BulkProgress:
    total_jobs: int
    completed_jobs: int
    current_symbol: str
    current_interval: str
    current_chunk_start: Optional[datetime]
    current_chunk_end: Optional[datetime]
    rows_downloaded_total: int
    errors: List[str] = field(default_factory=list)

    @property
    def percent(self) -> float:
        if self.total_jobs == 0:
            return 0.0
        return self.completed_jobs / self.total_jobs * 100


# ---------------------------------------------------------------------------
# Внутренние функции
# ---------------------------------------------------------------------------

def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _download_chunk_with_retry(
    symbol: str,
    interval: str,
    chunk_start: datetime,
    chunk_end: datetime,
    category: str,
    session: requests.Session,
) -> int:
    """Скачать один чанк с retry. Возвращает количество скачанных строк."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            df = download_bybit_klines(
                symbol=symbol,
                interval=interval,
                start=chunk_start,
                end=chunk_end,
                category=category,
                session=session,
            )
            return df, attempt
        except Exception as e:
            if attempt == MAX_RETRIES:
                raise
            wait = RETRY_BASE_SEC * (2 ** (attempt - 1))
            logger.warning(
                "[%s/%s] Attempt %d failed: %s. Retrying in %.1fs...",
                symbol, interval, attempt, e, wait,
            )
            time.sleep(wait)
    return None, MAX_RETRIES  # unreachable


def _download_job(
    job: BulkDownloadJob,
    lake: DataLakeManager,
    progress_cb: Optional[Callable[[BulkProgress, DownloadResult], None]],
    total_jobs: int,
    job_index: int,
    shared_state: Dict,
) -> DownloadResult:
    """Скачать одно задание (symbol × interval), записать в lake."""
    t0 = time.time()
    symbol = job.symbol
    interval = job.interval
    start = _ensure_utc(job.start)
    end = _ensure_utc(job.end)

    # Определить отсутствующие диапазоны (инкрементальная логика)
    missing_ranges = lake.get_missing_ranges(symbol, interval, start, end)
    if not missing_ranges:
        logger.info("[%s/%s] Already up-to-date, skipping.", symbol, interval)
        result = DownloadResult(job=job, success=True, rows_written=0, duration_sec=0.0)
        return result

    chunk_days = DEFAULT_CHUNK_DAYS.get(interval, 30)
    session = requests.Session()
    total_rows = 0

    for range_start, range_end in missing_ranges:
        current = range_start
        while current < range_end:
            chunk_end = min(current + timedelta(days=chunk_days), range_end)

            try:
                df, _ = _download_chunk_with_retry(
                    symbol=symbol,
                    interval=interval,
                    chunk_start=current,
                    chunk_end=chunk_end,
                    category=job.category,
                    session=session,
                )
            except Exception as e:
                err_msg = f"[{symbol}/{interval}] Failed chunk {current:%Y-%m-%d}..{chunk_end:%Y-%m-%d}: {e}"
                logger.error(err_msg)
                shared_state.setdefault("errors", []).append(err_msg)
                current = chunk_end + INTERVAL_TO_DELTA.get(interval, timedelta(minutes=1))
                time.sleep(REQUEST_DELAY_SEC)
                continue

            if df is not None and not df.empty:
                lake.write_dataframe(symbol, interval, df)
                total_rows += len(df)
                logger.info(
                    "[%s/%s] +%d rows (%s → %s)",
                    symbol, interval, len(df),
                    current.strftime("%Y-%m-%d"),
                    chunk_end.strftime("%Y-%m-%d"),
                )

            # Обновить прогресс
            if progress_cb:
                shared_state["rows_total"] = shared_state.get("rows_total", 0) + (len(df) if df is not None else 0)
                progress = BulkProgress(
                    total_jobs=total_jobs,
                    completed_jobs=shared_state.get("completed", 0),
                    current_symbol=symbol,
                    current_interval=interval,
                    current_chunk_start=current,
                    current_chunk_end=chunk_end,
                    rows_downloaded_total=shared_state.get("rows_total", 0),
                    errors=shared_state.get("errors", []),
                )
                result_so_far = DownloadResult(job=job, success=True, rows_written=total_rows)
                try:
                    progress_cb(progress, result_so_far)
                except Exception:
                    pass

            current = chunk_end + INTERVAL_TO_DELTA.get(interval, timedelta(minutes=1))
            time.sleep(REQUEST_DELAY_SEC)

    duration = time.time() - t0
    shared_state["completed"] = shared_state.get("completed", 0) + 1
    result = DownloadResult(
        job=job,
        success=True,
        rows_written=total_rows,
        duration_sec=duration,
    )
    logger.info(
        "[%s/%s] Done: %d rows in %.1fs",
        symbol, interval, total_rows, duration,
    )
    return result


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------

def build_default_jobs(
    symbols: Optional[List[str]] = None,
    intervals: Optional[List[str]] = None,
    years: float = 3.0,
    end: Optional[datetime] = None,
) -> List[BulkDownloadJob]:
    """Собрать список заданий для закачки."""
    symbols = symbols or SUPPORTED_COINS
    intervals = intervals or SUPPORTED_INTERVALS
    end = end or datetime.now(timezone.utc)
    start = end - timedelta(days=int(365 * years))

    jobs = []
    for symbol in symbols:
        for interval in intervals:
            jobs.append(BulkDownloadJob(
                symbol=symbol,
                interval=interval,
                start=start,
                end=end,
            ))
    return jobs


def run_bulk_download(
    jobs: List[BulkDownloadJob],
    lake: Optional[DataLakeManager] = None,
    workers: int = 4,
    progress_cb: Optional[Callable[[BulkProgress, DownloadResult], None]] = None,
) -> List[DownloadResult]:
    """
    Запустить параллельную загрузку по списку заданий.

    Args:
        jobs: список BulkDownloadJob
        lake: DataLakeManager (создаётся автоматически если None)
        workers: кол-во параллельных потоков (рекомендуется 3-4)
        progress_cb: колбэк(BulkProgress, DownloadResult) вызывается после каждого чанка

    Returns:
        Список DownloadResult для каждого задания
    """
    if lake is None:
        lake = DataLakeManager()

    total = len(jobs)
    shared_state: Dict = {"completed": 0, "rows_total": 0, "errors": []}
    results: List[DownloadResult] = []

    logger.info("Starting bulk download: %d jobs, %d workers", total, workers)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _download_job,
                job=job,
                lake=lake,
                progress_cb=progress_cb,
                total_jobs=total,
                job_index=i,
                shared_state=shared_state,
            ): job
            for i, job in enumerate(jobs)
        }

        for future in as_completed(futures):
            job = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                err = f"[{job.symbol}/{job.interval}] Unhandled error: {e}"
                logger.exception(err)
                results.append(DownloadResult(
                    job=job,
                    success=False,
                    error=str(e),
                ))
                shared_state.setdefault("errors", []).append(err)
                shared_state["completed"] = shared_state.get("completed", 0) + 1

    logger.info(
        "Bulk download complete: %d/%d success, %d total rows",
        sum(1 for r in results if r.success),
        total,
        sum(r.rows_written for r in results),
    )
    return results
