from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, List

import numpy as np


def resample_signature(values: Iterable[float], target_size: int = 60) -> List[float]:
    vector = [safe_float(value) for value in values]
    if not vector:
        return [0.0] * target_size
    if len(vector) == 1:
        return [vector[0]] * target_size
    source_index = np.linspace(0.0, 1.0, num=len(vector))
    target_index = np.linspace(0.0, 1.0, num=target_size)
    return np.interp(target_index, source_index, vector).tolist()


def build_pattern_matcher_template(
    symbol: str,
    source_path: Path,
    selection_start_ts: str,
    selection_end_ts: str,
    horizon_seconds: int,
    close_values: Iterable[float],
    volume_values: Iterable[float],
    trigger_side: str,
    expected_move_pct: float,
    similarity_threshold: float = 0.82,
) -> dict:
    close_array = np.array([safe_float(value) for value in close_values], dtype=float)
    volume_array = np.array([safe_float(value) for value in volume_values], dtype=float)
    base_close = close_array[0] if len(close_array) else 1.0
    close_signature = ((close_array / base_close) - 1.0).tolist() if base_close else close_array.tolist()
    volume_mean = float(volume_array.mean()) if len(volume_array) else 0.0
    volume_std = float(volume_array.std()) if len(volume_array) else 0.0
    volume_signature = ((volume_array / volume_mean) - 1.0).tolist() if volume_mean else volume_array.tolist()
    burst_ratio = float(volume_array.max() / volume_mean) if volume_mean and len(volume_array) else 0.0
    phase_profile = build_phase_profile(close_array, volume_array)
    observed_risk = estimate_observed_risk(close_array=close_array, trigger_side=trigger_side)
    entry_rule = build_entry_rule(
        trigger_side=trigger_side,
        expected_move_pct=expected_move_pct,
        similarity_threshold=similarity_threshold,
        burst_ratio=burst_ratio,
        phase_profile=phase_profile,
    )
    risk_plan = build_risk_plan(
        trigger_side=trigger_side,
        expected_move_pct=expected_move_pct,
        burst_ratio=burst_ratio,
        observed_risk_pct=observed_risk["adverse_excursion_pct"],
    )

    return {
        "template_kind": "formation_volume_1s",
        "symbol": symbol,
        "source_path": str(source_path),
        "selection_start_ts": selection_start_ts,
        "selection_end_ts": selection_end_ts,
        "horizon_seconds": horizon_seconds,
        "template_seconds": int(len(close_array)),
        "trigger_side": trigger_side,
        "expected_move_pct": expected_move_pct,
        "similarity_threshold": similarity_threshold,
        "volume_mean": volume_mean,
        "volume_std": volume_std,
        "burst_volume_ratio": burst_ratio,
        "phase_profile": phase_profile,
        "entry_rule": entry_rule,
        "risk_plan": risk_plan,
        "close_signature": resample_signature(close_signature, 60),
        "volume_signature": resample_signature(volume_signature, 60),
    }


def score_matcher_template(template: dict, close_values: Iterable[float], volume_values: Iterable[float]) -> dict:
    candidate = build_pattern_matcher_template(
        symbol=template.get("symbol", ""),
        source_path=Path(template.get("source_path", ".")),
        selection_start_ts=template.get("selection_start_ts", ""),
        selection_end_ts=template.get("selection_end_ts", ""),
        horizon_seconds=int(template.get("horizon_seconds", 0)),
        close_values=close_values,
        volume_values=volume_values,
        trigger_side=template.get("trigger_side", "long"),
        expected_move_pct=float(template.get("expected_move_pct", 0.0)),
        similarity_threshold=float(template.get("similarity_threshold", 0.82)),
    )
    close_score = cosine_similarity(template.get("close_signature", []), candidate.get("close_signature", []))
    volume_score = cosine_similarity(template.get("volume_signature", []), candidate.get("volume_signature", []))
    total_score = close_score * 0.45 + volume_score * 0.55
    return {
        "score": total_score,
        "close_score": close_score,
        "volume_score": volume_score,
        "is_match": total_score >= float(template.get("similarity_threshold", 0.82)),
    }


