# Mint attestation + refund (public summary)

**Invariant:** $CHILLER shares are minted only after a **clean** eligibility screen and a **single-use attestation**. Ineligible / high-risk deposits are **not minted**, stay out of NAV, never go to the trading sleeve, and receive a **full refund** (minus network fee).

## Investor UX

1. Connect Phantom / Solflare / Backpack  
2. Short “Checking eligibility…”  
3. **Allow** → deposit UI  
4. **Deny** → modal (neutral wording, no “dirty” / no risk scores):  
   *Service unavailable for this wallet under our compliance policy.*  
5. After a successful deposit → holder cabinet  

If SOL is sent despite a deny (or fails post-tx screen): **no mint → quarantine → refund** with on-screen status + `refund_txid`.

## Deposit states

`DETECTED → SCREENING → CLEAN_READY → MINTING → MINTED`  
or `REJECTED → QUARANTINED → REFUNDING → REFUNDED`  
or `HOLD_MANUAL` / `PAUSED` (ops / circuit breaker)

## Attestation rules

- Binds `wallet`, `amount`, `deposit_txid`, `nav`, `shares`, `nonce`, `exp`  
- TTL 15–30 minutes, single-use (`spent` after mint)  
- Fail-closed: missing / bad / expired attestation → no mint  
- Each top-up is a **new** deposit (re-screen)

## Refund policy

- Full inbound amount minus network fee only  
- Idempotent worker keyed by `deposit_id` (no double pay)  
- Failed refund → `HOLD_MANUAL` (never into sleeve)

## Quarantine ≠ liquidity reserve

| Rail | Role | In NAV? | To trading sleeve? |
|------|------|---------|--------------------|
| Liquidity reserve | Redemptions | Yes | No (except SOP) |
| Trading sleeve | Strategy on Bybit sub-account | Via equity mark | Yes (clean only) |
| Quarantine | Reject / hold / refund | **No** | **Never** |

## Circuit breakers

Independent pauses: `mint` | `redeem` | `convert` | `refund` | `all`.

## Acceptance (short)

- Dirty direct send → `REFUNDED`, shares = 0  
- Mint without attestation → reject  
- Refund worker retry → no second payout  
- Quarantine excluded from public NAV  
- UI shows `refund_txid` when refunded  

Off-chain schema: [`compliance-schema.sql`](./compliance-schema.sql).  
Sequences: [`db-and-sequences.md`](./db-and-sequences.md).
