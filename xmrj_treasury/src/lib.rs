use anchor_lang::prelude::*;

// THE CRATE THIS PROGRAM FUNDS. The `cpi` feature xmrj_treasury's Cargo.toml
// enables on xmrj_jackpot is what generates this module at all — without it
// neither `xmrj_jackpot::cpi::*` nor the `program::XmrjJackpot` marker below
// exists and this crate cannot build. The marker is not decoration: it is what
// makes the account passed as `jackpot_program` provably BE the jackpot
// program (anchor generates the id comparison from the callee's declare_id!),
// so a call can never be pointed at a lookalike with the same instructions.
use xmrj_jackpot::program::XmrjJackpot;

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
///
/// MIGRATION NOTE (Anchor 1.x — UNCOMPILED in this environment, no Rust or
/// Anchor toolchain exists here): the `anchor_lang::solana_program::pubkey!`
/// path this const used is gone with the Solana 3.x crate split that Anchor
/// 1.x rides. `solana_program::pubkey` is now the `solana_pubkey` crate
/// itself, whose const decoder is `from_str_const` — and `Pubkey` here is the
/// prelude's, i.e. that same type. The drive pins the literal either way.
pub const GOVERNANCE_PROGRAM_ID: Pubkey =
    Pubkey::from_str_const("AVbKyQ8KZa8hTHQwwSJTRGQsVQxVHzTmzmohVCH5CLe");

/// The governance authority PDA's own seed — the SAME bytes xmrj_governance
/// signs with (its `GOVERNANCE_AUTHORITY_SEED`). The address derived here is
/// therefore the one address that program can sign for; the drive pins the
/// two crates' literals together.
pub const GOVERNANCE_AUTHORITY_SEED: &[u8] = b"governance_authority";

/// The singleton state PDA's seed (one treasury per program, by design).
pub const TREASURY_STATE_SEED: &[u8] = b"treasury_state";

/// THE FUNDER PDA'S SEED — the address this program signs for when it funds
/// the jackpot, and the ONLY funder xmrj_jackpot accepts.
///
/// It is derived under THIS program's id (below, in FundJackpotWithXmr, with
/// no `seeds::program`: our own id is the default). That is the entire
/// mechanism: a PDA is off-curve, so no keypair can sign for it, and a
/// program can only ever sign for PDAs derived under its OWN id. The jackpot
/// derives the same address with `seeds::program = TREASURY_PROGRAM_ID` and
/// requires it as a SIGNER, which is why the seed literal has to be byte-for-
/// byte equal in both crates — the wiring drive pins them together.
///
/// WHAT THIS REPLACES: xmrj_jackpot::fund_jackpot used to compare the caller
/// account's KEY to its recorded treasury id. Any wallet could pass the
/// treasury program's address and pass that check without the treasury being
/// involved; the check proved nothing. There is no such comparison left on
/// either side — funding is a signature now, and this program is the only
/// thing that can produce it.
pub const JACKPOT_FUNDER_SEED: &[u8] = b"jackpot_funder";

/// THE UPGRADEABLE LOADER — the loader whose ProgramData account records who
/// may upgrade this program. `initialize` reads it to gate creation of the
/// fixed-seed state PDA on that authority, so a front-runner cannot create the
/// state first and hand itself the admin powers.
pub const UPGRADEABLE_LOADER_ID: Pubkey =
    Pubkey::from_str_const("BPFLoaderUpgradeab1e11111111111111111111111");

/// THE RUNTIME'S ProgramData RULE: the upgradeable loader derives a program's
/// ProgramData account as `[<program id>]` under UPGRADEABLE_LOADER_ID — the
/// program's own id and NOTHING ELSE. There is deliberately no seed constant
/// to share with the suite here: the `"programdata"` prefix this crate used
/// to prepend derived 5ALdAqdhEmXK8gDjj3JsT84Cs1QVHy98Rd3bHW74D93L for this
/// program's id, while the account the loader actually created is
/// CRANd8V3PhnZd9gs46qHMHQGvLoeJgQS5XAZaqmd4wNo — so `initialize` was
/// refused on a real cluster with `NotProgramData` (6004). One seed, no
/// prefix, pinned against the pointer the deployed program account carries in
/// its own 32 bytes.

