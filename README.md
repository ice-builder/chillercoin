<p align="center">
  <img src="assets/banner.png" alt="$CHILLER Banner" width="100%">
</p>

<p align="center">
  <strong>🧊 AI-Powered Yield Vault on Solana</strong><br>
  <em>Deposit SOL → Get $CHILLER → AI trades → You earn. Just chill.</em>
</p>

<p align="center">
  <a href="#architecture">Architecture</a> •
  <a href="#smart-contract">Smart Contract</a> •
  <a href="#dashboard">Dashboard</a> •
  <a href="#security">Security</a> •
  <a href="#roadmap">Roadmap</a>
</p>

---

## What is $CHILLER?

$CHILLER is a **non-custodial yield vault** on Solana. Users deposit SOL into a smart contract and receive $CHILLER tokens representing their share of the vault. AI-powered trading bots generate yield through perpetual futures on [Drift Protocol](https://drift.trade).

### How It Works

```
1. Deposit SOL into the Vault         →  Receive $CHILLER tokens
2. AI bots trade on Drift Protocol    →  Vault NAV grows
3. Burn $CHILLER anytime              →  Withdraw SOL + profits
```

### Key Features

| Feature | Description |
|---------|-------------|
| 🔐 **Non-custodial** | Your funds are in a Solana smart contract, not a wallet we control |
| 🤖 **AI Trading** | Multiple trading strategies running 24/7 on Drift Protocol |
| 📊 **Transparent** | All trades logged on-chain, NAV updated in real-time |
| 🧊 **Just Chill** | No active management needed — deposit and earn |
| 🛡️ **Security First** | Two-step authority transfer, daily drain limits, emergency pause |

---

## Architecture

```
                    ┌──────────────────────┐
                    │   Landing Page       │
                    │   chillercoin.io     │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │   Dashboard          │
                    │   app.chillercoin.io │
                    │                      │
                    │  • Portfolio view     │
                    │  • Deposit / Withdraw │
                    │  • Trade history      │
                    │  • NAV charts         │
                    └──────────┬───────────┘
                               │
          Phantom / Solflare / Backpack
                               │
                    ┌──────────▼───────────┐
                    │  Solana Vault         │
                    │  (Anchor Program)     │
                    │                       │
                    │  • deposit()          │
                    │  • withdraw()         │
                    │  • update_nav()       │
                    │  • log_trade()        │
                    │  • drain_to_trade()   │
                    │  • transfer_authority │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │  AI Trading Engine    │
                    │  (Off-chain)          │
                    │                       │
                    │  Drift Protocol       │
                    │  Perpetual Futures    │
                    └──────────────────────┘
```

---

## Smart Contract

**Program ID:** `7ayYqgiiBtXdk13f9DBFTxJoYKkZyr3AaaLt2f2TPDoH`

Built with [Anchor](https://www.anchor-lang.com/) v0.31.1 on Solana.

### Instructions

| Instruction | Access | Description |
|------------|--------|-------------|
| `create_mint` | Authority | Create the $CHILLER SPL token mint |
| `create_treasury` | Authority | Create the SOL treasury PDA |
| `initialize` | Authority | Initialize vault with fee config |
| `deposit` | Anyone | Deposit SOL → receive $CHILLER tokens |
| `withdraw` | Anyone | Burn $CHILLER → receive SOL back |
| `update_nav` | Authority | Report total assets (on-chain + Drift) |
| `log_trade` | Authority | Log a completed trade on-chain |
| `drain_to_trade` | Authority | Move SOL to Drift (daily limit enforced) |
| `fund_vault` | Authority | Return profits from Drift to vault |
| `set_paused` | Authority | Emergency pause/unpause |
| `transfer_authority` | Authority | Propose new authority (step 1) |
| `accept_authority` | New Auth | Accept authority transfer (step 2) |
| `set_drain_limit` | Authority | Configure daily drain cap |

### Fee Structure

| Fee | Default | Max |
|-----|---------|-----|
| Performance | 20% of profits above HWM | 50% |
| Management | 2% annual | 10% |
| Withdrawal | 0.5% | 5% |

### Building

```bash
cd vault
anchor build
```

### Testing

```bash
anchor test
# 11 tests, all passing
```

---

## Dashboard

A modern, responsive SPA for interacting with the vault.

### Features
- 📊 Real-time NAV chart with price history
- 💰 Deposit SOL / Withdraw $CHILLER with preview
- 📋 Full trade history with P&L breakdown
- 🌙 Dark/Light theme with system preference detection
- 🔗 Multi-wallet: Phantom, Solflare, Backpack
- 📱 Fully responsive (mobile + desktop)

### Running Locally

```bash
cd dashboard
python3 -m http.server 8080
# Open http://localhost:8080
```

---

## Security

### Smart Contract Security
- ✅ **Checked arithmetic** — all operations use `checked_add/sub` (no overflow)
- ✅ **PDA-based accounts** — deterministic, no spoofing
- ✅ **Authority validation** — `has_one` constraint on all admin instructions
- ✅ **Two-step authority transfer** — propose → accept pattern prevents key loss
- ✅ **Daily drain limits** — max 30% of TVL per epoch prevents rug pulls
- ✅ **NAV zero protection** — cannot zero NAV with outstanding token supply
- ✅ **Rent-exempt checks** — withdrawal respects minimum account balance
- ✅ **Emergency pause** — instant halt of deposits/withdrawals
- ✅ **Fee caps** — hardcoded maximums prevent fee manipulation
- ✅ **Epoch withdrawal caps** — prevent bank runs

### Infrastructure Security
- ✅ CSP headers (Content-Security-Policy)
- ✅ Rate limiting (30 req/s)
- ✅ Bot blocking
- ✅ No `innerHTML` with user data (XSS prevention)
- ✅ SSH key-only authentication
- ✅ fail2ban intrusion detection

---

## Project Structure

```
chillercoin/
├── vault/                    # Solana smart contract
│   ├── programs/
│   │   └── chiller-vault/
│   │       └── src/
│   │           └── lib.rs    # Main program (13 instructions)
│   ├── tests/
│   ├── Anchor.toml
│   └── Cargo.toml
│
├── dashboard/                # Web dashboard (SPA)
│   ├── index.html
│   ├── style.css
│   └── app.js
│
├── landing/                  # Marketing landing page
│   ├── index.html
│   ├── style.css
│   └── script.js
│
└── assets/                   # Branding & media
```

---

## Roadmap

- [x] Smart contract — 13 instructions, security hardened
- [x] Dashboard — multi-wallet, themes, responsive
- [x] Landing page — marketing site
- [x] Security audit — 20 findings, 18 resolved
- [ ] Devnet deployment
- [ ] Domain + SSL (chillercoin.io)
- [ ] Telegram bot + broadcast channel
- [ ] Mainnet launch
- [ ] Mobile app (React Native)

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Smart Contract | Rust, Anchor 0.31.1, Solana |
| Dashboard | HTML5, CSS3, Vanilla JS |
| Landing | HTML5, CSS3, Vanilla JS |
| Trading | Python, Drift Protocol SDK |
| Infrastructure | Ubuntu 24.04, Nginx, Certbot |

---

## Disclaimer

> ⚠️ **$CHILLER is experimental software.** Use at your own risk. Past performance does not guarantee future results. This is not financial advice. The smart contract has not been audited by a third party. Only deposit what you can afford to lose.

---

<p align="center">
  <strong>🧊 Just Chill & Earn</strong><br>
  <em>$CHILLER — AI-Powered Yield Vault on Solana</em>
</p>
