# XMRJ DEVNET RUNBOOK — programs, state, and the token

**STATUS: COMPILED, NOT DEPLOYED — and nothing below has been run against a
cluster.** `cargo` 1.89.0 and `anchor` 1.1.2 are on this box now, and
`anchor build` produced the three `.so` artifacts and their IDLs on 2026-10-27
(`cargo check --workspace` is clean). `solana` is still absent, and no SOL is in
the deployer wallet: the devnet RPC faucet answers `410 closed`, so §2's funding
step is blocked on a human visiting the web faucet. Nothing is deployed on any
cluster and no XMRJ mint exists — `create_xmrj_mint.js` has never produced its
record file. This runbook is the operational order of the gated sequence in
[XMRJ_SOLANA_DEPLOYMENT_PLAN.md](XMRJ_SOLANA_DEPLOYMENT_PLAN.md); the plan holds
the rationale, this file holds the commands and what to check after each one.

Devnet only. Every command below names `devnet` explicitly (or is guarded by a
genesis-hash check) so that a stray `[provider]` value cannot redirect a deploy.

---

## 0. What you are bringing up

| artifact | id / value | source of truth |
| --- | --- | --- |
| `xmrj_treasury` program | `GGPyRTZB73reGyjANw9rdEFX8g26MmkKyAEpDqNQ2468` | `declare_id!` + `Anchor.toml` + `target/deploy/*-keypair.json` |
| `xmrj_jackpot` program | `6oHq39qTPVtXEiKPLt1mmxfRwRy3hucd3Qx5SJXdYfox` | same |
| `xmrj_governance` program | `AVbKyQ8KZa8hTHQwwSJTRGQsVQxVHzTmzmohVCH5CLe` | same |
| treasury state PDA | `["treasury_state"]` under the treasury program | `TREASURY_STATE_SEED` |
| jackpot state PDA | `["jackpot_state"]` under the jackpot program | `JACKPOT_STATE_SEED` |
| governance state PDA | `["governance_state"]` under the governance program | seed literal in the `InitializeGovernance` constraint (no named const) |
| governance authority PDA | `["governance_authority"]` under the governance program | `GOVERNANCE_AUTHORITY_SEED` |
| funding PDA (treasury signs, jackpot demands) | `["jackpot_funder"]` under the treasury program | `JACKPOT_FUNDER_SEED` |
| XMRJ mint | created in step 6, recorded in `xmrj_devnet_token.json` | this runbook |
| fixed supply | 100,000,000 XMRJ, 9 decimals = `100000000000000000` base units | `create_xmrj_mint.js` |
| pinned toolchain | anchor-cli 1.1.2 · solana-cli 3.1.10 · rustc 1.89.0 | `Anchor.toml [toolchain]`, `rust-toolchain.toml` |

### 0.1 Preflight

```bash
# from the workspace root
node --version && npm --version
for t in anchor solana rustc cargo avm; do
  printf '%-8s ' "$t"; command -v "$t" >/dev/null 2>&1 && "$t" --version 2>&1 | head -1 || echo MISSING
done
git status --porcelain          # expect only files you intend to commit
```

**Verify:** `node` is v24.x (the client deps are pinned to it), and you know
which tools are missing before you start. On the reference box here, node 24.18
is present and all five Solana/Rust tools are `MISSING` — step 1 is not optional.

---

## 1. Toolchain (pinned — never `stable`)

Follow plan §1.1 exactly; these are the only installs this workspace supports.

```bash
# 1a. host Rust (rustup first on native Windows; WSL/Linux/macOS below)
rustup toolchain install 1.89.0 --profile minimal --component rustfmt,clippy

# 1b. avm, then the pinned anchor-cli
cargo install avm --git https://github.com/otter-sec/anchor --locked
avm install 1.1.2
avm use 1.1.2

# 1c. the pinned Solana CLI (WSL / Linux / macOS)
sh -c "$(curl -sSfL https://release.anza.xyz/v3.1.10/install)"
```

Native Windows without WSL: plan §1.1 gives the versioned
`agave-install-init.exe` path; WSL is the route both toolchains recommend.

