# RUNBOOK — Silent-Rotation Investigation (Sep 28)

## Trigger
The boundary line at a rotation edge reads "SILENT ROTATION 12-16:
N rail-worthy candidate(s) were buried" (or the ask-why voice fires),
or the console's burial-hindsight expander shows a payer-rate you do
not believe.

## The four questions, in order

1. **Who was buried?**
   `uv run python -X utf8 per_gate_export.py <rotation_id>` — e.g.
   `2026-09-28-12-16`. Prints the per-gate table (B_g / P_g / r_g) and
   writes `artifacts/per_gate_ratios/rotation_<id>.csv`.

2. **What did they do afterwards?**
   Open the console's 🪦 BURIAL HINDSIGHT expander (same numbers, live),
   or read the CSV: each payer is a candidate the gates refused that
   reached +1.5% within 7h. The top rows are the cost of the refusal.

3. **Which gate owns it?**
   The per-gate table sorts by payers then rate. One gate carrying
   >50% of the payers is a SINGLE-GATE STORY (the auto-adjust's
   concentration gate will refuse to auto-apply on it) — investigate
   that gate's threshold, not the guard.

4. **Should the guard move?**
   The rule moves it automatically ONLY when every rail passes
   (B >= 5 measurable, r >= 0.25, concentration <= 0.5, daily cap).
   A queued adjustment waits in `profit_guard_state.json` →
   `auto_adjust_ledger` with status `queued` (the ledger lives in
   profit_guard's state — the file the factor itself is written to).
   To apply a queued one:
   `hindsight_api.apply_action(...)` with the operator token
   (HINDSIGHT_OPERATOR_TOKEN); to refuse it, do nothing — it expires
   with the rotation and the ledger keeps the row.

## If the numbers look WRONG
- Check the join's approximations first: entry = last close before the
  refusal, run = max high within 7h (a peak, not a fill). The caption
  on the panel says so for a reason.
- Verify the evidence: `evidence_store.verify('<rotation_id>')` — a
  drifted bundle means the bytes changed under the study.
- Cross-check against `_verdict_study.py`'s burial side (the same join,
  independently written); a disagreement is a bug in one of them.

## Escalation
A rotation with B >= 10 and r >= 0.5 (half the buried candidates paid)
is not a tuning problem — it is a gate malfunction. Roll the feature
back (`playbooks/auto_adjust_rollback.md`) and postmortem it.
