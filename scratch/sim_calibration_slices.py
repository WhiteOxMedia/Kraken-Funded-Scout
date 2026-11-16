"""Calibration over date slices: more achieved points, but not more independent paths.

The tape is cut into three consecutive calendar thirds and each slice is treated as a
realized path of its own length. For every slice the block-50 simulator is run at the
SAME trade count, and the slice's drawdown and peak are placed on the simulated
percentile scale.

What this buys: three comparisons instead of one, so a single unlucky point cannot
decide whether the simulator manufactures distress, and a spread across sub-periods
becomes visible.

What it does NOT buy, and the report has to say so: these are not independent draws.
All three come from the same history, they are correlated by construction, and a slice
is hypothetical — the tape did not only experience that window, so a slice's drawdown is
not a drawdown any strategy actually lived through. The only genuinely realized path
remains the full tape.
"""
from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import numpy as np

A, B = math.log(1.12), math.log(1 / 0.97)
SIZE, BLOCK, PATHS = 0.01, 50, 900
SERIES = os.path.join(HERE, "scratch", "_cc_series.json")
OUT = os.path.join(HERE, "scratch", "_sim_calibration_slices.json")


def stats_of(nets):
    L = np.cumsum(np.log1p(SIZE * np.asarray(nets, float) / 100.0))
    hp = int(np.argmax(L >= A)) if (L >= A).any() else -1
    hd = int(np.argmax(L <= -B)) if (L <= -B).any() else -1
    return {"trades": int(nets.size),
            "max_dd": float((L - np.maximum.accumulate(L)).min()),
            "peak": float(L.max()), "final": float(L[-1]),
            "completed": bool(hp >= 0 and (hd < 0 or hp < hd))}


def sim(nets, n, paths=PATHS, seed=211):
    rng = np.random.default_rng(seed)
    dds, peaks, comp = [], [], 0
    for _ in range(paths):
        starts = rng.integers(0, max(1, nets.size - BLOCK), size=n // BLOCK + 1)
        seq = np.concatenate([nets[s:s + BLOCK] for s in starts])[:n]
        L = np.cumsum(np.log1p(SIZE * seq / 100.0))
        dds.append(float((L - np.maximum.accumulate(L)).min()))
        peaks.append(float(L.max()))
        hp = int(np.argmax(L >= A)) if (L >= A).any() else -1
        hd = int(np.argmax(L <= -B)) if (L <= -B).any() else -1
        if hp >= 0 and (hd < 0 or hp < hd):
            comp += 1
    return np.asarray(dds), np.asarray(peaks), comp / paths


def pct(value, sample, lower_is_extreme):
    s = np.asarray(sample, float)
    return float((s <= value).mean() if lower_is_extreme else (s >= value).mean())


def main():
    data = json.load(open(SERIES, encoding="utf-8"))
    nets = np.asarray(data["configs"]["wide"]["nets"], float)
    ts = np.asarray(data["configs"]["wide"]["ts"], float)
    # the series stores seconds since epoch (1.788e9); handle ms defensively
    scale = 1000.0 if ts.max() > 1e11 else 1.0
    lo, hi = float(ts.min()), float(ts.max())
    edges = [lo + (hi - lo) * k / 3 for k in (0, 1, 2, 3)]

    res = {"config": "wide", "paths": PATHS, "block": BLOCK, "size": SIZE,
           "slices": {}, "tape_days": round((hi - lo) / (86400 * scale), 1),
           "ts_unit": "seconds" if scale == 1.0 else "milliseconds"}
    for k in range(3):
        m = (ts >= edges[k]) & (ts < edges[k + 1] if k < 2 else ts <= edges[k + 1])
        slice_nets = nets[m]
        if slice_nets.size < 200:
            continue
        st = stats_of(slice_nets)
        dds, peaks, share = sim(nets, st["trades"])   # matched length, same tape
        entry = {
            "window": [str(np.datetime64(int(edges[k] / scale), "s")),
                       str(np.datetime64(int(edges[k + 1] / scale), "s"))],
            "tape": {key: (round(v, 5) if isinstance(v, float) else v)
                     for key, v in st.items()},
            "max_dd": {"sim_median": round(float(np.median(dds)), 5),
                       "sim_p05": round(float(np.percentile(dds, 5)), 5),
                       "sim_p95": round(float(np.percentile(dds, 95)), 5),
                       "tape_percentile": round(pct(st["max_dd"], dds, True), 4)},
            "peak": {"sim_median": round(float(np.median(peaks)), 5),
                     "sim_p05": round(float(np.percentile(peaks, 5)), 5),
                     "sim_p95": round(float(np.percentile(peaks, 95)), 5),
                     "tape_percentile": round(pct(st["peak"], peaks, False), 4)},
            "completion": {"share_simulated": round(share, 4),
                           "tape_completed": st["completed"]},
        }
        res["slices"][f"slice_{k + 1}"] = entry
        print(f"\n=== slice_{k + 1}  {entry['window'][0]} .. {entry['window'][1]}  "
              f"n={st['trades']}", flush=True)
        print(f"  max dd  tape {st['max_dd']:+.5f}  sim median "
              f"{entry['max_dd']['sim_median']:+.5f}  "
              f"[{entry['max_dd']['sim_p05']:+.5f}, {entry['max_dd']['sim_p95']:+.5f}]  "
              f"percentile {entry['max_dd']['tape_percentile']:.1%}", flush=True)
        print(f"  peak    tape {st['peak']:+.5f}  sim median "
              f"{entry['peak']['sim_median']:+.5f}  "
              f"[{entry['peak']['sim_p05']:+.5f}, {entry['peak']['sim_p95']:+.5f}]  "
              f"percentile {entry['peak']['tape_percentile']:.1%}", flush=True)
        print(f"  completed  sim {share:.1%}  tape {st['completed']}", flush=True)

    sl = list(res["slices"].values())
    res["summary"] = {
        "n_slices": len(sl),
        "dd_percentiles": [s["max_dd"]["tape_percentile"] for s in sl],
        "peak_percentiles": [s["peak"]["tape_percentile"] for s in sl],
        "inside_central_90": sum(1 for s in sl
                                 if 0.05 <= s["max_dd"]["tape_percentile"] <= 0.95
                                 and 0.05 <= s["peak"]["tape_percentile"] <= 0.95),
        "expected_inside_if_model_right": round(0.9 ** 2 * len(sl), 2),
        "independence": ("none: the slices are consecutive windows of ONE path, "
                         "correlated by construction, and each is hypothetical - the tape "
                         "did not trade only that window, so a slice drawdown is not a "
                         "drawdown anyone lived through. Three points cannot turn one "
                         "realized path into a distribution."),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print("\nsummary", json.dumps(res["summary"], indent=1))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
