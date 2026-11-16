"""Does the block simulator manufacture distress? Compare it with the one tape.

The only realized evidence in the project is a single 8,682-trade path. That is one
draw from a distribution nobody has seen, so the comparison can do two things and no
more: put the tape's statistics on the simulator's percentile scale, and ask whether
the tape would be extraordinary if the simulator were right. It cannot estimate the
simulator's error, and it cannot validate the tail it never samples.

Statistics compared, at MATCHED trade count first (8,682 trades, the tape's own
length) and then at the horizons the report actually uses:
  max drawdown   worst log-equity fall from a running peak
  peak           best log gain reached
  final          log gain at the end
  completed      whether +12% was touched before -3%
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
SIZE, BLOCK = 0.01, 50
SERIES = os.path.join(HERE, "scratch", "_cc_series.json")
OUT = os.path.join(HERE, "scratch", "_sim_calibration.json")


def path_stats(L):
    peak_run = np.maximum.accumulate(L)
    dd = float((L - peak_run).min())
    hit_p = int(np.argmax(L >= A)) if (L >= A).any() else -1
    hit_d = int(np.argmax(L <= -B)) if (L <= -B).any() else -1
    complete = hit_p >= 0 and (hit_d < 0 or hit_p < hit_d)
    return {"max_dd": dd, "peak": float(L.max()), "final": float(L[-1]),
            "completed": complete, "trades_to_target": hit_p if complete else None}


def sim(nets, n_trades, paths, seed):
    rng = np.random.default_rng(seed)
    out = []
    nb = n_trades // BLOCK + 1
    for _ in range(paths):
        starts = rng.integers(0, max(1, nets.size - BLOCK), size=nb)
        seq = np.concatenate([nets[s:s + BLOCK] for s in starts])[:n_trades]
        L = np.cumsum(np.log1p(SIZE * seq / 100.0))
        out.append(path_stats(L))
    return out


def pct(value, sample, lower_is_extreme=True):
    """Percentile of `value` inside `sample`, with the direction that matters."""
    s = np.asarray(sample, float)
    if lower_is_extreme:
        return float((s <= value).mean())
    return float((s >= value).mean())


def main():
    data = json.load(open(SERIES, encoding="utf-8"))
    nets = np.asarray(data["configs"]["wide"]["nets"], float)
    tape = path_stats(np.cumsum(np.log1p(SIZE * nets / 100.0)))
    n_trades = nets.size
    res = {"config": "wide", "tape_trades": n_trades,
           "tape": {k: (round(v, 5) if isinstance(v, float) else v)
                    for k, v in tape.items()}, "arms": {}}

    for label, n, paths, seed in (("matched_8682", n_trades, 900, 71),
                                  ("horizon_365d", 17301, 900, 73),
                                  ("horizon_730d", 34602, 600, 79)):
        s = sim(nets, n, paths, seed)
        dds = [x["max_dd"] for x in s]
        peaks = [x["peak"] for x in s]
        finals = [x["final"] for x in s]
        comp = [x for x in s if x["completed"]]
        to_t = [x["trades_to_target"] for x in comp]
        arm = {
            "trades": n, "paths": paths,
            "max_dd": {"tape": round(tape["max_dd"], 5),
                       "sim_median": round(float(np.median(dds)), 5),
                       "sim_p05": round(float(np.percentile(dds, 5)), 5),
                       "sim_p95": round(float(np.percentile(dds, 95)), 5),
                       "tape_percentile": round(pct(tape["max_dd"], dds), 4),
                       "share_at_least_as_bad": round(float((np.array(dds)
                                                             <= tape["max_dd"]).mean()), 4)},
            "peak": {"tape": round(tape["peak"], 5),
                     "sim_median": round(float(np.median(peaks)), 5),
                     "sim_p05": round(float(np.percentile(peaks, 5)), 5),
                     "sim_p95": round(float(np.percentile(peaks, 95)), 5),
                     "tape_percentile": round(pct(tape["peak"], peaks,
                                                  lower_is_extreme=False), 4)},
            "final": {"tape": round(tape["final"], 5),
                      "sim_median": round(float(np.median(finals)), 5),
                      "tape_percentile": round(pct(tape["final"], finals,
                                                   lower_is_extreme=False), 4)},
            "completion": {"share_completed": round(len(comp) / paths, 4),
                           "tape_completed": bool(tape["completed"]),
                           "median_trades_to_target": (round(float(np.median(to_t)), 0)
                                                       if to_t else None),
                           "p10_trades_to_target": (round(float(np.percentile(to_t, 10)), 0)
                                                    if to_t else None)},
        }
        res["arms"][label] = arm
        print(f"\n=== {label}  ({paths} paths x {n} trades)", flush=True)
        print(f"  max drawdown  tape {arm['max_dd']['tape']:+.5f}  "
              f"sim median {arm['max_dd']['sim_median']:+.5f}  "
              f"[p05 {arm['max_dd']['sim_p05']:+.5f}, p95 {arm['max_dd']['sim_p95']:+.5f}]  "
              f"tape percentile {arm['max_dd']['tape_percentile']:.1%}", flush=True)
        print(f"  peak          tape {arm['peak']['tape']:+.5f}  "
              f"sim median {arm['peak']['sim_median']:+.5f}  "
              f"[p05 {arm['peak']['sim_p05']:+.5f}, p95 {arm['peak']['sim_p95']:+.5f}]  "
              f"tape percentile {arm['peak']['tape_percentile']:.1%}", flush=True)
        print(f"  completed     sim {arm['completion']['share_completed']:.1%}  "
              f"tape {arm['completion']['tape_completed']}  "
              f"median trades to target {arm['completion']['median_trades_to_target']}",
              flush=True)

    # what one path can settle: the probability of a tape this mild, if the model holds
    matched = res["arms"]["matched_8682"]
    res["verdict"] = {
        "tape_is_milder_than": matched["max_dd"]["share_at_least_as_bad"],
        "reading": ("the tape's worst drawdown is milder than the simulator's median; "
                    "one path cannot say the simulator is wrong, only that it draws "
                    "deeper troughs than the single path that happened"),
        "power": ("with n=1 there is no estimate of the model's error: a single path can "
                  "reject a model only if it sits far outside the simulated family, and "
                  "this one does not"),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print("\ntape", res["tape"])
    print("wrote", OUT)


if __name__ == "__main__":
    main()
