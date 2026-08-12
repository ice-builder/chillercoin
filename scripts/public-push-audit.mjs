#!/usr/bin/env node
/**
 * ChillerCoin public push gate
 *
 * Scans a tree (default: repo root) against public/private boundary policy.
 * Writes a report; exit 1 if any BLOCK findings (so you can decide before push).
 *
 * Usage:
 *   node scripts/public-push-audit.mjs
 *   node scripts/public-push-audit.mjs --root /path/to/staging
 *   node scripts/public-push-audit.mjs --json reports/last.json
 *   node scripts/public-push-audit.mjs --allow-warn   # exit 0 even with warnings
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = path.resolve(__dirname, "..");
const POLICY_PATH = path.join(__dirname, "public-push-policy.json");

function parseArgs(argv) {
  const out = {
    root: DEFAULT_ROOT,
    jsonOut: null,
    mdOut: null,
    allowWarn: false,
    help: false,
  };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--help" || a === "-h") out.help = true;
    else if (a === "--allow-warn") out.allowWarn = true;
    else if (a === "--root") out.root = path.resolve(argv[++i]);
    else if (a === "--json") out.jsonOut = path.resolve(argv[++i]);
    else if (a === "--md") out.mdOut = path.resolve(argv[++i]);
    else throw new Error(`Unknown arg: ${a}`);
  }
  return out;
}

function loadPolicy() {
  return JSON.parse(fs.readFileSync(POLICY_PATH, "utf8"));
}

function globToRegExp(glob) {
  // Minimal ** / * support for path matching (POSIX-ish, case-sensitive)
  let s = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*" && glob[i + 1] === "*") {
      s += ".*";
      i++;
      if (glob[i + 1] === "/") i++;
    } else if (c === "*") s += "[^/]*";
    else if ("+?.^${}()|[]\\".includes(c)) s += "\\" + c;
    else s += c;
  }
  return new RegExp("^" + s + "$");
}

function walk(root, skipDirs) {
  const files = [];
  function rec(dir) {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const ent of entries) {
      if (skipDirs.includes(ent.name)) continue;
      const full = path.join(dir, ent.name);
      if (ent.isDirectory()) rec(full);
      else if (ent.isFile() || ent.isSymbolicLink()) files.push(full);
    }
  }
  rec(root);
  return files;
}

function relPosix(root, full) {
  return path.relative(root, full).split(path.sep).join("/");
}

function matchAny(rel, regexes) {
  return regexes.find((re) => re.test(rel) || re.test("./" + rel));
}

function isProbablyBinary(buf) {
  const n = Math.min(buf.length, 8000);
  let weird = 0;
  for (let i = 0; i < n; i++) {
    const b = buf[i];
    if (b === 0) return true;
    if (b < 7 || (b > 13 && b < 32)) weird++;
  }
  return weird / n > 0.3;
}

function scan() {
  const args = parseArgs(process.argv);
  if (args.help) {
    console.log(`Usage: node scripts/public-push-audit.mjs [--root DIR] [--md FILE] [--json FILE] [--allow-warn]`);
    process.exit(0);
  }

  const policy = loadPolicy();
  const root = args.root;
  if (!fs.existsSync(root)) {
    console.error(`Root not found: ${root}`);
    process.exit(2);
  }

  const blockPathRes = policy.path_block_globs.map(globToRegExp);
  const warnPathRes = policy.path_warn_globs.map(globToRegExp);
  const skipContentRes = (policy.skip_content_globs || []).map(globToRegExp);
  const blockContent = policy.content_block_patterns.map((p) => ({
    ...p,
    re: new RegExp(p.regex, "i"),
  }));
  const warnContent = policy.content_warn_patterns.map((p) => ({
    ...p,
    re: new RegExp(p.regex, "i"),
  }));

  const findings = [];
  const files = walk(root, policy.skip_dirs);
  const top = new Set(
    fs.readdirSync(root).filter((n) => n !== ".git")
  );

  for (const name of top) {
    if (!policy.allowed_top_level.includes(name)) {
      findings.push({
        severity: "BLOCK",
        rule: "allowed_top_level",
        path: name,
        message: `Top-level path not in public allowlist: ${name}`,
      });
    }
  }

  // Remotes with embedded credentials
  const gitConfig = path.join(root, ".git", "config");
  if (fs.existsSync(gitConfig)) {
    const cfg = fs.readFileSync(gitConfig, "utf8");
    if (/https?:\/\/[^\/\s:]+:[^\/\s@]+@/i.test(cfg) || /github_pat_/i.test(cfg)) {
      findings.push({
        severity: "BLOCK",
        rule: "git_remote_credentials",
        path: ".git/config",
        message: "Git remote URL appears to embed credentials/PAT — use clean URL + credential helper",
      });
    }
  }

  for (const full of files) {
    const rel = relPosix(root, full);
    if (rel.startsWith(".git/")) continue;

    const blockPath = matchAny(rel, blockPathRes);
    if (blockPath) {
      findings.push({
        severity: "BLOCK",
        rule: "path_block",
        path: rel,
        message: `Path matches private/trading denylist`,
      });
      continue;
    }
    const warnPath = matchAny(rel, warnPathRes);
    if (warnPath) {
      findings.push({
        severity: "WARN",
        rule: "path_warn",
        path: rel,
        message: `Path is gray-zone — confirm it belongs in public repo`,
      });
    }

    if (matchAny(rel, skipContentRes)) continue;

    let st;
    try {
      st = fs.statSync(full);
    } catch {
      continue;
    }
    if (st.size > policy.max_file_bytes_for_content_scan) {
      findings.push({
        severity: "WARN",
        rule: "file_too_large",
        path: rel,
        message: `Skipped content scan (>${policy.max_file_bytes_for_content_scan} bytes)`,
      });
      continue;
    }

    let buf;
    try {
      buf = fs.readFileSync(full);
    } catch {
      continue;
    }
    if (isProbablyBinary(buf)) continue;
    const text = buf.toString("utf8");

    for (const p of blockContent) {
      if (p.re.test(text)) {
        findings.push({
          severity: "BLOCK",
          rule: p.id,
          path: rel,
          message: p.message,
        });
      }
    }
    for (const p of warnContent) {
      if (p.id === "trading_architecture" && (
        rel.startsWith("docs/") ||
        rel === "README.md" ||
        rel === ".gitignore"
      )) {
        // Boundary docs / denylist intentionally name private components.
        continue;
      }
      if (p.re.test(text)) {
        findings.push({
          severity: "WARN",
          rule: p.id,
          path: rel,
          message: p.message,
        });
      }
    }
  }

  const blocks = findings.filter((f) => f.severity === "BLOCK");
  const warns = findings.filter((f) => f.severity === "WARN");
  const generatedAt = new Date().toISOString();

  const report = {
    ok: blocks.length === 0,
    decision: blocks.length === 0 ? "READY_FOR_YOUR_PUSH_DECISION" : "DO_NOT_PUSH",
    generatedAt,
    root,
    policy: policy.name,
    policyVersion: policy.version,
    summary: {
      filesScanned: files.length,
      blocks: blocks.length,
      warns: warns.length,
    },
    findings,
  };

  const md = renderMd(report);
  const defaultMd = path.join(root, "scripts", "reports", `public-push-audit-${stamp(generatedAt)}.md`);
  const defaultJson = path.join(root, "scripts", "reports", `public-push-audit-${stamp(generatedAt)}.json`);
  const mdOut = args.mdOut || defaultMd;
  const jsonOut = args.jsonOut || defaultJson;
  fs.mkdirSync(path.dirname(mdOut), { recursive: true });
  fs.writeFileSync(mdOut, md);
  fs.writeFileSync(jsonOut, JSON.stringify(report, null, 2));

  // Also write latest pointers
  fs.writeFileSync(path.join(path.dirname(mdOut), "LATEST.md"), md);
  fs.writeFileSync(path.join(path.dirname(jsonOut), "LATEST.json"), JSON.stringify(report, null, 2));

  console.log(md);
  console.log(`\nReport written:\n  ${mdOut}\n  ${jsonOut}`);

  if (blocks.length > 0) process.exit(1);
  if (warns.length > 0 && !args.allowWarn) {
    console.log("\nWARNINGS present — review LATEST.md before you decide to push.");
    process.exit(2);
  }
  process.exit(0);
}

function stamp(iso) {
  return iso.replace(/[:.]/g, "-");
}

function renderMd(report) {
  const lines = [];
  lines.push(`# ChillerCoin public push audit`);
  lines.push("");
  lines.push(`- Generated: ${report.generatedAt}`);
  lines.push(`- Root: \`${report.root}\``);
  lines.push(`- Policy: ${report.policy} v${report.policyVersion}`);
  lines.push(`- Files scanned: ${report.summary.filesScanned}`);
  lines.push(`- BLOCKs: **${report.summary.blocks}**`);
  lines.push(`- WARNs: **${report.summary.warns}**`);
  lines.push(`- Decision: **${report.decision}**`);
  lines.push("");
  if (report.ok) {
    lines.push(`✅ No blockers. Review warnings (if any), then you decide whether to push.`);
  } else {
    lines.push(`🛑 Do not push until blockers are fixed or intentionally removed from the tree.`);
  }
  lines.push("");
  lines.push(`## Findings`);
  lines.push("");
  if (!report.findings.length) {
    lines.push(`_None._`);
  } else {
    lines.push(`| Severity | Rule | Path | Message |`);
    lines.push(`|----------|------|------|---------|`);
    for (const f of report.findings) {
      lines.push(
        `| ${f.severity} | \`${f.rule}\` | \`${f.path}\` | ${esc(f.message)} |`
      );
    }
  }
  lines.push("");
  lines.push(`## Criteria (short)`);
  lines.push("");
  lines.push(`- Public: landing, dashboard, vault program, docs without secrets`);
  lines.push(`- Private: trading system, bridges, executors, keys, KYT credentials, ops workers`);
  lines.push(`- See: \`../docs\` boundary or Crypto-Code \`chiller-vault/docs/PUBLIC_PRIVATE_BOUNDARY.md\``);
  lines.push("");
  return lines.join("\n");
}

function esc(s) {
  return String(s).replace(/\|/g, "\\|");
}

scan();
