# Simulating today's code over ten weeks — and why the kept fires lose (Nov 15)

**Headline — the mission's state, decided.** Today's gate books **−$197.27** over
Sep 3 – Nov 15 at **45.1%** wins (vs −$1,972.24 at 40.8% ungated): it avoids the
death-line and cuts the worst fire from −$431 to −$58, but it is **not profitable**.
**The entry side is closed** — extension, participation, compression and structure
all fail to separate winners from losers (no feature reaches |d| ≥ 0.5). **The exit
side is decided: the +2% take-profit is REJECTED.** It is the best change measured
(booked −$197.27 → +$2.19, drawdown $406 → $47) and it *is* the reason the kept
fires lose — winners capture only 42% of a +2.13% peak — but November is **−$1.13**
over 86 fires, not positive, so the agreed bar is not met, and the live recorder
holds **1** fire that reached +2% against the **65** the effect would need.

**The ceiling.** With this generator and this tape the reachable win rate is
**43–46%** (46.3% in-sample with the best exit, 43.0% in November) at expectancy
≈0. +80% is not reachable by any entry rule or exit tweak on these fires; it needs
a different generator or a new signal source.

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

## The entry side is closed — every untested axis is noise

Participation, compression and pullback structure, computed strictly from bars
before the fill, on the same kept fires (349 with a full pre-entry frame; volume
exists for 205 of them — the aligned tape carries no spot volume for the alts).

| axis | feature | n (w/l) | winners | losers | diff | 95% CI | d |
|---|---|---|---|---|---|---|---|
| participation | volume vs own baseline (last hour) | 85/120 | 2.67× | 3.77× | −1.11 | −2.41…+0.20 | **−0.21** |
| participation | volume trend 3h vs prior 3h | 85/120 | 2.33× | 2.69× | −0.36 | −1.69…+0.97 | −0.07 |
| participation | 6h volume vs own 6h baseline | 85/120 | 1.72× | 1.56× | +0.16 | −0.24…+0.55 | +0.11 |
| participation | 24h volume vs prior 24h | 85/120 | 1.20× | 1.52× | −0.32 | −0.73…+0.08 | −0.20 |
| compression | 6h range width (%) | 158/191 | 3.39 | 3.29 | +0.10 | −0.20…+0.40 | +0.07 |
| compression | 6h range / 24h range | 158/191 | 0.59 | 0.56 | +0.03 | −0.01…+0.07 | +0.14 |
| compression | hours coiled (range < half norm) | 158/191 | 0.55 | 0.62 | −0.07 | −0.28…+0.14 | −0.07 |
| compression | 6h range vs 48h range | 158/191 | 1.01 | 0.99 | +0.02 | −0.04…+0.08 | +0.07 |
| structure | dip from 12h high to 3h low (%) | 158/191 | 2.64 | 2.57 | +0.08 | −0.18…+0.33 | +0.06 |
| structure | fill's recovery within that dip | 158/191 | 0.80 | 0.79 | +0.01 | −0.02…+0.05 | +0.08 |
| structure | first break of the prior high | 158/191 | 0.27 | 0.32 | −0.06 | −0.16…+0.04 | −0.13 |
| structure | distinct re-breaks in 6h | 158/191 | 0.96 | 0.85 | +0.11 | −0.01…+0.24 | +0.19 |
| structure | hours since the 24h high | 158/191 | 6.01 | 5.62 | +0.39 | −1.45…+2.24 | +0.04 |

**Nothing clears a real effect size: `|d| ≥ 0.5` with a CI excluding zero — none do.**
The largest new-axis effect is volume expansion at the fire (d = −0.21, CI spanning
zero) and it points the *wrong* way: losers fired on bigger volume expansion (3.77×
baseline) than winners (2.67×). Coiling, range compression, dip-and-recover
structure, first-break vs re-break, hours since the high: all |d| ≤ 0.20.

