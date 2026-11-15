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

## 4b. The champion's timing label leads the list

The label on the champion's `reasons` is inserted at index 0, not appended. Every
reader truncates from the head — the bridge stores `"; ".join(reasons[:3])[:200]`,
the winner row stores `reasons[:5]`, only the console reads nine — and a champion
already carries five reasons, so an appended label was dropped by the bridge and
the database and showed only on screen. Nothing parses these strings (every
reader is a substring test or a slice), and the label shares no keyword with any
gate (`exhaustion`, `breakout failure`, `exit signal`, momentum and liquidity
checks), so the position was the only thing to fix. `test_timinglabel.py` holds
it: 17 checks.

## 4c. `entry_quality` on every close — the field, and where it lands

The tournament's own timing number used to die with the bracket: it picked the
champion, labelled it, and was gone. It is now persisted on the fire and carried
to the journal, through the same plumbing the setup stamps use.

| where | key | value |
|---|---|---|
| watch row (fire) | `entry_quality` | the champion's 0..1 position in its prior-hour range, exactly the number the bracket used; `None` when the route never ran the bracket (mover, DCA) |
| watch row (fire) | `entry_quality_v` | `1` = stamped, `0` = not scored — a hole is never read as "the low" |
| journal row (close) | `entry_quality`, `entry_quality_v` | copied by `_family_fields()`, the one helper both close writers use (the scanner's GET OUT and the console's SOLD), so neither can drop it silently |

Chain: `tournament_scan` sets `champion.signal.entry_quality` → both fire routes
pass `entry_quality=getattr(signal, "entry_quality", None)` into
`auto_track_popup` → the watch row → `_family_fields(w)` → the journal line.

Rows closed before this change carry `entry_quality_v == 0` and no value; unlike
the setup stamps there is nothing to backfill them from, because the bracket's
number was never written down. `timing_eq` (§4) is the bar-reconstructed cousin
and is the right column for those older rows.

## 4d. The tournament's timing decision — flag, shadow, and what the closes say

**The flag.** `config.TradingConfig.entry_timing_rank`, default **False**. Off,
`tournament_scan` chooses exactly the champion it always chose; the only new
thing is a record. On, the winner may be swapped for a candidate that entered
materially lower in its own prior-hour range — provided that candidate's signal
confidence is within `entry_timing_window` (10 points) of the champion's and it
entered at least `entry_timing_min_gain` (0.15) lower. It never promotes a
materially weaker signal, and it cannot touch exits, stops or the death line.

Why a window and not a hard gate: the earlier diagnosis (1,106 entries) found
that buying low redraws *who* wins among equals but does not lift expectancy by
itself; a gate at `eq <= 0.2` returned **−$1.54** held-out. The veto is therefore
recorded (`veto: true` for a champion at or above 0.85 of its range with nothing
better on offer) and never enforced.

**The shadow record.** Every fire writes `entry_timing` (dict, versioned by
`entry_timing_v`) onto the watch row, and `_family_fields()` carries it into the
journal for both close routes. Keys: `would` (`hold` / `prefer` /
`insufficient` / `no_bracket`), `applied`, `src`, `eq`, `veto`, `pool`, and on a
swing `from`/`from_eq`/`to`/`to_eq`/`gain`. A route with no bracket (mover, DCA)
lands `would: no_bracket` — a record, never a blank. A champion whose bracket
number is missing gets `eq` filled from the fire's `timing_eq` bar stamp with
`eq_src: "timing_eq"`.

**The replay — `scratch/entry_quality_replay.py`.** Real closes, not simulation:
`contest_trades.jsonl`, positioned by the local 5m tape over the twelve bars
before the entry. The tape is now the aligned archive plus
`scratch/kraken_5m_cover_2026-11-15.json`, the coverage backfill
(`scratch/fetch_missing_tape.py`) that closed the 26-pair gap; the replay merges
both by timestamp, 55 symbols on disk.

**Coverage.** 2,706 closes on disk, **2,536 positioned (93.7%)** — up from 1,129
before the backfill. **170 excluded (6.3%), each with a reason:**

| reason | n | symbols | what the venue does |
|---|---|---|---|
| no tape pair — contract absent | 82 | AIXBT ×78, FAKE ×4 | AIXBT: `PF_AIXBTUSD` 5m returns **0 candles for every day of its Oct 12–Nov 7 trades** (a 3-hour Sep 30 window returns 37, so history exists only there); no AIXBT contract in `/derivatives/api/v3/instruments`; spot `AIXBTUSD` OHLC returns no candles. FAKE: 0 candles, spot returns `EQuery:Unknown asset pair`. |
| entry after the tape's last bar | 40 | S | `PF_SUSD` stops serving after Oct 14 21:30; 0 candles on every probed day Oct 14 → Nov 10; no SUSD contract in the instruments list; spot `SUSD` 5m returns no candles (spot keeps only ~720 bars anyway). |
| window has no range | 48 | S | the served bars are **degenerate** there (high == low; 24.4% of S's tape), so there is no hour to measure a position in. |

Zero stored rows carry `entry_quality` or `timing_eq`, so every number below is
**rebuilt** — the same quantity, not the field.

| entry position | n | win rate | 95% CI | mean close | net |
|---|---|---|---|---|---|
| 0.0–0.2 (low) | 900 | 47.0% | 43.8–50.3 | +0.05% | −$59 |
| 0.2–0.4 | 543 | 35.0% | 31.1–39.1 | −0.13% | −$1,077 |
| 0.4–0.6 | 377 | 34.5% | 29.9–39.4 | −0.17% | −$993 |
| 0.6–0.8 | 377 | 39.3% | 34.5–44.3 | −0.00% | +$129 |
| 0.8–1.0 (high) | 339 | 39.2% | 34.2–44.5 | +0.10% | −$21 |

Low (≤0.25) vs high (≥0.75): **n = 1,055 vs 449**, win **46.1% vs 39.0%** =
**+7.1 pp** with the intervals still overlapping (43.1–49.1 vs 34.6–43.6). Mean
close **+0.05% vs +0.09%** = −0.04 pp, bootstrap 95% CI −0.17 to +0.09. Split-half
sign consistent (+6.5 pp, +7.5 pp); thirds +4.1 pp overall (+2.6, +5.3).

**Verdict: NOT RESOLVABLE at 2,536 closes.** The extra 1,407 positioned closes
moved the quintile gap from +7.3 pp (n=463/226) to +7.1 pp (n=1,055/449) and left
the intervals overlapping — more sample, same answer. The win-rate edge points
the right way and is still inside the noise; the money edge is zero to slightly
negative. Closes per arm at 80% power: **1,492** for +5 pp, **583** for +8 pp,
**742** for the +7.1 pp now observed. At 15.4 closes/day that is ~48 trading days
per arm for the observed effect — about 3× the ledger that exists.

**What this means for the flag.** It stays off. The evidence does not yet earn a
live change, and the mechanism is built so the owner can flip it in one line
when it does. Turning it on costs champion swaps that the shadow has already
been logging; the comparison to grade is `would: prefer` rows' realized closes
against the champion that actually fired.

## 5. What the data needs

A +5 pp win-rate shift from 40% at 80% power needs **1,531 closes per arm**
(≈3,062 total, ≈199 days at the current 15.4/day). A coarser +8 pp shift needs
**601 per arm** (≈1,202 total, ≈78 days). The held-out tail today is 325 — about
a fifth of what a +5 pp answer requires, which is exactly why the gate above was
not shipped.

Until then: **no win-rate improvement can be claimed, and +80% remains unmet.**