/// `UpgradeableLoaderState::ProgramData` is bincode-encoded as: u32 variant
/// (3 = ProgramData), u64 slot, then `Option<Pubkey>` — a one-byte tag (1 =
/// Some) followed by the 32-byte upgrade authority. These offsets are the
/// layout the runtime itself writes; they are constants rather than a
/// dependency on the loader crate because Anchor 1.x does not re-export its
/// state enum.
const PROGRAM_DATA_VARIANT: u32 = 3;
const PROGRAM_DATA_AUTHORITY_OPTION_OFFSET: usize = 4 + 8;
const PROGRAM_DATA_AUTHORITY_OFFSET: usize = PROGRAM_DATA_AUTHORITY_OPTION_OFFSET + 1;
const PROGRAM_DATA_MIN_LEN: usize = PROGRAM_DATA_AUTHORITY_OFFSET + 32;

/// 8-byte discriminator + seven u64 fields + the admin authority pubkey +
/// the operational authority pubkey. The wiring drive recomputes this from
/// the struct's own fields, so a field added without this line is a failure
/// there rather than a runtime "account data too small" at initialize.
pub const TREASURY_STATE_SPACE: usize = 8 + 8 * 7 + 32 + 32;

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
    /// The four starting parameters are the deployer's, not the authority's:
    /// each is re-settable by governance afterwards (set_split_ratio,
    /// set_exchange_rate, set_operational_authority), which is the point of
    /// the CPI wiring.
    ///
    /// FRONT-RUN NOTE — NOW CLOSED, NOT RECORDED: the state PDA is a fixed
    /// seed, so whoever calls first normally wins the initialization race.
    /// `initialize` is therefore gated on THIS PROGRAM'S UPGRADE AUTHORITY:
    /// the instruction takes the program's own ProgramData account and the
    /// body requires its recorded `upgrade_authority_address` to be the
    /// signer paying for the state. Only the key that can upgrade this
    /// program can create its state, so a watcher of the chain cannot land
    /// ahead of the deployer and point `admin_authority` at itself — the one
    /// outcome that would have been irreversible. See
    /// `require_upgrade_authority` at the bottom of this file for the exact
    /// bytes read and XMRJ_SOLANA_DEPLOYMENT_PLAN.md 4.3 for the operational
    /// consequence (initialize before the program is made immutable).
    pub fn initialize(
        ctx: Context<Initialize>,
        reserve_bps: u64,
        jackpot_bps: u64,
        exchange_rate_bps: u64,
        operational_authority: Pubkey,
    ) -> Result<()> {
        require!(
            reserve_bps.checked_add(jackpot_bps) == Some(10_000),
            ErrorCode::InvalidSplitRatio
        );
        // THE RATE IS A MULTIPLIER on the funding path — see
        // fund_jackpot_with_xmr, where the recorded XMRJ share is valued in
        // XMR at this rate. A zero rate is refused where it is SET: it would
        // make every recorded share worth zero XMR and brick funding for the
        // life of the state, which is not a thing to discover at the first
        // funding call.
        require!(exchange_rate_bps > 0, ErrorCode::InvalidExchangeRate);

        // THE UPGRADE-AUTHORITY GATE: the payer must BE this program's
        // upgrade authority, read from the program's own ProgramData account
        // (which the constraint in `Initialize` derives from this program's
        // id, so it cannot be substituted). A front-runner holding any other
        // key — including one that watched the deploy — is rejected here,
        // before a single field of the state is written.
        require_upgrade_authority(
            &ctx.accounts.program_data,
            &ctx.accounts.payer.key(),
        )?;

        let state = &mut ctx.accounts.treasury_state;
        state.total_inflow_xmrj = 0;
        state.total_reserve_xmrj = 0;
        state.total_converted_xmrj = 0;
        state.total_jackpot_funded_xmr = 0;
        state.exchange_rate_bps = exchange_rate_bps;
        state.reserve_bps = reserve_bps;
        state.jackpot_bps = jackpot_bps;
        state.admin_authority = ctx.accounts.admin_authority.key();
        // THE OPERATIONAL AUTHORITY, chosen by the deployer here and by an
        // approved proposal afterwards (set_operational_authority). It is a
        // PARAMETER rather than a pinned const on purpose: the operating key
        // is infrastructure, not a security anchor, and a PDA cannot sign the
        // operational calls. It is seeded with the deployer's own key in the
        // deployment plan's 4.3 and rotated by governance from then on.
        state.operational_authority = operational_authority;

        Ok(())
    }

    pub fn record_inflow_xmrj(ctx: Context<RecordInflowXmrj>, amount: u64) -> Result<()> {
        let state = &mut ctx.accounts.treasury_state;

        state.total_inflow_xmrj = state
            .total_inflow_xmrj
            .checked_add(amount)
            .ok_or(ErrorCode::Overflow)?;

        // THE OPERATIONAL GATE lives in RecordInflowXmrj, not here: the state
        // carries `operational_authority` and the struct demands that exact
        // address as a SIGNER (`has_one` + `signer`). Recording an inflow
        // decides the reserve/conversion split of real money, so it is not an
        // open call any more — and it is NOT gated on `admin_authority`,
        // because that is the governance authority PDA and the proposal PDA is
        // a singleton (see XMRJ_SOLANA_DEPLOYMENT_PLAN.md 9.3): routing
        // operations through it would make each one a once-per-cluster act.

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

    /// SET THE ON-CHAIN EXCHANGE RATE — in basis points of XMR per XMRJ, the
    /// same unit and the same integer arithmetic the off-chain conversion
    /// service uses (`xmr_amount = xmrj_amount * exchange_rate_bps / 10 000`,
    /// see xmrj_conversion_service): 10 000 means 1 XMRJ = 1 XMR, and 1 250
    /// means 1 XMRJ = 0.125 XMR.
    ///
    /// Since F2 of XMRJ_DEVNET_EXECUTION_REPORT.md the rate is READ, not merely
    /// recorded: it VALUES the convertible share in fund_jackpot_with_xmr, so
    /// this setter moves the ceiling on how much XMR the treasury may forward.
    /// A rate of 0 is refused: it would make every recorded share worth zero
    /// XMR and brick funding permanently.
    pub fn set_exchange_rate(ctx: Context<SetExchangeRate>, exchange_rate_bps: u64) -> Result<()> {
        require!(exchange_rate_bps > 0, ErrorCode::InvalidExchangeRate);

        let state = &mut ctx.accounts.treasury_state;
        state.exchange_rate_bps = exchange_rate_bps;
        Ok(())
    }

    /// ROTATE THE OPERATIONAL AUTHORITY — the one lever governance has over
    /// the key that records inflows and triggers funding. Same shape as the
    /// other setters: `has_one = admin_authority` on the state plus a signer,
    /// and `admin_authority` is the governance authority PDA, so this is
    /// reachable only from an approved proposal's CPI and from nothing else.
    ///
    /// WHY IT MATTERS: without a rotate path the operational key would be a
    /// key that can never be replaced — a lost or leaked one would leave the
    /// treasury permanently unusable (or usable by the wrong holder) with no
    /// on-chain remedy. With it, the blast radius of a compromised operating
    /// key is one proposal, not the deployment.
    pub fn set_operational_authority(
        ctx: Context<SetOperationalAuthority>,
        new_authority: Pubkey,
    ) -> Result<()> {
        let state = &mut ctx.accounts.treasury_state;
        state.operational_authority = new_authority;
        Ok(())
    }

    /// FORWARD THE CONVERTIBLE SHARE TO THE JACKPOT — the treasury -> jackpot
    /// CPI, and the only instruction on Solana that can fund the jackpot.
    ///
    /// It is a cross-program call, not a bookkeeping entry: the xmrj_jackpot
    /// program does not accept a funding instruction from anyone else (its
    /// `FundJackpot` demands a SIGNER for a PDA derived under this program's
    /// pinned id — see JACKPOT_FUNDER_SEED), and this program is the only
    /// thing that can sign for that PDA. Both halves are needed and neither is
    /// sufficient: the jackpot's constraint is what makes the guarantee, and
    /// this instruction is what makes the guarantee usable.
    ///
    /// THE BOUND: what may be forwarded is what the treasury has RECORDED as
    /// convertible (`total_converted_xmrj`, accumulated by
    /// record_inflow_xmrj from the jackpot_bps share of every inflow), and only
    /// once — `total_jackpot_funded_xmr` accumulates what has already gone out
    /// and shrinks what remains. So this instruction can never mint jackpot
    /// balance out of nothing, and a repeated call cannot forward the same
    /// recorded amount twice. That bound is the reason this can be an
    /// operational call rather than a governance one.
    ///
    /// AUTHORITY — NOW ENFORCED, NOT JUST RECORDED: the caller must be the
    /// state's `operational_authority`, demanded as a SIGNER by
    /// FundJackpotWithXmr's `has_one` + `signer` pair. The open item this
    /// closes said the fix had to be an operational key recorded in the state
    /// rather than `admin_authority` — that authority is the governance
    /// authority PDA and the proposal PDA is a singleton (XMRJ_SOLANA_
    /// DEPLOYMENT_PLAN.md 9.3), so gating funding on it would make funding a
    /// once-per-cluster act. The key is seeded by the deployer at initialize
    /// and rotated by an approved proposal (set_operational_authority).
    ///
    /// The recorded-share bound below still stands on its own: even the
    /// operational key can never forward more than the treasury has already
    /// recorded as convertible, and never the same recorded amount twice.
    pub fn fund_jackpot_with_xmr(ctx: Context<FundJackpotWithXmr>, amount_xmr: u64) -> Result<()> {
        let state = &mut ctx.accounts.treasury_state;

        // THE CONVERSION — the fix for F2. The recorded convertible share is
        // denominated in XMRJ; the amount this instruction forwards is XMR.
        // Comparing the two 1:1 (which is what this did while
        // `exchange_rate_bps` was written by two instructions and read by
        // none) made the on-chain bound independent of the rate the off-chain
        // service converts at, and made a proposal "changing the rate" change
        // nothing on chain. The same arithmetic, in integers, in the SAME
        // direction as xmrj_conversion_service (which is the module that
        // defines the rate's unit — `xmr_amount = xmrj_amount *
        // exchange_rate_bps / 10 000`, so 10 000 means 1 XMRJ = 1 XMR):
        //
        //     convertible_xmr = total_converted_xmrj * exchange_rate_bps / 10 000
        //
        // CUMULATIVE, not per-call: the XMR reachable from everything recorded
        // so far, minus everything already forwarded. Converting each call in
        // isolation would let a caller shave the remainder instead of
        // forwarding it.
        let rate = state.exchange_rate_bps as u128;
        // A zero rate would make every recorded share worth zero XMR — funding
        // permanently bricked. initialize and set_exchange_rate refuse it
        // where it is set; refused here too rather than left to arithmetic.
        require!(rate > 0, ErrorCode::InvalidExchangeRate);

        let convertible_xmr = (state.total_converted_xmrj as u128)
            .checked_mul(rate)
            .ok_or(ErrorCode::Overflow)?
            / 10_000;
        let already_forwarded = state.total_jackpot_funded_xmr as u128;

        // SATURATING, not checked: a proposal that LOWERS the rate lowers this
        // ceiling, and a treasury that has already forwarded more than the new
        // ceiling computes may then subtract into negatives. Refusing every
        // further amount is the right answer — the bound is exhausted — and it
        // is reported as `ExceedsConvertibleShare` below. A subtraction that
        // returned `Overflow` would name the wrong cause.
        let forwardable = convertible_xmr.saturating_sub(already_forwarded);
        require!(
            (amount_xmr as u128) <= forwardable,
            ErrorCode::ExceedsConvertibleShare
        );

        state.total_jackpot_funded_xmr = state
            .total_jackpot_funded_xmr
            .checked_add(amount_xmr)
            .ok_or(ErrorCode::Overflow)?;

        // The two seeds this program signs with: its own funder PDA, derived
        // under its own id. `ctx.bumps` is the canonical bump Anchor just
        // verified the account against, so the signed address is the same one
        // the jackpot's constraint recomputes.
        let funder_bump = [ctx.bumps.jackpot_funder];
        let signer_seeds: &[&[&[u8]]] = &[&[JACKPOT_FUNDER_SEED, funder_bump.as_ref()]];

        // MIGRATION NOTE (Anchor 1.x — UNCOMPILED here: no Rust or Anchor
        // toolchain exists in this environment). 1.0.0 REMOVED the program
        // AccountInfo from CpiContext, because it was a redundant copy of an
        // account already in the Accounts struct: the first argument is the
        // program's Pubkey now (the generated call reads `ctx.program_id` and
        // passes `ctx.to_account_infos()` — an AccountInfo here would not even
        // compile). The account is still REQUIRED in the struct and still
        // validated: `jackpot_program: Program<'info, XmrjJackpot>` above
        // checks it against the callee's declared id, and it has to be in this
        // instruction's account list for the runtime to reach it at all.
        xmrj_jackpot::cpi::fund_jackpot(
            CpiContext::new_with_signer(
                XmrjJackpot::id(),
                xmrj_jackpot::cpi::accounts::FundJackpot {
                    jackpot_state: ctx.accounts.jackpot_state.to_account_info(),
                    funder: ctx.accounts.jackpot_funder.to_account_info(),
                },
                signer_seeds,
            ),
            amount_xmr,
        )?;

        Ok(())
    }
}

