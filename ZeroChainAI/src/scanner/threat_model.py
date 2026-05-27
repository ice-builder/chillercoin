"""
ZeroChainAI — Threat Model Engine
─────────────────────────────────
Methodology: "Needle in a Haystack" (Habr / devansh)

Generates blockchain-specific threat models from contract code,
using CVE history learning loop:
    1. Check local CVE store for known patterns FIRST
    2. Generate focused threat model from code characteristics
    3. Create audit slices for the ensemble agents
    4. After scan, store new findings back into local CVE DB

User feedback: "Найденные дырки сразу проверять,
а потом уже искать новое."
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("zerochainai.threat_model")

# Local CVE learning store
CVE_DB_PATH = os.getenv("CVE_DB", "/data/zerochainai_cve.db")


# ─── Data Classes ──────────────────────────────────────────────

@dataclass
class ContractSlice:
    """A thin audit slice for focused analysis."""
    name: str
    priority: int
    category: str               # reentrancy, access_control, etc.
    functions: List[str]        # target function names
    trust_boundary: str
    assumptions: List[str]      # to decompose and break
    known_cve_patterns: List[str]


@dataclass
class BlockchainThreatModel:
    """Threat model for a smart contract."""
    language: str
    contract_name: str
    code_hash: str
    risk_tier: str              # critical / high / medium / low
    # From CVE learning store
    known_patterns_matched: int
    known_pattern_details: List[Dict[str, str]]
    # Generated
    dominant_categories: List[str]
    trust_boundaries: List[str]
    high_risk_functions: List[str]
    slices: List[ContractSlice]
    attacker_model: str
    recommendations: List[str]

    def to_dict(self) -> dict:
        return {
            "language": self.language,
            "contract_name": self.contract_name,
            "risk_tier": self.risk_tier,
            "known_patterns_matched": self.known_patterns_matched,
            "known_pattern_details": self.known_pattern_details,
            "dominant_categories": self.dominant_categories,
            "slices": [
                {"name": s.name, "priority": s.priority,
                 "category": s.category, "functions": s.functions,
                 "assumptions": s.assumptions}
                for s in self.slices
            ],
        }


# ─── Blockchain CVE Knowledge Base ────────────────────────────

BLOCKCHAIN_CVE_PATTERNS: Dict[str, Dict[str, Any]] = {
    "reentrancy": {
        "severity": "critical",
        "keywords": [".call{", ".call(", "transfer(", "send("],
        "anti_patterns": ["nonReentrant", "ReentrancyGuard", "_status"],
        "description": "External call before state update (CEI violation)",
        "cwe": "CWE-841",
        "examples": ["TheDAO hack", "Curve pool exploit 2023"],
    },
    "access_control": {
        "severity": "critical",
        "keywords": ["onlyOwner", "msg.sender", "tx.origin", "require("],
        "anti_patterns": ["AccessControl", "Ownable", "hasRole"],
        "description": "Missing or bypassed authorization checks",
        "cwe": "CWE-285",
        "examples": ["Ronin Bridge ($625M)", "Wormhole ($326M)"],
    },
    "integer_overflow": {
        "severity": "high",
        "keywords": ["unchecked", "uint256", "uint128", "+", "*", "-"],
        "anti_patterns": ["SafeMath", "pragma solidity ^0.8"],
        "description": "Arithmetic overflow/underflow",
        "cwe": "CWE-190",
        "examples": ["BEC token overflow"],
    },
    "flash_loan": {
        "severity": "critical",
        "keywords": ["getReserves", "balanceOf", "totalSupply", "price"],
        "anti_patterns": ["TWAP", "chainlink", "timeWeightedAverage"],
        "description": "Price oracle manipulation via flash loan",
        "cwe": "CWE-682",
        "examples": ["Euler Finance ($197M)", "Mango Markets ($114M)"],
    },
    "delegatecall": {
        "severity": "critical",
        "keywords": ["delegatecall", "proxy", "implementation", "upgrade"],
        "anti_patterns": ["_authorizeUpgrade", "onlyProxy", "initializer"],
        "description": "Storage collision or unauthorized proxy upgrade",
        "cwe": "CWE-345",
        "examples": ["Parity Wallet freeze", "Nomad Bridge"],
    },
    "front_running": {
        "severity": "high",
        "keywords": ["swap", "addLiquidity", "approve", "transferFrom"],
        "anti_patterns": ["commit-reveal", "deadline", "minAmount"],
        "description": "Transaction ordering exploitation (MEV/sandwich)",
        "cwe": "CWE-362",
        "examples": ["Countless DEX sandwich attacks"],
    },
    "selfdestruct": {
        "severity": "high",
        "keywords": ["selfdestruct", "suicide"],
        "anti_patterns": ["onlyOwner"],
        "description": "Unauthorized contract destruction or forced ETH send",
        "cwe": "CWE-284",
    },
    "timestamp_dependence": {
        "severity": "medium",
        "keywords": ["block.timestamp", "block.number", "now"],
        "anti_patterns": [],
        "description": "Miner-manipulable timestamp for critical logic",
        "cwe": "CWE-330",
    },
}


# ─── Local CVE Learning Store ──────────────────────────────────

class CVELearningStore:
    """
    Local store for discovered vulnerabilities.
    Learning loop: found vulns → store → check first next time.

    User requirement: "найденные дырки сразу проверять,
    а потом уже искать новое"
    """

    def __init__(self, db_path: str = CVE_DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        try:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS learned_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern_hash TEXT UNIQUE,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    code_signature TEXT,
                    description TEXT,
                    line_pattern TEXT,
                    function_name TEXT,
                    contract_language TEXT,
                    times_found INTEGER DEFAULT 1,
                    first_found DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_found DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scan_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code_hash TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    total_findings INTEGER,
                    critical_count INTEGER,
                    high_count INTEGER,
                    patterns_reused INTEGER DEFAULT 0
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"CVE store init failed (using in-memory): {e}")
            self.db_path = ":memory:"

    def learn_from_findings(self, findings: List[Dict[str, Any]], language: str):
        """Store new findings for future pattern matching."""
        try:
            conn = sqlite3.connect(self.db_path)
            for f in findings:
                sig = f"{f.get('type','')}|{f.get('function','')}|{f.get('description','')[:100]}"
                pattern_hash = hashlib.sha256(sig.encode()).hexdigest()[:16]

                conn.execute("""
                    INSERT INTO learned_patterns
                        (pattern_hash, category, severity, code_signature,
                         description, function_name, contract_language)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(pattern_hash) DO UPDATE SET
                        times_found = times_found + 1,
                        last_found = CURRENT_TIMESTAMP
                """, (
                    pattern_hash,
                    f.get("type", "unknown"),
                    f.get("severity", "medium"),
                    sig,
                    f.get("description", ""),
                    f.get("function", ""),
                    language,
                ))
            conn.commit()
            conn.close()
            logger.info(f"Learned {len(findings)} patterns for {language}")
        except Exception as e:
            logger.warning(f"Learning failed: {e}")

    def get_known_patterns(self, language: str) -> List[Dict[str, str]]:
        """Retrieve previously discovered patterns to check first."""
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute("""
                SELECT category, severity, description, function_name, times_found
                FROM learned_patterns
                WHERE contract_language = ?
                ORDER BY times_found DESC, last_found DESC
                LIMIT 20
            """, (language,)).fetchall()
            conn.close()
            return [
                {
                    "category": r[0], "severity": r[1],
                    "description": r[2], "function": r[3],
                    "times_found": r[4],
                }
                for r in rows
            ]
        except Exception:
            return []

    def record_scan(self, code_hash: str, total: int, critical: int,
                    high: int, patterns_reused: int):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO scan_history
                    (code_hash, total_findings, critical_count,
                     high_count, patterns_reused)
                VALUES (?, ?, ?, ?, ?)
            """, (code_hash, total, critical, high, patterns_reused))
            conn.commit()
            conn.close()
        except Exception:
            pass


