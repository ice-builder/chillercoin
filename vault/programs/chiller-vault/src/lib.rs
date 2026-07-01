use anchor_lang::prelude::*;
use anchor_lang::system_program;
use anchor_spl::token::{self, Burn, Mint, MintTo, Token};

declare_id!("7ayYqgiiBtXdk13f9DBFTxJoYKkZyr3AaaLt2f2TPDoH");

// ═══════════════════════════════════════════════
// SOL Treasury PDA — owned by our program
// ═══════════════════════════════════════════════

/// Empty account owned by the program, holds SOL as treasury
#[account]
pub struct SolTreasury {
    pub bump: u8,
}

// ═══════════════════════════════════════════════
// State
// ═══════════════════════════════════════════════

#[account]
#[derive(InitSpace)]
pub struct VaultState {
    pub authority: Pubkey,         // 32
    pub pending_authority: Pubkey, // 32 — H-01: two-step authority transfer
    pub chiller_mint: Pubkey,      // 32
    pub team_wallet: Pubkey,       // 32
    pub total_assets: u64,         // 8  — in lamports (includes SOL on Drift)
    pub total_supply: u64,         // 8  — $CHILLER total supply (6 decimals)
    pub high_water_mark: u64,      // 8
    pub total_trades: u64,         // 8
    pub total_wins: u64,           // 8
    pub cumulative_pnl_bps: i64,   // 8
    pub performance_fee_bps: u16,  // 2
    pub management_fee_bps: u16,   // 2
    pub withdrawal_fee_bps: u16,   // 2
    pub min_deposit: u64,          // 8  — in lamports
    pub max_withdrawal_per_epoch: u64, // 8
    pub epoch_withdrawals: u64,    // 8
    pub current_epoch: u64,        // 8
    pub last_nav_update: i64,      // 8
    pub drain_per_epoch: u64,      // 8  — H-02: max drain per epoch (lamports)
    pub epoch_drained: u64,        // 8  — H-02: drained this epoch
    pub last_drain_epoch: u64,     // 8  — H-02: epoch tracker for drain reset
    pub is_paused: bool,           // 1
    pub bump: u8,                  // 1
    pub chiller_mint_bump: u8,     // 1
    pub sol_vault_bump: u8,        // 1
}

impl VaultState {
    /// NAV per $CHILLER token, scaled to 1e6
    pub fn nav_per_token(&self) -> u64 {
        if self.total_supply == 0 { return 1_000_000; }
        ((self.total_assets as u128 * 1_000_000) / self.total_supply as u128) as u64
    }
    /// How many $CHILLER tokens for a deposit of `lamports`
    pub fn tokens_for_deposit(&self, lamports: u64) -> u64 {
        if self.total_supply == 0 || self.total_assets == 0 {
            // Initial price: 1 $CHILLER = 0.01 SOL (10_000_000 lamports)
            return lamports / 10_000_000;
        }
        ((lamports as u128 * self.total_supply as u128) / self.total_assets as u128) as u64
    }
    /// How many lamports for burning `tokens` $CHILLER
    pub fn sol_for_withdrawal(&self, tokens: u64) -> u64 {
        if self.total_supply == 0 { return 0; }
        ((tokens as u128 * self.total_assets as u128) / self.total_supply as u128) as u64
    }
}

// ═══════════════════════════════════════════════
// Errors
// ═══════════════════════════════════════════════

#[error_code]
pub enum VaultError {
    #[msg("Vault is paused")] VaultPaused,
    #[msg("Deposit below minimum")] DepositBelowMinimum,
    #[msg("Insufficient SOL in vault")] InsufficientVaultBalance,
    #[msg("Epoch cap exceeded")] EpochCapExceeded,
    #[msg("Unauthorized")] Unauthorized,
    #[msg("Bad fee config")] InvalidFeeConfig,
    #[msg("Math overflow")] MathOverflow,
    #[msg("Zero amount")] ZeroAmount,
    #[msg("Pair too long")] PairTooLong,
    #[msg("Drain epoch limit exceeded")] DrainEpochLimitExceeded,
    #[msg("NAV cannot be zero with outstanding supply")] NavCannotBeZero,
    #[msg("Invalid side")] InvalidSide,
    #[msg("No pending authority")] NoPendingAuthority,
}

// ═══════════════════════════════════════════════
// M-01: TradeSide enum
// ═══════════════════════════════════════════════

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, PartialEq, Eq)]
pub enum TradeSide {
    Long,
    Short,
}

