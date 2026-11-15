# Ledger change: backfilled setup stamps on 2,601 closes

**What changed.** `contest_trades.jsonl` now carries the fire-time setup stamp on
**2,601 of 2,706 closes (96.1%)**, up from 38. `backfill_setup_stamps.py` joined
the untagged closes to their fire-time BUY popup in `popups.jsonl` and wrote
three keys on 2,563 rows that had none:

| key | value |
|---|---|
| `setup` | the dict produced by the production `_setup_tags` (route, tide, tide_pct, brain, climb, min_size, fast/re-entry/hail, …) |
| `setup_v` | `SETUP_TAGS_VERSION` (currently 1), taken from `setup["v"]` |
| `setup_src` | `"popup-backfill"` — marks a derived stamp, so it is never read as a fire-time one |

No other key was added, removed, or changed, and no row was reordered, added or
dropped. Verified against the pre-migration backup: 2,706 rows on both sides,
identical `sold_at` and `pair` sequences, 86 rows with added keys in the final
run (2,563 in total across the two runs), **0** value changes on any existing
key, net total unchanged at **−$2,257.59**.

## The join, and why it is trustworthy

Match on **ticker + (sold_at − held_min) + the popup's own goal equals the
close's `goal_pct`** (±0.06), nearest popup wins. A second popup within two
minutes that *disagrees* on the stamp fields is left untagged rather than
guessed (7 rows). Where `held_min` is missing or wrong the join cannot carry
the time, so it demands a unique goal match over six hours plus an entry-price
check against the popup's printed cash-out price.

Validation: re-running the extractor over the 38 closes that already carried a
fire-time stamp reproduces them exactly — tide **37/37**, breadth **37/37**,
climb verdict **37/37**, min-size **37/37**, brain **37/37**. Median fire-time
error on the join is 16 s (p90 28 s).

Two corrections that validation forced, both visible in the data:

* the popup's `NN% SURE` is the **brain** read ("the brain reads the setup at
  NN%"), not the fire's `signal.confidence`. Backfilled rows therefore carry
  `brain` and leave `conviction` **null**. Writing it to `conviction` would have
  mislabelled 24 of 37 known stamps.
* `min_size` is the `WEAKENED, SIZED TO PAY` / `MIN SIZE` title tail, not the
  literal phrase alone — that rule reproduces min-size on 37/37.

## What is deliberately not written

* `brain` / `mult` / `clearance` / `er` / `size` / `base` stay null unless the
  popup printed them; `null` from `_setup_tags` means "not read", which is true.
* `strategy_v` is **not** written. The strategy generation at fire is not
  knowable for these rows, and inventing it would make the `strategy_v`
  partition lie.
* Rows without a popup keep no stamp at all rather than a partial one.

## The 105 closes that remain untagged

| n | period | why no source |
|---|---|---|
| 46 | Sep 3–20 | no capture exists: `popups.jsonl` starts Sep 21, oldest scanner log is Sep 30 |
| 25 | Sep 21 – Oct 2 | fires that emitted no BUY popup (re-fires / already watched) |
| 18 | Oct 3–30 | no scanner log covers these dates at all |
| 16 | Nov 1–7 | re-fires: `ONE-WATCH: <TICK> already watched — no BUY popup` |

Scanner logs cannot close these: of 653 `AUTO-TRACK` fire records parsed from
every log, only 3 carry a climb verdict, and **every** `CLIMB-OUT` line in the
logs is a named gate *failure* — the logs never print a PASS. Since the family
key is `route/tide/climb`, a log-derived stamp could only ever assert
`climb:FAIL` and could never tell "passed" from "never evaluated".

## Auditing and rollback

`backfill_setup_stamps.py` is idempotent by construction: it skips any row whose
`setup` is a non-empty dict, so re-running it only touches new untagged closes.
Without `--apply` it prints the join plan and writes nothing. Each `--apply`
copies the ledger to `contest_trades.jsonl.bak-<UTC timestamp>` first and writes
through a temp file plus `os.replace`.

To roll back, restore the backup from before the run you want undone:

```
cp contest_trades.jsonl.bak-20261114-194827 contest_trades.jsonl
```

To re-derive the study from the stamps:

```
./.venv/Scripts/python.exe -X utf8 diag_setupfamily.py
```