No candidate variant exists on this side, so none was run and **nothing went live**.
Volume is also only 59% covered on the tape, so even a signal here would have needed
new data before it could gate anything.

## The 80% timing search: 216 shapes, every one negative

The user rule — buy the dip inside an uptrend, get out fast so most trades win a
little — was built and searched directly on the 5m tape: 55 symbols, Sep 3 – Nov 15,
0.080% round trip, dips of 2–3 bars, trend filters (4h and 24h means, 24h momentum,
deep-dip), targets 0.15–0.50%, stops 0.6–2.0%, timeouts 6–24 candles.

**80% is reachable, and it loses.** The best 80% shape (`momentum24, dip 2,
target 0.15%, stop 2.0%, 24 candles`) wins **80.5% of 6,046 trades** and books
**−$726.90** (−$0.120/trade). The best net of all 216 shapes is **−$514.40** over
4,365 trades at 65.3% wins. **Zero** shapes are positive on the train months;
**zero** are positive in November.

That is arithmetic, not bad luck. A win rate `p` with target `T` and stop `S` breaks
even when `p·T = (1−p)·S`, so 80% needs `S = 4T` — four units of loss per one of
gain. On a move with no directional edge the barrier-touch order is a martingale,
which makes that shape break even *before* costs on average; the 0.080% round trip
is then paid on every trade. **A win rate is a shape; an edge is a signal.** The
same identity sizes every row of the table below:

| stop | break-even target at 80% wins | after the 0.080% cost |
|---|---|---|
| 0.6% | 0.150% | +0.070% |
| 1.0% | 0.250% | +0.170% |
| 1.5% | 0.375% | +0.295% |
| 2.0% | 0.500% | +0.420% |

Every measured shape landed on the wrong side of that line because the entry has no
measured directional edge — the same conclusion the closed entry axes reached, now
confirmed from the other direction (by what it costs to win 80% rather than by what
predicts a winner).

**What was built:** `overwatch_timing.py` — the engine (dip entry, fast target,
tight stop, candle timeout) as pure functions with `ARMED = False` and the measured
rejection stored beside the switch, `should_arm()` reporting why, and
`breakeven_target()` / `required_stop()` so the geometry is never guessed again.
Covered by `test_overwatch_timing.py` (16 checks). **Nothing was armed:** at
−$0.12/trade it would spend the $300 cushion in days.

## The pre-entry signal search: 11 order-flow signals, nothing clears November

The 80% finding left one question: is there *any* pre-entry signal in the repo's
own data that predicts the next 30–60 minutes by more than the 0.08% round trip?
`scratch/orderflow_study.py` answers it on the only substrate with order-flow
content — `scratch/kraken_5m_long_183d.json`, **30 pairs, 1,544,445 5m bars, May 14
– Nov 13, volume present on 100% of bars** (the shorter 55-symbol tape used earlier
has zero volume on most alt bars, which is why it could never test this).

**Four of the five families the request names do not exist in the repo.** A scan of
every `.json`/`.jsonl`/`.db`/`.sqlite` in the tree (excluding `.venv`, `.git`,
`backups`) for any field matching funding / open interest / spread / bid / ask /
imbalance / depth / order book / liquidity found **0 files**: `execution_tiers.measure_book()`
reads a live book to price an order and throws it away, `app.py`'s `SPREAD` is the
modelled 10bps constant, and `config.max_spread_pct` is a live filter. There is no
history of those to test — the only thing that can be backtested is what candles
and volume carry, and that is exactly what "order flow" means here.

Eleven pre-entry signals, all computable from bars ≤ t: signed-volume imbalance
`ofi3`/`ofi12` (Σ(2·CLV−1)·vol / Σvol — the standard order-flow-imbalance proxy),
`vdelta12` (up-volume minus down-volume share), `volz` (volume vs its 24h sd),
`absorb` (high volume, small body), `dipvol` (capitulation volume), `wick3` (buying
tail), `vwapdev`, `rangex`, and `ret6`/`ret12` as momentum baselines. Quintile edges
are fitted on **May–Oct only** and applied unchanged to **Nov 1–13 (105,328 bars)**;
entries are strided by the horizon so t-stats are not inflated by overlap.

