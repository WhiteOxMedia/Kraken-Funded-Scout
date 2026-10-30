# XMRJ DEVNET EXECUTION REPORT

**Date:** 2026-10-27 · **Host:** Windows 11, Git Bash · **Cluster target:** devnet
**Runbook executed:** [XMRJ_DEVNET_RUNBOOK.md](XMRJ_DEVNET_RUNBOOK.md)
**Plan:** [XMRJ_SOLANA_DEPLOYMENT_PLAN.md](XMRJ_SOLANA_DEPLOYMENT_PLAN.md)

---

## 0. Verdict first

**MAINNET: NO-GO — but no longer because nothing has run.** The devnet run
happened end to end on 2026-10-27 and every previously-blocked step is now
backed by a signature or a decoded account: the deployer was funded, all three
programs are deployed with the deployer as upgrade authority, the graded suite
is **23/23 green including its payout step** (and green again on a *re-run*, see
F11), the stress harness exercises the chain and **S10 pays a funded jackpot
through governance**, and the XMRJ mint exists with its authority disabled. See
**§11** for the run; §§1–10 above are kept as the record of the state *before*
it.

What remains NO-GO for mainnet is what the live run found and what it still does
not cover:

| # | item | status |
| --- | --- | --- |
| B1 | the devnet deployment executed | **CLOSED** — 3 programs, authority `7YhEG31B…Sb5`; §11.2 |
| B2 | the payout executed on a chain | **CLOSED** — suite §3.9 *and* harness S10 both paid a funded jackpot; §11.4, §11.5 |
| B3 | the stress suite ran against a cluster | **CLOSED** — P0, S0–S10; §11.5 |
| F8 | **the programs derived a ProgramData address that does not exist** (`["programdata", id]` instead of `[id]`), so `initialize` was refused with `NotProgramData` (6004) on a real cluster. Eleven offline drives were green on a derivation that cannot work. | **fixed, red-then-green**; §11.3 |
| F9 | `create_xmrj_mint.js` double-initialized the treasury ATA (token error `0x6`); the mint had never run live either. | **fixed**; §11.7 |
| F11 | **the suite was a first-run-only gate.** Two of its assertions read *mutable* state as if it were the shape `initialize` had just written — `jackpotBalanceXmr == 0` and "the payout cap BEFORE the proposal is 10 000" — so it passed 23/23 on virgin state and then failed on every re-run. The 429s had been masking them: once the endpoint cleared, mocha ran to completion and reported `7 passing, 2 failing` with two real `AssertionError`s. | **fixed**; §11.10 |
| F10 | **the artifact the §4 deploy uploaded for `xmrj_treasury` was an entrypoint-less build of the same crate** — 896 bytes, `e_entry = 0x0`, 8 bytes of `.text`: a valid ELF that is an EMPTY program. The loader refused it (`Entrypoint out of bounds`, three attempts), so it could not overwrite the live program, but one of three programs failed to upgrade. Found by re-running the whole runbook in one pass, after eleven green offline drives had passed. | **fixed + gated**; §11.8 |
| R1 | **the program upgrade authority is still the deployer's hot key.** Governance owns the parameters, not the bytecode. | **OPEN** — §10 item 1 |
| R2 | **no off-chain conversion exists**: `xmrj_conversion_service/` is still a spec, so "XMRJ backing" is a design, not a mechanism. | **OPEN** — §10 item 3 |
| R3 | the harness's live half needed four fixes before it could run at all, and the public devnet RPC's rate limit aborted two attempts. Those are tool defects, not chain results. | **fixed, and proven on the throttled endpoint**; §11.6, §11.11 |
| F12 | **the endpoint's rate limit decided the outcome of the run.** web3's built-in retry gives up on a 429 after five attempts (~15 s) and its confirmation path gives up at 30 s, so a busy free RPC turned into "the programs do not work": three passes died on it, and it was hiding real assertion failures (F11). | **fixed, with the proof**; §11.11 |
| F13 | **the suite's funding step assumed a treasury nobody else had touched** — it recorded a fixed 1 000 000 XMRJ and forwarded into a convertible share that the stress harness had already spent (33.3 M convertible against 65.4 M forwarded), so the program refused the funding by its own bound and step 8's headroom precondition was negative. Not an RPC failure: `1 retried of 57`. | **fixed**; §11.12 |

A local validator is *still* not available on this host — `solana-test-validator`
panics with `Os { code: 1314 … }` (a symlink privilege Agave wants and Windows
withholds), and Docker/WSL are absent — so devnet was the only executable
cluster, and the web faucet the only way to pay for it.

---

## 1. What was executed, in one table

