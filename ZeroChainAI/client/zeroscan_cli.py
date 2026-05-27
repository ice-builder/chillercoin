#!/usr/bin/env python3
"""
ZeroChainAI — Mac Client CLI
Secure command-line interface for the ZeroScan orchestrator.

Setup:
  export ZEROSCAN_API_URL=http://10.99.0.1:8443
  export ZEROSCAN_API_KEY=zs-prod-your-key

Usage:
  zeroscan scan MyContract.sol
  zeroscan scan MyContract.sol --output report.json
  zeroscan scan MyContract.sol --output report.md
  zeroscan status
  zeroscan history
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    print("❌ pip install requests")
    sys.exit(1)


class ZeroScanClient:
    """Secure client for ZeroChainAI Orchestrator API."""

    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        hmac_secret: Optional[str] = None,
    ):
        self.url        = (url or os.getenv("ZEROSCAN_API_URL", "http://10.99.0.1:8443")).rstrip("/")
        self.api_key    = api_key or os.getenv("ZEROSCAN_API_KEY", "")
        self.hmac_secret = (hmac_secret or os.getenv("ZEROSCAN_HMAC_SECRET", "")).encode()

        if not self.api_key:
            raise ValueError(
                "No API key. Set ZEROSCAN_API_KEY or pass api_key=..."
            )

    def _sign_request(self, body: bytes) -> dict:
        """Generate HMAC-signed request headers."""
        timestamp = str(int(time.time()))
        headers = {
            "X-API-Key":   self.api_key,
            "X-Timestamp": timestamp,
            "Content-Type": "application/json",
        }
        if self.hmac_secret:
            message  = f"{timestamp}.".encode() + body
            sig      = hmac.new(self.hmac_secret, message, "sha256").hexdigest()
            headers["X-Signature"] = sig
        return headers

    def scan_code(
        self,
        code: str,
        language: str = "auto",
        contract_name: str = "",
        chain: str = "",
        force: bool = False,
    ) -> dict:
        """Scan smart contract code."""
        payload = {
            "code":          code,
            "language":      language,
            "contract_name": contract_name,
            "chain":         chain or None,
            "force_rescan":  force,
        }
        body    = json.dumps(payload).encode()
        headers = self._sign_request(body)

        resp = requests.post(
            f"{self.url}/scan",
            data=body,
            headers=headers,
            timeout=180,  # ensemble can take up to 2.5min with o3
        )
        resp.raise_for_status()
        return resp.json()

    def scan_file(
        self,
        path: str,
        chain: str = "",
        force: bool = False,
    ) -> dict:
        """Scan a contract file."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {path}")

        code     = p.read_text(encoding="utf-8")
        lang_map = {".sol": "solidity", ".rs": "rust", ".move": "move", ".vy": "vyper"}
        language = lang_map.get(p.suffix.lower(), "auto")

        return self.scan_code(
            code=code,
            language=language,
            contract_name=p.stem,
            chain=chain,
            force=force,
        )

    def status(self) -> dict:
        body    = b"{}"
        headers = self._sign_request(body)
        resp    = requests.get(f"{self.url}/status", headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def history(self, limit: int = 10) -> dict:
        body    = b"{}"
        headers = self._sign_request(body)
        resp    = requests.get(
            f"{self.url}/history?limit={limit}", headers=headers, timeout=10
        )
        resp.raise_for_status()
        return resp.json()


# ── CLI ──────────────────────────────────────────────────────

SEVERITY_COLORS = {
    "critical": "\033[91m",   # bright red
    "high":     "\033[31m",   # red
    "medium":   "\033[33m",   # yellow
    "low":      "\033[32m",   # green
    "info":     "\033[37m",   # grey
}
RESET = "\033[0m"
BOLD  = "\033[1m"

SEVERITY_EMOJI = {
    "critical": "🔴", "high": "🟠", "medium": "🟡",
    "low": "🟢", "info": "⚪"
}


def _color(sev: str, text: str) -> str:
    return f"{SEVERITY_COLORS.get(sev, '')}{text}{RESET}"


def print_report(report: dict, verbose: bool = False):
    """Pretty-print scan report to terminal."""
    risk  = report.get("risk_score", 0)
    sev   = report.get("severity_summary", "info")
    finds = report.get("findings", [])
    cache = report.get("from_cache", False)

    print(f"\n{'═'*60}")
    print(f"{BOLD}🛡️  ZeroChainAI Security Report{RESET}")
    print(f"{'═'*60}")
    print(f"Contract: {report.get('contract_name') or 'Unknown'}")
    print(f"Language: {report.get('language')} | Chain: {report.get('chain') or 'N/A'}")
    print(f"Scan ID:  {report.get('scan_id', '')[:16]}...")
    if cache:
        print(f"⚡ (from cache)")
    print(f"Duration: {report.get('scan_duration_ms', 0)}ms")
    print(f"{'─'*60}")

    risk_color = "\033[91m" if risk >= 7 else "\033[33m" if risk >= 4 else "\033[32m"
    print(f"Risk Score:  {risk_color}{BOLD}{risk:.1f}/10{RESET}")
    print(f"Severity:    {SEVERITY_EMOJI.get(sev, '⚪')} {_color(sev, sev.upper())}")
    print(f"Consensus:   {report.get('ensemble_consensus', 0):.0%}")
    print(f"Agents:      {', '.join(report.get('agents_used', []))}")
    print(f"{'─'*60}")

    counts = {
        "critical": report.get("critical_count", 0),
        "high":     report.get("high_count", 0),
        "medium":   report.get("medium_count", 0),
        "low":      report.get("low_count", 0),
    }
    print("Findings:")
    for sev_name, count in counts.items():
        if count > 0:
            print(f"  {SEVERITY_EMOJI.get(sev_name)} {sev_name.capitalize()}: {count}")

    print(f"\n{BOLD}Executive Summary:{RESET}")
    print(f"  {report.get('executive_summary', '')}")

    print(f"\n{BOLD}━ Findings ━{'━'*47}{RESET}")
    for i, f in enumerate(finds, 1):
        sev_f = f.get("severity", "info")
        em    = SEVERITY_EMOJI.get(sev_f, "⚪")
        print(f"\n{em} {BOLD}[{i}] [{sev_f.upper()}] {f.get('type', '?')}{RESET}")
        print(f"   Location:    {f.get('function', '?')} (L{f.get('line_start')}–{f.get('line_end')})")
        print(f"   Risk:        {f.get('risk_score', 0):.1f}/10")
        print(f"   Confidence:  {f.get('confidence', 0):.0%}")
        print(f"   Confirmed by: {', '.join(f.get('confirmed_by', []))}")
        if f.get("cwe"):
            print(f"   CWE:         {f.get('cwe')}")
        print(f"   {f.get('description', '')[:200]}")
        if verbose:
            print(f"   Fix: {f.get('recommendation', '')[:300]}")

    print(f"\n{'═'*60}\n")


def cmd_scan(args):
    client = ZeroScanClient()
    print(f"🔍 Scanning {args.file}...")
    try:
        report = client.scan_file(args.file, chain=args.chain or "", force=args.force)
    except requests.HTTPError as e:
        print(f"❌ API error: {e.response.status_code} — {e.response.text}")
        sys.exit(1)

    print_report(report, verbose=args.verbose)

    if args.output:
        out = Path(args.output)
        if out.suffix == ".md":
            # Re-request as markdown — build from report dict
            md_lines = [
                f"# ZeroChainAI Report: {report.get('contract_name', 'Unknown')}",
                f"**Risk:** {report.get('risk_score'):.1f}/10 | **Severity:** {report.get('severity_summary', '').upper()}",
                "",
            ]
            for i, f in enumerate(report.get("findings", []), 1):
                md_lines += [
                    f"## [{i}] {SEVERITY_EMOJI.get(f.get('severity'), '⚪')} {f.get('type')}",
                    f"**Lines:** {f.get('line_start')}–{f.get('line_end')} | **Confidence:** {f.get('confidence', 0):.0%}",
                    f"\n{f.get('description', '')}",
                    f"\n**Fix:** {f.get('recommendation', '')}",
                    "",
                ]
            out.write_text("\n".join(md_lines), encoding="utf-8")
        else:
            out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"✅ Report saved: {args.output}")