/// REQUIRE THAT `signer` IS THIS PROGRAM'S UPGRADE AUTHORITY.
///
/// `program_data` is the ProgramData account the `Initialize` constraint
/// derives as `[crate::ID]` under UPGRADEABLE_LOADER_ID — the loader's own
/// rule — so it is this program's own account and not a lookalike. This function then
/// proves three things about the BYTES the loader wrote:
///
/// 1. the loader owns the account (`owner == UPGRADEABLE_LOADER_ID`);
/// 2. it holds a `ProgramData` value, not a `Buffer` or an uninitialized
///    account that happens to sit at that address;
/// 3. the authority option is `Some` and equals the signer — an immutable
///    program (`None`) has no authority to gate on, so it is refused with its
///    own error rather than quietly accepted.
///
/// The check is a READ of runtime-owned state, not a caller-supplied value, so
/// there is nothing here for a caller to forge. UNCOMPILED (no Rust/Anchor
/// toolchain in this environment) — `try_borrow_data` returns a Ref, and the
/// slice indexing below is what the loader's own deserializer does.
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
pub struct TreasuryState {
    pub total_inflow_xmrj: u64,
    pub total_reserve_xmrj: u64,
    pub total_converted_xmrj: u64,
    /// THE XMR/XMRJ RATE the recorded convertible share is valued at, in
    /// basis points: 10 000 means 1 XMRJ = 1 XMR (the par the runbook deploys
    /// at). WRITTEN by initialize and by set_exchange_rate (admin-gated, so by
    /// an approved proposal), and READ by fund_jackpot_with_xmr — where it
    /// decides how much XMR the recorded convertible share is worth, and
    /// therefore how much may be forwarded. Zero is refused: it would value
    /// every share at zero and brick funding.
    pub exchange_rate_bps: u64,
    pub reserve_bps: u64,
    pub jackpot_bps: u64,
    pub admin_authority: Pubkey,
    /// What has already been forwarded to the jackpot by
    /// fund_jackpot_with_xmr. Appended after admin_authority, so no existing
    /// field moves, and it is what bounds funding to the RECORDED convertible
    /// share — see that instruction. TREASURY_STATE_SPACE follows this layout
    /// (the wiring drive recomputes the arithmetic from the fields).
    pub total_jackpot_funded_xmr: u64,
    /// THE OPERATIONAL AUTHORITY — the ONE key that may record inflows and
    /// trigger jackpot funding. Appended after total_jackpot_funded_xmr, so no
    /// existing field moves.
    ///
    /// It is a plain keypair-held address rather than a PDA because both of
    /// its callers must SIGN: a PDA cannot sign a transaction a human or a bot
    /// submits. The security comes from the constraints on those instructions
    /// (this exact address, as a signer) plus the rotate path
    /// (set_operational_authority, admin-gated through a governance proposal),
    /// not from the address being unguessable.
    pub operational_authority: Pubkey,
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
    /// CHECK: THIS PROGRAM'S ProgramData account on the upgradeable loader,
    /// derived here from `[<this program id>]` under the
    /// LOADER's id — so the address is fixed and a lookalike account cannot be
    /// passed. It is read, not written: `require_upgrade_authority` checks the
    /// loader owns it, that it is in the ProgramData variant, and that its
    /// recorded upgrade authority is the payer.
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
pub struct RecordInflowXmrj<'info> {
    #[account(
        mut,
        seeds = [b"treasury_state"],
        bump,
        has_one = operational_authority
    )]
    pub treasury_state: Account<'info, TreasuryState>,
    /// The state's own operational authority, demanded as a SIGNER: the
    /// `has_one` above is what makes the address checked rather than supplied,
    /// and this is what makes it unable to be merely relayed by a third party.
    /// CHECK: nothing is deserialized from this account. `#[account(signer)]`
    /// makes it the key that must sign, and the state's
    /// `has_one = operational_authority` is what binds that key to the state.
    #[account(signer)]
    pub operational_authority: AccountInfo<'info>,
}

