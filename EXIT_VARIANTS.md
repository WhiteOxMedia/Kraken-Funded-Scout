# Should the trend-reversal close be replaced? Graded, and the answer is no (Nov 15)

`scratch/exit_variant_study.py` takes the cohort `EXIT_LEAK.md` pointed at — every
EXIT WATCH trend-reversal close — and replays each one on the stored 5m closes
with the same round-trip charge, against the **shipped exit** (what the journal
actually booked). 1778 trades, chronological split: fit 1066, holdout 712.

| variant | what it does |
|---|---|
| **shipped** | the exit-watch close as it fired, booked P&L from `contest_trades.jsonl` |
| hold180 | no exit at all: the close at entry + 180m |
| stop180 | the hard stop (**−3%, unchanged**) or the 180m close |
| trail180 | the profit guard's trail (arms at half the goal, 1.5% off the peak) **plus** the same hard stop, or the 180m close |

A variant only earns the change if it beats the shipped **$ per trade on the
holdout** *and* does not deepen the **1% tail**. The hard stop is identical in
every variant by construction — nothing in this study can relax it.

## Fit span (1066 trades)

| variant | booked% | $ total | hit% | mean MAE | 1% worst | 5% worst | $/trade |
|---|---|---|---|---|---|---|---|
| **shipped** | −0.166 | −955.67 | 35.7 | −0.474 | −2.12 | −1.30 | **−0.897** |
| hold180 | −0.154 | −7.70 | 39.6 | −1.481 | −4.73 | −2.90 | −0.007 |
| stop180 | −0.237 | −111.14 | 38.4 | −1.406 | −3.08 | −3.08 | −0.104 |
| trail180 | −0.242 | −106.96 | 37.9 | −1.322 | −3.08 | −3.08 | −0.100 |

On the fit span the leak looks real and large: holding the window would have taken
the cohort from −$956 to −$8.

## Holdout span (712 trades) — the same test, disjoint

| variant | booked% | $ total | hit% | mean MAE | 1% worst | 5% worst | $/trade |
|---|---|---|---|---|---|---|---|
| **shipped** | −0.129 | −773.82 | 38.6 | −0.515 | **−2.29** | −1.36 | **−1.087** |
| hold180 | −0.314 | −1290.97 | 37.9 | −1.327 | −5.89 | −2.84 | −1.813 |
| stop180 | −0.255 | −1073.77 | 37.6 | −1.229 | −3.08 | −3.08 | −1.508 |
| trail180 | −0.275 | −1064.85 | 35.7 | −1.153 | −3.08 | −3.08 | −1.496 |

**Every variant is worse on the holdout — on $ per trade and on the 1% tail.**
The fit-window rescue reverses: holding the window goes from −$0.01 to −$1.81 per
trade, and the tail moves the wrong way in all three variants (−2.29% → −3.08%
with the stop, → −5.89% with no stop). The trail does not rescue it either.

## Verdict: the shipped exit stands; the exit question is closed

The measured "leak" in `EXIT_LEAK.md` is a fit-window artefact. In the later,
harsher span the trend-reversal close is doing real risk work — it is the reason
the cohort's tail is −2.29% instead of −3.08% (stop-only) or −5.89% (hold). Its
$ cost against holding the same horizon was already near zero (+0.005%/trade);
the $166 "drag" came from comparing against a window hold that the holdout says
loses more.

**Nothing changed in the live exit.** No flag, no threshold, no window, no trail
arithmetic. The hard stop is identical in every variant in this study and
untouched in the repo (`git log -1 --stat` on this commit touches only the study,
the result and this document).

If this is revisited, the honest framing is not "hold longer" — it is that a
trend-reversal close on a 15–60m book is nearly free against holding and is the
cheapest tail insurance the stack has.
