"""
ZeroChainAI — Anomaly Detection Engine
Adapted from QuantBrick analysis system (Soldier project).

This module provides the core pattern detection framework,
repurposed from market anomaly detection to code vulnerability detection.

Architecture:
- Z-score normalization (from QuantBrick) → applied to code metrics
- Energy classification (dormant/active/explosive) → vulnerability severity
- Rolling analysis windows → scanning code in chunks

Original: quant_brick.py (market candle analysis)
Adapted: code metric anomaly detection
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum

import numpy as np
import logging

logger = logging.getLogger("zerochainai.anomaly")


class Severity(Enum):
    """Vulnerability severity classification (maps to brick_class concept)."""
    INFO = "info"           # dormant — cosmetic, style issues
    LOW = "low"             # active — minor concerns
    MEDIUM = "medium"       # active — real issues, fixable
    HIGH = "high"           # explosive — serious vulnerability
    CRITICAL = "critical"   # explosive — 0-day / immediate exploit risk


@dataclass
class VulnerabilitySignal:
    """
    Atomic vulnerability detection unit.
    Adapted from QuantBrick (market activity unit) to code analysis.

    QuantBrick parallel:
    - energy → risk_score (combined metric intensity)
    - direction → exploit_direction (how the vuln can be exploited)
    - brick_class → severity (dormant→info, active→medium, explosive→critical)
    - z-scores → metric deviations from baseline
    """
    # Identity
    signal_id: str              # unique identifier
    detector: str               # which detector found this
    category: str               # "reentrancy", "access_control", etc.

    # Location
    file_path: str              # source file
    line_start: int             # start line
    line_end: int               # end line
    function_name: str = ""     # affected function

    # Classification (adapted from QuantBrick)
    severity: Severity = Severity.INFO
    risk_score: float = 0.0     # 0-10, maps to QuantBrick.energy
    confidence: float = 0.0     # 0-1, model confidence

    # Z-score metrics (from QuantBrick pattern)
    complexity_z: float = 0.0   # cyclomatic complexity z-score
    nesting_z: float = 0.0      # nesting depth z-score
    ext_calls_z: float = 0.0    # external calls z-score (maps to volume_z)
    state_changes_z: float = 0.0  # state mutations z-score (maps to price_z)

    # Details
    description: str = ""
    recommendation: str = ""
    cwe_id: str = ""            # CWE identifier
    references: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "detector": self.detector,
            "category": self.category,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "function_name": self.function_name,
            "severity": self.severity.value,
            "risk_score": round(self.risk_score, 2),
            "confidence": round(self.confidence, 2),
            "complexity_z": round(self.complexity_z, 2),
            "nesting_z": round(self.nesting_z, 2),
            "ext_calls_z": round(self.ext_calls_z, 2),
            "state_changes_z": round(self.state_changes_z, 2),
            "description": self.description,
            "recommendation": self.recommendation,
            "cwe_id": self.cwe_id,
            "references": self.references,
        }


def rolling_zscore(values: np.ndarray, lookback: int = 50) -> np.ndarray:
    """
    Z-score without lookahead bias.
    Directly ported from QuantBrick._rolling_zscore().

    Used to detect anomalous code metrics (complexity, call depth, etc.)
    relative to the baseline of the codebase.
    """
    if len(values) < 2:
        return np.zeros_like(values)

    min_periods = max(5, lookback // 4)
    result = np.zeros_like(values, dtype=np.float64)

    for i in range(1, len(values)):
        start = max(0, i - lookback)
        window = values[start:i]  # exclude current (no lookahead)

        if len(window) < min_periods:
            continue

        mean = np.mean(window)
        std = np.std(window)
        if std > 0:
            result[i] = (values[i] - mean) / std

    return result


def compute_risk_score(
    complexity_z: float,
    ext_calls_z: float,
    state_changes_z: float,
    nesting_z: float,
    mode: str = "geometric"
) -> float:
    """
    Compute combined risk score from z-score metrics.
    Adapted from QuantBrick energy calculation.

    QuantBrick parallel:
    - dollar_volume_z → ext_calls_z (interaction intensity)
    - price_change_z → state_changes_z (mutation intensity)
    - range_z → complexity_z (code complexity)
    """
    primary = abs(ext_calls_z) + abs(state_changes_z)
    secondary = abs(complexity_z) + abs(nesting_z)

    if mode == "geometric":
        score = np.sqrt(primary * secondary) if primary > 0 and secondary > 0 else 0
    elif mode == "product":
        score = primary * secondary
    else:  # sum
        score = primary + secondary

    return float(min(10.0, score))


def classify_severity(risk_score: float) -> Severity:
    """
    Map risk score to severity.
    Adapted from QuantBrick.brick_class classification:
    - dormant (energy < 1.0) → INFO/LOW
    - active (1.0 ≤ energy < 2.5) → MEDIUM/HIGH
    - explosive (energy ≥ 2.5) → CRITICAL
    """
    if risk_score >= 4.0:
        return Severity.CRITICAL
    elif risk_score >= 2.5:
        return Severity.HIGH
    elif risk_score >= 1.5:
        return Severity.MEDIUM
    elif risk_score >= 0.5:
        return Severity.LOW
    else:
        return Severity.INFO


class AnomalyDetector:
    """
    Code anomaly detector using z-score analysis.
    Adapted from the QuantBrick + StrategyEngine pattern.

    Strategy:
    1. Parse code into "units" (functions/blocks) — like candles
    2. Compute metrics for each unit — like OHLCV
    3. Z-score normalize — exactly like QuantBrick
    4. Classify anomalies — maps to energy/brick_class
    """

    def __init__(self, lookback: int = 50, energy_mode: str = "geometric"):
        self.lookback = lookback
        self.energy_mode = energy_mode
        self.baseline_metrics: Dict[str, np.ndarray] = {}

    def analyze_function_metrics(
        self,
        functions: List[Dict[str, Any]]
    ) -> List[VulnerabilitySignal]:
        """
        Analyze a list of function metrics and flag anomalies.

        Each function dict should contain:
        - name: str
        - file: str
        - line_start, line_end: int
        - complexity: int (cyclomatic)
        - nesting_depth: int
        - external_calls: int
        - state_changes: int
        """
        if not functions:
            return []

        # Extract metric arrays (like building candle arrays)
        complexities = np.array([f.get("complexity", 0) for f in functions], dtype=float)
        nestings = np.array([f.get("nesting_depth", 0) for f in functions], dtype=float)
        ext_calls = np.array([f.get("external_calls", 0) for f in functions], dtype=float)
        state_muts = np.array([f.get("state_changes", 0) for f in functions], dtype=float)

        # Z-score normalization (QuantBrick pattern)
        complexity_zs = rolling_zscore(complexities, self.lookback)
        nesting_zs = rolling_zscore(nestings, self.lookback)
        ext_calls_zs = rolling_zscore(ext_calls, self.lookback)
        state_muts_zs = rolling_zscore(state_muts, self.lookback)

        signals: List[VulnerabilitySignal] = []

        for i, func in enumerate(functions):
            risk = compute_risk_score(
                complexity_z=complexity_zs[i],
                ext_calls_z=ext_calls_zs[i],
                state_changes_z=state_muts_zs[i],
                nesting_z=nesting_zs[i],
                mode=self.energy_mode,
            )

            severity = classify_severity(risk)

            # Only report medium+ anomalies (filter noise)
            if severity.value in ("info", "low"):
                continue

            signal = VulnerabilitySignal(
                signal_id=f"ANOM-{i:04d}",
                detector="anomaly_zscore",
                category="complexity_anomaly",
                file_path=func.get("file", "unknown"),
                line_start=func.get("line_start", 0),
                line_end=func.get("line_end", 0),
                function_name=func.get("name", "unknown"),
                severity=severity,
                risk_score=risk,
                confidence=min(1.0, risk / 5.0),
                complexity_z=complexity_zs[i],
                nesting_z=nesting_zs[i],
                ext_calls_z=ext_calls_zs[i],
                state_changes_z=state_muts_zs[i],
                description=(
                    f"Function '{func.get('name', '?')}' shows anomalous metrics: "
                    f"complexity_z={complexity_zs[i]:.1f}, "
                    f"ext_calls_z={ext_calls_zs[i]:.1f}, "
                    f"state_changes_z={state_muts_zs[i]:.1f}"
                ),
                recommendation="Manual review recommended. High metric deviation suggests potential vulnerability.",
            )
            signals.append(signal)

        logger.info(
            "Anomaly scan: %d functions → %d signals (med+)",
            len(functions), len(signals)
        )
        return signals