impl std::fmt::Display for TradeSide {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            TradeSide::Long => write!(f, "LONG"),
            TradeSide::Short => write!(f, "SHORT"),
        }
    }
}

// ═══════════════════════════════════════════════
// Events
// ═══════════════════════════════════════════════

#[event]
pub struct TradeLogged {
    pub pair: String, pub side: String,
    pub entry_price: u64, pub exit_price: u64,
    pub pnl_bps: i32, pub pnl_usdt: i64, pub duration_secs: u64,
    pub vault_nav_after: u64, pub vault_total_assets: u64, pub timestamp: i64,
}

#[event]
pub struct NAVUpdated {
    pub old_total_assets: u64, pub new_total_assets: u64,
    pub old_nav: u64, pub new_nav: u64,
    pub perf_fee_collected: u64, pub total_supply: u64, pub timestamp: i64,
}

#[event]
pub struct UserDeposited {
    pub user: Pubkey, pub sol_amount: u64, pub chiller_minted: u64,
    pub nav_at_deposit: u64, pub vault_total_assets: u64, pub timestamp: i64,
}

#[event]
pub struct UserWithdrew {
    pub user: Pubkey, pub chiller_burned: u64,
    pub sol_gross: u64, pub withdrawal_fee: u64, pub sol_returned: u64,
    pub nav_at_withdrawal: u64, pub timestamp: i64,
}

#[event]
pub struct VaultPausedEvt { pub paused: bool, pub authority: Pubkey, pub timestamp: i64 }

#[event]
pub struct VaultDrained { pub amount: u64, pub remaining: u64, pub timestamp: i64 }

#[event]
pub struct VaultFunded { pub amount: u64, pub new_balance: u64, pub timestamp: i64 }

// ═══════════════════════════════════════════════
// Program
// ═══════════════════════════════════════════════

#[program]
pub mod chiller_vault {
    use super::*;

    /// Create the $CHILLER SPL mint (PDA as authority)
    pub fn create_mint(_ctx: Context<CreateMint>) -> Result<()> {
        msg!("🧊 $CHILLER mint created");
        Ok(())
    }

    /// Create the SOL treasury PDA (owned by program, can hold SOL)
    pub fn create_treasury(ctx: Context<CreateTreasury>) -> Result<()> {
        ctx.accounts.sol_vault.bump = ctx.bumps.sol_vault;
        msg!("🧊 SOL treasury created");
        Ok(())
    }

    /// Initialize vault with fee config
    pub fn initialize(ctx: Context<InitVault>, perf: u16, mgmt: u16, wfee: u16, min_dep: u64, max_wd: u64) -> Result<()> {
        require!(perf <= 5000 && mgmt <= 1000 && wfee <= 500, VaultError::InvalidFeeConfig);
        let v = &mut ctx.accounts.vault;
        v.authority = ctx.accounts.authority.key();
        v.pending_authority = Pubkey::default();  // H-01: no pending transfer
        v.chiller_mint = ctx.accounts.chiller_mint.key();
        v.team_wallet = ctx.accounts.team_wallet.key();
        v.total_assets = 0; v.total_supply = 0; v.high_water_mark = 0;
        v.total_trades = 0; v.total_wins = 0; v.cumulative_pnl_bps = 0;
        v.performance_fee_bps = perf; v.management_fee_bps = mgmt; v.withdrawal_fee_bps = wfee;
        v.min_deposit = min_dep; v.max_withdrawal_per_epoch = max_wd;
        v.epoch_withdrawals = 0; v.current_epoch = 0;
        v.last_nav_update = Clock::get()?.unix_timestamp;
        // H-02: default drain limit = 30% of 1000 SOL
        v.drain_per_epoch = 300_000_000_000; // 300 SOL
        v.epoch_drained = 0;
        v.last_drain_epoch = 0;
        v.is_paused = false;
        v.bump = ctx.bumps.vault;
        v.chiller_mint_bump = ctx.bumps.chiller_mint;
        v.sol_vault_bump = ctx.accounts.sol_vault.bump;
        msg!("🧊 Vault init! perf={}bps, min_dep={} lamports", perf, min_dep);
        Ok(())
    }

