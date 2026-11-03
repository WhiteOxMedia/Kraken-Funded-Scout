# Kraken Scout

The signal + execution brain: scans pairs, grades entries, runs the bot's
exit ladder, and reports to the operator. Runtime contracts live in
`RUNTIME_INVARIANTS.md`; sync machinery in `SYNC_BOT.md`; the review gate in
`CODERABBIT_GATE.md`. Ops are the `Launch Scout.bat` / `Health Check.bat` /
`Kill Switch - FLATTEN.bat` / `Kraken - STOP.bat` shims.

This README exists so the exit ladder's rationale survives as more than
tribal knowledge. Every file, constant, and number named here was verified
against the tree on 2026-09-27 (docs-honesty convention — a future docs-rot
drive may pin them).

## THE EXIT LADDER (bot rules)

The bot does not time a single exit. `background_scanner.scout_exit_check`
synthesizes ONE decision per cycle from an ordered ladder:

```
SL −5% → TP +10% → knife → ATR trail → strategy sell ≥75/60 → exhaustion → momentum-rider → THESIS DEAD
```

- **The trailing stop runs FIRST** in every check, and live price is the
  truth: the ticker seeds the in-progress candle (real-time, not the last
  closed bar).
- **THESIS DEAD**: a buy thesis stays alive only while the read is buy (or
  an ambiguous hold below the confidence bar); a sell read or a confident
  hold kills it.
- **The goal clock is contest-only**: no GOAL REACHED / WINDOW EXPIRED exits
  on the bot — it rides winners to its own exits. Popups stay contest-only;
  the exit write never notifies.

### The trail's shape (the part that ratchets)

`background_scanner.py` (constants around line 894, `_ratchet_trail_pct`):

- `CONTEST_TRAIL_PCT = 3.0` — base width, percent off the running peak.
- **Ratchet**: 2.0% once the peak reaches +1% of entry, 1.5% at +2% — the
  Sep-21 re-base from tier-blind +$100/+$200 thresholds that engaged ZERO
  times in the then-48-trade ledger (percent terms engage on any real run
  at every tier). Original sin on record: TAO ran nine twenties and gave
  back $150 before a flat 3% trail called the exit.
- `GOAL_TRAIL_ARM_FRAC = 0.5` — the half-goal arm (user rule, Sep 22): the
  goal-armed trail LATCHES at half the goal instead of waiting for the goal,
  and the armed width caps tighter as the run grows (floor 1.0%).
- ATR-multiple variants live in `discipline.py`
  (`TRAIL_SIM_ABLE = (1.0, 1.5, 2.0, 3.0)` — widths the study sims) and the
  ladder's knife/exhaustion legs in `strategy.py` / `background_scanner.py`.

### The pairing rationale (guard × trail)

The profit guard (`TP +10%`, `profit_guard.py`) fixes the bank; the trail
protects the run. Neither alone is sufficient:

- Guard alone caps winners whose run outruns the target and does nothing
  for losers that never reach it.
- Trail alone is a giveback machine on trades that were going to bank:
  it exits on the first retrace instead of letting the goal fill.
- Together, tier-scaled, they are the incumbent: the guard banks the goal,
  the ratcheting trail tightens protection as the run earns it, and the
  momentum-rider leg is the sanctioned "let it run" override.

## PRESET MAPPING (the operator's pairing vocabulary)

The conservative / balanced / aggressive pairing presets map onto the
ladder like this:

| preset | textbook shape | what the ladder already does |
|---|---|---|
| conservative | tight guard + 5–8% trail | the half-goal arm + post-+1%/+2% ratchet (2.0%/1.5%) converge here as the run grows |
| balanced | +12–20% guard, 6–12% trail | the incumbent default: SL −5% / TP +10% + 3%→1.5% ratchet |
| aggressive | no guard, 12–20% trail | the momentum-rider leg: overrides the TP to keep riding strong trends |

**Scale warning (Sep 27, measured, not vibes):** the scout is a scalper at
the current tier — `TAKE_IT_USD = 20`, `SCALP_TARGET_PCT = 0.4%`. Swing-scale
preset percentages (+15% guard, +12% trail) never intersect this book inside
the study horizon; applying them literally is a no-op, and applying the
conservative preset literally (+6% guard) actively CAPS trades whose goal was
higher (see the backtest below). Dials must be tier-scaled before they mean
anything.

## THE STUDIES (evidence, rerunnable)

