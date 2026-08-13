<p align="center">
  <img src="assets/banner.png" alt="$CHILLER Banner" width="100%">
</p>

<p align="center">
  <strong>$CHILLER — Solana vault. Just chill.</strong><br>
  <em>Deposit SOL → get $CHILLER by NAV → bots trade a sleeve → no yield promises.</em>
</p>

<p align="center">
  <a href="#architecture">Architecture</a> •
  <a href="#smart-contract">Smart Contract</a> •
  <a href="#dashboard">Dashboard</a> •
  <a href="#security">Security</a> •
  <a href="#docs">Docs</a> •
  <a href="#roadmap">Roadmap</a>
</p>

---

## What is $CHILLER?

$CHILLER is a **share token for a Solana vault**. You deposit SOL into the on-chain program and receive $CHILLER at the current NAV. Off-chain trading runs on a **segregated CEX sleeve** (Bybit sub-account via the project entity). NAV can go up or down. **We do not promise returns.**

### How It Works

```
1. Connect wallet (Phantom / Solflare / Backpack)
2. Eligibility screen (KYT) → deposit SOL → mint $CHILLER by NAV
3. Trading sleeve updates vault NAV over time (no guarantees)
4. Burn $CHILLER → redeem SOL by NAV (− fee), from liquidity reserve when available
```

Dirty / ineligible deposits are **not minted** and are **fully refunded** (minus network fee) via a quarantine rail. See `docs/`.

## Official channels

Copies of this repository are **not** the official vault. Before any transfer, match the program id in your wallet to this table.

| Channel | Canonical |
|---------|-----------|
| Site | [chillercoin.io](https://chillercoin.io) |
| App | [app.chillercoin.io](https://app.chillercoin.io) |
| GitHub | [ice-builder/chillercoin](https://github.com/ice-builder/chillercoin) |
| Program ID | `7ayYqgiiBtXdk13f9DBFTxJoYKkZyr3AaaLt2f2TPDoH` |

## Key Features

| Feature | Description |
|---------|-------------|
| **On-chain vault** | SOL + $CHILLER mint/burn in an Anchor program |
| **No yield promise** | NAV is mark-to-market; chill is not a return promise |
| **CEX sleeve** | Trading capital on a dedicated Bybit sub-account (not a DEX mirror story) |
| **Reserve + sleeve** | Liquidity buffer for redemptions; quarantine ≠ reserve |
| **Transparent surface** | This public repo: program, site, dashboard, compliance docs |
| **Security controls** | Pause, drain limits, two-step authority transfer |

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
                    │  portfolio / NAV     │
                    │  deposit / withdraw  │
                    └──────────┬───────────┘
                               │
          Phantom / Solflare / Backpack
                               │
                    ┌──────────▼───────────┐
                    │  Solana Vault         │
                    │  (Anchor Program)     │
                    │  deposit / withdraw  │
                    │  update_nav / pause   │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │  Trading sleeve       │
                    │  (private ops)        │
                    │  Bybit sub-account    │
                    │  — not in this repo   │
                    └──────────────────────┘
```

**Public vs private:** product + site + vault program are here. Trading bots, bridges, API keys, Telegram bots — **not published**. See `docs/PUBLIC_PRIVATE_BOUNDARY.md`.

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
| `mint_with_attestation` | User + authority co-sign | Investor mint after KYT ticket |
| `withdraw_with_attestation` | User + authority co-sign | Burn $CHILLER → SOL after screening |
| `deposit` / `withdraw` | Disabled | Open path deprecated on-chain |
| `update_nav` | Authority | Report total assets (on-chain + sleeve equity) |
| `log_trade` | Authority | Log a completed trade on-chain |
| `drain_to_trade` | Authority | Move SOL toward trading rails (daily limit) |
| `fund_vault` | Authority | Return capital to vault |
| `set_paused` | Authority | Emergency pause/unpause |
| `transfer_authority` | Authority | Propose new authority (step 1) |
| `accept_authority` | New Auth | Accept authority transfer (step 2) |
| `set_drain_limit` | Authority | Configure daily drain cap |

> Investor mint path is **`mint_with_attestation`** (open `deposit` is deprecated on-chain). Eligibility / refund rails are documented under `docs/`.

### Fee Structure

| Fee | Default | Max |
|-----|---------|-----|
| Performance | 20% of profits above HWM | 50% |
| Management | 2% annual | 10% |
| Withdrawal | 0.5% | 5% |

### Building

```bash
cd chiller-vault
anchor build
```

### Testing

```bash
anchor test
```

---

## Dashboard (`chiller-dashboard`)

SPA for vault interaction (demo/live wiring depends on deployment).

- NAV / portfolio view
- Deposit SOL / withdraw $CHILLER (attested mint UX; open deposit disabled)
- Trades tab reads Solana `TradeLogged` events (public tape; **no yield promises**)
- Same tape as `site/trades.html` for [chillercoin.io/trades.html](https://chillercoin.io/trades.html)
- Network badge defaults to **DEMO** (deposits stay paper; trades are on-chain)
- Multi-wallet: Phantom, Solflare, Backpack
- Responsive layout

```bash
cd chiller-dashboard
python3 -m http.server 8080
```

---

## Security

### Smart Contract
- Checked arithmetic, PDA accounts, authority `has_one`
- Two-step authority transfer
- Daily drain limits, epoch withdrawal caps
- Emergency pause, fee caps, NAV zero protection

### Public push gate
Before any push to this repo:

```bash
node scripts/public-push-audit.mjs
# report → scripts/reports/LATEST.md
```

Blocks trading code, secrets, Telegram bots, embedded PATs. You decide after reading the report.

---

## Docs

| Doc | Topic |
|-----|--------|
| [`docs/PUBLIC_PRIVATE_BOUNDARY.md`](docs/PUBLIC_PRIVATE_BOUNDARY.md) | What is public vs private |
| [`docs/attestation-refund.md`](docs/attestation-refund.md) | KYT, mint attestation, full refund SM |
| [`docs/compliance-schema.sql`](docs/compliance-schema.sql) | Off-chain deposit/refund tables |
| [`docs/db-and-sequences.md`](docs/db-and-sequences.md) | Sequences + inventory |

---

## Project Structure

```
chillercoin/
├── chiller-vault/                 # Solana smart contract
├── chiller-dashboard/             # Web dashboard (SPA)
├── docs/                  # Compliance & boundary docs
├── scripts/               # Public push audit gate
└── assets/
```

---

## Roadmap

- [x] Smart contract — hardened instructions
- [x] Dashboard + landing
- [x] Public/private boundary + push gate
- [x] Compliance specs (attestation, refund, schema)
- [ ] Align site copy + eligibility UX
- [ ] Devnet / mainnet with attested mint
- [ ] Holder cabinet after successful deposit
- [ ] Transparency lite (NAV / reserve split, no APY claims)

Telegram bots and trading engines stay **private** — not part of this repo.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Smart Contract | Rust, Anchor 0.31.1, Solana |
| Dashboard / Landing | HTML, CSS, Vanilla JS |
| Trading sleeve | Private ops (Bybit sub-account) — not in this repo |
| Infrastructure | Ubuntu, Nginx, TLS |

---

## Disclaimer

> **$CHILLER is experimental software.** NAV can decrease. No returns are promised or guaranteed. This is not financial advice. Prefer third-party audit before sizing up. Only deposit what you can afford to lose. Ineligible wallets may be refused; rejected deposits are refunded under the published policy.

---

<p align="center">
  <strong>Just chill.</strong><br>
  <em>$CHILLER — Solana vault. No yield promises.</em>
</p>
