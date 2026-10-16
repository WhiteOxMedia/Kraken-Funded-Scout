use anchor_lang::prelude::*;

// The program's on-chain address, and the SAME string Anchor.toml lists
// under [programs.devnet] / [programs.localnet]. It is the public key of
// target/deploy/xmrj_treasury-keypair.json (gitignored): `anchor keys sync`
// is the single command that re-aligns both sides if a keypair changes.
declare_id!("GGPyRTZB73reGyjANw9rdEFX8g26MmkKyAEpDqNQ2468");

/// THE GOVERNANCE PROGRAM THIS TREASURY TAKES ITS ORDERS FROM — pinned, and
/// pinned on purpose.
///
/// `admin_authority` below is the governance AUTHORITY PDA, and a PDA's
/// address only means something under the program that derives it. If the
/// program id were supplied by the caller at initialization, anyone could
/// point it at a program of their own, hand the treasury's authority to an
/// address THEY can sign for, and keep it. Pinning removes that possibility
/// from the instruction's inputs entirely.
///
/// The wiring drive (test_anchor_wiring.py) pins this const against
/// xmrj_governance's `declare_id!` and against Anchor.toml, so a rotated
/// governance keypair fails the drive loudly instead of silently disarming
/// the authority chain on-chain.
pub const GOVERNANCE_PROGRAM_ID: Pubkey =
    anchor_lang::solana_program::pubkey!("AVbKyQ8KZa8hTHQwwSJTRGQsVQxVHzTmzmohVCH5CLe");

/// The governance authority PDA's own seed — the SAME bytes xmrj_governance
/// signs with (its `GOVERNANCE_AUTHORITY_SEED`). The address derived here is
/// therefore the one address that program can sign for; the drive pins the
/// two crates' literals together.
pub const GOVERNANCE_AUTHORITY_SEED: &[u8] = b"governance_authority";

/// The singleton state PDA's seed (one treasury per program, by design).
pub const TREASURY_STATE_SEED: &[u8] = b"treasury_state";

/// 8-byte discriminator + six u64 fields + the admin authority pubkey.
pub const TREASURY_STATE_SPACE: usize = 8 + 8 * 6 + 32;

#[program]
pub mod xmrj_treasury {
    use super::*;

    /// CREATE THE TREASURY STATE — and hand its authority to the governance
    /// authority PDA, the only address xmrj_governance can sign for, and only
    /// while executing an approved proposal. Before this instruction existed
    /// the state account could not be created at all, so every setter and
    /// every governance CPI had no target: this is what makes the rest of the
    /// file reachable.
    ///
    /// The three starting parameters are the deployer's, not the authority's:
    /// each is re-settable by governance afterwards (set_split_ratio,
    /// set_exchange_rate), which is the point of the CPI wiring.
    ///
    /// FRONT-RUN NOTE: the state PDA is a fixed seed, so the first caller
    /// wins the initialization race. Call this immediately after `anchor
    /// deploy`, in the same run (see XMRJ_SOLANA_DEPLOYMENT_PLAN.md 4.3). The
    /// hardening step — gating initialize on the program's upgrade authority,
    /// which needs programdata introspection — is deliberately not taken here
    /// and is recorded as an open item rather than implied.
    pub fn initialize(
        ctx: Context<Initialize>,
        reserve_bps: u64,
        jackpot_bps: u64,
        exchange_rate_bps: u64,
    ) -> Result<()> {
        require!(
            reserve_bps.checked_add(jackpot_bps) == Some(10_000),
            ErrorCode::InvalidSplitRatio
        );

        let state = &mut ctx.accounts.treasury_state;
        state.total_inflow_xmrj = 0;
        state.total_reserve_xmrj = 0;
        state.total_converted_xmrj = 0;
        state.exchange_rate_bps = exchange_rate_bps;
        state.reserve_bps = reserve_bps;
        state.jackpot_bps = jackpot_bps;
        state.admin_authority = ctx.accounts.admin_authority.key();

        Ok(())
    }