| runbook step | status | note |
| --- | --- | --- |
| §0.1 preflight (five tools) | **PASS** | all present and at the pinned versions (§2) |
| §1 toolchain | **PASS (with a caveat)** | native-Windows Agave, not the installer path; §2 |
| §2 wallet | **PASS — funded** | `7YhEG31B…Sb5`; two finalized 5 SOL faucet deposits, 10 SOL; §11.1 |
| §3 build (clean) | **PASS** | `anchor build`, rc 0; rebuilt after the F8 fix (§11.3) |
| §4 deploy | **PASS** | 3/3 on devnet via `anchor program deploy --no-idl`; plain `anchor deploy` aborts on its IDL step (§11.2). Re-run after F10 with each program rebuilt on its own — 3/3 again (§11.9) |
| §5 initialize + prove | **PASS** | the graded suite: **23 passed, 0 failed**, payout included, and green again on a re-run; §11.4, §11.9 |
| §6 mint | **PASS** | 100,000,000 XMRJ, four tranches 50/25/15/10; §11.7 |
| §7 lock supply (one-way) | **PASS** | `mint_authority: null`, `mint_authority_disabled: true` |
| §8 keep records out of git | **PASS** | `xmrj_devnet_token.json` is untracked and unstaged (the runbook's first option) |
| §9 teardown | n/a | devnet state is kept; reruns assert invariants |

Offline verification (not part of the runbook, but the evidence that the
sources and the artifacts agree):

| drive | result | log |
| --- | --- | --- |
| `cargo check --workspace` (all four crates) | **clean**, 0 errors | `reports/xmrj_offline_drives.log` |
| `test_anchor_wiring.py` (source ↔ manifest ↔ plan) | **58 / 58** | same |
| `test_xmrj_build_artifacts.py` (artifacts ↔ IDL ↔ harness, incl. that each `.so` is a real program) | **11 / 11** | same |
| `test_xmrj_devnet_e2e.py` offline half | **22 / 22**, live half `SKIP` (opt-in) | same |
| `tsc --noEmit` (`tests/` + `tools/`) | **0 errors** | same |
| `tools/xmrj_loadtest.ts --offline` | **0**, 12 scenarios planned (P0, S0–S10) + 13 addresses derived | same |
| `tests/xmrj_rpc_resilience.ts` (the RPC layer, stub fetch, no chain) | **9 / 9** | CI job `xmrj-rpc-contract` |
| `test_xmrj_rpc_endpoint.py` (the endpoint contract: environment-only, no default cluster) | **21 / 21** | same |
| `test_anchor_wiring.py` on an UNBUILT checkout (what CI has) | **56 passed, 2 skipped** | CI job `xmrj-offline-drives` (§11.13) |
| `test_xmrj_offline_gates.py` (which drives a clone can run, and that the others refuse) | **13 / 13**, 10 mutations | same |
| `test_xmrj_build_artifacts.py` against a CI-STYLE build (placeholder keys, `--ignore-keys`) | **10 passed, 0 failed, 1 skipped** | CI job `xmrj-build-artifacts` (§11.14) |

---

## 2. Environment and toolchain (recorded, not assumed)

```
anchor-cli 1.1.2
solana-cli 3.1.10 (src:7bc9c805; feat:1620780344, client:Agave)
rustc 1.89.0 (29483883e 2025-08-04)
cargo 1.89.0 (c24e10642 2025-06-23)
node v24.18.0 · npm 11.16.0
```

The versions match `Anchor.toml [toolchain]` (`anchor_version = "1.1.2"`,
`solana_version = "3.1.10"`) and `rust-toolchain.toml` (`channel = "1.89.0"`),
so the pins the wiring drive enforces are the pins the build actually used.

**Caveat that matters for reproduction:** the documented install path
(`agave-install-init.exe` / the `release.anza.xyz` installer) **fails on this
host** — `agave-install-init: machine architecture is currently unsupported`.
The 3.1.10 release was therefore unpacked manually to
`~/.local/share/solana/solana-release/bin` and prepended to `PATH`. `anchor
build` finds `solana` there and skips its own toolchain install; without it,
`anchor --version` alone dies trying to install Agave. Two consequences:

1. `tools/xmrj_devnet_run.sh` exports that directory itself, so the runbook
   order works on this box without a shell-profile change.
2. The SBF compiler is the release's built-in **platform-tools v1.52**, mounted
   through the junction the runbook §1d describes
   (`…/platform-tools-sdk/sbf/dependencies/platform-tools` →
   `~/.cache/solana/v1.52/platform-tools`). It is present and was used; this is
   the mount that fails with `os error 1314` without Developer Mode.

---

## 3. Build report (§3 of the runbook)

**Command (exactly):**

```bash
cargo clean                       # 5170 files, 1.2 GiB removed, rc 0
cp <keypair backup>/*-keypair.json target/deploy/   # the three ids survive a clean
anchor build                      # rc 0
```

The keypairs are restored deliberately: `cargo clean` deletes `target/`, and
`target/deploy/<program>-keypair.json` *is* the program id. Losing them changes
every address and every state PDA, which is runbook §9 B, not a rebuild.

| metric | value |
| --- | --- |
| wall clock, `cargo clean` → artifacts on disk | **131 s** (`08:12:53` → `08:15:04`) |
| crates compiled | 272 `Compiling` lines |
| errors | **0** |
| warnings | **28** in the SBF pass that produces the deployed artifact (`xmrj_treasury` 12, `xmrj_jackpot` 8, `xmrj_governance` 8). Anchor then compiles the crates twice more for the IDL/`test` profile, where the counts differ (`treasury` 14, `jackpot` 8 then 10) because the `cpi`/test feature sets leave different fields unused — the numbers in the other passes are not the artifact's |
| warning kinds | 43 `unexpected cfg condition value` (anchor's own `anchor-debug` / `custom-heap` / `custom-panic` gates) + 17 `use of deprecated function anchor_lang::deprecated_account_info_usage` (counted across the three passes anchor runs; 5 sites per pass) |
| release profile in force | `overflow-checks = true`, `lto = "fat"`, `codegen-units = 1` |

**Artifacts** (`target/deploy/`, the files §4 would upload):

| program | id | `.so` bytes | rent-exempt (`(size+128)·5080`) |
| --- | --- | --- | --- |
| `xmrj_treasury` | `GGPyRTZB73reGyjANw9rdEFX8g26MmkKyAEpDqNQ2468` | 188 616 | 0.958820 SOL |
| `xmrj_jackpot` | `6oHq39qTPVtXEiKPLt1mmxfRwRy3hucd3Qx5SJXdYfox` | 181 832 | 0.924357 SOL |
| `xmrj_governance` | `AVbKyQ8KZa8hTHQwwSJTRGQsVQxVHzTmzmohVCH5CLe` | 220 792 | 1.122274 SOL |
| **total** | | 591 240 | **3.005450 SOL** + ≈0.0015 (mint + state) |

`anchor keys list` prints exactly those three ids, `test_xmrj_build_artifacts.py`
confirms each IDL's `address` equals its crate's `declare_id!` and each
`-keypair.json`, and every `.so` is newer than its crate's newest source file.

**Warnings are not cosmetic-neutral, but none is a defect:**
`anchor-debug`-style `cfg` warnings come from Anchor's generated code and will
disappear with an Anchor release that declares those cfgs. The
`deprecated_account_info_usage` warnings are ours: `AccountInfo<'info>` in five
`Accounts` structs of `xmrj_treasury` (the `#[account(signer)]` /
`has_one`-paired fields). Anchor 1.1.2 wants `UncheckedAccount`. Doing so is a
five-line, behaviour-preserving change — recommended, not done here, because
this pass is an execution of the frozen sources, not a refactor.

**Reproducibility note.** The `.so` sizes differ slightly from the 07:52 build
(`jackpot` 181800 → 181832, `treasury` 188656 → 188616). Same sources, same
compiler, same flags: the difference is small enough to be metadata, but it is
recorded rather than glossed — a binary-equality check between two clean builds
has **not** been demonstrated, so "reproducible build" is not claimed.

---

## 4. Deployment report (§4 of the runbook) — BLOCKED, with the receipts

### 4.1 The wallet cannot pay, and the faucet is closed

```
$ solana address
7YhEG31B7vyDp42qv31UW3TLukA4nhGJ89nwnQW12Sb5
$ solana balance --url devnet
0 SOL
$ solana airdrop 2 --url devnet
Requesting airdrop of 2 SOL
Error: airdrop request failed. This can happen when the rate limit is reached.
$ curl -sX POST https://api.devnet.solana.com -d '{"jsonrpc":"2.0","id":1,
    "method":"requestAirdrop","params":["7YhEG31B…Sb5",2000000000]}'
{"jsonrpc":"2.0","error":{"code":410,
 "message":"The RPC faucet is closed on this cluster. Request test SOL at https://faucet.solana.com"}}
```

That is not a rate limit that backoff fixes: the RPC faucet method is **gone**
(HTTP-equivalent code `410`). The remaining route is
<https://faucet.solana.com>, a web page with a captcha and a GitHub sign-in —
i.e. a human action, not a command. **3.005 SOL + ≈0.0015** is the number to
fund (`solana balance` should read ≥ 3.01 before §4).

### 4.2 Why no local validator was used instead

Three attempts, all recorded in `reports/xmrj_local_validator*.log`:

1. **`--ledger <path>` alone → panic before any RPC.** Agave creates a
   `validator.log` **symlink** in the ledger directory; without Developer Mode
   Windows returns `1314` and the process panics:
   `Os { code: 1314 … "A required privilege is not held by the client." }`
   at `validator/src/bin/solana-test-validator.rs:119`. **Worked around** by
   passing `--log`, which takes the code path that skips the symlink (the fix is
   documented in `tools/xmrj_devnet_run.sh`'s header and §5 of this report).
2. **`--log` → genesis fetch succeeds, unpack fails.** The validator downloads a
   valid 350 036-byte `genesis.tar.bz2` (checked with Python's `tarfile`: it
   contains `genesis.bin`, 1 468 875 bytes, plus a `rocksdb/` snapshot), then:
   ```
   Error: failed to start validator: Failed to create ledger at …:
   io error: Error checking to unpack genesis archive:
   IO error: Access is denied. (os error 5)
   ```
3. **Pre-extracted `genesis.bin` → same failure**, plus
   `failed to stash problematic rocksdb: The directory is not empty. (os error 145)`.
   The archive is fine (Python extracts it), so the defect is in Agave 3.1.10's
   own `unpack_genesis` on Windows — the same class of privileged-operation bug
   as the symlink, in the path that cannot be switched off with a flag.

`docker`, `podman` and `wsl` are all absent on this host, and installing WSL
needs administrator rights plus a reboot. So: **no executable cluster was
available to this run**, and this report does not pretend otherwise.

### 4.3 What the first funded run must do

Once `solana balance --url devnet` ≥ 3.01:

```bash
CLUSTER=devnet bash tools/xmrj_devnet_run.sh --deploy --e2e --stress
```

That executes §4 (`anchor deploy --provider.cluster devnet`), §4's
`solana program show` verification for all three ids, §5's graded suite
(`XMRJ_DEVNET_E2E=1 … python test_xmrj_devnet_e2e.py`) and the S0–S10 stress
harness — each step's command, output and exit status lands in
`reports/xmrj_runbook_devnet_<ts>.log`. Then, only if §5 is green:

```bash
CLUSTER=devnet bash tools/xmrj_devnet_run.sh --mint
```

### 4.4 Transactions, program ids, addresses

* **Transactions sent to devnet: none.** No signature exists to report; the
  report's JSON harness output records `transactions_sent: 0`.
* **Program ids (deployed nowhere yet, pinned in three places):**
  `GGPyRTZB73reGyjANw9rdEFX8g26MmkKyAEpDqNQ2468`,
  `6oHq39qTPVtXEiKPLt1mmxfRwRy3hucd3Qx5SJXdYfox`,
  `AVbKyQ8KZa8hTHQwwSJTRGQsVQxVHzTmzmohVCH5CLe`.
* **State addresses that the deploy will create** (derived from the seeds, the
  same values the programs compute — recorded in
  `reports/xmrj_loadtest_offline_*.json`):

| account | address |
| --- | --- |
| `treasury_state` | `BWgffBA4aWRfxdFUy88JuaxESszZzjSBinXEKVFMAhpB` |
| `jackpot_state` | `2i1MLDGMkjbRzhMMsv7ZvWJvfMcEHn8znBHqMJTCiqxA` |
| `jackpot_funder` (treasury PDA) | `8Cy6dQqVcHvjuvFUEwzT5zsWphkLYozLrw8cj3PZDgMC` |
| `governance_state` | `9pqcRCLjbHpKpJftpkPS7TNfv1VRiqUnScZ4rPXimdQ3` |
| `governance_authority` | `G7myNZ9W27oEQu5BF9bpmVNeLoUgRtiys59w9r93rVqH` |
| `proposal` | `BLfK7guV5hjtDcr2ubQUmhKMN3zcKTVDF2pHVyzSpAJg` |
| treasury ProgramData | `5ALdAqdhEmXK8gDjj3JsT84Cs1QVHy98Rd3bHW74D93L` |
| jackpot ProgramData | `J6Who9dKyuUeZ4Wv7ujkvMWeHfpGZqZZTBuRHGvHZyXP` |
| governance ProgramData | `E1oaAq5iv5YtykK9FYj1vfBfENefp5E3EbxmiUqQKPwo` |

---

## 5. Windows-specific findings (reusable, not one-offs)

| # | symptom | cause | fix |
| --- | --- | --- | --- |
| W1 | `anchor --version` tries to install Agave and dies: `machine architecture is currently unsupported` | the 3.1.10 native-Windows installer refuses this host; `anchor` needs `solana` on `PATH` | put `~/.local/share/solana/solana-release/bin` first on `PATH` (the script does this) |
| W2 | `solana-test-validator` panics with `os error 1314` at startup | it creates a `validator.log` symlink in the ledger dir | run it with `--log` (skips the symlink branch entirely) |
| W3 | `cargo-build-sbf` fails with `os error 1314 / 5`, deleting its cache | platform-tools mount is a symlink | pre-extract v1.52 and `mklink /J` the mount (runbook §1d) — done, and the build works |
| W4 | test validator cannot build a ledger: `Access is denied. (os error 5)` unpacking genesis | Agave's `unpack_genesis` performs a privileged operation on Windows | **no workaround found**; use WSL/Linux (or Developer Mode + a Linux host) for local validators. This is why §4.2 exists. |

---

## 6. Stress testing — designed, typechecked, and honest about not having run

`tools/xmrj_loadtest.ts` (2 431 lines, `npm run loadtest`) is
cluster-agnostic and self-bootstrapping: it reads the built IDLs, refuses to
guess a cluster, verifies the programs are deployed and that the running wallet
is each program's upgrade authority, creates any missing state (treasury,
jackpot, governance) and then drives twelve scenarios — P0 and S0–S10. S10 is
the governance → payout path: it is the live counterpart of F1 and the acceptance
criterion for this whole exercise. It writes a JSON report with
every signature, every raw error string, a root cause and a suggested fix, and
exits non-zero when an **expected** outcome does not happen.

| id | scenario | what it asserts (both directions) | status |
| --- | --- | --- | --- |
| P0 | preflight | cluster identity by genesis **and** slot depth (a local validator seeded with devnet's genesis is caught here), 3/3 programs executable, wallet is upgrade authority, balance ≥ one program's rent | NOT_EXECUTED |
| S0 | state bootstrap | creates what is missing, *asserts* what exists (create-once), checks the operational key this run can sign with, calls `get_jackpot_state` | NOT_EXECUTED |
| S1 | repeated mint operations | N inflow+funding pairs; the treasury counters move by **exactly** `amount`, `amount·bps/10000`, and both sides of the bridge move by the forwarded amount | NOT_EXECUTED |
| S2 | concurrent transactions | 8–40 instructions in flight from one signer: the state delta must equal the number of **confirmed** transactions (conservation, not "all succeeded") | NOT_EXECUTED |
| S3 | high-volume account creation | N fresh accounts, each read back at the exact lamport balance | NOT_EXECUTED |
| S4 | failed transactions | 14 instructions that must be **refused**, classified: `ConstraintHasOne`, `UnauthorizedTreasuryCaller` (6001), `UnauthorizedAdmin` (6002), `NotProgramData` (6003), `AccountAlreadyInUse`, `NotEnoughOwners`, signature-missing (including the governance authority PDA passed UNSIGNED to `trigger_payout`), `ProposalIdOutOfSequence` (an id that is not the state's next) | NOT_EXECUTED |
| S5 | boundary amounts | `0` and `1` accepted; `u64::MAX` refused (`Overflow`, because `amount·reserve_bps` overflows); one extra inflow to give the funding bound a standing edge; `fundable` (the exact edge, VALUED AT THE LIVE RATE) **accepted**; `fundable + 1` refused (`ExceedsConvertibleShare`); cap `10 001` refused | NOT_EXECUTED |
| S6 | wallet permissions | operational key allowed; deployer and strangers refused; the treasury admin is the governance PDA, not a wallet; direct `set_exchange_rate` / `set_operational_authority` refused | NOT_EXECUTED |
| S7 | recovery after failure | after a refusal **and** after an arithmetic refusal the account is byte-identical, and the same account then accepts a valid instruction | NOT_EXECUTED |
| S8 | RPC retry / failure | dead endpoint fails fast (< 60 s); a retry with a fresh blockhash per attempt succeeds; the retry budget is finite and reports the last error | NOT_EXECUTED |
| S9 | governance round trip | propose under the state's NEXT id → stranger's approval refused (`NotOwner`) → execute below threshold refused (`ThresholdNotMet`) → approve to threshold → execute → **parameter read back from the treasury state** → re-execute refused (`AlreadyExecuted`) → a SECOND proposal restores par (so the run leaves the rate where it found it) | NOT_EXECUTED |
| S10 | governance **pays the jackpot** | three controls first (a direct `triggerPayout` from a wallet refused; the governance PDA passed UNSIGNED refused; and a `TriggerPayout` proposal for MORE than the balance refused AT EXECUTION with `InsufficientJackpotBalance` — the only place that bound is observable, because the signature constraint is checked before the body) → an approved `TriggerPayout` proposal executes through the CPI → balance falls by exactly the amount, `total_payouts_xmr` rises by it, `last_winner` is the named winner, `balance + total_payouts == total_funded` still closes, the treasury does not move → re-execute refused (`AlreadyExecuted`) | NOT_EXECUTED |

Boundary arithmetic is pre-computed from the programs' own formulas at the
runbook's 7000/3000 split, so the numbers can be checked by hand before a single
lamport is spent (`npx ts-node tools/xmrj_loadtest.ts --offline` prints them).

**S9 and S10 each run on every pass:** a proposal's PDA is
`["proposal", governance_state, proposal_id]`, and the id comes from the state's
own `proposal_count`, so each scenario makes its own proposal and neither is
single-shot. (That was finding **F5** — one action per cluster, ever — and it is
why these scenarios can be re-run instead of skipped on the second pass.) Both
still SKIP, with the reason, when the governance state's owners are not the keys
the harness holds: they need enough approvals to reach the threshold.

---

## 7. Hostile analysis — what the code does that the docs do not say

These are read from the compiled IDLs and the Rust sources that produced them;
the first is severe enough to change the go/no-go on its own.

### F1 — CRITICAL, **FIXED**: the jackpot can be funded but never paid out

**What it was.** 1. `xmrj_jackpot::Initialize` does not take an
`admin_authority` **parameter**; it *derives* it: `seeds =
[b"governance_authority"], seeds::program = GOVERNANCE_PROGRAM_ID`. So the
recorded `admin_authority` is always the governance authority PDA.
2. `trigger_payout` requires that account to equal `state.admin_authority` **and
to be a `Signer`**. A PDA has no keypair, so it can only sign inside a CPI from
`xmrj_governance`. 3. `execute_action` matched on a `GovernanceAction` with no
variant that called `trigger_payout`. Every satoshi of `jackpot_balance_xmr` was
a one-way ratchet: `total_payouts_xmr` and `last_winner` could never change.

**What it is now.** `GovernanceAction::TriggerPayout { winner, amount_xmr }`
(appended last, so no existing variant's Borsh index moved) with a
`xmrj_jackpot::cpi::trigger_payout` arm that passes `governance_authority` as the
callee's `admin_authority` and signs the CPI with
`[GOVERNANCE_AUTHORITY_SEED, authority_bump]`. There is **exactly one** call
site for `trigger_payout` in the whole workspace, and it is that arm — the
governance authority PDA is the only signer, and governance is the only path.

**Evidence (offline).** `cargo check --workspace` clean; the wiring drive's
12.14 asserts the chain (variant → arm → CPI → signed by the authority →
callee's `has_one`/signer) and 12.15 mutates eight ways and red-lights each;
the suite pin adds the payout's *effect* assertions (§3.9 of the drive); the
harness's **S10** takes a funded jackpot through propose → approve → execute and
asserts the balance falls by exactly the amount, `total_payouts_xmr` rises by
it, `last_winner` is the named winner, `balance + total_payouts == total_funded`
still closes, the treasury does not move, and the direct and unsigned-PDA
attempts are refused.

**What is still open.** No chain has executed it. S10 and the suite's §3.9 are
`NOT_EXECUTED` / `SKIP` until a cluster exists (B1), so this is *implemented and
statically proven*, not *demonstrated*. That is the whole reason the verdict is
still NO-GO.

### F2 — `exchange_rate_bps` was written by three instructions and read by none — **FIXED**

**What it was.** `set_exchange_rate` and `GovernanceAction::SetExchangeRate`
wrote `state.exchange_rate_bps`; nothing read it. `fund_jackpot_with_xmr`
bounded the forwarded **XMR** by `total_converted_xmrj −
total_jackpot_funded_xmr`, an **XMRJ** counter, i.e. 1:1 — so the on-chain bound
was not the economic bound, and a proposal "changing the rate" changed an unused
field. That is worse than no field: it looks like a control.

**What it is now.** The rate VALUES the recorded share in the funding bound,
with the same integer arithmetic and the same direction as
`xmrj_conversion_service` (which defines the unit):

```
convertible_xmr = total_converted_xmrj * exchange_rate_bps / 10_000
forwardable     = convertible_xmr - total_jackpot_funded_xmr
```

Computed cumulatively in `u128`, `saturating_sub` on the subtraction (a proposal
that LOWERS the rate can leave the ceiling below what was already forwarded;
that must read as "bound exhausted", not as `Overflow`), and a rate of `0` is
refused in `initialize`, in `set_exchange_rate` **and** at the point of use,
because zero would brick funding permanently.

**Evidence (offline).** The wiring drive pins the expression; the suite drive
asserts the suite moves the rate through governance in BOTH directions and
re-asserts an amount that the program ACCEPTED as REFUSED after the change
(writing a value and reading it back would only prove it is writable); the
harness's S5 computes its boundary from the live rate, and S9 restores par
because leaves-it-where-it-landed would move the bound under later scenarios.

### F3 — `UpgradeProgram` upgrades nothing

The arm emits `ProgramUpgradeRequested { target_program, new_program_id }` and
sets `executed = true`; no CPI to the upgradeable loader exists, and no
instruction transfers program upgrade authority. The deployer wallet can still
replace all three programs at will (runbook §10.1 says so, but the enum's name
suggests otherwise). **Fix:** rename to `RequestProgramUpgrade`, or implement
the handover (authority → governance PDA) and require a proposal for it.

### F4 — the governance split check uses a bare `+`

`execute_action`'s `SetSplitRatio` arm validates with
`require!(reserve_bps + jackpot_bps == 10_000, …)` — not `checked_add`. It is
**safe today for two reasons**: the workspace pins
`[profile.release] overflow-checks = true` (so an overflow aborts instead of
wrapping), and the treasury re-validates with `checked_add`. Both are load-bearing
and neither is stated at the call site; a refactor of either turns a wrapped sum
into an accepted split. Also note the abort is a generic "program failed to
complete" rather than the named `InvalidSplitRatio`. **Fix:** use
`checked_add(...) == Some(10_000)`.

### F5 — one parameter change per cluster, ever — **FIXED**

**What it was.** The proposal PDA was `["proposal", governance_state]` with no
index or nonce and `init`: governance could execute **exactly one** action, after
which the PDA exists forever and `propose_action` can never `init` again. A
second parameter change would have needed new program ids and fresh state.

**What it is now.** `GovernanceState.proposal_count` is the sequence;
`propose_action(proposal_id, action)` requires `proposal_id == proposal_count`
(`ProposalIdOutOfSequence` otherwise), increments it, and all three instructions
derive the same three-seed PDA — `["proposal", governance_state,
proposal_id.to_le_bytes()]`. The id is stored on the account and re-checked in
both `approve_action` and `execute_action` (`ProposalIdMismatch`), so the id an
instruction names and the account it acts on cannot disagree. The account is
sized from `MAX_ACTION_LEN` (the largest variant) rather than a round number, so
a future variant that outgrows it is a constant to bump, not a runtime "account
data too small". An id can therefore be used once and never reused; the state
can hold as many proposals as it makes.

**Evidence (offline).** Wiring drive 12.16 (chain) and 12.17 (six mutations,
including the id dropped from the seeds and the counter never incremented); the
harness derives every proposal address from the chain's own count and asserts a
wrong id is refused; S9 uses one id and then ANOTHER for the restore, which is
the two-proposals-per-state case that was impossible before.

### F6 — the mint script's self-airdrop is dead on devnet

`create_xmrj_mint.js` tops itself up with `connection.requestAirdrop` when its
balance is below 0.02 SOL. That method now returns `410 The RPC faucet is
closed on this cluster`, so §6 will log `airdrop of X SOL failed: …` and then
fail on the first fee. **Fix:** fail fast with the faucet URL instead of
attempting the airdrop, and update runbook §6 to require a pre-funded wallet.

### F7 — smaller things worth one line each

* `xmrj_devnet_token.json` is not gitignored (runbook §8 says "either do not
  stage it, or add a rule"). Add `/xmrj_devnet_token.json` to `.gitignore`.
* The runbook's header said "`cargo`, `rustc`, `anchor` and `solana` are absent
  from this box … nothing has ever been compiled". Both halves were true when
  written and are false now; the header is updated to say COMPILED, NOT
  DEPLOYED so the next operator does not skip §1.
* `tests/` contains a second suite (`xmrj_gate_negative.ts`) plus
  `negative_gate_runner.py`; `npm test`'s glob runs **both**. That is fine, but
  the runbook's §5 only describes one of them.
* No instruction closes any account; every state PDA is `init`-once and the
  programs have no `close`. §9 says this; the practical consequence is that a
  devnet rehearsal permanently consumes three program ids and five state PDAs
  per iteration.

---

## 8. Recommendations, in the order that unblocks the most

1. **Fund the wallet** (a human, one visit): <https://faucet.solana.com> →
   `7YhEG31B7vyDp42qv31UW3TLukA4nhGJ89nwnQW12Sb5` → 3.1 SOL. Then
   `CLUSTER=devnet bash tools/xmrj_devnet_run.sh --deploy --e2e --stress`.
2. **Then prove the payout on a chain, and treat that as the acceptance
   criterion** — not "the instruction compiles". A funded jackpot must be paid
   through propose → approve → execute with no privileged off-path signer and no
   manual intervention. S10 and the suite's §3.9 assert exactly that; today both
   are `NOT_EXECUTED`. Until one of them is green on a real cluster, F1 is
   *implemented*, and this report stays NO-GO.
3. **Treat the exchange rate as live policy now that it is read.** It moves the
   funding ceiling, so the off-chain conversion service must be configured with
   the same bps the state holds — two different values is a treasury forwarding
   more (or less) XMR than the recorded XMRJ is worth. A rate change is a
   proposal, and the ledger of record is `exchange_rate_bps` on chain.
4. **Add a local-cluster path that works on this host** — WSL2 + the Linux
   Agave release, or a Linux CI runner. Everything except the devnet-specific
   genesis guard would then be testable without a faucet, S10 included.
5. **Run the stress suite on the freshly funded devnet once**, keep the JSON as
   the baseline, and re-run it after each program change (it is `npm run
   loadtest`, no build step). Re-running is now safe for the governance
   scenarios too: each takes the next id from the chain (F5), and S9 restores
   the rate it changed (F2).
6. **Fix F4 and F6's failure mode** (two small edits), add the gitignore rule,
   and refresh the runbook header.
7. **Replace `AccountInfo<'info>` with `UncheckedAccount<'info>`** in the five
   flagged treasury sites (Anchor 1.1.2 deprecation; behaviour-preserving).
8. **Demonstrate binary reproducibility** (`cargo clean` twice, compare `.so`
   hashes) before quoting a build hash anywhere.

---

## 9. Files this pass produced

| path | what it is |
| --- | --- |
| [tools/xmrj_loadtest.ts](tools/xmrj_loadtest.ts) | the S0–S10 stress/load harness (offline mode included; S10 is the governance → payout path) |
| [tools/xmrj_devnet_run.sh](tools/xmrj_devnet_run.sh) | runbook executor: every step's command + stdout/stderr + exit status into one log |
| [tools/rpc_resilience.ts](tools/rpc_resilience.ts) | the one RPC layer both clients build their provider from: endpoint from the environment, 429/5xx/network retry with `Retry-After`, a 180 s confirmation window, and counters (§11.11) |
| [tests/xmrj_rpc_resilience.ts](tests/xmrj_rpc_resilience.ts) | nine tests for that layer — stub `fetch`, injected clock, no chain (§11.11) |
| [test_xmrj_rpc_endpoint.py](test_xmrj_rpc_endpoint.py) | the endpoint contract: `XMRJ_RPC_URL`→`ANCHOR_PROVIDER_URL`→nothing, both clients built from the layer, no cluster literal in a constructor, the executor wiring, and the CI gate itself — 21 checks, red-first (§11.11) |
| [test_xmrj_offline_gates.py](test_xmrj_offline_gates.py) | the offline-gate boundary: the build-dependent drives refuse an unbuilt checkout, the host path still runs them, and CI runs the halves a clone can run, plus the build job's artifact checks — 13 checks, 10 mutations, red-first (§11.13, §11.14) |
| `reports/probe_throttle.mjs` | the probe that makes the public endpoint answer 429 on demand, so a run can be aimed at a *throttled* RPC on purpose (§11.11) |
| [test_xmrj_build_artifacts.py](test_xmrj_build_artifacts.py) | artifact ↔ IDL ↔ harness drive (11 checks, rent budget from real sizes; runs host-side and, in placeholder mode, in CI — §11.14) |
| `reports/xmrj_ci_build_measure.log` | the CI build job's phase timings, payload sizes, and the stale-rlib failure the measurement found (§11.14) |
| `tsconfig.json`, `package.json` | `tools/` is typechecked with `tests/`; `npm run loadtest`, `npm run loadtest:offline` |
| `reports/xmrj_build_log.txt` | the clean build, verbatim (995 lines) |
| `reports/xmrj_offline_drives.log` | all four offline drives, verbatim |
| `reports/xmrj_runbook_devnet_20261027T142829Z.log` | the runbook's read-only rehearsal (9 steps, all exit 0) |
| `reports/xmrj_local_validator*.log` | the three local-validator attempts and their exact failures |
| `reports/xmrj_loadtest_offline_*.json` | derived addresses, planned scenarios, `transactions_sent: 0` |

Nothing is staged or committed. `target/` is gitignored; the keypair backup taken
before `cargo clean` lives outside the repo at
`~/xmrj-backup-20261027T081228/` (the program ids are only recoverable from it
or from `Anchor.toml` + `declare_id!`).

---

## 10. Bottom line

*This section is the pre-funding conclusion, kept for the record. The run it asks
for happened — see §11 for what it produced.*

The build is real, reproducible-by-hand, and green against all 90 offline
checks (58 + 10 + 22); the artifacts, the IDLs, the manifest and the plan agree, and the money
path is complete in code — a funded jackpot can be paid, by governance and by
nothing else (F1), a state can hold more than one proposal (F5), and the
exchange rate the treasury advertises is the rate its funding bound uses (F2).

Go/no-go for mainnet was **NO-GO** pending one green funded run. That run is
now in §11.

---

## 11. The live run — 2026-10-27

Everything above describes the state BEFORE the deployer was funded. This is
what happened after, and it is the part that turned design into evidence.

### 11.1 Funding

Two finalized faucet deposits took the deployer from 0 to **10 SOL**
(`getSignaturesForAddress` went from an empty list to two entries, at slots
515 791 579 and 515 791 641, both `err: null`). Before that the wallet had **no
history on any cluster**: `getAccountInfo` returned `null` and
`getSignaturesForAddress` `[]` on devnet, testnet AND mainnet-beta — so this was
never a wrong-cluster funding problem, and the devnet genesis hash the RPC
reported (`EtWTRABZ…`) was the value the suite's guard compares against.

Every programmatic faucet was closed: `api.devnet.solana.com` → HTTP 410 "The
RPC faucet is closed on this cluster" (at 0.1, 0.5, 1 and 5 SOL alike — a 410 is
not a rate limit); `faucet.solana.com/api/request` → 400 "GitHub authentication
is required"; `solfaucet` unreachable, `faucet.triangleplatform.com` 503,
Ankr/Helius keyed. The captcha-and-GitHub web faucet is the only route, and it
needs a human — which is what made this the one step no agent could perform.

### 11.2 The deployment

```
xmrj_treasury    GGPyRTZB73reGyjANw9rdEFX8g26MmkKyAEpDqNQ2468  195 912 bytes  authority 7YhEG31B…Sb5
xmrj_jackpot     6oHq39qTPVtXEiKPLt1mmxfRwRy3hucd3Qx5SJXdYfox  182 112 bytes  authority 7YhEG31B…Sb5
xmrj_governance  AVbKyQ8KZa8hTHQwwSJTRGQsVQxVHzTmzmohVCH5CLe  232 368 bytes  authority 7YhEG31B…Sb5
```

`anchor deploy --provider.cluster devnet` **cannot complete on this host**: after
each program's upload it attempts the IDL upload and dies with `Error: program
not found`, aborting with 1 of 3 programs deployed — and the first one really is
on chain, which is what makes the failure confusing. `anchor program deploy
--program-name <p> --no-idl` is the same deploy without that step; the runbook
now documents it and `tools/xmrj_devnet_run.sh` uses it.

### 11.3 Finding F8 — the ProgramData address the programs derived did not exist (CRITICAL, fixed)

The first live suite run died at its first `initialize`:

```
Error: AnchorError thrown in src\lib.rs:407. Error Code: NotProgramData. Error Number: 6004.
Error Message: The program_data account is not this program's ProgramData account.
Program log: Left: 11111111111111111111111111111111
```

Read straight off the chain, the loader derives a program's ProgramData account
as **`[<program id>]` alone** under `BPFLoaderUpgradeab1e…`. Every file in the
repo prepended a `"programdata"` literal:

| derivation | treasury address |
| --- | --- |
| the chain's own pointer (bytes 4..36 of the program account) | `CRANd8V3PhnZd9gs46qHMHQGvLoeJgQS5XAZaqmd4wNo` |
| `PDA([program_id])` — the runtime's rule | `CRANd8V3PhnZd9gs46qHMHQGvLoeJgQS5XAZaqmd4wNo` |
| `PDA(["programdata", program_id])` — what every file used | `5ALdAqdhEmXK8gDjj3JsT84Cs1QVHy98Rd3bHW74D93L` |

Nothing exists at the third address, so `require_upgrade_authority`'s owner check
saw the System program and refused with 6004. **No `initialize` could ever have
succeeded on any cluster.** Fixed in all three crates (`seeds =
[crate::ID.as_ref()]`, no prefix constant), in the suite, in
`tests/xmrj_gate_negative.ts` and in the harness; the three Python guards were
re-pointed at the runtime rule, and a RED-FIRST mutation now puts the prefix
back and must trip `program_data_seed`. Eleven offline drives were green on a
derivation that cannot work — this is the whole value of the live run.

### 11.4 The graded suite — 23 / 23

```
PASS  5.2 the devnet suite ran to completion and every step passed
RESULT: 23 passed, 0 failed
```

The suite created both states, ran three governance proposals (a parameter
change, the payout, a restore) and asserted every refusal control. The payout it
executed is on chain, decoded from the state it wrote:

```
jackpot_state  2i1MLDGMkjbRzhMMsv7ZvWJvfMcEHn8znBHqMJTCiqxA   (owner = xmrj_jackpot)
  jackpot_balance_xmr 500000   total_payouts_xmr 100000   total_funded_xmr 600000
  last_winner 8GitgY1Gp4pjuZryvYoBRW8igN9jDTFQoEkPjg72dsbL
  500000 + 100000 == 600000   ✓ the accounting identity closes
xmrj_treasury_state BWgffBA4aWRfxdFUy88JuaxESszZzjSBinXEKVFMAhpB
  total_inflow 2000000  reserve 1400000  converted 600000  funded 600000
  split 7000/3000  admin_authority G7myNZ9W… (the governance PDA — not a wallet)
governance_state 9pqcRCLjbHpKpJftpkPS7TNfv1VRiqUnScZ4rPXimdQ3   owners 3, threshold 2, proposal_count 4
```

### 11.5 The stress harness — S0–S10

S10 pays a funded jackpot through governance, with its three negative controls
first. From the run's own output:

```
✔ S10 PASS  trigger_payout called DIRECTLY by the harness wallet
            refused (UnauthorizedAdmin) — the signer must be the governance authority PDA
✔ S10 PASS  trigger_payout with the governance authority PDA passed UNSIGNED
            refused (SignatureMissing) — the RIGHT address WITHOUT its signature
✔ S10 PASS  proposal 6 asks for MORE than the balance and is REFUSED at execution
            refused (InsufficientJackpotBalance)
✔ S10 PASS  proposal 7 carries TriggerPayout(770000) and executes through the CPI
            jackpot_balance_xmr 7700000 -> 6930000, total_payouts_xmr -> 870000
✔ S10 PASS  the accounting closes: 6930000 + 870000 == 7800000
✔ S10 PASS  the payout did NOT move the treasury
✔ S10 PASS  re-executing the payout proposal is REFUSED (AlreadyExecuted)
STRESS RESULT: 2 passed, 0 failed, 0 skipped      (S0 + S10, standalone re-run)
```

Across the session's runs every scenario passes: P0 and S0–S10
(`reports/xmrj_loadtest_live_2026-10-27T19-31-59-498Z.json`,
`…T20-05-15-783Z.json`).

### 11.6 Four harness defects the live run exposed (all fixed)

1. **Its identities were not the suite's.** `derivedKeypair("owner-0|1|2")`
   hashed the tag's first two characters — the same two for all three — so it
   derived ONE key three times, and none of them was the suite's. On create-once
   state that means S9/S10 can only SKIP. It now derives the suite's recipe bit
   for bit (verified: the derived owners and operational key are exactly the
   addresses the on-chain state records).
2. **S2's conservation count was unsound.** Eight *identical* instructions from
   one signer share ONE signature, and the runtime processes a signature once;
   "delta == sends that returned a signature" can never hold. It now counts
   DISTINCT signatures and re-reads each from the chain.
3. **S1 assumed 30%-per-inflow was always fundable.** It is not: the bound is
   cumulative and saturating — a rate LOWERED after earlier funding leaves the
   ceiling exhausted, and the program then refuses every amount with
   `ExceedsConvertibleShare`, exactly as `fund_jackpot_with_xmr` documents. S1
   now computes the live ceiling from the treasury's own formula.
4. **S8 could not report its own failure.** A provider send that fails is
   re-thrown as a `SendTransactionError` with `action` unset, whose message is
   the literal `Unknown action 'undefined'`; the real RPC error was invisible.
   S8 now sends through web3 directly, surfaces the error and its logs, and backs
   off between attempts — and the shared classifier names `RpcRateLimited` for a
   429 instead of "unclassified".

Two attempts were also aborted by `api.devnet.solana.com` returning `429 Too
Many Requests: Connection rate limits exceeded` — an environment limit on a
public endpoint, not a chain result. `devnet.rpcpool.com` mirrors the same
cluster and answers when the public one is saturated.

### 11.7 Finding F9 — the mint script had never run either (`0x6`, fixed)

```
Program log: Error: account or token already in use
Program TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA failed: custom program error: 0x6
```

The treasury tranche is the associated token account, and the ATA program's
`Create` **already initializes** the token account it materialises; the script
then called `InitializeAccount` on it again. Dropping that second call (the other
three tranches are plain system accounts and still need it) let the mint run:
`Esr6mbA5mg7WWgmXs9joeJbe2Kr32LxH188QsKp7qdQr`, 100 000 000 XMRJ split
50/25/15/10 into four accounts, then `--lock` disabled the mint authority
(one-way), and a rerun reports the existing record and changes nothing.

### 11.8 Finding F10 — the artifact the deploy uploaded was an EMPTY program

The one-pass rerun (§11.9) opened with `anchor build` exiting **0** and still
leaving an undeployable artifact behind:

```
Deploying program: xmrj_treasury
Program already exists, upgrading...
Sending upgrade transaction...
Attempt 1 failed: Failed to upgrade program: RPC response error -32002:
  Transaction simulation failed: Error processing Instruction 0: invalid account data
  Program BPFLoaderUpgradeab1e11111111111111111111111 invoke [1]
  Entrypoint out of bounds
  ...attempt 2 of 3, attempt 3 of 3, identical...
Deploy failed after 3 attempts.
Partial buffer: BKaUf7oXqtFha9NF7nibo5ztWBATyJW1DK2z4o6pywTc
```

Read from disk rather than inferred from the message, the artifact was:

```
target/deploy/xmrj_treasury.so                          896 bytes
target/sbpf-solana-solana/release/xmrj_treasury.so     4840 bytes  e_entry=0x0     .text=8
target/sbpf-solana-solana/release/xmrj_jackpot.so    247608 bytes  e_entry=0x7208  .text=146344
```

`e_machine = 263` (sBPF) and a well-formed ELF header, with **`e_entry = 0x0`** —
which is precisely the loader's `Entrypoint out of bounds`. It is the
entrypoint-less build of the treasury, the one compiled as the library for the
sibling that CPIs into it, where the `cpi` feature implies `no-entrypoint`. Both
builds write the same `target/sbpf-solana-solana/release/<program>.so`, and that
is the path `anchor build` copies into `target/deploy/`. Every program in this
workspace is built twice this way; on this pass the treasury's artifact was the
entrypoint-less one, which is why two programs upgraded normally in the same
step and one did not.

**The related trap, found while pinning it down.** `cargo build-sbf` also writes
`target/sbpf-solana-solana/release/deps/libxmrj_jackpot.rlib` — **UNHASHED** —
for the crate it builds as the primary package. A later build of a crate that
depends on that program picks that path up as the artifact of the *dependency*
unit whatever feature set it was built with (cargo reports the dependency
`Fresh`) and then cannot compile it:

```
error[E0433]: failed to resolve: could not find `cpi` in `xmrj_jackpot`
error: the `#[global_allocator]` in this crate conflicts with global allocator in: xmrj_jackpot
```

Deleting the unhashed `libxmrj_*.rlib` files and rebuilding the program from its
own directory clears it — reproduced (three errors), then verified
(263 504 bytes, `e_entry=0x7678`, deploy copy 195 896 bytes).

**The fix, and what proves it.** `tools/xmrj_devnet_run.sh` §3 now clears those
unhashed rlibs, rebuilds each program from its **own** directory (a
single-package build, where nothing can turn `no-entrypoint` on) and then runs
the artifact drive, whose new **check 1.3** reads each `.so`'s ELF header:
`e_machine = 263`, non-zero `e_entry`, `.text` at or above 4096 bytes. The
runbook's §3 lists the same two commands, and its §4 failure playbook now names
this signature (three `Entrypoint out of bounds` failures plus `Partial buffer:`)
and says to fix §3 rather than retry the deploy, because the retry resumes the
buffer written from the same empty `.so`. The drive was run against the stub
(the empty-`.text` artifact of the F10 build is what made it red) and against
the real artifacts (11/11) — one code path, both answers.

### 11.9 The one-pass rerun — three logs, and what each failure was

The whole runbook, through the executor, in one invocation each time:

| | pass 1 | pass 2 | pass 3 |
| --- | --- | --- | --- |
| command | `--build --deploy --e2e --stress --mint` | `--deploy --e2e --stress` | `--e2e --stress` |
| log | `…203418Z.log` (18 steps) | `…205124Z.log` (16 steps) | `…205929Z.log` (14 steps) |
| §0.1 preflight (5 tools, node, git) | PASS | PASS | PASS |
| §3 `anchor build` | PASS rc 0 — **and it left the F10 stub** | omitted (`--build` not passed) | omitted |
| §3 artifact gate (clear unhashed rlibs → rebuild each program alone → 11/11 drive) | not yet in the executor | **PASS** | **PASS** |
| §2 wallet + rent budget | PASS | PASS | PASS |
| §4 deploy | **FAIL 1/3** — F10, three attempts, partial buffer | **PASS 3/3** | omitted |
| §4 verify (`program show`) | PASS (stale programs) | PASS — slots 515819245 / 515819328 / 515819410 | omitted |
| §5 suite | FAIL — mocha aborted on 429 | FAIL — mocha aborted on 429 | FAIL — first read 429 |
| stress harness | FAIL — hard 429 after S0 | FAIL — hard 429 at S1 | FAIL — hard 429 after S0 |
| §6 mint + rerun + §7 lock + verify | PASS | omitted | omitted |
| §8 git hygiene | PASS | PASS | PASS |

**Pass 1's deploy failure was real** — that is F10 (§11.8), and it is the reason
the artifact gate exists now. **Everything else that failed failed on the same
thing**: `429 Too Many Requests` from `api.devnet.solana.com`. In pass 3 the
suite's *first* RPC read is what fails, in its own words:

```
1) XMRJ devnet end-to-end
   "before all" hook for "0. runs against devnet and nowhere else":
   Error: failed to get info about account DaWs2s7kLo2QvDf7VkfZiadumTLyBDRzsj1yKXz1riGx:
   Error: 429 Too Many Requests: {"jsonrpc":"2.0","error":{"code": 429,
   "message":"Connection rate limits exceeded"}}
     at Connection.getAccountInfo (web3.js/src/connection.ts:3659:13)
0 passing, 1 pending, 1 failing
```

`DaWs2s7k…` is the jackpot's ProgramData account, i.e. the suite did not fail an
assertion — it could not read an account. The harness gets further because it
retries: P0 and S0 pass in every pass (S0 re-reading the live state: split
7000/3000, jackpot balance 16 167 000, the three governance owners), and then a
hard `429` ends it. **Pointing the same suite at `devnet.rpcpool.com` — the
mirror §11.6 names — does not help any more either: same 429** (the request
budget for this host is spent for the window, on both endpoints).

So these three passes neither confirm nor contradict the code: their two
failures are the endpoint, and both halves were green on this same cluster
earlier in the day, before the window filled (§11.4 **23/23**, §11.5 **P0 and
S0–S10**). What passes 2 and 3 *did* establish on chain is the F10 fix: pass 2
re-upgraded all three programs with each built from its own directory —
`xmrj_treasury` to the real program (`CRANd8V3…`, slot 515819245), with
`xmrj_jackpot` (`515819328`) and `xmrj_governance` (`515819410`) alongside it —
and the harness's P0 re-read 3/3 executable with this wallet as authority.

Pass 2 also dealt with what pass 1 left behind: `solana program show --buffers`
listed the partial buffer `BKaUf7oXqtF…` (0.0054 SOL), and it was closed, with the
rent returned to the wallet. The vault's balance is now 4.62 SOL.

**Then the endpoint cleared, and the polling paid for itself.** Nine more
attempts, each one getting further, with a probe (`getSlot` + six account reads)
standing in front of the ones that mattered:

| when | what ran | outcome |
| --- | --- | --- |
| 21:27Z | `--e2e --stress` | suite failed inside the rate-halving proposal; harness P0, S0, **S1** then 429 |
| 21:39Z | `--e2e` | **the suite ran to completion: `7 passing, 2 failing`** — two real `AssertionError`s, no 429 in them → F11 (§11.10) |
| 21:46Z | `--e2e` (after fixing F11) | **`8 passing, 1 failing`** — the failure is `TransactionExpiredTimeoutError: Transaction was not confirmed in 30.00 seconds`, raised while proposing the rate-halving action, with 429s filling the log |
| **21:58Z** | `--e2e` | **THE SUITE IS GREEN: `RESULT: 23 passed, 0 failed`**, mocha's own report `9 passing (34s) / 1 pending` — and green on state that earlier runs had already mutated, which is the property F11 was about (§11.10) |
| 22:06Z | `--stress` | harness P0, S0, **S1** green, then the hard 429 again: `Connection rate limits exceeded` |
| **22:11Z** | the harness against `devnet.rpcpool.com` | **THE HARNESS IS GREEN: `STRESS RESULT: 12 passed, 0 failed, 0 skipped`** — P0 and S0–S10, with S9 moving the rate through governance and S10 paying 6 276 700 out of the jackpot through proposal 41, accounting closing, replay refused |

That progression is the whole story of this section: the rate limit was not only
blocking the run, it was **hiding two real assertion failures** behind a wall of
retry notices — which is why the suite's full output is now kept (§11.10), and
why the harness's green run is the one through the mirror that §11.6 names.

Both halves are therefore green on this cluster, on state that has been mutated
by a dozen runs: the suite on the public endpoint, the harness on the mirror.
The public endpoint alone cannot carry the whole harness at this hour — it
survives the suite's ~35 s of traffic and dies partway through S2's — and that
is a limit of a free public RPC, not of the programs.

**Superseded at 22:41Z.** That last sentence is the finding F12 is about: the
endpoint was the limit because nothing in either client was retrying past
web3's own five attempts. With the shared retry layer in place, the whole
runbook ran green **on the public endpoint inside a 429 window** — §11.11.

### 11.10 Finding F11 — the suite passed 23/23 once, and could not pass again

Once the endpoint cleared, mocha ran to completion and its own report — not a
tail of retries — said:

```
  7 passing (25s)
  1 pending
  2 failing

  1) 2. both state accounts exist with the initialized shape:
      AssertionError: expected 21967000 to equal +0     (tests\xmrj_devnet_e2e.ts:695)

  2) 4. THE CPI CHANGED THE PARAMETER ON CHAIN (and could not have failed silently):
      AssertionError: expected 4000 to equal 10000      (tests\xmrj_devnet_e2e.ts:732)
```

Neither is a chain defect, and neither is a 429. Both assertions read **mutable
state as if it were the shape `initialize` had just written**:

* `expect(jackpotBefore.jackpotBalanceXmr).to.equal(0)` — the first run funds the
  jackpot before this point, so `0` held only on virgin state.
* `expect(before).to.equal(INITIAL_MAX_PAYOUT_BPS)` in test 4 — the cap is moved
  to 4 000 by proposal 0, which carries a **fixed id and executes once** (§3.5),
  so on a re-run the chain already holds 4 000 and the "before" value is the
  proposed one.

That is exactly the failure mode the suite's own §3.5 names ("a devnet test that
fails on its second run teaches nothing") and the one F5 was about — it had been
fixed for account *creation* and proposal *ids*, not for these two assertions.
The earlier `23 / 23` (§11.4) was a first run against virgin state; every run
after it was red, and the 429s had been hiding it.

**Fix, with the proof kept.** Both now assert what survives a re-run and still
cannot pass vacuously:

* the cap on chain is one of the two values this suite can account for
  (`initialize`'s 10 000 or the proposal's 4 000), and
* `after == PROPOSED_MAX_PAYOUT_BPS` remains the assertion only a real CPI can
  satisfy — a no-op execution would leave the cap where it was.

The eighteen red-first mutations still trip the same predicate (§4.1 stays
green: 22/22 offline after the change). The driver also keeps mocha's **full**
output now (`reports/xmrj_suite_mocha_<rc>.log`) and reports the first line that
names a problem instead of the last 25 lines of retry noise — without that, this
finding would have stayed invisible behind the 429s.

After the fix the same suite reported `8 passing, 1 failing`, that one failure
being `TransactionExpiredTimeoutError` while *proposing* the rate-halving action
— a transaction the throttled endpoint did not confirm inside 30 s. On the next
free window (§11.9, 21:58Z) it reported **`RESULT: 23 passed, 0 failed`**, mocha
`9 passing / 1 pending`: the suite now re-runs, which is exactly what F11 was
about. The stress harness then ran green too — `12 passed, 0 failed, 0 skipped`
— through `devnet.rpcpool.com`, because the public endpoint's budget for this
host cannot carry it end to end at this hour.

### 11.11 Finding F12 — the endpoint's rate limit was treated as a chain result, and is now an input

§11.9's passes 1–3 did not fail a program: they failed `429 Too Many Requests`
from `api.devnet.solana.com`. That was never a defect in the programs — but it
was also never *handled*, and it decided the outcome of the run. Read out of the
installed `@solana/web3.js` 1.99 rather than guessed, there are two separate
limits behind the two messages those passes produced:

* `Connection.callServer` retries an HTTP 429 **five times** — 500, 1000, 2000,
  4000, 8000 ms — and then throws the 429 as a plain `Error`. Fifteen seconds is
  shorter than the window a free endpoint enforces on a host that has just run a
  load harness, so the built-in retry ran out and the run died with the wall of
  "Retrying after … delay" lines that fills those transcripts.
* `confirmTransaction` gives up after `_confirmTransactionInitialTimeout` (60 s
  by default, **30 s** on the legacy path this repo hits). The 21:46Z pass died
  with `TransactionExpiredTimeoutError: Transaction was not confirmed in 30.00
  seconds` *while proposing an action* — a transaction that may well have been
  landing, since the whole cluster was being throttled, not the program.

**Fix: one layer, both clients.** [tools/rpc_resilience.ts](tools/rpc_resilience.ts)
is now the only place that builds a provider or a connection: the suite calls
`makeProvider()` (§5 of the runbook) and the harness calls `makeConnection()`,
so there is one behavior to reason about rather than two:

* **the endpoint comes from the environment** — `XMRJ_RPC_URL`, then
  `ANCHOR_PROVIDER_URL`, then nothing: no cluster is ever guessed (the rule the
  harness already enforced and the suite now shares). `tools/xmrj_devnet_run.sh`
  passes the same URL to every child, so one variable moves the whole run.
* **`resilientFetch` is installed as the Connection's own `fetch`**, so web3's
  five-attempt loop never sees a 429 it would give up on. It retries *only*
  transport failures — HTTP 429, HTTP 5xx, and network errors — with exponential
  backoff (±20 % jitter, capped at 30 s), **honouring `Retry-After`** (a number
  of seconds or an HTTP date), bounded twice over (12 attempts *and* a 180 s
  wall-clock budget). At the bound it hands the last 429 back to the caller, so
  the error the operator sees is still the endpoint's own.
* **program errors are never retried.** A program refusal arrives as a JSON-RPC
  `error` inside an HTTP 200, which the layer returns on the first attempt — a
  defect must still fail loudly and fast, and so must a 400/403/404, which is
  not a busy endpoint either.
* **the confirmation window is `XMRJ_CONFIRM_TIMEOUT_MS`** (default 180 s), so a
  confirmation that is being throttled no longer throws at 30 s.
* **every retry is logged as it happens, and counted** (`rpcStats()`), printed
  at the end of both runs: a transcript should show the throttling being
  *survived*, not quietly hidden.

**Nine tests, no chain, no network**: [tests/xmrj_rpc_resilience.ts](tests/xmrj_rpc_resilience.ts)
drives the layer with a stub `fetch` and an injected clock, and asserts the
things above: a 429 is waited out and the caller gets the eventual success;
`Retry-After` is honoured in both formats and capped at the ceiling; an endpoint
that never recovers gets the honest 429 back instead of a hang; a JSON-RPC error
inside a 200 and a 400 are returned at once, unretried; 5xx and network errors
are retried, and the original error is rethrown when they persist; the wall-clock
budget stops the loop even with attempts left to spend; the endpoint and the
confirmation window come from the environment and are never guessed; and a real
web3.js `Connection` over the stub fetch returns a slot — i.e. the layer is
where web3 actually calls it.

**And a gate, so this cannot rot.** Neither the behaviour nor the wiring is
checked by a live devnet run any more: `npm run test:rpc` runs the nine tests,
and [test_xmrj_rpc_endpoint.py](test_xmrj_rpc_endpoint.py) is a read-only
contract drive over the source and the wiring — the layer exports what the
clients use and retries 429/5xx only; the endpoint is `XMRJ_RPC_URL` then
`ANCHOR_PROVIDER_URL` with **no** default cluster; both clients build from the
layer and pass no cluster hostname to a constructor (the harness's deliberate
loopback dead endpoint stays legal); the runbook executor resolves one endpoint
and hands it to both client steps; the loop is bounded twice over; and the CI
workflow runs both. Its own section 7 is red-first: fifteen mutations each break
one property in a **copy** of the tree and must trip the check that owns it,
with an unmutated copy as the control — `RESULT: 21 passed, 0 failed`. CI's
`xmrj-rpc-contract` job runs `npm ci` → `npm run typecheck` → `npm run test:rpc`
→ this drive, and the pipeline gate waits on it, so a regression here fails a
push instead of a live run.

**The proof, against the throttled endpoint.** One runbook invocation, `--e2e
--stress`, pointed at `https://api.devnet.solana.com` **while it was answering
429**, with a probe keeping the window open:

| artifact | what it shows |
| --- | --- |
| `reports/throttle_probe_20261027T223923Z.txt` | 240 `getSlot` calls, **63 answered 429** (the endpoint is throttling this host) |
| `reports/throttle_pressure_20261027T224121Z.txt` | the same probe every 30 s from 22:41:21Z to 22:55Z, i.e. **through both client runs** — e.g. `120 requests, 87 answered 429` |
| `reports/xmrj_runbook_devnet_20261027T224147Z.log` | **14 steps, every one `[exit 0]`** |
| `reports/xmrj_suite_mocha_0_rc.log` | the suite's own report: **`18 passing (2m)`, 1 pending, 0 failing**; and `[rpc] 110 RPC requests, 4 retried (3 throttled, 0 server-side, 1 network), 0 given up on` |
| same log | the throttle being honoured, in the layer's own words: `[rpc] api.devnet.solana.com HTTP 429 — retry 2/12 in 10000ms (Retry-After asked 10000ms)` (×3) and `[rpc] api.devnet.solana.com network error: fetch failed — retry 2/12 in 408ms` |
| `reports/xmrj_loadtest_live_2026-10-27T22-52-25-226Z.json` | `rpc_url: https://api.devnet.solana.com`, **12 scenarios, 12 `PASS`**, and `[rpc] 349 RPC requests, 15 retried (14 throttled, 0 server-side, 1 network), 0 given up on` |

Both halves are therefore green **on the public endpoint inside a 429 window** —
which §11.9 said the endpoint could not carry — and the run is no longer a race
against a free RPC's budget. The mirror is still one environment variable away
(`XMRJ_RPC_URL=https://devnet.rpcpool.com`, 12/12 at 22:13Z), but it is now an
option rather than a workaround.

What this does *not* buy: the layer is **bounded on purpose**, so a window that
outlasts 12 attempts or 180 s still fails the run (test 3 asserts exactly that,
and that is the honest outcome); the throttle still *slows* a run — the suite
reported **`18 passing (2m)`** here against **`16 passing (20s)`** on the same
endpoint at 22:26Z (`reports/xmrj_suite_mocha_2_rc.log`), with individual steps
at 4.2 s, 12.4 s, 27.5 s and 36.4 s; and the confirmation-window change is only
exercised implicitly (no `TransactionExpiredTimeoutError` in this run, against
one in the 21:46Z pass) — no test asserts the number itself.

### 11.12 Finding F13 — the suite's funding step assumed a treasury nobody else had touched

With the throttle no longer ending the run first, the 22:26Z pass reached mocha's
own report and failed on something else — and the transcript says what:

```
    ✔ 5. the CPI is the ONLY path: a direct call from the deployer is rejected (339ms)
    1) 6. the jackpot is fundable only BY THE TREASURY PROGRAM
       Error: AnchorError thrown in xmrj_treasury\src\lib.rs:346. Error Code:
       ExceedsConvertibleShare. Error Message: Amount exceeds the convertible share
       the treasury has recorded.        at tests\xmrj_devnet_e2e.ts:819
    2) 8. the exchange rate is a LIVE bound on funding, not a dead field
       AssertionError: expected -32100000 to be at least 1    (tests\xmrj_devnet_e2e.ts:1100)
[rpc] 57 RPC requests, 1 retried (1 throttled, 0 server-side, 0 network), 0 given up on
```

`1 retried of 57` and no 429 in either failure: this was **not** the endpoint.
The treasury's recorded convertible share was **already spent** — `33 300 000`
convertible at par against `65 400 000` already forwarded, so every amount is
refused (`ExceedsConvertibleShare`, the program's own bound, behaving exactly as
designed) and step 8's "there is headroom left" precondition is negative.

That headroom is **cumulative state on a shared cluster**, and this suite is not
its only writer: [tools/xmrj_loadtest.ts](tools/xmrj_loadtest.ts) records inflow
and funds against the same `treasury_state`, and its rate scenario restores the
rate to par — which, by the program's documented rule in
`fund_jackpot_with_xmr` (the saturating subtraction §11.6's harness defects were
about), **lowers the ceiling below what an earlier doubled-rate forwarding put
through**. Step 6 recorded its fixed `FUNDING_INFLOW_XMRJ = 1 000 000` and
assumed that was enough. On a virgin devnet it is exactly enough (§11.4's
`23/23`); after a dozen runs of both clients it is not.

**Fix, and it repairs the state instead of papering over it.** Step 6 now
computes the inflow from the chain: the shortfall it reads (asked twice — at the
live rate, so the funding it is about to make is accepted, and at *par*, because
step 8 puts the rate back to par before it computes its bound), plus the
`FUNDING_AMOUNT_XMR` it is about to forward, ceiling-divided by the split ratio
the program banks. `FUNDING_INFLOW_XMRJ` is now a **floor** — it stays exactly
the size of the inflow on an untouched devnet, so the offline arithmetic the
driver checks (3.6) is unchanged. The green run says what it did:

```
the treasury's convertible share was already spent (33300000 convertible at 10000
bps against 65400000 forwarded): recording 108000000 XMRJ instead of 1000000, so
this step and step 8's bound have room to be tested at all
```

Nothing was weakened: the bound is still computed from the chain, still exact to
the unit (step 8 refused `300 001`, refused `300 000` once the rate was halved to
5 000 bps, and accepted it again at 20 000 bps), and the inflow is sized so the
treasury ends the step **at parity** — the same condition a virgin devnet is in —
which is why the suite can be run again rather than only once.

### 11.13 The offline gates, and the ones a push cannot check

§11.11 and §11.12 put the RPC layer and the endpoint contract on every push. The
rest of the offline set followed, and doing it surfaced a boundary that is worth
writing down rather than papering over, because the two classes of drive fail in
completely different ways when you point CI at them.

**A clone can run** — and CI now does, in a job called `xmrj-offline-drives`
(`npm ci`, then the drives; it is in the pipeline gate's `needs`):

| drive | on a fresh checkout | what a push can break in it |
| --- | --- | --- |
| [test_anchor_wiring.py](test_anchor_wiring.py) | **56 passed, 0 failed, 2 skipped** | crates ↔ `Anchor.toml` ↔ toolchain pins ↔ the plan ↔ every client call's instruction and account names, the `.gitignore` hygiene rules, and the drive's own red-first pin mutations |
| [test_xmrj_devnet_e2e.py](test_xmrj_devnet_e2e.py) (offline half) | **22 passed** | the suite's entire contract with the programs and the graded driver's grade |
| [test_xmrj_offline_gates.py](test_xmrj_offline_gates.py) | **13 passed** | the refusals below, the executor wiring, both CI jobs' own existence, and the build step's rlib cleanup (§11.14) |

**Needs a build, and refuses to pretend otherwise.** Both of these read `target/`,
and on a checkout without it they say so and exit non-zero rather than reporting a
green fleet over an unbuilt tree:

```
$ python test_xmrj_build_artifacts.py
RESULT: cannot continue without artifacts — run `anchor build` first          [exit 1]
$ npx ts-node tools/xmrj_loadtest.ts --offline
✖ missing .../target/idl/xmrj_treasury.json — run `anchor build` first        [exit 1]
```

They cannot run untouched on a push, and the reason is not the toolchain. The
artifact-side half of the wiring drive reads
`target/deploy/<program>-keypair.json`, which **holds the private half of each
program address**: `target/` is gitignored for exactly that reason (check 6.1),
the ids every crate declares are only recoverable from those keypairs
(`KEY_MANAGEMENT_PLAN.md`, and the backup kept outside the repo), so a CI runner
that generated fresh ones would produce a tree whose declared ids match nothing.

**That is no longer the end of the story for the artifact drive** (Oct 27, later
the same day): §11.14 builds the programs in CI with placeholder keys and runs
the drive on the result, which puts everything a build can prove on a push. The
IDENTITY half — is the keypair on this machine the one behind the declared id —
stays host-side, because it is a fact about this machine. Details and the
keypair/secret argument are in §11.14. `tools/xmrj_loadtest.ts --offline` is
still a host-side gate (its plan needs `target/idl/*.json`, which the build job
now provides, but the harness itself is not run there); the runbook's §3 runs
both drives, and `test_xmrj_offline_gates.py` fails if the executor ever stops
running them.

**One change was needed to the wiring drive, and it is deliberately narrow.**
Checks 4.2/4.3 now report themselves **SKIPPED** — in the transcript, in the
count, with the reason attached, never silently — when the checkout has no
`target/deploy` *at all*:

```
SKIP  4.2 every program has target/deploy/<program>-keypair.json in the 64-byte Solana shape
      (this checkout has no target/deploy — nothing has been built here)
RESULT: 56 passed, 0 failed, 2 skipped          # a fresh checkout (what CI sees)
RESULT: 58 passed, 0 failed                     # this host, where 3 programs are built
```

The skip cannot hide what the check exists for: if `target/deploy` **exists** and a
keypair is missing there, both checks still FAIL — verified by creating an empty
`target/deploy` in a copy of the tree (`RESULT: 56 passed, 2 failed`). A drive that
went green on a tree with no keypairs at all would be worse than no drive.

**Red-first, as with §11.11.** `test_xmrj_offline_gates.py`'s own section 4 breaks
ten properties in copies of the tree — the artifact drive's guard changed to exit
0, its refusal message reworded, the harness's message reworded, the executor's
invocation removed, the offline-drives job's two step names dropped, the
placeholder declaration removed from the build job, either job dropped from the
pipeline gate's `needs`, the stale-rlib cleanup removed, and avm's `--rev` pin
removed — and requires each to trip the check that owns it, with an unmutated
copy as the control. `RESULT: 13 passed, 0 failed`. Building it that way caught two of my own checks that could not fail: a
`run: python test_anchor_wiring.py` test that still matched a renamed
`...py.disabled`, and a detail string that killed the drive on a cp1252 console
instead of reporting the check that went red.

### 11.14 The build gate, and why the program keypairs do not belong in CI secrets

§11.13 left the artifact drive host-side for a reason that survives scrutiny — the
keypairs in `target/deploy` are private halves — but the conclusion did not have
to. Separating what a *build* proves from what only *this machine's keypairs*
prove gives a CI job that runs the first half on every push and says out loud
that it skipped the second.

**The job: `xmrj-build-artifacts`** (`ci.yml`, a member of the pipeline gate's
`needs`; a 15-minute timeout, which is 2.4x the worst case measured on a runner —
the second half of this section). It installs exactly what step 1 of the runbook
installs, from the same pins, and then the runbook's §3 build recipe:

| step | source of the pin |
| --- | --- |
| rustup `1.89.0` (minimal, + rustfmt/clippy) | `rust-toolchain.toml` |
| Agave `3.1.10` | **read from** `Anchor.toml` `[toolchain] solana_version` by the step itself — no second hardcoded copy to drift |
| anchor-cli `1.1.2` via `avm` (`otter-sec/anchor`) | `Anchor.toml` `anchor_version`; avm itself built from commit `85c5293e` (avm 1.2.1, the build this workspace's install used) |
| the SBF compiler | `cargo build-sbf --install-only`, i.e. the release's own default |
| toolchain cache | `~/.avm`, `~/.local/share/solana`, `~/.cache/solana` — keyed on `Anchor.toml` + `rust-toolchain.toml` |
| build cache | `~/.cargo/registry`, `~/.cargo/git`, `target/release`, `target/debug`, `target/sbpf-solana-solana` — keyed on those plus `Cargo.lock`, with `restore-keys` for a partial hit |
| **not** cached | `target/deploy`: it holds the `.so` files and the placeholder keypairs, and check 2.1 must never be able to read a `.so` that a cache carried into the job |

The build is the runbook's two commands, not `anchor build` alone: the second
loop is what keeps the entrypoint-less `.so` off the deploy path (F10) — and it
now carries the runbook's rlib cleanup, because measuring the job found that it
needs it. Then the drive runs against those artifacts. Ten of its eleven checks
execute; one skips:

| check | on a push |
| --- | --- |
| 1.1 / 1.2 the three `.so` and three IDLs exist | **enforced** |
| **1.3 every deploy artifact is a REAL program** (sBPF ELF, non-zero `e_entry`, `.text` ≥ 4096) | **enforced** — this is F10's guard, and it now fails a PR instead of a deploy |
| 2.1 each `.so` is newer than its crate's newest source | **enforced** |
| 3.1 / 3.2 `[programs.*]` agrees with every `declare_id!` | **enforced** |
| 3.3 every keypair holds the key its crate declares | **SKIPPED**, with the reason |
| 3.4 each IDL's metadata address equals `declare_id!` | **enforced** — it also holds for a placeholder build, measured (below) |
| 4.1 the IDL surface is the one the harness and the suite drive (names, argument order, account order) | **enforced** |
| 4.2 the stress harness names every instruction | **enforced** |
| 5.1 the rent budget from the real sizes | **enforced** (reported, never guessed) |

**What the job costs, measured — and the defect the measurement found.** I timed
its phases in an isolated copy of the tree (a scratch `.ci-measure/`, deleted
when the numbers were recorded), first with a fresh `CARGO_HOME` and no
`target/`, then again the way a follow-up push arrives: same caches, one source
file touched.

| phase | cold | warm (a second push) |
| --- | --- | --- |
| `anchor build --ignore-keys` (all four crates) | **146 s** | **14 s** |
| `cargo build-sbf` per program, after it | 3 s / 2 s / 5 s | 3 s |
| the artifact drive | <1 s | <1 s |
| `cargo install avm --git …` | **266 s** and 508 MB of crates | 158 s even with warm crates |
| payloads | Agave `215 MB`, platform-tools `519 MB`, anchor-cli `27 MB` | restored from cache |

**The warm run failed**, on `xmrj_treasury`, with precisely the error the
runbook's §3 bullet describes: `error[E0433]: failed to resolve: could not find
`cpi` in `xmrj_jackpot`` and `the `#[global_allocator]` in this crate conflicts
with global allocator in: xmrj_jackpot`. That is the stale UNHASHED
`lib<program>.rlib` `cargo build-sbf` leaves in `deps/`, and the first version of
the job did not clear it: a first push would have been green and every push after
it red, for a reason that has nothing to do with the change. The build step now
runs `rm -f target/sbpf-solana-solana/release/deps/libxmrj_*.rlib` before the
loop (what `tools/xmrj_devnet_run.sh` §3 already did), and the warm path becomes
14 s + 3 s + the drive, clean. `test_xmrj_offline_gates.py` now pins that line
(check 3.5) so it cannot be dropped again.

**Four changes came straight out of these numbers**, each with a cost it removes:

1. **`~/.avm` (27 MB) is cached and the install is conditional.** The compile of
   avm alone was 4.5 min — the largest single cost in the job, larger than the
   program build it exists to serve.
2. **Agave and anchor-cli installs are guarded by their own version checks**
   (`solana --version` must match the manifest pin), so a cache hit skips the
   215 MB download and the install script instead of re-running them blind.
3. **`target/release`, `target/debug` and `target/sbpf-solana-solana` (767 MB
   here) are cached**, which is the difference between 146 s and 14 s. The
   registry/git caches ride along; `target/deploy` deliberately does not.
4. **avm is built from a pinned commit** (`--rev 85c5293e`, verified to resolve:
   `Installed package `avm v1.2.1 (…?rev=85c5293e#85c5293e)``). Without it the
   toolchain cache would be unsound: a hit restores a binary a miss would have
   built from whatever HEAD is that day.

The timeout went from a guessed 40 minutes to 30 on those numbers. Then I stopped
guessing: the job ran on Actions, and the runner disagreed with the host in ways
that mattered.

**The runner, measured — six executions on a scratch branch (three runs, each
re-run once: cold, then warm).** The branch carried only this job and the files it
reads on top of
`main`, so nothing else in the tree could fail the run or pad it; it and its caches
were deleted once the numbers were recorded. `ubuntu-latest`, public repo, so the
runs bill nothing. A re-run of a completed run re-executes it against the caches the
first attempt saved, which is exactly the "every push after the first" path.

| step | cold (run 1) | warm (re-run) |
| --- | --- | --- |
| checkout + setup | 3 s | 2 s |
| cache restore: toolchain, **1269 MB** | 0 s (miss) | **20 s** |
| cache restore: crates + `target/`, **423 MB** | 0 s (miss) | **4 s** |
| rustup + Rust 1.89.0 (rustfmt, clippy) | 8 s | 11 s |
| Agave 3.1.10 (215 MB) | 14 s | 0 s (guard hit) |
| `cargo install avm` + `avm install 1.1.2` | **108 s** | 0 s (cached) |
| platform-tools v1.52 (519 MB) | 33-36 s | 0 s (cached) |
| `anchor build` + 3x `cargo build-sbf` | **131 s** | **16 s** |
| keygen + the artifact drive | <1 s | 1 s |
| cache SAVE (only when the key is new) | 15 s | 0 s |
| **job wall** | **318 s** | **57 s** |

The drive reported `RESULT: 10 passed, 0 failed, 1 skipped` on the runner — the
CI-style count this section claimed from a host simulation, now observed where it is
asserted. The warm build was green on its first attempt, so the stale-rlib failure
that the host measurement found is absent with the cleanup line in place (that run
cannot prove the line is *necessary*; the host run that failed without it does).

**What the runner changed.** The host numbers were wrong in one direction and right
in another. Right: `cargo install avm` is the largest single cost, and the two
compiles are the ones that scale. Wrong: avm took **108 s**, not 266 s — this
hardware compiles it 2.5x faster than a 14-core Windows box, which is the opposite
of the floor I predicted, and it makes the 30-minute timeout absurd rather than
generous. **15 minutes** is now the pin: 2.4x the worst cold case actually seen
(**370 s**, on a busier runner that also took the build to 153 s), so a hang still
fails fast instead of burning a default-sized budget. Cold-to-warm is 5.6x — the
1.7 GB of caches buy back about 270 s of work for 24 s of restoring.

**The one cache change I tried did not survive its own measurement.** Only
`build-sbf` writes `target/sbpf-solana-solana/release`, so `target/release` and
`target/debug` looked like dead weight in the archive and I dropped them. The
archive fell from 423 MB to **221 MB** — and the warm build step went from 16 s to
**110 s**, warm wall 57 s to 154 s. Those two directories are the host build
`anchor build` runs alongside the SBF one to produce the IDL; they were 202 MB of
the archive earning 94 s on every push. They are back, and the comment in `ci.yml`
records the experiment so the next reader does not repeat it. Two smaller facts
came out of the same runs: the crates archive is **238 MB** when it is saved by a
run whose toolchain cache hit (no `~/.cargo/git`, because avm never re-cloned),
which is harmless — the anchors that change that key also change the toolchain key,
so avm's clone is never needed from a warm crates cache — and a cache key whose
archive was saved without a path will never be re-saved while that key exists, so a
path removal needs its key changed or its cache deleted, not just a new commit.

**Should the program keypairs come from CI secrets? No — and the reason is
arithmetic, not taste.** I tried to avoid the question first: a script synthesized
`target/deploy/<program>-keypair.json` from the declared ids (32 zero bytes plus
the declared public half, the standard `solana-keygen` shape). `anchor build`
refused it:

```
Error: Unable to read keypair file (.../target/deploy/xmrj_treasury-keypair.json)
```

Anchor validates the 64-byte pair, and it is right to: **an address IS an ed25519
public key**, so a keypair file that Anchor accepts for a chosen address must
contain that address's PRIVATE half. Nothing can derive it from the public one.
That leaves two options, and only two:1. **Put the private halves in repository secrets.** Then 3.3 executes in CI.
   What that buys is narrow: confirmation that the keypair file on the runner is
   the file on the operator's disk. It does not make a deploy safer (deploying is
   not a CI action, and the upgrade authority is a different key — the wallet),
   and it costs: a copy of each program's control key in a third party, a rotation
   that means new addresses and a redeploy, and a job that must skip that check on
   every fork PR anyway, because fork PRs cannot read secrets.
2. **Generate placeholders and `--ignore-keys`** (what the job does). The
   artifacts are real — the id is compiled in from `declare_id!`, so the `.so`
   carries the declared address regardless of the keypair file — and only the
   keypair FILES are placeholders: 3.3 reports **SKIP** naming that, while 3.4
   passes because Anchor stamps the IDL's address from `declare_id!` too (a
   measurement, not an assumption — a placeholder build reports `10 passed, 0
   failed, 1 skipped`). The identity fact stays where it can be checked:
   `test_anchor_wiring.py` 4.3 on this host, where the real halves are.

Option 2 is the default because the secret would exist to serve the one check
that cannot hold in CI's position anyway. If the operator later wants 3.3 on a
push, the switch is small and reversible: add `XMRJ_*_KEYPAIR` secrets, restore
them into `target/deploy`, drop `--ignore-keys` and `XMRJ_PLACEHOLDER_KEYS`. The
drive is built for that — the flag is consulted **only** when a keypair does not
match, so a host with the real keypairs runs 3.3 whatever CI sets, and the host
run is unchanged at `11 passed, 0 failed`. Both branches were exercised on a
CI-style build over generated placeholders (never the real keypairs, which never
left `target/`): without the flag `RESULT: 10 passed, 1 failed` (exit 1); with it
`RESULT: 10 passed, 0 failed, 1 skipped` (exit 0). The skip is therefore a
property of the flag, not of a missing keypair — a mangled keypair on a
workstation still goes red.

**What this does not establish, stated plainly.** The job has now run on Actions,
but only from a scratch branch carrying this job alone: the full `ci.yml` — fourteen
jobs, the `ci` gate that `needs` all of them — has not run end to end since these
edits, so the aggregator's behaviour on a red sibling is still source-reading rather
than observation. The numbers are two cold and four warm runs on one day, so the
runner-to-runner spread is estimated from a single busier machine (370 s vs 318 s),
not characterised; if a cold run ever exceeds 15 minutes the right response is to
look at the step that grew, not to raise the ceiling. The cache *hit* path's
correctness rests on cargo's own fingerprints: a restored `target/` that is stale
for the current sources is rebuilt, which is why the drive's 2.1 freshness check is
safe to run after a restore — and the host experiment that produced the rlib
cleanup is also the evidence that a stale `target/` is not a hypothetical there.
`tools/xmrj_loadtest.ts --offline` is still host-side even though the IDLs it needs
now exist in CI: it is the harness's planner, and running it is not the same gate as
the IDL surface check that passes in CI.

### 11.15 What this still does not prove

* **The upgrade authority is still one hot key** (`7YhEG31B…Sb5`) holding all
  three programs. Governance owns the parameters, not the bytecode.
* **No off-chain conversion exists** — `xmrj_conversion_service/` is a spec, so
  "XMRJ backing" remains a design rather than a mechanism.
* **The endpoint's throttle is survived, not removed**: the harness no longer
  dies on 429 (§11.11 — `12 passed, 0 failed, 0 skipped` on the public endpoint
  inside a window), but a window longer than the layer's bound (12 attempts /
  180 s) still fails a run, and run *timings* are not comparable between a
  throttled and an unthrottled pass.
* **S3's allocation-account assertion is still a SKIP** in the harness (it points
  at §6, which has now actually run, but the harness itself does not verify it).
