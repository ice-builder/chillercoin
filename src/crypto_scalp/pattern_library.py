"""PatternLibrary — SQLite хранилище эталонных импульсов и их фаз.

Позволяет:
- сохранять вручную размеченные импульсы
- искать похожие Birth-паттерны по cosine similarity
- хранить статистику и метаданные
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import numpy as np

from .quant_brick import QuantBrick
from .impulse_detector import DetectedImpulse
from .impulse_decomposer import DecomposedImpulse, cosine_similarity, SIGNATURE_SIZE


DB_NAME = "pattern_library.db"


def _get_db_path(root: Path) -> Path:
    return root / ".local_ai" / DB_NAME


def init_pattern_library(root: Path) -> Path:
    """Инициализирует SQLite базу для хранения паттернов."""
    db_path = _get_db_path(root)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS impulses (
                impulse_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL DEFAULT '1m',
                source TEXT NOT NULL DEFAULT 'manual',
                direction INTEGER NOT NULL,
                direction_label TEXT NOT NULL,
                start_index INTEGER,
                end_index INTEGER,
                start_timestamp TEXT,
                end_timestamp TEXT,
                total_price_move_pct REAL,
                total_volume REAL,
                total_dollar_volume REAL,
                peak_energy REAL,
                mean_energy REAL,
                duration_bricks INTEGER,
                duration_seconds INTEGER,
                entry_price REAL,
                exit_price REAL,
                max_favorable_pct REAL,
                max_adverse_pct REAL,
                pre_energy_mean REAL,
                pre_volume_z_mean REAL,
                quality_score REAL,
                tags TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                data_path TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS impulse_phases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                impulse_id TEXT NOT NULL REFERENCES impulses(impulse_id),
                phase_type TEXT NOT NULL,
                start_index INTEGER,
                end_index INTEGER,
                price_move_pct REAL,
                volume_share REAL,
                mean_energy REAL,
                peak_energy REAL,
                duration_bricks INTEGER,
                duration_seconds INTEGER,
                energy_signature TEXT,
                price_signature TEXT,
                volume_signature TEXT
            );

            CREATE TABLE IF NOT EXISTS impulse_signatures (
                impulse_id TEXT PRIMARY KEY REFERENCES impulses(impulse_id),
                full_energy_signature TEXT,
                full_price_signature TEXT,
                full_volume_signature TEXT,
                birth_energy_signature TEXT,
                birth_price_signature TEXT,
                birth_volume_signature TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_impulses_symbol ON impulses(symbol);
            CREATE INDEX IF NOT EXISTS idx_impulses_direction ON impulses(direction);
            CREATE INDEX IF NOT EXISTS idx_impulses_source ON impulses(source);
            CREATE INDEX IF NOT EXISTS idx_phases_impulse ON impulse_phases(impulse_id);
        """)
        conn.commit()
    finally:
        conn.close()

    return db_path


