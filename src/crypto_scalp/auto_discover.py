"""
auto_discover.py — Автоматическое обнаружение торговых гипотез

Два режима:
  auto_discover_hypotheses()        — классический (CSV, малые данные)
  auto_discover_hypotheses_big()    — chunked поверх Data Lake (3 года, RAM-efficient)
"""

import json
import re
from pathlib import Path
from typing import Any, List, Optional

import pandas as pd

from .quant_brick import build_bricks_from_ohlcv, build_features_df
from .impulse_detector import detect_impulses, get_impulse_context_comparison, ImpulseDetectorConfig
from .hypothesis_vault import HypothesisRecord, add_hypothesis, DEFAULT_DB_PATH


def _load_data_smart(data_path: Path) -> pd.DataFrame:
    """Загружает данные из CSV, Parquet или data_lake:// URI."""
    path_str = str(data_path)
    
    # Handle data_lake:// URIs
    if path_str.startswith("data_lake://"):
        try:
            from .data_manager import HistoryManager
            parts = path_str.replace("data_lake://", "").strip("/").split("/")
            symbol = parts[0] if len(parts) > 0 else ""
            interval = parts[1] if len(parts) > 1 else "5"
            hm = HistoryManager()
            return hm.load(symbol, interval)
        except Exception as e:
            print(f"Failed to load from Data Lake: {e}")
            return pd.DataFrame()
    
    p = Path(data_path)
    if not p.exists():
        print(f"File not found: {data_path}")
        return pd.DataFrame()
    
    try:
        if p.suffix == ".parquet":
            return pd.read_parquet(p)
        else:
            return pd.read_csv(p)
    except Exception as e:
        print(f"Failed to read {data_path}: {e}")
        return pd.DataFrame()


def auto_discover_hypotheses(
    data_path: Path,
    client: Any,
    symbol: str,
    limit: int = 5,
    db_path: Path = DEFAULT_DB_PATH
) -> List[int]:
    """
    Автоматически находит аномальные квантовые импульсы в данных 
    и генерирует для них торговые гипотезы с помощью LLM (Gemini/Ollama).

    ⚠️ Классический режим — работает с CSV файлами (≤100k строк).
    Для больших данных (3 года) используй auto_discover_hypotheses_big().
    """
    print(f"Loading data from {data_path}...")
    df = _load_data_smart(data_path)
    if df.empty:
        print(f"No data loaded from {data_path}")
        return []
    
    print("Building quantum bricks and features...")
    bricks = build_bricks_from_ohlcv(df)
    
    print("Detecting impulses (anomalies)...")
    config = ImpulseDetectorConfig()
    impulses = detect_impulses(bricks, config)
    
    if not impulses:
        print("No impulses found in the dataset.")
        return []
        
    # Сортируем импульсы по пиковой энергии (самые мощные аномалии сверху)
    impulses.sort(key=lambda x: x.peak_energy, reverse=True)
    top_impulses = impulses[:limit]
    
    print(f"Found {len(impulses)} total impulses. Analyzing top {len(top_impulses)} by peak energy...")
    
    # Пытаемся извлечь таймфрейм из имени файла
    tf_match = re.search(r'_(\d+[A-Za-z]*)_', data_path.name)
    timeframe = f"{tf_match.group(1)}m" if (tf_match and tf_match.group(1).isdigit()) else "1m"
    if tf_match and not tf_match.group(1).isdigit():
        timeframe = tf_match.group(1)

    return _process_impulses(
        top_impulses=top_impulses,
        bricks=bricks,
        client=client,
        symbol=symbol,
        timeframe=timeframe,
        data_path=str(data_path.resolve()),
        db_path=db_path,
    )


