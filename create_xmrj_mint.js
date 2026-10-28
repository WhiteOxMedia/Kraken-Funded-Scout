#!/usr/bin/env node
/**
 * create_xmrj_mint.js — SECTION 2 (and 3.1) OF XMRJ_SOLANA_DEPLOYMENT_PLAN.md, ONCE.
 *
 * WHAT THIS DOES:
 *   2.1  creates the XMRJ SPL mint on devnet
 *        (name "XMR Jackpot Treasury Token", symbol XMRJ, decimals 9)
 *   2.2  creates the four allocation accounts, all owned by the deployer: the
 *        treasury reserve is the mint's ASSOCIATED token account (what
 *        `spl-token create-account <MINT>` makes), and the other three are
 *        plain token accounts, because an ATA is unique per (owner, mint) —
 *        one wallet cannot have four of them. Their account keypairs are
 *        throwaway: authority is the owner field, so only the address matters.
 *   2.3  mints the whole FIXED cap, 100,000,000 XMRJ, once, split
 *        50 / 25 / 15 / 10 into those four accounts (decided Oct 26)
 *   --lock  runs 3.1: disables the mint authority. ONE-WAY — never a default.
 *
 * Then it writes the printed addresses to xmrj_devnet_token.json.
 *
 * WHY THE SPLIT IS MINTED HERE AND NOT LEFT OPEN: 2.3 and 3 were decisions, not
 * steps, and they are decided — fixed supply, no emission schedule. The cap is
 * minted in full at genesis so that the disable in 3.1 can be the last thing
 * that ever touches supply. Minting in tranches later would mean keeping a live
 * mint authority around, which is the thing 3.2 rules out.
 *
 * WHAT THE SPLIT IS NOT: custody. All four accounts are ATAs of one key, so one
 * signer can move any of them; and the team/foundation tranche has NO on-chain
 * vesting, because this workspace has no vesting program. Both limits are
 * recorded in the plan (2.3) rather than implied away here.
 *
 * WHY NODE AND NOT `spl-token`: neither the Solana CLI nor the SPL Token CLI
 * is installed on this box, and the native-Windows Agave installer wants an
 * elevated shell. package.json already declares @anchor-lang/core, which
 * re-exports web3.js, so the instructions are encoded directly below — no new
 * dependency, no admin prompt, same bytes the CLI would send. (The encodings
 * were diffed against @solana/spl-token's own builders before the first run:
 * tag 20 with a ONE-byte option tag for the freeze key — the account STATE uses
 * a four-byte COption for the same field, which is why parseMint() differs —
 * tag 7 for MintTo, and tag 6 with a one-byte COption::None to disable.)
 *
 * THE KEYPAIR is the plan's 1.2 path: $HOME/.config/solana/id.json, which is
 * also Anchor.toml's [provider] wallet. solana-keygen's JSON-array format —
 * i.e. the file `solana-keygen new` writes — never committed (see .gitignore).
 *
 * SAFETY RAIL: the genesis hash must be devnet's. A URL can carry a stale
 * label or a proxy; the hash cannot. This script will not touch another chain.
 *
 *   node create_xmrj_mint.js            # mint + allocation, authority live
 *   node create_xmrj_mint.js --lock     # …and then disable the authority
 */
"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
// web3.js through the client package this workspace pins (package.json), so
// the script needs nothing that a fresh `npm ci` would not install.
const {
  Connection,
  Keypair,
  PublicKey,
  SystemProgram,
  Transaction,
  TransactionInstruction,
  sendAndConfirmTransaction,
  LAMPORTS_PER_SOL,
} = require("@anchor-lang/core").web3;

const RPC_URL = "https://api.devnet.solana.com";
// devnet's genesis hash. The e2e suite (tests/xmrj_devnet_e2e.ts) pins the
// same value for the same reason.
const DEVNET_GENESIS = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG";

const TOKEN_PROGRAM_ID = new PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");
const ATA_PROGRAM_ID = new PublicKey("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL");
// InitializeAccount still reads the rent sysvar (unlike InitializeMint2), and
// it is a REQUIRED account: four keys, owner third, rent fourth. Leaving it
// out fails the instruction rather than defaulting anything.
const SYSVAR_RENT = new PublicKey("SysvarRent111111111111111111111111111111111");

