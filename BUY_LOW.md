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

- the deep read runs in the **running scanner**, every full pass, over the top of
  the bracket (six candidates), and logs before the crown:

  ```
  DEEP PASS: 6 candidate(s) read deep, no buy among them — readings CRV 0.6578, …
             (min_conf 48, 720 bars, src=deep_history,ticker)
  DEEP CHAMPION ZEC: cap $2670, cheap 0.18 (hour+day), dd 3.6%, src=deep_history,ticker
  ```

- every fire stamps `spend_capacity`, `spend_rank`, `buy_low`, `buy_low_rank` on
  the watch row and carries them to the journal (`_family_fields`);
- `entry_timing_rank`, `spend_rank`, `buy_low_rank` are all **False**.

### The fire path, proven on live data without an order (`scratch/fire_probe.py`)

**Multi-candidate record (v2, 05:22 MST).** The probe walks the live book
(`cfg.trading_pairs`, 54 pairs) and the ranking now records every priceable
candidate it read — not only the buys — marking which ones could actually be
crowned. KAITO/USD row:

```json
"buy_low_rank": {"v": 2, "flag": "buy_low_rank", "on": false, "applied": false,
 "would": "hold", "pool": 2, "eligible": 2,
 "compared": [
  {"pair": "KAITO/USD", "cheap": 0.6448, "conf": 33.1, "src": "hour+day", "action": "buy",  "eligible": true},
  {"pair": "CRV/USD",   "cheap": 0.6174, "conf": 32.7, "src": "hour+day", "action": "buy",  "eligible": true},
  {"pair": "TRX/USD",   "cheap": 0.229,  "conf": 55.6, "src": "hour+day", "action": "sell", "eligible": false},
  {"pair": "SOL/USD",   "cheap": 0.2075, "conf": 39.5, "src": "hour+day", "action": "sell", "eligible": false},
  {"pair": "POL/USD",   "cheap": 0.1801, "conf": 39.6, "src": "hour+day", "action": "hold", "eligible": false},
  {"pair": "PNUT/USD",  "cheap": 0.172,  "conf": 39.5, "src": "hour+day", "action": "hold", "eligible": false}],
 "champion": {"pair": "KAITO/USD", "cheap": 0.6448, "src": "hour+day"},
 "cheapest": {"pair": "KAITO/USD", "cheap": 0.6448, "src": "hour+day"},
 "gain_cheap": 0.0, "min_gain": 0.15, "would_change": false}
```

The champion was already the cheapest eligible buy (CRV is 0.027 more extended),
so the explicit verdict is **hold — the bracket would not change**, stated on the
record itself rather than left to inference.


The probe drives the loop's own `deep_crown_pool()` and then the loop's own
`auto_track_popup()` — the same two functions a live fire calls — against the
current Kraken read, with the watch file pointed at `scratch/_probe_watch.json`.
CRV/USD, 15 Nov 05:08 MST, 720 bars:

```json
"spend_capacity": {"src": ["deep_history", "ticker"], "bars": 720, "span_days": 30.0,
  "pos_in_day_pct": 25.5, "off_high_pct": 11.018, "off_low_pct": 3.912,
  "dd_pct": 3.3, "dd_src": "volatility_profile", "dd_empirical_pct": 19.824,
  "cap_usd": 2913.02, "cap_deep_usd": 484.92, "cap_frac_pct": 29.2}
"buy_low": {"cheap": 0.6473, "pos_hour": 0.4505, "pos_day": 0.255, "src": "hour+day"}
"buy_low_rank": {"flag": "buy_low_rank", "on": false, "applied": false,
  "would": "hold", "pool": 1,
  "compared": [{"pair": "CRV/USD", "cheap": 0.6473, "conf": 38.6, "src": "hour+day"}]}
"spend_rank": {"on": false, "would": "hold", "cap_usd": 2913.02}
```

`cap_usd` is the guard's number and `cap_deep_usd` sits beside it, never in
place of it. Balance before/after: identical ($9,982.73), live watch file
untouched, 0 open rows before and after. The pool held one coin because the
whole liquid book had exactly one buy that night — the multi-candidate
comparison is covered by `test_buylow` 3.1–3.7 (two coins, EXT vs LOW).

### Two bugs the live run found (both fixed)

1. **The loop never read deep at all.** `scanner.tournament_scan` — where the
   deep read and the three rankings were wired — is not what the running
   scanner uses to crown. Its full-scan loop calls `evaluate_strategy` directly
   and picks max confidence, so no live fire could ever carry a deep field. The
   loop now builds reads for its crown pool, runs the rankings in order and
   stamps the winner (fail-soft, flags off ⇒ no decision moves).
2. **Every deep read said `no_history`.** The loop hands `deep_scan` a bare ccxt
   exchange, not `KrakenAPI`; the read called `fetch_ohlcv(pair=…,
   interval_minutes=…)`, which ccxt rejects, and the fallback swallowed it. A
   candidate like ZEC was priced on its ticker alone (`src=no_history,ticker`).
   `_fetch_history()` now tries the wrapper shape, then ccxt's positional
   `(symbol, '1h', None, limit)` with the limit capped at Kraken's 720 ceiling.
   Verified live: same coin now reads `720 bars, src=deep_history,ticker`.