def auto_discover_hypotheses_big(
    symbol: str,
    interval: str,
    client: Any,
    limit: int = 10,
    chunk_size: int = 50_000,
    overlap: int = 200,
    lookback: int = 80,
    db_path: Path = DEFAULT_DB_PATH,
    history_manager=None,
) -> List[int]:
    """
    Chunked поиск гипотез поверх Data Lake — RAM-efficient, для 3 лет истории.

    Обрабатывает данные кусками по chunk_size свечей с overlap для корректности
    rolling z-scores на границах чанков. Находит ВСЕ импульсы за 3 года,
    затем выбирает top N по пиковой энергии.

    Parameters
    ----------
    symbol : str       (напр. "BTCUSDT")
    interval : str     (напр. "5" = 5m)
    client : Any       HybridAIClient / OllamaClient
    limit : int        Топ N импульсов для анализа
    chunk_size : int   Свечей в одном чанке (~50k = ~2 MB RAM)
    overlap : int      Перекрытие с предыдущим чанком (для z-scores)
    lookback : int     Окно rolling z-score
    db_path : Path     Путь к базе гипотез
    history_manager    HistoryManager | None (создаётся автоматически)

    Returns
    -------
    list[int] — IDs сохранённых гипотез
    """
    # Ленивый импорт чтобы избежать циклических зависимостей
    if history_manager is None:
        from .data_manager import HistoryManager
        history_manager = HistoryManager()

    print(f"[AutoDiscover-Big] Loading {symbol}/{interval} from Data Lake...")
    df_full = history_manager.load(symbol, interval)

    if df_full.empty:
        print(f"[AutoDiscover-Big] No data for {symbol}/{interval}. Run download first.")
        return []

    n_total = len(df_full)
    print(f"[AutoDiscover-Big] Loaded {n_total:,} candles. Processing in chunks of {chunk_size:,}...")

    config = ImpulseDetectorConfig()
    all_impulses = []
    all_bricks = []

    pos = 0
    chunk_idx = 0
    offset = 0  # смещение индексов для объединения всех чанков

    while pos < n_total:
        chunk_start = max(0, pos - overlap)
        chunk_end = min(n_total, pos + chunk_size)
        chunk_df = df_full.iloc[chunk_start:chunk_end].reset_index(drop=True)
        chunk_idx += 1

        print(f"[AutoDiscover-Big] Chunk {chunk_idx}: rows {chunk_start}–{chunk_end} ({len(chunk_df):,} candles)")

        # Векторизованный feature engine (быстро)
        features_df = build_features_df(chunk_df, lookback=lookback)

        if features_df.empty:
            pos = chunk_end
            continue

        # Конвертируем в bricks только для detect_impulses (обратная совместимость)
        from .quant_brick import QuantBrick
        bricks = [
            QuantBrick(
                index=int(i) + chunk_start + offset,
                timestamp=row["timestamp"],
                duration_seconds=60,
                price_open=float(row["open"]),
                price_close=float(row["close"]),
                price_high=float(row["high"]),
                price_low=float(row["low"]),
                price_change_pct=float(row["price_change_pct"]),
                price_change_abs=float(row["price_change_abs"]),
                range_pct=float(row["range_pct"]),
                body_pct=float(row["body_pct"]),
                volume=float(row["volume"]),
                dollar_volume=float(row["dollar_volume"]),
                volume_z=float(row["volume_z"]),
                dollar_volume_z=float(row["dollar_volume_z"]),
                price_change_z=float(row["price_change_z"]),
                range_z=float(row["range_z"]),
                energy=float(row["energy"]),
                direction=int(row["direction"]),
                brick_class=str(row["brick_class"]),
            )
            for i, row in features_df.iterrows()
        ]

        impulses = detect_impulses(bricks, config)

        # Пропускаем импульсы в overlap-зоне (они уже были в предыдущем чанке)
        if pos > 0 and overlap > 0:
            impulses = [imp for imp in impulses if imp.start_index >= chunk_start + overlap]

        all_bricks.extend(bricks)
        all_impulses.extend(impulses)

        pos = chunk_end

    print(f"[AutoDiscover-Big] Total impulses found: {len(all_impulses)} across {chunk_idx} chunks")

    if not all_impulses:
        print("[AutoDiscover-Big] No impulses found in the dataset.")
        return []

    # Топ N по пиковой энергии
    all_impulses.sort(key=lambda x: x.peak_energy, reverse=True)
    top_impulses = all_impulses[:limit]

    # Маппинг brick_index → brick object
    brick_map = {b.index: b for b in all_bricks}

    tf_label_map = {"1": "1m", "5": "5m", "15": "15m", "60": "1h", "240": "4h", "D": "1D"}
    timeframe = tf_label_map.get(interval, f"{interval}m")

    print(f"[AutoDiscover-Big] Analyzing top {len(top_impulses)} impulses via LLM...")
    return _process_impulses(
        top_impulses=top_impulses,
        bricks=all_bricks,
        client=client,
        symbol=symbol,
        timeframe=timeframe,
        data_path=f"data_lake://{symbol}/{interval}/3yr",
        db_path=db_path,
    )


