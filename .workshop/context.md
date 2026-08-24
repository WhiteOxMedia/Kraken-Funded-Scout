# Kraken Scout — Project Context

## What it is
Daily trade picker + auto-trader for Kraken. Scout scans 54 challenge assets, runs tournaments, signals the XRP bot (kraken_xrp_trading_bot) which executes on the regular Kraken spot account. Advisory for the Funded challenge; auto-trades spot. One merged "Trading Console" dashboard (localhost:8501) — wood-grain soundboard aesthetic, flow pages + MASTER/TOURNAMENT/QUICK CHECK/EXIT CHECK tabs.

## THE CONTEST GAME (user's words, Aug 24): scalping $20s
The user's income system: alert/deep-scan → green light → all-in $5,000 → out at +$20 (+0.4%) → repeat. They built $251 doing this manually. The system's job: find coins whose $20 comes FAST, green-light fresh entries, count the twenties (TAKE IT), call exits. Trend conviction is a REAR-VIEW score — it maxes AFTER a move runs (PENGU: 100% at the top of a +5.6% leg, -$44; PUMP: 79% at the base, +$133). Never ask conviction to pick scalps; the VERDICT (green light) does that.

## QC SCALP GATE (Aug 24 redesign — the four yeses)
Green light requires ALL: (1) setup conviction ≥ QC_SCALP_FLOOR=60; (2) $20 pace — _hours_to_scalp ≤ _max_resolve_hours (pace caps 4/5/6h by rotation avg, never past midnight); (3) FRESH — chase guard QC_CHASE_PCT=2.0: live price ≤2% above the last hour's low (4×15m candle lows from the scan df); (4) not cooldown (20:00-24:00). Replaced the old median confidence bar — replay of a week of signals: sub-bar buys hit $20 at 80% vs 72% for bar-passers (the bar predicts nothing for scalps and swung 80→60 on one engine refresh). Panel LEDs: "$20 IN ~X.H", "FRESH +X% OFF BASE / CHASED — STALE MOVE", "SCALP GREEN LIGHT". Rejection toasts in order: ALREADY RAN → TOO SLOW → COOLDOWN → weak setup. The trend-confidence NUMBER stays displayed (setup quality info) — it just doesn't gate scalps.

## ⚡ FAST $20 leaderboard (Aug 24)
Top of QUICK CHECK: every coin ranked by $20-ETA = (SCALP_TARGET_PCT/avg_move)×4h from the engine's current rotation cells, liquidity-floored (module-level LIQ_ROT_FLOOR_WEEKDAY=15k/WEEKEND=10k in background_scanner — moved out of filter_illiquid so console shares them). Click a coin → loads into the picker → DEEP SCAN. _fast_twenty() is @st.cache_data(ttl=300). PUMP ranked #2 (0.8h, 2.1%/rot) — discovery only, the scan still judges.

