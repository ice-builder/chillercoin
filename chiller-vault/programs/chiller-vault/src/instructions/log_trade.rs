use anchor_lang::prelude::*;

use crate::errors::VaultError;
use crate::events::TradeLogged;
use crate::state::VaultState;

/// Log a completed trade on-chain for full transparency.
/// Only callable by the vault operator (Soldier bot).
pub fn handler(
    ctx: Context<LogTrade>,
    pair: String,
    side: String,
    entry_price: u64,
    exit_price: u64,
    pnl_bps: i32,
    pnl_usdt: i64,
    duration_secs: u64,
) -> Result<()> {
    require!(pair.len() <= 16, VaultError::PairTooLong);

    let vault = &mut ctx.accounts.vault;
    let clock = Clock::get()?;

    // ─── Update trade stats ──────────────
    vault.total_trades = vault.total_trades.checked_add(1)
        .ok_or(VaultError::MathOverflow)?;

    if pnl_bps > 0 {
        vault.total_wins = vault.total_wins.checked_add(1)
            .ok_or(VaultError::MathOverflow)?;
    }

    vault.cumulative_pnl_bps = vault.cumulative_pnl_bps.checked_add(pnl_bps as i64)
        .ok_or(VaultError::MathOverflow)?;

    // ─── Emit trade event ────────────────
    emit!(TradeLogged {
        pair,
        side,
        entry_price,
        exit_price,
        pnl_bps,
        pnl_usdt,
        duration_secs,
        vault_nav_after: vault.nav_per_token(),
        vault_total_assets: vault.total_assets,
        timestamp: clock.unix_timestamp,
    });

    msg!("📝 Trade #{} logged on-chain", vault.total_trades);

    Ok(())
}

#[derive(Accounts)]
pub struct LogTrade<'info> {
    #[account(mut)]
    pub authority: Signer<'info>,

    #[account(
        mut,
        seeds = [b"vault"],
        bump = vault.bump,
        has_one = authority @ VaultError::Unauthorized,
    )]
    pub vault: Account<'info, VaultState>,
}
