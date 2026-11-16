"""The completions/day metric done properly: elapsed days, not horizon slots.

passes/(paths*horizon) saturates the moment every path completes, which is exactly
the case at 730d. The objective is completions per ELAPSED day, so a path that
completes on day 209 counts more than one that completes on day 237: days accrue
until the completion (or the horizon, for paths that never complete).

Recomputes the four policies on the wide tape at both horizons, sharing the rolling
statistics across policies so this runs in a couple of minutes.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import controller_backtest as bt

SERIES = os.path.join(HERE, "scratch", "_cc_series.json")
OUT = os.path.join(HERE, "scratch", "_controller_metric.json")
POLS = ("always_on", "halt_only", "size_only", "live")


def arm(nets, paths, horizon_days, seed):
    rng = np.random.default_rng(seed)
    n = int(bt.PACE * horizon_days)
    acc = {p: {"pass": 0, "death": 0, "alive": 0, "days": 0.0, "taken": 0.0}
           for p in POLS}
    for _ in range(paths):
        starts = rng.integers(0, max(1, nets.size - bt.BLOCK), size=n // bt.BLOCK + 1)
        seq = np.concatenate([nets[s:s + bt.BLOCK] for s in starts])[:n]
        mu_s, t_s, mu_m, mu_all = bt.rolling(seq)
        halted = bt.halt_flags(t_s)
        sched = bt.size_mult(mu_m, mu_all)
        ones = np.ones_like(sched)
        zero = np.zeros_like(halted)
        for pol, (mult, hz) in (("always_on", (ones, zero)),
                                ("halt_only", (ones, halted)),
                                ("size_only", (sched, zero)),
                                ("live", (sched, halted))):
            verdict, at, _f, _v = bt.run_policy(seq, mult, hz)
            a = acc[pol]
            a[verdict.lower()] += 1
            a["taken"] += float((~hz).mean())
            # elapsed days: until the completion for passes, the whole horizon otherwise
            a["days"] += (at / bt.PACE) if verdict == "PASS" else horizon_days
    out = {}
    for p, a in acc.items():
        out[p] = {"p_pass": round(a["pass"] / paths, 4),
                  "p_death": round(a["death"] / paths, 4),
                  "completions": a["pass"],
                  "elapsed_days": round(a["days"], 1),
                  "completions_per_day": a["pass"] / a["days"] if a["days"] else 0.0,
                  "taken_frac": round(a["taken"] / paths, 3)}
    return out


def main():
    data = json.load(open(SERIES, encoding="utf-8"))
    nets = np.asarray(data["configs"]["wide"]["nets"], float)
    res = {"config": "wide", "paths": 300, "arms": {}}
    for label, horizon, seed in (("365d", 365, 41), ("730d", 730, 43)):
        got = arm(nets, 300, horizon, seed)
        base = max(got["always_on"]["completions_per_day"],
                   got["size_only"]["completions_per_day"])
        res["arms"][label] = {"policies": got,
                              "live_vs_best_baseline":
                                  got["live"]["completions_per_day"] - base,
                              "halt_cost":
                                  got["halt_only"]["completions_per_day"]
                                  - got["always_on"]["completions_per_day"],
                              "size_gain":
                                  got["size_only"]["completions_per_day"]
                                  - got["always_on"]["completions_per_day"]}
        print(f"\n=== wide {label} ({300} paths, elapsed-day metric)", flush=True)
        for p in POLS:
            g = got[p]
            print(f"  {p:<10} pass {g['p_pass']:.1%} death {g['p_death']:.1%} "
                  f"elapsed {g['elapsed_days']:.0f}d  "
                  f"comp/day {g['completions_per_day']:.6f}  taken {g['taken_frac']:.0%}",
                  flush=True)
        a = res["arms"][label]
        print(f"  -> halt alone {a['halt_cost']:+.6f}/day | size alone "
              f"{a['size_gain']:+.6f}/day | live vs best {a['live_vs_best_baseline']:+.6f}/day",
              flush=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