#[derive(Accounts)]
pub struct SetSplitRatio<'info> {
    #[account(mut, seeds = [b"treasury_state"], bump, has_one = admin_authority)]
    pub treasury_state: Account<'info, TreasuryState>,
    /// CHECK: an account key, not an account body. Bound by the state's
    /// `has_one = admin_authority` above and demanded as a SIGNER here,
    /// so the value is checked by the derive rather than read through a type.
    #[account(signer)]
    pub admin_authority: AccountInfo<'info>,
}

#[derive(Accounts)]
pub struct SetExchangeRate<'info> {
    #[account(mut, seeds = [b"treasury_state"], bump, has_one = admin_authority)]
    pub treasury_state: Account<'info, TreasuryState>,
    /// CHECK: an account key, not an account body. Bound by the state's
    /// `has_one = admin_authority` above and demanded as a SIGNER here,
    /// so the value is checked by the derive rather than read through a type.
    #[account(signer)]
    pub admin_authority: AccountInfo<'info>,
}

#[derive(Accounts)]
pub struct SetOperationalAuthority<'info> {
    #[account(mut, seeds = [b"treasury_state"], bump, has_one = admin_authority)]
    pub treasury_state: Account<'info, TreasuryState>,
    /// CHECK: an account key, not an account body. Bound by the state's
    /// `has_one = admin_authority` above and demanded as a SIGNER here,
    /// so the value is checked by the derive rather than read through a type.
    #[account(signer)]
    pub admin_authority: AccountInfo<'info>,
}