    /// Deposit SOL → receive $CHILLER tokens
    pub fn deposit(ctx: Context<DepositCtx>, amount: u64) -> Result<()> {
        let v = &ctx.accounts.vault;
        require!(!v.is_paused, VaultError::VaultPaused);
        require!(amount > 0, VaultError::ZeroAmount);
        require!(amount >= v.min_deposit, VaultError::DepositBelowMinimum);
        let tokens = v.tokens_for_deposit(amount);
        require!(tokens > 0, VaultError::MathOverflow);
        let nav = v.nav_per_token();

        // Transfer SOL: user → sol_vault PDA
        system_program::transfer(
            CpiContext::new(ctx.accounts.system_program.to_account_info(), system_program::Transfer {
                from: ctx.accounts.user.to_account_info(),
                to: ctx.accounts.sol_vault.to_account_info(),
            }),
            amount,
        )?;

        // Mint $CHILLER to user
        let seeds = &[b"vault".as_ref(), &[v.bump]];
        token::mint_to(CpiContext::new_with_signer(ctx.accounts.token_program.to_account_info(), MintTo {
            mint: ctx.accounts.chiller_mint.to_account_info(),
            to: ctx.accounts.user_chiller.to_account_info(),
            authority: ctx.accounts.vault.to_account_info(),
        }, &[seeds]), tokens)?;

        let v = &mut ctx.accounts.vault;
        v.total_assets = v.total_assets.checked_add(amount).ok_or(VaultError::MathOverflow)?;
        v.total_supply = v.total_supply.checked_add(tokens).ok_or(VaultError::MathOverflow)?;
        if v.high_water_mark == 0 { v.high_water_mark = v.total_assets; }

        emit!(UserDeposited { user: ctx.accounts.user.key(), sol_amount: amount, chiller_minted: tokens, nav_at_deposit: nav, vault_total_assets: v.total_assets, timestamp: Clock::get()?.unix_timestamp });
        Ok(())
    }

    /// Withdraw: burn $CHILLER → receive SOL
    pub fn withdraw(ctx: Context<WithdrawCtx>, tokens: u64) -> Result<()> {
        let v = &ctx.accounts.vault;
        require!(!v.is_paused, VaultError::VaultPaused);
        require!(tokens > 0, VaultError::ZeroAmount);
        let gross = v.sol_for_withdrawal(tokens);
        require!(gross > 0, VaultError::MathOverflow);
        let fee = (gross as u128 * v.withdrawal_fee_bps as u128 / 10_000) as u64;
        let net = gross.checked_sub(fee).ok_or(VaultError::MathOverflow)?;
        let nav = v.nav_per_token();

        // Check sol_vault has enough (above rent-exempt minimum)
        let rent_min = Rent::get()?.minimum_balance(8 + 1); // SolTreasury size
        let available = ctx.accounts.sol_vault.to_account_info().lamports()
            .checked_sub(rent_min).unwrap_or(0);
        require!(available >= gross, VaultError::InsufficientVaultBalance);

        // Burn user's $CHILLER
        token::burn(CpiContext::new(ctx.accounts.token_program.to_account_info(), Burn {
            mint: ctx.accounts.chiller_mint.to_account_info(),
            from: ctx.accounts.user_chiller.to_account_info(),
            authority: ctx.accounts.user.to_account_info(),
        }), tokens)?;

        // Transfer SOL: sol_vault → user (program-owned account, direct lamport ok)
        **ctx.accounts.sol_vault.to_account_info().try_borrow_mut_lamports()? -= net;
        **ctx.accounts.user.try_borrow_mut_lamports()? += net;

        // Transfer fee SOL: sol_vault → team_wallet
        if fee > 0 {
            **ctx.accounts.sol_vault.to_account_info().try_borrow_mut_lamports()? -= fee;
            **ctx.accounts.team_wallet.try_borrow_mut_lamports()? += fee;
        }

        let v = &mut ctx.accounts.vault;
        v.total_assets = v.total_assets.checked_sub(gross).ok_or(VaultError::MathOverflow)?;
        v.total_supply = v.total_supply.checked_sub(tokens).ok_or(VaultError::MathOverflow)?;
        let day = (Clock::get()?.unix_timestamp / 86400) as u64;
        if day != v.current_epoch { v.current_epoch = day; v.epoch_withdrawals = 0; }
        v.epoch_withdrawals = v.epoch_withdrawals.checked_add(gross).ok_or(VaultError::MathOverflow)?;
        if v.max_withdrawal_per_epoch > 0 { require!(v.epoch_withdrawals <= v.max_withdrawal_per_epoch, VaultError::EpochCapExceeded); }

        emit!(UserWithdrew { user: ctx.accounts.user.key(), chiller_burned: tokens, sol_gross: gross, withdrawal_fee: fee, sol_returned: net, nav_at_withdrawal: nav, timestamp: Clock::get()?.unix_timestamp });
        Ok(())
    }

