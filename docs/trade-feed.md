# Trade feed (on-chain tape)

Closed sleeve fills are published with vault `log_trade`. The public UI never talks to the trader host.

```
sleeve close
    │
    ▼
private worker (LOG_ENABLED=1)
    │
    ▼
on-chain log_trade  →  TradeLogged event + Solana signature
    │
    ▼
browser reads Solana RPC
    ├─ chillercoin.io/trades.html     (marketing-site tape)
    └─ app.chillercoin.io/#trades     (app Trades tab)
```

Both pages use the same decoder (`js/onchain-trades.js`): `getSignaturesForAddress` on the dedicated logger, then parse `Program data:` with discriminator `event:TradeLogged`.

JSON `data/onchain-trades.json` is only a fallback if RPC is unreachable. Rows without a valid signature show "—", never a fake explorer link.

Local preview of the app uses public devnet RPC. Loopback validator is opt-in: `?cluster=localnet` or `?rpc=http://127.0.0.1:8899`.

Do not auto-generate the vault authority on the trader host. Logger key only.