```bash
# 1d. verify — all four lines must match, and the resolvers must agree
anchor --version            # anchor-cli 1.1.2
solana --version            # solana-cli 3.1.10 (client:Agave ...)
rustc --version             # rustc 1.89.0
cargo --version             # cargo 1.89.0
avm list                    # 1.1.2 installed, marked in use
avm solana resolve          # resolves 3.1.10 out of Anchor.toml [toolchain]
avm platform-tools resolve  # the SBF compiler that release maps to
```

**Verify:** the three version strings equal the pins. `avm platform-tools
resolve` may print an install hint on first use — run the
`cargo build-sbf --install-only` it prints, then re-run.

**The platform-tools mount, once you actually build.** `anchor build` does NOT
use the version `avm platform-tools resolve` prints (v1.57 for 3.1.10):
cargo-build-sbf 3.1.10 picks platform-tools by its own compiled-in default —
**v1.52** (rustc 1.89.0-dev) — and mounts it at
`<solana-release>/bin/platform-tools-sdk/sbf/dependencies/platform-tools`, a
DIRECTORY SYMLINK to `~/.cache/solana/v1.52/platform-tools`. On Windows a shell
without Developer Mode (or admin) cannot create that symlink: the first run
dies with `A required privilege is not held by the client. (os error 1314)`,
later runs with `Access is denied. (os error 5)` — and each failure deletes the
cache it just downloaded. Either enable Developer Mode, or fetch the tarball
once and point that one path at it with a junction (`mklink /J`), which needs
no privilege:

```bat
curl -L -o %TEMP%\pt152.tar.bz2 https://github.com/anza-xyz/platform-tools/releases/download/v1.52/platform-tools-windows-x86_64.tar.bz2
mkdir "%USERPROFILE%\.cache\solana\v1.52\platform-tools"
tar -xjf %TEMP%\pt152.tar.bz2 -C "%USERPROFILE%\.cache\solana\v1.52\platform-tools"
mklink /J "%USERPROFILE%\.local\share\solana\solana-release\bin\platform-tools-sdk\sbf\dependencies\platform-tools" "%USERPROFILE%\.cache\solana\v1.52\platform-tools"
```

`test_anchor_wiring.py` is the offline guard that these five values agree with
the manifest, the crates and the plan: `python test_anchor_wiring.py` → 58/58.

---

## 2. Deployer wallet and devnet SOL

```bash
mkdir -p "$HOME/.config/solana"
solana-keygen new --outfile "$HOME/.config/solana/id.json"   # never commit this file
solana config set --keypair "$HOME/.config/solana/id.json"
solana config set --url devnet
solana address
solana airdrop 2
solana balance
```

**Verify:** `solana config get` shows `Rpc URL: https://api.devnet.solana.com`
and the keypair path; `solana address` matches `Anchor.toml [provider] wallet`.

**How much SOL.** Program accounts pay rent of ~7,000 lamports per byte of
`.so`, so budget from the real artifact sizes after step 3:

```bash
for so in target/deploy/*.so; do printf '%-44s %8s bytes\n' "$so" "$(stat -c%s "$so")"; done
solana rent <bytes> --url devnet     # per program; add 0.004 SOL for the mint
```

A few hundred KB per program is ~1.5–2.8 SOL each. Devnet airdrops are capped
per request and rate-limited — use the web faucet rather than hammering
`solana airdrop`, and top up **before** step 4 (a deploy that runs out of rent
half-way leaves a partially written program buffer). Devnet SOL is faucet-only;
the pool's XMR can never buy it (plan §1.3).

---

## 3. Build (the first compile this workspace has ever had)

```bash
anchor build

# THEN rebuild each program on its own, and prove the artifacts are programs.
# `anchor build` is not enough on this workspace — see the first bullet below.
for p in xmrj_treasury xmrj_jackpot xmrj_governance; do (cd "$p" && cargo build-sbf); done
```

**Verify:**

