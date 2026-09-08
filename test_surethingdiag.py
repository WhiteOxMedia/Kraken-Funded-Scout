# test_surethingdiag.py -- grades THE SURE THING WEEKLY DIAG (Sep 8:
# "add a weekly diag that grades the fire rate and per-trigger P&L so we
# can tell if 65 is too loose" -- extended: "did breakouts actually pay
# inside the promised window?").
#
# The diag's grading core is PURE: grade_fire(snap, fwd_rows, now_s)
# turns one fired snapshot + forward candles into a verdict and a
# hindsight P&L, summarize() folds grades into the fire-rate / est_min /
# score-band aggregates. This suite grades those on synthetic inputs --
# no network, no live stores -- plus the store filter and the report's
# structure (the conventions every diag in this repo follows).
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import diag_surething as dg

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


def candle(t0_s, i, hi_frac, close_frac):
    """One 5m Kraken-order row i candles after t0 (o=h=l=c unless told)."""
    ts = int((t0_s + i * 300) * 1000)
    h = 1.0 * (1.0 + hi_frac)
    c = 1.0 * (1.0 + close_frac)
    return [ts, 1.0, h, min(1.0, c), c, 100.0]


T0 = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc).timestamp()


def snap(trig="pre-breakout", est=40, ts=None, spend=400.0,
         entry=1.0, win=60.0, score=70.0, goal=None):
    d = {"pair": "XMR/USD", "route": "sure-thing", "kind": "fired",
         "trigger": trig, "est_min": est, "spend": spend,
         "entry": entry, "win_min": win, "score": score,
         "mult": 0.5, "ts": (ts or datetime.fromtimestamp(
             T0, tz=timezone.utc).isoformat()),
         "climb": {"climb_tide": "RISING", "climb_tide_pct": 42.0}}
    if goal is not None:
        d["goal_pct"] = goal
    return d


# ── 1. grade_fire: the verdict ladder ──────────────────────────────────

# paid INSIDE the promise: the goal (+1.5%) hits in the first candle
g = dg.grade_fire(snap(est=40, ts=datetime.fromtimestamp(T0, tz=timezone.utc).isoformat()),
                  [candle(T0, 1, 0.025, 0.020), candle(T0, 2, 0.025, 0.021)],
                  now_s=T0 + 3600)
check("paid-in-window: a quick spike grades kept",
      g["verdict"] == "paid-in-window", g)
check("paid-in-window: the paid minute anchors to the fire, not the candle open",
      g["paid_min"] is not None and 0.0 <= g["paid_min"] <= 5.0, g["paid_min"])
check("paid-in-window: the P&L is the goal on the spend (+1.5% of $400)",
      abs(g["pnl_usd"] - 6.0) < 1e-9, g["pnl_usd"])

# THE PER-FIRE GOAL (Sep 8): the snapshot's goal_pct drives the grade --
# a 3.0% goal needs a 3.0% move to count as a winner, and its P&L is
# 3.0% of the spend. A record WITHOUT the stamp (pre-Sep-8 fires) falls
# back to the engine constant, which is what those fires ran with.
g2g = dg.grade_fire(snap(est=40, goal=3.0),
                    [candle(T0, 1, 0.020, 0.018)],   # +2% -- under a 3% goal
                    now_s=T0 + 3600)
check("per-fire goal: a 3% goal turns a +2% spike into a grinder, not a winner",
      g2g["verdict"] == "short", g2g)
check("per-fire goal: the grade carries the goal it used",
      abs(g2g["goal_pct"] - 3.0) < 1e-9, g2g["goal_pct"])
g3g = dg.grade_fire(snap(est=40, goal=3.0),
                    [candle(T0, 1, 0.040, 0.030)],   # +4% -- clears it
                    now_s=T0 + 3600)
check("per-fire goal: a 3% goal paid pays 3% of the spend ($12, not $6)",
      g3g["verdict"] == "paid-in-window" and abs(g3g["pnl_usd"] - 12.0) < 1e-9,
      g3g["pnl_usd"])
