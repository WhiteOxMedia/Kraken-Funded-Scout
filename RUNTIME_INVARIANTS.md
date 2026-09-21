# RUNTIME INVARIANTS — kraken_scout

**Runtime state is never stashed, never committed, never auto-saved by hooks.
Runtime directories are owned exclusively by the running scanner.**

These invariants are policy AND enforcement. The guard tests below fail the
suite (and the pre-commit hook blocks the commit) if any of this regresses.

---

## What counts as runtime state

Any file the running stack *writes while trading*:

- **Live positions & books:** `contest_watch.json`, `contest_account.json`,
  `contest_trades.jsonl`, `take_payouts.json`, `signal_snapshots.jsonl`,
  `snapshot_outcomes.jsonl`, `pending_check.txt`
- **Scanner memory & caches:** `signals.db`, `ohlcv_cache.db`,
  `rotation_thresholds.json`, `rotation_lockout.json`, `climb_levels.json`,
  `volatility_universe.json`, `sure_thing_autoweights.json`, `coin_floors.json`,
  `tide_bar_*`, `discipline_state.json`, `scanner_state.json`, `scanner.lock`
- **Voice & logs:** `voice_log.jsonl`, `speech_queue/`, `scanner.log`,
  `logs/`, every `*.log`
- **Console state:** `console_state.json`, `console_prefs.json`,
  `popup_suppressions.jsonl`

Live **positions** (the rows in `contest_watch.json`) are real money in
flight. On 2026-09-21 the old pre-commit hook's stash dance reset this file
to `{}` **twice mid-session**, leaving a live FARTCOIN position unwatched
with no exit checks. That incident is the reason every rule below exists.

## The rules

1. **Never stashed.** No hook, script, alias, or human workflow may run any
   stash operation on this repository while the scanner runs — which is
   always. Enforced by `test_no_runtime_stash.py` and the pre-commit guard
   (a commit adding stash logic is blocked).
2. **Never committed.** Runtime state files must not be added to commits.
   The post-commit review filter class-filters them out of reviews; the
   tripwire reports any commit that touches them.
3. **Never auto-saved by hooks.** Hooks must not read, write, move, copy, or
   *reference* runtime paths. They review code only. Enforced by
   `test_hooks_ignore_runtime_state.py`.
4. **Runtime directories are owned exclusively by the running scanner.**
   Humans and tooling observe (read-only); nothing else mutates them. The
   `SOLD` button, the scanner's exit path, and the contest itself are the
   only writers of position state.
5. **A review that would require hiding the working tree is wrong.** The
   CodeRabbit review runs against the tree as-is. Churn is noise; noise is
   never an excuse to move files under a live trading system.

## Enforcement map

| Layer | Guard |
|---|---|
| pre-commit hook | blocks any staged diff adding stash logic; runs no file operations itself |
| `test_no_runtime_stash.py` | fails if hooks/scripts/aliases contain stash invocations; fails if `git stash list` is non-empty |
| `test_hooks_ignore_runtime_state.py` | fails if any active hook references runtime paths |
| `runtime_tripwire.py` | passive monitor: checksums runtime state, reports commits/stashes/scripts touching it |
| `safe_commit.py` | refuses to commit while the scanner is mid-write (live pid + watch rows) |

## If the stash list is ever non-empty again

Do not pop blindly. Audit content against the worktree first, quarantine
unique bytes OUTSIDE the repo (see `..\stash_rescue_2026-09-21\README.md`
for the 2026-09-21 precedent and method), then clear. Positions recovered
from a stash snapshot must be re-verified against the live feed.
