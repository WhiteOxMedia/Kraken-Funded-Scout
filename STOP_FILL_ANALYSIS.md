# Stop fills: the overshoot is detection lag, not slippage

`scratch/stopgap_check.py` measures how far each stopped-out close actually filled
from its intended stop level, and splits the gap into late detection versus real
slippage. The answer: **all of it is late detection, and none of it is slippage.**

## Method

1. **Intended stop price.** The ledger does not store a stop price. It stores the
   stop *loss* in the reason text (`stop is -$5`) and the position notional, so
   `units = size / entry` and `stop_px = entry - stop_dollars / units`. Because
   `units` is derived from the same entry and notional, the stop's percentage of
   notional and its percentage price distance are the same number.
2. **Entry time.** `sold_at - held_min`, both from the ledger (`held_min` is
   elapsed time since the position was added, so this recovers the moment the
   position opened — not the moment the stop was noticed, which is what the
   measurement is trying to find).
3. **Breach bar.** Scan the pair's 5m bars from the bar containing the fire time
   up to the bar containing `sold_at`, and take the **first bar whose low is at or
   below `stop_px`**. Its timestamp is the earliest moment the stop condition was
   true. `lag = sold_at - breach_bar_start`.
4. **Detection vs fill.** Compare the booked `exit` against the price at the
   decision moment. If they agree, the gap between the stop level and the exit is
   not execution — it is the price travel while nobody looked.

## Finding

Six of the fifteen stopped-out closes have 5m bars. On those six:

| pair | stop $ | stop % | entry | breached | lag | actual loss | overshoot |
|---|---|---|---|---|---|---|---|
| SOL | 5 | 0.23 | 14:55 | 15:00 | 18 min | $38.96 | $32.25 |
| SUI | 5 | 0.26 | 14:56 | 15:00 | 18 min | $31.10 | $24.58 |
| TAO | 5 | 1.01 | 14:57 | 15:00 | 18 min | $8.73 | $3.33 |
| DOGE | 5 | 0.27 | 14:58 | 15:00 | 18 min | $35.13 | $28.65 |
| WLD | 5 | 0.28 | 14:58 | 15:00 | 18 min | $31.18 | $24.74 |
| BTC | 5 | 0.31 | 15:17 | 15:20 | 5 min | $11.66 | $5.37 |

- the loss decomposes exactly: intended stop cost **$30.00** + fees **$7.84** +
  price travel beyond the stop **$118.92** = **$156.76**, the actual loss. The
  overshoot figure is gross (price movement) while the actual loss is net of
  fees, so the fee term is stated separately rather than folded in — and it is
  76% of the loss that is price travel, not execution cost.
- detection lag **5–18 minutes, median 18**
- **detection → exit is 0.00% on all six**, and every exit price sits inside its
  own 5-minute bar (6/6). The ledger's `exit` on a paper contest book is the mark
  at decision time, not an exchange fill. There is no fill record to have
  slippage, so the entire overshoot is the price moving between the breach and
  the next look.

The whole stopped-out set, by ledger arithmetic alone (no bars needed): 15 closes,
intended **$83.00**, actual **$242.86**, overshoot **$159.86**.

## Coverage limit — this is a six-row measurement, not fifteen

Nine of the fifteen have no bars in the 29-pair 5m store: PENGU, VIRTUAL (both),
MOODENG, AIXBT, CRV, POPCAT, S, FIL. So **$118.92 is a measured floor on 6 rows,
not the total for the book**; the $159.86 figure is ledger arithmetic for all 15
and does not verify where the price sat at any moment.

Thirteen of the fifteen fall inside one 7-minute cascade on Nov 5 (two bursts
~6.7 min apart), so this is one event's worth of evidence, not fifteen
independent ones. The two stopped-out closes outside the cascade overshot by
$1.49 combined.

## What it does not say

- Lag resolution is 5 minutes (bar start), so "18 min" could be 13–18.
- Bars give no intrabar path: the breach is located to the bar, not the tick.
- The stop amount is read back from the closing reason, which **rounds to whole
  dollars** and records the level as it stood at the close. The live hard stop can
  move with the account buffer, so `stop_px` carries up to ~$0.50 / units of error
  — around 0.02pp on a 0.23% stop, enough to move a breach by a bar. The lag is
  therefore good to the bar, not the second.
- "Detection" is inferred, not observed. What is measured is breach → booking.
  Whether the loop saw the condition earlier and declined to act is not visible
  in the ledger, and this analysis cannot separate "did not look" from "looked
  and did not sell".
- The separate exit-cycle study found the wait is the loop's ~9-minute wakeup
  interval, with only ~3 seconds per position of queueing. This analysis measures
  the same gap for stops specifically and does not distinguish the two; it
  establishes only that the gap is time, not fill quality.
