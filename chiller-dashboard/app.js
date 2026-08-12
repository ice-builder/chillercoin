/**
 * $CHILLER Dashboard — app.js
 * Phantom wallet integration, vault interaction, live trade feed
 */

// ═══════════════════════════════════════════════
// Config
// ═══════════════════════════════════════════════

const CONFIG = {
  programId: '7ayYqgiiBtXdk13f9DBFTxJoYKkZyr3AaaLt2f2TPDoH',
  rpcUrl: 'https://api.devnet.solana.com',
  network: 'devnet',
  explorerBase: 'https://solscan.io/tx/',
  explorerSuffix: '?cluster=devnet',
  LAMPORTS: 1_000_000_000,
  CHILLER_DECIMALS: 1_000_000,
  NAV_INITIAL: 0.01, // SOL per $CHILLER
  REFRESH_INTERVAL: 30_000, // 30s
  /** Investor mint path — open `deposit` ix is deprecated on-chain. */
  mintPath: 'mint_with_attestation',
  attestationTtlSec: 20 * 60,
};

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

// Demo trades for display
const DEMO_TRADES = [
  { pair: 'BTC-PERP', side: 'LONG', entry: 97450, exit: 98630, pnl_bps: 121, duration: 3600, time: Date.now() - 1800000, tx: '5VJYYm...abc1' },
  { pair: 'ETH-PERP', side: 'SHORT', entry: 3580, exit: 3520, pnl_bps: 168, duration: 2700, time: Date.now() - 5400000, tx: '4ffMqp...abc2' },
  { pair: 'SOL-PERP', side: 'LONG', entry: 178.5, exit: 181.2, pnl_bps: 151, duration: 5400, time: Date.now() - 9000000, tx: '8xKpWn...abc3' },
  { pair: 'ETH-PERP', side: 'LONG', entry: 3510, exit: 3490, pnl_bps: -57, duration: 1800, time: Date.now() - 14400000, tx: '3mNqRt...abc4' },
  { pair: 'BTC-PERP', side: 'SHORT', entry: 98100, exit: 97350, pnl_bps: 76, duration: 7200, time: Date.now() - 21600000, tx: '9pLzKq...abc5' },
  { pair: 'SOL-PERP', side: 'LONG', entry: 175.8, exit: 179.1, pnl_bps: 188, duration: 4500, time: Date.now() - 28800000, tx: '2dWxYr...abc6' },
  { pair: 'BTC-PERP', side: 'LONG', entry: 96800, exit: 97650, pnl_bps: 88, duration: 6000, time: Date.now() - 36000000, tx: '7hNfVs...abc7' },
  { pair: 'ETH-PERP', side: 'SHORT', entry: 3545, exit: 3560, pnl_bps: -42, duration: 900, time: Date.now() - 43200000, tx: '6kMtBp...abc8' },
];

// Demo vault state
const DEMO_VAULT = {
  totalAssets: 42.85,
  totalSupply: 3850,
  highWaterMark: 41.2,
  totalTrades: 47,
  totalWins: 34,
  cumulativePnlBps: 1850,
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
  if (dep) {
    dep.disabled = !enabled;
    if (depositLabel) dep.textContent = depositLabel;
  }
  if (rej) rej.disabled = !enabled;
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
    showToast('Wallet connected', 'success');
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

  if (!STATE.deposits.length) {
    timeline.hidden = true;
    empty.hidden = false;
    if (STATE.eligibilityStatus === 'deny') {
      empty.textContent = 'This wallet cannot use the vault under our compliance policy. Deposit UI stays locked.';
    } else if (STATE.eligibilityStatus === 'allow') {
      empty.textContent = 'No deposits yet. Deposits mint via attestation only (open deposit disabled).';
    } else {
      empty.textContent = 'Connect a wallet to continue. Deposits use attested mint; rejected transfers are returned.';
    }
    return;
  }

  empty.hidden = true;
  timeline.hidden = false;
  timeline.innerHTML = STATE.deposits
    .slice()
    .reverse()
    .map((d) => {
      const steps = (d.history || []).map((h) =>
        `<li class="deposit-step"><span class="deposit-step-state">${h.state}</span><span class="deposit-step-note">${h.note || ''}</span></li>`
      ).join('');
      const refund = d.refund_txid
        ? `<div class="deposit-refund">Refund tx: <code>${d.refund_txid}</code></div>`
        : '';
      return `<article class="deposit-item" data-state="${d.state}">
        <div class="deposit-item-head">
          <strong>${d.id}</strong>
          <span>${d.amount} SOL → <em>${d.state}</em></span>
        </div>
        <ol class="deposit-steps">${steps}</ol>
        ${refund}
      </article>`;
    })
    .join('');
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
  audit('deposit_minted', { deposit_id: dep.id, tokens, mint_path: CONFIG.mintPath });
  updateUserUI();
  updateVaultStats();
  showToast(`Deposited ${amount} SOL → ${tokens} $CHILLER`, 'success');
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
  document.getElementById('trades-pnl').textContent = '+' + (v.cumulativePnlBps / 100).toFixed(2) + '%';

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
    configItem('Contract', CONFIG.programId.slice(0, 8) + '...', ''),
  ].join('');

  // Deposit preview
  document.getElementById('deposit-nav').textContent = nav.toFixed(4) + ' SOL';
}

