"""CONTROLLER BACKTEST — cost on the tape, benefit where death happens.

The live policy under test is exactly the one wired into the scanner
(contest_control.ContestController):

  halt    short-window (last 500 trades) t <= -1.96 stops new entries;
          resume only once t > 0 AND 3 hours have passed since the halt
  size    clip(mid-window mu / all-window mu, 0.25, 2.0), re-anchored at the
          expanding mean so nothing looks ahead

Two channels, because the question is asymmetric:

  COST     the realized tape, one path, where death never happens (max log
           trough -0.0083). Here a stop rule has nothing to prevent and can only
           interrupt a recovery, so this channel measures what arming gives up.
  BENEFIT  block-resampled paths, where death does happen. Here the same rule can
           actually save a run, and its size schedule can compound a good stretch.

Four policies on both channels:
  always_on   size 1.0, never halt          (constant-size baseline)
  halt_only   halt, size 1.0                (halt's own effect)
  size_only   never halt, schedule          (schedule's own effect)
  live        halt + schedule               (what ARMED=True would actually do)

Completions are counted, not modelled: a path completes when its equity first
touches +12% before -3%, and completions/day = completions / (paths * horizon_days).
"""
from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

A = math.log(1.12)
B = math.log(1 / 0.97)
PACE = 47.4                 # trades/day the live controller's clock assumes
SIZE_BASE = 0.01
SHORT, MID, BLOCK = 500, 2000, 50
STOP_T, RESUME_T = -1.96, 0.0
HOLD_TRADES = int(round(3 * PACE / 24.0))     # 3 hours of wall clock, in trades
HORIZON_DAYS = 365
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERIES = os.path.join(HERE, "scratch", "_cc_series.json")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "_controller_backtest.json")


def rolling(nets):
    """mu, t for the 500- and 2000-trade windows, plus the expanding mean.

    The policy reads the trade stream it is standing on, so every statistic is
    computed from trades already seen: no window ever contains its own future.
    """
    a = np.asarray(nets, float)
    n = a.size
    c1, c2 = np.cumsum(a), np.cumsum(a * a)
    idx = np.arange(n)

    def window(k):
        start = np.maximum(0, idx - k + 1)
        cnt = idx - start + 1
        lo1 = np.where(start > 0, c1[np.maximum(start - 1, 0)], 0.0)
        lo2 = np.where(start > 0, c2[np.maximum(start - 1, 0)], 0.0)
        mu = (c1 - lo1) / cnt
        var = np.maximum((c2 - lo2) / cnt - mu * mu, 1e-12)
        t = mu / np.sqrt(var / cnt)
        return mu, t

    mu_s, t_s = window(SHORT)
    mu_m, _t_m = window(MID)
    mu_all = c1 / np.arange(1, n + 1)
    return mu_s, t_s, mu_m, mu_all


def halt_flags(t_s):
    """Latch: stop when the short window is significantly negative, resume when it
    turns non-negative and the 3-hour hold has elapsed."""
    halted = np.zeros(t_s.size, bool)
    on, since = False, -1
    for i, t in enumerate(t_s):
        if not on:
            if t <= STOP_T:
                on, since = True, i
        elif i - since >= HOLD_TRADES and t > RESUME_T:
            on = False
        halted[i] = on
    return halted


def size_mult(mu_m, mu_all):
    ref = np.where(np.abs(mu_all) > 1e-9, np.abs(mu_all), 1e-9)
    return np.clip(mu_m / ref, 0.25, 2.0)


def run_policy(nets, mult, halted):
    """Return (verdict, trade index of the outcome, final log equity).

    A halted trade is not taken: it contributes no return but still consumes a
    trade slot of time, which is what sitting out means at a fixed pace.
    """
    a = np.asarray(nets, float)
    m = np.where(halted, 0.0, mult)
    inc = np.log1p(SIZE_BASE * m * a / 100.0)
    L = np.cumsum(inc)
    hit_p = np.argmax(L >= A) if (L >= A).any() else -1
    hit_d = np.argmax(L <= -B) if (L <= -B).any() else -1
    if hit_p >= 0 and (hit_d < 0 or hit_p < hit_d):
        return "PASS", int(hit_p), float(L[-1]), float(L[max(hit_p, 0)])
    if hit_d >= 0:
        return "DEATH", int(hit_d), float(L[-1]), float(L[hit_d])
    return "ALIVE", -1, float(L[-1]), float(L[-1])


