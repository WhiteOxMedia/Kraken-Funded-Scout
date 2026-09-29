# STATE-FILE RECOVERY RUNBOOK

The shared state files (`contest_watch.json`, `profit_guard_state.json`,
`scanner_state.json`, `meta_layers_state.json`) are written by multiple
processes through one contract: **merge-save + tmp + os.replace +
retry-on-named-contention** (`atomic_io.replace_atomic`). Every save
also emits structured telemetry to `artifacts/io_events.jsonl` (one
jsonl line per try: `op/target/attempt/outcome/error/elapsed_ms/ts`).

## 1. NAMED CONTENTION OBSERVED

**Symptom** — `io_events.jsonl` grows `outcome:"retried"` lines, or the
scanner log shows `contest watch save failed` / `scanner state save
failed` warnings.

```bash
# what happened, newest last
grep replace_atomic artifacts/io_events.jsonl | tail -20
# the pressure summary: retries per target over the last day
python - <<'PY'
import json, collections
c = collections.Counter()
for line in open('artifacts/io_events.jsonl', encoding='utf-8'):
    try: e = json.loads(line)
    except Exception: continue
    if e.get('outcome') in ('retried', 'exhausted'):
        c[(e['target'], e['outcome'])] += 1
for (t, o), n in c.most_common():
    print(f"{n:5d}  {o:9s}  {t}")
PY
```

**Decision rule:** occasional `retried` lines are the design working
(≤ a few per day per target on this machine's load). A rising rate or
any `exhausted` line means systemic pressure: check for a second
scanner instance (`netstat -ano | findstr :8501` plus the pid files),
a backup/sync tool holding handles on the repo, or an SMB-moved
working tree. Fix the cause; do not widen the retry budget — the
current 5×50ms-doubling (≤0.75s) is tuned to the tick cadence, and a
lost save is healed by the next tick by design.

**If a save was LOST** (an `exhausted` line on a state target):
re-run the healing tick by hand — the machine's own retry is the next
cycle; nothing else to do. For the guard state specifically, prefer
§2's replay over hand-editing.

## 2. TORN JSON OBSERVED (or the state file is missing/garbage)

**Symptom** — a reader logged a JSONDecodeError that is NOT the
documented empty-view race (file size > 0 but the body will not
parse), or a state file is simply gone.

```bash
# quarantine the evidence BEFORE anything else
cp profit_guard_state.json scratch/evidence/pg_state.$(date +%Y%m%d_%H%M%S).bak 2>/dev/null
ls -la profit_guard_state.json*
```

Do **not** hand-edit the live file. Two paths:

### 2a. The guard state — the tested replay (preferred)

```bash
# the plan first — shows the anchor factor and row count, writes nothing
uv run python -X utf8 -c "
import sys; sys.path.insert(0, '.')
from pathlib import Path
import restore_state as rs
r = rs.restore(Path('artifacts/auto_adjust/telemetry.jsonl'))
print(r)
"
# land it (merge-save + atomic through profit_guard._save)
uv run python -X utf8 -c "
import sys; sys.path.insert(0, '.')
from pathlib import Path
import restore_state as rs
r = rs.restore(Path('artifacts/auto_adjust/telemetry.jsonl'), commit=True)
print(r)
"
```

The replay is **idempotent and cannot double-apply**: the factor is
anchored at the newest applied event's `before_gf` (the pre-apply
restore target), the ledger rows land as data through
`profit_guard._save` (no `apply_adjustment` call, no multiplication),
and re-running produces the same state. `test_resilience.py` pins this
—including the mutated double-apply shape reading 0.8336 and failing.

### 2b. Other shared files — merge-restore from the clean copies

```bash
uv run python -X utf8 diag_statewriters.py        # the writer census
```

The fail-soft readers already treat a missing/garbled file as `{}` and
the writers re-merge on the next tick; for `contest_watch.json` the
recovery input is the journal (`contest_trades.jsonl` + the bot's
Postgres `positions`) — reconcile with the SOLD/GET-OUT paths rather
than reconstructing rows by hand. When in doubt: quarantine, let the
ticks rebuild, verify with the drives (`test_stateclobber.py`).

## 3. CROSS-PLATFORM SEMANTICS MATRIX

The contract was built on Windows (the production machine). The
behavioral surface on other platforms:

| behavior | Windows (NTFS, local) | Linux (ext4, local) | macOS (APFS) | NFS / SMB mounts |
|---|---|---|---|---|
| `os.replace` atomicity | atomic (MoveFileEx) | atomic (rename(2)) | atomic | **not guaranteed** — partial visibility possible |
| lost rename contention | WinError 5 (access denied) | **succeeds silently** (rename(2) replaces unconditionally) | succeeds silently | varies with the mount |
| reader holds target open | WinError 32 (sharing violation) | no error (no mandatory locks) | no error | varies |
| errno rendering | winerror 5/32 | EACCES(13)/EBUSY(16) | EACCES/EBUSY | EACCES/EAGAIN |

Consequences, stated plainly:

- The helper's retry family (`winerror 5/32`, `errno 13/32`) is
  Windows-shaped. POSIX returns `errno 13` for the access family, so
  the common cases still map; `EBUSY(16)` is NOT in the family. On a
  POSIX host, retries mostly never fire because renames don't lose —
  the helper is a no-op wrapper there, which is correct.
