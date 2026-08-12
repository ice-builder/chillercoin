-- Chiller Vault — off-chain compliance schema
-- Source: attestation-refund techspec (12.08.2026)
-- Store: Postgres preferred; SQLite OK for paper/dev
-- NAV must NEVER include quarantine / rejected balances

CREATE TABLE IF NOT EXISTS wallets (
  wallet           TEXT PRIMARY KEY,          -- base58 Solana pubkey
  first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_screened_at TIMESTAMPTZ,
  eligibility      TEXT NOT NULL DEFAULT 'unknown'
                   CHECK (eligibility IN ('unknown','allow','deny','review')),
  deny_until       TIMESTAMPTZ,               -- optional cool-off / cache
  notes_internal   TEXT                       -- ops only; never expose to UI
);

CREATE TABLE IF NOT EXISTS kyt_screens (
  id               BIGSERIAL PRIMARY KEY,
  wallet           TEXT NOT NULL REFERENCES wallets(wallet),
  trigger          TEXT NOT NULL
                   CHECK (trigger IN ('connect','deposit','topup','periodic','manual')),
  provider         TEXT NOT NULL,             -- e.g. trm / elliptic / chainalysis / mock
  external_ref     TEXT,                      -- provider case id
  risk_score       NUMERIC,                   -- internal
  decision         TEXT NOT NULL
                   CHECK (decision IN ('allow','deny','review','error','timeout')),
  reason_code      TEXT,                      -- internal taxonomy; not shown in UI
  raw_meta         JSONB,                     -- redacted/provider payload (secure store)
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_kyt_wallet_created ON kyt_screens(wallet, created_at DESC);

CREATE TABLE IF NOT EXISTS deposits (
  id               TEXT PRIMARY KEY,          -- dep_…
  wallet           TEXT NOT NULL REFERENCES wallets(wallet),
  chain            TEXT NOT NULL DEFAULT 'solana',
  deposit_txid     TEXT NOT NULL UNIQUE,      -- inbound transfer signature
  amount_lamports  BIGINT NOT NULL CHECK (amount_lamports > 0),
  state            TEXT NOT NULL
                   CHECK (state IN (
                     'DETECTED','SCREENING','CLEAN_READY','MINTING','MINTED',
                     'REJECTED','QUARANTINED','REFUNDING','REFUNDED',
                     'HOLD_MANUAL','PAUSED','MINT_FAILED'
                   )),
  kyt_screen_id    BIGINT REFERENCES kyt_screens(id),
  nav_at_mint      NUMERIC,                   -- set at mint
  shares_minted    NUMERIC,                   -- 0 if rejected
  mint_txid        TEXT,
  quarantine_txid  TEXT,
  detected_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_deposits_wallet_state ON deposits(wallet, state);
CREATE INDEX IF NOT EXISTS idx_deposits_state_updated ON deposits(state, updated_at);

CREATE TABLE IF NOT EXISTS attestations (
  id               BIGSERIAL PRIMARY KEY,
  deposit_id       TEXT NOT NULL REFERENCES deposits(id),
  nonce            TEXT NOT NULL UNIQUE,
  wallet           TEXT NOT NULL,
  amount_lamports  BIGINT NOT NULL,
  nav              NUMERIC NOT NULL,
  shares           NUMERIC NOT NULL,
  signer_pubkey    TEXT NOT NULL,
  signature        TEXT NOT NULL,
  payload_hash     TEXT NOT NULL,
  exp              TIMESTAMPTZ NOT NULL,
  status           TEXT NOT NULL DEFAULT 'issued'
                   CHECK (status IN ('issued','spent','expired','revoked')),
  spent_at         TIMESTAMPTZ,
  mint_txid        TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_att_deposit ON attestations(deposit_id);

CREATE TABLE IF NOT EXISTS refunds (
  id               BIGSERIAL PRIMARY KEY,
  deposit_id       TEXT NOT NULL UNIQUE REFERENCES deposits(id), -- 1:1
  to_wallet        TEXT NOT NULL,             -- must equal deposit.wallet (sender)
  amount_in        BIGINT NOT NULL,           -- original lamports
  fee_lamports     BIGINT NOT NULL DEFAULT 0,
  amount_out       BIGINT NOT NULL,           -- full return minus fee
  state            TEXT NOT NULL
                   CHECK (state IN ('pending','sent','confirmed','failed','manual')),
  attempt_count    INT NOT NULL DEFAULT 0,
  refund_txid      TEXT,
  last_error       TEXT,                      -- internal
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS circuit_breakers (
  scope            TEXT PRIMARY KEY
                   CHECK (scope IN ('mint','redeem','convert','refund','all')),
  paused           BOOLEAN NOT NULL DEFAULT FALSE,
  reason_internal  TEXT,
  updated_by       TEXT,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ops_holds (
  id               BIGSERIAL PRIMARY KEY,
  deposit_id       TEXT NOT NULL REFERENCES deposits(id),
  reason_code      TEXT NOT NULL,             -- internal
  status           TEXT NOT NULL DEFAULT 'open'
                   CHECK (status IN ('open','resolved','escalated')),
  assignee         TEXT,
  resolution       TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  resolved_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS deposit_events (
  id               BIGSERIAL PRIMARY KEY,
  deposit_id       TEXT NOT NULL REFERENCES deposits(id),
  from_state       TEXT,
  to_state         TEXT NOT NULL,
  actor            TEXT NOT NULL,             -- system | worker | ops | program
  detail           JSONB,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dep_events ON deposit_events(deposit_id, created_at);

-- Seed breakers
INSERT INTO circuit_breakers (scope, paused) VALUES
  ('mint', false),
  ('redeem', false),
  ('convert', false),
  ('refund', false),
  ('all', false)
ON CONFLICT (scope) DO NOTHING;
