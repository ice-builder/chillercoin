#!/usr/bin/env node
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const PROGRAM = "7ayYqgiiBtXdk13f9DBFTxJoYKkZyr3AaaLt2f2TPDoH";
const app = readFileSync(join(ROOT, "chiller-dashboard", "app.js"), "utf8");
const html = readFileSync(join(ROOT, "chiller-dashboard", "index.html"), "utf8");
const rust = readFileSync(
  join(ROOT, "chiller-vault", "programs", "chiller-vault", "src", "lib.rs"),
  "utf8"
);

describe("CH-ID-001 canonical identity", () => {
  it("program id in CONFIG and HTML", () => {
    assert.match(app, new RegExp(`programId:\\s*'${PROGRAM}'`));
    assert.ok(html.includes(PROGRAM));
    assert.ok(html.includes("chillercoin.io"));
    assert.ok(html.includes("app.chillercoin.io"));
  });
});

describe("CH-SITE-001 dashboard shell", () => {
  it("three pages + demo wallets", () => {
    for (const p of ["overview", "trades", "vault"]) {
      assert.ok(html.includes(`id="page-${p}"`), p);
    }
    assert.ok(html.includes("Demo Mode"));
    assert.ok(html.includes("Demo Ineligible"));
    assert.match(app, /network:\s*'demo'/);
  });
});

describe("CH-MINT-001 attested mint path", () => {
  it("UI and program use mint_with_attestation", () => {
    assert.match(app, /mintPath:\s*'mint_with_attestation'/);
    assert.ok(rust.includes("mint_with_attestation"));
    assert.ok(rust.includes("OpenDepositDeprecated"));
  });
});

describe("CH-FEED-001 on-chain TradeLogged tape", () => {
  it("reads Solana RPC, never the trader host", () => {
    assert.match(app, /tradesSource:\s*'rpc'/);
    assert.match(app, /tradesFeedUrl:\s*'data\/onchain-trades\.json'/);
    assert.match(app, /ChillerOnchainTrades/);
    assert.match(app, /cluster === 'localnet'/);
    assert.equal(app.includes("isLoopbackHost(location.hostname)"), false);
    const deploy = readFileSync(join(ROOT, "chiller-dashboard", "deploy.sh"), "utf8");
    assert.match(deploy, /js\/onchain-trades\.js/);
    assert.match(deploy, /trades\.html/);
    assert.equal(deploy.includes("chiller-tg-bot"), false);
    assert.equal(app.includes("vault_bridge"), false);
    assert.equal(app.includes("185.207."), false);
    const feed = readFileSync(
      join(ROOT, "chiller-dashboard", "js", "onchain-trades.js"),
      "utf8"
    );
    const siteFeed = readFileSync(join(ROOT, "site", "js", "onchain-trades.js"), "utf8");
    assert.equal(feed, siteFeed);
    assert.match(feed, /TradeLogged/);
    assert.match(feed, /getSignaturesForAddress/);
    assert.equal(feed.includes("185.207."), false);
    const tape = readFileSync(join(ROOT, "site", "trades.html"), "utf8");
    assert.match(tape, /onchain-trades\.js/);
    assert.match(tape, /No yield|not a return promise/i);
    assert.equal(tape.includes("vault_bridge"), false);
  });
});
