# Simulating today's code over ten weeks — and why the kept fires lose (Nov 15)

**Headline, corrected and out of sample.** With the unmeasured leak closed, today's
gate books **−$197.27** over Sep 3 – Nov 15 at **45.1%** wins, against **−$1,972.24**
at 40.8% without it — it avoids the death-line and cuts the worst fire from −$431
to −$58, but it is **not profitable**. The kept fires lose for one measurable
reason: **the exit gives back a gain that was there** (winners capture only 42% of
their peak), not the entry timing — and a +2% take-profit on those fires turns the
window to ≈break-even, but **November is still −$1.13, so live stays unchanged.**

Only one thing shipped today changes behaviour: the **breakout gate** (`entry_quality`
≥ 0.85). The near-miss recorder is shadow-only; the entry and exit studies changed
nothing.

## The gate, all fires measured (no unmeasured pass-through)

61 fires can't be scored from repo tape (history begins within two hours of the
fill) and are vetoed with that reason; the script prints `unmeasured pass-through: 0`.

| | fires | win% | net | $/fire | worst fire | max DD | through −$300? |
|---|---|---|---|---|---|---|---|
| yesterday's code | 2,495 | 40.8 | −$1,972.24 | −0.790 | −$431.45 | $2,416.74 | **yes, Oct 18** |
| today's gate | 350 | 45.1 | −$197.27 | −0.564 | −$58.00 | $405.55 | never |
| difference | −2,145 | +4.3 pts | **+$1,774.97** | +0.226 | +$373 | — | — |

Monthly: Sep +$251 → +$88, Oct −$1,299 → −$125, Nov −$925 → −$160. No threshold
recovers the edge (0.60 → −$496, 0.95 → −$23); that is settled and not revisited here.

## Why the kept fires lose: extended entry, or exit giveback?

350 kept fires (158 winners / 192 losers), features from the tape only.

**What was knowable at the fill does not separate them.**

| pre-entry feature | winners | losers | diff | 95% CI | d |
|---|---|---|---|---|---|
| fill over 12h low | +1.49% | +1.32% | +0.16 | −0.04…+0.36 | +0.17 |
| fill vs 12h high | −0.51% | −0.46% | −0.04 | −0.15…+0.06 | −0.09 |
| **bar-to-bar extension** | −0.12% | −0.00% | **−0.12** | **−0.19…−0.05** | **−0.38** |
| share of the hour move done | 0.74 | 0.72 | +0.02 | −0.03…+0.06 | +0.09 |
| run-up 3h | +1.46% | +1.52% | −0.06 | −0.28…+0.17 | −0.05 |
| run-up 6h | +2.04% | +1.96% | +0.07 | −0.21…+0.35 | +0.05 |
| run-up 12h | +3.20% | +3.12% | +0.09 | −0.33…+0.51 | +0.04 |
| position in 12h range | 0.89 | 0.89 | −0.00 | −0.02…+0.01 | −0.04 |

The only real signal is a 0.12-point one: winners entered on a slightly **negative**
bar (−0.12%) and losers on a flat one (0.00%). Run-up, distance from the low,
distance from the high and "how much of the move is spent" are **all d < 0.2**.
**Buying extended is not what separates the outcomes** — the gate has already
selected for extension, so that axis is used up.

**What happens after the fill separates them strongly.**

| post-entry | winners | losers | diff | 95% CI | d |
|---|---|---|---|---|---|
| peak gain (MFE) | **+2.13%** | **+0.89%** | +1.24 | +0.94…+1.53 | **+0.89** |
| peak-to-exit giveback | +1.31% | +1.43% | −0.13 | −0.38…+0.12 | −0.11 |
| share of peak captured | 0.42 | −1.40 | +1.82 | +1.47…+2.17 | **+1.07** |

The loss splits exactly in half: **96 losers never got 0.5% up (−$396.33)** and
**96 losers were up (peak +1.60% on average) and gave it back (−$397.81)**. Winners
keep only **42%** of a +2.13% peak. The gate picks coins that are moving; the exit
does not collect the move.

