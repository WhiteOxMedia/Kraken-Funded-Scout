# Sliding-Scale Market Impact & Liquidity Tiers — with Liquidity Health Gates

## Context

The bot fires single market orders sized at `trade_pct × available_quote` (99%). On majors that's fine; on thin challenge alts a full-size buy eats a large fraction of the visible book. With the $10K account incoming, this stops being theoretical.

This adds an execution-aware layer: measure the order book before sizing and every order, classify into 6 impact tiers, gate on a Liquidity Health Score, forecast slippage, verify depth stability, execute per-tier (market / ladder / TWAP / capped), abort on hostile books, audit actual vs expected slippage, blacklist dead markets, and scale into thin books over multiple ticks instead of slamming them.

**Locked decisions:** (1) Both sides — bot executes tier-aware, Console advisory readouts only. (2) Tier 5/6 → cap at Tier 3 max (50% of book), remainder unfilled/scheduled. (3) Hybrid sizing — account-% sizing stays, hard-capped at Tier 2 (20% of book). Multi-venue routing impossible (US geo-blocks) — capping is the only T5/6 behavior.

## Tier Definitions

| Tier | Name | % of book | Execution |
|------|------|-----------|-----------|
| 1 | Micro | 0–5% | Market order |
| 2 | Small | 5–20% | Market order |
| 3 | Medium | 20–50% | Limit ladder (3 slices, 45s wait, market-sweep remainder) |
| 4 | Large | 50–100% | TWAP (4 market slices, 30s apart, book re-check per slice) |
| 5 | Very Large | 100–300% | Cap to 50% of book → ladder, remainder → scale-in queue |
| 6 | Institutional | 300%+ | Same as Tier 5 |

- **Book = cumulative USD depth of top 10 levels** (asks for buys, bids for sells). `DEPTH_LEVELS=10`, tunable.
- Increment rules for slicing: <20% consumption → 5% steps; 20–100% → 10%; >100% → 25%.

## The Gate Pipeline (every buy, in order)

1. **Blacklist check** — pair in 24h blacklist (thin book or 2 audit strikes) → skip.
2. **Health Score ≥ 60** — see below. Below 60 → skip ("liquidity unhealthy").
3. **Tier-2 sizing cap** — `spend = min(spend, 0.20 × book)`. If capped below min order → skip ("book too thin").
4. **Slippage forecast ≤ 0.3%** — VWAP-walk the book with the order size (subsumes `(size/TOB)² × spread` — walks actual uneven depth). Above 0.3% → skip.
5. **Depth stability** — sample book, wait 10s, sample again. Fail if best-level depth moved >±20%, spread widened >2 ticks, or depth vanished. Fail → skip. (Adds 10s to buys; sells exempt — exits must complete.)
6. **Tiered execution** — per table above.
7. **TWAP/ladder abort conditions** (checked per slice): spread widens >2×, depth drops >50%, price jumps >0.5% vs slice start, fill slippage ≥2× forecast, or consumption >20%. Abort remaining slices — never chase.
8. **Post-execution slippage audit** — actual vs forecast; actual > 2× forecast → strike. Strike 1 → pair max size ×0.5 for 1h. Strike 2 in 24h → 24h blacklist. Self-correcting.
9. **Scale-in scheduling** — if desired size was capped (or T5/6 remainder > $100): queue remainder, drained ≤20% of book per tick, gates 1–5 re-checked per slice, expires after 2h or 4 slices. Kill switch + 20:00–24:00 cooldown block slices.

**Fail-open rule:** if `fetch_order_book` errors, gates degrade to today's behavior (T1 market, full size) with a loud log — a flaky public endpoint must not halt trading.

**Sells:** tiered execution only (ladder/TWAP for T3+, hard timeout → market-sweep remainder). No health/stability/blacklist gating — exits always process (kill-switch convention).

## Health Score (0–100, pass ≥ 60)

- **Spread width — 25 pts**: ≤10 bps = 25; scales down; >100 bps = 0.
- **Depth bands — 35 pts**: USD depth within ±1% (15), ±2% (10), ±5% (10) of mid, from the book snapshot. Log-scaled against a $5K reference.
- **Volatility — 20 pts**: per-asset σ from the Volatility Engine's existing `rotation_thresholds.json` (reuse — no new pipeline). Current rotation cell; missing cell → neutral 10.
- **Maker presence / consistency — 20 pts**: from the two-sample stability check (small Δ = makers holding).

## Implementation Plan

### 1. NEW `kraken_scout/execution_tiers.py` (~550 lines, shared library)

Pure library, no DB. Bot imports via the existing `SCOUT_PATH` sys.path mechanism (`scout_signal_reader.py:35-37`).

