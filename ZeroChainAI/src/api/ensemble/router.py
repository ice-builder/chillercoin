"""
ZeroChainAI — Ensemble Router v2.0
──────────────────────────────────
Updated with Threat Model pre-analysis and CVE Learning Loop.

Flow:
1. Generate threat model from code (check known CVEs first)
2. Pass threat model context to ensemble prompts
3. Run all 4 agents in parallel
4. Aggregate results
5. Learn from findings → store in local CVE DB
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import List
import uuid

from ..models import AgentResult, ScanRequest, ScanReport
from .agents import claude, gemini, openai_agent, slither_agent
from .aggregator import aggregate
from ...scanner.threat_model import (
    BlockchainThreatModelGenerator,
    get_cve_store,
)

logger = logging.getLogger("zeroscan.router")

# Timeout for the entire ensemble (agents that exceed this are cancelled)
ENSEMBLE_TIMEOUT = 150.0  # seconds


async def run_ensemble(request: ScanRequest) -> ScanReport:
    """
    Run all 4 agents in parallel and return aggregated ScanReport.
    Now with Threat Model pre-analysis and CVE learning loop.
    """
    scan_id  = str(uuid.uuid4())
    start_ts = datetime.now(timezone.utc)
    t0       = time.perf_counter()

    lang = _detect_language(request.code, request.language.value)
    code_hash = hashlib.sha256(request.code.encode()).hexdigest()

    logger.info(
        f"[{scan_id}] Starting ensemble: lang={lang} "
        f"code_len={len(request.code)} agents=4"
    )

    # ── Step 1: Generate Threat Model (Habr methodology) ──────
    threat_model_context = None
    try:
        tmg = BlockchainThreatModelGenerator()
        threat_model = tmg.generate(
            request.code, lang, request.contract_name or ""
        )
        threat_model_context = threat_model.to_dict()

        known_count = threat_model.known_patterns_matched
        if known_count > 0:
            logger.info(
                f"[{scan_id}] Threat model: {known_count} known patterns "
                f"matched (checking these FIRST)"
            )
        logger.info(
            f"[{scan_id}] Threat model: risk={threat_model.risk_tier} "
            f"categories={threat_model.dominant_categories} "
            f"slices={len(threat_model.slices)}"
        )
    except Exception as e:
        logger.warning(f"[{scan_id}] Threat model generation failed: {e}")

    # ── Step 2: Build coroutines with threat model context ────
    coros = [
        claude.run(request.code, lang, request.contract_name or "",
                   threat_model_context=threat_model_context),
        gemini.run(request.code, lang, request.contract_name or "",
                   threat_model_context=threat_model_context),
        openai_agent.run(request.code, lang, request.contract_name or "",
                         threat_model_context=threat_model_context),
        slither_agent.run(request.code, lang, request.contract_name or ""),
    ]

    # ── Step 3: Run all in parallel ───────────────────────────
    try:
        results: List[AgentResult | BaseException] = await asyncio.wait_for(
            asyncio.gather(*coros, return_exceptions=True),
            timeout=ENSEMBLE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error(f"[{scan_id}] Ensemble timed out after {ENSEMBLE_TIMEOUT}s")
        results = [asyncio.TimeoutError("Ensemble timeout")] * 4

    # Convert exceptions to error AgentResults
    agent_names = ["claude", "gemini", "openai", "slither"]
    agent_results: List[AgentResult] = []
    for name, res in zip(agent_names, results):
        if isinstance(res, BaseException):
            logger.error(f"[{scan_id}] Agent {name} exception: {res}")
            agent_results.append(AgentResult(
                agent=name, model=name,
                error=str(res),
            ))
        else:
            agent_results.append(res)

    duration_ms = int((time.perf_counter() - t0) * 1000)

    # ── Step 4: Aggregate ─────────────────────────────────────
    report = aggregate(
        agent_results=agent_results,
        request=request,
        scan_id=scan_id,
        start_ts=start_ts,
        duration_ms=duration_ms,
    )

    # ── Step 5: Learning Loop — store findings for future ─────
    try:
        cve_store = get_cve_store()
        findings_for_learning = [
            {
                "type": f.type,
                "severity": f.severity,
                "function": f.function,
                "description": f.description,
            }
            for f in report.findings
            if f.severity in ("critical", "high", "medium")
        ]
        if findings_for_learning:
            cve_store.learn_from_findings(findings_for_learning, lang)
            logger.info(
                f"[{scan_id}] Learning loop: stored {len(findings_for_learning)} "
                f"patterns for future scans"
            )
        cve_store.record_scan(
            code_hash, report.total_findings,
            report.critical_count, report.high_count,
            threat_model.known_patterns_matched if threat_model_context else 0,
        )
    except Exception as e:
        logger.warning(f"[{scan_id}] Learning loop failed: {e}")

    logger.info(
        f"[{scan_id}] Ensemble complete: "
        f"risk={report.risk_score:.1f} findings={report.total_findings} "
        f"duration={duration_ms}ms consensus={report.ensemble_consensus:.0%}"
    )
    return report


def _detect_language(code: str, declared: str) -> str:
    """Auto-detect language if not specified."""
    if declared != "auto":
        return declared
    code_lower = code.lower()
    if "pragma solidity" in code_lower or "contract " in code_lower:
        return "solidity"
    if "#[program]" in code_lower or "use anchor_lang" in code_lower:
        return "rust"
    if "module " in code_lower and "fun " in code_lower:
        return "move"
    if "@external" in code_lower or "def " in code_lower and "uint256" in code_lower:
        return "vyper"
    return "solidity"  # default
