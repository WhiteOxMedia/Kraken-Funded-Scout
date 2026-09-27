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
