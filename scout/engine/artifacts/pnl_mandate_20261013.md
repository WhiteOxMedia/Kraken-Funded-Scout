# THE PnL-FIRST MANDATE — LIVE VERIFICATION RECEIPT (Oct 13)

User decree, Oct 13: profit is the primary metric; scores exist only to
improve profit. A high Sniper score with negative PnL is a MISALIGNED
SUCCESS, never a clean win; negative PnL discounts the score's
confidence even if the pattern was correct; the early-exit/continuation
weights rise only on an execution-shaped mismatch; "Did this rotation
make money?" is asked before "did it score well?".

## 1. THE RESTART (the running scanner's old memory image retired)

- stop_stack.ps1: 9 processes killed pass 1, TWICE-verified silence,
  watchdog disarmed.
- launch_stack.ps1: all five components UP — scanner/consoles/hindsight/
  volatility/bot pairs born 06:11 MDT, i.e. AFTER the mandate landed on
  disk (discipline.py, app.py plate, test_discipline.py pins).

## 2. THE PRE-RESTART DEFECT EXHIBIT (why this commit exists)

The 04:02 MDT row, written by the OLD code: score 95.9, realized
-$2.79, flagless — a Sniper-band score the money didn't confirm, graded
green by the old scorer. The plate's money-fallback now grades that row
amber (MISALIGNED SUCCESS).

## 3. THE LIVE PROOF — the first rollover the new process saw

Row written BY THE RESTARTED SCANNER at 08:01:00.907 MDT, verified
against the discipline state (the same source the plate/voice read):

```json
{"capped_zero_zero": false, "date": "2026-10-13", "empty_fire": false,
 "misaligned_success": false, "profit_negative": false,
 "realized_pnl": 3.99, "rot": "04-08", "score": 54.0,
 "score_confidence": "normal", "ts": "2026-10-13T08:01:00.907350-06:00"}
```

Checks (all true): flag_profit_negative_present, flag_confidence_present,
flag_misaligned_present → MANDATE VERIFIED LIVE. A profit-positive
rotation reads the correct inverse of the losing shapes.

## 4. THE HISTORY BACKFILL (the mandate on past rotations)

- 59/60 rows backfilled THROUGH discipline._pnl_flags (the canonical
  derivation, never a re-implementation), idempotent; saved via
  discipline.save_state (atomic tmp + os.replace) after backup
  logs/discipline_state_pre_backfill_20261013_081212.json.
- Re-read from disk: 60/60 rows carry all three flags — 0 missing,
  0 disagreements vs independent money recomputation.
- The mandate's failure shape existed in the real past, twice, and now
  speaks: rot 12-16 scored 94.1 with -$24.28 realized; rot 16-20 scored
  91.6 with -$16.77 realized — both now misaligned_success=true,
  confidence discounted, plate amber, winning-streak excluded.

## 5. CHECK RECEIPTS AT COMMIT TIME

- test_discipline.py 85/0 (the mandate section 6 pins, red-first)
- test_zerowatch.py 21/0
- full fleet gate 44/44 drives, 1152 checks, 0 regressions (exit 0)
- py-level syntax clean on app.py and discipline.py

## 6. CURED IN-AMEND (the commit gate's second opinions, Oct 13)

The commit-time reviews surfaced three findings; all three were cured
and re-pinned the same hour (pins 6j/6k/6l, red-first):

- CodeRabbit [minor] x2 (discipline.py): the flags judged the RAW score
  while the row stores the ROUNDED one — an 84.98 raw grading 85.0 on
  the row could disagree with its own misaligned_success flag. CURE:
  _row_score = round(score, 1); the result's "score" AND the _pnl_flags
  call both use it, so the row and its flags can never disagree.
- Copilot [medium] (app.py): GREEN was score-only — an 80-84 loss still
  rendered a green "SOLID" grade, and a LOSS was still a green chip in
  the history strip. CURE: the plate's green band now requires the
  money's agreement (s >= 80 and not _capped and not _pnl_neg);  an 80-84 loss grades "CAREFUL — high score, negative PnL: the money
  didn't confirm it" (amber); the strip colors any loss amber (a losing
  row is never a green chip). The streak already excluded losses.
- Copilot [medium], round 2 (app.py zero-PnL): GREEN counted $0 realized
  as money-confirmed — a >=80 score with nothing realized yet real leave
  on the table still rendered green SNIPER/SOLID, though the scorer's
  own agreement bar calls exactly that case UNPROVEN. CURE: GREEN now
  requires STRICTLY-POSITIVE realized money on the plate (and the
  strip chip); a zero-realized high score grades "UNPROVEN — nothing
  realized: the money hasn't confirmed the score" (amber), and the
  voice speaks "but unproven: nothing realized" instead of a trumpet.
  Pins 6m/6n (red-first), 6k/6l re-pointed to the cured shapes.
- Copilot [low], round 3 (discipline.py sub-cent): the result persists
  realized_pnl rounded to CENTS but _pnl_flags judged the UNROUNDED
  value - a loss rounding to $0.00 on the row could carry a
  profit-negative flag the row contradicts (the same row-flag
  inconsistency the row-score cure bought, on the money side).
  CURE: the flags judge the persisted money — _pnl_flags(_row_score,
  round(realized_pnl, 2), empty_fire). Pin 6o (red-first), 6d/6j
  re-pointed to the shipped call shape.
- Copilot [medium] x2, round 4 (app.py adjacent bands): (a) the streak
  still let a ZERO-realized >=70 uncapped row feed it — the exact UNPROVEN
  case the plate now grades; CURE: the streak earns on REALIZED money,
  strictly positive. (b) A 65-79 loss kept the amber color but kept the
  "SOLID — tight rotation" TEXT; CURE: that band now says
  "SOLID? — tight rotation, but the money lost" — the mandate applies
  to  the text, not just the color. Pins 6p/6q (red-first); the pre-
  existing streak-window grep widened again (assertions unchanged).
- Copilot [low] x2, round 5 (discipline.py lesson labels): (a) a
  giveback-dominant LOSING rotation routes to the giveback branch
  first, so its lesson never stated the mandate's verdict; CURE: the
  money-first verdict is appended there, graded by the same band rule
  the row's flag obeys. (b) The 80-84 loser's lesson claimed
  "MISALIGNED SUCCESS", a label the row flag and the plate award only
  at >=85; CURE: the lesson's lead now follows the row's own band rule
  (_lead: 85+ reads MISALIGNED SUCCESS, 80-84 reads PROFIT-NEGATIVE —
  not a misaligned SUCCESS until the Sniper band confirms it), on the
  burial/exec/pattern sub-lessons alike; a behavior pin (6s) pins the
  84-point loser, a shared-lead pin (6r) the label display, 6t the
  giveback verdict. The helm points at labels only — the weight logic
  was already band-correct.
