"""
strategy_pack.py — Экспорт/импорт стратегий для Paper Trader

StrategyCard описывает одну проверенную стратегию из Hypothesis Vault.
Strategy Pack = JSON-файл с набором стратегий, готовых к деплою на VPS.

Пайплайн:
    1. Auto-Discover / Impulse Lab → Hypothesis Vault (status=testing)
    2. Валидация (backtest) → status=paper_ready
    3. export_strategy_pack() → strategy_pack.json
    4. SCP → VPS / Paper Trader загружает пак
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .hypothesis_vault import (
    DEFAULT_DB_PATH,
    HypothesisRecord,
    connect_db,
    ensure_schema,
    get_hypothesis,
    list_hypotheses_full,
    update_hypothesis,
    utc_now,
)

logger = logging.getLogger(__name__)

# Пороги для автоматической промоции в paper_ready
# WR ≥ 70% — обязательный минимум для входа
MIN_WIN_RATE = 0.70       # 70% — минимальный порог win rate
MIN_TRADES = 10           # минимум 10 сделок в бэктесте
MIN_AVG_PNL_PCT = 0.0     # средний PnL >= 0 (хотя бы в плюс)

DEFAULT_PACK_DIR = Path(".local_ai") / "strategy_packs"
DEFAULT_PACK_PATH = DEFAULT_PACK_DIR / "strategy_pack.json"


@dataclass
class StrategyCard:
    """Одна проверенная стратегия, готовая к деплою на VPS."""

    hypothesis_id: int              # ID из Hypothesis Vault
    name: str                       # "BTCUSDT 5m LONG z3.0"
    symbol: str                     # "BTCUSDT" или "*" (любой)
    timeframe: str                  # "5m"
    direction: str                  # "long" | "short" | "both"
    params: Dict[str, Any]          # validated strategy params
    win_rate: float                 # backtest win rate (0..1)
    total_trades: int               # сделок в бэктесте
    avg_pnl_pct: float              # средний PnL на сделку
    total_pnl_pct: float = 0.0     # суммарный PnL
    exported_at: str = ""           # ISO timestamp
    source: str = "auto_discover"   # "auto_discover" | "impulse_lab" | "manual"


def export_strategy_pack(
    db_path: Path = DEFAULT_DB_PATH,
    output_path: Optional[Path] = None,
    statuses: Optional[List[str]] = None,
) -> dict:
    """
    Экспортирует все проверенные стратегии из Hypothesis Vault в JSON.

    Parameters
    ----------
    db_path : Path — путь к БД гипотез
    output_path : Path | None — куда сохранить (None = DEFAULT_PACK_PATH)
    statuses : list[str] | None — какие статусы экспортировать
                                  (default: paper_ready, confirmed)

    Returns
    -------
    dict — содержимое пака
    """
    statuses = statuses or ["paper_ready", "confirmed"]
    rows = list_hypotheses_full(db_path=db_path, limit=500)

    cards: List[dict] = []
    for row in rows:
        if row["status"] not in statuses:
            continue

        # Извлекаем валидированные параметры
        params = _parse_strategy_params(row)
        validation = _parse_validation_result(row)

        if not params:
            logger.warning("Hypothesis #%d has no strategy_params, skipping", row["id"])
            continue

        # Определяем направление из evidence или title
        direction = _extract_direction(row)

        card = StrategyCard(
            hypothesis_id=row["id"],
            name=_build_card_name(row),
            symbol=row.get("symbol", "*") or "*",
            timeframe=row.get("timeframe", "5m") or "5m",
            direction=direction,
            params=params,
            win_rate=validation.get("win_rate", 0.0),
            total_trades=validation.get("trades", 0),
            avg_pnl_pct=validation.get("avg_realized_move_pct", 0.0),
            total_pnl_pct=validation.get("total_pnl_pct", 0.0),
            exported_at=utc_now(),
            source=_detect_source(row),
        )
        cards.append(asdict(card))

    pack = {
        "version": 1,
        "exported_at": utc_now(),
        "total_strategies": len(cards),
        "min_win_rate_threshold": MIN_WIN_RATE,
        "min_trades_threshold": MIN_TRADES,
        "strategies": cards,
    }

    output_path = output_path or DEFAULT_PACK_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "Exported strategy pack: %d strategies → %s", len(cards), output_path
    )
    return pack


def load_strategy_pack(path: Optional[Path] = None) -> List[StrategyCard]:
    """
    Загружает strategy pack из JSON файла.

    Returns
    -------
    list[StrategyCard] — список стратегий, готовых для Paper Trader
    """
    path = path or DEFAULT_PACK_PATH
    if not path.exists():
        logger.info("No strategy pack found at %s", path)
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to load strategy pack: %s", e)
        return []

    cards = []
    for item in data.get("strategies", []):
        try:
            card = StrategyCard(
                hypothesis_id=item["hypothesis_id"],
                name=item["name"],
                symbol=item.get("symbol", "*"),
                timeframe=item.get("timeframe", "5m"),
                direction=item.get("direction", "both"),
                params=item.get("params", {}),
                win_rate=item.get("win_rate", 0.0),
                total_trades=item.get("total_trades", 0),
                avg_pnl_pct=item.get("avg_pnl_pct", 0.0),
                total_pnl_pct=item.get("total_pnl_pct", 0.0),
                exported_at=item.get("exported_at", ""),
                source=item.get("source", "unknown"),
            )
            cards.append(card)
        except (KeyError, TypeError) as e:
            logger.warning("Skipping invalid strategy card: %s", e)

    logger.info("Loaded %d strategies from pack", len(cards))
    return cards


def auto_promote_hypothesis(
    hypothesis_id: int,
    validation_result: dict,
    db_path: Path = DEFAULT_DB_PATH,
    min_win_rate: float = MIN_WIN_RATE,
    min_trades: int = MIN_TRADES,
) -> bool:
    """
    Автоматически промоутит гипотезу в paper_ready если валидация прошла.

    Returns True если промоушн состоялся.
    """
    win_rate = validation_result.get("win_rate", 0.0)
    trades = validation_result.get("trades", 0)
    avg_pnl = validation_result.get("avg_realized_move_pct", 0.0)

    # Для промоции нужно: WR ≥ threshold И avg_pnl > 0 (реально прибыльная)
    if win_rate >= min_win_rate and trades >= min_trades and avg_pnl >= MIN_AVG_PNL_PCT:
        # Промоутим
        row = get_hypothesis(hypothesis_id, db_path=db_path)
        if row is None:
            return False

        record = HypothesisRecord(
            title=row["title"],
            thesis=row["thesis"],
            evidence=row.get("evidence", ""),
            tags=row.get("tags", ""),
            symbol=row.get("symbol", ""),
            timeframe=row.get("timeframe", ""),
            status="paper_ready",
            score=row.get("score", 0.0),
            data_path=row.get("data_path", ""),
            window_start_idx=row.get("window_start_idx", -1),
            window_end_idx=row.get("window_end_idx", -1),
            window_start_ts=row.get("window_start_ts", ""),
            window_end_ts=row.get("window_end_ts", ""),
            strategy_params=row.get("strategy_params", ""),
            validation_result=json.dumps(validation_result, ensure_ascii=False),
            paper_status="promoted",
        )
        update_hypothesis(hypothesis_id, record, db_path=db_path)
        logger.info(
            "✅ Hypothesis #%d promoted to paper_ready (WR=%.0f%%, trades=%d, PnL=%.2f%%)",
            hypothesis_id, win_rate * 100, trades, avg_pnl,
        )
        return True
    else:
        logger.debug(
            "Hypothesis #%d NOT promoted (WR=%.0f%%, trades=%d, PnL=%.2f%%)",
            hypothesis_id, win_rate * 100, trades, avg_pnl,
        )
        return False


def get_pack_summary(path: Optional[Path] = None) -> dict:
    """Возвращает сводку по стратегиям в паке."""
    cards = load_strategy_pack(path)
    if not cards:
        return {"total": 0, "symbols": [], "directions": {}, "avg_win_rate": 0}

    symbols = sorted(set(c.symbol for c in cards))
    directions = {}
    for c in cards:
        directions[c.direction] = directions.get(c.direction, 0) + 1

    return {
        "total": len(cards),
        "symbols": symbols,
        "directions": directions,
        "avg_win_rate": sum(c.win_rate for c in cards) / len(cards),
        "avg_trades": sum(c.total_trades for c in cards) / len(cards),
        "total_combined_trades": sum(c.total_trades for c in cards),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_strategy_params(row: dict) -> dict:
    raw = row.get("strategy_params", "")
    if not raw:
        return {}
    try:
        return json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _parse_validation_result(row: dict) -> dict:
    raw = row.get("validation_result", "")
    if not raw:
        return {}
    try:
        return json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _extract_direction(row: dict) -> str:
    """Определяет направление стратегии из title/evidence."""
    title = (row.get("title", "") or "").upper()
    evidence_raw = row.get("evidence", "") or ""
    try:
        evidence = json.loads(evidence_raw) if isinstance(evidence_raw, str) else evidence_raw
    except (json.JSONDecodeError, TypeError):
        evidence = {}

    direction_from_evidence = str(evidence.get("direction", "")).lower()
    if direction_from_evidence in ("long", "short"):
        return direction_from_evidence

    if "LONG" in title and "SHORT" not in title:
        return "long"
    if "SHORT" in title and "LONG" not in title:
        return "short"
    return "both"


def _build_card_name(row: dict) -> str:
    """Красивое имя для стратегии."""
    symbol = row.get("symbol", "?")
    tf = row.get("timeframe", "?")
    title = row.get("title", "")
    # Извлекаем direction из title
    direction = _extract_direction(row).upper()
    # Извлекаем energy score
    score = row.get("score", 0)
    return f"{symbol} {tf} {direction} E{score:.1f}"


def _detect_source(row: dict) -> str:
    """Определяет источник гипотезы."""
    tags = (row.get("tags", "") or "").lower()
    if "auto-generated" in tags or "anomaly" in tags:
        return "auto_discover"
    if "impulse" in tags:
        return "impulse_lab"
    return "manual"
