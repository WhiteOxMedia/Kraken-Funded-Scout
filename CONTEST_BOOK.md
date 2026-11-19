# CONTEST BOOK — the calibrated answer to "+12% without crossing −3%"

Written 2026-11-19. **Revised twice.** Every constant was refitted on data before 2026-10-19 only
and reported on anchors after it (§7); then the book was rebuilt on the challenge's LIQUID universe,
because 95% of the §7 book's fills could not have been taken at the venue (§9). **§9 is the shipped
book; §3–§8 are kept as the record of what was swept and why it was wrong.** Receipts: `scratch/_feasibility.py`,
`scratch/_portfolio_contest.py`, `scratch/_rolling_contest.py`, `scratch/_regime_gate.py`,
`scratch/_final_calibration.py`, `scratch/_liquid_book.py`, `scratch/_split_calibration.py`,
`scratch/_now.py`. Trade caches: `scratch/_books_next_open.json` (§3–§8) and
`scratch/_books_liquid_contest.json` (the shipped §9 book).

## 1. Why the live account lost, measured on its own fills

2,712 closes, 2026-09-03 → 2026-11-19, $10,000 start, trading only:

| | value |
|---|---:|
| gross | −$952.93 (−9.53%) |
| fees | −$1,309.94 (−13.10%) |
| net | −$2,263.01 (−22.63%) |
| closes | 2,712 = 35.3/day, 0.080% round trip |
| win rate | 45.4% (avg win +0.614%, avg loss −0.571%) |
| gross expectancy | **−0.033% per trade** |

The toll exceeded the entire +12% prize ($1,309.94 vs $1,200). Required to clear the prize in
22 days at that trade rate: **+0.578% gross on every trade**, a **2.23:1** payoff. The account ran
1.08:1. No sizing change fixes a negative coin flip: capping every position at 5% of start moves
−22.63% to −11.53%, still a loss.

## 2. What was already measured and never used

`edge_registry.json`: 1,728 policies evaluated, 336 decision-grade, **`active: null`** — no policy
had ever been wired to the scanner. Of those, 78 clear both in-sample windows (`w1` 08-17→09-18 and
`w2` 09-18→10-19) with ≥40 trades and no death, across 35 distinct entry/regime/exit families. That
pool is the raw material below. Both of those windows end on or before 2026-10-19, so the pool was
never selected on the test month; the constant-fitting was (fixed in §7).

## 3. The composed book, on the contest's own rule — in-sample sweep

Rule implemented literally: start $10,000, fixed win line $11,200, fixed death line $9,700, checked
at every close, never rolling. Composed book = 10 policies, one per family, ranked by `w1+w2`,
`next_open` fills, 0.08% round trip, one position per pair, N concurrent slots.

Rolled from **all 55 daily anchors** (each a fresh 22-day contest), filling every signal, no gate:

| size × slots | won | died | pass rate | median trades | median days |
|---|---:|---:|---:|---:|---:|
| 6% × 6 | 16/55 | 33 | 29% | 140 | 16.0 |
| 12% × 6 | 16/55 | 39 | 29% | 65 | 7.5 |
| 24% × 4 | 10/55 | 45 | 18% | 16 | 1.5 |
| 30% × 2 | 13/55 | 42 | 24% | 10 | 4.0 |
| 30% × 3 | 11/55 | 44 | 20% | 12 | 2.0 |

**This table is in-sample and is kept only as the record of what was swept.** The 55 anchors are 25
train + 21 straddle + 9 test; they overlap each other 21 days out of 22, so they are about three
independent contests counted 55 ways. Every constant in §4–§6 was picked on this table, which is
exactly why §7 exists.

## 4. The constraint that actually binds: liquidity

1% of a pair's median hourly dollar volume is the shipped participation rule. Share of the book's
signals whose order fits that rule:

| order | 1% rule | 5% rule |
|---|---:|---:|
| $1,200 (12%) | 6.5% | 23.6% |
| $2,400 (24%) | 4.4% | 14.2% |
| $3,000 (30%) | 3.3% | 13.1% |

The pool fires mostly on coins where a $2,400 order is 10–30% of an hour's volume. That is why the
size-slots sweep capped out at +4.15%/53 days: the notional needed for +12% does not exist in the
names the entries fire on. The rule is applied per signal at the live order size in
`contest_book.py calibrate`, not as a fixed $10,000 notional.

## 5. Breadth gate: what separates "trades constantly" from "trades when it can work"

Account-level breadth = share of the 199 pairs with close above their 21-hour EMA.
Full tape: mean 0.305, p25 0.136, p75 0.457. By period:

| period | hours | mean | p25 | p75 | open @0.30 | open @0.40 |
|---|---:|---:|---:|---:|---:|---:|
| 09-03 → 09-27 (train) | 600 | 0.418 | 0.256 | 0.593 | 69.5% | 56.5% |
| 09-28 → 10-18 (straddle) | 504 | 0.305 | 0.149 | 0.464 | 47.2% | 31.5% |
| 10-19 → 11-18 (test) | 723 | 0.216 | 0.090 | 0.327 | 30.0% | 12.3% |

On the same 55 in-sample anchors, at 12% × 2 slots the no-gate book went 3/55 with 49 deaths; at
breadth ≥0.45 it went 11/55 with 12 deaths. That sweep picked 0.40–0.45 — but refitted on train only
the threshold moves to **0.30**, a good sign that the exact level is noise and only the direction
("trade the broad tape, stand down in a narrow one") survives.

## 6. Book-level stop

Guard closes the book and stands down at a fixed loss from the anchor, so the −3% death line is
never reachable. Fitted on train only, the best zero-death stop is **−2.0%**, not the −1.5% that the
full-55 sweep picked. In the untouched test month it fired in **9 of 9** windows (§7): the death line
held, at the cost of a guaranteed ≈−2% every time the month does not work.

## 7. The train-only refit, and the honest out-of-sample number

Split rule: a training anchor is one whose whole 22-day window ends before 2026-10-19 (25 anchors,
09-03 → 09-27). A test anchor starts on or after it (9 anchors, 10-19 → 10-27). No post-cutoff trade
touched book membership, size, slots, gate or stop. The setting was then replayed once, frozen.

Book membership rule: keep a policy only if it traded ≥100 times before the cut with positive mean
net. **All 10 kept**, so membership is unchanged — the refit had no teeth at policy level.

Fit grid: 192 configs (size 12/18/24/30%, slots 2/3/4, gate none/0.30/0.40/0.50, stop
none/1.0/1.5/2.0%), selected by most train wins with zero train deaths, ties to fewer trades.

| constant | was (fitted on all 55) | refitted (pre-10-19 only) |
|---|---:|---:|
| size | 24% | **18%** |
| slots | 3 | **3** |
| gate | 0.40 | **0.30** |
| book stop | 1.5% | **2.0%** |
| book | 10 policies | **10 policies** |

| anchors | won | died | stopped | flat | rate |
|---|---:|---:|---:|---:|---:|
| train, 09-03…09-27 (in-sample) | 9/25 | 0 | 13 | 3 | 36% |
| **test, 10-19…10-27 (untouched)** | **0/9** | 0 | 9 | 0 | **0% (95% CI 0–30%)** |

Every test anchor stopped between 11-02 and 11-05 at −$205 to −$225 (−2.0% to −2.2%): 8–21 trades,
never +12%, never −3%. For comparison the old 24%/0.40/1.5% setting on the same test anchors made
0–8 trades and finished flat at $9,961–$10,140 — no win, no stop, no prize.

Independent, non-overlapping 22-day contests (the honest unit — overlapping anchors are one stream
counted many times):

