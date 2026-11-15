# The pre-registered strategy monitor

`monitor_strategy.py` implements §4 of `scout/engine/STRATEGY_MVP.md`: the
fixed-sample test that decides whether the smallest viable strategy has an edge,
with the look schedule pre-committed so a good run at 30 closes cannot end it.

## The design it enforces

| | |
|---|---|
| metric | mean `pnl_net` per close, fees included; H0 μ ≤ 0 vs H1 μ > 0 |
| sample | **N = 170** closes, **one interim look at n = 85** |
| bounds | O'Brien-Fleming, two-sided α = 0.05: **\|z\| ≥ 2.96** interim, **\|z\| ≥ 1.98** final |
| estimators | mean + 20,000-resample bootstrap 95% CI, t vs zero, expectancy excluding the top 3 closes, largest pair's share of net — all four, every run |
| decision | *edge exists*: final lower bound > 0. *Worth the budget*: point ≥ +$2.00 **and** lower bound > 0 **and** ex-top-3 > 0 **and** top-pair share < 40%. *Kill*: cumulative net ≤ −4% of balance at any look, or final z < 1.98 |

Looks are **recorded**. `--interim` writes the look to `monitor_strategy_looks.json`
(`since` + `strategy_v` → `{at, n, mean, z, net, ci, verdict}`); a second
`--interim` on the same cohort prints that record instead of a new verdict, so
the registered alpha cannot be spent twice. The final is recorded the same way:
from n = 170 the first run fixes the verdict, and later runs replay it — closes
that arrive after the final are descriptive, not a second test. A corrupt record
is an error, not an empty one: the script refuses to take a look rather than
treat a spent look as unspent. Delete the file only to discard a
look, and then say so — a discarded look is still a look.

The script **refuses to render an edge verdict off-schedule**: before n = 85 it
withholds; between 85 and 169 it prints the numbers but takes no decision unless
you pass `--interim` once; only the final (n ≥ 170) decides. A negative final
z below 1.98 is reported as **KILL**, not as "no decision" — that is what the
pre-registration says it means. Reading the report at n=120 and again at n=150
is exactly the behaviour this closes.

## Two cohorts, never mixed

- **TEST** — closes that carry the generation stamp **and** the strategy's cuts
  (full size, fast route, no re-entry, no hail, climb PASS). `strategy_v` alone
  does **not** identify this strategy: the close writers stamp `STRATEGY_VERSION`
  on every fire through `family_fields`' fallback, so it marks which rule
  generation wrote the row, not which strategy fired. Counting the stamp alone
  would pour every ordinary fire into the judged cohort.
- **HISTORY** — closes matching the strategy's *shape* since `--since` (full
  size, not min-size, fast route, no re-entry, no hail, climb PASS). Useful as a
  sanity read; explicitly **not** the test, and never part of the z.

## Usage

```
./.venv/Scripts/python.exe -X utf8 monitor_strategy.py
./.venv/Scripts/python.exe -X utf8 monitor_strategy.py --trades PATH
./.venv/Scripts/python.exe -X utf8 monitor_strategy.py --since 2026-11-06
./.venv/Scripts/python.exe -X utf8 monitor_strategy.py --interim   # consume the one registered look
./.venv/Scripts/python.exe -X utf8 monitor_strategy.py --json      # JSON only on stdout
```

Options: `--trades` (ledger path), `--since` (cohort start, default the Nov 6
pre-registration date), `--strategy-v` (generation stamp to require, default 1),
`--balance` (the balance the −4% kill is measured against; defaults to
`contest_account.json`'s `start_balance`), `--interim` (render the single
registered interim verdict), `--json` (machine-readable output, no prose).

Run it after every observation, not every day: the point is that the numbers are
identical to the last run unless the ledger moved.

## Current state (Nov 15)

- **TEST: n = 0.** No close has landed since `strategy_v` was wired into the
  close writers this session, so no row carries it — and because the writers
  stamp it on *every* fire, the first ordinary close would satisfy the stamp
  without being the strategy. That is why the cohort also requires the shape
  cuts, and why it will stay nearly empty until the coded strategy ships
  (no-re-entry gate, 2:1 room check, risk-based sizing are still "to build" in
  §5 of the MVP).
- **HISTORY: n = 5** since Nov 6 — strict mode refuses min-size fires, so the
  strategy's shape barely occurs. No look is available from 5 rows.

Mechanics were verified by running the same script over all history
(`--since 2026-07-01`): n = 117 triggers the interim branch, z = +0.69 against the
2.96 bar, ex-top-3 −$0.89, top pair FIL at 111% of net. That reproduces the
MVP's own red flags, which is the check that the estimator suite works.

## What has to be true before a verdict exists

1. the coded strategy ships and writes `strategy_v` on every close;
2. closes accumulate under that stamp;
3. n reaches 85 for the interim, 170 for the final.

Until all three hold, every run prints `n=0` for the judged cohort and withholds
a verdict — which is exactly what the pre-registration asks for.
