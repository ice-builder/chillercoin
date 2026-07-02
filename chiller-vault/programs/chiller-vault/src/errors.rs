use anchor_lang::prelude::*;

#[error_code]
pub enum VaultError {
    #[msg("Vault is currently paused")]
    VaultPaused,

    #[msg("Deposit amount below minimum ($100)")]
    DepositBelowMinimum,

    #[msg("Insufficient $CHILLER balance")]
    InsufficientBalance,

    #[msg("Insufficient vault USDT for withdrawal")]
    InsufficientVaultBalance,

    #[msg("Withdrawal exceeds daily epoch cap")]
    EpochCapExceeded,

    #[msg("NAV update is still in timelock period")]
    NAVTimelocked,

    #[msg("Unauthorized: only operator can perform this action")]
    Unauthorized,

    #[msg("Invalid fee configuration (must be <= 10000 bps)")]
    InvalidFeeConfig,

    #[msg("Math overflow in calculation")]
    MathOverflow,

    #[msg("Cannot withdraw: would leave vault with 0 assets")]
    CannotDrainVault,

    #[msg("Invalid amount: must be greater than 0")]
    ZeroAmount,

    #[msg("Trade PnL string too long (max 16 chars)")]
    PairTooLong,
}
