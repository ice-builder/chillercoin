"""
ZeroChainAI — Vote Aggregator
Merges findings from all 4 agents, deduplicates, weights scores.
"""
from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import List
import uuid

from ..models import (
    AgentResult, Finding, MergedFinding, ScanReport, ScanRequest, Severity
)

logger = logging.getLogger("zeroscan.aggregator")

# Agent weights in final score (must sum to 1.0)
AGENT_WEIGHTS = {
    "claude":  0.40,
    "gemini":  0.35,
    "openai":  0.25,
    "slither": 0.0,   # Slither doesn't affect score directly — it boosts findings
}

# Line-proximity threshold: findings within N lines = same location
LINE_MERGE_WINDOW = 5

SEVERITY_ORDER = {
    Severity.CRITICAL: 5,
    Severity.HIGH:     4,
    Severity.MEDIUM:   3,
    Severity.LOW:      2,
    Severity.INFO:     1,
}


def aggregate(
    agent_results: List[AgentResult],
    request: ScanRequest,
    scan_id: str,
    start_ts: datetime,
    duration_ms: int,
) -> ScanReport:
    """
    Aggregate results from all agents into a unified ScanReport.

    Algorithm:
    1. Collect all findings from all agents
    2. Group findings by (type, function, line_proximity)
    3. Keep only findings confirmed by ≥ 1 AI agent (or Slither alone = deterministic)
    4. Compute weighted risk score from AI agents
    5. Generate executive summary
    """
    # Filter successful agents
    successful = [r for r in agent_results if r.error is None]
    ai_agents  = [r for r in successful if r.agent != "slither"]
    slither_r  = next((r for r in successful if r.agent == "slither"), None)

    # Compute weighted risk score (AI agents only)
    risk_score = _weighted_risk(ai_agents)

    # Merge findings
    all_findings = []
    for result in successful:
        all_findings.extend(result.findings)

    merged = _merge_findings(all_findings, ai_agents, slither_r)

    # Sort by severity desc, then risk_score desc
    merged.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 0), f.risk_score), reverse=True)

    # Severity summary = highest severity found
    severity_summary = merged[0].severity if merged else Severity.INFO

    # Ensemble consensus: avg % of agents agreeing on each HIGH+ finding
    consensus = _compute_consensus(merged, len(ai_agents))

    # Counts
    counts = {s: sum(1 for f in merged if f.severity == s) for s in Severity}

    # Executive summary from best-performing agent (Claude)
    exec_summary = _build_executive_summary(
        merged, risk_score, ai_agents, request
    )

    return ScanReport(
        scan_id=scan_id,
        timestamp=start_ts,
        contract_name=request.contract_name,
        language=request.language.value,
        chain=request.chain,
        risk_score=round(risk_score, 2),
        severity_summary=severity_summary,
        findings=merged,
        total_findings=len(merged),
        critical_count=counts[Severity.CRITICAL],
        high_count=counts[Severity.HIGH],
        medium_count=counts[Severity.MEDIUM],
        low_count=counts[Severity.LOW],
        agents_used=[r.agent for r in successful],
        agent_results=agent_results,
        ensemble_consensus=round(consensus, 2),
        executive_summary=exec_summary,
        scan_duration_ms=duration_ms,
    )


def _weighted_risk(ai_agents: List[AgentResult]) -> float:
    """Compute weighted average risk score from AI agents."""
    if not ai_agents:
        return 0.0
    total_weight = sum(AGENT_WEIGHTS.get(r.agent, 0) for r in ai_agents)
    if total_weight == 0:
        return sum(r.risk_score for r in ai_agents) / len(ai_agents)
    return sum(
        r.risk_score * AGENT_WEIGHTS.get(r.agent, 0)
        for r in ai_agents
    ) / total_weight


