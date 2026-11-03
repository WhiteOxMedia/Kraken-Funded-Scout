# Plan: Auto-Reset Positions on Time Block Rotation

## Context
The background scanner runs an adaptive schedule with 5 time blocks (Morning Rush, Midday, Afternoon, Evening, Night). Each block has different confidence thresholds. The user wants a clean slate each time the scanner rotates to a new block — all open positions should be sold on Kraken and the DB cleared for the new session. The qualifications are already high enough that all positions should be profitable, so there's no concern about selling at a loss.

The XRP trading bot already has `reset_positions.py` — a standalone script that:
- Fetches all open positions from the bot's DB
- Sells the full free balance of each position's base asset at market on Kraken
- Logs the trades with PnL
- Closes the DB positions
- Reports final state

We should reuse this existing script rather than building new sell-signal logic through the bridge.

## Scope & Non-Goals
**In scope:** Detect time block changes in the background scanner, run `reset_positions.py` on rotation, clear the signal bridge DB, notify the user.
**Not in scope:** Changing confidence thresholds, scan intervals, or strategy logic.

## Implementation Plan

### 1. Add `reset_all_positions()` to `signal_bridge.py`
New function that clears the Scout-side bridge DB:
- Mark all `filled` positions as `closed` (they'll be sold via reset_positions.py)
- Mark all `pending` signals as `cancelled` (stale from the old block)
- Log a summary count
- Returns dict with counts

**File:** `C:\Users\birds\Workspace\kraken_scout\signal_bridge.py` — add after `get_open_positions()` (~line 290)

### 2. Track time block changes in `background_scanner.py` main loop
- Add `current_block_name` variable, initialized to `None` before the while loop (~line 430)
- At the top of each loop iteration, get `block_name` from `get_time_block(hour)`
- If `block_name != current_block_name` and `current_block_name is not None`:
  - Log: "🔄 Time block changed: {old} → {new} — resetting all positions"
  - Run `reset_positions.py` via subprocess (from the XRP bot directory): `python reset_positions.py` in `C:\Users\birds\Workspace\kraken_xrp_trading_bot`
  - Call `reset_all_positions()` from signal_bridge to clear the bridge DB
  - Clear `alerted_recently` dict (clean slate for alerts)
  - Reset `last_full_scan = 0` to force an immediate tournament in the new block
  - Send desktop notification: "🔄 Market Rotation: {old} → {new}\nAll positions sold\nStarting fresh scan cycle"
- Update `current_block_name = block_name`
- On first iteration (when `current_block_name is None`), just set it without resetting

**File:** `C:\Users\birds\Workspace\kraken_scout\background_scanner.py` — in `main()`

### 3. Handle reset_positions.py running in a separate process
Since `reset_positions.py` is in the XRP bot's directory and imports from that project, we run it via subprocess with `cwd` set to the XRP bot directory. Use `subprocess.run` with a timeout of 60 seconds. Capture output and log it. If it fails, log the error but continue — the scanner shouldn't crash because the reset failed.

**File:** `C:\Users\birds\Workspace\kraken_scout\background_scanner.py` — in the rotation detection block

## Verification
1. Start the background scanner
2. Wait for a time block transition (or temporarily narrow a block's end hour to trigger sooner)
3. Check scanner logs for "🔄 Time block changed" message
4. Check that `reset_positions.py` ran — look for "SOLD" output in logs
5. Check `signals.db` — all filled positions should be closed, all pending cancelled
6. Verify desktop notification fires
7. Verify `alerted_recently` is cleared (new block can re-alert same assets)
8. Verify a tournament runs immediately after the reset
