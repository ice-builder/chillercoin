# Key roles (pre-mainnet)

Do not use one key for everything. Compromise of a single `vault.authority` today would cover mint co-sign, NAV, drain, pause, and (separately) program upgrade.

| Role | Signs | May hold funds? | Notes |
|------|--------|-----------------|--------|
| **Attestation issuer** | `mint_with_attestation` / `withdraw_with_attestation` co-sign | No | After KYT allow only |
| **NAV reporter** | `update_nav` | No | Authority-reported sleeve mark |
| **Treasury operator** | `drain_to_trade`, `fund_vault` | Moves treasury | Multisig / timelock when live |
| **Pause guardian** | `set_paused` | No | May pause; should not drain |
| **Upgrade authority** | BPF loader upgrade | No | Multisig; not the same as vault authority |
| **Trade logger** | `log_trade` only | No | Dedicated pubkey. Cannot mint, pause, drain, NAV, or upgrade. |

Until those are separate accounts on-chain, treat current `vault.authority` as a **dev/demo key**. Before value-at-risk: split pubkeys, hardware/multisig for treasury + upgrade, written rotation/revocation.

Paper/localnet may still init all roles to one keypair for tests.
