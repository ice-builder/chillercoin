#!/usr/bin/env node
/**
 * Public feature registry gate. Run before push:
 *   node --test features/check-registry.mjs
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const REGISTRY_PATH = join(ROOT, "features", "registry.json");
const ALLOWED_COVERAGE = ["dedicated", "e2e", "integration", "full"];
const ALLOWED_TEST_TYPES = [
  "dedicated",
  "behavior",
  "static_guard",
  "e2e",
  "integration",
  "full",
];
const MANDATORY_IDS = [
  "CH-ID-001",
  "CH-MINT-001",
  "CH-TRADE-001",
  "CH-FEED-001",
  "CH-PUSH-001",
];

function load() {
  return JSON.parse(readFileSync(REGISTRY_PATH, "utf8"));
}

describe("Public feature registry", () => {
  it("valid JSON with unique ids", () => {
    assert.ok(existsSync(REGISTRY_PATH));
    const r = load();
    const ids = r.features.map((f) => f.id);
    assert.ok(r.features.length > 0);
    assert.equal(ids.length, new Set(ids).size);
  });

  it("files and tests exist", () => {
    const missing = [];
    for (const f of load().features) {
      for (const file of [...f.files, ...f.tests]) {
        if (!existsSync(join(ROOT, file))) missing.push(`${f.id}: ${file}`);
      }
    }
    assert.equal(missing.length, 0, missing.join("\n"));
  });

  it("critical done coverage + assertions", () => {
    const bad = [];
    for (const f of load().features) {
      if (f.status !== "done" || !f.critical) continue;
      if (!ALLOWED_COVERAGE.includes(f.coverage)) bad.push(`${f.id} coverage`);
      if (!f.tests.length) bad.push(`${f.id} tests`);
      if (!ALLOWED_TEST_TYPES.includes(f.test_type)) bad.push(`${f.id} test_type`);
      if (!Array.isArray(f.assertions) || f.assertions.length < 2) {
        bad.push(`${f.id} assertions`);
      }
    }
    assert.equal(bad.length, 0, bad.join("\n"));
  });

  it("mandatory ids present", () => {
    const present = new Set(load().features.map((f) => f.id));
    const missing = MANDATORY_IDS.filter((id) => !present.has(id));
    assert.equal(missing.length, 0, missing.join(", "));
  });
});
