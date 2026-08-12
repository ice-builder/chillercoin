use anchor_lang::prelude::*;

// ─── On-chain trade log (permanent record) ──────────
#[event]
pub struct TradeLogged {
    /// Trading pair (e.g., "ETHUSDT")
    #[index]
    pub pair: String,
    /// Trade direction ("SHORT" or "LONG")
    pub side: String,
    /// Entry price (×100, e.g., $2541.20 → 254120)
    pub entry_price: u64,
    /// Exit price
    pub exit_price: u64,
    /// PnL in basis points (e.g., +124 = +1.24%)
    pub pnl_bps: i32,
    /// PnL in USDT lamports (e.g., 31_200_000 = $31.20)
    pub pnl_usdt: i64,
    /// Trade duration in seconds
    pub duration_secs: u64,
    /// Vault NAV after this trade
    pub vault_nav_after: u64,
    /// Vault total assets after
    pub vault_total_assets: u64,
    /// Unix timestamp
    pub timestamp: i64,
}

// ─── NAV update event ───────────────────────────────
#[event]
pub struct NAVUpdated {
    /// Previous total assets
    pub old_total_assets: u64,
    /// New total assets
    pub new_total_assets: u64,
    /// NAV per token before
    pub old_nav: u64,
    /// NAV per token after
    pub new_nav: u64,
    /// Performance fee collected (if any)
    pub perf_fee_collected: u64,
    /// Total supply at time of update
    pub total_supply: u64,
    /// Unix timestamp
    pub timestamp: i64,
}

// ─── Deposit event ──────────────────────────────────
#[event]
pub struct UserDeposited {
    /// Depositor's wallet
    #[index]
    pub user: Pubkey,
    /// Amount of USDT deposited
    pub usdt_amount: u64,
    /// $CHILLER tokens minted
    pub chiller_minted: u64,
    /// NAV at time of deposit
    pub nav_at_deposit: u64,
    /// Vault total assets after deposit
    pub vault_total_assets: u64,
    /// Unix timestamp
    pub timestamp: i64,
}

// ─── Withdrawal event ───────────────────────────────
#[event]
pub struct UserWithdrew {
    /// Withdrawer's wallet
    #[index]
    pub user: Pubkey,
    /// $CHILLER tokens burned
    pub chiller_burned: u64,
    /// Gross USDT (before fee)
    pub usdt_gross: u64,
    /// Withdrawal fee charged
    pub withdrawal_fee: u64,
    /// Net USDT returned to user
    pub usdt_returned: u64,
    /// NAV at time of withdrawal
    pub nav_at_withdrawal: u64,
    /// Unix timestamp
    pub timestamp: i64,
}

// ─── Vault paused/unpaused ──────────────────────────
#[event]
pub struct VaultPaused {
    pub paused: bool,
    pub authority: Pubkey,
    pub timestamp: i64,
}
