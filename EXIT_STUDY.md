# The exit question, measured on real data (Nov 15)

`scratch/exit_edge_study.py` asks the exit side's question with the same method
that settled the ranking: chronological, no lookahead, paired, net of cost,
fit → untouched holdout.

**Method.** The decision points and the fired coin are the entry study's: at each
6h point the scanner's own `evaluate_strategy` runs over the 5m tape aggregated
to 1h, and the highest-confidence BUY fires. Each fired entry records its **full
forward path** — every 5m high, low and close for four hours — so every variant
is marked over the same 147 entries and the same windows. Round trip 0.080%
(the journal's median `cost_rt/size`). Fit = earlier 88 entries, holdout = later
59, chronological, no overlap.

**The shipped exit**, as the scanner's own row stamps express it: goal 3.0%,
hold 180 min, hard stop at the contest's 3% guard band. Variants move one thing
at a time; stops are only ever tightened (a wider stop is not tested and never
shipped), and a bar that touches both the stop and the target counts as the
**stop**, never a manufactured win.

| variant | fit n | fit Δ | fit 95% CI | hold n | **hold Δ** | hold 95% CI | hold win% | hold net/$1 |
|---|---|---|---|---|---|---|---|---|
| **shipped (tp 3.0 / stop 3.0 / hold 180)** | 88 | 0.000 | — | 59 | 0.000 | — | 30.5 | **−0.00473** |
| hold 60 | 88 | +0.050 | −0.204..+0.304 | 59 | +0.138 | −0.111..+0.387 | 27.1 | −0.00335 |
| hold 120 | 88 | −0.052 | −0.199..+0.095 | 59 | −0.041 | −0.255..+0.173 | 32.2 | −0.00514 |
| hold 240 | 88 | −0.098 | −0.238..+0.042 | 59 | −0.057 | −0.205..+0.091 | 35.6 | −0.00530 |
| tp 1.5 | 88 | −0.087 | −0.199..+0.025 | 59 | −0.036 | −0.131..+0.059 | 30.5 | −0.00509 |
| tp 2.0 | 88 | −0.006 | −0.107..+0.095 | 59 | −0.045 | −0.096..+0.006 | 30.5 | −0.00518 |
| tp 4.0 | 88 | +0.073 | +0.019..+0.126 | 59 | +0.028 | −0.011..+0.068 | 30.5 | −0.00445 |
| tp 5.0 | 88 | +0.121 | +0.023..+0.218 | 59 | +0.045 | −0.025..+0.115 | 30.5 | −0.00428 |
| stop 2.0 (tighter) | 88 | +0.086 | −0.019..+0.191 | 59 | −0.069 | −0.267..+0.128 | 28.8 | −0.00542 |
| stop 1.5 (tighter) | 88 | +0.119 | −0.050..+0.288 | 59 | +0.001 | −0.203..+0.206 | 28.8 | −0.00472 |
| tp 2.0 + hold 120 | 88 | −0.024 | −0.191..+0.143 | 59 | −0.058 | −0.275..+0.158 | 32.2 | −0.00531 |
| tp 2.0 + stop 2.0 | 88 | +0.081 | −0.065..+0.226 | 59 | −0.097 | −0.298..+0.103 | 28.8 | −0.00570 |

## Verdict: nothing clears — the shipped exit stands

Every holdout delta's 95% interval straddles zero. The two survivors of the fit
window (`tp 5.0` +0.121, `tp 4.0` +0.073 — both CIs excluding zero on fit) shrink
to +0.045 and +0.028 on the holdout and lose significance: that is what a fit
artefact looks like. `hold 60` has the largest holdout delta (+0.138) and the
widest interval (−0.111..+0.387) — noise, not an edge. Tightening the stop never
helps out of sample either (−0.069 and +0.001).

And the level matters more than the ranking: **every variant, shipped included,
is net negative per dollar on the holdout** (−0.0033 to −0.0057). The exit is not
one knob away from profitability on this window.

So, per the same discipline as the entry side:

- **live exit behaviour is unchanged** — no stop widened, no death-line relaxed,
  no protective exit delayed, nothing turned on;
- the negative result is **stamped on every fire and carried to every close**
  (`exit_study` / `exit_study_v` in the watch row and through `_family_fields`),
  with the shipped baseline's holdout win rate and net per dollar, every
  variant's delta and CI, and the verdict string;
- the measurement is reproducible: `--analyse-only` re-runs the table from the
  recorded entry paths without touching the tape again.

## What this says about the +80% question

Selection was worth +0.11%/trade out of sample (`pos_hour`). The exit sweep finds
no knob that pays at all, and the whole arm set is negative net. On this evidence
the remaining lever is **not** another scalar on the exit — TP/stop/hold are all
flat within noise — but the shape of the trade: which coins are allowed to be
champions at all, and whether a 60-minute-horizon edge of +0.1% can survive the
0.08% round trip plus the coin's own noise. Closing that gap needs either a much
larger per-trade edge or far fewer, higher-conviction entries — not a tighter
stop.
