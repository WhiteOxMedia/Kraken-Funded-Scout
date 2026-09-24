# test_replaylive.py -- grades THE REPLAY-LIVE DIAG (user spec, Sep 8:
# "extend the replay engine to grade the recorded live snapshots
# (champion + mover) against the deep candle archive as a first pass
# before any refactor").
#
# The diag's grading core is PURE: outcome_of() walks forward candles
# for a first-touch verdict, grade_popup/grade_burial record one event's
# grade, summarize() folds them, and graded_live() covers pairs/spans
# the archive forgets (XMR -- the live bot's own pair -- is the reason
# this exists). Synthetic candles only: no network, no live stores.
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import diag_replaylive as dl
import scratch_space   # scratch policy: nothing volatile lands in the repo root

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


T0 = 1_800_000_000.0


def candle(i, o, h, l, c):
    """One 5m row i candles after T0, [ts_ms, o, h, l, c, v]."""
    return [int(T0 * 1000) + i * 300_000, o, h, l, c, 1.0]


# ── 1. outcome_of: the first-touch semantics ──────────────────────────

check("tp touched before sl grades 'tp'",
      dl.outcome_of([candle(1, 100, 102, 99, 101)], T0, 100.0,
                    101.5, 98.5) == "tp")
check("sl touched before tp grades 'sl'",
      dl.outcome_of([candle(1, 100, 101.2, 98, 99)], T0, 100.0,
                    101.5, 98.5) == "sl")
check("a candle containing both grades 'ambig' (the honest 5m bar)",
      dl.outcome_of([candle(1, 100, 102, 98, 100)], T0, 100.0,
                    101.5, 98.5) == "ambig")
check("first touch wins across candles (a high before the low)",
      dl.outcome_of([candle(1, 100, 101.6, 99.5, 100),
                     candle(2, 100, 100.2, 98.0, 99)], T0, 100.0,
                    101.5, 98.5) == "tp")
check("no touch inside the window grades 'timeout'",
      dl.outcome_of([candle(1, 100, 100.4, 99.6, 100)], T0, 100.0,
                    101.5, 98.5) == "timeout")
check("the window cuts at win_min (a touch past it never grades)",
      dl.outcome_of([candle(25, 100, 102, 98, 100)], T0, 100.0,
                    101.5, 98.5) == "timeout")
check("a touch exactly on the last in-window candle grades",
      dl.outcome_of([candle(24, 100, 102, 100, 100)], T0, 100.0,
                    101.5, 98.5) == "tp")
check("no candles after t0 grades 'no-data'",
      dl.outcome_of([candle(-2, 100, 102, 98, 100)], T0, 100.0,
                    101.5, 98.5) == "no-data")
check("a zero/negative entry grades 'no-data' (never divides by it)",
      dl.outcome_of([candle(1, 100, 102, 98, 100)], T0, 0.0,
                    101.5, 98.5) == "no-data")

# ── 2. THE BURIAL FIX: no-entry must not collapse to no-data ──────────

check("a burial with no entry but a reachable tp grades (no-data bug)",
      dl.outcome_of([candle(1, 100, 102.0, 99, 101)], T0, None,
                    101.5, None) == "tp")
check("a burial tp never reached grades 'timeout', not 'no-data'",
      dl.outcome_of([candle(1, 100, 100.4, 99.6, 100)], T0, None,
                    105.0, None) == "timeout")
check("a burial tp of None grades 'no-data' (nothing promised)",
      dl.outcome_of([candle(1, 100, 102, 99, 101)], T0, None,
                    None, None) == "no-data")

# ── 3. candles_for: the archive window ────────────────────────────────

arch = {"XMR/USD": [candle(-30, 100, 100, 100, 100),
                    candle(10, 100, 102, 99, 101)]}
w, src = dl.candles_for(arch, "XMR/USD", T0)
check("the window pulls the archive rows around the event",
      len(w) == 1 and src == "archive")
w2, src2 = dl.candles_for(arch, "PEPE/USD", T0)
check("a pair the archive forgot returns (empty, 'none')",
      w2 == [] and src2 == "none")
