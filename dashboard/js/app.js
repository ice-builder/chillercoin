// ═══════════════════════════════════════════════════
// $CHILLER Dashboard — Mobile PWA
// ═══════════════════════════════════════════════════

(function() {
    'use strict';

    // ─── Config ──────────────────────────────────
    const CONFIG = {
        PROGRAM_ID: '7ayYqgiiBtXdk13f9DBFTxJoYKkZyr3AaaLt2f2TPDoH',
        RPC_URL: 'https://api.devnet.solana.com',
        NETWORK: 'devnet',
    };

    // ─── Demo Data (replaced by on-chain data when connected) ───
    const DEMO = {
        nav: 1.1505,
        totalAssets: 24750_000000,  // $24,750 in lamports
        totalSupply: 21512_000000,
        totalTrades: 431,
        totalWins: 366,
        holders: 12,
        nav24h: 0.34,
        nav7d: 2.18,
        nav30d: 8.45,
        navAll: 15.05,
        trades: [
            { pair: 'ETHUSDT', side: 'SHORT', pnl_bps: 124, pnl_usdt: 31.20, time: '2h ago' },
            { pair: 'SOLUSDT', side: 'LONG', pnl_bps: 95, pnl_usdt: 18.50, time: '5h ago' },
            { pair: 'ADAUSDT', side: 'LONG', pnl_bps: -42, pnl_usdt: -8.40, time: '8h ago' },
            { pair: 'BTCUSDT', side: 'SHORT', pnl_bps: 88, pnl_usdt: 22.10, time: '12h ago' },
            { pair: 'AVAXUSDT', side: 'SHORT', pnl_bps: 210, pnl_usdt: 42.00, time: '18h ago' },
            { pair: 'LINKUSDT', side: 'LONG', pnl_bps: 67, pnl_usdt: 13.40, time: '1d ago' },
            { pair: 'XRPUSDT', side: 'SHORT', pnl_bps: -31, pnl_usdt: -6.20, time: '1d ago' },
            { pair: 'DOTUSDT', side: 'SHORT', pnl_bps: 156, pnl_usdt: 31.20, time: '2d ago' },
        ],
        navHistory: generateNavHistory(),
    };

    function generateNavHistory() {
        const pts = [];
        let val = 1.0;
        for (let i = 0; i < 60; i++) {
            val += (Math.random() - 0.35) * 0.012;
            val = Math.max(val, 0.95);
            pts.push(val);
        }
        pts.push(1.1505);
        return pts;
    }

    // ─── State ───────────────────────────────────
    let state = {
        connected: false,
        wallet: null,
        balance: 0,       // $CHILLER tokens
        depositCost: 0,    // USDT deposited
    };

    // ─── DOM ─────────────────────────────────────
    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    // ─── Init ────────────────────────────────────
    function init() {
        renderDemoData();
        drawNavChart();
        setupModals();
        setupNavigation();
        setupWallet();
    }

    // ─── Render Demo Data ────────────────────────
    function renderDemoData() {
        // NAV price
        $('#navPrice').textContent = `$${DEMO.nav.toFixed(4)}`;

        // NAV changes
        $('#nav24h').textContent = `+${DEMO.nav24h}%`;
        $('#nav7d').textContent = `+${DEMO.nav7d}%`;
        $('#nav30d').textContent = `+${DEMO.nav30d}%`;
        $('#navAll').textContent = `+${DEMO.navAll}%`;

        // Vault stats
        const tvl = DEMO.totalAssets / 1_000_000;
        $('#statTVL').textContent = `$${formatNum(tvl)}`;
        $('#statTrades').textContent = DEMO.totalTrades.toLocaleString();
        const wr = ((DEMO.totalWins / DEMO.totalTrades) * 100).toFixed(1);
        $('#statWR').textContent = `${wr}%`;
        $('#statHolders').textContent = DEMO.holders;

        // Trades list
        renderTrades(DEMO.trades.slice(0, 5));

        // Deposit/withdraw nav
        $('#depositNav').textContent = `$${DEMO.nav.toFixed(4)}`;
        $('#withdrawNav').textContent = `$${DEMO.nav.toFixed(4)}`;
    }

    function renderTrades(trades) {
        const list = $('#tradesList');
        list.innerHTML = trades.map(t => {
            const win = t.pnl_bps > 0;
            const pnlStr = win ? `+${(t.pnl_bps / 100).toFixed(2)}%` : `${(t.pnl_bps / 100).toFixed(2)}%`;
            const usdStr = win ? `+$${t.pnl_usdt.toFixed(2)}` : `-$${Math.abs(t.pnl_usdt).toFixed(2)}`;
            return `
                <div class="trade-item">
                    <div class="ti-left">
                        <div class="ti-icon ${win ? 'win' : 'loss'}">${win ? '✓' : '✗'}</div>
                        <div>
                            <span class="ti-pair">${t.pair}</span>
                            <span class="ti-side">${t.side}</span>
                        </div>
                    </div>
                    <div class="ti-right">
                        <span class="ti-pnl ${win ? 'green' : 'red'}">${pnlStr}</span>
                        <span class="ti-time">${usdStr} · ${t.time}</span>
                    </div>
                </div>
            `;
        }).join('');
    }

    // ─── NAV Chart (Canvas) ──────────────────────
    function drawNavChart() {
        const canvas = $('#navChart');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const dpr = window.devicePixelRatio || 1;
        const rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * dpr;
        canvas.height = 120 * dpr;
        ctx.scale(dpr, dpr);

        const w = rect.width;
        const h = 120;
        const data = DEMO.navHistory;
        const min = Math.min(...data) * 0.995;
        const max = Math.max(...data) * 1.005;

        // Gradient fill
        const grad = ctx.createLinearGradient(0, 0, 0, h);
        grad.addColorStop(0, 'rgba(0,180,255,0.2)');
        grad.addColorStop(1, 'rgba(0,180,255,0)');

        // Draw filled area
        ctx.beginPath();
        data.forEach((v, i) => {
            const x = (i / (data.length - 1)) * w;
            const y = h - ((v - min) / (max - min)) * (h - 10) - 5;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.lineTo(w, h);
        ctx.lineTo(0, h);
        ctx.closePath();
        ctx.fillStyle = grad;
        ctx.fill();

        // Draw line
        ctx.beginPath();
        data.forEach((v, i) => {
            const x = (i / (data.length - 1)) * w;
            const y = h - ((v - min) / (max - min)) * (h - 10) - 5;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.strokeStyle = '#00b4ff';
        ctx.lineWidth = 2;
        ctx.lineJoin = 'round';
        ctx.stroke();

        // Current price dot
        const lastX = w;
        const lastY = h - ((data[data.length - 1] - min) / (max - min)) * (h - 10) - 5;
        ctx.beginPath();
        ctx.arc(lastX - 2, lastY, 4, 0, Math.PI * 2);
        ctx.fillStyle = '#00b4ff';
        ctx.fill();
        ctx.beginPath();
        ctx.arc(lastX - 2, lastY, 8, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(0,180,255,0.2)';
        ctx.fill();
    }

    // ─── Modals ──────────────────────────────────
    function setupModals() {
        // Deposit
        $('#depositBtn').addEventListener('click', () => openModal('depositModal'));
        $('#closeDeposit').addEventListener('click', () => closeModal('depositModal'));

        // Withdraw
        $('#withdrawBtn').addEventListener('click', () => openModal('withdrawModal'));
        $('#closeWithdraw').addEventListener('click', () => closeModal('withdrawModal'));

        // Close on overlay click
        $$('.modal-overlay').forEach(el => {
            el.addEventListener('click', (e) => {
                if (e.target === el) closeModal(el.id);
            });
        });

        // Deposit amount input
        const depInput = $('#depositAmount');
        depInput.addEventListener('input', () => {
            const amt = parseFloat(depInput.value) || 0;
            const tokens = amt / DEMO.nav;
            $('#depositReceive').textContent = tokens.toFixed(2);
            $('#depositAmt').textContent = `$${amt.toFixed(2)}`;
            $('#depositRcv').textContent = `${tokens.toFixed(2)} $CHILLER`;
        });

        // Withdraw amount input
        const wdInput = $('#withdrawAmount');
        wdInput.addEventListener('input', () => {
            const tokens = parseFloat(wdInput.value) || 0;
            const gross = tokens * DEMO.nav;
            const fee = gross * 0.005;
            const net = gross - fee;
            $('#withdrawReceive').textContent = net.toFixed(2);
            $('#withdrawBurn').textContent = `${tokens.toFixed(2)} $CHILLER`;
            $('#withdrawGross').textContent = `$${gross.toFixed(2)}`;
            $('#withdrawFee').textContent = `$${fee.toFixed(2)}`;
            $('#withdrawNet').textContent = `$${net.toFixed(2)} USDT`;
        });
    }

    function openModal(id) {
        const modal = $(`#${id}`);
        modal.classList.add('open');
        document.body.style.overflow = 'hidden';
    }

    function closeModal(id) {
        const modal = $(`#${id}`);
        modal.classList.remove('open');
        document.body.style.overflow = '';
    }

    // ─── Bottom Navigation ───────────────────────
    function setupNavigation() {
        $$('.bnav-item').forEach(btn => {
            btn.addEventListener('click', () => {
                $$('.bnav-item').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');

                const tab = btn.dataset.tab;
                // Scroll to relevant section
                if (tab === 'portfolio') {
                    $('#portfolioCard').scrollIntoView({ behavior: 'smooth' });
                } else if (tab === 'trades') {
                    $('.trades-card').scrollIntoView({ behavior: 'smooth' });
                } else if (tab === 'vault') {
                    $('.stats-card').scrollIntoView({ behavior: 'smooth' });
                }
            });
        });
    }

    // ─── Wallet Connect (Phantom) ────────────────
    function setupWallet() {
        const btn = $('#walletBtn');
        btn.addEventListener('click', async () => {
            if (state.connected) {
                disconnectWallet();
                return;
            }
            await connectWallet();
        });
    }

    async function connectWallet() {
        const provider = window.solana;
        if (!provider || !provider.isPhantom) {
            // No Phantom — show install prompt
            const ok = confirm('Phantom wallet not found.\n\nOpen Phantom download page?');
            if (ok) window.open('https://phantom.app/', '_blank');
            return;
        }

        try {
            const resp = await provider.connect();
            state.wallet = resp.publicKey.toString();
            state.connected = true;

            // Update UI
            const short = state.wallet.slice(0, 4) + '...' + state.wallet.slice(-4);
            $('#walletLabel').textContent = short;
            $('#walletBtn').classList.add('connected');

            // Enable buttons
            $('#confirmDeposit').disabled = false;
            $('#confirmDeposit').textContent = 'Confirm Deposit';
            $('#confirmWithdraw').disabled = false;
            $('#confirmWithdraw').textContent = 'Confirm Withdrawal';

            // Show demo balance
            showDemoBalance();

        } catch (err) {
            console.error('Wallet connect failed:', err);
        }
    }

    function disconnectWallet() {
        if (window.solana) window.solana.disconnect();
        state.connected = false;
        state.wallet = null;
        state.balance = 0;

        $('#walletLabel').textContent = 'Connect';
        $('#walletBtn').classList.remove('connected');
        $('#confirmDeposit').disabled = true;
        $('#confirmDeposit').textContent = 'Connect Wallet First';
        $('#confirmWithdraw').disabled = true;
        $('#confirmWithdraw').textContent = 'Connect Wallet First';

        $('#portfolioValue').textContent = '$0.00';
        $('#portfolioTokens').textContent = '0 $CHILLER';
        $('#pnlBadge').textContent = '+$0.00 (0%)';
        $('#pnlBadge').className = 'pnl-badge neutral';
    }

    function showDemoBalance() {
        // Simulated: user has 500 $CHILLER, deposited at $1.00
        state.balance = 500;
        state.depositCost = 500;

        const value = state.balance * DEMO.nav;
        const pnl = value - state.depositCost;
        const pnlPct = ((pnl / state.depositCost) * 100).toFixed(2);

        $('#portfolioValue').textContent = `$${value.toFixed(2)}`;
        $('#portfolioTokens').textContent = `${state.balance} $CHILLER`;

        const badge = $('#pnlBadge');
        if (pnl >= 0) {
            badge.textContent = `+$${pnl.toFixed(2)} (+${pnlPct}%)`;
            badge.className = 'pnl-badge green';
        } else {
            badge.textContent = `-$${Math.abs(pnl).toFixed(2)} (${pnlPct}%)`;
            badge.className = 'pnl-badge red';
        }
    }

    // ─── Helpers ──────────────────────────────────
    function formatNum(n) {
        if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
        if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
        return n.toFixed(0);
    }

    // ─── Resize handler for chart ────────────────
    window.addEventListener('resize', () => drawNavChart());

    // ─── Start ───────────────────────────────────
    document.addEventListener('DOMContentLoaded', init);
})();