def evaluate_trade_signal(template: dict, close_values: Iterable[float], volume_values: Iterable[float]) -> dict:
    score = score_matcher_template(template, close_values=close_values, volume_values=volume_values)
    close_array = np.array([safe_float(value) for value in close_values], dtype=float)
    volume_array = np.array([safe_float(value) for value in volume_values], dtype=float)
    entry_rule = template.get("entry_rule", {}) or {}
    base_close = close_array[0] if len(close_array) else 0.0
    last_close = close_array[-1] if len(close_array) else 0.0
    path_change_pct = (last_close / base_close - 1.0) * 100 if base_close else 0.0
    volume_mean = float(volume_array.mean()) if len(volume_array) else 0.0
    burst_ratio = float(volume_array.max() / volume_mean) if volume_mean and len(volume_array) else 0.0
    phase_profile = build_phase_profile(close_array, volume_array)
    dominant_phase = max(phase_profile, key=lambda item: item.get("volume_share", 0.0))["phase"] if phase_profile else "early"

    meets_score = score["score"] >= safe_float(entry_rule.get("min_total_score", template.get("similarity_threshold", 0.82)))
    meets_volume = score["volume_score"] >= safe_float(entry_rule.get("min_volume_score", 0.72))
    meets_close = score["close_score"] >= safe_float(entry_rule.get("min_close_score", 0.68))
    meets_burst = burst_ratio >= safe_float(entry_rule.get("min_burst_volume_ratio", 1.4))
    preferred_phase = str(entry_rule.get("preferred_phase", dominant_phase))
    meets_phase = dominant_phase == preferred_phase

    trigger_side = template.get("trigger_side", "long")
    risk_plan = template.get("risk_plan", {}) or {}
    cancel_rule = risk_plan.get("cancel_rule", {}) or {}
    invalidation_move_pct = safe_float(cancel_rule.get("pattern_invalidation_move_pct", 0.0))
    invalidated_by_path = False
    if invalidation_move_pct > 0:
        if trigger_side == "short":
            invalidated_by_path = path_change_pct > invalidation_move_pct
        else:
            invalidated_by_path = path_change_pct < -invalidation_move_pct

    is_signal = all([meets_score, meets_volume, meets_close, meets_burst, meets_phase]) and not invalidated_by_path
    return {
        **score,
        "path_change_pct": path_change_pct,
        "burst_volume_ratio": burst_ratio,
        "dominant_phase": dominant_phase,
        "preferred_phase": preferred_phase,
        "invalidated_by_path": invalidated_by_path,
        "stop_loss_pct": safe_float(risk_plan.get("fixed_stop_loss_pct", 0.0)),
        "take_profit_pct": safe_float(risk_plan.get("take_profit_pct", 0.0)),
        "cancel_if_no_follow_seconds": int(cancel_rule.get("no_follow_seconds", 0) or 0),
        "cancel_if_no_follow_move_pct": safe_float(cancel_rule.get("no_follow_move_pct", 0.0)),
        "is_signal": is_signal,
        "action": f"open_{trigger_side}" if is_signal else "no_trade",
    }


def evaluate_trade_path(
    trigger_side: str,
    entry_price: float,
    future_close_values: Iterable[float],
    risk_plan: dict,
) -> dict:
    closes = np.array([safe_float(value) for value in future_close_values], dtype=float)
    if entry_price <= 0 or len(closes) == 0:
        return {
            "outcome": "no_data",
            "exit_reason": "no_data",
            "realized_move_pct": 0.0,
            "favorable_move_pct": 0.0,
            "adverse_move_pct": 0.0,
            "seconds_to_exit": 0,
        }

    stop_pct = safe_float(risk_plan.get("fixed_stop_loss_pct", 0.0))
    take_pct = safe_float(risk_plan.get("take_profit_pct", 0.0))
    cancel_rule = risk_plan.get("cancel_rule", {}) or {}
    no_follow_seconds = int(cancel_rule.get("no_follow_seconds", 0) or 0)
    no_follow_move_pct = safe_float(cancel_rule.get("no_follow_move_pct", 0.0))

    if trigger_side == "short":
        moves = (1.0 - closes / entry_price) * 100
    else:
        moves = (closes / entry_price - 1.0) * 100

    favorable_move_pct = float(np.max(moves)) if len(moves) else 0.0
    adverse_move_pct = float(abs(np.min(moves))) if len(moves) else 0.0
    exit_index = len(moves) - 1
    exit_reason = "time_exit"

    for idx, move_pct in enumerate(moves):
        if stop_pct and move_pct <= -stop_pct:
            exit_index = idx
            exit_reason = "fixed_stop"
            break
        if take_pct and move_pct >= take_pct:
            exit_index = idx
            exit_reason = "take_profit"
            break
        if no_follow_seconds and idx + 1 >= no_follow_seconds and no_follow_move_pct:
            best_so_far = float(np.max(moves[:idx + 1]))
            if best_so_far < no_follow_move_pct:
                exit_index = idx
                exit_reason = "cancel_no_follow"
                break

    realized_move_pct = float(moves[exit_index])
    return {
        "outcome": "win" if realized_move_pct > 0 else "loss",
        "exit_reason": exit_reason,
        "realized_move_pct": realized_move_pct,
        "favorable_move_pct": favorable_move_pct,
        "adverse_move_pct": adverse_move_pct,
        "seconds_to_exit": int(exit_index + 1),
    }


