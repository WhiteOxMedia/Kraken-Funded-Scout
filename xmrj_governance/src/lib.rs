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

/// THE UPGRADEABLE LOADER — the loader that owns every deployed program and
/// the ProgramData account recording who may upgrade it. This program's
/// `initialize_governance` reads its own ProgramData account and refuses to
/// create the fixed-seed governance state unless the payer IS the upgrade
/// authority, so a chain watcher cannot race the deployer and install itself
/// as an owner.
pub const UPGRADEABLE_LOADER_ID: Pubkey =
    Pubkey::from_str_const("BPFLoaderUpgradeab1e11111111111111111111111");

/// THE RUNTIME'S ProgramData RULE: the upgradeable loader derives a program's
/// ProgramData account as `[<program id>]` under UPGRADEABLE_LOADER_ID — the
/// program's own id and NOTHING ELSE. There is deliberately no seed constant
/// to share with the suite here: the `"programdata"` prefix this crate used
/// to prepend derived an address with no account at it, so
/// `initialize_governance` was refused on a real cluster with `NotProgramData`
/// (6004). One seed, no prefix, pinned against the pointer the deployed
/// program account carries in its own 32 bytes.

/// `UpgradeableLoaderState::ProgramData` bincode layout: u32 variant (3 =
/// ProgramData), u64 slot, then `Option<Pubkey>` — a one-byte tag (1 = Some)
/// followed by the 32-byte upgrade authority.
const PROGRAM_DATA_VARIANT: u32 = 3;
const PROGRAM_DATA_AUTHORITY_OPTION_OFFSET: usize = 4 + 8;
const PROGRAM_DATA_AUTHORITY_OFFSET: usize = PROGRAM_DATA_AUTHORITY_OPTION_OFFSET + 1;
const PROGRAM_DATA_MIN_LEN: usize = PROGRAM_DATA_AUTHORITY_OFFSET + 32;

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

        // THE UPGRADE-AUTHORITY GATE — the first-initializer race is real,
        // and this state is where it bites hardest: the owners below BECOME
        // the governance of both other programs, and the state is create-once.
        // The payer must therefore BE this program's upgrade authority, read
        // from this program's own ProgramData account (derived by the
        // constraint below, so it cannot be substituted). A chain watcher who
        // is not the deployer is rejected here, before an owner is written.
        require_upgrade_authority(
            &ctx.accounts.program_data,
            &ctx.accounts.payer.key(),
        )?;

        let state = &mut ctx.accounts.governance_state;
        state.owners = owners;
        state.threshold = threshold;
        // Anchor 0.29+ made ctx.bumps a generated struct with named fields;
        // the map lookup this file used before (`ctx.bumps.get(..).unwrap()`)
        // no longer exists and could not compile.
        state.bump = ctx.bumps.governance_state;
        state.authority_bump = ctx.bumps.governance_authority;
        // The ID SEQUENCE starts here: the first proposal anyone makes must
        // carry id 0, the next id 1, and so on.
        state.proposal_count = 0;
        Ok(())
    }

    /// PROPOSE AN ACTION — the first of the three governance instructions, and
    /// the one that hands out proposal IDs.
    ///
    /// THE ID IS A SEQUENCE, NOT A FREE CHOICE: `proposal_id` must equal the
    /// state's `proposal_count`, which this call then increments.
    pub fn propose_action(
        ctx: Context<ProposeAction>,
        proposal_id: u64,
        action: GovernanceAction,
    ) -> Result<()> {
        let state = &mut ctx.accounts.governance_state;
        require!(
            proposal_id == state.proposal_count,
            ErrorCode::ProposalIdOutOfSequence
        );

        let proposal = &mut ctx.accounts.proposal;
        proposal.proposal_id = proposal_id;
        proposal.action = action;
        proposal.approvals = 0;
        proposal.executed = false;

        state.proposal_count = state
            .proposal_count
            .checked_add(1)
            .ok_or(ErrorCode::Overflow)?;
        Ok(())
    }

    pub fn approve_action(ctx: Context<ApproveAction>, proposal_id: u64) -> Result<()> {
        let state = &ctx.accounts.governance_state;
        let proposal = &mut ctx.accounts.proposal;
        // The PDA's own seeds already fix this (the address is derived from
        // the id), and this re-states it against the STORED field, so a
        // proposal whose body disagrees with its address cannot be approved.
        require!(
            proposal.proposal_id == proposal_id,
            ErrorCode::ProposalIdMismatch
        );
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
    pub fn execute_action(ctx: Context<ExecuteAction>, proposal_id: u64) -> Result<()> {
        let state = &ctx.accounts.governance_state;
        let proposal = &mut ctx.accounts.proposal;
        require!(
            proposal.proposal_id == proposal_id,
            ErrorCode::ProposalIdMismatch
        );
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
                // MIGRATION NOTE (Anchor 1.x — UNCOMPILED here: no Rust or
                // Anchor toolchain exists in this environment). 1.0.0 REMOVED
                // the program AccountInfo from CpiContext, because it was a
                // redundant copy of an account already in the Accounts struct;
                // the first argument is the program's Pubkey now. The account
                // is still validated — `Program<'info, XmrjTreasury>` in
                // ExecuteAction checks it against this crate's declared id.
                xmrj_treasury::cpi::set_split_ratio(
                    CpiContext::new_with_signer(
                        XmrjTreasury::id(),
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
                        XmrjTreasury::id(),
                        xmrj_treasury::cpi::accounts::SetExchangeRate {
                            treasury_state: ctx.accounts.treasury_state.to_account_info(),
                            admin_authority: ctx.accounts.governance_authority.to_account_info(),
                        },
                        authority_seeds,
                    ),
                    exchange_rate_bps,
                )?;
            }
            GovernanceAction::SetOperationalAuthority { new_authority } => {
                // The treasury's own setter, reached by CPI — same shape as the
                // two above. The callee checks `has_one = admin_authority` and
                // a signature, and the signer here is THIS program's authority
                // PDA, which is the address the treasury stored as its admin:
                // so the operational key can be rotated, but never replaced by
                // anyone outside an approved proposal.
                xmrj_treasury::cpi::set_operational_authority(
                    CpiContext::new_with_signer(
                        XmrjTreasury::id(),
                        xmrj_treasury::cpi::accounts::SetOperationalAuthority {
                            treasury_state: ctx.accounts.treasury_state.to_account_info(),
                            admin_authority: ctx.accounts.governance_authority.to_account_info(),
                        },
                        authority_seeds,
                    ),
                    new_authority,
                )?;
            }
            GovernanceAction::SetJackpotPayoutRules { max_payout_bps } => {
                xmrj_jackpot::cpi::set_payout_rules(
                    CpiContext::new_with_signer(
                        XmrjJackpot::id(),
                        xmrj_jackpot::cpi::accounts::SetPayoutRules {
                            jackpot_state: ctx.accounts.jackpot_state.to_account_info(),
                            admin_authority: ctx.accounts.governance_authority.to_account_info(),
                        },
                        authority_seeds,
                    ),
                    max_payout_bps,
                )?;
            }
            GovernanceAction::TriggerPayout {
                winner,
                amount_xmr,
            } => {
                // The CPI F1 is about: the jackpot's own `trigger_payout`, called
                // with the governance authority PDA as `admin_authority` and
                // signed by it. The callee checks that the account IS its stored
                // admin authority and that it SIGNED — the same two checks a
                // wallet cannot satisfy, because a PDA has no keypair and only
                // the deriving program can sign for it. This arm is the only
                // code path in the workspace that reaches `trigger_payout`.
                xmrj_jackpot::cpi::trigger_payout(
                    CpiContext::new_with_signer(
                        XmrjJackpot::id(),
                        xmrj_jackpot::cpi::accounts::TriggerPayout {
                            jackpot_state: ctx.accounts.jackpot_state.to_account_info(),
                            admin_authority: ctx.accounts.governance_authority.to_account_info(),
                        },
                        authority_seeds,
                    ),
                    winner,
                    amount_xmr,
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

/// REQUIRE THAT `signer` IS THIS PROGRAM'S UPGRADE AUTHORITY.
///
/// `program_data` is the ProgramData account the `InitializeGovernance`
/// constraint derives as `[crate::ID]` under UPGRADEABLE_LOADER_ID — the
/// loader's own rule — so it is this program's own account and not a
/// lookalike. This function then proves three things about the BYTES the
/// loader wrote: the loader owns the account; the value is `ProgramData`
/// (variant 3), not a Buffer or an empty account that happens to sit at that
/// address; and the authority option is `Some` and equals the signer. An
/// immutable program has `None` and is refused with its own error rather than
/// quietly accepted.
///
/// The check reads runtime-owned state, not a caller-supplied value, so there
/// is nothing here to forge. UNCOMPILED (no Rust/Anchor toolchain exists in
/// this environment), and structurally the same function as the treasury's and
/// jackpot's copies — the wiring drive pins all three.
fn require_upgrade_authority(
    program_data: &UncheckedAccount,
    signer: &Pubkey,
) -> Result<()> {
    let info = program_data.to_account_info();
    require_keys_eq!(
        *info.owner,
        UPGRADEABLE_LOADER_ID,
        ErrorCode::NotProgramData
    );

    // Borrow first, then view the bytes as a plain slice: the offsets below
    // are then ordinary slice indexing rather than method calls through a
    // `Ref`, which keeps the check readable and type-unambiguous.
    let borrowed = info.try_borrow_data()?;
    let data: &[u8] = &borrowed;

    require!(
        data.len() >= PROGRAM_DATA_MIN_LEN,
        ErrorCode::NotProgramData
    );

    let variant = u32::from_le_bytes([data[0], data[1], data[2], data[3]]);
    require!(variant == PROGRAM_DATA_VARIANT, ErrorCode::NotProgramData);

    require!(
        data[PROGRAM_DATA_AUTHORITY_OPTION_OFFSET] == 1,
        ErrorCode::ProgramIsImmutable
    );

    let mut authority = [0u8; 32];
    authority.copy_from_slice(&data[PROGRAM_DATA_AUTHORITY_OFFSET..PROGRAM_DATA_MIN_LEN]);

    require_keys_eq!(
        Pubkey::new_from_array(authority),
        *signer,
        ErrorCode::NotUpgradeAuthority
    );

    Ok(())
}

#[account]
pub struct GovernanceState {
    pub owners: Vec<Pubkey>,
    pub threshold: u8,
    pub bump: u8,
    /// The bump of the authority PDA above — stored so execute_action can
    /// sign the CPI without re-deriving it.
    pub authority_bump: u8,
    /// HOW MANY PROPOSALS THIS GOVERNANCE HAS EVER CREATED, which is also the
    /// id the NEXT one must carry. This is the sequence behind the proposal
    /// PDA: `["proposal", governance_state, proposal_id]`. Before it existed
    /// the proposal PDA was `["proposal", governance_state]` with `init`, so
    /// exactly ONE action could ever exist per cluster (F5 in
    /// XMRJ_DEVNET_EXECUTION_REPORT.md) — a governance that could change one
    /// parameter and then never again.
    pub proposal_count: u64,
}

/// THE LARGEST SERIALIZED `GovernanceAction` — today `TriggerPayout`, whose
/// 1-byte discriminant is followed by a 32-byte key and a u64. The proposal
/// account is sized from THIS rather than from a round number, so a future
/// variant that outgrows it is a constant to bump here instead of an
/// "account data too small" discovered when someone tries to propose.
pub const MAX_ACTION_LEN: usize = 1 + 32 + 8;

/// 8-byte discriminator + the proposal id + the action + approvals + executed.
pub const PROPOSAL_SPACE: usize = 8 + 8 + MAX_ACTION_LEN + 1 + 1;

#[account]
pub struct Proposal {
    /// The id this proposal was created with, which is also the third seed of
    /// its own PDA (`["proposal", governance_state, proposal_id]`). Stored, not
    /// merely derived, so the account itself says which id it is.
    pub proposal_id: u64,
    pub action: GovernanceAction,
    pub approvals: u8,
    pub executed: bool,
}

#[derive(Accounts)]
pub struct InitializeGovernance<'info> {
    #[account(
        init,
        payer = payer,
        space = 8 + 4 + 32 * 10 + 1 + 1 + 1 + 8,
        seeds = [b"governance_state"],
        bump
    )]
    pub governance_state: Account<'info, GovernanceState>,
    /// CHECK: this program's own authority PDA. Derived, not a signer here —
    /// only its canonical bump is read (`ctx.bumps`) and stored.
    #[account(seeds = [b"governance_authority"], bump)]
    pub governance_authority: UncheckedAccount<'info>,
    /// CHECK: THIS PROGRAM'S ProgramData account on the upgradeable loader,
    /// derived here from `[<this program id>]` under the
    /// LOADER's id — the address is fixed, so a lookalike cannot be passed.
    /// Read, not written: `require_upgrade_authority` checks the loader owns
    /// it, that it is in the ProgramData variant, and that its recorded
    /// upgrade authority is the payer.
    // `bump` is REQUIRED by anchor whenever `seeds` are given, and here it is
    // what turns the comment above into a constraint: without it the derive
    // fails ("bump must be provided with seeds") and the whole instruction
    // disappears; with it the address is DERIVED and compared, so a lookalike
    // is rejected by the runtime, not by a comment.
    #[account(
        seeds = [crate::ID.as_ref()],
        seeds::program = UPGRADEABLE_LOADER_ID,
        bump
    )]
    pub program_data: UncheckedAccount<'info>,
    #[account(mut)]
    pub payer: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(proposal_id: u64)]