def cmd_status(args):
    client = ZeroScanClient()
    try:
        st = client.status()
        print(f"✅ Service: {st.get('status')}")
        print(f"   Version: {st.get('version')}")
        print(f"   Agents:  {', '.join(st.get('agents', []))}")
        cache = st.get("cache", {})
        print(f"   Cache:   {cache.get('total_scans', 0)} scans cached")
    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)


def cmd_history(args):
    client = ZeroScanClient()
    try:
        h = client.history(limit=args.limit)
        scans = h.get("scans", [])
        if not scans:
            print("No scan history yet.")
            return
        print(f"\n{'─'*60}")
        print(f"{'Scan ID':<18} {'Contract':<20} {'Risk':>5} {'Findings':>8} {'Date'}")
        print(f"{'─'*60}")
        for s in scans:
            print(
                f"{str(s.get('scan_id',''))[:16]:<18} "
                f"{str(s.get('contract_name','Unknown'))[:18]:<20} "
                f"{s.get('risk_score', 0):>5.1f} "
                f"{s.get('total_findings', 0):>8} "
                f"{str(s.get('timestamp',''))[:16]}"
            )
    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="zeroscan",
        description="🛡️ ZeroChainAI — AI-powered smart contract security scanner",
    )
    sub = parser.add_subparsers(dest="command")

    # scan
    p_scan = sub.add_parser("scan", help="Scan a smart contract file")
    p_scan.add_argument("file", help="Path to contract file (.sol, .rs, .move)")
    p_scan.add_argument("--chain", default="", help="Blockchain (ethereum, bsc, ...)")
    p_scan.add_argument("--output", "-o", help="Save report (.json or .md)")
    p_scan.add_argument("--verbose", "-v", action="store_true", help="Show recommendations")
    p_scan.add_argument("--force", "-f", action="store_true", help="Skip cache")

    # status
    p_status = sub.add_parser("status", help="Check orchestrator status")

    # history
    p_hist = sub.add_parser("history", help="View recent scans")
    p_hist.add_argument("--limit", type=int, default=10)

    args = parser.parse_args()

    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "history":
        cmd_history(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
