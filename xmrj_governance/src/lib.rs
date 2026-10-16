use anchor_lang::prelude::*;

// The program's on-chain address, and the SAME string Anchor.toml lists
// under [programs.devnet] / [programs.localnet]. It is the public key of
// target/deploy/xmrj_governance-keypair.json (gitignored): `anchor keys
// sync` is the single command that re-aligns both sides if a keypair
// changes.
declare_id!("AVbKyQ8KZa8hTHQwwSJTRGQsVQxVHzTmzmohVCH5CLe");

// The two programs governance DRIVES. Both are path dependencies with the
// `cpi` feature enabled (see Cargo.toml): Anchor gates the generated `cpi`
// module behind that feature, so without it these paths do not exist.
use xmrj_jackpot::program::XmrjJackpot;
use xmrj_treasury::program::XmrjTreasury;

/// THE GOVERNANCE AUTHORITY (a PDA, never a keypair).
///
/// This is the address that OWNS the parameters on the other two programs:
/// each program's `admin_authority` is set to this PDA at initialization, and
/// `execute_action` signs the cross-program call with this PDA's seeds. That
/// is what makes an approved proposal BITE: the callee's own `has_one =
/// admin_authority` + signer check is satisfied by the governance PDA, and
/// satisfied by nothing else — no owner key has to be handed around, and no
/// single signer can change a parameter outside a passed proposal.
///
/// A program cannot deserialize and mutate another program's state accounts
/// directly (the runtime only lets a program treat as its own type an account
/// IT owns), so the CPI is not a shortcut here — it is the only correct
/// mechanism, and it is why the earlier `Account<'info, crate::TreasuryState>`
/// shape was unrunnable rather than merely misnamed.
pub const GOVERNANCE_AUTHORITY_SEED: &[u8] = b"governance_authority";

#[program]
pub mod xmrj_governance {
    use super::*;

    pub fn initialize_governance(
        ctx: Context<InitializeGovernance>,
        owners: Vec<Pubkey>,
        threshold: u8,
    ) -> Result<()> {
        require!(owners.len() >= 3, ErrorCode::NotEnoughOwners);
        require!(
            threshold > 0 && threshold as usize <= owners.len(),
            ErrorCode::InvalidThreshold
        );

        let state = &mut ctx.accounts.governance_state;
        state.owners = owners;
        state.threshold = threshold;
        // Anchor 0.29+ made ctx.bumps a generated struct with named fields;
        // the map lookup this file used before (`ctx.bumps.get(..).unwrap()`)
        // no longer exists and could not compile.
        state.bump = ctx.bumps.governance_state;
        state.authority_bump = ctx.bumps.governance_authority;
        Ok(())
    }

    pub fn propose_action(ctx: Context<ProposeAction>, action: GovernanceAction) -> Result<()> {
        let proposal = &mut ctx.accounts.proposal;
        proposal.action = action;
        proposal.approvals = 0;
        proposal.executed = false;
        Ok(())
    }

    pub fn approve_action(ctx: Context<ApproveAction>) -> Result<()> {
        let state = &ctx.accounts.governance_state;
        let proposal = &mut ctx.accounts.proposal;
        let signer = ctx.accounts.owner.key();
        require!(state.owners.contains(&signer), ErrorCode::NotOwner);
        require!(!proposal.executed, ErrorCode::AlreadyExecuted);
        proposal.approvals = proposal
            .approvals
            .checked_add(1)
            .ok_or(ErrorCode::Overflow)?;
        Ok(())
    }

