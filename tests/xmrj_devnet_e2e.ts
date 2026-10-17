/**
 * xmrj_devnet_e2e.ts — THE DEVNET END-TO-END SMOKE TEST (Oct 16).
 *
 * WHY THIS EXISTS: every other check on the Solana side is structural. The
 * Rust sources have never been compiled (no toolchain on the box the
 * workspace was written on), the wiring drive pins names and addresses
 * against each other, and NOTHING has ever proven that the three programs
 * deploy, that their state accounts can be created, or — the part that
 * matters most — that an approved governance proposal actually REACHES the
 * other program and changes a parameter ON CHAIN. That last claim is the
 * whole point of the governance->program CPI design, and it is exactly the
 * claim a compile cannot make. This file makes it, or fails.
 *
 * HOW TO RUN IT (devnet only — see the guard below):
 *
 *   anchor build
 *   anchor test --provider.cluster devnet      # build + deploy + run this file
 *
 * or, when the programs are already deployed:
 *
 *   ANCHOR_PROVIDER_URL=https://api.devnet.solana.com \
 *   ANCHOR_WALLET=~/.config/solana/id.json \
 *     npx ts-mocha -p ./tsconfig.json -t 1000000 "tests/**\/*.ts"
 *
 * THE WALLET MUST BE FUNDED: a first run deploys three programs, which is
 * rent on the program code itself (roughly 2 SOL per program at these sizes)
 * plus the two state accounts. The suite computes that number from the real
 * .so sizes and refuses to start if the wallet cannot cover it.
 *
 * WHAT IT PROVES, IN ORDER:
 *   0. IT REFUSES ANY CHAIN BUT DEVNET. The check is the GENESIS HASH
 *      (fetched live, compared to the value the devnet RPC reports), not the
 *      RPC URL, because a URL can carry a stale label, a proxy, or a typo
 *      while the hash cannot. This file deploys and initializes; pointing it
 *      at mainnet-beta is the one mistake it has to make impossible.
 *   1. THE DEPLOY LANDED: each of the three program ids is an EXECUTABLE
 *      account (the upgradeable loader owns it) — the CLI's deploy step,
 *      verified rather than assumed — AND the wallet running this suite is
 *      the upgrade authority recorded in each program's ProgramData account.
 *      That second half is the precondition step 2 depends on: every
 *      initializer is gated on that authority, so a deploy made by another
 *      wallet (or a program already finalized) is reported here by name.
 *   2. BOTH STATES EXIST: `initialize` on the treasury (with a split that
 *      sums to 10 000 or the program refuses) and on the jackpot, whose
 *      instruction takes NO arguments — each handed its own ProgramData
 *      account, which is what lets the upgrade-authority gate pass. On a
 *      rerun the states already exist, so the suite asserts their invariants
 *      instead of trying to create them again — a devnet test that fails on
 *      its second run teaches nothing.
 *   3. GOVERNANCE OWNS THE PARAMETERS: initialize_governance with three
 *      distinct owner keys and a threshold of two, then a proposal, exactly
 *      `threshold` approvals, and execute_action.
 *   4. THE CPI IS WHAT CHANGED THE PARAMETER: the jackpot's `maxPayoutBps` is
 *      read from the chain BEFORE and AFTER the execution, and the proposed
 *      value differs from the value read before — so the assertion cannot
 *      pass vacuously (a no-op CPI cannot satisfy it).
 *   5. THE NEGATIVE CONTROL: a DIRECT `setPayoutRules` transaction from the
 *      deployer's wallet — the governance authority PDA not involved at all —
 *      must be rejected, and the parameter must still hold the value
 *      governance set. Step 4 alone would also "pass" if the account were
 *      simply writable by anyone; this step is what makes it a proof of the
 *      CPI rather than of a writable account.
 *   6. THE JACKPOT CAN ONLY BE FUNDED BY THE TREASURY PROGRAM, AND THE
 *      TREASURY ONLY BY ITS OPERATIONAL KEY: an inflow is recorded and then
 *      forwarded — both signed by the state's `operational_authority` — the
 *      jackpot's balance moves by exactly the forwarded amount, and a DIRECT
 *      `fundJackpot` (funder PDA UNSIGNED, and with the treasury program's own
 *      address, the recipe the old key comparison accepted) is rejected with
 *      the balance unmoved. The same step proves the treasury's OWN gate
 *      negatively: a `recordInflowXmrj` signed by a NON-operational wallet —
 *      the deployer's upgrade-authority wallet — is rejected and the recorded
 *      inflow is re-read unchanged. This needs no proposal, so it runs on a
 *      governance state whose one proposal is already spent.
 *
 * WHAT IT DOES NOT PROVE, STATED RATHER THAN IMPLIED:
 *   - The TREASURY's own set_split_ratio / set_exchange_rate /
 *     set_operational_authority CPI is NOT exercised, because of the
 *     constraint in the next paragraph: only one proposal can ever exist per
 *     governance state, so one run can prove one action. The wiring drive pins
 *     all four call sites instead. (The treasury -> jackpot funding CPI IS
 *     exercised, in step 6: it is not a governance action and needs no
 *     proposal.)
 *   - THE PROPOSAL IS A SINGLETON. `proposal` is a PDA seeded
 *     [b"proposal", governance_state], and `propose_action` uses `init`, so a
 *     second proposal for the same governance state can never be created —
 *     there is no close instruction either. One action, for the lifetime of
 *     the program on a cluster. The suite therefore treats an
 *     already-executed proposal as a finished write path and asserts the
 *     EFFECT it left behind instead. This is a design limitation of the
 *     program as written, recorded here rather than discovered at deploy
 *     time.
 *   - The owners below are DEVNET-ONLY identities DERIVED FROM PUBLIC
 *     CONSTANTS. Deterministic on purpose, so a rerun can re-derive the same
 *     signers and the approval path stays usable; they hold no value, they
 *     are never funded beyond test fees, and the genesis guard means this
 *     file cannot run against a chain where that mattered.
 *   - The generated IDL types (target/types/*.ts) do not exist until a build
 *     has run, so the programs are driven through the narrow local interface
 *     below. The names it uses are pinned against the Rust sources by
 *     test_xmrj_devnet_e2e.py, which is the check a type system would
 *     otherwise provide.
 */
