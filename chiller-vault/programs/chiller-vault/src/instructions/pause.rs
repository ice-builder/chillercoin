use anchor_lang::prelude::*;

use crate::errors::VaultError;
use crate::events::VaultPaused;
use crate::state::VaultState;

/// Emergency pause/unpause — stops deposits and withdrawals.
pub fn handler(ctx: Context<Pause>, paused: bool) -> Result<()> {
    let vault = &mut ctx.accounts.vault;

    vault.is_paused = paused;

    emit!(VaultPaused {
        paused,
        authority: ctx.accounts.authority.key(),
        timestamp: Clock::get()?.unix_timestamp,
    });

    if paused {
        msg!("🚨 Vault PAUSED by operator");
    } else {
        msg!("✅ Vault UNPAUSED by operator");
    }

    Ok(())
}

#[derive(Accounts)]
pub struct Pause<'info> {
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