| contest | outcome | trades | final |
|---|---|---:|---:|
| 09-03 → 09-25 | **WON** 09-21 | 49 | $11,203 |
| 09-25 → 10-17 | STOP 10-12 | 47 | $9,799 |
| 10-17 → 11-08 | STOP 11-05 | 21 | $9,792 |
| 10-19 → 11-10 (pure test) | STOP 11-05 | 21 | $9,792 |

**The honest rate is not 7%.** In the only fully out-of-sample month the book passed **0 of 1**
contests and 0 of 9 overlapping views of it. The 4/55 (7%) claimed earlier and the 9/55 (16%) this
refit produces on all 55 are both artifacts: constants fitted on the same overlapping windows they
are scored on, in a month (September) when breadth averaged 0.418 and the gate was open 70% of hours.
The only claim the data supports is a regime one: this book wins contests in a broad tape and
guarantees ≈−2% in a narrow one (test mean breadth 0.216, gate open 30% of hours). There is no
measured 22-day pass rate to act on. Reproduce with `contest_book.py calibrate`.

## 8. State on 2026-11-19

Tape last bar 2026-11-18 02:00Z. **Breadth 0.166 — 31st percentile.** 24h change −0.111. 17% of
the 199 pairs are above their 21-hour EMA.

The shipped book is the §9 one: 6 policies on 6 fillable pairs, 18% × 4 slots, no breadth gate,
−2% book stop. It stopped out in every one of the four independent contests measured, including
the September one the filtered book "won". Balance $9,982.77 against a $11,200 win line and a
$9,700 death line. There is no configuration in this document that has passed a contest on
fillable liquidity, and the scanner now refuses to speak about pairs it cannot fill.

## 9. The liquid universe — rebuilt, and the honest answer got worse

Written 2026-11-19 after the §7 book was measured to be filling 4.9% of its own
signals (2,510 of 51,525). A signal the order cannot fill is not a signal, so the
universe was rebuilt instead of the fills being filtered afterwards.

**The universe.** The challenge's own 54 pairs, screened on pre-2026-10-19 volume
only: keep a pair when `$2,400 <= 1% x median hourly dollar volume`.

| pair | median $/hour | 1% capacity | |
|---|---:|---:|---|
| BTC/USD | 3,003,807 | 30,038 | keep |
| ETH/USD | 1,200,270 | 12,003 | keep |
| XRP/USD | 889,543 | 8,895 | keep |
| SOL/USD | 768,283 | 7,683 | keep |
| ZEC/USD | 549,085 | 5,491 | keep |
| NEAR/USD | 259,393 | 2,594 | keep |
| SUI/USD | 206,790 | 2,068 | out |
| HYPE/USD | 204,531 | 2,045 | out |
| TAO/USD | 145,346 | 1,453 | out |
| ADA/USD | 141,428 | 1,414 | out |

**6 of 54 pairs.** The ten policies were then re-resolved on those pairs alone
(next-open fills, no post-hoc filter): 2,504 trades. Every order the frozen book
can place is ≤ $2,016 (18% of the $11,200 win line), below the $2,400 the
universe was built for — measured, not assumed: **0 signals were refused by the
participation rule**, against 95% refused before.

Two wiring defects were found while doing it, both silent:

1. `edge_engine._panel_ranks` reads `closes["BTC/USD"]` for `btc24`. The contest
   cache keys bare symbols, so `btc24` was all zeros, and because `risk_on`
   needs `btc24 > 0` and `risk_off` needs `btc24 < 0`, **both polarity gates were
   permanently shut** — 8 of 10 policies produced zero signals on the contest
   panel. Alias BTC for the feature panel and they fire normally.
2. `contest_book._load` looked capacity up by exact key, so every bare symbol was
   priced at zero liquidity and the participation rule refused every order: a
   zero-trade replay that looks like a result. It now resolves `BTC` -> `BTC/USD`.

**Membership now has teeth.** Pre-cutoff rule (≥100 trades, positive mean net):

