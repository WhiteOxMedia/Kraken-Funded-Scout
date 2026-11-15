# Buy low, sell high — the ranking and its grade (Nov 15)

> The idea: among near-equal signals, prefer the coin being bought **cheaply right
> now** — low in its prior hour and low in its day's range — instead of one that
> has already run. This is what was built to test it, and what the data says.

## 1. What it computes (`buy_low.py`)

Read from the deep per-coin read (`spend_capacity.deep_scan`) plus the fire's own
`entry_quality`:

| component | meaning |
|---|---|
`pos_hour` | where price sits in the prior 12 hourly bars (the live `entry_quality`) |
`pos_day` | where price sits in today's range (`pos_in_day_pct / 100`) |
`cheap` | **1 − mean(available components)** — 0.0 at the lows, 1.0 extended |
`src` | which components were actually measured (`hour`, `day`, `hour+day`) |

A coin with neither component is **not ranked** — `cheapness` returns `None` and
the verdict says `insufficient` / `no_read`. It is never defaulted to 0, which
would read as "the low" and quietly win every comparison.

The record also carries the context the decision was made in (`day_range_pct`,
`off_high_pct`, `off_low_pct`, `dd_pct`, `dd_empirical_pct`, volume, spread), so
a study can band the outcome without re-deriving it.

**It never prices anything.** Capacity stays `spend_capacity`'s job, whose
`cap_usd` is the guard's own number; the deep-history variant is reported beside
it as `cap_deep_usd` and never substituted.

## 2. The decision, in shadow

`buy_low.verdict()` — one record per fire — compares every buy candidate within
`buy_low_window` (10 confidence points) of the champion and names the cheapest:

- `hold` — the champion is already the cheapest, or the gap is under
  `buy_low_min_gain` (0.15 of range space)
- `prefer` — a near-equal candidate is materially cheaper (`from`/`to`,
  `from_cheap`/`to_cheap`, `gain`)
- `insufficient` / `no_read` — nothing measurable to compare

`config.buy_low_rank` decides whether it is **honoured**; it ships **False**. The
record lists what it compared (`compared`: pair, cheap, confidence, source) and
travels `Signal.buy_low` + `Signal.buy_low_rank` → `auto_track_popup` → watch row
(`buy_low`, `buy_low_rank`, `buy_low_v`) → journal through `_family_fields()`.
It runs **after** the entry-timing and spend rankings, so it sees the champion
they produced; with every flag off none of them changes an order.

## 3. The grade — `scratch/buy_low_study.py`

### A. The signal, on the stored closes (n = 2,536)

| band (cheap) | n | win | 95% CI | mean close | net $ | net per $1 |
|---|---|---|---|---|---|---|
≥ 0.75 (bought at the low) | 930 | 35.6% | 32.6–38.7 | −0.17% | −$1,727 | −0.00293 |
0.60–0.75 | 427 | 41.0% | 36.4–45.7 | +0.02% | −$160 | −0.00057 |
0.40–0.60 | 527 | 42.1% | 38.0–46.4 | +0.03% | −$244 | −0.00093 |
0.25–0.40 | 225 | 40.9% | 34.7–47.4 | +0.02% | −$203 | −0.00164 |
≤ 0.25 (extended) | 427 | **47.8%** | 43.1–52.5 | +0.16% | **+$311** | **+0.00098** |

**The cheap band does worse on every measure** — and the extended band is the
only one that made money. That inverts the earlier entry-quality finding (cheap in
the *hour* was mildly positive, 46.1% vs 39.0%) because this score also reads the
*day's* range: being at the bottom of the day, in this book, is where falling
knives are — the coin is low because it is still going down. The hour component
alone is a weak positive; the day component is a strong negative.

### B. The decision, replayed on the tape (paired, same horizon, costs included)

The stored closes can only grade the coin that fired, so B replays the choice: at
each decision point the scanner's own `evaluate_strategy` runs over every pair's
hourly tape, the buy candidates form the pool, and **the highest-confidence coin
is compared with the coin this ranking prefers**, both marked to the same 60-minute
horizon from the same tape, net of the round-trip cost the journal actually paid
(median 0.080%/trade).

```
20 days, every 6h: 77 decision points with >=2 buy candidates
                  17 of them where the ranking would have changed the coin
what fired (highest confidence)   n=17  win 23.5%  mean -0.401%  net/$1 -0.00401
what the ranking preferred        n=17  win 29.4%  mean -0.316%  net/$1 -0.00316
paired difference (preferred - fired): +0.086pp +/- 0.225 (95%)  n=17
all 38 paired points:                  +0.038pp +/- 0.100 (95%)  n=38
```

### Verdict

**The ranking points the right way in the replay and is not resolvable there
(+0.086 pp, CI −0.14 … +0.31), and on the real closes its own cheap band is
clearly worse.** Two honest conclusions, one decision: `buy_low_rank` stays
**OFF**. Buying "cheap in the day" is not a selling point in this book — it is a
description of a coin in a downtrend — and the replay's small edge is smaller than
its own noise. The flag stays off; the shadow record now accumulates the exact
comparison needed to revisit it, on the coins that actually fire, with real exits.

Sample needed: to resolve a +0.09 pp per-trade edge at the observed dispersion
(σ ≈ 0.47 pp) takes ~10⁵ pairs — i.e. this is not a question more of these tapes
will settle; it is one where the money must come from the exit side, not the
selection side.

## 4. What is live right now

- the deep read runs **in the running scanner** (pid reported in the delivery
  note), logging `DEEP <coin>: <bars> bars, dd … (volatility_profile), cap $…,
  daypos …, src=deep_history,ticker` for every actionable candidate;
- every fire stamps `spend_capacity`, `spend_rank`, `buy_low`, `buy_low_rank` on
  the watch row and carries them to the journal;
- `entry_timing_rank`, `spend_rank`, `buy_low_rank` are all **False**.
