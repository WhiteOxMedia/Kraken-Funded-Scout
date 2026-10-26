# XMRJ Solana Deployment — Final Version

This plan is a concrete implementation path for the requested task: deploy XMRJ (SPL),
create the treasury vault, mint initial supply, lock/delegate mint authority, deploy Treasury
and Jackpot programs, and wire the off-chain XMR conversion pipeline. It does NOT perform
any Kraken listing actions.

## Current environment assessment

- Node.js is available: node v24.18.0
- Solana CLI is NOT installed: solana not found
- SPL Token CLI is NOT installed: spl-token not found
- Rust toolchain is NOT installed: cargo not found
- Keypair not present: no wallet.json in the workspace
- No Solana RPC config set: no devnet/mainnet config in this environment

Because the tooling is missing, the deployment cannot be executed directly from this workspace yet.
The steps below are the exact recommended sequence, with the first part being environment preparation
rather than token/program deployment.

## Recommended deployment model

- Build/test first on Solana devnet.
- Use one dedicated deployer keypair for program deployment and initial mint.
- Lock or delegate mint authority away from a single hot wallet before any mainnet use.
- Keep the off-chain XMR conversion service separate from the on-chain programs and from any Kraken account actions.

## 1. Solana environment setup

### 1.1 Install toolchain (PINNED — never "stable")

Five versions define this workspace. Each is pinned in a file in this repo, not
in someone's shell history, and the wiring drive fails if any of them drifts:

| what | pinned value | where it lives |
| --- | --- | --- |
| anchor-cli (the CLI) | 1.1.2 | Anchor.toml `[toolchain] anchor_version` |
| Solana CLI (Agave) | `=3.1.10` | Anchor.toml `[toolchain] solana_version` |
| host Rust (cargo, rustc) | 1.89.0 | rust-toolchain.toml `channel` |
| anchor-lang (the crate the programs link) | `=1.1.2` | each crate's Cargo.toml |
| the SBF compiler (what emits the .so) | follows `=3.1.10` | that Solana release's platform-tools map |

The `=` on the Solana version is deliberate and load-bearing: AVM parses this
value as a semver REQUIREMENT and picks the newest hosted CLI that satisfies it,
so a bare "3.1.10" would drift to the next 3.x the day it ships. `=3.1.10`
requires that one release. All five values are checked by
test_anchor_wiring.py section 13 — including the numbers in this table.

Step 1 — host Rust. On WSL, Linux and macOS after rustup is installed; on native
Windows install rustup from rustup.rs first, then the same command:

rustup toolchain install 1.89.0 --profile minimal --component rustfmt,clippy

Step 2 — avm, then the pinned anchor-cli:

cargo install avm --git https://github.com/otter-sec/anchor --locked
avm install 1.1.2
avm use 1.1.2
anchor --version          # expect: anchor-cli 1.1.2

(There is also a published one-liner,
`curl -sSfL https://raw.githubusercontent.com/otter-sec/anchor/master/avm/install | sh`.
It installs the latest avm binary, whose own build is a nightly — fine, because
avm is the version MANAGER; it does not change which anchor-cli you select. The
cargo form above builds the tagged release and is the one the release notes
give.)

Step 3 — the pinned Solana CLI. WSL, Linux, macOS:

sh -c "$(curl -sSfL https://release.anza.xyz/v3.1.10/install)"
solana --version          # expect: solana-cli 3.1.10 (... client:Agave)

Native Windows, from an Administrator Command Prompt (same versioned path):

cmd /c "curl https://release.anza.xyz/v3.1.10/agave-install-init-x86_64-pc-windows-msvc.exe --output C:\agave-install-tmp\agave-install-init.exe --create-dirs"
C:\agave-install-tmp\agave-install-init.exe v3.1.10
solana --version

WSL remains the route both toolchains' docs recommend for Windows (`wsl --install`,
restart, then the Linux steps); the native path above is the documented fallback.

Installing from upstream's moving `stable` channel — which is what this plan used
to say — gets whatever is newest that day and is NOT part of this path (the
drive greps this document for that endpoint and fails on it, so the pinned
commands above are the only install path here). Upgrading is an edit to
the pins (Anchor.toml, rust-toolchain.toml and every Cargo.toml together), never
`agave-install update` on its own: that would leave the manifest, the crates and
the compiler on three different versions.

