"""trendride_refine.py — push the one surviving shape past 80% wins and into profit.

trendride_study.py found that the cut-fast/ride-the-winner structure lifts e3_band
(the lower-band pullback in an uptrend) to 74.6% wins and takes the family from
deeply negative to breakeven (-0.010%/trade; November +0.019%). This script searches
the neighbourhood of that shape, and adds two entry variants that ask for more
confirmation before buying the band:

  e3_band     low touches the lower Bollinger band, close back inside, close up
  e3b_trend   the same, plus EMA200 is actually rising (the trend is live)
  e3c_vol     the same, plus volume above 1.5x its 24h mean (someone is buying)

Wider stops (up to 3.0%, the contest's own guard) and tighter give-backs are in the
grid because the ratio of stop to give-back is what sets the win rate. A config only
counts if it is positive on TRAIN and on NOVEMBER; November is never used to fit.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trendride_study import COST, MAX_HOLD, NOV1, load, prep, score, simulate

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "_trendride_refine.json")


def extra_entries(p):
    """Two confirmations on top of the band pullback."""
    c, l, e200, vol, volm = p["c"], p["l"], p["e200"], p["vol"], p["vol_mean24"]
    o = p["o"]
    n = len(c)
    e3b, e3c = [], []
    for i in p["entries"]["e3_band"]:
        if i < 13:
            continue
        if e200[i] > e200[i - 12]:
            e3b.append(i)
        if vol[i] > 1.5 * volm[i] > 0:
            e3c.append(i)
    return {"e3b_trend": e3b, "e3c_vol": e3c}


def with_volume(bars):
    """prep() plus volume and its 24h mean, which the extra variants need."""
    p = prep(bars)
    v = [float(b[5]) for b in bars]
    n = len(v)
    vm = [0.0] * n
    run = 0.0
    for i in range(n):
        run += v[i]
        if i >= 288:
            run -= v[i - 288]
        vm[i] = run / min(i + 1, 288)
    p["vol"] = v
    p["vol_mean24"] = vm
    return p


def main():
    d = load()
    grid = []
    for stop in (1.5, 2.0, 2.5, 3.0):
        for act in (0.15, 0.25, 0.4):
            for give in (0.15, 0.3, 0.45):
                grid.append((stop, act, give, False))

    results = {}
    for sym, bars in d["pairs"].items():
        p = with_volume(bars)
        entries = {"e3_band": p["entries"]["e3_band"]}
        entries.update(extra_entries(p))
        for var, idx in entries.items():
            for (stop, act, give, tb) in grid:
                key = f"{var}|S{stop}|A{act}|G{give}"
                b = results.setdefault(key, {"all": [], "train": [], "nov": []})
                tr = simulate(p, idx, stop, act, give, tb)
                b["all"] += tr
                for t in tr:
                    (b["nov"] if t[0] >= NOV1 else b["train"]).append(t)

    rows = [{"config": k, "all": score(v["all"]), "train": score(v["train"]),
             "nov": score(v["nov"])} for k, v in results.items()]
    rows.sort(key=lambda r: -r["all"].get("win_pct", 0))
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)

    print(f"{'config':30s} {'n':>6s} {'win%':>5s} {'net%':>8s} {'tot%':>8s} "
          f"{'hold':>5s} {'worst':>6s} {'best':>6s} | {'NovWin':>6s} {'NovNet':>7s} {'NovN':>6s} {'NovT':>6s}")
    for r in rows[:24]:
        a, nv = r["all"], r["nov"]
        print(f"{r['config']:30s} {a['n']:6d} {a['win_pct']:5.1f} "
              f"{a['mean_net_pct']:+8.3f} {a['total_net_pct']:+8.1f} {a['median_hold_h']:5.1f} "
              f"{a['worst']:+6.2f} {a['best']:+6.2f} | {nv.get('win_pct',0):6.1f} "
              f"{nv.get('mean_net_pct',0):+7.3f} {nv.get('n',0):6d} {nv.get('t',0):+6.2f}")

    print("\n=== >=80% wins overall ===")
    w80 = [r for r in rows if r["all"].get("win_pct", 0) >= 80.0]
    for r in w80[:15]:
        print(f"  {r['config']:30s} win {r['all']['win_pct']:5.1f}%  "
              f"net {r['all']['mean_net_pct']:+.3f}%/trade  "
              f"Nov {r['nov'].get('win_pct',0):5.1f}% win {r['nov'].get('mean_net_pct',0):+.3f}%")
    if not w80:
        best = max(rows, key=lambda r: r["all"].get("win_pct", 0))
        print(f"  NONE — best win rate is {best['all']['win_pct']}% "
              f"({best['config']}, net {best['all']['mean_net_pct']:+.3f}%/trade, "
              f"Nov {best['nov'].get('mean_net_pct',0):+.3f}%)")

    print("\n=== positive on BOTH train and November ===")
    both = [r for r in rows if r["train"].get("mean_net_pct", -9) > 0
            and r["nov"].get("mean_net_pct", -9) > 0]
    both.sort(key=lambda r: -r["nov"].get("mean_net_pct", 0))
    for r in both[:15]:
        print(f"  {r['config']:30s} train {r['train']['mean_net_pct']:+.3f}% "
              f"Nov {r['nov']['mean_net_pct']:+.3f}% n={r['nov']['n']} "
              f"win {r['all']['win_pct']:.1f}%")
    if not both:
        print("  NONE is positive on both")
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
