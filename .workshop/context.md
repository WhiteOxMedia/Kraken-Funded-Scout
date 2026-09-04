# Kraken Scout — Project Context

## What it is
Daily trade picker + auto-trader for Kraken. Scout scans 54 challenge assets, runs tournaments, signals the XRP bot (kraken_xrp_trading_bot) which executes on the regular Kraken spot account. Advisory for the Funded challenge (Kraken Funded); auto-trades spot. One merged "Trading Console" dashboard (localhost:8501) — wood-grain soundboard aesthetic, flow pages + MASTER/TOURNAMENT/QUICK CHECK/EXIT CHECK tabs. REMOTE: xmrjackpot.com/scout/ reverse-proxies 8501 (admin login; pool server = monero_project\pool\server.py on C:\Python314, SCOUT_UPSTREAM=http://127.0.0.1:8501; separate python; each browser tab is its own Streamlit session).

## THE REBUILD (Sep 1, commits 4e793a6 + 93e1324 + 09a87f6) — "Using the rules for the contest you just learned about, we need to start fresh rebuilding the contest popups"
THE SITUATION THAT FORCED IT: the user lost every contest account (no live contest as of Sep 1 ~15:00). The popups killed them: all-in entries against the -3% platform death line on shaky 100%-confidence calls (FIL 10:42 fired 60s after the bot refused it; APT 12:11 = a 100% re-entry 4h after the peak, thin book, sank on entry). The bot survived; the contest side died. The user: "the first win was luck not discipline."
THE NEW GAME (user's words): "Scalping is over with, count the 20's is gone — only solid trends that go up, buy orders only, no more wiggles. 1 hour to 3 hour time limits and percentage based goals >1%-3% depending on the current market analysis." Default tier 10k, pace $1,200/week (≈$240/day). Console-independent of Workshop. Coin profiles learned over time, tide-aware per rotation/day, deep scans before popups, never a big risk.

### STAGE 1 — THE CONTEST ACCOUNT + THE 3% DEATH-LINE GUARD (4e793a6, test_contestline.py 33/33)
**contest_account.py** (repo root): TIERS {1k/$20, 5k/$50, 10k/$90}; MAX_LOSS_PCT=3.0 (the death line: -3% from START, never moves, touch = account closes); PROFIT_TARGET_PCT=12; WEEKLY_GOAL_PCT=12; COST_ROUNDTRIP_PCT=0.08; RISK_PER_TRADE_FRAC=0.34; DEFAULT_WINDOW_DD_PCT=3.0; DEFAULT_TIER=10000. Functions: death_line/buffer/daily_pace_usd/pnl_net_usd ((gross, cost_rt, net) — the 0.08% RT cost counts toward target AND line)/window_drawdown_pct (engine cell max_drop, conservative 3% default)/max_entry_usd ((buffer×0.34)/dd; buffer gone → $0)/entry_size_usd. State: contest_account.json — **LIVE CHALLENGE: the user bought the 10k Sep 2 ~17:29 (tier 10000, fresh $10,000; pace $240/day, buffer $300, worth floor $60 at their 25% dial)**. **THE GROWING BUFFER: every gate reads current_balance LIVE at alert time — wins add buffer dollar-for-dollar; the tier itself changes nothing (guard and floor both scale linearly); THE BALANCE IS AUTO: the SOLD tap computes the net and adjusts it (ed6073c) — hand-correct in the sidebar anytime; stale-high is the dangerous direction.** WIRED: console sidebar (tier selector resets fresh; ☠ CONTEST ACCOUNT plate; header LED; cfg.challenge_balance = live). QC: the guard caps _spend_usd; risk_cap ≤ 0 → "☠ AT THE DEATH LINE" + no green. send_alert: the guard caps the popup's spend, sizing base = live account, buffer gone → NO popup.

### STAGE 2 — THE POPUP BRAIN (93e1324 + ad13541, test_popupbrain.py 33/33)
1. **THE BRAIN-JUDGED BAR — FAIL-CLOSED (ad13541, the JUP lesson)**: a CURRENT deep scan runs before the popup; the BRAIN'S number must clear the popup floor (89/78/55-lone-wolf). **A broken/unavailable deep read = NO popup, ever.** Champion + mover popups. The QC still fails open (manual scans). Bridge writes unaffected.
2. **THE STALE-MOVE GUARD** (STALE_MOVE_H=2.0, STALE_MOVE_DROP_PCT=1.5): a RE-ENTRY whose 4h high is ≥2h old AND price ≥1.5% below it → popup skipped. Fresh first-fires exempt. Fail-open.
3. **THE GOAL CLOCK** (_window_goal_pct): goal = the engine's expected move clamped 1-3% (unknown coins read the rotation MEDIAN); window = goal×60min clamped 60-180min. "Aim for plus N percent! ... If it hasn't paid by {out_by}, get out!"
4. **THE WORTH FLOOR (ad13541 + cfa83e2 — a DIAL)**: the sized entry × goal must pay at least the DIAL's share of the daily pace or the popup is bumped. Sidebar slider under the CONTEST ACCOUNT plate (0-100% of daily pace; 0% = off) writes `worth_floor_frac` to console_state.json; **both gates read it LIVE via _worth_floor_frac() at alert time — no scanner restart. Currently 0.25 (the user's choice = $60 floor on the live 10k).** Fail-open on a broken account read. Fresh accounts: high-dd coins stay bumped until the buffer grows.

### STAGE 3 — THE GOAL GAME (09a87f6, test_goalgame.py 21/21)
The light: TAKE IT $20-counting → the GOAL call — watch entries carry goal_pct + window_min. "Goal reached! X up N percent — take the profit!"; _save_take_payout at the goal. QC: the goal gate (engine raw move ≥1% — "TOO SLOW"); header "GOAL +X% · ENGINE EXPECTS Y%". FAST $20 board behind FAST_BOARD_ENABLED=False. **THE LEFTOVER-NAME TRAP (1ea6f42)**: the pace_ok→goal_ok rename missed one QC LED-row reference → NameError killed the QC render; test_goalgame asserts "pace_ok" appears nowhere in app.py. LESSON: renames need a no-old-name assertion; pyflakes (uvx) catches undefined names.

### THE SOLD AUTO-PnL (Sep 2, ed6073c, test_soldpnl.py 14/14)
The SOLD tap does everything: fetches the LIVE price at tap time → pnl_net_usd(entry, exit, size) (0.08% RT cost included) → **auto-fills the contest balance** (current + net; losses subtract) → **appends to contest_trades.jsonl** (sold_at stamp, pair, entry, exit, size, pnl_gross, cost_rt, pnl_net, balance_after, goal_pct, held_min) → speaks "X sold! Plus/Minus N dollars net! The balance is now B dollars! Adjust me in the sidebar if that's off!" **No size / no live price → the manual reminder path stands.** Console-side — live on F5. contest_trades.jsonl = the contest trade log.

### THE WINNER-LOCK EXCEPTION + THE DEEP SCAN (Sep 2, d7d2c59 + d3861cc, test_winnerlock.py 10/10)
THE ASTER FADE: ASTER ran +2.5% to 0.74501, faded, lost momentum — the Aug 30 winner lock froze it while green ("we don't want to hold weak positions"). THE FIX: the fresh scan runs BEFORE the lock in _challenge_weakest; **a winner whose OWN chart says SELL is challengeable even in profit — the chart decides, not the price trail** (no giveback test — the user explicitly chose bearish-read-only). **The lock-breaking read is the DEEP read (d3861cc): the brain's number decides the lock; enable/disable wrapped in try/finally so the deep flag never leaks.** Buy/hold reads keep the lock; a broken scan keeps the lock (fail-locked); the strength math guts a bearish incumbent (entry_conf − scan_conf); fresh-buy protection + the 10-point CHALLENGE_MARGIN unchanged (the Aug 29 ENA/NEAR churn lesson intact).

### THE POSITION LADDER (Sep 2, d3861cc bot + 1b19849 scout, test_positionscale.py 20/20)
User rule: "a growing scale will change the focus from one asset to multiple assets as the account balance grows. 1k = 2 assets, 5k = 5 and so on up to 10k = 10." **max_positions_for(total_account_value)** in signal_bridge (the copy both processes resolve): piecewise through (1k→2, 5k→5, 10k→10), clamped [1,10]; **below $1k = 1 — concentrate all capital on the single best pick (the Aug 25 rule)**. Balance = TOTAL account value (free cash + positions at market — the user chose this; _get_total_account_value in the reader). The READER enforces the ladder at take-time (the gate: open_count >= max_positions_for(total)); **the ENGINE sizes each buy at total/slots** so the spread is real (guard: _slots > 1 keeps all-in for single-slot accounts; fail-open to all-in on a broken read); the bridge WRITE-side caps at POSITION_CEILING (10) so challengers always reach the reader. MAX_CONCURRENT_POSITIONS=1 RETIRED (kept defined). **At today's ~$184 the bot stays 1-slot — the ladder unlocks as winnings land.**

### THE JUP INCIDENT (Sep 2, 14:52 — the case that forced fail-closed + the worth floor)
The champion alert fired "Buy JUP! 92 percent sure! Small size only $233!" — the 92 was the QUICK number (deep read errored → fail-open hole), the QC's brain said 67 two minutes later, the 1h knife flipped at 15:00 → GET OUT ×7 → cleared 15:08. All in one candle. Fixes = ad13541.

### THE MOVER POPUP CONVERSION (Sep 2, 0488911, test_moverfunnel.py 44/44)
Title "🚀 MOVER BUY {asset} — {brain}% SURE" (the BRAIN'S number); the goal clock; stale-move guard on EVERY mover fire; sizing = live account → book carry → 3% GUARD cap (MOVER DEATH LINE / MOVER TOO THIN → no popup); her four size verdicts; tide + re-entry lines. The bridge write stays BEFORE the desktop stage. ad13541 added fail-closed + the MOVER WORTH FLOOR (dial-aware).

### THE DCA TRANCHE RETIREMENT (Sep 2, 50c5f83, test_dcagate.py 10/10)
The exit-check loop's buy-the-dip path RETIRED behind **SCOUT_DCA_ENABLED=False**. Code kept; the reader side untouched (dormant). Re-arm ONLY by the user's explicit call.

### THE EXIT-CHECK RATCHET BUGFIX (Sep 2, 1ea6f42 — pyflakes-found)
The EXIT CHECK page read `high` BEFORE `_cloned_trailing` defined it — the ratchet NEVER fired there. Fixed: reads the tracked high (bridge + session) BEFORE the call. Also `from typing import Any` added.

### THE CARD KNIFE-GUARD BUGFIX (Sep 2, 187abe8 — pyflakes-found)
The card's headline picker read `live` BEFORE fetching the ticker — the knife NEVER ran on the card path. Fixed: fetch first. AppTest smoke clean.

### TIER AUTO-ADJUST (Sep 2, 8a678bf, test_tierauto.py 26/26)
The light's hard stop: half the current buffer (min $5). Giveback/loss warnings: 1.5% of entry size. TODAY'S FACTS tier-aware. Goal language everywhere.

## THE VENV INCIDENT (Sep 2 — REPAIRED)
`uv add --dev pyflakes` ran WITH THE STACK RUNNING: uv hit DLL locks, aborted mid-transaction, left the venv PARTIALLY UNINSTALLED. REPAIR: user closed all 4 windows (COUNT 0 verified) → `uv sync` completed cleanly (streamlit 1.61.1 verified) → import chain verified → tests green. LESSON (burned in): NEVER uv add/uv sync while the stack runs — use `uvx <tool>`.

## CALIBRATION DISCIPLINE (Aug 31): verdicts come from PRICE DATA, never user taps. The JUP 14:52 case: the qcbar lesson (90+ = least trustworthy band) applies doubly when the number isn't even the brain's.

## THE KRACKEN FUNDED RULES (support.kraken.com/articles/funded, Aug 28 2026; NO API endpoint)
Tiers 1k/$20, 5k/$50, 10k/$90. THE DEATH LINE: -3% from starting balance, never moves, touch = account closes; NO daily limit. Challenge: +12% target, no time limit, one at a time. Funded: 80% split, withdraw anytime (resets balance to base). Costs: 0.04% spread each side (0.08% RT), no commissions, counts toward target AND the line. Mobile app only, dollar-amount buys, NO leverage, NO STOP ORDERS (the alert system is the ONLY exit). MYTHS CORRECTED: no re-entry fine; trading windows/rotations/20:00 cooldown are OUR constructs.

## THE KILL-SWITCH/CIRCUIT-BREAKER + THE FLATTEN LESSON (Sep 1-2)
The breaker (−10% daily) reads withdrawals as losses — fired on fees three times (Sep 1, Sep 2 ×2). **THE ASTER FLATTEN (Sep 2 17:38): the $90 challenge fee fired the breaker → the flatten SOLD ASTER at 17:38 — 2 minutes BEFORE the re-baseline could prevent it (re-baselined 17:40). LESSON: when the user plans to fund anything from the real account, re-baseline FIRST (diag_rebaseline2.py) or tell me before the transfer.** After the re-baseline cleared the lock, the pending APT signal took the seat (filled 17:48). NVDA.EQ tokenized stock in the balance (untradeable).

## RESTART DISCIPLINE (Aug 30, user rule) — I do NOT restart the stack
The user starts via the desktop Kraken shortcut (start-only-missing + mutex). **Stack restarted 9/2 16:57 (scanner batch live: 0488911, 50c5f83, 1ea6f42, ad13541, cfa83e2, 187abe8). 005b9d3 + ed6073c + 1b19849(scout signal_bridge) are console/scout-side — F5 / live-read. PENDING BOT WINDOW RESTART (2 commits): d7d2c59 (winner-lock exception) + d3861cc (position ladder + deep winner-lock scan) — close the Bot window + run the shortcut. APT position unaffected (spot lives at the exchange; fresh-buy cooldown is DB-timestamped).**

## THE ONE-VOICE REVERT LESSON (Aug 31) — a question mark or "thoughts?" = ASK. (Burned 3×.)

## THE PRIORITY DIRECTIVE (Aug 31): "less entries but more money, bigger margins from big coins, high confidence... it still needs the brain" — enforced by the worth floor dial.
THE THIN-BOOK BRAIN RULE (451ae90, test_thinbrain.py 23/23): a book that can't carry the FULL balance only greens when the brain clears the champion floor. ONE BUY VOICE: the champion alert + the mover popup.

## THE 'ALREADY RAN' CONFIRMATION WINDOW (82011a4, test_confirmwindow.py 17/17): QC + card pass 15 min; scanner + funnel stay strict.

## HER SIZE VOICE (ba891e8, test_hersize.py 11/11): "Big book — all $BAL in fine!" / "Small size only — $X!" / "Too thin!" / "Book unreadable — your call on size!"

## THE DEPTH-STABILITY FIX (6f4a0fa, test_depthcheck.py 14/14): dries-only + ≥$500 material rows.

## THE TREND-REVERSAL EXIT + SILENT CHANNEL (76a8810/0b40507/768668a — RETIRED Sep 4): the original design was trend reversal = GET OUT NOW via the 1h EMA knife → silent scout_trend_exit bridge write → reader carve-out. With the Sep 3 bot blessed, the channel is retired (SCOUT_TREND_EXIT_ENABLED=False; the reader never admits that source) — the knife function is kept (the BOT'S BOOK panel shows the trend state, informational). EXIT STACK NOW: SL −5% → TP +10% → ATR trail (arm +0.5%, ×4) → strategy sell (≥+4% AND ≥25 conf) engine-side, plus the scanner's 5-min exit check (scout_exit_check — trailing stop, strategy sell ≥75/≥60-momentum, exhaustion).

## THE NEAR-MISS DEEP SCAN + TIDE-AWARE ROTATION BAR (8fff734, test_nearmiss.py 39/39): within 5 pts BELOW the bar → deep scan → judged on the BRAIN'S number. RISING relaxes 11 (floor 60); FLAT keeps the data bar. ONE READ, EVERY CONSUMER.

## THE SILENCE DIAGNOSIS + TIDE-AWARE FLOORS (272add3, test_tidefloor.py 18/18): DESKTOP_ALERT_MIN: RISING → 78; FLAT → 89; lone wolves 55; broken read fails closed to 89 (and the brain read itself fails closed — ad13541).

## THE SEP 3 BOT IS THE BLESSED DESIGN (user decision, Sep 4: "keep the simpler Sep 3 bot — the rotation design replaces the old rules")
The Sep 3 reader rework (uncommitted by the prior session — safety-snapshotted as 112cd3e) is THE design, with ONE amendment the same day: THE POSITION LADDER IS BACK (user rule, respecified Sep 4: "'max_positions_for' is for when the real money account grows we shift gears and focus on more assets then one at a time. 1k-3k=3 assets; 3k-5k=5 assets; 5k-10k=10 assets and so on") — RANGES stepped (anywhere in 1k-3k is exactly 3), below $1k = 1 (concentrate), $10k+ holds at 10 (capped — new checkpoints when the account gets there). SIZING STAYS ALL-IN (user choice, Sep 4): each buy deploys 99% of whatever's free — the first position takes everything, later seats fill via ROTATION (sell the weakest to fund a >=70% signal); the ladder is the CONCURRENCY CEILING the reader enforces at take-time on the TOTAL account value. STILL RETIRED BY DECISION: winner-lock/challenge displacement (the rotation's _find_weakest_position does the job: a recovering own-read buy>=50 is skipped, sell-read -5, fading -2, no pnl immunity), the scout_trend_exit channel (SCOUT_TREND_EXIT_ENABLED=False in the scanner; the reader admits sells ONLY from scout_exit_check), the engine knife (exit floor = the engine's SL/TP/ATR-trail + the scanner's 5-min exit check), the carry cap (engine deploys 99% of available), restricted_pairs (deleted; the scanner-side blacklist still gates scout-originated buys). RESTORED (collateral damage of the rewrite, not part of the decision): scout_mover re-admitted to the reader's buy sources. Tests: positionscale 27 (ladder v2 + rotation), winnerlock 11, trendbridge 30 — full battery 577/0. The BOT'S BOOK panel no longer claims "GET OUT SENT" (channel retired); the knife state is informational.

## THE ROTATION-AWARE POPUP FLOOR + HEAD-TO-HEAD CHALLENGE (Sep 4, 6a9fe4d, test_popupfloor.py 51/51)
The popup floor knows the rotation schedule now: DESKTOP_ALERT_MIN starts from ROTATION_POPUP_FLOOR {00-04: 84, 04-08: 89, 08-12: 84, 12-16: 89, 16-20: 93, 20-24: 99}, then RISING relaxes by 11 (never below 78), lone wolf discounts to 55, broken reads fail closed to the rail. Calibrated by diag_popupfloor.py (110 decided outcomes: in Asia/EU-US 89+ reads won 45-50% while 60-78 reads won 75-83%; US-Late 89+ went 0-1 → the tighten). POPUP_NEAR_MISS_PTS=5 — the HEAD-TO-HEAD CHALLENGE: a brain read within 5 BELOW the floor fires as a CHALLENGER popup (title "— CHALLENGER", voice "just under the floor", message line "a near-miss, not a champion: size it like one"); below floor−5 stays suppressed; no brain read = no popup. popup_floor()/popup_floor_for_rotation() in background_scanner.py = ONE source of truth — the QC's thin-book champ_floor reads the same stack + the same challenger window (champ_challenge/champ_challenge_min in qc_result; the why carries ", challenger"; green reads "SCALP GREEN LIGHT — CHALLENGER"). TRUMP/RENDER (91-96% reads) were ABOVE every rail — that was the breakout-detector fix (dd380cb), not this one.

## THE MOVER FUNNEL (46abac9/169052a + 0488911 + ad13541, test_moverfunnel.py 44/44)
All-pair runners ≥+5%/24h + yesterday's losers; MOVER_MIN_CONF=55; deep ≥60 + no downward trend; tide stamp + OUT = no write; fresh-listing guard (≥100 candles); rotation lockout; source="scout_mover"; 10-min pacing. Desktop: brain-judged ≥89 (fail-closed) + new-game format + worth floor (dial). Bridge write = the bot's ledger.

## ALL-IN SIZING + CARRY CAP (bot side, test_carrycap.py 17/17): trade_pct=99, dca_enabled=False. deployment = min(99% of available, 20% of top-of-book, total/slots per the ladder); <$100 carry → skip.

## THE TIDE SIZING DECISION (test_tidestamp.py 22/22): 99% in every tradeable tide. ⏰ "KrakenTideDataReminder" fires 2026-09-07 10:00 AM → diag_tidedata.py (keep 99% unless FLAT ≥15pts below RISING AND negative avg).

## PHONE PUSH = BUY NOW / GET OUT NOW ONLY (77e8bc6, test_pushfilter.py 11/11): buys HIGH; GET OUT/SELL/STOPPED OUT URGENT; TAKE IT does NOT push; 5-min throttle. Topic kraken-scout-lsvdtkep (re-subscribe pending).

## HER WORDS FEED + POPUPS (4f072ab + 3763fdc): _speak_server is the ONE mouth (speaks + logs voice_log.jsonl capped 150 + pushes); _render_her_words on every page; 15s ticker. The console's _speak() queues speech_queue files the scanner picks up.

## ROTATION MEMORY (125f1f3, test_rotation.py 24/24): LOCKOUT (one green per asset per rotation); YESTERDAY'S HOUR (blowoff demotion).

## THE LONE WOLF EXCEPTION (ecae0bb + bc7fbd0, test_lonewolf 21 + test_qclonewolf 13): FLAT tide + own 24h move ≥+8% → 35 pts / ≥+5% → 25 pts.

## THE QC'S BAR: QC_SCALP_FLOOR=60, deep conviction. qcbar data: conviction does NOT separate winners; the 90+ band = one triple-counted trade (80-89 won 11%).

## TWO-LEDGER EXIT RULE (Aug 30, updated Sep 4): contest exits never sell real money; the ONE scout SELL source the reader admits on real money is scout_exit_check (the scanner's 5-min exit check). scout_trend_exit is retired (SCOUT_TREND_EXIT_ENABLED=False).

## THE os.kill WINDOWS BUG + SAFE LAUNCHER (Aug 28): use tasklist for liveness. launch_stack.ps1: starts only missing, never kills.

## VISIBLE WINDOWS (Aug 30): four cmd windows; visible instances don't write logs — liveness = tasklist.

## BOT EXIT FIX (21ab35c): min_profit_to_sell_pct 4.0. Fee math: bot real-spot RT 0.52%; contest RT 0.08%.

## BOOKS & MONEY-FLOW (Aug 30) + Sep 1-2
Payouts: $251.64 (Aug 20-21). OPEN MYSTERIES: Aug 22 -$305, Aug 25→26 -$90, unattributed ADA sell Aug 27 20:00:30. Fees burned: $90 + $20 + one + $90 (the 10k, Sep 2). **The bot's position rolled (Sep 2): UNI closed 05:20 −$16.35 → ASTER 08:22 @ 0.72655 (92%) → peaked 0.74501 (+2.5%) → faded → FLATTENED by the breaker 17:38 (the fee misread) → APT bought 17:48 @ 0.5839 (91% scout_auto) — CURRENT.** Real account ~$183.59 free + APT. diag_syrup.py (bridge dump) + diag_aster.py (position roll audit) are the tools.

## FIAT-PAIR BOUNCE SPAM (Aug 28) + LEARNED PAIR BLACKLIST (9f2c4a1): _FIAT_BASES filtered; restricted_pairs.py blocks restriction-error pairs.

## QC SCALP GATE (rebuilt Sep 1): green light requires ALL: conviction ≥60 (deep); goal gate ≥1%; FRESH; not cooldown; no FALLING trend; book carry >0; not TIDE OUT; rotation not locked; no blowoff yesterday; THIN BOOKS NEED THE CHAMPION FLOOR; 3% GUARD caps size; buffer gone = AT THE DEATH LINE. Rejection order: ALREADY RAN → DUMPED YESTERDAY → TIDE OUT → DOWNWARD TREND → TOO THIN → AT THE DEATH LINE → THIN BOOK BRAIN NOT EXCEPTIONAL → TOO SLOW → COOLDOWN → weak setup.

## THE ROTATION CONFIDENCE BAR (terminology): auto_trade_threshold = the ROTATION BAR / Gate 2's bar. Data-calibrated (60+35×fbr). Tournament evaluates at 50. The funnel ignores it (own 55).

## TWO LEDGERS (rebuilt Sep 1): CONTEST path = the goal game (live 10k, brain-judged popups FAIL-CLOSED, goals 1-3% on 1-3h windows, 3% guard, worth floor DIAL at 25%, SOLD auto-PnL, solid trends only, buy-only). BOT path = trend logic + the winner-lock exception (deep scan) + the position ladder. No old-game buy paths remain.

## Voice/push architecture
ONE voice engine (the scanner speaks via Zira every 2s). ONE BUY VOICE: the champion alert + the mover popup (same format). Alert voice: "Buy X! {brain}% sure! [Re-entry!] [Lone wolf!] Aim for plus N percent! [size verdict] {The tide is rising!} If it hasn't paid by {out_by}, get out!" The QC speaks when asked. The light: goal calls, tier-scaled warnings, the clock, tide warnings. SOLD: "X sold! Plus/Minus N dollars net! The balance is now B dollars! Adjust me in the sidebar if that's off!" (manual fallback: "Update the contest balance in the sidebar"). PHONE: BUY/GET OUT only. The silent trend-exit channel bypasses everything.

## Timezone + conventions
ZoneInfo("America/Denver"). ONE desktop shortcut (start-only-missing, mutex, VISIBLE windows, never touches XMR). LONG-ONLY. Test scripts write *_check.txt and exit 0 even on FAIL — read the file. Never python -c with nested quotes — write .py files. PS 5.1 limits. The bot's scout_signal_reader.py is the LIVE copy (and inserts the SCOUT path first — the scout's signal_bridge/strategy are what the bot resolves). Streamlit edits roll back on interrupt — verify with git diff. NEVER uv add/uv sync while the stack runs — use uvx.

