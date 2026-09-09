# diag_surething.py -- THE SURE THING WEEKLY DIAG (user spec, Sep 8:
# "add a weekly diag that grades the fire rate and per-trigger P&L so we
# can tell if 65 is too loose" -- extended the same day: grade est_min,
# "did breakouts actually pay inside the promised window?").
#
# Reads the fired sure-thing snapshots (route='sure-thing' in
# signal_snapshots.jsonl), pulls forward 5m candles from Kraken's public
# OHLC, and grades every fire four ways:
#
#   FIRE RATE   fires/day overall + per trigger, next to the popup cap
#   P&L MODEL   the hindsight verdict per fire, size-weighted by the
#               popup's spend -- the FLAT tide's half-size must show up
#               here or the sizing factor is decorative
#   EST_MIN     the countdown's promise vs reality: pre-breakout fires
#               promise ~N minutes; did the goal actually pay INSIDE the
#               promise, late, or never? (crack fires promise the full
#               window -- graded against it the same way)
#   IS 65 LOOSE win rate by score band (65-74 vs 75+) -- if the bottom
#               band wins materially less, the threshold is too loose;
#               if it keeps up, 65 earns its keep
#
# HONEST LIMITS, printed in the report:
#   - Kraken's public OHLC serves only ~720 recent 5m candles (~60h), so
#     fires older than that grade 'no-data' here -- the archive
#     downloader (bt_surething.py) covers deep history separately.
#   - The goal is graded PER FIRE from the snapshot's goal_pct stamp
#     (Sep 8); records from before the stamp fall back to the engine
#     constant -- the value those fires actually ran with.
#   - P&L model: winner = the goal captured (+goal% of spend), anything
#     else = the window-close exit (end_move% of spend). Hindsight, not
#     fills.
#
# Run: .venv\Scripts\python.exe diag_surething.py [--days 7]
#      ->  _surething_check.txt
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import background_scanner as bs
import hindsight_scorer as hs
import sure_thing as st

lines = []


def out(s=""):
    print(s)
    lines.append(s)


def _median(vals):
    if not vals:
        return None
    v = sorted(vals)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


# ── 1. THE FIRES (the store the scanner writes at every popup) ────────


def load_fires(path=None, days=7.0, now_s=None):
    """Fired sure-thing snapshots inside the window. Pure file read --
    no network, never raises. now_s: 'now' epoch override (tests)."""
    p = Path(path) if path else Path(bs.SIGNAL_SNAPSHOTS_FILE)
    if not p.exists():
        return []
    now_s = now_s if now_s is not None else datetime.now().timestamp()
    fires = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("route") != "sure-thing" or r.get("kind") != "fired":
                continue
            try:
                t = datetime.fromisoformat(str(r.get("ts"))).timestamp()
            except Exception:
                continue
            if now_s - t > days * 86400.0:
                continue
            fires.append(r)
    except Exception:
        return []
    fires.sort(key=lambda r: str(r.get("ts")))
    return fires


# ── 2. GRADE ONE FIRE (pure -- the test grades this directly) ─────────