    /// Update NAV — authority reports total assets (on-chain + Drift)
    pub fn update_nav(ctx: Context<UpdateNAVCtx>, new_total: u64) -> Result<()> {
        let v = &ctx.accounts.vault;
        // M-02: Prevent zeroing NAV with outstanding supply
        require!(new_total > 0 || v.total_supply == 0, VaultError::NavCannotBeZero);
        let old_total = v.total_assets; let old_nav = v.nav_per_token();
        let mut pfee: u64 = 0; let mut after = new_total;

        // Performance fee on new profits above HWM
        if new_total > v.high_water_mark && v.high_water_mark > 0 {
            let profit = new_total - v.high_water_mark;
            pfee = (profit as u128 * v.performance_fee_bps as u128 / 10_000) as u64;
            after = new_total.checked_sub(pfee).ok_or(VaultError::MathOverflow)?;
        }

        // Transfer perf fee in SOL: sol_vault → team_wallet (program-owned, direct ok)
        if pfee > 0 {
            let sol_vault_info = ctx.accounts.sol_vault.to_account_info();
            let rent_min = Rent::get()?.minimum_balance(8 + 1);
            let available = sol_vault_info.lamports().checked_sub(rent_min).unwrap_or(0);
            let actual_fee = pfee.min(available); // Don't transfer more than available
            if actual_fee > 0 {
                **sol_vault_info.try_borrow_mut_lamports()? -= actual_fee;
                **ctx.accounts.team_wallet.try_borrow_mut_lamports()? += actual_fee;
            }
        }

        let v = &mut ctx.accounts.vault;
        v.total_assets = after; v.last_nav_update = Clock::get()?.unix_timestamp;
        if after > v.high_water_mark { v.high_water_mark = after; }
        let new_nav = v.nav_per_token();

        emit!(NAVUpdated { old_total_assets: old_total, new_total_assets: after, old_nav, new_nav, perf_fee_collected: pfee, total_supply: v.total_supply, timestamp: Clock::get()?.unix_timestamp });
        Ok(())
    }

    /// Drain SOL from vault to authority (for Drift trading)
    /// H-02: Limited to drain_per_epoch per day
    pub fn drain_to_trade(ctx: Context<DrainCtx>, amount: u64) -> Result<()> {
        require!(amount > 0, VaultError::ZeroAmount);
        let sol_vault_info = ctx.accounts.sol_vault.to_account_info();
        let rent_min = Rent::get()?.minimum_balance(8 + 1);
        let available = sol_vault_info.lamports().checked_sub(rent_min).unwrap_or(0);
        require!(available >= amount, VaultError::InsufficientVaultBalance);

        // H-02: Epoch-based drain limiting
        let v = &mut ctx.accounts.vault;
        let day = (Clock::get()?.unix_timestamp / 86400) as u64;
        if day != v.last_drain_epoch {
            v.last_drain_epoch = day;
            v.epoch_drained = 0;
        }
        // Dynamic limit: max 30% of total_assets or drain_per_epoch (whichever is smaller)
        let dynamic_limit = if v.total_assets > 0 {
            (v.total_assets as u128 * 30 / 100) as u64
        } else {
            v.drain_per_epoch
        };
        let effective_limit = v.drain_per_epoch.min(dynamic_limit);
        v.epoch_drained = v.epoch_drained.checked_add(amount).ok_or(VaultError::MathOverflow)?;
        require!(v.epoch_drained <= effective_limit, VaultError::DrainEpochLimitExceeded);

        let sol_vault_info = ctx.accounts.sol_vault.to_account_info();
        **sol_vault_info.try_borrow_mut_lamports()? -= amount;
        **ctx.accounts.authority.try_borrow_mut_lamports()? += amount;

        let remaining = ctx.accounts.sol_vault.to_account_info().lamports();
        emit!(VaultDrained { amount, remaining, timestamp: Clock::get()?.unix_timestamp });
        msg!("🔄 Drained {} lamports to trade. Remaining: {} (epoch used: {}/{})", amount, remaining, v.epoch_drained, effective_limit);
        Ok(())
    }

