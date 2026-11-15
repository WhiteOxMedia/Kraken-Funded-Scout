# Simulating today's code over the last ten weeks (Nov 15)

**Question:** how much better would the Scout have done from Sep 3 to Nov 15 if it
had today's code? **Answer: +$2,069 over the window, 83% fewer fires, +4.3 points
of win rate, and it never comes near the death-line — but the gate's retained band
does not yet earn on its own.**

Only one thing shipped today changes behaviour: the **breakout gate** (a champion
fires only when its `entry_quality` ≥ 0.85; unmeasurable buys keep the old rule).
The near-miss recorder is shadow-only, and the entry and exit studies changed
nothing. So this is the gate's own counterfactual.

## Method

- 2,709 journal rows → **2,698 contest fires**: 4 test fixtures (`FAKE/USD`, size
  500 @ 100.0) and 7 rows from a spliced $5,000 account are removed; 91 balance
  stamps were written by competing writers and are ignored.
- `pnl_net` is verified to equal price math on **all 2,709** rows, so each fire's
  dollars are real. **2,495** fires are replayable on the 5m tape (203 fall outside
  it, all excluded from both arms).
- `entry_quality` is recomputed with the scanner's own `_entry_quality` on the
  hourly frame at the fill, and the gate's own `passes()` decides — the shipped
  rule, not a lookalike. 89 fires have <60h of history: the gate passes those, as
  it does live.

## The two arms

| | fires | win% | net | $/fire | worst fire | max DD | through −$300 cushion? |
|---|---|---|---|---|---|---|---|
| **yesterday's code** | 2,495 | 40.8 | **−$1,972.24** | −0.790 | −$431.45 | $2,416.74 | **yes — Oct 18 13:48**, bottoms Nov 14 |
| **today's code** | 435 | **45.1** | **+$96.66** | **+0.222** | **−$58.00** | **$380.31** | **never** |
| difference | −2,060 | +4.3 pts | **+$2,068.90** | +1.012 | +$373.45 | −$2,036 | — |

The gate refused 82.6% of fires, which booked **−$2,068.90** (−$1.004 each). It
kept **−$4,604.84** of their losses and gave back **+$2,535.94** of their wins.

## By month

| month | fires | win% | net | delta |
|---|---|---|---|---|
| Sep | 112 → 84 | 57.1 → 59.5 | +$251.15 → **+$324.60** | +$73.45 |
| Oct | 1,779 → 265 | 40.2 → 41.9 | −$1,298.55 → **−$68.18** | **+$1,230.37** |
| Nov | 604 → 86 | 39.2 → 40.7 | −$924.84 → **−$159.76** | +$765.08 |

October is the whole story: 1,779 fires and −$1,299 became 265 fires and −$68.

## The mechanism — and the honest part

Booked $ per fire by the champion's own `entry_quality`:

| band | n | win% | net | $/fire |
|---|---|---|---|---|
| 0.00–0.20 | 1,242 | 39.0 | −$1,755.57 | **−1.414** |
| 0.20–0.40 | 183 | 35.0 | −$41.86 | −0.229 |
| 0.40–0.60 | 149 | 43.6 | +$14.36 | +0.096 |
| 0.60–0.75 | 252 | 46.8 | −$139.25 | −0.553 |
| 0.75–0.85 | 234 | 38.5 | −$146.58 | −0.626 |
| 0.85–0.95 | 140 | 44.3 | −$179.54 | **−1.282** |
| 0.95–1.01 | 206 | 45.1 | −$27.04 | −0.131 |
| unmeasured | 89 | 46.1 | **+$303.24** | **+3.407** |

**The bottom band is the disaster** (1,242 fires, −$1,755 — 89% of the total loss),
which is what the gate removes. But the **kept 0.85+ band is itself −$207** over
346 fires, and the positive window rides on the 89 unmeasured fires (+$303). Without
the unmeasured pass-through today's code would book **−$206.58** with a $415 DD.

Threshold sensitivity (in-sample for every row here; the shipped 0.85 was chosen on
its own fit/holdout):

| min | fires | net | win% | max DD |
|---|---|---|---|---|
| 0.60 | 921 | −$189.17 | 43.9 | $688 |
| 0.75 | 669 | −$49.92 | 42.8 | $440 |
| 0.80 | 543 | −$54.06 | 43.5 | $476 |
| **0.85** | **435** | **+$96.66** | **45.1** | **$380** |
| 0.90 | 372 | +$123.34 | 44.6 | $420 |
| 0.95 | 295 | +$276.20 | 45.4 | $401 |

## What this says for the mission

- **Death-line: materially solved by today's change.** Yesterday's curve falls
  through the entire $300 cushion on Oct 18 and ends −$1,972; today's never
  crosses it, ends +$97, and its worst single fire is −$58 instead of −$431.
- **+80% win rate: not reached** (45.1%). The gate raises the average by removing
  the toxic band, not by finding a winning one.
- **Next lever, stated by the numbers:** the unmeasured pass-through is the only
  profitable bucket (+$303 on 89 fires) — either those coins deserve real
  measurement or they deserve a veto, and that is a testable question. The 0.95
  band is near break-even at −$0.13/fire versus −$1.28 at 0.85–0.95, which is what
  the near-miss recorder can now settle out of sample with hundreds of rows.

## Caveats

1. **Counterfactual on realized fires, not a re-run.** When the gate vetoes, the
   loop fires nothing; it does not pick a replacement. A veto that frees capital
   for a better coin is invisible here, and so is a veto that frees capital for a
   worse one.
2. **Sizes are as booked**, so the dollars do not compound through the smaller arm;
   they are the same fills judged twice.
3. **The whole window is in-sample** for the gate study (its fit and holdout both
   live inside Sep–Nov), so +$2,069 is a description of what the rule would have
   done, not a forecast of what it will do.
4. **The journal's balance stamps are not trustworthy** (91 competing writes, a
   spliced $5k account), so the death-line is read from the booked-P&L curve — the
   one series that reconciles — not from `balance_after`.

*Script: `scratch/gate_counterfactual.py`; output: `scratch/_gate_counterfactual.json`.
Gate study: `BREAKOUT_GATE.md`. Entry/exit verdicts: `ENTRY_TIMING_GRADE.md`, `EXIT_VARIANTS.md`.*