    pub fn record_inflow_xmrj(ctx: Context<RecordInflowXmrj>, amount: u64) -> Result<()> {
        let state = &mut ctx.accounts.treasury_state;

        state.total_inflow_xmrj = state
            .total_inflow_xmrj
            .checked_add(amount)
            .ok_or(ErrorCode::Overflow)?;

        let reserve_amount =
            amount.checked_mul(state.reserve_bps).ok_or(ErrorCode::Overflow)? / 10_000;
        let conversion_amount =
            amount.checked_mul(state.jackpot_bps).ok_or(ErrorCode::Overflow)? / 10_000;

        state.total_reserve_xmrj = state
            .total_reserve_xmrj
            .checked_add(reserve_amount)
            .ok_or(ErrorCode::Overflow)?;

        state.total_converted_xmrj = state
            .total_converted_xmrj
            .checked_add(conversion_amount)
            .ok_or(ErrorCode::Overflow)?;

        Ok(())
    }

    pub fn set_split_ratio(
        ctx: Context<SetSplitRatio>,
        reserve_bps: u64,
        jackpot_bps: u64,
    ) -> Result<()> {
        require!(
            reserve_bps.checked_add(jackpot_bps) == Some(10_000),
            ErrorCode::InvalidSplitRatio
        );

        let state = &mut ctx.accounts.treasury_state;
        state.reserve_bps = reserve_bps;
        state.jackpot_bps = jackpot_bps;

        Ok(())
    }

    pub fn set_exchange_rate(ctx: Context<SetExchangeRate>, exchange_rate_bps: u64) -> Result<()> {
        let state = &mut ctx.accounts.treasury_state;
        state.exchange_rate_bps = exchange_rate_bps;
        Ok(())
    }
}

#[account]
pub struct TreasuryState {
    pub total_inflow_xmrj: u64,
    pub total_reserve_xmrj: u64,
    pub total_converted_xmrj: u64,
    pub exchange_rate_bps: u64,
    pub reserve_bps: u64,
    pub jackpot_bps: u64,
    pub admin_authority: Pubkey,
}

#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(
        init,
        payer = payer,
        space = TREASURY_STATE_SPACE,
        seeds = [b"treasury_state"],
        bump
    )]
    pub treasury_state: Account<'info, TreasuryState>,
    /// CHECK: the governance authority PDA, derived under the PINNED
    /// governance program id (`seeds::program`). Nothing is read from this
    /// account; only its ADDRESS is stored, and it is stored precisely because
    /// xmrj_governance — and nothing else — can sign for it.
    #[account(
        seeds = [b"governance_authority"],
        seeds::program = GOVERNANCE_PROGRAM_ID,
        bump
    )]
    pub admin_authority: UncheckedAccount<'info>,
    #[account(mut)]
    pub payer: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct RecordInflowXmrj<'info> {
    #[account(mut, seeds = [b"treasury_state"], bump)]
    pub treasury_state: Account<'info, TreasuryState>,
}

#[derive(Accounts)]
pub struct SetSplitRatio<'info> {
    #[account(mut, seeds = [b"treasury_state"], bump, has_one = admin_authority)]
    pub treasury_state: Account<'info, TreasuryState>,
    #[account(signer)]
    pub admin_authority: AccountInfo<'info>,
}

#[derive(Accounts)]
pub struct SetExchangeRate<'info> {
    #[account(mut, seeds = [b"treasury_state"], bump, has_one = admin_authority)]
    pub treasury_state: Account<'info, TreasuryState>,
    #[account(signer)]
    pub admin_authority: AccountInfo<'info>,
}

#[error_code]
pub enum ErrorCode {
    #[msg("Overflow in arithmetic operation")]
    Overflow,
    #[msg("Invalid split ratio; reserve_bps + jackpot_bps must equal 10_000")]
    InvalidSplitRatio,
}