w3, _ = dl.candles_for(arch, "XMR/USD", T0, before_min=0.0)
check("the pre-window is sized (before_min=0 drops the old candle)",
      len(w3) == 1)
check("a window-empty run reports 'none' (the archive cannot grade it)",
      dl.candles_for({"XMR/USD": [candle(500, 100, 100, 100, 100)]},
                     "XMR/USD", T0)[1] == "none")

# ── 4. THE LIVE FALLBACK: XMR grades instead of 'no-data' ─────────────

_t0_row = candle(0, 100.0, 100.1, 99.9, 100.0)


def fake_fetch(pair, t0_s):
    """Public-OHLC stand-in (the diag's _live_rows seam: pair, t0):
    rows from the fetch moment forward."""
    return [[_t0_row[0] + 300_000 * i,
             100.0, 100.0 + (2.5 if i == 3 else 0.1),
             100.0 - (0.1 if i != 3 else 0), 100.0, 10.0]
            for i in range(6)]


graded = dl.graded_live([_t0_row], fake_fetch, "XMR/USD", T0,
                        entry=100.0, tp=101.5, sl=98.5)
check("the live fetch covers the gap (verdict from fetched rows)",
      graded["verdict"] == "tp" and graded["source"] == "live")
check("the live grade reports how many candles covered it",
      graded["candles"] == 6)
graded_b = dl.graded_live([_t0_row], fake_fetch, "XMR/USD", T0,
                          entry=None, tp=101.5, sl=None)
check("the burial fix rides the same fetch (no entry needed)",
      graded_b["verdict"] == "tp")

# the burial variant: the goal is minted from the CHOSEN rows
cov = [candle(i, 100, 100.1, 99.9, 100.0) for i in range(0, 26)]
gb = dl.graded_burial_live(cov, lambda p, m: [], "NEAR/USD", T0,
                           quick=70.0)
check("a covered burial mints its goal from the archive close",
      gb["verdict"] == "timeout"
      and abs(gb["hypothetical_tp"] - 101.5) < 1e-9
      and gb["source"] == "archive")
gb2 = dl.graded_burial_live([_t0_row], fake_fetch, "NEAR/USD", T0,
                            quick=70.0)
check("an uncovered burial falls back to live rows for its goal",
      gb2["verdict"] == "tp" and gb2["source"] == "live")
gb3 = dl.graded_burial_live([_t0_row], lambda p, m: [], "NEAR/USD",
                            T0, quick=70.0)
check("no rows reaching the event -> no goal minted (never a fiction)",
      gb3["verdict"] == "no-data")

# THE HONESTY RULE: a partial view may not claim 'never touched'
partial = [candle(i, 100, 100.1, 99.9, 100.0) for i in range(0, 10)]
gp = dl.graded_live(partial, lambda p, m: [], "XMR/USD", T0,
                    entry=100.0, tp=101.5, sl=98.5)
check("partial archive + dead fetch -> ungraded, NOT a fake timeout",
      gp["verdict"] == "no-data")
gp2 = dl.graded_live(partial,
                     lambda p, m: [candle(i, 100, 100.1, 99.9, 100.0)
                                   for i in range(0, 26)],
                     "XMR/USD", T0, entry=100.0, tp=105.0, sl=98.5)
check("partial archive + live span proof -> honest timeout from live",
      gp2["verdict"] == "timeout" and gp2["source"] == "live")
gp3 = dl.graded_live(partial, lambda p, m: [candle(i, 100, 100.4,
                                                     99.6, 100.0)
                                            for i in range(0, 26)],
                     "XMR/USD", T0, entry=100.0, tp=101.5, sl=98.5)
check("a spanned live window with no touch grades timeout from live",
      gp3["verdict"] == "timeout" and gp3["source"] == "live")
graded_0 = dl.graded_live([candle(1, 100, 100.4, 99.6, 100)],
                          lambda p, m: [], "XMR/USD", T0,
                          entry=100.0, tp=101.5, sl=98.5)
check("an empty fetch leaves the event ungraded (no premature timeout)",
      graded_0["verdict"] == "no-data" and graded_0["source"] == "archive")
