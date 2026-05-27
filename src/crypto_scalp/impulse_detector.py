"""ImpulseDetector — находит последовательности кирпичиков с высокой энергией.

Импульс = серия QuantBrick'ов с:
- энергией выше порога
- преимущественно одним направлением
- допуском на небольшие gaps (1-2 слабых кирпичика внутри)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from .quant_brick import QuantBrick, bricks_to_dataframe, get_brick_statistics


@dataclass
class DetectedImpulse:
    """Найденный импульс — последовательность кирпичиков с высокой энергией."""

    impulse_id: str               # уникальный ID
    start_index: int              # индекс первого кирпичика в общем потоке
    end_index: int                # индекс последнего кирпичика
    direction: int                # +1 (long) / -1 (short)
    bricks: List[QuantBrick] = field(repr=False)

    # Агрегированные метрики
    total_price_move_pct: float = 0.0
    total_volume: float = 0.0
    total_dollar_volume: float = 0.0
    peak_energy: float = 0.0
    mean_energy: float = 0.0
    duration_bricks: int = 0
    duration_seconds: int = 0

    # Ценовые экстремумы
    entry_price: float = 0.0
    exit_price: float = 0.0
    max_price: float = 0.0
    min_price: float = 0.0
    max_favorable_pct: float = 0.0   # максимальное движение в сторону импульса
    max_adverse_pct: float = 0.0     # максимальный откат против

    # Контекст — состояние рынка ДО импульса
    pre_energy_mean: float = 0.0     # средняя энергия перед импульсом
    pre_volume_z_mean: float = 0.0   # средний volume_z перед импульсом

    # Timestamps
    start_timestamp: object = None
    end_timestamp: object = None

    def to_dict(self) -> dict:
        return {
            "impulse_id": self.impulse_id,
            "start_index": self.start_index,
            "end_index": self.end_index,
            "direction": self.direction,
            "direction_label": "long" if self.direction > 0 else "short",
            "total_price_move_pct": self.total_price_move_pct,
            "total_volume": self.total_volume,
            "total_dollar_volume": self.total_dollar_volume,
            "peak_energy": self.peak_energy,
            "mean_energy": self.mean_energy,
            "duration_bricks": self.duration_bricks,
            "duration_seconds": self.duration_seconds,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "max_price": self.max_price,
            "min_price": self.min_price,
            "max_favorable_pct": self.max_favorable_pct,
            "max_adverse_pct": self.max_adverse_pct,
            "pre_energy_mean": self.pre_energy_mean,
            "pre_volume_z_mean": self.pre_volume_z_mean,
            "start_timestamp": str(self.start_timestamp),
            "end_timestamp": str(self.end_timestamp),
            "brick_count": len(self.bricks),
        }


@dataclass
class ImpulseDetectorConfig:
    """Настройки детектора импульсов."""

    # Порог энергии для считания кирпичика "горячим"
    min_energy: float = 1.5

    # Минимальное кол-во горячих кирпичиков для импульса
    min_impulse_bricks: int = 3

    # Максимальное кол-во слабых кирпичиков подряд внутри импульса (gap tolerance)
    max_gap_bricks: int = 2

    # Минимальная доля направленных кирпичиков (>= этого % должны быть в одну сторону)
    min_direction_ratio: float = 0.6

    # Минимальное суммарное движение цены в % для фиксации импульса
    min_total_move_pct: float = 0.3

    # Окно "перед импульсом" для расчёта контекста
    pre_context_bricks: int = 20

    # Максимальная длина импульса (защита от бесконечных трендов)
    max_impulse_bricks: int = 60

    def to_dict(self) -> dict:
        return {
            "min_energy": self.min_energy,
            "min_impulse_bricks": self.min_impulse_bricks,
            "max_gap_bricks": self.max_gap_bricks,
            "min_direction_ratio": self.min_direction_ratio,
            "min_total_move_pct": self.min_total_move_pct,
            "pre_context_bricks": self.pre_context_bricks,
            "max_impulse_bricks": self.max_impulse_bricks,
        }


def detect_impulses(
    bricks: List[QuantBrick],
    config: Optional[ImpulseDetectorConfig] = None,
) -> List[DetectedImpulse]:
    """Автоматический детектор импульсов по потоку кирпичиков.

    Алгоритм:
    1. Сканируем поток кирпичиков
    2. Если кирпичик "горячий" (energy >= min_energy) — начинаем набирать последовательность
    3. Допускаем до max_gap_bricks подряд "холодных" кирпичиков внутри
    4. Если gap слишком длинный или направление развернулось — фиксируем импульс
    5. Валидируем: минимальная длина, направленность, суммарный ход
    """
    if config is None:
        config = ImpulseDetectorConfig()

    if not bricks:
        return []

    impulses: List[DetectedImpulse] = []
    n = len(bricks)
    i = 0
    impulse_counter = 0

    while i < n:
        brick = bricks[i]

        # Ищем начало — горячий кирпичик
        if brick.energy < config.min_energy:
            i += 1
            continue

        # Нашли горячий — начинаем набирать последовательность
        sequence_start = i
        sequence: List[QuantBrick] = [brick]
        gap_count = 0
        j = i + 1

        while j < n and (j - sequence_start) < config.max_impulse_bricks:
            next_brick = bricks[j]

            if next_brick.energy >= config.min_energy:
                # Горячий кирпичик — сбрасываем gap, добавляем
                # Добавляем все gap-кирпичики тоже (они часть структуры)
                if gap_count > 0:
                    # Добавляем пропущенные gap-кирпичики
                    for g in range(j - gap_count, j):
                        sequence.append(bricks[g])
                    gap_count = 0
                sequence.append(next_brick)
                j += 1
            else:
                # Холодный кирпичик — считаем gap
                gap_count += 1
                if gap_count > config.max_gap_bricks:
                    # Gap слишком длинный — заканчиваем
                    break
                j += 1

        # Валидируем последовательность
        if len(sequence) >= config.min_impulse_bricks:
            impulse = _validate_and_build_impulse(
                sequence=sequence,
                all_bricks=bricks,
                config=config,
                impulse_counter=impulse_counter,
            )
            if impulse is not None:
                impulses.append(impulse)
                impulse_counter += 1

        # Перепрыгиваем за конец последовательности
        i = j if j > i + 1 else i + 1

    return impulses


def detect_impulses_from_selection(
    bricks: List[QuantBrick],
    start_index: int,
    end_index: int,
    all_bricks: Optional[List[QuantBrick]] = None,
    impulse_id: Optional[str] = None,
) -> Optional[DetectedImpulse]:
    """Создаёт DetectedImpulse из ручного выделения на графике.

    Пользователь выделил участок [start_index, end_index] — мы оборачиваем его
    в DetectedImpulse со всеми метриками.
    """
    selected = [b for b in bricks if start_index <= b.index <= end_index]
    if not selected:
        return None

    context_bricks = all_bricks or bricks
    return _build_impulse_from_bricks(
        sequence=selected,
        all_bricks=context_bricks,
        impulse_id=impulse_id or f"manual_{start_index}_{end_index}",
        pre_context_bricks=20,
    )


def _validate_and_build_impulse(
    sequence: List[QuantBrick],
    all_bricks: List[QuantBrick],
    config: ImpulseDetectorConfig,
    impulse_counter: int,
) -> Optional[DetectedImpulse]:
    """Валидирует последовательность и строит DetectedImpulse."""

    # Проверяем направленность
    directions = [b.direction for b in sequence if b.direction != 0]
    if not directions:
        return None

    up_count = sum(1 for d in directions if d > 0)
    down_count = sum(1 for d in directions if d < 0)
    total_directional = up_count + down_count
    if total_directional == 0:
        return None

    dominant_ratio = max(up_count, down_count) / total_directional
    if dominant_ratio < config.min_direction_ratio:
        return None

    dominant_direction = 1 if up_count >= down_count else -1

    # Проверяем суммарное движение
    total_move = sum(b.price_change_pct for b in sequence)
    if abs(total_move) < config.min_total_move_pct:
        return None

    # Направление движения должно совпадать с доминантным направлением
    if np.sign(total_move) != dominant_direction:
        return None

    return _build_impulse_from_bricks(
        sequence=sequence,
        all_bricks=all_bricks,
        impulse_id=f"auto_{impulse_counter:05d}",
        pre_context_bricks=config.pre_context_bricks,
    )


def _build_impulse_from_bricks(
    sequence: List[QuantBrick],
    all_bricks: List[QuantBrick],
    impulse_id: str,
    pre_context_bricks: int = 20,
) -> DetectedImpulse:
    """Строит DetectedImpulse из списка кирпичиков."""

    # Направление по суммарному движению
    total_move = sum(b.price_change_pct for b in sequence)
    direction = 1 if total_move >= 0 else -1

    energies = [b.energy for b in sequence]
    entry_price = sequence[0].price_open
    exit_price = sequence[-1].price_close

    # Ценовые экстремумы
    prices_high = [b.price_high for b in sequence]
    prices_low = [b.price_low for b in sequence]
    max_price = max(prices_high)
    min_price = min(prices_low)

    if direction > 0:  # long
        max_favorable_pct = (max_price / entry_price - 1) * 100 if entry_price else 0
        max_adverse_pct = (1 - min_price / entry_price) * 100 if entry_price else 0
    else:  # short
        max_favorable_pct = (1 - min_price / entry_price) * 100 if entry_price else 0
        max_adverse_pct = (max_price / entry_price - 1) * 100 if entry_price else 0

    # Контекст до импульса
    start_idx = sequence[0].index
    pre_bricks = [b for b in all_bricks
                  if (start_idx - pre_context_bricks) <= b.index < start_idx]
    pre_energy_mean = float(np.mean([b.energy for b in pre_bricks])) if pre_bricks else 0.0
    pre_volume_z_mean = float(np.mean([b.dollar_volume_z for b in pre_bricks])) if pre_bricks else 0.0

    return DetectedImpulse(
        impulse_id=impulse_id,
        start_index=sequence[0].index,
        end_index=sequence[-1].index,
        direction=direction,
        bricks=sequence,
        total_price_move_pct=total_move,
        total_volume=sum(b.volume for b in sequence),
        total_dollar_volume=sum(b.dollar_volume for b in sequence),
        peak_energy=max(energies),
        mean_energy=float(np.mean(energies)),
        duration_bricks=len(sequence),
        duration_seconds=len(sequence) * sequence[0].duration_seconds,
        entry_price=entry_price,
        exit_price=exit_price,
        max_price=max_price,
        min_price=min_price,
        max_favorable_pct=max_favorable_pct,
        max_adverse_pct=max_adverse_pct,
        pre_energy_mean=pre_energy_mean,
        pre_volume_z_mean=pre_volume_z_mean,
        start_timestamp=sequence[0].timestamp,
        end_timestamp=sequence[-1].timestamp,
    )


def get_impulse_context_comparison(
    impulse: DetectedImpulse,
    all_bricks: List[QuantBrick],
    context_window: int = 60,
) -> dict:
    """Сравнение метрик импульса vs окружающего контекста (флет).

    Это нужно для визуального ответа на вопрос:
    'как пороги импульса отличаются от флета/лёгкого тренда?'
    """
    from .quant_brick import get_brick_statistics

    impulse_stats = get_brick_statistics(impulse.bricks)

    # Собираем кирпичики ДО импульса (контекст/флет)
    pre_bricks = [b for b in all_bricks
                  if (impulse.start_index - context_window) <= b.index < impulse.start_index]
    pre_stats = get_brick_statistics(pre_bricks) if pre_bricks else {}

    # Собираем кирпичики ПОСЛЕ импульса
    post_bricks = [b for b in all_bricks
                   if impulse.end_index < b.index <= (impulse.end_index + context_window)]
    post_stats = get_brick_statistics(post_bricks) if post_bricks else {}

    return {
        "impulse": impulse_stats,
        "pre_context": pre_stats,
        "post_context": post_stats,
        "energy_ratio_vs_pre": (
            impulse_stats.get("energy_mean", 0) / pre_stats["energy_mean"]
            if pre_stats.get("energy_mean", 0) > 0 else 0
        ),
        "volume_z_ratio_vs_pre": (
            impulse_stats.get("dollar_volume_z_mean", 0) / pre_stats["dollar_volume_z_mean"]
            if pre_stats.get("dollar_volume_z_mean", 0) > 0 else 0
        ),
    }


def impulses_to_dataframe(impulses: List[DetectedImpulse]) -> pd.DataFrame:
    """Конвертирует список импульсов в DataFrame для отображения."""
    if not impulses:
        return pd.DataFrame()
    records = [imp.to_dict() for imp in impulses]
    return pd.DataFrame(records)
