# mint_with_attestation — on-chain plan (P2→P3)

Status: **ix shipped + open `deposit` deprecated**. Bankrun covers attested mint + open-deposit reject.

## Goal

Mint $CHILLER only when a valid, single-use, unexpired attestation is presented.
Inbound dirty SOL without attestation never receives shares (handled off-chain: quarantine → refund).

## Instruction (only mint path)

`mint_with_attestation(amount, min_tokens_out, { nonce, exp, attested_wallet })`

- `attestation_authority` signer must equal `vault.authority`
- `attested_wallet` must equal depositor
- `exp` must be >= `Clock`
- `AttestationReceipt` PDA `["attestation", nonce]` — init once (replay-safe)

## Legacy `deposit`

Returns `OpenDepositDeprecated`. Old clients fail closed; investors must use attested mint.
Plain SOL can still hit the treasury PDA without mint; refund rail covers that.

## Acceptance

- [x] Bankrun: happy mint
- [x] Replay / mismatch / expired / unauthorized
- [x] Off-chain paper refund (shares=0)
- [x] Default UI to attested path; open `deposit` deprecated
- [ ] Optional: ed25519 detached sig (beyond authority co-sign)