## The one candidate change: take the gain at +2%

Same engine for every column (shipped stop −3%, trail, 180m), so the comparison is
exit-vs-exit; "baseline booked" is what the journal actually paid.

| variant | fires | win% | net | $/fire | worst | max DD |
|---|---|---|---|---|---|---|
| baseline booked | 350 | 45.1 | −$197.27 | −0.564 | −$58.00 | $405.55 |
| sim, no target | 350 | 44.9 | −$26.30 | −0.075 | −$3.09 | $46.58 |
| **take-profit +2%** | 350 | **46.3** | **+$2.19** | +0.006 | −$3.09 | $46.61 |
| take-profit +3% | 350 | 45.1 | −$3.02 | −0.009 | −$3.09 | $46.24 |
| take-profit +5% | 350 | 44.9 | −$17.48 | −0.050 | −$3.09 | $38.82 |

The extended-entry **veto** candidates the evidence does not support all stay
negative on the same window: bar_ext > 0.5% → −$181, run-up 3h > 3% → −$116 (best
veto), run-up 3h > 5% → −$194, fill > 2% over the low → −$131, "move done" > 0.95
→ −$167. Consistent with the effect sizes above: there is nothing at the entry to
refuse that separates winners from losers.

## Out of sample: chosen on Sep–Oct, scored on November

| variant | train n | train $ | Nov n | **Nov $** | Nov win% | Nov DD |
|---|---|---|---|---|---|---|
| baseline booked | 264 | −$37.51 | 86 | **−$159.76** | 40.7 | $173.09 |
| sim, no target | 264 | −$13.82 | 86 | **−$12.48** | 41.9 | $31.86 |
| **take-profit +2%** | 264 | **+$3.32** | 86 | **−$1.13** | **43.0** | $40.02 |

**It does not clear the bar.** November is **−$1.13 on 86 fires (−$0.013/fire)** —
statistically zero, not positive. What the split does show is that the improvement
is real and large relative to the shipped exit: **−$12.48 → −$1.13 on the same
engine, and −$159.76 → −$1.13 against what was actually booked**, with the drawdown
falling from $173 (booked) and $32 (sim) to $40. But zero is zero: no live change.

## The binding constraint, named

**After a 0.85+ breakout entry, the remaining move is small.** Losers peak at
+0.89% on average — under the round trip plus any worthwhile target — and the
winners' +2.13% peak is kept at 42%. So the entry is paying for a move that is
mostly already spent, and no entry-time feature available distinguishes the coins
that continue from the ones that stall (all d ≤ 0.38, most ≈ 0.05). The achievable
ceiling with this entry is **expectancy ≈ 0** (November −$0.013/fire) at a **43–46%
win rate**; the +2% target is what removes the giveback, and it can only get to
break-even because half the fires never offer the target at all.

For +80%, the entry has to find coins *before* the move, or hold for a move larger
than the typical remaining +1% — that is a different generator, not a threshold,
and the near-miss recorder is the instrument with which to look for it.

## Live is unchanged

No gate, threshold, veto or exit change in the running scanner. The take-profit is
the best candidate measured so far and it is still **−$1.13 in November**, so
shipping it would be shipping a zero. It is recorded here as the leading candidate
for the next test, with the recorder accumulating exactly the rows needed to judge
it (fires that reach +2% and what they would have done next).

## Caveats

1. Counterfactual on realized fires; a veto fires nothing, replacements invisible.
2. Sizes as booked; dollars do not compound through a smaller arm.
3. The tape-simulated exit is more favourable than the booked one (−$26 vs −$197),
   so every variant is compared against `sim, no target` as well as the journal.
4. The journal's balance stamps are unreliable; drawdown is read from the
   booked-P&L curve.

*Scripts: `scratch/gate_counterfactual.py`, `scratch/kept_diagnosis.py`.
Artifacts: `scratch/_gate_counterfactual.json`, `scratch/_kept_diagnosis.json`.
Gate study: `BREAKOUT_GATE.md`. Entry/exit: `ENTRY_TIMING_GRADE.md`, `EXIT_VARIANTS.md`.*