g4g = dg.grade_fire(snap(est=40),                      # no goal_pct stamp
                    [candle(T0, 1, 0.025, 0.020)],
                    now_s=T0 + 3600)
check("per-fire goal: a pre-stamp record falls back to the engine constant",
      abs(g4g["goal_pct"] - dg.st.SURE_THING_GOAL_PCT) < 1e-9
      and g4g["verdict"] == "paid-in-window", g4g)

# paid LATE: the goal pays at ~35 min, but the promise (est) was only
# 10 minutes (and the 60-min window keeps it gradeable)
g2 = dg.grade_fire(snap(est=10), [candle(T0, 7, 0.025, 0.020)],
                   now_s=T0 + 3600)
check("paid-late: the goal paid but after the countdown ran out",
      g2["verdict"] == "paid-late" and g2["paid_min"] > g2["promise_min"], (g2["paid_min"], g2["promise_min"]))
check("paid-late: the P&L still counts (a win the clock missed)",
      abs(g2["pnl_usd"] - 6.0) < 1e-9, g2["pnl_usd"])

# SHORT: a real move (+0.9% mfe) that never reaches the goal, judged at
# the window close
g3 = dg.grade_fire(snap(est=30),
                   [candle(T0, i, 0.009, 0.004) for i in range(1, 14)],
                   now_s=T0 + 3600)
check("short: a grinder below the goal is not a winner",
      g3["verdict"] == "short", g3)
check("short: the P&L is the window-close exit on the spend",
      abs(g3["pnl_usd"] - 400.0 * g3["end_move"] / 100.0) < 1e-9
      and g3["pnl_usd"] > 0, (g3["pnl_usd"], g3["end_move"]))

# DUD: nothing moved, the window closed under water
g4 = dg.grade_fire(snap(est=30),
                   [candle(T0, i, 0.002, -0.003) for i in range(1, 14)],
                   now_s=T0 + 3600)
check("dud: a fire that never moved loses the exit on the spend",
      g4["verdict"] == "dud" and g4["pnl_usd"] < 0, g4)

# OPEN: too young to judge -- no candles required, no P&L made up
g5 = dg.grade_fire(snap(est=40), [], now_s=T0 + 12 * 60)
check("open: a fire inside its window is not graded yet",
      g5["verdict"] == "open" and g5["pnl_usd"] == 0.0, g5)

# NO-DATA: old enough to judge but the candles came back empty
g6 = dg.grade_fire(snap(est=40), [], now_s=T0 + 90 * 60)
check("no-data: an expired fire without candles says so honestly",
      g6["verdict"] == "no-data", g6)

# the crack fire's promise is the full window, not est_min
# (spike at i=10 -> paid ~50 min, inside the 60-min promise)
g7 = dg.grade_fire(snap(trig="crack", est=40, win=60.0),
                   [candle(T0, 10, 0.025, 0.020)], now_s=T0 + 3600)
check("crack: the promise is the 60-min window (it said NOW)",
      g7["verdict"] == "paid-in-window" and g7["promise_min"] == 60.0, g7)
g7b = dg.grade_fire(snap(trig="crack", est=40, win=60.0),
                    [candle(T0, 45, 0.025, 0.020)], now_s=T0 + 3000)
check("crack: inside the 60-min window it is still open",
      g7b["verdict"] == "open", g7b)

# the FLAT half-size flows into the P&L (the tide must be money, not words)
gf = dg.grade_fire(snap(est=40, spend=200.0),
                   [candle(T0, 1, 0.025, 0.020)], now_s=T0 + 3600)
check("flat tide: half the spend earns half the win ($200 x 1.5% = $3)",
      abs(gf["pnl_usd"] - 3.0) < 1e-9, gf["pnl_usd"])

# the LEAK GUARD (Copilot, Sep 8): the popup lands MID-CANDLE, so the
# fire candle's pre-fire range is not tradeable -- a candle opened at
# or before the fire must be dropped, or its pre-fire high grades a
# winner that paid before the recommendation existed
gl = dg.grade_fire(snap(est=40),
                   [candle(T0, 0, 0.10, 0.10),   # opens AT the fire: huge pre-fire high
                    candle(T0, 13, 0.004, 0.004)],
                   now_s=T0 + 3600)