def grade_fire(snap, fwd_rows=None, now_s=None):
    """One fired snapshot -> the grade dict. Pure (no I/O): the caller
    supplies forward candles (Kraken order: [ts_ms, o, h, l, c, v]) and
    the 'now' epoch. Verdicts:
      paid-in-window  goal paid INSIDE the promised window
      paid-late       goal paid, but after the promise ran out
      short           moved (+0.3%+) but never reached the goal
      dud             never really moved
      open            too young to judge (still inside its window)
      no-data         old enough to judge but no forward candles came
                      back (the ~60h public-archive limit)
    Candles that opened BEFORE the fire are dropped (Copilot, Sep 8):
    the popup lands mid-candle and the fire candle's PRE-fire range is
    not tradeable -- hs.outcome treats the first row as the entry
    moment, so a leak there could grade a winner that paid before the
    recommendation existed. Understating speed beats flattering it."""
    now_s = now_s if now_s is not None else datetime.now().timestamp()
    try:
        t0 = datetime.fromisoformat(str(snap.get("ts"))).timestamp()
    except Exception:
        return None
    entry = float(snap.get("entry") or 0.0)
    win_min = float(snap.get("win_min") or st.SURE_THING_WINDOW_MIN)
    est = int(snap.get("est_min") or win_min)
    trig = str(snap.get("trigger") or "crack")
    # THE PER-FIRE GOAL (Sep 8): the snapshot now carries the goal the
    # fire was graded on at fire time (sure_thing.SURE_THING_GOAL_PCT --
    # the same number the climb-out gate and the danger panel used).
    # Pre-stamp records fall back to the engine constant -- which is the
    # value those fires actually ran with.
    goal = float(snap.get("goal_pct") or st.SURE_THING_GOAL_PCT)
    fwd_rows = [x for x in (fwd_rows or [])
                if float(x[0]) > t0 * 1000.0]
    # and the WINDOW is fire-relative (Copilot, Sep 8): hs.outcome
    # anchors its win_min at the first surviving row, so a poll gap
    # would let a goal hit at minute ~62 grade as in-window on a 60-min
    # promise -- trim everything past the fire's true deadline.
    # GRANULARITY HONESTY: a boundary candle opened just before the
    # deadline still carries its full 5-min high, so a goal crossing in
    # its final minutes can be stamped in-window at the candle's open
    # (bounded by ~5 min; disclosed in the report's limits -- dropping
    # the boundary candle would under-count real in-window pays, the
    # same lie in the other direction).
    fwd_rows = [x for x in fwd_rows
                if float(x[0]) <= t0 * 1000.0 + win_min * 60.0 * 1000.0]
    # the promise: a pre-breakout fire promises its countdown; a crack
    # fire promises the full window (it said NOW, and NOW pays in-window)
    promise_min = est if trig == "pre-breakout" else win_min
    age_min = (now_s - t0) / 60.0
    g = {"pair": snap.get("pair"), "ts": snap.get("ts"),
         "trigger": trig, "score": float(snap.get("score") or 0.0),
         "spend": float(snap.get("spend") or 0.0),
         "mult": float(snap.get("mult") or 0.0),
         "goal_pct": goal,
         "est_min": est, "promise_min": promise_min,
         "entry": entry, "age_min": round(age_min, 1),
         "tide": (snap.get("climb") or {}).get("climb_tide"),
         "verdict": None, "paid_min": None, "pnl_usd": 0.0}
    if age_min < promise_min:
        g["verdict"] = "open"
        return g
    if entry <= 0 or not fwd_rows:
        g["verdict"] = "no-data"
        return g
    oc = hs.outcome(fwd_rows, entry, goal, win_min)
    if oc is None:
        g["verdict"] = "no-data"
        return g
    # hs.outcome measures min_to_goal from the FIRST forward candle's
    # open; the countdown promise starts at the FIRE. Anchor paid_min to
    # the fire so a goal hit in the first candle reads ~0-5 min, not 0
    # -- the skew is the poll gap between the popup and the candle
    # boundary (clamped: a candle that opened before the fire is the
    # fire itself).
    paid = oc.get("min_to_goal_min")
    if paid is not None:
        offset_min = max(0.0, (float(fwd_rows[0][0]) / 1000.0 - t0) / 60.0)
        paid = round(offset_min + paid, 1)
    g["paid_min"] = paid
    g["mfe"] = oc.get("mfe_pct")
    g["end_move"] = oc.get("end_move_pct")
    payoff = oc.get("payoff")
    if payoff == "winner" and paid is not None:
        # the goal paid -- did it pay inside the promise?
        g["verdict"] = ("paid-in-window" if paid <= promise_min
                        else "paid-late")
        g["pnl_usd"] = g["spend"] * goal / 100.0
    elif payoff == "grinder":
        g["verdict"] = "short"
        g["pnl_usd"] = g["spend"] * (g["end_move"] or 0.0) / 100.0
    else:
        g["verdict"] = "dud"
        g["pnl_usd"] = g["spend"] * (g["end_move"] or 0.0) / 100.0
    return g


# ── 3. SUMMARIZE (pure -- the test grades this too) ──────────────────


