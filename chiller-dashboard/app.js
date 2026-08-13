/**
 * $CHILLER Dashboard — app.js
 * Phantom wallet integration, vault interaction, live trade feed
 */

// ═══════════════════════════════════════════════
// Config
// ═══════════════════════════════════════════════

const CONFIG = {
  programId: '7ayYqgiiBtXdk13f9DBFTxJoYKkZyr3AaaLt2f2TPDoH',
  officialSite: 'https://chillercoin.io',
  officialApp: 'https://app.chillercoin.io',
  officialGithub: 'https://github.com/ice-builder/chillercoin',
  vaultPda: '5y4PGY6KkXE1Cdgiz7UaHvUXjWFtdyje4zdgz8pAse62',
  /** Public demo defaults — switch rpc/network when vault is live on Solana. */
  rpcUrl: 'https://api.devnet.solana.com',
  network: 'demo',
  explorerBase: 'https://solscan.io/tx/',
  explorerSuffix: '?cluster=devnet',
  tradesFeedUrl: 'data/onchain-trades.json',
  /** rpc = public Solana TradeLogged tape; json = static cache fallback. */
  tradesSource: 'rpc',
  loggerPubkey: 'GFSkeQW77EMvZhu8UBut1QFjgzREv3oiLCGM77KdznpU',
  LAMPORTS: 1_000_000_000,
  CHILLER_DECIMALS: 1_000_000,
  NAV_INITIAL: 0.01, // SOL per $CHILLER
  REFRESH_INTERVAL: 30_000, // 30s
  /** Investor mint path — open `deposit` ix is deprecated on-chain. */
  mintPath: 'mint_with_attestation',
  attestationTtlSec: 20 * 60,
};

/** Anchor event discriminator: sha256("event:TradeLogged")[0..8] */
const TRADE_LOGGED_DISC = [0xa8, 0xcc, 0x72, 0x96, 0x96, 0x7b, 0x6c, 0x4d];

const TRADE_UI = {
  filter: 'all',
  query: '',
  loading: false,
  error: '',
};

function isLoopbackHost(host) {
  const h = String(host || '').replace(/^\[|\]$/g, '');
  return h === '127.0.0.1' || h === 'localhost' || h === '::1';
}

function isLoopbackRpcUrl(url) {
  try {
    const u = new URL(url);
    return (u.protocol === 'http:' || u.protocol === 'https:') && isLoopbackHost(u.hostname);
  } catch {
    return false;
  }
}

