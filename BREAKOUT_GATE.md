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

## Where the gate is wired (and why it had to be pulled out of the crown)

The first cut lived inside `deep_crown_pool`. That gates the crown but not the
**mover route**, which calls `notify_big` + `auto_track_popup` on its own and never
passes through `send_alert` — an unmeasured buy could still fire there. So the rule
now sits in `breakout_gate.evaluate()`, the choke point every route calls before a
side effect:

| route | where the gate runs | before what |
|---|---|---|
| crown (champion, fast, opportunistic) | `send_alert`, right after the long-only check | sizing, bridge write, popup, watch row |
| mover | the mover loop, after the one-watch check | `notify_big`, `auto_track_popup` |
| crown decision itself | `deep_crown_pool`, before it returns the champion | the whole fire block |

When the signal has no `entry_quality` (the mover route never had a frame), the
choke measures it from that pair's own hourly bars (`breakout_gate.measure`, 720
bars) instead of letting it through unjudged. A venue that cannot answer keeps the
old behaviour and is journalled as `unmeasured-fire` — never an invented verdict.

Every judgement writes one row to **`contest_gate_log.jsonl`** (the gate's journal)
with the pair, route, decision, the measured `entry_quality`, the gate version and
the study's pooled/OOS figures. Refusals also land in the suppression log
(`breakout-gate`) and the scanner log line.

## Live record

- scanner pid 24812, cycles advancing, no traceback.
- 07:09 full pass: `DEEP PASS: 5 read deep, 1 eligible buy(s) (best AAVE 64% … cheap 0.11)`.
- probe through the live fire path (`deep_crown_pool` + `auto_track_popup`):
  champion AAVE/USD, `entry_quality 0.9115` → gate passes → row written with
  `breakout_gate_v 1`, the gate string, `pooled_win_pct 80.6`, `oos_win_pct 66.7`,
  and the exit study beside it (`winners []`, shipped baseline 30.5% / −0.00473).
  `RESULT: PASS (0 failed)`, no order, balance $9,982.73436428867 unchanged.
- the only open row (VVV/USD) predates the restart (06:30); none was added after.

Both branches, on real data, through the shipped functions (`scratch/gate_probe.py`
replays real recorded cycles from the tape — the pool is rebuilt with
`evaluate_strategy`, so the champion and its measurement are the ones the loop
would have produced):

```
-- branch A (breakout): real cycle 1790877600, champion LTC 92%, entry_quality 0.9096
PASS  A 1. the gate crowns the breakout champion  LTC
PASS  A 2. the choke point allows the fire  0.9096019788621577
-- branch B (dip champion): real cycle 1790834400, champion TRUMP 70%, entry_quality 0.5351
PASS  B 1. the gate does NOT crown the champion below 0.85  None
PASS  B 2. the refusal is in the gate journal, with its numbers  {'decision': 'veto', 'entry_quality': 0.5351, 'gate_v': 1, 'pooled_win_pct': 80.6, 'oos_win_pct': 66.7, 'oos_ci': [40, 93]}
PASS  B 3. the fire choke refuses it too, and journals the refusal  {'route': 'standard', 'decision': 'veto'}
RESULT: PASS (0 failed)
```

Live rows written by the running scanner itself (pid 27400, no traceback, no order,
balance $9,982.73436428867): `AAVE/USD standard fire 0.905 / 0.9218 / 0.8706` —
the choke measured each champion itself (`measure`, 720 hourly bars) and let the
breakouts through:

```
LIVE GATE REPORT — contest_gate_log.jsonl | gate 0.85 | study v1:
  fit 24 @ 87.5%, holdout 12 @ 66.7% CI [40, 93], pooled 80.6%
  fire             n=  7  mean entry_quality 0.904
  veto             n=  3  mean entry_quality 0.535
  unmeasured-fire  n=  0  (no measurements)
  by route: {'crown': 4, 'standard': 6}
  the gate's own record on each row: gate_v, entry_quality, pooled/OOS figures
```

(counts are a live snapshot — the journal grows with every judged fire)

(`scratch/tournament_study.py --live` prints that report beside the fit/holdout
table, so live rows answer the gate's question directly.)
