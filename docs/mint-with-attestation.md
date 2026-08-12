# mint_with_attestation — on-chain plan (P2→P3)

Status: **ix shipped + open `deposit` deprecated**. Bankrun covers attested mint + open-deposit reject.

## Goal

Mint $CHILLER only when a valid, single-use, unexpired attestation is presented.
Inbound dirty SOL without attestation never receives shares (handled off-chain: quarantine → refund).

Investor flow: **transfer SOL → treasury → KYT → authority co-signs mint**. Mint claims **unallocated** treasury (`lamports − rent − total_assets`); it does **not** pull a second transfer from the user.

## Instruction (only mint path)

`mint_with_attestation(amount, min_tokens_out, AttestationArgs { nonce, exp, attested_wallet, amount_lamports, payload_hash })`

- `attestation_authority` signer must equal `vault.authority`
- `attested_wallet` must equal depositor
- `amount_lamports` must equal `amount`
- `payload_hash` = `sha256(nonce ‖ wallet ‖ amount_le ‖ exp_le)`
- `exp` must be >= `Clock`
- `AttestationReceipt` PDA `["attestation", nonce]` — init once (replay-safe)

## Withdraw

Open `withdraw` returns `OpenWithdrawDeprecated`.
Use `withdraw_with_attestation` (authority co-sign + single-use `wd-attestation` receipt).

## Legacy `deposit`

Returns `OpenDepositDeprecated`. Old clients fail closed; investors must use attested mint.
Plain SOL can still hit the treasury PDA without mint; refund rail covers that.

## Trust note

NAV / share quantities are computed from live vault state at mint time. Off-chain tickets may record extra audit fields (`deposit_txid`, paper nav/shares); those are not independently verified by the program.

## Acceptance

- [x] Bankrun: happy mint (prefunded treasury)
- [x] Replay / mismatch / expired / unauthorized / amount / hash
- [x] Open deposit + open withdraw rejected
- [x] Off-chain paper refund (shares=0)
- [x] Default UI to attested path
- [ ] Optional: ed25519 detached sig (beyond authority co-sign)
- [ ] Separate attestation issuer key vs treasury authority (pre-mainnet)
