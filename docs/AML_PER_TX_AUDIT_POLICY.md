# AML / audit policy (agreed)

**Date:** 12.08.2026

## Rules

1. **Connect** — wallet KYT once for UX (show/hide deposit). Neutral deny modal only on hard block.
2. **Every deposit / top-up** — silent AML again (wallet + inbound tx / source). No extra “checking” toast; stay on “Processing…”.
3. **Every withdraw** — silent AML on destination wallet **before** payout. Same: no extra notifications.
4. **On deny at tx time** — no mint / no payout; deposit path → quarantine → full refund; withdraw → blocked, funds stay in vault shares. User sees only status / refund_txid if relevant — not risk scores.
5. **Incidents** — every deny / review / timeout / breaker hit written to `incidents` (ops only).
6. **Full user audit** — log connect, disconnect, eligibility results (decision only in UI cache; reason_code internal), deposits, withdraws, attestation issue/spend, refunds, support contacts. Wallet binding is first-class.

Connect-clean then later dirty inflow is covered by **per-tx screening**, not connect TTL alone.

## UX

- Investor never sees risk scores, provider names, or reason codes.
- Timeline / toasts stay generic: “Processing…”, “Transfer not accepted — funds returned”, “Withdrawal unavailable”.
- Ops sees `incidents` + `kyt_screens.reason_code` + full `user_audit_events`.

## Audit minimum (must log)

| action | when |
|--------|------|
| `wallet_connect` / `wallet_disconnect` | bind / unbind |
| `kyt_screen` | every connect / deposit / tx_source / withdraw screen |
| `deposit_submit` → state transitions → `deposit_minted` / `deposit_refunded` | every inbound |
| `withdraw_submit` → `withdraw_paid` / `withdraw_rejected` | every outbound |
| `attestation_issued` / spent | mint gate |
| support / ops overrides | when added |

Silent ≠ unlogged. Silent means **no extra user notifications**; DB always gets the row.