const MINT_SPACE = 82; // SPL Mint: authority+supply+decimals+flags
const TOKEN_ACCOUNT_SPACE = 165; // SPL Account: mint+owner+amount+state
const DECIMALS = 9;

// ── the decided tokenomics (plan 2.3) ───────────────────────────────────────
// FIXED cap. There is no emission schedule; 3.1 is the chosen branch.
const MAX_SUPPLY = 100_000_000n; // whole XMRJ
const MAX_SUPPLY_BASE = MAX_SUPPLY * 10n ** BigInt(DECIMALS);
// bps must total 10000 — asserted below, so a typo here cannot mint a
// different cap than the plan states.
const TRANCHE_BPS = [
  { key: "treasury_reserve", label: "Treasury reserve", bps: 5000 },
  { key: "jackpot_rewards", label: "Jackpot & rewards", bps: 2500 },
  { key: "community_growth", label: "Community growth", bps: 1500 },
  { key: "team_foundation", label: "Team / foundation", bps: 1000 },
];

const RECORD = path.join(__dirname, "xmrj_devnet_token.json");
const KEYPAIR_PATH = process.env.XMRJ_KEYPAIR
  ? path.resolve(process.env.XMRJ_KEYPAIR)
  : path.join(os.homedir(), ".config", "solana", "id.json");

/** Base units for one tranche of the fixed cap. */
function trancheAmount(bps) {
  if (!Number.isInteger(bps) || bps < 0) throw new Error(`bad bps ${bps}`);
  return (MAX_SUPPLY_BASE * BigInt(bps)) / 10000n;
}

function assertAllocation() {
  const total = TRANCHE_BPS.reduce((n, t) => n + t.bps, 0);
  if (total !== 10000) throw new Error(`tranche bps total ${total}, not 10000`);
  const sum = TRANCHE_BPS.reduce((n, t) => n + trancheAmount(t.bps), 0n);
  if (sum !== MAX_SUPPLY_BASE) {
    throw new Error(`tranches sum to ${sum} base units, not the cap ${MAX_SUPPLY_BASE}`);
  }
}

/**
 * SPL Token `InitializeMint2` (tag 20):
 *   u8 tag=20 | u8 decimals | Pubkey mint_authority | Option<Pubkey> freeze
 * The option here is Borsh's ONE-byte tag — not the four-byte COption used in
 * the 82-byte mint ACCOUNT state that parseMint() below reads. Two different
 * encodings of the same idea, and mixing them up is a silent malformed
 * instruction: 35 bytes with no freeze authority, 67 with one.
 */
function initializeMint2(mint, authority, freezeAuthority) {
  const data = Buffer.alloc(freezeAuthority ? 67 : 35);
  data.writeUInt8(20, 0);
  data.writeUInt8(DECIMALS, 1);
  authority.toBuffer().copy(data, 2);
  data.writeUInt8(freezeAuthority ? 1 : 0, 34);
  if (freezeAuthority) freezeAuthority.toBuffer().copy(data, 35);
  return new TransactionInstruction({
    programId: TOKEN_PROGRAM_ID,
    keys: [{ pubkey: mint, isSigner: false, isWritable: true }],
    data,
  });
}

/** SPL Token `InitializeAccount` (tag 1): no operands; rent sysvar required. */
function initializeAccount(account, mint, owner) {
  return new TransactionInstruction({
    programId: TOKEN_PROGRAM_ID,
    keys: [
      { pubkey: account, isSigner: false, isWritable: true },
      { pubkey: mint, isSigner: false, isReadOnly: true },
      { pubkey: owner, isSigner: false, isReadOnly: true },
      { pubkey: SYSVAR_RENT, isSigner: false, isReadOnly: true },
    ],
    data: Buffer.from([1]),
  });
}

