# THE COOLDOWN AGENDA

Standing operational instructions for the Agent Controller, held between
cycles. Added Sep 24, 2026 (user order: "Add to the cooldown agenda").
Steps are executed in the order written — the order of operations is
mandatory. After the checklist, resume normal operational-cycle duties
(daily / mid-week / weekly / end-of-week per the controller protocol).

---

## TOMORROW MORNING CHECKLIST — START OF NEXT DAILY CYCLE

At the beginning of the next operational day, perform these steps in order:

1. Run stalewatch:
   - Purge all scratch dirs and OS-temp workspace of files older than 90 minutes.
   - Write filename/age/timestamp-only summaries into reports/.

2. Run hygiene census:
   - List all remaining root writers.
   - Defer files carrying another thread's uncommitted hunks.
   - Report only names, never contents.

3. Verify scratch routing:
   - All volatile artifacts must land in scratch/ or OS temp.
   - No new root churn is allowed.

4. Verify report integrity:
   - All audit summaries must live in repo/reports/.
   - No stray reports may appear in the root.

5. Confirm swept-ledger trace is healthy:
   - No recurring scratch classes.
   - No regenerate-able litter loops.
   - No hidden hygiene regressions.

6. Confirm RULE D alignment:
   - No drift since the last known good commit.
   - No contract or ledger regressions.

After completing the checklist, resume normal operational-cycle duties.

---

## EXECUTION NOTES (from the Sep 24 cycle — how to run the checklist)

- Commands: `kraken_stalewatch.py` (step 1), `diag_hygiene.py` (steps 2-4,
  its audit covers routing + report integrity), then read
  `reports/hygiene_swept_ledger.jsonl` (step 5) and compare fossils vs
  `diag_hygiene.KNOWN_DEAD_ARTIFACTS` (step 6). Interpreter:
  `.venv/Scripts/python.exe -X utf8`.
- Step 1, the purge-vs-active-file question (CodeRabbit triaged, declined):
  90-minute age IS the disposability contract, not a heuristic — every
  scratch artifact is regenerate-able by construction (the drives produce
  their evidence right before asserting it, the 6e346b6 rule), and no drive
  can create-then-use across the 90-min line because MAX_MEASURED_BUDGET
  caps any battery entry at 1800s. Nothing needs protection the purge can
  reach; the review's scenario has no live instance.
- Step 4, the root-summaries exception (CodeRabbit triaged, accepted): the
  root `_*.txt` check summaries written by the 63 deferred drives are the
  documented writer BACKLOG (census-tracked, owned by the other thread's
  uncommitted hunks), not new strays — "no NEW root churn" is the
  enforceable test until their owner migrates them; a NEW name in the root
  is the violation.
- Step 5's health test: the ledger carries only genuine rows and no name
  reaches RECUR_AT (3) occurrences in the trailing window; diag_hygiene's
  selftest pins the machinery — run it if anything looks off.
- Step 6's drift test: the KNOWN_DEAD_ARTIFACTS names stay absent from both
  repo roots; `git log` after the last known good commit names no new
  contract changes, and the battery baseline is the ledger of record.
  Scope (CodeRabbit triaged, accepted): `git log` sees COMMITTED state only
  — the other thread's uncommitted hunks are explicitly OUTSIDE this
  verdict, so the report says "alignment confirmed for committed state; N
  deferred files left to their owner" instead of implying the worktree was
  examined.
- Current known-good baseline: `battery_history/run_20260924_165835.json`
  (mode=full, chunks=2, 145 passed / 0 FAILED / 49 skipped); landings
  `bc93ff6` (the merged CodeRabbit lanes contract) and `185ca58`
  (the review-of-the-review fix).
- Known noise, not drift: test_find_eth's one-off 56s contention outlier
  (measured 4.5s directly); the scanner/app "duplicate" PIDs are venv
  stub->child pairs — decode the parent PID before killing anything.
