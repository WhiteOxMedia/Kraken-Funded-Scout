# Two fee gates, both falsified — do not re-test

The fee analysis pointed at two candidate gates: require the stated goal to clear
at least twice the round-trip fee, and refuse to book an exit inside a band around
entry. Both were tested with the ledger split into halves by time (first half
selects, second half measures). **Neither improves expectancy, and both fail for
the same structural reason: the fee is invariant to where the exit is booked.**

## 1. Goal vs fee gate — vacuous at 2×, harmful when tightened

Round trip is 0.08%, so "goal ≥ 2× fee" is 0.16%. Across 2,706 closes the
**smallest goal ever fired is 1.00% = 12.5× the fee** (p5 1.00%, median 1.89% =
23.6×). The 2× gate, and every threshold up to 10× (0.80%), **excludes zero rows**.
A gate that filters nothing cannot change expectancy.

Tightening until it bites makes it worse. Train expectancy rises monotonically as
the threshold tightens (a small-sample fit), peaking at 30× fee (2.40% goal) with
+$0.55/trade on 301 train rows. Applied to the second half: **−$3.23/trade against
a −$1.77 baseline** — it selects the worse half of the book.

## 2. Exit-band gate — the premise is true, the remedy is not

Two ways of choosing the band, same result.

**Chosen to maximise train expectancy:** ±0.30%, test **+$0.02/trade** (baseline
−$1.53), bootstrap 95% CI **[−$0.60, +$0.67]**. At ±0.50% and ±0.75% the train
half loses (−$0.53, −$0.80) while the test half gains (+$0.38, +$0.40) — the
halves disagree on sign.

**Chosen by value destruction** (widest band whose inside-band train closes have a
negative mean, per the fee finding): ±0.75%, where train inside-band closes average
−$0.08 and test −$0.39. Refusing those exits gives test **−$1.13/trade vs −$1.53**
(+$0.40), per-deferred-row effect **+$0.52 with CI [−$0.46, +$1.50]**, 77% of
closes deferred, median hold 75 minutes, worst single deferral −$76. Split inside
the test half it flips: +$1.80 early, −$1.00 late.

So the premise is real — exits booked within a fraction of a percent of entry do
destroy value — but refusing to book them only re-rolls the exit price.

## Why both fail: the fee does not depend on the exit price

The round trip is 0.08% of notional, charged once per trade (`cost_rt`). It is paid
whether the exit lands at +0.01% or at the band edge, and whether the goal was 1%
or 3%. Both gates were attempts to recover that cost by choosing a *better exit
price*:

- the deferred rows actually booked a mean move of **−0.002%**; the replay booked
  them at **+0.011%**, a **1.3 bp** improvement against an **8 bp** fee — a ceiling
  of 16% of the cost, before any statistical test;
- the re-roll is symmetric: 51% of deferred rows improved, 49% worsened;
- holding changes exposure, not cost. The only things that change the arithmetic
  are not taking the trade, taking a bigger move per trade, or paying less to trade.

## What this rules out

- Re-testing either gate on a different split, band, or threshold. The result is
  structural: no exit-price rule can recover a cost that is invariant to exit price.
- "Hold until the move is bigger" as a fee fix, in any phrasing, on this book.
- Reading a small positive test delta (like +$0.40) as evidence: the interval
  straddles zero and the effect reverses inside the test half.

What the same evidence does point at, and what has not been tested: the
payoff shape (mean win $4.07 vs a 1% stop risking $7.71 at 0.94:1 and a 38–42% win
rate), i.e. fewer, larger trades — an entry-side decision, not an exit-side one.

## Scripts

- `scratch/exit_band_replay.py` — the band replay: decision at `sold_at` from
  `|exit/entry − 1|`, forward walk on 5m closes, band exit or a fixed 24h cap,
  fee unchanged. `--audit` prints the bar-gap and fee checks; `--floor` adds a
  stop while holding.
- `scratch/stopgap_check.py` — the stop-fill decomposition that motivated all of
  this; see `STOP_FILL_ANALYSIS.md`.
- `scratch/exit_band_gate.py` — the value-destruction variant of the band test
  (untracked).