```bash
ls -l target/deploy/*.so target/idl/*.json
python test_xmrj_build_artifacts.py   # check 1.3 reads the ELF header of each .so
anchor keys list     # three ids, identical to the table in step 0
```

- Each `.so` exists and is the artifact step 4 deploys.
- **`anchor build` alone can leave an entrypoint-less artifact on the deploy
  path.** Each program is compiled TWICE here: once as the target, and once as
  the library the sibling that CPIs into it links — where the `cpi` feature
  implies `no-entrypoint`. Both write the same
  `target/sbpf-solana-solana/release/<program>.so`, which is the path
  `anchor build` copies into `target/deploy/`. When the entrypoint-less build
  is the one that lands there, the artifact is a *valid* ELF with `e_entry = 0`
  and 8 bytes of `.text`: an EMPTY program. The loader refuses it (`invalid
  account data for instruction` / `Entrypoint out of bounds`), so it can never
  overwrite a working program — but step 4 then fails after three attempts on
  that one program while the other two upgrade normally, and the retries resume
  the same bad buffer. On Oct 27 this happened to `xmrj_treasury`: 896 bytes on
  the deploy path, refused three times. `cargo build-sbf` from the program's own
  directory is a single-package build — nothing in it can turn `no-entrypoint`
  on — and that is what puts a real program back on the path. Check 1.3 of
  `test_xmrj_build_artifacts.py` is what proves it (ELF magic, `e_machine =
  sBPF`, non-zero `e_entry`, `.text` at or above 4096 bytes); a green
  `ls -l` does not.
- `anchor keys list` must print exactly the three ids above. If Anchor reports
  an id/keypair mismatch, `anchor keys sync` rewrites `declare_id!` and
  `Anchor.toml` from the keypairs — do that only on devnet keypairs, never on a
  mainnet set.
- **A build that fails with `could not find \`cpi\` in \`xmrj_jackpot\`** (and,
  next to it, `the \`#[global_allocator]\` in this crate conflicts with global
  allocator in: xmrj_jackpot`) is not a source problem. `cargo build-sbf`
  writes `target/sbpf-solana-solana/release/deps/lib<name>.rlib` — UNHASHED —
  for the crate it builds as the primary package, and a later build of a crate
  that *depends* on that program picks that path up as the artifact of the
  dependency unit whatever features it was built with (cargo reports the
  dependency `Fresh`). Delete the leftovers and rebuild:
  `rm -f target/sbpf-solana-solana/release/deps/libxmrj_*.rlib`, then
  `cargo build-sbf` in the program's own directory. Verified on Oct 27: three
  errors before, a 263 504-byte program after. `tools/xmrj_devnet_run.sh` §3
  does both steps on every run.
- **Expect compile errors on the first run.** The Anchor 1.x migrations
  (`Pubkey::from_str_const`, the `CpiContext` call sites, the removed
  `[registry]` section) are labelled in the source and were written against the
  published breaking-change list, not against a compiler. Fix forward; do not
  "solve" a mismatch by changing a pinned version.
- **The drives in this step are HOST-side, and the trees that cannot run them
  say so instead of passing.** `target/` is gitignored by design — the program
  keypairs in it are the PRIVATE halves of the program addresses — so a clone
  has no `.so`, no IDL and no keypair: `test_xmrj_build_artifacts.py` refuses an
  unbuilt checkout (`RESULT: cannot continue without artifacts — run `anchor
  build` first`), `tools/xmrj_loadtest.ts --offline` refuses too (its plan needs
  `target/idl/*.json`), and checks 4.2/4.3 of `test_anchor_wiring.py` report
  themselves **SKIPPED, with the reason**, rather than failed. CI runs the halves
  a clone *can* run (`xmrj-offline-drives`: `test_anchor_wiring.py` → 56 passed +
  2 skipped, the suite's offline half, and `test_xmrj_offline_gates.py`, which
  pins both refusals and the wiring of this very step). The skip is not a free
  pass: where `target/deploy` **exists**, a missing keypair fails 4.2 again —
  which is the regression that check is for.
