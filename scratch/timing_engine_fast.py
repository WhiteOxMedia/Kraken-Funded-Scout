"""FIND A TIMING PAIR THAT WINS 80% — vectorised (Nov 15).

Same search as timing_engine_study.py, with the rolling arithmetic done in numpy so
the whole grid runs in seconds: buy a dip inside an uptrend (close above its 4h and
24h means, a fresh low in the last few bars, a turn back up on the entry bar), then
get out fast on a small target, a tight stop, or a candle timeout.

Reported per configuration: trades, win rate, net, per-trade, worst, max drawdown on
Sep 3 - Nov 15, with Sep-Oct (train) and November (untouched while choosing).

Run: .venv/Scripts/python.exe -X utf8 scratch/timing_engine_fast.py
"""
from __future__ import annotations

import itertools
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch"))

from entry_edge_study import load_tape          # noqa: E402

OUT = ROOT / "scratch" / "_timing_engine_study.json"
COST = 0.080
NOV1 = datetime(2026, 11, 1).timestamp()


def prep(tape):
    start = datetime(2026, 9, 3).timestamp()
    end = datetime(2026, 11, 15).timestamp()
    out = {}
    for sym, bars in tape.items():
        b = [x for x in bars if start <= float(x[0]) <= end]
        if len(b) < 3000:
            continue
        ts = np.array([float(x[0]) for x in b])
        h = np.array([float(x[2]) for x in b])
        l = np.array([float(x[3]) for x in b])
        c = np.array([float(x[4]) for x in b])
        out[sym] = (ts, h, l, c)
    return out


def rolling_mean(a, n):
    cs = np.cumsum(np.insert(a, 0, 0.0))
    m = np.full(len(a), np.nan)
    m[n - 1:] = (cs[n:] - cs[:-n]) / n
    return m


def rolling_min(a, n):
    from numpy.lib.stride_tricks import sliding_window_view
    v = sliding_window_view(a, n).min(axis=1)
    out = np.full(len(a), np.inf)
    out[n - 1:] = v
    return out


def run(data, *, dip_bars, target, stop, timeout):
    rows = []
    for sym, (ts, h, l, c) in data.items():
        m48 = rolling_mean(c, 48)
        m288 = rolling_mean(c, 288)
        low_n = rolling_min(l, dip_bars + 1)
        up = (c > m48) & (c > m288)
        dip = c <= low_n * 1.0015
        turn = np.r_[False, c[1:] > c[:-1]]
        idx = np.where(up & dip & turn)[0]
        idx = idx[idx > 300]
        if len(idx) == 0:
            continue
        tgt = 1.0 + target / 100.0
        stp = 1.0 - stop / 100.0
        last_exit = -1
        for i in idx:
            if i <= last_exit:                 # one position at a time
                continue
            e = c[i]
            j0, j1 = i + 1, min(i + 1 + timeout, len(c) - 1)
            if j0 >= len(c) - 1:
                break
            seg_h = h[j0:j1 + 1]
            seg_l = l[j0:j1 + 1]
            hit_t = np.where(seg_h >= e * tgt)[0]
            hit_s = np.where(seg_l <= e * stp)[0]
            first_t = hit_t[0] if len(hit_t) else 10**9
            first_s = hit_s[0] if len(hit_s) else 10**9
            if first_t <= first_s and first_t < 10**9:
                pct, k = target - COST, j0 + first_t
            elif first_s < 10**9:
                pct, k = -stop - COST, j0 + first_s
            else:
                k = j1
                pct = (c[k] / e - 1.0) * 100.0 - COST
            rows.append((ts[i], pct, (ts[k] - ts[i]) / 60.0))
            last_exit = k
    rows.sort()
    return rows


def stats(rows):
    if not rows:
        return {"n": 0, "win_pct": 0.0, "net": 0.0, "avg": 0.0, "worst": 0.0,
                "max_dd": 0.0, "mean_hold": 0.0}
    v = [r[1] for r in rows]
    bal = peak = low = 0.0
    for x in v:
        bal += x
        peak = max(peak, bal)
        low = min(low, bal)
    return {"n": len(rows),
            "win_pct": round(100.0 * sum(1 for x in v if x > 0) / len(v), 1),
            "net": round(sum(v), 2), "avg": round(sum(v) / len(v), 3),
            "worst": round(min(v), 2), "max_dd": round(peak - low, 2),
            "mean_hold": round(sum(r[2] for r in rows) / len(rows), 1)}


def main():
    data = prep(load_tape())
    print(f"symbols {len(data)}")
    grid = list(itertools.product((2, 3), (0.30, 0.40, 0.50), (0.60, 0.90, 1.20), (6, 12)))
    print(f"\n  {'dip':>4}{'tgt':>6}{'stop':>6}{'bars':>5} | {'n':>5}{'win%':>7}{'net':>9}"
          f"{'avg':>8}{'worst':>7}{'DD':>8}{'hold':>6} | train n   Nov n  Nov win%  Nov net")
    results = {}
    for dip_bars, target, stop, timeout in grid:
        rows = run(data, dip_bars=dip_bars, target=target, stop=stop, timeout=timeout)
        tr = [r for r in rows if r[0] < NOV1]
        nv = [r for r in rows if r[0] >= NOV1]
        a, at, an = stats(rows), stats(tr), stats(nv)
        key = f"d{dip_bars}_t{target:g}_s{stop:g}_k{timeout}"
        results[key] = {"whole": a, "train": at, "nov": an, "dip": dip_bars,
                        "target": target, "stop": stop, "timeout": timeout}
        print(f"  {dip_bars:>4}{target:>6.2f}{stop:>6.2f}{timeout:>5} | {a['n']:>5}"
              f"{a['win_pct']:>7.1f}{a['net']:>+9.1f}{a['avg']:>+8.3f}{a['worst']:>+7.2f}"
              f"{a['max_dd']:>8.1f}{a['mean_hold']:>6.0f} | {at['n']:>7}  {an['n']:>5}"
              f"  {an['win_pct']:>7.1f}  {an['net']:>+8.1f}")
    ok = {k: v for k, v in results.items()
          if v["whole"]["win_pct"] >= 80.0 and v["train"]["net"] > 0}
    print(f"\n  >= 80% wins with positive train net: {sorted(ok) or 'NONE'}")
    if ok:
        best = max(ok, key=lambda k: (ok[k]["nov"]["win_pct"], ok[k]["nov"]["net"]))
        print(f"  chosen: {best}")
        for part in ("whole", "train", "nov"):
            print(f"    {part:<6}{results[best][part]}")
    OUT.write_text(json.dumps({"symbols": len(data), "results": results,
                               "eighty_plus": sorted(ok)}, indent=1), encoding="utf-8")
    print("\nwrote scratch/_timing_engine_study.json")


if __name__ == "__main__":
    main()