def policies_for(nets):
    mu_s, t_s, mu_m, mu_all = rolling(nets)
    halted = halt_flags(t_s)
    sched = size_mult(mu_m, mu_all)
    ones = np.ones_like(sched)
    zero = np.zeros_like(halted)
    return {
        "always_on": (ones, zero),
        "halt_only": (ones, halted),
        "size_only": (sched, zero),
        "live": (sched, halted),
    }


def tape_channel(data, name):
    nets = np.asarray(data["configs"][name]["nets"], float)
    out = {}
    for pol, (mult, halted) in policies_for(nets).items():
        verdict, at, final, atval = run_policy(nets, mult, halted)
        out[pol] = {"verdict": verdict, "trade": at,
                    "log_at_close": round(final, 4),
                    "taken_frac": round(float((~halted).mean()), 3),
                    "log_peak": round(atval, 4)}
    base = out["always_on"]["log_at_close"]
    for pol in out:
        out[pol]["cost_vs_always_on"] = round(out[pol]["log_at_close"] - base, 4)
    return out


def sim_channel(data, name, paths, seed=17):
    nets = np.asarray(data["configs"][name]["nets"], float)
    if nets.size < BLOCK * 2:
        return None
    rng = np.random.default_rng(seed)
    n = int(PACE * HORIZON_DAYS)
    # draw one shared trade-index path per simulated run so every policy sees the
    # same tape; otherwise the comparison is between different random paths
    counts = {p: {"PASS": 0, "DEATH": 0, "ALIVE": 0, "days": []}
              for p in ("always_on", "halt_only", "size_only", "live")}
    for _ in range(paths):
        starts = rng.integers(0, nets.size - BLOCK, size=n // BLOCK + 1)
        seq = np.concatenate([nets[s:s + BLOCK] for s in starts])[:n]
        for pol, (mult, halted) in policies_for(seq).items():
            verdict, at, _final, _val = run_policy(seq, mult, halted)
            counts[pol][verdict] += 1
            if verdict == "PASS":
                counts[pol]["days"].append(at / PACE)
    out = {}
    total_days = paths * HORIZON_DAYS
    for pol, c in counts.items():
        med = float(np.median(c["days"])) if c["days"] else None
        out[pol] = {"p_pass": round(c["PASS"] / paths, 4),
                    "p_death": round(c["DEATH"] / paths, 4),
                    "median_days": None if med is None else round(med, 1),
                    "completions": c["PASS"],
                    "completions_per_day": c["PASS"] / total_days}
    return out


def main():
    data = json.load(open(SERIES, encoding="utf-8"))
    res = {"pace": PACE, "horizon_days": HORIZON_DAYS, "block": BLOCK,
           "size_base": SIZE_BASE, "stop_t": STOP_T, "hold_trades": HOLD_TRADES,
           "barriers": {"A": A, "B": B}, "tape": {}, "sim": {}}
    for name, paths in (("tight", 150), ("mid", 150), ("shipped", 150), ("wide", 300)):
        if name not in data["configs"]:
            continue
        res["tape"][name] = tape_channel(data, name)
        res["sim"][name] = sim_channel(data, name, paths)
        t, s = res["tape"][name], res["sim"][name]
        print(f"\n=== {name}   tape {t['always_on']['verdict']}   "
              f"({paths} paths x {HORIZON_DAYS}d)", flush=True)
        for pol in ("always_on", "halt_only", "size_only", "live"):
            print(f"  tape {pol:<10} {t[pol]['verdict']:<5} log {t[pol]['log_at_close']:+.4f} "
                  f"({t[pol]['cost_vs_always_on']:+.4f})  taken {t[pol]['taken_frac']:.0%}  |  "
                  f"sim pass {s[pol]['p_pass']:.1%} death {s[pol]['p_death']:.1%} "
                  f"comp/day {s[pol]['completions_per_day']:.5f} "
                  f"med {s[pol]['median_days']}d", flush=True)
    # gate-facing summary: the live policy against the better of its two baselines
    wide = res["sim"]["wide"]
    base = max(wide["always_on"]["completions_per_day"],
               wide["size_only"]["completions_per_day"])
    res["net_completions_delta"] = wide["live"]["completions_per_day"] - base
    res["cost_channel"] = {"tape": res["tape"], "horizon_days": None}
    res["benefit_channel"] = {"sim": res["sim"], "paths": 300,
                              "trades": int(PACE * HORIZON_DAYS)}
    res["paths"] = 300
    res["trades"] = int(PACE * HORIZON_DAYS)
    res["verdict"] = ("live beats both baselines" if res["net_completions_delta"] > 0
                      else "live does not beat the better baseline")
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print(f"\nnet completions/day (wide, live - best baseline): "
          f"{res['net_completions_delta']:+.6f}  -> {res['verdict']}")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
