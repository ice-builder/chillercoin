# Chiller Coin — Public / Private boundary

Date: 2026-08-12  
Principle: **product and site are public; trading systems and secrets stay private.**

Public repository: `github.com/ice-builder/chillercoin`  
Private work stays in private repositories and ops hosts — never mirrored into this tree.

---

## PUBLIC

### Site and product
| Artifact | Why public |
|----------|------------|
| Dashboard (`chiller-dashboard/`) | Holder UX: connect, deposit/withdraw UI, NAV view |
| Brand assets | Brand trust |
| README / LICENSE / docs | Trust story |
| Program id + explorer links | On-chain verification |

### On-chain vault
| Artifact | Why public |
|----------|------------|
| `chiller-vault/programs/.../lib.rs` | Investors can review mint/burn/pause/limits |
| Anchor/Cargo/package manifests | Reproducible build |
| Tests (no secrets) | Behavior evidence |
| Devnet deploy helper (no keys) | Deployment transparency |
| Compliance **specs without keys** | Process maturity |

### Transparency without trading code
- Public NAV snapshot / attestation numbers (not keys)
- On-chain `log_trade` events when published
- High-level description: CEX trading sleeve via an entity; KYT on deposit
- **Not** strategies, signals, or exchange API integrations

---

## PRIVATE

### Trading and ops
| Layer | Why private |
|-------|-------------|
| Strategy / signal / execution services | Alpha + infrastructure |
| Private deploy tooling and model artifacts | Engine / deploy |
| Bridge / executor / exchange adapters | Execution |
| Exchange API keys and sub-account ops | Funds access |
| Predictors / retrain pipelines | Edge |
| Ops bots and bot tokens | Operations — not trust surface |

### Compliance ops
| Item | Public? |
|------|---------|
| KYT + attestation + refund state-machine docs | ✅ docs |
| Provider API keys, webhook secrets | ❌ |
| Quarantine / attestation signer keys | ❌ |
| Raw vendor payloads with PII | ❌ |
| Ops consoles with manual overrides | ❌ (separate private repo) |

### Secrets
| Item | |
|------|--|
| `.env`, user DBs, internal trade logs | ❌ |
| SSH keys, process-manager dumps with tokens | ❌ |
| Git remotes with embedded credentials | ❌ — rotate if ever exposed |

---

## Grey zone

| Topic | Decision |
|-------|----------|
| Messaging / shop bots | Private — not this trust repo |
| On-chain attestation verify | Public |
| Attestation issuer service | Private |
| Deposit watcher / refund worker | Private |
| KYT client | Private (or public stub + private vendor client) |

---

## Export rule

Only allowlisted product paths may leave private trees.  
Push gate must block credentials **and** infrastructure topology (hosts, SSH tuples, remote paths).  
Publishing vault/compliance source is intentional for investor review; trading engines are never exported.