graded_err = dl.graded_live([candle(1, 100, 100.4, 99.6, 100)],
                            lambda p, m: (_ for _ in ()).throw(
                          RuntimeError("net down")),
                            "XMR/USD", T0, entry=100.0, tp=101.5, sl=98.5)
check("a broken fetch fails honest (ungraded, not a crash)",
      graded_err["verdict"] == "no-data" and graded_err["source"] == "archive")
check("timeout wins when candles exist but the window is empty",
      dl.outcome_of([candle(30, 100, 102, 98, 100)], T0, None,
                    105.0, None) == "timeout")

# the COVERAGE gate: a partial archive view is not a verdict
full = [candle(i, 100, 100.1, 99.9, 100.0) for i in range(0, 26)]
check("rows spanning the deadline count as covered",
      dl._archive_covers(full, T0, dl.WINDOW_MIN, None) is True)
check("rows stopping short of the deadline are NOT covered",
      dl._archive_covers(full[:10], T0, dl.WINDOW_MIN, None) is False)
check("an empty row set is never covered",
      dl._archive_covers([], T0, dl.WINDOW_MIN, None) is False)
check("a running window only needs to reach ~now",
      dl._archive_covers(full[:20], T0, dl.WINDOW_MIN, T0 + 90 * 60)
      is True)
check("a still-running window without fresh rows is NOT covered",
      dl._archive_covers(full[:10], T0, dl.WINDOW_MIN, T0 + 90 * 60)
      is False)

# the 'open' verdict: the promise not yet ripe is not a timeout
check("an unripe window with no touch grades 'open', not timeout",
      dl.outcome_of(full[:20], T0, 100.0, 101.5, 98.5, now_s=T0 + 90 * 60)
      == "open")
check("a touch inside a running window still grades on its merits",
      dl.outcome_of(full[:20] + [candle(19, 100, 102, 99.9, 100)],
                    T0, 100.0, 101.5, 98.5, now_s=T0 + 90 * 60) == "tp")
check("an elapsed window with no touch stays 'timeout'",
      dl.outcome_of(full, T0, 100.0, 101.5, 98.5, now_s=T0 + 200 * 60)
      == "timeout")

# ── 5. THE STORE READERS (schema contracts) ───────────────────────────

check("load_popups survives a missing store (returns [])",
      dl.load_popups(db_path="_no_such_db_here.db") == [])
check("load_burials survives a missing store (returns [])",
      dl.load_burials(path="_no_such_file.jsonl") == [])
check("load_burials skips malformed lines without dying",
      dl.load_burials(path="_rl_tmp_lines.jsonl") is not None)

# ── 6. summarize: the folds ───────────────────────────────────────────

pops = [{"pair": "NEAR/USD", "source": "scout_auto", "ts": "t",
         "verdict": "tp"},
        {"pair": "NEAR/USD", "source": "bot_strategy", "ts": "t",
         "verdict": "timeout"},
        {"pair": "XMR/USD", "source": "scout_auto", "ts": "t",
         "verdict": "sl"}]
burs = [{"pair": "NEAR/USD", "gate": "vol_floor", "ts": "t",
         "verdict": "sl"},
        {"pair": "NEAR/USD", "gate": "trend_gate", "ts": "t",
         "verdict": "no-data"}]
agg = dl.summarize(pops, burs, {"NEAR/USD"})
check("summarize grades only covered pairs (XMR lands in uncovered)",
      agg["popups"]["graded"] == 2
      and agg["uncovered"]["popup:XMR/USD"] == 1)
check("per-source folds split by the popup's source",
      agg["popups"]["per_source"]["scout_auto"]["tp"] == 1
      and agg["popups"]["per_source"]["bot_strategy"]["timeout"] == 1)
check("per-gate folds skip ungraded burials (no-data never counts)",
      agg["burials"]["graded"] == 1
      and agg["burials"]["per_gate"]["vol_floor"]["sl"] == 1
      and "trend_gate" not in agg["burials"]["per_gate"])
agg_empty = dl.summarize([], [], set())
check("summarize on empty stores is all zeros, no crash",
      agg_empty["popups"]["graded"] == 0 and not agg_empty["uncovered"])