## TWO LEDGERS (user-mandated separation)
- CONTEST/QC path = SCALP logic: pace + freshness + 60 floor. Desktop alerts keep the 89% floor (user's standing rule: "for the screen only put the 89% back"). QC changes need only browser F5 — Streamlit re-reads app.py from disk every rerun; imported modules stay cached in the console process.
- BOT/bridge path = TREND logic: per-asset confidence bars (60+35×false_breakout_rate, per-asset when reliable else median), move gate, tier gates. Bot buys hold to TP with trailing stops — bars are correct there.

## Timezone + conventions
- ZoneInfo("America/Denver") everywhere. NEVER fixed offsets — the bot's bridge-notify block used fixed UTC-7 for 3 days and stamped records an hour early (Aug 24 fix: ZoneInfo, commit e025ca1).
- ONE desktop shortcut "Kraken" (Launch Kraken.bat, PURE ASCII) fires all 4: Volatility Engine → Scanner → Trader Bot (bot venv, entry point bot_runner.py) → Console (8501). Dup-safe via PowerShell Get-CimInstance command lines (tasklist/wmic broken on this build).
- LONG-ONLY mandate: buy always beats sell in bot matches; send_alert hard-guards action != "buy"; console sells read "EXIT / AVOID".
- Kraken order book levels are [price, amount, timestamp] — star-unpack. Funded payouts = ledger type 'transfer', direction 'in'. @st.cache_data functions taking KrakenAPI use _kraken param.
- Test scripts write output to *_check.txt files (terminal echo mangles inline prints; never python -c with nested quotes; PowerShell one-liners with nested $() break — write .ps1 files). cmd separator is & (not ;).

## Process/version discipline (Aug 24 lessons — the hard way)
- Dup-safe keeps OLD processes alive across "restarts": after ANY code change, verify process start time > commit time before saying it's live (diag_procs4.ps1 pattern). The user's 8:56 launch predated a 10:15 fix and every later restart click skipped (dup-safe) — old code served for 90 min.
- The console caches imported modules: function-level `from background_scanner import NEW_NAME` crashes against the stale cache. QC handler uses the reload-if-missing pattern (import, check hasattr, importlib.reload). Old names imported at function level are safe.
- When killing processes: say exactly what's now dead and what it means (no alerts / no exit checks / positions unmanaged). "Done" = verified end-to-end + told the user whether a restart is needed to make it live.
- Sandbox cannot spawn venv trampolines — test via `cmd /c ".venv\Scripts\python.exe -X utf8 script.py"` from the project dir.

## Bridge state discipline (Aug 24)
- Signal lifecycle: pending → acknowledged → filled → closed (the reader marks 'acknowledged' BEFORE executing — any dup check must cover non-terminal statuses: `status NOT IN ('closed','expired','rejected')`). The bot's bridge-notify block wrote a DUPLICATE 'filled' bot_strategy record for every scout buy (is_already_holding only checked 'filled') — duplicates inflated the open-position count → bogus challenge sells → fee churn (~$7 fees on 26 trades/day). Fixed (32b08e0) + regression test test_dupcheck.py (rollback-transaction synthetic).
- Bridge open-record count MUST equal real bot positions (get_open_positions). Clean duplicates with clean_dupes.py (backs up signals.db.bak-* first, deletes only bot_strategy 'filled' rows with a scout_* 'filled' sibling for the same pair).
- Challenge system (scout_signal_reader): new signals displace weakest incumbent by ≥10 points; winners >5% up and <30min entries protected. Displacement = sell+buy = fees — watch the churn rate; margin is the dial if it over-fires (deferred per one-week rule).

## Key files
- kraken_scout/app.py — console: QC scalp gate (QC_SCALP_FLOOR/QC_CHASE_PCT near CONTEST_CHUNKS), FAST $20 (_fast_twenty), pace/chase LEDs, contest watch, TAKE IT popups, simple-speak
- kraken_scout/background_scanner.py — ROTATIONS + module-level LIQ floors, SCALP_TARGET_PCT=0.4, TAKE_IT_USD=20, _hours_to_scalp (THE resolve gate), _max_resolve_hours, send_alert (89% floor + scalp resolve + long-only guard), contest_light (25s, TAKE IT + GET OUT NOW + Zira), bounce path (yesterday's losers), cooldown flatten 20:00 persisted in scanner_state.json
- kraken_scout/volatility_engine.py — per-asset × rotation × weekday/weekend stats, rotation_thresholds.json, ohlcv_cache.db (1h candles, ms timestamps)
- kraken_scout/execution_tiers.py — 6 tiers by % of book, health score, VWAP slippage, tiered exec, audit strikes, scale-in queue
- kraken_scout/diag_replay.py — replay tool: signals vs actual price, $20 hit rates (rerun to recalibrate gates with fresh data)
- kraken_xrp_trading_bot/bot_engine.py — _execute_buy gate pipeline + fixed bridge notify (ZoneInfo + non-terminal dup check); own_scanning_enabled=False (scout funnel is the ONLY buy source)
- kraken_xrp_trading_bot/scout_signal_reader.py — ghost sweep (35min grace), challenge system, scale drain; runs as bot_runner.py --interval 15
- kraken_xrp_trading_bot/logs/bot.log — ground truth for bot behavior (daily rotation, 14 days)

## Accounting notes
- Funded payouts tracked in payout_track.json ($251.64). Live state Aug 24: contest watch had BTC (12:15, 79,000 — negative all day, -$8.50) and PUMP (14:28 manual WATCH at 0.004673 — +$133 by 16:08).
- Fees matter: 0.26% taker × 2 per round trip ≈ $0.50/trade at $100 sizes; 26 trades/day ≈ $7. Event PnL in bot log is GROSS of fees.
- ATOM21.S, NVDA.EQ, DOT/TAO dust = permanently in wallet, NOT positions.

## Open items (deferred by the one-week rule, review ~Aug 29-31)
- Challenge margin / displacement churn — judge on a week of clean data (dup-bug days don't count)
- QC_SCALP_FLOOR=60 — if green lights feel too frequent, 65 is the dial; use diag_replay.py + actual scalp results
- Optional: chase guard on the DESKTOP alert path (scanner-side; the QC already gates confirmations) — hold unless away-mode entries get burned
- TAO/USD blacklisted until Aug 25 10:02 AM (slippage audit, 2 strikes)