AVM reads those same pins: `avm solana resolve` (or `avm solana install`) uses
`[toolchain] solana_version`, and `avm platform-tools resolve` prints the SBF
compiler that Solana version maps to.

If spl-token is not included in the installed bundle, the SPL Token CLI can be built from Rust:

cargo install spl-token-cli
spl-token --version

### 1.2 Configure wallet and keypair

Create a keypair for deployment/testing. Keep this file out of shared repos and do not commit it to version control.
Example for PowerShell with a native Windows CLI:

New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\solana"
solana-keygen new --outfile "$env:USERPROFILE\.config\solana\id.json"
solana config set --keypair "$env:USERPROFILE\.config\solana\id.json"

For WSL/Ubuntu, use the Unix home path instead:

solana-keygen new
solana config set --keypair "$HOME/.config/solana/id.json"

Verify active configuration:

solana config get

### 1.3 Select devnet and fund wallet

solana config set --url devnet
solana airdrop 2
solana balance

If airdrop requests are rate-limited or fail, use the Web Faucet instead of repeated CLI airdrop calls.
Devnet airdrops are limited per request.

WHERE THE DEVNET SOL COMES FROM — NOT THE POOL (recorded Oct 26). The jackpot
pool cannot fund this step and must not be drawn on for it, for two
independent reasons. (1) It holds XMR, not SOL: the live pot figure the admin
dashboard shows comes from `kraken_referral_sim_v1` — a SIMULATED referral
projection, not a balance — and the most recent real drawings were 0.05 and
0.093 XMR. (2) Even a real XMR → SOL conversion on Kraken produces MAINNET SOL.
Devnet is a separate ledger with no bridge to mainnet, and the address formats
are identical, so mainnet SOL sent to the devnet wallet's address lands on
mainnet and is invisible on devnet. Devnet SOL exists only as faucet airdrops;
nothing is ever paid for it. Devnet costs: 0.004 SOL for the mint in section 2,
and the three program deploys in section 9 need rent computed from the real .so
sizes (~7,000 lamports per byte of account data), which is why section 9 says to
top up before the first run rather than during it.

MAINNET, BY CONTRAST, COSTS REAL MONEY. The same three deploys would be paid in
purchased SOL, roughly 1.5–2 SOL per program of a few hundred KB, non-refundable
if a program id has to be abandoned after deploy; and the mint is irreversible
under the section 3.1 lock. That is section 8, gated deliberately, and it is
not a shortcut around a faucet that has not been clicked.

### 1.4 Confirm tooling before moving on

Do not proceed to token creation until these commands succeed in the chosen
environment AND print the pinned versions from 1.1:

anchor --version                # anchor-cli 1.1.2
solana --version                # solana-cli 3.1.10 (... client:Agave)
rustc --version                 # rustc 1.89.0
cargo --version                 # cargo 1.89.0
avm list                        # 1.1.2 installed and marked in use
avm solana resolve              # resolves 3.1.10 from Anchor.toml [toolchain]
avm platform-tools resolve      # the SBF compiler that version maps to
                                # (if it is not cached, AVM prints the exact
                                #  `cargo build-sbf --install-only` to run)
spl-token --version             # needed only for sections 2 and 3

### 1.5 What the pins do and do not guarantee

What they DO: the CLI, the crate and the compiler are decided by files here, so
two clones on two machines with different toolchain defaults still run the same
versions, and a drift in any one of the five values is a failed drive rather
than a surprise at deploy time.

What they do NOT do:

- No Cargo.lock is committed yet. cargo has never run in this workspace, so
  nothing here has been resolved, let alone locked; the `=` pins on anchor-lang
  are the stand-in until the first successful build, whose lockfile should be
  committed and kept.
- The SBF compiler is pinned only TRANSITIVELY, through the Solana CLI version
  and that release's platform-tools map. Pinning it explicitly needs Anchor CLI
  1.2.0 or newer (`anchor build --tools-version`, or cargo-build-sbf's
  `[workspace.metadata.solana] tools-version`) — a lever for the next upgrade,
  not a value invented here.
- Byte-identical .so files across machines are NOT implied. Anchor's answer to
  that is `anchor build --verifiable`, which builds inside a pinned Docker
  image; use it for any artifact a third party has to reproduce.