function configItem(label, value, sub) {
  return `<div class="portfolio-item">
    <div class="portfolio-item-left"><div><div class="portfolio-name">${label}</div>${sub ? `<div class="portfolio-sub">${sub}</div>` : ''}</div></div>
    <div class="portfolio-value"><div class="portfolio-amount">${value}</div></div>
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
  };
  STATE.withdraws.push(wd);
  audit('withdraw_submit', { withdraw_id: wd.id, tokens });
  showToast('Processing withdrawal…', 'info');
  document.getElementById('btn-withdraw').disabled = true;
  document.getElementById('btn-withdraw').textContent = 'Processing…';

  await sleep(400);
  wd.state = 'SCREENING';
  // Silent AML before payout — no extra AML toast
  const decision = await silentPerTxScreen('withdraw');
  if (decision === 'deny') {
    wd.state = 'REJECTED';
    audit('withdraw_rejected', { withdraw_id: wd.id });
    updateUserUI();
    showToast('Withdrawal unavailable right now', 'info');
    return;
  }

  await sleep(500);
  const nav = getCurrentNAV();
  const gross = tokens * nav;
  const fee = gross * (DEMO_VAULT.wdFeeBps / 10000);
  const net = gross - fee;
  STATE.userChillerBalance -= tokens;
  STATE.userSolBalance += net;
  DEMO_VAULT.totalAssets -= gross;
  DEMO_VAULT.totalSupply -= tokens;
  wd.state = 'PAID';
  audit('withdraw_paid', { withdraw_id: wd.id, net });
  updateUserUI();
  updateVaultStats();
  document.getElementById('withdraw-amount').value = '';
  document.getElementById('withdraw-receive').textContent = '0 SOL';
  showToast(`Withdrew ${tokens} $CHILLER → ${net.toFixed(4)} SOL`, 'success');
}

// ═══════════════════════════════════════════════
// Trades Table
// ═══════════════════════════════════════════════

const PAIR_ICONS = {
  'BTC-PERP': '₿', 'ETH-PERP': 'Ξ', 'SOL-PERP': '◎',
  'DOGE-PERP': '🐕', 'AVAX-PERP': '🔺', 'LINK-PERP': '⬡',
};

function renderTrades() {
  const recentBody = document.getElementById('recent-trades-body');
  const allBody = document.getElementById('all-trades-body');

  const recentHtml = DEMO_TRADES.slice(0, 5).map(tradeRow).join('');
  const allHtml = DEMO_TRADES.map(tradeRowFull).join('');

  recentBody.innerHTML = recentHtml;
  allBody.innerHTML = allHtml;
}

function tradeRow(t) {
  const isWin = t.pnl_bps > 0;
  const pnl = (t.pnl_bps / 100).toFixed(2);
  const dur = formatDuration(t.duration);
  const time = formatTime(t.time);
  const icon = PAIR_ICONS[t.pair] || '•';

  return `<tr>
    <td><div class="pair-cell"><span class="pair-icon">${icon}</span>${t.pair}</div></td>
    <td><span class="side-badge ${t.side.toLowerCase()}">${t.side}</span></td>
    <td><span class="pnl-cell ${isWin ? 'profit' : 'loss'}">${isWin ? '+' : ''}${pnl}%</span></td>
    <td>${dur}</td>
    <td>${time}</td>
    <td><a class="tx-link" href="${CONFIG.explorerBase}${t.tx}${CONFIG.explorerSuffix}" target="_blank">${t.tx}</a></td>
  </tr>`;
}

function tradeRowFull(t) {
  const isWin = t.pnl_bps > 0;
  const pnl = (t.pnl_bps / 100).toFixed(2);
  const dur = formatDuration(t.duration);
  const time = formatTime(t.time);
  const icon = PAIR_ICONS[t.pair] || '•';
  const nav = getCurrentNAV();

  return `<tr>
    <td><div class="pair-cell"><span class="pair-icon">${icon}</span>${t.pair}</div></td>
    <td><span class="side-badge ${t.side.toLowerCase()}">${t.side}</span></td>
    <td style="font-family:var(--font-mono)">$${t.entry.toLocaleString()}</td>
    <td style="font-family:var(--font-mono)">$${t.exit.toLocaleString()}</td>
    <td><span class="pnl-cell ${isWin ? 'profit' : 'loss'}">${isWin ? '+' : ''}${pnl}%</span></td>
    <td>${dur}</td>
    <td style="font-family:var(--font-mono)">${nav.toFixed(4)}</td>
    <td>${time}</td>
    <td><a class="tx-link" href="${CONFIG.explorerBase}${t.tx}${CONFIG.explorerSuffix}" target="_blank">${t.tx}</a></td>
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

function init() {
  initTheme();
  generateNavHistory();
  updateVaultStats();
  renderTrades();
  drawNavChart();
  renderDepositActivity();

  // Redraw chart on resize
  window.addEventListener('resize', () => drawNavChart());

  // Auto-refresh
  setInterval(() => {
    updateVaultStats();
    renderTrades();
  }, CONFIG.REFRESH_INTERVAL);
}

document.addEventListener('DOMContentLoaded', init);
