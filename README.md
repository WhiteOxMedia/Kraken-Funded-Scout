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

### Standing fleet fixtures (must survive DB sweeps)

Two probe accounts live in the live `pool/jackpot.db` on purpose. The
live-server drives never touch the DB file — they go through the API
like real clients — but they re-register-or-login these accounts every
run, so deleting them makes the next fleet run fail or re-bootstrap
into the 5/hour register limiter. Database cleanups must PRESERVE:

- `security_probe` — owned by `test_security.py` (cookie-hardening
  checks ride a real login).
- `prefs_live_probe_fleet` — owned by `test_prefs_live_server.py`.
  A unique suffix per run exhausted the register limiter under
  repeated fleet runs; the fixed name registers once, logs in every
  run after, and restores its own state at the end.

The rule the completed sweeps follow: fix the fixtures, remove the
probe debris around them, and leave an audit row with the full
manifest (`users.probe_swept`; `jackpot.orphans_swept` for the FK
sweep). Pre-sweep backups land next to the DB as
`_backup_jackpot_pre_*.db`.

The discipline lives in `sweep_lib.py` and its operator face is
`sweep_db.py`: a bare `python sweep_db.py` is a READ-ONLY dry run
(it rehearses the labeled statements on a throwaway backup-API
copy, prints the per-label counts and the gate verdict, and
writes nothing), while `python sweep_db.py --apply` performs the
sweep through `sweep_lib` - backup beside the DB, the
`PRAGMA foreign_key_check` gate INSIDE the transaction (a
violation rolls the whole sweep back), and the manifest audit
row. New FK edges need a new labeled statement pair in
`sweep_db.py`; the dry run flips to `would ROLLBACK` until one is
added. Pinned by `test_sweep_cli.py`.

The planning half is `python sweep_db.py --report` (backed by
`sweep_lib.orphans()`): it reads every FK edge from the schema
itself and reports per-edge violation counts with sample rowids
and the disposition a sweep would take (delete / null /
UNPLANNED) - all through a mode=ro connection, so a report can
run against the live primary with zero write access. Its EXIT
CODE is the alert (0 all-clear, 1 violations found, 2 an edge is
UNPLANNED), so cron/alerting reacts to the receipt itself.
Pinned by `test_fk_regression` 3c and `test_sweep_cli` section 6.

Scheduled alerting: the Windows task `CodebuffWeeklyOrphanReport`
(Sundays 06:15, user-level) runs `run_sweep_report.cmd`, which
appends the receipt to `logs_sweep_report.txt` and leaves the
exit code (0/1/2 above) in Task Scheduler's Last Result -
alerting reacts to the rc without parsing the log. Re-create the
task after moving the checkout (the wrapper resolves its own
directory, but the task's command line is absolute):
`schtasks //Create //TN CodebuffWeeklyOrphanReport //TR
"'<repo>un_sweep_report.cmd'" //SC WEEKLY //D SUN //ST 06:15 //F`.

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

## Runtime operations

**The pool server on :3094.** Restart it (bash on Windows — the PID
comes from the listener, the server runs hidden and logs to a file):

```bash
taskkill //F //PID $(netstat -ano | grep ":3094" | grep LISTEN | awk '{print $5}' | head -1)
powershell -NoProfile -Command "Start-Process cmd -ArgumentList '/c set APP_PORT=3094&& .venv\\Scripts\\python.exe pool\\server.py > logs_server_restart.txt 2>&1' -WorkingDirectory 'C:\\Users\\birds\\Workspace\\monero_project' -WindowStyle Hidden"
sleep 9 && curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:3094/api/stats
```

The `curl` health check is the confirmation step — 200 means the fleet
gate's live drive will run, 000 means it will self-skip (honestly).

**Logs.** `logs/` carries `pool_stdout.log`, `pool_stderr.log`,
`server_restart.log`, and `xmrig.log`; the restart command above
tee's its own run to `logs_server_restart.txt` in the repo root. Log
files are runtime state — gitignored, safe to delete while the server
is stopped.

**Shipping a frontend change.** Static files and templates are
cache-busted by version query strings, so the workflow is:

1. edit `pool/static/*` and/or `pool/templates/index.html`;
2. bump the touched script's `?v=` in `pool/templates/index.html`
   (the served HTML must reference a version the browser has not
   seen — parity checks in the fleet are version-agnostic on
   purpose);
3. `python make_bundle.py` — regenerates the Hostinger bundle from
   the new files and runs the parity gate itself;
4. commit — the fleet gate re-proves bundle parity and the text/
   contract drives.

**Registering a live drive in the Scout battery.** When a pool drive
becomes a battery live-contract entry, copy the fill-in template
beside the Scout battery (`kraken_scout/LIVE_CONTRACT_TEMPLATE.py`) —
it encodes the whole convention: execute through the pool venv,
stream output verbatim, map the drive's honest self-skip signature
(RESULT 0/0 **plus** a `SKIP:` line) to the battery's SKIP vocabulary,
and add the wrapper to `LIVE_ENTRIES` so `--fast` stays hermetic.

## Related one-command tools

- `python setup_dev.py` — new-clone setup: venv from `uv.lock` + the
  commit-gate hook, then verifies both (idempotent).
- `python make_bundle.py` — regenerate the Hostinger review bundle from
  the live template + statics; runs the parity drive as its own gate.