/** SPL Token `MintTo` (tag 7): u8 tag | u64 amount. Authority signs. */
function mintTo(mint, destination, authority, amount) {
  const base = BigInt(amount);
  if (base < 0n || base > 0xffffffffffffffffn) throw new Error(`amount ${base} out of u64 range`);
  const data = Buffer.alloc(9);
  data.writeUInt8(7, 0);
  data.writeBigUInt64LE(base, 1);
  return new TransactionInstruction({
    programId: TOKEN_PROGRAM_ID,
    keys: [
      { pubkey: mint, isSigner: false, isWritable: true },
      { pubkey: destination, isSigner: false, isWritable: true },
      { pubkey: authority, isSigner: true, isReadOnly: true },
    ],
    data,
  });
}

/**
 * SPL Token `SetAuthority` (tag 6), AuthorityType::MintTokens (0), new
 * authority COption::None (one-byte 0) — the plan's 3.1 disable. There is no
 * instruction that undoes it.
 */
function disableMintAuthority(mint, currentAuthority) {
  return new TransactionInstruction({
    programId: TOKEN_PROGRAM_ID,
    keys: [
      { pubkey: mint, isSigner: false, isWritable: true },
      { pubkey: currentAuthority, isSigner: true, isReadOnly: true },
    ],
    data: Buffer.from([6, 0, 0]),
  });
}

/** The associated token account address for (owner, mint) and its bump. */
function associatedTokenAddress(owner, mint) {
  return PublicKey.findProgramAddressSync(
    [owner.toBuffer(), TOKEN_PROGRAM_ID.toBuffer(), mint.toBuffer()],
    ATA_PROGRAM_ID
  );
}

/** Associated Token Program `Create` — the only instruction that can materialise a PDA account. */
function createAssociatedTokenAccount(payer, ata, owner, mint) {
  return new TransactionInstruction({
    programId: ATA_PROGRAM_ID,
    keys: [
      { pubkey: payer, isSigner: true, isWritable: true },
      { pubkey: ata, isSigner: false, isWritable: true },
      { pubkey: owner, isSigner: false, isReadOnly: true },
      { pubkey: mint, isSigner: false, isReadOnly: true },
      { pubkey: SystemProgram.programId, isSigner: false, isReadOnly: true },
      { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isReadOnly: true },
    ],
    data: Buffer.alloc(0), // `Create` carries no operands
  });
}

/** Reads a mint account back the way `spl-token supply` would. */
function parseMint(info) {
  const d = info.data;
  const hasMintAuth = d.readUInt32LE(0) === 1;
  const authority = hasMintAuth ? new PublicKey(d.subarray(4, 36)) : null;
  const supply = d.readBigUInt64LE(36);
  const decimals = d.readUInt8(44);
  const isInitialized = d.readUInt8(45) === 1;
  const hasFreeze = d.readUInt32LE(46) === 1;
  const freezeAuthority = hasFreeze ? new PublicKey(d.subarray(50, 82)) : null;
  return { authority, supply, decimals, isInitialized, freezeAuthority };
}

/** Reads a token account: mint, owner, amount. */
function parseTokenAccount(info) {
  return {
    mint: new PublicKey(info.data.subarray(0, 32)),
    owner: new PublicKey(info.data.subarray(32, 64)),
    amount: info.data.readBigUInt64LE(64),
  };
}

function loadOrCreateKeypair() {
  fs.mkdirSync(path.dirname(KEYPAIR_PATH), { recursive: true, mode: 0o700 });
  if (fs.existsSync(KEYPAIR_PATH)) {
    const raw = JSON.parse(fs.readFileSync(KEYPAIR_PATH, "utf8"));
    return { keypair: Keypair.fromSecretKey(Uint8Array.from(raw)), created: false };
  }
  const keypair = Keypair.generate();
  fs.writeFileSync(KEYPAIR_PATH, JSON.stringify(Array.from(keypair.secretKey)), {
    mode: 0o600,
  });
  return { keypair, created: true };
}

function writeRecord(record) {
  fs.writeFileSync(RECORD, JSON.stringify(record, null, 2) + "\n");
  console.log(`\nrecorded ${RECORD}`);
}

