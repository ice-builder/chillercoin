"""
ZeroChainAI — AI Agent: Gemini 2.5 Pro
Google AI API integration.
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

logger = logging.getLogger("zeroscan.agent.gemini")

MAX_TOKENS = 8192
TIMEOUT    = 90.0


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
            return sec["data"]["data"]["google_key"]
        except Exception as e:
            logger.warning(f"Vault read failed: {e}")
    key = os.getenv("GOOGLE_API_KEY", "")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY not set and Vault unavailable")
    return key


async def run(
    code: str,
    language: str,
    contract_name: str = "",
    model: str = "gemini-2.5-pro-preview-05-06",
    threat_model_context: Optional[dict] = None,
) -> AgentResult:
    start = time.perf_counter()
    try:
        import google.generativeai as genai
        genai.configure(api_key=_get_api_key())

        system = get_system_prompt(language)
        user   = get_user_prompt(code, language, contract_name,
                                 threat_model_context=threat_model_context)

        gemini_model = genai.GenerativeModel(
            model_name=model,
            system_instruction=system,
            generation_config=genai.GenerationConfig(
                temperature=0.1,       # low temp for deterministic analysis
                max_output_tokens=MAX_TOKENS,
                response_mime_type="application/json",  # force JSON output
            ),
        )

        # Use asyncio executor for sync Gemini SDK
        import asyncio
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: gemini_model.generate_content(user)),
            timeout=TIMEOUT,
        )

        raw_text = response.text.strip()
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_text:
            raw_text = raw_text.split("```")[1].split("```")[0].strip()

        data     = json.loads(raw_text)
        findings = _parse_findings(data.get("findings", []), agent="gemini")
        latency  = int((time.perf_counter() - start) * 1000)

        logger.info(
            f"Gemini done: score={data.get('risk_score', 0):.1f} "
            f"findings={len(findings)} latency={latency}ms"
        )
        return AgentResult(
            agent="gemini",
            model=model,
            risk_score=float(data.get("risk_score", 0)),
            findings=findings,
            summary=data.get("summary", ""),
            latency_ms=latency,
        )
    except Exception as e:
        latency = int((time.perf_counter() - start) * 1000)
        logger.error(f"Gemini agent error: {e}")
        return AgentResult(
            agent="gemini", model=model,
            error=str(e), latency_ms=latency,
        )
