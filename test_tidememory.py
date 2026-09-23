"""Test THE PER-COIN TIDE MEMORY (Sep 10) — the false-breakout pattern
applied to the tide: per-coin payer-rate edge by tide state over the
deep store, a capped ±3 pt adjustment, and the evidence gate (the
split-half persistence that justifies OUT/FLAT but not RISING).

Graded here:
  1. the math (edges over MEASURED coin-hours — the NaN bias the suite
     forces into the open — the MIN_STATE_HOURS gate, the ±3 clamp)
  2. tide_edge's fail-closed + rate-limited cache contract
  3. the scanner's floor stack: memory eases/tightens OUTSIDE OUT —
     in OUT the memory rides the LEARNED BAR via tide_personal_bar
     (Sep 10 rebalance: ONE tax, a coin is never taxed twice for the
     same tide); the wolf floor of 55 is inviolable, back-compat
     without pair
  4. the wiring: alert popup path + the bot's Gate 2 + the QC mirrors
  5. the real store: XPL eases in OUT, BTC tightens, unknown stays 0
  6. the live layer: the funnel's own popup outcomes blend into the
     edge (decay, ramp, clamp) — fired payers strengthen it, buried
     payers restore it, the store keeps the baseline

Run: .venv\\Scripts\\python.exe -X utf8 test_tidememory.py
"""
import sys
import time
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))

def _src(name):
    return (BASE / name).read_text(encoding="utf-8").replace("\r\n", "\n")

bs_src = _src("background_scanner.py")
ap_src = _src("app.py")
tm_src = _src("tide_memory.py")

import tide_memory as tm

# ── 1. the math ──────────────────────────────────────────────────────
check("the dials exist (30h gate, 10x scale, ±3 cap, OUT+FLAT only)",
      tm.MIN_STATE_HOURS == 30 and tm.EDGE_SCALE == 10.0
      and tm.EDGE_MAX_PTS == 3.0
      and tm.ADJUSTED_STATES == ("OUT", "FLAT"))

idx = pd.date_range("2026-08-01", periods=40, freq="1h", tz="UTC")
ffwd = pd.DataFrame({"AAA/USD": [0.02] * 40,          # pays every hour
                     "BBB/USD": [0.0] * 40,           # pays never
                     "CCC/USD": [None] * 40},         # no data at all
                    index=idx)
states = pd.Series(["OUT"] * 40, index=idx)
m = tm._measure(states, ffwd)
out = m["OUT"]
check("the fleet rate weights MEASURED coin-hours (NaN never counts as a non-payer)",
      abs(out["fleet"] - 0.5) < 1e-9, out["fleet"])
aaa = out["coins"]["AAA/USD"]
bbb = out["coins"]["BBB/USD"]
check("a coin that pays every hour earns the +3 cap (edge +0.5 -> clamp)",
      aaa["adj"] == 3.0 and aaa["edge"] == 0.5, aaa)
check("a coin that never pays earns the -3 cap",
      bbb["adj"] == -3.0, bbb)
check("a coin with no data earns nothing (absent, not zero-biased)",
      "CCC/USD" not in out["coins"], list(out["coins"]))
check("the measurement records the state's hours",
      out["hours"] == 40, out["hours"])

# thin history: a coin with only 10 measured hours earns nothing
ffwd2 = pd.DataFrame({"AAA/USD": [0.02] * 10 + [None] * 30}, index=idx)
m2 = tm._measure(states, ffwd2)
check("thin history (<30h) earns no adjustment",
      "AAA/USD" not in m2["OUT"]["coins"], list(m2["OUT"]["coins"]))
check("a too-thin STATE is named and left unpriced",
      tm._measure(pd.Series(["OUT"] * 5, index=idx[:5]),
                  pd.DataFrame({"A/USD": [0.0] * 5}, index=idx[:5])
                  )["OUT"]["why"] is not None)

