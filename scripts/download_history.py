#!/usr/bin/env python3
"""
download_history.py — CLI для скачки исторических данных в Data Lake

Примеры использования:
  # Скачать всё (20 монет × 6 TF × 3 года)
  python scripts/download_history.py --all

  # Только отдельные монеты и таймфреймы
  python scripts/download_history.py --coins BTC ETH SOL --tf 1 5 15

  # Инкрементальное обновление (докачать только новое)
  python scripts/download_history.py --update

  # Тест на маленьком диапазоне
  python scripts/download_history.py --coins BTC --tf 1 --years 0.1

  # Проверить статус data lake
  python scripts/download_history.py --status
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Добавляем src в Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from crypto_scalp.data_lake import DataLakeManager, SUPPORTED_COINS, SUPPORTED_INTERVALS
from crypto_scalp.bulk_downloader import (
    BulkDownloadJob,
    BulkProgress,
    DownloadResult,
    build_default_jobs,
    run_bulk_download,
)

# ---------------------------------------------------------------------------
# Настройка логирования
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("download_history")


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

SYMBOL_ALIASES = {
    "BTC":  "BTCUSDT",
    "ETH":  "ETHUSDT",
    "SOL":  "SOLUSDT",
    "XRP":  "XRPUSDT",
    "BNB":  "BNBUSDT",
    "SUI":  "SUIUSDT",
    "TRX":  "TRXUSDT",
    "TON":  "TONUSDT",
    "SHIB": "SHIB1000USDT",
    "PEPE": "1000PEPEUSDT",
    "AVAX": "AVAXUSDT",
    "DOGE": "DOGEUSDT",
    "ADA":  "ADAUSDT",
    "DOT":  "DOTUSDT",
    "POL":  "POLUSDT",
    "MATIC": "POLUSDT",
    "LINK": "LINKUSDT",
    "UNI":  "UNIUSDT",
    "LTC":  "LTCUSDT",
    "ATOM": "ATOMUSDT",
    "NEAR": "NEARUSDT",
}

TF_ALIASES = {
    "1m": "1", "1": "1",
    "5m": "5", "5": "5",
    "15m": "15", "15": "15",
    "1h": "60", "60": "60",
    "4h": "240", "240": "240",
    "1d": "D", "D": "D", "d": "D",
}


def resolve_coins(raw: list[str]) -> list[str]:
    out = []
    for s in raw:
        up = s.upper()
        if up in SYMBOL_ALIASES:
            out.append(SYMBOL_ALIASES[up])
        elif up in SUPPORTED_COINS:
            out.append(up)
        else:
            logger.warning("Unknown symbol '%s', skipping.", s)
    return out


def resolve_tfs(raw: list[str]) -> list[str]:
    out = []
    for t in raw:
        resolved = TF_ALIASES.get(t.lower(), TF_ALIASES.get(t, None))
        if resolved and resolved in SUPPORTED_INTERVALS:
            out.append(resolved)
        else:
            logger.warning("Unknown timeframe '%s', skipping.", t)
    return out


def format_size(n_bytes: int) -> str:
    if n_bytes < 1024:
        return f"{n_bytes} B"
    if n_bytes < 1_048_576:
        return f"{n_bytes/1024:.1f} KB"
    if n_bytes < 1_073_741_824:
        return f"{n_bytes/1_048_576:.1f} MB"
    return f"{n_bytes/1_073_741_824:.2f} GB"


def print_status(lake: DataLakeManager) -> None:
    """Вывести таблицу покрытия Data Lake."""
    df = lake.get_catalog_summary()
    total_size = lake.get_lake_size_bytes()

    print(f"\n{'='*70}")
    print(f"  📊 Data Lake Status — {datetime.now():%Y-%m-%d %H:%M}")
    print(f"  📁 Root: {lake.root}")
    print(f"  💾 Total size: {format_size(total_size)}")
    print(f"{'='*70}")

    # Таблица покрытия
    tf_labels = {"1": "1m", "5": "5m", "15": "15m", "60": "1h", "240": "4h", "D": "1D"}
    header = f"{'Symbol':<16}" + "".join(f"{'TF':>8}" for _ in SUPPORTED_INTERVALS)
    print(f"\n{'Symbol':<16}" + "".join(f"{tf_labels[tf]:>10}" for tf in SUPPORTED_INTERVALS))
    print("-" * (16 + 10 * len(SUPPORTED_INTERVALS)))

    for symbol in SUPPORTED_COINS:
        row_str = f"{symbol:<16}"
        for interval in SUPPORTED_INTERVALS:
            sub = df[(df["symbol"] == symbol) & (df["interval"] == interval)]
            if sub.empty or sub["coverage_pct"].iloc[0] == 0:
                row_str += f"{'—':>10}"
            else:
                pct = sub["coverage_pct"].iloc[0]
                row_str += f"{pct:>9.0f}%"
        print(row_str)

    print(f"\n  Symbols with data: {df[df['coverage_pct'] > 0]['symbol'].nunique()}/{len(SUPPORTED_COINS)}")
    print(f"  Total rows: {df['rows'].sum():,}")
    print(f"{'='*70}\n")


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------

_last_print = 0.0

def make_progress_cb(verbose: bool = True):
    def cb(progress: BulkProgress, result: DownloadResult):
        global _last_print
        now = time.time()
        if now - _last_print < 2.0 and progress.percent < 100:
            return
        _last_print = now
        sym = progress.current_symbol
        tf = progress.current_interval
        pct = progress.percent
        rows = progress.rows_downloaded_total
        done = progress.completed_jobs
        total = progress.total_jobs
        chunk_s = progress.current_chunk_start
        chunk_e = progress.current_chunk_end
        chunk_info = ""
        if chunk_s and chunk_e:
            chunk_info = f" | chunk: {chunk_s:%Y-%m-%d}→{chunk_e:%Y-%m-%d}"
        print(
            f"\r  [{done}/{total} jobs | {pct:5.1f}%] "
            f"{sym}/{tf}{chunk_info} | rows: {rows:,}",
            end="", flush=True,
        )
    return cb


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crypto Data Lake — Historical Data Downloader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--all", action="store_true", help="Скачать всё (20 монет × все TF × 3 года)")
    mode.add_argument("--update", action="store_true", help="Инкрементальное обновление (только новые данные)")
    mode.add_argument("--status", action="store_true", help="Показать статус Data Lake")
    mode.add_argument("--coins", nargs="+", metavar="COIN", help="Список монет (BTC ETH SOL ...)")

    parser.add_argument("--tf", nargs="+", default=None, metavar="TF", help="Таймфреймы (1 5 15 60 240 D)")
    parser.add_argument("--years", type=float, default=3.0, help="Глубина истории в годах (default: 3)")
    parser.add_argument("--workers", type=int, default=3, help="Потоки параллельной загрузки (default: 3)")
    parser.add_argument("--lake-path", type=str, default=None, help="Путь к Data Lake (default: auto)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Подробный лог")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Инициализация lake
    lake_path = Path(args.lake_path) if args.lake_path else None
    lake = DataLakeManager(root=lake_path)

    # --status
    if args.status:
        print_status(lake)
        return

    # Определяем монеты и TF
    if args.all or args.update:
        symbols = SUPPORTED_COINS
        intervals = resolve_tfs(args.tf) if args.tf else SUPPORTED_INTERVALS
    else:
        symbols = resolve_coins(args.coins or [])
        intervals = resolve_tfs(args.tf) if args.tf else SUPPORTED_INTERVALS

    if not symbols:
        print("❌ No valid symbols specified.")
        sys.exit(1)
    if not intervals:
        print("❌ No valid intervals specified.")
        sys.exit(1)

    print(f"\n🚀 Crypto Data Lake — Starting download")
    print(f"   Coins ({len(symbols)}): {', '.join(s.replace('USDT','') for s in symbols)}")
    print(f"   Timeframes: {', '.join(intervals)}")
    print(f"   Years: {args.years}")
    print(f"   Workers: {args.workers}")
    print(f"   Lake: {lake.root}")
    print()

    # Строим задания
    jobs = build_default_jobs(symbols=symbols, intervals=intervals, years=args.years)

    if args.update:
        # Для --update проверяем что уже есть и берём только недостающее
        print(f"   Mode: INCREMENTAL UPDATE")
    else:
        print(f"   Mode: FULL DOWNLOAD")

    print(f"   Total jobs: {len(jobs)}\n")

    t_start = time.time()
    results = run_bulk_download(
        jobs=jobs,
        lake=lake,
        workers=args.workers,
        progress_cb=make_progress_cb(verbose=args.verbose),
    )
    print()  # newline after progress

    elapsed = time.time() - t_start
    success = sum(1 for r in results if r.success)
    failed = [r for r in results if not r.success]
    total_rows = sum(r.rows_written for r in results)

    print(f"\n{'='*60}")
    print(f"  ✅ Completed in {elapsed/60:.1f} min")
    print(f"  Jobs: {success}/{len(results)} success")
    print(f"  Rows written: {total_rows:,}")
    print(f"  Lake size: {format_size(lake.get_lake_size_bytes())}")

    if failed:
        print(f"\n  ❌ Failed jobs ({len(failed)}):")
        for r in failed:
            print(f"    • {r.job.symbol}/{r.job.interval}: {r.error}")

    print(f"{'='*60}\n")

    # Итоговый статус
    print_status(lake)


if __name__ == "__main__":
    main()
