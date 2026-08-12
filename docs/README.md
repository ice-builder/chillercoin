# Docs index

Public compliance and product docs for `$CHILLER` (no secrets, no trading engines).

| File | Description |
|------|-------------|
| [PUBLIC_PRIVATE_BOUNDARY.md](./PUBLIC_PRIVATE_BOUNDARY.md) | What belongs in this repo vs private ops |
| [attestation-refund.md](./attestation-refund.md) | KYT eligibility, mint attestation, full refund |
| [mint-with-attestation.md](./mint-with-attestation.md) | On-chain attested mint ix |
| [compliance-schema.sql](./compliance-schema.sql) | Off-chain tables for deposits / refunds |
| [db-and-sequences.md](./db-and-sequences.md) | Happy / reject sequences |
| [key-roles.md](./key-roles.md) | Split authority roles before value-at-risk |
| [ops-runbook.md](./ops-runbook.md) | Deny, refund, pause — no live deposits yet |

Push gate: `node scripts/public-push-audit.mjs` → `scripts/reports/LATEST.md`.