# a modest edge scales linearly under the cap (AAA pays 50%, BBB pays
# 0% — the fleet rate 25% makes AAA's edge +0.25)
ffwd3 = pd.DataFrame({"AAA/USD": [0.02] * 20 + [0.0] * 20,
                      "BBB/USD": [0.0] * 40}, index=idx)
m3 = tm._measure(states, ffwd3)
check("a +0.25 edge -> +2.5 pts (linear under the cap)",
      m3["OUT"]["coins"]["AAA/USD"]["adj"] == 2.5,
      m3["OUT"]["coins"]["AAA/USD"])

# ── 2. the cache contract (fail-closed, rate-limited) ────────────────
_real_compute = tm.compute
_calls = {"n": 0}

def _fake_compute_ok(now=None):
    _calls["n"] += 1
    idx = pd.date_range("2026-08-01", periods=40, freq="1h", tz="UTC")
    data = {"err": None, "generated_ts": "t", "hours": 40,
            "window": ["a", "b"],
            "states": {"OUT": {"fleet": 0.11, "hours": 40, "why": None,
                               "coins": {"AAA/USD": {"hours": 40,
                                                     "rate": 0.30,
                                                     "edge": 0.19,
                                                     "adj": 1.9}}},
                       "FLAT": {}, "RISING": {}}}
    tm._cache.update(data=data, ts=time.time(), err=None)
    return data

def _fake_compute_fail(now=None):
    _calls["n"] += 1
    tm._cache.update(data=None, ts=time.time(), err="store broke")
    return {"err": "store broke", "states": {}, "generated_ts": None,
            "hours": 0, "window": None}

try:
    tm.compute = _fake_compute_ok
    tm._cache.update(data=None, ts=0.0, err=None)
    adj, why = tm.tide_edge("AAA/USD", "OUT")
    check("cold cache: the coin's earned adjustment is served",
          adj == 1.9 and "pays" in why, (adj, why))
    check("cold cache cost exactly ONE build", _calls["n"] == 1)
    _calls["n"] = 0
    tm.tide_edge("AAA/USD", "OUT")
    check("warm cache: no rebuild inside the TTL", _calls["n"] == 0)

    tm.compute = _fake_compute_fail
    tm._cache.update(data=None, ts=time.time(), err="store broke")
    _calls["n"] = 0
    adj2, why2 = tm.tide_edge("AAA/USD", "OUT")
    check("fresh failure: 0 pts from the cache, no rebuild",
          adj2 == 0.0 and "store broke" in why2 and _calls["n"] == 0,
          (adj2, why2, _calls["n"]))
    tm._cache.update(data=None, ts=time.time() - tm.CACHE_TTL_S * 2,
                     err="store broke")
    tm.tide_edge("AAA/USD", "OUT")
    check("stale failure: exactly one retry", _calls["n"] == 1)

    tm.compute = _fake_compute_ok
    tm._cache.update(data=None, ts=0.0, err=None)
    adj3, why3 = tm.tide_edge("AAA/USD", "RISING")
    check("RISING rides the fleet — no adjustment by design",
          adj3 == 0.0 and "rides the fleet" in why3, (adj3, why3))
    adj4, why4 = tm.tide_edge("ZZZ/USD", "OUT")
    check("an unknown coin fails closed with its reason",
          adj4 == 0.0 and "no measured memory" in why4, (adj4, why4))
    adj5, why5 = tm.tide_edge("AAA/USD", None)
    check("a broken tide read fails closed",
          adj5 == 0.0, (adj5, why5))
finally:
    tm.compute = _real_compute
    tm._cache.update(data=None, ts=0.0, err=None)

# ── 3. the scanner's floor stack ─────────────────────────────────────
check("popup_floor takes the pair (the memory needs the coin)",
      "def popup_floor(tide_state: str | None = None, lw_pts: float = 0,\n"
      "                rot_key: str | None = None,\n"
      "                pair: str | None = None) -> float:" in bs_src)
check("the floor applies the memory in every adjusted state BUT OUT "
      "(the rebalance: OUT's evidence rides tide_personal_bar — one tax)",
      "if pair and tide_state != \"OUT\" and tide_state in tide_memory_states():" in bs_src)