- NOTHING ON THE SOLANA SIDE HAS BEEN COMPILED. The move to 1.1.2 was made
  against Anchor's published 1.0.0 breaking-change list and the 1.1.2 source,
  not against a compiler: the three CPI call sites (CpiContext lost its program
  AccountInfo argument), the two pinned program-id constants (solana_program's
  `pubkey!` is gone with the Solana 3.x crate split; `Pubkey::from_str_const`
  replaces it) and the removal of Anchor.toml's `[registry]` section are each
  labelled in place, and the first `anchor build` is what verifies them.
- If tests are added later: `anchor test` and `anchor localnet` default to
  Surfpool (v1.1.2+) instead of solana-test-validator in Anchor 1.x; pass
  `--validator legacy` to keep the old backend.

## 2. Create the XMRJ SPL token

Token parameters:

- Name: XMR Jackpot Treasury Token
- Symbol: XMRJ
- Decimals: 9
- Standard: SPL

### 2.1 Create token

spl-token --url devnet create-token --decimals 9

Store the printed mint address as XMRJ_MINT.

### 2.2 Create treasury token account

spl-token --url devnet create-account <XMRJ_MINT>

Store the returned associated token account address as TREASURY_VAULT.

### 2.3 Mint initial supply — DECIDED (Oct 26)

Max supply is FIXED at **100,000,000 XMRJ** (9 decimals). There is no emission
schedule and no post-genesis mint: section 3.1 is the chosen branch. The whole
cap is minted once, at genesis, split four ways:

| Tranche             | Share | Amount       | Account                          |
|---------------------|-------|--------------|----------------------------------|
| Treasury reserve    | 50%   | 50,000,000   | associated token account (2.2)   |
| Jackpot & rewards   | 25%   | 25,000,000   | token account                    |
| Community growth    | 15%   | 15,000,000   | token account                    |
| Team / foundation   | 10%   | 10,000,000   | token account                    |

Mechanically: the treasury reserve is the ATA of 2.2, and the other three are
ordinary 165-byte token accounts, because an ATA is unique per (owner, mint) —
one wallet cannot have four. All four are owned by the deployer, so this is one
transaction per tranche and no extra key custody.

Two limits of that split, recorded so nobody reads more into it than the chain
enforces:

- **The split is bookkeeping, not custody.** All four accounts have the same
  owner, so one signer can move any of them. Real separation needs four distinct
  owners (or the multisig/program of 3.2, which is NOT the chosen branch) and is
  a separate step.
- **Team/foundation vesting is not enforced on chain.** This workspace has no
  vesting program, so a lock there would be a promise, not a constraint. Do not
  describe that tranche as vested until something enforces it.

The one-line CLI equivalent, per tranche:

spl-token --url devnet mint <XMRJ_MINT> <AMOUNT> <ACCOUNT>

`create_xmrj_mint.js` is the implementation: it asserts the bps total 10000,
mints the four tranches, and reads every account back (mint, owner, amount, and
total supply against the cap) instead of trusting what it sent.

## 3. Lock or delegate mint authority — DECIDED (Oct 26): 3.1

### 3.1 Fixed supply — CHOSEN

Disable mint authority:

spl-token --url devnet authorize <XMRJ_MINT> mint --disable

This is ONE-WAY: no instruction re-enables it, and the plan has no path back.
So the order is fixed and not interchangeable — mint the full 100,000,000
across the four accounts of 2.3 first, disable second, then verify that supply
reads the cap and that no mint authority is reported.

`create_xmrj_mint.js --lock` does exactly that and refuses to disable on any
supply other than the cap. It is opt-in on purpose: the irreversible step is
never a side effect of creating the token. Until it runs, the mint authority
is a single hot wallet key, which is a DEVNET-ONLY state — 3.2 below is why it
cannot survive into anything public.

### 3.2 Governed emission — NOT the chosen branch

Kept for the record: set the mint authority to a governance program or
multisig, where minting is a governed act. Mint authority must never remain on
a single hot wallet, which is the constraint that rules this out as a resting
state. If the cap is ever revisited, that is a new mint under 3.2 — not a
remint of this one.

spl-token --url devnet authorize <XMRJ_MINT> mint <NEW_AUTHORITY_PUBLICKEY>

In governed mode, the Treasury program should hold/enforce minting through that authority rather than a lone hot wallet key.

## 4. Deploy Treasury program

The Treasury program is responsible for:

- holding XMRJ in the treasury vault conceptually/on-chain as applicable
- recording XMRJ inflows
- applying split ratios (reserve vs jackpot)
- emitting conversion instructions for off-chain XMR conversion
- sending jackpot funding instructions to the Jackpot program

Required interface (as specified):

- record_inflow_xmrj(amount_xmrj) — operational-only
- set_split_ratio(reserve_bps, jackpot_bps) — admin-only
- set_operational_authority(new_authority) — admin-only
- convert_xmrj_to_xmr(amount_xmrj)
- fund_jackpot_with_xmr(amount_xmr) — operational-only

Interface status (Oct 17 — stated rather than implied, so nothing here reads as
"done" when it is only specified):

- `initialize(reserve_bps, jackpot_bps, exchange_rate_bps,
  operational_authority)` — implemented, and REQUIRED first: the state account
  does not exist until it runs (4.3). It is gated on the program's upgrade
  authority (the ProgramData account is an account of the instruction), so the
  create-once state cannot be front-run. The fourth parameter is the key that
  will be allowed to record inflows and trigger funding; it is the deployer's
  choice here, not a hard-coded address.
- `record_inflow_xmrj(amount_xmrj)` — implemented, and GATED on the state's
  `operational_authority`: the state account is paired to that address by
  `has_one`, and the address must SIGN. Recording an inflow decides the
  reserve/conversion split of real money, so it is no longer an open call.
- `set_split_ratio` / `set_exchange_rate` — implemented, admin-gated; the admin
  is the governance authority PDA, so these are reached by an approved
  proposal through the governance CPI
- `set_operational_authority(new_authority)` — implemented, admin-gated the same
  way: the ONE lever that can rotate the operational key, and therefore the
  reason a lost or leaked operating key is recoverable rather than final. The
  governance program carries the matching
  `GovernanceAction::SetOperationalAuthority { new_authority }` variant, whose
  handler CPI-calls this setter signed as the governance authority PDA.
- `convert_xmrj_to_xmr(amount_xmrj)` — NOT implemented: the conversion is
  off-chain (section 6), which is where that work belongs
- `fund_jackpot_with_xmr(amount_xmr)` — IMPLEMENTED. It is a real CPI into the
  jackpot program, signed with the treasury's funder PDA (`[b"jackpot_funder"]`
  under the TREASURY program id, so only this program can sign for it), and it
  is the only call on Solana the jackpot accepts (5.3). It takes the treasury
  state, the jackpot state, the funder PDA and the jackpot program; it forwards
  at most what the treasury has RECORDED as convertible — the amount is bounded
  by `total_converted_xmrj - total_jackpot_funded_xmr`, so it can never mint
  jackpot balance and never forwards the same recorded amount twice.
  It is gated on the state's `operational_authority`, exactly like
  `record_inflow_xmrj`: `has_one` fixes the address and a `signer` constraint
  demands the signature, so neither call can be merely relayed. Deliberately
  NOT `admin_authority` — that is the governance authority PDA and the proposal
  PDA is a singleton (9.3), so routing funding through it would make funding a
  one-time act per cluster. The operational key is seeded by the deployer at
  `initialize` and rotatable only by an approved proposal
  (`set_operational_authority`), which keeps a compromised key to one proposal's
  blast radius instead of the deployment's.

### 4.1 Build

Build with the pinned toolchain from section 1.1, from the workspace root:

anchor build                       # all three programs (Anchor.toml [scripts] build)

or just this program's artifact:

cargo build-sbf --manifest-path xmrj_treasury/Cargo.toml

Either way the artifact lands at target/deploy/xmrj_treasury.so. The path this
section used to name (`treasury-program/`) is a directory that does not exist;
the crate is `xmrj_treasury`, which is the name Anchor.toml lists.

### 4.2 Deploy

solana program deploy --url devnet /path/to/treasury_program.so

Store the deployed program ID as TREASURY_PROGRAM_ID.

### 4.3 Initialize (required — the state account does not exist until this runs)

Call `initialize(reserve_bps, jackpot_bps, exchange_rate_bps,
operational_authority)` once, immediately after the deploy, with:

- `treasury_state` — the PDA `[b"treasury_state"]` under the treasury program
- `admin_authority` — the governance authority PDA, `[b"governance_authority"]`
  derived under the GOVERNANCE program. The treasury derives and checks this
  itself, and the governance program id is pinned inside the program rather
  than supplied — so the authority cannot be pointed anywhere else.
