"""exit_take_sweep.py — stop, arm, giveback AND take, ranked by theta on cheap pairs.

Extends the earlier sweep in the three ways that matter for the contest:

  * TAKE enters the grid. The shipped exit has no target ("nothing caps the upside"); a
    fixed take cuts sigma and limits how far a winner can run, which is exactly the
    trade theta = 2mu/sigma^2 is about.
  * CHEAP PAIRS ONLY, with each pair's own measured round-trip toll from
    scratch/_cost_live.json (0.0002% BTC .. 0.038% WLD) instead of the 0.08 constant.
  * Both 1% and 2% tickets. theta scales as 1/s so the ORDER cannot change between
    them; what changes is the date, so P(pass) in a horizon is reported for both.

Model notes: a take fills at its price when the bar's high reaches it (optimistic for a
polled exit, and the same optimism the stop already carries); the stop is checked first,
so a bar that spans both is booked as a loss. Holds cap at 576 bars and the capped
count is reported.

    python scratch/exit_take_sweep.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import overwatch_timing as ot
from trendride_study import NOV1, load, prep

ENTRIES = os.path.join(HERE, "_band_entries.json")
COST = os.path.join(HERE, "_cost_live.json")
OUT = os.path.join(HERE, "_exit_take_sweep.json")
CAP = 576
A, B = math.log(1.12), math.log(1.0 / 0.97)

STOPS = (1.0, 1.5, 2.0, 3.0)
ARMS = (0.2, 0.4, 0.8)
GIVES = (0.08, 0.10, 0.15, 0.30)
TAKES = (0.0, 0.5, 1.0, 2.0, 5.0)


def tokens():
    d = json.load(open(COST))
    return {p.split("/")[0]: v["spread_rt_pct"] for p, v in d["pairs"].items()
            if "spread_rt_pct" in v and v["class"] == "cheap"}


def theta(e, sd, s):
    mu, sig = s * e / 100.0, s * sd / 100.0
    return (2 * mu / sig ** 2) if sig > 0 else 0.0


def p_free(th):
    if th <= 0:
        return 0.0
    return (1 - math.exp(-th * B)) / (1 - math.exp(-th * (A + B)))


def simulate(entries, series, tolls, stop, arm, give, take):
    ot.STOP_PCT, ot.ACTIVATE_PCT, ot.GIVE_PCT = stop, arm, give
    nets, tss, holds, capped = [], [], [], 0
    for sym, ts, entry in entries:
        s = series[sym]
        i = s["index"].get(ts)
        if i is None or i >= len(s["c"]) - 1:
            continue
        hi, lo, cl = s["h"], s["l"], s["c"]
        peak, stop_px = hi[i], ot.initial_stop(entry)
        take_px = entry * (1.0 + take / 100.0) if take > 0 else None
        exit_px, bars = None, None
        end = min(len(cl), i + CAP)
        for k in range(i, end):
            peak, stop_px, exit_px, _r = ot.manage(entry=entry, peak=peak, stop=stop_px,
                                                   bar_high=hi[k], bar_low=lo[k])
            if exit_px is not None:
                bars = k - i + 1
                break
            if take_px is not None and hi[k] >= take_px:
                exit_px, bars = take_px, k - i + 1
                break
        if exit_px is None:
            capped += 1
            exit_px, bars = cl[end - 1], end - i
        nets.append((exit_px / entry - 1.0) * 100.0 - tolls[sym])
        tss.append(ts)
        holds.append(bars)
    return nets, tss, holds, capped


def mc(arr, s, n, paths=1200, seed=9):
    a = np.asarray(arr, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, a.size, size=(paths, min(n, 60000)))
    L = np.cumsum(np.log1p(s * a[idx] / 100.0), axis=1)
    up, dn = L >= A, L <= -B
    fu = np.where(up.any(1), up.argmax(1), L.shape[1] + 1)
    fd = np.where(dn.any(1), dn.argmax(1), L.shape[1] + 1)
    return float((fu < fd).mean()), float((fd < fu).mean())


def main():
    t0 = time.time()
    tolls = tokens()
    entries_all = json.load(open(ENTRIES))
    entries = [e for e in entries_all if e[0] in tolls]
    d = load()
    series = {}
    for sym in tolls:
        if sym not in d["pairs"]:
            continue
        p = prep(d["pairs"][sym])
        series[sym] = {"h": p["h"], "l": p["l"], "c": p["c"],
                       "index": {t: i for i, t in enumerate(p["ts"])}}
    entries = [e for e in entries if e[0] in series]
    entries.sort(key=lambda r: r[1])
    print(f"cheap pairs {len(series)}  entries {len(entries):,}  "
          f"tolls {min(tolls.values()):.4f}%..{max(tolls.values()):.4f}%  "
          f"({time.time()-t0:.0f}s)")

    rows = []
    cells = [(st, ar, gv, tk) for st in STOPS for ar in ARMS
             for gv in GIVES for tk in TAKES]
    for n, (st, ar, gv, tk) in enumerate(cells, 1):
        nets, tss, holds, capped = simulate(entries, series, tolls, st, ar, gv, tk)
        a = np.asarray(nets)
        tsa = np.asarray(tss)
        e, sd = float(a.mean()), float(a.std())
        th1 = theta(e, sd, 0.01)
        rows.append({"stop": st, "arm": ar, "give": gv, "take": tk,
                     "win_pct": round(100.0 * float((a > 0).mean()), 1),
                     "e": round(e, 4), "sd": round(sd, 4),
                     "theta_1pct": round(th1, 1), "theta_2pct": round(th1 / 2, 1),
                     "p_free": round(p_free(th1), 3),
                     "nov": round(float(a[tsa >= NOV1].mean()), 4),
                     "hold_h": round(float(np.median(holds)) * 5 / 60, 2),
                     "capped": capped, "n": len(nets)})
        if n % 20 == 0:
            print(f"  {n}/{len(cells)}  best so far "
                  f"{max(r['theta_1pct'] for r in rows):.0f}  "
                  f"({time.time()-t0:.0f}s)")

    rows.sort(key=lambda r: -r["theta_1pct"])
    print(f"\nTOP 15 OF {len(rows)} BY THETA (1% ticket, cheap pairs, measured tolls)")
    print(f"  {'stop':>5}{'arm':>5}{'give':>6}{'take':>6}{'win%':>7}{'e%':>9}"
          f"{'sd%':>8}{'theta1':>9}{'theta2':>8}{'Pfree':>7}{'Nov%':>9}{'hold':>6}")
    for r in rows[:15]:
        print(f"  {r['stop']:>5}{r['arm']:>5}{r['give']:>6}{r['take']:>6}"
              f"{r['win_pct']:>7.1f}{r['e']:>+9.4f}{r['sd']:>8.4f}"
              f"{r['theta_1pct']:>9.0f}{r['theta_2pct']:>8.0f}"
              f"{r['p_free']*100:>6.1f}%{r['nov']:>+9.4f}{r['hold_h']:>6.2f}")

    shipped = next(r for r in rows
                   if (r["stop"], r["arm"], r["give"], r["take"]) == (2.0, 0.4, 0.15, 0.0))
    rank = 1 + [i for i, r in enumerate(rows) if r is shipped][0]
    print(f"\nTHE 82%-WIN SHAPE (2.0/0.4/0.15, no take): win {shipped['win_pct']}%, "
          f"e {shipped['e']:+.4f}%, sd {shipped['sd']:.4f}%, "
          f"theta {shipped['theta_1pct']:.0f}, ranks {rank} of {len(rows)}")

    # best-by-take for the take dimension
    print("\nBEST CELL PER TAKE")
    for tk in TAKES:
        b = max((r for r in rows if r["take"] == tk), key=lambda r: r["theta_1pct"])
        print(f"  take {tk:>4}: {b['stop']}/{b['arm']}/{b['give']}  "
              f"win {b['win_pct']:>5.1f}%  e {b['e']:+.4f}%  sd {b['sd']:.4f}%  "
              f"theta {b['theta_1pct']:.0f}  Nov {b['nov']:+.4f}%")

    print("\nHORIZON P(pass) FOR THE TOP 5 (87 trades/day)")
    for r in rows[:5]:
        nets, _t, _h, _c = simulate(entries, series, tolls, r["stop"], r["arm"],
                                    r["give"], r["take"])
        h = {}
        for s in (0.01, 0.02):
            for dd in (120, 180, 365):
                p, dth = mc(nets, s, int(87 * dd))
                h[f"{s*100:g}%|{dd}d"] = {"pass": round(p, 3), "death": round(dth, 3)}
        r["horizon"] = h
        print(f"  {r['stop']}/{r['arm']}/{r['give']}/{r['take']}: " +
              "  ".join(f"{k} {v['pass']*100:.0f}%/{v['death']*100:.0f}%"
                        for k, v in h.items()))

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"cheap_pairs": sorted(series), "tolls": tolls, "cap": CAP,
                   "a": A, "b": B, "grid": rows,
                   "shipped_rank": rank}, fh, indent=1)
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
