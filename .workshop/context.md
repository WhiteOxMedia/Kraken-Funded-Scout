# Kraken Scout — Project Context

## What it is
Daily trade picker + auto-trader for Kraken. Scout scans 54 challenge assets, runs tournaments, signals the XRP bot (kraken_xrp_trading_bot) which executes on the regular Kraken spot account. Advisory for the Funded challenge; auto-trades spot. One merged "Trading Console" dashboard (localhost:8501) — wood-grain soundboard aesthetic, MASTER/TOURNAMENT/QUICK CHECK/EXIT CHECK tabs.

## Critical conventions
- Timezone: `ZoneInfo("America/Denver")` everywhere (auto DST). NEVER use fixed UTC-7.
- ONE desktop shortcut: "Kraken" (Launch Kraken.bat) fires the ENTIRE stack with dup-safe windows: Volatility Engine → Scanner → Trader Bot (bot venv) → Console dashboard (8501, opens browser). Kill Switch FLATTEN/RESET shortcuts remain.
- LONG-ONLY MANDATE (Aug 22, user: "I will never short an asset, only long positions"): tournament match rules — a buy ALWAYS beats a sell head-to-head (scanner.py _compare_signals); evaluate_and_check + scanner tournament champion selection accept buys ONLY; bounce champions must be buys; send_alert hard-guards `action != "buy"` → suppressed with log (never alerts, never reaches bridge). The bot physically can't short (only buy-to-open, sell-to-close) and only executes sell signals from source="scout_exit_check". Tournament sells in the bridge were always dead letters. Console labels sell readouts "EXIT / AVOID" + "never a short: exit if you hold it, otherwise stay out".
- Duplicate detection uses PowerShell Get-CimInstance command lines (tasklist/wmic do not work; wmic KILLS bats on this Win11 build).
- The scout .venv is fragile: uv trampolines break if the uv python junction disappears. Launch bats self-heal via `uv sync`. Sandbox cannot spawn venv trampolines — use `cmd /c ".venv\Scripts\python.exe -X utf8 script.py"` from project dir for testing (works). Sandbox `start` spawns die with the session — only terminal_start processes persist for the session; user's own launches persist always.
- reset_positions.py must be called with the BOT's venv python: `C:\Users\birds\Workspace\kraken_xrp_trading_bot\.venv\Scripts\python.exe`.
- Kill switch (kill_switch.lock in kraken_scout): active = no new buys, sells still process; also flattens via reset_positions.py + clears bridge. Balance circuit breaker: 10% drop from daily start (daily_balance table).
- Test scripts: write output to a file (e.g., *_check.txt) and view with editor tool — terminal echo mangles inline python prints (nested quotes break; NEVER use `python -c` one-liners with nested quotes; write a .py file). PowerShell one-liners with nested $() also break — write .ps1 files for complex checks.
- KRAKEN ORDER BOOK QUIRK: ccxt kraken fetch_order_book levels carry 3 elements [price, amount, timestamp] — ALWAYS star-unpack (`for p, s, *_ in levels`).
- KRAKEN LEDGER QUIRK: Funded-account payouts arrive as ledger entries type='transfer', direction='in' (NOT 'deposit'). Trades are type 'trade', rewards 'receive' — both filtered out of payout tracking.
- Bot imports scout modules via SCOUT_PATH sys.path trick (scout_signal_reader.py:35) — shared libraries live in kraken_scout, one copy.
- Streamlit cache quirk: @st.cache_data functions taking the KrakenAPI object must use a leading-underscore param name (`_kraken`) — the object is unhashable.

