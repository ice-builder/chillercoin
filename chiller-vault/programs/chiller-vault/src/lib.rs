use anchor_lang::prelude::*;
use anchor_lang::system_program;
use anchor_lang::solana_program::bpf_loader_upgradeable;
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
    pub trade_wallet: Pubkey,      // 32 — C-1: whitelisted destination for drain
    pub total_assets: u64,         // 8  — in lamports (on-chain portion)
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
    pub assets_on_drift: u64,      // 8  — C-1: SOL currently on Drift (drained)
    pub drift_cost_basis: u64,     // 8  — N-5: cumulative SOL sent to Drift (for realized P&L)
    pub pause_timestamp: i64,      // 8  — H-4: when pause was activated
    pub is_paused: bool,           // 1
    pub initialized: bool,         // 1  — H-1: prevents re-init front-run
    pub bump: u8,                  // 1
    pub chiller_mint_bump: u8,     // 1
    pub sol_vault_bump: u8,        // 1
}

impl VaultState {
    /// N-1 FIX: Total system value = on-chain + off-chain (Drift)
    pub fn effective_assets(&self) -> u64 {
        self.total_assets.saturating_add(self.assets_on_drift)
    }
    /// NAV per $CHILLER token, scaled to 1e6
    /// N-1 FIX: uses effective_assets (vault + drift)
    pub fn nav_per_token(&self) -> u64 {
        if self.total_supply == 0 { return 1_000_000; }
        ((self.effective_assets() as u128 * 1_000_000) / self.total_supply as u128) as u64
    }
    /// How many $CHILLER tokens for a deposit of `lamports`
    /// M-2 FIX: accounts for 6 decimal places on mint
    pub fn tokens_for_deposit(&self, lamports: u64) -> u64 {
        let eff = self.effective_assets();
        if self.total_supply == 0 || eff == 0 {
            // Initial price: 1 $CHILLER (1e6 raw) = 0.01 SOL (1e7 lamports)
            // tokens_raw = lamports * 1e6 / 1e7 = lamports / 10
            return lamports / 10;
        }
        ((lamports as u128 * self.total_supply as u128) / eff as u128) as u64
    }
    /// How many lamports for burning `tokens` $CHILLER
    pub fn sol_for_withdrawal(&self, tokens: u64) -> u64 {
        if self.total_supply == 0 { return 0; }
        ((tokens as u128 * self.effective_assets() as u128) / self.total_supply as u128) as u64
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
    #[msg("NAV change exceeds 10% cap")] NavChangeTooLarge,
    #[msg("NAV update cooldown (1h)")] NavUpdateTooFrequent,
    #[msg("NAV exceeds real assets")] NavExceedsRealAssets,
    #[msg("Drain limit too high")] DrainLimitTooHigh,
    #[msg("Invalid trade wallet")] InvalidTradeWallet,
    #[msg("Pause cooldown active")] PauseCooldown,
    #[msg("Slippage exceeded")] SlippageExceeded,                    // M-1
    #[msg("Vault already initialized")] AlreadyInitialized,          // H-1
    #[msg("Invalid mint decimals (expected 6)")] InvalidMintDecimals, // M-2
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
    /// H-1 FIX: verifies authority == program upgrade authority (anti front-run)
    pub fn initialize(ctx: Context<InitVault>, perf: u16, mgmt: u16, wfee: u16, min_dep: u64, max_wd: u64) -> Result<()> {
        require!(perf <= 5000 && mgmt <= 1000 && wfee <= 500, VaultError::InvalidFeeConfig);
        // M-2 FIX: Validate mint decimals match expected (6)
        require!(ctx.accounts.chiller_mint.decimals == 6, VaultError::InvalidMintDecimals);

        // H-1 FIX: Verify caller is the program's upgrade authority
        // ProgramData layout: 4 bytes (type) + 8 bytes (slot) + 1 byte (Option tag) + 32 bytes (pubkey)
        let pd = ctx.accounts.program_data.try_borrow_data()?;
        require!(pd.len() >= 45, VaultError::Unauthorized);
        require!(pd[12] == 1, VaultError::Unauthorized); // Option::Some
        let upgrade_auth = Pubkey::try_from(&pd[13..45]).map_err(|_| VaultError::Unauthorized)?;
        require!(ctx.accounts.authority.key() == upgrade_auth, VaultError::Unauthorized);
        drop(pd);

        let v = &mut ctx.accounts.vault;
        require!(!v.initialized, VaultError::AlreadyInitialized);
        v.authority = ctx.accounts.authority.key();
        v.pending_authority = Pubkey::default();  // H-01: no pending transfer
        v.chiller_mint = ctx.accounts.chiller_mint.key();
        v.team_wallet = ctx.accounts.team_wallet.key();
        v.trade_wallet = ctx.accounts.trade_wallet.key(); // C-1: whitelisted trade dest
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
        v.assets_on_drift = 0;      // C-1: no SOL on Drift initially
        v.drift_cost_basis = 0;     // N-5: no SOL sent to Drift yet
        v.pause_timestamp = 0;      // H-4: not paused
        v.is_paused = false;
        v.initialized = true;       // H-1: mark as initialized
        v.bump = ctx.bumps.vault;
        v.chiller_mint_bump = ctx.bumps.chiller_mint;
        v.sol_vault_bump = ctx.accounts.sol_vault.bump;
        msg!("🧊 Vault init! perf={}bps, min_dep={} lamports", perf, min_dep);
        Ok(())
    }

    /// Deposit SOL → receive $CHILLER tokens
    /// M-1 FIX: slippage protection via min_tokens_out
    pub fn deposit(ctx: Context<DepositCtx>, amount: u64, min_tokens_out: u64) -> Result<()> {
        let v = &ctx.accounts.vault;
        require!(!v.is_paused, VaultError::VaultPaused);
        require!(amount > 0, VaultError::ZeroAmount);
        require!(amount >= v.min_deposit, VaultError::DepositBelowMinimum);
        let tokens = v.tokens_for_deposit(amount);
        require!(tokens > 0, VaultError::MathOverflow);
        // M-1 FIX: slippage check — user specifies minimum acceptable tokens
        require!(tokens >= min_tokens_out, VaultError::SlippageExceeded);
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
    /// M-1 FIX: slippage protection via min_sol_out
    pub fn withdraw(ctx: Context<WithdrawCtx>, tokens: u64, min_sol_out: u64) -> Result<()> {
        let v = &ctx.accounts.vault;
        // H-4 FIX: auto-unpause after 24 hours
        if v.is_paused {
            let elapsed = Clock::get()?.unix_timestamp - v.pause_timestamp;
            require!(elapsed > 86400, VaultError::VaultPaused); // 24h auto-unpause
        }
        require!(tokens > 0, VaultError::ZeroAmount);
        let gross = v.sol_for_withdrawal(tokens);
        require!(gross > 0, VaultError::MathOverflow);
        let fee = (gross as u128 * v.withdrawal_fee_bps as u128 / 10_000) as u64;
        let net = gross.checked_sub(fee).ok_or(VaultError::MathOverflow)?;
        // M-1 FIX: slippage check — user specifies minimum acceptable SOL
        require!(net >= min_sol_out, VaultError::SlippageExceeded);
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

    /// Update NAV — authority reports total effective assets (on-chain + Drift)
    /// N-4 FIX: accepts mark-to-market for vault AND drift separately
    /// N-5 FIX: ceiling from stored state, NO perf fee here (realized-only at fund_vault)
    /// C-3 FIX: ±10% cap per update, 1h cooldown, real-assets ceiling
    pub fn update_nav(ctx: Context<UpdateNAVCtx>, new_vault_value: u64, new_drift_value: u64) -> Result<()> {
        let v = &ctx.accounts.vault;
        let new_effective = new_vault_value.saturating_add(new_drift_value);

        // M-02: Prevent zeroing NAV with outstanding supply
        require!(new_effective > 0 || v.total_supply == 0, VaultError::NavCannotBeZero);

        // C-3 FIX: Cooldown — minimum 1 hour between NAV updates
        let now = Clock::get()?.unix_timestamp;
        require!(now - v.last_nav_update >= 3600, VaultError::NavUpdateTooFrequent);

        // N-1 FIX: Cap change to ±10% of current effective_assets
        let old_effective = v.effective_assets();
        if old_effective > 0 {
            let max_change = old_effective / 10; // 10%
            let diff = if new_effective > old_effective {
                new_effective - old_effective
            } else {
                old_effective - new_effective
            };
            require!(diff <= max_change, VaultError::NavChangeTooLarge);
        }

        // N-5b FIX: Ceiling uses drift_cost_basis (NOT assets_on_drift which we overwrite)
        // cost_basis is only modified by drain_to_trade/fund_vault, never by update_nav
        // This prevents compounding: Aₙ = A₀·1.1ⁿ attack via repeated update_nav calls
        let real_balance = ctx.accounts.sol_vault.to_account_info().lamports();
        let max_nav = real_balance
            .saturating_add(v.drift_cost_basis)
            .saturating_add(v.drift_cost_basis / 10); // +10% for real trading gains
        require!(new_effective <= max_nav, VaultError::NavExceedsRealAssets);

        let old_nav = v.nav_per_token();

        // N-5 FIX: NO performance fee in update_nav
        // Perf fee is only charged on REALIZED profit in fund_vault
        // This prevents extracting real SOL from unrealized mark-to-market gains

        // N-4 FIX: Update both fields directly (mark-to-market)
        let v = &mut ctx.accounts.vault;
        v.total_assets = new_vault_value;
        v.assets_on_drift = new_drift_value;
        v.last_nav_update = now;
        if new_effective > v.high_water_mark { v.high_water_mark = new_effective; }
        let new_nav = v.nav_per_token();

        msg!("📊 NAV updated: vault={} drift={} effective={} nav={}", 
             new_vault_value, new_drift_value, new_effective, new_nav);
        emit!(NAVUpdated { old_total_assets: old_effective, new_total_assets: new_effective, old_nav, new_nav, perf_fee_collected: 0, total_supply: v.total_supply, timestamp: now });
        Ok(())
    }

    /// Drain SOL from vault to trade_wallet (for Drift trading)
    /// C-1 FIX: sends to whitelisted trade_wallet, NOT authority
    /// C-4 FIX: limit based on real vault balance
    pub fn drain_to_trade(ctx: Context<DrainCtx>, amount: u64) -> Result<()> {
        require!(amount > 0, VaultError::ZeroAmount);
        // C-1: trade_wallet verified by DrainCtx address constraint

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
        // C-4 FIX: Dynamic limit based on REAL vault balance
        let dynamic_limit = if available > 0 {
            (available as u128 * 30 / 100) as u64
        } else {
            0
        };
        let effective_limit = v.drain_per_epoch.min(dynamic_limit);
        v.epoch_drained = v.epoch_drained.checked_add(amount).ok_or(VaultError::MathOverflow)?;
        require!(v.epoch_drained <= effective_limit, VaultError::DrainEpochLimitExceeded);

        // C-1 FIX: Transfer SOL to trade_wallet (NOT authority)
        let sol_vault_info = ctx.accounts.sol_vault.to_account_info();
        **sol_vault_info.try_borrow_mut_lamports()? -= amount;
        **ctx.accounts.trade_wallet.try_borrow_mut_lamports()? += amount;

        // N-1 FIX: move from total_assets to assets_on_drift (effective_assets unchanged)
        v.total_assets = v.total_assets.checked_sub(amount).ok_or(VaultError::MathOverflow)?;
        v.assets_on_drift = v.assets_on_drift.checked_add(amount).ok_or(VaultError::MathOverflow)?;
        // N-5: track cost basis for realized profit calculation
        v.drift_cost_basis = v.drift_cost_basis.checked_add(amount).ok_or(VaultError::MathOverflow)?;

        let remaining = ctx.accounts.sol_vault.to_account_info().lamports();
        emit!(VaultDrained { amount, remaining, timestamp: Clock::get()?.unix_timestamp });
        msg!("🔄 Drained {} to trade_wallet. Vault: {} | Drift: {} (epoch: {}/{})",
             amount, remaining, v.assets_on_drift, v.epoch_drained, effective_limit);
        Ok(())
    }

    /// Fund vault with SOL (return profits from Drift)
    /// C-1 FIX: credits total_assets back, reduces assets_on_drift
    /// N-5 FIX: perf fee charged here on REALIZED profit only
    pub fn fund_vault(ctx: Context<FundCtx>, amount: u64) -> Result<()> {
        require!(amount > 0, VaultError::ZeroAmount);

        system_program::transfer(
            CpiContext::new(ctx.accounts.system_program.to_account_info(), system_program::Transfer {
                from: ctx.accounts.authority.to_account_info(),
                to: ctx.accounts.sol_vault.to_account_info(),
            }),
            amount,
        )?;

        let v = &mut ctx.accounts.vault;

        // N-5 FIX: Calculate REALIZED profit
        // Profit = amount returned - cost_basis consumed
        // Cost basis is consumed proportionally: min(amount, cost_basis)
        let cost_consumed = amount.min(v.drift_cost_basis);
        let realized_profit = amount.saturating_sub(cost_consumed);

        // Reduce cost basis by what was "returned"
        v.drift_cost_basis = v.drift_cost_basis.saturating_sub(cost_consumed);

        // Performance fee ONLY on realized profit AND only if above HWM
        let mut pfee: u64 = 0;
        let post_fund_effective = v.total_assets.saturating_add(amount).saturating_add(
            v.assets_on_drift.saturating_sub(amount)
        );
        if realized_profit > 0 && v.performance_fee_bps > 0 && post_fund_effective > v.high_water_mark {
            pfee = (realized_profit as u128 * v.performance_fee_bps as u128 / 10_000) as u64;
            // Transfer perf fee: sol_vault → team_wallet
            let sol_vault_info = ctx.accounts.sol_vault.to_account_info();
            let rent_min = Rent::get()?.minimum_balance(8 + 1);
            let available = sol_vault_info.lamports().checked_sub(rent_min).unwrap_or(0);
            let actual_fee = pfee.min(available);
            if actual_fee > 0 {
                **sol_vault_info.try_borrow_mut_lamports()? -= actual_fee;
                **ctx.accounts.team_wallet.try_borrow_mut_lamports()? += actual_fee;
            }
            pfee = actual_fee; // actual collected
        }

        // Credit total_assets (amount minus fee), reduce drift tracking
        let net_funded = amount.saturating_sub(pfee);
        v.total_assets = v.total_assets.checked_add(net_funded).ok_or(VaultError::MathOverflow)?;
        v.assets_on_drift = v.assets_on_drift.saturating_sub(amount);

        let new_balance = ctx.accounts.sol_vault.to_account_info().lamports();
        if v.effective_assets() > v.high_water_mark { v.high_water_mark = v.effective_assets(); }
        emit!(VaultFunded { amount, new_balance, timestamp: Clock::get()?.unix_timestamp });
        msg!("💰 Funded {} (profit: {}, fee: {}). Balance: {} | Drift: {} | Cost basis: {}", 
             amount, realized_profit, pfee, new_balance, v.assets_on_drift, v.drift_cost_basis);
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
    /// H-4 FIX: records pause_timestamp, can't re-pause within 24h
    pub fn set_paused(ctx: Context<SetPausedCtx>, paused: bool) -> Result<()> {
        let v = &mut ctx.accounts.vault;
        let now = Clock::get()?.unix_timestamp;
        // H-4 FIX: prevent re-pause within 24h (stops infinite freeze)
        if paused && v.pause_timestamp > 0 {
            require!(now - v.pause_timestamp >= 86400, VaultError::PauseCooldown);
        }
        v.is_paused = paused;
        if paused {
            v.pause_timestamp = now;
        }
        emit!(VaultPausedEvt { paused, authority: ctx.accounts.authority.key(), timestamp: now });
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
    /// H-2 FIX: capped at 30% of effective_assets
    pub fn set_drain_limit(ctx: Context<SetPausedCtx>, new_limit: u64) -> Result<()> {
        let v = &ctx.accounts.vault;
        let eff = v.effective_assets();
        let max_allowed = eff * 30 / 100;
        require!(new_limit <= max_allowed || eff == 0, VaultError::DrainLimitTooHigh);
        ctx.accounts.vault.drain_per_epoch = new_limit;
        msg!("🔧 Drain limit set to {} lamports/epoch (max: {})", new_limit, max_allowed);
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
    /// CHECK: C-1: whitelisted trade wallet for drain destination
    pub trade_wallet: AccountInfo<'info>,
    /// CHECK: H-1: program data account to verify deployer == authority
    #[account(
        seeds = [crate::ID.as_ref()],
        bump,
        seeds::program = bpf_loader_upgradeable::id(),
    )]
    pub program_data: AccountInfo<'info>,
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
    /// CHECK: SOL treasury (program-owned) — needed for real_balance ceiling check
    #[account(seeds = [b"sol-vault"], bump = vault.sol_vault_bump)]
    pub sol_vault: AccountInfo<'info>,
}

#[derive(Accounts)]
pub struct DrainCtx<'info> {
    #[account(mut)] pub authority: Signer<'info>,
    #[account(mut, seeds = [b"vault"], bump = vault.bump, has_one = authority @ VaultError::Unauthorized)]
    pub vault: Account<'info, VaultState>,
    /// CHECK: SOL treasury (program-owned)
    #[account(mut, seeds = [b"sol-vault"], bump = vault.sol_vault_bump)]
    pub sol_vault: AccountInfo<'info>,
    /// CHECK: C-1: must match vault.trade_wallet
    #[account(mut, address = vault.trade_wallet @ VaultError::InvalidTradeWallet)]
    pub trade_wallet: AccountInfo<'info>,
}

#[derive(Accounts)]
pub struct FundCtx<'info> {
    #[account(mut)] pub authority: Signer<'info>,
    #[account(mut, seeds = [b"vault"], bump = vault.bump, has_one = authority @ VaultError::Unauthorized)]
    pub vault: Account<'info, VaultState>,
    /// CHECK: SOL treasury (program-owned)
    #[account(mut, seeds = [b"sol-vault"], bump = vault.sol_vault_bump)]
    pub sol_vault: AccountInfo<'info>,
    /// CHECK: N-5: team wallet for realized perf fee
    #[account(mut, address = vault.team_wallet)]
    pub team_wallet: AccountInfo<'info>,
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