def _finding_key(f: Finding) -> str:
    """
    Generate a grouping key for a finding.
    Groups findings of the same type near the same location.
    """
    line_bucket = (f.line_start // LINE_MERGE_WINDOW) * LINE_MERGE_WINDOW
    return f"{f.type}|{f.function}|{line_bucket}"


def _merge_findings(
    all_findings: List[Finding],
    ai_agents: List[AgentResult],
    slither_result: "AgentResult | None",
) -> List[MergedFinding]:
    """
    Deduplicate and merge findings from all agents.

    Rules:
    - Slither findings with HIGH/CRITICAL always included (deterministic)
    - AI findings included if ≥ 1 AI agent confirmed them
    - When multiple agents find same thing: take highest severity, avg confidence
    """
    # Group by key
    groups: dict[str, list[Finding]] = defaultdict(list)
    for f in all_findings:
        groups[_finding_key(f)].append(f)

    merged: List[MergedFinding] = []
    ai_agent_names = {r.agent for r in ai_agents}

    for key, group in groups.items():
        ai_findings     = [f for f in group if f.agent in ai_agent_names]
        slither_finding = next((f for f in group if f.agent == "slither"), None)

        # Slither HIGH/CRITICAL → always include even without AI confirmation
        if slither_finding and SEVERITY_ORDER.get(slither_finding.severity, 0) >= 4:
            confirmed_by = {slither_finding.agent}
            confirmed_by.update(f.agent for f in ai_findings)
        elif ai_findings:
            # Need at least 1 AI agent for non-Slither findings
            confirmed_by = {f.agent for f in ai_findings}
            if slither_finding:
                confirmed_by.add(slither_finding.agent)
        else:
            continue  # Only low-severity Slither finding — skip unless solo

        # Best finding: highest severity
        best = max(group, key=lambda f: SEVERITY_ORDER.get(f.severity, 0))

        # Aggregate confidence across all confirmations
        avg_confidence = sum(f.confidence for f in group) / len(group)

        # Compute risk for this finding
        finding_risk = _finding_risk(best.severity, avg_confidence, len(confirmed_by))

        # Per-agent descriptions
        details = {f.agent: f.description for f in group if f.description}

        merged.append(MergedFinding(
            id=f"ZCA-{hashlib.sha256(key.encode()).hexdigest()[:8].upper()}",
            type=best.type,
            severity=best.severity,
            line_start=best.line_start,
            line_end=best.line_end,
            function=best.function,
            description=_best_description(group),
            recommendation=best.recommendation,
            cwe=best.cwe or next((f.cwe for f in group if f.cwe), ""),
            risk_score=round(finding_risk, 2),
            confidence=round(avg_confidence, 2),
            confirmed_by=sorted(confirmed_by),
            details=details,
        ))

    return merged


def _finding_risk(severity: Severity, confidence: float, confirmations: int) -> float:
    base = {
        Severity.CRITICAL: 9.0, Severity.HIGH: 7.0,
        Severity.MEDIUM: 5.0,   Severity.LOW: 2.0, Severity.INFO: 0.5,
    }.get(severity, 1.0)
    # Boost for multiple confirmations
    boost = min(1.0, confirmations * 0.15)
    return base * (0.7 + 0.3 * confidence) * (1 + boost)


def _best_description(group: List[Finding]) -> str:
    """Pick the most detailed description from the group."""
    return max(group, key=lambda f: len(f.description)).description


def _compute_consensus(merged: List[MergedFinding], n_ai: int) -> float:
    """Average % of AI agents that agreed on each HIGH+ finding."""
    if not merged or n_ai == 0:
        return 0.0
    high_plus = [f for f in merged if SEVERITY_ORDER.get(f.severity, 0) >= 4]
    if not high_plus:
        return 1.0  # no high findings = perfect consensus (nothing found)
    ai_agent_names_count = lambda f: sum(1 for a in f.confirmed_by if a != "slither")
    avg = sum(ai_agent_names_count(f) / n_ai for f in high_plus) / len(high_plus)
    return avg


def _build_executive_summary(
    findings: List[MergedFinding],
    risk_score: float,
    ai_agents: List[AgentResult],
    request: ScanRequest,
) -> str:
    """Build executive summary from best AI agent result."""
    # Find Claude's summary (most thorough)
    for agent_name in ("claude", "gemini", "openai"):
        agent = next((r for r in ai_agents if r.agent == agent_name and r.summary), None)
        if agent:
            return agent.summary

    # Fallback: generate from findings
    if not findings:
        return "No significant vulnerabilities detected in the analyzed contract."

    critical = sum(1 for f in findings if f.severity == Severity.CRITICAL)
    high     = sum(1 for f in findings if f.severity == Severity.HIGH)
    name     = request.contract_name or "The contract"

    severity_str = "CRITICAL" if critical > 0 else ("HIGH" if high > 0 else "MEDIUM")
    return (
        f"{name} presents {severity_str} risk (score: {risk_score:.1f}/10). "
        f"Found {len(findings)} issue(s): "
        f"{critical} critical, {high} high severity. "
        f"Immediate remediation recommended before deployment."
    )
