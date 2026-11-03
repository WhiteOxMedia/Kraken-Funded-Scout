# SYNC_BOT — what the XRP bot shares with the scanner

> **Purpose:** every change to a *shared* contract must update or re-verify the bot side.
> This note is the checklist. Last verified live: **2026-09-21**.

## The one rule

If the bot reads it, the change isn't done until the bot side sees it.
The bot runs in its own workspace (`C:\Users\birds\Workspace\kraken_xrp_trading_bot`)
and process — it only knows what this repo publishes.

## What is shared (live-verified 2026-09-21)

| Contract | Direction | Mechanism |
|---|---|---|
| **Buy / sell signals** | scanner → bot | `signals.db` (`signal_bridge.write_buy_signal` / `write_exit_signal`; bot polls `get_pending_signals`, acknowledges, fills, closes) |
| **Trailing high + trail stop** | scanner tracks, bot/sold flow read | `signals.highest_price` via `update_trailing_high` / `check_trailing_stop` (Sep 21: trail-just-under-recent-high after goal hit) |
| **EXIT WATCH switch** | console → both processes | `console_state.json` `exit_watch` read by `exit_watch_enabled()` (30s cache, fail-open). Silences the *trend-exit brain* on both sides; the mechanical ladder is never paused. |
| **Tide stamp + OUT bar** | scanner → bot | `signals.tide_state` / `tide_pct` / `tide_bar` columns |
| **Tide-bar re-arm gate** | shared verdict | `signal_bridge.bot_tide_rearm_gate` — bot's reader calls it per priced OUT pass (fail-closed both ways) |
| **Kill switch** | either side | `kill_switch.lock` in this folder — checked before every new signal |
| **Contest watch** | scanner → bot | `contest_watch.json` (`CONTEST_WATCH_JSON_BOT` in bot_engine) |
| **Circuit breaker** | shared | `daily_balance` table + `MAX_DAILY_LOSS_PCT` |

## How the bot imports this repo (the import-order fact)

The bot keeps a **shadow copy** of `signal_bridge.py` in its own folder, but the
reader (`scout_signal_reader.py`) inserts this repo at `sys.path[0]` **before**
its first `signal_bridge` import — first import wins, so the live bot process
resolves **this repo's current copy** (proven with the bot's own venv
interpreter, 2026-09-21: resolved `kraken_scout\signal_bridge.py`,
`bot_tide_rearm_gate: True`). The engine's direct-bridge sites re-insert the
path defensively; three entrypoints (`bot_runner`, `bot_service`, `bot_engine`)
import nothing bridge-related before the reader runs.

**Never rely on the shadow copy being stale-safe: keep it current** (see below).

## Sync procedure (for any shared-contract change)

1. Make the change in this repo.
2. Re-verify the bot side resolves it:
   `bot_venv python -c "import sys; sys.path.insert(0, r'C:\Users\birds\Workspace\kraken_xrp_trading_bot'); import signal_bridge as sb; print(sb.__file__, hasattr(sb, '<the new name>'))"`
3. Refresh the bot's shadow copy (protects every import order):
   `cp signal_bridge.py C:\Users\birds\Workspace\kraken_xrp_trading_bot\signal_bridge.py`
   then `py_compile` it there.
4. If the bot needs *new* behavior (not just data), edit `scout_signal_reader.py`
   in the bot repo — do not grow reader logic here.
5. Note the change under "Recent syncs" below.

## Recent syncs

- **2026-09-21** — Exit Check no longer treats the window's end as an exit
  (scanner-side); trail-after-goal landed scanner-side (`check_trailing_stop`
  logic unchanged, new usage). Bot-side shadow bridge refreshed to the current
  full contract (914 lines, compiles under the bot's venv, full surface present:
  re-arm gate, trail check, ladder). Live verification: EXIT WATCH switch ON
  (fail-open), scout copy resolves in the bot's interpreter with the reader's
  path insertion, bot repo's pending work left untouched.
