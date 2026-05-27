"""ImpulseDecomposer — разложение импульса на фазы Birth / Drive / Decay.

Декомпозиция адаптивная, на основе профиля энергии:
- Birth: энергия нарастает (градиент > 0)
- Drive: энергия на плато (максимальная, стабильная)
- Decay: энергия убывает (градиент < 0)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from .quant_brick import QuantBrick, get_brick_statistics
from .impulse_detector import DetectedImpulse


@dataclass
class ImpulsePhase:
    """Одна фаза импульса."""

    phase_type: str              # "birth" / "drive" / "decay"
    bricks: List[QuantBrick] = field(repr=False)
    start_index: int = 0
    end_index: int = 0

    # Метрики фазы
    price_move_pct: float = 0.0
    volume_share: float = 0.0    # доля объёма фазы от всего импульса
    mean_energy: float = 0.0
    peak_energy: float = 0.0
    duration_bricks: int = 0
    duration_seconds: int = 0

    # Нормализованная сигнатура фазы (для matching)
    energy_signature: List[float] = field(default_factory=list)
    price_signature: List[float] = field(default_factory=list)
    volume_signature: List[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "phase_type": self.phase_type,
            "start_index": self.start_index,
            "end_index": self.end_index,
            "price_move_pct": self.price_move_pct,
            "volume_share": self.volume_share,
            "mean_energy": self.mean_energy,
            "peak_energy": self.peak_energy,
            "duration_bricks": self.duration_bricks,
            "duration_seconds": self.duration_seconds,
            "energy_signature": self.energy_signature,
            "price_signature": self.price_signature,
            "volume_signature": self.volume_signature,
        }


@dataclass
class DecomposedImpulse:
    """Импульс, разложенный на фазы."""

    impulse: DetectedImpulse
    birth: ImpulsePhase
    drive: ImpulsePhase
    decay: ImpulsePhase
    quality_score: float = 0.0   # насколько "чистый" импульс (0-1)

    # Нормализованные сигнатуры всего импульса (для matching)
    full_energy_signature: List[float] = field(default_factory=list)
    full_price_signature: List[float] = field(default_factory=list)
    full_volume_signature: List[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "impulse": self.impulse.to_dict(),
            "birth": self.birth.to_dict(),
            "drive": self.drive.to_dict(),
            "decay": self.decay.to_dict(),
            "quality_score": self.quality_score,
            "full_energy_signature": self.full_energy_signature,
            "full_price_signature": self.full_price_signature,
            "full_volume_signature": self.full_volume_signature,
        }

    def get_phase_summary(self) -> dict:
        """Краткая сводка по фазам для UI."""
        return {
            "birth_bricks": self.birth.duration_bricks,
            "birth_move_pct": round(self.birth.price_move_pct, 4),
            "birth_energy": round(self.birth.mean_energy, 3),
            "birth_volume_share": round(self.birth.volume_share * 100, 1),
            "drive_bricks": self.drive.duration_bricks,
            "drive_move_pct": round(self.drive.price_move_pct, 4),
            "drive_energy": round(self.drive.mean_energy, 3),
            "drive_volume_share": round(self.drive.volume_share * 100, 1),
            "decay_bricks": self.decay.duration_bricks,
            "decay_move_pct": round(self.decay.price_move_pct, 4),
            "decay_energy": round(self.decay.mean_energy, 3),
            "decay_volume_share": round(self.decay.volume_share * 100, 1),
            "quality_score": round(self.quality_score, 3),
        }


# ---------------------------------------------------------------------------
# Signature helpers
# ---------------------------------------------------------------------------

SIGNATURE_SIZE = 30  # длина нормализованной сигнатуры


def _resample_to_signature(values: List[float], size: int = SIGNATURE_SIZE) -> List[float]:
    """Ресемплирует произвольный вектор до фиксированной длины."""
    if not values:
        return [0.0] * size
    if len(values) == 1:
        return [values[0]] * size
    arr = np.array(values, dtype=float)
    src_idx = np.linspace(0, 1, len(arr))
    tgt_idx = np.linspace(0, 1, size)
    resampled = np.interp(tgt_idx, src_idx, arr)
    return resampled.tolist()


def _normalize_signature(values: List[float]) -> List[float]:
    """Нормализует сигнатуру: вычитаем mean, делим на std."""
    arr = np.array(values, dtype=float)
    if len(arr) == 0:
        return values
    std = arr.std()
    if std == 0 or np.isnan(std):
        return (arr - arr.mean()).tolist()
    return ((arr - arr.mean()) / std).tolist()


# ---------------------------------------------------------------------------
# Decomposition
# ---------------------------------------------------------------------------

def decompose_impulse(impulse: DetectedImpulse) -> DecomposedImpulse:
    """Разлагает импульс на 3 фазы: Birth → Drive → Decay.

    Алгоритм адаптивный — основан на профиле энергии:
    1. Строим кумулятивный профиль энергии
    2. Находим точку перехода Birth→Drive: макс. градиент энергии
    3. Находим точку перехода Drive→Decay: начало устойчивого снижения энергии
    4. Если импульс короткий (< 5 кирпичиков) — делим равномерно
    """
    bricks = impulse.bricks
    n = len(bricks)

    if n < 3:
        # Слишком короткий — каждая фаза = 1 кирпичик
        birth_end = 0
        drive_end = max(0, n - 2)
    elif n < 6:
        # Короткий — простое деление на 3 части
        birth_end = n // 3
        drive_end = 2 * n // 3
    else:
        # Адаптивная декомпозиция по профилю энергии
        birth_end, drive_end = _find_phase_boundaries(bricks)

    birth_bricks = bricks[:birth_end + 1]
    drive_bricks = bricks[birth_end + 1:drive_end + 1] if drive_end > birth_end else bricks[birth_end + 1:birth_end + 2]
    decay_bricks = bricks[drive_end + 1:] if drive_end + 1 < n else bricks[-1:]

    # Гарантируем что каждая фаза не пустая
    if not birth_bricks:
        birth_bricks = [bricks[0]]
    if not drive_bricks:
        drive_bricks = [bricks[min(1, n - 1)]]
    if not decay_bricks:
        decay_bricks = [bricks[-1]]

    total_volume = impulse.total_volume or 1.0

    birth = _build_phase("birth", birth_bricks, total_volume)
    drive = _build_phase("drive", drive_bricks, total_volume)
    decay = _build_phase("decay", decay_bricks, total_volume)

    # Полные сигнатуры импульса
    full_energy_sig = _normalize_signature(
        _resample_to_signature([b.energy for b in bricks])
    )
    full_price_sig = _normalize_signature(
        _resample_to_signature([b.price_change_pct for b in bricks])
    )
    full_volume_sig = _normalize_signature(
        _resample_to_signature([b.dollar_volume_z for b in bricks])
    )

    # Quality score
    quality = _compute_quality_score(impulse, birth, drive, decay)

    return DecomposedImpulse(
        impulse=impulse,
        birth=birth,
        drive=drive,
        decay=decay,
        quality_score=quality,
        full_energy_signature=full_energy_sig,
        full_price_signature=full_price_sig,
        full_volume_signature=full_volume_sig,
    )


def _find_phase_boundaries(bricks: List[QuantBrick]) -> tuple:
    """Находит границы фаз по профилю энергии.

    Returns (birth_end_idx, drive_end_idx) — индексы в списке bricks.
    """
    n = len(bricks)
    energies = np.array([b.energy for b in bricks], dtype=float)

    # Сглаживаем профиль энергии (скользящее среднее с окном 3)
    if n >= 5:
        kernel = min(3, n // 2)
        smoothed = np.convolve(energies, np.ones(kernel) / kernel, mode="same")
    else:
        smoothed = energies

    # Градиент сглаженной энергии
    gradient = np.diff(smoothed)

    # Birth→Drive: ищем точку где градиент первый раз становится ≤ 0
    # (энергия перестала расти → birth закончился)
    birth_end = 0
    for i in range(len(gradient)):
        if gradient[i] > 0:
            birth_end = i + 1
        else:
            if birth_end > 0:
                break

    # Ограничиваем birth — не больше 40% импульса
    birth_end = min(birth_end, int(n * 0.4))
    # Минимум 1 кирпичик
    birth_end = max(0, birth_end)

    # Drive→Decay: ищем точку устойчивого снижения энергии
    # Берём от конца birth и ищем начало устойчивого падения
    peak_idx = int(np.argmax(smoothed[birth_end:])) + birth_end
    drive_end = peak_idx

    # От пика ищем, где энергия падает ниже median(drive_zone)
    if drive_end < n - 2:
        drive_zone_energies = smoothed[birth_end:n]
        if len(drive_zone_energies) > 0:
            half_peak = smoothed[peak_idx] * 0.5
            for i in range(peak_idx + 1, n):
                if smoothed[i] < half_peak:
                    drive_end = max(drive_end, i - 1)
                    break
            else:
                drive_end = n - 2

    # Ограничиваем drive — decay должен быть хотя бы 1 кирпичик
    drive_end = min(drive_end, n - 2)
    drive_end = max(drive_end, birth_end + 1)

    return birth_end, drive_end


def _build_phase(
    phase_type: str,
    bricks: List[QuantBrick],
    total_impulse_volume: float,
) -> ImpulsePhase:
    """Строит ImpulsePhase из набора кирпичиков."""
    energies = [b.energy for b in bricks]
    phase_volume = sum(b.volume for b in bricks)

    energy_sig = _normalize_signature(
        _resample_to_signature(energies, SIGNATURE_SIZE // 3)
    )
    price_sig = _normalize_signature(
        _resample_to_signature([b.price_change_pct for b in bricks], SIGNATURE_SIZE // 3)
    )
    volume_sig = _normalize_signature(
        _resample_to_signature([b.dollar_volume_z for b in bricks], SIGNATURE_SIZE // 3)
    )

    return ImpulsePhase(
        phase_type=phase_type,
        bricks=bricks,
        start_index=bricks[0].index,
        end_index=bricks[-1].index,
        price_move_pct=sum(b.price_change_pct for b in bricks),
        volume_share=phase_volume / total_impulse_volume if total_impulse_volume else 0.0,
        mean_energy=float(np.mean(energies)) if energies else 0.0,
        peak_energy=float(np.max(energies)) if energies else 0.0,
        duration_bricks=len(bricks),
        duration_seconds=len(bricks) * bricks[0].duration_seconds if bricks else 0,
        energy_signature=energy_sig,
        price_signature=price_sig,
        volume_signature=volume_sig,
    )


def _compute_quality_score(
    impulse: DetectedImpulse,
    birth: ImpulsePhase,
    drive: ImpulsePhase,
    decay: ImpulsePhase,
) -> float:
    """Оценка качества импульса (0-1).

    Идеальный импульс:
    - Birth: энергия растёт
    - Drive: энергия максимальная, основное движение цены
    - Decay: энергия падает
    - Направление стабильное
    - Drive даёт основной ценовой ход
    """
    scores = []

    # 1. Профиль энергии: birth < drive > decay (по mean_energy)
    if drive.mean_energy > 0:
        energy_profile = 0.0
        if birth.mean_energy < drive.mean_energy:
            energy_profile += 0.5
        if decay.mean_energy < drive.mean_energy:
            energy_profile += 0.5
        scores.append(energy_profile)

    # 2. Drive даёт основной ценовой ход
    total_abs = abs(impulse.total_price_move_pct) or 1.0
    drive_contribution = abs(drive.price_move_pct) / total_abs
    scores.append(min(1.0, drive_contribution))

    # 3. Направленность: кирпичики преимущественно в одну сторону
    directions = [b.direction for b in impulse.bricks if b.direction != 0]
    if directions:
        dominant = max(
            sum(1 for d in directions if d > 0),
            sum(1 for d in directions if d < 0),
        )
        scores.append(dominant / len(directions))

    # 4. Adverse excursion: маленький откат = высокое качество
    if impulse.max_favorable_pct > 0:
        adverse_ratio = impulse.max_adverse_pct / impulse.max_favorable_pct
        scores.append(max(0, 1.0 - adverse_ratio))

    return float(np.mean(scores)) if scores else 0.0


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity между двумя сигнатурами."""
    va = np.array(a, dtype=float)
    vb = np.array(b, dtype=float)
    if len(va) != len(vb) or len(va) == 0:
        return 0.0
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def match_birth_signature(
    candidate_bricks: List[QuantBrick],
    reference: DecomposedImpulse,
) -> dict:
    """Сравнивает текущий хвост кирпичиков с Birth-сигнатурой эталона.

    Returns dict с score и деталями.
    """
    if not candidate_bricks:
        return {"birth_score": 0.0, "is_match": False}

    # Строим сигнатуры кандидата
    cand_energy = _normalize_signature(
        _resample_to_signature([b.energy for b in candidate_bricks], SIGNATURE_SIZE // 3)
    )
    cand_price = _normalize_signature(
        _resample_to_signature([b.price_change_pct for b in candidate_bricks], SIGNATURE_SIZE // 3)
    )
    cand_volume = _normalize_signature(
        _resample_to_signature([b.dollar_volume_z for b in candidate_bricks], SIGNATURE_SIZE // 3)
    )

    energy_sim = cosine_similarity(cand_energy, reference.birth.energy_signature)
    price_sim = cosine_similarity(cand_price, reference.birth.price_signature)
    volume_sim = cosine_similarity(cand_volume, reference.birth.volume_signature)

    # Взвешенный score: объём и энергия важнее формы цены
    total_score = energy_sim * 0.35 + volume_sim * 0.40 + price_sim * 0.25

    return {
        "birth_score": total_score,
        "energy_similarity": energy_sim,
        "volume_similarity": volume_sim,
        "price_similarity": price_sim,
        "is_match": total_score >= 0.70,
        "expected_drive_move_pct": reference.drive.price_move_pct,
        "expected_decay_start_bricks": reference.birth.duration_bricks + reference.drive.duration_bricks,
        "reference_impulse_id": reference.impulse.impulse_id,
        "reference_quality": reference.quality_score,
    }