- **NFS/SMB: never host the working tree on a network mount.** No
  local lock or retry protocol makes a non-atomic rename atomic over
  the wire; a torn write is *observable* there by design. The fleet's
  state contract assumes a local volume.
- The stateclobber drive (the storm harness) runs the real
  `os.replace` of the host it executes on, so any portability drift
  shows up as a red drive on that platform, not as a silent divergence.

## 4. WHY NO ADVISORY LOCKS (the decision)

`fcntl.flock`/`LockFileEx` were considered for the hottest
single-owner files and rejected, deliberately:

1. **Deadlock class.** A crashed holder between lock and replace turns
   a transient 50ms retry problem into a stuck machine until reboot;
   the current contract fails soft and heals by tick.
2. **No cross-platform semantics.** `flock` over NFS is a no-op or a
   lie depending on the server; `LockFileEx` is Windows-only. The
   fleet would carry two locking truths.
3. **No reader protection.** The observed contention is
   writer-vs-writer on replace and reader-vs-swap on open; POSIX
   advisory locks don't even talk to readers that don't opt in, and
   our fail-soft readers deliberately never block.
4. **Measure first.** The retry telemetry now quantifies real
   pressure; if a target ever shows sustained `exhausted` rates after
   cause-fixing, revisit this decision WITH that data — not before.

Revisit trigger: ≥ 1 `exhausted` event per day on the same target for
a week, after the environmental causes in §1 are excluded.

## 5. CHAOS SCHEDULING (the decision)

The destructive concurrent replay is `test_stateclobber.py` — real
subprocess writer storms against sandboxed state files. It runs:

- **Every commit** touching `background_scanner.py`, `profit_guard.py`
  or the drive itself (the pre-commit gate, budget 60s) — the cheap,
  high-signal cadence.
- **Every Tuesday** by hand or watchdog, on the live machine, plus
  after ANY windows update, storage change, or Python upgrade (the
  environment-drift class): `uv run python -X utf8 test_stateclobber.py`.
- **Owner:** the operator (birds). There is no CI runner for this
  repo; the watchdog reminder (`reminders/`) is the calendar.

## 6. GATE TIME BUDGETS

The gate runs drives with per-drive budgets ~5x measured runtime
(5s–180s). The budget guard (not the OS) kills a hung drive and blocks
the commit, so worker-import slowness degrades the gate's *speed*, not
its correctness. Rules:

- Never raise a budget to accommodate a newly-slow drive — split the
  drive instead (short in-process pins vs long subprocess storms; the
  stateclobber/commitgate split is the template).
- The gate's cold total is ~2.5 minutes on the production machine
  (dominated by the commitgate sandbox drills); a laptop-class runner
  needs the long drives trimmed, not the budgets widened.
- Every drive must exit non-zero on failure — a DONE-always exit code
  is a silent gate hole (the hookparser drive guards the hook's own
  census for exactly this).

## 7. SECRETS AND EVIDENCE HANDLING

The evidence bundles under `artifacts/auto_adjust/rotation_*` contain
the burial candidates, per-gate ratios, and OHLCV excerpts — trading
signals, not credentials. Still:

- No state file, journal, or evidence bundle may contain API keys or
  account secrets; the Kraken credentials live only in the env of the
  two trading processes and are never written by any writer in the
  registry (the statewriters census would show the file; nothing
  reads an env-keyed secret into state).
- `artifacts/` and `scratch/` are git-ignored; evidence never rides a
  commit. The ledger's `evidence_checksum` is a sha256 of the bundle
  — verification without exposure.
- When quarantining per §2, keep the copies inside `scratch/` (same
  policy as the evidence itself); scrub terminal captures of any
  account identifier before pasting into tickets or postmortems.

## 8. POST-FAILURE FORENSICS

After any contention or torn-write event:

```bash
# 1. the telemetry timeline (all tries, in order)
grep -c '"outcome": "retried"' artifacts/io_events.jsonl
# 2. the writers that touched the target (the census names the suspects)
uv run python -X utf8 diag_statewriters.py
# 3. open-handle check (who held the file)
handle.exe -a <state file> 2>/dev/null || powershell -NoProfile -Command "Get-Process | Where-Object { \$_.Path -like '*python*' } | Select-Object Id, ProcessName"
# 4. the drive battery over the touched surface
uv run python -X utf8 test_stateclobber.py 2>&1 | tail -3
```

Attach the quarantined file from §2, the `io_events.jsonl` slice, and
the drive output to the postmortem (template:
`playbooks/postmortem_template.md`).

## 9. THE LINTER CONTRACT (regression prevention)

The state-writer rules are enforced per-commit:

- `diag_statewriters.py` — R1 every production writer of a shared
  state file is REGISTERED (a new writer refuses the commit until it
  joins the registry with a note), R2 every save is atomic
  (tmp + replace; one-shots need a written reason), R3 merge-writers
  never pop without a None-guard.
- It runs as `test_statewriters.py` in the pre-commit gate whenever
  `atomic_io.py`, `profit_guard.py`, `restore_state.py`,
  `diag_statewriters.py`, `meta_layers.py` or `background_scanner.py`
  is staged.

To add a writer: extend REGISTRY with the (file, writer) pair and a
note — never bypass the census.