async function requireDevnet(connection) {
  const genesis = await connection.getGenesisHash();
  if (genesis !== DEVNET_GENESIS) {
    throw new Error(
      `refusing to run: ${RPC_URL} reports genesis ${genesis}, not devnet's ` +
        `${DEVNET_GENESIS}. This script only makes a devnet token.`
    );
  }
  return genesis;
}

async function ensureFunded(connection, payer) {
  let balance = await connection.getBalance(payer.publicKey);
  if (balance < 0.02 * LAMPORTS_PER_SOL) {
    // Mint rent + four token-account rents is about 0.007 SOL on devnet; ask
    // for a little headroom, and fall back rather than failing if the faucet
    // caps it. The RPC faucet has been answering 410 Gone, so this is a
    // courtesy attempt, not a plan.
    for (const sol of [1, 0.1]) {
      try {
        console.log(`airdrop  ${sol} SOL ...`);
        const sig = await connection.requestAirdrop(payer.publicKey, sol * LAMPORTS_PER_SOL);
        const bh = await connection.getLatestBlockhash();
        await connection.confirmTransaction({ signature: sig, ...bh }, "confirmed");
        break;
      } catch (err) {
        console.log(`         airdrop of ${sol} SOL failed: ${err.message}`);
      }
    }
    balance = await connection.getBalance(payer.publicKey);
  }
  console.log(`balance  ${(balance / LAMPORTS_PER_SOL).toFixed(6)} SOL`);
  if (balance === 0) {
    throw new Error(
      "wallet has no devnet SOL and the faucet refused — fund it at " +
        "https://faucet.solana.com and re-run"
    );
  }
}

/** Reads the mint back and refuses to continue on anything the plan did not ask for. */
async function readMintOrThrow(connection, mint) {
  const info = await connection.getAccountInfo(mint);
  if (!info) throw new Error(`mint ${mint.toBase58()} is not on chain`);
  const parsed = parseMint(info);
  if (!parsed.isInitialized || parsed.decimals !== DECIMALS || parsed.freezeAuthority !== null) {
    throw new Error(`mint account does not match the plan: ${JSON.stringify(parsed)}`);
  }
  return parsed;
}

/** 3.1 — the one-way step. Idempotent: an already-disabled mint is a no-op. */
async function disableAuthority(connection, payer, mint, expectedSupply) {
  let parsed = await readMintOrThrow(connection, mint);
  if (parsed.supply !== expectedSupply) {
    throw new Error(
      `refusing to disable: supply is ${parsed.supply}, not the cap ${expectedSupply}. ` +
        "Minting is impossible once this runs."
    );
  }
  if (parsed.authority === null) {
    console.log("lock     mint authority is already disabled");
    return null;
  }
  if (!parsed.authority.equals(payer.publicKey)) {
    throw new Error(
      `refusing to disable: mint authority is ${parsed.authority.toBase58()}, ` +
        `not this wallet (${payer.publicKey.toBase58()})`
    );
  }
  const sig = await sendAndConfirmTransaction(
    connection,
    new Transaction().add(disableMintAuthority(mint, payer.publicKey)),
    [payer],
    { commitment: "confirmed" }
  );
  parsed = await readMintOrThrow(connection, mint);
  if (parsed.authority !== null) {
    throw new Error(`SetAuthority confirmed (${sig}) but the mint still reports an authority`);
  }
  console.log(`lock     mint authority disabled ${sig}`);
  return sig;
}