def save_realtime_matcher_template(template: dict, root: Path) -> Path:
    target_dir = root / ".local_ai" / "realtime_matchers"
    target_dir.mkdir(parents=True, exist_ok=True)
    symbol = (template.get("symbol") or "market").lower()
    timestamp = re.sub(r"[^0-9]", "", template.get("selection_end_ts", ""))[:14] or "latest"
    target_path = target_dir / f"{symbol}_formation_1s_matcher_{timestamp}.json"
    target_path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    return target_path


def safe_float(value: object) -> float:
    try:
        output = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(output):
        return 0.0
    return output


def cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    left_vec = np.array([safe_float(value) for value in left], dtype=float)
    right_vec = np.array([safe_float(value) for value in right], dtype=float)
    if len(left_vec) != len(right_vec) or len(left_vec) == 0:
        return 0.0
    denom = np.linalg.norm(left_vec) * np.linalg.norm(right_vec)
    if denom == 0:
        return 0.0
    return float(np.dot(left_vec, right_vec) / denom)


def build_phase_profile(close_array: np.ndarray, volume_array: np.ndarray) -> list[dict]:
    if len(close_array) == 0 or len(volume_array) == 0:
        return []
    total_volume = float(volume_array.sum())
    segments = np.array_split(np.arange(len(close_array)), 3)
    labels = ["early", "mid", "late"]
    profile = []
    for label, segment in zip(labels, segments):
        if len(segment) == 0:
            continue
        segment_close = close_array[segment]
        segment_volume = volume_array[segment]
        base_close = float(segment_close[0]) if len(segment_close) else 0.0
        end_close = float(segment_close[-1]) if len(segment_close) else 0.0
        profile.append(
            {
                "phase": label,
                "seconds": int(len(segment)),
                "volume_share": float(segment_volume.sum() / total_volume) if total_volume else 0.0,
                "volume_mean": float(segment_volume.mean()) if len(segment_volume) else 0.0,
                "return_pct": (end_close / base_close - 1.0) * 100 if base_close else 0.0,
            }
        )
    return profile


def build_entry_rule(
    trigger_side: str,
    expected_move_pct: float,
    similarity_threshold: float,
    burst_ratio: float,
    phase_profile: list[dict],
) -> dict:
    dominant_phase = max(phase_profile, key=lambda item: item.get("volume_share", 0.0))["phase"] if phase_profile else "late"
    move_threshold = max(0.03, abs(expected_move_pct) * 0.2)
    return {
        "trigger_side": trigger_side,
        "preferred_phase": dominant_phase,
        "min_total_score": max(0.78, similarity_threshold),
        "min_volume_score": 0.72,
        "min_close_score": 0.66,
        "min_burst_volume_ratio": max(1.25, burst_ratio * 0.72),
        "min_expected_follow_move_pct": move_threshold,
        "action_on_match": f"open_{trigger_side}",
    }


def estimate_observed_risk(close_array: np.ndarray, trigger_side: str) -> dict:
    if len(close_array) == 0:
        return {"adverse_excursion_pct": 0.0, "favorable_excursion_pct": 0.0}
    entry_price = float(close_array[-1])
    if entry_price <= 0:
        return {"adverse_excursion_pct": 0.0, "favorable_excursion_pct": 0.0}
    if trigger_side == "short":
        moves = (1.0 - close_array / entry_price) * 100
    else:
        moves = (close_array / entry_price - 1.0) * 100
    return {
        "adverse_excursion_pct": float(abs(np.min(moves))) if len(moves) else 0.0,
        "favorable_excursion_pct": float(np.max(moves)) if len(moves) else 0.0,
    }


def build_risk_plan(
    trigger_side: str,
    expected_move_pct: float,
    burst_ratio: float,
    observed_risk_pct: float,
) -> dict:
    expected_abs = abs(expected_move_pct)
    fixed_stop = max(0.12, min(0.75, max(observed_risk_pct * 1.25, expected_abs * 0.35)))
    take_profit = max(fixed_stop * 1.35, expected_abs * 0.75, 0.18)
    no_follow_move = max(0.04, min(take_profit * 0.35, expected_abs * 0.25 if expected_abs else 0.08))
    invalidation_move = max(0.05, fixed_stop * 0.55)
    return {
        "trigger_side": trigger_side,
        "fixed_stop_loss_pct": round(float(fixed_stop), 4),
        "take_profit_pct": round(float(take_profit), 4),
        "min_reward_risk": round(float(take_profit / fixed_stop), 3) if fixed_stop else 0.0,
        "burst_volume_ratio_reference": round(float(burst_ratio), 4),
        "cancel_rule": {
            "no_follow_seconds": 20,
            "no_follow_move_pct": round(float(no_follow_move), 4),
            "pattern_invalidation_move_pct": round(float(invalidation_move), 4),
        },
    }
