"""block_bootstrap.py — the theta/pass figures with the tape's streaks intact.

The sweep's Monte Carlo drew each trade INDEPENDENTLY from the pool of 8,682
outcomes. That destroys the one property the November tape is famous for: 19
losses in a row. An iid draw of 19 losses has probability p^19; on the real tape
they arrived together, and a contest dies from a RUN of losses plus the drift it
leaves behind, not from a single bad trade. So every P(death)=0 the earlier
sweeps printed was an artifact of the sampler.

Here the resample keeps contiguous runs: a path is built from blocks of L
consecutive trades (in tape order) drawn with replacement and concatenated, so
within-block streaks, clustering and drift survive. L is swept — a small L
converges back to iid, a large L converges to re-running the tape.

Reports P(pass) (+12% before -3%) and P(death) at 1%, 2% and 5% tickets on the
cheap pairs, iid vs block, for the tight-trail and shipped exits.

    python scratch/block_bootstrap.py
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

from trendride_study import load, prep
import scratch.exit_take_sweep as TS

A = math.log(1.12)
B = math.log(1.0 / 0.97)
PACE = None          # trades/day, measured from the entries
OUT = os.path.join(HERE, "_block_bootstrap.json")


def series_and_entries():
    tolls = TS.tokens()
    entries = [e for e in json.load(open(TS.ENTRIES)) if e[0] in tolls]
    d = load()
    sr = {}
    for sym in tolls:
        if sym not in d["pairs"]:
            continue
        p = prep(d["pairs"][sym])
        sr[sym] = {"h": p["h"], "l": p["l"], "c": p["c"],
                   "index": {t: i for i, t in enumerate(p["ts"])}}
    entries = [e for e in entries if e[0] in sr]
    entries.sort(key=lambda r: r[1])
    return entries, sr, tolls


def path_blocks(rng, n, npath, block, size):
    """(npath, n) index matrix built from contiguous runs of length `block`."""
    nb = int(math.ceil(n / block))
    starts = rng.integers(0, max(size - block, 1), size=(npath, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(npath, nb * block)
    return idx[:, :n]


def simulate_paths(nets, s, n, npath, block, seed):
    """Wall-crossing over block-resampled paths; block=0 means iid (the old way)."""
    a = np.asarray(nets, float)
    rng = np.random.default_rng(seed)
    pass_n = death_n = 0
    maxdd = []
    step = 200
    for lo in range(0, npath, step):
        m = min(step, npath - lo)
        if block <= 1:
            idx = rng.integers(0, a.size, size=(m, n))
        else:
            idx = path_blocks(rng, n, m, block, a.size)
        L = np.cumsum(np.log1p(s * a[idx] / 100.0), axis=1)
        up, dn = L >= A, L <= -B
        fu = np.where(up.any(1), up.argmax(1), n + 1)
        fd = np.where(dn.any(1), dn.argmax(1), n + 1)
        pass_n += int((fu < fd).sum())
        death_n += int((fd < fu).sum())
        maxdd.append(float(np.min(L, axis=1).min()))
    return pass_n / npath, death_n / npath, min(maxdd)


def main():
    t0 = time.time()
    entries, sr, tolls = series_and_entries()
    pace = len(entries) / 183.0
    print(f"cheap pairs {len(sr)}  entries {len(entries):,}  pace {pace:.1f}/day  "
          f"({time.time()-t0:.0f}s)")

    exits = {
        "tight 1.0/0.2/0.08": (1.0, 0.2, 0.08, 0.0),
        "mid   1.5/0.2/0.08": (1.5, 0.2, 0.08, 0.0),
        "ship  2.0/0.4/0.15": (2.0, 0.4, 0.15, 0.0),
    }
    out = {"pace": round(pace, 2), "entries": len(entries), "exits": {}}
    for label, cfg in exits.items():
        nets, _ts, holds, capped = TS.simulate(entries, sr, tolls, *cfg)
        a = np.asarray(nets, float)
        # the tape's own worst run and worst stretch, in log terms at 1%
        sgn = np.sign(a)
        run = worst = 0
        for v in sgn:
            run = run + 1 if v < 0 else 0
            worst = max(worst, run)
        tau = float(np.mean(a) / np.var(a, ddof=1)) if np.var(a) > 0 else 0.0
        print(f"\n{label}: n={len(a):,} win {100*(a>0).mean():.1f}% "
              f"e {a.mean():+.4f}% sd {a.std(ddof=1):.4f}% "
              f"worst loss run {worst} | capped {capped} | {time.time()-t0:.0f}s")
        row = {"n": len(a), "win_pct": round(100 * float((a > 0).mean()), 1),
               "mean": round(float(a.mean()), 4),
               "sd": round(float(a.std(ddof=1)), 4),
               "worst_loss_run": int(worst), "capped": int(capped),
               "theta_iid": round(2 * float(a.mean()) / float(a.std(ddof=1)) ** 2, 1)
               if a.std(ddof=1) > 0 else None,
               "theta_trade": round(tau, 4), "cells": {}}
        for size in (0.01, 0.02, 0.05):
            for days in (180, 365):
                n = int(pace * days)
                p_i, d_i, dd_i = simulate_paths(nets, size, n, 1500, 0, seed=11)
                cells = {"iid": {"pass": round(p_i, 4), "death": round(d_i, 4),
                                 "worst_logpath": round(dd_i, 4)}}
                for L in (20, 50, 200):
                    p_b, d_b, dd_b = simulate_paths(nets, size, n, 1500, L, seed=11)
                    cells[f"block{L}"] = {"pass": round(p_b, 4), "death": round(d_b, 4),
                                          "worst_logpath": round(dd_b, 4)}
                row["cells"][f"{size*100:g}%|{days}d"] = cells
                txt = "  ".join(f"{k}: {v['pass']*100:5.1f}/{v['death']*100:4.1f}"
                                for k, v in cells.items())
                print(f"  {size*100:>3g}% {days:>3}d  pass/death%  {txt}")
        out["exits"][label] = row

    json.dump(out, open(OUT, "w"), indent=1)
    print("\nwrote", OUT, f"({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
