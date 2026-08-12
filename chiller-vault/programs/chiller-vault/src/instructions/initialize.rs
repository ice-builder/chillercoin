use anchor_lang::prelude::*;
use anchor_spl::token::{self, Mint, Token, TokenAccount};

use crate::state::VaultState;

/// Initialize the Chiller Vault — called once at deployment.
pub fn handler(
    ctx: Context<Initialize>,
    performance_fee_bps: u16,
    management_fee_bps: u16,
    withdrawal_fee_bps: u16,
    min_deposit: u64,
    max_withdrawal_per_epoch: u64,
    nav_timelock_seconds: i64,
) -> Result<()> {
    require!(performance_fee_bps <= 5000, crate::errors::VaultError::InvalidFeeConfig); // max 50%
    require!(management_fee_bps <= 1000, crate::errors::VaultError::InvalidFeeConfig); // max 10%
    require!(withdrawal_fee_bps <= 500, crate::errors::VaultError::InvalidFeeConfig);   // max 5%

    let vault = &mut ctx.accounts.vault;
    vault.authority = ctx.accounts.authority.key();
    vault.usdt_mint = ctx.accounts.usdt_mint.key();
    vault.chiller_mint = ctx.accounts.chiller_mint.key();
    vault.vault_usdt_account = ctx.accounts.vault_usdt_account.key();
    vault.team_wallet = ctx.accounts.team_wallet.key();

    vault.total_assets = 0;
    vault.total_supply = 0;
    vault.high_water_mark = 0;

    vault.total_trades = 0;
    vault.total_wins = 0;
    vault.cumulative_pnl_bps = 0;

    vault.performance_fee_bps = performance_fee_bps;
    vault.management_fee_bps = management_fee_bps;
    vault.withdrawal_fee_bps = withdrawal_fee_bps;

    vault.min_deposit = min_deposit;
    vault.max_withdrawal_per_epoch = max_withdrawal_per_epoch;
    vault.epoch_withdrawals = 0;
    vault.current_epoch = 0;
    vault.nav_timelock_seconds = nav_timelock_seconds;
    vault.pending_nav = 0;
    vault.pending_nav_timestamp = 0;

    vault.last_nav_update = Clock::get()?.unix_timestamp;
    vault.is_paused = false;
    vault.bump = ctx.bumps.vault;
    vault.chiller_mint_bump = ctx.bumps.chiller_mint;
    vault._reserved = [0; 64];

    msg!("🧊 $CHILLER Vault initialized!");
    msg!("   Performance fee: {}bps, Withdrawal fee: {}bps", performance_fee_bps, withdrawal_fee_bps);
    msg!("   Min deposit: {} lamports", min_deposit);

    Ok(())
}

#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(mut)]
    pub authority: Signer<'info>,

    #[account(
        init,
        payer = authority,
        space = 8 + VaultState::INIT_SPACE,
        seeds = [b"vault"],
        bump,
    )]
    pub vault: Account<'info, VaultState>,

    /// USDT mint (existing on Solana)
    pub usdt_mint: Account<'info, Mint>,

    /// $CHILLER mint — PDA-controlled, created here
    #[account(
        init,
        payer = authority,
        seeds = [b"chiller-mint"],
        bump,
        mint::decimals = 6,
        mint::authority = vault,
    )]
    pub chiller_mint: Account<'info, Mint>,

    /// Vault's USDT token account
    #[account(
        init,
        payer = authority,
        token::mint = usdt_mint,
        token::authority = vault,
    )]
    pub vault_usdt_account: Account<'info, TokenAccount>,

    /// Team wallet for fee collection (must be USDT token account)
    /// CHECK: validated as token account owned by team
    pub team_wallet: AccountInfo<'info>,

    pub token_program: Program<'info, Token>,
    pub system_program: Program<'info, System>,
    pub rent: Sysvar<'info, Rent>,
}
