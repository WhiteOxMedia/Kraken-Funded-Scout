"""Horizon P(pass) for the sweep's leading exits, at sizes and tolls that can finish.

The sweep's own horizon column capped trades at 20,000, which is fewer than the ~13,000
needed at 1% and far fewer than a year's pace, so it read 0% for every cell. This
recomputes it honestly: uncapped trade counts, 1/2/5% tickets, and both the assumed
0.08% toll and the measured 0.021% cheap-pair toll.
"""
from __future__ import annotations
import json, math, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
from trendride_study import load, prep
import scratch.exit_theta_sweep as S

CASES = [(2.0, 0.2, 0.10), (1.5, 0.2, 0.10), (3.0, 0.2, 0.10),
         (2.0, 0.4, 0.10), (2.0, 0.4, 0.15)]
A, B = S.A, S.B
PER_DAY, PATHS = 87, 1500

def mc(nets, s, n, seed=3):
    arr = np.asarray(nets, float); rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(PATHS, min(n, 60000)))
    L = np.cumsum(np.log1p(s * arr[idx] / 100.0), axis=1)
    up, dn = L >= A, L <= -B
    fu = np.where(up.any(1), up.argmax(1), L.shape[1] + 1)
    fd = np.where(dn.any(1), dn.argmax(1), L.shape[1] + 1)
    return float((fu < fd).mean()), float((fd < fu).mean())

def main():
    entries = S.build_entries(); d = load(); series = {}
    for sym, bars in d["pairs"].items():
        p = prep(bars)
        series[sym] = {"h": p["h"], "l": p["l"], "c": p["c"],
                       "index": {t: i for i, t in enumerate(p["ts"])}}
    out = {}
    print(f"{'exit':>14} {'toll':>7} {'size':>5} {'days':>5} {'pass':>6} {'death':>6}")
    for st_, ar, gv in CASES:
        nets, *_ = S.simulate(entries, series, st_, ar, gv)
        for toll, shift in (("0.08", 0.0), ("0.021", 0.059)):
            arr = [x + shift for x in nets]
            for s in (0.01, 0.02, 0.05):
                for days in (120, 180, 365):
                    n = int(PER_DAY * days)
                    p, dth = mc(arr, s, n)
                    out[f"{st_}/{ar}/{gv}|{toll}|{s:g}|{days}"] = {
                        "pass": round(p, 3), "death": round(dth, 3), "n": n}
                    print(f"{st_}/{ar}/{gv:>4} {toll:>7} {s*100:>4g}% {days:>5} "
                          f"{p*100:>5.1f}% {dth*100:>5.1f}%")
    json.dump(out, open(os.path.join(HERE, "_exit_finalists.json"), "w"), indent=1)
    print("wrote _exit_finalists.json")

main()
