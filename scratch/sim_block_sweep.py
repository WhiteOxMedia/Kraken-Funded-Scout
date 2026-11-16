"""Where manufactured distress would come from: the block length itself.

Block resampling re-orders real 50-trade chunks. That preserves local clustering, which
is real, but it also splices unrelated chunks end to end, which is not. Shrinking the
block toward iid shows how much of the simulated drawdown is a property of the splice
rather than of the tape, and the tape's own drawdown says which block length is right.

Also reproduces the tape's drawdown under every config, because the figure quoted in the
project notes (-0.0083) does not come out of the cached series and should not be used as
the reference until it is found.
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
SIZE = 0.01
SERIES = os.path.join(HERE, "scratch", "_cc_series.json")
OUT = os.path.join(HERE, "scratch", "_sim_block_sweep.json")


def tape(nets):
    L = np.cumsum(np.log1p(SIZE * nets / 100.0))
    return {"max_dd": float((L - np.maximum.accumulate(L)).min()),
            "final": float(L[-1]), "peak": float(L.max()), "trades": int(nets.size)}


def sweep(nets, n, blocks, paths=600, seed=101):
    out = {}
    for block in blocks:
        rng = np.random.default_rng(seed)
        dds, comp, finals = [], 0, []
        for _ in range(paths):
            if block <= 1:
                seq = nets[rng.integers(0, nets.size, size=n)]
            else:
                starts = rng.integers(0, max(1, nets.size - block), size=n // block + 1)
                seq = np.concatenate([nets[s:s + block] for s in starts])[:n]
            L = np.cumsum(np.log1p(SIZE * seq / 100.0))
            dds.append(float((L - np.maximum.accumulate(L)).min()))
            finals.append(float(L[-1]))
            hp = int(np.argmax(L >= A)) if (L >= A).any() else -1
            hd = int(np.argmax(L <= -B)) if (L <= -B).any() else -1
            if hp >= 0 and (hd < 0 or hp < hd):
                comp += 1
        dds = np.asarray(dds)
        out[str(block)] = {"block": block,
                           "dd_median": round(float(np.median(dds)), 5),
                           "dd_p05": round(float(np.percentile(dds, 5)), 5),
                           "dd_p95": round(float(np.percentile(dds, 95)), 5),
                           "final_median": round(float(np.median(finals)), 5),
                           "share_completed": round(comp / paths, 4)}
    return out


def main():
    data = json.load(open(SERIES, encoding="utf-8"))
    res = {"config": "wide", "size": SIZE, "paths": 600, "arms": {}}
    wide = np.asarray(data["configs"]["wide"]["nets"], float)
    res["tape_all_configs"] = {name: {k: (round(v, 5) if isinstance(v, float) else v)
                                      for k, v in tape(np.asarray(g["nets"], float)).items()}
                               for name, g in data["configs"].items()}
    for label, n in (("matched_8682", wide.size), ("horizon_365d", 17301)):
        res["arms"][label] = sweep(wide, n, (1, 10, 50, 200), 600, 101)
        print(f"\n=== {label} (n={n}, 600 paths)", flush=True)
        for b, r in res["arms"][label].items():
            print(f"  block {b:>3}: dd median {r['dd_median']:+.5f} "
                  f"[p05 {r['dd_p05']:+.5f}, p95 {r['dd_p95']:+.5f}]  "
                  f"completed {r['share_completed']:.1%}", flush=True)
    t = res["tape_all_configs"]
    print("\ntape drawdown by config (1% size):",
          {k: v["max_dd"] for k, v in t.items()})
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
