from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, List, Optional

import numpy as np

from .ollama_local import OllamaClient, OllamaConfig


DEFAULT_VAULT_DIR = Path(".local_ai")
DEFAULT_DB_PATH = DEFAULT_VAULT_DIR / "hypotheses.db"
DEFAULT_CONFIG_PATH = DEFAULT_VAULT_DIR / "config.json"


@dataclass
class HypothesisRecord:
    title: str
    thesis: str
    evidence: str = ""
    tags: str = ""
    symbol: str = ""
    timeframe: str = ""
    status: str = "new"
    score: float = 0.0
    data_path: str = ""
    window_start_idx: int = -1
    window_end_idx: int = -1
    window_start_ts: str = ""
    window_end_ts: str = ""
    strategy_params: str = ""
    validation_result: str = ""
    paper_status: str = ""


@dataclass
class MemorySynthesisResult:
    query: str
    retrieved: List[dict]
    batch_summaries: List[dict]
    final_answer: str
    applied_filters: dict
    saved_memory_ids: List[int]

    def to_dict(self) -> dict:
        return asdict(self)


def init_local_ai(
    config_path: Path = DEFAULT_CONFIG_PATH,
    db_path: Path = DEFAULT_DB_PATH,
    ollama_config: Optional[OllamaConfig] = None,
) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    config = ollama_config or OllamaConfig()
    config_path.write_text(
        json.dumps(
            {
                "ollama_host": config.host,
                "reasoning_model": config.reasoning_model,
                "embedding_model": config.embedding_model,
                "created_at": utc_now(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    with connect_db(db_path) as conn:
        ensure_schema(conn)
        conn.commit()


def add_hypothesis(
    record: HypothesisRecord,
    db_path: Path = DEFAULT_DB_PATH,
    client: Optional[Any] = None,
) -> int:
    embedding = None
    if client is not None:
        try:
            embedding = client.embed(compose_embedding_text(record))
        except Exception:
            embedding = None

    with connect_db(db_path) as conn:
        ensure_schema(conn)
        cursor = conn.execute(
            """
            INSERT INTO hypotheses (
                created_at, updated_at, title, thesis, evidence, tags, symbol, timeframe, status, score, embedding,
                data_path, window_start_idx, window_end_idx, window_start_ts, window_end_ts,
                strategy_params, validation_result, paper_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now(),
                utc_now(),
                record.title,
                record.thesis,
                record.evidence,
                record.tags,
                record.symbol,
                record.timeframe,
                record.status,
                record.score,
                json.dumps(embedding) if embedding is not None else None,
                record.data_path,
                record.window_start_idx,
                record.window_end_idx,
                record.window_start_ts,
                record.window_end_ts,
                record.strategy_params,
                record.validation_result,
                record.paper_status,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def list_hypotheses(db_path: Path = DEFAULT_DB_PATH, limit: int = 20) -> List[dict]:
    with connect_db(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT id, created_at, title, thesis, tags, symbol, timeframe, status, score
            FROM hypotheses
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_hypotheses_full(db_path: Path = DEFAULT_DB_PATH, limit: int = 200) -> List[dict]:
    with connect_db(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT id, created_at, updated_at, title, thesis, evidence, tags, symbol, timeframe, status, score, embedding,
                   data_path, window_start_idx, window_end_idx, window_start_ts, window_end_ts,
                   strategy_params, validation_result, paper_status
            FROM hypotheses
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_summary_memories(
    db_path: Path = DEFAULT_DB_PATH,
    limit: int = 50,
    symbol: str = "",
    timeframe: str = "",
    memory_type: str = "",
) -> List[dict]:
    rows = _fetch_summary_memory_rows(db_path, limit=limit)

    filtered = []
    normalized_symbol = symbol.strip().lower()
    normalized_timeframe = timeframe.strip().lower()
    normalized_type = memory_type.strip().lower()
    for row in rows:
        row_dict = dict(row)
        if normalized_symbol and row_dict["symbol"].lower() != normalized_symbol:
            continue
        if normalized_timeframe and row_dict["timeframe"].lower() != normalized_timeframe:
            continue
        if normalized_type and row_dict["memory_type"].lower() != normalized_type:
            continue
        row_dict.pop("embedding", None)
        filtered.append(row_dict)
    return filtered


def get_hypothesis(hypothesis_id: int, db_path: Path = DEFAULT_DB_PATH) -> Optional[dict]:
    with connect_db(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            """
            SELECT id, created_at, updated_at, title, thesis, evidence, tags, symbol, timeframe, status, score, embedding,
                   data_path, window_start_idx, window_end_idx, window_start_ts, window_end_ts,
                   strategy_params, validation_result, paper_status
            FROM hypotheses
            WHERE id = ?
            """,
            (hypothesis_id,),
        ).fetchone()
    return dict(row) if row else None


def update_hypothesis(
    hypothesis_id: int,
    record: HypothesisRecord,
    db_path: Path = DEFAULT_DB_PATH,
    client: Optional[Any] = None,
) -> None:
    embedding = None
    if client is not None:
        try:
            embedding = client.embed(compose_embedding_text(record))
        except Exception:
            embedding = None

    with connect_db(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            """
            UPDATE hypotheses
            SET updated_at = ?, title = ?, thesis = ?, evidence = ?, tags = ?, symbol = ?, timeframe = ?, status = ?, score = ?, embedding = ?,
                data_path = ?, window_start_idx = ?, window_end_idx = ?, window_start_ts = ?, window_end_ts = ?,
                strategy_params = ?, validation_result = ?, paper_status = ?
            WHERE id = ?
            """,
            (
                utc_now(),
                record.title,
                record.thesis,
                record.evidence,
                record.tags,
                record.symbol,
                record.timeframe,
                record.status,
                record.score,
                json.dumps(embedding) if embedding is not None else None,
                record.data_path,
                record.window_start_idx,
                record.window_end_idx,
                record.window_start_ts,
                record.window_end_ts,
                record.strategy_params,
                record.validation_result,
                record.paper_status,
                hypothesis_id,
            ),
        )
        conn.commit()


def delete_hypothesis(hypothesis_id: int, db_path: Path = DEFAULT_DB_PATH) -> None:
    with connect_db(db_path) as conn:
        ensure_schema(conn)
        conn.execute("DELETE FROM hypotheses WHERE id = ?", (hypothesis_id,))
        conn.commit()


def search_hypotheses(
    query: str,
    db_path: Path = DEFAULT_DB_PATH,
    client: Optional[Any] = None,
    top_k: int = 5,
    symbol: str = "",
    timeframe: str = "",
    status: str = "",
) -> List[dict]:
    rows = filter_rows(
        _fetch_full_rows(db_path),
        symbol=symbol,
        timeframe=timeframe,
        status=status,
    )
    if not rows:
        return []

    rows = [normalize_hypothesis_row(dict(row)) for row in rows]

    lexical_results = lexical_search(rows, query, top_k=max(top_k * 3, top_k))
    lexical_by_id = {row["retrieval_key"]: row for row in lexical_results}
    lexical_scale = max([float(row.get("similarity", 0.0)) for row in lexical_results] + [1.0])

    if client is None:
        return finalize_ranked_rows(rows, lexical_by_id, top_k=top_k, lexical_scale=lexical_scale)

    try:
        query_embedding = np.array(client.embed(query), dtype=np.float32)
    except Exception:
        return finalize_ranked_rows(rows, lexical_by_id, top_k=top_k, lexical_scale=lexical_scale)

    semantic_scores = {}
    for row in rows:
        if not row["embedding"]:
            continue
        vector = np.array(json.loads(row["embedding"]), dtype=np.float32)
        denom = np.linalg.norm(query_embedding) * np.linalg.norm(vector)
        similarity = 0.0 if denom == 0 else float(np.dot(query_embedding, vector) / denom)
        semantic_scores[row["retrieval_key"]] = similarity

    return finalize_ranked_rows(
        rows,
        lexical_by_id,
        top_k=top_k,
        lexical_scale=lexical_scale,
        semantic_scores=semantic_scores,
    )


def search_summary_memories(
    query: str,
    db_path: Path = DEFAULT_DB_PATH,
    client: Optional[Any] = None,
    top_k: int = 5,
    symbol: str = "",
    timeframe: str = "",
    status: str = "",
) -> List[dict]:
    rows = normalize_summary_rows(
        filter_summary_memory_rows(
            _fetch_summary_memory_rows(db_path, limit=max(100, top_k * 10)),
            symbol=symbol,
            timeframe=timeframe,
        )
    )
    if status:
        rows = [row for row in rows if row["status"].lower() == status.lower()]
    if not rows:
        return []

    lexical_results = lexical_search(rows, query, top_k=max(top_k * 3, top_k))
    lexical_by_id = {row["retrieval_key"]: row for row in lexical_results}
    lexical_scale = max([float(row.get("similarity", 0.0)) for row in lexical_results] + [1.0])

    if client is None:
        return finalize_ranked_rows(
            rows,
            lexical_by_id,
            top_k=top_k,
            lexical_scale=lexical_scale,
            source_bias=0.92,
        )

    try:
        query_embedding = np.array(client.embed(query), dtype=np.float32)
    except Exception:
        return finalize_ranked_rows(
            rows,
            lexical_by_id,
            top_k=top_k,
            lexical_scale=lexical_scale,
            source_bias=0.92,
        )

    semantic_scores = {}
    for row in rows:
        if not row["embedding"]:
            continue
        vector = np.array(json.loads(row["embedding"]), dtype=np.float32)
        denom = np.linalg.norm(query_embedding) * np.linalg.norm(vector)
        similarity = 0.0 if denom == 0 else float(np.dot(query_embedding, vector) / denom)
        semantic_scores[row["retrieval_key"]] = similarity

    return finalize_ranked_rows(
        rows,
        lexical_by_id,
        top_k=top_k,
        lexical_scale=lexical_scale,
        semantic_scores=semantic_scores,
        source_bias=0.92,
    )


def search_memory_layers(
    query: str,
    db_path: Path = DEFAULT_DB_PATH,
    client: Optional[Any] = None,
    top_k: int = 8,
    symbol: str = "",
    timeframe: str = "",
    status: str = "",
) -> List[dict]:
    hypotheses = search_hypotheses(
        query=query,
        db_path=db_path,
        client=client,
        top_k=max(top_k, 4),
        symbol=symbol,
        timeframe=timeframe,
        status=status,
    )
    summary_memories = search_summary_memories(
        query=query,
        db_path=db_path,
        client=client,
        top_k=max(top_k, 4),
        symbol=symbol,
        timeframe=timeframe,
        status=status,
    )
    merged = hypotheses + summary_memories
    merged.sort(key=lambda item: (item["retrieval_score"], item["updated_at"], item["id"]), reverse=True)
    return merged[:top_k]


def synthesize_hypotheses(
    query: str,
    db_path: Path = DEFAULT_DB_PATH,
    client: Optional[Any] = None,
    top_k: int = 5,
    batch_size: int = 4,
    symbol: str = "",
    timeframe: str = "",
    status: str = "",
) -> str:
    result = synthesize_hypotheses_memory(
        query=query,
        db_path=db_path,
        client=client,
        retrieval_k=top_k,
        batch_size=batch_size,
        symbol=symbol,
        timeframe=timeframe,
        status=status,
    )
    return result.final_answer


def synthesize_hypotheses_memory(
    query: str,
    db_path: Path = DEFAULT_DB_PATH,
    client: Optional[Any] = None,
    retrieval_k: int = 8,
    batch_size: int = 4,
    symbol: str = "",
    timeframe: str = "",
    status: str = "",
    persist: bool = True,
) -> MemorySynthesisResult:
    if client is None:
        raise ValueError("Ollama client is required for synthesis")

    matches = search_memory_layers(
        query=query,
        db_path=db_path,
        client=client,
        top_k=retrieval_k,
        symbol=symbol,
        timeframe=timeframe,
        status=status,
    )
    if not matches:
        return MemorySynthesisResult(
            query=query,
            retrieved=[],
            batch_summaries=[],
            final_answer="Под подходящие фильтры и запрос пока не найдено гипотез в vault.",
            applied_filters={
                "symbol": symbol,
                "timeframe": timeframe,
                "status": status,
                "retrieval_k": retrieval_k,
                "batch_size": batch_size,
            },
            saved_memory_ids=[],
        )

    summaries = []
    saved_memory_ids = []
    normalized_batch_size = max(1, batch_size)
    for batch_index, chunk in enumerate(chunked(matches, normalized_batch_size), start=1):
        prompt = (
            f"Запрос исследователя:\n{query}\n\n"
            f"Пакет гипотез #{batch_index}:\n{build_context(chunk, include_window=True)}\n\n"
            "В пакете могут быть как сырые гипотезы, так и уже сохраненные summary memories.\n"
            "Сделай короткую выжимку пакета.\n"
            "1. Повторяющиеся паттерны\n"
            "2. Главные подтверждения\n"
            "3. Конфликты и слабые места\n"
            "4. Следующие проверки\n"
            "Пиши компактно, но содержательно."
        )
        summary = client.chat(prompt=prompt, system=summary_system_prompt())
        item = {
            "batch_index": batch_index,
            "ids": [int(row["id"]) for row in chunk],
            "source_labels": [row.get("retrieval_key", str(row["id"])) for row in chunk],
            "count": len(chunk),
            "summary": summary,
        }
        if persist:
            item["memory_id"] = save_summary_memory(
                db_path=db_path,
                memory_type="batch",
                query=query,
                symbol=symbol,
                timeframe=timeframe,
                status_filter=status,
                retrieval_k=retrieval_k,
                batch_size=normalized_batch_size,
                source_hypothesis_ids=collect_source_hypothesis_ids(chunk),
                content=summary,
                client=client,
            )
            saved_memory_ids.append(int(item["memory_id"]))
        summaries.append(item)

    final_prompt = (
        f"Запрос исследователя:\n{query}\n\n"
        f"Примененные фильтры:\n{json.dumps({'symbol': symbol, 'timeframe': timeframe, 'status': status}, ensure_ascii=False)}\n\n"
        f"Retrieval candidates:\n{build_retrieval_digest(matches)}\n\n"
        f"Промежуточные summaries:\n{build_summary_digest(summaries)}\n\n"
        "Сделай итоговый synthesis.\n"
        "1. Какие идеи сейчас выглядят самыми жизнеспособными\n"
        "2. Какие гипотезы конфликтуют или переобучены на наблюдения\n"
        "3. Какие эксперименты и рыночные условия проверить дальше\n"
        "4. Какие данные или признаки стоит добавить в систему памяти"
    )
    final_answer = client.chat(prompt=final_prompt, system=final_system_prompt())
    if persist:
        final_memory_id = save_summary_memory(
            db_path=db_path,
            memory_type="final",
            query=query,
            symbol=symbol,
            timeframe=timeframe,
            status_filter=status,
            retrieval_k=retrieval_k,
            batch_size=normalized_batch_size,
            source_hypothesis_ids=collect_source_hypothesis_ids(matches),
            content=final_answer,
            client=client,
        )
        saved_memory_ids.append(int(final_memory_id))
    return MemorySynthesisResult(
        query=query,
        retrieved=matches,
        batch_summaries=summaries,
        final_answer=final_answer,
        applied_filters={
            "symbol": symbol,
            "timeframe": timeframe,
            "status": status,
            "retrieval_k": retrieval_k,
            "batch_size": normalized_batch_size,
        },
        saved_memory_ids=saved_memory_ids,
    )


def lexical_search(rows: Iterable[sqlite3.Row], query: str, top_k: int) -> List[dict]:
    tokens = [token for token in query.lower().split() if token]
    scored = []
    for row in rows:
        haystack = " ".join(
            [
                row["title"],
                row["thesis"],
                row["evidence"],
                row["tags"],
                row["symbol"],
                row["timeframe"],
                row["status"],
                row.get("memory_type", ""),
                row.get("query", ""),
            ]
        ).lower()
        score = sum(1 for token in tokens if token in haystack)
        if not tokens:
            score = 1
        if score > 0:
            row_dict = dict(row)
            row_dict["similarity"] = float(score)
            scored.append((score, row_dict))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored[:top_k]]


def build_context(rows: Iterable[dict], include_window: bool = False) -> str:
    chunks = []
    for row in rows:
        lines = [
            f"ID: {row['id']}",
            f"Source: {row.get('source_type', 'hypothesis')}",
            f"Title: {row['title']}",
            f"Thesis: {row['thesis']}",
            f"Evidence: {row['evidence']}",
            f"Tags: {row['tags']}",
            f"Symbol: {row['symbol']}",
            f"Timeframe: {row['timeframe']}",
            f"Status: {row['status']}",
            f"Score: {row['score']}",
        ]
        if "retrieval_score" in row:
            lines.append(f"Retrieval score: {row['retrieval_score']:.4f}")
        if include_window and row.get("data_path"):
            lines.append(
                "Linked window: "
                f"{row.get('window_start_ts', '')} -> {row.get('window_end_ts', '')} "
                f"(rows {row.get('window_start_idx', -1)}..{row.get('window_end_idx', -1)})"
            )
        if row.get("source_type") == "summary_memory":
            lines.append(f"Memory type: {row.get('memory_type', '')}")
            lines.append(f"Memory query: {row.get('query', '')}")
            lines.append(f"Source hypotheses: {row.get('source_hypothesis_ids', '[]')}")
        chunks.append("\n".join(lines))
    return "\n\n---\n\n".join(chunks) if chunks else "Нет сохраненных гипотез."


def compose_embedding_text(record: HypothesisRecord) -> str:
    return " | ".join(
        [
            record.title,
            record.thesis,
            record.evidence,
            record.tags,
            record.symbol,
            record.timeframe,
            record.status,
        ]
    )


def connect_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hypotheses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            title TEXT NOT NULL,
            thesis TEXT NOT NULL,
            evidence TEXT NOT NULL,
            tags TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            status TEXT NOT NULL,
            score REAL NOT NULL,
            embedding TEXT
        )
        """
    )
    existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(hypotheses)").fetchall()
    }
    required = {
        "data_path": "TEXT NOT NULL DEFAULT ''",
        "window_start_idx": "INTEGER NOT NULL DEFAULT -1",
        "window_end_idx": "INTEGER NOT NULL DEFAULT -1",
        "window_start_ts": "TEXT NOT NULL DEFAULT ''",
        "window_end_ts": "TEXT NOT NULL DEFAULT ''",
        "strategy_params": "TEXT NOT NULL DEFAULT ''",
        "validation_result": "TEXT NOT NULL DEFAULT ''",
        "paper_status": "TEXT NOT NULL DEFAULT ''",
    }
    for column, ddl in required.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE hypotheses ADD COLUMN {column} {ddl}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS summary_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            query TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            status_filter TEXT NOT NULL,
            retrieval_k INTEGER NOT NULL,
            batch_size INTEGER NOT NULL,
            source_hypothesis_ids TEXT NOT NULL,
            content TEXT NOT NULL,
            embedding TEXT
        )
        """
    )
    summary_existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(summary_memories)").fetchall()
    }
    if "embedding" not in summary_existing:
        conn.execute("ALTER TABLE summary_memories ADD COLUMN embedding TEXT")


def _fetch_full_rows(db_path: Path) -> List[sqlite3.Row]:
    with connect_db(db_path) as conn:
        ensure_schema(conn)
        return conn.execute(
            """
            SELECT id, created_at, updated_at, title, thesis, evidence, tags, symbol, timeframe, status, score, embedding,
                   data_path, window_start_idx, window_end_idx, window_start_ts, window_end_ts
            FROM hypotheses
            ORDER BY id DESC
            """
        ).fetchall()


def _fetch_summary_memory_rows(db_path: Path, limit: int = 100) -> List[sqlite3.Row]:
    with connect_db(db_path) as conn:
        ensure_schema(conn)
        return conn.execute(
            """
            SELECT id, created_at, updated_at, memory_type, query, symbol, timeframe, status_filter,
                   retrieval_k, batch_size, source_hypothesis_ids, content, embedding
            FROM summary_memories
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_summary_memory(
    memory_type: str,
    query: str,
    symbol: str,
    timeframe: str,
    status_filter: str,
    retrieval_k: int,
    batch_size: int,
    source_hypothesis_ids: List[int],
    content: str,
    client: Optional[Any] = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    embedding = None
    if client is not None:
        try:
            embedding = client.embed(compose_summary_memory_text(memory_type, query, symbol, timeframe, content))
        except Exception:
            embedding = None
    with connect_db(db_path) as conn:
        ensure_schema(conn)
        cursor = conn.execute(
            """
            INSERT INTO summary_memories (
                created_at, updated_at, memory_type, query, symbol, timeframe, status_filter,
                retrieval_k, batch_size, source_hypothesis_ids, content, embedding
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now(),
                utc_now(),
                memory_type,
                query,
                symbol,
                timeframe,
                status_filter,
                retrieval_k,
                batch_size,
                json.dumps(source_hypothesis_ids),
                content,
                json.dumps(embedding) if embedding is not None else None,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def filter_rows(
    rows: Iterable[sqlite3.Row],
    symbol: str = "",
    timeframe: str = "",
    status: str = "",
) -> List[sqlite3.Row]:
    filtered = []
    normalized_symbol = symbol.strip().lower()
    normalized_timeframe = timeframe.strip().lower()
    normalized_status = status.strip().lower()
    for row in rows:
        if normalized_symbol and row["symbol"].lower() != normalized_symbol:
            continue
        if normalized_timeframe and row["timeframe"].lower() != normalized_timeframe:
            continue
        if normalized_status and row["status"].lower() != normalized_status:
            continue
        filtered.append(row)
    return filtered


def filter_summary_memory_rows(
    rows: Iterable[sqlite3.Row],
    symbol: str = "",
    timeframe: str = "",
) -> List[sqlite3.Row]:
    filtered = []
    normalized_symbol = symbol.strip().lower()
    normalized_timeframe = timeframe.strip().lower()
    for row in rows:
        if normalized_symbol and row["symbol"].lower() != normalized_symbol:
            continue
        if normalized_timeframe and row["timeframe"].lower() != normalized_timeframe:
            continue
        filtered.append(row)
    return filtered


def finalize_ranked_rows(
    rows: Iterable[sqlite3.Row],
    lexical_by_id: dict,
    top_k: int,
    lexical_scale: float,
    semantic_scores: Optional[dict] = None,
    source_bias: float = 1.0,
) -> List[dict]:
    ranked = []
    semantic_scores = semantic_scores or {}
    for raw_row in rows:
        row = dict(raw_row)
        retrieval_key = row.get("retrieval_key", str(row["id"]))
        lexical_score = float(lexical_by_id.get(retrieval_key, {}).get("similarity", 0.0))
        normalized_lexical = lexical_score / lexical_scale if lexical_scale else 0.0
        semantic_score = float(semantic_scores.get(retrieval_key, 0.0))
        confidence_score = float(row.get("score", 0.0))
        if semantic_scores:
            retrieval_score = (semantic_score * 0.7 + normalized_lexical * 0.2 + confidence_score * 0.1) * source_bias
        else:
            retrieval_score = (normalized_lexical * 0.8 + confidence_score * 0.2) * source_bias
        if retrieval_score <= 0:
            continue
        row["semantic_similarity"] = semantic_score
        row["lexical_similarity"] = lexical_score
        row["retrieval_score"] = retrieval_score
        ranked.append(row)
    ranked.sort(key=lambda item: (item["retrieval_score"], item["updated_at"], item["id"]), reverse=True)
    return ranked[:top_k]


def build_retrieval_digest(rows: Iterable[dict]) -> str:
    lines = []
    for row in rows:
        lines.append(
            " | ".join(
                [
                    f"#{row['id']}",
                    f"source={row.get('source_type', 'hypothesis')}",
                    row["title"],
                    f"symbol={row['symbol'] or '-'}",
                    f"timeframe={row['timeframe'] or '-'}",
                    f"status={row['status']}",
                    f"retrieval={float(row.get('retrieval_score', 0.0)):.4f}",
                ]
            )
        )
    return "\n".join(lines)


def build_summary_digest(summaries: Iterable[dict]) -> str:
    lines = []
    for item in summaries:
        lines.append(
            f"Batch #{item['batch_index']} | items={item.get('source_labels', item['ids'])} | count={item['count']}\n{item['summary']}"
        )
    return "\n\n---\n\n".join(lines)


def chunked(items: List[dict], size: int) -> Iterable[List[dict]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def summary_system_prompt() -> str:
    return (
        "Ты локальный research-ассистент по крипто-фьючерсам. "
        "Сжимаешь группы гипотез в короткие рабочие summaries без выдумок. "
        "Сохраняй только сигналы, конфликты, риски и следующие проверки."
    )


def final_system_prompt() -> str:
    return (
        "Ты локальный research-ассистент по крипто-фьючерсам. "
        "Работай только по retrieval candidates и промежуточным summaries. "
        "Твоя задача: собрать итоговый synthesis, не раздувая контекст и не повторяя одни и те же тезисы."
    )


def normalize_hypothesis_row(row: dict) -> dict:
    normalized = dict(row)
    normalized["source_type"] = "hypothesis"
    normalized["retrieval_key"] = f"h:{row['id']}"
    return normalized


def normalize_summary_rows(rows: Iterable[dict]) -> List[dict]:
    normalized = []
    for row in rows:
        item = dict(row)
        item["source_type"] = "summary_memory"
        item["retrieval_key"] = f"m:{row['id']}"
        item["title"] = f"{row['memory_type']} memory #{row['id']}"
        item["thesis"] = row["content"]
        item["evidence"] = f"Built from query: {row['query']}"
        item["tags"] = f"summary-memory,{row['memory_type']}"
        item["status"] = item.get("status_filter", "") or "memory"
        item["score"] = 0.0
        item["embedding"] = item.get("embedding")
        item["data_path"] = ""
        item["window_start_idx"] = -1
        item["window_end_idx"] = -1
        item["window_start_ts"] = ""
        item["window_end_ts"] = ""
        normalized.append(item)
    return normalized


def compose_summary_memory_text(memory_type: str, query: str, symbol: str, timeframe: str, content: str) -> str:
    return " | ".join([memory_type, query, symbol, timeframe, content])


def collect_source_hypothesis_ids(rows: Iterable[dict]) -> List[int]:
    output = []
    seen = set()
    for row in rows:
        if row.get("source_type") == "hypothesis":
            hypothesis_id = int(row["id"])
            if hypothesis_id not in seen:
                seen.add(hypothesis_id)
                output.append(hypothesis_id)
            continue
        if row.get("source_type") == "summary_memory":
            try:
                source_ids = json.loads(row.get("source_hypothesis_ids", "[]"))
            except Exception:
                source_ids = []
            for item in source_ids:
                hypothesis_id = int(item)
                if hypothesis_id not in seen:
                    seen.add(hypothesis_id)
                    output.append(hypothesis_id)
    return output