def summarize(grades, days=7.0):
    """grades -> the aggregate dict the report prints. Pure."""
    agg = {"fires": len(grades), "per_day": len(grades) / max(days, 0.001),
           "graded": 0, "per_trigger": {}, "bands": {},
           "est": {"kept": 0, "broken": 0, "kept_pct": None,
                   "median_paid": None, "median_promise": None},
           "pnl_usd": 0.0, "verdict_65": "no graded fires yet"}
    for g in grades:
        if g is None:
            continue
        agg["pnl_usd"] += g.get("pnl_usd") or 0.0
        if g.get("verdict") in ("paid-in-window", "paid-late",
                                "short", "dud"):
            agg["graded"] += 1
        trig = g.get("trigger") or "?"
        t = agg["per_trigger"].setdefault(trig, {
            "n": 0, "graded": 0, "win": 0, "late": 0, "short": 0,
            "dud": 0, "open": 0, "nodata": 0, "paid_min": [],
            "promise": [], "pnl": 0.0})
        t["n"] += 1
        v = g.get("verdict")
        if v in ("paid-in-window", "paid-late", "short", "dud"):
            t["graded"] += 1
            t["pnl"] += g.get("pnl_usd") or 0.0
        if v == "paid-in-window":
            t["win"] += 1
            t["paid_min"].append(g.get("paid_min"))
            t["promise"].append(g.get("promise_min"))
        elif v == "paid-late":
            t["late"] += 1
            t["paid_min"].append(g.get("paid_min"))
            t["promise"].append(g.get("promise_min"))
        elif v == "short":
            t["short"] += 1
        elif v == "dud":
            t["dud"] += 1
        elif v == "open":
            t["open"] += 1
        else:
            t["nodata"] += 1
    # the est_min accountability (pre-breakout's promise is the product)
    pb = agg["per_trigger"].get("pre-breakout", {})
    kept = pb.get("win", 0)
    late = pb.get("late", 0)
    graded_pb = kept + late
    agg["est"] = {"kept": kept, "broken": late,
                  "kept_pct": (kept / graded_pb) if graded_pb else None,
                  "median_paid": _median(pb.get("paid_min") or []),
                  "median_promise": _median(pb.get("promise") or [])}
    # the 65 verdict: the bottom band vs everything above it
    def _band(g):
        s = g.get("score") or 0.0
        return "65-74" if s < 75 else ("75-84" if s < 85 else "85+")
    bands = {}
    for g in grades:
        if g is None or g.get("verdict") not in (
                "paid-in-window", "paid-late", "short", "dud"):
            continue
        b = _band(g)
        d = bands.setdefault(b, {"n": 0, "win": 0, "pnl": 0.0})
        d["n"] += 1
        if g.get("verdict") == "paid-in-window":
            d["win"] += 1
        d["pnl"] += g.get("pnl_usd") or 0.0
    for b in ("65-74", "75-84", "85+"):
        if b in bands:
            bands[b]["win_rate"] = bands[b]["win"] / bands[b]["n"]
    agg["bands"] = bands
    low, high = bands.get("65-74"), {
        k: v for k, v in bands.items() if k != "65-74"}
    hn = sum(v["n"] for v in high.values())
    hw = sum(v["win"] for v in high.values())
    if low and low["n"] >= 5 and hn >= 3:
        hr = hw / hn
        if low["win_rate"] + 0.15 < hr:
            agg["verdict_65"] = (
                f"TIGHTEN: the 65-74 band wins {low['win_rate']:.0%} vs "
                f"{hr:.0%} above it -- the loose tail is paying for the "
                f"headliners")
        else:
            agg["verdict_65"] = (
                f"65 holds: the bottom band keeps up ({low['win_rate']:.0%} "
                f"vs {hr:.0%})")
    elif agg["graded"]:
        agg["verdict_65"] = (f"small sample (65-74 n={low['n'] if low else 0}, "
                             f"75+ n={hn}) -- keep 65, keep grading")
    # the GOAL BANDS (Sep 8): is the live market goal over-aiming for
    # breakouts? Breakout moves are violent but SHORT -- a goal the
    # coin's excursion never reaches grades 'short' no matter how good
    # the setup was. Reach (the goal reached at all: in-window + late)
    # by goal size is the tell: the band whose reach collapses is asking
    # for what the market doesn't give. The aim-gap (median goal minus
    # median realized excursion) says by how much.
    def _gband(g):
        gp = float(g.get("goal_pct") or 0.0)
        if gp < 1.5:
            return "1.0-1.4"
        if gp < 2.0:
            return "1.5-1.9"
        if gp < 2.5:
            return "2.0-2.4"
        return "2.5-3.0"
    gb = {}
    for g in grades:
        if g is None or g.get("verdict") not in (
                "paid-in-window", "paid-late", "short", "dud"):
            continue
        b = _gband(g)
        d = gb.setdefault(b, {"n": 0, "win": 0, "late": 0, "short": 0,
                              "dud": 0, "mfe": [], "goal": []})
        d["n"] += 1
        if g.get("verdict") == "paid-in-window":
            d["win"] += 1
        elif g.get("verdict") == "paid-late":
            d["late"] += 1
        elif g.get("verdict") == "short":
            d["short"] += 1
        else:
            d["dud"] += 1
        if g.get("mfe") is not None:
            d["mfe"].append(float(g["mfe"]))
        if g.get("goal_pct") is not None:
            d["goal"].append(float(g["goal_pct"]))
    for b, d in gb.items():
        d["reach"] = ((d["win"] + d["late"]) / d["n"]) if d["n"] else None
        d["win_rate"] = (d["win"] / d["n"]) if d["n"] else None
        d["median_mfe"] = _median(d["mfe"]) if d["mfe"] else None
        d["median_goal"] = _median(d["goal"]) if d["goal"] else None
        d["aim_gap"] = ((d["median_goal"] - d["median_mfe"])
                        if (d["median_mfe"] is not None
                            and d["median_goal"] is not None) else None)
    agg["goal_bands"] = gb
    lo_b = gb.get("1.0-1.4")
    hi_n = gb.get("2.0-2.4", {}).get("n", 0) + gb.get("2.5-3.0", {}).get("n", 0)
    hi_reach_hits = (gb.get("2.0-2.4", {}).get("win", 0)
                     + gb.get("2.0-2.4", {}).get("late", 0)
                     + gb.get("2.5-3.0", {}).get("win", 0)
                     + gb.get("2.5-3.0", {}).get("late", 0))
    hi_reach = (hi_reach_hits / hi_n) if hi_n else None
    lo_reach = lo_b.get("reach") if lo_b else None
    if hi_n >= 5 and lo_reach is not None and hi_reach is not None:
        if lo_reach - hi_reach >= 0.15:
            agg["verdict_goal"] = (
                f"THE GOAL OVER-AIMS: big asks (2.0%+) reach the goal "
                f"{hi_reach:.0%} vs {lo_reach:.0%} for the modest ones -- "
                f"breakout moves are short; the aim should come down")
        else:
            agg["verdict_goal"] = (
                f"the live goal tracks what breakouts give (big asks "
                f"reach {hi_reach:.0%} vs {lo_reach:.0%} modest)")
    elif gb:
        agg["verdict_goal"] = (f"small sample (2.0+ n={hi_n}) -- keep grading")
    else:
        agg["verdict_goal"] = "no graded fires yet"
    # the calibration evidence (Sep 8: "let the goal bands calibrate
    # back"), written AFTER the verdict exists -- DEFINITIVE verdicts
    # only: an OVER-AIM replaces the measurement (the tilt's numerator
    # and denominator), a tracking verdict writes its own evidence (the
    # seam reads neutral -- and a real tracking week LIFTS a standing
    # squeeze immediately instead of waiting out the TTL). Inconclusive
    # runs (small sample, no graded fires) NEVER write: a quiet week
    # must not overwrite a standing OVER-AIM whose TTL handles expiry.
    if ("OVER-AIM" in agg["verdict_goal"]
            or "tracks" in agg["verdict_goal"]):
        _hi_med = [b for b in ("2.0-2.4", "2.5-3.0") if b in gb
                   and gb[b].get("median_goal") is not None]
        try:
            _n = sum(gb[b]["n"] for b in _hi_med)
            _asked = _median([gb[b]["median_goal"] for b in _hi_med])
            _gave = _median([gb[b]["median_mfe"] for b in _hi_med
                             if gb[b].get("median_mfe") is not None])
            _evid = {"ts": datetime.now(timezone.utc).isoformat(),
                     "verdict": agg["verdict_goal"],
                     "median_gave": _gave, "median_asked": _asked,
                     "big_n": _n}
            # ATOMIC write (Copilot, Sep 8): the scanner reads this file
            # at fire time -- a truncated in-place write could be read
            # mid-flight, graded neutral, and the tilt skipped for a
            # week. UNIQUE temp + os.replace: a reader ever sees the old
            # complete file or the new one, never a partial -- and two
            # concurrent diag runs (scheduled + manual) cannot clobber
            # each other's temp (Copilot round 2: the fixed shared temp
            # path was not concurrency-safe).
            _fdr, _tmpname = tempfile.mkstemp(
                dir=str(Path(__file__).parent),
                prefix="surething_goal_tilt.json.tmp")
            try:
                with os.fdopen(_fdr, "w", encoding="utf-8") as _f:
                    json.dump(_evid, _f, indent=1)
                os.replace(_tmpname,
                           Path(__file__).parent / "surething_goal_tilt.json")
            finally:
                # a crash before the replace must not litter the repo
                if os.path.exists(_tmpname):
                    try:
                        os.unlink(_tmpname)
                    except OSError:
                        pass
            agg["tilt_evidence"] = _evid
        except Exception as _exc:
            # fail-open for the REPORT, but visible -- a silent pass here
            # once hid an ordering bug from the very tests built to catch it
            print(f"(tilt evidence write failed: {_exc})")
    return agg