function applyLocalRpcConfig() {
  if (typeof location === 'undefined') return;
  const params = new URLSearchParams(location.search);
  const rpc = (params.get('rpc') || '').trim();
  const cluster = (params.get('cluster') || params.get('network') || '').trim();
  if (rpc) {
    if (!isLoopbackRpcUrl(rpc)) {
      console.warn('Ignoring rpc= — only loopback RPC is allowed');
      return;
    }
    CONFIG.rpcUrl = rpc.replace(/\/$/, '');
    CONFIG.tradesSource = 'rpc';
    return;
  }
  if (cluster === 'demo') return;
  if (cluster === 'devnet') {
    CONFIG.rpcUrl = 'https://api.devnet.solana.com';
    CONFIG.network = 'devnet';
    CONFIG.tradesSource = 'rpc';
    CONFIG.explorerSuffix = '?cluster=devnet';
    return;
  }
  if (cluster === 'localnet') {
    CONFIG.rpcUrl = 'http://127.0.0.1:8899';
    CONFIG.tradesSource = 'rpc';
    CONFIG.network = 'localnet';
  }
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function isDemoMode() {
  return CONFIG.network === 'demo';
}

// ═══════════════════════════════════════════════
// State
// ═══════════════════════════════════════════════

const STATE = {
  wallet: null,
  walletAddress: null,
  walletProvider: null,
  connected: false,
  eligible: false,
  eligibilityStatus: 'idle', // idle | checking | allow | deny
  vaultData: null,
  userSolBalance: 0,
  userChillerBalance: 0,
  navHistory: [],
  trades: [],
  deposits: [],
  withdraws: [],
  auditLog: [],
  incidents: [],
  theme: 'dark',
};

// On-chain TradeLogged feed (populated from data/onchain-trades.json / RPC)
let ONCHAIN_TRADES = [];

// Demo vault state
const DEMO_VAULT = {
  totalAssets: 42.85,
  totalSupply: 3850,
  highWaterMark: 41.2,
  totalTrades: 0,
  totalWins: 0,
  cumulativePnlBps: 0,
  perfFeeBps: 2000,
  mgmtFeeBps: 200,
  wdFeeBps: 50,
  minDeposit: 0.5,
  maxWdPerEpoch: 100,
  isPaused: false,
};

// ═══════════════════════════════════════════════
// Wallet Connection
// ═══════════════════════════════════════════════

async function connectWallet() {
  const btn = document.getElementById('connect-btn');

  if (STATE.connected) {
    // Disconnect
    audit('wallet_disconnect', { provider: STATE.walletProvider });
    STATE.connected = false;
    STATE.walletAddress = null;
    STATE.walletProvider = null;
    STATE.eligible = false;
    STATE.eligibilityStatus = 'idle';
    STATE.deposits = [];
    STATE.withdraws = [];
    btn.className = 'connect-btn';
    btn.textContent = '🔗 Connect Wallet';
    document.getElementById('stat-balance').textContent = '—';
    document.getElementById('stat-balance-sol').textContent = 'Connect wallet';
    setDepositActionsEnabled(false, 'Connect Wallet');
    document.getElementById('btn-withdraw').disabled = true;
    document.getElementById('btn-withdraw').textContent = 'Connect Wallet';
    renderDepositActivity();
    showToast('Wallet disconnected', 'info');
    return;
  }

  // Open wallet selection modal
  openWalletModal();
}

// ═══════════════════════════════════════════════
// Wallet Modal
// ═══════════════════════════════════════════════

const WALLET_PROVIDERS = {
  phantom: {
    name: 'Phantom',
    getProvider: () => window?.phantom?.solana || window?.solana,
    check: () => !!(window?.phantom?.solana?.isPhantom || window?.solana?.isPhantom),
    icon: '👻',
  },
  solflare: {
    name: 'Solflare',
    getProvider: () => window?.solflare,
    check: () => !!window?.solflare?.isSolflare,
    icon: '🔆',
  },
  backpack: {
    name: 'Backpack',
    getProvider: () => window?.backpack,
    check: () => !!window?.backpack?.isBackpack,
    icon: '🎒',
  },
};

function openWalletModal() {
  const modal = document.getElementById('wallet-modal');
  modal.classList.add('show');

  // Detect installed wallets
  for (const [key, wallet] of Object.entries(WALLET_PROVIDERS)) {
    const el = document.getElementById('detect-' + key);
    if (!el) continue;
    if (wallet.check()) {
      el.textContent = 'Detected';
      el.className = 'wallet-detect detected';
    } else {
      el.textContent = 'Not found';
      el.className = 'wallet-detect not-detected';
    }
  }
}

function closeWalletModal(event) {
  if (event && event.target !== event.currentTarget) return;
  document.getElementById('wallet-modal').classList.remove('show');
}

async function connectSpecificWallet(type) {
  closeWalletModal();
  const btn = document.getElementById('connect-btn');

  if (type === 'demo' || type === 'demo-deny') {
    const deny = type === 'demo-deny';
    STATE.walletAddress = deny
      ? 'DemoDeny' + Math.random().toString(36).substr(2, 5) + '…'
      : 'Demo' + Math.random().toString(36).substr(2, 6) + '…';
    STATE.walletProvider = type;
    STATE.connected = true;
    STATE.userSolBalance = 24.5;
    STATE.userChillerBalance = deny ? 0 : 500;
    btn.className = 'connect-btn connected';
    btn.textContent = '';
    const dot1 = document.createElement('span');
    dot1.className = 'wallet-dot';
    btn.appendChild(dot1);
    btn.appendChild(document.createTextNode('🧊 ' + STATE.walletAddress));
    const badge = document.getElementById('network-badge');
    if (badge) {
      badge.textContent = '⚠️ DEMO';
      badge.style.background = 'rgba(245,158,11,0.15)';
      badge.style.color = '#f59e0b';
    }
    await runEligibilityCheck({ forceDeny: deny });
    return;
  }

  const walletInfo = WALLET_PROVIDERS[type];
  if (!walletInfo) return;

  if (!walletInfo.check()) {
    showToast(`❌ ${walletInfo.name} not detected. Install it first.`, 'error');
    window.open(
      type === 'phantom' ? 'https://phantom.app' :
      type === 'solflare' ? 'https://solflare.com' :
      'https://backpack.app',
      '_blank'
    );
    return;
  }

  try {
    const provider = walletInfo.getProvider();
    const resp = await provider.connect();
    STATE.walletAddress = resp.publicKey.toString();
    STATE.wallet = provider;
    STATE.walletProvider = type;
    STATE.connected = true;

    const short = STATE.walletAddress.slice(0, 4) + '...' + STATE.walletAddress.slice(-4);
    btn.className = 'connect-btn connected';
    btn.textContent = '';
    const dot2 = document.createElement('span');
    dot2.className = 'wallet-dot';
    btn.appendChild(dot2);
    btn.appendChild(document.createTextNode(walletInfo.icon + ' ' + short));

    await fetchUserBalances();
    await runEligibilityCheck();
  } catch (err) {
    console.error('Wallet connect error:', err);
    showToast('❌ ' + (err.message || 'Connection rejected'), 'error');
  }
}

// ═══════════════════════════════════════════════
// Paper KYT / eligibility + deposit state machine
// ═══════════════════════════════════════════════

function audit(action, detail = {}) {
  const ev = {
    at: Date.now(),
    wallet: STATE.walletAddress,
    action,
    detail,
  };
  STATE.auditLog.push(ev);
  try {
    const key = 'chiller_audit_v1';
    const prev = JSON.parse(localStorage.getItem(key) || '[]');
    prev.push(ev);
    localStorage.setItem(key, JSON.stringify(prev.slice(-500)));
  } catch (_) { /* ignore */ }
}

function logIncident(kind, detail = {}) {
  const inc = {
    at: Date.now(),
    wallet: STATE.walletAddress,
    kind,
    detail,
  };
  STATE.incidents.push(inc);
  try {
    const key = 'chiller_activity_v1';
    const prev = JSON.parse(localStorage.getItem(key) || '[]');
    prev.push(inc);
    localStorage.setItem(key, JSON.stringify(prev.slice(-200)));
  } catch (_) { /* ignore */ }
}

/** Silent per-tx screen. No extra UI toasts. Returns allow|deny. */
async function silentPerTxScreen(trigger, { forceDeny = false } = {}) {
  await sleep(400);
  const decision = mockKytDecision(STATE.walletAddress, { forceDeny });
  audit('screen', { trigger, decision });
  if (decision === 'deny') {
    const kind =
      trigger === 'withdraw' ? 'deny_withdraw' :
      trigger === 'connect' ? 'deny_connect' : 'deny_deposit';
    logIncident(kind, { trigger });
  }
  return decision;
}

function setDepositActionsEnabled(enabled, depositLabel) {
  const dep = document.getElementById('btn-deposit');
  const rej = document.getElementById('btn-paper-reject');
  const wdDeny = document.getElementById('btn-paper-wd-deny');
  if (dep) {
    dep.disabled = !enabled;
    if (depositLabel) dep.textContent = depositLabel;
  }
  if (rej) rej.disabled = !enabled;
  if (wdDeny) wdDeny.disabled = !enabled;
}

/** Mock KYT: paper only. Never shows scores/reason codes in UI. */
function mockKytDecision(wallet, { forceDeny = false } = {}) {
  if (forceDeny) return 'deny';
  if (!wallet) return 'deny';
  if (/deny/i.test(wallet)) return 'deny';
  return 'allow';
}

async function runEligibilityCheck(opts = {}) {
  STATE.eligibilityStatus = 'checking';
  STATE.eligible = false;
  setDepositActionsEnabled(false, 'Checking…');
  document.getElementById('btn-withdraw').disabled = true;
  document.getElementById('btn-withdraw').textContent = 'Checking…';
  renderDepositActivity();
  document.getElementById('eligibility-checking')?.classList.add('show');
  audit('wallet_connect', { provider: STATE.walletProvider });

  await sleep(900);
  const decision = await silentPerTxScreen('connect', opts);
  document.getElementById('eligibility-checking')?.classList.remove('show');

  if (decision === 'allow') {
    STATE.eligible = true;
    STATE.eligibilityStatus = 'allow';
    updateUserUI();
    // Soft success only — no AML wording
    showToast(
      isDemoMode()
        ? 'Demo wallet eligible — use Deposit for a paper mint (not on-chain)'
        : 'Wallet connected',
      isDemoMode() ? 'info' : 'success'
    );
  } else {
    STATE.eligible = false;
    STATE.eligibilityStatus = 'deny';
    setDepositActionsEnabled(false, 'Unavailable');
    document.getElementById('btn-withdraw').disabled = true;
    document.getElementById('btn-withdraw').textContent = 'Unavailable';
    renderDepositActivity();
    openEligibilityModal();
  }
}

function openEligibilityModal() {
  document.getElementById('eligibility-modal')?.classList.add('show');
}

function closeEligibilityModal(event) {
  if (event && event.target !== event.currentTarget) return;
  document.getElementById('eligibility-modal')?.classList.remove('show');
}

function disconnectDueToIneligible() {
  closeEligibilityModal();
  if (STATE.connected) connectWallet();
}

function newDepositId() {
  return 'dep_' + Math.random().toString(36).slice(2, 10);
}

function fakeTxid(prefix) {
  return prefix + Math.random().toString(36).slice(2, 10) + Math.random().toString(36).slice(2, 8);
}

/** Paper attestation ticket → mint_with_attestation (open deposit disabled). */
function issuePaperAttestation(dep, tokens) {
  const nonce = 'att_' + Math.random().toString(36).slice(2, 14);
  const att = {
    path: CONFIG.mintPath,
    nonce,
    exp: Date.now() + CONFIG.attestationTtlSec * 1000,
    attested_wallet: STATE.walletAddress,
    amount: dep.amount,
    shares: tokens,
  };
  dep.attestation = att;
  audit('attestation_issued', { deposit_id: dep.id, nonce, path: CONFIG.mintPath });
  return att;
}

function renderDepositActivity() {
  const badge = document.getElementById('eligibility-badge');
  const empty = document.getElementById('deposit-activity-empty');
  const timeline = document.getElementById('deposit-timeline');
  if (!badge || !empty || !timeline) return;

  const map = {
    idle: 'Not connected',
    checking: 'Checking…',
    allow: 'Eligible',
    deny: 'Unavailable',
  };
  badge.textContent = map[STATE.eligibilityStatus] || STATE.eligibilityStatus;
  badge.dataset.status = STATE.eligibilityStatus;

  const hasDep = STATE.deposits.length > 0;
  const hasWd = STATE.withdraws.length > 0;
  if (!hasDep && !hasWd) {
    timeline.hidden = true;
    empty.hidden = false;
    if (STATE.eligibilityStatus === 'deny') {
      empty.textContent = 'This wallet cannot use the vault right now. Actions stay locked.';
    } else if (STATE.eligibilityStatus === 'allow') {
      empty.textContent = 'No activity yet. Deposit mints via attestation only; withdraws are screened before payout.';
    } else {
      empty.textContent = 'Connect a wallet. Deposits use attested mint; withdraws are screened before payout.';
    }
    return;
  }

  empty.hidden = true;
  timeline.hidden = false;
  const depHtml = STATE.deposits
    .slice()
    .reverse()
    .map((d) => {
      const steps = (d.history || []).map((h) =>
        `<li class="deposit-step"><span class="deposit-step-state">${escapeHtml(h.state)}</span><span class="deposit-step-note">${escapeHtml(h.note || '')}</span></li>`
      ).join('');
      const refund = d.refund_txid
        ? `<div class="deposit-refund">Refund tx: <code>${escapeHtml(d.refund_txid)}</code></div>`
        : '';
      const att = d.attestation?.nonce
        ? `<div class="deposit-refund">Attestation: <code>${escapeHtml(d.attestation.nonce)}</code></div>`
        : '';
      return `<article class="deposit-item" data-state="${escapeHtml(d.state)}">
        <div class="deposit-item-head">
          <strong>Deposit ${escapeHtml(d.id)}</strong>
          <span>${escapeHtml(d.amount)} SOL → <em>${escapeHtml(d.state)}</em></span>
        </div>
        <ol class="deposit-steps">${steps}</ol>
        ${att}${refund}
      </article>`;
    })
    .join('');
  const wdHtml = STATE.withdraws
    .slice()
    .reverse()
    .map((w) => {
      const steps = (w.history || [{ state: w.state, note: '' }]).map((h) =>
        `<li class="deposit-step"><span class="deposit-step-state">${escapeHtml(h.state)}</span><span class="deposit-step-note">${escapeHtml(h.note || '')}</span></li>`
      ).join('');
      return `<article class="deposit-item" data-state="${escapeHtml(w.state)}">
        <div class="deposit-item-head">
          <strong>Withdraw ${escapeHtml(w.id)}</strong>
          <span>${escapeHtml(w.amount)} $CHILLER → <em>${escapeHtml(w.state)}</em></span>
        </div>
        <ol class="deposit-steps">${steps}</ol>
      </article>`;
    })
    .join('');
  timeline.innerHTML = depHtml + wdHtml;
}

async function advancePaperDeposit(dep, states) {
  for (const step of states) {
    dep.state = step.state;
    dep.history.push({ state: step.state, note: step.note || '', at: Date.now() });
    if (step.refund_txid) dep.refund_txid = step.refund_txid;
    if (step.shares != null) dep.shares = step.shares;
    renderDepositActivity();
    await sleep(step.wait || 450);
  }
}

async function simulateRejectedDeposit() {
  if (!STATE.connected || !STATE.eligible) {
    showToast('Eligible wallet required for paper reject simulation', 'error');
    return;
  }
  const amount = Math.max(DEMO_VAULT.minDeposit, 1);
  const dep = {
    id: newDepositId(),
    amount,
    state: 'DETECTED',
    history: [{ state: 'DETECTED', note: 'Transfer received', at: Date.now() }],
    refund_txid: null,
    shares: 0,
    attestation: null,
  };
  STATE.deposits.push(dep);
  audit('deposit_submit', { deposit_id: dep.id, amount, paper_reject: true });
  renderDepositActivity();
  showToast('Processing deposit…', 'info');
  await advancePaperDeposit(dep, [
    { state: 'SCREENING', note: 'Verifying transfer…', wait: 400 },
  ]);
  await silentPerTxScreen('deposit', { forceDeny: true });
  await advancePaperDeposit(dep, [
    { state: 'REJECTED', note: 'Transfer not accepted', wait: 400 },
    { state: 'QUARANTINED', note: 'Held for return', wait: 400 },
    { state: 'REFUNDING', note: 'Returning funds…', wait: 500 },
    { state: 'REFUNDED', note: 'Returned', refund_txid: fakeTxid('rfnd_'), wait: 200 },
  ]);
  audit('deposit_refunded', { deposit_id: dep.id, refund_txid: dep.refund_txid });
  showToast('Transfer not accepted — funds returned', 'info');
}

async function executeDeposit() {
  const amount = parseFloat(document.getElementById('deposit-amount').value);
  if (!amount || amount < DEMO_VAULT.minDeposit) {
    showToast('❌ Minimum deposit: ' + DEMO_VAULT.minDeposit + ' SOL', 'error');
    return;
  }

  if (!STATE.connected) {
    showToast('Connect wallet first', 'error');
    return;
  }
  if (!STATE.eligible) {
    openEligibilityModal();
    return;
  }
  if (amount > STATE.userSolBalance) {
    showToast('❌ Insufficient SOL balance', 'error');
    return;
  }

  setDepositActionsEnabled(false, 'Processing…');
  const dep = {
    id: newDepositId(),
    amount,
    state: 'DETECTED',
    history: [{ state: 'DETECTED', note: 'Transfer received', at: Date.now() }],
    refund_txid: null,
    shares: 0,
    attestation: null,
  };
  STATE.deposits.push(dep);
  audit('deposit_submit', { deposit_id: dep.id, amount, mint_path: CONFIG.mintPath });
  renderDepositActivity();
  showToast('Processing deposit…', 'info');

  await advancePaperDeposit(dep, [
    { state: 'SCREENING', note: 'Verifying transfer…', wait: 350 },
  ]);

  // Silent per-tx AML (wallet + source) — no extra user notification
  const txDecision = await silentPerTxScreen('deposit');
  await silentPerTxScreen('tx_source');

  if (txDecision === 'deny') {
    await advancePaperDeposit(dep, [
      { state: 'REJECTED', note: 'Transfer not accepted', wait: 400 },
      { state: 'QUARANTINED', note: 'Held for return', wait: 400 },
      { state: 'REFUNDING', note: 'Returning funds…', wait: 500 },
      { state: 'REFUNDED', note: 'Returned', refund_txid: fakeTxid('rfnd_'), wait: 200 },
    ]);
    audit('deposit_refunded', { deposit_id: dep.id, refund_txid: dep.refund_txid });
    updateUserUI();
    showToast('Transfer not accepted — funds returned', 'info');
    return;
  }

  const nav = getCurrentNAV();
  const tokens = Math.floor(amount / nav);
  const att = issuePaperAttestation(dep, tokens);
  await advancePaperDeposit(dep, [
    {
      state: 'CLEAN_READY',
      note: 'Preparing shares…',
      wait: 350,
    },
    {
      state: 'ATTESTING',
      note: 'Confirming…',
      wait: 400,
    },
    {
      state: 'MINTING',
      note: 'Confirming…',
      wait: 500,
    },
    {
      state: 'MINTED',
      note: tokens + ' $CHILLER',
      shares: tokens,
      wait: 200,
    },
  ]);
  audit('attestation_spent', {
    deposit_id: dep.id,
    nonce: att.nonce,
    path: CONFIG.mintPath,
  });

  STATE.userSolBalance -= amount;
  STATE.userChillerBalance += tokens;
  DEMO_VAULT.totalAssets += amount;
  DEMO_VAULT.totalSupply += tokens;
  document.getElementById('deposit-amount').value = '';
  document.getElementById('deposit-receive').textContent = '0 $CHILLER';
  audit('deposit_minted', {
    deposit_id: dep.id,
    tokens,
    mint_path: CONFIG.mintPath,
    paper: isDemoMode(),
  });
  updateUserUI();
  updateVaultStats();
  if (isDemoMode()) {
    showToast(
      `DEMO paper only — simulated ${amount} SOL → ${tokens} $CHILLER (not on-chain)`,
      'info'
    );
  } else {
    showToast(`Deposited ${amount} SOL → ${tokens} $CHILLER`, 'success');
  }
}

async function fetchUserBalances() {
  if (!STATE.walletAddress || STATE.walletAddress.startsWith('Demo')) return;

  try {
    const resp = await fetch(CONFIG.rpcUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        jsonrpc: '2.0', id: 1,
        method: 'getBalance',
        params: [STATE.walletAddress],
      }),
    });
    const data = await resp.json();
    STATE.userSolBalance = (data.result?.value || 0) / CONFIG.LAMPORTS;
  } catch (e) {
    console.error('Balance fetch error:', e);
  }
}