check("the memory never drops the floor below the wolf's 55",
      "fl = max(LONE_WOLF_DESKTOP_FLOOR, fl - _tadj)" in bs_src)
check("the memory block is fail-closed",
      "memory opens nothing" in bs_src)
check("the alert popup path passes the pair",
      "popup_floor(_df_state, _lw_pts, _rot_now_key, pair)" in bs_src)
check("the OUT gate prices the coin ONCE through the personal bar",
      "def tide_personal_bar(pair" in bs_src
      and 'tide_personal_bar(pair, "OUT", _lbar)' in bs_src)
check("the QC's OUT gate mirrors the personal bar (never disagrees)",
      '_bs4.tide_personal_bar(pair, "OUT", _tl_bar)' in ap_src)

# functional: the real stack with a fake memory (delta checks — immune
# to whatever relief the live journal carries right now)
import background_scanner as bs

real_tm = sys.modules.get("tide_memory")
try:
    class _FakeTM:
        ADJUSTED_STATES = ("OUT", "FLAT")

        @staticmethod
        def tide_edge(pair, state):
            return (2.5, "fake memory") if pair == "XPL/USD" else (0.0, "fake memory")

    sys.modules["tide_memory"] = _FakeTM
    _base = bs.popup_floor("FLAT", 0, "08-12")
    check("functional: a payer eases the FLAT floor by exactly the memory",
          abs(bs.popup_floor("FLAT", 0, "08-12", "XPL/USD") - (_base - 2.5)) < 1e-9,
          (_base, bs.popup_floor("FLAT", 0, "08-12", "XPL/USD")))
    check("functional: a neutral coin's FLAT floor is the base",
          abs(bs.popup_floor("FLAT", 0, "08-12", "BTC/USD") - _base) < 1e-9)
    check("functional: OUT's floor ignores the memory entirely (one tax)",
          abs(bs.popup_floor("OUT", 0, "08-12", "XPL/USD") - _base) < 1e-9,
          bs.popup_floor("OUT", 0, "08-12", "XPL/USD"))
    check("functional: no-pair call stays back-compatible",
          abs(bs.popup_floor("FLAT", 0, "08-12") - _base) < 1e-9)
    check("functional: the wolf floor holds at 55 even with the memory easing",
          bs.popup_floor("FLAT", 35, "08-12", "XPL/USD") == 55.0)
except Exception as exc:
    check("functional floor checks ran", False, exc)
finally:
    if real_tm is not None:
        sys.modules["tide_memory"] = real_tm

# ── 4. the wiring: Gate 2 + the QC mirror ────────────────────────────
check("the bot's Gate 2 reads the coin's memory (logged, capped, floored)",
      "TIDE MEMORY: %s %+.1f pts in %s" in bs_src
      and "_tmem2.tide_edge(pair, _g0_state)" in bs_src
      and "max(LONE_WOLF_BAR_FLOOR, effective_thr - _tadj2)" in bs_src)
check("the QC's champ floor mirrors the alert (passes the pair)",
      "_bs5.popup_floor(_tide_state, _lw_pts, None, pair)" in ap_src)
check("the QC's Hail Mary floor mirrors it too",
      "_bs_hm.popup_floor(_hm_tstate, _hm_lw_pts, None, pair)" in ap_src)
check("the memory module reuses the learner's loader (one store, one cut)",
      "_tl._load_deep_fleet()" in tm_src)

# ── 5. the real store: the memory says what the evidence said ────────
_store = BASE / "surething_data" / "kraken"
if not list(_store.glob("*-1h.feather")):
    check("real memory skipped (no 1h store in this environment)", True,
          "store absent")