#[derive(Accounts)]
pub struct FundJackpotWithXmr<'info> {
    #[account(
        mut,
        seeds = [b"treasury_state"],
        bump,
        has_one = operational_authority
    )]
    pub treasury_state: Account<'info, TreasuryState>,
    /// CHECK: the operational authority of the state above — the `has_one`
    /// pair (address) and this one (signature) are together the caller gate.
    /// Nothing is read from the account; its signature is the payload.
    #[account(signer)]
    pub operational_authority: AccountInfo<'info>,
    /// CHECK: the JACKPOT's state account, owned by the jackpot program — this
    /// program cannot deserialize it and does not need to: the callee
    /// validates its own state (including its PDA seed) inside the CPI, and a
    /// wrong account fails there. Passed mut because the CPI writes it.
    #[account(mut)]
    pub jackpot_state: UncheckedAccount<'info>,
    /// CHECK: the funder PDA this program signs for, derived under its OWN id
    /// (no `seeds::program` — our id is the default). The jackpot derives the
    /// same address under the PINNED treasury id and demands a signature; this
    /// constraint is what guarantees the address we put `invoke_signed` behind
    /// is exactly the one the callee is checking.
    #[account(seeds = [JACKPOT_FUNDER_SEED], bump)]
    pub jackpot_funder: UncheckedAccount<'info>,
    /// The jackpot program itself. The `Program<XmrjJackpot>` marker makes the
    /// CPI target checked rather than chosen: anchor compares this account
    /// against the callee's declare_id!, so the call can never be aimed at an
    /// attacker's program that happens to expose the same instruction name.
    pub jackpot_program: Program<'info, XmrjJackpot>,
}

#[error_code]
pub enum ErrorCode {
    #[msg("Overflow in arithmetic operation")]
    Overflow,
    #[msg("Invalid split ratio; reserve_bps + jackpot_bps must equal 10_000")]
    InvalidSplitRatio,
    #[msg("Amount exceeds the convertible share the treasury has recorded")]
    ExceedsConvertibleShare,
    #[msg("Exchange rate must be greater than zero")]
    InvalidExchangeRate,
    #[msg("The program_data account is not this program's ProgramData account")]
    NotProgramData,
    #[msg("This program has no upgrade authority (it is immutable); initialize before finalizing it")]
    ProgramIsImmutable,
    #[msg("Only this program's upgrade authority may initialize it")]
    NotUpgradeAuthority,
}