Root-level `_` studies, read-only, built on `_verdict_study.py`'s graded
ledger (86 trades, Sep 3 → Sep 27, fees via `net()`). All study numbers
below are snapshots frozen at Sep 27 — the live ledger grows daily, so
the truth lives in the studies (rerunnable) and the drives; re-run them
for today's numbers. Control accounting is the corrected form: every
simulated exit (banks included) counts, the bar is a relative bar
against the incumbent ratchet's own control cost (baseline -95.96 on
the frozen cohort), not a naive zero.

`_trail_k_sweep.py` — fixed armed-trail widths 1.0/1.5/2.0/3.0 vs actual:
every width BROKE the took-the-goal control (−43.53 / −86.89 / −131.28 /
−145.54 $) even where `stayed too long` and `exited too soon` classes gained.
The guard discipline (`trail_proposal` in `discipline.py`: a good dial must
not break the control) therefore keeps the incumbent. A fixed-width trail
cannot see the goal; the ladder can.

`_hybrid_trail_sweep.py` — THE FAIR VERDICT (Sep 27): the same widths
re-simmed as a TP-preserving hybrid — the goal bank stays LIVE inside the
sim (guard-first bar ordering), so the control becomes structurally
satisfiable, and only the post-goal RIDE leg is replaced. The cohort that
rules is the armed near-miss rides (trails legitimately have a say only
there). CORRECTED CONTROL ACCOUNTING (CodeRabbit major, Sep 27): every
simulated exit — banks included — counts toward the control, because the
ladder rides past goal on many took-the-goal books and banking them at
goal is a real loss; the goal-fill counterfactual alone is −54.24 and the
incumbent ratchet's own control cost is −95.96. The bar is therefore
RELATIVE: a candidate's control cost must not exceed the incumbent's own
under the identical walk. Under that bar the tight widths are excluded
(−102.57 / −99.84) and the incumbent stands — verdict unchanged, now on
defensible arithmetic. Methodology note: a ruling cohort must be FROZEN —
the live ledger flipped a mutant run 18/1 → 19/0 within minutes before
the freeze rule existed (now pinned in `test_trailk_sweep.py` 3.3).

`_preset_tierscaled.py` — the presets re-mapped to the scout's real scale:
guard AT the trade's own goal (the fixed-guard poison gone by
construction), trails 1.5/2.0/3.0 running FROM ENTRY (preset semantics
kept — no arm bar). At the scout's own 4h horizon, from-entry presets are
catastrophic (−219 to −297 vs actual): the ladder's synthesis is worth
that spread. At 24h the aggressive variant beat the ladder's TOTAL
(+20.14) riding entirely on `bought the top` — and was DISQUALIFIED by
the control (−142.68, corrected accounting). Five studies, five defense
wins, the same guard each time.

`_guardfree_trail.py` — decomposed the conservative preset's "medicine":
with the guard gone and the full-goal bank live, `stayed too long` keeps
only +0.60 of its claimed +114.16 — the savings WERE the poison operating
on another cohort. The flat 1.0% trail's real gain (+38.16 on
`exited too soon`) costs −102.57 of control (worse than the incumbent's
−95.96): disqualified under the relative bar.

