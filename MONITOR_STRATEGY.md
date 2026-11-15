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

The script **refuses to render an edge verdict before n = 85**. That is the point
of a pre-registration, not a limitation to work around.

## Two cohorts, never mixed

- **TEST** — closes carrying `strategy_v` (the coded strategy's own stamp). The
  only cohort the pre-registration judges. It is **empty until the strategy
  ships**, and an empty test is the honest starting state.
- **HISTORY** — closes matching the strategy's *shape* since `--since` (full
  size, not min-size, fast route, no re-entry, no hail, climb PASS). Useful as a
  sanity read; explicitly **not** the test, and never part of the z.

## Usage

```
./.venv/Scripts/python.exe -X utf8 monitor_strategy.py
./.venv/Scripts/python.exe -X utf8 monitor_strategy.py --trades PATH
./.venv/Scripts/python.exe -X utf8 monitor_strategy.py --since 2026-11-06
./.venv/Scripts/python.exe -X utf8 monitor_strategy.py --json
```

Options: `--trades` (ledger path), `--since` (cohort start, default the Nov 6
pre-registration date), `--strategy-v` (stamp value to count, default 1),
`--balance` (the balance the −4% kill is measured against; defaults to
`contest_account.json`'s `start_balance`).

Run it after every observation, not every day: the point is that the numbers are
identical to the last run unless the ledger moved.

## Current state (Nov 15)

- **TEST: n = 0.** `strategy_v` was wired into the close writers this session,
  so it stamps from the next close onward — but the strategy it would label is
  not shipped yet (no-re-entry gate, 2:1 room check, risk-based sizing are all
  still "to build" in §5 of the MVP). Watching for a stamp the running code does
  not write would be inventing a cohort.
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
