# GOVERNANCE — Adjustment Policy (Sep 28)

Scope: every parameter that changes how much risk the machine takes
per unit of evidence. Currently: `ALPHA`, `DELTA_MAX`, `DAILY_CAP`,
`B_MIN`, `R_MIN`, `CONCENTRATION_MAX`, `ADJUST_DURATION_ROTATIONS`
(`auto_adjust.py`), and the guard clamp in `profit_guard.GUARD_CLAMP`.

## The standing defaults
`ALPHA=0.05, DELTA_MAX=0.10, DAILY_CAP=0.20, B_MIN=5, R_MIN=0.25,
CONCENTRATION_MAX=0.5, BOX=3 rotations` — chosen conservatively (the
task-5 sweep's recommended cell) and not to be tuned by feel.

## Change cadence
- Weekly for the first month after first live canary, monthly
  thereafter, and ALWAYS after a postmortem whose class touches the
  rule (masked-failure, silent-degradation).
- Every review runs the sweep (`simulate_sweep.py`) on the current
  history; a parameter change that widens risk must show, in the
  report, a better expected-net WITH a bounded worst single-book loss.
- A parameter change is a gated commit touching the parameter block
  ONLY (reviewable as one screen), plus updated sweep artifacts.

## Approval flow
1. PROPOSED — an issue/ticket with the sweep cell comparison.
2. CANARY — the change rides a cohort (`canary_runner.flip_canary`),
   minimum 24h AND >= 50 cycles, thresholds per the canary runner.
   A breach auto-rolls back and returns to PROPOSED with the run
   report attached. The cohort arms on ITS OWN file
   (`auto_adjust_canary.json`) — a cohort arm or rollback can never
   silently disable (or re-enable) the exit-extension feature;
   stopping the feature stays the operator's own dial action.
3. SIGN-OFF — Ops AND Product (one human each, named in the ticket).
   No sign-off, no full fleet; auto-adjust's promotion from canary to the full book is
   controlled by the cohort configuration in
   `auto_adjust_canary.json` (the two-file design); the
   exit-extension dial file is the FEATURE master, never the
   auto-adjust gate.
4. FULL — the cohort configuration moves for the book; the change is
   permanent only when its gated commit is on master. A temporary
   cohort or dial state is NOT a deployment.

## Rollback authority
Anyone on call may roll back (the runbook's one-liner) WITHOUT
approval, at any time, for any reason. Rollbacks are never punished;
a punished rollback becomes a slower next rollback.

## Non-negotiables (not subject to this policy's cadence)
- The concentration gate's existence (an auto-apply on a single-gate
  story is forbidden; the threshold value may be reviewed).
- The append-only ledger and telemetry (no rewriting history).
- The time box (any auto-applied loosening expires; permanence
  requires the flow above).
- The fail-closed directions: lost counter = no arm; lost evidence =
  no apply; unreadable rate = no adjust.