`_preset_backtest.py` — the three presets as complete exit policies
(guard limit + trailing stop) over the same ledger, actual ladder as
baseline (+81.54 $ realized), now at THREE horizons (Sep 27: 4h/24h/48h,
with per-horizon CENSORSHIP counts — a trade whose window runs past
data end keeps its actual exit and its untriggered status is NOT proof
of no-fill; fills beyond the data window stay unknown):
conservative −73.79 at 4h, −53.17 at 24h, and +7.65 at 48h — but that
48h "win" fails the control (−14.72) and rides on 13 censored books;
balanced's 4h innocence was horizon illusion (−458.51 at 24h: its 8%
trail whipsaws `scratch` and `took the goal` once the window gives it
room); aggressive, the no-op the extension existed to test, is CONDEMNED
at every horizon (−192.45 at 24h, −386.95 at 48h, control broken in
both — the wide trail cuts winners' tails, `exited too soon` −107.74).
Verdict: the ladder beats every preset at every horizon under the house
rules; the medicine is the tight trail on held-too-long, the poison is a
fixed guard below the goal, and a trail wide enough to survive chop is
wide enough to give back the run.

Same-bar ambiguity note: on 1h candles a bar can touch both guard and
trail; the studies assume guard-first (conservative ordering).

`_livearm_shape.py` — the EXACT live half-goal arm simulated on 1h bars
(latch at GOAL_TRAIL_ARM_FRAC × goal, armed cap max(goal/2, 1.0), price
floor at entry + 2×RT): the result (banks collapsing 34 → 11, rides 68,
net −212.39 vs hybrid +42.38) was DECLINED as a verdict — both fidelity
gaps (intrabar ordering, the omitted guard-factor widening) bias the
1h walk against the live machinery, and a bar-level number would have
condemned the dial on an artifact. The study's contribution is the
refusal, stated in the artifact itself.

`_livearm_referee.py` — THE TICK-LEVEL REFEREE (Sep 27, commit 459c02b):
the half-goal arm question is CLOSED on the record at the resolution the
machinery actually runs at. Data: the deep 5m store (108 pair feathers,
Aug 9 → today) — 12× the 1h studies' resolution; guard factor: the LIVE
value from `profit_guard_state.json` (2.0), folded into the trail width
and the armed cap exactly as `background_scanner` does — both 1h fidelity
gaps closed with real assets, and the intrabar-ambiguity question is
MEASURED away (zero same-bar goal+stop conflicts on the whole book, not
assumed). Fresh reruns on the grown 87-trade ledger reproduce every
number to the cent. The verdict: LIVE +39.50 (ctrl −126.67, floor ×62) |
HYBRID +138.96 (ctrl −68.59) | ladder +80.69 — the hybrid idealization
beats the ladder's total but the REAL live shape does not, and under the
relative control bar the ladder is the only policy that pairs its total
with its control discipline. The referee's finding is structural: the
live cap is GF-scaled, so the same guard factor that loosens the trail
also loosens the cap and strangles rides (40 rides at −15.41 total).

`_gf_sensitivity.py` — THE BRACKET (commit b57aed4): is the referee's
verdict robust to the hidden historical guard-factor time series? The
state file recomputes self-consistent from its own parts (twice-verified
across drift: raw composites 15.86 and 8.97 both clamp to 2.000 — the
0.5–2.0 clamp absorbs even a near-halving of the raw factor), and the
GF 0.7/1.0/1.5/2.0 sensitivity matrix keeps the ordering at every rung:
the hybrid beats the ladder's total at every GF, the live shape never
does, and the relative bar disqualifies the hybrid at every GF. The
verdict rests on a bracket, not a point estimate. GOING FORWARD the
series is no longer hidden: `profit_guard.self_adjust` preserves every
day's adjusted factor + parts in the state file's `history` array
(`factor_history()` is the studies' seam; `test_profitguard.py` 9b.1–9b.8
pin the append/dedupe/prune/degrade semantics; the live file was seeded
with Sep 27's row so the series starts now) — future studies map each
trade to its actual day's GF and the bracket becomes a robustness bonus,
not a necessity.

`_floored_90arm.py` — THE REFEREE'S CANDIDATE, ANSWERED (Sep 27, revised
same day: the CodeRabbit review caught the candidate's arm latch as
symbolic — armed never flipped, so the first draft's +47.43 row and its
"cap IS the floor, floor ×0" story were a dead-code artifact; the latch
is real now and the parity rows are untouched): the one variant the
referee made askable — the hybrid's 90%-arm fused with the live
machinery's price floor and GF scaling, on the referee's exact walk
(live and hybrid rows reproduce the committed numbers exactly).
Result: +69.73 total (46 banks +162.59, 15 rides −92.86, ctrl −81.51,
floor ×18 — at GF=2 the lock_w width overshoots below the floor and the
clamp fires for real). GATE: FAIL — control-safe (−81.51 vs the
ladder's own −126.67) but still short of the ladder's +80.69: the
protected 90%-arm keeps most of the hybrid's banks (46 of 49) at less
protection cost than the live shape (−81.51 vs −126.67), yet the
cap+floor break the hybrid's 2 big late rides into 15 earlier, smaller
ride exits (−92.86) and the fusion never catches the ladder. The
structural conclusion stands: the hybrid's edge is inseparable from
running UNPROTECTED — the referee's question is closed, no 90%-arm
variant with the machinery's protection beats the ladder at 5m. The
candidate stack on record: live +39.50, candidate +69.73, ladder
+80.69, hybrid +138.96 (disqualified).

`_protected_extension_gate.py` — THE EXTENSION'S ACCEPTANCE GATE (Sep 27,
revised same day: the CodeRabbit review caught the walk fabricating a
last-close horizon exit for books whose pair's feather ends inside the
24h window — censorship is pair-local now, touches/trails observed
before the data end still count, the verdicts are unchanged and the
measured nets move). With `test_protected_extension_gate.py` red-first,
15 checks: the protected 24h extension (below) shipped on parity
constraints alone; this study supplies the measured gate — the
CORRECTED CONTROL BAR as a mechanical PASS/FAIL on the real ledger (one
87-trade cohort, the referee's 5m walk, journal-keyed cut classes). The
raw dial FAILED its own gate honestly: a cut whose TP fill sat a hair
below the goal level gets re-held, the goal never re-touches, and the
GF-loosened trail bleeds it out (control −15.08, not the required 0.00
— arming a PAID book is a leak, the bar caught it). Above-goal
blow-throughs (4 books, −136.75 phantom rebank delta) are counted
separately, never gated — closed rows the dial never touches. THE
PRECONDITIONED FORM (arm only when the cut fill sits below 98% of the
goal level) PASSES at exactly +0.00 control over the cut+banked cohort,
with the best measured net of any extension variant (+144.44 on 16 of
40 cuts; recoveries +216.37, leaks −71.93; 3 of 40 protected cuts
censor as unknown). The precondition is IN the shipped machinery (Sep
28: the arm path refuses any fill at/above 98% of the goal level — the
gate's passing form made mechanical; see the extension section below).

`_referee_gfdelta.py` — THE REFEREE, RE-RUN AT PER-DAY GF (Sep 28,
armed for the accrual): the half-goal-arm thread's one deferred
refinement now has its instrument. Every referee-arc study priced its
simulated widths at ONE frozen factor — the state file's current
value — while the machinery actually runs each day at that day's
factor; the bracket (b57aed4) bounded that error, this study measures
it once the series is long enough to matter. The walk is the referee's
exact branch structure with every GF site inside the bar loop (ratchet
width, armed cap, lock_w) reading the factor live on THAT bar's MST
day from `factor_history()` — bar-level, because a ride feels whatever
factor stands while it rides (chain-back: pre-series bars carry the
newest known factor; the 1.0 floor only when no series exists at all).
Two gates: the frozen rows must reproduce the committed referee
exactly (drift aborts exit 3 — a drift indicts the copy, not the
refinement, the `_floored_90arm.py` lesson) and the series must span
≥ 14 distinct days (~2 weeks, the operator's accrual ask) before the
delta prints; before that the study reports NOT YET with the current
span and exits 0. Today: parity OK, span 1 day, the per-day rows are
the chain-back preview (308 of 6,911 bars on the series day).
`test_referee_gfdelta.py` (19 checks) pins the chain-back rule, the
MST day key (the writer's calendar — 02:00 UTC is the previous MST
day), both gates hermetically, the real tree's parity, and the shared
seam itself. THE SWEEP (Sep 28, same session): the mapping left the
study and became `_gf_series.py` — `GFSeries`, the arc's single source
for the day→factor contract (chain-back, dedupe, MST bar key, span
gate, NOT-YET text) — and the rest of the referee arc now accepts the
same refinement behind the same gate: `_floored_90arm.py`'s walk takes
`gf_fn` (frozen rows + parity untouched, a span-gated per-day section
appended after the parity check) and `_protected_extension_gate.py`'s
walk does too (frozen-factor rows remain the committed gate; the
preconditioned variant re-derives at per-day GF only once the series
is ready — the precondition is the shipped machinery's form either
way). `_goalreach_24h.py` has no GF site — it never widens a stop — so
there is nothing to refine there. Frozen-factor outputs are
byte-identical to the committed rows everywhere; the per-day sections
wake up automatically when the history clears 14 days.

Half-goal-arm thread, closed end to end: the 1h study declined to
adjudicate, the referee adjudicated at 5m (the incumbent stands, the
unprotected hybrid is disqualified by the bar), the bracket made the
verdict GF-robust, the floored candidate closed the referee's one open
question negatively, and the extension gate turned the same corrected
bar into a shipped-mechanism acceptance test. Every claim above is
re-runnable; every verdict was derived from the artifacts' own printed
numbers and pinned by a drive where it mattered — and the CodeRabbit
advisory pass earned its keep the same day, catching the candidate's
symbolic arm latch and the gate's fabricated horizon exits before the
numbers were relied on. The one refinement the thread deferred — the
per-day real GF re-run — is armed behind its own parity + span gates
and prints the measured delta the moment the history clears two weeks.

## THE PROTECTED 24H EXTENSION (Sep 27, dial-gated, default OFF)

The goal-reach study (`_goalreach_24h.py`, commit 2a100c6) found the one
result in this stack pointing TOWARD a change: a uniform goal-or-24h hold
on CUT books nets +140.16 $ (cut cohort +173.09 $) — but the NAKED hold
it simulated has a fat tail (worst book −14.25 % below the cut price), so
the study's own verdict was "extend, but only under protection". The
implementable form is now in the tree as `exit_extension.py`, wired into
`contest_light`'s exit branch through a seam the machinery ALWAYS asks:

- The gate: only CUT-class exits (profit guard, hard stop, trend bar,
  climax) may arm the extension. THESIS DEAD is never a cut — a
  deliberate no-reentry signal stays final. A naked hold is impossible by
  construction: an extended book keeps riding under the INCUMBENT trail
  (the same ratchet, the same half-goal armed cap, the same price floor
  — parity pinned by the drive's mutant check 7.x).
- The dials (SCHEDULER-style, the pool's pattern): read LIVE on every
  verdict — no restart, no import-time freeze. Precedence: env var
  (tests/sandbox) > `exit_extension.dial.json` beside the state file
  (the operator: `{"EXIT_EXTENSION_ENABLED": "1"}` flips it on; delete
  or "0" flips it off) > defaults (OFF). `EXIT_EXTENSION_HOURS` clamps
  to 1–72 (default 24), `EXIT_EXTENSION_MAX_PER_DAY` caps the arms per
  day (default 3), `EXIT_EXTENSION_MIN_AGE_MIN` refuses to extend a book
  cut minutes after add (default 30). The master is fail-safe: anything
  but exactly "1" means every verdict exits and the dial-off sweep drops
  every stamp on the next scanner cycle — revert is mechanical.
- The receipts: an armed cycle logs `EXIT EXTENSION: <pair> — armed ...
  until <stamp>` and persists the stamp ON the watch row; when the
  window lapses, the stamp clears, the next exit verdict fires through
  the normal machinery, and the close journal is the receipt the study's
  counterfactual never had (the `_journaled` gate — same session —
  guarantees no row is ever popped without its ledger row).
- The below-goal arm precondition (Sep 28, the gate's PASSING form in
  the shipped machinery): the seam judges every arm on the fill the
  machinery SAW — a cut whose fill sits at/above 98% of the goal level
  exits (arming a PAID book is the leak the gate measured: raw ctrl
  −15.08), and an unverifiable fill or goal level also exits fail-safe.
  A row already riding its window is NOT re-judged (it armed under this
  gate, and a ride must be allowed to approach its goal). The 0.98
  slack is HARDCODED — a safety gate like THESIS DEAD, deliberately not
  a dial; no operator knob can relax it. Numeric edge kept honestly:
  for goals under ~2.04% the 98% line sits below entry itself, so a
  fill at entry fails (less room than the slack band).
- The drive: `test_exit_extension.py` (44 checks) ran RED-FIRST — the
  seam-existence, wiring-order, and revert-sweep pins failed against the
  unwired tree before the splice, then went green with it; the Sep 28
  batch (4.4 + 11.1–11.6) failed red-first against the pre-gate seam —
  unpaid cuts arm, paid-at-goal cuts exit at the strict boundary, and
  the 0.98 constant is pinned in source. It also pins the cap/age
  brakes, the env-beats-file precedence, and the sandbox convention
  (state resolved LIVE from `EXIT_EXTENSION_STATE_FILE`, never at
  import — the `1f4bc7c`/ledger-leak lesson applied).

The operator's runbook: to ARM, write `{"EXIT_EXTENSION_ENABLED": "1"}`
into `exit_extension.dial.json` — the next scanner tick starts asking
the seam; to REVERT, delete the file (or set "0") — the next tick sweeps
every stamp and the incumbent ladder stands alone again. The ladder is
unchanged while the dial is OFF; this is the first exit-side change the
studies ever voted FOR, shipped behind a gate the operator owns.

## DRIVES

`test_trailk_sweep.py` (20 checks — THE WIDTH CONTRACT: the sweep set
`TRAIL_SIM_ABLE`, the 90% arm, fees-via-net(), the guard semantics in
`trail_proposal`, and the INCUMBENT DECISION RULE executed on a cohort
frozen at load — cutoff = newest exit + 1 ms, because the live ledger
flipped a mutant run 18/1 → 19/0 within minutes before the freeze. 3.3
is a tripwire: it turns red the day a width clears positive-edge +
clean control through the fair hybrid walk, demanding a human re-
examine the dial), `test_trailstop.py` (27 checks),
`test_exitsynthesis.py` (ladder order + bot-only rules), `test_profitguard.py` (67 checks — hermetic in BOTH
venvs since Sep 27: its sections 3–8 pin meta_layers'
`discipline_penalty_extra` to zero and restore it before the LIVE
probes; 3.6 proves the RCL/SAL dock composes — the live stamp had
been leaking into the pure-math pins, the metalayers latent-bomb
class, sibling of 1f4bc7c's STATE_FILE fix). The
pool's docs-rot contract (`test_pool_docsrot_live.py`) carries the pool
README's file-honesty check inside this battery without importing across
repos — this README follows the same honesty convention voluntarily.
