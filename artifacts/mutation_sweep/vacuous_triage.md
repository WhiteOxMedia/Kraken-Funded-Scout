# THE FLEET VERDICT — every mutation-sweep drive adjudicated (2026-09-30)

The mutation sweep judges 163 test drives. As of the Sep-30 full fleet
battery, **every drive holds either a mutant-kill or a sealed, reasoned
untestable class — there are zero unexplained verdicts**: VACUOUS 0,
BASELINE-RED 0, UNMAPPED 0, timeout 0. No drive in this fleet is green
for an unknown reason.

This is the permanent verdict record and the per-drive reasoning home
referenced by `diag_mutation_sweep.py`'s drive-class manifest. It began
as the task-56 triage of the 52 VACUOUS verdicts (that 62-drive
point-in-time snapshot — killed 23 / VACUOUS 39 — lives in git history);
after the manifest landed and the full 162-drive battery ran, it was
rewritten to cover the whole fleet. The repair history in section 4 is
retained verbatim-in-spirit because it records *how* the fleet reached
zero unexplained, and the killed drives' pins are only as trustworthy
as the reasoning behind their repairs.

## 1. The ledger — 163 drives, nothing manufactured

| verdict | n | meaning |
|---|---|---|
| KILLED | 104 | baseline green; ≥1 behavior mutant turned the drive red |
| UNTESTABLE | 59 | sealed class: behavior mutation cannot judge it by design (section 3) |
| VACUOUS | 0 | stayed green under every mutation, no class — **none left** |
| BASELINE-RED | 0 | fails before mutation — **none left** |
| UNMAPPED | 0 | no guard mapping, no class yet — **none left** |
| TIMEOUT | 0 | exceeded its budget — **none left** |

The class mix of the 59: LIVE-MARKET 6, LIVE-STATE 15, SOURCE-TEXT 17,
STATIC-AUDIT 1, SIBLING-CHECKOUT 4, HOST-BOUND 1, STANDALONE 13
(= 57 manifest entries) + SCAFFOLD-ONLY 2 (auto-detected, no manifest
entry) = 59.

Re-derive from the journal at any time:

```bash
python -c "import json,collections;j=json.load(open('artifacts/mutation_sweep/journal.json',encoding='utf-8'));print(collections.Counter(e['verdict'] for e in j['results'].values()),len(j['results']))"
```

(`diag_mutation_sweep.py --all` regenerates the same ledger into
`mutation_sweep_report.txt`, with `<CLASS>` tags on the untestable rows.)
The pre-triage journal is preserved at `journal.pretriage.json`.

## 2. Verdict vocabulary

- **KILLED** — the only verdict that proves coverage: the drive's
  baseline is green and at least one source mutant turned it red.
- **UNTESTABLE** — an *adjudicated* skip: the drive is classed in the
  manifest (or auto-detected), the class is named in the journal entry,
  and the sweep runs **no baseline, no sandbox, no mutation** for it. A
  classed drive cannot manufacture a VACUOUS verdict.
- **VACUOUS** — stayed green under every mutation *without* a class.
  This is the failure mode the manifest exists to prevent. Zero today,
  and the machinery keeps it that way — but it stays a live verdict,
  because a *new* drive, or a classed drive that was wrongly classed,
  can still read VACUOUS. A VACUOUS verdict is a triage obligation,
  never a resting state.
- **BASELINE-RED** — fails before any mutation: pin drift or a broken
  drive. Repair, then re-judge.
- **UNMAPPED** — no guarded-module mapping and no class. **Deliberately
  kept alive as the triage queue**: the sweep still runs the drive, and
  the verdict surfaces any new unmapped drive for a human look.
  STANDALONE is the *adjudicated* verdict after that look; UNMAPPED is
  the *queue* before it. Deleting UNMAPPED would silently bury new
  drives — never do that.
- **TIMEOUT** — exceeded `DRIVE_TIMEOUT_DEFAULT` (300s; HEAVY drives get
  360–480s, section 5). Never a verdict of record: retry, then triage.

