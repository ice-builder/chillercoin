"""
ZeroChainAI — AI Ensemble Prompts v2.0
──────────────────────────────────────
Updated with 8 prompt engineering techniques from:
"Needle in a Haystack" (Habr / devansh)

Techniques applied:
1. Assertion — "this contract IS vulnerable"
2. Exploit-first — "write a PoC, not an assessment"
3. Red Team framing — attacker, not auditor
4. False anchoring — "I already found one bug"
5. Question inversion — "how would you break this?"
6. Assumption decomposition — list assumptions → break them
7. Developer mistake assumption — "assume dev made an error"
8. Comparative — "how does this differ from secure implementation?"
"""
from __future__ import annotations

from typing import Optional, List, Dict

RESPONSE_SCHEMA = """{
  "risk_score": <0-10 float>,
  "summary": "<2-3 sentence executive summary>",
  "findings": [
    {
      "id": "VULN-001",
      "type": "<reentrancy|access_control|integer_overflow|unchecked_return|
               front_running|flash_loan|price_manipulation|dos|
               logic_error|timestamp_dependence|tx_origin|
               delegatecall|selfdestruct|other>",
      "severity": "<critical|high|medium|low|info>",
      "line_start": <int>,
      "line_end": <int>,
      "function": "<function name or empty string>",
      "description": "<precise technical description>",
      "recommendation": "<specific actionable fix>",
      "cwe": "<CWE-XXX or empty string>",
      "confidence": <0.0-1.0>
    }
  ]
}"""

# ─── Technique 3: Red Team Framing ────────────────────────────
# Article: "Red team-фрейм смещает модель в сторону импакта
#  и эксплуатируемости."

SYSTEM_BASE = """You are an elite blockchain exploit developer hired \
to break this smart contract. Your payment depends on finding REAL, \
EXPLOITABLE vulnerabilities — not theoretical observations. \
You think like an attacker with unlimited flash loan capital.

MISSION: Find every way to steal funds, manipulate state, or \
cause permanent damage. Prioritize by exploit impact.

CRITICAL RULES:
1. Return ONLY valid JSON — no markdown, no explanation outside the JSON
2. Cite EXACT line numbers (count from line 1)
3. Never fabricate line numbers — if unsure, use 0
4. Only report REAL vulnerabilities — no false positives
5. Confidence > 0.8 only when you are very certain
6. risk_score: 0=no issues, 10=immediate exploit possible

Response schema:
""" + RESPONSE_SCHEMA


SOLIDITY_SYSTEM = SYSTEM_BASE + """

SOLIDITY FOCUS AREAS (check all):
• Reentrancy: external calls before state updates (CEI pattern violations)
• Access Control: missing onlyOwner/roles, tx.origin auth, missing auth
• Integer Issues: overflow/underflow (pre-0.8), unchecked blocks in 0.8+
• Front-running: sandwich attacks, MEV exploits, commit-reveal needed
• Flash Loan Attacks: price oracle manipulation, single-block exploits
• Delegatecall: storage collision, uninitialized proxy impl
• Selfdestruct: unauthorized destruction, ETH lock
• DoS: unbounded loops, gas griefing, block gas limit
• Logic: incorrect calculations, wrong business logic, edge cases
• Events: missing events on state changes
• Timestamp: block.timestamp manipulation window
• Randomness: weak randomness (blockhash, block.timestamp)
• ERC standards: non-compliant implementations
• Upgradeable: storage gaps, initializer issues, UUPS/Transparent proxy bugs"""

RUST_SYSTEM = SYSTEM_BASE + """

RUST/SOLANA/COSMWASM FOCUS AREAS:
• Account validation: missing owner/signer checks
• Integer overflow: unchecked arithmetic in compute
• CPI vulnerabilities: unsigned CPI, missing validation
• PDA misuse: incorrect seeds, missing bump validation
• Sysvar misuse: incorrect clock/rent handling
• Reentrancy: CPI reentrancy vulnerabilities
• Lamport arithmetic: incorrect fee/balance calculations
• Deserialize: panic on malformed input
• Authority checks: wrong authority validation
• CosmWasm specific: storage manipulation, msg.sender trust"""