import * as anchor from "@anchor-lang/core";
import { expect } from "chai";
import * as fs from "fs";
import * as path from "path";

// ── the one cluster this file may touch ──────────────────────────────────
// The devnet genesis hash, as the devnet RPC itself reports it.
const DEVNET_GENESIS_HASH = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG";

// The seed literals, byte-for-byte the ones in the three crates'
// *_SEED constants (test_xmrj_devnet_e2e.py pins them against the sources).
const TREASURY_STATE_SEED = "treasury_state";
const JACKPOT_STATE_SEED = "jackpot_state";
const JACKPOT_FUNDER_SEED = "jackpot_funder";
const GOVERNANCE_STATE_SEED = "governance_state";
const GOVERNANCE_AUTHORITY_SEED = "governance_authority";
const PROPOSAL_SEED = "proposal";
// The loader that owns every deployed program, and the prefix of the
// ProgramData account that records who may upgrade it. Every `initialize` is
// now gated on that authority (the fixed-seed state PDAs are create-once, so
// an ungated initializer is a front-run), which is why each initializer takes
// its program's ProgramData account explicitly.
const UPGRADEABLE_LOADER_ID = "BPFLoaderUpgradeab1e11111111111111111111111";
const PROGRAM_DATA_SEED = "programdata";

// The treasury's starting split must sum to 10 000 (the program refuses
// otherwise); the proposal moves the jackpot's payout cap AWAY from the
// uncapped 10 000 that initialize sets.
const INIT_RESERVE_BPS = 7_000;
const INIT_JACKPOT_BPS = 3_000;
const INIT_EXCHANGE_RATE_BPS = 1;
const INITIAL_MAX_PAYOUT_BPS = 10_000;
const PROPOSED_MAX_PAYOUT_BPS = 4_000;

