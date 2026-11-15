# Entry timing: the tournament already buys low — and the sample wall is gone (Nov 15)

Two questions this turn. **Does the shipped entry buy low and sell high well enough?**
And the one underneath every study this week: **why is every answer 12 trades wide?**

---

## 1. What the shipped entry books

2348 real entries replayed on the stored 5m closes over 45 days, each re-entered
three other ways with **the same exit** (hard stop −3%, the profit-guard trail, the
180m close) so only the entry moves:

| | fit n=1408 | holdout n=940 |
|---|---|---|
| **shipped booked%** | **−0.159** | **−0.253** |
| $ over 45d | **+$233.11** | **−$1732.20** |
| $ per trade | **+$0.166** | **−$1.843** |
| hit rate | 39.9% | 38.1% |
| mean MAE | −1.246% | −1.164% |
| 1% / 5% tail | −3.08 / −3.08 | −3.08 / −3.08 |

The sign flip between the two spans is the whole story of the contest so far: the
same entry rule that books +$0.17/trade on the earlier span loses −$1.84/trade on
the later one. **The entry is not the leak the fit window says it is** — and the
exit study already showed the exit is near-free against holding. What is left is
the candidate flow itself.

## 2. Every variant, against the shipped entry

| variant | fit n | fit $/trade | holdout n | holdout $/trade | holdout hit% | mean MAE | 1% / 5% tail | verdict |
|---|---|---|---|---|---|---|---|---|
| shipped | 1408 | +0.166 | 940 | **−1.843** | 38.1 | −1.164 | **−3.08 / −3.08** | — |
| next5m (one bar's confirmation) | 1408 | +0.371 | 940 | −1.835 | 36.9 | −1.181 | −3.08 / −3.08 | **indeterminate** |
| pullback to the hour's low decile | 1408 | −0.679 | 940 | −2.096 | 36.2 | −1.281 | −3.08 / −3.08 | reject |
| extension cap 1.0% over base (skips the chase) | 1117 | −1.770 | 756 (184 skipped) | −2.069 | 37.0 | −1.135 | −3.08 / −3.08 | reject |

Hard stop −3% in every row; the tails are identical because the stop binds them all.

**Why the entry choice stands.** Waiting for a pullback is the *opposite* of an
edge here: it buys −$2.10/trade against −$1.84 and lifts the MAE (−1.28% vs
−1.16%), i.e. it buys later into weakness that keeps going. The chase guard
(`ext_cap`) is worse still: skipping 184 chases at 1% over the base removes
**+$168** of the holdout's gross and leaves −$2.07/trade, so the "chase" entries
were on average the *better* ones. The scanner's entry is not chasing a pump it
should sit out; by 4-hour extension the pool is compressed (median 0.74% spread),
so a 1% cap is mostly cutting normal range.

**Why `next5m` is not shipped, despite clearing the letter of the bar.**
It passes the rule as written — better $/trade (+$0.008) and identical tails — but
that is not a win, it's a coin flip:

| span | paired Δ per trade | 95% CI | t |
|---|---|---|---|
| fit | +$0.205 | −$0.117 … +$0.527 | +1.25 |
| holdout | **+$0.008** | −$0.260 … +$0.276 | **+0.06** |

The fit effect (+20¢) collapsed by 96% out of sample to eight tenths of a cent,
with a confidence interval 34× the point estimate. Shipping a one-bar delay on
that would be noise-fitting dressed as a rule — and this session's standard is
that a variant must *genuinely* clear. So the live entry path is **untouched**:
no flag, no delay, no cap, no pullback. `git log` on the live files this turn
touches only `near_miss` wiring.

## 3. The sample wall — and the shadow recorder that removes it

Every study hit it: 12–15 holdout trades. The gate and the crown produce far more
*decisions* than fills — a champion vetoed at `entry_quality 0.78` is evidence —
and until now those decisions were logged with a verdict line and nothing else.

`near_miss.py` (wired into `crown_cycle`) now journals, for each cycle:

- the **vetoed champion** and every pool candidate within 0.15 of the gate,
- `pair`, `ts`, `entry_quality`, `confidence`, `ext4h` (4-hour extension),
  `base_pct` (price over the hour's low), `gate_min`, and the **reason**
  (champion veto / near miss / mover-bridge refusal).

Each full scan then calls `near_miss.resolve()`, which fills every row older than
the 180m hold window from real 5m closes: `fwd_pct`, `mae_pct`,
`hit_hard_stop` (≤ −3%), `bars`. It is a shadow in the strict sense — one journal,
`contest_near_miss.jsonl`, appended by the cycle and rewritten only by its resolver.
No watch row, no bridge, no order, no balance: `test_shadownearmiss.py` proves the module
contains no order/watch path (17 checks) and the live proof hashes
`contest_account.json` and `contest_watch.json` before and after a seed+resolve run.

**What it makes measurable:** the gate's own counterfactual at every near-miss —
the distribution of forward returns for the fires it refused, split by
`entry_quality` and `ext4h`, at no cost to balance. That is how a future
threshold or a better-timed mover pick gets a sample of hundreds instead of the
12 the journals gave this week.

---

*Companion to `ENTRY_TIMING.md` (the Aug–Nov entry diagnosis, untouched).*

*Files: `scratch/entry_timing_variants.py` → `scratch/_entry_variant_result.json`,
`near_miss.py` → `contest_near_miss.jsonl`, `test_shadownearmiss.py`,
`scratch/near_miss_proof.py`. The exit question is settled in `EXIT_VARIANTS.md`;
the picker question in `PICKER_STUDY.md`.*
