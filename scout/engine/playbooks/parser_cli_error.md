# RUNBOOK — Parser / CLI Error (CodeRabbit 0.8.1) (Sep 28)

## Symptom classes

1. `⚠ review errors: rate_limit — advisories may be incomplete
   (fail-open, never silence...)` — the CLI bounced. Free allowance:
   3 reviews/hr. This is HONEST noise, not a failure. Wait out the
   hour; use `git commit --no-verify` ONLY for docs/comment-only edits
   AND record the skipped review in the ticket.

2. `unparsed stream` + a raw JSON head — the parser could not classify
   the CLI's output. The stream shape drifted or the CLI errored in a
   new way. Actions:
   - Read the printed head: an `errorType` record is the known shape
     (rate_limit etc.); anything else is a NEW shape.
   - Capture the raw stream: re-run the review in a worktree and save
     the output to `tests/fixtures/` as a new case, then extend
     `tests/hookparser_test.py` (the 0.8.1 fixture drive shows the
     pattern) — a new shape becomes a pinned shape, never a patch on
     the live commit.

3. A finding renders `file:? [sev] (stream gave no line/text - full
   record in the local store)` — the skeletal 0.8.1 record. Join the
   store: `coderabbit review list` (or read the review dir named by
   CR_SNAPSHOT in the hook log). This is the honest form; the parser
   must never fabricate a title.

4. `COUNT 0` + `CLEANJSON` on a stream that OBVIOUSLY had findings —
   the masquerade class (fixed once, pinned in test_hookparser §3d).
   Stop and bisect the parser heredoc in `hooks/pre-commit`
   (`PYCODE` block) — this is a gate integrity bug, treat it like a
   safety regression: red test first, then fix, both in one commit.

## The invariant
The hook's reviews are advisory and fail-OPEN, but they are never
SILENT: every degradation prints a named banner. If you ever see a
degradation with no banner, that is a finding against the hook itself.

## Store snapshot lock
The store join is snapshot-locked (CR_SNAPSHOT + git head). A store
miss after a rebase usually means the snapshot dirs were pruned —
re-run the review rather than loosening the lock.
