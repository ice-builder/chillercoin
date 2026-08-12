use anchor_lang::prelude::*;
use anchor_spl::token::{self, Mint, MintTo, Token, TokenAccount, Transfer};

use crate::errors::VaultError;
use crate::events::UserDeposited;
use crate::state::VaultState;

/// Deposit USDT → receive $CHILLER tokens at current NAV.
pub fn handler(ctx: Context<Deposit>, usdt_amount: u64) -> Result<()> {
    let vault = &ctx.accounts.vault;

    // ─── Guards ──────────────────────────
    require!(!vault.is_paused, VaultError::VaultPaused);
    require!(usdt_amount > 0, VaultError::ZeroAmount);
    require!(usdt_amount >= vault.min_deposit, VaultError::DepositBelowMinimum);

    // ─── Calculate tokens to mint ────────
    let tokens_to_mint = vault.tokens_for_deposit(usdt_amount);
    require!(tokens_to_mint > 0, VaultError::MathOverflow);

    let nav_at_deposit = vault.nav_per_token();

    // ─── Transfer USDT: user → vault ─────
    token::transfer(
        CpiContext::new(
            ctx.accounts.token_program.to_account_info(),
            Transfer {
                from: ctx.accounts.user_usdt_account.to_account_info(),
                to: ctx.accounts.vault_usdt_account.to_account_info(),
                authority: ctx.accounts.user.to_account_info(),
            },
        ),
        usdt_amount,
    )?;

    // ─── Mint $CHILLER → user ────────────
    let vault_seeds = &[b"vault".as_ref(), &[ctx.accounts.vault.bump]];
    let signer_seeds = &[&vault_seeds[..]];

    token::mint_to(
        CpiContext::new_with_signer(
            ctx.accounts.token_program.to_account_info(),
            MintTo {
                mint: ctx.accounts.chiller_mint.to_account_info(),
                to: ctx.accounts.user_chiller_account.to_account_info(),
                authority: ctx.accounts.vault.to_account_info(),
            },
            signer_seeds,
        ),
        tokens_to_mint,
    )?;

    // ─── Update vault state ──────────────
    let vault = &mut ctx.accounts.vault;
    vault.total_assets = vault.total_assets.checked_add(usdt_amount)
        .ok_or(VaultError::MathOverflow)?;
    vault.total_supply = vault.total_supply.checked_add(tokens_to_mint)
        .ok_or(VaultError::MathOverflow)?;

    // Update high-water mark if first deposit
    if vault.high_water_mark == 0 {
        vault.high_water_mark = vault.total_assets;
    }

    // ─── Emit event ──────────────────────
    emit!(UserDeposited {
        user: ctx.accounts.user.key(),
        usdt_amount,
        chiller_minted: tokens_to_mint,
        nav_at_deposit,
        vault_total_assets: vault.total_assets,
        timestamp: Clock::get()?.unix_timestamp,
    });

    msg!("🧊 Deposited {} USDT → minted {} $CHILLER (NAV: {})",
        usdt_amount, tokens_to_mint, nav_at_deposit);

    Ok(())
}

#[derive(Accounts)]
pub struct Deposit<'info> {
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

    /// User's USDT token account (source)
    #[account(
        mut,
        token::mint = vault.usdt_mint,
        token::authority = user,
    )]
    pub user_usdt_account: Account<'info, TokenAccount>,

    /// Vault's USDT token account (destination)
    #[account(
        mut,
        address = vault.vault_usdt_account,
    )]
    pub vault_usdt_account: Account<'info, TokenAccount>,

    /// User's $CHILLER token account (receives minted tokens)
    #[account(
        mut,
        token::mint = vault.chiller_mint,
        token::authority = user,
    )]
    pub user_chiller_account: Account<'info, TokenAccount>,

    pub token_program: Program<'info, Token>,
}