    /// Fund vault with SOL (return profits from Drift)
    pub fn fund_vault(ctx: Context<FundCtx>, amount: u64) -> Result<()> {
        require!(amount > 0, VaultError::ZeroAmount);

        system_program::transfer(
            CpiContext::new(ctx.accounts.system_program.to_account_info(), system_program::Transfer {
                from: ctx.accounts.authority.to_account_info(),
                to: ctx.accounts.sol_vault.to_account_info(),
            }),
            amount,
        )?;

        let new_balance = ctx.accounts.sol_vault.to_account_info().lamports();
        emit!(VaultFunded { amount, new_balance, timestamp: Clock::get()?.unix_timestamp });
        msg!("💰 Funded {} lamports. New balance: {}", amount, new_balance);
        Ok(())
    }

    /// Log a completed trade on-chain (M-01: uses TradeSide enum)
    pub fn log_trade(ctx: Context<LogTradeCtx>, pair: String, side: TradeSide, ep: u64, xp: u64, bps: i32, usdt: i64, dur: u64) -> Result<()> {
        require!(pair.len() <= 16, VaultError::PairTooLong);
        let v = &mut ctx.accounts.vault;
        v.total_trades = v.total_trades.checked_add(1).ok_or(VaultError::MathOverflow)?;
        if bps > 0 { v.total_wins = v.total_wins.checked_add(1).ok_or(VaultError::MathOverflow)?; }
        v.cumulative_pnl_bps = v.cumulative_pnl_bps.checked_add(bps as i64).ok_or(VaultError::MathOverflow)?;
        emit!(TradeLogged { pair, side: side.to_string(), entry_price: ep, exit_price: xp, pnl_bps: bps, pnl_usdt: usdt, duration_secs: dur, vault_nav_after: v.nav_per_token(), vault_total_assets: v.total_assets, timestamp: Clock::get()?.unix_timestamp });
        Ok(())
    }

    /// Emergency pause / unpause
    pub fn set_paused(ctx: Context<SetPausedCtx>, paused: bool) -> Result<()> {
        ctx.accounts.vault.is_paused = paused;
        emit!(VaultPausedEvt { paused, authority: ctx.accounts.authority.key(), timestamp: Clock::get()?.unix_timestamp });
        Ok(())
    }

    /// H-01: Step 1 — Propose new authority
    pub fn transfer_authority(ctx: Context<SetPausedCtx>, new_authority: Pubkey) -> Result<()> {
        ctx.accounts.vault.pending_authority = new_authority;
        msg!("🔑 Authority transfer proposed → {}", new_authority);
        Ok(())
    }

    /// H-01: Step 2 — New authority accepts (must sign)
    pub fn accept_authority(ctx: Context<AcceptAuthorityCtx>) -> Result<()> {
        let v = &mut ctx.accounts.vault;
        require!(v.pending_authority == ctx.accounts.new_authority.key(), VaultError::Unauthorized);
        require!(v.pending_authority != Pubkey::default(), VaultError::NoPendingAuthority);
        let old = v.authority;
        v.authority = v.pending_authority;
        v.pending_authority = Pubkey::default();
        msg!("🔑 Authority transferred: {} → {}", old, v.authority);
        Ok(())
    }

    /// H-02: Update drain limit
    pub fn set_drain_limit(ctx: Context<SetPausedCtx>, new_limit: u64) -> Result<()> {
        ctx.accounts.vault.drain_per_epoch = new_limit;
        msg!("🔧 Drain limit set to {} lamports/epoch", new_limit);
        Ok(())
    }
}

// ═══════════════════════════════════════════════
// Account Contexts
// ═══════════════════════════════════════════════

#[derive(Accounts)]
pub struct CreateMint<'info> {
    #[account(mut)] pub authority: Signer<'info>,
    /// CHECK: vault PDA used as mint authority — doesn't need to exist yet
    #[account(seeds = [b"vault"], bump)]
    pub vault: AccountInfo<'info>,
    #[account(init, payer = authority, seeds = [b"chiller-mint"], bump, mint::decimals = 6, mint::authority = vault)]
    pub chiller_mint: Account<'info, Mint>,
    pub token_program: Program<'info, Token>,
    pub system_program: Program<'info, System>,
    pub rent: Sysvar<'info, Rent>,
}

