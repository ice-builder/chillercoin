use anchor_lang::prelude::*;

/// Vault state — single account holding all configuration and metrics.
#[account]
#[derive(InitSpace)]
pub struct VaultState {
    /// Operator authority (multisig recommended)
    pub authority: Pubkey,
    /// USDT token mint address
    pub usdt_mint: Pubkey,
    /// $CHILLER token mint address (PDA-controlled)
    pub chiller_mint: Pubkey,
    /// Vault's USDT token account (holds on-chain portion)
    pub vault_usdt_account: Pubkey,
    /// Team wallet for fee collection
    pub team_wallet: Pubkey,

    // ─── Financial State ───────────────────
    /// Total assets under management (on-chain + off-chain, in USDT lamports)
    pub total_assets: u64,
    /// Total $CHILLER supply (minted - burned)
    pub total_supply: u64,
    /// High-water mark for performance fee (prevents double-charging)
    pub high_water_mark: u64,

    // ─── Trading Metrics ───────────────────
    /// Total number of trades logged
    pub total_trades: u64,
    /// Total winning trades
    pub total_wins: u64,
    /// Cumulative PnL in basis points (can be negative)
    pub cumulative_pnl_bps: i64,

    // ─── Fee Configuration ─────────────────
    /// Performance fee in basis points (2000 = 20%)
    pub performance_fee_bps: u16,
    /// Management fee in basis points per year (200 = 2%)
    pub management_fee_bps: u16,
    /// Withdrawal fee in basis points (50 = 0.5%)
    pub withdrawal_fee_bps: u16,

    // ─── Security ──────────────────────────
    /// Minimum deposit in USDT lamports ($100 = 100_000_000 for 6-decimal USDT)
    pub min_deposit: u64,
    /// Maximum withdrawal per epoch (daily cap) — 0 = unlimited
    pub max_withdrawal_per_epoch: u64,
    /// Current epoch withdrawals
    pub epoch_withdrawals: u64,
    /// Current epoch number (resets daily)
    pub current_epoch: u64,
    /// Timelock duration for NAV updates (seconds) — 0 = immediate
    pub nav_timelock_seconds: i64,
    /// Pending NAV (set during timelock)
    pub pending_nav: u64,
    /// Pending NAV timestamp
    pub pending_nav_timestamp: i64,

    // ─── Operational ───────────────────────
    /// Last NAV update timestamp
    pub last_nav_update: i64,
    /// Emergency pause flag
    pub is_paused: bool,
    /// PDA bump seed
    pub bump: u8,
    /// Chiller mint bump
    pub chiller_mint_bump: u8,

    // ─── Reserved for future upgrades ──────
    pub _reserved: [u8; 64],
}

impl VaultState {
    /// Calculate NAV per token (price of 1 $CHILLER in USDT lamports)
    /// Returns in 1e6 precision (same as USDT decimals)
    pub fn nav_per_token(&self) -> u64 {
        if self.total_supply == 0 {
            return 1_000_000; // $1.00 initial price
        }
        // total_assets and total_supply are both in their native units
        // We want: (total_assets * 1e6) / total_supply for precision
        // But total_assets is already in USDT lamports (1e6)
        // So: total_assets / total_supply gives us USDT per token
        // To avoid rounding down to 0, we multiply first
        ((self.total_assets as u128 * 1_000_000) / self.total_supply as u128) as u64
    }

    /// Calculate how many $CHILLER tokens to mint for a given USDT deposit
    pub fn tokens_for_deposit(&self, usdt_amount: u64) -> u64 {
        if self.total_supply == 0 || self.total_assets == 0 {
            return usdt_amount; // 1:1 for first deposit
        }
        // tokens = usdt_amount * total_supply / total_assets
        ((usdt_amount as u128 * self.total_supply as u128) / self.total_assets as u128) as u64
    }

    /// Calculate how much USDT to return for burning $CHILLER tokens
    pub fn usdt_for_withdrawal(&self, chiller_amount: u64) -> u64 {
        if self.total_supply == 0 {
            return 0;
        }
        // usdt = chiller_amount * total_assets / total_supply
        ((chiller_amount as u128 * self.total_assets as u128) / self.total_supply as u128) as u64
    }
}
