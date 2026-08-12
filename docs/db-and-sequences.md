# DB + sequences (public summary)

Built on top of the existing Anchor vault (deposit/withdraw/pause). Compliance rails (KYT, attestation, quarantine, refund) are specified here and implemented off-chain first; program mint-gating follows.

## Tables (see `compliance-schema.sql`)

| Table | Role |
|-------|------|
| `wallets` | Eligibility cache after connect |
| `kyt_screens` | Each screen (connect / deposit / top-up) |
| `deposits` | One inbound txid state machine |
| `attestations` | One-time mint tickets |
| `refunds` | 1:1 full refund − fee |
| `circuit_breakers` | mint / redeem / convert / refund / all |
| `ops_holds` | Manual review |
| `deposit_events` | Audit log of transitions |

## Sequence A — happy path

```
Connect → KYT allow → Deposit SOL → DETECTED → SCREENING allow
→ CLEAN_READY → issue attestation → mint → MINTED → cabinet
```

## Sequence B — ineligible → full refund

```
Connect → KYT deny → modal (deposit UI hidden)
Optional: user still sends SOL → DETECTED → deny → REJECTED
→ QUARANTINED → REFUNDING → REFUNDED + refund_txid (shares = 0)
```

## Sequence C — timeout / breaker

```
SCREENING timeout → fail-closed REJECTED (+ refund) 
or HOLD_MANUAL if provider down + policy
or PAUSED if mint breaker on (no new attestation)
```

## Sequence D — idempotent refund

```
Worker(deposit_id):
  if REFUNDED → no-op
  if in-flight → poll confirm only
  else send once (idempotency key = deposit_id)
  N failures → HOLD_MANUAL
```

## What stays private

Trading bots, vault↔bot bridge, exchange executors, API keys, attestation **signer** key, quarantine keys, Telegram bots — see [PUBLIC_PRIVATE_BOUNDARY.md](./PUBLIC_PRIVATE_BOUNDARY.md).
