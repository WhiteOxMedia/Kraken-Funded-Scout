# Where the entry edge goes — and why no timing gate ships yet

Measured on the only history there is: 2,706 ledger closes, 1,082 of them
walkable on the 5m bar store (29 majors, Sep 3 – Nov 12), reconstructed with
`scratch/entry_timing_diagnosis.py`. Every entry feature uses bars *before* the
entry, every exit feature bars *during* the hold — no lookahead.

## 1. The three candidate leaks, separated

| | n | win | payoff | avg win | avg loss | total | expectancy |
|---|---|---|---|---|---|---|---|
| whole book | 1,082 | 40.0% | 0.94 | $3.69 | −$3.92 | **−$948.52** | −$0.88 |

**Entry-side: buying high.** Position of the fill inside the prior hour's range
(0 = the hour's low, 1 = its high):

| eq | n | win | payoff | travel paid | expectancy |
|---|---|---|---|---|---|
| 0.0–0.2 | 377 | 47.5% | 1.00 | $0.91 | −$0.17 |
| 0.2–0.4 | 226 | 32.3% | 0.67 | $3.95 | −$1.88 |
| 0.4–0.6 | 161 | 36.0% | 0.61 | $5.25 | −$2.14 |
| 0.6–0.8 | 160 | 37.5% | 1.25 | $6.24 | −$0.48 |
| 0.8–1.0 | 158 | 39.9% | **1.37** | $10.76 | −$0.25 |

Dollars paid above the prior low rise 12× from the cheapest to the most
stretched bucket ($0.91 → $10.76), so the book *is* paying up — but the payoff
rises with it. The stretched fifth is the second-best bucket by expectancy and
the best by payoff. **Buying high is not what kills the book.**

**Exit-side: giving the gain back.** 201 rows reached +1% or better. They booked
+$1,007.90 *between them* (win 84.6%, payoff 3.16) while handing back 1.13 pp
from their peak (55% of them booked more than a point under it). The 628 rows
that never reached +0.5% lost **−$1,981.07** (win 17.0%).

**Frequency.** 1,082 closes over 70.4 days = **15.4/day**, median hold 18 min.
The book trades plenty; the leak is not scarcity of trades.

## 2. The verdict

The loss is concentrated in the 58% of entries that never move (MFE < +0.5%),
not in the price paid for them and not primarily in the exit. Paying away
$4,778 on entry and handing back $4,617 after peaks are both real costs, but the
runners cover them: the 201 rows that ran paid +$1,008 net of a full stop-out
rate elsewhere. Roughly: *the entry is fine, but too many entries are into
assets that never continue.*

## 3. Why no timing gate ships

The obvious rule — only buy inside the cheapest part of the hour's range — was
chosen **on the tuning half only** (first 70% by close time, Sep 3 – Oct 22,
n=757) and tested on the held-out tail (Oct 22 – Nov 12, n=325), never shuffled:

| rule | slice | n | win | payoff | total | expectancy |
|---|---|---|---|---|---|---|
| current | tuning | 757 | 41.5% | 1.15 | −$296.06 | −$0.39 |
| eq ≤ 0.2 | tuning (chosen) | 242 | — | — | — | **+$0.59** |
| current | **held out** | 325 | 36.6% | 0.51 | −$652.46 | −$2.01 |
| eq ≤ 0.2 | **held out** | 135 | 43.7% | 0.48 | −$207.34 | **−$1.54** |

The gate lifts the held-out win rate (+7.1 pp) and cuts the loss by $445, but it
does that by trading 58% less, not by picking better: its payoff is unchanged
(0.48 vs 0.51) and its expectancy flips from +0.59 in tuning to −1.54 out of
sample. That is a fitted rule, not a found one. **It is rejected.**

This also corrects the earlier note in `test_timing.py` (a 1,106-entry study
that read the cheapest fifth at 52% in both halves): on the closes it is 47.5%,
and the cheap-entry advantage does not survive a time split.

## 4. What ships instead: the measurement

`_entry_timing_stamp()` fires with every tracked entry and writes:

| field | meaning |
|---|---|
| `timing_eq` | fill position inside the prior hour's 5m range, 0 = low, 1 = high |
| `timing_above_low_pct` | how far above that hour's low the fire paid |
| `timing_above_day_low_pct` | how far above the prior 24h low it paid |
| `timing_bars` | bars actually behind the stamp (diagnostic) |

and at the close the row carries those plus `mfe_pct` (its own running high vs
entry). One OHLCV read per fire; fail-soft (no exchange or a broken read leaves
the row unstamped and still tracking). `test_entrystamp.py`: 20 checks.

## 5. What the data needs

A +5 pp win-rate shift from 40% at 80% power needs **1,531 closes per arm**
(≈3,062 total, ≈199 days at the current 15.4/day). A coarser +8 pp shift needs
**601 per arm** (≈1,202 total, ≈78 days). The held-out tail today is 325 — about
a fifth of what a +5 pp answer requires, which is exactly why the gate above was
not shipped.

Until then: **no win-rate improvement can be claimed, and +80% remains unmet.**