function updateUserUI() {
  const nav = getCurrentNAV();
  const solVal = (STATE.userChillerBalance * nav).toFixed(4);

  document.getElementById('stat-balance').textContent = STATE.userChillerBalance + ' $CHILLER';
  document.getElementById('stat-balance-sol').textContent = '≈ ' + solVal + ' SOL';
  document.getElementById('user-sol-bal').textContent = STATE.userSolBalance.toFixed(2);
  document.getElementById('user-chiller-bal').textContent = STATE.userChillerBalance;
  document.getElementById('port-chiller').textContent = STATE.userChillerBalance;
  document.getElementById('port-chiller-val').textContent = '≈ ' + solVal + ' SOL';
  document.getElementById('port-sol').textContent = STATE.userSolBalance.toFixed(4);

  if (STATE.eligible) {
    setDepositActionsEnabled(true, 'Deposit SOL');
    document.getElementById('btn-withdraw').disabled = false;
    document.getElementById('btn-withdraw').textContent = 'Withdraw $CHILLER';
  } else {
    setDepositActionsEnabled(false, STATE.connected ? 'Unavailable' : 'Connect Wallet');
    document.getElementById('btn-withdraw').disabled = true;
    document.getElementById('btn-withdraw').textContent = STATE.connected ? 'Unavailable' : 'Connect Wallet';
  }
  renderDepositActivity();
}