pub struct ProposeAction<'info> {
    /// MUT because `propose_action` increments `proposal_count` — the write
    /// that makes the next id knowable and the sequence append-only.
    #[account(mut, seeds = [b"governance_state"], bump = governance_state.bump)]
    pub governance_state: Account<'info, GovernanceState>,
    #[account(
        init,
        payer = proposer,
        space = PROPOSAL_SPACE,
        seeds = [
            b"proposal",
            governance_state.key().as_ref(),
            proposal_id.to_le_bytes().as_ref()
        ],
        bump
    )]
    pub proposal: Account<'info, Proposal>,
    #[account(mut)]
    pub proposer: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(proposal_id: u64)]
pub struct ApproveAction<'info> {
    #[account(seeds = [b"governance_state"], bump = governance_state.bump)]
    pub governance_state: Account<'info, GovernanceState>,
    /// DERIVED from the same three seeds its `init` used, so the id in the
    /// instruction and the proposal's own address cannot disagree.
    #[account(
        mut,
        seeds = [
            b"proposal",
            governance_state.key().as_ref(),
            proposal_id.to_le_bytes().as_ref()
        ],
        bump
    )]
    pub proposal: Account<'info, Proposal>,
    /// CHECK: an account key, not an account body: `approve_action` requires
    /// it to be one of `governance_state.owners` before the approval counts,
    /// and the derive demands its signature.
    #[account(signer)]
    pub owner: AccountInfo<'info>,
}

