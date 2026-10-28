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
 *      inflow is re-read unchanged. This needs no proposal at all.
 *   7. THE JACKPOT CAN BE PAID OUT — THE PATH FINDING F1 WAS ABOUT. A
 *      proposal carries `GovernanceAction::TriggerPayout`, and the effects are
 *      read back FROM THE JACKPOT: the balance down by exactly the amount,
 *      `total_payouts_xmr` up by the same, `last_winner` set, the identity
 *      `balance + total_payouts == total_funded` intact, and the TREASURY
 *      unmoved. Then the two controls that make it a proof rather than a demo:
 *      a DIRECT `triggerPayout` signed by the deployer's wallet is REFUSED
 *      (the stored admin authority is a PDA, and only governance can sign for
 *      it — so no off-path key can pay), and re-executing the approved payout
 *      proposal is REFUSED (no replay).
 *   8. THE EXCHANGE RATE IS A LIVE BOUND, NOT A DEAD FIELD (finding F2). At
 *      par (10 000 bps, i.e. 1 XMRJ = 1 XMR) the exact remaining convertible
 *      share is accepted and ONE UNIT MORE is refused; a proposal that HALVES
 *      the rate then makes that same accepted amount REFUSED, and a proposal
 *      that DOUBLES it makes it accepted again with the jackpot balance moving
 *      by it. A field no instruction read could not pass this.
 *
 * EVERY RUN MAKES FRESH PROPOSALS. `proposal` is a PDA seeded ["proposal",
 * governance_state, proposal_id], and `propose_action` requires the id to be
 * the next one in the state's sequence — so the payout and the rate changes
 * take a NEW id each run and their effects are asserted as DELTAS. That is what
 * makes a second run (and a tenth) meaningful: before finding F5 was fixed, a
 * governance state could execute exactly one action for the lifetime of the
 * program on a cluster.
 *
 * WHAT IT DOES NOT PROVE, STATED RATHER THAN IMPLIED:
 *   - The treasury's `set_split_ratio` and `set_operational_authority` CPIs are
 *     NOT exercised: the proposals this run spends are the payout cap, a
 *     payout, and two rate changes. Those two call sites are pinned
 *     structurally, offline, by test_xmrj_devnet_e2e.py and
 *     test_anchor_wiring.py 12.12/12.13 instead. (The treasury -> jackpot
 *     FUNDING CPI *is* exercised, in steps 6 and 8 — it is not a governance
 *     action and needs no proposal.)
 *   - A PAYOUT IS AN AUTHORIZATION AND AN ACCOUNTING ENTRY, NOT A TRANSFER.
 *     `trigger_payout` records the winner and moves the jackpot's own counters;
 *     the XMR itself lives on the Monero side and leaves the jackpot's wallet
 *     off chain. This suite proves WHO may pay and WHAT it does to the
 *     accounts, and says so rather than implying value moved.
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

import { makeProvider, providerUrl, resetRpcStats, rpcStats } from "../tools/rpc_resilience";

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

// The treasury's starting split must sum to 10 000 (the program refuses
// otherwise); the proposal moves the jackpot's payout cap AWAY from the
// uncapped 10 000 that initialize sets.
const INIT_RESERVE_BPS = 7_000;
const INIT_JACKPOT_BPS = 3_000;
// PAR — the rate the treasury is initialized at, in the unit the off-chain
// conversion service defines: xmr = xmrj * rate / 10 000, so 10 000 means
// 1 XMRJ = 1 XMR. It is 10 000 rather than an arbitrary value because since
// finding F2 was fixed the rate is READ on the funding path: it values the
// recorded convertible share, and therefore bounds what may be forwarded. Step
// 8 changes it in both directions and watches the bound move.
const INIT_EXCHANGE_RATE_BPS = 10_000;
// The rate the step-8 proposals move to: halving it halves what the recorded
// share is worth, doubling it doubles that.
const HALVED_RATE_BPS = 5_000;
const DOUBLED_RATE_BPS = 20_000;
const INITIAL_MAX_PAYOUT_BPS = 10_000;
const PROPOSED_MAX_PAYOUT_BPS = 4_000;
// The payout's size and its winner. The winner never signs: `trigger_payout`
// stores the address and moves the jackpot's counters, and the XMR itself is
// moved on the Monero side.
const PAYOUT_AMOUNT_XMR = 100_000;