- **CI also BUILDS, in a second job** (`xmrj-build-artifacts`, report §11.14):
  the pinned rust 1.89.0, Agave 3.1.10 and anchor-cli 1.1.2 from step 1, then
  this step's two-command build, then `test_xmrj_build_artifacts.py` against the
  result — so 1.3's "is this a real program", the freshness checks, the IDL
  surface the harness drives and the rent are enforced on every push. No program
  keypair comes from a CI secret, and that is arithmetic rather than policy: an
  address IS a public key, so a keypair file Anchor accepts for the ids this
  repo declares needs their PRIVATE halves, and nothing can derive those. The
  job  generates placeholder keypairs and passes `anchor build --ignore-keys`;
  check 3.3 then reports **SKIPPED with that reason** (3.4 still runs — Anchor
  stamps the IDL's address from `declare_id!`, not from the keypair, measured),
  and the keypair identity stays a host-side check (wiring 4.3) where the real
  halves are. The job's build step also runs the stale-rlib `rm` from the next
  bullet before its loop: a warm rebuild without it was measured failing on
  `xmrj_treasury` with `could not find `cpi` in `xmrj_jackpot``, which is what
  every push after the first one is.
- **Commit `Cargo.lock`** once the build is green (plan §1.5 says it should be
  the first lockfile this repo has). It is not gitignored.

---

## 4. Deploy the three programs

```bash
for p in xmrj_treasury xmrj_jackpot xmrj_governance; do
  anchor program deploy --program-name "$p" --provider.cluster devnet --no-idl
done
```

The cluster is named on the command on purpose: a bare deploy would use
`[provider]` whatever it happens to be.

`anchor deploy` is a deprecated alias, and on this host it cannot finish: after
each program's upload it attempts the IDL upload and dies with `Error: program
not found`, which aborts the run with 1 of 3 programs deployed — and the first
one really is on chain, which is what makes the failure confusing. `anchor
program deploy --program-name … --no-idl` is the same deploy without that step.
The IDLs stay in `target/idl/`, where the suite and the harness read them;
nothing in this runbook consumes an on-chain IDL.

**Verify, per program:**

```bash
solana program show GGPyRTZB73reGyjANw9rdEFX8g26MmkKyAEpDqNQ2468 --url devnet
solana program show 6oHq39qTPVtXEiKPLt1mmxfRwRy3hucd3Qx5SJXdYfox --url devnet
solana program show AVbKyQ8KZa8hTHQwwSJTRGQsVQxVHzTmzmohVCH5CLe --url devnet
```

Each must show:

- `Owner: BPFLoaderUpgradeab1e11111111111111111111111`
- `Authority: <your wallet>` — this is the upgrade authority, and **step 5's
  `initialize` calls are gated on it**: any other signer is refused by the
  program itself, before a single field is written
- `Executable: true`
- a lamport balance at or above the rent-exempt minimum for the account size

**Failure:** `insufficient funds for rent` → top up (step 2) and re-run; Anchor
resumes the buffer rather than starting over. If a deploy is interrupted
mid-buffer, `solana program show --buffers --url devnet` lists what exists, and
`solana program close --buffers --url devnet` reclaims it.

**Failure:** `invalid account data for instruction` with `Entrypoint out of
bounds` in the logs, three attempts in a row, after which Anchor prints
`Partial buffer: <address>` and `Resume: re-run the same command (buffer
auto-loaded)` — that is step 3's artifact, not the cluster. Re-running changes
nothing: the retry reuses the buffer written from the same empty `.so`. Fix
step 3 (rebuild the program on its own, re-run the artifact drive), then
`solana program close <buffer address> --url devnet` and delete
`target/deploy/<program>-upgrade-buffer.json` so the next deploy starts a fresh
buffer instead of resuming the old one.

---

## 5. Initialize the state — and prove it on chain

There is no `anchor` command that "initializes"; the initializers live in the
client suite, which creates each state only when it is absent. Install the
pinned client once:

```bash
npm ci
```

Then either the one-shot script (builds, deploys, runs the suite):

```bash
anchor run e2e-devnet
```

…or, after steps 3–4, the suite alone — plus the **graded** driver, which is the
same run with the gate and the grade attached:

```bash
XMRJ_DEVNET_E2E=1 \
ANCHOR_PROVIDER_URL=https://api.devnet.solana.com \
ANCHOR_WALLET="$HOME/.config/solana/id.json" \
python test_xmrj_devnet_e2e.py
```

Without those three variables the driver prints `SKIP` and grades 22 offline
checks instead — it will not silently pretend the live half ran.

**What the suite does (in order), and what it creates if missing:**

| # | assertion | creates when absent |
| --- | --- | --- |
| 0 | its RPC reports **devnet's genesis hash** `EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG`; anything else throws | — |
| 1 | all three ids are executable accounts, and the running wallet is each program's recorded upgrade authority | — |
| 2 | both state accounts exist with the initialized shape | treasury `initialize(reserve_bps, jackpot_bps, exchange_rate_bps, operational_authority)` — split must sum to 10 000, operational key is the suite's own; jackpot `initialize()` |
| 3 | a proposal reaches threshold and executes | `initialize_governance(owners[3], threshold 2)`, owners funded |
| 4 | the CPI actually changed the parameter on chain (read back) | — |
| 5 | a **direct** `setPayoutRules` from the deployer is refused, and the parameter is unchanged after | — |
| 6 | the jackpot is fundable only by the treasury: a recorded inflow → treasury→jackpot CPI, then a direct `fundJackpot` with the funder PDA **unsigned** and with the treasury program's own address — both refused | — |
| 7 | **the jackpot is PAID OUT through governance and only through governance**: a funded jackpot, a `TriggerPayout` proposal executed by the governance authority PDA, and the EFFECT read back — balance down by exactly the amount, `total_payouts_xmr` up by it, `last_winner` set, `balance + total_payouts == total_funded` closed, treasury untouched; then a direct `triggerPayout` from the deployer refused, the executed proposal refused a second time (no replay) | — |
| 8 | the **exchange rate is a live bound**, not a dead field: the rate is moved through governance in both directions and an amount the program ACCEPTED is re-asserted as REFUSED at the lower rate | — |

Steps 3, 7 and 8 each take a **fresh proposal id** from the state's own
`proposal_count` (`["proposal", governance_state, id_le_u64]`), so the suite runs
on a state that has already executed proposals — it does not need fresh state per
assertion (finding F5). Step 8 **starts** by putting the rate back to par (a
previous run leaves it doubled), so a rerun computes its bound from the same rate
the first run did.

Step 6's inflow is computed from the chain, not fixed: the treasury may forward
only what it has RECORDED as convertible, that record is cumulative, and the
stress harness writes to the same treasury. When a shared devnet has spent it,
the suite records the shortfall itself (the constant is a floor) and says so:

```
the treasury's convertible share was already spent (33300000 convertible at 10000 bps
against 65400000 forwarded): recording 108000000 XMRJ instead of 1000000, so this step
and step 8's bound have room to be tested at all
```

**The endpoint: one environment variable, and a busy one is expected.** Both
clients build their provider through [tools/rpc_resilience.ts](tools/rpc_resilience.ts),
and both read the cluster from the environment — `XMRJ_RPC_URL`, then
`ANCHOR_PROVIDER_URL`, and if neither is set they refuse to run rather than guess
(§5's genesis-hash guard is the second line of that defence). What that layer
does, so a free public RPC cannot decide the outcome:

* retries **only** transport failures — HTTP 429, HTTP 5xx, network errors —
  with exponential backoff, honouring `Retry-After`, bounded by 12 attempts and
  a 180 s budget; at the bound it hands the endpoint's own error back;
* never retries a program refusal (a JSON-RPC `error` inside an HTTP 200) or a
  400/403/404 — a real defect must fail loudly and fast;
* waits `XMRJ_CONFIRM_TIMEOUT_MS` (default 180 s) for a confirmation, because a
  throttled confirmation used to throw `TransactionExpiredTimeoutError` at 30 s;
* logs every retry as it happens and counts them, so each run ends with a line
  like `[rpc] 110 RPC requests, 4 retried (3 throttled, 0 server-side, 1 network),
  0 given up on` — that line is the evidence the throttle was survived.

To see that for yourself, aim a run at a throttled endpoint on purpose (the
proof in the execution report's §11.11 was produced this way):

```bash
node reports/probe_throttle.mjs        # makes api.devnet.solana.com answer 429
XMRJ_RPC_URL=https://api.devnet.solana.com \
  tools/xmrj_devnet_run.sh --e2e --stress