    /// Execute an approved action. Every parameter action is applied by CPI
    /// into the program that OWNS that parameter, signed by the governance
    /// authority PDA; the action that has no program instruction behind it
    /// (a BPF upgrade) is emitted as an intent event instead of pretending to
    /// be applied here.
    pub fn execute_action(ctx: Context<ExecuteAction>) -> Result<()> {
        let state = &ctx.accounts.governance_state;
        let proposal = &mut ctx.accounts.proposal;
        require!(!proposal.executed, ErrorCode::AlreadyExecuted);
        require!(
            proposal.approvals >= state.threshold,
            ErrorCode::ThresholdNotMet
        );

        // The PDA signs for itself: one seed group, this program's authority.
        // The const is already a &[u8] and the bump is materialized as an
        // array first, so every element of the seed slice is unambiguously a
        // plain &[u8] — nothing is left to unsized-coercion inference.
        let authority_bump = [state.authority_bump];
        let authority_seeds: &[&[&[u8]]] =
            &[&[GOVERNANCE_AUTHORITY_SEED, authority_bump.as_ref()]];

        match proposal.action {
            GovernanceAction::SetSplitRatio {
                reserve_bps,
                jackpot_bps,
            } => {
                require!(
                    reserve_bps + jackpot_bps == 10_000,
                    ErrorCode::InvalidSplitRatio
                );
                xmrj_treasury::cpi::set_split_ratio(
                    CpiContext::new_with_signer(
                        ctx.accounts.treasury_program.to_account_info(),
                        xmrj_treasury::cpi::accounts::SetSplitRatio {
                            treasury_state: ctx.accounts.treasury_state.to_account_info(),
                            admin_authority: ctx.accounts.governance_authority.to_account_info(),
                        },
                        authority_seeds,
                    ),
                    reserve_bps,
                    jackpot_bps,
                )?;
            }
            GovernanceAction::SetExchangeRate { exchange_rate_bps } => {
                xmrj_treasury::cpi::set_exchange_rate(
                    CpiContext::new_with_signer(
                        ctx.accounts.treasury_program.to_account_info(),
                        xmrj_treasury::cpi::accounts::SetExchangeRate {
                            treasury_state: ctx.accounts.treasury_state.to_account_info(),
                            admin_authority: ctx.accounts.governance_authority.to_account_info(),
                        },
                        authority_seeds,
                    ),
                    exchange_rate_bps,
                )?;
            }
            GovernanceAction::SetJackpotPayoutRules { max_payout_bps } => {
                xmrj_jackpot::cpi::set_payout_rules(
                    CpiContext::new_with_signer(
                        ctx.accounts.jackpot_program.to_account_info(),
                        xmrj_jackpot::cpi::accounts::SetPayoutRules {
                            jackpot_state: ctx.accounts.jackpot_state.to_account_info(),
                            admin_authority: ctx.accounts.governance_authority.to_account_info(),
                        },
                        authority_seeds,
                    ),
                    max_payout_bps,
                )?;
            }
            GovernanceAction::UpgradeProgram { new_program_id } => {
                // A BPF upgrade is a chain-level operation (the upgrade
                // authority), not an instruction either program exposes — so
                // this action records the decision and emits the intent. The
                // operator applies it with the upgrade authority; the event is
                // the on-chain receipt that governance asked for it.
                emit!(ProgramUpgradeRequested {
                    target_program: ctx.accounts.target_program.key(),
                    new_program_id,
                });
            }
        }
        proposal.executed = true;
        Ok(())
    }
}

#[account]
pub struct GovernanceState {
    pub owners: Vec<Pubkey>,
    pub threshold: u8,
    pub bump: u8,
    /// The bump of the authority PDA above — stored so execute_action can
    /// sign the CPI without re-deriving it.
    pub authority_bump: u8,
}

#[account]
pub struct Proposal {
    pub action: GovernanceAction,
    pub approvals: u8,
    pub executed: bool,
}

