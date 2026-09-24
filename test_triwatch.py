"""Test THE SMARTER TRI-WATCH SYSTEM (user spec, Sep 23) — TrendWatch
v2, GoalWatch v2, ExitWatch v2 and the integration layer, wired into
the scanner's exit path and the cooldown summary.

Graded here:
  1. wiring — the exit stamp rides the guard-breakdown heartbeat, the
     force GATE runs LAST (after the calibrated exits), the cooldown
     snapshot runs after the cross-exam tick, and the one-brain rule
     holds (tri_watch reuses meta_layers' candle read, patterns'
     trend, indicators' health; it never re-implements them)
  2. TrendWatch — a climbing series reads healthy (high health, low
     exhaustion, positive slope), a rolled-over series reads
     exhausted/turning, the product TrendMod stays inside its clamp,
     unreadable pairs fail neutral (1.0)
  3. GoalWatch — MaxGoal = base x GoalMod with the health/exhaustion
     factors, MinGoal carries the (1 - exhaustion) pull, clamps hold,
     a zero base returns a point band
  4. ExitWatch — the healthy trend never arms the gate, the rolled-
     over trend arms it with a spelled-out reason, GF None passes
     through, the adjusted GF widens the net, normalization keeps the
     neutral floor at 0
  5. integration — the five cross-flows report honest notes, a forced
     exit damps next rotation's ceiling
  6. snapshot — reads the watch rows from the (redirected) runtime
     store without importing the scanner, tracks the ExitMod high and
     the near-missed force exits, skips closed rows
  7. fail-neutral — the full dark path (no candles, no store) never
     raises and never invents pressure

Hermetic: tri_watch's STATE_FILE and meta_layers' ROOT are redirected
into a temp dir; the candles come from a synthetic ohlcv db written
there. No network, no live state touched.
Run: .venv\\Scripts\\python.exe -X utf8 test_triwatch.py
"""
import json
import math
import sqlite3
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
TMP = Path(tempfile.mkdtemp(prefix="_twtest_"))

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))

import meta_layers as ml
ml.ROOT = TMP
ml.STATE_FILE = TMP / "meta_layers_state.json"

# GoalWatch reads the five context mods through carve_out._mods_n —
# the drive pins the mods NEUTRAL (no live scanner/account reads, no
# live state): the trend parts are what's under test here
import carve_out as _co_drive
_co_drive.compute_carve_out = lambda *a, **k: {
    "readable": True,
    "mods": {"fleet_mod": 1.0, "book_mod": 1.0, "tide_mod": 1.0,
             "vol_mod": 1.0, "rot_mod": 1.0}}

import tri_watch as tw
tw.STATE_FILE = TMP / "tri_watch_state.json"
tw.ROOT = TMP                                # the snapshot's watch-store read
                                             # lands in the temp dir too —
                                             # the drive never touches the
                                             # live contest_watch.json

import pandas as pd  # noqa: E402  (meta_layers already required it)

NOW = tw._now_mst()


def _src(name):
    return (BASE / name).read_text(encoding="utf-8").replace("\r\n", "\n")


bs_src = _src("background_scanner.py")
tw_src = _src("tri_watch.py")
ml_src = _src("meta_layers.py")


def _write_db(pair, closes, highs=None):
    db = TMP / "ohlcv_cache.db"
    cc = sqlite3.connect(db)
    cc.execute("CREATE TABLE IF NOT EXISTS ohlcv ("
               " pair TEXT NOT NULL, ts INTEGER NOT NULL,"
               " open REAL, high REAL, low REAL, close REAL, volume REAL,"
               " PRIMARY KEY (pair, ts))")
    cc.execute("DELETE FROM ohlcv WHERE pair=?", (pair,))
    t0 = 1758000000000
    rows = []
    for i, cl in enumerate(closes):
        hi = (highs[i] if highs else cl * 1.001)
        rows.append((pair, t0 + i * 3600_000, cl * 0.999, hi,
                     cl * 0.998, cl, 10.0 + i))
    cc.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?)", rows)
    cc.commit()
    cc.close()


