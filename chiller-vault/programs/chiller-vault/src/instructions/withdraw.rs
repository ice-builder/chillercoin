use anchor_lang::prelude::*;
use anchor_spl::token::{self, Burn, Mint, Token, TokenAccount, Transfer};

use crate::errors::VaultError;
use crate::events::UserWithdrew;
use crate::state::VaultState;

/// Withdraw: burn $CHILLER → receive USDT at current NAV (minus fee).
pub fn handler(ctx: Context<Withdraw>, chiller_amount: u64) -> Result<()> {
    let vault = &ctx.accounts.vault;

    // ─── Guards ──────────────────────────
    require!(!vault.is_paused, VaultError::VaultPaused);
    require!(chiller_amount > 0, VaultError::ZeroAmount);

    // ─── Calculate USDT to return ────────
    let usdt_gross = vault.usdt_for_withdrawal(chiller_amount);
    require!(usdt_gross > 0, VaultError::MathOverflow);

    // Withdrawal fee (0.5% = 50 bps)
    let fee = (usdt_gross as u128 * vault.withdrawal_fee_bps as u128 / 10_000) as u64;
    let usdt_net = usdt_gross.checked_sub(fee).ok_or(VaultError::MathOverflow)?;

    // Check vault has enough USDT on-chain
    require!(
        ctx.accounts.vault_usdt_account.amount >= usdt_net,
        VaultError::InsufficientVaultBalance
    );

    // Check epoch cap
    let clock = Clock::get()?;
    let current_day = clock.unix_timestamp / 86400;

    let nav_at_withdrawal = vault.nav_per_token();

    // ─── Burn $CHILLER from user ─────────
    token::burn(
        CpiContext::new(
            ctx.accounts.token_program.to_account_info(),
            Burn {
                mint: ctx.accounts.chiller_mint.to_account_info(),
                from: ctx.accounts.user_chiller_account.to_account_info(),
                authority: ctx.accounts.user.to_account_info(),
            },
        ),
        chiller_amount,
    )?;

    // ─── Transfer USDT: vault → user ─────
    let vault_seeds = &[b"vault".as_ref(), &[ctx.accounts.vault.bump]];
    let signer_seeds = &[&vault_seeds[..]];

    token::transfer(
        CpiContext::new_with_signer(
            ctx.accounts.token_program.to_account_info(),
            Transfer {
                from: ctx.accounts.vault_usdt_account.to_account_info(),
                to: ctx.accounts.user_usdt_account.to_account_info(),
                authority: ctx.accounts.vault.to_account_info(),
            },
            signer_seeds,
        ),
        usdt_net,
    )?;

    // ─── Update vault state ──────────────
    let vault = &mut ctx.accounts.vault;
    vault.total_assets = vault.total_assets.checked_sub(usdt_gross)
        .ok_or(VaultError::MathOverflow)?;
    vault.total_supply = vault.total_supply.checked_sub(chiller_amount)
        .ok_or(VaultError::MathOverflow)?;

    // Track epoch withdrawals
    if current_day as u64 != vault.current_epoch {
        vault.current_epoch = current_day as u64;
        vault.epoch_withdrawals = 0;
    }
    vault.epoch_withdrawals = vault.epoch_withdrawals.checked_add(usdt_gross)
        .ok_or(VaultError::MathOverflow)?;

    if vault.max_withdrawal_per_epoch > 0 {
        require!(
            vault.epoch_withdrawals <= vault.max_withdrawal_per_epoch,
            VaultError::EpochCapExceeded
        );
    }

    // ─── Emit event ──────────────────────
    emit!(UserWithdrew {
        user: ctx.accounts.user.key(),
        chiller_burned: chiller_amount,
        usdt_gross,
        withdrawal_fee: fee,
        usdt_returned: usdt_net,
        nav_at_withdrawal,
        timestamp: clock.unix_timestamp,
    });

    msg!("🧊 Withdrew {} $CHILLER → {} USDT (fee: {} USDT)",
        chiller_amount, usdt_net, fee);

    Ok(())
}

#[derive(Accounts)]
pub struct Withdraw<'info> {
    #[account(mut)]
    pub user: Signer<'info>,

    #[account(
        mut,
        seeds = [b"vault"],
        bump = vault.bump,
    )]
    pub vault: Account<'info, VaultState>,

    #[account(
        mut,
        address = vault.chiller_mint,
    )]
    pub chiller_mint: Account<'info, Mint>,

    /// User's $CHILLER account (source — will be burned)
    #[account(
        mut,
        token::mint = vault.chiller_mint,
        token::authority = user,
    )]
    pub user_chiller_account: Account<'info, TokenAccount>,

    /// Vault's USDT account (source)
    #[account(
        mut,
        address = vault.vault_usdt_account,
    )]
    pub vault_usdt_account: Account<'info, TokenAccount>,

    /// User's USDT account (destination)
    #[account(
        mut,
        token::mint = vault.usdt_mint,
        token::authority = user,
    )]
    pub user_usdt_account: Account<'info, TokenAccount>,

    pub token_program: Program<'info, Token>,
}
