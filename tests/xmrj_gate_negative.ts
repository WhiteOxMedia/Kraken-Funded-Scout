/**
 * xmrj_gate_negative.ts — THE INITIALIZE GATE, PROVEN NEGATIVELY ON DEVNET.
 *
 * WHY THIS EXISTS: the three gated initializers are pinned structurally by
 * test_anchor_wiring.py 12.10/12.11, and the devnet suite proves the POSITIVE
 * path (the deployer can initialize). Neither proves the NEGATIVE half — that
 * a signer who is NOT the program's upgrade authority is rejected — and the
 * negative half is the whole point of the gate: without it, a chain watcher
 * could create the fixed-seed state first and choose the admin authority.
 *
 * HOW IT IS PROVEN WITHOUT DEPLOYING A FOURTH WORKSPACE PROGRAM: the runner
 * (tests/negative_gate_runner.py) copies the REAL xmrj_jackpot crate into a
 * temp dir, re-declares its id to a freshly generated throwaway keypair,
 * builds it and deploys it to devnet. That program is the gated code itself,
 * at an address nobody has ever initialized — so its `jackpot_state` PDA is
 * genuinely fresh, and the test can attempt the negative case BEFORE anything
 * writes it. No stubbed gate, no second implementation to drift.
 *
 * SKIPPED UNLESS THE RUNNER STARTED IT: with no XMRJ_GATE_PROGRAM_ID in the
 * environment this file registers one PENDING placeholder instead of the suite
 * (see the note on `negativeSuite` — mocha runs a skipped suite's body, so a
 * bare `describe.skip` would still read ANCHOR_PROVIDER_URL at load time and
 * break the ordinary `npx ts-mocha "tests/**\/*.ts"` run). The runner sets the
 * env and runs only this file.
 *
 * WHAT IT ASSERTS, IN ORDER:
 *   0. devnet, by GENESIS HASH (this file spends SOL).
 *   1. the throwaway program is executable, the wallet IS its recorded upgrade
 *      authority, and the state PDA is EMPTY (otherwise the negative is
 *      vacuous and the suite fails for that reason, by name).
 *   2. a freshly funded NON-AUTHORITY signer calling `initialize` is rejected
 *      with EXACTLY the NotUpgradeAuthority custom code (computed by the
 *      runner from the Rust enum, so reordering the enum cannot stale it),
 *      the program log names the gate, and the send never lands.
 *   3. the state is STILL empty after the rejected attempt.
 *   4. the authority's own `initialize` SUCCEEDS — the gate rejects the
 *      outsider, not everyone — and the state it writes has the pinned
 *      layout: treasury program, governance authority PDA, uncapped 10 000.
 */
import * as anchor from "@anchor-lang/core";
import { expect } from "chai";
import { createHash } from "crypto";

// ── the cluster this file may touch ─────────────────────────────────────
const DEVNET_GENESIS_HASH = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG";

// Set by tests/negative_gate_runner.py. Absent => the suite is skipped.
const PROGRAM_ID_STR = process.env.XMRJ_GATE_PROGRAM_ID || "";
const EXPECTED_CODE = Number(process.env.XMRJ_GATE_ERROR || "0");

// The seed literals, byte-for-byte the crates' own constants.
const JACKPOT_STATE_SEED = "jackpot_state";
const GOVERNANCE_AUTHORITY_SEED = "governance_authority";
const LOADER_ID = "BPFLoaderUpgradeab1e11111111111111111111111";

// xmrj_jackpot's pinned governance program (declare_id! in that crate).
const GOVERNANCE_PROGRAM_ID = "AVbKyQ8KZa8hTHQwwSJTRGQsVQxVHzTmzmohVCH5CLe";
// xmrj_jackpot's pinned treasury program (state.treasury_program).
const TREASURY_PROGRAM_ID = "GGPyRTZB73reGyjANw9rdEFX8g26MmkKyAEpDqNQ2468";

// Anchor's discriminator for `initialize`: sha256("global:initialize")[0..8].
const INITIALIZE_DISC = createHash("sha256")
  .update("global:initialize")
  .digest()
  .subarray(0, 8);