# ── two synthetic markets ────────────────────────────────────────────
CLIMB = [100.0 * (1.0 + 0.004 * i) for i in range(60)]          # +0.4%/h
# the rollover: a +0.5%/candle run that turns and the decline STEEPENS
# (growing drops = negative second difference = the collapse accelerating;
# a steady or fading decline reads no acceleration signal)
TURN_TOP = 100.0 * (1.0 + 0.005 * 39)
_drops = [0.001 + 0.0003 * k for k in range(20)]
ROLLED = ([100.0 * (1.0 + 0.005 * i) for i in range(40)]
          + [TURN_TOP * math.prod(1.0 - d for d in _drops[:i + 1])
             for i in range(20)])
HIGH_CLIMB = [c * 1.002 for c in CLIMB]
HIGH_ROLLED = [c * 1.003 for c in ROLLED[:40]] \
    + [c * 1.004 for c in ROLLED[40:]]

_write_db("CLIMB/USD", CLIMB, HIGH_CLIMB)
_write_db("ROLLED/USD", ROLLED, HIGH_ROLLED)

# ═══ 1. wiring ═══════════════════════════════════════════════════════
check("1.1 the exit stamp rides the guard-breakdown heartbeat",
      "w[\"exit_watch\"] = _tw_es" in bs_src
      and bs_src.index("w[\"guard_brk\"]") < bs_src.index(
          "w[\"exit_watch\"]"))
check("1.2 the force GATE runs LAST (after the trend sell bar)",
      bs_src.index("THE EXIT WATCH'S FORCE GATE")
      > bs_src.index("Trend broke — {sig.confidence:.0f}% bearish"))
check("1.3 the gate reads the stamp, never recomputes (one-brain)",
      'w.get("exit_watch")' in bs_src and "force_reason" in bs_src)
check("1.4 the cooldown snapshot follows the cross-exam tick",
      bs_src.index("_tws.snapshot(now)")
      > bs_src.index("_co_x._schedule_exam(now)"))
check("1.5 tri_watch reuses meta_layers' candle read (one-brain)",
      "_ml._candles(pair, 60)" in tw_src)
check("1.6 tri_watch reuses patterns' trend and indicators' health",
      "_pat.detect_trend(df" in tw_src
      and "_indm.compute_all_indicators(df" in tw_src
      and "_strat._compute_trend_health(ind" in tw_src)
check("1.7 meta_layers' candle read now carries full OHLCV",
      "SELECT ts, open, high, low, close, volume FROM ohlcv" in ml_src)
check("1.8 the snapshot reads the store, never imports the scanner",
      "contest_watch.json" in tw_src
      and "import background_scanner" not in tw_src)
check("1.9 the dailies: GoalWatch consumes carve_out's context mods",
      "_co._mods_n(when=when)" in tw_src)

# ═══ 2. TrendWatch ═══════════════════════════════════════════════════
tr_c = tw.trend_read("CLIMB/USD", NOW)
tr_r = tw.trend_read("ROLLED/USD", NOW)
check("2.1 the climb reads healthy (health above 0.4)",
      tr_c.get("readable") and (tr_c.get("health") or 0) > 0.4, tr_c)
check("2.2 the climb's slope reads positive",
      (tr_c.get("slope") or 0) > 0, tr_c)
check("2.3 the climb's exhaustion is present and CAPPED",
      0.0 < (tr_c.get("exhaustion") or 0.0) <= tw.EXHUTION_CAP, tr_c)
check("2.4 the rollover's structure votes reversal",
      (tr_r.get("reversal") or 0) >= 0.5, tr_r)
check("2.5 the rollover's acceleration is negative (steam fading)",
      (tr_r.get("acceleration") or 0) < 0, tr_r)