- Constants (all tunable): `DEPTH_LEVELS=10`, `TIER_BOUNDS`, `SIZE_CAP_PCT=20`, `T5_CAP_PCT=50`, `HEALTH_MIN=60`, `SLIPPAGE_MAX_PCT=0.3`, `STABILITY_WINDOW_S=10`, `STABILITY_DEPTH_PCT=20`, `STABILITY_SPREAD_TICKS=2`, `LADDER_SLICES=3`, `LADDER_TIMEOUT_S=45`, `TWAP_SLICES=4`, `TWAP_INTERVAL_S=30`, abort thresholds, `AUDIT_STRIKE_RATIO=2.0`, `SCALE_MAX_SLICES=4`, `SCALE_EXPIRE_H=2`, `THIN_BOOK_USD=500`
- `measure_book(exchange, pair, side)` → `{tob_usd, best, mid, spread_bps, levels}`
- `liquidity_health_score(book, pair, stability_delta)` → `(score, breakdown_dict)` — volatility pulls `get_move_threshold`-adjacent σ from engine JSON (lazy import)
- `classify_tier(notional, tob)` → `(num, name, consumption_pct)`
- `forecast_slippage(levels, notional)` → VWAP walk → `(slippage_pct, vwap_fill_price)`
- `check_depth_stability(exchange, pair)` → `(stable, reason)` — 10s two-sample
- `execute_tiered_buy(kraken, pair, volume, ref_price, validate=False)` → per-tier dispatch + per-slice abort checks → unified result `{id, average, filled, cost, fee, tier, consumption_pct, slippage_pct, method, unfilled_volume, breakout}`
- `execute_tiered_sell(...)` → never caps, force-completes
- **Shared state `kraken_scout/execution_state.json`** (atomic write): `blacklist {pair: until_iso}`, `strikes {pair: {count, last_iso, size_mult, until_iso}}`, `pending_scales {pair: {remaining_usd, slices_left, expire_iso, entry_ref}}`
- `audit_slippage(pair, forecast_pct, actual_pct)` → strike logic
- `schedule_scale_in(pair, remaining_usd, entry_ref)` / `drain_pending_scales(kraken, config)` → tick-drainable slice executor (full gates per slice)
- Own logger — every gate/skip/abort line lands in the bot window

### 2. MODIFY `kraken_xrp_trading_bot/bot_engine.py`

- `_execute_buy` (~line 326): insert gate pipeline after `spend_amount` (~line 353/398): blacklist → health → Tier-2 cap → min-order → slippage forecast → stability → `execute_tiered_buy` → `audit_slippage` → `schedule_scale_in` if remainder. Tier/health/slip/abort ride in the trade-log reason string.
- `_execute_sell` (~line 570): replace `place_market_sell` (~line 591) with `execute_tiered_sell`.

### 3. MODIFY `kraken_xrp_trading_bot/scout_signal_reader.py`

- Top of `check_scout_signals` (after kill-switch check): `drain_pending_scales(kraken, config)` — scale slices respect kill switch + cooldown.
- Direct sell path (~line 463): `execute_tiered_sell`.

### 4. MODIFY `kraken_scout/app.py` — Console advisory readouts

- Quick Check panel: "LIQUIDITY & EXECUTION (advisory)" section — health score + breakdown, book depth (bid/ask), spread, tier for the signal's `position_size_usd`, slippage forecast, method, est. time, blacklist status. Read-only, no bridge writes. Public `fetch_order_book` added to the inline wrapper (`app.py:362-365`).

### 5. MODIFY `kraken_scout/background_scanner.py` — advisory, no duplicate gating

- `send_alert` (~line 422): one book snapshot → skip writing signal if pair blacklisted; else append `LIQUIDITY: health 74 · T2 · 12% of book · est slip 0.08%` to alert + log. Enforcement stays bot-side (fresh book at execution).

## Scope & Non-Goals

**In:** 6-tier classifier, health score gate, Tier-2 sizing cap, VWAP slippage forecast + 0.3% skip, 10s depth-stability check, ladder/TWAP/capped execution, extended TWAP aborts, post-trade slippage audit with strikes, 24h thin-book blacklist, multi-tick scale-in queue, console advisory panel, alert-text readouts, shared execution state JSON.

**Out:** multi-venue routing (impossible), native iceberg orders, DB schema changes, scanner sizing math changes, bridge schema changes, health-gating sells.

## Verification

1. **Unit tests** (synthetic books, no network): tier boundaries (4.9/5/19.9/20/49.9/50/100/300%), VWAP slippage math, health score bands, stability detection (stable/unstable book pairs), abort triggers (depth −50%, price ±0.5%, spread 2×), audit strike ladder, scale queue lifecycle. Run `cmd /c ".venv\Scripts\python.exe -X utf8 test_execution_tiers.py"` → `test_execution_tiers_check.txt`.
2. **Live book probe**: real top-10 depth + health scores across majors → thin challenge alts; table of what $100/$400/$1,400 orders tier at; confirm cap binds on thin alts, never on majors.
3. **Simulated execution**: synthetic exchange stub driving `execute_tiered_buy` through T3 ladder and T4 TWAP with a mid-flight depth collapse → verify abort fires and result reports unfilled volume.
4. **Paper mode**: `validate=True` passes through every slice order — existing paper path intact.
5. **Console**: AppTest smoke — QC panel renders liquidity section.
6. **Live**: next signal in the bot window shows `HEALTH 74 · T2 · 12% · slip 0.08%` and the trade reason carries tier; blacklisted pairs stop generating bridge signals.

## Notes & Risks

- Rate limits: `fetch_order_book` is public; usage ≈ 2 calls per buy (measurement + stability), 1 per QC scan, 1 per signal — negligible vs OHLCV pulls.
- The 0.3% slippage gate can block trades the 20% cap allowed on uneven books — intended (second safety net).
- Stability check adds 10s to buy latency; move-gate noise floors are 1.2–1.8% so 10s drift (~0.05%) is immaterial.
- TWAP worst case blocks the bot tick ~2–3 min (single-position-per-tick design — acceptable, logged).
- `execution_state.json` races: bot is the only writer; scanner/console read. Last-writer-wins on rare concurrent bot writes is benign (state re-derives from next trade).