## Rotation system (Aug 22 — data-driven)
- 5 trading rotations (Denver local): R1 00-04 🌏 Asia, R2 04-08 🌅 Pre-US, R3 08-12 ☀️ EU/US Overlap, R4 12-16 🌤 US Afternoon, R5 16-20 🌆 US Late. 20:00-24:00 = COOLDOWN: no buys/DCA/tournaments (exit checks + trailing stops keep running; flatten fires at 20:00 block change).
- volatility_engine.py (own window, hourly refresh + daily full pull): pulls rolling 1h OHLCV for 54 assets + captured gainers into ohlcv_cache.db (own sqlite), computes per-asset × rotation × weekday/weekend stats, writes rotation_thresholds.json. Prints threshold tables in its console window.
- KRAKEN OHLC HARD LIMIT: only ~720 1h candles (~30 days) served regardless of `since`. Cache ACCUMULATES toward the 12-week target; data_weeks in JSON + engine window + dashboard LED.
- Dual gates on every buy (send_alert): (1) confidence bar = 60 + 35×false-breakout rate, per-asset when reliable else universe median, clamped 50-99; (2) expected-move gate = projected move must clear rotation noise floor (avg + 0.5σ; aggressive = avg for gainers).
- Reliability: weekday cells ≥10 sessions, weekend ≥8; unreliable cells fall back to median. Gainer captures persist to volatility_universe.json (14 days).

## Execution tiers system (Aug 22 — sliding-scale market impact)
- **execution_tiers.py** (kraken_scout, shared lib — bot imports via SCOUT_PATH): 6 tiers by % of top-10 book depth consumed (T1 Micro 0-5%, T2 Small 5-20%, T3 Medium 20-50%, T4 Large 50-100%, T5 VL 100-300%, T6 Inst 300%+).
- **Buy gate pipeline** (bot_engine._execute_buy, in order): 24h blacklist (thin book <$500 or 2 slippage-audit strikes) → Liquidity Health Score ≥60 (spread 25pts + depth bands ±1/2/5% 35pts + engine σ 20pts + maker presence 20pts) → hybrid sizing cap at Tier 2 (20% of book) → VWAP-walk slippage forecast ≤0.3% → 10s depth-stability double-sample (best-level ±20%, spread blowout, TOB -50%) → tiered execution.
- **Tiered execution**: T1-2 market · T3 limit ladder ×3 (45s timeout, market-sweep remainder) · T4 TWAP ×4 (30s apart, increment-bounded slices) · T5/6 capped at 50% of book + ladder. Per-slice aborts: spread 2×, depth -50%, price ±0.5%, slip 2× forecast — buys never chase; sells ALWAYS complete (hard market-sweep; never capped; never health-gated).
- **Slippage audit**: actual >2× forecast → strike 1 = size ×0.5 for 1h; strike 2 in 24h = 24h blacklist. Self-correcting.
- **Scale-in queue** (execution_state.json pending_scales): capped remainders ≥$100 drained ≤20% of book per tick via BotEngine._execute_buy (positions fold + trades log), all gates re-checked per slice, expires 2h/4 slices, kill switch + 20-24 cooldown block. drain_pending_scales runs at top of check_scout_signals (before pending check — runs even with no signals). Scale slices carry "liquidity scale-in slice" reason marker to suppress re-scheduling. Sell/exit cancels the pair's pending scale.
- **Fail-open**: book measurement errors degrade to plain market orders (logged) — a flaky public endpoint never halts trading. Tiered results carry "tiered": True flag; bot's zero-fill fallback is bypassed for them (no phantom positions; zero-fill sell does NOT close position).
- Shared state in kraken_scout/execution_state.json (atomic write; bot only writer). Scanner + console read (blacklist blocks signal writes; QC panel shows state).
- Console QC "Liquidity & Execution — advisory" plate: health/tier/slip/method/blacklist LEDs + breakdown, read-only, never writes bridge. Scanner send_alert appends `LIQUIDITY: health · tier · % of book · est slip` line to desktop alerts + skips blacklisted pairs.
- Live calibration (Aug 22 probe): majors BTC/ETH/SOL/XRP all T1 Micro at $100-$1400 (slip ≤0.01%) — system invisible there. Thin alts bite: EIGEN $1400 = T3 22%, MOODENG $1400 = T3 32% → capped + scale-in. Health 87-90 normal markets.
- Tests: test_execution_tiers.py (66 checks, synthetic books + stub exchanges, ALL PASS; patches STATE_JSON/_current_vol_std to temp). book_probe.py (live depth probe → book_probe_check.txt). test_longonly.py (6 checks: buy always beats sell; ALL PASS).