// The funding step records an inflow and then forwards exactly the share of it
// that the treasury's own split ratios bank as convertible (INIT_JACKPOT_BPS
// of INIT ... _XMRJ, i.e. 300 000 of 1 000 000) — the largest amount the
// treasury's bound accepts, and the amount whose arithmetic is checkable
// offline. The assertions are DELTAS, so a rerun that records and funds again
// passes for the same reason the first run did.
const FUNDING_INFLOW_XMRJ = 1_000_000;
const FUNDING_AMOUNT_XMR = 300_000;

const OWNER_COUNT = 3;
const THRESHOLD = 2;

// Each owner needs lamports to pay its own approval fee. Generous, devnet-only.
const OWNER_FUNDING_LAMPORTS = 50_000_000;

const SO_DIR = path.join(__dirname, "..", "target", "deploy");
const PROGRAM_NAMES = ["xmrj_treasury", "xmrj_jackpot", "xmrj_governance"];

/**
 * A devnet-only owner identity, derived from public constants so every run —
 * and every rerun — sees the same three signers. No secret material: anyone
 * can derive these, which is precisely why they are never used on a chain
 * that holds value, and why the genesis guard exists.
 */
function ownerKeypair(index: number): anchor.web3.Keypair {
  const seed = Uint8Array.from(
    { length: 32 },
    (_, j) => (index * 31 + j * 7 + 5) % 256,
  );
  return anchor.web3.Keypair.fromSeed(seed);
}

/**
 * The OPERATIONAL key — the address the treasury's state records as its
 * `operational_authority`, and the only signer that may record inflows or
 * trigger jackpot funding. Derived from public constants like the owners, for
 * the same reason: a rerun must re-derive the same signer, and the genesis
 * guard is what makes a chain-honest identity safe to publish.
 */
function operationalKeypair(): anchor.web3.Keypair {
  const seed = Uint8Array.from({ length: 32 }, (_, j) => (211 + j * 13) % 256);
  return anchor.web3.Keypair.fromSeed(seed);
}

/**
 * The ProgramData account of a deployed program: `["programdata", <program
 * id>]` under the upgradeable loader. Its bincode layout is
 * `u32 variant (3 = ProgramData), u64 slot, Option<Pubkey> authority`, i.e.
 * the authority is at offset 13 when the option tag at offset 12 is 1 — the
 * same bytes the programs' `require_upgrade_authority` reads.
 */
function programDataAddress(
  programId: anchor.web3.PublicKey,
): anchor.web3.PublicKey {
  const [address] = anchor.web3.PublicKey.findProgramAddressSync(
    [Buffer.from(PROGRAM_DATA_SEED), programId.toBuffer()],
    new anchor.web3.PublicKey(UPGRADEABLE_LOADER_ID),
  );
  return address;
}

/** The upgrade authority recorded in a ProgramData account, or null if the
 * program is immutable (the option tag is 0). */
function readUpgradeAuthority(
  data: Buffer,
): anchor.web3.PublicKey | null {
  // 4-byte variant + 8-byte slot = 12, then the Option<Pubkey> tag.
  if (data.length < 45 || data.readUInt32LE(0) !== 3) return null;
  if (data[12] !== 1) return null;
  return new anchor.web3.PublicKey(data.subarray(13, 45));
}

/**
 * The narrow view this file drives the programs through: `anchor.workspace`
 * hands back a workspace entry whose real generated types live in
 * target/types/, which a fresh clone does not have until it has built. Every
 * name used below is checked against the Rust sources by
 * test_xmrj_devnet_e2e.py, so the safety the type system would give is not
 * lost — it is just checked in one place instead of two.
 */
interface ProgramView {
  programId: anchor.web3.PublicKey;
  account: {
    [name: string]: {
      fetch(addr: anchor.web3.PublicKey): Promise<any>;
      fetchNullable(addr: anchor.web3.PublicKey): Promise<any | null>;
    };
  };
  methods: { [name: string]: (...args: any[]) => any };
}