## 3. The sealed classes — the 59 UNTESTABLE

The manifest lives in `diag_mutation_sweep.py::UNTESTABLE_CLASSES` — 57
entries across six sealed classes, each carrying its reasoning. A classed
drive is skipped with an UNTESTABLE verdict and its class + reason are
written to the journal entry (`drive_class` + `detail`). Two further
properties, by construction:

- **The manifest never vacuities by omission.** Anything *not* listed
  still runs, and can still read VACUOUS — an unmapped or unclassed
  drive is a triage obligation, not a silent pass.
- **Delisting is the re-judge.** If a classed drive later grows real
  runtime pins (a mutation-sink rewrite, say), remove it from the
  manifest and the sweep will judge it again — including killing it.

### 3.1 LIVE-MARKET (6) — verdicts keyed to live ccxt prices / OHLCV

eth_gap, hype_now, kii, kii_woulda, chase, xrp_scan. The "why did X
happen" diagnostics: their pins assert relationships between live
market data and scanner output. A sandbox has no live feed; mutants
can't move the market.

### 3.2 LIVE-STATE (15) — walks of live db / keyring / account / console state

bot_day, botdb, bracket_click, dopamine, night, night_pnl, no_alerts,
one_asset, payouts, payouts_e2e, sunday, tour_state, winners,
xrp_signals, zec_trace. Live signals.db walkers, keyring reads, live
Postgres via sqlalchemy. Where guarded-module calls exist they carry no
verdict; the verdict is the live walk.

### 3.3 SOURCE-TEXT (17) — pins guarded code by reading its source as text

balancepop, carry, cooldowncheck, dcagate, entry_control, exitsynthesis,
funnelplate, goalgame, hersize, hookparser, persistexit, qclonewolf,
thinbrain, tidefloor, toggleguard, uismooth, winnerlock. The pin is
`"constant" in src` — file bytes are immune to runtime mutants *by
construction*. (Several former source-text drives *additionally* grew
runtime pins through the repairs in section 4 — herwords, trendbridge,
nearmiss, tierauto, popupbrain, liq_filter, scansethonesty, contest_mode
— and were re-judged KILLED, leaving the pure-text 17.)

### 3.4 STATIC-AUDIT (1)

sink_hygiene runs diag_testleaks's AST audit as text — behavior mutants
cannot alter a static verdict; the audit engine itself is guarded by its
own permanent self-check.

### 3.5 SIBLING-CHECKOUT (4)

charter_signal_push, jackpotplate, pool_docsrot_live, pool_prefs_live —
the verdict imports or demands `../monero_project` (the pool's database
module, the pool `_live` probes). The sandbox copy can never satisfy it:
green from the real repo, structurally red in a copy — environment, not
regression.

### 3.6 HOST-BOUND (1) — probe-classified

test_hooks_ignore_runtime_state audits the *host's* live git identity —
the machine-local `.git/hooks` mirror and `core.hooksPath` — which the
sandbox cannot contain (`.git` is excluded by SKIP_COPY). Classified by
a dedicated three-shape sandbox probe (tempdir, drive + hooks/ copied in):

| shape | result | reading |
|---|---|---|
| A: git + armed mirror | rc=0, 4/0 GREEN | satisfiable when the identity is faithfully reproduced |
| B: git, no mirror | rc=1, 2/2 | fails *as designed* (missing fallback copies) |
| C: no git (sandbox shape) | rc=1, 2/1 | the sandbox can never host the audit |

The probe proves both halves: environment-not-regression, *and* that
behavior mutation could never flip the verdict either way — the drive
reads the host, not the sources.

