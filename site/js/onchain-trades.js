/**
 * Public TradeLogged tape. Reads Solana RPC only — never the trader host.
 */
(function (global) {
  const TRADE_LOGGED_DISC = [0xa8, 0xcc, 0x72, 0x96, 0x96, 0x7b, 0x6c, 0x4d];

  const DEFAULTS = {
    rpcUrl: "https://api.devnet.solana.com",
    programId: "7ayYqgiiBtXdk13f9DBFTxJoYKkZyr3AaaLt2f2TPDoH",
    vaultPda: "5y4PGY6KkXE1Cdgiz7UaHvUXjWFtdyje4zdgz8pAse62",
    loggerPubkey: "GFSkeQW77EMvZhu8UBut1QFjgzREv3oiLCGM77KdznpU",
    explorerBase: "https://solscan.io/tx/",
    explorerSuffix: "?cluster=devnet",
    sigLimit: 80,
  };

  function mergeCfg(cfg) {
    return Object.assign({}, DEFAULTS, cfg || {});
  }

  async function rpcCall(rpcUrl, method, params) {
    const resp = await fetch(rpcUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
    });
    const data = await resp.json();
    if (data.error) throw new Error(data.error.message || method);
    return data.result;
  }

  async function rpcBatch(rpcUrl, calls) {
    if (!calls.length) return [];
    const out = [];
    const CHUNK = 20;
    for (let i = 0; i < calls.length; i += CHUNK) {
      const slice = calls.slice(i, i + CHUNK);
      const body = slice.map((c, j) => ({
        jsonrpc: "2.0",
        id: j + 1,
        method: c.method,
        params: c.params,
      }));
      const resp = await fetch(rpcUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await resp.json();
      const arr = Array.isArray(data) ? data.slice() : [data];
      arr.sort((a, b) => (a.id || 0) - (b.id || 0));
      for (let k = 0; k < slice.length; k++) {
        const x = arr[k];
        out.push(x && x.result !== undefined ? x.result : null);
      }
    }
    return out;
  }

  function b64ToBytes(b64) {
    try {
      const bin = atob(b64);
      const out = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
      return out;
    } catch {
      return null;
    }
  }

  function readBorshString(bytes, view, off) {
    const len = view.getUint32(off, true);
    const start = off + 4;
    const value = new TextDecoder().decode(bytes.subarray(start, start + len));
    return { value, next: start + len };
  }

  function decodeTradeLogged(bytes, sig) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    let off = 8;
    const pair = readBorshString(bytes, view, off);
    off = pair.next;
    const side = readBorshString(bytes, view, off);
    off = side.next;
    const entry = Number(view.getBigUint64(off, true)) / 1e6;
    off += 8;
    const exitPx = Number(view.getBigUint64(off, true)) / 1e6;
    off += 8;
    const pnl_bps = view.getInt32(off, true);
    off += 4;
    const pnl_usdt = Number(view.getBigInt64(off, true)) / 1e6;
    off += 8;
    const duration = Number(view.getBigUint64(off, true));
    off += 8;
    const nav_after = Number(view.getBigUint64(off, true)) / 1e9;
    off += 8;
    const vault_assets = Number(view.getBigUint64(off, true)) / 1e9;
    off += 8;
    const ts = Number(view.getBigInt64(off, true));
    return {
      pair: pair.value,
      side: side.value,
      entry,
      exit: exitPx,
      pnl_bps,
      pnl_usdt,
      duration,
      nav_after,
      vault_assets,
      ts,
      sig: sig || "",
    };
  }

  function parseTradeLoggedFromLogs(logs, sig) {
    if (!logs || !logs.length) return null;
    for (let i = 0; i < logs.length; i++) {
      const line = logs[i];
      if (typeof line !== "string" || line.indexOf("Program data: ") !== 0) continue;
      const bytes = b64ToBytes(line.slice("Program data: ".length).trim());
      if (!bytes || bytes.length < 24) continue;
      let match = true;
      for (let d = 0; d < 8; d++) {
        if (bytes[d] !== TRADE_LOGGED_DISC[d]) {
          match = false;
          break;
        }
      }
      if (!match) continue;
      return decodeTradeLogged(bytes, sig);
    }
    return null;
  }

  function collectTradesFromTransactions(txs, sigs) {
    const trades = [];
    const seen = new Set();
    for (let i = 0; i < txs.length; i++) {
      const tx = txs[i];
      if (!tx || !tx.meta) continue;
      const sig =
        sigs[i] ||
        (tx.transaction && tx.transaction.signatures && tx.transaction.signatures[0]);
      const row = parseTradeLoggedFromLogs(tx.meta.logMessages, sig);
      if (!row || seen.has(row.sig)) continue;
      seen.add(row.sig);
      trades.push(row);
    }
    trades.sort((a, b) => (b.ts || 0) - (a.ts || 0));
    return trades;
  }

  async function signaturesFor(rpcUrl, addr, limit) {
    if (!addr) return [];
    return (await rpcCall(rpcUrl, "getSignaturesForAddress", [addr, { limit }])) || [];
  }

  async function fetchTradesForAddresses(c, addrs) {
    const seen = new Set();
    const sigs = [];
    for (const addr of addrs) {
      const rows = await signaturesFor(c.rpcUrl, addr, c.sigLimit);
      for (const row of rows) {
        if (!row || !row.signature || seen.has(row.signature)) continue;
        seen.add(row.signature);
        sigs.push(row.signature);
      }
    }
    if (!sigs.length) return [];
    const txs = await rpcBatch(
      c.rpcUrl,
      sigs.map((signature) => ({
        method: "getTransaction",
        params: [
          signature,
          { encoding: "json", commitment: "confirmed", maxSupportedTransactionVersion: 0 },
        ],
      }))
    );
    return collectTradesFromTransactions(txs, sigs);
  }

  async function fetchTrades(cfg) {
    const c = mergeCfg(cfg);
    return fetchTradesForAddresses(c, [c.loggerPubkey, c.vaultPda].filter(Boolean));
  }

  function stats(rows) {
    const n = rows.length;
    const wins = rows.filter((t) => (t.pnl_bps || 0) > 0).length;
    const pnl = rows.reduce((s, t) => s + (t.pnl_bps || 0), 0);
    return {
      n,
      wins,
      losses: n - wins,
      winrate: n ? Math.round((wins / n) * 100) : 0,
      pnlBps: pnl,
    };
  }

  function pairIcon(pair) {
    const p = String(pair || "").toUpperCase();
    if (p.startsWith("BTC")) return "₿";
    if (p.startsWith("ETH")) return "Ξ";
    if (p.startsWith("SOL")) return "◎";
    if (p.startsWith("DOGE")) return "Ð";
    if (p.startsWith("AVAX")) return "🔺";
    if (p.startsWith("LINK")) return "⬡";
    return "•";
  }

  function formatDuration(secs) {
    const s = Number(secs) || 0;
    if (s < 60) return s + "s";
    if (s < 3600) return Math.floor(s / 60) + "m";
    return (s / 3600).toFixed(1) + "h";
  }

  function formatTime(tsSec) {
    const d = new Date((Number(tsSec) || 0) * 1000);
    if (!tsSec || Number.isNaN(d.getTime())) return "—";
    const diffMs = Date.now() - d.getTime();
    const diffH = Math.floor(diffMs / 3600000);
    if (diffH < 1) return Math.max(0, Math.floor(diffMs / 60000)) + "m ago";
    if (diffH < 24) return diffH + "h ago";
    return Math.floor(diffH / 24) + "d ago";
  }

  function formatPx(n) {
    const x = Number(n) || 0;
    if (x >= 1000) return x.toLocaleString(undefined, { maximumFractionDigits: 2 });
    if (x >= 1) return x.toLocaleString(undefined, { maximumFractionDigits: 4 });
    return x.toLocaleString(undefined, { maximumFractionDigits: 6 });
  }

  function explorerUrl(cfg, sig) {
    const c = mergeCfg(cfg);
    return c.explorerBase + sig + (c.explorerSuffix || "");
  }

  function isValidSig(sig) {
    return /^[1-9A-HJ-NP-Za-km-z]{32,88}$/.test(String(sig || ""));
  }

  global.ChillerOnchainTrades = {
    DEFAULTS,
    fetchTrades,
    stats,
    pairIcon,
    formatDuration,
    formatTime,
    formatPx,
    explorerUrl,
    isValidSig,
    parseTradeLoggedFromLogs,
    decodeTradeLogged,
  };
})(typeof window !== "undefined" ? window : globalThis);
