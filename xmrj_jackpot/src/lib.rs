use anchor_lang::prelude::*;

// The program's on-chain address, and the SAME string Anchor.toml lists
// under [programs.devnet] / [programs.localnet]. It is the public key of
// target/deploy/xmrj_jackpot-keypair.json (gitignored): `anchor keys sync`
// is the single command that re-aligns both sides if a keypair changes.
declare_id!("6oHq39qTPVtXEiKPLt1mmxfRwRy3hucd3Qx5SJXdYfox");

/// THE TREASURY PROGRAM THIS JACKPOT ACCEPTS FUNDING FROM — pinned, for the
/// same reason the treasury pins governance: the ONLY account `fund_jackpot`
/// accepts as its funder is a PDA derived under THIS id (see
/// JACKPOT_FUNDER_SEED below), and a caller-supplied value would let whoever
/// initializes first point the jackpot at a program of their choosing.
/// `treasury_program` on the state records the same id; the drive pins the
/// const against xmrj_treasury's `declare_id!` and the manifest.
///
/// MIGRATION NOTE (Anchor 1.x — UNCOMPILED here, as no Rust/Anchor toolchain
/// exists in this environment): `solana_program::pubkey!` went away with the
/// Solana 3.x crate split. `anchor_lang::solana_program::pubkey` is now the
/// `solana_pubkey` crate, and its const decoder is `from_str_const`; the
/// `Pubkey` in the signature above is the prelude's, the same type.
pub const TREASURY_PROGRAM_ID: Pubkey =
    Pubkey::from_str_const("GGPyRTZB73reGyjANw9rdEFX8g26MmkKyAEpDqNQ2468");

/// The governance program whose AUTHORITY PDA owns this jackpot's admin
/// powers (the payout cap). Pinned like the treasury's — a PDA is only
/// meaningful under the program that derives it.
pub const GOVERNANCE_PROGRAM_ID: Pubkey =
    Pubkey::from_str_const("AVbKyQ8KZa8hTHQwwSJTRGQsVQxVHzTmzmohVCH5CLe");

/// The governance authority PDA's seed, byte-for-byte the one xmrj_governance
/// signs with. The drive pins the two crates' literals together.
pub const GOVERNANCE_AUTHORITY_SEED: &[u8] = b"governance_authority";

/// The singleton state PDA's seed (one jackpot per program, by design).
pub const JACKPOT_STATE_SEED: &[u8] = b"jackpot_state";

/// THE FUNDER PDA'S SEED — the half of the funding authorization that does
/// not exist on this side of the call.
///
/// `fund_jackpot` moved funding from a KEY COMPARISON to a SIGNATURE. The old
/// check ("the caller account's address equals state.treasury_program") was
/// satisfied by anyone passing the treasury program's address, so the
/// treasury program did not have to be involved at all. That account is gone.
/// What replaced it: an account derived from THIS seed under the PINNED
/// treasury program id (`seeds::program = TREASURY_PROGRAM_ID`) and demanded
/// as a SIGNER. A PDA is off-curve, so no keypair can sign for it; the only
/// thing on Solana that can produce that signature is `invoke_signed` inside
/// the treasury program. Funding therefore comes from xmrj_treasury's
/// `fund_jackpot_with_xmr` and from nothing else.
///
/// The treasury derives and signs for the same address under its own id (this
/// is its PDA, not ours), so the literal has to agree in both crates — the
/// wiring drive pins the two together.
pub const JACKPOT_FUNDER_SEED: &[u8] = b"jackpot_funder";

/// THE UPGRADEABLE LOADER — the loader that owns every deployed program and
/// the ProgramData account recording who may upgrade it. `initialize` reads
/// that account to gate creation of the fixed-seed state PDA on this
/// program's own upgrade authority.
pub const UPGRADEABLE_LOADER_ID: Pubkey =
    Pubkey::from_str_const("BPFLoaderUpgradeab1e11111111111111111111111");