def _process_impulses(
    top_impulses: list,
    bricks: list,
    client: Any,
    symbol: str,
    timeframe: str,
    data_path: str,
    db_path: Path,
) -> List[int]:
    """Общий код анализа импульсов через LLM и сохранения гипотез."""
    saved_ids = []

    for i, impulse in enumerate(top_impulses):
        print(f"\n[{i+1}/{len(top_impulses)}] Analyzing impulse at index {impulse.start_index} "
              f"(Energy: {impulse.peak_energy:.2f})")
        
        try:
            context = get_impulse_context_comparison(impulse, bricks)
        except Exception as e:
            print(f"  Context extraction failed: {e}")
            context = {}

        system_prompt = (
            "You are an elite quantitative crypto trader specializing in high-frequency scalping. "
            "Your task is to analyze an anomalous volume and price spike (an impulse) and formulate "
            "a concise, actionable trading hypothesis. Focus on market microstructure, stop-loss hunting, "
            "and orderbook dynamics. Return ONLY the hypothesis text in Russian. Do not include markdown blocks."
        )
        
        direction_str = "LONG" if impulse.direction > 0 else "SHORT"
        
        prompt = (
            f"Symbol: {symbol}\n"
            f"Impulse Direction: {direction_str}\n"
            f"Duration: {impulse.duration_bricks} candles ({impulse.duration_seconds}s)\n"
            f"Total Price Move: {impulse.total_price_move_pct:.3f}%\n"
            f"Max Favorable Move: {impulse.max_favorable_pct:.3f}%\n"
            f"Max Adverse Move: {impulse.max_adverse_pct:.3f}%\n"
            f"Peak Energy: {impulse.peak_energy:.2f}\n\n"
            f"Context Data (JSON metrics comparing the impulse to the pre-impulse flat):\n"
            f"{json.dumps(context, indent=2)}\n\n"
            "Based on the metrics above, explain why this impulse likely happened and suggest a trading "
            "hypothesis (e.g., 'Continuation pattern with tight SL below the anomaly candle', or "
            "'Mean reversion after a stop-hunt'). Keep it under 5 sentences."
        )
        
        try:
            thesis = client.chat(prompt=prompt, system=system_prompt)
            print(f"  Generated thesis:\n  {thesis[:200]}...\n")
            
            evidence = json.dumps({
                "impulse_id": impulse.impulse_id,
                "start_idx": impulse.start_index,
                "end_idx": impulse.end_index,
                "peak_energy": impulse.peak_energy,
                "direction": direction_str
            }, ensure_ascii=False)
            
            record = HypothesisRecord(
                title=f"Auto Anomaly: {direction_str} {impulse.peak_energy:.1f}E",
                thesis=thesis.strip(),
                evidence=evidence,
                tags="auto-generated, anomaly",
                symbol=symbol,
                timeframe=timeframe,
                status="new",
                score=impulse.peak_energy,
                data_path=data_path,
            )
            
            hyp_id = add_hypothesis(record=record, db_path=db_path, client=client)
            saved_ids.append(hyp_id)
            print(f"  ✅ Saved as Hypothesis #{hyp_id}")
            
        except Exception as e:
            print(f"  ❌ Failed to analyze impulse: {e}")
            
    return saved_ids
