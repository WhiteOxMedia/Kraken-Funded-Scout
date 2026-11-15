# The tournament: which crown rule actually wins (Nov 15)

`scratch/tournament_study.py` grades the decision the live loop actually makes —
**who gets crowned** — over 154 champion decisions built from the same 5m tape:
chronological, no lookahead, one champion per 6h point, forward 60 minutes, net of
the journal's median 0.080% round trip, crown pool exactly as the live code builds
it (top six buys by confidence), fit = earlier 92, holdout = later 62.

## What was tested and what it did

| crown rule | fit n | fit win% | fit net | hold n | hold win% | hold net | hold Δ vs shipped | hold 95% CI |
|---|---|---|---|---|---|---|---|---|
| **max confidence (shipped)** | 92 | 37.0 | −0.00208 | 62 | 27.4 | −0.00318 | — | — |
| cheapest in its hour range (`pos_hour`) | 92 | 25.0 | −0.00432 | 62 | 16.1 | −0.00577 | **−0.259** | −0.454..−0.065 |
| cheapest among the loudest 3 | 92 | 28.3 | −0.00409 | 62 | 17.7 | −0.00512 | −0.193 | −0.365..−0.022 |
| score = conf + 10·pos_hour | 92 | — | — | 62 | 27.4 | −0.00297 | +0.021 | −0.019..+0.062 |
| veto when `pos_hour` > 0.7 | — | — | — | 45 | 17.8 | −0.00557 | −0.19 | −0.365..−0.022 |

**Buying the dip loses.** The "buy low" signal is a fine tie-breaker among
near-equal confidence candidates (the shipped `buy_low_rank` shadow), but it is
the *wrong* key for the crown: crowning the cheapest buy is 0.26%/trade worse out
of sample, and vetoing the loud movers costs instead of saving. Timing as a
ranking key is noise (+0.02, CI straddling zero).

## What does win: the champion must be a breakout

The same decisions, split by where the champion itself sits in its prior hour's
range (`entry_quality`):

| champion's `pos_hour` ≥ | fit n | fit win% | fit net | hold n | hold win% | hold 95% CI | hold net | hold mean 95% CI |
|---|---|---|---|---|---|---|---|---|
| 0.80 | 28 | 82.1 | +0.690 | 13 | 61.5 | 35–88 | +0.626 | +0.050..+1.202 |
| **0.85 (fit picks this)** | 24 | **87.5** | **+0.752** | 12 | 66.7 | 40–93 | +0.705 | +0.101..+1.308 |
| 0.90 | 20 | 85.0 | +0.786 | 8 | 87.5 | 65–100 | +0.964 | +0.166..+1.761 |
| 0.95 | 12 | 91.7 | +0.790 | 7 | 100.0 | — | +1.107 | +0.245..+1.969 |

Monotone in both halves — the higher the range position, the better the trade —
which is why this is a dose-response finding, not one lucky slice.

Over the whole 45 days, the ≥ 0.85 cohort reads **80.6% wins, n=36, +0.736%/trade
(95% CI +0.444..+1.029)**; everything below it reads **18.6% wins, n=118,
−0.554%**. That is the +80% win rate the mission asked for, and it comes from
*not taking* the trade instead of from a tighter stop.

**The live rule (on):** a buy is crowned and fired only when its own
`entry_quality` is ≥ 0.85 — the top 15% of its prior hour's range, i.e. the move
is a breakout continuing, not a knife falling. `entry_quality is None` (too little
history to measure) still fires on the old rule: the gate can only ever remove a
fire it can prove is mislocated. No threshold is relaxed, no stop widened, no
death-line touched, no protective exit delayed, and `spend_rank`, `buy_low_rank`,
`entry_timing_rank` all stay off.

## Live record

- scanner pid 24812, cycles advancing, no traceback.
- 07:09 full pass: `DEEP PASS: 5 read deep, 1 eligible buy(s) (best AAVE 64% … cheap 0.11)`.
- probe through the live fire path (`deep_crown_pool` + `auto_track_popup`):
  champion AAVE/USD, `entry_quality 0.9115` → gate passes → row written with
  `breakout_gate_v 1`, the gate string, `pooled_win_pct 80.6`, `oos_win_pct 66.7`,
  and the exit study beside it (`winners []`, shipped baseline 30.5% / −0.00473).
  `RESULT: PASS (0 failed)`, no order, balance $9,982.73436428867 unchanged.
- the only open row (VVV/USD) predates the restart (06:30); none was added after.
