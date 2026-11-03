# Finish the resolve-gate fix: measure the $20 scalp, not TP distance

## Context
User asked "Why has there been no desktop alerts?" — diagnosis: the resolve gate (a trade must hit its goal within the rotation's allowance) was implemented as **time-to-TP**, but the user's system resolves at **+$20 on $5,000 = +0.4%**. Every 92% breakout (BTC, ETH, VIRTUAL, FARTCOIN, RENDER — TP-based ETAs 10–46h) was suppressed as "too slow" even though their $20 scalp is minutes-to-an-hour away at rotation pace. Fix was started and interrupted mid-edit:

- `_hours_to_scalp(pair, entry)` EXISTS (background_scanner.py:497) — references `SCALP_TARGET_PCT`, which is **NOT defined** → NameError if called
- All three gate call sites still use TP-based `_hours_to_goal` / `_time_to_goal`

Sanity check of the new math: R3 Monday pace avg_move ≈1.24% → scalp ETA = (0.4 / 1.24) × 4 ≈ **1.3h** → passes the 4–6h cap. The exact wave that was blocked now passes. Genuinely dead coins (avg 0.3%, ETA 5.3h, slow rotation cap 4h) still get blocked — correct.

## Scope & Non-Goals
- **In scope**: define the constant, swap the 3 gate call sites, make the "FOR:" hold-time line honest (scalp-based), update audit contract + tests, commit.
- **Non-goals**: no changes to `_max_resolve_hours` (pace caps + midnight clamp stay), no changes to the bot, no changes to the SHOULD I SELL verdict display (TP ETA there is display-only "how far to the win price"), no new features.

## Implementation Plan

### 1. `kraken_scout/background_scanner.py`
- **Add constant** next to `TAKE_IT_USD = 20` (line ~395):
  `SCALP_TARGET_PCT = 0.4  # +$20 on $5,000 — the take the user actually plays for`
- **`send_alert` resolve gate** (line ~874): swap the BLOCK decision to
  `_hours = _hours_to_scalp(pair, signal.entry_price)` — keep `_max_resolve_hours()` cap.
- **`hold_txt` / FOR line** (lines ~879–886): derive from the **scalp** hours (what they actually wait — "~1 hour", not "~26 hours"); keep existing bands. The alert body keeps `cash out at $TP (win)` as the win-price reference.
- **TOO SLOW log** (line ~876): reword to "~Xh to the $20".
- **Bounce gate** (lines ~1143–1156): swap `_b_hours = _hours_to_goal(...)` → `_hours_to_scalp(pair, signal.entry_price)`; update its TOO SLOW log the same way.

### 2. `kraken_scout/app.py` — green light gate (line ~1483)
- Import `_hours_to_scalp` alongside `_max_resolve_hours`.
- Gate: block green light only when `_scalp_h is not None and _scalp_h > _resolve_cap()` (computed at live price `px`).
- Too-slow toast text (line ~1505): "~Xh to the $20" phrasing.
- `_time_to_goal` stays for the exit verdict's "HITS GOAL IN" (display-only).

### 3. `kraken_scout/full_audit.py` — design contract (line ~160)
- "adaptive resolve rule present": require `_hours_to_scalp` + `SCALP_TARGET_PCT = 0.4` + the swapped gate call (`_hours = _hours_to_scalp` in send_alert).
- "resolve rule on the green light too": require `_hours_to_scalp` in app_src.

### 4. Tests
- `test_takeit.py`: add import check for `_hours_to_scalp` + `SCALP_TARGET_PCT` presence + numeric sanity (avg 1.24% → ≈1.3h, under cap).
- `test_no_alerts.py` (the diagnostic that found the bug): swap verdict computation to `_hours_to_scalp` so a re-run proves the blocked wave now passes.

## Verification
1. `python -m py_compile` on background_scanner.py + app.py.
2. Run `test_takeit.py` → `takeit_check.txt` (all checks pass, new scalp checks pass).
3. Run `test_no_alerts.py` → confirm previously-blocked 92% picks (FARTCOIN/RENDER etc., if still in the pool) now show "should have POPPED".
4. Run `full_audit.py` → all contracts green.
5. Commit: `git commit -m "Resolve gate measures the $20 scalp (+0.4%), not TP distance"`.
6. User relaunches **Kraken** (single desktop shortcut) — scanner picks up the fix.

## Note
One judgment call: the alert's "FOR:" line goes scalp-based (the time they actually hold) while the body keeps the TP price as the "win" reference — earlier I'd said "display keeps TP estimate", but a "~26 hours" hold time on a $20 scalp would be misleading. Flagging in case the user prefers TP there.