```

A second endpoint is always one variable away — `XMRJ_RPC_URL=https://devnet.rpcpool.com`
for the same cluster — and that works for a *dead* endpoint; it is no longer
needed for a *busy* one.

Neither the layer's behaviour nor that environment contract waits for a devnet
run: `npm run test:rpc` runs the layer's nine tests off a stub fetch, and
`python test_xmrj_rpc_endpoint.py` checks the wiring above in the source (21
checks, each proven able to fail). CI's `xmrj-rpc-contract` job runs both, plus
`npm run typecheck`.

**Verify:**

```bash
# the three state PDAs, derived exactly as the programs derive them
node -e '
const { PublicKey } = require("@anchor-lang/core").web3;
const p = (seed, id) => PublicKey.findProgramAddressSync([Buffer.from(seed)], new PublicKey(id))[0].toBase58();
console.log("treasury_state  ", p("treasury_state",  "GGPyRTZB73reGyjANw9rdEFX8g26MmkKyAEpDqNQ2468"));
console.log("jackpot_state   ", p("jackpot_state",   "6oHq39qTPVtXEiKPLt1mmxfRwRy3hucd3Qx5SJXdYfox"));
console.log("governance_state", p("governance_state","AVbKyQ8KZa8hTHQwwSJTRGQsVQxVHzTmzmohVCH5CLe"));
'
solana account <pda-from-above> --url devnet   # Owner must be that program id
```

