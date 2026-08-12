# Security Policy

## Reporting

Report suspected vulnerabilities in the public product (dashboard / vault program) privately to the repository maintainers. Do not open public issues with exploit details.

## Scope

In scope: on-chain vault program behavior, dashboard XSS/auth UX, credential or infrastructure leaks in this repository.

Out of scope: social engineering, third-party wallet malware, physical attacks, and private trading systems (not published here).

## Trust model (summary)

- Mint and withdraw require vault **authority co-sign** plus single-use attestation receipts.
- NAV split between on-chain treasury and off-chain sleeve is **authority-reported** within program caps; it is not an independent oracle.
- Emergency pause blocks mint, withdraw, NAV updates, drain, and fund until cleared or auto-expired after 24 hours.
- Do not treat this repository’s push gate alone as a complete release boundary.