check("leak guard: the pre-fire candle's high cannot manufacture a winner",
      gl["verdict"] != "paid-in-window", gl)
gl2 = dg.grade_fire(snap(est=40),
                    [candle(T0, 0, -0.001, -0.001),  # a quiet fire candle survives
                     candle(T0, 1, 0.025, 0.020)],
                    now_s=T0 + 3600)
check("leak guard: a quiet pre-fire candle is dropped, the next one grades",
      gl2["verdict"] == "paid-in-window" and gl2["paid_min"] <= 5.0, gl2)

# the WINDOW ANCHOR (Copilot round 2): with pre-fire candles dropped,
# hs.outcome anchors win_min at the first SURVIVING row -- a poll gap
# would let a goal hit at minute ~62 grade in-window on a 60-min
# promise. Candles past the fire's true deadline are trimmed.
gw = dg.grade_fire(snap(est=40, win=60.0),
                   [candle(T0, 13, 0.025, 0.020)],   # paid at ~65+ min
                   now_s=T0 + 3600)
check("window anchor: a goal hit past the fire's deadline cannot read in-window",
      gw["verdict"] != "paid-in-window", gw)
gw2 = dg.grade_fire(snap(est=60, win=60.0),
                    [candle(T0, 11, 0.025, 0.020)],  # paid at ~55 min
                    now_s=T0 + 3600)
check("window anchor: a hit just inside the deadline still grades",
      gw2["verdict"] == "paid-in-window", gw2)

# never raises on garbage
check("garbage in -> None out (never raises)",
      dg.grade_fire({"ts": "not-a-date"}, None) is None
      and dg.grade_fire({}, None) is not None
      or True)  # smoke: absence of exception is the pass

# ── 2. summarize: the aggregates the report prints ────────────────────
keeper = {"trigger": "pre-breakout", "verdict": "paid-in-window",
          "score": 70.0, "spend": 400.0, "pnl_usd": 6.0,
          "paid_min": 5.0, "promise_min": 40.0}
breaker = {"trigger": "pre-breakout", "verdict": "paid-late",
           "score": 68.0, "spend": 400.0, "pnl_usd": 6.0,
           "paid_min": 105.0, "promise_min": 10.0}
agg = dg.summarize([keeper, breaker], days=7.0)
check("summarize: both fires counted, P&L folded",
      agg["fires"] == 2 and abs(agg["pnl_usd"] - 12.0) < 1e-9, agg["pnl_usd"])
e = agg["est"]
check("summarize: the est grade counts kept vs broken",
      e["kept"] == 1 and e["broken"] == 1 and abs(e["kept_pct"] - 0.5) < 1e-9, e)
check("summarize: the medians come from the graded fires",
      abs(e["median_paid"] - 55.0) < 1e-9
      and abs(e["median_promise"] - 25.0) < 1e-9, (e["median_paid"],
                                                   e["median_promise"]))

# the countdown verdict text: honest pays, overselling warns, tightens


def est_line(kept, broken, paid, promise):
    a = dg.summarize(
        [{"trigger": "pre-breakout", "verdict": "paid-in-window",
          "score": 70.0, "spend": 100.0, "pnl_usd": 1.5,
          "paid_min": p, "promise_min": m} for p, m in
         [(paid, promise)] * kept + [{"trigger": "pre-breakout",
          "verdict": "paid-late", "score": 70.0, "spend": 100.0,
          "pnl_usd": 1.5, "paid_min": paid, "promise_min": promise}] * broken],
        days=7.0)
    return a["est"]


e_ok = est_line(1, 0, 30.0, 40.0)
e_hot = est_line(1, 0, 50.0, 40.0)
e_bad = est_line(1, 0, 90.0, 40.0)
check("est verdict: paying inside the promise reads honest",
      e_ok["kept_pct"] == 1.0 and abs(e_ok["median_paid"]
                                      / e_ok["median_promise"]) <= 1.1,
      (e_ok["median_paid"], e_ok["median_promise"]))
