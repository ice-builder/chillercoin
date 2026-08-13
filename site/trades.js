(function () {
  const T = window.ChillerOnchainTrades;
  const state = { rows: [], filter: "all", q: "", error: "" };

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  function pass(t) {
    const side = String(t.side || "").toUpperCase();
    const pnl = t.pnl_bps || 0;
    const q = state.q.trim().toUpperCase();
    if (state.filter === "long" && side !== "LONG") return false;
    if (state.filter === "short" && side !== "SHORT") return false;
    if (state.filter === "win" && pnl <= 0) return false;
    if (state.filter === "loss" && pnl >= 0) return false;
    if (q && !String(t.pair || "").toUpperCase().includes(q)) return false;
    return true;
  }

  function render() {
    const vis = state.rows.filter(pass);
    const s = T.stats(state.rows);
    document.getElementById("s-n").textContent = String(s.n);
    document.getElementById("s-wr").textContent = s.n ? s.winrate + "%" : "—";
    const pnl = document.getElementById("s-pnl");
    pnl.textContent = (s.pnlBps >= 0 ? "+" : "") + (s.pnlBps / 100).toFixed(2) + "%";
    pnl.style.color = s.pnlBps < 0 ? "var(--loss)" : "var(--profit)";
    const status = document.getElementById("status");
    if (state.error) status.textContent = "RPC error: " + state.error;
    else status.textContent = vis.length + " of " + state.rows.length + " on-chain closes";
    const body = document.getElementById("rows");
    if (!vis.length) {
      body.innerHTML = '<tr><td colspan="9">No matching on-chain trades.</td></tr>';
      return;
    }
    body.innerHTML = vis.map((t) => {
      const win = (t.pnl_bps || 0) > 0;
      const sig = T.isValidSig(t.sig) ? t.sig : "";
      const tx = sig
        ? `<a class="tx" href="${esc(T.explorerUrl(null, sig))}" target="_blank" rel="noopener">${esc(sig.slice(0, 8) + "…" + sig.slice(-4))}</a>`
        : "—";
      return `<tr>
        <td class="pair">${esc(T.pairIcon(t.pair))} ${esc(t.pair)}</td>
        <td><span class="side ${esc(String(t.side).toLowerCase())}">${esc(t.side)}</span></td>
        <td>$${esc(T.formatPx(t.entry))}</td>
        <td>$${esc(T.formatPx(t.exit))}</td>
        <td class="pnl ${win ? "profit" : "loss"}">${win ? "+" : ""}${(t.pnl_bps / 100).toFixed(2)}%</td>
        <td>${esc(T.formatDuration(t.duration))}</td>
        <td>${t.nav_after != null ? Number(t.nav_after).toFixed(4) : "—"}</td>
        <td>${esc(T.formatTime(t.ts))}</td>
        <td>${tx}</td>
      </tr>`;
    }).join("");
  }

  async function load() {
    document.getElementById("status").textContent = "Loading Solana…";
    try {
      state.rows = await T.fetchTrades();
      state.error = "";
    } catch (e) {
      state.error = String(e.message || e);
      state.rows = [];
    }
    render();
  }

  document.querySelectorAll(".chip[data-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.filter = btn.getAttribute("data-filter");
      document.querySelectorAll(".chip[data-filter]").forEach((b) => b.classList.toggle("active", b === btn));
      render();
    });
  });
  document.getElementById("q").addEventListener("input", (e) => {
    state.q = e.target.value;
    render();
  });
  document.getElementById("refresh").addEventListener("click", load);
  load();
  setInterval(load, 30000);
})();