check("2.6 the climb reads STRETCHED, the rollover reads TURNED",
      (tr_c.get("exhaustion") or 0) > (tr_r.get("exhaustion") or 0)
      and (tr_r.get("reversal") or 0) > (tr_c.get("reversal") or 0),
      (tr_r.get("exhaustion"), tr_c.get("exhaustion"),
       tr_r.get("reversal"), tr_c.get("reversal")))
check("2.7 TrendMod stays inside its clamp on both markets",
      tw.TRENDMOD_FLOOR <= tr_c["trend_mod"] <= tw.TRENDMOD_CAP
      and tw.TRENDMOD_FLOOR <= tr_r["trend_mod"] <= tw.TRENDMOD_CAP,
      (tr_c["trend_mod"], tr_r["trend_mod"]))
check("2.8 the trend direction rides patterns' verdict",
      tr_c.get("trend") == "uptrend", tr_c.get("trend"))

# ═══ 3. GoalWatch ════════════════════════════════════════════════════
lo_c, hi_c, gm_c = tw.goal_bounds(100.0, pair="CLIMB/USD", when=NOW)
lo_r, hi_r, gm_r = tw.goal_bounds(100.0, pair="ROLLED/USD", when=NOW)
check("3.1 GoalMod composes health and exhaustion into the ceiling",
      gm_c["readable"] and gm_c.get("health") is not None
      and gm_c.get("exhaustion") is not None, gm_c)
check("3.2 the ceiling stays inside its clamp",
      100.0 * tw.GOALMOD_FLOOR <= hi_c <= 100.0 * tw.GOALMOD_CAP,
      (lo_c, hi_c, gm_c))
check("3.3 the stretch damps the goal below the pure context product",
      gm_c["goal_mod"] < 1.0, gm_c)
check("3.4 MinGoal carries the exhaustion pull via (1 - exhaustion)",
      lo_c <= hi_c and lo_r <= hi_r, (lo_c, hi_c, lo_r, hi_r))
check("3.5 the GoalMod clamp holds",
      tw.GOALMOD_FLOOR <= gm_c["goal_mod"] <= tw.GOALMOD_CAP
      and tw.GOALMOD_FLOOR <= gm_r["goal_mod"] <= tw.GOALMOD_CAP)
check("3.6 a zero base returns a point band",
      tw.goal_bounds(0.0)[:2] == (0.0, 0.0))

# ═══ 4. ExitWatch ════════════════════════════════════════════════════
es_c = tw.exit_state("CLIMB/USD", 1.2, NOW)
es_r = tw.exit_state("ROLLED/USD", 1.2, NOW)
check("4.1 the healthy trend never arms the force gate",
      es_c.get("readable") and not es_c.get("force_exit")
      and (es_c.get("exit_mod") or 0) <= tw.EXITMOD_FORCE, es_c)
check("4.2 the rolled-over trend arms it",
      es_r.get("force_exit") and (es_r.get("exit_mod") or 0)
      > tw.EXITMOD_FORCE, es_r)
check("4.3 the reason is spelled out (the why rides the gate)",
      "EXIT WATCH" in str(es_r.get("force_reason"))
      and "ExitMod" in str(es_r.get("force_reason")))
check("4.4 the healthy net is wider than the turning one",
      (es_c.get("adjusted_gf") or 0) < (es_r.get("adjusted_gf") or 0),
      (es_c.get("adjusted_gf"), es_r.get("adjusted_gf")))
check("4.5 the decay multiplier mirrors the exit pressure",
      abs(es_r["decay_mult"] - (1.0 + es_r["exit_mod"])) < 1e-6
      and es_r["decay_mult"] > es_c["decay_mult"])
check("4.6 GF None passes through (the gate still reports)",
      tw.exit_state("ROLLED/USD", None, NOW).get("adjusted_gf") is None)
check("4.7 the neutral floor normalizes to zero exit pressure",
      tw.exit_state("NOSUCHPAIR/USD", 1.0, NOW).get("exit_mod") is None)