#[derive(Accounts)]
pub struct CreateTreasury<'info> {
    #[account(mut)] pub authority: Signer<'info>,
    #[account(init, payer = authority, space = 8 + 1, seeds = [b"sol-vault"], bump)]
    pub sol_vault: Account<'info, SolTreasury>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct InitVault<'info> {
    #[account(mut)] pub authority: Signer<'info>,
    #[account(init, payer = authority, space = 8 + VaultState::INIT_SPACE, seeds = [b"vault"], bump)]
    pub vault: Account<'info, VaultState>,
    #[account(seeds = [b"chiller-mint"], bump)]
    pub chiller_mint: Account<'info, Mint>,
    #[account(seeds = [b"sol-vault"], bump)]
    pub sol_vault: Account<'info, SolTreasury>,
    /// CHECK: team wallet for fee collection
    pub team_wallet: AccountInfo<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct DepositCtx<'info> {
    #[account(mut)] pub user: Signer<'info>,
    #[account(mut, seeds = [b"vault"], bump = vault.bump)]
    pub vault: Account<'info, VaultState>,
    #[account(mut, address = vault.chiller_mint)]
    pub chiller_mint: Account<'info, Mint>,
    /// CHECK: SOL treasury (program-owned)
    #[account(mut, seeds = [b"sol-vault"], bump = vault.sol_vault_bump)]
    pub sol_vault: AccountInfo<'info>,
    #[account(mut, token::mint = vault.chiller_mint, token::authority = user)]
    pub user_chiller: Account<'info, anchor_spl::token::TokenAccount>,
    pub token_program: Program<'info, Token>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct WithdrawCtx<'info> {
    #[account(mut)] pub user: Signer<'info>,
    #[account(mut, seeds = [b"vault"], bump = vault.bump)]
    pub vault: Account<'info, VaultState>,
    #[account(mut, address = vault.chiller_mint)]
    pub chiller_mint: Account<'info, Mint>,
    /// CHECK: SOL treasury (program-owned)
    #[account(mut, seeds = [b"sol-vault"], bump = vault.sol_vault_bump)]
    pub sol_vault: AccountInfo<'info>,
    #[account(mut, token::mint = vault.chiller_mint, token::authority = user)]
    pub user_chiller: Account<'info, anchor_spl::token::TokenAccount>,
    /// CHECK: team wallet for withdrawal fee
    #[account(mut, address = vault.team_wallet)]
    pub team_wallet: AccountInfo<'info>,
    pub token_program: Program<'info, Token>,
}

#[derive(Accounts)]
pub struct UpdateNAVCtx<'info> {
    #[account(mut)] pub authority: Signer<'info>,
    #[account(mut, seeds = [b"vault"], bump = vault.bump, has_one = authority @ VaultError::Unauthorized)]
    pub vault: Account<'info, VaultState>,
    /// CHECK: SOL treasury (program-owned)
    #[account(mut, seeds = [b"sol-vault"], bump = vault.sol_vault_bump)]
    pub sol_vault: AccountInfo<'info>,
    /// CHECK: team wallet for perf fee
    #[account(mut, address = vault.team_wallet)]
    pub team_wallet: AccountInfo<'info>,
}

#[derive(Accounts)]
pub struct DrainCtx<'info> {
    #[account(mut)] pub authority: Signer<'info>,
    #[account(mut, seeds = [b"vault"], bump = vault.bump, has_one = authority @ VaultError::Unauthorized)]
    pub vault: Account<'info, VaultState>,
    /// CHECK: SOL treasury (program-owned)
    #[account(mut, seeds = [b"sol-vault"], bump = vault.sol_vault_bump)]
    pub sol_vault: AccountInfo<'info>,
}

#[derive(Accounts)]
pub struct FundCtx<'info> {
    #[account(mut)] pub authority: Signer<'info>,
    #[account(seeds = [b"vault"], bump = vault.bump, has_one = authority @ VaultError::Unauthorized)]
    pub vault: Account<'info, VaultState>,
    /// CHECK: SOL treasury (program-owned)
    #[account(mut, seeds = [b"sol-vault"], bump = vault.sol_vault_bump)]
    pub sol_vault: AccountInfo<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct LogTradeCtx<'info> {
    #[account(mut)] pub authority: Signer<'info>,
    #[account(mut, seeds = [b"vault"], bump = vault.bump, has_one = authority @ VaultError::Unauthorized)]
    pub vault: Account<'info, VaultState>,
}

#[derive(Accounts)]
pub struct SetPausedCtx<'info> {
    #[account(mut)] pub authority: Signer<'info>,
    #[account(mut, seeds = [b"vault"], bump = vault.bump, has_one = authority @ VaultError::Unauthorized)]
    pub vault: Account<'info, VaultState>,
}

/// H-01: Accept authority transfer (new authority must sign)
#[derive(Accounts)]
pub struct AcceptAuthorityCtx<'info> {
    #[account(mut)] pub new_authority: Signer<'info>,
    #[account(mut, seeds = [b"vault"], bump = vault.bump)]
    pub vault: Account<'info, VaultState>,
}