## Process/version discipline
Verify process start > commit time before saying live. I do NOT restart. Console module cache: reload-if-missing pattern. 2 pythons per component = ONE instance. NEVER os.kill for liveness (use tasklist).

## Bridge state discipline (Aug 24): pending → acknowledged → filled → closed. Open records = real positions. Challenge displaces weakest (with the Sep 2 lock exception) ≥10 pts; the position ladder sets the slot count. own_scanning_enabled=False.

## Key files
- kraken_scout/contest_account.py — THE CONTEST ACCOUNT + pnl_net_usd; contest_account.json (LIVE 10k challenge, fresh $10,000)
- kraken_scout/contest_trades.jsonl — the SOLD auto-PnL trade log
- kraken_scout/app.py — console: CONTEST ACCOUNT plate + THE WORTH FLOOR DIAL (at 0.25), QC (full gate stack), the CARD, the light panel, her words, tier-aware TODAY'S FACTS, GOAL CALL toggle, SOLD + auto-PnL; FAST_BOARD_ENABLED=False; EXIT CHECK ratchet reads the tracked high
- kraken_scout/background_scanner.py — ROTATIONS, rotation_gate(+confirm window), lone_wolf_discount, knife_reversed_1h + TREND-EXIT WATCH, NEAR-MISS + TIDE-AWARE BAR, _window_goal_pct + _stale_move + GOAL CLOCK + BRAIN-JUDGED BAR (FAIL-CLOSED) + STALE GUARD + 3% GUARD + THE WORTH FLOOR via _worth_floor_frac(), THE NEW-GAME MOVER POPUP, THE DCA TRANCHE (RETIRED), contest_light, THE MOVER FUNNEL, _speak_server/_log_her_line/_push_to_phone, tide, scanner.lock
- kraken_scout/signal_bridge.py — THE bridge DB + **max_positions_for (the position ladder)** + POSITION_CEILING write-through (the copy BOTH processes resolve)
- kraken_xrp_trading_bot/scout_signal_reader.py — THE LIVE READER (Sep 3 design, blessed Sep 4 + ladder v2): POSITION LADDER gate (max_positions_for(_get_total_account_value) — <1k=1, 1k-3k=3, 3k-5k=5, 5k+=10; all-in sizing, later seats fill via rotation), already-holding gates (bridge + bot DB), THE ROTATION (>=70% buy with <$100 free sells the weakest BEFORE the $100 min reject; a second rotation fires on engine-Insufficient), buy sources scout_auto/tournament/gainer/mover/dca, sells ONLY from scout_exit_check (incl. manual Kraken positions), kill switch + circuit breaker, 30-min sell cooldown, scout-path-first imports (the scout's signal_bridge is what it resolves)
- kraken_xrp_trading_bot/bot_engine.py — per-slot sizing (total/slots, fail-open to all-in) + the carry cap + the exit stack
- kraken_scout/diag_syrup.py (bridge dump), diag_aster.py (position roll audit), diag_rebaseline2.py (breaker re-baseline)
- kraken_scout/execution_tiers.py — measure_book; SIZE_CAP_PCT=20
- kraken_scout/volatility_engine.py — THE coin profiles; rotation_thresholds.json
- tests (kraken_scout root): moverfunnel 44, contestline 33, popupbrain 34, positionscale 27 (ladder v2 + rotation, Sep 4), soldpnl 14, tierauto 26, winnerlock 11 (displacement protection), nearmiss 39, rotation 24, trendbridge 30 (two-ledger sell gate + retired channel), lonewolf 21, tidestamp 20, thinbrain 23, confirmwindow 17, tidefloor 19, depthcheck 14, qclonewolf 13, hersize 11, pushfilter 11, persist 10, dcagate 10, herwords 9, entrycontrol 55, popupfloor 51, trumpfix 11 (~577 checks, all green) + apptest
- kraken_scout/reminders/ — the Sep 7 tide task

## Accounting notes
Real account ~$183.59 free + **the bot holds APT @ 0.5839 since 9/2 17:48 (91% scout_auto)**. NVDA.EQ untradeable. **LIVE CONTEST: 10k tier, fresh $10,000 (bought Sep 2 ~17:29)**. Fees burned: $90 + $20 + one + $90. Pace $240/day; worth floor $60 (dial 25%). The position ladder at ~$184 = 1 slot (all-in APT); slots unlock at $1k → 2, $5k → 5, $10k → 10.

## Open items
- USER RULES (cumulative): ASK FIRST; never modify desktop/system scripts without telling; never restart the stack myself; "don't delete anything"; a question mark / thoughts request = ASK; the $20 game RETIRED; THE PRIORITY: less entries, more money — big coins, high confidence, brain-driven; never uv add/uv sync while the stack runs.
- **STACK RESTART PENDING (Sep 4, after the 08:34 restart): scanner (trend-exit retirement flag) + console (BOT'S BOOK labels) + bot (scout_mover re-admitted + POSITION LADDER v2 gate). Close the console window + rerun the shortcut. (Per RESTART DISCIPLINE: the user restarts, never me.) The popup floor + challenger went LIVE with the 08:34 restart.**
- **THE FLATTEN LESSON: re-baseline BEFORE funding anything from the real account (the ASTER flatten fired 2 min ahead of the re-baseline). Tell me before transfers.**
- **THE LIVE 10k RUN: first alerts through the full gauntlet are the validation. Dial at 25% ($60 floor). SOLD auto-fills the balance — hand-correct when reality differs.**
- ⏰ TIDE DATA REMINDER: 2026-09-07 10:00 AM → diag_tidedata.py.
- Re-subscribe the ntfy topic (kraken-scout-lsvdtkep) on the phone.
- QC-side rotation_mark_fired may silently fail — log exceptions if entries go missing.
- OPEN MONEY MYSTERIES: Aug 22 -$305, Aug 25→26 -$90, the ADA sell Aug 27.
- If /scout fails on the phone: suspect VPN/private DNS/ad-block.
- Pre-existing: pool ProxyPoller error every ~60s.
- FOLLOW-UP POLISH: console light panel $-remnants; QC knob wording; TODAY'S FACTS could read contest_trades.jsonl.