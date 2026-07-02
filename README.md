# 🧊 $CHILLER — AI-Powered Yield Vault

> **Nothing promised. Nothing guaranteed. Just chill & see what happens.**

Decentralized vault on Solana where AI trading bots manage pooled capital via Drift Protocol. Users deposit SOL, receive $CHILLER tokens, and bots trade perpetuals to grow the vault's NAV.

---

## Architecture

```
┌──────────────────────────────────────────────────┐
│                    Users                          │
│         Deposit SOL → Get $CHILLER                │
│         Burn $CHILLER → Get SOL back              │
└──────────────┬───────────────────┬────────────────┘
               │                   │
     ┌─────────▼──────┐   ┌──────▼──────────┐
     │  Landing Page   │   │   Dashboard      │
     │  chillercoin.io │   │  app.chiller.io  │
     └────────────────┘   └───────┬──────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │     Solana Smart Contract    │
                    │     (Anchor / Rust)          │
                    │  ┌────────────────────────┐  │
                    │  │ VaultState              │  │
                    │  │ ├ total_assets (SOL)    │  │
                    │  │ ├ total_supply ($CHILL) │  │
                    │  │ ├ high_water_mark       │  │
                    │  │ ├ trades / wins / pnl   │  │
                    │  │ └ fee config            │  │
                    │  └────────────────────────┘  │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │       Vault Bridge           │
                    │    (Python · vault_bridge.py)│
                    │  ├ NAV updates               │
                    │  ├ Trade logging              │
                    │  └ Position sync              │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │     Exchange Adapter          │
                    │  ├ DriftAdapter (on-chain)    │
                    │  ├ BybitAdapter (CEX)          │
                    │  └ PaperAdapter (testing)      │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │      Trading Bots            │
                    │  ├ Soldier (trend-following)  │
                    │  └ Scalper Pro (mean-revert)  │
                    └──────────────────────────────┘
```

## Project Structure

```
Crypto-Code/
├── chiller-vault/           # Solana smart contract (Anchor)
│   ├── programs/            # Rust program source
│   ├── tests/               # TypeScript integration tests
│   ├── vault_bridge.py      # On-chain ↔ exchange bridge
│   ├── exchange_adapter.py  # Pluggable exchange adapters
│   └── deploy-devnet.sh     # One-click devnet deploy
│
├── chiller-dashboard/       # Web dashboard (vanilla JS)
│   ├── index.html           # SPA with 3 pages
│   ├── style.css            # Dark/Light theme system
│   ├── app.js               # Wallet connect, charts, vault UI
│   └── deploy.sh            # One-click VPS deploy
│
├── chiller-landing/         # Marketing landing page
│   ├── index.html
│   └── style.css
│
└── chiller-tg-bot/          # Telegram bot
    ├── bot.py               # 7 commands + trade broadcasts
    ├── .env                 # Credentials (not committed)
    └── requirements.txt
```

## Quick Start

### Dashboard (local)
```bash
cd chiller-dashboard
python3 -m http.server 8080
# → http://localhost:8080
```

### Deploy to VPS
```bash
cd chiller-dashboard
./deploy.sh all              # Deploy everything
./deploy.sh dashboard        # Dashboard only
./deploy.sh landing          # Landing only
```

### Vault Contract
```bash
cd chiller-vault
anchor build                 # Compile
anchor test                  # Run tests (11/11)
./deploy-devnet.sh           # Deploy to Solana devnet
```

### Vault Bridge
```bash
cd chiller-vault
python3 vault_bridge.py --cluster devnet status
python3 vault_bridge.py --cluster devnet daemon --exchange drift
python3 vault_bridge.py --cluster devnet daemon --exchange paper
```

### TG Bot
```bash
cd chiller-tg-bot
# Edit .env with your BOT_TOKEN from @BotFather
pip install -r requirements.txt
python3 bot.py
```

## Features

| Feature | Status |
|---------|--------|
| Solana Vault (Anchor) | ✅ Built, 11/11 tests |
| Dashboard (Dark/Light) | ✅ Deployed |
| Multi-wallet (Phantom/Solflare/Backpack) | ✅ |
| Vault Bridge | ✅ |
| Exchange Adapters (Drift/Bybit/Paper) | ✅ |
| TG Bot (7 commands) | ✅ Code ready |
| VPS Hardened (SSH/UFW/fail2ban) | ✅ |
| Trade Broadcast | ✅ |
| SSL/HTTPS | ⏳ Needs domain |
| Devnet Deploy | ⏳ Needs SOL airdrop |

## Security

- **VPS**: SSH key-only (port 2847), no root login, UFW, fail2ban
- **Nginx**: Rate limiting, bot blocking, version hidden, security headers
- **Vault**: Authority-only operations, pause capability, fee caps
- **Frontend**: No private keys stored, wallet-only auth

## Tech Stack

- **Contract**: Rust / Anchor Framework / Solana
- **Frontend**: Vanilla HTML/CSS/JS (no framework overhead)
- **Bridge**: Python / solders
- **Bot**: python-telegram-bot
- **Server**: Ubuntu 24.04 / Nginx / UFW / fail2ban
- **Exchange**: Drift Protocol (on-chain DEX)

---

*🧊 Just chill.*
