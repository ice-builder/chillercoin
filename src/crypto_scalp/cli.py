from __future__ import annotations

import argparse
import subprocess
from datetime import datetime
import json
from pathlib import Path
import sys

from . import research_app

from .backtest import run_backtest
from .bybit import find_reusable_bybit_csv, save_bybit_klines_csv
from .bybit_live import stream_bybit_public_trades
from .config import RunConfig
from .data import make_demo_data
from .hypothesis_vault import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_DB_PATH,
    HypothesisRecord,
    add_hypothesis,
    init_local_ai,
    list_hypotheses,
    list_summary_memories,
    search_hypotheses,
    synthesize_hypotheses_memory,
)
from .ollama_local import OllamaClient, OllamaConfig, load_ollama_config
from .vertex_client import HybridAIClient
from .auto_discover import auto_discover_hypotheses
from .paper_trading import run_paper_trading
from .train import run_training


def main() -> None:
    parser = argparse.ArgumentParser(description="Local crypto futures scalp model")
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("make-demo-data", help="Create synthetic OHLCV dataset")
    demo.add_argument("--output", type=Path, required=True)
    demo.add_argument("--rows", type=int, default=5000)

    train = subparsers.add_parser("train", help="Train the model on OHLCV CSV")
    train.add_argument("--data", type=Path, required=True)
    train.add_argument("--artifacts", type=Path, required=True)

    backtest = subparsers.add_parser("backtest", help="Run backtest from saved model")
    backtest.add_argument("--data", type=Path, required=True)
    backtest.add_argument("--artifacts", type=Path, required=True)

    research = subparsers.add_parser("research-app", help="Launch interactive candle research UI")
    research.add_argument("--data", type=Path, required=False)
    research.add_argument("--artifacts", type=Path, required=False)
    research.add_argument("--port", type=int, default=8501)

    bybit = subparsers.add_parser("download-bybit", help="Download Bybit futures candles to CSV")
    bybit.add_argument("--symbol", type=str, required=True)
    bybit.add_argument("--interval", type=str, required=True)
    bybit.add_argument("--start", type=str, required=True, help="UTC date/time, example: 2026-04-01 or 2026-04-01T12:00:00")
    bybit.add_argument("--end", type=str, required=True, help="UTC date/time, example: 2026-04-16 or 2026-04-16T12:00:00")
    bybit.add_argument("--output", type=Path, required=True)

    collect_trades = subparsers.add_parser("collect-bybit-trades", help="Collect Bybit public trades for optional local aggregation")
    collect_trades.add_argument("--symbol", type=str, required=True)
    collect_trades.add_argument("--duration-seconds", type=int, default=300)
    collect_trades.add_argument("--category", type=str, default="linear")
    collect_trades.add_argument("--output-trades", type=Path, required=False)
    collect_trades.add_argument("--output-1s", type=Path, required=False)

    watch_live = subparsers.add_parser("watch-bybit-live", help="Collect Bybit public trades and emit live matcher signals")
    watch_live.add_argument("--symbol", type=str, required=True)
    watch_live.add_argument("--duration-seconds", type=int, default=300)
    watch_live.add_argument("--category", type=str, default="linear")
    watch_live.add_argument("--output-trades", type=Path, required=False)
    watch_live.add_argument("--output-1s", type=Path, required=False)
    watch_live.add_argument("--output-signals", type=Path, required=False)

    paper_live = subparsers.add_parser("paper-trade-live", help="Run realtime paper trading from queued hypotheses")
    paper_live.add_argument("--symbol", type=str, required=True)
    paper_live.add_argument("--duration-seconds", type=int, default=300)
    paper_live.add_argument("--deposit-usdt", type=float, default=1000.0)
    paper_live.add_argument("--category", type=str, default="linear")
    paper_live.add_argument("--output", type=Path, required=False)

    ai_init = subparsers.add_parser("ai-init", help="Initialize local AI workspace and hypothesis vault")
    ai_init.add_argument("--reasoning-model", type=str, default="qwen3:4b")
    ai_init.add_argument("--embedding-model", type=str, default="embeddinggemma")
    ai_init.add_argument("--host", type=str, default="http://127.0.0.1:11434")

    ai_pull = subparsers.add_parser("ai-pull", help="Pull an Ollama model into local machine")
    ai_pull.add_argument("--model", type=str, required=True)
    ai_pull.add_argument("--host", type=str, default="http://127.0.0.1:11434")

    ai_status = subparsers.add_parser("ai-status", help="Show Ollama models available locally")
    ai_status.add_argument("--host", type=str, default="http://127.0.0.1:11434")

    hypothesis_add = subparsers.add_parser("hypothesis-add", help="Add a hypothesis into local vault")
    hypothesis_add.add_argument("--title", type=str, required=True)
    hypothesis_add.add_argument("--thesis", type=str, required=True)
    hypothesis_add.add_argument("--evidence", type=str, default="")
    hypothesis_add.add_argument("--tags", type=str, default="")
    hypothesis_add.add_argument("--symbol", type=str, default="")
    hypothesis_add.add_argument("--timeframe", type=str, default="")
    hypothesis_add.add_argument("--status", type=str, default="new")
    hypothesis_add.add_argument("--score", type=float, default=0.0)
    hypothesis_add.add_argument("--no-embed", action="store_true")

    hypothesis_list = subparsers.add_parser("hypothesis-list", help="List latest hypotheses")
    hypothesis_list.add_argument("--limit", type=int, default=20)

    summary_memory_list = subparsers.add_parser("summary-memory-list", help="List saved summary memories")
    summary_memory_list.add_argument("--limit", type=int, default=20)
    summary_memory_list.add_argument("--symbol", type=str, default="")
    summary_memory_list.add_argument("--timeframe", type=str, default="")
    summary_memory_list.add_argument("--type", type=str, default="")

    hypothesis_search = subparsers.add_parser("hypothesis-search", help="Search hypotheses with embeddings")
    hypothesis_search.add_argument("--query", type=str, required=True)
    hypothesis_search.add_argument("--top-k", type=int, default=5)
    hypothesis_search.add_argument("--no-embed", action="store_true")

    hypothesis_synthesize = subparsers.add_parser("hypothesis-synthesize", help="Summarize matching hypotheses with local LLM")
    hypothesis_synthesize.add_argument("--query", type=str, required=True)
    hypothesis_synthesize.add_argument("--top-k", type=int, default=5)
    hypothesis_synthesize.add_argument("--batch-size", type=int, default=4)
    hypothesis_synthesize.add_argument("--symbol", type=str, default="")
    hypothesis_synthesize.add_argument("--timeframe", type=str, default="")
    hypothesis_synthesize.add_argument("--status", type=str, default="")
    hypothesis_synthesize.add_argument("--json", action="store_true")

    auto_discover = subparsers.add_parser("auto-discover", help="Auto-generate hypotheses from quantum anomalies")
    auto_discover.add_argument("--data", type=Path, required=True, help="Path to OHLCV CSV file")
    auto_discover.add_argument("--symbol", type=str, required=True)
    auto_discover.add_argument("--limit", type=int, default=5, help="Number of top anomalies to analyze")
    auto_discover.add_argument("--no-embed", action="store_true", help="Disable local embeddings")

    args = parser.parse_args()
    config = RunConfig()

    if args.command == "make-demo-data":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        df = make_demo_data(rows=args.rows)
        df.to_csv(args.output, index=False)
        print(f"Saved demo data to {args.output}")
        return

    if args.command == "train":
        summary = run_training(args.data, args.artifacts, config)
        print(summary)
        return

    if args.command == "backtest":
        summary = run_backtest(args.data, args.artifacts, config)
        print(summary)
        return

    if args.command == "research-app":
        workspace_app_path = Path.cwd() / "src" / "crypto_scalp" / "research_app.py"
        app_path = workspace_app_path if workspace_app_path.exists() else Path(research_app.__file__).resolve()
        command = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(app_path),
            "--server.port",
            str(args.port),
        ]
        if args.data:
            command.extend(["--", "--data", str(args.data)])
            if args.artifacts:
                command.extend(["--artifacts", str(args.artifacts)])
        elif args.artifacts:
            command.extend(["--", "--artifacts", str(args.artifacts)])

        subprocess.run(command, check=True)
        return

    if args.command == "download-bybit":
        start = parse_datetime(args.start)
        end = parse_datetime(args.end)
        cache_hit = find_reusable_bybit_csv(
            root=Path.cwd(),
            symbol=args.symbol,
            interval=args.interval,
            start=start,
            end=end,
        )
        if cache_hit is not None:
            print(f"Using cached Bybit candles from {cache_hit.path}")
            return
        output_path = save_bybit_klines_csv(
            output_path=args.output,
            symbol=args.symbol,
            interval=args.interval,
            start=start,
            end=end,
        )
        print(f"Saved Bybit candles to {output_path}")
        return

    if args.command == "collect-bybit-trades":
        summary = stream_bybit_public_trades(
            symbol=args.symbol,
            duration_seconds=args.duration_seconds,
            root=Path.cwd(),
            category=args.category,
            matcher_root=Path.cwd() / "__disabled_matchers__",
            trades_output_path=args.output_trades,
            seconds_output_path=args.output_1s,
        )
        print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
        return

    if args.command == "watch-bybit-live":
        summary = stream_bybit_public_trades(
            symbol=args.symbol,
            duration_seconds=args.duration_seconds,
            root=Path.cwd(),
            category=args.category,
            trades_output_path=args.output_trades,
            seconds_output_path=args.output_1s,
            signals_output_path=args.output_signals,
        )
        print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
        return

    if args.command == "paper-trade-live":
        summary = run_paper_trading(
            root=Path.cwd(),
            symbol=args.symbol,
            duration_seconds=args.duration_seconds,
            deposit_usdt=args.deposit_usdt,
            category=args.category,
            output_path=args.output,
        )
        print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
        return

    if args.command == "ai-init":
        init_local_ai(
            config_path=DEFAULT_CONFIG_PATH,
            db_path=DEFAULT_DB_PATH,
            ollama_config=OllamaConfig(
                host=args.host,
                reasoning_model=args.reasoning_model,
                embedding_model=args.embedding_model,
            ),
        )
        print(f"Initialized local AI workspace in {DEFAULT_CONFIG_PATH.parent}")
        return

    if args.command == "ai-pull":
        client = OllamaClient(OllamaConfig(host=args.host))
        payload = client.pull(args.model)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if args.command == "ai-status":
        client = OllamaClient(OllamaConfig(host=args.host))
        payload = client.tags()
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if args.command == "hypothesis-add":
        client = None if args.no_embed else HybridAIClient(load_ollama_config(DEFAULT_CONFIG_PATH))
        hypothesis_id = add_hypothesis(
            record=HypothesisRecord(
                title=args.title,
                thesis=args.thesis,
                evidence=args.evidence,
                tags=args.tags,
                symbol=args.symbol,
                timeframe=args.timeframe,
                status=args.status,
                score=args.score,
            ),
            client=client,
        )
        print(f"Saved hypothesis #{hypothesis_id}")
        return

    if args.command == "hypothesis-list":
        print(json.dumps(list_hypotheses(limit=args.limit), indent=2, ensure_ascii=False))
        return

    if args.command == "summary-memory-list":
        print(
            json.dumps(
                list_summary_memories(
                    limit=args.limit,
                    symbol=args.symbol,
                    timeframe=args.timeframe,
                    memory_type=args.type,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    if args.command == "hypothesis-search":
        client = None if args.no_embed else HybridAIClient(load_ollama_config(DEFAULT_CONFIG_PATH))
        results = search_hypotheses(query=args.query, client=client, top_k=args.top_k)
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    if args.command == "hypothesis-synthesize":
        client = HybridAIClient(load_ollama_config(DEFAULT_CONFIG_PATH))
        result = synthesize_hypotheses_memory(
            query=args.query,
            client=client,
            retrieval_k=args.top_k,
            batch_size=args.batch_size,
            symbol=args.symbol,
            timeframe=args.timeframe,
            status=args.status,
        )
        if args.json:
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(result.final_answer)
        return

    if args.command == "auto-discover":
        client = None if args.no_embed else HybridAIClient(load_ollama_config(DEFAULT_CONFIG_PATH))
        saved_ids = auto_discover_hypotheses(
            data_path=args.data,
            client=client,
            symbol=args.symbol,
            limit=args.limit
        )
        print(f"Auto-discovery completed. Saved {len(saved_ids)} hypotheses.")
        return


def parse_datetime(raw: str) -> datetime:
    if "T" in raw:
        return datetime.fromisoformat(raw)
    return datetime.fromisoformat(f"{raw}T00:00:00")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# history-sync command (added by Big Data Infrastructure)
# ---------------------------------------------------------------------------

def _cmd_history_sync(args) -> None:
    """Синхронизация исторических данных с Bybit → Data Lake."""
    import time as _time
    from .data_manager import HistoryManager
    from .bulk_downloader import build_default_jobs, run_bulk_download, BulkProgress, DownloadResult
    from .data_lake import SUPPORTED_COINS, SUPPORTED_INTERVALS

    lake_path = Path(args.lake_path) if getattr(args, "lake_path", None) else None
    hm = HistoryManager(root=lake_path)

    if getattr(args, "status", False):
        hm.print_status()
        return

    # Определяем монеты
    if getattr(args, "all", False) or not getattr(args, "symbols", None):
        symbols = SUPPORTED_COINS
    else:
        symbols = [s.upper() if not s.endswith("USDT") else s.upper() for s in args.symbols]

    # Определяем TF
    tf_map = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}
    if getattr(args, "timeframes", None):
        intervals = [tf_map.get(t.lower(), t) for t in args.timeframes]
    else:
        intervals = SUPPORTED_INTERVALS

    years = getattr(args, "years", 3.0)
    workers = getattr(args, "workers", 4)

    print(f"\n🚀 History Sync — {len(symbols)} coins × {len(intervals)} TF × {years}y")
    print(f"   Mode: {'UPDATE' if getattr(args, 'update', False) else 'FULL'}")
    print(f"   Workers: {workers}")
    print()

    _last = [0.0]
    def cb(progress: BulkProgress, result: DownloadResult):
        now = _time.time()
        if now - _last[0] < 3.0:
            return
        _last[0] = now
        print(
            f"\r  [{progress.completed_jobs}/{progress.total_jobs}] "
            f"{progress.percent:.1f}% | {progress.current_symbol}/{progress.current_interval}"
            f" | {progress.rows_downloaded_total:,} rows",
            end="", flush=True,
        )

    jobs = build_default_jobs(symbols=symbols, intervals=intervals, years=years)
    results = run_bulk_download(jobs=jobs, lake=hm.get_lake_manager(), workers=workers, progress_cb=cb)
    print()

    success = sum(1 for r in results if r.success)
    rows = sum(r.rows_written for r in results)
    print(f"\n✅ Done: {success}/{len(results)} jobs | {rows:,} rows | {hm.get_lake_size_mb():.1f} MB")
    hm.print_status()