// ═══════════════════════════════════════════════
// NAV & Vault
// ═══════════════════════════════════════════════

function getCurrentNAV() {
  if (!DEMO_VAULT.totalSupply) return CONFIG.NAV_INITIAL;
  return DEMO_VAULT.totalAssets / DEMO_VAULT.totalSupply;
}

function updateVaultStats() {
  const v = DEMO_VAULT;
  const nav = getCurrentNAV();
  const winrate = v.totalTrades > 0 ? ((v.totalWins / v.totalTrades) * 100).toFixed(0) : '0';

  document.getElementById('stat-tvl').textContent = v.totalAssets.toFixed(2) + ' SOL';
  document.getElementById('stat-nav').textContent = nav.toFixed(4) + ' SOL';
  document.getElementById('stat-trades').textContent = v.totalTrades;
  document.getElementById('stat-winrate').textContent = 'Win: ' + winrate + '%';
  document.getElementById('stat-tvl-change').textContent = '↑ +' + (v.cumulativePnlBps / 100).toFixed(2) + '%';
  document.getElementById('stat-nav-change').textContent = '↑ +' + ((nav / CONFIG.NAV_INITIAL - 1) * 100).toFixed(2) + '%';

  // Trades page
  document.getElementById('trades-total').textContent = v.totalTrades;
  document.getElementById('trades-winrate').textContent = winrate + '%';
  const pnlBps = v.cumulativePnlBps || 0;
  const pnlSign = pnlBps >= 0 ? '+' : '';
  document.getElementById('trades-pnl').textContent = pnlSign + (pnlBps / 100).toFixed(2) + '%';
  const pnlEl = document.getElementById('trades-pnl');
  if (pnlEl) {
    pnlEl.classList.toggle('profit', pnlBps >= 0);
    pnlEl.classList.toggle('loss', pnlBps < 0);
  }

  // Vault page
  document.getElementById('vault-assets').textContent = v.totalAssets.toFixed(2) + ' SOL';
  document.getElementById('vault-supply').textContent = v.totalSupply.toLocaleString() + ' $CHILLER';
  document.getElementById('vault-hwm').textContent = v.highWaterMark.toFixed(2) + ' SOL';

  // Vault config
  document.getElementById('vault-config').innerHTML = [
    configItem('Performance Fee', (v.perfFeeBps / 100) + '%', 'On profits above HWM'),
    configItem('Management Fee', (v.mgmtFeeBps / 100) + '%', 'Annual on AUM'),
    configItem('Withdrawal Fee', (v.wdFeeBps / 100) + '%', 'Per withdrawal'),
    configItem('Min Deposit', v.minDeposit + ' SOL', ''),
    configItem('Max Daily Withdraw', v.maxWdPerEpoch + ' SOL', 'Per epoch'),
    configItem('Mint path', 'mint_with_attestation', 'Open deposit disabled'),
    configItem('Status', v.isPaused ? '⏸️ Paused' : '✅ Active', ''),
    configItem('Exchange', 'Bybit sleeve', 'Segregated CEX sub-account'),
    configItem('Contract', CONFIG.programId, 'Official program — reject any other id'),
  ].join('');

  // Deposit preview
  document.getElementById('deposit-nav').textContent = nav.toFixed(4) + ' SOL';
}

function configItem(label, value, sub) {
  return `<div class="portfolio-item">
    <div class="portfolio-item-left"><div><div class="portfolio-name">${escapeHtml(label)}</div>${sub ? `<div class="portfolio-sub">${escapeHtml(sub)}</div>` : ''}</div></div>
    <div class="portfolio-value"><div class="portfolio-amount">${escapeHtml(value)}</div></div>
  </div>`;
}

// ═══════════════════════════════════════════════
// Deposit / Withdraw
// ═══════════════════════════════════════════════

function switchAction(action) {
  document.getElementById('form-deposit').style.display = action === 'deposit' ? 'block' : 'none';
  document.getElementById('form-withdraw').style.display = action === 'withdraw' ? 'block' : 'none';
  document.getElementById('tab-deposit').className = 'action-tab' + (action === 'deposit' ? ' active' : '');
  document.getElementById('tab-withdraw').className = 'action-tab' + (action === 'withdraw' ? ' active' : '');
}

function updateDepositPreview() {
  const amount = parseFloat(document.getElementById('deposit-amount').value) || 0;
  const nav = getCurrentNAV();
  const tokens = nav > 0 ? Math.floor(amount / nav) : 0;
  document.getElementById('deposit-receive').textContent = tokens + ' $CHILLER';
}

function updateWithdrawPreview() {
  const tokens = parseInt(document.getElementById('withdraw-amount').value) || 0;
  const nav = getCurrentNAV();
  const gross = tokens * nav;
  const fee = gross * (DEMO_VAULT.wdFeeBps / 10000);
  const net = gross - fee;
  document.getElementById('withdraw-receive').textContent = net.toFixed(4) + ' SOL';
}

