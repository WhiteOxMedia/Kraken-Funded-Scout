/**
 * tools/xmrj_loadtest.ts — THE XMRJ STRESS / LOAD HARNESS (Oct 27).
 *
 * WHY THIS EXISTS. The devnet suite (tests/xmrj_devnet_e2e.ts) proves the
 * HAPPY PATH once: one inflow, one funding, one proposal, one refusal. Nothing
 * in the repo asked what happens on the tenth inflow, on forty transactions at
 * once, at `u64::MAX`, one unit past the convertible share, or after a
 * transaction FAILS. This harness asks those questions and records the
 * answers — including the transactions that are SUPPOSED to fail. A stress
 * test whose only possible outcome is "passed" is a smoke test; every
 * scenario below pins BOTH directions, and an expected refusal that does not
 * refuse is a FAIL.
 *
 * USAGE
 *
 *   # plan + derivations + build check only (no cluster, no wallet, no fees):
 *   npx ts-node tools/xmrj_loadtest.ts --offline
 *
 *   # against a cluster (devnet once the wallet in §2 is funded):
 *   XMRJ_RPC_URL=https://api.devnet.solana.com \
 *   ANCHOR_WALLET="$HOME/.config/solana/id.json" \
 *     npx ts-node tools/xmrj_loadtest.ts
 *
 * FLAGS
 *   --offline             derive + validate only; the report is marked
 *                         NOT_EXECUTED (offline never fabricates a result)
 *   --scenarios P0,S1,S5  run a subset (default: all)
 *   --loops N             iterations per loop scenario (default 10)
 *   --parallel N          concurrent transactions in S2 (default 8,
 *                         hard ceiling 40 — Solana refuses more)
 *   --accounts N          accounts created in S3 (default 20)
 *   --json PATH           report path (default reports/xmrj_loadtest_<mode>_<ts>.json)
 *
 * ENV
 *   XMRJ_RPC_URL / ANCHOR_PROVIDER_URL   the cluster (no default: this file
 *                                        will not guess a network)
 *   ANCHOR_WALLET                        deployer keypair (default
 *                                        ~/.config/solana/id.json)
 *   XMRJ_OPERATIONAL_KEYPAIR             the keypair holding the state's
 *                                        `operational_authority` (optional; if
 *                                        the recorded authority is a key this
 *                                        harness cannot sign with, the
 *                                        scenarios that need it SKIP with the
 *                                        reason, never silently pass)
 *
 * THE CLUSTER-IDENTITY GUARD (kept from the e2e suite's design rule): the
 * genesis hash is read and reported, NOT trusted. The suite refuses any chain
 * but devnet; this harness accepts any chain but says which one it is and
 * marks a local cluster as such by ABSOLUTE SLOT (devnet is tens of millions of
 * slots deep, a local validator is hundreds), because a test validator seeded
 * with a cluster's genesis reports that cluster's genesis hash and would
 * otherwise be indistinguishable in a report.
 *
 * ▸ INPUTS: target/idl/*.json (built), Anchor.toml [programs.<cluster>] only
 *   as a cross-check, and the derived keypairs below.
 * ▸ OUTPUTS: a JSON report (signatures, error text, root causes, fixes) and a
 *   non-zero exit when any expected outcome did not happen.
 * ▸ READ-ONLY WITH RESPECT TO THE REPO: it writes nothing but its report.
 * ▸ IT DOES NOT BUILD OR DEPLOY. Run the runbook's §3/§4 first; this harness
 *   refuses to run against an undeployed program.
 */
import * as anchor from "@anchor-lang/core";
import * as fs from "fs";
import * as path from "path";

import { makeConnection, providerUrl, resetRpcStats, rpcStats } from "./rpc_resilience";

const ROOT = path.join(__dirname, "..");
const IDL_DIR = path.join(ROOT, "target", "idl");
const REPORTS_DIR = path.join(ROOT, "reports");

// ── the seeds, exactly as the programs derive them ───────────────────────
const SEED = {
  treasuryState: "treasury_state",
  jackpotState: "jackpot_state",
  jackpotFunder: "jackpot_funder",
  governanceState: "governance_state",
  governanceAuthority: "governance_authority",
  proposal: "proposal",
} as const;
const UPGRADEABLE_LOADER_ID = "BPFLoaderUpgradeab1e11111111111111111111111";
// devnet's genesis hash (pinned in the e2e suite and in create_xmrj_mint.js).
const DEVNET_GENESIS = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG";
// A local validator starts at slot 0; devnet is at tens of millions. This is
// how a cluster that REPORTS devnet's genesis hash is still identified as
// local rather than mistaken for the real thing.
const LOCAL_SLOT_MAX = 10_000_000;

const BPS_TOTAL = 10_000n;
const U64_MAX = 18_446_744_073_709_551_615n;
// PAR: the exchange rate, in basis points of XMR per XMRJ, at which one XMRJ of
// recorded convertible share is worth one XMR. Since finding F2 was fixed the
// rate is READ on the funding path — it values the recorded share, and so sets
// the ceiling on what may be forwarded — so every bound this harness computes
// is computed AT THE RATE IT READS.

// The state's own defaults, mirrored from the programs (initialize pins them):
const JACKPOT_MAX_PAYOUT_BPS_AT_INIT = 10_000n;

// ── flags ───────────────────────────────────────────────────────────────
function flag(name: string, fallback: string): string {
  const i = process.argv.indexOf(`--${name}`);
  const value = i >= 0 ? process.argv[i + 1] : undefined;
  return value && !value.startsWith("--") ? value : fallback;
}
const OFFLINE = process.argv.includes("--offline");
const LOOPS = Number(flag("loops", "10"));
const PARALLEL = Math.min(40, Math.max(1, Number(flag("parallel", "8"))));
const ACCOUNTS = Number(flag("accounts", "20"));
const PAR_BPS = 10_000n;
const WANTED = new Set(
  flag("scenarios", "P0,S0,S1,S2,S3,S4,S5,S6,S7,S8,S9,S10")
    .split(",")
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean),
);

const TITLES: Record<string, string> = {
  P0: "preflight: cluster identity, program ids, upgrade authorities, rent",
  S0: "state bootstrap: initialize what is missing, verify what is present",
  S1: "repeated mint operations (N inflow+funding pairs, exact deltas)",
  S2: "concurrent transactions (M instructions in flight at once)",
  S3: "high-volume account creation (N funded accounts, exact balances)",
  S4: "failed transactions (refusals that MUST refuse, with the error class)",
  S5: "boundary-value amounts (0, 1, u64::MAX, share and share+1, cap)",
  S6: "wallet permission checks (the signer matrix, both directions)",
  S7: "recovery after failures (state unchanged, then still usable)",
  S8: "RPC retry / failure handling (dead endpoint, stale blockhash)",
  S9: "governance round trip (propose, refuse below threshold, approve, execute, re-execute, restore)",
  S10: "governance PAYS the jackpot out (TriggerPayout through a proposal, with its refusals)",
};

// ── report plumbing ─────────────────────────────────────────────────────
type Step = {
  name: string;
  status: "PASS" | "FAIL" | "SKIP" | "NOT_EXECUTED";
  detail?: string;
  txs?: string[];
  error?: string;
  rootCause?: string;
  suggestedFix?: string;
};
type Scenario = {
  id: string;
  title: string;
  status: "PASS" | "FAIL" | "SKIP" | "NOT_EXECUTED";
  steps: Step[];
  metrics?: Record<string, number | string>;
  notes?: string[];
};

const scenarios: Scenario[] = [];
const startedAt = Date.now();

function rec(sc: Scenario, step: Step): Step {
  sc.steps.push(step);
  const tag = `${sc.id} ${step.status.padEnd(12)} ${step.name}`;
  console.log(step.status === "PASS" ? `✔ ${tag}` : `${step.status === "FAIL" ? "✖" : "○"} ${tag}`);
  if (step.detail) console.log(`    ${step.detail}`);
  if (step.error) console.log(`    error: ${step.error.split("\n")[0].slice(0, 200)}`);
  if (step.rootCause) console.log(`    root cause: ${step.rootCause}`);
  if (step.suggestedFix) console.log(`    suggested fix: ${step.suggestedFix}`);
  return step;
}

function close(sc: Scenario): Scenario {
  const failed = sc.steps.some((s) => s.status === "FAIL");
  const ran = sc.steps.some((s) => s.status === "PASS" || s.status === "FAIL");
  sc.status = failed ? "FAIL" : ran ? "PASS" : sc.status;
  scenarios.push(sc);
  console.log(`\n— ${sc.id} ${sc.title}: ${sc.status}\n`);
  return sc;
}

function config(): Record<string, unknown> {
  return {
    flags: { offline: OFFLINE, loops: LOOPS, parallel: PARALLEL, accounts: ACCOUNTS, scenarios: [...WANTED] },
    env: {
      rpc_url: providerUrl(),
      anchor_wallet: process.env.ANCHOR_WALLET ?? null,
      operational_keypair: process.env.XMRJ_OPERATIONAL_KEYPAIR ?? null,
    },
    node: process.versions.node,
    anchor_client: require("@anchor-lang/core/package.json").version,
  };
}

