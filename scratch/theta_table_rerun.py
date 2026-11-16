"""theta_table_rerun.py — the exit ranking and the death table at the TABLE's tolls.

The 240-cell theta sweep charged each cheap pair the two-quote snapshot in
`_cost_live.json`. The sampler has since replaced those numbers with day means, and
several moved enough to matter (ONDO 0.0066 -> 0.0912, ETH 0.0064 -> 0.0005, WLD's
today print 0.0944). This re-runs the same 240 cells and the same block bootstrap with
`spread_table` as the source of truth, so the ranking reflects what the pairs charge now.

    python scratch/theta_table_rerun.py
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

from trendride_study import NOV1, load, prep
import spread_table as ST
import scratch.exit_take_sweep as TS
import scratch.block_bootstrap as BB

OUT = os.path.join(HERE, "_theta_table.json")


def table_tolls(snapshot_tolls):
    """The same cheap universe, repriced from the live table (avg, else day, else snap)."""
    out = {}
    for sym, snap in snapshot_tolls.items():
        row = (ST.load_table().get("pairs") or {}).get(sym) or {}
        pct = row.get("avg_pct") or row.get("day_pct") or snap
        out[sym] = float(pct)
    return out


def theta(e, sd, s):
    mu, sig = s * e / 100.0, s * sd / 100.0
    return (2 * mu / sig ** 2) if sig > 0 else 0.0


def main():
    t0 = time.time()
    snap_tolls = TS.tokens()
    tolls = table_tolls(snap_tolls)
    print(f"cheap pairs {len(tolls)}  toll sum snapshot {sum(snap_tolls.values()):.4f} "
          f"-> table {sum(tolls.values()):.4f}  ({time.time()-t0:.0f}s)")

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
    pace = len(entries) / 183.0
    print(f"entries {len(entries):,}  pace {pace:.1f}/day  ({time.time()-t0:.0f}s)")

    cells = []
    for stop in TS.STOPS:
        for arm in TS.ARMS:
            for give in TS.GIVES:
                for take in TS.TAKES:
                    nets, tss, holds, capped = TS.simulate(entries, sr, tolls,
                                                           stop, arm, give, take)
                    a = np.asarray(nets, float)
                    tt = np.asarray(tss)
                    nov = a[tt >= NOV1]
                    sd = float(a.std(ddof=1))
                    e = float(a.mean())
                    cells.append({
                        "stop": stop, "arm": arm, "give": give, "take": take,
                        "n": len(a), "win": round(100 * float((a > 0).mean()), 1),
                        "e": round(e, 4), "sd": round(sd, 4),
                        "theta1": round(theta(e, sd, 0.01), 1),
                        "theta2": round(theta(e, sd, 0.02), 1),
                        "nov": round(float(nov.mean()), 4) if nov.size else None,
                        "hold": round(float(np.mean(holds)), 2),
                        "capped": capped, "nets": a.tolist(),
                    })
        print(f"  stops {stop} done ({time.time()-t0:.0f}s)")

    cells.sort(key=lambda c: -c["theta1"])
    print(f"\nTOP 12 OF {len(cells)} BY THETA (1% ticket, table tolls)")
    print("   stop  arm  give  take   win%       e%     sd%   theta1  theta2     Nov%  hold")
    for c in cells[:12]:
        print(f"  {c['stop']:>5.1f} {c['arm']:>4.1f} {c['give']:>5.2f} {c['take']:>5.1f} "
              f"{c['win']:>6.1f} {c['e']:>+8.4f} {c['sd']:>7.4f} {c['theta1']:>8.0f} "
              f"{c['theta2']:>7.0f} {c['nov'] if c['nov'] is not None else 0:>+8.4f} "
              f"{c['hold']:>5.2f}")

    def find(stop, arm, give, take=0.0):
        for i, c in enumerate(cells, 1):
            if (c["stop"], c["arm"], c["give"], c["take"]) == (stop, arm, give, take):
                return i, c
        return None, None

    rank, win_cell = find(1.0, 0.2, 0.08)
    srank, ship_cell = find(2.0, 0.4, 0.15)
    print(f"\nTHE TIGHT WINNER 1.0/0.2/0.08 (no take): ranks {rank} of {len(cells)}, "
          f"theta {win_cell['theta1']}, e {win_cell['e']:+.4f}%, sd {win_cell['sd']:.4f}%")
    print(f"SHIPPED 2.0/0.4/0.15 (no take):          ranks {srank} of {len(cells)}, "
          f"theta {ship_cell['theta1']}, e {ship_cell['e']:+.4f}%, sd {ship_cell['sd']:.4f}%")

    best_per_take = {}
    for take in TS.TAKES:
        cand = [c for c in cells if c["take"] == take][:1]
        if cand:
            c = cand[0]
            best_per_take[take] = c
            print(f"  best take {take:>3.1f}: {c['stop']:.1f}/{c['arm']:.1f}/{c['give']:.2f}"
                  f"  theta {c['theta1']:.0f}  e {c['e']:+.4f}%  Nov {c['nov']:+.4f}%")

    # ── the block bootstrap on the same tolls ─────────────────────────
    picks = [(f"{c['stop']:.1f}/{c['arm']:.1f}/{c['give']:.2f}/t{c['take']:g}", c)
             for c in cells[:5]]
    picks.append(("SHIPPED 2.0/0.4/0.15", ship_cell))
    print("\nBLOCK BOOTSTRAP (table tolls) — pass/death %")
    rows = {}
    for label, c in picks:
        nets = c["nets"]
        rows[label] = {}
        for size in (0.01, 0.02, 0.05):
            for days in (180, 365):
                n = int(pace * days)
                p_i, d_i, _ = BB.simulate_paths(nets, size, n, 1500, 0, seed=11)
                cell = {"iid": [round(p_i, 4), round(d_i, 4)]}
                for L in (20, 50, 200):
                    p_b, d_b, _ = BB.simulate_paths(nets, size, n, 1500, L, seed=11)
                    cell[f"block{L}"] = [round(p_b, 4), round(d_b, 4)]
                rows[label][f"{size*100:g}%|{days}d"] = cell
                txt = "  ".join(f"{k} {v[0]*100:5.1f}/{v[1]*100:4.1f}" for k, v in cell.items())
                print(f"  {label:<26} {size*100:>3g}% {days:>3}d  {txt}")

    json.dump({"tolls": tolls, "snapshot_tolls": snap_tolls, "pace": pace,
               "entries": len(entries),
               "cells": [{k: v for k, v in c.items() if k != "nets"} for c in cells],
               "rank_tight": rank, "rank_shipped": srank,
               "bootstrap": rows},
              open(OUT, "w"), indent=1)
    print("\nwrote", OUT, f"({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