function setMaxDeposit() {
  const max = Math.max(0, STATE.userSolBalance - 0.05); // Keep 0.05 SOL for fees
  document.getElementById('deposit-amount').value = max.toFixed(2);
  updateDepositPreview();
}

function setMaxWithdraw() {
  document.getElementById('withdraw-amount').value = STATE.userChillerBalance;
  updateWithdrawPreview();
}

async function executeWithdraw() {
  const tokens = parseInt(document.getElementById('withdraw-amount').value);
  if (!tokens || tokens <= 0) {
    showToast('❌ Enter amount to withdraw', 'error');
    return;
  }

  if (!STATE.connected) {
    showToast('Connect wallet first', 'error');
    return;
  }
  if (!STATE.eligible) {
    openEligibilityModal();
    return;
  }
  if (tokens > STATE.userChillerBalance) {
    showToast('❌ Insufficient $CHILLER', 'error');
    return;
  }

  const wd = {
    id: 'wd_' + Math.random().toString(36).slice(2, 8),
    amount: tokens,
    state: 'REQUESTED',
    history: [{ state: 'REQUESTED', note: 'Withdraw requested', at: Date.now() }],
  };
  STATE.withdraws.push(wd);
  audit('withdraw_submit', { withdraw_id: wd.id, tokens });
  renderDepositActivity();
  showToast('Processing withdrawal…', 'info');
  document.getElementById('btn-withdraw').disabled = true;
  document.getElementById('btn-withdraw').textContent = 'Processing…';

  await sleep(350);
  wd.state = 'SCREENING';
  wd.history.push({ state: 'SCREENING', note: 'Verifying…', at: Date.now() });
  renderDepositActivity();
  const decision = await silentPerTxScreen('withdraw');
  if (decision === 'deny') {
    wd.state = 'REJECTED';
    wd.history.push({ state: 'REJECTED', note: 'Unavailable right now', at: Date.now() });
    audit('withdraw_rejected', { withdraw_id: wd.id });
    renderDepositActivity();
    updateUserUI();
    showToast('Withdrawal unavailable right now', 'info');
    return;
  }

  await sleep(450);
  const nav = getCurrentNAV();
  const gross = tokens * nav;
  const fee = gross * (DEMO_VAULT.wdFeeBps / 10000);
  const net = gross - fee;
  STATE.userChillerBalance -= tokens;
  STATE.userSolBalance += net;
  DEMO_VAULT.totalAssets -= gross;
  DEMO_VAULT.totalSupply -= tokens;
  wd.state = 'PAID';
  wd.history.push({ state: 'PAID', note: net.toFixed(4) + ' SOL', at: Date.now() });
  audit('withdraw_paid', { withdraw_id: wd.id, net });
  renderDepositActivity();
  updateUserUI();
  updateVaultStats();
  document.getElementById('withdraw-amount').value = '';
  document.getElementById('withdraw-receive').textContent = '0 SOL';
  if (isDemoMode()) {
    showToast(
      `DEMO paper only — simulated ${tokens} $CHILLER → ${net.toFixed(4)} SOL (not on-chain)`,
      'info'
    );
  } else {
    showToast(`Withdrew ${tokens} $CHILLER → ${net.toFixed(4)} SOL`, 'success');
  }
}

async function simulateRejectedWithdraw() {
  if (!STATE.connected || !STATE.eligible) {
    showToast('Eligible wallet required', 'error');
    return;
  }
  const tokens = Math.max(1, Math.min(10, STATE.userChillerBalance || 10));
  const wd = {
    id: 'wd_' + Math.random().toString(36).slice(2, 8),
    amount: tokens,
    state: 'REQUESTED',
    history: [{ state: 'REQUESTED', note: 'Withdraw requested', at: Date.now() }],
  };
  STATE.withdraws.push(wd);
  audit('withdraw_submit', { withdraw_id: wd.id, tokens, paper_deny: true });
  renderDepositActivity();
  showToast('Processing withdrawal…', 'info');
  await sleep(350);
  wd.state = 'SCREENING';
  wd.history.push({ state: 'SCREENING', note: 'Verifying…', at: Date.now() });
  renderDepositActivity();
  await silentPerTxScreen('withdraw', { forceDeny: true });
  wd.state = 'REJECTED';
  wd.history.push({ state: 'REJECTED', note: 'Unavailable right now', at: Date.now() });
  audit('withdraw_rejected', { withdraw_id: wd.id, paper_deny: true });
  renderDepositActivity();
  showToast('Withdrawal unavailable right now', 'info');
}

// ═══════════════════════════════════════════════
// Trades Table
// ═══════════════════════════════════════════════

const PAIR_ICONS = {
  'BTC-PERP': '₿', 'ETH-PERP': 'Ξ', 'SOL-PERP': '◎',
  'DOGE-PERP': '🐕', 'AVAX-PERP': '🔺', 'LINK-PERP': '⬡',
};

function pairGlyph(pair) {
  if (window.ChillerOnchainTrades) return ChillerOnchainTrades.pairIcon(pair);
  return PAIR_ICONS[pair] || '•';
}

function filteredTrades() {
  const q = TRADE_UI.query.trim().toUpperCase();
  return ONCHAIN_TRADES.filter((t) => {
    const side = String(t.side || '').toUpperCase();
    const pnl = t.pnl_bps || 0;
    if (TRADE_UI.filter === 'long' && side !== 'LONG') return false;
    if (TRADE_UI.filter === 'short' && side !== 'SHORT') return false;
    if (TRADE_UI.filter === 'win' && pnl <= 0) return false;
    if (TRADE_UI.filter === 'loss' && pnl >= 0) return false;
    if (q && !String(t.pair || '').toUpperCase().includes(q)) return false;
    return true;
  });
}

function setTradeFilter(filter) {
  TRADE_UI.filter = filter;
  document.querySelectorAll('.trade-filter').forEach((el) => {
    el.classList.toggle('active', el.getAttribute('data-filter') === filter);
  });
  renderTrades();
}

function setTradeQuery(value) {
  TRADE_UI.query = value || '';
  renderTrades();
}

function renderTrades() {
  const recentBody = document.getElementById('recent-trades-body');
  const allBody = document.getElementById('all-trades-body');
  const note = document.getElementById('trades-feed-note');
  const pageCopy = document.getElementById('trades-page-copy');

  if (TRADE_UI.loading && !ONCHAIN_TRADES.length) {
    const load = '<tr><td colspan="9" class="trades-empty">Loading on-chain tape…</td></tr>';
    if (recentBody) recentBody.innerHTML = load.replace('colspan="9"', 'colspan="6"');
    if (allBody) allBody.innerHTML = load;
    return;
  }

  const list = filteredTrades().map(normalizeTrade);

  if (!ONCHAIN_TRADES.length) {
    const msg = TRADE_UI.error
      ? 'Could not read Solana RPC. Trades appear here after closed sleeve fills are logged on-chain.'
      : 'No on-chain trades yet. Closed sleeve fills show up here with Solana signatures. No yield is promised.';
    if (recentBody) recentBody.innerHTML = `<tr><td colspan="6" class="trades-empty">${msg}</td></tr>`;
    if (allBody) allBody.innerHTML = `<tr><td colspan="9" class="trades-empty">${msg}</td></tr>`;
    if (note) {
      note.hidden = false;
      note.textContent = msg;
    }
    return;
  }
    if (note) note.hidden = true;
  if (pageCopy) {
    pageCopy.innerHTML =
      'Closed sleeve trades from Solana <code>TradeLogged</code>. Same tape as <a href="https://chillercoin.io/trades.html" target="_blank" rel="noopener">chillercoin.io/trades</a>. Signatures are public. No yield is promised.';
  }

  if (recentBody) recentBody.innerHTML = list.slice(0, 8).map(tradeRow).join('');
  if (allBody) {
    allBody.innerHTML = list.length
      ? list.map(tradeRowFull).join('')
      : '<tr><td colspan="9" class="trades-empty">No trades match this filter.</td></tr>';
  }
}