// WHICH PROPOSAL ID CARRIES WHICH ACTION. The cap change has a fixed id (its
// effect is an absolute value, so a rerun asserts the same thing by reusing
// it); the payout and the two rate changes take the NEXT id on every run — see
// runNextProposal — because their effects are DELTAS and a rerun has to make the
// write again to assert them.
const PROPOSAL_PAYOUT_CAP = 0;

// THE ACTIONS THE SUITE PUTS THROUGH GOVERNANCE, as literals: the variant name
// and its field names ARE the borsh encoding, and test_xmrj_devnet_e2e.py pins
// every one of them against the Rust enum without a chain.
const ACTION_SET_PAYOUT_RULES = {
  setJackpotPayoutRules: { maxPayoutBps: new anchor.BN(PROPOSED_MAX_PAYOUT_BPS) },
};
// The address the payout records in `last_winner`. Only an ADDRESS is needed:
// nothing signs with this key (see winnerKeypair below).
const WINNER = winnerKeypair().publicKey;

const ACTION_TRIGGER_PAYOUT = {
  triggerPayout: { winner: WINNER, amountXmr: new anchor.BN(PAYOUT_AMOUNT_XMR) },
};
const ACTION_RATE_AT_PAR = {
  setExchangeRate: { exchangeRateBps: new anchor.BN(INIT_EXCHANGE_RATE_BPS) },
};
const ACTION_RATE_HALVED = {
  setExchangeRate: { exchangeRateBps: new anchor.BN(HALVED_RATE_BPS) },
};
const ACTION_RATE_DOUBLED = {
  setExchangeRate: { exchangeRateBps: new anchor.BN(DOUBLED_RATE_BPS) },
};

// The funding step records an inflow and then forwards exactly the share of it
// that the treasury's own split ratios bank as convertible (INIT_JACKPOT_BPS
// of INIT ... _XMRJ, i.e. 300 000 of 1 000 000) — the largest amount the
// treasury's bound accepts, and the amount whose arithmetic is checkable
// offline. The assertions are DELTAS, so a rerun that records and funds again
// passes for the same reason the first run did.
//
// THE INFLOW IS A MINIMUM, NOT THE WHOLE STORY. What the treasury may forward
// is CUMULATIVE state on a shared devnet — the stress harness records inflows
// and forwards against the same treasury_state, and its rate scenario restores
// the rate to par, which by the program's own documented rule (see
// `fund_jackpot_with_xmr`) LOWERS the ceiling below what a doubled-rate
// forwarding already pushed through. A treasury in that state refuses every
// amount with ExceedsConvertibleShare until enough inflow accrues, which is
// what stopped step 6 of this suite on the shared devnet on 2026-10-27 while
// every program was behaving exactly as designed. Step 6 therefore computes the
// inflow from the CHAIN — the shortfall it finds plus the amount it is about to
// forward — and only falls back to this constant when the chain needs no more.
const FUNDING_INFLOW_XMRJ = 1_000_000;
const FUNDING_AMOUNT_XMR = 300_000;

const OWNER_COUNT = 3;
const THRESHOLD = 2;

// Each owner needs lamports to pay its own approval fee. Generous, devnet-only.
const OWNER_FUNDING_LAMPORTS = 50_000_000;

// RESOLVED FROM THE PROJECT ROOT, not from `__dirname`: Node 24 loads a `.ts`
// file through its own type stripping when the runner imports it as ESM
// (ts-mocha does), and in that scope `__dirname` does not exist — the suite
// died at import time before a single assertion ran. Both `npm test` and
// `npm run loadtest` invoke their runner from the project root, so the cwd IS
// the root; the offline drive pins this path against the real .so files.
const SO_DIR = path.resolve(process.cwd(), "target", "deploy");
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
 * The payout's WINNER — a devnet-only identity derived the same way, and used
 * for its ADDRESS only. Nothing signs with it: `trigger_payout` stores it in
 * `last_winner` and moves the jackpot's counters.
 */