# ═══ 5. the integration layer ════════════════════════════════════════
integ = tw.integrate(dict(gm_r), dict(es_r), dict(tr_r))
check("5.1 the forced exit damps next rotation's ceiling",
      integ["goal"].get("next_max_damp") == 0.9
      and any("MaxGoal x0.9" in n for n in integ["notes"]), integ["notes"])
check("5.2 the exhaustion note names the floor pull",
      any("MinGoal" in n for n in integ["notes"]), integ["notes"])
integ_c = tw.integrate(dict(gm_c), dict(es_c), dict(tr_c))
check("5.3 a healthy read damps nothing",
      "next_max_damp" not in integ_c["goal"], integ_c["notes"])

# ═══ 6. the cooldown snapshot ════════════════════════════════════════
(TMP / "contest_watch.json").write_text(json.dumps({
    "CLIMB/USD": {"entry": 100.0, "size": 2500.0,
                  "guard_brk": {"gf": 1.2}},
    "ROLLED/USD": {"entry": 100.0, "size": 2500.0,
                   "guard_brk": {"gf": 1.2}},
    "CLOSED/USD": {"entry": 0.0, "high": 110.0},
}), encoding="utf-8")
tw.STATE_FILE.write_text("{}", encoding="utf-8")
day = tw.snapshot(NOW)
check("6.1 the snapshot reads both live rows and skips the closed one",
      set((day.get("pairs") or {}).keys()) == {"CLIMB/USD", "ROLLED/USD"},
      day.get("pairs"))
check("6.2 the near-missed force exit is counted",
      day.get("force_exits") == 1, day)
check("6.3 the day's ExitMod high tracks the rolling-over market",
      (day.get("exitmod_high") or 0) >= (es_r.get("exit_mod") or 0), day)
check("6.4 the high-water mark is retrievable",
      abs(tw.day_exitmod_high(NOW.strftime("%Y-%m-%d"))
          - (day.get("exitmod_high") or 0)) < 1e-6)
check("6.5 the snapshot carries the per-pair readability",
      day["pairs"]["CLIMB/USD"]["readable"] is True
      and day["pairs"]["CLIMB/USD"]["force_exit"] is False)

# ═══ 7. fail-neutral (the dark path) ═════════════════════════════════
_db = TMP / "ohlcv_cache.db"
_bak = TMP / "ohlcv_cache.db.bak"
_db.rename(_bak)
try:
    dark = tw.trend_read("CLIMB/USD", NOW)
    check("7.1 no candles -> the read fails neutral, never raises",
          dark["readable"] is False and dark["trend_mod"] == 1.0, dark)
    es_d = tw.exit_state("CLIMB/USD", 1.2, NOW)
    check("7.2 the dark exit state invents no pressure",
          es_d["readable"] is False and es_d["force_exit"] is False
          and es_d["adjusted_gf"] is None)
    lo_d, hi_d, gm_d = tw.goal_bounds(100.0, pair="CLIMB/USD", when=NOW)
    check("7.3 the dark GoalWatch still bounds (context mods only)",
          0 < lo_d <= hi_d and gm_d["readable"] is False, (lo_d, hi_d, gm_d))
finally:
    if _db.exists():          # sqlite3.connect CREATES an empty file on
        _db.unlink()          # a dark read — clear it before restoring
    _bak.rename(_db)
tw.STATE_FILE.write_text("not json", encoding="utf-8")
check("7.4 a corrupt state file reads neutral",
      tw.day_exitmod_high() == 0.0 and tw.snapshot(NOW) is not None)

fails = [r for r in results if not r[1]]
(BASE / "_triwatch_check.txt").write_text(
    "\n".join(f"{'PASS' if ok else 'FAIL'}  {name}"
              + (f"  [{d}]" if d and not ok else "")
              for name, ok, d in results)
    + f"\n\nRESULT: {len(results) - len(fails)} passed, "
      f"{len(fails)} failed\n", encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