function normalizeTrade(t) {
  const ts = t.ts || t.time || 0;
  return {
    pair: t.pair || t.symbol || '—',
    side: (t.side || 'LONG').toUpperCase(),
    entry: t.entry,
    exit: t.exit,
    pnl_bps: t.pnl_bps || 0,
    duration: t.duration || 0,
    nav_after: t.nav_after,
    time: ts > 1e12 ? ts : ts * 1000,
    ts: ts > 1e12 ? Math.floor(ts / 1000) : ts,
    tx: t.sig || t.tx || '',
  };
}

function applyTradesToState(rows) {
  ONCHAIN_TRADES = rows;
  STATE.trades = ONCHAIN_TRADES;
  const s = window.ChillerOnchainTrades
    ? ChillerOnchainTrades.stats(ONCHAIN_TRADES)
    : { n: rows.length, wins: 0, winrate: 0, pnlBps: 0 };
  DEMO_VAULT.totalTrades = s.n;
  DEMO_VAULT.totalWins = s.wins;
  DEMO_VAULT.cumulativePnlBps = s.pnlBps;
}

async function loadOnchainTrades() {
  TRADE_UI.loading = true;
  TRADE_UI.error = '';
  renderTrades();
  try {
    if (CONFIG.tradesSource === 'rpc' && window.ChillerOnchainTrades && !isLoopbackRpcUrl(CONFIG.rpcUrl)) {
      const rows = await ChillerOnchainTrades.fetchTrades({
        rpcUrl: CONFIG.rpcUrl,
        programId: CONFIG.programId,
        vaultPda: CONFIG.vaultPda,
        loggerPubkey: CONFIG.loggerPubkey,
        explorerBase: CONFIG.explorerBase,
        explorerSuffix: CONFIG.explorerSuffix,
      });
      applyTradesToState(rows);
      TRADE_UI.loading = false;
      renderTrades();
      updateVaultStats();
      return;
    }
    if (CONFIG.tradesSource === 'rpc') {
      const rows = await fetchTradesFromRpc();
      if (rows.length) {
        applyTradesToState(rows);
        TRADE_UI.loading = false;
        renderTrades();
        updateVaultStats();
        return;
      }
    }
    const resp = await fetch(CONFIG.tradesFeedUrl + '?t=' + Date.now(), { cache: 'no-store' });
    if (!resp.ok) throw new Error('feed ' + resp.status);
    const data = await resp.json();
    applyTradesToState(Array.isArray(data) ? data : []);
  } catch (e) {
    TRADE_UI.error = String(e.message || e);
    console.warn('on-chain trades feed:', TRADE_UI.error);
    ONCHAIN_TRADES = ONCHAIN_TRADES || [];
  }
  TRADE_UI.loading = false;
  renderTrades();
  updateVaultStats();
}

async function rpcCall(method, params) {
  const resp = await fetch(CONFIG.rpcUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
  });
  const data = await resp.json();
  if (data.error) throw new Error(data.error.message || method);
  return data.result;
}

