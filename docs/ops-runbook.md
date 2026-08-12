# Ops runbook — deny, refund, pause

No live deposits yet. Use this when the vault is funded.

## Deposit denied (KYT / policy)

1. Do **not** mint. Open `deposit` is already disabled on-chain.
2. Inbound SOL stays in treasury as **unallocated** until refund (not in `total_assets`).
3. Worker: `REJECTED → QUARANTINED → REFUNDING → REFUNDED`.
4. Refund to the **same sender**, full amount minus network fee only.
5. User UI: status + `refund_txid`. No risk scores, no “dirty” wording.

If refund fails: `HOLD_MANUAL`. Do not drain that SOL to the trading sleeve.

## Withdraw denied

1. Do not co-sign `withdraw_with_attestation`.
2. Open `withdraw` already fails on-chain.
3. Shares stay with the holder. Neutral copy: “Unavailable right now.”

## Emergency pause

`set_paused(true)` blocks mint, withdraw, NAV updates, drain, and fund.

- After 24h the pause **expires** (cleared on the next checked instruction). Re-pause requires a new explicit pause after expiry/unpause — no timestamp refresh loop.
- Unpause: `set_paused(false)` when the incident is over.
- Pause guardian should not be the same key as treasury operator.

## Who signs mint

Only the attestation issuer, after two allows (wallet + tx source) and a single-use ticket (`nonce` + amount + wallet + exp + payload hash). Never regenerate nonce at co-sign time.