# ─── Threat Model Generator ───────────────────────────────────

# Global singleton
_cve_store: Optional[CVELearningStore] = None


def get_cve_store() -> CVELearningStore:
    global _cve_store
    if _cve_store is None:
        _cve_store = CVELearningStore()
    return _cve_store


class BlockchainThreatModelGenerator:
    """
    Generate threat model for smart contract code.

    Algorithm (from article):
    1. Check local CVE store for known patterns FIRST
    2. Analyze code structure (functions, modifiers, calls)
    3. Match against BLOCKCHAIN_CVE_PATTERNS
    4. Generate prioritized audit slices
    5. Create focused attacker model
    """

    def __init__(self):
        self.cve_store = get_cve_store()

    def generate(
        self, code: str, language: str, contract_name: str = ""
    ) -> BlockchainThreatModel:
        """Generate threat model from smart contract source code."""
        code_hash = hashlib.sha256(code.encode()).hexdigest()

        # Step 1: Check known patterns FIRST (learning loop)
        known = self.cve_store.get_known_patterns(language)
        known_matched = self._match_known_patterns(code, known)

        # Step 2: Analyze code structure
        functions = self._extract_functions(code, language)
        high_risk = self._identify_high_risk_functions(code, functions)

        # Step 3: Match CVE patterns
        matched_categories = self._match_cve_patterns(code, language)

        # Step 4: Determine risk tier
        risk_tier = self._compute_risk(matched_categories, known_matched)

        # Step 5: Generate slices
        slices = self._generate_slices(
            matched_categories, functions, known_matched, language
        )

        # Step 6: Build attacker model
        attacker = self._build_attacker_model(language, matched_categories)

        # Step 7: Recommendations
        recs = self._generate_recommendations(matched_categories, risk_tier)

        # Trust boundaries
        boundaries = self._get_trust_boundaries(language)

        return BlockchainThreatModel(
            language=language,
            contract_name=contract_name,
            code_hash=code_hash,
            risk_tier=risk_tier,
            known_patterns_matched=len(known_matched),
            known_pattern_details=known_matched,
            dominant_categories=[c["category"] for c in matched_categories[:6]],
            trust_boundaries=boundaries,
            high_risk_functions=high_risk[:10],
            slices=slices,
            attacker_model=attacker,
            recommendations=recs,
        )

    def _match_known_patterns(
        self, code: str, known: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        """Check code against previously discovered patterns."""
        matched = []
        code_lower = code.lower()
        for p in known:
            func = p.get("function", "")
            if func and func.lower() in code_lower:
                matched.append({
                    "category": p["category"],
                    "severity": p["severity"],
                    "source": "learned_pattern",
                    "description": f"Previously found: {p['description'][:80]}",
                    "times_found": str(p.get("times_found", 1)),
                })
        return matched

    def _extract_functions(self, code: str, language: str) -> List[str]:
        """Extract function names from source code."""
        if language in ("solidity", "vyper"):
            return re.findall(r'function\s+(\w+)', code)
        elif language == "rust":
            return re.findall(r'(?:pub\s+)?fn\s+(\w+)', code)
        elif language == "move":
            return re.findall(r'(?:public\s+)?fun\s+(\w+)', code)
        return []

    def _identify_high_risk_functions(
        self, code: str, functions: List[str]
    ) -> List[str]:
        """Identify functions with high-risk patterns."""
        high_risk = []
        lines = code.split("\n")
        for func in functions:
            # Find function body region
            for i, line in enumerate(lines):
                if func in line and ("function" in line or "fn " in line):
                    # Scan next 30 lines for dangerous patterns
                    region = "\n".join(lines[i:i+30])
                    danger_score = 0
                    if ".call{" in region or ".call(" in region:
                        danger_score += 3
                    if "delegatecall" in region:
                        danger_score += 3
                    if "selfdestruct" in region:
                        danger_score += 3
                    if "msg.sender" in region and "require" not in region:
                        danger_score += 2
                    if "transfer(" in region or "send(" in region:
                        danger_score += 1
                    if danger_score >= 2:
                        high_risk.append(func)
                    break
        return high_risk

    def _match_cve_patterns(
        self, code: str, language: str
    ) -> List[Dict[str, Any]]:
        """Match code against known CVE patterns."""
        matched = []
        code_lower = code.lower()

        for category, pattern in BLOCKCHAIN_CVE_PATTERNS.items():
            # Check for vulnerable keywords
            keyword_hits = sum(
                1 for kw in pattern["keywords"]
                if kw.lower() in code_lower
            )
            if keyword_hits == 0:
                continue

            # Check for protective anti-patterns
            protection = sum(
                1 for ap in pattern["anti_patterns"]
                if ap.lower() in code_lower
            )

            # If keywords present but no protection → likely vulnerable
            if keyword_hits > 0 and protection == 0:
                matched.append({
                    "category": category,
                    "severity": pattern["severity"],
                    "keyword_hits": keyword_hits,
                    "description": pattern["description"],
                    "cwe": pattern.get("cwe", ""),
                })
            elif keyword_hits >= 2 and protection < keyword_hits:
                # Partial protection
                matched.append({
                    "category": category,
                    "severity": "medium",
                    "keyword_hits": keyword_hits,
                    "description": f"Partial: {pattern['description']}",
                    "cwe": pattern.get("cwe", ""),
                })

        # Sort by severity
        severity_order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        matched.sort(
            key=lambda x: severity_order.get(x["severity"], 0),
            reverse=True,
        )
        return matched

    def _compute_risk(
        self,
        matched: List[Dict[str, Any]],
        known_matched: List[Dict[str, str]],
    ) -> str:
        """Compute overall risk tier."""
        if any(m["severity"] == "critical" for m in matched):
            return "critical"
        if any(m["severity"] == "critical" for m in known_matched):
            return "critical"
        if any(m["severity"] == "high" for m in matched) or len(matched) >= 3:
            return "high"
        if matched:
            return "medium"
        return "low"

    def _generate_slices(
        self,
        matched: List[Dict[str, Any]],
        functions: List[str],
        known: List[Dict[str, str]],
        language: str,
    ) -> List[ContractSlice]:
        """Generate audit slices from matched patterns."""
        slices = []
        seen_cats = set()

        # Known patterns first (learning loop priority)
        for p in known:
            cat = p["category"]
            if cat in seen_cats:
                continue
            seen_cats.add(cat)
            pattern_info = BLOCKCHAIN_CVE_PATTERNS.get(cat, {})
            slices.append(ContractSlice(
                name=f"[KNOWN] {cat}",
                priority=0,  # highest
                category=cat,
                functions=[p.get("function", "")],
                trust_boundary="previously_exploited",
                assumptions=[
                    f"Pattern from {p.get('times_found', 1)}x previous scans",
                    pattern_info.get("description", ""),
                ],
                known_cve_patterns=[p.get("description", "")],
            ))

        # New pattern slices
        for i, m in enumerate(matched):
            cat = m["category"]
            if cat in seen_cats:
                continue
            seen_cats.add(cat)
            pattern = BLOCKCHAIN_CVE_PATTERNS.get(cat, {})
            slices.append(ContractSlice(
                name=cat,
                priority=i + 1,
                category=cat,
                functions=functions[:5],
                trust_boundary=_TRUST_BOUNDARIES.get(cat, "external ↔ contract"),
                assumptions=_ASSUMPTIONS.get(cat, []),
                known_cve_patterns=pattern.get("examples", []),
            ))

        return slices[:8]

    def _build_attacker_model(
        self, language: str, matched: List[Dict[str, Any]]
    ) -> str:
        if any(m["category"] == "flash_loan" for m in matched):
            return ("Flash loan attacker: unlimited capital for single-block "
                    "price manipulation via DEX/lending protocol interaction")
        if any(m["category"] == "access_control" for m in matched):
            return ("Unauthorized external caller attempting to invoke "
                    "privileged functions without proper role/ownership")
        if any(m["category"] == "front_running" for m in matched):
            return ("MEV searcher monitoring mempool for profitable "
                    "transaction ordering/sandwich attacks")
        return "External EOA interacting with public contract functions"

    def _get_trust_boundaries(self, language: str) -> List[str]:
        if language == "solidity":
            return [
                "EOA ↔ contract", "contract ↔ external_contract",
                "owner ↔ public", "proxy ↔ implementation",
                "oracle ↔ price_consumer",
            ]
        elif language == "rust":
            return [
                "user ↔ program", "CPI caller ↔ callee",
                "PDA authority ↔ signer", "validator ↔ runtime",
            ]
        return ["external ↔ contract", "admin ↔ user"]

    def _generate_recommendations(
        self, matched: List[Dict[str, Any]], risk_tier: str
    ) -> List[str]:
        recs = []
        cats = {m["category"] for m in matched}

        if risk_tier == "critical":
            recs.append("⚠️ DO NOT DEPLOY: Critical vulnerabilities detected")

        if "reentrancy" in cats:
            recs.append("Apply CEI pattern + ReentrancyGuard on all external calls")
        if "access_control" in cats:
            recs.append("Use OpenZeppelin AccessControl with role-based modifiers")
        if "flash_loan" in cats:
            recs.append("Use TWAP oracle instead of spot price for all calculations")
        if "integer_overflow" in cats:
            recs.append("Use Solidity 0.8+ or SafeMath for all arithmetic")
        if "delegatecall" in cats:
            recs.append("Add storage gaps, use initializer modifier, validate impl")
        if "front_running" in cats:
            recs.append("Implement commit-reveal or use deadline + minAmount params")

        return recs[:6]


# ─── Constants ─────────────────────────────────────────────────

_TRUST_BOUNDARIES = {
    "reentrancy": "contract ↔ external_contract",
    "access_control": "owner ↔ public_caller",
    "flash_loan": "oracle ↔ price_consumer",
    "delegatecall": "proxy ↔ implementation",
    "front_running": "mempool ↔ block_inclusion",
    "selfdestruct": "owner ↔ anyone",
    "integer_overflow": "input ↔ arithmetic",
    "timestamp_dependence": "miner ↔ contract_logic",
}

_ASSUMPTIONS = {
    "reentrancy": [
        "State is updated before external calls",
        "Reentrancy guard protects all state-changing functions",
        "No callback to untrusted contracts mid-execution",
    ],
    "access_control": [
        "All admin functions have onlyOwner/role modifier",
        "tx.origin is never used for authentication",
        "Role assignment requires multi-sig or timelock",
    ],
    "flash_loan": [
        "Price oracle uses time-weighted average (TWAP)",
        "No single-block price manipulation is possible",
        "Reserve calculations are resistant to donation attacks",
    ],
    "delegatecall": [
        "Storage layout matches between proxy and implementation",
        "Implementation cannot be self-destructed",
        "Upgrade function requires owner + timelock",
    ],
    "front_running": [
        "Slippage protection via deadline + minAmountOut",
        "No commit-reveal needed for sensitive operations",
        "Batch auction prevents sandwich attacks",
    ],
    "integer_overflow": [
        "All arithmetic uses checked operations (0.8+)",
        "unchecked blocks only contain provably safe math",
        "External input is validated before arithmetic",
    ],
}