def save_impulse(
    root: Path,
    decomposed: DecomposedImpulse,
    symbol: str,
    timeframe: str = "1m",
    source: str = "manual",
    tags: str = "",
    notes: str = "",
    data_path: str = "",
) -> str:
    """Сохраняет размеченный импульс в библиотеку.

    Returns impulse_id.
    """
    db_path = init_pattern_library(root)
    imp = decomposed.impulse

    # Генерируем уникальный ID
    impulse_id = f"{source}_{symbol.lower()}_{uuid.uuid4().hex[:8]}"

    conn = sqlite3.connect(str(db_path))
    try:
        # Сохраняем импульс
        conn.execute("""
            INSERT OR REPLACE INTO impulses (
                impulse_id, symbol, timeframe, source, direction, direction_label,
                start_index, end_index, start_timestamp, end_timestamp,
                total_price_move_pct, total_volume, total_dollar_volume,
                peak_energy, mean_energy, duration_bricks, duration_seconds,
                entry_price, exit_price, max_favorable_pct, max_adverse_pct,
                pre_energy_mean, pre_volume_z_mean, quality_score,
                tags, notes, created_at, data_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            impulse_id, symbol.upper(), timeframe, source,
            imp.direction, "long" if imp.direction > 0 else "short",
            imp.start_index, imp.end_index,
            str(imp.start_timestamp), str(imp.end_timestamp),
            imp.total_price_move_pct, imp.total_volume, imp.total_dollar_volume,
            imp.peak_energy, imp.mean_energy,
            imp.duration_bricks, imp.duration_seconds,
            imp.entry_price, imp.exit_price,
            imp.max_favorable_pct, imp.max_adverse_pct,
            imp.pre_energy_mean, imp.pre_volume_z_mean,
            decomposed.quality_score,
            tags, notes,
            datetime.now(timezone.utc).isoformat(),
            data_path,
        ))

        # Сохраняем фазы
        for phase in [decomposed.birth, decomposed.drive, decomposed.decay]:
            conn.execute("""
                INSERT INTO impulse_phases (
                    impulse_id, phase_type, start_index, end_index,
                    price_move_pct, volume_share, mean_energy, peak_energy,
                    duration_bricks, duration_seconds,
                    energy_signature, price_signature, volume_signature
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                impulse_id, phase.phase_type,
                phase.start_index, phase.end_index,
                phase.price_move_pct, phase.volume_share,
                phase.mean_energy, phase.peak_energy,
                phase.duration_bricks, phase.duration_seconds,
                json.dumps(phase.energy_signature),
                json.dumps(phase.price_signature),
                json.dumps(phase.volume_signature),
            ))

        # Сохраняем сигнатуры
        conn.execute("""
            INSERT OR REPLACE INTO impulse_signatures (
                impulse_id,
                full_energy_signature, full_price_signature, full_volume_signature,
                birth_energy_signature, birth_price_signature, birth_volume_signature
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            impulse_id,
            json.dumps(decomposed.full_energy_signature),
            json.dumps(decomposed.full_price_signature),
            json.dumps(decomposed.full_volume_signature),
            json.dumps(decomposed.birth.energy_signature),
            json.dumps(decomposed.birth.price_signature),
            json.dumps(decomposed.birth.volume_signature),
        ))

        conn.commit()
    finally:
        conn.close()

    return impulse_id


def list_impulses(
    root: Path,
    symbol: Optional[str] = None,
    direction: Optional[int] = None,
    source: Optional[str] = None,
    min_quality: float = 0.0,
) -> List[dict]:
    """Список всех сохранённых импульсов с фильтрами."""
    db_path = _get_db_path(root)
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT * FROM impulses WHERE quality_score >= ?"
        params: list = [min_quality]

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.upper())
        if direction is not None:
            query += " AND direction = ?"
            params.append(direction)
        if source:
            query += " AND source = ?"
            params.append(source)

        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_impulse_phases(root: Path, impulse_id: str) -> List[dict]:
    """Получает фазы конкретного импульса."""
    db_path = _get_db_path(root)
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM impulse_phases WHERE impulse_id = ? ORDER BY phase_type",
            (impulse_id,),
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            for key in ("energy_signature", "price_signature", "volume_signature"):
                if d.get(key):
                    d[key] = json.loads(d[key])
            result.append(d)
        return result
    finally:
        conn.close()


def get_impulse_signatures(root: Path, impulse_id: str) -> Optional[dict]:
    """Получает сигнатуры импульса."""
    db_path = _get_db_path(root)
    if not db_path.exists():
        return None

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM impulse_signatures WHERE impulse_id = ?",
            (impulse_id,),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        for key in d:
            if key != "impulse_id" and d[key]:
                d[key] = json.loads(d[key])
        return d
    finally:
        conn.close()


def find_similar_impulses(
    root: Path,
    birth_energy_sig: List[float],
    birth_volume_sig: List[float],
    birth_price_sig: List[float],
    symbol: Optional[str] = None,
    direction: Optional[int] = None,
    top_k: int = 10,
    min_score: float = 0.5,
) -> List[dict]:
    """Ищет похожие импульсы по Birth-сигнатуре.

    Сравнивает birth-сигнатуры через cosine similarity.
    """
    db_path = _get_db_path(root)
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT s.*, i.* FROM impulse_signatures s JOIN impulses i ON s.impulse_id = i.impulse_id WHERE 1=1"
        params: list = []

        if symbol:
            query += " AND i.symbol = ?"
            params.append(symbol.upper())
        if direction is not None:
            query += " AND i.direction = ?"
            params.append(direction)

        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        d = dict(row)
        ref_energy = json.loads(d.get("birth_energy_signature") or "[]")
        ref_volume = json.loads(d.get("birth_volume_signature") or "[]")
        ref_price = json.loads(d.get("birth_price_signature") or "[]")

        energy_sim = cosine_similarity(birth_energy_sig, ref_energy)
        volume_sim = cosine_similarity(birth_volume_sig, ref_volume)
        price_sim = cosine_similarity(birth_price_sig, ref_price)

        total_score = energy_sim * 0.35 + volume_sim * 0.40 + price_sim * 0.25

        if total_score >= min_score:
            results.append({
                "impulse_id": d["impulse_id"],
                "symbol": d.get("symbol", ""),
                "direction_label": d.get("direction_label", ""),
                "total_price_move_pct": d.get("total_price_move_pct", 0),
                "duration_bricks": d.get("duration_bricks", 0),
                "quality_score": d.get("quality_score", 0),
                "similarity_score": total_score,
                "energy_similarity": energy_sim,
                "volume_similarity": volume_sim,
                "price_similarity": price_sim,
                "start_timestamp": d.get("start_timestamp", ""),
                "end_timestamp": d.get("end_timestamp", ""),
                "mean_energy": d.get("mean_energy", 0),
                "peak_energy": d.get("peak_energy", 0),
            })

    results.sort(key=lambda x: x["similarity_score"], reverse=True)
    return results[:top_k]


def delete_impulse(root: Path, impulse_id: str) -> bool:
    """Удаляет импульс из библиотеки."""
    db_path = _get_db_path(root)
    if not db_path.exists():
        return False

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("DELETE FROM impulse_signatures WHERE impulse_id = ?", (impulse_id,))
        conn.execute("DELETE FROM impulse_phases WHERE impulse_id = ?", (impulse_id,))
        conn.execute("DELETE FROM impulses WHERE impulse_id = ?", (impulse_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def get_library_stats(root: Path, symbol: Optional[str] = None) -> dict:
    """Статистика по библиотеке паттернов."""
    db_path = _get_db_path(root)
    if not db_path.exists():
        return {"total": 0}

    conn = sqlite3.connect(str(db_path))
    try:
        where = ""
        params: list = []
        if symbol:
            where = " WHERE symbol = ?"
            params = [symbol.upper()]

        total = conn.execute(f"SELECT COUNT(*) FROM impulses{where}", params).fetchone()[0]
        manual = conn.execute(
            f"SELECT COUNT(*) FROM impulses{where}" + (" AND" if where else " WHERE") + " source = 'manual'",
            params,
        ).fetchone()[0]
        auto = total - manual

        longs = conn.execute(
            f"SELECT COUNT(*) FROM impulses{where}" + (" AND" if where else " WHERE") + " direction = 1",
            params,
        ).fetchone()[0]
        shorts = total - longs

        avg_quality = conn.execute(
            f"SELECT AVG(quality_score) FROM impulses{where}", params
        ).fetchone()[0] or 0

        avg_move = conn.execute(
            f"SELECT AVG(ABS(total_price_move_pct)) FROM impulses{where}", params
        ).fetchone()[0] or 0

        return {
            "total": total,
            "manual": manual,
            "auto": auto,
            "longs": longs,
            "shorts": shorts,
            "avg_quality": round(avg_quality, 3),
            "avg_abs_move_pct": round(avg_move, 4),
        }
    finally:
        conn.close()
