use anchor_lang::prelude::*;

// The program's on-chain address, and the SAME string Anchor.toml lists
// under [programs.devnet] / [programs.localnet]. It is the public key of
// target/deploy/xmrj_jackpot-keypair.json (gitignored): `anchor keys sync`
// is the single command that re-aligns both sides if a keypair changes.
declare_id!("6oHq39qTPVtXEiKPLt1mmxfRwRy3hucd3Qx5SJXdYfox");

/// THE TREASURY PROGRAM THIS JACKPOT ACCEPTS FUNDING FROM — pinned, for the
/// same reason the treasury pins governance: `treasury_program` is the value
/// `fund_jackpot` authorizes against, and a caller-supplied value would let
/// whoever initializes first point the jackpot at a program of their choosing.
/// The drive pins this const against xmrj_treasury's `declare_id!` and the
/// manifest.
pub const TREASURY_PROGRAM_ID: Pubkey =
    anchor_lang::solana_program::pubkey!("GGPyRTZB73reGyjANw9rdEFX8g26MmkKyAEpDqNQ2468");

/// The governance program whose AUTHORITY PDA owns this jackpot's admin
/// powers (the payout cap). Pinned like the treasury's — a PDA is only
/// meaningful under the program that derives it.
pub const GOVERNANCE_PROGRAM_ID: Pubkey =
    anchor_lang::solana_program::pubkey!("AVbKyQ8KZa8hTHQwwSJTRGQsVQxVHzTmzmohVCH5CLe");

/// The governance authority PDA's seed, byte-for-byte the one xmrj_governance
/// signs with. The drive pins the two crates' literals together.
pub const GOVERNANCE_AUTHORITY_SEED: &[u8] = b"governance_authority";

/// The singleton state PDA's seed (one jackpot per program, by design).
pub const JACKPOT_STATE_SEED: &[u8] = b"jackpot_state";

/// 8-byte discriminator + five fields + the payout cap: balance, total
/// payouts, last winner, treasury program, admin authority, max payout bps.
pub const JACKPOT_STATE_SPACE: usize = 8 + 8 + 8 + 32 + 32 + 32 + 8;

#[program]
pub mod xmrj_jackpot {
    use super::*;

    /// CREATE THE JACKPOT STATE — with its authority and its funder PINNED,
    /// not supplied. Before this instruction existed the state account could
    /// not be created, so funding, payouts and the payout cap had no target.
    ///
    /// It takes NO parameters on purpose: every value a caller could choose
    /// here would be one a mistake (or a front-runner) could get wrong
    /// irreversibly. The authority is the governance authority PDA, the funder
    /// is the treasury program above, and the payout cap starts UNCAPPED
    /// (10 000 bps) — the one of the three that governance can change later,
    /// through an approved proposal, via set_payout_rules.
    ///
    /// FRONT-RUN NOTE: the state PDA is a fixed seed, so the first caller wins
    /// the initialization race; this is why the instruction accepts nothing
    /// but the correct pinned values. Call it immediately after `anchor
    /// deploy` (XMRJ_SOLANA_DEPLOYMENT_PLAN.md 5.3). Gating initialize on the
    /// program's upgrade authority is recorded as an open item, not implied.
    pub fn initialize(ctx: Context<Initialize>) -> Result<()> {
        let state = &mut ctx.accounts.jackpot_state;
        state.jackpot_balance_xmr = 0;
        state.total_payouts_xmr = 0;
        state.last_winner = Pubkey::default();
        state.treasury_program = TREASURY_PROGRAM_ID;
        state.admin_authority = ctx.accounts.admin_authority.key();
        state.max_payout_bps = 10_000;

        Ok(())
    }

    pub fn fund_jackpot(ctx: Context<FundJackpot>, amount_xmr: u64) -> Result<()> {
        let state = &mut ctx.accounts.jackpot_state;

        // NOTE — KNOWN WEAKNESS, RECORDED RATHER THAN SILENTLY TRUSTED:
        // `caller` is any account whose KEY equals state.treasury_program, so
        // a caller can pass the treasury program's own address without the
        // treasury program being involved at all. This does NOT yet enforce
        // "the Treasury program is the only funder", which is what
        // XMRJ_SOLANA_DEPLOYMENT_PLAN.md section 5 asks for. The fix is a
        // treasury funder PDA (a `seeds::program = TREASURY_PROGRAM_ID`
        // account that only the treasury program can sign for) plus the
        // treasury -> jackpot funding CPI, which does not exist yet; that is
        // the next piece of work, not a property to assume today.
        require!(
            ctx.accounts.caller.key() == state.treasury_program,
            ErrorCode::UnauthorizedTreasuryCaller
        );

        state.jackpot_balance_xmr = state
            .jackpot_balance_xmr
            .checked_add(amount_xmr)
            .ok_or(ErrorCode::Overflow)?;

        Ok(())
    }