**Gated since Sep 30** (the class sharpens, it does not lift): the
commit gate now stages the host assets in its own sandbox — a scratch
git repo with the staged hooks in its index and the `.git/hooks` mirror
armed FROM THE STAGED HOOKS (the same bytes `check_mirror` just
verified against the host mirror) — so the drive runs gated on every
staged-hook commit, fail-closed, and its no-runtime-state scan judges
the STAGED hook. The sweep class remains: the drive imports no guarded
module, so behavior mutation has nothing to flow through — arming the
sweep sandbox too would only manufacture the VACUOUS verdict the
manifest exists to prevent. This is the template for any future
HOST-BOUND drive: the gate judges staged content, the sweep judges
mutation sensitivity, and needing host assets makes a drive gateable
without making it mutation-testable.

### 3.7 STANDALONE (13) — AST-verified zero guarded-repo-module imports

dupcheck, find_eth, fleetsweep, gatestrips, lifecycle, liq_cal,
no_runtime_stash, pending, protected_extension_gate, reviewplate,
trailstop, verdicttrail, why_rejected. Each verified by AST: imports no
guarded repo module at all — the verdict rides subprocesses, source
text, or live artifacts, so there is nothing for a behavior mutant to
flow through.

### 3.8 SCAFFOLD-ONLY (2) — auto-detected, no manifest entry

test_commitgate.py, test_dupfinal.py: the only mapped guarded module is
`scratch_space.py` (test scaffolding) — no verdict-bearing logic to
mutate. `diag_mutation_sweep.py` detects this shape automatically
("auto:" reasons in the journal), so a future scaffolding-only drive is
classed without a manifest edit.

## 4. The killed 103 — and the repair history that got here

A KILLED verdict requires baseline-green plus ≥1 red mutant. The 103
include every drive repaired in the Sep-30 honesty arc; the tables below
are the per-drive reasoning for those repairs. Three recurring *drive
flaw classes* surfaced along the way, each now guarded by a tripwire:

1. **Exit-blind** — real mutant-killing pins, but failures printed after
   the exit (or with rc 0), invisible to the rc-only judge. Tripwire:
   output-aware `run_battery`.
2. **Never-aggregating** — per-check PASS/FAIL rendered as prose, no
   counters, so the drive cannot fail regardless of the mutants.
   Tripwire: commitgate's no-aggregate probe (static, fleet-wide).
3. **Test-the-live-tree** — absolute `sys.path.insert(0, r"C:\...")`
   imports meant the drive tested the LIVE checkout, never sandbox code
   (inside the commit-gate sandbox it still passed while staged code
   shipped). Tripwire: `Path(__file__)` idiom; commitgate judges the
   staged INDEX.

### 4.1 Obsolete verdicts — the exit-blind tail (7 → KILLED)

depthcheck, popupbrain, positionscale, soldpnl, tierauto, nearmiss,
scansethonesty. Their journal verdicts predated the same-morning
fleet-honesty patch: the pins were real, but exit-blind tails hid them
from the rc-only judge (red printed, rc 0 → "green"). Re-run under the
fixed tree: all KILLED (soldpnl and tierauto under 130 contest_account
mutants; nearmiss under 1851 background_scanner mutants).

### 4.2 Missing verdicts — four repairs (→ KILLED)

| drive | defect | repair | kill proof |
|---|---|---|---|
| test_longonly.py | verdicts rendered into text, no aggregation — could never fail | ok-flags + RESULT + honest exit | 3 failed under scanner+strategy mutation |
| test_contest_mode.py | same | same | 1 failed under scanner+strategy mutation |
| test_liq_filter.py | counted results but printed no verdict, no exit | RESULT + honest exit (cases 1/3 were already complementary only-path pins: rotation-floor vs daily-backstop) | 1 failed under background_scanner mutation |
| test_trump_render.py | counted + printed `TOTAL` but no exit, **and** imported the live tree via absolute sys.path insert | `Path(__file__)` idiom + RESULT + honest exit | 3 failed under config+strategy mutation |

### 4.3 The red six (pin drift, → KILLED)