async function rpcBatch(calls) {
  if (!calls.length) return [];
  const body = calls.map((c, i) => ({ jsonrpc: '2.0', id: i + 1, method: c.method, params: c.params }));
  const resp = await fetch(CONFIG.rpcUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await resp.json();
  const arr = Array.isArray(data) ? data.slice() : [data];
  arr.sort((a, b) => (a.id || 0) - (b.id || 0));
  return arr.map((x) => (x && x.result !== undefined ? x.result : null));
}

async function fetchTradesFromRpc() {
  if (isLoopbackRpcUrl(CONFIG.rpcUrl)) {
    return fetchTradesFromRecentBlocks();
  }
  return fetchTradesFromAddressIndex();
}

async function fetchTradesFromAddressIndex() {
  if (window.ChillerOnchainTrades) {
    return ChillerOnchainTrades.fetchTrades({
      rpcUrl: CONFIG.rpcUrl,
      programId: CONFIG.programId,
      vaultPda: CONFIG.vaultPda,
      loggerPubkey: CONFIG.loggerPubkey,
    });
  }
  const addrs = [CONFIG.loggerPubkey, CONFIG.programId, CONFIG.vaultPda].filter(Boolean);
  const seen = new Set();
  const sigs = [];
  for (const addr of addrs) {
    const rows = (await rpcCall('getSignaturesForAddress', [addr, { limit: 40 }])) || [];
    for (const row of rows) {
      if (!row || !row.signature || seen.has(row.signature)) continue;
      seen.add(row.signature);
      sigs.push(row.signature);
    }
  }
  const txs = await rpcBatch(
    sigs.map((signature) => ({
      method: 'getTransaction',
      params: [signature, { encoding: 'json', commitment: 'confirmed', maxSupportedTransactionVersion: 0 }],
    }))
  );
  return collectTradesFromTransactions(txs, sigs);
}

async function fetchTradesFromRecentBlocks() {
  const slot = await rpcCall('getSlot', []);
  const from = Math.max(0, slot - 2500);
  const calls = [];
  for (let s = slot; s >= from; s--) {
    calls.push({
      method: 'getBlock',
      params: [s, {
        encoding: 'json',
        transactionDetails: 'full',
        rewards: false,
        maxSupportedTransactionVersion: 0,
      }],
    });
  }
  const trades = [];
  const seen = new Set();
  const chunk = 40;
  for (let i = 0; i < calls.length; i += chunk) {
    const blocks = await rpcBatch(calls.slice(i, i + chunk));
    for (const block of blocks) {
      const txs = block && block.transactions;
      if (!txs) continue;
      for (const tx of txs) {
        const sig = tx.transaction && tx.transaction.signatures && tx.transaction.signatures[0];
        const logs = tx.meta && tx.meta.logMessages;
        const row = parseTradeLoggedFromLogs(logs, sig);
        if (!row || seen.has(row.sig)) continue;
        seen.add(row.sig);
        trades.push(row);
      }
    }
  }
  trades.sort((a, b) => (b.ts || 0) - (a.ts || 0));
  return trades;
}

function collectTradesFromTransactions(txs, sigs) {
  const trades = [];
  const seen = new Set();
  for (let i = 0; i < txs.length; i++) {
    const tx = txs[i];
    if (!tx || !tx.meta) continue;
    const sig = sigs[i] || (tx.transaction && tx.transaction.signatures && tx.transaction.signatures[0]);
    const row = parseTradeLoggedFromLogs(tx.meta.logMessages, sig);
    if (!row || seen.has(row.sig)) continue;
    seen.add(row.sig);
    trades.push(row);
  }
  trades.sort((a, b) => (b.ts || 0) - (a.ts || 0));
  return trades;
}

function parseTradeLoggedFromLogs(logs, sig) {
  if (!logs || !logs.length) return null;
  for (let i = 0; i < logs.length; i++) {
    const line = logs[i];
    if (typeof line !== 'string' || line.indexOf('Program data: ') !== 0) continue;
    const bytes = b64ToBytes(line.slice('Program data: '.length).trim());
    if (!bytes || bytes.length < 24) continue;
    let match = true;
    for (let d = 0; d < 8; d++) {
      if (bytes[d] !== TRADE_LOGGED_DISC[d]) { match = false; break; }
    }
    if (!match) continue;
    return decodeTradeLogged(bytes, sig);
  }
  return null;
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

function decodeTradeLogged(bytes, sig) {
  if (window.ChillerOnchainTrades) {
    return ChillerOnchainTrades.decodeTradeLogged(bytes, sig);
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let off = 8;
  const pair = readBorshString(bytes, view, off); off = pair.next;
  const side = readBorshString(bytes, view, off); off = side.next;
  const entry = Number(view.getBigUint64(off, true)) / 1e6; off += 8;
  const exitPx = Number(view.getBigUint64(off, true)) / 1e6; off += 8;
  const pnl_bps = view.getInt32(off, true); off += 4;
  const pnl_usdt = Number(view.getBigInt64(off, true)) / 1e6; off += 8;
  const duration = Number(view.getBigUint64(off, true)); off += 8;
  off += 16;
  const ts = Number(view.getBigInt64(off, true));
  return {
    pair: pair.value,
    side: side.value,
    entry,
    exit: exitPx,
    pnl_bps,
    pnl_usdt,
    duration,
    ts,
    sig: sig || '',
  };
}

function readBorshString(bytes, view, off) {
  const len = view.getUint32(off, true);
  const start = off + 4;
  const value = new TextDecoder().decode(bytes.subarray(start, start + len));
  return { value, next: start + len };
}

function tradeTxCell(sig) {
  const api = window.ChillerOnchainTrades;
  const raw = String(sig || '');
  const ok = api ? api.isValidSig(raw) : /^[1-9A-HJ-NP-Za-km-z]{32,88}$/.test(raw);
  if (!ok) return '—';
  const txShort = escapeHtml(raw.slice(0, 8) + '…' + raw.slice(-4));
  if (!CONFIG.explorerBase || isLoopbackRpcUrl(CONFIG.rpcUrl)) {
    return `<span class="tx-link" title="${escapeHtml(raw)}">${txShort}</span>`;
  }
  const href = api
    ? api.explorerUrl(CONFIG, raw)
    : CONFIG.explorerBase + raw + CONFIG.explorerSuffix;
  return `<a class="tx-link" href="${escapeHtml(href)}" target="_blank" rel="noopener">${txShort}</a>`;
}

function tradeRow(t) {
  const isWin = t.pnl_bps > 0;
  const pnl = (t.pnl_bps / 100).toFixed(2);
  const dur = window.ChillerOnchainTrades ? ChillerOnchainTrades.formatDuration(t.duration) : formatDuration(t.duration);
  const time = window.ChillerOnchainTrades ? ChillerOnchainTrades.formatTime(t.ts || t.time / 1000) : formatTime(t.time);
  const pair = escapeHtml(t.pair);
  const side = escapeHtml(t.side);
  const icon = escapeHtml(pairGlyph(t.pair));
  const txCell = tradeTxCell(t.tx);

  return `<tr>
    <td><div class="pair-cell"><span class="pair-icon">${icon}</span>${pair}</div></td>
    <td><span class="side-badge ${side.toLowerCase()}">${side}</span></td>
    <td><span class="pnl-cell ${isWin ? 'profit' : 'loss'}">${isWin ? '+' : ''}${escapeHtml(pnl)}%</span></td>
    <td>${escapeHtml(dur)}</td>
    <td>${escapeHtml(time)}</td>
    <td>${txCell}</td>
  </tr>`;
}

function tradeRowFull(t) {
  const isWin = t.pnl_bps > 0;
  const pnl = (t.pnl_bps / 100).toFixed(2);
  const dur = window.ChillerOnchainTrades ? ChillerOnchainTrades.formatDuration(t.duration) : formatDuration(t.duration);
  const time = window.ChillerOnchainTrades ? ChillerOnchainTrades.formatTime(t.ts || t.time / 1000) : formatTime(t.time);
  const pair = escapeHtml(t.pair);
  const side = escapeHtml(t.side);
  const icon = escapeHtml(pairGlyph(t.pair));
  const txCell = tradeTxCell(t.tx);
  const px = window.ChillerOnchainTrades ? ChillerOnchainTrades.formatPx : (n) => Number(n || 0).toLocaleString();
  const nav = t.nav_after != null ? Number(t.nav_after).toFixed(4) : '—';

  return `<tr>
    <td><div class="pair-cell"><span class="pair-icon">${icon}</span>${pair}</div></td>
    <td><span class="side-badge ${side.toLowerCase()}">${side}</span></td>
    <td class="mono-cell">$${escapeHtml(px(t.entry))}</td>
    <td class="mono-cell">$${escapeHtml(px(t.exit))}</td>
    <td><span class="pnl-cell ${isWin ? 'profit' : 'loss'}">${isWin ? '+' : ''}${escapeHtml(pnl)}%</span></td>
    <td>${escapeHtml(dur)}</td>
    <td class="mono-cell">${escapeHtml(nav)}</td>
    <td>${escapeHtml(time)}</td>
    <td>${txCell}</td>
  </tr>`;
}

function formatDuration(secs) {
  if (secs < 60) return secs + 's';
  if (secs < 3600) return Math.floor(secs / 60) + 'm';
  return (secs / 3600).toFixed(1) + 'h';
}

function formatTime(ts) {
  const d = new Date(ts);
  const now = new Date();
  const diffMs = now - d;
  const diffH = Math.floor(diffMs / 3600000);
  if (diffH < 1) return Math.floor(diffMs / 60000) + 'm ago';
  if (diffH < 24) return diffH + 'h ago';
  return Math.floor(diffH / 24) + 'd ago';
}

// ═══════════════════════════════════════════════
// NAV Chart (Canvas)
// ═══════════════════════════════════════════════

function generateNavHistory() {
  const points = 60;
  const data = [];
  let nav = 0.0100;
  for (let i = 0; i < points; i++) {
    const change = (Math.random() - 0.38) * 0.0003; // Slight upward bias
    nav = Math.max(0.008, nav + change);
    data.push({ time: i, nav: nav });
  }
  STATE.navHistory = data;
}

function drawNavChart() {
  const canvas = document.getElementById('nav-chart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const rect = canvas.parentElement.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;

  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  canvas.style.width = rect.width + 'px';
  canvas.style.height = rect.height + 'px';
  ctx.scale(dpr, dpr);

  const w = rect.width;
  const h = rect.height;
  const data = STATE.navHistory;
  if (!data.length) return;

  const padding = { top: 20, right: 20, bottom: 30, left: 60 };
  const chartW = w - padding.left - padding.right;
  const chartH = h - padding.top - padding.bottom;

  const minNav = Math.min(...data.map(d => d.nav)) * 0.98;
  const maxNav = Math.max(...data.map(d => d.nav)) * 1.02;
  const navRange = maxNav - minNav || 1;

  const xScale = (i) => padding.left + (i / (data.length - 1)) * chartW;
  const yScale = (v) => padding.top + (1 - (v - minNav) / navRange) * chartH;

  // Background grid — theme-aware
  const isLight = STATE.theme === 'light';
  ctx.strokeStyle = isLight ? 'rgba(0,80,180,0.08)' : 'rgba(255,255,255,0.04)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = padding.top + (chartH / 4) * i;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(w - padding.right, y);
    ctx.stroke();

    // Labels
    const val = maxNav - (navRange / 4) * i;
    ctx.fillStyle = isLight ? 'rgba(0,50,120,0.4)' : 'rgba(255,255,255,0.3)';
    ctx.font = '11px JetBrains Mono, monospace';
    ctx.textAlign = 'right';
    ctx.fillText(val.toFixed(4), padding.left - 8, y + 4);
  }

  // Gradient fill
  const gradient = ctx.createLinearGradient(0, padding.top, 0, h - padding.bottom);
  const isProfit = data[data.length - 1].nav >= data[0].nav;
  if (isProfit) {
    gradient.addColorStop(0, 'rgba(0, 199, 255, 0.15)');
    gradient.addColorStop(1, 'rgba(0, 199, 255, 0)');
  } else {
    gradient.addColorStop(0, 'rgba(255, 77, 106, 0.15)');
    gradient.addColorStop(1, 'rgba(255, 77, 106, 0)');
  }

  ctx.beginPath();
  ctx.moveTo(xScale(0), h - padding.bottom);
  data.forEach((d, i) => ctx.lineTo(xScale(i), yScale(d.nav)));
  ctx.lineTo(xScale(data.length - 1), h - padding.bottom);
  ctx.closePath();
  ctx.fillStyle = gradient;
  ctx.fill();

  // Line
  ctx.beginPath();
  data.forEach((d, i) => {
    if (i === 0) ctx.moveTo(xScale(i), yScale(d.nav));
    else ctx.lineTo(xScale(i), yScale(d.nav));
  });
  ctx.strokeStyle = isProfit ? (isLight ? '#0077dd' : '#00c7ff') : (isLight ? '#ef4444' : '#ff4d6a');
  ctx.lineWidth = 2;
  ctx.lineJoin = 'round';
  ctx.stroke();

  // End dot
  const last = data[data.length - 1];
  ctx.beginPath();
  ctx.arc(xScale(data.length - 1), yScale(last.nav), 4, 0, Math.PI * 2);
  ctx.fillStyle = isProfit ? (isLight ? '#0077dd' : '#00c7ff') : (isLight ? '#ef4444' : '#ff4d6a');
  ctx.fill();

  // Glow
  ctx.beginPath();
  ctx.arc(xScale(data.length - 1), yScale(last.nav), 8, 0, Math.PI * 2);
  ctx.fillStyle = isProfit ? 'rgba(0, 199, 255, 0.3)' : 'rgba(255, 77, 106, 0.3)';
  ctx.fill();

  // Current NAV label
  ctx.fillStyle = isLight ? '#1a2d4a' : '#fff';
  ctx.font = 'bold 13px Inter, sans-serif';
  ctx.textAlign = 'left';
  ctx.fillText(last.nav.toFixed(4) + ' SOL', xScale(data.length - 1) - 50, yScale(last.nav) - 14);
}

function setChartPeriod(period) {
  document.querySelectorAll('.period-tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  generateNavHistory();
  drawNavChart();
}

// ═══════════════════════════════════════════════
// Navigation
// ═══════════════════════════════════════════════

function showPage(page) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById('page-' + page).classList.add('active');

  document.querySelectorAll('.nav-item[data-page]').forEach(n => n.classList.remove('active'));
  document.querySelector(`.nav-item[data-page="${page}"]`)?.classList.add('active');

  const titles = { overview: 'Overview', trades: 'Trades', vault: 'Vault' };
  document.getElementById('page-title').textContent = titles[page] || page;
  if (location.hash !== '#' + page) {
    history.replaceState(null, '', '#' + page);
  }
}

// ═══════════════════════════════════════════════
// Toast
// ═══════════════════════════════════════════════

function showToast(msg, type = 'info') {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.className = 'toast show ' + type;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => toast.className = 'toast', 3000);
}

// ═══════════════════════════════════════════════
// Theme
// ═══════════════════════════════════════════════

function toggleTheme() {
  const newTheme = STATE.theme === 'dark' ? 'light' : 'dark';
  setTheme(newTheme);
}

function setTheme(theme) {
  STATE.theme = theme;
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('chiller-theme', theme);
  document.getElementById('theme-icon').textContent = theme === 'dark' ? '☀️' : '🌙';
  // Redraw chart with new colors
  drawNavChart();
}

function initTheme() {
  // Check saved preference
  const saved = localStorage.getItem('chiller-theme');
  if (saved) {
    setTheme(saved);
    return;
  }
  // Check system preference
  if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
    setTheme('light');
  } else {
    setTheme('dark');
  }
  // Listen for system changes
  window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', (e) => {
    if (!localStorage.getItem('chiller-theme')) {
      setTheme(e.matches ? 'light' : 'dark');
    }
  });
}