- Mocha must end with a `passing` line and all nine `it()` blocks green.
- Run the suite through `anchor test` / `anchor run e2e-devnet` (both supply
  `ANCHOR_PROVIDER_URL`), not bare `npm test`: the suite creates its provider at
  load time, so without a provider URL it stops before the first assertion.
- The payout assertion (step 7) needs a **funded** jackpot: step 6 funds it,
  and step 7 asserts the payout is within the live cap BEFORE paying, so an
  unfunded jackpot fails loudly instead of "proving" a payout of zero. If step 6
  is red, read that first — step 7's failure is its shadow.
- **Rerun safety:** a second run asserts invariants instead of re-creating
  state. If the governance state was created earlier with **different** owner
  keypairs, the suite reports that the approval path belongs to whoever owns it
  rather than pretending to approve — expect that message, not a green run, on a
  shared devnet state.
- The state PDAs are create-once. To start over, see step 9.

---

## 6. Mint XMRJ (fixed supply, one shot)

```bash
node create_xmrj_mint.js
```

What it does: creates the SPL mint (`XMR Jackpot Treasury Token`, symbol
`XMRJ`, 9 decimals), creates four allocation accounts owned by the deployer, and
mints the whole 100,000,000 XMRJ once — 50 / 25 / 15 / 10 into
`treasury_reserve`, `jackpot_rewards`, `community_growth`, `team_foundation`.
It airdrops itself devnet SOL if short, refuses any chain but devnet by genesis
hash, and writes `xmrj_devnet_token.json`.

**Verify:**

```bash
cat xmrj_devnet_token.json     # mint, decimals, supply, allocated accounts
node create_xmrj_mint.js       # re-running reports the existing record
```

Expect: `supply 100000000000000000 base units`, `mint authority <your wallet>`,
and the four accounts carrying 50 000 000 / 25 000 000 / 15 000 000 / 10 000 000
whole XMRJ. A rerun prints `already recorded — mint …` and changes nothing.

**What this is not:** custody, vesting, or a price. All four accounts are
associated token accounts of **one** key, so one signature moves any of them,
and nothing vests on chain — recorded in the mint script's own header and in
plan §2.3.