#[derive(Accounts)]
pub struct InitializeGovernance<'info> {
    #[account(
        init,
        payer = payer,
        space = 8 + 4 + 32 * 10 + 1 + 1 + 1,
        seeds = [b"governance_state"],
        bump
    )]
    pub governance_state: Account<'info, GovernanceState>,
    /// CHECK: this program's own authority PDA. Derived, not a signer here —
    /// only its canonical bump is read (`ctx.bumps`) and stored.
    #[account(seeds = [b"governance_authority"], bump)]
    pub governance_authority: UncheckedAccount<'info>,
    #[account(mut)]
    pub payer: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct ProposeAction<'info> {
    #[account(seeds = [b"governance_state"], bump = governance_state.bump)]
    pub governance_state: Account<'info, GovernanceState>,
    #[account(
        init,
        payer = proposer,
        space = 8 + 64,
        seeds = [b"proposal", governance_state.key().as_ref()],
        bump
    )]
    pub proposal: Account<'info, Proposal>,
    #[account(mut)]
    pub proposer: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct ApproveAction<'info> {
    #[account(seeds = [b"governance_state"], bump = governance_state.bump)]
    pub governance_state: Account<'info, GovernanceState>,
    #[account(mut)]
    pub proposal: Account<'info, Proposal>,
    #[account(signer)]
    pub owner: AccountInfo<'info>,
}

#[derive(Accounts)]
pub struct ExecuteAction<'info> {
    #[account(seeds = [b"governance_state"], bump = governance_state.bump)]
    pub governance_state: Account<'info, GovernanceState>,
    #[account(mut)]
    pub proposal: Account<'info, Proposal>,
    /// CHECK: owned by the TREASURY program; this program cannot deserialize
    /// it, and does not need to — the CPI target validates ownership of its
    /// own account, and it is passed through to that call unchanged.
    #[account(mut)]
    pub treasury_state: UncheckedAccount<'info>,
    /// CHECK: owned by the JACKPOT program — same reasoning as above.
    #[account(mut)]
    pub jackpot_state: UncheckedAccount<'info>,
    /// The treasury program account: `Program<XmrjTreasury>` checks it against
    /// that crate's declared id, so a proposal can never be pointed at a
    /// lookalike program.
    pub treasury_program: Program<'info, XmrjTreasury>,
    /// The jackpot program account, checked the same way.
    pub jackpot_program: Program<'info, XmrjJackpot>,
    /// CHECK: this program's authority PDA — it becomes the CPI signer (for
    /// this program's seeds) and the `admin_authority` the callee checks. The
    /// seed literal here must equal GOVERNANCE_AUTHORITY_SEED above; the
    /// wiring drive pins that they agree.
    #[account(seeds = [b"governance_authority"], bump = governance_state.authority_bump)]
    pub governance_authority: UncheckedAccount<'info>,
    /// CHECK: only read for its key when an UpgradeProgram proposal is
    /// executed (the intent event names it). No instruction is invoked on it.
    pub target_program: UncheckedAccount<'info>,
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy)]
pub enum GovernanceAction {
    SetSplitRatio { reserve_bps: u64, jackpot_bps: u64 },
    SetExchangeRate { exchange_rate_bps: u64 },
    SetJackpotPayoutRules { max_payout_bps: u64 },
    UpgradeProgram { new_program_id: Pubkey },
    // SetConversionServiceParams is GONE, deliberately. The conversion
    // service is off-chain (it holds no Solana account, so there is no state
    // for a proposal to write); its limits are configured where the service
    // reads them, per its README and section 6 of
    // XMRJ_SOLANA_DEPLOYMENT_PLAN.md. Keeping a variant that names a
    // nonexistent state type was how this crate stopped compiling.
}

#[event]
pub struct ProgramUpgradeRequested {
    pub target_program: Pubkey,
    pub new_program_id: Pubkey,
}

#[error_code]
pub enum ErrorCode {
    #[msg("Not enough owners")]
    NotEnoughOwners,
    #[msg("Invalid threshold")]
    InvalidThreshold,
    #[msg("Signer is not an owner")]
    NotOwner,
    #[msg("Proposal already executed")]
    AlreadyExecuted,
    #[msg("Threshold not met")]
    ThresholdNotMet,
    #[msg("Invalid split ratio")]
    InvalidSplitRatio,
    #[msg("Overflow")]
    Overflow,
}