else:
    data = tm.compute()
    if data.get("err"):
        check("real memory: fail-closed shape on a broken store",
              tm.tide_edge("XPL/USD", "OUT")[0] == 0.0, data.get("err"))
    else:
        outs = (data.get("states") or {}).get("OUT") or {}
        coins = outs.get("coins") or {}
        check("real memory: the popup fleet is measured (54 coins)",
              len(coins) >= 40, len(coins))
        xpl = coins.get("XPL/USD")
        btc = coins.get("BTC/USD")
        check("real memory: XPL (the Sep 6 burial) eases in OUT",
              xpl is not None and xpl["adj"] > 0, xpl)
        check("real memory: BTC (pays 1.1% in OUT hours) tightens",
              btc is not None and btc["adj"] < 0, btc)
        check("real memory: every adjustment stays inside ±3",
              all(abs(c["adj"]) <= tm.EDGE_MAX_PTS + 1e-9
                  for c in coins.values()))
        check("real memory: every coin has the full state history",
              all(c["hours"] >= tm.MIN_STATE_HOURS
                  for c in coins.values()))

# ── 6. the live layer: popup outcomes blend into the edge ────────────
from datetime import datetime, timedelta, timezone as _tz


def _live_rec(pair, st, mfe, hours_ago, goal=1.5):
    # SINGLE-CLOCK fixtures (the clock-freeze sweep, Sep 23): the stamp
    # derives from the SAME epoch clock that supplies now_ts — the old
    # datetime.now() stamp disagreed with time.time() under any clock
    # perturbation, silently aging the "fresh" payers past the decay
    # (the metalayers shadow-bomb precondition, caught at +40d)
    return {"pair": pair, "tide": f"{st} 22%", "goal_pct": goal,
            "ts": datetime.fromtimestamp(time.time() - hours_ago * 3600,
                                         _tz.utc).isoformat(),
            "outcome": {"mfe_pct": mfe}}


check("the tide parser reads the state word (and fails closed)",
      tm._parse_tide("OUT 22%") == "OUT"
      and tm._parse_tide("rising 85%") == "RISING"
      and tm._parse_tide("") is None and tm._parse_tide(None) is None
      and tm._parse_tide("WEIRD 50%") is None)

recs = [
    _live_rec("AAA/USD", "OUT", 2.5, 1),        # fresh payer
    _live_rec("AAA/USD", "OUT", 0.1, 72),       # 3-day-old dud (decays)
    _live_rec("BBB/USD", "OUT", 0.2, 1),        # fresh dud
    _live_rec("CCC/USD", "OUT", 5.0, 1),        # fresh payer
]
_now_ts = time.time()
_m = tm._live_measure(recs, now_ts=_now_ts)["OUT"]
check("live: a payer is mfe >= the popup's own goal (the store's definition)",
      _m["coins"]["AAA/USD"]["rate"] > 0.4
      and _m["coins"]["BBB/USD"]["rate"] == 0.0, _m["coins"])
check("live: decay fades old events (the 3-day dud weighs under a fresh one)",
      _m["coins"]["AAA/USD"]["w"] < 2.0, _m["coins"]["AAA/USD"])
check("live: ungraded events (no tide / no outcome) carry no evidence",
      tm._live_measure([{"pair": "D/USD", "tide": "OUT 1%", "outcome": None},
                        {"pair": "E/USD", "tide": None,
                         "outcome": {"mfe_pct": 3.0}}],
                       now_ts=_now_ts)["OUT"]["events"] == 0)


def _fake_model(store_adj):
    states = {"OUT": {"fleet": 0.11, "hours": 474,
                      "coins": {"AAA/USD": {"hours": 100, "rate": 0.238,
                                            "edge": 0.128, "adj": store_adj},
                                "BBB/USD": {"hours": 100, "rate": 0.05,
                                            "edge": -0.06, "adj": -0.6}}},
              "FLAT": {"fleet": 0.10, "hours": 500, "coins": {}},
              "RISING": {"fleet": 0.25, "hours": 200, "coins": {}}}
    return {"states": states, "err": None,
            "live": tm._live_measure(recs, now_ts=_now_ts)}


