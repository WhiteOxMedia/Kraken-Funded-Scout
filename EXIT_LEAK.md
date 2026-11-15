# What the exits actually book, versus just holding (Nov 15)

`scratch/exit_leak_study.py` takes every real close in `contest_trades.jsonl`
(entry, exit, size, minutes held, net P&L) and replays it on the recorded 5m
tape, so each exit can be compared with the same trade held to the same horizon
it was actually held, charged the same round trip. Nothing is changed by this
study — it is the measurement that has to come first.

**2375 closes in 45 days** had tape coverage at their own horizon.

| what | dollars |
|---|---|
| booked by the exits | **−2,182.82** |
| holding each trade to the SAME horizon it was held | −2,207.10 |
| **exit effect, same horizon** | **+24.28** (the exits are a wash) |
| holding every trade to the shipped 180m window | **−2,016.35** |
| **exit effect versus holding the full window** | **−166.47 — this is the leak** |

So the exits are neutral against "held exactly as long as you did", but they cost
$166 against "held the whole 180m window". 45% of trades (1069/2375) would have
booked more by holding the window.

## The leak is not free — here is the tail it buys

| | booked | held to 180m |
|---|---|---|
| 1% worst trade | −2.81% | **−5.84%** |
| 5% worst trade | −1.65% | **−3.13%** |
| worst adverse excursion inside the window (mean) | — | −1.42% |
| worst adverse excursion, 5% / 1% worst | — | −4.10% / −6.76% |

Holding everything would have risked roughly **twice the per-trade drawdown**
for +$166 over 45 days on a $10k book. That is the trade the exits are making,
and it is why "just hold longer" is not the fix.

## Where the drag actually sits (by class × how long it ran)

Leak = booked − held-same-horizon; negative = the exit cost money.

| class | band | n | booked% | held% | leak% | leak $ |
|---|---|---|---|---|---|---|
| exit watch (trend reversal) | 15–60m | 1498 | −0.101 | −0.106 | +0.005 | **−50.80** |
| exit watch (trend reversal) | 60–180m | 152 | −0.695 | −0.633 | **−0.062** | **−43.88** |
| trend bar broke | <15m | 77 | −0.180 | −0.130 | −0.049 | −36.67 |
| no reason recorded | <15m | 7 | +0.858 | +1.110 | −0.252 | −29.47 |
| profit guard (trail) | 60–180m | 118 | +0.116 | +0.054 | +0.062 | −21.96 |
| profit guard (trail) | 15–60m | 87 | −0.045 | −0.298 | +0.253 | **+94.21** |
| profit guard (trail) | ≥180m | 69 | +0.037 | −0.146 | +0.183 | **+55.33** |
| hard stop | 15–60m | 9 | −1.774 | −1.804 | +0.031 | +9.55 |
| exit watch (trend reversal) | ≥180m | 54 | −0.149 | −0.187 | +0.038 | +23.99 |

## The single biggest leak

**The EXIT WATCH trend-reversal close**: 1830 trades, −$68.91 in total, and it is
the worst *per-trade* leak of any sizeable cell at 60–180m (−0.062%/trade over 152
trades, −$43.88) plus the 15–60m mass (−$50.80 over 1498). The two cells together
are −$94.68 of the −$166 drag. These are positions the exit-watch engine cuts on a
"trend reversal X + exhaustion Y" score, which then do not fall any further — held
to the same horizon they would have booked −0.633% instead of −0.695% in the
60–180m band.

By contrast the **profit guard is the stack's best earner: +$135.10** (+0.253%/trade
in 15–60m, +0.183%/trade at ≥180m) — it waits for a peak and books it, which is
exactly what the exit-watch close does not do. The hard stop is nearly neutral on
this tape (+$6.12 over 13 trades) and is not to be relaxed regardless.

## What this points at (to be tested, not shipped)

The leak is one specific rule, not the exit stack: **the exit-watch trend-reversal
close, applied to books held 60–180m.** The candidate is to require an actual
adverse move before that close can fire in that band (−0.06%/trade is inside the
tape's noise, and the position is already 0.7% under water when it fires), then
grade it offline on the same disjoint spans before any live change. Nothing was
changed here: `background_scanner.py`'s exit, stop, goal and death-line logic and
their accounting are untouched by this pass (diff grep: 0 matches).

The next study has an obvious shape: for the exit-watch cohort, test
"hold until the trail, the hard stop, or the 180m window" against the current
close, on fit and disjoint holdout, with the drawdown tail reported beside the
return — because the numbers above say the tail is what makes this decision hard.
