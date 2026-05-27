"""
ZeroChainAI — AI Agent: Claude Opus 4
Anthropic API integration with structured output.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from ...models import AgentResult, Finding, Severity
from ..prompts import get_system_prompt, get_user_prompt

logger = logging.getLogger("zeroscan.agent.claude")

MAX_TOKENS = 8192
TIMEOUT    = 90.0  # seconds


def _get_api_key() -> str:
    """Load from Vault or env."""
    vault_token = os.getenv("VAULT_TOKEN")
    if vault_token:
        try:
            import hvac
            client = hvac.Client(
                url=os.getenv("VAULT_ADDR", "http://127.0.0.1:8200"),
                token=vault_token,
            )
            sec = client.secrets.kv.v2.read_secret_version(
                path="zeroscan", mount_point="secret"
            )
            return sec["data"]["data"]["anthropic_key"]
        except Exception as e:
            logger.warning(f"Vault read failed: {e}")
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set and Vault unavailable")
    return key


async def run(
    code: str,
    language: str,
    contract_name: str = "",
    model: str = "claude-opus-4-5",
    threat_model_context: Optional[dict] = None,
) -> AgentResult:
    """Run Claude Opus 4 analysis. Returns AgentResult."""
    start = time.perf_counter()
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=_get_api_key())

        system = get_system_prompt(language)
        user   = get_user_prompt(code, language, contract_name,
                                 threat_model_context=threat_model_context)

        response = await client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
            timeout=TIMEOUT,
        )

        raw_text = response.content[0].text.strip()
        # Extract JSON if wrapped in markdown
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_text:
            raw_text = raw_text.split("```")[1].split("```")[0].strip()

        data = json.loads(raw_text)
        findings = _parse_findings(data.get("findings", []), agent="claude")
        latency  = int((time.perf_counter() - start) * 1000)

        logger.info(
            f"Claude done: score={data.get('risk_score', 0):.1f} "
            f"findings={len(findings)} latency={latency}ms"
        )
        return AgentResult(
            agent="claude",
            model=model,
            risk_score=float(data.get("risk_score", 0)),
            findings=findings,
            summary=data.get("summary", ""),
            latency_ms=latency,
        )
    except Exception as e:
        latency = int((time.perf_counter() - start) * 1000)
        logger.error(f"Claude agent error: {e}")
        return AgentResult(
            agent="claude", model=model,
            error=str(e), latency_ms=latency,
        )


def _parse_findings(raw: list, agent: str) -> list[Finding]:
    findings = []
    for i, item in enumerate(raw):
        try:
            findings.append(Finding(
                id=item.get("id", f"{agent.upper()}-{i:03d}"),
                agent=agent,
                type=item.get("type", "unknown"),
                severity=Severity(item.get("severity", "info")),
                line_start=int(item.get("line_start", 0)),
                line_end=int(item.get("line_end", 0)),
                function=item.get("function", ""),
                description=item.get("description", ""),
                recommendation=item.get("recommendation", ""),
                cwe=item.get("cwe", ""),
                confidence=float(item.get("confidence", 0.5)),
            ))
        except Exception as e:
            logger.warning(f"Skipping malformed finding #{i}: {e}")
    return findings