## Separation of concerns (user-mandated)
- MANUAL console tools (Quick Check, Exit Check, tournament display) = CONTEST ADVISORY ONLY. Never write to bridge, never execute trades, never counted. Scanner→bridge→trader is the ONLY path touching real money.
- Auto-Watch (Exit Check tab, st.fragment run_every — NOT st_autorefresh) = independent monitor: cloned tools, read-only bridge peeks (get_trailing_high), desktop alerts only.
- Contest watch list: contest_watch.json (console-local, NOT the bridge).
- CONSOLE = CONTEST COPILOT (user direction Aug 22): bot activities/kill-switch/trader status are NOT shown in the console UI (kill switch lives on desktop shortcuts; sidebar shows only SCANNER status + "KILL SWITCH ON DESKTOP" note). Contest workflow: pick (tournament/QC) → enter on Kraken contest app → watch → popup says sell → sell on app → auto-drops off list. No contest trade history is tracked/displayed (Kraken's dashboard owns it).

## Console UI conventions (Aug 22 — contest-first, simple speak)
- Plain casual English everywhere ("THE CALL: HOLD — still riding", "HOW SURE ARE WE? ... Still 10 points short", "PROFIT GUARD", "pulls the plug at"). Sell readouts say "EXIT / AVOID" + "never a short: exit if you hold it, otherwise stay out" — user never shorts.
- MASTER tab: "🏆 Contest Payouts — results in" plate (big green total, funded-account payouts from ledger, transfer/in entries; celebration banner + st.toast on NEW payout; payout_track.json persists history forever — first run baselines silently) + "My Contest Score — live" (live P&L% of watched contest positions, auto from contest_watch.json, no manual entry) + Master Bus wallet knobs. Bot-account scoreboard ("Today's Score"/"Win Feed") REMOVED — bot trades don't matter for the contest.
- CONTEST EXIT PROFILE (buy big, get out fast — separate from the bot, same engine/data): CONTEST_TRAIL_PCT=4.0 (profit guard) + CONTEST_SELL_BAR=50 (flat) in app.py. Applies to contest watch positions (auto-watch, contest=True) and the manual SHOULD I SELL check. Wallet/bot positions in auto-watch mirror the bot's rules (8% guard, 75/60 bar). Bot itself NEVER adjusted.
- EXIT CHECK: "SHOULD I SELL?" button; verdict panel in plain English; manual checks fire the SAME alerts as auto-watch (st.toast in-app popup + _desktop_notify, 15-min dedupe, manual: prefixed keys in watch_notified). Auto-Watch plate shows ONLY contest positions (big green hold / red SELL IT rows); bot/spot positions = one dim background line. Exited contest positions auto-remove from watch. Watch-list expander = live positions only (entry price, no dates/highs).
- THE LIGHT: every auto-watch check (default 1 min) pops st.toast — red "🔴 SELL: asset (reason)" / green "🟢 STILL GOOD — stay in: X +1.2%" / empty "🟢 Watch is running…". Desktop popups stay deduped 15 min.
- QUICK CHECK AUTO-WATCH: deep scan + green light (buy ≥ bar) → coin AUTO-ADDED to contest_watch.json (entry = live price, no click). Toast confirms. Re-scans don't duplicate/reset. "WATCH anyway" button only for non-green-light manual conviction. Adjust-entry box corrects actual fill.
- Global banner: "🚨 TIME TO SELL" + "sell on the contest app — nothing sells by itself".
- NOW strip under header (plain English, all tabs): scanner state, positions held, cooldown warning. "🆕 New here?" 30-sec tour expander.
- Tournament button: "▶ RUN BRACKET" (RUN TOURNAMENT wrapped). Bracket winner rows color by action (green buy / red sell) — sells never green. Tournament champions are always BUYs (long-only mandate); a sell champion only possible when zero buy candidates exist (displays EXIT / AVOID, never a short call).
- CSS: .score-big (win pulses green / loss pulses red), .celebrate (animated payout banner with confetti ✦), .win-pill/.loss-pill, .feed-row, .now-strip — in the <style> block near .led.
- AppTest smoke: test_apptest.py → apptest_check.txt (probes header/NOW/score plates; expanders don't auto-expand so "New here" probe shows missing — fine).

## Profit optimizations
- Trailing stop 8% below tracked high (signals.highest_price), winners only, exit check every 5 min. Momentum positions: 60% sell bar; others 75%.
- Rotation keeps winners: positions up >5% ride through rotation (reset_positions.py --keep). Weekend rotations skipped.
- Loss cooldown 4h; MAX_CONCURRENT_POSITIONS=3, min trade $100; momentum ceiling 6%; momentum exit bar 60% (others 75%); morning scans 15 min; trend-pullback confluence +10; DCA tranches (scout_dca, max 1, ≥2.5% dip, no DCA in cooldown); liquidity filter $1M Kraken-only volume basis.
- Bot sizes buys at trade_pct=99% of available quote (concentration) — the Tier 2 cap is now the real size limiter on thin books.

## Key files
- kraken_scout/execution_tiers.py — shared execution-tier library (tiers, health score, VWAP slippage forecast, stability check, tiered exec, audit strikes, blacklist, scale-in queue, execution_state.json, gate_buy, liquidity_snapshot)
- kraken_scout/volatility_engine.py — stats engine, rotation_thresholds.json, ohlcv_cache.db, gate lookups, remember_gainer
- kraken_scout/scanner.py — _compare_signals with LONG-ONLY match rule (buy beats sell), tournament_scan, bounce tools
- kraken_scout/background_scanner.py — ROTATIONS + rotation_key_for_hour + _schedule_for_dow; get_time_block 7-tuple; send_alert LONG-ONLY hard guard + dual gates + liquidity advisory + blacklist skip; evaluate_and_check buys only; cooldown skips buys/DCA/tournaments; exit checks every 5 min
- kraken_scout/Launch Kraken.bat — single launcher (all 4 processes, dup-safe each; PURE ASCII — no em-dashes/emoji, cmd codepage mangles them)
- kraken_scout/signal_bridge.py — SQLite bridge (signals.db): lifecycle, daily_balance, trailing stops, cooldowns, reset_all_positions(keep_pairs), kill switch
- kraken_scout/app.py — Trading Console: contest-first UI (payouts + contest score on MASTER, simple-speak EXIT CHECK, contest exit profile, every-minute light, QC green-light auto-watch, NOW strip, celebration banners, st.toast popups); payout_track.json + contest_watch.json console-local state
- kraken_xrp_trading_bot/scout_signal_reader.py — executes bridge signals; scale-in drain at top of check_scout_signals; direct sells tiered
- kraken_xrp_trading_bot/bot_engine.py — _execute_buy gate pipeline (gate_buy → cap → stability → execute_tiered_buy → audit → schedule_scale_in); _execute_sell tiered + cancel_scale_in; tiered zero-fill guards; liq_note rides in trade reasons
- kraken_xrp_trading_bot/reset_positions.py — two-phase flatten, --keep winners, skips dust <$1
- kraken_xrp_trading_bot/kraken_api.py — ccxt wrapper: market/limit/cancel/fetch_order all present

## Accounting notes
- Funded contest payouts (Aug 22): $135.72 Fri 8:10 PM + $115.92 Sat 8:08 AM = $251.64 total tracked in payout_track.json. These land as transfer/in USD ledger entries. Caveat: ANY incoming USD transfer counts as a "payout" (Kraken doesn't label source) — manual wires in would too; trades never do.
- Console "Today P&L" (Master Bus) includes deposits (balance-based).
- Master Bus knobs hard-scaled to $1500 account — user will rescale when $10K lands (tier caps also worth revisiting then: 20% of book on majors is no constraint at $10K; thin-alts still protected).
- ATOM21.S, NVDA.EQ, DOT/TAO dust = permanently in wallet, NOT positions.

## Live state (Aug 22 ~15:00)
Stack processes all died with the last Workshop session — user double-clicks "Kraken" to relaunch everything (dup-safe). Console auto-reloads on browser refresh; SCANNER must relaunch to pick up the long-only tournament rules + earlier send_alert changes. execution_state.json not yet created (no gates fired — clean). payout_track.json baselined by test then reset — the app does its own silent baseline on first MASTER load. XRP spot position open since 07:03 @ $1.4921 (bot_strategy; trail floor $1.3889 with the bot's 8% rule).