| drive | failing pins | diagnosis | repair |
|---|---|---|---|
| test_lonewolf.py | 5 (tier math) | drift vs the Sep-23 adaptive carve-out retune (documented in lone_wolf_discount's docstring): req/scale are carve_out.py's regime-aware reading, OUT tide needs the lone_wolf permission gate | deterministic carve_out stub (restored before the tail) restores the documented 35/25/0 values + new section 13 pinning the adaptive semantics (scale 1.5 → 38/52, tightened req, permission-denied hatch closed) — 26/0 |
| test_trendbridge.py | 1 + absolute-path hole | the Sep-14 EXIT-WATCH switch wiring was replaced by the SCOUT_TREND_EXIT_ENABLED module gate (same no-silent-SELL guarantee); the absolute bot-repo sys.path insert shadowed the scout's signal_bridge | pin re-aimed at the current gate shape; absolute insert removed — 28/0 |
| test_eracompare.py | 1 | environment drift: the pin asserted the repo-root one-shot marker doesn't exist, but the scheduler's mark_done fired Sep 28 20:00 (the feature's own success); write_report itself never regressed | STATE_PATH sandboxed in the block like REPORT_PATH — 35/0 |
| test_herwords.py | 1 | the her-words feed call block re-indented 8 → 4 spaces in the Sep-21 status-line re-fit; the adjacency itself never moved | pin re-aimed at the current indent — 9/0 |
| test_marketgoal.py | 1 (morning) | NO REPRO twice (46/0 rc=0) — same live-state race family as commitgate's transient | none needed; re-judged KILLED by the sweep |
| test_worthdial.py | 1 | the pin demanded the diag's RUN ARTIFACT (scratch/reports_temp/_worthdial_check.txt) — manually generated, scratch-cleanable, ALWAYS absent inside the gate sandbox (SKIP_COPY excludes scratch/) | pin now tests the tool's wiring: diag exists + writes to the sanctioned sink — 37/0 |

Journal re-judge after the repairs: all six KILLED
(lonewolf/eracompare/herwords/marketgoal/trendbridge under
background_scanner's 1851 mutants; eracompare also kills discipline 493
+ era_compare 20; worthdial kills scanner 1851), and the stale
test_takeit.py BASELINE-RED (pre-repair) re-judged → KILLED.

### 4.4 Fleet-honesty tail #2 (5 → KILLED)

carry, execution_tiers, giveback, headline, trend — printed
`FAILURES: N` without exiting (takeit-family shape, invisible to
rc-only consumers). All patched with `raise SystemExit(1 if fails else
0)`; all verified green rc=0, all KILLED in the final battery.

### 4.5 The three drifted drives (task 57, → KILLED)

| drive | diagnosis | repair |
|---|---|---|
| test_allgoodlead.py | three pins predated the Sep-29 give-back-lead redesign and read the LIVE guard state — a real $55 give-back day made the production code correctly lead with the erosion while the drive demanded the old shape | guard-state sink (`_pg_sink.STATE_FILE` + `_ta_sink.TELEMETRY_PATH` in the shared `_sink_dir`) hoisted over sections 1–4 with an all-good giveback `{give_back:0,peak_total:0,now_total:0}`; section 7 re-ships per window; restore deletes STATE_FILE — 26/0, kills scanner 1851 |
| test_carveout.py | pins assumed mid-contest-window math while `compute_carve_out` reads `meta_layers.endgame_active(when)` LIVE — the end-game phase suppresses ×0.5 with mode OFF, so every neutral/expansion pin read half its value | `ml.endgame_active = lambda when=None: False` stub before section 2, new 2E.1/2E.2 pins holding the end-game truth itself (neutral suppress 0.05×0.5 mode OFF; end-game outranks expansion — five-mod product ×0.5), stub spanning section 3's relationship comparisons (both sides same regime) — 50/0, kills carve_out 509 + meta_layers 354 + profit_guard 209 |
| test_watchliveness.py | live probe crashed `.get()` on null rows — ALL 17 live contest_watch.json rows are None (the tombstone cleanup shape) | non-dict rows print "tombstone (cleared)" and skip; the probe fails only on a genuinely unreadable file — 72/0, kills scanner 1851 |

## 5. Maintenance rules

- **A VACUOUS verdict is a triage obligation.** Class it into the
  manifest with reasoning (this file is the reasoning home), repair the
  drive, or both. Never leave one resting.
- **Never vacuity by omission, never delete UNMAPPED.** The manifest
  lists only drives proven untestable; everything else runs. UNMAPPED
  stays the queue that surfaces new drives.
- **Delisting is the re-judge.** A classed drive that grows real
  runtime pins comes out of `UNTESTABLE_CLASSES` and is judged by the
  sweep again — remove the entry, run `diag_mutation_sweep.py --drives`.
- **commitgate judges the INDEX.** The gate's sandbox is built from the
  staged tree — stage repairs before running it, or it judges unrepaired
  HEAD (this caused two false reds on Sep 30).
- **Known flake**: commitgate's 2.2/2.21/2.22 trio is a scanner
  live-state race; it clears on re-run (final each time 73/0). Re-run
  before diagnosing.
- **SWEEP_REUSE_SBX**: the sandbox copytree alone takes ~8 min
  (artifacts/ carries the scanner's rotation dirs — NOT excluded from
  SKIP_COPY; 13 drives legitimately read artifacts/). With the env set
  and a sandbox already present the copy is REUSED, so a window that
  died mid-drive is followed by a window that spends its whole budget on
  the drive. Default (no env) is unchanged: always rebuild — the
  mutation run must see the CURRENT tree. Never leave a stale sandbox
  reused across unrelated work.
- **HEAVY budgets**: test_apptest 360s, test_tidebarweekly 360s,
  test_stateclobber 480s; everything else 300s. A HEAVY drive's
  baseline+mutation cannot fit one fresh-sandbox window — chain windows
  with `--resume`.
- **This file must stay consistent with the journal — automatically.**
  The doc-journal consistency probe (commitgate, Sep 30) re-derives the
  ledger table and every class count from the STAGED `journal.json` at
  commit time; any drift between this document's numbers and the
  journal blocks the gate. Update the numbers here in the same commit
  that changes the journal. The manifest-consistency probe
  (`_SEALED` seven-class set) guards the code side; member lists and
  repair numbers are still checked by hand when the manifest changes.

## 6. The cross-repo verdict — the sibling bot's fleet (Sep 30)

The scout's mutation standard was carried to `kraken_xrp_trading_bot` by
`scratch/bot_mutation_audit.py` — the same protocol (sandbox copytree,
`mutation_ops` all-sites-at-once per guarded module, the output-aware
judge), with the bot's honest equivalent of the hook matrix: AST-verified
runtime imports **plus** the drives' source-slice consumption
(learningplate exec-extracts app.py, lockguard exec-runs bot_runner's
guard block, the scoreboard exec-extracts the fills fetcher). Scout-side
artifacts, bot repo untouched: `scratch/bot_mutation_journal.json` +
`scratch/bot_mutation_report.txt`.

**The bot ledger: total 5 — killed 4, VACUOUS 0, BASELINE-RED 0, LIVE 1.**

| drive | verdict | kills (mutation sites -> red) |
|---|---|---|
| test_fills | KILLED | fills 44, scout_signal_reader 232, bot_engine 263 — genuine pin failures (15/12) |
| test_learningplate | KILLED | app 285 — the literal-string pins bite (20/10) |
| test_lockguard | KILLED | bot_runner 49 — the exec-slice itself fails on mutated source |
| test_rearmmirror | KILLED | signal_bridge 127 (15/1), bot_engine 263, scout_signal_reader 232 |
| test_scoreboard | LIVE | no verdict contract: keyring + real Kraken API, an accounting summary, no RESULT/exit — structurally unkillable |

Same-day follow-through: **test_scoreboard grew the fleet contract**
(bot `2432d92`) — hermetic fixture-exchange seams (KrakenAPI never
constructed; the fake rides `kraken.exchange`), convert pairing +
fallbacks + cent-exact accounting pinned, RESULT + honest exit — and is
**kill-proven** (the 285-site all-sites mutant of app.py turns it red on
the convert-pairing pin). The bot ledger is now **5 judged: 5 killed**,
zero unexplained. Note: `test_lockguard.py` is still UNTRACKED in the
bot repo — four of the five audited drives are versioned; tracking it is
the open bookkeeping item. The scoreboard's upgrade is the template for
any future LIVE-class drive: a live diagnostic becomes judgeable by
replacing its live inputs with fixtures at the seams the production code
already exposes — no production change required.

**Oct 1 follow-up wave — the ledger claim above is now tool-verified,
not just asserted.** The scoreboard drive is MAPPED
(`DRIVE_MODULES["test_scoreboard.py"] = ["app.py"]`: the 2432d92 fixture
seams let the 285-site app.py mutant bite it exactly like learningplate's),
and the full-fleet re-run reproduces **5 judged: 5 KILLED** in
`scratch/bot_mutation_journal.json` — 1,789 mutation sites across 9
drive-module pairs, all red; zero VACUOUS, zero BASELINE-RED, zero LIVE.
Repairs riding the same wave: `signal_bridge.py` gained `_sql_retry`
(transient-lock-only bounded retries) and idempotent
acknowledge/mark_executed — first REAL truth wins (a placeholder 0/''
from the callers' .get fallbacks never cements itself and is repaired by
the real price), closed/rejected stay terminal against stale claims, but
a confirmed fill records over a cancelled/expired intent the world raced
(the Copilot-caught pair; the phantom sweep reconciles downstream) —
synced byte-identical to both repos, pinned by rearmmirror section 5's
real-sqlite duplicate/resurrection/race/repair pins (26/0);
`bot_runner.py`'s guard is
extracted to a testable `lock_guard()` and test_lockguard drives it
directly instead of exec-slicing source (10/0) — both files are
currently user-dirty with the uncommitted recycled-pid hardening the
drive pins, so guard+drive land together in the bot's next commit.
`test_lockguard.py` remains UNTRACKED — still the open bookkeeping item.
The audit tool itself gained a `--drive/--module` per-module mode
(journal row merge, separate per-module report, mapped-module
validation) and a `BOT_MUTATION_AUDIT_BOT` env override;ready-to-use CI workflows sit in the bot repo's `.github/workflows/`
(bot-drives on push/PR, nightly bot-mutation-audit with journal/report
artifacts) — dormant until the bot repo has a git remote, which it has
never had.

**Oct 4 wave — the survivor and the mask that hid it.** The targeted
sweep over the Oct-3 review pins (test_autoadjust.py, test_apptest.py)
killed autoadjust's module mutant, but the surgical M1-c revert (the
suppression-trim's archive write silently dead, every pinned string
intact) passed apptest 335/0: a TEXT census pin can never see a runtime
kill, and the behavioral drive first placed inside test_apptest was
MASKED — app.py's `_bridge` hardcoded
`sys.path.insert(r"C:\Users\birds\Workspace\kraken_scout")`, so inside
the sandbox every later import resolved from the REAL tree and the
mutant never executed (proven by printing the imported module's
`__file__`). The path now rides the `Path(__file__)` idiom (the
trump_render precedent), and the trim's lifecycle moved to its own
STANDALONE drive: `test_trimlifecycle.py` imports ONLY
background_scanner and drives the real `_log_suppression` — the cap
holds the LAST rows, every eviction lands in the archive in order,
pre-existing archive content survives the append, no tmp residue.
Official verdicts re-judged with the current tree: test_autoadjust.py
KILLED (re-judged — replaces its Sep-30 KILLED entry),
test_trimlifecycle.py KILLED (surgical dead-write
mutant turns T2 red with a seed-only archive; green restored). Ledger:
163 drives, KILLED 104. The masking hole class is new to the doc:
a drive can be perfectly behavioral and still vacuous-in-fact if the
sandbox cannot make it execute the mutated bytes — hence STANDALONE
drives (no repo imports beyond the guarded module) are the sweep's
preferred shape, and absolute-path sys.path inserts in production
modules are gate-worthy defects even when every drive is green.
