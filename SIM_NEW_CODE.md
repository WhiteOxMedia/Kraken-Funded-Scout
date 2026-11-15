# Simulating today's code over the last ten weeks — corrected (Nov 15)

**Corrected result: with the unmeasured leak closed, today's gate books −$197.27
over Sep 3 – Nov 15 at 45.1% wins, against −$1,972.24 at 40.8% without it. The
gate is a large loss-reducer and death-line avoider; it is not an edge, and the
earlier +$96.66 was the unexamined bucket, not the rule.**

Only one thing shipped today changes behaviour: the **breakout gate** (a champion
fires only when its `entry_quality` ≥ 0.85). The near-miss recorder is shadow-only,
and the entry and exit studies changed nothing.

## The hole, and how it was closed

The previous run passed 89 fires the gate could not measure (the live rule does
this by design: an unmeasurable buy keeps the old behaviour). Those 89 booked
**+$303** and were the entire positive result.

- The study had required 60 hourly bars before computing `entry_quality`; the
  scanner's real requirement is `TIMING_RANGE_CANDLES + 1` = **13**. Using the
  scanner's own rule made **28** of those fires measurable (4 of them pass 0.85).
- The remaining **61** genuinely cannot be scored from data in this repo: their
  tape begins within two hours of the fill, so no prior hour exists to read.
  They are now **vetoed with a stated reason** — "no prior-hour history on the
  repo tape" — not passed through.
- The script prints **`unmeasured pass-through: 0`**.

Those 61 vetoed fires booked **+$310.69 at 49.2% wins ($5.09/fire)**. That money
was real, and it was unexamined — which is exactly why it cannot be counted as
evidence for the gate.

## The two arms, measured only

Ledger: 2,709 journal rows → 2,698 contest fires (4 test fixtures, 7 rows from a
spliced $5,000 account; 91 competing balance stamps ignored). 2,495 replay on the
5m tape; 61 of those are vetoed as unmeasurable.

| | fires | win% | net | $/fire | worst fire | max DD | through −$300? |
|---|---|---|---|---|---|---|---|
| **yesterday's code** | 2,495 | 40.8 | **−$1,972.24** | −0.790 | −$431.45 | $2,416.74 | **yes, Oct 18 13:48** |
| **today's code** | 350 | **45.1** | **−$197.27** | −0.564 | **−$58.00** | **$405.55** | **never** |
| difference | −2,145 | +4.3 pts | **+$1,774.97** | +0.226 | +$373 | — | — |

The gate refused 86.0% of fires, which booked **−$1,774.97** (−$0.827 each): it
kept **−$4,688.66** of their losses and gave back **+$2,913.69** of their wins.

| month | fires | win% | net | delta |
|---|---|---|---|---|
| Sep | 112 → 64 | 57.1 → 57.8 | +$251.15 → +$87.70 | −$163.45 |
| Oct | 1,779 → 200 | 40.2 → 43.0 | −$1,298.55 → **−$125.21** | **+$1,173.34** |
| Nov | 604 → 86 | 39.2 → 40.7 | −$924.84 → −$159.76 | +$765.08 |

September loses money to the gate (−$163): it was the one month where the
low-quality fires were still profitable. October carries the whole benefit.

## Where the money is (every fire measured)

| band | n | win% | net | $/fire |
|---|---|---|---|---|
| 0.00–0.20 | 1,260 | 38.7 | −$1,761.36 | **−1.398** |
| 0.20–0.40 | 184 | 35.3 | −$40.27 | −0.219 |
| 0.40–0.60 | 149 | 43.6 | **+$14.36** | +0.096 |
| 0.60–0.75 | 255 | 47.1 | −$144.87 | −0.568 |
| 0.75–0.85 | 236 | 38.6 | −$153.52 | −0.651 |
| 0.85–0.95 | 143 | 44.8 | −$173.87 | **−1.216** |
| 0.95–1.01 | 207 | 45.4 | −$23.40 | −0.113 |

The gate removes the band that holds 89% of the total loss, but **every kept band
is still negative**. The 0.85–0.95 band is the worst of the kept ones.

## Out-of-sample: Sep–Oct → November

Threshold picked on Sep–Oct, scored on the November it never saw (604 fires):

| min | train n | train $ | train win% | Nov n | Nov $ | Nov win% | Nov DD |
|---|---|---|---|---|---|---|---|
| 0.60 | 624 | −$258.35 | 43.8 | 217 | −$237.31 | 44.2 | $254 |
| 0.75 | 424 | −$145.26 | 43.4 | 162 | −$205.53 | 40.1 | $217 |
| **0.85** | 264 | −$37.51 | 46.6 | **86** | **−$159.76** | 40.7 | $173 |
| 0.90 | 209 | −$11.63 | 45.9 | 76 | −$161.80 | 40.8 | $173 |
| 0.95 | 150 | **+$33.39** | 46.7 | 57 | −$56.79 | **42.1** | **$92** |
| off | — | — | — | 604 | −$924.84 | 39.2 | — |

**The edge does not survive, plainly.** The best threshold on the training months
(0.95, the only positive one) loses **−$56.79 on 57 November fires** at 42.1% wins.
No threshold is profitable in November. What does survive, at every threshold, is
the loss reduction: −$925 ungated → −$57 to −$238 gated, with the drawdown falling
from $2,417 to $92–$254.

**The win-rate ceiling is 45–46% in-sample and 40–42% out of sample** across the
whole threshold range. Nothing here approaches 80%, and the numbers say the +80%
target cannot come from this gate at any setting.

## Live is unchanged, deliberately

No threshold change, no veto-the-unmeasured change in the running scanner. The
out-of-sample result gives no support for one: 0.95 is better on both dollars and
drawdown in November, but on 57 fires, and it still loses. Moving the threshold
to it would be tuning on a sample that thin. The gate's live job — refuse provably
mislocated fires, never invent one — stays exactly as shipped.

Deciding the unmeasured question: in the **measurement** they are vetoed (an
unexamined fire cannot count as evidence); in **live** they still pass, because the
only evidence available is a study artefact of where the tape starts. The
near-miss recorder is the instrument that will replace that judgement with rows.

## Caveats

1. Counterfactual on realized fires: a veto fires nothing, so replacement trades
   are invisible in both directions.
2. Sizes are as booked, so dollars do not compound through the smaller arm.
3. The window is in-sample for the gate study; the Sep–Oct → Nov split is the only
   out-of-sample check here.
4. The journal's balance stamps are unreliable (91 competing writes, a spliced $5k
   account), so the death-line is read from the booked-P&L curve.

*Script: `scratch/gate_counterfactual.py`; artifact: `scratch/_gate_counterfactual.json`.
Gate study: `BREAKOUT_GATE.md`. Entry/exit: `ENTRY_TIMING_GRADE.md`, `EXIT_VARIANTS.md`.*
