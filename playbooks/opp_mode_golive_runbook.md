# THE OPP MODE / TIDE-RETUNE GO-LIVE RUNBOOK (Oct 3)

Status of the plan that circulated as "immediate next steps": **every
code item already shipped (all committed, gated, reviewed, kill-proofed
red-first)** — this runbook records what landed, the two acceptance
checks that remain manual, the mutation-audit command in this repo's
real CLI shape, and the canary/rollback drill. Nothing here needs a
branch or a patch file.

---

## 1. The champion-opportunistic tripwire — SHIPPED (14dff04 + 8a81979)

The first `champion-opportunistic` popup ever fires a one-shot reminder
in-process (one Zira line, one popup naming the verdict command and the
KEEP-PROVISIONAL status), written into the reminders/ marker idiom:
`_opp_first_fire_reminder.done`, runtime state, mark-before-notify, the
read fails OPEN (a lost toast re-reminds — never a silently unwatched
window). Champion and champion-fast stay silent.

- Pins: `test_hailmary.py` 8.31–8.36 (8.36 is a structural AST pin on
  `send_alert` — the dict-key-is-not-a-local class dies there).
- **Acceptance still open (manual, first live fire):** the popup lands
  and `_opp_first_fire_reminder.done` is created in the repo root with
  an ISO timestamp; the spoken line is heard once. Check after the
  first opp fire: `ls _opp_first_fire_reminder.done`.