- `program_data` — the treasury program's own ProgramData account, the PDA
  `[b"programdata", <treasury program id>]` under the upgradeable loader
  (`BPFLoaderUpgradeab1e11111111111111111111111`). The program derives it
  itself, so a lookalike cannot be substituted.
- `payer` — the deployer, and it MUST be the program's upgrade authority: the
  instruction reads the authority the loader recorded in `program_data` and
  refuses to create the state for any other signer.

`reserve_bps + jackpot_bps` must equal 10 000, or the instruction refuses. Every
parameter is re-settable afterwards by an approved governance proposal
(`set_split_ratio` / `set_exchange_rate` / `set_operational_authority`), so the
deployer's starting values are not final.

**`operational_authority` is a plain keypair, not a PDA**, because both calls it
unlocks must SIGN a transaction a human or a bot submits — a PDA cannot. It is
an instruction ARGUMENT (`Pubkey`), not an account of `initialize`: the program
stores the value and derives nothing from it. Seed it
with the deployer's own key for the first deploy (as the devnet suite does), or
with the operating wallet if one already exists; rotate it to a dedicated key
before any real value is recorded. The key is bound to the state, not to the
program: `test_anchor_wiring.py` 12.12/12.13 pin that both money-moving calls
pair `has_one = operational_authority` with a signature, and that the only
writer of that field is the admin-gated `set_operational_authority` reached by
CPI from an approved proposal.

**The initialization race is CLOSED, not merely noted.** The state PDA is a
fixed seed, so without a gate whichever transaction landed first would create
it — and creating it chooses `admin_authority`, permanently. `initialize` now
requires the signer to be the program's own upgrade authority (the
`upgrade_authority_address` in `program_data`, at byte offset 13 of that
account). Only the key that can upgrade the program can initialize it, which is
the deployer's keypair on a normal `anchor deploy`, so a watcher of the chain
cannot front-run the initialization. Consequences that matter operationally:

- initialize in the same run as the deploy, before handing the upgrade
  authority to a multisig or wallet you are not currently using;
- if the upgrade authority is a multisig, the multisig must be the `payer`
  (and therefore sign) for this call;
- do NOT finalize the program (`solana program deploy --final`) before its
  state exists. A finalized program has no upgrade authority, and the gate
  refuses with `ProgramIsImmutable` rather than silently accepting anyone.

The same gate is in `xmrj_jackpot::initialize` (5.3) and
`xmrj_governance::initialize_governance`, which takes its ProgramData account
the same way. `test_anchor_wiring.py` 12.10/12.11 pins all three, and
`test_xmrj_devnet_e2e.py` pins the suite that has to pass the account.

The devnet end-to-end smoke test (section 9) performs this initialization and
the jackpot's itself, and then proves the governance CPI reaches the programs.

## 5. Deploy Jackpot program

The Jackpot program is responsible for:

- tracking logical jackpot balance (XMR amount)
- accepting funding only from the Treasury program
- managing payout events
- providing jackpot state to the UI

Required interface (as specified):

- fund_jackpot(amount_xmr) — callable only by Treasury

### 5.1 Build

From the workspace root, with the pinned toolchain (section 1.1):

anchor build                       # all three programs

or just this one:

cargo build-sbf --manifest-path xmrj_jackpot/Cargo.toml

The artifact lands at target/deploy/xmrj_jackpot.so (`jackpot-program/` in the
earlier draft of this plan was never a path in this repo).

### 5.2 Deploy

solana program deploy --url devnet /path/to/jackpot_program.so

Store the deployed program ID as JACKPOT_PROGRAM_ID.

### 5.3 Initialize (required — the state account does not exist until this runs)

Call `initialize()` — it takes NO parameters — once, immediately after the
deploy, with:

- `jackpot_state` — the PDA `[b"jackpot_state"]` under the jackpot program
- `admin_authority` — the governance authority PDA, derived and checked by the
  jackpot program itself under the same pinned governance program id
- `program_data` — the jackpot program's own ProgramData account
  (`[b"programdata", <jackpot program id>]` under the upgradeable loader)
- `payer` — the deployer, and it MUST be the program's upgrade authority; the
  instruction refuses to create the state for any other signer (4.3 has the
  full reasoning and the `--final` caveat — it applies here unchanged)