// ═══════════════════════════════════════════════
// Init
// ═══════════════════════════════════════════════

function fillCanonical() {
  const el = document.getElementById('canonical-program-id');
  if (el) el.textContent = CONFIG.programId;
}

function updateTradeSourceBadges() {
  const rpc = CONFIG.tradesSource === 'rpc';
  const loop = isLoopbackRpcUrl(CONFIG.rpcUrl);
  const label = rpc
    ? (loop ? 'Local RPC · this machine' : 'Solana · on-chain')
    : 'On-chain when live';
  const title = rpc
    ? (loop
      ? 'TradeLogged events from loopback Solana RPC — not a public trader feed'
      : 'TradeLogged events from Solana RPC — public trade tape')
    : 'Empty until live log_trade';
  ['trades-source-badge', 'trades-source-badge-all'].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = label;
    el.title = title;
  });
  const pageCopy = document.getElementById('trades-page-copy');
  if (pageCopy && rpc && !loop) {
    pageCopy.innerHTML =
      'Closed sleeve trades from Solana <code>TradeLogged</code>. Same tape as <a href="https://chillercoin.io/trades.html" target="_blank" rel="noopener">chillercoin.io/trades</a>. Signatures are public. No yield is promised.';
  } else if (pageCopy && rpc) {
    pageCopy.textContent =
      'Read from this machine’s validator (127.0.0.1:8899). Signatures stay local — Solscan will not have them. No yield is promised.';
  }
  const banner = document.querySelector('.demo-banner');
  if (banner && rpc && !loop) {
    banner.innerHTML =
      'Deposits stay paper until go-live. <b>Trades</b> are read from Solana (<code>TradeLogged</code>) — not from the trader host.';
  } else if (banner && rpc) {
    banner.innerHTML =
      'Paper demo for deposits. Trades are read from <b>this machine</b> (<code>127.0.0.1:8899</code>) — no public RPC, no trader VPS.';
  }
}

function init() {
  applyLocalRpcConfig();
  initTheme();
  fillCanonical();
  const badge = document.getElementById('network-badge');
  if (badge) {
    badge.textContent = CONFIG.tradesSource === 'rpc' && isLoopbackRpcUrl(CONFIG.rpcUrl)
      ? 'LOCAL RPC'
      : (CONFIG.network || 'devnet').toUpperCase();
  }
  updateTradeSourceBadges();
  generateNavHistory();
  updateVaultStats();
  drawNavChart();
  renderDepositActivity();
  loadOnchainTrades();
  const hash = (location.hash || '').replace('#', '');
  if (hash === 'trades' || hash === 'vault' || hash === 'overview') showPage(hash);
  window.addEventListener('hashchange', () => {
    const p = (location.hash || '').replace('#', '');
    if (p === 'trades' || p === 'vault' || p === 'overview') showPage(p);
  });

  window.addEventListener('resize', () => drawNavChart());

  setInterval(() => {
    updateVaultStats();
    loadOnchainTrades();
  }, CONFIG.REFRESH_INTERVAL);
}

document.addEventListener('DOMContentLoaded', init);