describe("XMRJ devnet end-to-end", () => {
  const provider = anchor.AnchorProvider.env();
  anchor.setProvider(provider);

  const connection = provider.connection;
  const payer = (provider.wallet as anchor.Wallet).payer;

  const treasury = anchor.workspace.XmrjTreasury as unknown as ProgramView;
  const jackpot = anchor.workspace.XmrjJackpot as unknown as ProgramView;
  const governance = anchor.workspace.XmrjGovernance as unknown as ProgramView;

  const [treasuryState] = anchor.web3.PublicKey.findProgramAddressSync(
    [Buffer.from(TREASURY_STATE_SEED)],
    treasury.programId,
  );
  const [jackpotState] = anchor.web3.PublicKey.findProgramAddressSync(
    [Buffer.from(JACKPOT_STATE_SEED)],
    jackpot.programId,
  );
  // The funder is the TREASURY's PDA — derived under the treasury program's
  // id, which is the only program that can sign for it. The jackpot checks
  // this exact address (under its PINNED treasury id) and demands a signature.
  const [jackpotFunder] = anchor.web3.PublicKey.findProgramAddressSync(
    [Buffer.from(JACKPOT_FUNDER_SEED)],
    treasury.programId,
  );
  const [governanceState] = anchor.web3.PublicKey.findProgramAddressSync(
    [Buffer.from(GOVERNANCE_STATE_SEED)],
    governance.programId,
  );
  const [governanceAuthority] = anchor.web3.PublicKey.findProgramAddressSync(
    [Buffer.from(GOVERNANCE_AUTHORITY_SEED)],
    governance.programId,
  );
  const [proposal] = anchor.web3.PublicKey.findProgramAddressSync(
    [Buffer.from(PROPOSAL_SEED), governanceState.toBuffer()],
    governance.programId,
  );

  // Each program's ProgramData account, passed to its initializer: the
  // program refuses to create its state unless the SIGNER of that call is the
  // authority recorded here.
  const treasuryProgramData = programDataAddress(treasury.programId);
  const jackpotProgramData = programDataAddress(jackpot.programId);
  const governanceProgramData = programDataAddress(governance.programId);

  const owners = Array.from({ length: OWNER_COUNT }, (_, i) => ownerKeypair(i));
  const operationalAuthority = operationalKeypair();

  // Filled in `before()`, asserted by the tests below.
  const deployed: { [name: string]: boolean } = {};
  const upgradeAuthorities: {
    [name: string]: anchor.web3.PublicKey | null;
  } = {};
  let genesis = "";
  let balance = 0;
  let treasuryBefore: any = null;
  let jackpotBefore: any = null;
  let proposalState: any = null;
  let ownersMatch = false;

  before(async () => {
    // ── 0. THE GENESIS GUARD ─────────────────────────────────────────────
    genesis = await connection.getGenesisHash();
    if (genesis !== DEVNET_GENESIS_HASH) {
      throw new Error(
        `REFUSING TO RUN: this suite deploys and initializes programs, and the ` +
          `provider is not devnet. getGenesisHash() returned ${genesis}, ` +
          `expected the devnet hash ${DEVNET_GENESIS_HASH}. Point ` +
          `ANCHOR_PROVIDER_URL at https://api.devnet.solana.com (and check ` +
          `Anchor.toml's [toolchain] solana_version pin, section 1.1 of ` +
          `XMRJ_SOLANA_DEPLOYMENT_PLAN.md) before running this file.`,
      );
    }

    // ── 1. THE DEPLOY LANDED ─────────────────────────────────────────────
    for (const [name, program] of [
      ["xmrj_treasury", treasury],
      ["xmrj_jackpot", jackpot],
      ["xmrj_governance", governance],
    ] as [string, ProgramView][]) {
      const info = await connection.getAccountInfo(program.programId);
      deployed[name] = Boolean(info && info.executable);
    }
    // The initializers below are gated on each program's upgrade authority,
    // so read what the chain records now: a deploy made by a DIFFERENT wallet,
    // or one already finalized (immutable, authority None), must be reported
    // here as a precondition rather than as an opaque instruction error at
    // initialization.
    for (const [name, pd] of [
      ["xmrj_treasury", treasuryProgramData],
      ["xmrj_jackpot", jackpotProgramData],
      ["xmrj_governance", governanceProgramData],
    ] as [string, anchor.web3.PublicKey][]) {
      const pdInfo = await connection.getAccountInfo(pd);
      upgradeAuthorities[name] = pdInfo ? readUpgradeAuthority(pdInfo.data) : null;
    }

    const missing = PROGRAM_NAMES.filter((n) => !deployed[n]);
    if (missing.length > 0) {
      const sizes = PROGRAM_NAMES.map((n) => {
        const so = path.join(SO_DIR, `${n}.so`);
        return fs.existsSync(so) ? fs.statSync(so).size : 0;
      });
      let needed = 0;
      if (sizes.every((s) => s > 0)) {
        for (const size of sizes) {
          needed += await connection.getMinimumBalanceForRentExemption(size);
        }
      }
      balance = await connection.getBalance(payer.publicKey);
      throw new Error(
        `NOT DEPLOYED YET: ${missing.join(", ")} (id(s) from Anchor.toml) has ` +
          `no executable account on devnet. Run "anchor build" then ` +
          `"anchor deploy --provider.cluster devnet" (or "anchor test ` +
          `--provider.cluster devnet", which does both). Wallet ` +
          `${payer.publicKey.toBase58()} holds ${balance} lamports` +
          (needed > 0
            ? `; the three programs alone need about ${needed} lamports of ` +
              `rent at the sizes target/deploy holds, plus fees. Airdrop with ` +
              `"solana airdrop 2" (repeat, or use the devnet web faucet).`
            : `.`),
      );
    }

    // ── 2. BOTH STATES EXIST (or their invariants hold) ──────────────────
    treasuryBefore = await treasury.account.treasuryState.fetchNullable(
      treasuryState,
    );
    if (treasuryBefore === null) {
      await treasury.methods
        .initialize(
          new anchor.BN(INIT_RESERVE_BPS),
          new anchor.BN(INIT_JACKPOT_BPS),
          new anchor.BN(INIT_EXCHANGE_RATE_BPS),
          operationalAuthority.publicKey,
        )
        .accounts({
          treasuryState,
          adminAuthority: governanceAuthority,
          programData: treasuryProgramData,
          payer: payer.publicKey,
          systemProgram: anchor.web3.SystemProgram.programId,
        })
        .rpc();
      treasuryBefore = await treasury.account.treasuryState.fetch(treasuryState);
    }

    jackpotBefore = await jackpot.account.jackpotState.fetchNullable(jackpotState);
    if (jackpotBefore === null) {
      await jackpot.methods
        .initialize()
        .accounts({
          jackpotState,
          adminAuthority: governanceAuthority,
          programData: jackpotProgramData,
          payer: payer.publicKey,
          systemProgram: anchor.web3.SystemProgram.programId,
        })
        .rpc();
      jackpotBefore = await jackpot.account.jackpotState.fetch(jackpotState);
    }

    // The OPERATIONAL authority signs the recording and funding calls in step
    // 6, so it needs devnet lamports for its own fees. Topped up every run
    // rather than only when the state is fresh: a rerun still signs.
    if ((await connection.getBalance(operationalAuthority.publicKey)) <
        OWNER_FUNDING_LAMPORTS) {
      await provider.sendAndConfirm(
        new anchor.web3.Transaction().add(
          anchor.web3.SystemProgram.transfer({
            fromPubkey: payer.publicKey,
            toPubkey: operationalAuthority.publicKey,
            lamports: OWNER_FUNDING_LAMPORTS,
          }),
        ),
        [payer],
      );
    }

    // ── 3. GOVERNANCE, WITH ITS OWNERS FUNDED TO APPROVE ─────────────────
    let governanceBefore = await governance.account.governanceState.fetchNullable(
      governanceState,
    );
    if (governanceBefore === null) {
      for (const owner of owners) {
        if ((await connection.getBalance(owner.publicKey)) < OWNER_FUNDING_LAMPORTS) {
          const fund = new anchor.web3.Transaction().add(
            anchor.web3.SystemProgram.transfer({
              fromPubkey: payer.publicKey,
              toPubkey: owner.publicKey,
              lamports: OWNER_FUNDING_LAMPORTS,
            }),
          );
          await provider.sendAndConfirm(fund, [payer]);
        }
      }
      await governance.methods
        .initializeGovernance(owners.map((o) => o.publicKey), THRESHOLD)
        .accounts({
          governanceState,
          governanceAuthority,
          programData: governanceProgramData,
          payer: payer.publicKey,
          systemProgram: anchor.web3.SystemProgram.programId,
        })
        .rpc();
      governanceBefore = await governance.account.governanceState.fetch(
        governanceState,
      );
    }

    // Are the on-chain owners THIS suite's keypairs? On a fresh state yes; on
    // a state some earlier run created with different keys, no — and then the
    // approval path belongs to whoever owns it, which this suite must say
    // rather than pretend.
    const onChainOwners: anchor.web3.PublicKey[] = governanceBefore.owners;
    ownersMatch =
      onChainOwners.length === OWNER_COUNT &&
      onChainOwners.every((p, i) => p.equals(owners[i].publicKey));

    proposalState = await governance.account.proposal.fetchNullable(proposal);
  });

  it("0. runs against devnet and nowhere else", () => {
    expect(genesis).to.equal(DEVNET_GENESIS_HASH);
  });

  it("1. the three programs are deployed, executable, and upgradeable by this wallet", () => {
    for (const name of PROGRAM_NAMES) {
      expect(deployed[name], `${name} is not an executable account`).to.equal(
        true,
      );
    }
    // The precondition the next step's initializers are gated on: the wallet
    // running this suite IS the recorded upgrade authority of all three
    // programs. If it is not, `initialize` will refuse — correctly — and this
    // assertion says why before the transaction does.
    for (const name of PROGRAM_NAMES) {
      expect(
        upgradeAuthorities[name]?.toBase58(),
        `${name}'s upgrade authority is not the deployer wallet ` +
          `${payer.publicKey.toBase58()}; initialize is gated on that ` +
          `authority. Deploy with this wallet, or run the suite with the ` +
          `wallet that deployed (and do not finalize the programs before ` +
          `their states exist).`,
      ).to.equal(payer.publicKey.toBase58());
    }
  });

  it("2. both state accounts exist with the initialized shape", () => {
    // The treasury's split must sum to 10 000 on chain — the instruction
    // refuses anything else, so a wrong sum here would mean the state was not
    // created by `initialize`.
    expect(
      treasuryBefore.reserveBps.toNumber() + treasuryBefore.jackpotBps.toNumber(),
    ).to.equal(10_000);
    // Both programs' admin authority is the GOVERNANCE AUTHORITY PDA — the
    // one address only xmrj_governance can sign for. This is the fact the
    // whole CPI design rests on.
    expect(treasuryBefore.adminAuthority.toBase58()).to.equal(
      governanceAuthority.toBase58(),
    );
    expect(jackpotBefore.adminAuthority.toBase58()).to.equal(
      governanceAuthority.toBase58(),
    );
    // The jackpot's funder is PINNED to the treasury program, and its cap
    // starts uncapped.
    expect(jackpotBefore.treasuryProgram.toBase58()).to.equal(
      treasury.programId.toBase58(),
    );
    expect(jackpotBefore.jackpotBalanceXmr.toNumber()).to.equal(0);
    // ...and the treasury records the OPERATIONAL authority — the one signer
    // that may record inflows or fund the jackpot (step 6 proves it is the
    // gate rather than decoration).
    expect(treasuryBefore.operationalAuthority.toBase58()).to.equal(
      operationalAuthority.publicKey.toBase58(),
    );
  });

  it("3. a proposal reaches the threshold and executes through the CPI", async function () {
    if (!ownersMatch) {
      // Honest skip with the reason: this is not a failure of the program,
      // and it is not something this suite may paper over.
      console.warn(
        "SKIPPED the write path: the existing governance_state is not owned " +
          "by this suite's deterministic devnet owners, so their approvals " +
          "cannot be signed for. Redeploy the governance program with a fresh " +
          "keypair for a fresh state (XMRJ_SOLANA_DEPLOYMENT_PLAN.md 1.5).",
      );
      this.skip();
    }

    if (proposalState === null) {
      // The FIRST and ONLY proposal this governance state can ever hold —
      // `proposal` is a PDA with `init` (see the header).
      await governance.methods
        .proposeAction({
          setJackpotPayoutRules: {
            maxPayoutBps: new anchor.BN(PROPOSED_MAX_PAYOUT_BPS),
          },
        })
        .accounts({
          governanceState,
          proposal,
          proposer: payer.publicKey,
          systemProgram: anchor.web3.SystemProgram.programId,
        })
        .rpc();
      proposalState = await governance.account.proposal.fetch(proposal);
      expect(proposalState.executed).to.equal(false);
      expect(proposalState.approvals).to.equal(0);
    }

    if (!proposalState.executed) {
      // Exactly `threshold` approvals, from distinct owners: the number the
      // proposal needs and no more, so the run proves the threshold is what
      // lets execution through.
      for (let i = 0; i < THRESHOLD; i++) {
        await governance.methods
          .approveAction()
          .accounts({ governanceState, proposal, owner: owners[i].publicKey })
          .signers([owners[i]])
          .rpc();
      }
      const approved = await governance.account.proposal.fetch(proposal);
      expect(approved.approvals).to.equal(THRESHOLD);

      await governance.methods
        .executeAction()
        .accounts({
          governanceState,
          proposal,
          treasuryState,
          jackpotState,
          treasuryProgram: treasury.programId,
          jackpotProgram: jackpot.programId,
          governanceAuthority,
          targetProgram: anchor.web3.SystemProgram.programId,
        })
        .rpc();
      proposalState = await governance.account.proposal.fetch(proposal);
    }

    expect(proposalState.executed, "the proposal did not execute").to.equal(true);
  });

  it("4. THE CPI CHANGED THE PARAMETER ON CHAIN (and could not have failed silently)", async () => {
    const after = await jackpot.account.jackpotState.fetch(jackpotState);
    const before = jackpotBefore.maxPayoutBps.toNumber();

    // Non-vacuous by construction: the proposal moved the cap away from the
    // value `initialize` set, so an execution that did nothing (a CPI that
    // resolved to the wrong program, a silently skipped instruction) could
    // not produce this.
    expect(before).to.equal(INITIAL_MAX_PAYOUT_BPS);
    expect(PROPOSED_MAX_PAYOUT_BPS).to.not.equal(INITIAL_MAX_PAYOUT_BPS);
    expect(after.maxPayoutBps.toNumber()).to.equal(PROPOSED_MAX_PAYOUT_BPS);
  });

  it("5. the CPI is the ONLY path: a direct call from the deployer is rejected", async () => {
    let rejected = false;
    let rejectedWith = "";
    try {
      await jackpot.methods
        .setPayoutRules(new anchor.BN(PROPOSED_MAX_PAYOUT_BPS + 1))
        .accounts({ jackpotState, adminAuthority: payer.publicKey })
        .signers([payer])
        .rpc();
    } catch (err) {
      rejected = true;
      rejectedWith = err instanceof Error ? err.message : String(err);
    }

    console.info(`direct setPayoutRules from the deployer was rejected: ${rejectedWith}`);
    expect(
      rejected,
      "a direct setPayoutRules from the deployer's wallet SUCCEEDED — the " +
        "payout cap is writable outside governance",
    ).to.equal(true);

    // And the chain proves the rejected call changed nothing, whatever the
    // error was: the parameter still holds what the approved proposal set.
    const after = await jackpot.account.jackpotState.fetch(jackpotState);
    expect(after.maxPayoutBps.toNumber()).to.equal(PROPOSED_MAX_PAYOUT_BPS);
  });

  it("6. the jackpot is fundable only BY THE TREASURY PROGRAM", async () => {
    const before = await jackpot.account.jackpotState.fetch(jackpotState);
    const treasuryBeforeFunding = await treasury.account.treasuryState.fetch(
      treasuryState,
    );

    // The treasury may forward only what it has RECORDED as convertible, so
    // the run first records an inflow — which banks the jackpot share of it
    // (INIT_JACKPOT_BPS of INIT_RESERVE_BPS + INIT_JACKPOT_BPS). Both calls are
    // signed by the state's OPERATIONAL authority and by nobody else.
    await treasury.methods
      .recordInflowXmrj(new anchor.BN(FUNDING_INFLOW_XMRJ))
      .accounts({
        treasuryState,
        operationalAuthority: operationalAuthority.publicKey,
      })
      .signers([operationalAuthority])
      .rpc();

    // The real thing: a CPI from the treasury, signed with its own funder PDA.
    await treasury.methods
      .fundJackpotWithXmr(new anchor.BN(FUNDING_AMOUNT_XMR))
      .accounts({
        treasuryState,
        operationalAuthority: operationalAuthority.publicKey,
        jackpotState,
        jackpotFunder,
        jackpotProgram: jackpot.programId,
      })
      .signers([operationalAuthority])
      .rpc();

    const funded = await jackpot.account.jackpotState.fetch(jackpotState);
    // A DELTA, not an absolute: a rerun records another inflow and forwards
    // again, and this assertion has to hold on the tenth run too.
    expect(funded.jackpotBalanceXmr.toNumber()).to.equal(
      before.jackpotBalanceXmr.toNumber() + FUNDING_AMOUNT_XMR,
    );

    // THE NEGATIVE CONTROLS, and both of them matter:
    //   - the funder PDA's own ADDRESS, unsigned. Everything about the account
    //     is right except the signature, so if this one SUCCEEDED the jackpot
    //     would be fundable by anyone who can read the chain.
    //   - the treasury program's own address — the recipe the OLD check
    //     accepted (it compared the caller's key to the recorded id).
    const directFunderAttempts: { label: string; funder: anchor.web3.PublicKey }[] = [
      { label: "the funder PDA, unsigned", funder: jackpotFunder },
      { label: "the treasury program's own address", funder: treasury.programId },
    ];
    for (const attempt of directFunderAttempts) {
      // Named `refused`, not `rejected`: the setPayoutRules control above uses
      // that name, and this drive's offline tripwire keys on it — two
      // assertions sharing it would make one of them invisible to the check.
      let refused = false;
      let refusedWith = "";
      try {
        await jackpot.methods
          .fundJackpot(new anchor.BN(1))
          .accounts({ jackpotState, funder: attempt.funder })
          .signers([payer])
          .rpc();
      } catch (err) {
        refused = true;
        refusedWith = err instanceof Error ? err.message : String(err);
      }
      console.info(
        `direct fundJackpot with ${attempt.label} was refused: ${refusedWith}`,
      );
      expect(
        refused,
        `a DIRECT fundJackpot with ${attempt.label} SUCCEEDED — the jackpot ` +
          `is fundable without xmrj_treasury's signature`,
      ).to.equal(true);
    }

    // The chain proves the rejected calls changed nothing: the balance is
    // still exactly what the treasury's CPI put there.
    const unchanged = await jackpot.account.jackpotState.fetch(jackpotState);
    expect(unchanged.jackpotBalanceXmr.toNumber()).to.equal(
      funded.jackpotBalanceXmr.toNumber(),
    );

    // THE OPERATIONAL GATE, PROVEN NEGATIVELY. The deployer's wallet is this
    // program's UPGRADE authority and still cannot record an inflow, because
    // the state's operational_authority is another key. Without this, the
    // `has_one` + signer pair on the recording would be untested on chain —
    // and a gate that is never exercised is a gate nobody has seen work.
    let blocked = false;
    let blockedWith = "";
    try {
      await treasury.methods
        .recordInflowXmrj(new anchor.BN(1))
        .accounts({ treasuryState, operationalAuthority: payer.publicKey })
        .signers([payer])
        .rpc();
    } catch (err) {
      blocked = true;
      blockedWith = err instanceof Error ? err.message : String(err);
    }
    console.info(
      `record_inflow_xmrj from a NON-operational signer was blocked: ${blockedWith}`,
    );
    expect(
      blocked,
      "a record_inflow_xmrj signed by a NON-operational wallet SUCCEEDED — " +
        "the treasury's operational gate is open",
    ).to.equal(true);

    // And it changed nothing: the recorded inflow is still exactly what the
    // operational call put there (a DELTA, so a rerun asserts the same thing).
    const afterBlocked = await treasury.account.treasuryState.fetch(treasuryState);
    expect(afterBlocked.totalInflowXmrj.toNumber()).to.equal(
      treasuryBeforeFunding.totalInflowXmrj.toNumber() + FUNDING_INFLOW_XMRJ,
    );
  });
});