async function main() {
  const lockRequested = process.argv.includes("--lock");
  assertAllocation();

  const connection = new Connection(RPC_URL, "confirmed");
  const genesis = await requireDevnet(connection);

  const { keypair: payer, created } = loadOrCreateKeypair();
  console.log(`wallet   ${payer.publicKey.toBase58()}`);
  console.log(`keypair  ${KEYPAIR_PATH}${created ? " (newly generated)" : ""}`);

  // ── an earlier run already made the token ──────────────────────────────
  if (fs.existsSync(RECORD)) {
    const record = JSON.parse(fs.readFileSync(RECORD, "utf8"));
    const mint = new PublicKey(record.mint);
    console.log(
      `already recorded — mint ${record.mint}\n` +
        `(${path.basename(RECORD)} exists; delete it only if that mint is really gone)`
    );
    if (!lockRequested) {
      console.log(`supply   ${record.supply} base units, mint authority ${record.mint_authority ?? "none"}`);
      return;
    }
    const sig = await disableAuthority(connection, payer, mint, MAX_SUPPLY_BASE);
    if (sig) {
      record.mint_authority = null;
      record.mint_authority_disabled = true;
      record.transactions.disable_mint_authority = sig;
      record.locked_at = new Date().toISOString();
      writeRecord(record);
    }
    return;
  }

  await ensureFunded(connection, payer);

  // ── 2.1 the mint ────────────────────────────────────────────────────────
  const mint = Keypair.generate();
  const mintRent = await connection.getMinimumBalanceForRentExemption(MINT_SPACE);

  const createMintSig = await sendAndConfirmTransaction(
    connection,
    new Transaction().add(
      SystemProgram.createAccount({
        fromPubkey: payer.publicKey,
        newAccountPubkey: mint.publicKey,
        lamports: mintRent,
        space: MINT_SPACE,
        programId: TOKEN_PROGRAM_ID,
      }),
      initializeMint2(mint.publicKey, payer.publicKey, null)
    ),
    [payer, mint],
    { commitment: "confirmed" }
  );
  console.log(`mint     ${mint.publicKey.toBase58()}`);
  console.log(`         create+initialize ${createMintSig}`);

  const created0 = await readMintOrThrow(connection, mint.publicKey);
  if (created0.supply !== 0n || !created0.authority.equals(payer.publicKey)) {
    throw new Error(`fresh mint is not as expected: ${JSON.stringify(created0)}`);
  }

  // ── 2.2 + 2.3 four accounts, the fixed cap split 50/25/15/10 ───────────
  const accounts = {};
  const signatures = { create_mint: createMintSig };
  const accountRent = await connection.getMinimumBalanceForRentExemption(TOKEN_ACCOUNT_SPACE);

  for (const tranche of TRANCHE_BPS) {
    const amount = trancheAmount(tranche.bps);
    const isTreasury = tranche.key === "treasury_reserve";
    let address;
    let signers;
    let createIxs;

    if (isTreasury) {
      // 2.2 verbatim: the associated token account `create-account` makes.
      [address] = associatedTokenAddress(payer.publicKey, mint.publicKey);
      signers = [payer];
      // `Create` does BOTH: it materialises the PDA and initializes it as a
      // token account. Following it with `InitializeAccount` initializes the
      // same account twice, which the token program refuses with
      // `custom program error: 0x6` ("account or token already in use") — the
      // failure the first live run of this script hit. The other three
      // tranches are plain system accounts, which the token program does NOT
      // initialize, so they still need the explicit call below.
      createIxs = [
        createAssociatedTokenAccount(payer.publicKey, address, payer.publicKey, mint.publicKey),
      ];
    } else {
      // An ATA is unique per (owner, mint), so the remaining tranches are
      // ordinary 165-byte token accounts with their own address. The account
      // key signs only its creation; spending is by the owner field below.
      const acct = Keypair.generate();
      address = acct.publicKey;
      signers = [payer, acct];
      createIxs = [
        SystemProgram.createAccount({
          fromPubkey: payer.publicKey,
          newAccountPubkey: address,
          lamports: accountRent,
          space: TOKEN_ACCOUNT_SPACE,
          programId: TOKEN_PROGRAM_ID,
        }),
        initializeAccount(address, mint.publicKey, payer.publicKey),
      ];
    }

    // one transaction per tranche: four account creations plus four mints do
    // not fit one 1232-byte transaction.
    const sig = await sendAndConfirmTransaction(
      connection,
      new Transaction().add(...createIxs, mintTo(mint.publicKey, address, payer.publicKey, amount)),
      signers,
      { commitment: "confirmed" }
    );

    const acct = parseTokenAccount(await connection.getAccountInfo(address));
    if (!acct.mint.equals(mint.publicKey) || !acct.owner.equals(payer.publicKey)) {
      throw new Error(`${tranche.key}: account points at the wrong mint/owner`);
    }
    if (acct.amount !== amount) {
      throw new Error(`${tranche.key}: holds ${acct.amount} base units, expected ${amount}`);
    }
    accounts[tranche.key] = {
      label: tranche.label,
      share_bps: tranche.bps,
      address: address.toBase58(),
      kind: isTreasury ? "associated token account" : "token account",
      amount: amount.toString(),
      amount_xmrj: (amount / 10n ** BigInt(DECIMALS)).toString(),
    };
    console.log(
      `${tranche.key.padEnd(16)} ${address.toBase58()}  ` +
        `${accounts[tranche.key].amount_xmrj.padStart(11)} XMRJ (${tranche.bps / 100}%)`
    );
    console.log(`                 create+mint ${sig}`);
    signatures[tranche.key] = sig;
  }

  // ── read the whole allocation back from the chain ──────────────────────
  const minted = await readMintOrThrow(connection, mint.publicKey);
  if (minted.supply !== MAX_SUPPLY_BASE) {
    throw new Error(`supply reads ${minted.supply}, expected the cap ${MAX_SUPPLY_BASE}`);
  }
  if (!minted.authority.equals(payer.publicKey)) {
    throw new Error(`mint authority is ${minted.authority.toBase58()}`);
  }

  const record = {
    _note:
      "DEVNET ONLY. Created by create_xmrj_mint.js from " +
      "XMRJ_SOLANA_DEPLOYMENT_PLAN.md sections 2 and 3.1. Devnet tokens have no " +
      "value and are not a listing, a treasury or a promise of either. Supply is " +
      "the fixed 100,000,000 cap split 50/25/15/10; the split is bookkeeping, " +
      "not custody — all four accounts have one owner — and the team/foundation " +
      "tranche has no on-chain vesting.",
    cluster: "devnet",
    rpc_url: RPC_URL,
    genesis_hash: genesis,
    token: {
      name: "XMR Jackpot Treasury Token",
      symbol: "XMRJ",
      standard: "spl-token",
      decimals: DECIMALS,
    },
    mint: mint.publicKey.toBase58(),
    // 2.2's "treasury token account" is the treasury reserve tranche.
    treasury_vault: accounts.treasury_reserve.address,
    treasury_vault_kind: "associated token account",
    treasury_vault_owner: payer.publicKey.toBase58(),
    allocation: accounts,
    max_supply: MAX_SUPPLY.toString(),
    max_supply_base_units: MAX_SUPPLY_BASE.toString(),
    supply: minted.supply.toString(),
    mint_authority: minted.authority ? minted.authority.toBase58() : null,
    mint_authority_disabled: false,
    freeze_authority: null,
    created_at: new Date().toISOString(),
    transactions: signatures,
  };

  // ── 3.1, only when asked: the irreversible step ────────────────────────
  if (lockRequested) {
    const sig = await disableAuthority(connection, payer, mint.publicKey, MAX_SUPPLY_BASE);
    record.mint_authority = null;
    record.mint_authority_disabled = true;
    record.transactions.disable_mint_authority = sig;
    record.locked_at = new Date().toISOString();
  } else {
    console.log(
      "\nmint authority is STILL LIVE on this wallet. That is devnet-only: the\n" +
        "plan's 3.2 forbids a lone hot wallet holding it in anything public.\n" +
        "Run `node create_xmrj_mint.js --lock` to disable it — one-way, and only\n" +
        "correct once the cap above is final."
    );
  }

  writeRecord(record);
}

if (require.main === module) {
  main().catch((err) => {
    console.error(`FAILED: ${err.message}`);
    process.exitCode = 1;
  });
}

module.exports = {
  MAX_SUPPLY,
  MAX_SUPPLY_BASE,
  TRANCHE_BPS,
  DECIMALS,
  trancheAmount,
  assertAllocation,
  initializeMint2,
  initializeAccount,
  mintTo,
  disableMintAuthority,
  associatedTokenAddress,
  createAssociatedTokenAccount,
  parseMint,
  parseTokenAccount,
};
