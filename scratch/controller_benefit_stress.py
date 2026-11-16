"""Where does the halt actually earn its keep? Run the policy on tapes that die.

The 365d channel found 0% death for every config, so it cannot price the halt's
benefit: a stop rule with no deaths to prevent is pure cost. This harness builds
the two conditions where death is real and repeats the same four-policy comparison:

  730d      the same tape, double the horizon (death first appears here)
  worst-rg  only the trades of the regime whose mu is negative (wide: -0.547%/trade)
  synthetic mu -0.15%/trade, sd 0.5, 4000 trades: a genuinely losing tape

If the halt helps anywhere it must help here; if it still only costs, arming buys
nothing anywhere.
"""
from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import contest_control as cc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import controller_backtest as bt  # noqa: E402  (same directory)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SERIES = os.path.join(HERE, "scratch", "_cc_series.json")
OUT = os.path.join(HERE, "scratch", "_controller_benefit_stress.json")


def run_arm(nets, paths, horizon_days, seed=23):
    pace = bt.PACE
    n = int(pace * horizon_days)
    rng = np.random.default_rng(seed)
    out = {p: {"PASS": 0, "DEATH": 0, "ALIVE": 0, "days": [], "taken": 0.0}
           for p in ("always_on", "halt_only", "size_only", "live")}
    for _ in range(paths):
        starts = rng.integers(0, max(1, nets.size - bt.BLOCK), size=n // bt.BLOCK + 1)
        seq = np.concatenate([nets[s:s + bt.BLOCK] for s in starts])[:n]
        for pol, (mult, halted) in bt.policies_for(seq).items():
            verdict, at, _f, _v = bt.run_policy(seq, mult, halted)
            out[pol][verdict] += 1
            out[pol]["taken"] += float((~halted).mean())
            if verdict == "PASS":
                out[pol]["days"].append(at / pace)
    total_days = paths * horizon_days
    for pol, c in out.items():
        med = float(np.median(c["days"])) if c["days"] else None
        c.update({"p_pass": round(c["PASS"] / paths, 4),
                  "p_death": round(c["DEATH"] / paths, 4),
                  "median_days": None if med is None else round(med, 1),
                  "completions_per_day": c["PASS"] / total_days,
                  "taken_frac": round(c["taken"] / paths, 3)})
        c.pop("days")
        c.pop("taken")
    return out


def main():
    data = json.load(open(SERIES, encoding="utf-8"))
    wide = np.asarray(data["configs"]["wide"]["nets"], float)
    ts = data["configs"]["wide"]["ts"]
    labels, _ = cc.trade_regime_labels(wide, ts)
    res = {"paths": 300, "block": bt.BLOCK, "pace": bt.PACE, "arms": {}}

    res["arms"]["730d_wide"] = run_arm(wide, 300, 730, seed=23)

    prof = []
    for j in sorted({int(x) for x in labels if x >= 0}):
        m = labels == j
        prof.append({"regime": j, "trades": int(m.sum()),
                     "mu": round(float(wide[m].mean()), 4)})
    worst = min(prof, key=lambda r: r["mu"])
    sub = wide[labels == worst["regime"]]
    res["worst_regime"] = worst
    res["arms"]["worst_regime"] = run_arm(sub, 300, 365, seed=29)

    rng = np.random.default_rng(31)
    synth = rng.normal(-0.15, 0.5, 4000)
    res["arms"]["synthetic_losing"] = run_arm(synth, 300, 365, seed=31)

    for arm, got in res["arms"].items():
        print(f"\n=== {arm}", flush=True)
        for pol in ("always_on", "halt_only", "size_only", "live"):
            g = got[pol]
            print(f"  {pol:<10} pass {g['p_pass']:.1%}  death {g['p_death']:.1%}  "
                  f"comp/day {g['completions_per_day']:.5f}  med {g['median_days']}d  "
                  f"taken {g['taken_frac']:.0%}", flush=True)
        base = max(got["always_on"]["completions_per_day"],
                   got["size_only"]["completions_per_day"])
        print(f"  -> live vs best baseline: "
              f"{got['live']['completions_per_day'] - base:+.6f} comp/day  "
              f"(halt alone {got['halt_only']['completions_per_day'] - got['always_on']['completions_per_day']:+.6f})",
              flush=True)
        res["arms"][arm]["_delta"] = {
            "live_vs_best": got["live"]["completions_per_day"] - base,
            "halt_vs_always": got["halt_only"]["completions_per_day"]
            - got["always_on"]["completions_per_day"]}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
