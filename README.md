# monero_project — XMR Daily Jackpot Pool

A Monero daily-jackpot pool: FastAPI server (`pool/server.py`, port 3094),
native push system, Kraken referral projection, and the Scout funnel
(see the sibling `kraken_scout` checkout for the trading half).

## The drive fleet

**Every `test_*.py` in the repo root is a drive.** New drives join
automatically — nothing is registered anywhere. Drives print a final
`RESULT: N passed, M failed` line (parsed for the ledger); prose-style
drives without one are graded by exit code. Every drive owns its own
sandbox (temp-dir DB, stubbed transports) — the live `pool/jackpot.db`
is never written by a drive, and external legs (wallet RPC, Kraken, the
push library) are stubbed at their boundaries so a drive is hermetic by
construction.

```
./test                          # the whole fleet (run_fleet.py underneath)
./test --only push prefs        # substring subset
./test --fail-fast              # debug loop: stop at the first regression,
                                #   print that drive's full output
./test --history                # per-drive FLAKE% / REGRESSION% over time
python run_fleet.py --list      # show the discovered drives and exit
```

### The commit gate

`githooks/pre-commit` runs the entire fleet before every commit lands
(`git config core.hooksPath githooks` — set per clone, done
automatically by `python setup_dev.py`).

- **REGRESSION blocks the commit.** A failure that reproduces on the
  rerun is real; fix the drive, or bypass once with
  `git commit --no-verify`.
- **FLAKE does not block.** A failure that passes on the rerun is
  triaged as a flake and listed loudly — instability is a warning, not
  a wall.
- **`FLEET_ARGS` subsetting:** `FLEET_ARGS="--only push prefs" git commit …`
  runs a subset at the gate (for iterating on an unrelated one-liner).
- A gate run costs ~2 minutes; give your terminal a generous timeout.

### Skip semantics (and the triage vocabulary)

A drive may skip **itself** — honestly, on purpose. The live-server
drive (`test_prefs_live_server.py`) probes `:3094` first and answers
`SKIP: …` + `RESULT: 0 passed, 0 failed` (exit 0) when the pool server
is down, so the fleet stays green on boxes without the tunnel. The
runner gives a skipped drive one cooldown retry (65s — the stats rate
limiter's window) before accepting the skip, because a fleet run can
trip a limiter it caused itself.

The runner classifies every drive into exactly one of:

| Status | Meaning |
|---|---|
| `PASS` | green on the first run |
| `SCENARIO` | exit 0, no RESULT line (prose-style drive) |
| `SKIP` | the drive skipped itself (twice, after the cooldown retry) |
| `FLAKE` | failed the first run, passed the rerun — a warning, listed |
| `REGRESSION` | failed both runs — blocks the commit |

A SKIP is **never** a FAIL and **never** silently green: it is a named
row in the ledger with a reason. The same honesty rules apply in
reverse — a drive that answered green with zero checks when it meant to
verify something is a bug (the live-contract wrapper in the Scout repo
exists precisely because of that class).

History: every classified run appends one JSON line to
`fleet_history.jsonl` (gitignored — it churns per run, including inside
commit gates). `./test --history` turns it into per-drive flake rates,
so "this drive flakes every third run" is a number, not folklore.

## How run_battery.py differs (the Scout repo)

The sibling `kraken_scout` checkout has its own one-command sweep,
`run_battery.py`, and it is **not** the same tool as `run_fleet.py`:

| | `run_fleet.py` (this repo) | `run_battery.py` (kraken_scout) |
|---|---|---|
| **Role** | the commit gate — every commit survives the drives | the scheduled whole-verification sweep (~20 min, 100+ entries) |
| **Entries** | `test_*.py` drives, auto-discovered | `test_*.py` suites **plus** `diag_*` worlds (`DIAG_RUN` names the runnable ones; the rest are listed as SKIP so nothing silently disappears) |
| **Runtime control** | per-drive timeout + rerun triage | lanes by measured time: `--fast` (terminal-safe by construction, budget-bounded), `--slow` (the long tail), plus auto-chunking and resume (`battery_resume.json`) so a window death costs one entry |
| **History** | `fleet_history.jsonl` + `--history` rates | `battery_history/` JSON records + week-over-week delta printed after each run |

The two touch exactly once, deliberately: the battery's
**live-contract entry** (`test_pool_prefs_live`) executes this repo's
live prefs drive through **this repo's venv** (it needs the pool's
fastapi/starlette; the Scout venv does not carry them). That entry never
runs in the battery's `--fast` lane — fast is hermetic by construction
and a live surface has no place in it — and when the pool server is
down, its honest self-skip surfaces as a SKIP row in the battery's
ledger ("pool server down"), the same skip semantics both repos share.

## Related one-command tools

- `python setup_dev.py` — new-clone setup: venv from `uv.lock` + the
  commit-gate hook, then verifies both (idempotent).
- `python make_bundle.py` — regenerate the Hostinger review bundle from
  the live template + statics; runs the parity drive as its own gate.
