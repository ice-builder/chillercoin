# mint_with_attestation — on-chain plan (P2)

Status: **ix shipped (authority co-sign + nonce PDA)**. Legacy `deposit` remains; attested path is the compliance rail. Bankrun: `tests/attestation_bankrun.ts` (5/5).

## Goal

Mint $CHILLER only when a valid, single-use, unexpired attestation is presented.
Inbound dirty SOL without attestation never receives shares (handled off-chain: quarantine → refund).

## New instruction (preferred)

Add next to `deposit` in `programs/chiller-vault/src/lib.rs`:

`mint_with_attestation(ctx, amount, min_tokens_out, attestation)`

Do **not** only wrap existing `deposit` — plain SOL can still hit the treasury PDA without mint; refund rail covers that.

## Shipped model (v1)

`mint_with_attestation(amount, min_tokens_out, { nonce, exp, attested_wallet })`

- `attestation_authority` signer must equal `vault.authority`
- `attested_wallet` must equal depositor
- `exp` must be >= `Clock`
- `AttestationReceipt` PDA `["attestation", nonce]` — init once (replay-safe)
- Legacy `deposit` still exists

## Acceptance

- [x] Bankrun: happy mint
- [x] Replay / mismatch / expired / unauthorized
- [x] Off-chain paper refund (shares=0)
- [ ] Default UI to attested path; deprecate open `deposit`
- [ ] Optional: ed25519 detached sig (beyond authority co-sign)