_saved_cache = dict(tm._cache)
try:
    tm._cache.update(data=_fake_model(1.29), ts=time.time(), err=None)
    _a, _w = tm.tide_edge("AAA/USD", "OUT")
    check("live: a live payer blend STRENGTHENS the store edge",
          1.29 < _a <= 3.0 and "live popups" in _w, (_a, _w))
    _b, _w2 = tm.tide_edge("BBB/USD", "OUT")
    check("live: a live dud tightens the store edge",
          _b < -0.6 and _b >= -3.0, (_b, _w2))

    # BURIED PAYERS RESTORE: the store missed this coin (adj 0) but its
    # recent popups all paid while OTHER coins' didn't (the edge is
    # relative to the fleet — a coin that is the whole sample earns
    # nothing) -> the live layer lifts it toward the cap.
    fresh_payers = ([_live_rec("AAA/USD", "OUT", 3.0, h) for h in (1, 2, 3)]
                    + [_live_rec("ZZZ/USD", "OUT", 0.1, h) for h in (1, 2)])
    tm._cache.update(data={"states": _fake_model(0.0)["states"], "err": None,
                           "live": tm._live_measure(fresh_payers, now_ts=_now_ts)},
                     ts=time.time(), err=None)
    _c, _w3 = tm.tide_edge("AAA/USD", "OUT")
    check("live: buried payers RESTORE a flat store edge (and the band holds)",
          _c > 1.0 and _c <= 3.0, (_c, _w3))

    # COLD LIVE CUT: no live evidence -> the store edge stands alone.
    tm._cache.update(data={"states": _fake_model(1.29)["states"], "err": None,
                           "live": {"OUT": {"fleet": None, "events": 0,
                                            "coins": {}}}},
                     ts=time.time(), err=None)
    _d, _w4 = tm.tide_edge("AAA/USD", "OUT")
    check("live: a cold live cut changes nothing (the store edge stands)",
          abs(_d - 1.29) < 1e-9 and "live popups" not in _w4, (_d, _w4))
finally:
    tm._cache.update(data=_saved_cache.get("data"),
                     ts=_saved_cache.get("ts", 0.0),
                     err=_saved_cache.get("err"))

check("live: refresh_live re-scores the real outcomes file and attaches the cut",
      "live" in (tm.snapshot().get("data") or {}))
check("live: the three fired-snapshot sites record the tide (the grader needs it)",
      bs_src.count('"tide": (f"{_df_state} {_df_pct:.0f}%"') >= 1
      and bs_src.count('"tide": (f"{_st_tide_state} {_st_tide_pct:.0f}%"') >= 1
      and bs_src.count('"tide": (f"{_tsw} {_tpw:.0f}%"') >= 1)
check("live: the driver thread runs in main() (daemon, stops with the scanner)",
      "live_driver_loop" in bs_src and "daemon=True" in bs_src
      and "import threading" in bs_src)

# ── 7. the hindsight grader: the binder classifies honestly ──────────
try:
    from diag_tidememory_hindsight import _binder
    check("hindsight: a priced detail -> the bar from the text (not the floor field)",
          _binder({"gate": "tide-out", "floor": 93.0,
                   "detail": "quick 50.0 vs personal bar 93 (learned OUT bar 92)"})
          == ("priced", 93.0))
    check("hindsight: the blanket text is the BOT wall (never the memory's bar)",
          _binder({"gate": "tide-out",
                   "detail": "all ships sink together; no solo move"})[0] == "wall")
    check("hindsight: non-tide gates are not the memory's to price",
          _binder({"gate": "deep-veto", "detail": "the brain says no"})[0]
          .startswith("other:"))
except Exception as exc:
    check("hindsight: the binder classifies honestly", False, exc)

# ── RUN ──────────────────────────────────────────────────────────────
fails = [r for r in results if not r[1]]
(BASE / "_tidememory_check.txt").write_text(
    "\n".join(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{d}]" if d and not ok else "")
              for name, ok, d in results)
    + f"\n\nRESULT: {len(results) - len(fails)} passed, {len(fails)} failed\n",
    encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