#[derive(Accounts)]
#[instruction(proposal_id: u64)]
pub struct ExecuteAction<'info> {
    #[account(seeds = [b"governance_state"], bump = governance_state.bump)]
    pub governance_state: Account<'info, GovernanceState>,
    /// DERIVED from the proposal's own three seeds, exactly as in
    /// ApproveAction: the id is an input, and the account it resolves to is
    /// checked by the runtime before this instruction's body runs.
    #[account(
        mut,
        seeds = [
            b"proposal",
            governance_state.key().as_ref(),
            proposal_id.to_le_bytes().as_ref()
        ],
        bump
    )]
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
    /// ROTATE the treasury's operational authority — the key that records
    /// inflows and triggers jackpot funding. Appended LAST on purpose: every
    /// existing variant keeps its index, so this cannot change the borsh
    /// encoding of an action someone already proposed.
    SetOperationalAuthority { new_authority: Pubkey },
    /// PAY THE JACKPOT OUT — the action that was MISSING, and the one F1 of
    /// XMRJ_DEVNET_EXECUTION_REPORT.md is about.
    ///
    /// The jackpot's `trigger_payout` requires its `admin_authority` — the
    /// governance authority PDA — to SIGN, and a PDA can only sign inside a
    /// CPI from the program that derives it. With no variant calling it, a
    /// funded jackpot could be topped up forever and never paid: the balance
    /// was a one-way ratchet. This arm is that path, and it is the ONLY one.
    /// The PDA has no keypair, so no wallet can produce the signature, and no
    /// other instruction on either program reaches `trigger_payout`.
    ///
    /// Appended LAST, after SetOperationalAuthority, for the reason given
    /// there: every existing variant keeps its index, so this cannot change
    /// the Borsh encoding of an action someone already proposed.
    TriggerPayout { winner: Pubkey, amount_xmr: u64 },
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
    #[msg("Proposal id is not the next one in sequence; each id is used exactly once")]
    ProposalIdOutOfSequence,
    #[msg("The proposal account does not hold the id the instruction named")]
    ProposalIdMismatch,
    #[msg("Overflow")]
    Overflow,
    #[msg("The program_data account is not this program's ProgramData account")]
    NotProgramData,
    #[msg("This program has no upgrade authority (it is immutable); initialize before finalizing it")]
    ProgramIsImmutable,
    #[msg("Only this program's upgrade authority may initialize it")]
    NotUpgradeAuthority,
}