function winnerKeypair(): anchor.web3.Keypair {
  const seed = Uint8Array.from({ length: 32 }, (_, j) => (97 + j * 29) % 256);
  return anchor.web3.Keypair.fromSeed(seed);
}

/**
 * The ProgramData account of a deployed program, by the loader's own rule:
 * `[<program id>]` under the upgradeable loader — ONE seed, no string prefix
 * (a `"programdata"` prefix derives an address that has no account at it, and
 * `initialize` is then refused with `NotProgramData` 6004). Its bincode layout is
 * `u32 variant (3 = ProgramData), u64 slot, Option<Pubkey> authority`, i.e.
 * the authority is at offset 13 when the option tag at offset 12 is 1 — the
 * same bytes the programs' `require_upgrade_authority` reads.
 */
function programDataAddress(
  programId: anchor.web3.PublicKey,
): anchor.web3.PublicKey {
  const [address] = anchor.web3.PublicKey.findProgramAddressSync(
    [programId.toBuffer()],
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
  // THE PROVIDER IS BUILT HERE RATHER THAN BY `AnchorProvider.env()`, so the
  // suite uses the same RPC layer as the stress harness
  // (tools/rpc_resilience.ts): a throttled public endpoint is waited out instead
  // of ending the run, and the confirmation window is long enough to outlast a
  // throttled confirmation — the suite died on both of those on Oct 27
  // (`429 Too Many Requests`, then `TransactionExpiredTimeoutError: … 30.00
  // seconds`). The endpoint is XMRJ_RPC_URL ?? ANCHOR_PROVIDER_URL; nothing here
  // guesses a cluster.
  resetRpcStats();
  const { provider } = makeProvider();
  anchor.setProvider(provider);

  // What the layer absorbed, at the end of the run: a green suite on a busy
  // endpoint should show the throttling as work survived, not leave it implied.
  after(() => {
    console.info(`[rpc] ${rpcStats().summary}`);
  });

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
  /**
   * THE PROPOSAL PDA FOR ONE ID: ["proposal", governance_state,
   * proposal_id as an 8-byte little-endian u64] — the same three seeds
   * `ProposeAction`, `ApproveAction` and `ExecuteAction` each derive, so the id
   * an instruction names and the account it acts on cannot disagree. Before
   * finding F5 was fixed the third seed did not exist and a governance state
   * could hold exactly one proposal for the life of the program.
   */
  function proposalPda(proposalId: number): anchor.web3.PublicKey {
    const idBytes = Buffer.alloc(8);
    idBytes.writeBigUInt64LE(BigInt(proposalId));
    const [address] = anchor.web3.PublicKey.findProgramAddressSync(
      [Buffer.from(PROPOSAL_SEED), governanceState.toBuffer(), idBytes],
      governance.programId,
    );
    return address;
  }

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
    // The owners pay their OWN approval fees, and EVERY run needs approvals
    // (the payout and both rate changes are proposals), so they are topped up
    // every run rather than only when the state is fresh — a rerun that cannot
    // sign an approval would silently stop proving anything.
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

    if (governanceBefore === null) {
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
  });

  /**
   * TAKE ONE PROPOSAL THROUGH THE WHOLE PATH, BY ID: propose it if this
   * governance state has not made that id yet, approve it exactly `threshold`
   * times from distinct owners, execute it, and hand back the proposal account
   * so a caller can assert the EFFECT on the other program rather than the act
   * of executing.
   *
   * The id is what makes this usable more than once: proposals are PDAs seeded
   * ["proposal", governance_state, proposal_id] and `propose_action` requires
   * the id to be the NEXT one in the state's `proposal_count` sequence. Before
   * finding F5 was fixed there was one id-less proposal per cluster, which is
   * why this suite could only ever prove one governance action per run.
   *
   * RERUN SAFE: an id that already exists and already executed is returned
   * as-is, so a caller asserting an ABSOLUTE effect (the cap) reads the same
   * answer on the tenth run as on the first.
   */
  async function runProposal(
    proposalId: number,
    action: Record<string, unknown>,
    label: string,
  ): Promise<any> {
    const address = proposalPda(proposalId);
    let proposalState: any = await governance.account.proposal.fetchNullable(address);

    if (proposalState === null) {
      await governance.methods
        .proposeAction(new anchor.BN(proposalId), action)
        .accounts({
          governanceState,
          proposal: address,
          proposer: payer.publicKey,
          systemProgram: anchor.web3.SystemProgram.programId,
        })
        .rpc();
      proposalState = await governance.account.proposal.fetch(address);
      expect(
        proposalState.proposalId.toNumber(),
        `${label}: the proposal did not record the id it was created with`,
      ).to.equal(proposalId);
      expect(proposalState.executed, `${label}: a fresh proposal is not executed`).to.equal(
        false,
      );
      expect(proposalState.approvals, `${label}: a fresh proposal has no approvals`).to.equal(
        0,
      );
    }

    if (!proposalState.executed) {
      // Exactly `threshold` approvals, from distinct owners: the number the
      // proposal needs and no more, so the run proves the threshold is what
      // lets execution through.
      for (let i = 0; i < THRESHOLD; i++) {
        await governance.methods
          .approveAction(new anchor.BN(proposalId))
          .accounts({
            governanceState,
            proposal: address,
            owner: owners[i].publicKey,
          })
          .signers([owners[i]])
          .rpc();
      }
      const approved = await governance.account.proposal.fetch(address);
      expect(approved.approvals, `${label}: approvals`).to.equal(THRESHOLD);

      await governance.methods
        .executeAction(new anchor.BN(proposalId))
        .accounts({
          governanceState,
          proposal: address,
          treasuryState,
          jackpotState,
          treasuryProgram: treasury.programId,
          jackpotProgram: jackpot.programId,
          governanceAuthority,
          targetProgram: anchor.web3.SystemProgram.programId,
        })
        .rpc();
      proposalState = await governance.account.proposal.fetch(address);
    }

    expect(proposalState.executed, `${label}: the proposal did not execute`).to.equal(true);
    return proposalState;
  }

  /**
   * THE NEXT ID THIS GOVERNANCE STATE WILL ACCEPT, read from the chain: the
   * payout and the rate changes use it so that every run makes a FRESH
   * proposal and can therefore assert a DELTA. It is also the demonstration
   * that the id sequence is real — `propose_action` refuses any id but this
   * one, so a suite that guessed would fail on chain.
   */
  async function runNextProposal(
    action: Record<string, unknown>,
    label: string,
  ): Promise<any> {
    const state = await governance.account.governanceState.fetch(governanceState);
    return runProposal(state.proposalCount.toNumber(), action, label);
  }

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
    // The BALANCE is mutable state, not shape — a first run funds the jackpot
    // before this point, so `balance == 0` held only on virgin state and turned
    // this test into a first-run-only gate (found by re-running the runbook:
    // `expected 21967000 to equal +0`). What the shape of an initialized
    // jackpot does pin is that its cap is one of the two values this suite can
    // account for: the one `initialize` writes, or the one proposal 0 sets.
    expect([INITIAL_MAX_PAYOUT_BPS, PROPOSED_MAX_PAYOUT_BPS]).to.include(
      jackpotBefore.maxPayoutBps.toNumber(),
    );
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

    // PROPOSAL ID 0: the payout cap. A FIXED id, because its effect is an
    // absolute value — the cap the chain ends up holding — so a rerun asserts
    // exactly what the first run asserted instead of trying to create the
    // account again.
    await runProposal(PROPOSAL_PAYOUT_CAP, ACTION_SET_PAYOUT_RULES, "payout cap");
  });

  it("4. THE CPI CHANGED THE PARAMETER ON CHAIN (and could not have failed silently)", async () => {
    const after = await jackpot.account.jackpotState.fetch(jackpotState);
    const before = jackpotBefore.maxPayoutBps.toNumber();

    // Non-vacuous by construction: the cap the chain holds is the value the
    // PROPOSAL asked for, and that value is not the one `initialize` writes —
    // an execution that did nothing (a CPI resolved to the wrong program, a
    // silently skipped instruction) would leave the cap where it was.
    //
    // RERUN-SAFE, deliberately: the cap is mutable state, so `before` is the
    // initialize value only on the FIRST run. Proposal 0 carries a fixed id and
    // executes once (§3.5), so on a rerun the chain already holds the proposed
    // value and this step re-reads what the first run established. Requiring
    // `before == INITIAL` made the suite a first-run-only gate — it passed
    // 23/23 on virgin state and then failed this line on every re-run
    // (`expected 4000 to equal 10000`), which is exactly what §3.5 says a devnet
    // suite must not do. The proof survives: `after` is asserted to be the
    // proposed value, which only a real CPI can write.
    expect([INITIAL_MAX_PAYOUT_BPS, PROPOSED_MAX_PAYOUT_BPS]).to.include(before);
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

    // HOW MUCH CONVERTIBLE SHARE IS LEFT, read off the chain: the same
    // expression `fund_jackpot_with_xmr` evaluates (total_converted_xmrj *
    // exchange_rate_bps / 10 000) less what has already been forwarded. On an
    // untouched devnet this leaves room for the whole of FUNDING_AMOUNT_XMR and
    // the inflow below is exactly FUNDING_INFLOW_XMRJ; on a treasury some other
    // writer has already spent it is a shortfall, and it is the inflow's job to
    // clear it — nothing else in this file can.
    const convertibleAtLiveRate = Math.floor(
      (treasuryBeforeFunding.totalConvertedXmrj.toNumber() *
        treasuryBeforeFunding.exchangeRateBps.toNumber()) /
        10_000,
    );
    // ...and the same share at PAR, which is the recorded XMRJ itself. Step 8
    // puts the rate back to par before it computes its bound, and a suite that
    // has just run leaves the rate DOUBLED — so an inflow sized only for the
    // rate this step happens to find would leave step 8 without headroom the
    // moment the rate moved. Both requirements are computed; the larger wins.
    const convertibleAtPar = treasuryBeforeFunding.totalConvertedXmrj.toNumber();
    const alreadyForwarded =
      treasuryBeforeFunding.totalJackpotFundedXmr.toNumber();
    const shortfall = Math.max(
      alreadyForwarded + FUNDING_AMOUNT_XMR - convertibleAtLiveRate,
      alreadyForwarded + FUNDING_AMOUNT_XMR - convertibleAtPar,
    );
    // CEILING DIVISION, so the banked share (the inflow * INIT_JACKPOT_BPS /
    // 10 000 the program rounds down) still covers the shortfall to the unit.
    const inflowXmrj = Math.max(
      FUNDING_INFLOW_XMRJ,
      Math.ceil((Math.max(0, shortfall) * 10_000) / INIT_JACKPOT_BPS),
    );
    if (inflowXmrj !== FUNDING_INFLOW_XMRJ) {
      console.info(
        `the treasury's convertible share was already spent ` +
          `(${convertibleAtLiveRate} convertible at ` +
          `${treasuryBeforeFunding.exchangeRateBps} bps against ` +
          `${alreadyForwarded} forwarded): recording ${inflowXmrj} XMRJ ` +
          `instead of ${FUNDING_INFLOW_XMRJ}, so this step and step 8's bound ` +
          `have room to be tested at all`,
      );
    }

    // The treasury may forward only what it has RECORDED as convertible, so
    // the run first records an inflow — which banks the jackpot share of it
    // (INIT_JACKPOT_BPS of INIT_RESERVE_BPS + INIT_JACKPOT_BPS). Both calls are
    // signed by the state's OPERATIONAL authority and by nobody else.
    await treasury.methods
      .recordInflowXmrj(new anchor.BN(inflowXmrj))
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
    // ...and the funding side of the identity moves with it. This is the
    // accounting a payout is later reconciled against:
    //     balance + total_payouts == everything ever funded.
    expect(funded.totalFundedXmr.toNumber()).to.equal(
      before.totalFundedXmr.toNumber() + FUNDING_AMOUNT_XMR,
    );
    expect(
      funded.jackpotBalanceXmr.toNumber() + funded.totalPayoutsXmr.toNumber(),
      "the jackpot's own accounting does not close after funding",
    ).to.equal(funded.totalFundedXmr.toNumber());

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
      treasuryBeforeFunding.totalInflowXmrj.toNumber() + inflowXmrj,
    );
  });

  it("7. the jackpot can be PAID OUT — through governance, and only through governance", async function () {
    if (!ownersMatch) {
      console.warn(
        "SKIPPED the payout path: the existing governance_state is not owned " +
          "by this suite's deterministic devnet owners, so an approval cannot " +
          "be signed for. Redeploy the governance program with a fresh keypair " +
          "for a fresh state (XMRJ_SOLANA_DEPLOYMENT_PLAN.md 1.5).",
      );
      this.skip();
    }

    const before = await jackpot.account.jackpotState.fetch(jackpotState);
    const treasuryBeforePayout = await treasury.account.treasuryState.fetch(
      treasuryState,
    );

    // THE PRECONDITION, ASSERTED RATHER THAN ASSUMED: the amount has to be
    // within the live payout cap, or the program would refuse it and this step
    // would be testing the cap instead of the payout.
    const capAmount = Math.floor(
      (before.jackpotBalanceXmr.toNumber() * before.maxPayoutBps.toNumber()) /
        10_000,
    );
    expect(
      PAYOUT_AMOUNT_XMR,
      `the payout (${PAYOUT_AMOUNT_XMR}) must be within the governance cap ` +
        `(${capAmount} = ${before.maxPayoutBps} bps of ${before.jackpotBalanceXmr})`,
    ).to.be.at.most(capAmount);

    // THE PATH F1 WAS ABOUT. The proposal carries TriggerPayout, whose handler
    // CPIs into the jackpot signed by the governance authority PDA — the only
    // address `trigger_payout` accepts as its admin authority, and one that no
    // keypair holds. runNextProposal takes the next id, so the payout is made
    // FRESH on every run and the assertions below can be deltas.
    const payoutProposal = await runNextProposal(ACTION_TRIGGER_PAYOUT, "payout");
    const payoutId = payoutProposal.proposalId.toNumber();

    const after = await jackpot.account.jackpotState.fetch(jackpotState);

    // 1-4. THE EFFECTS, READ FROM THE CHAIN.
    expect(
      after.jackpotBalanceXmr.toNumber(),
      "the payout did not move the jackpot's balance",
    ).to.equal(before.jackpotBalanceXmr.toNumber() - PAYOUT_AMOUNT_XMR);
    expect(
      after.totalPayoutsXmr.toNumber(),
      "the payout was not recorded in total_payouts_xmr",
    ).to.equal(before.totalPayoutsXmr.toNumber() + PAYOUT_AMOUNT_XMR);
    expect(after.lastWinner.toBase58(), "last_winner is not the winner").to.equal(
      WINNER.toBase58(),
    );
    expect(after.totalFundedXmr.toNumber(), "funding is cumulative").to.equal(
      before.totalFundedXmr.toNumber(),
    );
    // 5. THE ACCOUNTING CLOSES, on both sides of the payout: this is what "the
    // balances reconcile" means as a fact about the account.
    expect(
      after.jackpotBalanceXmr.toNumber() + after.totalPayoutsXmr.toNumber(),
      "the jackpot's accounting does not close after a payout",
    ).to.equal(after.totalFundedXmr.toNumber());
    // ...and the TREASURY did not move: a payout is the jackpot's own business,
    // and a payout that wrote the treasury would mean the CPI was signed by
    // something other than the authority PDA.
    const treasuryAfterPayout = await treasury.account.treasuryState.fetch(
      treasuryState,
    );
    expect(
      treasuryAfterPayout.totalJackpotFundedXmr.toNumber(),
      "the payout moved the TREASURY's counters",
    ).to.equal(treasuryBeforePayout.totalJackpotFundedXmr.toNumber());

    // CONTROL 1 — NO OFF-PATH SIGNER. The deployer's wallet is this program's
    // upgrade authority, holds the lamports, and still cannot pay the jackpot:
    // `trigger_payout` requires the stored admin authority to SIGN, that
    // address is a PDA, and only xmrj_governance can sign for it. If this call
    // SUCCEEDED the whole governance path would be decoration.
    let refused = false;
    let refusedWith = "";
    try {
      await jackpot.methods
        .triggerPayout(WINNER, new anchor.BN(PAYOUT_AMOUNT_XMR))
        .accounts({ jackpotState, adminAuthority: payer.publicKey })
        .signers([payer])
        .rpc();
    } catch (err) {
      refused = true;
      refusedWith = err instanceof Error ? err.message : String(err);
    }
    console.info(`a DIRECT triggerPayout from the deployer was refused: ${refusedWith}`);
    expect(
      refused,
      "a DIRECT triggerPayout from the deployer's wallet SUCCEEDED — the " +
        "payout has a path that does not go through governance",
    ).to.equal(true);

    // CONTROL 2 — NO REPLAY. The approved proposal cannot pay twice.
    let replayed = false;
    let replayedWith = "";
    try {
      await governance.methods
        .executeAction(new anchor.BN(payoutId))
        .accounts({
          governanceState,
          proposal: proposalPda(payoutId),
          treasuryState,
          jackpotState,
          treasuryProgram: treasury.programId,
          jackpotProgram: jackpot.programId,
          governanceAuthority,
          targetProgram: anchor.web3.SystemProgram.programId,
        })
        .rpc();
    } catch (err) {
      replayed = true;
      replayedWith = err instanceof Error ? err.message : String(err);
    }
    console.info(`re-executing the payout proposal was refused: ${replayedWith}`);
    expect(replayed, "the payout proposal executed TWICE").to.equal(true);

    // Both refusals changed nothing: the account holds exactly what the
    // approved proposal left it holding.
    const unchanged = await jackpot.account.jackpotState.fetch(jackpotState);
    expect(unchanged.jackpotBalanceXmr.toNumber()).to.equal(
      after.jackpotBalanceXmr.toNumber(),
    );
    expect(unchanged.totalPayoutsXmr.toNumber()).to.equal(
      after.totalPayoutsXmr.toNumber(),
    );
  });

  it("8. the exchange rate is a LIVE bound on funding, not a dead field", async function () {
    if (!ownersMatch) {
      console.warn(
        "SKIPPED the rate path: the existing governance_state is not owned by " +
          "this suite's deterministic devnet owners.",
      );
      this.skip();
    }

    // THE RATE IS STATE, NOT CODE: a previous run left it wherever it left it
    // (this step ends at DOUBLED_RATE_BPS), so put it back to par first — with
    // a fresh proposal, since a proposal executes once. On a first run the
    // rate is already par and no proposal is made.
    if (
      (await treasury.account.treasuryState.fetch(treasuryState)).exchangeRateBps.toNumber() !==
      INIT_EXCHANGE_RATE_BPS
    ) {
      await runNextProposal(ACTION_RATE_AT_PAR, "rate back to par");
    }

    // Record another inflow so there is convertible share to spend. At par the
    // share is worth itself.
    await treasury.methods
      .recordInflowXmrj(new anchor.BN(FUNDING_INFLOW_XMRJ))
      .accounts({
        treasuryState,
        operationalAuthority: operationalAuthority.publicKey,
      })
      .signers([operationalAuthority])
      .rpc();

    const atPar = await treasury.account.treasuryState.fetch(treasuryState);
    expect(
      atPar.exchangeRateBps.toNumber(),
      "this step's arithmetic assumes the suite runs at par",
    ).to.equal(INIT_EXCHANGE_RATE_BPS);

    // THE BOUND, COMPUTED FROM THE CHAIN rather than from constants: what the
    // treasury has recorded as convertible, valued at the rate it records, less
    // what it has already forwarded. The same expression the program evaluates.
    const convertibleAtPar =
      (atPar.totalConvertedXmrj.toNumber() * atPar.exchangeRateBps.toNumber()) /
      10_000;
    const forwardableAtPar =
      convertibleAtPar - atPar.totalJackpotFundedXmr.toNumber();
    expect(forwardableAtPar).to.be.at.least(1);

    // ONE UNIT PAST THE BOUND IS REFUSED — the bound is exact, not a guess.
    let overRefused = false;
    let overWith = "";
    try {
      await treasury.methods
        .fundJackpotWithXmr(new anchor.BN(forwardableAtPar + 1))
        .accounts({
          treasuryState,
          operationalAuthority: operationalAuthority.publicKey,
          jackpotState,
          jackpotFunder,
          jackpotProgram: jackpot.programId,
        })
        .signers([operationalAuthority])
        .rpc();
    } catch (err) {
      overRefused = true;
      overWith = err instanceof Error ? err.message : String(err);
    }
    console.info(
      `one unit past the convertible bound (${forwardableAtPar + 1}) was refused: ${overWith}`,
    );
    expect(
      overRefused,
      `a funding of ${forwardableAtPar + 1} xmr — one unit past the convertible ` +
        `bound — SUCCEEDED, so the treasury's own bound is not what gates funding`,
    ).to.equal(true);

    // HALVE THE RATE, THROUGH GOVERNANCE. The recorded share is now worth half
    // as much XMR, so the ceiling falls to C/2 — and the amount that was
    // exactly acceptable a moment ago must now be REFUSED. That is what makes
    // the field live: an instruction on the funding path reads it, so a passed
    // proposal that changes it changes the bound. Before finding F2 was fixed,
    // `exchange_rate_bps` was written by two instructions and read by none, and
    // this step is the assertion that could not have been written.
    await runNextProposal(ACTION_RATE_HALVED, "rate halved");
    const halved = await treasury.account.treasuryState.fetch(treasuryState);
    expect(halved.exchangeRateBps.toNumber(), "the rate did not move").to.equal(
      HALVED_RATE_BPS,
    );

    const halvedCeiling =
      (atPar.totalConvertedXmrj.toNumber() * HALVED_RATE_BPS) / 10_000;
    expect(
      halvedCeiling,
      `precondition: halving the rate must bring the ceiling ` +
        `(${halvedCeiling}) to or below what is already forwarded ` +
        `(${atPar.totalJackpotFundedXmr.toNumber()}) for the assertion below to mean anything`,
    ).to.be.at.most(atPar.totalJackpotFundedXmr.toNumber());

    let nowRefused = false;
    let nowWith = "";
    try {
      await treasury.methods
        .fundJackpotWithXmr(new anchor.BN(forwardableAtPar))
        .accounts({
          treasuryState,
          operationalAuthority: operationalAuthority.publicKey,
          jackpotState,
          jackpotFunder,
          jackpotProgram: jackpot.programId,
        })
        .signers([operationalAuthority])
        .rpc();
    } catch (err) {
      nowRefused = true;
      nowWith = err instanceof Error ? err.message : String(err);
    }
    console.info(
      `the amount accepted at par (${forwardableAtPar}) was refused at ` +
        `${HALVED_RATE_BPS} bps: ${nowWith}`,
    );
    expect(
      nowRefused,
      `a funding of ${forwardableAtPar} xmr — exactly the bound at par — ` +
        `SUCCEEDED after the rate was halved, so the rate is NOT read on the ` +
        `funding path (finding F2 is not fixed)`,
    ).to.equal(true);

    // DOUBLE IT: the same amount is accepted again and the jackpot's balance
    // moves by it. The rate is a control in BOTH directions, which is what
    // distinguishes a live bound from a one-way constant.
    await runNextProposal(ACTION_RATE_DOUBLED, "rate doubled");
    const doubled = await treasury.account.treasuryState.fetch(treasuryState);
    expect(doubled.exchangeRateBps.toNumber(), "the rate did not move").to.equal(
      DOUBLED_RATE_BPS,
    );

    const jackpotBeforeTopUp = await jackpot.account.jackpotState.fetch(jackpotState);
    await treasury.methods
      .fundJackpotWithXmr(new anchor.BN(forwardableAtPar))
      .accounts({
        treasuryState,
        operationalAuthority: operationalAuthority.publicKey,
        jackpotState,
        jackpotFunder,
        jackpotProgram: jackpot.programId,
      })
      .signers([operationalAuthority])
      .rpc();
    const jackpotAfterTopUp = await jackpot.account.jackpotState.fetch(jackpotState);
    expect(
      jackpotAfterTopUp.jackpotBalanceXmr.toNumber(),
      `the doubled rate did not make the same amount acceptable`, 
    ).to.equal(
      jackpotBeforeTopUp.jackpotBalanceXmr.toNumber() + forwardableAtPar,
    );
    expect(
      jackpotAfterTopUp.jackpotBalanceXmr.toNumber() +
        jackpotAfterTopUp.totalPayoutsXmr.toNumber(),
      "the jackpot's accounting does not close after funding at a changed rate",
    ).to.equal(jackpotAfterTopUp.totalFundedXmr.toNumber());
  });
});
