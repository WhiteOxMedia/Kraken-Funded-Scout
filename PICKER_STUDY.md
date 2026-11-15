# Does timing the champion beat crowning the loudest? (Nov 15)

`scratch/picker_study.py` rebuilds the live crown pool at every recorded decision
point (buys ≥ 48 confidence, top six by confidence — exactly `crown_cycle`'s pool),
measures each candidate's position in its prior-hour range (`eq`), how far it has
already run in four hours (`ext4h`), the forward return over the live 180-minute
hold net of the 0.080% round trip, and the worst adverse excursion inside that
hold (the drawdown that walks toward the death-line).

**154 cycles · fit 92 · holdout 62 (disjoint, chronological) · tuned on fit only.**

## The comparison

Per-cycle return counts a gate-vetoed cycle as 0 (that is what the live book
does). Hit rate is over cycles that actually traded.

| picker | span | cycles | trades | ret/cycle | 95% CI | hit% | ret/trade | mean MAE | worst MAE |
|---|---|---|---|---|---|---|---|---|---|
| **live_today** (loudest, gate may veto) | fit | 92 | 24 | +0.2652 | 0.062..0.468 | 70.8 | +1.017 | −0.44 | −1.77 |
| **live_today** | **holdout** | 62 | 12 | **+0.1552** | −0.039..0.349 | **75.0** | +0.802 | −0.34 | **−0.867** |
| gated_conf (loudest that passes) | holdout | 62 | 15 | +0.1558 | −0.038..0.350 | 73.3 | +0.644 | −0.39 | −0.947 |
| least_ext (earliest in its move) | fit | 92 | 37 | +0.3056 | 0.080..0.531 | 67.6 | +0.760 | −0.45 | −1.77 |
| least_ext | **holdout** | 62 | 15 | **+0.1699** | −0.026..0.366 | 73.3 | +0.702 | −0.37 | **−0.947** |
| lowest_eq_passer (buy the cheapest) | holdout | 62 | 15 | +0.0405 | −0.161..0.242 | 60.0 | — | — | −2.891 |
| score = conf − k·ext4h, k ∈ {5,10,20,40,80} | holdout | 62 | 15 | +0.153..+0.156 | — | 73.3 | — | — | −0.947 |
| tie-break: least extended within 3 confidence | holdout | 62 | 15 | +0.1558 | — | 73.3 | — | — | −0.947 |

## Verdict: nothing ships — the data says the timing dimension is too thin to matter

The one variant that gains return — the least-extended gate-passer, +0.1699 vs
+0.1552 per cycle — buys that gain **by lowering the hit rate (73.3% vs 75.0%) and
deepening the tail (−0.947% vs −0.867% worst MAE)**. It fails the stated bar on two
of three counts, and its return edge (+0.015/cycle) is inside the CI of both
pickers (−0.026..0.366 vs −0.039..0.349). Buying the cheapest passer is plainly
worse (+0.04/cycle, 60% hit, −2.9% tail).

The reason is visible in the pool itself: among gate-passing candidates in the same
cycle, the **confidence spread has a median of 23 points while the four-hour
extension spread has a median of 0.74%**. There is no cycle where "this one is
earlier in its move" is information the confidence ranking doesn't already swamp —
an extension penalty needs k > 80 before it changes a single pick, and then it just
becomes `least_ext` again. Crowning the loudest, then refusing to buy anything that
is not a breakout, is the best-measured rule on this data.

So the shipped selection is unchanged: `crown_cycle` still crowns the loudest
eligible buy and the breakout gate still decides whether it may fire. Nothing was
tuned into the live path; the timing dimension is recorded here as measured and
rejected rather than quietly applied.

**What the numbers say instead:** the win rate comes from *refusing* cycles (the
gate vetoes 62 → 12–15 trades and lifts the hit rate from 27% unfiltered to 75% on
the holdout), not from choosing better among the coins that pass. The next honest
lever is not a finer ranking inside the pool — it is either more cycles (so a
0.7%-wide extension spread has the sample to prove itself) or a candidate set wide
enough that extension actually varies.

## One real fix found by this pass

Running the live scanner surfaced a genuine bypass: movers **outside the 54**
write their buy straight to the bridge and skip the popup path where the gate
sat (`MOVER TREAD -- bridge-only` at 08:01). The choke now runs before that
bridge write (`route="mover-bridge"`), and the duplicate judgement at the popup
was removed so each fire is judged once. DCA re-buys into an already-gated
position are deliberately not gated: they are continuation, not a new entry.