function writeReport(ctx: Record<string, unknown>, ok: boolean): string {
  fs.mkdirSync(REPORTS_DIR, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const target =
    flag("json", path.join(REPORTS_DIR, `xmrj_loadtest_${ctx.mode}_${stamp}.json`));
  const payload = {
    generated_at: new Date().toISOString(),
    duration_ms: Date.now() - startedAt,
    ...ctx,
    config: config(),
    scenarios: scenarios.map((s) => ({
      id: s.id,
      title: s.title,
      status: s.status,
      metrics: s.metrics ?? null,
      notes: s.notes ?? null,
      steps: s.steps,
    })),
    totals: {
      scenarios: scenarios.length,
      passed: scenarios.filter((s) => s.status === "PASS").length,
      failed: scenarios.filter((s) => s.status === "FAIL").length,
      skipped: scenarios.filter((s) => s.status === "SKIP").length,
      not_executed: scenarios.filter((s) => s.status === "NOT_EXECUTED").length,
      steps: scenarios.reduce((n, s) => n + s.steps.length, 0),
      transactions_sent: scenarios.reduce(
        (n, s) => n + s.steps.reduce((m, x) => m + (x.txs?.length ?? 0), 0),
        0,
      ),
    },
    ok,
  };
  fs.writeFileSync(target, JSON.stringify(payload, null, 2));
  console.log(`report: ${target}`);
  return target;
}

// ── helpers ─────────────────────────────────────────────────────────────
async function withRetries<T>(label: string, attempts: number, fn: () => Promise<T>): Promise<T> {
  let last: unknown;
  for (let i = 1; i <= attempts; i += 1) {
    try {
      return await fn();
    } catch (err) {
      last = err;
      if (i < attempts) await new Promise((r) => setTimeout(r, 200 * i));
    }
  }
  throw new Error(
    `${label}: failed after ${attempts} attempt(s): ${last instanceof Error ? last.message : String(last)}`,
  );
}

/**
 * Runs `fn` and reports whether it was REFUSED (threw) or accepted, keeping the
 * resolved value (a signature, for every call here) so the report can carry the
 * transactions a scenario actually sent.
 */
async function attempt(
  fn: () => Promise<unknown>,
): Promise<{ refused: boolean; error?: string; value?: unknown }> {
  try {
    return { refused: false, value: await fn() };
  } catch (err) {
    return { refused: true, error: err instanceof Error ? err.message : String(err) };
  }
}

/** The signature of an accepted call, as a one-element list for the report. */
function sig(outcome: { refused: boolean; value?: unknown; error?: string }): string[] {
  if (outcome.refused || typeof outcome.value !== "string") return [];
  return [outcome.value];
}

/** The Anchor error name out of a failure, if the RPC surfaced one. */
function errorName(message: string | undefined): string {
  if (!message) return "";
  const named = /\bError Code:\s*(\w+)/.exec(message);
  if (named) return named[1];
  const custom = /custom program error: (0x[0-9a-fA-F]+)/.exec(message);
  if (custom) return `custom ${custom[1]}`;
  if (/already in use/i.test(message)) return "AccountAlreadyInUse";
  if (/signature verification failed|missing signature|not signed/i.test(message))
    return "SignatureMissing";
  if (/blockhash not found|BlockhashNotFound/i.test(message)) return "BlockhashNotFound";
  // A 429 is the public RPC refusing the request, not the program refusing the
  // instruction: the two must never be reported as the same class.
  if (/429|Too Many Requests/i.test(message)) return "RpcRateLimited";
  if (/Transaction simulation failed/i.test(message)) return "SimulationFailed";
  return "unclassified";
}

function derive(seeds: (string | Buffer)[], program: anchor.web3.PublicKey) {
  return anchor.web3.PublicKey.findProgramAddressSync(
    seeds.map((s) => (typeof s === "string" ? Buffer.from(s) : s)),
    program,
  );
}

/** The 8-byte little-endian id the proposal PDA's third seed carries — the
 * encoding `proposal_id.to_le_bytes()` writes in the program. */
function proposalIdBytes(id: number): Buffer {
  const bytes = Buffer.alloc(8);
  bytes.writeBigUInt64LE(BigInt(id));
  return bytes;
}

/** The proposal PDA for ONE id: ["proposal", governance_state, id_le_u64].
 * Before F5 was fixed this was ["proposal", governance_state] and `init`, so a
 * governance state could hold exactly one proposal for the life of the program. */
function proposalAddress(
  id: number,
  governanceState: anchor.web3.PublicKey,
  governanceProgram: anchor.web3.PublicKey,
): anchor.web3.PublicKey {
  return derive(
    [SEED.proposal, governanceState.toBuffer(), proposalIdBytes(id)],
    governanceProgram,
  )[0];
}

/**
 * THE DEVNET SUITE'S RECIPE, BIT FOR BIT (tests/xmrj_devnet_e2e.ts). The three
 * governance owners and the operational key must be the SAME identities the
 * suite initializes the state with: the state is create-once, so a harness
 * that derives its own keys cannot approve a proposal on state the suite
 * created, and S9/S10 can then only SKIP. This is not a style choice — it is
 * the difference between "the state is not mine" and a run that exercises the
 * payout path.
 */
function ownerKeypair(index: number): anchor.web3.Keypair {
  const seed = Uint8Array.from({ length: 32 }, (_, j) =>
    (index * 31 + j * 7 + 5) % 256,
  );
  return anchor.web3.Keypair.fromSeed(seed);
}

/** The treasury's `operational_authority`: the only signer for the money path. */
function operationalKeypair(): anchor.web3.Keypair {
  const seed = Uint8Array.from({ length: 32 }, (_, j) => (211 + j * 13) % 256);
  return anchor.web3.Keypair.fromSeed(seed);
}

/**
 * A VALUELESS identity derived from public constants, used for the STRANGER —
 * a key that must be none of the owners above and none of the state's, for the
 * same reason: a rerun must re-derive the same signer, and these keys are never
 * used on a chain that holds value.
 */
function derivedKeypair(tag: string): anchor.web3.Keypair {
  const seed = Uint8Array.from({ length: 32 }, (_, j) =>
    (tag.charCodeAt(0) * 7 + tag.charCodeAt(1) + j * 13 + 5) % 256,
  );
  return anchor.web3.Keypair.fromSeed(seed);
}

// ── offline mode ────────────────────────────────────────────────────────
function offline(): number {
  console.log("OFFLINE — no cluster, no wallet, no fees, no signatures.\n");
  let ok = true;
  const addresses: Record<string, string> = {};
  const programs: Record<string, anchor.web3.PublicKey> = {};

  for (const name of ["xmrj_treasury", "xmrj_jackpot", "xmrj_governance"]) {
    const file = path.join(IDL_DIR, `${name}.json`);
    if (!fs.existsSync(file)) {
      console.log(`✖ missing ${file} — run \`anchor build\` first`);
      ok = false;
      continue;
    }
    const idl = JSON.parse(fs.readFileSync(file, "utf-8"));
    const address = idl.address ?? idl.metadata?.address;
    if (!address) {
      console.log(`✖ ${name}: the IDL carries no program address`);
      ok = false;
      continue;
    }
    programs[name] = new anchor.web3.PublicKey(address);
    addresses[name] = address;
    console.log(
      `✔ ${name}: ${address} — ${idl.instructions?.length ?? 0} instructions, ` +
        `${idl.accounts?.length ?? 0} account types, spec ${idl.metadata?.spec}`,
    );
  }
  if (!ok) return 1;

  const [treasuryState] = derive([SEED.treasuryState], programs.xmrj_treasury);
  const [jackpotState] = derive([SEED.jackpotState], programs.xmrj_jackpot);
  const [jackpotFunder] = derive([SEED.jackpotFunder], programs.xmrj_treasury);
  const [governanceState] = derive([SEED.governanceState], programs.xmrj_governance);
  const [governanceAuthority] = derive([SEED.governanceAuthority], programs.xmrj_governance);
  const proposalFor = (id: number) =>
    proposalAddress(id, governanceState, programs.xmrj_governance);
  // The loader's own rule for a program's ProgramData account: ONE seed, the
  // program's own id, no string prefix. A `["programdata", id]` prefix derives
  // an address with no account at it, and every `initialize` is then refused
  // with `NotProgramData` (6004).
  const programData = (p: anchor.web3.PublicKey) =>
    derive([p.toBuffer()], new anchor.web3.PublicKey(UPGRADEABLE_LOADER_ID))[0];

  Object.assign(addresses, {
    treasury_state: treasuryState.toBase58(),
    jackpot_state: jackpotState.toBase58(),
    jackpot_funder: jackpotFunder.toBase58(),
    governance_state: governanceState.toBase58(),
    governance_authority: governanceAuthority.toBase58(),
    proposal_id_0: proposalFor(0).toBase58(),
    proposal_id_1: proposalFor(1).toBase58(),
    treasury_programdata: programData(programs.xmrj_treasury).toBase58(),
    jackpot_programdata: programData(programs.xmrj_jackpot).toBase58(),
    governance_programdata: programData(programs.xmrj_governance).toBase58(),
  });
  console.log("\nderived addresses (the same seeds the programs use):");
  for (const [k, v] of Object.entries(addresses)) console.log(`  ${k.padEnd(22)} ${v}`);

  // The three arithmetic boundaries this harness will assert, computed from the
  // RUNBOOK's own split (7000/3000) so the numbers in the report are checkable
  // by hand before anything is spent:
  const inflow = 1_000_000n;
  const shareXmrj = (inflow * 3_000n) / BPS_TOTAL;
  const shareXmr = (shareXmrj * PAR_BPS) / BPS_TOTAL;
  console.log(
    `\nboundary arithmetic at the runbook split (reserve 7000 / jackpot 3000 bps) ` +
      `and PAR (${PAR_BPS} bps = 1 XMRJ is worth 1 XMR):\n` +
      `  inflow ${inflow} -> convertible share ${shareXmrj} XMRJ -> ${shareXmr} XMR at par\n` +
      `  funding bound accepted: ${shareXmr}, refused: ${shareXmr} + 1 (ExceedsConvertibleShare)\n` +
      `  the same share at HALF the rate is worth ${shareXmr / 2n} XMR — the rate is READ, so a\n` +
      `  proposal that changes it changes this ceiling (finding F2)\n` +
      `  inflow u64::MAX refused: amount * 7000 overflows u64 (ErrorCode::Overflow)\n` +
      `  payout cap: max_payout_bps is pinned to ${JACKPOT_MAX_PAYOUT_BPS_AT_INIT} at initialize`,
  );

  for (const id of Object.keys(TITLES)) scenarios.push({
    id,
    title: TITLES[id],
    status: "NOT_EXECUTED",
    steps: [{
      name: "requires a cluster",
      status: "NOT_EXECUTED",
      detail:
        "no XMRJ_RPC_URL / ANCHOR_PROVIDER_URL was set, so this scenario was planned and " +
        "its addresses derived, but nothing was sent to a validator",
    }],
  });
  writeReport({ mode: "offline", cluster: null, addresses, ok: true }, true);
  return 0;
}

// ── the interface this harness drives (pinned against the BUILT IDLs by
//    test_xmrj_build_artifacts.py section 4, so a Rust rename cannot leave
//    this file driving an instruction that no longer exists) ─────────────
/**
 * The narrow view this file drives the programs through. Anchor 1.1.2's
 * `Program<IDL>` types its `account` namespace off the IDL's own type parameter,
 * which a runtime-loaded JSON IDL cannot supply. Every name used below is
 * checked against the BUILT IDLs by test_xmrj_build_artifacts.py section 4, so
 * the safety a generated type would give is checked in one place instead of
 * lost — the same trade the devnet suite makes.
 */
interface ProgramView {
  programId: anchor.web3.PublicKey;
  account: { [name: string]: { fetch(addr: anchor.web3.PublicKey): Promise<any> } };
  methods: { [name: string]: (...args: any[]) => any };
}

function view(program: anchor.Program): ProgramView {
  return program as unknown as ProgramView;
}

type Ctx = {
  provider: anchor.AnchorProvider;
  connection: anchor.web3.Connection;
  wallet: anchor.Wallet;
  programs: Record<string, ProgramView>;
  treasuryState: anchor.web3.PublicKey;
  jackpotState: anchor.web3.PublicKey;
  jackpotFunder: anchor.web3.PublicKey;
  governanceState: anchor.web3.PublicKey;
  governanceAuthority: anchor.web3.PublicKey;
  /** The proposal PDA for one id — the ids are the state's own sequence, so the
   * harness asks the chain which one is next instead of assuming a fixed one. */
  proposals: (id: number) => anchor.web3.PublicKey;
  programData: Record<string, anchor.web3.PublicKey>;
  operations: anchor.web3.Keypair;
  owners: anchor.web3.Keypair[];
  stranger: anchor.web3.Keypair;
  treasury: () => Promise<any>;
  jackpot: () => Promise<any>;
};

async function main(): Promise<number> {
  if (OFFLINE) return offline();

  const rpcUrl = providerUrl();
  if (!rpcUrl) {
    console.log(
      "no XMRJ_RPC_URL / ANCHOR_PROVIDER_URL set — this harness will not guess a cluster.\n" +
        "Set it (devnet: https://api.devnet.solana.com) or run --offline for the derived plan.",
    );
    return 2;
  }
  const walletPath =
    process.env.ANCHOR_WALLET ??
    path.join(process.env.HOME ?? process.env.USERPROFILE ?? "", ".config", "solana", "id.json");
  if (!fs.existsSync(walletPath)) {
    console.log(`wallet ${walletPath} does not exist — nothing to sign with`);
    return 2;
  }

  // THE CONNECTION CARRIES THE SHARED RPC LAYER (tools/rpc_resilience.ts). The
  // free public devnet endpoint rate-limits per IP, and web3.js's own retry for
  // that is five attempts over fifteen seconds — shorter than the window it
  // enforces on a host that has just run a load harness, which is why earlier
  // runs died mid-S2 with a wall of "Retrying after … delay" lines. This layer
  // waits the throttling out (bounded by attempts AND by a wall-clock budget),
  // and retries nothing else: a PROGRAM refusal arrives as a JSON-RPC `error`
  // inside a 200 and is handed straight back to the caller.
  resetRpcStats();
  const connection = makeConnection(rpcUrl);
  const wallet = new anchor.Wallet(
    anchor.web3.Keypair.fromSecretKey(Uint8Array.from(JSON.parse(fs.readFileSync(walletPath, "utf-8")))),
  );
  const provider = new anchor.AnchorProvider(connection, wallet, { commitment: "confirmed" });
  anchor.setProvider(provider);

  const programs: Record<string, ProgramView> = {};
  for (const name of ["xmrj_treasury", "xmrj_jackpot", "xmrj_governance"]) {
    const idl = JSON.parse(fs.readFileSync(path.join(IDL_DIR, `${name}.json`), "utf-8"));
    // The built IDL carries `address`, which is what Anchor 1.1.2's Program
    // constructor reads (idl, provider, coder) — no address argument.
    programs[name] = view(new anchor.Program(idl, provider));
  }

  const ctx: Ctx = {
    provider,
    connection,
    wallet,
    programs,
    treasuryState: derive([SEED.treasuryState], programs.xmrj_treasury.programId)[0],
    jackpotState: derive([SEED.jackpotState], programs.xmrj_jackpot.programId)[0],
    jackpotFunder: derive([SEED.jackpotFunder], programs.xmrj_treasury.programId)[0],
    governanceState: derive([SEED.governanceState], programs.xmrj_governance.programId)[0],
    governanceAuthority: derive([SEED.governanceAuthority], programs.xmrj_governance.programId)[0],
    proposals: (id: number) =>
      proposalAddress(
        id,
        derive([SEED.governanceState], programs.xmrj_governance.programId)[0],
        programs.xmrj_governance.programId,
      ),
    programData: {
      xmrj_treasury: derive([programs.xmrj_treasury.programId.toBuffer()], new anchor.web3.PublicKey(UPGRADEABLE_LOADER_ID))[0],
      xmrj_jackpot: derive([programs.xmrj_jackpot.programId.toBuffer()], new anchor.web3.PublicKey(UPGRADEABLE_LOADER_ID))[0],
      xmrj_governance: derive([programs.xmrj_governance.programId.toBuffer()], new anchor.web3.PublicKey(UPGRADEABLE_LOADER_ID))[0],
    },
    operations: process.env.XMRJ_OPERATIONAL_KEYPAIR
      ? anchor.web3.Keypair.fromSecretKey(
          Uint8Array.from(
            JSON.parse(fs.readFileSync(process.env.XMRJ_OPERATIONAL_KEYPAIR, "utf-8")),
          ),
        )
      : operationalKeypair(),
    owners: [ownerKeypair(0), ownerKeypair(1), ownerKeypair(2)],
    stranger: derivedKeypair("stranger-not-an-owner"),
    treasury: async () => programs.xmrj_treasury.account.treasuryState.fetch(ctx.treasuryState),
    jackpot: async () => programs.xmrj_jackpot.account.jackpotState.fetch(ctx.jackpotState),
  };

  // ── P0 preflight ──────────────────────────────────────────────────────
  const identity: Record<string, unknown> = {
    rpc_url: rpcUrl,
    wallet: wallet.publicKey.toBase58(),
    genesis_hash: null,
    is_devnet_genesis: null,
    cluster: null,
    slot: null,
    programs: {},
    addresses: {
      treasury_state: ctx.treasuryState.toBase58(),
      jackpot_state: ctx.jackpotState.toBase58(),
      jackpot_funder: ctx.jackpotFunder.toBase58(),
      governance_state: ctx.governanceState.toBase58(),
      governance_authority: ctx.governanceAuthority.toBase58(),
      proposal_id_0: ctx.proposals(0).toBase58(),
      proposal_id_1: ctx.proposals(1).toBase58(),
      treasury_programdata: ctx.programData.xmrj_treasury.toBase58(),
      jackpot_programdata: ctx.programData.xmrj_jackpot.toBase58(),
      governance_programdata: ctx.programData.xmrj_governance.toBase58(),
    },
  };

  if (WANTED.has("P0")) {
    const sc: Scenario = { id: "P0", title: TITLES.P0, status: "PASS", steps: [] };
    let genesis = "";
    let slot = 0;
    let balance = 0;
    try {
      genesis = await connection.getGenesisHash();
      slot = await connection.getSlot();
      balance = await connection.getBalance(wallet.publicKey);
    } catch (err) {
      rec(sc, {
        name: "the cluster answers at all",
        status: "FAIL",
        error: err instanceof Error ? err.message : String(err),
        rootCause: "the RPC endpoint is unreachable or is not a Solana JSON-RPC node",
        suggestedFix: "check XMRJ_RPC_URL; a dead endpoint must abort the run, not retry forever",
      });
      close(sc);
      writeReport({ mode: "live", ...identity, ok: false }, false);
      return 3;
    }
    const isDevnet = genesis === DEVNET_GENESIS;
    const local = slot < LOCAL_SLOT_MAX;
    identity.genesis_hash = genesis;
    identity.is_devnet_genesis = isDevnet;
    identity.slot = slot;
    identity.cluster = isDevnet ? (local ? "local-validator-with-devnet-genesis" : "devnet") : "other";
    rec(sc, {
      name: `cluster identity: ${identity.cluster}`,
      status: "PASS",
      detail:
        `genesis ${genesis}${isDevnet ? " (= devnet's)" : ""}, slot ${slot}, ` +
        `wallet ${wallet.publicKey.toBase58()} holding ${(balance / 1e9).toFixed(6)} SOL`,
    });
    if (isDevnet && local) {
      rec(sc, {
        name: "NOT devnet, despite the genesis hash",
        status: "FAIL",
        detail:
          "this endpoint reports devnet's genesis hash but is only " +
          `${slot} slots deep. That is a LOCAL validator seeded with devnet's genesis ` +
          "(the e2e suite's genesis guard cannot tell the two apart — this check can).",
        rootCause: "a local test validator that fetched the cluster's genesis",
        suggestedFix:
          "report results as local-cluster evidence, never as a devnet run; use a cluster " +
          "with a deep slot history for anything that will be quoted as a devnet result",
      });
    }
    if (balance < 1_000_000_000) {
      rec(sc, {
        name: "the wallet can pay for at least one program's rent",
        status: "FAIL",
        detail: `balance ${balance} lamports`,
        rootCause: "the deployer wallet is unfunded",
        suggestedFix: "fund it from the devnet web faucet (the RPC faucet is closed: 410)",
      });
    }
    const deployed: Record<string, unknown> = {};
    let authorityMismatch = false;
    for (const [name, program] of Object.entries(programs)) {
      const info = await connection.getAccountInfo(program.programId);
      const pd = await connection.getAccountInfo(ctx.programData[name]);
      const authority =
        pd && pd.data.length >= 45 && pd.data.readUInt32LE(0) === 3 && pd.data[12] === 1
          ? new anchor.web3.PublicKey(pd.data.subarray(13, 45)).toBase58()
          : null;
      deployed[name] = {
        program_id: program.programId.toBase58(),
        executable: Boolean(info?.executable),
        owner: info?.owner.toBase58() ?? null,
        data_len: info?.data.length ?? 0,
        lamports: info?.lamports ?? 0,
        upgrade_authority: authority,
      };
      if (!info?.executable) authorityMismatch = true;
      if (authority !== null && authority !== wallet.publicKey.toBase58()) authorityMismatch = true;
      console.log(
        `    ${name.padEnd(16)} ${program.programId.toBase58()} executable=${Boolean(info?.executable)} ` +
          `authority=${authority} size=${info?.data.length ?? 0} lamports=${info?.lamports ?? 0}`,
      );
    }
    identity.programs = deployed;
    rec(sc, {
      name: "every program is deployed, executable, and this wallet is its upgrade authority",
      status: authorityMismatch ? "FAIL" : "PASS",
      detail: authorityMismatch
        ? "at least one program is missing, not executable, or has a different upgrade authority"
        : "3/3 deployed; the initializer gate (upgrade authority) is satisfied",
      rootCause: authorityMismatch
        ? "the deploy step (§4) did not complete, or a different wallet deployed the programs"
        : undefined,
      suggestedFix: authorityMismatch
        ? "run `anchor deploy --provider.cluster devnet` from the wallet in Anchor.toml [provider]"
        : undefined,
    });
    close(sc);
    if (authorityMismatch) {
      writeReport({ mode: "preflight_failed", ...identity, ok: false }, false);
      return 3;
    }
  }

  const stateVerdict = await bootstrapState(ctx);
  for (const id of ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10"]) {
    if (!WANTED.has(id)) continue;
    if (id === "S0") continue;
    const needsState = ["S1", "S2", "S4", "S5", "S6", "S7", "S10"].includes(id);
    if (needsState && !stateVerdict.ready) {
      close({
        id,
        title: TITLES[id],
        status: "SKIP",
        steps: [{
          name: "state is not usable from this harness",
          status: "SKIP",
          detail: stateVerdict.reason,
          rootCause: stateVerdict.reason,
          suggestedFix:
            "re-run on fresh state (§9 B of the runbook) or export the key that owns " +
            "`operational_authority` as XMRJ_OPERATIONAL_KEYPAIR",
        }],
        notes: [stateVerdict.reason ?? ""],
      });
      continue;
    }
    if (id === "S1") await s1RepeatedOperations(ctx);
    if (id === "S2") await s2Concurrency(ctx);
    if (id === "S3") await s3AccountVolume(ctx);
    if (id === "S4") await s4Refusals(ctx);
    if (id === "S5") await s5Boundaries(ctx);
    if (id === "S6") await s6Permissions(ctx);
    if (id === "S7") await s7Recovery(ctx);
    if (id === "S8") await s8RpcFailure(ctx);
    if (id === "S9") await s9GovernanceRoundTrip(ctx);
    if (id === "S10") await s10GovernancePayout(ctx);
  }

  const failed = scenarios.filter((s) => s.status === "FAIL");
  writeReport({ mode: "live", ...identity, state: stateVerdict, ok: failed.length === 0 }, failed.length === 0);
  console.log(
    `\nSTRESS RESULT: ${scenarios.filter((s) => s.status === "PASS").length} passed, ` +
      `${failed.length} failed, ${scenarios.filter((s) => s.status === "SKIP").length} skipped`,
  );
  // What the RPC layer absorbed, so a green run on a busy endpoint is visible as
  // work done rather than as a quiet omission.
  console.log(`[rpc] ${rpcStats().summary}`);
  return failed.length === 0 ? 0 : 1;
}

// ── S0: state bootstrap ─────────────────────────────────────────────────
async function bootstrapState(ctx: Ctx): Promise<{ ready: boolean; reason?: string; created: string[]; txs: string[] }> {
  const sc: Scenario = { id: "S0", title: TITLES.S0, status: "PASS", steps: [] };
  const created: string[] = [];
  const txs: string[] = [];
  if (!WANTED.has("S0")) {
    // still need to know whether the state exists, without recording a scenario
  }

  const readOrNull = async (fn: () => Promise<any>) => {
    try {
      return await fn();
    } catch {
      return null;
    }
  };

  const treasury = await readOrNull(ctx.treasury);
  if (!treasury) {
    const outcome = await attempt(() =>
      withRetries("initialize treasury", 2, () =>
        ctx.programs.xmrj_treasury.methods
          .initialize(
            new anchor.BN(7_000),
            new anchor.BN(3_000),
            new anchor.BN(1),
            ctx.operations.publicKey,
          )
          .accounts({
            treasuryState: ctx.treasuryState,
            adminAuthority: ctx.governanceAuthority,
            programData: ctx.programData.xmrj_treasury,
            payer: ctx.wallet.publicKey,
            systemProgram: anchor.web3.SystemProgram.programId,
          })
          .rpc(),
      ),
    );
    if (outcome.refused) {
      rec(sc, {
        name: "treasury state created",
        status: "FAIL",
        error: outcome.error,
        rootCause:
          "treasury initialize was refused — most often the payer is not the program's upgrade authority",
        suggestedFix: "run from the deployer in Anchor.toml [provider] wallet",
      });
    } else {
      created.push("treasury_state");
      rec(sc, { name: "treasury state created (7000/3000 split, operational authority set)", status: "PASS", detail: outcome.error ?? "" });
    }
  } else {
    rec(sc, {
      name: "treasury state already exists (create-once: asserted, not re-created)",
      status: "PASS",
      detail:
        `split ${treasury.reserveBps.toString()}/${treasury.jackpotBps.toString()} bps, ` +
        `inflow ${treasury.totalInflowXmrj.toString()}, ` +
        `operational ${treasury.operationalAuthority.toBase58()}, admin ${treasury.adminAuthority.toBase58()}`,
    });
  }

  const jackpot = await readOrNull(ctx.jackpot);
  if (!jackpot) {
    const outcome = await attempt(() =>
      withRetries("initialize jackpot", 2, () =>
        ctx.programs.xmrj_jackpot.methods
          .initialize()
          .accounts({
            jackpotState: ctx.jackpotState,
            adminAuthority: ctx.governanceAuthority,
            programData: ctx.programData.xmrj_jackpot,
            payer: ctx.wallet.publicKey,
            systemProgram: anchor.web3.SystemProgram.programId,
          })
          .rpc(),
      ),
    );
    if (outcome.refused) {
      rec(sc, {
        name: "jackpot state created",
        status: "FAIL",
        error: outcome.error,
        rootCause: "jackpot initialize was refused",
        suggestedFix: "check the payer's upgrade authority and the governance authority PDA derivation",
      });
    } else {
      created.push("jackpot_state");
      rec(sc, { name: "jackpot state created (admin pinned to the governance authority PDA)", status: "PASS" });
    }
  } else {
    rec(sc, {
      name: "jackpot state already exists",
      status: "PASS",
      detail:
        `balance ${jackpot.jackpotBalanceXmr.toString()}, max_payout_bps ` +
        `${jackpot.maxPayoutBps.toString()}, admin ${jackpot.adminAuthority.toBase58()}`,
    });
  }

  const treasuryNow = await readOrNull(ctx.treasury);
  let ready = Boolean(treasuryNow);
  let reason: string | undefined;
  if (treasuryNow && String(treasuryNow.operationalAuthority) !== ctx.operations.publicKey.toBase58()) {
    ready = false;
    reason =
      `the state's operational authority is ${treasuryNow.operationalAuthority} but this harness ` +
      `signs with ${ctx.operations.publicKey.toBase58()}: every money-moving call would be refused ` +
      "(correctly). Export the owning key as XMRJ_OPERATIONAL_KEYPAIR.";
    rec(sc, {
      name: "the operational authority is a key this run can sign with",
      status: "FAIL",
      detail: reason,
      rootCause: "state created by a different operator (or by the e2e suite's own derived key)",
      suggestedFix: "XMRJ_OPERATIONAL_KEYPAIR=<path to that keypair> or re-initialize on fresh state",
    });
  }
  if (!ready && !reason) reason = "no treasury state and the initializer was refused";

  // ── the governance state + the read-only jackpot instruction ──────────
  const governance = await readOrNull(() =>
    ctx.programs.xmrj_governance.account.governanceState.fetch(ctx.governanceState),
  );
  if (!governance) {
    const outcome = await attempt(() =>
      withRetries("initialize governance", 2, () =>
        ctx.programs.xmrj_governance.methods
          .initializeGovernance(ctx.owners.map((o) => o.publicKey), 2)
          .accounts({
            governanceState: ctx.governanceState,
            governanceAuthority: ctx.governanceAuthority,
            programData: ctx.programData.xmrj_governance,
            payer: ctx.wallet.publicKey,
            systemProgram: anchor.web3.SystemProgram.programId,
          })
          .rpc(),
      ),
    );
    rec(sc, {
      name: outcome.refused
        ? "governance state created (3 owners, threshold 2)"
        : "governance state created (3 owners, threshold 2)",
      status: outcome.refused ? "FAIL" : "PASS",
      error: outcome.refused ? outcome.error : undefined,
      detail: outcome.refused
        ? undefined
        : `owners ${ctx.owners.map((o) => o.publicKey.toBase58()).join(", ")}`,
      rootCause: outcome.refused ? "initialize_governance was refused" : undefined,
      suggestedFix: outcome.refused
        ? "check the payer's upgrade authority and the governance_authority PDA derivation"
        : undefined,
    });
    if (!outcome.refused) created.push("governance_state");
  } else {
    const recorded = (governance.owners as anchor.web3.PublicKey[]).map((o) => o.toBase58());
    const ours = ctx.owners.map((o) => o.publicKey.toBase58());
    const mine = ours.every((o) => recorded.includes(o));
    rec(sc, {
      name: mine
        ? "governance state already exists and this harness holds its owners"
        : "governance state exists with a DIFFERENT owner set",
      status: "PASS",
      detail:
        `threshold ${governance.threshold}, owners ${recorded.join(", ")}` +
        (mine ? "" : ` — this run's derived owners (${ours.join(", ")}) cannot approve, so S9 will SKIP`),
    });
  }

  // `get_jackpot_state` is a real instruction (accounts: jackpot_state) and the
  // only path that proves it is callable at all.
  const readCall = await attempt(() =>
    ctx.programs.xmrj_jackpot.methods
      .getJackpotState()
      .accounts({ jackpotState: ctx.jackpotState })
      .rpc(),
  );
  rec(sc, {
    name: "get_jackpot_state is callable and returns Ok",
    status: readCall.refused ? "FAIL" : "PASS",
    error: readCall.refused ? readCall.error : undefined,
    detail: readCall.refused ? undefined : "confirmed",
    rootCause: readCall.refused ? "the read instruction could not be invoked" : undefined,
    suggestedFix: readCall.refused
      ? "it takes only jackpot_state; a failure here means the account list changed"
      : undefined,
  });

  sc.metrics = { created: created.length, txs: txs.length };
  close(sc);
  return { ready, reason, created, txs };
}

// ── S1: repeated operations, exact deltas ───────────────────────────────
async function s1RepeatedOperations(ctx: Ctx): Promise<void> {
  const sc: Scenario = { id: "S1", title: TITLES.S1, status: "PASS", steps: [], metrics: {} };
  const before = await ctx.treasury();
  const jBefore = await ctx.jackpot();
  const share = 1_000_000n; // the runbook's own inflow size
  const intended = (share * BigInt(before.jackpotBps.toString())) / BPS_TOTAL; // 30%
  const txs: string[] = [];
  const latencies: number[] = [];
  let completed = 0;
  let forwarded = 0n;
  let ceilingZeroAt: number | null = null;
  let failedAt: number | null = null;
  let failure: string | undefined;

  for (let i = 0; i < LOOPS; i += 1) {
    const t0 = Date.now();
    try {
      const sig1 = await withRetries<string>("record_inflow_xmrj", 3, () =>
        ctx.programs.xmrj_treasury.methods
          .recordInflowXmrj(new anchor.BN(share.toString()))
          .accounts({ treasuryState: ctx.treasuryState, operationalAuthority: ctx.operations.publicKey })
          .signers([ctx.operations])
          .rpc(),
      );
      // THE CEILING IS READ LIVE, and only what it still allows is forwarded —
      // the program's own formula, from `fund_jackpot_with_xmr`:
      //
      //   convertible_xmr = total_converted_xmrj * exchange_rate_bps / 10_000
      //   forwardable     = max(0, convertible_xmr - total_jackpot_funded_xmr)
      //
      // The subtraction SATURATES on purpose: a proposal that LOWERS the rate
      // lowers this ceiling, so a treasury funded at a higher rate can be left
      // with nothing forwardable, and the program then refuses every amount
      // (ExceedsConvertibleShare) until enough inflow accrues. Forwarding 30%
      // of the new inflow regardless would report that documented bound as a
      // harness failure.
      const now = await ctx.treasury();
      const convertible =
        (BigInt(now.totalConvertedXmrj.toString()) *
          BigInt(now.exchangeRateBps.toString())) / BPS_TOTAL;
      const already = BigInt(now.totalJackpotFundedXmr.toString());
      const forwardable = convertible > already ? convertible - already : 0n;
      const forward = intended < forwardable ? intended : forwardable;
      if (forward === 0n) {
        if (ceilingZeroAt === null) ceilingZeroAt = i + 1;
        txs.push(sig1);
        latencies.push(Date.now() - t0);
        completed += 1;
        continue;
      }
      const sig2 = await withRetries<string>("fund_jackpot_with_xmr", 3, () =>
        ctx.programs.xmrj_treasury.methods
          .fundJackpotWithXmr(new anchor.BN(forward.toString()))
          .accounts({
            treasuryState: ctx.treasuryState,
            operationalAuthority: ctx.operations.publicKey,
            jackpotState: ctx.jackpotState,
            jackpotFunder: ctx.jackpotFunder,
            jackpotProgram: ctx.programs.xmrj_jackpot.programId,
          })
          .signers([ctx.operations])
          .rpc(),
      );
      txs.push(sig1, sig2);
      forwarded += forward;
      latencies.push(Date.now() - t0);
      completed += 1;
    } catch (err) {
      failedAt = i + 1;
      failure = err instanceof Error ? err.message : String(err);
      break;
    }
  }

  const after = await ctx.treasury();
  const jAfter = await ctx.jackpot();
  const dInflow = BigInt(after.totalInflowXmrj.toString()) - BigInt(before.totalInflowXmrj.toString());
  const dConverted = BigInt(after.totalConvertedXmrj.toString()) - BigInt(before.totalConvertedXmrj.toString());
  const dReserve = BigInt(after.totalReserveXmrj.toString()) - BigInt(before.totalReserveXmrj.toString());
  const dFunded = BigInt(after.totalJackpotFundedXmr.toString()) - BigInt(before.totalJackpotFundedXmr.toString());
  const dJackpotBalance = BigInt(jAfter.jackpotBalanceXmr.toString()) - BigInt(jBefore.jackpotBalanceXmr.toString());

  const expectInflow = share * BigInt(completed);
  const expectConverted = ((share * BigInt(before.jackpotBps.toString())) / BPS_TOTAL) * BigInt(completed);
  const expectReserve = ((share * BigInt(before.reserveBps.toString())) / BPS_TOTAL) * BigInt(completed);
  const expectFunded = forwarded;

  rec(sc, {
    name: `${completed}/${LOOPS} inflow+funding pairs confirmed`,
    status: completed === LOOPS ? "PASS" : "FAIL",
    detail: failedAt
      ? `stopped at iteration ${failedAt}`
      : `all ${LOOPS} pairs confirmed${ceilingZeroAt === null ? "" : 
          `; the treasury's convertible ceiling was already exhausted from iteration ${ceilingZeroAt} (a rate LOWERED after earlier funding — the program refuses every amount then, by design), so those iterations recorded the inflow and forwarded nothing`}` + 
        `; forwarded ${forwarded} of ${intended * BigInt(LOOPS)} XMR`,
    txs,
    error: failure,
    rootCause: failure ? "one iteration did not confirm inside its 3 attempts" : undefined,
    suggestedFix: failure
      ? "a 429 from a public RPC needs backoff, a program error is a defect: read the recorded error text"
      : undefined,
  });
  rec(sc, {
    name: "treasury counters moved by EXACTLY the arithmetic the program defines",
    status:
      dInflow === expectInflow && dConverted === expectConverted && dReserve === expectReserve
        ? "PASS"
        : "FAIL",
    detail:
      `inflow +${dInflow} (expected +${expectInflow}), converted +${dConverted} ` +
      `(expected +${expectConverted}), reserve +${dReserve} (expected +${expectReserve})`,
    rootCause:
      dInflow === expectInflow && dConverted === expectConverted && dReserve === expectReserve
        ? undefined
        : "a recorded inflow did not land with the split the program computes",
    suggestedFix:
      dInflow === expectInflow ? undefined : "compare the signatures in this report against the ledger",
  });
  rec(sc, {
    name: "the funded amount moved BOTH sides by the same amount (no bridge lies)",
    status: dFunded === expectFunded && dJackpotBalance === expectFunded ? "PASS" : "FAIL",
    detail: `treasury.total_jackpot_funded_xmr +${dFunded}, jackpot.balance +${dJackpotBalance}`,
    rootCause:
      dFunded === expectFunded && dJackpotBalance === expectFunded
        ? undefined
        : "the treasury recorded a forwarding the jackpot did not receive, or vice versa",
    suggestedFix:
      dFunded === expectFunded ? undefined : "read both states at the same commitment level",
  });
  if (latencies.length) {
    latencies.sort((a, b) => a - b);
    sc.metrics = {
      loops: LOOPS,
      completed,
      transactions: txs.length,
      per_pair_ms_p50: latencies[Math.floor(latencies.length / 2)],
      per_pair_ms_max: latencies[latencies.length - 1],
      inflow_delta: dInflow.toString(),
      funded_delta: dFunded.toString(),
    };
  }
  sc.notes = [
    "the mint script (create_xmrj_mint.js) is a separate, devnet-only artifact and is " +
      "exercised by the runbook's §6 (record, rerun, lock) — not from here, so this " +
      "scenario does not claim it",
  ];
  close(sc);
}

// ── S2: concurrency ─────────────────────────────────────────────────────
async function s2Concurrency(ctx: Ctx): Promise<void> {
  const sc: Scenario = { id: "S2", title: TITLES.S2, status: "PASS", steps: [] };
  const before = await ctx.treasury();
  const t0 = Date.now();
  const settled = await Promise.allSettled(
    Array.from({ length: PARALLEL }, () =>
      ctx.programs.xmrj_treasury.methods
        .recordInflowXmrj(new anchor.BN(1))
        .accounts({ treasuryState: ctx.treasuryState, operationalAuthority: ctx.operations.publicKey })
        .signers([ctx.operations])
        .rpc(),
    ),
  );
  const elapsed = Date.now() - t0;
  const returned = settled.filter((s) => s.status === "fulfilled") as PromiseFulfilledResult<string>[];
  const rejected = settled.filter((s) => s.status === "rejected") as PromiseRejectedResult[];
  // IDENTICAL instructions from one signer under one blockhash share ONE
  // signature, and the runtime processes a signature once — so several sends
  // can return the same signature and land as a single transaction. Counting
  // "how many sends returned a signature" therefore cannot equal the state
  // delta. Conservation is asserted against DISTINCT signatures, and each one
  // is re-read from the chain instead of being trusted from the send result.
  const distinct = [...new Set(returned.map((c) => c.value))];
  const statuses = distinct.length
    ? (await ctx.connection.getSignatureStatuses(distinct)).value
    : [];
  const landed = distinct.filter((_s, i) => {
    const st = statuses[i] as { err?: unknown; confirmationStatus?: string } | null;
    return Boolean(st) && !st!.err &&
      (st!.confirmationStatus === "confirmed" || st!.confirmationStatus === "finalized");
  });
  const after = await ctx.treasury();
  const delta = BigInt(after.totalInflowXmrj.toString()) - BigInt(before.totalInflowXmrj.toString());
  const reasons = [...new Set(rejected.map((r) => {
    const reason = r.reason as { message?: string; logs?: string[] } | undefined;
    return errorName(
      [String(reason?.message ?? r.reason), reason?.logs?.join(" ")]
        .filter(Boolean).join(" | "),
    );
  }))];

  rec(sc, {
    name: `conservation of value: the state delta equals the ${landed.length} DISTINCT transaction(s) the chain confirms`,
    status: delta === BigInt(landed.length) ? "PASS" : "FAIL",
    detail:
      `${PARALLEL} fired at once; ${returned.length} sends returned a signature (${distinct.length} distinct), ` +
      `${landed.length} confirmed ON CHAIN, ${rejected.length} rejected in ${elapsed} ms; delta ${delta}`,
    txs: landed,
    error: reasons.length ? `rejection classes: ${reasons.join(", ")}` : undefined,
    rootCause:
      delta === BigInt(landed.length)
        ? undefined
        : "a chain-confirmed transaction left no state change, or an unconfirmed one changed state",
    suggestedFix:
      delta === BigInt(landed.length)
        ? undefined
        : "re-read each distinct signature at a higher commitment before counting it",
  });
  sc.metrics = {
    in_flight: PARALLEL,
    returned_signature: returned.length,
    distinct_signatures: distinct.length,
    confirmed_on_chain: landed.length,
    rejected: rejected.length,
    wall_ms: elapsed,
    delta: delta.toString(),
  };
  sc.notes = [
    "rejections under one signer are EXPECTED: Solana serializes a single signer's " +
      "transactions and identical recent blockhashes collide — two sends can share a " +
      "signature and land as ONE transaction. That is why this scenario asserts " +
      "conservation (delta == the DISTINCT signatures the chain confirms) instead of " +
      "'all succeeded'.",
    `distinct rejection classes observed: ${reasons.join(", ") || "none"}`,
  ];
  close(sc);
}

// ── S3: account volume ──────────────────────────────────────────────────
async function s3AccountVolume(ctx: Ctx): Promise<void> {
  const sc: Scenario = { id: "S3", title: TITLES.S3, status: "PASS", steps: [] };
  const lamports = 25_000_000; // 0.025 SOL per fresh account
  const funded: string[] = [];
  const txs: string[] = [];
  let mismatch = 0;
  let stopped: string | undefined;
  for (let i = 0; i < ACCOUNTS; i += 1) {
    const kp = anchor.web3.Keypair.generate();
    const outcome = await attempt(() =>
      withRetries(`fund account ${i + 1}`, 3, async () => {
        const sig = await ctx.provider.sendAndConfirm(
          new anchor.web3.Transaction().add(
            anchor.web3.SystemProgram.transfer({
              fromPubkey: ctx.wallet.publicKey,
              toPubkey: kp.publicKey,
              lamports,
            }),
          ),
          [],
        );
        txs.push(sig);
        return sig;
      }),
    );
    if (outcome.refused) {
      stopped = outcome.error;
      break;
    }
    funded.push(kp.publicKey.toBase58());
    const info = await ctx.connection.getAccountInfo(kp.publicKey);
    if (!info || info.lamports !== lamports) mismatch += 1;
  }
  rec(sc, {
    name: `${ACCOUNTS} fresh accounts created, each readable with the exact lamports requested`,
    status: funded.length === ACCOUNTS && mismatch === 0 ? "PASS" : "FAIL",
    detail: `created ${funded.length}/${ACCOUNTS}, ${mismatch} with a wrong balance, ${txs.length} transactions`,
    txs,
    error: stopped,
    rootCause: stopped
      ? "creating accounts stopped early"
      : mismatch
        ? "an account exists with a different balance than the transfer asked for"
        : undefined,
    suggestedFix: stopped
      ? "top the payer up; a public RPC's per-IP rate limit surfaces here first"
      : mismatch
        ? "check for a concurrent balance change on that address"
        : undefined,
  });
  // The ATA half is only meaningful with a real mint, which is the runbook's
  // §6 artifact: name the gap instead of pretending to check it.
  rec(sc, {
    name: "associated token accounts for the four allocations",
    status: "SKIP",
    detail:
      "needs the XMRJ mint (runbook §6). It is asserted there — create_xmrj_mint.js " +
      "reports each allocation account and its balance on a rerun.",
  });
  sc.metrics = { requested: ACCOUNTS, created: funded.length, mismatched: mismatch };
  sc.notes = [`sample addresses: ${funded.slice(0, 5).join(", ")}`];
  close(sc);
}

// ── S4: the refusals ────────────────────────────────────────────────────
async function s4Refusals(ctx: Ctx): Promise<void> {
  const sc: Scenario = { id: "S4", title: TITLES.S4, status: "PASS", steps: [] };
  const deployer = ctx.wallet.publicKey;
  const T = ctx.programs.xmrj_treasury;
  const J = ctx.programs.xmrj_jackpot;
  const G = ctx.programs.xmrj_governance;

  const cases: Array<{ name: string; expect: string; mustRefuse: boolean; run: () => Promise<unknown> }> = [
    {
      name: "record_inflow_xmrj from the DEPLOYER (not the operational authority)",
      expect: "ConstraintHasOne / UnauthorizedAdmin — the has_one pairs the state with the key, the signer demand is separate",
      mustRefuse: true,
      run: () =>
        T.methods
          .recordInflowXmrj(new anchor.BN(1))
          .accounts({ treasuryState: ctx.treasuryState, operationalAuthority: deployer })
          .rpc(),
    },
    {
      name: "record_inflow_xmrj from an unrelated wallet",
      expect: "refused",
      mustRefuse: true,
      run: () =>
        T.methods
          .recordInflowXmrj(new anchor.BN(1))
          .accounts({ treasuryState: ctx.treasuryState, operationalAuthority: ctx.stranger.publicKey })
          .signers([ctx.stranger])
          .rpc(),
    },
    {
      name: "set_split_ratio signed by the operational authority (governance-only parameter)",
      expect: "refused — the treasury's admin is the governance authority PDA",
      mustRefuse: true,
      run: () =>
        T.methods
          .setSplitRatio(new anchor.BN(1), new anchor.BN(9_999))
          .accounts({ treasuryState: ctx.treasuryState, adminAuthority: ctx.operations.publicKey })
          .signers([ctx.operations])
          .rpc(),
    },
    {
      name: "a SECOND treasury initialize on existing state",
      expect: "AccountAlreadyInUse — state is create-once",
      mustRefuse: true,
      run: () =>
        T.methods
          .initialize(new anchor.BN(7_000), new anchor.BN(3_000), new anchor.BN(1), ctx.operations.publicKey)
          .accounts({
            treasuryState: ctx.treasuryState,
            adminAuthority: ctx.governanceAuthority,
            programData: ctx.programData.xmrj_treasury,
            payer: deployer,
            systemProgram: anchor.web3.SystemProgram.programId,
          })
          .rpc(),
    },
    {
      name: "fund_jackpot with the funder PDA passed UNSIGNED",
      expect: "a signature error — a PDA cannot sign for itself outside the deriving program's invoke_signed",
      mustRefuse: true,
      run: () =>
        J.methods
          .fundJackpot(new anchor.BN(1))
          .accounts({ jackpotState: ctx.jackpotState, funder: ctx.jackpotFunder })
          .rpc(),
    },
    {
      name: "fund_jackpot with the treasury PROGRAM ID as the funder (the pre-fix recipe)",
      expect: "UnauthorizedTreasuryCaller (6001) — the address is not the funder PDA derived under the pinned treasury id",
      mustRefuse: true,
      run: () =>
        J.methods
          .fundJackpot(new anchor.BN(1))
          .accounts({ jackpotState: ctx.jackpotState, funder: T.programId })
          .rpc(),
    },
    {
      name: "fund_jackpot from a funded wallet that is not the treasury",
      expect: "refused",
      mustRefuse: true,
      run: () =>
        J.methods
          .fundJackpot(new anchor.BN(1))
          .accounts({ jackpotState: ctx.jackpotState, funder: deployer })
          .rpc(),
    },
    {
      name: "trigger_payout called DIRECTLY by the deployer (a funded jackpot's only signer is the governance PDA)",
      expect:
        "UnauthorizedAdmin (6002) — the account is a wallet, not the state's admin authority. " +
        "Since finding F1 was fixed there IS a path that satisfies this check " +
        "(GovernanceAction::TriggerPayout, exercised in S10), which is what makes this " +
        "refusal a CONTROL: the payout runs through governance, not around it",
      mustRefuse: true,
      run: () =>
        J.methods
          .triggerPayout(ctx.stranger.publicKey, new anchor.BN(1))
          .accounts({ jackpotState: ctx.jackpotState, adminAuthority: deployer })
          .rpc(),
    },
    {
      name: "set_payout_rules called DIRECTLY by the deployer (must be a proposal)",
      expect: "UnauthorizedAdmin (6002)",
      mustRefuse: true,
      run: () =>
        J.methods
          .setPayoutRules(new anchor.BN(1))
          .accounts({ jackpotState: ctx.jackpotState, adminAuthority: deployer })
          .rpc(),
    },
    {
      name: "a principal that is not this program's ProgramData (a lookalike)",
      expect: "NotProgramData (6003) — the constraint derives the address, so a substitute is rejected",
      mustRefuse: true,
      run: () =>
        T.methods
          .initialize(new anchor.BN(7_000), new anchor.BN(3_000), new anchor.BN(1), ctx.operations.publicKey)
          .accounts({
            treasuryState: ctx.treasuryState,
            adminAuthority: ctx.governanceAuthority,
            programData: ctx.programData.xmrj_jackpot, // the JACKPOT's ProgramData, on purpose
            payer: deployer,
            systemProgram: anchor.web3.SystemProgram.programId,
          })
          .rpc(),
    },
    {
      name: "initialize_governance with zero owners",
      expect: "NotEnoughOwners (6000)",
      mustRefuse: true,
      run: () =>
        G.methods
          .initializeGovernance([], 0)
          .accounts({
            governanceState: ctx.governanceState,
            governanceAuthority: ctx.governanceAuthority,
            programData: ctx.programData.xmrj_governance,
            payer: deployer,
            systemProgram: anchor.web3.SystemProgram.programId,
          })
          .rpc(),
    },
    {
      name: "initialize_governance with a threshold above the owner count",
      expect: "InvalidThreshold (6001) or NotEnoughOwners",
      mustRefuse: true,
      run: () =>
        G.methods
          .initializeGovernance([ctx.owners[0].publicKey], 2)
          .accounts({
            governanceState: ctx.governanceState,
            governanceAuthority: ctx.governanceAuthority,
            programData: ctx.programData.xmrj_governance,
            payer: deployer,
            systemProgram: anchor.web3.SystemProgram.programId,
          })
          .rpc(),
    },
    {
      name: "trigger_payout with the GOVERNANCE AUTHORITY PDA as the admin authority, UNSIGNED",
      expect:
        "a signature error — the address is exactly right and only the signature is missing, " +
        "which is the one case a key comparison can never catch. The real path is a governance " +
        "CPI signed with that PDA's seeds (S10)",
      mustRefuse: true,
      run: () =>
        J.methods
          .triggerPayout(deployer, new anchor.BN(1))
          .accounts({ jackpotState: ctx.jackpotState, adminAuthority: ctx.governanceAuthority })
          .rpc(),
    },
    {
      name: "propose_action with an id that is NOT the next one in the sequence",
      expect:
        "ProposalIdOutOfSequence — the id must equal the state's proposal_count, so a proposal " +
        "cannot be created out of order, and an id that was already used can never be reused " +
        "(finding F5)",
      mustRefuse: true,
      run: async () => {
        const state = await G.account.governanceState.fetch(ctx.governanceState);
        const bad = Number(state.proposalCount.toString()) + 5;
        return G.methods
          .proposeAction(new anchor.BN(bad), {
            setExchangeRate: { exchangeRateBps: new anchor.BN(PAR_BPS.toString()) },
          })
          .accounts({
            governanceState: ctx.governanceState,
            proposal: ctx.proposals(bad),
            proposer: deployer,
            systemProgram: anchor.web3.SystemProgram.programId,
          })
          .rpc();
      },
    },
  ];

  for (const c of cases) {
    const outcome = await attempt(c.run);
    const ok = outcome.refused === c.mustRefuse;
    const kind = errorName(outcome.error);
    rec(sc, {
      name: c.name,
      status: ok ? "PASS" : "FAIL",
      detail: ok
        ? `refused (${kind}) — expected ${c.expect}`
        : c.mustRefuse
          ? `NOT REFUSED — expected ${c.expect}`
          : "accepted, as required",
      error: outcome.error && !ok ? outcome.error : undefined,
      rootCause: ok
        ? undefined
        : "an authorization or validation gate did not fire for a caller that should not be able to move money",
      suggestedFix: ok
        ? undefined
        : "CRITICAL: re-read this instruction's constraints against the IDL and the source before any mainnet step",
    });
  }
  sc.metrics = { cases: cases.length, refused: cases.filter((c) => c.mustRefuse).length };
  sc.notes = [
    "each case records the raw error text in the JSON report — a refusal that says " +
      "'AccountNotEnoughKeys' is a harness bug, while ConstraintHasOne / UnauthorizedAdmin / " +
      "UnauthorizedTreasuryCaller / AccountAlreadyInUse are the gates doing their job",
  ];
  close(sc);
}

// ── S5: boundaries ──────────────────────────────────────────────────────
async function s5Boundaries(ctx: Ctx): Promise<void> {
  const sc: Scenario = { id: "S5", title: TITLES.S5, status: "PASS", steps: [] };

  const expectOutcome = async (
    name: string,
    mustRefuse: boolean,
    expectedClass: string,
    run: () => Promise<unknown>,
  ) => {
    const outcome = await attempt(run);
    const ok = outcome.refused === mustRefuse;
    const kind = errorName(outcome.error);
    rec(sc, {
      name,
      status: ok ? "PASS" : "FAIL",
      detail: mustRefuse
        ? ok
          ? `refused (${kind}); expected ${expectedClass}`
          : "ACCEPTED — a boundary guard is missing"
        : "accepted, as required",
      error: ok ? undefined : outcome.error,
      rootCause: ok
        ? undefined
        : mustRefuse
          ? "the boundary guard did not fire"
          : "a legitimate boundary value was rejected",
      suggestedFix: ok
        ? undefined
        : "re-read the guard's comparison (checked_add/checked_mul vs a bare operator) and whether " +
          "the release profile keeps overflow checks on",
    });
    return outcome;
  };

  const zero = () =>
    ctx.programs.xmrj_treasury.methods
      .recordInflowXmrj(new anchor.BN(0))
      .accounts({ treasuryState: ctx.treasuryState, operationalAuthority: ctx.operations.publicKey })
      .signers([ctx.operations])
      .rpc();

  await expectOutcome("record_inflow_xmrj(0) — a zero inflow is legal arithmetic, not a wrap", false, "accepted", zero);

  await expectOutcome(
    "record_inflow_xmrj(1) — the smallest unit",
    false,
    "accepted",
    () =>
      ctx.programs.xmrj_treasury.methods
        .recordInflowXmrj(new anchor.BN(1))
        .accounts({ treasuryState: ctx.treasuryState, operationalAuthority: ctx.operations.publicKey })
        .signers([ctx.operations])
        .rpc(),
  );

  await expectOutcome(
    "record_inflow_xmrj(u64::MAX) — amount * reserve_bps must overflow into ErrorCode::Overflow, not wrap",
    true,
    "ErrorCode::Overflow",
    () =>
      ctx.programs.xmrj_treasury.methods
        .recordInflowXmrj(new anchor.BN(U64_MAX.toString()))
        .accounts({ treasuryState: ctx.treasuryState, operationalAuthority: ctx.operations.publicKey })
        .signers([ctx.operations])
        .rpc(),
  );

  // THE FUNDING BOUND, computed from the live state rather than assumed:
  // forwardable = total_converted_xmrj * exchange_rate_bps / 10 000 -
  // total_jackpot_funded_xmr — the rate VALUES the recorded share, so it is
  // part of the bound and not a field written by two instructions and read by
  // none (finding F2).
  //
  // The loop scenarios above deliberately forward EXACTLY the convertible
  // share of every inflow they record, which leaves forwardable at 0 — there
  // would be no edge to stand on. So this records one more inflow of the
  // runbook's own size (100% of it becomes convertible share at 30%), which
  // makes the boundary a real two-sided test: forwardable must be ACCEPTED and
  // forwardable + 1 must be REFUSED.
  await expectOutcome(
    "record_inflow_xmrj(1_000_000) to give the funding bound a standing edge",
    false,
    "accepted",
    () =>
      ctx.programs.xmrj_treasury.methods
        .recordInflowXmrj(new anchor.BN(1_000_000))
        .accounts({ treasuryState: ctx.treasuryState, operationalAuthority: ctx.operations.publicKey })
        .signers([ctx.operations])
        .rpc(),
  );

  const state = await ctx.treasury();
  const forwardable =
    (BigInt(state.totalConvertedXmrj.toString()) *
      BigInt(state.exchangeRateBps.toString())) /
      BPS_TOTAL -
    BigInt(state.totalJackpotFundedXmr.toString());
  const funding = (amount: bigint) =>
    ctx.programs.xmrj_treasury.methods
      .fundJackpotWithXmr(new anchor.BN(amount.toString()))
      .accounts({
        treasuryState: ctx.treasuryState,
        operationalAuthority: ctx.operations.publicKey,
        jackpotState: ctx.jackpotState,
        jackpotFunder: ctx.jackpotFunder,
        jackpotProgram: ctx.programs.xmrj_jackpot.programId,
      })
      .signers([ctx.operations])
      .rpc();

  if (forwardable === 0n) {
    rec(sc, {
      name: "the funding bound's exact edge (forwardable, forwardable + 1)",
      status: "SKIP",
      detail: "forwardable is 0 on this state, so there is no edge to stand on — S1 records inflows first",
    });
  } else {
    // Both sides of the edge, in this order: the exact share must be accepted
    // (a bound that refuses its own boundary is off by one), then one unit more
    // must be refused with the program's own error.
    await expectOutcome(
      `fund_jackpot_with_xmr(forwardable = ${forwardable}) — the exact edge, must be ACCEPTED`,
      false,
      "accepted",
      () => funding(forwardable),
    );
    await expectOutcome(
      `fund_jackpot_with_xmr(forwardable + 1 = ${forwardable + 1n}) — ExceedsConvertibleShare (6002)`,
      true,
      "ExceedsConvertibleShare",
      () => funding(forwardable + 1n),
    );
  }

  await expectOutcome(
    "set_split_ratio(9_999, 1) from the deployer — refused on AUTHORIZATION (the admin is the governance PDA), never reaching the sum check",
    true,
    "ConstraintHasOne / UnauthorizedAdmin",
    () =>
      ctx.programs.xmrj_treasury.methods
        .setSplitRatio(new anchor.BN(9_999), new anchor.BN(1))
        .accounts({ treasuryState: ctx.treasuryState, adminAuthority: ctx.wallet.publicKey })
        .rpc(),
  );

  await expectOutcome(
    "set_payout_rules(10_001) directly — refused (cap above 100% is invalid AND the caller is not the admin)",
    true,
    "UnauthorizedAdmin / InvalidPayoutCap",
    () =>
      ctx.programs.xmrj_jackpot.methods
        .setPayoutRules(new anchor.BN(BPS_TOTAL + 1n))
        .accounts({ jackpotState: ctx.jackpotState, adminAuthority: ctx.wallet.publicKey })
        .rpc(),
  );

  rec(sc, {
    name: "trigger_payout at the cap boundary (cap == balance)",
    status: "SKIP",
    detail:
      "unreachable for the same reason S4 records: the only permitted signer is the governance " +
      "authority PDA and no GovernanceAction variant calls trigger_payout. The boundary is " +
      "therefore asserted against the SOURCE (ceiling = balance * max_payout_bps / 10_000) rather " +
      "than on chain. Negative control that IS reachable: the direct call above is refused, so a " +
      "future fix is what makes this SKIP turn into a case.",
  });

  sc.notes = [
    "the value-boundary cases that only governance can reach (a legal split, a legal cap, a " +
      "rotation of the operational authority) are exercised by tests/xmrj_devnet_e2e.ts and " +
      "tests/xmrj_gate_negative.ts, which propose and execute real proposals",
    "the two AUTHORIZATION refusals above are deliberate: from a wallet, an invalid VALUE and an " +
      "invalid CALLER are indistinguishable — the gate that fires first is the caller's",
  ];
  close(sc);
}

// ── S6: permissions ─────────────────────────────────────────────────────
async function s6Permissions(ctx: Ctx): Promise<void> {
  const sc: Scenario = { id: "S6", title: TITLES.S6, status: "PASS", steps: [] };
  const state = await ctx.treasury();
  const deployer = ctx.wallet.publicKey.toBase58();

  rec(sc, {
    name: "the operational authority is NOT the deployer (two roles, two keys)",
    status: String(state.operationalAuthority) !== deployer ? "PASS" : "FAIL",
    detail: `operational ${state.operationalAuthority}, admin ${state.adminAuthority}, deployer ${deployer}`,
    rootCause:
      String(state.operationalAuthority) === deployer
        ? "the operational key IS the upgrade authority: the separation the design depends on is absent"
        : undefined,
    suggestedFix:
      String(state.operationalAuthority) === deployer
        ? "initialize on fresh state with an operational key distinct from the upgrade authority"
        : undefined,
  });
  rec(sc, {
    name: "the admin authority is the governance authority PDA (so no wallet can retune the treasury)",
    status: String(state.adminAuthority) === ctx.governanceAuthority.toBase58() ? "PASS" : "FAIL",
    detail: `admin ${state.adminAuthority}, governance authority PDA ${ctx.governanceAuthority.toBase58()}`,
    rootCause:
      String(state.adminAuthority) === ctx.governanceAuthority.toBase58()
        ? undefined
        : "the recorded admin is a wallet address",
    suggestedFix:
      String(state.adminAuthority) === ctx.governanceAuthority.toBase58()
        ? undefined
        : "a wallet-held admin can retune the split without a proposal — treat as CRITICAL",
  });

  const matrix: Array<{ who: string; signer: anchor.web3.Keypair; allowed: boolean }> = [
    { who: "operational authority", signer: ctx.operations, allowed: true },
    { who: "deployer/upgrade authority", signer: ctx.wallet.payer as anchor.web3.Keypair, allowed: false },
    { who: "unrelated wallet", signer: ctx.stranger, allowed: false },
  ];
  for (const m of matrix) {
    const outcome = await attempt(() =>
      ctx.programs.xmrj_treasury.methods
        .recordInflowXmrj(new anchor.BN(1))
        .accounts({ treasuryState: ctx.treasuryState, operationalAuthority: m.signer.publicKey })
        .signers([m.signer])
        .rpc(),
    );
    const ok = outcome.refused !== m.allowed;
    rec(sc, {
      name: `${m.who} records an inflow: ${m.allowed ? "allowed" : "refused"}`,
      status: ok ? "PASS" : "FAIL",
      detail: outcome.refused ? `refused (${errorName(outcome.error)})` : "accepted",
      error: ok ? undefined : outcome.error,
      rootCause: ok
        ? undefined
        : m.allowed
          ? "the operational key cannot do the one thing it exists for"
          : "a key that should not move money moved money",
      suggestedFix: ok ? undefined : "review has_one + signer on RecordInflowXmrj",
    });
  }
  for (const [ix, run] of [
    [
      "set_exchange_rate",
      () =>
        ctx.programs.xmrj_treasury.methods
          .setExchangeRate(new anchor.BN(1))
          .accounts({ treasuryState: ctx.treasuryState, adminAuthority: ctx.wallet.publicKey })
          .rpc(),
    ],
    [
      "set_operational_authority",
      () =>
        ctx.programs.xmrj_treasury.methods
          .setOperationalAuthority(ctx.stranger.publicKey)
          .accounts({ treasuryState: ctx.treasuryState, adminAuthority: ctx.operations.publicKey })
          .signers([ctx.operations])
          .rpc(),
    ],
  ] as Array<[string, () => Promise<unknown>]>) {
    const outcome = await attempt(run);
    rec(sc, {
      name: `direct ${ix} is refused (governance-only parameter, even for the operational key)`,
      status: outcome.refused ? "PASS" : "FAIL",
      detail: outcome.refused ? `refused (${errorName(outcome.error)})` : "ACCEPTED from a wallet",
      rootCause: outcome.refused
        ? undefined
        : "a parameter the plan reserves for governance can be changed by a wallet",
      suggestedFix: outcome.refused ? undefined : "review has_one = admin_authority on this setter",
    });
  }

  // The governance authority PDA itself is not in the signer matrix: a PDA
  // cannot be a transaction signer, and passing it as an unsigned account is
  // exactly what S4's program_data lookalike case covers for the other gate.
  rec(sc, {
    name: "the governance authority PDA can never sign a transaction (it is a PDA)",
    status: "PASS",
    detail:
      "not a runtime assertion but a structural fact: it has no private key, so it can only " +
      "sign inside a CPI from xmrj_governance — which is what makes every admin-gated " +
      "parameter proposal-gated",
  });
  sc.metrics = { signers_tested: matrix.length };
  close(sc);
}

// ── S7: recovery ────────────────────────────────────────────────────────
async function s7Recovery(ctx: Ctx): Promise<void> {
  const sc: Scenario = { id: "S7", title: TITLES.S7, status: "PASS", steps: [] };
  const before = await ctx.treasury();
  const refused = await attempt(() =>
    ctx.programs.xmrj_treasury.methods
      .setSplitRatio(new anchor.BN(1), new anchor.BN(1))
      .accounts({ treasuryState: ctx.treasuryState, adminAuthority: ctx.wallet.publicKey })
      .rpc(),
  );
  const afterRefusal = await ctx.treasury();
  const unchanged = JSON.stringify(before) === JSON.stringify(afterRefusal);
  rec(sc, {
    name: "a refused instruction leaves the account byte-identical",
    status: unchanged ? "PASS" : "FAIL",
    detail: refused.refused
      ? `refused (${errorName(refused.error)}); state ${unchanged ? "unchanged" : "CHANGED"}`
      : "the call was ACCEPTED — no refusal to recover from",
    rootCause: unchanged ? undefined : "a refused instruction still wrote to the account",
    suggestedFix: unchanged ? undefined : "look for a write before the failing require!()",
  });

  const landmine = await attempt(() =>
    ctx.programs.xmrj_treasury.methods
      .recordInflowXmrj(new anchor.BN(U64_MAX.toString()))
      .accounts({ treasuryState: ctx.treasuryState, operationalAuthority: ctx.operations.publicKey })
      .signers([ctx.operations])
      .rpc(),
  );
  const afterOverflow = await ctx.treasury();
  rec(sc, {
    name: "an arithmetic refusal (u64::MAX) also leaves the account untouched",
    status: landmine.refused && JSON.stringify(afterRefusal) === JSON.stringify(afterOverflow) ? "PASS" : "FAIL",
    detail: landmine.refused ? `refused (${errorName(landmine.error)})` : "the overflow was ACCEPTED",
    rootCause: landmine.refused ? undefined : "checked arithmetic did not cover this path",
    suggestedFix: landmine.refused ? undefined : "CRITICAL: an overflow would corrupt the counters",
  });

  const recovered = await attempt(() =>
    ctx.programs.xmrj_treasury.methods
      .recordInflowXmrj(new anchor.BN(1))
      .accounts({ treasuryState: ctx.treasuryState, operationalAuthority: ctx.operations.publicKey })
      .signers([ctx.operations])
      .rpc(),
  );
  rec(sc, {
    name: "the SAME account then accepts a valid instruction (no bricking)",
    status: recovered.refused ? "FAIL" : "PASS",
    error: recovered.refused ? recovered.error : undefined,
    detail: recovered.refused ? undefined : "recovered",
    rootCause: recovered.refused ? "a refused call left the account unusable" : undefined,
    suggestedFix: recovered.refused ? "check for state written before the failing guard" : undefined,
  });
  close(sc);
}

// ── S8: RPC failure handling ────────────────────────────────────────────
async function s8RpcFailure(ctx: Ctx): Promise<void> {
  const sc: Scenario = { id: "S8", title: TITLES.S8, status: "PASS", steps: [] };

  const t0 = Date.now();
  const dead = new anchor.web3.Connection("http://127.0.0.1:1", "confirmed");
  const deadOutcome = await attempt(() => dead.getLatestBlockhash());
  const deadMs = Date.now() - t0;
  rec(sc, {
    name: "a dead endpoint fails fast instead of hanging",
    status: deadOutcome.refused && deadMs < 60_000 ? "PASS" : "FAIL",
    detail: `${deadMs} ms — ${(deadOutcome.error ?? "").split("\n")[0].slice(0, 120)}`,
    rootCause: deadOutcome.refused ? undefined : "the client swallowed an unreachable endpoint",
    suggestedFix: deadOutcome.refused ? undefined : "an unreachable cluster must abort the run",
  });

  // A stale blockhash is the shape of every real retry: build, fail, rebuild
  // against a fresh blockhash, succeed. Each attempt is its own transaction id.
  //
  // The send goes through web3 directly rather than through the provider: a
  // provider send that fails is re-thrown as a `SendTransactionError` whose
  // `action` is unset, and its message is then the literal "Unknown action
  // 'undefined'" — which hides the very RPC error (429, blockhash not found, a
  // program refusal) this scenario exists to report. The pause between attempts
  // is deliberate too: the public devnet RPC rate-limits per IP, and the
  // runbook's failure playbook answers a 429 with backoff, not more attempts.
  const attempts: string[] = [];
  let recovered = false;
  let lastError: string | undefined;
  for (let i = 1; i <= 3; i += 1) {
    if (i > 1) await new Promise((r) => setTimeout(r, 1_500 * (i - 1)));
    try {
      const fresh = await ctx.connection.getLatestBlockhash("confirmed");
      const tx = new anchor.web3.Transaction({
        blockhash: fresh.blockhash,
        lastValidBlockHeight: fresh.lastValidBlockHeight,
        feePayer: ctx.wallet.publicKey,
      }).add(
        anchor.web3.SystemProgram.transfer({
          fromPubkey: ctx.wallet.publicKey,
          toPubkey: ctx.operations.publicKey,
          lamports: 1,
        }),
      );
      const signed = await ctx.wallet.signTransaction(tx);
      const sig = await ctx.connection.sendRawTransaction(signed.serialize(), {
        skipPreflight: true,
        maxRetries: 0,
      });
      await ctx.connection.confirmTransaction(
        { signature: sig, blockhash: fresh.blockhash, lastValidBlockHeight: fresh.lastValidBlockHeight },
        "confirmed",
      );
      attempts.push(sig);
      recovered = true;
      break;
    } catch (err) {
      const logs = (err as { logs?: string[] }).logs;
      lastError = [err instanceof Error ? err.message : String(err),
                   logs?.slice(-3).join(" | ")]
        .filter(Boolean).join(" — ");
      attempts.push(`attempt ${i}: ${lastError.split("\n")[0].slice(0, 160)}`);
    }
  }
  rec(sc, {
    name: "a retry with a FRESH blockhash per attempt succeeds",
    status: recovered ? "PASS" : "FAIL",
    detail: attempts.join(" | "),
    txs: attempts.filter((a) => !a.startsWith("attempt")),
    error: recovered ? undefined : lastError,
    rootCause: recovered ? undefined : "every attempt failed — unreachable cluster or rate limiting",
    suggestedFix: recovered
      ? undefined
      : "log the RPC's response body: a 429 needs backoff, a 5xx needs a second endpoint",
  });

  const permanent = await attempt(() =>
    withRetries("permanent-failure probe", 3, async () => {
      throw new Error("permanent test failure");
    }),
  );
  rec(sc, {
    name: "the retry budget is finite and reports the LAST error",
    status: permanent.refused && (permanent.error ?? "").includes("attempt(s)") ? "PASS" : "FAIL",
    detail: (permanent.error ?? "").split("\n")[0],
    error: permanent.refused ? undefined : permanent.error,
    rootCause: permanent.refused ? undefined : "a permanent failure was retried forever or hidden",
    suggestedFix: permanent.refused ? undefined : "bound the retries and surface the underlying error",
  });
  sc.metrics = { dead_endpoint_ms: deadMs, attempts: attempts.length };
  sc.notes = [
    "the public devnet RPC rate-limits per IP; the runbook's own failure playbook expects a " +
      "429 here and calls for backoff rather than a bigger retry count",
  ];
  close(sc);
}

// ── S9: governance round trip ───────────────────────────────────────────
/**
 * The only path by which a governance-held parameter can change: propose under
 * the NEXT id, be refused below threshold, reach threshold by approving, execute
 * the CPI, and prove the parameter moved ON CHAIN (read back from the treasury
 * state, not from the proposal). Every step is recorded with its signature; the
 * refusal is recorded as a step that PASSED, not as an error.
 *
 * The id comes from the state's own `proposal_count`, which is what
 * `propose_action` requires: the id must be the next one in the sequence. So
 * this scenario runs on EVERY run of the harness rather than once per cluster —
 * before finding F5 was fixed the proposal PDA was ["proposal",
 * governance_state] with `init`, and a state that had executed one action could
 * never make a second proposal.
 *
 * IT ENDS BY RESTORING PAR. Since the rate is READ on the funding path (finding
 * F2), leaving it wherever this scenario put it would silently move the funding
 * bound out from under any scenario that runs later, and out from under the
 * NEXT run of this harness. So the last thing it does is propose the par rate
 * back and re-read it.
 *
 * It SKIPs — with the reason — only when the governance state's owners are not
 * the keys this harness holds, because then the approvals cannot be signed for.
 */
async function s9GovernanceRoundTrip(ctx: Ctx): Promise<void> {
  const sc: Scenario = { id: "S9", title: TITLES.S9, status: "PASS", steps: [] };
  const G = ctx.programs.xmrj_governance;
  const T = ctx.programs.xmrj_treasury;

  const governance = await (async () => {
    try {
      return await G.account.governanceState.fetch(ctx.governanceState);
    } catch (err) {
      return null;
    }
  })();
  if (!governance) {
    close({
      id: "S9",
      title: TITLES.S9,
      status: "SKIP",
      steps: [{ name: "no governance state", status: "SKIP", detail: "initialize_governance was not run" }],
    });
    return;
  }

  const recorded = (governance.owners as anchor.web3.PublicKey[]).map((o) => o.toBase58());
  const ours = ctx.owners.filter((o) => recorded.includes(o.publicKey.toBase58()));
  if (ours.length < Number(governance.threshold)) {
    close({
      id: "S9",
      title: TITLES.S9,
      status: "SKIP",
      steps: [{
        name: "this run does not hold enough of the governance state's owners",
        status: "SKIP",
        detail:
          `owners on chain: ${recorded.join(", ")} (threshold ${governance.threshold}); ` +
          `this run can sign for ${ours.length || "none"} of them. The approval path belongs ` +
          "to whoever created this state — that is a property of the state, not a failure.",
        suggestedFix:
          "run against fresh state (§9 B) so this harness creates the governance state itself, " +
          "or re-run the devnet suite with the keys that own it",
      }],
    });
    return;
  }

  // Fund the owners that will pay their own fees (they must, since each
  // approval is its own transaction signed by its own key).
  for (const owner of ours) {
    const balance = await ctx.connection.getBalance(owner.publicKey);
    if (balance < 10_000_000) {
      const outcome = await attempt(() =>
        withRetries(`fund owner ${owner.publicKey.toBase58()}`, 2, () =>
          ctx.provider.sendAndConfirm(
            new anchor.web3.Transaction().add(
              anchor.web3.SystemProgram.transfer({
                fromPubkey: ctx.wallet.publicKey,
                toPubkey: owner.publicKey,
                lamports: 50_000_000 - balance,
              }),
            ),
            [],
          ),
        ),
      );
      rec(sc, {
        name: `owner ${owner.publicKey.toBase58()} funded for its own approval fee`,
        status: outcome.refused ? "FAIL" : "PASS",
        error: outcome.refused ? outcome.error : undefined,
        rootCause: outcome.refused ? "the deployer could not fund an owner" : undefined,
        suggestedFix: outcome.refused ? "top the deployer up" : undefined,
      });
    }
  }

  const before = await ctx.treasury();
  // THE NEXT ID, from the chain: this is the id `propose_action` will accept and
  // any other id is refused, so the harness cannot silently reuse an account.
  const governanceNow = await G.account.governanceState.fetch(ctx.governanceState);
  const proposalId = Number(governanceNow.proposalCount.toString());
  const proposalAddressNow = ctx.proposals(proposalId);
  // A value that DIFFERS from the current rate (the assertion is a change, not
  // a write), and that is NOT par — par is what the restore at the end sets, and
  // a no-op change would make that read-back vacuous.
  const currentRate = Number(before.exchangeRateBps.toString());
  const target = currentRate === 12_345 ? 6_789 : 12_345;
  const propose = await attempt(() =>
    withRetries("propose_action", 2, () =>
      G.methods
        .proposeAction(new anchor.BN(proposalId), {
          setExchangeRate: { exchangeRateBps: new anchor.BN(target) },
        })
        .accounts({
          governanceState: ctx.governanceState,
          proposal: proposalAddressNow,
          proposer: ours[0].publicKey,
          systemProgram: anchor.web3.SystemProgram.programId,
        })
        .signers([ours[0]])
        .rpc(),
    ),
  );
  rec(sc, {
    name: `propose_action creates proposal ${proposalId} (the next id in the state's own sequence)`,
    status: propose.refused ? "FAIL" : "PASS",
    error: propose.refused ? propose.error : undefined,
    detail: propose.refused
      ? undefined
      : `proposed SetExchangeRate(${target}); the PDA is ["proposal", governance_state, ${proposalId} as an 8-byte LE u64], so this state can hold as many proposals as it makes`,
    txs: sig(propose),
    rootCause: propose.refused
      ? "propose_action was refused — a stale proposal_count, a non-existent id, or a proposer without fees"
      : undefined,
    suggestedFix: propose.refused
      ? "re-read governanceState.proposalCount and the harness's derived PDA before assuming a program fault"
      : undefined,
  });
  if (propose.refused) {
    sc.notes = [
      "every run makes its own proposal, so a refusal here is a REAL failure — with ids there " +
        "is no 'this scenario has already been used' state to hide behind (finding F5)",
    ];
    close(sc);
    return;
  }

  const strangerApproves = await attempt(() =>
    G.methods
      .approveAction(new anchor.BN(proposalId))
      .accounts({ governanceState: ctx.governanceState, proposal: proposalAddressNow, owner: ctx.stranger.publicKey })
      .signers([ctx.stranger])
      .rpc(),
  );
  rec(sc, {
    name: "approve_action from a wallet that is not an owner is REFUSED (NotOwner)",
    status: strangerApproves.refused ? "PASS" : "FAIL",
    detail: strangerApproves.refused ? `refused (${errorName(strangerApproves.error)})` : "ACCEPTED — a stranger approved a proposal",
    rootCause: strangerApproves.refused ? undefined : "the owner set is not enforced on approve_action",
    suggestedFix: strangerApproves.refused ? undefined : "CRITICAL: review the NotOwner check",
  });

  const approve1 = await attempt(() =>
    G.methods
      .approveAction(new anchor.BN(proposalId))
      .accounts({ governanceState: ctx.governanceState, proposal: proposalAddressNow, owner: ours[0].publicKey })
      .signers([ours[0]])
      .rpc(),
  );
  const afterApprove1 = await G.account.proposal.fetch(proposalAddressNow);
  rec(sc, {
    name: `owner 1 approves: approvals now ${afterApprove1.approvals}, still below threshold ${governance.threshold}`,
    status: !approve1.refused && Number(afterApprove1.approvals) === 1 ? "PASS" : "FAIL",
    detail: approve1.refused ? `refused (${errorName(approve1.error)})` : `approvals ${afterApprove1.approvals}`,
    error: approve1.refused ? approve1.error : undefined,
    rootCause: approve1.refused ? "an owner's approval was refused" : undefined,
    suggestedFix: approve1.refused ? "check the owner's balance (it pays its own fee)" : undefined,
  });

  const earlyExecute = await attempt(() =>
    G.methods
      .executeAction(new anchor.BN(proposalId))
      .accounts({
        governanceState: ctx.governanceState,
        proposal: proposalAddressNow,
        treasuryState: ctx.treasuryState,
        jackpotState: ctx.jackpotState,
        treasuryProgram: T.programId,
        jackpotProgram: ctx.programs.xmrj_jackpot.programId,
        governanceAuthority: ctx.governanceAuthority,
        targetProgram: T.programId,
      })
      .rpc(),
  );
  const unchangedAfterEarly = await ctx.treasury();
  rec(sc, {
    name: "execute_action BELOW the threshold is refused (ThresholdNotMet) and changes nothing",
    status: earlyExecute.refused ? "PASS" : "FAIL",
    detail: earlyExecute.refused
      ? `refused (${errorName(earlyExecute.error)})`
      : `ACCEPTED at ${afterApprove1.approvals} approvals`,
    error: earlyExecute.refused ? undefined : earlyExecute.error,
    rootCause: earlyExecute.refused
      ? undefined
      : "a proposal executed before reaching its threshold — the single most important guard in this program",
    suggestedFix: earlyExecute.refused ? undefined : "CRITICAL: stop, the threshold check is not firing",
  });
  if (String(unchangedAfterEarly.exchangeRateBps) !== String(before.exchangeRateBps)) {
    rec(sc, {
      name: "the parameter did not move on the refused execute",
      status: "FAIL",
      detail: `exchange_rate_bps is now ${unchangedAfterEarly.exchangeRateBps}, was ${before.exchangeRateBps}`,
      rootCause: "a refused execute_action still performed the CPI",
      suggestedFix: "CRITICAL",
    });
  }

  let approvals = Number(afterApprove1.approvals);
  while (approvals < Number(governance.threshold) && approvals < ours.length) {
    const owner = ours[approvals];
    const outcome = await attempt(() =>
      withRetries("approve_action", 2, () =>
        G.methods
          .approveAction(new anchor.BN(proposalId))
          .accounts({ governanceState: ctx.governanceState, proposal: proposalAddressNow, owner: owner.publicKey })
          .signers([owner])
          .rpc(),
      ),
    );
    if (outcome.refused) {
      rec(sc, {
        name: `approval ${approvals + 1} failed`,
        status: "FAIL",
        error: outcome.error,
        rootCause: "approve_action was refused",
        suggestedFix: "check the owner's balance and the proposal's recorded approvals",
      });
      break;
    }
    approvals += 1;
    rec(sc, { name: `owner ${approvals} approves`, status: "PASS", detail: `approvals ${approvals}` });
  }

  const execute = await attempt(() =>
    withRetries("execute_action", 2, () =>
      G.methods
        .executeAction(new anchor.BN(proposalId))
        .accounts({
          governanceState: ctx.governanceState,
          proposal: proposalAddressNow,
          treasuryState: ctx.treasuryState,
          jackpotState: ctx.jackpotState,
          treasuryProgram: T.programId,
          jackpotProgram: ctx.programs.xmrj_jackpot.programId,
          governanceAuthority: ctx.governanceAuthority,
          targetProgram: T.programId,
        })
        .rpc(),
    ),
  );
  const afterExecute = await ctx.treasury();
  rec(sc, {
    name: `execute_action at ${approvals}/${governance.threshold} approvals performs the CPI`,
    status: !execute.refused ? "PASS" : "FAIL",
    txs: sig(execute),
    detail: execute.refused ? undefined : `exchange_rate_bps ${before.exchangeRateBps} -> ${afterExecute.exchangeRateBps}`,
    error: execute.refused ? execute.error : undefined,
    rootCause: execute.refused ? "an approved proposal could not be executed" : undefined,
    suggestedFix: execute.refused ? "check the CPI account list against the IDL" : undefined,
  });
  rec(sc, {
    name: "the parameter on chain equals the proposed value (read back, not assumed)",
    status: !execute.refused && String(afterExecute.exchangeRateBps) === String(target) ? "PASS" : "FAIL",
    detail: `exchange_rate_bps = ${afterExecute.exchangeRateBps}, proposed ${target}`,
    error: execute.refused ? execute.error : undefined,
    rootCause:
      !execute.refused && String(afterExecute.exchangeRateBps) !== String(target)
        ? "the CPI reported success but the treasury state did not take the value"
        : undefined,
    suggestedFix:
      !execute.refused && String(afterExecute.exchangeRateBps) !== String(target)
        ? "read the CPI's inner instructions; a mismatched account list can CPI into the wrong state"
        : undefined,
  });

  const replay = await attempt(() =>
    G.methods
      .executeAction(new anchor.BN(proposalId))
      .accounts({
        governanceState: ctx.governanceState,
        proposal: proposalAddressNow,
        treasuryState: ctx.treasuryState,
        jackpotState: ctx.jackpotState,
        treasuryProgram: T.programId,
        jackpotProgram: ctx.programs.xmrj_jackpot.programId,
        governanceAuthority: ctx.governanceAuthority,
        targetProgram: T.programId,
      })
      .rpc(),
  );
  rec(sc, {
    name: "re-executing the same proposal is REFUSED (AlreadyExecuted) — no replay",
    status: replay.refused ? "PASS" : "FAIL",
    detail: replay.refused ? `refused (${errorName(replay.error)})` : "the proposal executed TWICE",
    rootCause: replay.refused ? undefined : "CRITICAL: a proposal is replayable",
    suggestedFix: replay.refused ? undefined : "review the executed flag's check-and-set",
  });

  // RESTORE PAR, with a proposal of its own. The rate is READ on the funding
  // path, so leaving this scenario's value behind would move the bound under
  // every later scenario and under the next run of this harness.
  const restore = await runProposalAs(ctx, ours, {
    setExchangeRate: { exchangeRateBps: new anchor.BN(PAR_BPS.toString()) },
  });
  const restored = await ctx.treasury();
  rec(sc, {
    name: `a follow-up proposal (id ${restore.id}) sets the rate back to PAR (${PAR_BPS} bps)`,
    status: !restore.refused && String(restored.exchangeRateBps) === String(PAR_BPS) ? "PASS" : "FAIL",
    txs: restore.txs,
    detail: `exchange_rate_bps = ${restored.exchangeRateBps}`,
    error: restore.refused ? restore.error : undefined,
    rootCause: restore.refused
      ? "the state could not take a second proposal — which is what finding F5 was about"
      : undefined,
    suggestedFix: restore.refused
      ? "check the derive of proposal_count against the id this harness sent"
      : undefined,
  });

  sc.metrics = {
    threshold: Number(governance.threshold),
    approvals,
    target_exchange_rate_bps: target,
    proposals_used: `${proposalId},${restore.id}`,
  };
  sc.notes = [
    "the rate is restored to par here because it is READ on the funding path: leaving this " +
      "scenario's value behind would silently move the funding bound for later scenarios",
  ];
  close(sc);
}

/**
 * TAKE ONE PROPOSAL THROUGH THE WHOLE PATH — propose it under the NEXT id, get
 * it over the threshold with distinct owners, execute it — and report the id and
 * the signatures, so a caller can assert the EFFECT rather than the act of
 * executing.
 *
 * The id is read from the chain (`proposal_count`) because the program requires
 * exactly that id; a harness that guessed one would be testing the refusal path
 * instead of the write path. Returns `refused: true` at the FIRST step that
 * fails, with that step's error, so the caller can report which leg broke.
 */
async function runProposalAs(
  ctx: Ctx,
  owners: anchor.web3.Keypair[],
  action: Record<string, unknown>,
): Promise<{ id: number; refused: boolean; error?: string; txs: string[] }> {
  const G = ctx.programs.xmrj_governance;
  const state = await G.account.governanceState.fetch(ctx.governanceState);
  const id = Number(state.proposalCount.toString());
  const address = ctx.proposals(id);

  const propose = await attempt(() =>
    withRetries("propose_action", 2, () =>
      G.methods
        .proposeAction(new anchor.BN(id), action)
        .accounts({
          governanceState: ctx.governanceState,
          proposal: address,
          proposer: owners[0].publicKey,
          systemProgram: anchor.web3.SystemProgram.programId,
        })
        .signers([owners[0]])
        .rpc(),
    ),
  );
  if (propose.refused) return { id, refused: true, error: propose.error, txs: [] };
  const txs: string[] = sig(propose);

  for (let i = 0; i < Number(state.threshold) && i < owners.length; i += 1) {
    const approval = await attempt(() =>
      withRetries("approve_action", 2, () =>
        G.methods
          .approveAction(new anchor.BN(id))
          .accounts({
            governanceState: ctx.governanceState,
            proposal: address,
            owner: owners[i].publicKey,
          })
          .signers([owners[i]])
          .rpc(),
      ),
    );
    if (approval.refused) return { id, refused: true, error: approval.error, txs };
    txs.push(...sig(approval));
  }

  const execute = await attempt(() =>
    withRetries("execute_action", 2, () =>
      G.methods
        .executeAction(new anchor.BN(id))
        .accounts({
          governanceState: ctx.governanceState,
          proposal: address,
          treasuryState: ctx.treasuryState,
          jackpotState: ctx.jackpotState,
          treasuryProgram: ctx.programs.xmrj_treasury.programId,
          jackpotProgram: ctx.programs.xmrj_jackpot.programId,
          governanceAuthority: ctx.governanceAuthority,
          targetProgram: ctx.programs.xmrj_treasury.programId,
        })
        .rpc(),
    ),
  );
  txs.push(...sig(execute));
  return { id, refused: execute.refused, error: execute.error, txs };
}

// ── S10: governance PAYS the jackpot out ────────────────────────────────
/**
 * THE COUNTERPART OF F1. S9 proves a proposal can change a parameter; this one
 * proves a proposal can MOVE THE MONEY — that a funded jackpot can be paid. The
 * finding was that it could not be: `trigger_payout` demands the governance
 * authority PDA as a signer, and no `GovernanceAction` variant called it, so the
 * balance was a one-way ratchet.
 *
 * What is asserted, in order: a TriggerPayout proposal is proposed, approved and
 * executed; the jackpot's balance falls by exactly the amount, `total_payouts_xmr`
 * rises by it, `last_winner` is the winner, the identity
 * `balance + total_payouts == total_funded` still closes, and the TREASURY does
 * not move. Then the controls: a DIRECT `trigger_payout` from a wallet is
 * refused, the PDA passed UNSIGNED is refused (the address is right and only the
 * signature is missing), an amount above the governance cap is refused, an
 * amount above the live balance is refused, and the executed proposal cannot be
 * executed twice.
 */
async function s10GovernancePayout(ctx: Ctx): Promise<void> {
  const sc: Scenario = { id: "S10", title: TITLES.S10, status: "PASS", steps: [], metrics: {} };
  const G = ctx.programs.xmrj_governance;
  const J = ctx.programs.xmrj_jackpot;
  const T = ctx.programs.xmrj_treasury;

  const governance = await (async () => {
    try {
      return await G.account.governanceState.fetch(ctx.governanceState);
    } catch {
      return null;
    }
  })();
  if (!governance) {
    close({
      id: "S10",
      title: TITLES.S10,
      status: "SKIP",
      steps: [{ name: "no governance state", status: "SKIP", detail: "initialize_governance was not run — S0 creates it" }],
    });
    return;
  }

  const recorded = (governance.owners as anchor.web3.PublicKey[]).map((o) => o.toBase58());
  const ours = ctx.owners.filter((o) => recorded.includes(o.publicKey.toBase58()));
  if (ours.length < Number(governance.threshold)) {
    close({
      id: "S10",
      title: TITLES.S10,
      status: "SKIP",
      steps: [{
        name: "this run does not hold enough of the governance state's owners",
        status: "SKIP",
        detail:
          `owners on chain: ${recorded.join(", ")} (threshold ${governance.threshold}); this run can ` +
          `sign for ${ours.length || "none"} of them, so no payout proposal can reach its threshold.`,
        suggestedFix: "run against fresh state so this harness creates the governance state itself",
      }],
    });
    return;
  }

  const before = await ctx.jackpot();
  const treasuryBefore = await ctx.treasury();
  const balance = BigInt(before.jackpotBalanceXmr.toString());
  const capBps = BigInt(before.maxPayoutBps.toString());
  const capAmount = (balance * capBps) / BPS_TOTAL;

  // THE AMOUNT, chosen from the LIVE state so the scenario is not quietly
  // testing the cap: a tenth of the balance, at least one unit, never above the
  // cap the chain currently enforces.
  const amount = (() => {
    const tenth = balance / 10n;
    const wanted = tenth > 0n ? tenth : 1n;
    return wanted <= capAmount ? wanted : capAmount;
  })();

  if (balance === 0n || amount === 0n) {
    close({
      id: "S10",
      title: TITLES.S10,
      status: "SKIP",
      steps: [{
        name: "the jackpot holds nothing to pay out",
        status: "SKIP",
        detail:
          `jackpot_balance_xmr is ${before.jackpotBalanceXmr} and the governance cap ` +
          `(max_payout_bps ${before.maxPayoutBps}) allows ${capAmount}. Run S1/S5 (or the ` +
          "runbook's funding step) first: a payout of zero would prove nothing.",
        suggestedFix: "fund the jackpot through the treasury's CPI (S1's inflow+funding pairs) and re-run",
      }],
    });
    return;
  }

  // THE NEGATIVE CONTROLS FIRST, while the balance is still what it was: each
  // of them must be refused, and the state must be unmoved afterwards.
  const controls: Array<{ name: string; expect: string; run: () => Promise<unknown> }> = [
    {
      name: "trigger_payout called DIRECTLY by the harness wallet",
      expect: "UnauthorizedAdmin — no wallet can pay the jackpot; the signer must be the governance authority PDA, which only a governance CPI can produce",
      run: () =>
        J.methods
          .triggerPayout(ctx.stranger.publicKey, new anchor.BN(amount.toString()))
          .accounts({ jackpotState: ctx.jackpotState, adminAuthority: ctx.wallet.publicKey })
          .rpc(),
    },
    {
      name: "trigger_payout with the governance authority PDA passed UNSIGNED",
      expect: "a signature error — the RIGHT address without the signature, which is the case a key comparison cannot catch",
      run: () =>
        J.methods
          .triggerPayout(ctx.stranger.publicKey, new anchor.BN(amount.toString()))
          .accounts({ jackpotState: ctx.jackpotState, adminAuthority: ctx.governanceAuthority })
          .rpc(),
    },
  ];
  for (const c of controls) {
    const outcome = await attempt(c.run);
    rec(sc, {
      name: c.name,
      status: outcome.refused ? "PASS" : "FAIL",
      detail: outcome.refused ? `refused (${errorName(outcome.error)}) — expected ${c.expect}` : `ACCEPTED — ${c.expect}`,
      error: outcome.refused ? undefined : outcome.error,
      rootCause: outcome.refused ? undefined : "a payout gate did not fire for a caller that should not be able to pay",
      suggestedFix: outcome.refused ? undefined : "CRITICAL: re-read TriggerPayout's constraints before any mainnet step",
    });
  }

  const winner = ctx.stranger.publicKey;

  // CONTROL 3 — the GOVERNANCE path refuses an amount the jackpot does not
  // hold. This one cannot be asserted with a direct call: `trigger_payout`
  // checks the authority's signature in its account CONSTRAINTS, so an unsigned
  // attempt is refused before the instruction body — and in particular before
  // the balance check. The only caller that reaches the body is a signed CPI, so
  // the boundary is only observable through a real approved proposal.
  const overBalance = await runProposalAs(ctx, ours, {
    triggerPayout: { winner, amountXmr: new anchor.BN((balance + 1n).toString()) },
  });
  const overBalanceError = overBalance.error ?? "";
  // The named error, or its code (0x1773 = 6003, the fourth variant of the
  // jackpot's ErrorCode), whichever the RPC surfaced.
  const overBalanceNamed =
    /InsufficientJackpotBalance/.test(overBalanceError) || /\b(6003|0x1773)\b/i.test(overBalanceError);
  rec(sc, {
    name: `proposal ${overBalance.id} asks for MORE than the balance and is REFUSED at execution`,
    status: overBalance.refused && overBalanceNamed ? "PASS" : "FAIL",
    txs: overBalance.txs,
    detail: overBalance.refused
      ? `refused (${errorName(overBalance.error)}) as expected — the governance path is not a blank cheque`
      : `ACCEPTED a payout of ${balance + 1n} against a balance of ${balance}`,
    error: overBalance.refused && overBalanceNamed ? undefined : overBalanceError,
    rootCause:
      !overBalance.refused
        ? "CRITICAL: trigger_payout paid more than the jackpot held"
        : overBalanceNamed
          ? undefined
          : "the proposal was refused for some other reason, so the balance bound was not what was proven",
    suggestedFix: overBalanceNamed
      ? undefined
      : "re-run with the program logs; a missing InsufficientJackpotBalance means the refusal happened earlier",
  });

  const unmoved = await ctx.jackpot();
  rec(sc, {
    name: "the refused payout attempts changed nothing",
    status: BigInt(unmoved.jackpotBalanceXmr.toString()) === balance ? "PASS" : "FAIL",
    detail: `jackpot_balance_xmr still ${unmoved.jackpotBalanceXmr} (was ${balance})`,
    rootCause: BigInt(unmoved.jackpotBalanceXmr.toString()) === balance ? undefined : "a refused trigger_payout still wrote the state",
    suggestedFix: BigInt(unmoved.jackpotBalanceXmr.toString()) === balance ? undefined : "CRITICAL",
  });

  // THE REAL THING: an approved proposal carries TriggerPayout, and the CPI is
  // signed by the governance authority PDA — the one signer the jackpot accepts.
  const payout = await runProposalAs(ctx, ours, {
    triggerPayout: { winner, amountXmr: new anchor.BN(amount.toString()) },
  });
  const after = await ctx.jackpot();
  rec(sc, {
    name: `proposal ${payout.id} carries TriggerPayout(${amount}) and executes through the CPI`,
    status: !payout.refused ? "PASS" : "FAIL",
    txs: payout.txs,
    detail: payout.refused
      ? undefined
      : `jackpot_balance_xmr ${balance} -> ${after.jackpotBalanceXmr}, total_payouts_xmr -> ${after.totalPayoutsXmr}`,
    error: payout.refused ? payout.error : undefined,
    rootCause: payout.refused
      ? "a funded jackpot could not be paid through a governance proposal — this is finding F1 un-fixed"
      : undefined,
    suggestedFix: payout.refused
      ? "check that TriggerPayout's arm passes the governance authority and is signed with its seeds"
      : undefined,
  });

  const balanceNow = BigInt(after.jackpotBalanceXmr.toString());
  const payoutsNow = BigInt(after.totalPayoutsXmr.toString());
  const fundedNow = BigInt(after.totalFundedXmr.toString());
  rec(sc, {
    name: "the balance fell by exactly the amount, and total_payouts_xmr rose by it",
    status: balanceNow === balance - amount && payoutsNow === BigInt(before.totalPayoutsXmr.toString()) + amount ? "PASS" : "FAIL",
    detail: `balance ${balance} - ${amount} = ${balanceNow}; payouts ${before.totalPayoutsXmr} + ${amount} = ${payoutsNow}`,
    rootCause: balanceNow === balance - amount ? undefined : "the payout did not move the balance by exactly the requested amount",
    suggestedFix: balanceNow === balance - amount ? undefined : "read the CPI's inner instructions; a partially-applied payout is a critical accounting fault",
  });
  rec(sc, {
    name: "last_winner is the winner the proposal named",
    status: after.lastWinner.toBase58() === winner.toBase58() ? "PASS" : "FAIL",
    detail: `last_winner = ${after.lastWinner.toBase58()}, named ${winner.toBase58()}`,
    rootCause: after.lastWinner.toBase58() === winner.toBase58() ? undefined : "the payout recorded a different winner",
    suggestedFix: after.lastWinner.toBase58() === winner.toBase58() ? undefined : "CRITICAL",
  });
  rec(sc, {
    name: "THE ACCOUNTING CLOSES: balance + total_payouts == total_funded, after a payout",
    status: balanceNow + payoutsNow === fundedNow ? "PASS" : "FAIL",
    detail: `${balanceNow} + ${payoutsNow} == ${fundedNow}`,
    rootCause: balanceNow + payoutsNow === fundedNow ? undefined : "the jackpot's own counters do not reconcile after a payout",
    suggestedFix: balanceNow + payoutsNow === fundedNow ? undefined : "CRITICAL: a payout moved one counter and not the other",
  });
  const treasuryAfter = await ctx.treasury();
  rec(sc, {
    name: "the payout did NOT move the treasury: it is the jackpot's own business",
    status: String(treasuryAfter.totalJackpotFundedXmr) === String(treasuryBefore.totalJackpotFundedXmr) ? "PASS" : "FAIL",
    detail: `treasury.total_jackpot_funded_xmr = ${treasuryAfter.totalJackpotFundedXmr} (was ${treasuryBefore.totalJackpotFundedXmr})`,
    rootCause: String(treasuryAfter.totalJackpotFundedXmr) === String(treasuryBefore.totalJackpotFundedXmr) ? undefined : "a payout wrote the TREASURY",
    suggestedFix: String(treasuryAfter.totalJackpotFundedXmr) === String(treasuryBefore.totalJackpotFundedXmr) ? undefined : "CRITICAL: the CPI reached a program it should not have",
  });

  // NO REPLAY. The approved proposal is spent.
  const replay = await attempt(() =>
    G.methods
      .executeAction(new anchor.BN(payout.id))
      .accounts({
        governanceState: ctx.governanceState,
        proposal: ctx.proposals(payout.id),
        treasuryState: ctx.treasuryState,
        jackpotState: ctx.jackpotState,
        treasuryProgram: T.programId,
        jackpotProgram: J.programId,
        governanceAuthority: ctx.governanceAuthority,
        targetProgram: T.programId,
      })
      .rpc(),
  );
  rec(sc, {
    name: "re-executing the payout proposal is REFUSED (AlreadyExecuted) — a payout cannot be replayed",
    status: replay.refused ? "PASS" : "FAIL",
    detail: replay.refused ? `refused (${errorName(replay.error)})` : "the payout executed TWICE",
    rootCause: replay.refused ? undefined : "CRITICAL: a payout proposal is replayable",
    suggestedFix: replay.refused ? undefined : "review the executed flag on Proposal",
  });
  const finalState = await ctx.jackpot();
  rec(sc, {
    name: "the replay changed nothing",
    status: BigInt(finalState.totalPayoutsXmr.toString()) === payoutsNow ? "PASS" : "FAIL",
    detail: `total_payouts_xmr still ${finalState.totalPayoutsXmr}`,
    rootCause: BigInt(finalState.totalPayoutsXmr.toString()) === payoutsNow ? undefined : "the refused replay still wrote the state",
    suggestedFix: BigInt(finalState.totalPayoutsXmr.toString()) === payoutsNow ? undefined : "CRITICAL",
  });

  sc.metrics = {
    proposal_id: payout.id,
    payout_amount_xmr: amount.toString(),
    balance_before: balance.toString(),
    balance_after: balanceNow.toString(),
    total_payouts_after: payoutsNow.toString(),
    max_payout_bps: capBps.toString(),
  };
  sc.notes = [
    "a payout is an AUTHORIZATION and an ACCOUNTING ENTRY: last_winner is recorded and the " +
      "jackpot's counters move; the XMR itself leaves the jackpot's Monero wallet off chain",
    "the amount is a tenth of the live balance, capped by max_payout_bps, so this scenario tests " +
      "the payout rather than the cap",
  ];
  close(sc);
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error("HARNESS ERROR:", err);
    writeReport({ mode: "harness_error", error: String(err), ok: false }, false);
    process.exit(1);
  });