| policy | pre-cutoff n | mean % | |
|---|---:|---:|---|
| ignition.risk_on.fixed | 257 | +0.465 | keep |
| dip.risk_off.fixed | 139 | +1.452 | keep |
| dip.risk_off.chandelier | 139 | +1.588 | keep |
| dip.trend.chandelier.6 | 126 | +1.839 | keep |
| ignition.risk_on.breakeven | 257 | +0.389 | keep |
| range_reclaim.risk_off.fixed | 243 | +0.152 | keep |
| pullback.risk_on.chandelier | 95 | −0.511 | drop |
| pullback.risk_on.breakeven | 145 | −0.582 | drop |
| pullback.risk_off.breakeven | 36 | −1.327 | drop |
| pullback.trend.fixed.6 | 253 | −0.207 | drop |

**6 of 10 kept** (the §7 book kept all 10, so the rule had no teeth there).

**The fit, on train only** — 144 configs, size 6/12/18% (24% and 30% put a
$2,688–$3,360 order on the book, above the universe's $2,400, so they are not on
the menu), slots 2/3/4, gate none/0.30/0.40/0.50, stop none/1/2%:

| size | slots | gate | stop | won | died |
|---:|---:|---:|---:|---:|---:|
| 18% | 4 | 0.00 | 2% | 5/25 | 0 |
| 18% | 4 | 0.30 | 2% | 3/25 | 0 |
| 18% | 4 | 0.40 | 2% | 3/25 | 0 |

Frozen: **18% × 4 slots, no breadth gate, −2% book stop**. The breadth gate that
§5/§7 leaned on does not survive on six liquid names — it is off, and `state()`
therefore always reads OPEN. The −2% stop is the only brake left.

| anchors | won | died | stopped | flat | rate |
|---|---:|---:|---:|---:|---:|
| train, 09-03…09-27 (in-sample) | 5/25 | 0 | 19 | 1 | 20% [9–39%] |
| **test, 10-19…10-27 (untouched)** | **0/9** | 0 | 9 | 0 | **0% [0–30%]** |

Every test anchor stopped between 10-23 and 10-30 at $9,732–$9,784, on 5–34
trades. Independent, non-overlapping contests, all four stopped:

| contest | outcome | trades | final |
|---|---|---:|---:|
| 09-03 → 09-25 | STOP 09-10 | 28 | $9,788 |
| 09-25 → 10-17 | STOP 09-27 | 6 | $9,794 |
| 10-17 → 11-08 | STOP 10-18 | 11 | $9,780 |
| 10-19 → 11-10 | STOP 10-24 | 24 | $9,783 |

**The honest answer got worse, and this is the corrected one.** The September
contest that §7 records as a win (+12.03%, 49 trades) was won on the illiquid
alts the account could not have filled. On the six pairs it could fill, the book
has **never won a contest in the three months measured: 0 of 4 independent
contests, 0 of 9 test anchors**, and its in-sample rate fell from 36% to 20%. The
old 9/25 and 7% are not optimistic estimates of this book — they describe a book
that does not exist at the venue.

**Live wiring (same day).** `liquid_universe.py` is the rule; `config.py` ships
`liquid_universe_enabled = True` at `$2,400`. It runs BEFORE the bracket in the
running scanner (`background_scanner.py`, right after the rotation liquidity
filter) and inside both per-pair evaluations (`scanner.py`), so an unfillable pair
is demoted to hold with `No fill: ...` and can never be crowned, deep-read, ranked
or fired. Every refusal is logged with the pair's capacity; the gate is one
config flag away from off. It is advisory-consistent: the scanner still places no
orders, it only stops speaking about coins the account cannot buy.

Reproduce: `scratch/_liquid_contest_book.py` (rebuilds the cache from the
contest's own candles), `scratch/_liquid_anchor.py` (the split), and
`contest_book.py calibrate` (the frozen module, same numbers).