- No `data/opp_first_popup.jsonl` exists in this design — the fired
  row itself is the immutable start event: `signal_snapshots.jsonl`,
  `kind="fired"`, `route="champion-opportunistic"` (that ledger is the
  hindsight grader's input; a separate file would be a second truth).

## 2. opp_hindsight in the weekly cadence — SHIPPED (a86e429 + 720751d)

`tidebar_weekly.opp_verdict_driver_loop` runs `scratch/opp_hindsight.py`
as a **subprocess** (zero API coupling; the untracked-then-tracked tool
owns its method), parses the `>>> ` verdict + opp census, and PREPENDS
the full section to `opp_hindsight_report.txt` (newest verdict on top,
byte-exact 500 KB rotation). The scanner runs it as a daemon beside the
flip-grade driver: first read ~20 min after boot, then weekly. Fail-safe
at every door: gone grader, non-zero exit, or no verdict line writes
NOTHING — never a fake verdict. The parser allowlists exactly the four
pre-registered verdicts (KEEP-PROVISIONAL / KILL / RETAIN-TIGHTEN /
KEEP).

- Pins: `test_tidebarweekly.py` 1b.1–1b.9 (219/0).
- **Acceptance still open (manual):** the console panel (or the
  scanner log's `OPP VERDICT:` line) shows KEEP-PROVISIONAL ~20 min
  after the next scanner restart; `opp_hindsight_report.txt` carries
  the section. There is no `ops/generate_weekly_report.sh` in this
  repo — the cadence daemon replaced it by design.

## 3. CodeRabbit backfills — DONE (07c7a02 + d133381-era work)

`coderabbit review --agent --committed --base 651b16f` completed after
the rate-limit reset (651b16f..720751d, 3 findings, all closed
red-first). The `4321007`-base backfill was audited REDUNDANT: 651b16f
already received a post-commit review that necessarily covered 11b6824.
2 free review slots remain. Evidence: `scratch/cr_backfill_oct2.txt`.
No `tools/coderabbit_backfill.py` exists here — the CLI's own
`--base-commit` flag is the interface.

## 4. The Sep-30 study reproduction — DONE (7f46a50)

`scratch/pergate_calibration.py` reads the union (live + all
commit-era `_supp_*.jsonl` archives, sha256 + commit-date provenance),
states coverage (4 days for September — the study's own window rotated
away unrecoverably), carries the archived verdict as a pre-registered
claim with a reproduction check (which exposed the 17/8 label
transposition), and prices the valve AS BUILT via `--audit [month]`:
Sep 5/5 payers +$7.97 observed / +$6.82 median; Oct 3/3 +$1.79 /
+$4.82. No Python backfill of reviews reproduces a suppression study —
the ledger union reproduces the STUDY.

## 5. Full suite + mutation audit — the real commands

```bash
.venv/Scripts/python.exe -X utf8 run_battery.py          # every suite, one command
.venv/Scripts/python.exe -X utf8 scratch/bot_mutation_audit.py --drive test_apptest.py --timeout 60
```

(`bot_mutation_audit` is bot-fleet; scout-side per-module drills ride
`diag_mutation_sweep.py` at the repo root. `pytest`/`-q` is not this
repo's runner — every drive is a `test_*.py` script printing
`RESULT: N passed, M failed`.)

## 6. CANARY — the real lever order

The canary is **dial + restart**, on the LIVE posture first verified
Oct 2: no env var, `exit_extension.dial.json` = `{"EXIT_EXTENSION_ENABLED": "0"}`
(no retune key → valve OFF), probe answered `(False, 'the retune dial
is OFF')`.

1. Edit `exit_extension.dial.json`:
   `{"EXIT_EXTENSION_ENABLED": "0", "TIDE_RETUNE_ENABLED": "1", "TIDE_RETUNE_DAILY_CAP": "1"}`
2. Restart the scanner (the dial is read LIVE — no restart strictly
   needed for the retune; the restart also activates the queued code:
   pair-exact carve grants, opp verdict daemon, greeting throttle).
3. First live day: cap 1. One admission, journaled, graded by the
   fill model before a second slot is ever granted.
4. **Two-person sign-off before flipping the cap to 3.**

Monitoring (all existing surfaces, no new telemetry needed):
`popup_suppressions.jsonl` (`retune:` why-lines), the fired snapshot
row (`route="champion-opportunistic"`, `spend`, `goal_pct`, `win_min`),
`opp_hindsight_report.txt` (graded count + EV + verdict),
`tide_retune_state.json` (the day's spent slots),
`_opp_first_fire_reminder.done` (the tripwire fired).

## 7. ROLLBACK (two doors)

- **Dial door (instant, no restart):** `TIDE_RETUNE_ENABLED` → `"0"`
  (or delete the key) — the next read closes the valve mid-rotation;
  the blanket wall stands exactly as before Oct 2. The day ledger
  (`tide_retune_state.json`) persists and survives — that is its job;
  delete it only if you also want the cap refilled deliberately.
- **Code door (restart):** the valve is dial-gated with absence = OFF;
  with the key absent the shipped code is behaviorally identical to
  pre-bfb5cac on every customer path (fail-closed at every door).

## 8. Runbook notes (recorded as requested)

- **Accepted race (documented at the decision, app.py):** the boot
  greeting's marker is a throttle, not a mutex — simultaneous sessions
  may both read before either stamps; the chorus is bounded to
  simultaneous connects (~2), not every restored tab. A file lock to
  dedupe a two-voice instant is machinery the greeting does not owe.
- **Marker semantics:** `.last_boot_greet` (machine-wide 30-min voice
  throttle, per-tab silent toast unaffected, kill-switch warning always
  speaks and never spends the marker) and `_opp_first_fire_reminder.done`
  (one-shot, ever, mark-before-notify, fail-open) are both runtime
  state (gitignored + RUNTIME_INVARIANTS.md) — repo operations can
  never re-chorus or re-fire them.
- **Telemetry:** boot-greet and the kill-switch warning are already
  distinct events (different toasts, different voice lines); the
  boot-greet's marker timestamp IS the machine-wide last-greet record
  (readable from the file itself).
- **Evidence for reviewers:** `scratch/opp_hindsight_report.txt`
  (the prepended verdict sections), `scratch/gate_ev_table.txt`
  (NOTE: the committed copy still prices the old ~1-day population —
  re-run the rotation-aware tool before attaching; the union run
  prices 186 candidates / 6 stated days vs 104), and
  `scratch/cr_backfill_oct2.txt` (the review coverage proof).

## 9. Open items NOT in the plan (carried)

- stateclobber storm flake (WinError 5 rename contention) — disrupted
  four commits; de-flake red-first is the top queue item.
- Retune-ledger cross-process half: thread-half closed under
  `_TIDE_RETUNE_LOCK`; scanner/bot stays the documented single-writer
  convention + lockfile-idiom follow-up.
- Operator churn uncommitted by design: `tidebar_weekly.py` floor
  change, `scratch/gate_ev_table.txt` (worktree-modified).
