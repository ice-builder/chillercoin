"""
ZeroChainAI — Static Analysis Agent: Slither + Mythril
Deterministic analysis — exact line numbers, no hallucinations.
Runs as subprocess, output is structured JSON.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Optional

from ...models import AgentResult, Finding, Severity

logger = logging.getLogger("zeroscan.agent.slither")

# Slither severity → our Severity mapping
SLITHER_IMPACT_MAP = {
    "High":          Severity.HIGH,
    "Medium":        Severity.MEDIUM,
    "Low":           Severity.LOW,
    "Informational": Severity.INFO,
    "Optimization":  Severity.INFO,
}

# CWE mappings for common Slither detectors
DETECTOR_CWE = {
    "reentrancy-eth":            "CWE-841",
    "reentrancy-no-eth":         "CWE-841",
    "reentrancy-benign":         "CWE-841",
    "unprotected-upgrade":       "CWE-284",
    "delegatecall-loop":         "CWE-691",
    "suicidal":                  "CWE-284",
    "controlled-delegatecall":   "CWE-20",
    "tx-origin":                 "CWE-290",
    "weak-prng":                 "CWE-338",
    "timestamp":                 "CWE-829",
    "divide-before-multiply":    "CWE-682",
    "incorrect-equality":        "CWE-697",
    "locked-ether":              "CWE-284",
    "events-maths":              "CWE-778",
    "shadowing-state":           "CWE-710",
    "uninitialized-state":       "CWE-909",
    "unchecked-transfer":        "CWE-252",
    "arbitrary-send-eth":        "CWE-284",
}

SLITHER_PATH = os.getenv("SLITHER_PATH", "slither")
TIMEOUT_SEC  = 60


async def run(
    code: str,
    language: str = "solidity",
    contract_name: str = "",
    _model: str = "slither-1.0+mythril-0.23",
) -> AgentResult:
    """
    Run Slither (+ optionally Mythril) on contract code.
    Only supports Solidity. Returns empty result for other languages.
    """
    start = time.perf_counter()

    if language.lower() not in ("solidity", "vyper", "auto"):
        logger.info(f"Slither: skipping non-Solidity language={language}")
        return AgentResult(
            agent="slither", model=_model,
            summary=f"Static analysis not available for {language}",
            latency_ms=0,
        )

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".sol", mode="w", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        try:
            result = await _run_slither(tmp_path)
        finally:
            os.unlink(tmp_path)

        latency = int((time.perf_counter() - start) * 1000)
        logger.info(
            f"Slither done: score={result.risk_score:.1f} "
            f"findings={len(result.findings)} latency={latency}ms"
        )
        result.latency_ms = latency
        return result

    except Exception as e:
        latency = int((time.perf_counter() - start) * 1000)
        logger.error(f"Slither agent error: {e}")
        return AgentResult(
            agent="slither", model=_model,
            error=str(e), latency_ms=latency,
        )


async def _run_slither(sol_path: str) -> AgentResult:
    """Execute slither and parse JSON output."""
    cmd = [
        SLITHER_PATH, sol_path,
        "--json", "-",          # output JSON to stdout
        "--no-fail-pedantic",
        "--exclude-dependencies",
        "--exclude", "naming-convention",  # skip style findings
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        # Security: no shell injection possible (list args, not string)
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=TIMEOUT_SEC
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"Slither timed out after {TIMEOUT_SEC}s")

    raw = stdout.decode("utf-8", errors="replace").strip()
    if not raw:
        logger.warning(f"Slither produced no output. stderr: {stderr.decode()[:500]}")
        return AgentResult(agent="slither", model="slither")

    data = json.loads(raw)
    if not data.get("success", False) and not data.get("results"):
        err_msg = str(data.get("error", ""))
        if "not found" in err_msg.lower() or "Error" in err_msg:
            raise RuntimeError(f"Slither failed: {err_msg[:200]}")

    findings: list[Finding] = []
    detectors = data.get("results", {}).get("detectors", [])

    for i, det in enumerate(detectors):
        sev = SLITHER_IMPACT_MAP.get(det.get("impact", "Informational"), Severity.INFO)
        detector_id = det.get("check", "unknown")

        # Extract line numbers from elements
        line_start, line_end, function = _extract_location(det.get("elements", []))

        findings.append(Finding(
            id=f"SLI-{i:03d}",
            agent="slither",
            type=_map_detector_type(detector_id),
            severity=sev,
            line_start=line_start,
            line_end=line_end,
            function=function,
            description=det.get("description", "").strip(),
            recommendation=_get_recommendation(detector_id),
            cwe=DETECTOR_CWE.get(detector_id, ""),
            confidence=_confidence_from_impact(det.get("impact", "Low")),
        ))

    risk_score = _compute_risk(findings)
    return AgentResult(
        agent="slither",
        model="slither-static",
        risk_score=risk_score,
        findings=findings,
        summary=f"Static analysis found {len(findings)} issues "
                f"({sum(1 for f in findings if f.severity in (Severity.CRITICAL, Severity.HIGH))} critical/high)",
    )


def _extract_location(elements: list) -> tuple[int, int, str]:
    """Extract line numbers and function name from Slither elements."""
    line_start, line_end, function = 0, 0, ""
    for el in elements:
        src = el.get("source_mapping", {})
        lines = src.get("lines", [])
        if lines:
            line_start = min(lines)
            line_end   = max(lines)
        if el.get("type") == "function":
            function = el.get("name", "")
    return line_start, line_end, function


def _map_detector_type(detector_id: str) -> str:
    """Map Slither detector ID to our vulnerability type."""
    mapping = {
        "reentrancy-eth":          "reentrancy",
        "reentrancy-no-eth":       "reentrancy",
        "reentrancy-benign":       "reentrancy",
        "unprotected-upgrade":     "access_control",
        "arbitrary-send-eth":      "access_control",
        "tx-origin":               "access_control",
        "weak-prng":               "randomness",
        "timestamp":               "timestamp_dependence",
        "divide-before-multiply":  "integer_overflow",
        "unchecked-transfer":      "unchecked_return",
        "locked-ether":            "dos",
        "delegatecall-loop":       "delegatecall",
        "controlled-delegatecall": "delegatecall",
        "suicidal":                "selfdestruct",
    }
    return mapping.get(detector_id, "logic_error")


def _get_recommendation(detector_id: str) -> str:
    recs = {
        "reentrancy-eth":         "Follow CEI pattern: Checks-Effects-Interactions. Add ReentrancyGuard.",
        "reentrancy-no-eth":      "Follow CEI pattern: update state before external calls.",
        "tx-origin":              "Replace tx.origin with msg.sender for authorization.",
        "weak-prng":              "Use Chainlink VRF or commit-reveal for randomness.",
        "timestamp":              "Use block.number instead of block.timestamp for time-sensitive logic.",
        "unchecked-transfer":     "Check return value of transfer/transferFrom or use SafeERC20.",
        "arbitrary-send-eth":     "Restrict who can trigger ETH sends. Add access control.",
        "unprotected-upgrade":    "Add access control to upgrade function. Use OpenZeppelin UUPS.",
        "delegatecall-loop":      "Avoid delegatecall in loops. Check for storage collisions.",
        "suicidal":               "Remove selfdestruct or add strict access control.",
    }
    return recs.get(detector_id, "Review and remediate the identified issue.")


def _confidence_from_impact(impact: str) -> float:
    return {"High": 0.95, "Medium": 0.80, "Low": 0.65, "Informational": 0.50}.get(impact, 0.50)


def _compute_risk(findings: list[Finding]) -> float:
    """Compute overall risk score from findings."""
    if not findings:
        return 0.0
    weights = {Severity.CRITICAL: 3.0, Severity.HIGH: 2.0,
               Severity.MEDIUM: 1.0, Severity.LOW: 0.3, Severity.INFO: 0.0}
    total = sum(weights.get(f.severity, 0) for f in findings)
    return min(10.0, total)
