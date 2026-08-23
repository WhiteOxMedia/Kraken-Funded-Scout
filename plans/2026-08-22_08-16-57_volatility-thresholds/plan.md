# Data-Driven Rotation Thresholds (Volatility Engine)

## Status: DRAFT — awaiting user decisions (see Open Questions)

## Context
Current rotation thresholds (Morning 95 / Midday 85 / Afternoon 80 / Evening 72 / Night 65 + WEEKDAY_ADJUST Mon -5 … Sat +5) were hand-guessed. User wants them derived from real data: 12 weeks of 1h OHLCV per asset → per-rotation volatility stats → thresholds for 5 rotations × weekday/weekend. Universe = 54 challenge assets + captured gainers (persisted for next-day thresholds). A new "Volatility Engine" window fires with the Kraken Trader shortcut.

## What exists already (reuse — no duplicates)
- `background_scanner.py`: TIME_BLOCKS + WEEKDAY_ADJUST + `get_time_block()`/_apply_dow — threshold plumbing in place
- `background_scanner.py`: `fetch_ohlcv_parallel()` (1h OHLCV via ccxt, threaded) — data fetch ready
- `background_scanner.py`: `filter_illiquid()` — Kraken-only 24h quote volume (correct basis for liquidity stats)
- `background_scanner.py` gainer scan — captures top-3 new gainers per cycle (needs persistence added)
- `strategy.py`: ATR volatility_rank + R:R — projected-move numbers available per signal
- `signal_bridge.py` (sqlite) — pattern for a new ohlcv/threshold store
- Desktop: Kraken Console → Launch Scout.bat (scanner+dashboard); Kraken Trader → Launch Bot.bat (bot only)

## Discovered live state (re-checked Aug 22 ~08:25 — all healthy)
- Scanner: RUNNING (PID 19008, launched via Launch Scout.bat pattern)
- bot_runner: RUNNING (PID 15920, --interval 15)
- Merged console :8501: RUNNING
- STALE: old bot dashboard pythonw on :3000 (PID 16336, started Aug 21 16:22, hidden orphan). Nothing auto-spawns it (grep: no pythonw/3000 spawn in bot code — manual launch). Cleanup = kill PID once, no recurrence risk.
- Kraken Trader currently fires bot only; user confirmed scanner "can be combined" into it.

## Scope & Non-Goals
**In scope**
- `volatility_engine.py` (new): 12-week rolling 1h OHLCV per asset → per-rotation stats → `rotation_thresholds.json`; own visible console window with tables + backtesting notes; dup-safe; hourly refresh + full nightly recompute.
- `background_scanner.py`: new 5×4h rotations + 20:00–24:00 no-buy window; thresholds from JSON (fallback = current values); expected-move gate; gainer captures persist to volatility universe.
- New single launcher bat + ONE desktop shortcut ("Kraken") replacing the two old Kraken shortcuts. Kill Switch shortcuts untouched.
- Kill stale orphan :3000 dashboard (PID 16336).

**Non-goals (deferred)**
- No changes to sell-side logic (trailing stop 8%, exit bars 75/60 stay).
- Weekend rotation-flatten skip stays as-is.
- Per-day gating (Mon vs Tue) — weekday/weekend split only; dow observations (Sat morning rush, Sun evening reversal) printed as notes.
- Auto-scaling dials for $10K (user-deferred).

## Implementation Plan

### 0. Cleanup
Kill stale :3000 pythonw (PID 16336). Verified orphan — nothing respawns it.

### 1. `volatility_engine.py` (new, kraken_scout)
- **Cache**: `ohlcv_cache.db` (own sqlite — signals.db untouched), incremental candle fetch. 1h timeframe, 12-week window (~2016 candles/asset; Kraken 720/request → 3 pulls/asset; ~54 assets ≈ 3 min full refresh).
- **Universe**: 54 challenge assets + `volatility_universe.json` (captured gainers, first_seen/last_seen).
- **Stats per asset × rotation × {weekday, weekend}** (America/Denver, DST-aware):
  - avg move = mean |rotation return| (open→close of 4h block); σ = std of |returns|
  - max spike/drop = max candle-high / min candle-low vs rotation open
  - liquidity = median USD volume (Kraken-only basis, matches filter_illiquid)
  - false breakout rate = excursion beyond standard threshold but round-trip close (< 0.25 × avg move)
  - thresholds: aggressive = avg; standard = avg + 0.5σ; conservative = avg + σ
  - confidence bar = 60 + 35 × false_breakout_rate (clamped 50–99)
- **Output**: `rotation_thresholds.json` (per-asset + universe medians) + console tables: weekday, weekend, differences summary, backtesting notes (walk-forward, fee-drag warnings from audit).
- **Loop**: run on start, hourly incremental, nightly full recompute. Dup-safe via Get-CimInstance.

### 2. `background_scanner.py` integration
- Replace TIME_BLOCKS: R1 00–04 🌏 Asia, R2 04–08 🌅 Pre-US, R3 08–12 ☀️ EU/US Overlap, R4 12–16 🌤 US Afternoon, R5 16–20 🌆 US Late, Cooldown 20–24 🌙 (no BUY scanning; exit checks keep running for safety).
- `get_time_block()`: mtime-cached read of rotation_thresholds.json, weekday/weekend schedule, calibrated bar + scan interval. Fallback = hardcoded defaults + existing WEEKDAY_ADJUST.
- **Expected-move gate** in `send_alert` before bridge write: projected move (signal target, else ATR×2) ≥ asset's standard threshold (aggressive for gainer-source). Logs "MOVE GATE" rejections.
- Rotation flatten on block change (winner-keep unchanged) → 20:00 flatten fires at R5→Cooldown.
- Gainer capture appends to `volatility_universe.json`.

### 3. Launcher + shortcut
- New `Launch Kraken.bat`: venv self-heal → fires each piece dup-safe in own window: Scanner → Volatility Engine → Bot (bot_runner --interval 15) → Console dashboard (8501 dup check → browser).
- Desktop: ONE new shortcut "Kraken" (console.ico) → Launch Kraken.bat. Remove old Kraken Console + Kraken Trader shortcuts (bats remain).

### 4. Console dashboard touch-up
Check app.py for hardcoded block names/bars; read rotation_thresholds.json so MASTER/QUICK CHECK show live calibrated bars. Cosmetic only.

## Verification
1. Run `volatility_engine.py` via `cmd /c ".venv\Scripts\python.exe volatility_engine.py"` — JSON + tables for all 54 assets.
2. Restart scanner → new rotation names, calibrated bars, MOVE GATE rejections in log.
3. Delete JSON → scanner falls back, no crash.
4. Run Launch Kraken.bat twice → exactly one of each process (Get-CimInstance check).
5. Single shortcut fires all 4; :3000 stays dead.
6. Next 20:00 boundary: flatten fires, no buys until 00:00.
