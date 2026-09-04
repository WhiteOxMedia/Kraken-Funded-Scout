# One Entry Control + Real-Time Exit Data

## Problem

Two bugs in the contest watch, both corrupting the data the exit logic depends on:

1. **Entry semantics.** The "I RE-ENTERED" control (app.py `_render_reenter_control`) does a
   full dict replacement: `w[pair] = {"entry": ..., "high": entry, "size": spent, "added": now}`.
   Used to correct a deep-scan entry to the real fill, it wrongly:
   - takes a NEW timestamp (goal window clock / out-by time restarts),
   - WIPES `goal_pct` and `window_min` (goal call silently falls back to 1.5%),
   - resets the tracked peak (`high = entry`), so the trail guard forgets the real high.
   The "+/- position" control (Quick Check, app.py ~2752) does keyed updates and never resets
   the clock — but takes entry-price/size inputs (not the contest app's spent÷owned), and when
   it CREATES a new entry (manual thin-coin trades the scan rejected) it sets no goal, no
   window, and sometimes no size — PnL toasts then compute against a $2,500 default.
   "WATCH anyway" (conviction entries) has the same holes.

2. **Exit bar reads candle closes, not the live ticker.** `_run_exit_logic` (app.py ~2850)
   takes `live = df["close"].iloc[-1]` from 1h candles. Trail hit, goal call, PnL, and peak
   tracking all lag. The strategy sell bar (50% contest) evaluates candles that may not
   reflect the move happening now. Same pattern in the EXIT CHECK tab (~3717) and the
   scanner-side voice announcements.

## Decision (user, Sep 3): Option B — merge, don't keep separate

RE-ENTERED and +/- become ONE control with a mode toggle. One form, one language
(spent ÷ owned — the numbers the contest app actually shows), no ambiguity about
which tool to reach for.

## Part 1 — The unified entry control

New function `_render_entry_control()` in app.py, rendered from BOTH:
- the auto-watch panel (below the SOLD buttons — the sold → re-entered loop), and
- Quick Check (replacing the +/- column's entry/size inputs).

### The form
- **Pair:** selectbox of watched pairs when any exist; free-text input otherwise
  (covers thin-coin manual entries registered fresh).
- **Mode toggle (radio):** `◉ Same trade — fix my numbers` (default) / `◯ New trade — fresh clock`.
- **Amounts (radio):** `◉ $ you spent + coins you own` (primary; entry = spent ÷ owned,
  size = spent) / `◯ entry $ + size $` (power input).
- Submit: `✎ SET MY ENTRY`.

### Same-trade mode (correction — the deep-scan adjust-to-reality case)
- Keyed updates ONLY: `w[pair]["entry"] = ...`, `w[pair]["size"] = ...`.
- KEEP `added` — the clock keeps running, no new timestamp.
- KEEP `goal_pct`, `window_min` — the goal call is unchanged.
- Peak survives, floored at the corrected entry: `w[pair]["high"] = max(high, new_entry)`.
- Toast: "numbers corrected — the clock keeps running".

### New-trade mode (true re-entry, or fresh manual registration)
- New `added = now`; `high = entry`; entry + size set.
- PRESERVE `goal_pct`/`window_min` if present (fixes the wipe bug).
- If absent, derive from the engine — reuse the green light's call
  (`_bs._window_goal_pct(pair)`, app.py ~2106) with a 1.5% fallback.
- Toast: "fresh clock — she counts from now".

### Companion fixes
- **"WATCH anyway"** (Quick Check ~2743): derive goal/window on add; add a size input
  (kills the $2,500 default PnL). Fresh clock stays correct here — it IS a new trade.
- **Helper** `_derive_goal(pair) -> tuple[float, float]`: wraps `_window_goal_pct` +
  window scaling (`max(60, min(180, goal * 60))`), used by green light, WATCH-anyway,
  new-trade mode, and +/- creation so all five paths share one derivation.
- All writes become keyed updates — no full-dict replacement anywhere.

## Part 2 — Real-time exit data

1. `_run_exit_logic`: fetch `live = float(kraken.fetch_ticker(pair)["last"])`
   (fallback: candle close). Use the ticker for trail_hit, goal-call %, PnL, peak
   tracking (`high = max(high, live)`), and reason text.
2. Keep candles for the strategy signal (RSI/MACD/BB need them) but **seed the
   in-progress candle** with the live price before `evaluate_strategy` — overwrite the
   last candle's close (and extend high/low) so the exit bar reacts to the current move.
3. EXIT CHECK tab (`render_exit_check`): same ticker-first reads for trail/goal/PnL.
4. Scanner voice (`background_scanner.py`): align its per-minute price checks to the
   ticker so "she" talks real-time even with the browser closed.
5. Rate check: ~1–5 watched pairs × 1 ticker/min is well within public limits
   (the code already pulls full OHLCV per pair per cycle).

## Invariants (do not break)
- Two-ledger rule: contest exits never write to the bridge; the control is advisory-only.
- The SOLD button stays the ONLY thing that clears a contest position.
- Green-light auto-watch path (fresh entry, engine goal, recommended size) unchanged.

## Verification
- [ ] Same-trade correction on a green-light entry: `added`/`goal_pct`/`window_min`/`high`
      preserved, entry/size updated, toast says clock keeps running.
- [ ] New-trade on the same entry: fresh `added`, `high = entry`, goal NOT wiped.
- [ ] Thin-coin manual entry via the unified control (new pair, new-trade mode):
      engine-derived goal, real size, PnL toast uses the real size (not $2,500).
- [ ] WATCH-anyway on a rejected scan: goal derived, size honored.
- [ ] Simulated fast drop: trail fires from the ticker price without waiting for the
      candle close; EXIT CHECK and the scanner voice agree with the console.
- [ ] No bridge writes from any contest-side path.