---

## 7. Lock the supply (ONE-WAY)

```bash
node create_xmrj_mint.js --lock
```

**Verify:** `xmrj_devnet_token.json` now has `"mint_authority": null` and
`"mint_authority_disabled": true`; a further `node create_xmrj_mint.js` prints
`lock mint authority is already disabled`.

There is no unlock. Run this only when the allocation above is the final answer
for supply; on devnet it is a rehearsal, on mainnet it is the irreversible step
in plan §3.1.

---

## 8. Keep the record and the keys out of git

```bash
git status --porcelain | grep -E 'xmrj_devnet_token.json|keypair|\.so' || echo "clean"
```

- `target/` is gitignored, so the three program keypairs and every `.so` are
  already excluded — verify, do not assume.
- `xmrj_devnet_token.json` is **not** gitignored today. Either do not stage it,
  or add a rule (it holds only public addresses, but it is machine-local state):

  ```
  /xmrj_devnet_token.json
  ```
- `~/.config/solana/id.json` lives outside the repo; never copy it in.

---

## 9. Tear down / start over on devnet

**There is no reset button in these programs.** None of the three has a close
instruction, and every state account is created with `init` — create-once. So
closing a program and redeploying it to the same id does **not** give you fresh
state: the old PDAs stay on chain owned by that id, `initialize` fails with
"account already in use", and nothing in the code can ever close them. Plan
accordingly:

**A. Keep the ids (the normal devnet case).** Reruns are safe — the suite
asserts invariants instead of re-creating state, so use it to exercise
governance changes. Nothing to tear down.

**B. Re-id from scratch (destructive; the only real reset).** New program
keypairs means new ids means new state PDAs. Close the old programs **first**,
while you still hold their ids — once a keypair is gone that address can never
be closed and its rent is stranded:

```bash
# 1. reclaim the old programs' rent (state PDAs stay orphaned either way)
solana program close GGPyRTZB73reGyjANw9rdEFX8g26MmkKyAEpDqNQ2468 --url devnet
solana program close 6oHq39qTPVtXEiKPLt1mmxfRwRy3hucd3Qx5SJXdYfox --url devnet
solana program close AVbKyQ8KZa8hTHQwwSJTRGQsVQxVHzTmzmohVCH5CLe --url devnet

# 2. new ids: generate the three keypairs, then sync both sides from them
rm -f target/deploy/xmrj_treasury-keypair.json \
      target/deploy/xmrj_jackpot-keypair.json \
      target/deploy/xmrj_governance-keypair.json
solana-keygen new --no-bip39-passphrase --silent -o target/deploy/xmrj_treasury-keypair.json
solana-keygen new --no-bip39-passphrase --silent -o target/deploy/xmrj_jackpot-keypair.json
solana-keygen new --no-bip39-passphrase --silent -o target/deploy/xmrj_governance-keypair.json
anchor keys sync        # rewrites declare_id! AND Anchor.toml from the new keypairs
anchor keys list        # three NEW ids — update the table in step 0

# 3. rebuild and redeploy under the new ids
anchor build
anchor deploy --provider.cluster devnet
```

Then run step 5 again (new state PDAs), and for a new token:

```bash
rm -f xmrj_devnet_token.json    # step 6 mints a fresh one; a mint account itself can never be closed
```

The old programs and their state remain on devnet as unreferenced garbage.
`solana program close <old-id> --url devnet` still reclaims the *program's*
rent; the orphaned state accounts keep theirs, unrecoverable. For a deploy that
died mid-upload, `solana program show --buffers --url devnet` lists the buffers
and `solana program close --buffers --url devnet` reclaims them.

---

## 10. What this runbook does NOT cover

1. **Program upgrades are still owned by the deployer key.** Governance owns the
   *parameters* (split, exchange rate, payout cap, operational authority) via
   CPI from a passed proposal, but no instruction transfers program upgrade
   authority — the wallet from step 2 can replace all three programs. Decide and
   execute the handover before any mainnet step.