// JACKPOT_STATE_SPACE = 8 + 8 + 8 + 32 + 32 + 32 + 8 + 8 — the fields in
// order: balance, total payouts, last winner, treasury program, admin
// authority, max payout bps, and (appended last, finding F1's accounting)
// total funded. The offsets below are unchanged by that field because it went
// on the END; a field inserted in the middle would move them, which is why
// they are written as sums rather than as numbers.
const STATE_LEN = 8 + 8 + 8 + 32 + 32 + 32 + 8 + 8;
const OFF_TREASURY = 8 + 8 + 8 + 32;
const OFF_ADMIN = OFF_TREASURY + 32;
const OFF_MAX_BPS = OFF_ADMIN + 32;

/**
 * Mocha EXECUTES a skipped suite's body, so `describe.skip` on its own would
 * still call AnchorProvider.env() while loading this file and break the
 * ordinary tests glob on any box without ANCHOR_PROVIDER_URL. The suite body
 * is therefore inside a function that is only REGISTERED when the runner set
 * the program id; otherwise one pending placeholder is.
 */
function negativeSuite(): void {
  describe("XMRJ initialize gate — negative proof on devnet", () => {
  const provider = anchor.AnchorProvider.env();
  anchor.setProvider(provider);

  const connection = provider.connection;
  const authority = (provider.wallet as anchor.Wallet).payer;
  const programId = new anchor.web3.PublicKey(PROGRAM_ID_STR);

  const [jackpotState] = anchor.web3.PublicKey.findProgramAddressSync(
    [Buffer.from(JACKPOT_STATE_SEED)],
    programId,
  );
  const [adminAuthority] = anchor.web3.PublicKey.findProgramAddressSync(
    [Buffer.from(GOVERNANCE_AUTHORITY_SEED)],
    new anchor.web3.PublicKey(GOVERNANCE_PROGRAM_ID),
  );
  // The loader's own rule: one seed, the program's id, no string prefix.
  const [programData] = anchor.web3.PublicKey.findProgramAddressSync(
    [programId.toBuffer()],
    new anchor.web3.PublicKey(LOADER_ID),
  );

  let genesis = "";
  let recordedAuthority: anchor.web3.PublicKey | null = null;
  let stateBefore: anchor.web3.AccountInfo<Buffer> | null = null;
  let outsider: anchor.web3.Keypair;

  /** The initialize instruction over the pinned account list. */
  function initializeIx(payer: anchor.web3.PublicKey) {
    return new anchor.web3.TransactionInstruction({
      programId,
      keys: [
        { pubkey: jackpotState, isSigner: false, isWritable: true },
        { pubkey: adminAuthority, isSigner: false, isWritable: false },
        { pubkey: programData, isSigner: false, isWritable: false },
        { pubkey: payer, isSigner: true, isWritable: true },
        {
          pubkey: anchor.web3.SystemProgram.programId,
          isSigner: false,
          isWritable: false,
        },
      ],
      data: Buffer.from(INITIALIZE_DISC),
    });
  }

  /** ProgramData's upgrade authority, read from the loader's own layout. */
  function upgradeAuthorityOf(data: Buffer): anchor.web3.PublicKey | null {
    if (data.length < 45 || data.readUInt32LE(0) !== 3) return null;
    if (data[12] !== 1) return null;
    return new anchor.web3.PublicKey(data.subarray(13, 45));
  }

  async function signedTx(
    kp: anchor.web3.Keypair,
  ): Promise<anchor.web3.Transaction> {
    const tx = new anchor.web3.Transaction().add(initializeIx(kp.publicKey));
    tx.feePayer = kp.publicKey;
    tx.recentBlockhash = (
      await connection.getLatestBlockhash("confirmed")
    ).blockhash;
    tx.sign(kp);
    return tx;
  }

  before(async () => {
    genesis = await connection.getGenesisHash();
    if (genesis !== DEVNET_GENESIS_HASH) {
      throw new Error(
        `REFUSING TO RUN: this file deploys nothing but DOES spend SOL on ` +
          `transactions, and getGenesisHash() returned ${genesis}, not the ` +
          `devnet hash ${DEVNET_GENESIS_HASH}. Point ANCHOR_PROVIDER_URL at ` +
          `https://api.devnet.solana.com.`,
      );
    }

    const info = await connection.getAccountInfo(programId);
    expect(Boolean(info && info.executable), "throwaway program not deployed")
      .to.equal(true);

    const pd = await connection.getAccountInfo(programData);
    recordedAuthority = pd ? upgradeAuthorityOf(pd.data) : null;
    stateBefore = await connection.getAccountInfo(jackpotState);

    // A second, throwaway signer with no relationship to the program. Funded
    // from the authority so its failed transaction is about the GATE, not fees.
    outsider = anchor.web3.Keypair.generate();
    await anchor.web3.sendAndConfirmTransaction(
      connection,
      new anchor.web3.Transaction().add(
        anchor.web3.SystemProgram.transfer({
          fromPubkey: authority.publicKey,
          toPubkey: outsider.publicKey,
          lamports: 20_000_000,
        }),
      ),
      [authority],
    );
  });

  it("0. runs against devnet and nowhere else", () => {
    expect(genesis).to.equal(DEVNET_GENESIS_HASH);
  });

  it("1. the throwaway program is live, this wallet is its authority, and the state is EMPTY", () => {
    // The negative attempt below is only meaningful while nothing has created
    // the state: once it exists, EVERY initializer fails on `init` and the
    // rejection would prove nothing about the gate.
    expect(
      stateBefore,
      `the state PDA ${jackpotState.toBase58()} already exists, so a rejected ` +
        `initialize would prove nothing. The runner deploys a FRESH throwaway ` +
        `program per run; if you see this, it reused one — rerun the runner.`,
    ).to.equal(null);
    expect(recordedAuthority?.toBase58()).to.equal(
      authority.publicKey.toBase58(),
    );
  });

  it("2. a NON-AUTHORITY signer is rejected with NotUpgradeAuthority", async () => {
    const tx = await signedTx(outsider);

    // The exact code, computed by the runner from the Rust enum — not a
    // literal this file could keep after the enum is reordered.
    const sim = await connection.simulateTransaction(tx);
    const err = sim.value.err as any;
    expect(err, "the outsider's initialize SIMULATED successfully").to.not.equal(
      null,
    );
    expect(
      err.InstructionError?.[1]?.Custom,
      `expected custom code ${EXPECTED_CODE}; simulation said ${JSON.stringify(err)}`,
    ).to.equal(EXPECTED_CODE);

    // The log names the gate, so a rejection for some OTHER reason (a bad
    // account, a fee problem) cannot be read as a pass.
    const logs = (sim.value.logs || []).join("\n");
    expect(
      logs,
      "the failure does not name the upgrade-authority gate: " + logs,
    ).to.match(/NotUpgradeAuthority|ProgramIsImmutable|NotProgramData/);

    // And it must never land: a preflight rejection is good, a landed failure
    // is also acceptable proof, but a SUCCESS here is the bug.
    let landed = true;
    try {
      await anchor.web3.sendAndConfirmTransaction(connection, tx, [outsider]);
    } catch {
      landed = false;
    }
    expect(landed, "the outsider's initialize LANDED — the gate is open").to.equal(
      false,
    );
  });

  it("3. the rejected attempt left the state uncreated", async () => {
    expect(await connection.getAccountInfo(jackpotState)).to.equal(null);
  });

  it("4. the AUTHORITY can initialize — the gate is not rejecting everyone", async () => {
    const tx = await signedTx(authority);
    await anchor.web3.sendAndConfirmTransaction(connection, tx, [authority]);

    const info = await connection.getAccountInfo(jackpotState);
    expect(info, "the authority's initialize did not create the state").to.not
      .equal(null);
    const data = info!.data as Buffer;

    // The pinned layout, read back: discriminator + the fields the gated
    // initialize writes. A gate that passed but wrote nothing would fail here.
    expect(data.length).to.equal(STATE_LEN);
    expect(
      new anchor.web3.PublicKey(data.subarray(OFF_TREASURY, OFF_TREASURY + 32))
        .toBase58(),
    ).to.equal(TREASURY_PROGRAM_ID);
    expect(
      new anchor.web3.PublicKey(data.subarray(OFF_ADMIN, OFF_ADMIN + 32))
        .toBase58(),
    ).to.equal(adminAuthority.toBase58());
    expect(Number(data.readBigUInt64LE(OFF_MAX_BPS))).to.equal(10_000);
  });
  });
}

if (PROGRAM_ID_STR) {
  negativeSuite();
} else {
  describe.skip(
    "XMRJ initialize gate — negative proof on devnet (run " +
      "tests/negative_gate_runner.py with XMRJ_GATE_NEGATIVE=1)",
    () => {
      it("is skipped: the runner did not deploy a throwaway program", () => {
        /* nothing to do — see the note on `negativeSuite` above */
      });
    },
  );
}