/// The loader's ProgramData address prefix: `[PROGRAM_DATA_SEED, <program
/// id>]` under UPGRADEABLE_LOADER_ID. One value in all three crates and in
/// the devnet suite — a derivation that disagrees with the runtime's is an
/// account that does not exist.
pub const PROGRAM_DATA_SEED: &[u8] = b"programdata";

/// `UpgradeableLoaderState::ProgramData` bincode layout: u32 variant (3 =
/// ProgramData), u64 slot, then `Option<Pubkey>` — a one-byte tag (1 = Some)
/// followed by the 32-byte upgrade authority.
const PROGRAM_DATA_VARIANT: u32 = 3;
const PROGRAM_DATA_AUTHORITY_OPTION_OFFSET: usize = 4 + 8;
const PROGRAM_DATA_AUTHORITY_OFFSET: usize = PROGRAM_DATA_AUTHORITY_OPTION_OFFSET + 1;
const PROGRAM_DATA_MIN_LEN: usize = PROGRAM_DATA_AUTHORITY_OFFSET + 32;

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
    /// FRONT-RUN NOTE — CLOSED, NOT IMPLIED: the state PDA is a fixed seed,
    /// and the instruction accepts nothing but the pinned values, so the WORST
    /// a first caller could do was get the configuration right. That was never
    /// the whole risk: whoever created the state first also fixed the account
    /// that can change the cap later, and the state is create-once.
    /// `initialize` is therefore gated on THIS PROGRAM'S UPGRADE AUTHORITY —
    /// the ProgramData account is derived from this program's id, and the body
    /// requires its recorded authority to be the signer paying for the state,
    /// so a watcher of the chain cannot land ahead of the deployer. Call it
    /// immediately after `anchor deploy` (XMRJ_SOLANA_DEPLOYMENT_PLAN.md 5.3),
    /// and before the program is ever finalized.
    pub fn initialize(ctx: Context<Initialize>) -> Result<()> {
        // THE UPGRADE-AUTHORITY GATE: the payer must BE this program's
        // upgrade authority, read from this program's own ProgramData account
        // (derived by the constraint below, so it cannot be substituted).
        require_upgrade_authority(
            &ctx.accounts.program_data,
            &ctx.accounts.payer.key(),
        )?;

        let state = &mut ctx.accounts.jackpot_state;
        state.jackpot_balance_xmr = 0;
        state.total_payouts_xmr = 0;
        state.last_winner = Pubkey::default();
        state.treasury_program = TREASURY_PROGRAM_ID;
        state.admin_authority = ctx.accounts.admin_authority.key();
        state.max_payout_bps = 10_000;

        Ok(())
    }

    /// FUND THE JACKPOT — reachable only THROUGH the treasury program.
    ///
    /// The authorization is in FundJackpot's account constraints, not in the
    /// body below, and it is the difference between "the treasury program was
    /// passed as an argument" and "the treasury program signed": the `funder`
    /// account must BE the treasury's funder PDA (derived from
    /// JACKPOT_FUNDER_SEED under the PINNED treasury id) AND be a signer. A
    /// PDA cannot sign for itself, so a direct call from any wallet fails the
    /// constraint no matter what it passes, and a program cannot fake it
    /// either — a PDA is only a signer inside a call the DERIVING program
    /// signed for with invoke_signed. The one caller that satisfies this is
    /// xmrj_treasury::fund_jackpot_with_xmr, which is why
    /// XMRJ_SOLANA_DEPLOYMENT_PLAN.md section 5's "callable only by Treasury"
    /// is now true rather than intended.
    pub fn fund_jackpot(ctx: Context<FundJackpot>, amount_xmr: u64) -> Result<()> {
        let state = &mut ctx.accounts.jackpot_state;

        // Defence in depth, kept because it is cheap and it fails LOUDLY: the
        // state's recorded funder must be the same pinned id the funder PDA
        // above is derived under, so the two can never describe two different
        // programs. (initialize writes it from the same const, so this
        // cannot disagree today; a future second initializer, or a state
        // migrated by hand, would have to keep them equal.)
        require!(
            state.treasury_program == TREASURY_PROGRAM_ID,
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

/// REQUIRE THAT `signer` IS THIS PROGRAM'S UPGRADE AUTHORITY.
///
/// `program_data` is the ProgramData account the `Initialize` constraint
/// derives as `[PROGRAM_DATA_SEED, crate::ID]` under UPGRADEABLE_LOADER_ID, so
/// it is this program's own account and not a lookalike. This function then
/// proves three things about the BYTES the loader wrote: the loader owns the
/// account; the value is `ProgramData` (variant 3), not a Buffer or an empty
/// account that happens to sit at that address; and the authority option is
/// `Some` and equals the signer. An immutable program has `None` and is
/// refused with its own error rather than quietly accepted.
///
/// The check reads runtime-owned state, not a caller-supplied value, so there
/// is nothing here to forge. UNCOMPILED (no Rust/Anchor toolchain exists in
/// this environment), and structurally the same function as the treasury's and
/// governance's copies — the wiring drive pins all three.
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
    /// CHECK: THIS PROGRAM'S ProgramData account on the upgradeable loader,
    /// derived here from `[b"programdata", <this program id>]` under the
    /// LOADER's id — the address is fixed, so a lookalike cannot be passed.
    /// Read, not written: `require_upgrade_authority` checks the loader owns
    /// it, that it is in the ProgramData variant, and that its recorded
    /// upgrade authority is the payer.
    #[account(
        seeds = [PROGRAM_DATA_SEED, crate::ID.as_ref()],
        seeds::program = UPGRADEABLE_LOADER_ID,
    )]
    pub program_data: UncheckedAccount<'info>,
    #[account(mut)]
    pub payer: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct FundJackpot<'info> {
    #[account(mut, seeds = [b"jackpot_state"], bump)]
    pub jackpot_state: Account<'info, JackpotState>,
    /// CHECK: the treasury's funder PDA. Two constraints, and BOTH are
    /// load-bearing: `seeds`+`seeds::program` force the address to be the one
    /// derived from JACKPOT_FUNDER_SEED *under the pinned treasury program*,
    /// and `signer` demands a signature for it. No keypair holds that
    /// address (it is off-curve) and no other program can sign for it, so the
    /// only thing in the runtime that can satisfy this account is
    /// `invoke_signed` inside xmrj_treasury. Nothing is read from the account;
    /// its existence as a signature is the whole payload.
    ///
    /// UNCOMPILED TIMING NOTE (Anchor 1.x): anchor-syn emits
    /// `&TREASURY_PROGRAM_ID.key()` for this constraint, so it relies on
    /// Anchor 1.x's `impl Key for Pubkey` (`key()` returns `*self`). A bare
    /// `Pubkey` constant is correct on the version this workspace pins — the
    /// same shape the treasury's and this crate's `Initialize` already use for
    /// `seeds::program = GOVERNANCE_PROGRAM_ID` — and it is one more reason the
    /// anchor-lang/CLI pin matters rather than being a formality.
    #[account(
        seeds = [JACKPOT_FUNDER_SEED],
        seeds::program = TREASURY_PROGRAM_ID,
        bump,
        signer
    )]
    pub funder: UncheckedAccount<'info>,
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
    #[msg("Funder is not the treasury program's funder PDA")]
    UnauthorizedTreasuryCaller,
    #[msg("Caller is not authorized admin")]
    UnauthorizedAdmin,
    #[msg("Insufficient jackpot balance")]
    InsufficientJackpotBalance,
    #[msg("Payout cap must be <= 10_000 basis points")]
    InvalidPayoutCap,
    #[msg("Payout exceeds the governance cap (max_payout_bps of the balance)")]
    PayoutExceedsCap,
    #[msg("The program_data account is not this program's ProgramData account")]
    NotProgramData,
    #[msg("This program has no upgrade authority (it is immutable); initialize before finalizing it")]
    ProgramIsImmutable,
    #[msg("Only this program's upgrade authority may initialize it")]
    NotUpgradeAuthority,
}
