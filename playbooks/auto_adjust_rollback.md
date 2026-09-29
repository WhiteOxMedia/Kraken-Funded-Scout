# RUNBOOK — Auto-Adjust Rollback (Sep 28)

## When
An applied auto-adjust (guard factor moved, time-boxed) is wrong: the
payer-rate was gamed, the regime turned, or the operator simply
disagrees. Roll back FIRST, investigate second — loosening the guard is
the riskier direction.

## Fast path (one command)
```
uv run python -X utf8 -c "import auto_adjust as aa, sys; r = aa.rollback('<rotation_id>'); print(r); sys.exit(0 if r['status']=='reverted' else 1)"
```
- Restores the ledger row's recorded `before`, clears the time box,
  appends a `reverted` row (append-only — nothing is deleted).
- Verify: `uv run python -X utf8 -c "import auto_adjust as aa; print(aa.read_guard_factor())"`
  must show the pre-adjust factor.

## Dial path (if the whole feature should stop)
The feature kill-switch is the exit_extension dial — read live per
tick, no restart. Set the dial yourself (the operator's own action):
```
uv run python -X utf8 -c "import json, os; p='exit_extension.dial.json'; d=json.load(open(p)); d['EXIT_EXTENSION_ENABLED']='0'; json.dump(d, open(p+'.tmp','w'), indent=2); os.replace(p+'.tmp', p)"
```
The CANARY cohort is a separate control on its OWN file
(`auto_adjust_canary.json`) — `cr.rollback()` stops the cohort only
and never touches the feature master (the two-file rule,
governance/adjustment_policy.md step 2).

## Revert path (if the CODE is wrong, not the data)
The adjustment rides commit `<sha>` (every telemetry event carries
`commit_sha`). Revert the commit through the normal gated flow:
`git revert <sha>` — the gate re-runs the drives on the reverted tree.

## After
1. Snapshot THE ROLLED-BACK ROTATION's evidence if not already — pass
   its id explicitly (a bare `snapshot()` bundles the CURRENT rotation,
   not the one just reverted):
   `uv run python -X utf8 -c "import evidence_store as es; print(es.snapshot('<rotation_id>')['bundle_checksum'])"`
2. File the postmortem (`playbooks/postmortem_template.md`) within 48h.
3. Check the time box did not leave a dangling factor:
   `profit_guard_state.json` → `auto_adjust_box` must be ABSENT after a
   rollback (the box lives in profit_guard's state — that is the file
   `set_guard_factor_temporarily` writes); if present, re-run the fast
   path.

## Never
- Never hand-edit `profit_guard_state.json`'s `factor` in place —
  every change rides the ledger or it did not happen.
- Never delete ledger rows; `reverted` rows are the audit trail.
