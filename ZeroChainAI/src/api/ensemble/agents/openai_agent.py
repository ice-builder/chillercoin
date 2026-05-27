"""
ZeroChainAI — AI Agent: OpenAI o3
OpenAI API integration with structured JSON output.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from ...models import AgentResult, Finding, Severity
from ..prompts import get_system_prompt, get_user_prompt
from .claude import _parse_findings  # reuse parser

logger = logging.getLogger("zeroscan.agent.openai")

MAX_TOKENS = 8192
TIMEOUT    = 120.0  # o3 reasoning can be slow


def _get_api_key() -> str:
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
            return sec["data"]["data"]["openai_key"]
        except Exception as e:
            logger.warning(f"Vault read failed: {e}")
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set and Vault unavailable")
    return key


async def run(
    code: str,
    language: str,
    contract_name: str = "",
    model: str = "o3",
    threat_model_context: Optional[dict] = None,
) -> AgentResult:
    """Run OpenAI o3 analysis. o3 has deep reasoning for logic bugs."""
    start = time.perf_counter()
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=_get_api_key(), timeout=TIMEOUT)

        system = get_system_prompt(language)
        user   = get_user_prompt(code, language, contract_name,
                                 threat_model_context=threat_model_context)

        # o3 supports structured JSON output via response_format
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            max_completion_tokens=MAX_TOKENS,
            response_format={"type": "json_object"},
            # o3 reasoning effort: "high" for thorough analysis
            reasoning_effort="high",
        )

        raw_text = response.choices[0].message.content.strip()
        data     = json.loads(raw_text)
        findings = _parse_findings(data.get("findings", []), agent="openai")
        latency  = int((time.perf_counter() - start) * 1000)

        logger.info(
            f"OpenAI o3 done: score={data.get('risk_score', 0):.1f} "
            f"findings={len(findings)} latency={latency}ms "
            f"reasoning_tokens={response.usage.completion_tokens_details.reasoning_tokens if hasattr(response.usage, 'completion_tokens_details') else 'N/A'}"
        )
        return AgentResult(
            agent="openai",
            model=model,
            risk_score=float(data.get("risk_score", 0)),
            findings=findings,
            summary=data.get("summary", ""),
            latency_ms=latency,
        )
    except Exception as e:
        latency = int((time.perf_counter() - start) * 1000)
        logger.error(f"OpenAI agent error: {e}")
        return AgentResult(
            agent="openai", model=model,
            error=str(e), latency_ms=latency,
        )