It pins the funder (`treasury_program` = the treasury program id, a constant in
the program) and the authority, and starts the payout cap UNCAPPED (10 000 bps).
Governance can lower that cap later through an approved proposal
(`set_payout_rules`). Because the instruction accepts nothing, a first-caller
race could only ever produce the correct CONFIGURATION — but it would still
choose who may change the cap later, and the state is create-once. The
gate on `program_data` upgrades that "accepts nothing" argument to "only the
deployer can create it at all", which is the property this section now claims.

**Section 5's "accepting funding only from the Treasury program" is now
ENFORCED**, and the mechanism is worth knowing before debugging it. There is no
`caller` account any more: the old check compared that account's KEY to
`treasury_program`, which any wallet satisfied by passing the treasury program's
address with the treasury program nowhere in the call.

What replaced it is a signature. `fund_jackpot` takes one account, `funder`,
constrained as
`seeds = [b"jackpot_funder"], seeds::program = TREASURY_PROGRAM_ID, bump, signer`
— i.e. it must BE the PDA derived from `jackpot_funder` under the PINNED
treasury program id, and it must have SIGNED. A PDA is off-curve, so no keypair
can produce that signature, and a program can only ever sign for PDAs derived
under its own id; the only thing in the runtime that can satisfy both
constraints is `invoke_signed` inside `xmrj_treasury`, which is exactly what
`fund_jackpot_with_xmr` does (section 4). The seed literal is one value in both
crates, and the wiring drive (test_anchor_wiring.py 12.6/12.7) fails if the two
ever disagree or if the `signer` constraint is dropped — a check that would have
caught the old hole.