    pub fn trigger_payout(
        ctx: Context<TriggerPayout>,
        winner: Pubkey,
        amount_xmr: u64,
    ) -> Result<()> {
        let state = &mut ctx.accounts.jackpot_state;

        require!(
            ctx.accounts.admin_authority.key() == state.admin_authority,
            ErrorCode::UnauthorizedAdmin
        );

        let balance = state.jackpot_balance_xmr;

        require!(
            amount_xmr <= balance,
            ErrorCode::InsufficientJackpotBalance
        );

        // THE GOVERNANCE CAP: `max_payout_bps` is the largest share of the
        // LIVE balance a single payout may take (10 000 = uncapped). It can
        // only be changed through the governance-authority CPI, so an
        // approved proposal can shrink the blast radius of a bad or
        // compromised payout call — the power that had no home before.
        if state.max_payout_bps < 10_000 {
            let ceiling = (balance as u128)
                .checked_mul(state.max_payout_bps as u128)
                .ok_or(ErrorCode::Overflow)?
                / 10_000u128;
            require!(amount_xmr as u128 <= ceiling, ErrorCode::PayoutExceedsCap);
        }

        state.jackpot_balance_xmr = balance
            .checked_sub(amount_xmr)
            .ok_or(ErrorCode::Overflow)?;

        state.total_payouts_xmr = state
            .total_payouts_xmr
            .checked_add(amount_xmr)
            .ok_or(ErrorCode::Overflow)?;

        state.last_winner = winner;

        emit!(PayoutEvent {
            winner,
            amount_xmr,
        });

        Ok(())
    }

    /// The payout cap, callable ONLY by the state's admin_authority — which
    /// is the governance authority PDA, so this instruction is reached by an
    /// approved proposal through `xmrj_governance::execute_action` and by
    /// nothing else. Same shape as the treasury's setters: admin-gated, has_one
    /// checked, signer required (a CPI from governance satisfies all three).
    pub fn set_payout_rules(ctx: Context<SetPayoutRules>, max_payout_bps: u64) -> Result<()> {
        require!(max_payout_bps <= 10_000, ErrorCode::InvalidPayoutCap);

        let state = &mut ctx.accounts.jackpot_state;
        state.max_payout_bps = max_payout_bps;

        Ok(())
    }

    pub fn get_jackpot_state(_ctx: Context<GetJackpotState>) -> Result<()> {
        Ok(())
    }
}

#[account]
pub struct JackpotState {
    pub jackpot_balance_xmr: u64,
    pub total_payouts_xmr: u64,
    pub last_winner: Pubkey,
    pub treasury_program: Pubkey,
    pub admin_authority: Pubkey,
    /// The governance-set payout cap in basis points of the live balance
    /// (10_000 = uncapped). Appended, so no existing field moves.
    pub max_payout_bps: u64,
}

#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(
        init,
        payer = payer,
        space = JACKPOT_STATE_SPACE,
        seeds = [b"jackpot_state"],
        bump
    )]
    pub jackpot_state: Account<'info, JackpotState>,
    /// CHECK: the governance authority PDA, derived under the PINNED
    /// governance program id (`seeds::program`). Nothing is read from it; only
    /// its address is stored, because xmrj_governance — and nothing else — can
    /// sign for it.
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
pub struct FundJackpot<'info> {
    #[account(mut, seeds = [b"jackpot_state"], bump)]
    pub jackpot_state: Account<'info, JackpotState>,
    pub caller: AccountInfo<'info>,
}

#[derive(Accounts)]
pub struct TriggerPayout<'info> {
    #[account(mut, seeds = [b"jackpot_state"], bump)]
    pub jackpot_state: Account<'info, JackpotState>,
    #[account(signer)]
    pub admin_authority: AccountInfo<'info>,
}

#[derive(Accounts)]
pub struct SetPayoutRules<'info> {
    #[account(mut, seeds = [b"jackpot_state"], bump, has_one = admin_authority)]
    pub jackpot_state: Account<'info, JackpotState>,
    #[account(signer)]
    pub admin_authority: AccountInfo<'info>,
}

#[derive(Accounts)]
pub struct GetJackpotState<'info> {
    #[account(seeds = [b"jackpot_state"], bump)]
    pub jackpot_state: Account<'info, JackpotState>,
}

#[event]
pub struct PayoutEvent {
    pub winner: Pubkey,
    pub amount_xmr: u64,
}

#[error_code]
pub enum ErrorCode {
    #[msg("Overflow in arithmetic operation")]
    Overflow,
    #[msg("Caller is not authorized treasury program")]
    UnauthorizedTreasuryCaller,
    #[msg("Caller is not authorized admin")]
    UnauthorizedAdmin,
    #[msg("Insufficient jackpot balance")]
    InsufficientJackpotBalance,
    #[msg("Payout cap must be <= 10_000 basis points")]
    InvalidPayoutCap,
    #[msg("Payout exceeds the governance cap (max_payout_bps of the balance)")]
    PayoutExceedsCap,
}
