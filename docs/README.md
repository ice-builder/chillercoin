# Docs index

Public compliance and product docs for `$CHILLER` (no secrets, no trading engines).

| File | Description |
|------|-------------|
| [PUBLIC_PRIVATE_BOUNDARY.md](./PUBLIC_PRIVATE_BOUNDARY.md) | What belongs in this repo vs private ops |
| [attestation-refund.md](./attestation-refund.md) | KYT eligibility, mint attestation, full refund |
| [compliance-schema.sql](./compliance-schema.sql) | Off-chain tables for deposits / refunds |
| [db-and-sequences.md](./db-and-sequences.md) | Happy / reject sequences |

Push gate: `node scripts/public-push-audit.mjs` → `scripts/reports/LATEST.md`.
| [mint-with-attestation.md](./mint-with-attestation.md) | On-chain attested mint ix |

