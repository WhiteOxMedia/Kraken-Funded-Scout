# VACUOUS TRIAGE — the 52 journal verdicts (2026-09-30)

Question: for each of the 52 drives the mutation sweep labeled VACUOUS
(stayed green under every mutation), is that a coverage gap worth pinning,
or is the drive genuinely untestable by this harness?

Method: (1) AST classification — resolve import aliases, count runtime
calls into each drive's guarded modules, detect live-dependency imports
(ccxt/keyring/sqlite3/sqlalchemy/streamlit). (2) Full re-check of every
candidate through the sweep (`diag_mutation_sweep.py --drives`, both
interpreters — pandas/ccxt drives ride `.venv`). (3) Sandbox kill proofs
for every repair, using `mutation_ops.mutate_source` on the same module
sets the sweep mutates.

## Verdict distribution (journal `results` updated in place, `triage` block added)

| class | n | disposition |
|---|---|---|
| obsolete-verdict kills | 7 | re-checked → KILLED (details below) |
| repaired (missing verdicts) | 4 | drives fixed, kill-proven in sandbox → KILLED |
| moved to BASELINE-RED | 2 | herwords, trendbridge — real pin drift, separate repair |
| genuinely untestable | 39 | live-market / live-state / source-text / static-audit — by design |

## 1. Obsolete verdicts — the exit-blind tail, already fixed (7)

depthcheck, popupbrain, positionscale, soldpnl, tierauto, nearmiss,
scansethonesty. The sweep's journal predates the same-morning
fleet-honesty patch: these drives *had* real mutant-killing pins, but
their exit-blind tails rendered the pins invisible to the rc-only judge
(red printed, rc 0 → "green"). Re-run under the fixed tree: all KILLED
(soldpnl and tierauto under 130 contest_account mutants; nearmiss under
1851 background_scanner mutants).

## 2. Missing coverage — repaired + kill-proven (4)

| drive | defect | repair | kill proof |
|---|---|---|---|
| test_longonly.py | verdicts rendered into text, no aggregation — could never fail | ok-flags + RESULT + honest exit | 3 failed under scanner+strategy mutation |
| test_contest_mode.py | same | same | 1 failed under scanner+strategy mutation |
| test_liq_filter.py | counted results but printed no verdict, no exit | RESULT + honest exit (cases 1/3 were already complementary only-path pins: rotation-floor vs daily-backstop) | 1 failed under background_scanner mutation |
| test_trump_render.py | counted + printed `TOTAL` but no exit, **and** imported via absolute `sys.path.insert(0, r"C:\...\kraken_scout")` — the drive tested the LIVE tree, never sandbox code (a gate-integrity hole: inside the commit-gate sandbox it still passes while staged code ships) | `Path(__file__)` idiom + RESULT + honest exit | 3 failed under config+strategy mutation |

Third flaw class (beyond exit-blind and print-blind): **verdicts that
never aggregate** — per-check PASS/FAIL rendered as prose, no counters,
so the drive cannot fail regardless of what the mutants do. The
commitgate tripwire grew a **no-aggregate probe** (static: a drive whose
last PASS/FAIL print has no aggregation idiom and no exit blocks the next
commit). commitgate 72/0 with the probe live.

The absolute-path hole extends to `test_trendbridge.py` too — fix its
`sys.path` insert when the pin-drift repair happens.

## 3. Moved to BASELINE-RED (2) — then repaired same day

test_herwords.py, test_trendbridge.py — baseline rc=1 at recheck; the
failing pins are the pin-drift family the fleet-honesty fix surfaced.
**Both repaired Sep 30** (see the red-six repair log below) and
re-judged through the sweep: KILLED. The full red-six repair:

| drive | failing pins | diagnosis | repair |
|---|---|---|---|
| test_lonewolf.py | 5 (tier math) | drift vs the Sep-23 adaptive carve-out retune (documented in lone_wolf_discount's docstring): req/scale are carve_out.py's regime-aware reading, OUT tide needs the lone_wolf permission gate | deterministic carve_out stub (restored before the tail) restores the documented 35/25/0 values + new section 13 pinning the adaptive semantics (scale 1.5 → 38/52, tightened req, permission-denied hatch closed) — 26/0 |
| test_trendbridge.py | 1 + absolute-path hole | the Sep-14 EXIT-WATCH switch wiring was replaced by the SCOUT_TREND_EXIT_ENABLED module gate (same no-silent-SELL guarantee); the absolute bot-repo sys.path insert shadowed the scout's signal_bridge | pin re-aimed at the current gate shape; absolute insert removed — 28/0 |
| test_eracompare.py | 1 | environment drift: the pin asserted the repo-root one-shot marker doesn't exist, but the scheduler's mark_done fired Sep 28 20:00 (the feature's own success); write_report itself never regressed | STATE_PATH sandboxed in the block like REPORT_PATH — 35/0 |
| test_herwords.py | 1 | the her-words feed call block re-indented 8 → 4 spaces in the Sep-21 status-line re-fit; the adjacency itself never moved | pin re-aimed at the current indent — 9/0 |
| test_marketgoal.py | 1 (morning) | NO REPRO twice (46/0 rc=0) — same live-state race family as commitgate's transient | none needed; re-judged KILLED by the sweep |
| test_worthdial.py | 1 | the pin demanded the diag's RUN ARTIFACT (scratch/reports_temp/_worthdial_check.txt) — manually generated, scratch-cleanable, ALWAYS absent inside the gate sandbox (SKIP_COPY excludes scratch/) | pin now tests the tool's wiring: diag exists + writes to the sanctioned sink — 37/0 |

Journal re-judge after the repairs (pop + `--drives … --resume`):
all six **KILLED** (lonewolf/eracompare/herwords/marketgoal/trendbridge
under background_scanner's 1851 mutants; eracompare also kills
discipline 493 + era_compare 20; worthdial kills scanner 1851), and the
stale test_takeit.py BASELINE-RED (pre-repair) was re-judged → KILLED.
**The journal's ledger now stands at 62 drives: killed 23, VACUOUS 39,
BASELINE-RED 0.**

## 4. Genuinely untestable by this harness (39)

- **LIVE-MARKET (4)**: eth_gap, hype_now, kii, kii_woulda — verdicts keyed
  to live ccxt prices/OHLCV (the "why did X happen" diagnostics).
- **LIVE-STATE (7)**: bot_day, dopamine, tour_state (live signals.db
  walkers); night, no_alerts, one_asset, payouts_e2e, xrp_scan,
  xrp_signals (live db/keyring state); plus botdb, night_pnl, sunday,
  winners, zec_trace (live Postgres via sqlalchemy). Guarded-module calls
  where present carry no verdict.
- **SOURCE-TEXT pins (~24)**: cooldowncheck, dcagate, exitsynthesis,
  goalgame, herwords*, tidefloor, tierauto*, popupbrain*, liq_filter*,
  nearmiss*, trendbridge*, carry*, scansethonesty*, sink_hygiene,
  balancepop, entry_control, hersize, hookparser, persistexit, qclonewolf,
  thinbrain, toggleguard, uismooth, winnerlock, funnelplate, bracket_click,
  chase, payouts, contest_mode* — these pin guarded code by reading its
  source as text (`"constant" in src`); file bytes are immune to runtime
  mutants *by construction*. (* starred ones ADDITIONALLY carry runtime
  pins that kill — their vacuity verdicts are already resolved in
  sections 1–2; the remainder pin formatting/wiring that behavior
  mutation cannot judge.)
- **SCAFFOLD-ONLY (rest)**: only `scratch_space.py` (test scaffolding)
  is mapped — no verdict-bearing logic to mutate.
- **STATIC-AUDIT (1)**: sink_hygiene runs diag_testleaks's AST audit as
  text — behavior mutants cannot alter a static verdict; the engine is
  guarded by its own permanent self-check.

## Mechanics

- Journal: `artifacts/mutation_sweep/journal.json` re-judged (52 → 39
  VACUOUS; +11 KILLED; +2 BASELINE-RED), with per-entry `detail` reasons
  and a `triage` summary block. Pre-triage copy preserved at
  `journal.pretriage.json`.
- Fleet-honesty tail #2: five drives printed `FAILURES: N` (takeit-family
  shape, invisible to rc-only consumers) without exiting — carry,
  execution_tiers, giveback, headline, trend — all patched with
  `raise SystemExit(1 if fails else 0)`; all verified green rc=0.
- New tripwire: commitgate's no-aggregate probe (fleet-wide, static).
- Classifier scripts kept in `scratch/` (`_vacuous_classify.py` idiom)
  — rerunnable when the journal changes.