What was open here is now closed: the treasury-side CALLER gate is the state's
`operational_authority`, demanded as a signer (section 4's interface status).
The recorded-share bound stays, and it is not redundant — even the operational
key can only forward what the treasury has already recorded as convertible,
once. The two constraints answer different questions: WHO may move it, and HOW
MUCH of it may move.

## 6. Off-chain XMR conversion pipeline

Define and integrate a separate off-chain microservice that:

- listens for Treasury conversion events
- converts XMRJ value to real XMR using the current exchange rate
- sends XMR to Kraken or the Monero jackpot wallet as applicable
- updates jackpot liquidity state

Exchange rate logic:

xmr_amount = xmrj_amount * exchange_rate

Admin-only controls:

- set_exchange_rate
- set_split_ratio

This service should be scoped, logged, and access-controlled. It is not a Kraken listing action and should
not perform any listing-related steps.

## 7. Application integration

Integrate into the application layer:

- XMRJ mint address
- Treasury program ID
- Jackpot program ID
- off-chain conversion service endpoint/config
- Scout Pro unlock logic (no token spending)

Scout unlock rules:

- Mining unlock (100 days) -> no XMRJ movement
- Paid unlock -> XMRJ inflow only
- No treasury outflow
- No token sinks
- No inflation

## 8. Mainnet deployment (when ready)

When ready to move off devnet:

solana config set --url https://api.mainnet-beta.solana.com

Then repeat, with mainnet-appropriate funding and authority controls:

- create XMRJ mint
- create treasury vault
- deploy Treasury program
- deploy Jackpot program
- mint initial supply
- lock or delegate mint authority

No Kraken listing actions should be performed at this time.

## 9. Devnet end-to-end smoke test

Everything above is a sequence of commands a human runs. tests/xmrj_devnet_e2e.ts
is that same sequence as a test, so "the governance CPI changed a parameter on
chain" is CHECKED rather than asserted — the one claim no compile can make.

### 9.1 Run it

One-time, installs the pinned client (package-lock.json is committed so every
clone resolves the same versions; node_modules/ is gitignored):

npm ci

Every run — builds, deploys with the cluster named on the command, then runs
the suite:

anchor run e2e-devnet

or by hand:

anchor build
anchor deploy --provider.cluster devnet
ANCHOR_PROVIDER_URL=https://api.devnet.solana.com ANCHOR_WALLET=~/.config/solana/id.json npx ts-mocha -p ./tsconfig.json -t 1000000 "tests/**/*.ts"

The build is not avoidable on a first run: the suite reads target/deploy/*.so to
work out the rent the deploy needs.

### 9.2 What it proves, in order

0. IT REFUSES ANY CHAIN BUT DEVNET — on the GENESIS HASH read live from the
   RPC (devnet reports EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG), not on the
   URL, because this file deploys and initializes and mainnet-beta has to be
   unreachable from it.
1. THE DEPLOY LANDED: all three manifest ids are executable accounts — and
   the wallet running the suite IS each program's recorded upgrade authority,
   read from ProgramData. That is the precondition every initializer below is
   gated on, and it is asserted as such rather than discovered as an
   instruction failure.
2. BOTH STATES EXIST: the treasury's initialize (a split summing to 10 000, and
   the operational authority the suite will sign its later calls with) and the
   jackpot's parameterless initialize, each handing the program its own
   `programData` account so the upgrade-authority gate can pass. A rerun
   asserts their invariants instead of trying to create them again — including
   that the state recorded the operational key the suite holds.
3. A PROPOSAL PASSES: initialize_governance (which takes its own `programData`
   under the same gate) with three owners and threshold 2, then propose,
   exactly two approvals from distinct owners, and execute_action.
4. THE CPI IS WHAT CHANGED THE PARAMETER: the jackpot's max_payout_bps is read
   FROM CHAIN before (10 000 — the value `initialize` writes) and after (the
   proposed 4 000), and the two are asserted different, so the test cannot pass
   vacuously.
5. THE NEGATIVE CONTROL: a direct set_payout_rules signed by the deployer is
   rejected, and the parameter is re-read to show the rejected call changed
   nothing. Without this, step 4 would also pass on a plain writable account.
6. THE JACKPOT IS FUNDABLE ONLY BY THE TREASURY PROGRAM: an inflow is recorded
   (which banks the convertible share), `fund_jackpot_with_xmr` forwards part of
   it, and the jackpot balance moves by exactly that amount — read from chain,
   as a DELTA so a rerun asserts the same thing. Then two DIRECT `fund_jackpot`
   calls are attempted from the deployer's wallet and both are asserted
   rejected: one with the funder PDA UNSIGNED (the address is right and only the
   signature is missing — the case a key comparison could never catch), and one
   with the treasury program's own address as the caller (the recipe the old
   check accepted). The balance is re-read after both. Step 6 needs no proposal,
   so it runs even where step 3's single proposal is already spent.
7. THE OPERATIONAL GATE HOLDS: a `recordInflowXmrj` signed by a wallet that is
   NOT the recorded operational authority is attempted and asserted REJECTED,
   and the state is re-read unchanged. Steps 2 and 6 sign with the operational
   key, so a gate that refused everyone would fail them — the positive and the
   negative halves are both present.

### 9.3 What it does not prove, and the limits it records

- The TREASURY's own set_split_ratio / set_exchange_rate /
  set_operational_authority CPI is NOT exercised. `proposal` is a PDA seeded
  [b"proposal", governance_state] created with `init`, and no close instruction
  exists, so exactly ONE governance action can ever exist per governance state
  on a cluster. One run proves one action; the other three call sites are pinned
  structurally, offline, by test_xmrj_devnet_e2e.py and
  test_anchor_wiring.py 12.12/12.13. (The treasury → jackpot FUNDING CPI is
  exercised — step 6 — because it is not a governance action and needs no
  proposal.)
- The owners are devnet-only identities DERIVED FROM PUBLIC CONSTANTS, chosen
  so a rerun can sign again. They hold no value — the genesis guard is what
  makes that acceptable.
- The suite drives the programs through a narrow local interface rather than
  generated IDL types (target/types/*.ts exist only after a build), so
  test_xmrj_devnet_e2e.py pins every instruction name, account key, seed
  literal and enum variant it uses against the Rust sources without a chain.
  That same driving runs the suite FOR REAL when anchor + solana are on PATH and
  XMRJ_DEVNET_E2E=1; otherwise it prints SKIP and names what is missing.
- FUNDING: a first run deploys three programs. The suite computes the rent from
  the real .so sizes and refuses to start if the wallet cannot cover it. Devnet
  airdrops are small and rate-limited (`solana airdrop 2`, repeat, or the web
  faucet) — top up before the first run, not during it.

## Implementation notes / constraints

- This plan assumes the above tooling is installed in the deployment environment before any Solana commands are run.
- Do not commit keypairs or sensitive RPC/auth material to the repo.
- Test token creation, account creation, minting, and authority changes on devnet first.
- Confirm program security properties (admin gates, mint authority handling, Treasury->Jackpot authorization)
  before mainnet deployment.

## Status

Not started in this environment, and nothing on the Solana side has been compiled
or deployed — cargo, rustc, anchor and solana are all absent here (see "Current
environment assessment" above), which is why every check on this side is
structural. The toolchain is now PINNED rather than described: anchor-cli 1.1.2,
Solana CLI 3.1.10 and host Rust 1.89.0 in Anchor.toml `[toolchain]` and
rust-toolchain.toml, anchor-lang `=1.1.2` in every crate, with
test_anchor_wiring.py section 13 enforcing that the five values and this
document agree. Installing the tooling (section 1.1) is the first required step;
choosing versions no longer is.

THE ONE THING THAT IS NOW WRITTEN AND WAITING: tests/xmrj_devnet_e2e.ts
(section 9) deploys the three programs, initializes both states, carries a
proposal through its threshold, proves the CPI changed the parameter on chain,
and funds the jackpot through the treasury's CPI while proving the two direct
funding paths are rejected. It has NEVER RUN — no toolchain exists here — and
its interface is pinned offline against the Rust sources by
test_xmrj_devnet_e2e.py, which reports SKIP for the live half on a box without
anchor, solana and a funded devnet wallet. That suite is what turns every
"unverified" label in this document into a fact, and it needs a machine with
the pinned toolchain to do it.

THE OPERATIONAL GATE IS CLOSED IN CODE. `record_inflow_xmrj` and
`fund_jackpot_with_xmr` used to be open calls — anyone could record an inflow
(choosing the reserve/conversion split of real money) and anyone could trigger
the funding transfer. The treasury state now records an `operational_authority`,
chosen at `initialize` and rotatable only by an approved governance proposal
(`set_operational_authority`, via the governance `SetOperationalAuthority`
action); both money-moving calls demand that exact address as a SIGNER, so a
third party cannot even relay them. It is not `admin_authority`, because the
proposal PDA is a singleton (9.3) and every operational act would then be a
once-per-cluster act. `test_anchor_wiring.py` 12.12/12.13 pin the whole chain —
field, initialize parameter and assignment, both `has_one` + `signer` pairs, the
admin-gated rotator, and the governance variant and arm that reaches it — and
the devnet suite (section 9) signs both calls with the key and proves a
non-operational signer is refused. UNCOMPILED and UNRUN here: no Rust or Anchor
toolchain exists in this environment, so the structural pins are the strongest
statement available until section 9 runs on a machine that has one.

THE INITIALIZE RACE IS CLOSED IN CODE, and its status has the same shape as
the funding guarantee below: all three programs now take their own ProgramData
account in the initializer and require its recorded `upgrade_authority_address`
to be the signing payer (`require_upgrade_authority`, one structurally identical
copy per crate). Since each state PDA is a fixed, create-once seed, that converts
"the deployer should initialize immediately" into "nobody but the deployer CAN
initialize", which is what the front-run exposure needed. The bytes read are the
loader's own ProgramData layout (variant 3 at offset 0, slot to 12, Option tag
at 12, authority at 13..45), so the check does not depend on a caller-supplied
value. It is WRITTEN AND PINNED — test_anchor_wiring.py 12.10/12.11 pass the
three real sources and fail six mutations (dropped account, dropped
`seeds::program`, flipped loader literal, deleted gate call, comparison against
the wrong key, missing immutable-program refusal) — and it is NOT YET OBSERVED:
the first `anchor build` and section 9's run are what execute it.

THE FUNDING GUARANTEE IS NEW CODE, so its exact status matters: the treasury →
jackpot CPI and the funder PDA are written, and the guarantee they make is a
property of the account CONSTRAINTS (a PDA under the pinned treasury id,
demanded as a signer) rather than of program logic. That property is checked
structurally here — the wiring drive fails if the seed literals disagree, if
`seeds::program` is dropped, or if the `signer` constraint goes — but it has
never been executed, and neither has the first `anchor build` of the treasury
crate that now depends on the jackpot with `features = ["cpi"]` (a new
dependency edge in the workspace). Treat "Treasury is the only funder" as
WRITTEN AND PINNED, not as OBSERVED, until section 9 runs green on a machine
with the pinned toolchain.