2. **Custody.** Four allocation accounts, one key, no on-chain vesting (§6).
3. **The conversion service.** `xmrj_conversion_service/` is a spec: no server,
   no Monero RPC, no Kraken integration.
4. **Mainnet.** `[programs.mainnet]` is deliberately absent; plan §8 is the
   gated sequence, with fresh keypairs and `anchor keys sync` never run against
   a mainnet set.
5. **Nothing off-chain knows XMRJ exists**, by design: the pool records referral
   awards in `referral_xmrj_awards`, and `test_no_xmrj_claims.py` fails any
   shipping surface that implies a token rail. That stays green until the token
   is real.

---

## Appendix A — copy-paste, in order

```bash
# 1-2: toolchain, wallet, funds
rustup toolchain install 1.89.0 --profile minimal --component rustfmt,clippy
cargo install avm --git https://github.com/otter-sec/anchor --locked
avm install 1.1.2 && avm use 1.1.2
sh -c "$(curl -sSfL https://release.anza.xyz/v3.1.10/install)"
solana-keygen new --outfile "$HOME/.config/solana/id.json"
solana config set --keypair "$HOME/.config/solana/id.json"
solana config set --url devnet
solana airdrop 2

# 3-5: build, deploy, initialize + prove
anchor build
npm ci
anchor deploy --provider.cluster devnet
XMRJ_DEVNET_E2E=1 ANCHOR_PROVIDER_URL=https://api.devnet.solana.com \
  ANCHOR_WALLET="$HOME/.config/solana/id.json" python test_xmrj_devnet_e2e.py

# 6-7: the coin
node create_xmrj_mint.js
node create_xmrj_mint.js --lock
```

Stop after step 5 if the suite is not green. The mint is independent of the
programs, so a green suite is not required to mint — but shipping a token while
the treasury proofs are red is exactly the omission
`test_no_xmrj_claims.py` exists to prevent.

## Appendix B — failure playbook

| symptom | cause | fix |
| --- | --- | --- |
| `initialize` fails with an authorization error | payer is not the program's upgrade authority | run from the wallet in `Anchor.toml [provider] wallet` that deployed it |
| deploy: `insufficient funds for rent` | under-funded for `.so` size | top up (§2), re-run; Anchor resumes the buffer |
| suite throws at step 0 | RPC is not devnet (URL label is not trusted) | point `ANCHOR_PROVIDER_URL` at `https://api.devnet.solana.com` |
| suite/harness: a wall of `Server responded with 429 Too Many Requests. Retrying after …`, or `TransactionExpiredTimeoutError: Transaction was not confirmed in 30.00 seconds` | an older client, or the layer's bound (12 attempts / 180 s) exceeded | both clients now wait a busy endpoint out and honour `Retry-After`; read the run's final `[rpc] … retried …, 0 given up on` line — if it says `given up on`, this host's budget for that endpoint is spent: wait for the window to pass, or set `XMRJ_RPC_URL` to the mirror |
| suite step 6 refused with `ExceedsConvertibleShare` (and step 8's precondition `expected -N to be at least 1`) | the treasury's convertible share is **cumulative** and already spent — the harness records inflows and forwards against the same `treasury_state`, and its rate scenario restores par, which lowers the ceiling below an earlier doubled-rate forwarding | the suite now sizes that inflow from the chain itself (report §11.12) and repairs the parity; if it still refuses, re-run and read the `convertible …. forwarded …` numbers it prints |
| suite: "owners are not this suite's keypairs" | governance state created earlier by another key set | re-id from scratch (§9 B), or run with the keys that own the state |
| `anchor build` id mismatch | keypair and `declare_id!` disagree | `anchor keys sync` (devnet keypairs only) |
| airdrop rate-limited | faucet cap | use the devnet web faucet, then re-check `solana balance` |
| mint script: genesis mismatch | RPC answered from another cluster | it refuses by design — nothing to fix in the script |
| `xmrj_devnet_token.json` says `mint_authority: null` but you wanted to mint more | supply is locked (§7) | not recoverable; a new mint is the only path |
