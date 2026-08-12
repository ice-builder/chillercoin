use anchor_lang::prelude::*;
use anchor_spl::token::{self, Token, TokenAccount, Transfer};

use crate::errors::VaultError;
use crate::events::NAVUpdated;
use crate::state::VaultState;

/// Operator updates vault NAV after trading activity.
/// This reflects off-chain (Bybit) P&L into on-chain vault value.
/// Performance fee is collected on profits above the high-water mark.
pub fn handler(ctx: Context<UpdateNAV>, new_total_assets: u64) -> Result<()> {
    let vault = &ctx.accounts.vault;
    let clock = Clock::get()?;

    // ─── Only operator ───────────────────
    require!(
        ctx.accounts.authority.key() == vault.authority,
        VaultError::Unauthorized
    );

    let old_total_assets = vault.total_assets;
    let old_nav = vault.nav_per_token();

    // ─── Calculate performance fee ───────
    let mut perf_fee: u64 = 0;
    let mut assets_after_fee = new_total_assets;

    // Only charge fee on profits above high-water mark
    if new_total_assets > vault.high_water_mark && vault.high_water_mark > 0 {
        let profit = new_total_assets - vault.high_water_mark;
        perf_fee = (profit as u128 * vault.performance_fee_bps as u128 / 10_000) as u64;
        assets_after_fee = new_total_assets.checked_sub(perf_fee)
            .ok_or(VaultError::MathOverflow)?;
    }

    // ─── Transfer performance fee to team wallet (if on-chain USDT available) ───
    if perf_fee > 0 && ctx.accounts.vault_usdt_account.amount >= perf_fee {
        let vault_seeds = &[b"vault".as_ref(), &[vault.bump]];
        let signer_seeds = &[&vault_seeds[..]];

        token::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.token_program.to_account_info(),
                Transfer {
                    from: ctx.accounts.vault_usdt_account.to_account_info(),
                    to: ctx.accounts.team_usdt_account.to_account_info(),
                    authority: ctx.accounts.vault.to_account_info(),
                },
                signer_seeds,
            ),
            perf_fee,
        )?;
    }

    // ─── Update vault state ──────────────
    let vault = &mut ctx.accounts.vault;
    vault.total_assets = assets_after_fee;
    vault.last_nav_update = clock.unix_timestamp;

    // Update high-water mark
    if assets_after_fee > vault.high_water_mark {
        vault.high_water_mark = assets_after_fee;
    }

    let new_nav = vault.nav_per_token();

    // ─── Emit event ──────────────────────
    emit!(NAVUpdated {
        old_total_assets,
        new_total_assets: assets_after_fee,
        old_nav,
        new_nav,
        perf_fee_collected: perf_fee,
        total_supply: vault.total_supply,
        timestamp: clock.unix_timestamp,
    });

    msg!("📈 NAV updated: {} → {} (fee: {} USDT)",
        old_nav, new_nav, perf_fee);

    Ok(())
}

#[derive(Accounts)]
pub struct UpdateNAV<'info> {
    #[account(mut)]
    pub authority: Signer<'info>,

    #[account(
        mut,
        seeds = [b"vault"],
        bump = vault.bump,
        has_one = authority @ VaultError::Unauthorized,
    )]
    pub vault: Account<'info, VaultState>,

    /// Vault's USDT account (for fee transfer)
    #[account(
        mut,
        address = vault.vault_usdt_account,
    )]
    pub vault_usdt_account: Account<'info, TokenAccount>,

    /// Team's USDT account (receives performance fee)
    #[account(
        mut,
        token::mint = vault.usdt_mint,
    )]
    pub team_usdt_account: Account<'info, TokenAccount>,

    pub token_program: Program<'info, Token>,
}
