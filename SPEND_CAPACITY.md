# What a coin can carry — the champion question, priced (Nov 15)

> The idea: *what if the tournament's champion were the coin that could support
> the highest spend?* This documents what was built to test it, and what the
> stored closes say.

## 1. The deep scan (built)

The tournament used to judge a coin on one 200-candle window plus a ticker — a
quick check. `spend_capacity.deep_scan()` now gives every candidate a real read,
and `scanner._evaluate_single` / `scan_pairs` attach it as `ScanResult.spend`:

| field | what it is | where it comes from |
|---|---|---|
`bars`, `span_days` | the coin's own deep history (one fetch, `deep_lookback_candles` = 1000 hourly bars ≈ 41 days) | `kraken.fetch_ohlcv(..., 60, 1000)` |
`dd_4h_pct`, `dd_24h_pct` | the empirical worst peak-to-trough drop inside those windows | measured from that history, no lookahead |
`abs_move_med_pct`, `abs_move_p95_pct` | how much the coin normally moves | same history |
`dd_profile_pct`, `dd_src` | the profiled window drawdown the 3% guard prices | volatility engine cell, else 3% (the engine's own conservative default) |
`dd_pct` | the number `cap_usd` is priced with — the **profile's**, i.e. the guard's, so the tournament and the money never disagree about one number | rule stated in code |
`dd_empirical_pct` | the worst drop the deep history actually proves | measured from that history |
`day_range_pct`, `pos_in_day_pct`, `volume_24h_usd`, `spread_pct` | the market read | one ticker |
`cap_usd`, `cap_frac_pct` | **the dollars the guard would allow on this coin** — what live sizing allows | `contest_account.max_entry_usd(balance, dd_pct, start)` |
`cap_deep_usd` | what the guard would allow if priced on the empirical worst window (evidence, never substituted) | same function, empirical drawdown |
`src` | which sources answered (`deep_history`, `eval_window`, `ticker`, `no_capacity`) | recorded, never implied |

`deep_scan_enabled` defaults **True** (data only — nothing here sizes, exits or
gates a live order). A coin that cannot be priced returns a dict that says so
rather than raising. Two cost controls, both measured: the read is taken only
for an **actionable** signal (`buy`/`sell`) — a pass of holds pays nothing — and
a 10-minute cache keeps a 54-pair scan from re-fetching the same tape. Measured
on the live venue: 721 hourly bars per coin (Kraken caps the request at ~720,
so 30 days), 1.9 s per coin end to end.

## 2. The decision, in shadow (built)

`spend_capacity.verdict()` — pure, one record per fire — names the candidate
within `spend_rank_window` (10 confidence points) of the champion that could
carry the most spend, by how many dollars (`spend_rank_min_usd` = 25), and
whether the bracket would have changed:

- `hold` — the champion already carries the most, or the edge is under $25
- `prefer` — a near-equal rival carries materially more (`from`/`to`, `gain_usd`)
- `no_spend` / `insufficient` — nothing to rank, or the champion has no capacity

`config.spend_rank` decides whether it is **honoured**; it ships **False**, so
the champion does not move. The record travels `Signal.spend_rank` +
`Signal.spend_capacity` → `auto_track_popup` → watch row (`spend_rank`,
`spend_capacity`, `spend_v`) → the journal through `_family_fields()`, the one
helper both close routes use. Every fire is stamped, bracket or not.

Ordering: when both `spend_rank` and `entry_timing_rank` were ever on, the spend
ranking runs second and has the last word — its record says so.

## 3. The study — `scratch/spend_capacity_study.py`

Real closes, capacity recomputed per row from the guard's own function and the
`balance_after` that row recorded.

**2,681 of 2,706 closes usable** (25 excluded: no positive balance or no
capacity). Drawdown uses the *current* volatility cell, not the one in force
that day — a limit, stated.

### Does the cap bind?

| measure | value |
|---|---|
closes where size ≥ 95% of the guard cap | **125 of 2,681 (4.7%)** |
size / cap | median **0.16**, p10 0.04, p90 0.61 |
cap range | $12 – $11,228; size range $1 – $10,575 |

The entry a fire wants is already far below what the guard allows: at
`contest_size_frac` 0.09 the cap is roughly 3× the ceiling. **Ranking on capacity
cannot put more dollars to work — it can only reorder which coin is bought.**

### Does capacity predict the close?

| capacity band | n | win | mean close | net | net per $1 |
|---|---|---|---|---|---|
cap < 10% start | 772 | 38.6% | +0.00% | −$314 | −0.00246 |
10–20% | 538 | 40.5% | −0.02% | −$158 | −0.00061 |
20–30% | 421 | 36.3% | −0.14% | −$624 | −0.00230 |
30–50% | 392 | 39.8% | −0.06% | −$882 | −0.00240 |
≥ 50% start | 558 | 40.9% | +0.04% | −$84 | −0.00015 |

Rank correlation (capacity vs realized close %): **+0.018** at n=2,681 — no
usable relationship. Upper half of capacity: win 38.9%, net −$1,593; lower half:
win 39.6%, net −$470. High-carry coins were, if anything, slightly worse.

### Verdict

**The idea is a no-op where it would matter and unpriced where it would not.**
The cap binds on 4.7% of closes, so preferring a bigger-carry coin changes which
coin is bought, not how much is at work — and the coins that carry more have not
closed better (+0.018 rank correlation, upper half slightly negative). `spend_rank`
stays **off**. The evidence gate for flipping it is the shadow field: once enough
fires carry `spend_rank.would == "prefer"`, grade those rows' realized closes
against the champion that actually fired — that is the counterfactual this study
deliberately does not claim to measure.

## 4. What would actually raise the spend

If the goal is more dollars per trade, the binding number is
`contest_size_frac` (0.09 of balance) and the buffer — not the per-coin cap.
Raising size is a risk decision (it moves the death-line exposure), so it is the
owner's call, not a tournament tweak. Nothing in this pass changed a size rule.