**Nothing clears.** Every quintile of every signal nets −0.07% to −0.19% at both
horizons, train and holdout alike. The decisive number is not that level (November
was a falling window, so all longs pay a drift) but the **separation**: top-minus-
bottom quintile is **≤0.03% and flips sign between train and November** for every
signal, including the two the request cares about most — high-volume `volz`
(top quintile 38.7% wins, −0.12%/30min) and `absorb` (39.2% wins, −0.12%). A signal
whose best quintile is indistinguishable from its worst cannot pay 0.08%.

Two features had to be repaired before their verdict was trustworthy, and both
repairs are in the script: `volz` divided by a near-zero volume sd and produced
~1e12 values that owned the entire top quintile (n=1 testable bar); it is now
undefined where volume does not vary, which restored ~19,000 holdout bars to the
high-volume bucket — where it still loses. `dipvol` with zero up-volume is likewise
undefined rather than infinite.

Because **nothing cleared, nothing was wired**: `overwatch_timing.ARMED` is still
`False` and the search is recorded inside its `STUDY['orderflow_search']` block,
with `should_arm()` now reporting both rejections in one line. An edge here would
have to come from data the repo does not collect (funding, open interest, live
book/depth), which cannot be backtested on history that was never written down.

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

## The +2% take-profit: rejected on the agreed bar

All three arms over the same 350 kept fires; `no_target` is the identical exit
engine without the target, `baseline` is what the journal actually paid.

| whole window | fires | win% | net | $/fire | worst | max DD |
|---|---|---|---|---|---|---|
| baseline booked | 350 | 45.1 | −$197.27 | −0.564 | −$58.00 | $405.55 |
| no-target sim | 350 | 44.9 | −$26.30 | −0.075 | −$3.09 | $46.58 |
| **take-profit +2%** | 350 | **46.3** | **+$2.19** | +0.006 | −$3.09 | $46.61 |

| **November (out of sample)** | fires | win% | net | $/fire | worst | max DD |
|---|---|---|---|---|---|---|
| baseline booked | 86 | 40.7 | −$159.76 | −1.858 | −$58.00 | $173.09 |
| no-target sim | 86 | 41.9 | −$12.48 | −0.145 | −$3.09 | $31.86 |
| **take-profit +2%** | 86 | **43.0** | **−$1.13** | −0.013 | −$3.09 | $40.02 |

Train Sep–Oct: baseline −$37.51, no-target −$13.82, candidate **+$3.32** (47.3%).
The take-profit is worth **+$11.35 in November** over the same engine and has not
lost money in a single month, but it has not *made* money in the one that counts:
November is −$1.13, which is zero, not positive.

**Sample.** Per-fire November effect: mean **+$0.132**, sd **$0.539**, so **65
fires** are needed for that effect to clear a 95% CI. The live near-miss recorder
holds **52 rows (11 written by live cycles), 9 resolved, 9 with a peak path, and
1 that reached +2%** — one against sixty-five. The recorder now stores `peak_pct`
and `reached_2pct` on every resolved row (and backfilled the nine older ones),
which is the measurement the decision needs; it did not have it before today.

**Decision: REJECTED — not shipped.** November is not positive and the live sample
is too small to overturn that. Required before it can be reconsidered: **65 fires
that reached +2% inside the hold window** (currently 1). At the current near-miss
rate that is roughly 300+ resolved rows.

## Live is unchanged, and why

No gate, threshold, veto or exit change in the running scanner; the only live-path
touch is the recorder storing the peak (shadow, no order, no balance). Shipping the
take-profit would be shipping a measured zero on a 1-fire live sample.

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