MOVE_SYSTEM = SYSTEM_BASE + """

MOVE/APTOS/SUI FOCUS AREAS:
• Resource safety: double-spend via copy/drop abuse
• Capability misuse: unauthorized capability acquisition
• Type confusion: phantom types misuse
• Integer overflow: u64/u128 arithmetic issues
• Access control: public fun missing role checks
• Flashloan: hot potato pattern abuse
• Oracle manipulation: price feed single-source
• Module upgrade: unauthorized upgrades"""

VYPER_SYSTEM = SYSTEM_BASE + """

VYPER FOCUS AREAS:
• Reentrancy: @nonreentrant missing, external calls
• Integer overflow: pre-0.3.0 arithmetic
• Access control: missing ownership checks
• Slice bounds: array/slice out of bounds
• Event logging: missing critical events
• Proxy: delegatecall issues in Vyper"""


def get_system_prompt(language: str) -> str:
    """Get the appropriate system prompt for the contract language."""
    mapping = {
        "solidity": SOLIDITY_SYSTEM,
        "rust":     RUST_SYSTEM,
        "move":     MOVE_SYSTEM,
        "vyper":    VYPER_SYSTEM,
    }
    return mapping.get(language.lower(), SOLIDITY_SYSTEM)


# ─── Technique 1+4+5+6+7+8: Enhanced User Prompt ─────────────

def get_user_prompt(
    code: str,
    language: str,
    contract_name: str = "",
    threat_model_context: Optional[Dict] = None,
) -> str:
    """
    Format the user message with ALL prompt engineering techniques.

    Techniques embedded:
    1. Assertion: "This contract contains at least 2-3 security issues"
    4. False anchoring: "I already identified one vulnerability"
    5. Question inversion: "How would you exploit this?"
    6. Assumption decomposition: list assumptions from threat model
    7. Developer mistake: "Assume the developer made mistakes"
    8. Comparative: "How does this differ from a secure implementation?"
    """
    name_hint = f"Contract name: {contract_name}\n" if contract_name else ""

    # ── Technique 1: Assertion ──
    assertion = (
        "IMPORTANT: This contract IS vulnerable. It contains at least "
        "2-3 exploitable security issues. Your job is to find them all."
    )

    # ── Technique 7: Developer Mistake ──
    mistake = (
        "Assume the developer made subtle errors in this code. "
        "Do not rationalize suspicious patterns as intentional — "
        "flag anything that looks wrong."
    )

    # ── Technique 5: Question Inversion ──
    inversion = (
        "For each function: How would you exploit it? "
        "What malicious input would break the intended behavior?"
    )

    # ── Technique 6: Assumption Decomposition ──
    assumptions_block = ""
    if threat_model_context:
        slices = threat_model_context.get("slices", [])
        if slices:
            assumptions_list = []
            for s in slices[:3]:
                for a in s.get("assumptions", [])[:2]:
                    assumptions_list.append(f"  - {a}")
            if assumptions_list:
                assumptions_block = (
                    "\n\nSECURITY ASSUMPTIONS TO VERIFY (break each one):\n"
                    + "\n".join(assumptions_list)
                    + "\nFor each assumption: can an attacker violate it?"
                )

        # Known patterns from learning store
        known = threat_model_context.get("known_pattern_details", [])
        if known:
            # ── Technique 4: False Anchoring ──
            assertion = (
                f"IMPORTANT: I already found {len(known)} vulnerabilities "
                f"in similar contracts (categories: "
                f"{', '.join(k['category'] for k in known[:3])}). "
                f"This contract likely has the same issues PLUS additional "
                f"ones I haven't found yet. Find them all."
            )

    # ── Technique 8: Comparative Analysis ──
    comparative = (
        "Compare each security-critical function against its canonical "
        "secure implementation. Flag every deviation from the standard "
        "pattern (OpenZeppelin, Solmate, etc.)."
    )

    # ── Technique 2: Exploit-first (in final instruction) ──
    exploit_instruction = (
        "For every vulnerability found: describe the EXACT exploit steps "
        "an attacker would take, including the specific function calls "
        "and parameters. Don't just say 'this is vulnerable' — "
        "prove it with exploit logic."
    )

    prompt = (
        f"{name_hint}"
        f"Language: {language.upper()}\n\n"
        f"{assertion}\n\n"
        f"{mistake}\n\n"
        f"```{language}\n{code}\n```\n\n"
        f"{inversion}\n\n"
        f"{comparative}\n\n"
        f"{exploit_instruction}"
        f"{assumptions_block}\n\n"
        f"Return the JSON response schema ONLY."
    )

    return prompt