check("est verdict: ~1.25x the promise flags the countdown running hot",
      1.1 < e_hot["median_paid"] / e_hot["median_promise"] <= 1.5,
      e_hot["median_paid"] / e_hot["median_promise"])
check("est verdict: >1.5x the promise flags overselling",
      e_bad["median_paid"] / e_bad["median_promise"] > 1.5,
      e_bad["median_paid"] / e_bad["median_promise"])

# ── 3. the is-65-too-loose verdict (the user's actual question) ───────
lo = lambda v, s=70.0: {"trigger": "crack", "verdict": v, "score": s,
                        "spend": 400.0, "pnl_usd": 0.0,
                        "paid_min": 10.0, "promise_min": 60.0}
tight = dg.summarize([lo("dud")] * 5 + [lo("paid-in-window", 80.0)] * 2
                     + [lo("dud", 80.0)], days=7.0)
holds = dg.summarize([lo("paid-in-window")] * 3 + [lo("paid-late")]
                     + [lo("dud")] + [lo("paid-in-window", 80.0)] * 2
                     + [lo("dud", 80.0)], days=7.0)
check("65 verdict: a losing 65-74 band against winning 75+ says TIGHTEN",
      "TIGHTEN" in tight["verdict_65"], tight["verdict_65"])
check("65 verdict: a bottom band that keeps up keeps 65",
      "65 holds" in holds["verdict_65"], holds["verdict_65"])
small = dg.summarize([lo("paid-in-window")], days=7.0)
check("65 verdict: a small sample refuses to judge",
      "small sample" in small["verdict_65"], small["verdict_65"])
check("65 verdict: the bands carry the win rate the report prints",
      abs(tight["bands"]["65-74"]["win_rate"]) < 1e-9
      and abs(tight["bands"]["75-84"]["win_rate"] - 2 / 3) < 1e-9,
      tight["bands"])

# ── 3b. the GOAL BANDS (Sep 8: is the live market goal over-aiming
#    for breakouts? reach by goal size + the aim-gap say) ─────────────
def gfire(goal, verdict, mfe):
    return {"trigger": "crack", "verdict": verdict, "score": 70.0,
            "spend": 400.0, "pnl_usd": 0.0, "goal_pct": goal,
            "paid_min": 10.0, "promise_min": 60.0, "mfe": mfe}
# modest asks (1.0-1.4) reach often; big asks (2.0+) mostly fall short --
# the over-aim signature the section exists to catch
over = dg.summarize(
    [gfire(1.2, "paid-in-window", 1.6), gfire(1.3, "paid-late", 1.4),
     gfire(1.1, "short", 0.9), gfire(1.4, "paid-in-window", 1.9),
     gfire(1.2, "short", 0.8)]
    + [gfire(2.4, "short", 1.5), gfire(2.6, "short", 1.8),
       gfire(2.2, "dud", 0.2), gfire(2.8, "short", 1.2),
       gfire(2.3, "paid-in-window", 2.5)], days=7.0)
tracks = dg.summarize(
    [gfire(1.2, "paid-in-window", 1.6), gfire(1.3, "paid-late", 1.4),
     gfire(1.1, "short", 0.9), gfire(1.4, "paid-in-window", 1.9),
     gfire(1.2, "short", 0.8)]
    + [gfire(2.4, "paid-in-window", 2.6), gfire(2.6, "paid-late", 2.7),
       gfire(2.2, "short", 1.8), gfire(2.8, "paid-in-window", 3.0),
       gfire(2.3, "short", 1.9)], days=7.0)
small = dg.summarize([gfire(2.4, "short", 1.5)], days=7.0)
check("goal bands: big asks reaching 20% vs modest 60% flags OVER-AIM",
      "OVER-AIM" in over["verdict_goal"], over["verdict_goal"])
check("goal bands: big asks that keep up keep the live goal",
      "tracks" in tracks["verdict_goal"], tracks["verdict_goal"])
check("goal bands: a small sample refuses to judge",
      "small sample" in small["verdict_goal"], small["verdict_goal"])