check("summarize uses plain dicts (json-writable report aggregates)",
      type(agg["popups"]).__name__ == "dict")

# ── 7. THE BOT_STRATEGY STAMP (user rule, Sep 9: the bot's own rows
# ── must promise what its exit engine enforces, so they grade too) ────

check("the config rails exist (stop 5, take 10 -- the exit engine's numbers)",
      dl.BOT_STOP_PCT == 5.0 and dl.BOT_TAKE_PCT == 10.0)
_sl, _tp = dl.bot_levels(1.39912)
check("bot_levels puts the rails exactly -5%% / +10%% off entry",
      abs(_sl - 1.39912 * 0.95) < 1e-9 and abs(_tp - 1.39912 * 1.10) < 1e-9)
check("bot_levels handles the None-safety contract (float coercion)",
      dl.bot_levels("0.5") == (round(0.5 * 0.95, 8), round(0.5 * 1.10, 8)))

prow = {"pair": "XMR/USD", "source": "bot_strategy", "ts": "t",
        "entry": 2.0, "tp": None, "sl": None, "t0": 0.0}
candle_rows = [[0, 2.0, 2.05, 1.99, 2.01, 10.0],
               [300_000, 2.01, 2.31, 2.0, 2.30, 10.0]]
check("an unstamped bot_strategy row grades via the config rails (tp hit)",
      dl.grade_popup(prow, candle_rows)["verdict"] == "tp")

prow_sl = {"pair": "XMR/USD", "source": "bot_strategy", "ts": "t",
           "entry": 2.0, "tp": None, "sl": None, "t0": 0.0}
crash_rows = [[0, 2.0, 2.01, 1.88, 1.89, 10.0]]
check("...and the same way on the stop side (-5%% rail reached first)",
      dl.grade_popup(prow_sl, crash_rows)["verdict"] == "sl")

prow_keep = {"pair": "XMR/USD", "source": "bot_strategy", "ts": "t",
             "entry": 2.0, "tp": 2.2, "sl": 1.9, "t0": 0.0}
check("a row WITH its own recorded tp keeps it (no rail overwrite)",
      dl.grade_popup(prow_keep, candle_rows)["verdict"] == "tp"
      and candle_rows[1][2] >= 2.2)

prow_null = {"pair": "XMR/USD", "source": "scout_auto", "ts": "t",
             "entry": 2.0, "tp": None, "sl": None, "t0": 0.0}
check("a scout row without levels still grades no-data (rails are bot-only)",
      dl.grade_popup(prow_null, candle_rows)["verdict"] == "no-data")

prow_exec = {"pair": "XMR/USD", "source": "bot_strategy", "ts": "t",
             "entry": 2.0, "exec": 2.2, "tp": None, "sl": None, "t0": 0.0}
middling_rows = [[0, 2.2, 2.30, 2.1, 2.25, 10.0]]
check("the rails ride the FILLED price when the row carries one (Copilot, Sep 9)",
      dl.grade_popup(prow_exec, middling_rows)["verdict"] == "timeout"
      and dl.grade_popup({**prow_exec, "exec": None},
                         middling_rows)["verdict"] == "tp")
check("a malformed NEGATIVE fill falls back to the entry (round 2 guard)",
      dl.grade_popup({**prow_exec, "exec": -2.2},
                     middling_rows)["verdict"] == "tp")

check("the backfill script exists (the 77 historical rows route)",
      Path("backfill_botstrategy_levels.py").exists())

# ── 8. THE CONVENTIONS ────────────────────────────────────────────────

check("the popup window is the 120m clock",
      dl.WINDOW_MIN == 120.0)
check("burials grade at the 1.5% default (they carry no goal call)",
      dl.BURIAL_GOAL == 1.5)
check("the report artifact exists (the diag was run)",
      scratch_space.report_temp("_replaylive_check.txt").exists())

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
scratch_space.report_temp('_replaylive_test.txt').write_text("\n".join(lines), encoding="utf-8")
print("DONE")
sys.exit(1 if FAIL else 0)
