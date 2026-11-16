"""exit_theta_sweep.py — choose the exit by theta, not by win rate.

Every exit decision so far was made on the win column. The contest scores
P(balance reaches +12% before -3%), which for a multiplicative walk is

    theta = 2*mu/sigma^2        P = (1-e^-theta b)/(1-e^-theta(a+b))

and sigma enters squared. So this sweeps the trend-ride exit's own parameters with the
shipped `overwatch_timing.manage` (constants swapped per cell, no reimplementation):

    stop      1.0 / 1.5 / 2.0 / 3.0 %      the initial stop
    arm       0.2 / 0.4 / 0.8 / 1.5 %      profit before the trail exists
    giveback  0.10 / 0.15 / 0.30 / 0.60 %  how far under the peak the trail sits

Entries are NOT swept: the band entry is fixed, so every cell trades the same 15,877
signals and only the exit differs. Ranked by theta at a 1% ticket; the top cells also
get a bootstrap P(pass) inside 90 / 180 / 365 days and their November slice, because a
cell that wins on theta and loses in November is not a winner.

    python scratch/exit_theta_sweep.py
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
from remeasure_live_config import live_entries

ENTRIES = os.path.join(HERE, "_band_entries.json")
OUT = os.path.join(HERE, "_exit_theta_sweep.json")
CAP_BARS = 576                       # 2 days; counted and reported, not hidden

A = math.log(1.12)
B = math.log(1.0 / 0.97)
STOPS = (1.0, 1.5, 2.0, 3.0)
ARMS = (0.2, 0.4, 0.8, 1.5)
GIVES = (0.10, 0.15, 0.30, 0.60)


def build_entries():
    """(sym, entry_ts, entry_px) for every band entry, cached."""
    if os.path.exists(ENTRIES):
        return json.load(open(ENTRIES))
    d = load()
    out = []
    for sym, bars in d["pairs"].items():
        p = prep(bars)
        for i in live_entries(p):
            j = i + 1
            if j >= len(p["c"]):
                continue
            out.append([sym, p["ts"][j], p["o"][j]])
    out.sort(key=lambda r: r[1])
    json.dump(out, open(ENTRIES, "w"))
    return out


def simulate(entries, series, stop_pct, arm_pct, give_pct):
    """Run ot.manage with these constants over every entry. Returns net% list."""
    ot.STOP_PCT, ot.ACTIVATE_PCT, ot.GIVE_PCT = stop_pct, arm_pct, give_pct
    nets, tss, holds, capped = [], [], [], 0
    for sym, ts, entry in entries:
        s = series.get(sym)
        if not s:
            continue
        hi, lo, c = s["h"], s["l"], s["c"]
        i = s["index"].get(ts)
        if i is None or i >= len(c) - 1:
            continue
        peak, stop = hi[i], ot.initial_stop(entry)
        exit_px, bars = None, None
        end = min(len(c), i + CAP_BARS)
        for k in range(i, end):
            peak, stop, exit_px, reason = ot.manage(entry=entry, peak=peak, stop=stop,
                                                    bar_high=hi[k], bar_low=lo[k])
            if exit_px is not None:
                bars = k - i + 1
                break
        if exit_px is None:
            capped += 1
            exit_px, bars = c[end - 1], end - i
        nets.append(ot.net_pct(entry, exit_px))
        tss.append(ts)
        holds.append(bars)
    return nets, tss, holds, capped


def theta(e, sd, s=0.01):
    mu, sig = s * e / 100.0, s * sd / 100.0
    return (2 * mu / sig ** 2) if sig > 0 else 0.0


def p_free(th):
    if th <= 0:
        return 0.0
    return (1 - math.exp(-th * B)) / (1 - math.exp(-th * (A + B)))


def mc_p(nets, s, n, paths=2000, seed=5):
    arr = np.asarray(nets, dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(paths, n))
    L = np.cumsum(np.log1p(s * arr[idx] / 100.0), axis=1)
    up, dn = L >= A, L <= -B
    fu = np.where(up.any(1), up.argmax(1), n + 1)
    fd = np.where(dn.any(1), dn.argmax(1), n + 1)
    return float((fu < fd).mean()), float((fd < fu).mean())


def main():
    t0 = time.time()
    entries = build_entries()
    d = load()
    series = {}
    for sym, bars in d["pairs"].items():
        p = prep(bars)
        series[sym] = {"h": p["h"], "l": p["l"], "c": p["c"],
                       "index": {t: i for i, t in enumerate(p["ts"])}}
    print(f"entries {len(entries):,} across {len(series)} pairs "
          f"({time.time()-t0:.0f}s)")

    rows = []
    grid = [(st_, ar, gv) for st_ in STOPS for ar in ARMS for gv in GIVES]
    for n, (st_, ar, gv) in enumerate(grid, 1):
        nets, tss, holds, capped = simulate(entries, series, st_, ar, gv)
        a = np.asarray(nets)
        e, sd = float(a.mean()), float(a.std())
        nov = float(a[np.asarray(tss) >= NOV1].mean()) if len(nets) else 0.0
        rows.append({"stop": st_, "arm": ar, "give": gv,
                     "n": len(nets), "win_pct": round(100.0 * float((a > 0).mean()), 1),
                     "e": round(e, 4), "sd": round(sd, 4),
                     "theta": round(theta(e, sd), 1),
                     "p_free": round(p_free(theta(e, sd)), 3),
                     "nov": round(nov, 4),
                     "median_hold_h": round(float(np.median(holds)) * 5 / 60, 2),
                     "capped": capped})
        print(f"  {n:>2}/{len(grid)} stop {st_:>4} arm {ar:>4} give {gv:>4}  "
              f"win {rows[-1]['win_pct']:>5.1f}%  e {e:+.4f}%  sd {sd:.4f}%  "
              f"theta {rows[-1]['theta']:>8.1f}  Nov {nov:+.4f}%")

    rows.sort(key=lambda r: -r["theta"])
    print(f"\nTOP 12 BY THETA  ({len(rows)} cells, {time.time()-t0:.0f}s)")
    print(f"  {'stop':>5} {'arm':>5} {'give':>5} {'win%':>6} {'e%':>8} {'sd%':>7} "
          f"{'theta':>9} {'Pfree':>6} {'Nov%':>8} {'hold h':>7}")
    for r in rows[:12]:
        print(f"  {r['stop']:>5} {r['arm']:>5} {r['give']:>5} {r['win_pct']:>6.1f} "
              f"{r['e']:>+8.4f} {r['sd']:>7.4f} {r['theta']:>9.1f} "
              f"{r['p_free']*100:>5.1f}% {r['nov']:>+8.4f} {r['median_hold_h']:>7.2f}")
    print("\nBOTTOM 5")
    for r in rows[-5:]:
        print(f"  stop {r['stop']:>4} arm {r['arm']:>4} give {r['give']:>4} "
              f"win {r['win_pct']:>5.1f}% e {r['e']:+.4f}% theta {r['theta']:>8.1f}")

    shipped = next(r for r in rows if (r["stop"], r["arm"], r["give"]) == (2.0, 0.4, 0.15))
    print(f"\nSHIPPED (2.0 / 0.4 / 0.15) ranks "
          f"{1 + [i for i, r in enumerate(rows) if r is shipped][0]} of {len(rows)}")

    # horizons + November for the leaders
    top = rows[:6]
    print("\nHORIZON P(pass) for the top cells (bootstrap, 87 trades/day)")
    for r in top:
        nets, tss, _h, _c = simulate(entries, series, r["stop"], r["arm"], r["give"])
        cuts = {}
        for dd in (90, 180, 365):
            ntr = int(min(87 * dd, 20000))
            p, dth = mc_p(nets, 0.01, ntr)
            cuts[f"{dd}d"] = {"pass": round(p, 3), "death": round(dth, 3)}
        r["horizon_1pct"] = cuts
        print(f"  {r['stop']}/{r['arm']}/{r['give']}: theta {r['theta']:>7.1f}  " +
              "  ".join(f"{k} {v['pass']*100:.0f}%/{v['death']*100:.0f}%"
                        for k, v in cuts.items()))

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"a": A, "b": B, "cap_bars": CAP_BARS,
                   "grid": rows, "shipped_rank": 1 + [i for i, r in enumerate(rows)
                                                      if r is shipped][0]}, fh, indent=1)
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