# ── 4. THE REPORT ─────────────────────────────────────────────────────


def _fmt_pct(x):
    return f"{x:.0%}" if x is not None else "-"


def run(days=7.0, fetch=True, out_path="_surething_check.txt", now_s=None):
    lines.clear()
    now_s = now_s if now_s is not None else datetime.now().timestamp()
    fires = load_fires(days=days, now_s=now_s)
    out(f"THE SURE THING WEEKLY DIAG  ({days:.0f}d window, "
        f"{datetime.fromtimestamp(now_s):%Y-%m-%d %H:%M} MST)")
    out(f"fires: {len(fires)}  (route='sure-thing' in "
        f"signal_snapshots.jsonl)")
    if not fires:
        out()
        out("NO FIRES YET -- the store accumulates from the live scanner")
        out("(the Breakout Alert commits stamp route/trigger/est_min/"
            "spend/entry).")
        out("VERDICT: nothing to grade -- rerun after the first fires.")
        _write(out_path)
        return None
    # forward candles (one fetch per fire, ~0.35s apart, be polite)
    grades, fetched = [], 0
    if fetch:
        try:
            import requests
            s = requests.Session()
            s.headers.update({"User-Agent": "scout-surething-diag"})
            for r in fires:
                try:
                    t0 = datetime.fromisoformat(str(r.get("ts"))).timestamp()
                    rows = hs._fetch_5m(
                        s, r.get("pair"), int((t0 - 6 * 60) * 1000))
                    cut = (t0 - 5 * 60) * 1000.0
                    fwd = [x for x in rows if float(x[0]) >= cut]
                    fetched += 1
                except Exception:
                    fwd = None
                grades.append(grade_fire(r, fwd, now_s=now_s))
                import time as _t
                _t.sleep(0.35)
        except Exception as exc:
            out(f"(fetch unavailable: {exc} -- grading without candles)")
            grades = [grade_fire(r, None, now_s=now_s) for r in fires]
    else:
        grades = [grade_fire(r, None, now_s=now_s) for r in fires]
    agg = summarize(grades, days=days)
    out()
    out("── FIRE RATE ──────────────────────────────────────────")
    out(f"  {agg['fires']} fires / {days:.0f}d = {agg['per_day']:.1f}/day "
        f"(popup cap: {bs.POPUP_CAP_DEFAULT} per "
        f"{bs.POPUP_CAP_WINDOW_MIN:.0f} min)")
    for trig, t in agg["per_trigger"].items():
        out(f"    {trig}: {t['n']} fired  "
            f"(graded {t['graded']}, open {t['open']}, no-data {t['nodata']})")
    out()
    out("── PER-TRIGGER P&L (the hindsight model: winner = the fire's OWN "
        "goal on the spend, else the window-close exit) ──")
    for trig, t in agg["per_trigger"].items():
        if not t["graded"]:
            continue
        n, wr = t["graded"], (t["win"] / t["graded"]) if t["graded"] else 0.0
        wr_all = ((t["win"] + t["late"]) / n) if n else 0.0
        out(f"    {trig}: {n} graded  in-window {t['win']} "
            f"({_fmt_pct(wr)})  late {t['late']}  short {t['short']}  "
            f"dud {t['dud']}")
        out(f"      winner-any {t['win'] + t['late']}/{n} "
            f"({_fmt_pct(wr_all)})   P&L ${t['pnl']:,.2f}")
    out(f"  TOTAL P&L ${agg['pnl_usd']:,.2f} across "
        f"{agg['graded']} graded fires")
    out()
    out("── EST_MIN: DID BREAKOUTS PAY INSIDE THE PROMISED WINDOW? ──")
    e = agg["est"]
    if e["kept"] + e["broken"] == 0:
        out("  no pre-breakout fires graded yet (the countdown's grade "
            "lands with the first ones)")
    else:
        out(f"  kept: {e['kept']}   broken: {e['broken']}   "
            f"promise kept {_fmt_pct(e['kept_pct'])}")
        mp, me = e["median_paid"], e["median_promise"]
        if mp is not None and me:
            ratio = mp / me if me else 0
            out(f"  median: paid {mp:.0f} min vs promised ~{me:.0f} min "
                f"({ratio:.1f}x the promise)")
            if ratio <= 1.1:
                out("  the countdown is honest (pays within the promise)")
            elif ratio <= 1.5:
                out("  the countdown runs ~hot -- consider +25% on est_min")
            else:
                out("  THE COUNTDOWN OVERSELLS -- est_min needs the "
                    "recalibration")
    out()
    out("── IS THE GOAL TOO BIG? (what the market gave vs what the goal "
        "asked, by goal band) ──")
    if not agg["goal_bands"]:
        out("  no graded fires yet")
    else:
        for b in ("1.0-1.4", "1.5-1.9", "2.0-2.4", "2.5-3.0"):
            if b in agg["goal_bands"]:
                d = agg["goal_bands"][b]
                mm = (f"{d['median_mfe']:.2f}%"
                      if d.get("median_mfe") is not None else "-")
                mg = (f"{d['median_goal']:.2f}%"
                      if d.get("median_goal") is not None else "-")
                gap = (f"  aim-gap {d['aim_gap']:+.2f}pts"
                       if d.get("aim_gap") is not None else "")
                out(f"    {b}%: n={d['n']}  reached {d['win'] + d['late']} "
                    f"({_fmt_pct(d.get('reach'))})  in-window {d['win']} "
                    f"({_fmt_pct(d.get('win_rate'))})  "
                    f"median gave {mm} vs asked {mg}{gap}")
        out("  (aim-gap positive = the coin's median excursion fell short "
            "of the ask -- over-aimed)")
        out(f"  VERDICT: {agg['verdict_goal']}")
    out()
    out("── IS 65 TOO LOOSE? (win rate by score band) ──────────")
    if not agg["bands"]:
        out("  no graded fires yet")
    else:
        for b in ("65-74", "75-84", "85+"):
            if b in agg["bands"]:
                d = agg["bands"][b]
                out(f"    {b}: n={d['n']}  in-window {d['win']} "
                    f"({_fmt_pct(d.get('win_rate'))})  P&L ${d['pnl']:,.2f}")
    out()
    out(f"VERDICT: {agg['verdict_65']}")
    out()
    out(f"({fetched}/{len(fires)} fires got forward candles; older fires "
        f"than ~60h grade no-data on the public API -- bt_surething.py "
        f"holds the deep archive; goal graded PER FIRE from the snapshot's "
        f"goal_pct stamp (pre-stamp fires fall back to "
        f"{st.SURE_THING_GOAL_PCT:.1f}%); boundary candle granularity "
        f"~5 min)")
    _write(out_path)
    return agg


def _write(out_path):
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n→ {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--no-fetch", action="store_true",
                    help="grade from the store without network calls")
    a = ap.parse_args()
    run(days=a.days, fetch=not a.no_fetch)
