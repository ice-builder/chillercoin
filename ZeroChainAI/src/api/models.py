"""
ZeroChainAI Orchestrator — API Models
Pydantic schemas for requests and responses.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field, field_validator


class Language(str, Enum):
    SOLIDITY = "solidity"
    RUST     = "rust"
    MOVE     = "move"
    VYPER    = "vyper"
    AUTO     = "auto"   # detect from extension/content


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH     = "high"
    MEDIUM   = "medium"
    LOW      = "low"
    INFO     = "info"


class ScanRequest(BaseModel):
    """Incoming scan request from Mac client."""
    code: str = Field(..., min_length=10, max_length=200_000,
                      description="Smart contract source code")
    language: Language = Language.AUTO
    contract_name: Optional[str] = Field(None, max_length=100)
    chain: Optional[str] = Field(None, max_length=50,
                                  description="e.g. ethereum, bsc, solana")
    options: Dict[str, Any] = Field(default_factory=dict)
    # If set — skip cache and force fresh scan
    force_rescan: bool = False

    @field_validator("code")
    @classmethod
    def sanitize_code(cls, v: str) -> str:
        """Basic input sanitization — strip null bytes."""
        return v.replace("\x00", "").strip()


class Finding(BaseModel):
    """Single vulnerability finding from one agent."""
    id: str
    agent: str                  # claude | gemini | openai | slither
    type: str                   # reentrancy, access_control, etc.
    severity: Severity
    line_start: int
    line_end: int
    function: str = ""
    description: str
    recommendation: str
    cwe: str = ""               # CWE-XXX
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class AgentResult(BaseModel):
    """Raw result from a single AI agent."""
    agent: str
    model: str
    risk_score: float = Field(0.0, ge=0.0, le=10.0)
    findings: List[Finding] = Field(default_factory=list)
    summary: str = ""
    latency_ms: int = 0
    error: Optional[str] = None   # set if agent failed


class MergedFinding(BaseModel):
    """Deduplicated finding after vote aggregation."""
    id: str
    type: str
    severity: Severity
    line_start: int
    line_end: int
    function: str = ""
    description: str
    recommendation: str
    cwe: str = ""
    risk_score: float            # weighted 0-10
    confidence: float            # % of agents that agreed
    confirmed_by: List[str]      # agents that found this
    details: Dict[str, str] = Field(default_factory=dict)  # per-agent descriptions


class ScanReport(BaseModel):
    """Final unified scan report returned to the client."""
    scan_id: str
    timestamp: datetime
    contract_name: Optional[str]
    language: str
    chain: Optional[str]

    # Risk
    risk_score: float = Field(0.0, ge=0.0, le=10.0)
    severity_summary: Severity   # overall severity
    findings: List[MergedFinding]

    # Stats
    total_findings: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int

    # Agent details
    agents_used: List[str]
    agent_results: List[AgentResult]
    ensemble_consensus: float    # 0-1, how much agents agreed

    # Summary
    executive_summary: str
    scan_duration_ms: int

    # Cached result?
    from_cache: bool = False

    def to_markdown(self) -> str:
        """Format report as Markdown for Telegram/CLI output."""
        severity_emoji = {
            "critical": "🔴", "high": "🟠",
            "medium": "🟡", "low": "🟢", "info": "⚪"
        }
        lines = [
            f"# 🛡️ ZeroChainAI Security Report",
            f"**Contract:** {self.contract_name or 'Unknown'}",
            f"**Chain:** {self.chain or 'N/A'} | **Language:** {self.language}",
            f"**Scan ID:** `{self.scan_id}`",
            f"**Date:** {self.timestamp.strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            f"## Risk Assessment",
            f"**Overall Risk Score:** {self.risk_score:.1f}/10",
            f"**Severity:** {severity_emoji.get(self.severity_summary, '⚪')} **{self.severity_summary.upper()}**",
            f"**Ensemble Consensus:** {self.ensemble_consensus:.0%}",
            "",
            f"## Findings Summary",
            f"- 🔴 Critical: {self.critical_count}",
            f"- 🟠 High: {self.high_count}",
            f"- 🟡 Medium: {self.medium_count}",
            f"- 🟢 Low: {self.low_count}",
            "",
            f"## Executive Summary",
            self.executive_summary,
            "",
            "## Detailed Findings",
        ]
        for i, f in enumerate(self.findings, 1):
            em = severity_emoji.get(f.severity, "⚪")
            lines += [
                f"### {i}. {em} [{f.severity.upper()}] {f.type}",
                f"**Location:** `{f.function}` (lines {f.line_start}–{f.line_end})",
                f"**Confidence:** {f.confidence:.0%} | **Confirmed by:** {', '.join(f.confirmed_by)}",
                f"**Risk Score:** {f.risk_score:.1f}/10",
                f"",
                f"**Description:** {f.description}",
                f"",
                f"**Recommendation:** {f.recommendation}",
                f"{'**CWE:** ' + f.cwe if f.cwe else ''}",
                "",
            ]
        lines += [
            "## Agents Used",
            *[f"- **{r.agent}** ({r.model}): score={r.risk_score:.1f}, "
              f"findings={len(r.findings)}, latency={r.latency_ms}ms"
              + (f" ⚠️ ERROR: {r.error}" if r.error else "")
              for r in self.agent_results],
        ]
        return "\n".join(lines)