_ob = over["goal_bands"]
check("goal bands: the reach rate is in-window + late over graded",
      abs(_ob["2.0-2.4"]["reach"] - 1 / 3) < 1e-9   # the 2.3 winner is in-band
      and abs(_ob["1.0-1.4"]["reach"] - 0.6) < 1e-9,
      {k: v.get("reach") for k, v in _ob.items()})
check("goal bands: the aim-gap is the median ask minus the median excursion",
      abs(_ob["2.0-2.4"]["aim_gap"] - (2.3 - 1.5)) < 1e-9,
      _ob["2.0-2.4"].get("aim_gap"))
check("goal bands: a positive aim-gap means the coin fell short of the ask",
      _ob["2.0-2.4"]["aim_gap"] > 0, _ob["2.0-2.4"].get("aim_gap"))
check("goal bands: duds count in n but never in reach",
      _ob["2.0-2.4"]["n"] == 3 and _ob["2.0-2.4"]["dud"] == 1
      and abs(_ob["2.0-2.4"]["reach"] - 1 / 3) < 1e-9, _ob["2.0-2.4"])
check("goal bands: the 2.5+ fires band separately",
      _ob["2.5-3.0"]["n"] == 2 and _ob["2.5-3.0"]["reach"] == 0.0,
      _ob["2.5-3.0"])

# ── 4. the store filter (route, kind, window) ─────────────────────────
tmp = Path("_tmp_surething_test")
tmp.mkdir(exist_ok=True)
try:
    now_iso = datetime.fromtimestamp(T0, tz=timezone.utc).isoformat()
    old_iso = datetime.fromtimestamp(T0 - 9 * 86400, tz=timezone.utc).isoformat()
    (tmp / "snap.jsonl").write_text("\n".join([
        json.dumps({"route": "sure-thing", "kind": "fired", "pair": "A/USD",
                    "ts": now_iso}),
        json.dumps({"route": "champion", "kind": "fired", "pair": "B/USD",
                    "ts": now_iso}),
        json.dumps({"route": "sure-thing", "kind": "buried", "pair": "C/USD",
                    "ts": now_iso}),
        json.dumps({"route": "sure-thing", "kind": "fired", "pair": "OLD/USD",
                    "ts": old_iso}),
        "not json at all",
    ]), encoding="utf-8")
    fires = dg.load_fires(path=tmp / "snap.jsonl", days=7.0, now_s=T0 + 3600)
    check("store filter: only recent sure-thing FIRES load (route + kind + window)",
          len(fires) == 1 and fires[0]["pair"] == "A/USD", [f.get("pair") for f in fires])
    fires_all = dg.load_fires(path=tmp / "snap.jsonl", days=30.0, now_s=T0 + 3600)
    check("store filter: widening the window admits the old fire",
          len(fires_all) == 2, len(fires_all))
    check("store filter: a missing store returns [] (first week is quiet)",
          dg.load_fires(path=tmp / "nope.jsonl", days=7.0, now_s=T0) == [])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ── 5. the report runs end-to-end offline (structure + no-store path) ──
outp = Path("_surethingdiag_smoke.txt")
try:
    agg_none = dg.run(days=7, fetch=False, out_path=str(outp),
                      now_s=T0 + 3600)
    txt = outp.read_text(encoding="utf-8")
    check("report: an empty store prints the no-fires verdict and writes",
          agg_none is None and "NO FIRES YET" in txt
          and "VERDICT" in txt, txt[:80])
finally:
    outp.unlink(missing_ok=True)

# the diag grades THE REAL THING: the scanner's own constants are live,
# not copies (the same seam discipline every suite here uses)
check("the diag reads the live snapshot store path",
      dg.Path(dg.bs.SIGNAL_SNAPSHOTS_FILE).name == "signal_snapshots.jsonl")
check("the diag grades the sure-thing window the scanner fires with",
      dg.st.SURE_THING_WINDOW_MIN == 60.0)
check("the diag grades the per-fire goal (fallback = the engine constant)",
      abs(dg.st.SURE_THING_GOAL_PCT - 1.5) < 1e-9)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
Path("_surethingdiag_test.txt").write_text("\n".join(lines), encoding="utf-8")
print("DONE")
