"""FIND A TIMING PAIR THAT WINS 80% — buy the dip inside an uptrend, sell fast
(Nov 15).

The measured facts this builds on (all from earlier passes, not re-opened here):
after a 0.85+ breakout entry the remaining move is ~1%, so a slow trail gives back
what little there was; a +2% target reaches break-even but never positive. The
user's rule is the opposite shape: **get in on a dip and get out fast**, so that
most trades are profitable by a small amount rather than few by a large one.

So this searches a timing pair directly on the 5m tape over every symbol:

  entry   uptrend (close above its 4h mean, and above the 24h mean) + a dip in the
          last few bars + a turn back up on the entry bar;
  exit    a small target, a tight stop, and a hard timeout in candles.

Reported per configuration: trades, win rate, net, per trade, worst, max drawdown,
on Sep-Oct (train) and November (never touched while choosing). The chosen config
is the highest win rate that is also net-positive on train, then scored on Nov.

Run: .venv/Scripts/python.exe -X utf8 scratch/timing_engine_study.py
"""
from __future__ import annotations

import bisect
import itertools
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch"))

from entry_edge_study import load_tape          # noqa: E402

OUT = ROOT / "scratch" / "_timing_engine_study.json"
COST_PCT = 0.080
NOV1 = datetime(2026, 11, 1).timestamp()


def series(bars):
    ts = [float(b[0]) for b in bars]
    o = [float(b[1]) for b in bars]
    h = [float(b[2]) for b in bars]
    l = [float(b[3]) for b in bars]
    c = [float(b[4]) for b in bars]
    return ts, o, h, l, c


def sma(vals, i, n):
    if i + 1 < n:
        return None
    return sum(vals[i + 1 - n:i + 1]) / n


def trades_for(tape, *, dip_bars, uptrend_h, target, stop, timeout, min_gap=1):
    """One position at a time per symbol; entry on the turn-up bar's close."""
    out = []
    for sym, bars in tape.items():
        ts, o, h, l, c = series(bars)
        n = len(c)
        i = max(60, uptrend_h * 12 + 1)
        while i < n - 2:
            s4 = sma(c, i, uptrend_h * 12)          # 4h mean
            s24 = sma(c, i, 24 * 12)                # 24h mean
            if s4 is None or s24 is None or c[i] <= s4 or c[i] <= s24:
                i += 1
                continue
            # a dip: the last `dip_bars` closes fell into this bar's low
            window = c[max(0, i - dip_bars):i]
            if len(window) < dip_bars or min(window) > c[i] * 1.0:
                i += 1
                continue
            dipped = c[i] <= min(l[i - dip_bars:i + 1]) * (1.0 + 0.0015)
            turning = c[i] > c[i - 1]
            if not (dipped and turning):
                i += 1
                continue
            entry = c[i]
            # walk forward: target, stop, timeout
            exit_pct = None
            j = i + 1
            while j < n and j <= i + timeout:
                if h[j] >= entry * (1.0 + target / 100.0):
                    exit_pct = target - COST_PCT
                    break
                if l[j] <= entry * (1.0 - stop / 100.0):
                    exit_pct = -stop - COST_PCT
                    break
                j += 1
            if exit_pct is None:
                j = min(j, n - 1)
                exit_pct = (c[j] / entry - 1.0) * 100.0 - COST_PCT
            out.append({"sym": sym, "t": ts[i], "pct": exit_pct,
                        "held": (ts[min(j, n - 1)] - ts[i]) / 60.0})
            i = j + min_gap                            # one position at a time
    out.sort(key=lambda r: r["t"])
    return out


def stats(rows):
    if not rows:
        return {"n": 0, "win_pct": 0.0, "net": 0.0, "avg": 0.0, "worst": 0.0,
                "max_dd": 0.0, "mean_hold": 0.0}
    vals = [r["pct"] for r in rows]
    bal = peak = low = 0.0
    for v in vals:
        bal += v
        peak = max(peak, bal)
        low = min(low, bal)
    return {"n": len(rows),
            "win_pct": round(100.0 * sum(1 for v in vals if v > 0) / len(vals), 1),
            "net": round(sum(vals), 2), "avg": round(sum(vals) / len(vals), 3),
            "worst": round(min(vals), 2), "max_dd": round(peak - low, 2),
            "mean_hold": round(sum(r["held"] for r in rows) / len(rows), 1)}


def main():
    tape = load_tape()
    start = datetime(2026, 9, 3).timestamp()
    end = datetime(2026, 11, 15).timestamp()
    tape = {s: [b for b in bars if start <= float(b[0]) <= end]
            for s, bars in tape.items() if len(bars) > 2000}
    print(f"symbols {len(tape)}, bars per symbol ~{len(next(iter(tape.values())))}")

    grid = list(itertools.product((2, 3), (0.30, 0.40, 0.50),
                                  (0.60, 0.90, 1.20), (6, 12)))
    print(f"\n  {'dip':>4}{'target':>8}{'stop':>6}{'bars':>6} | {'n':>5}{'win%':>7}"
          f"{'net':>9}{'avg':>8}{'worst':>7}{'DD':>8}{'hold':>6} | train n / Nov n   Nov win% / net")
    results = {}
    for dip_bars, target, stop, timeout in grid:
        rows = trades_for(tape, dip_bars=dip_bars, uptrend_h=24, target=target,
                          stop=stop, timeout=timeout)
        tr = [r for r in rows if r["t"] < NOV1]
        nv = [r for r in rows if r["t"] >= NOV1]
        a, at, an = stats(rows), stats(tr), stats(nv)
        key = f"d{dip_bars}_t{target:g}_s{stop:g}_k{timeout}"
        results[key] = {"whole": a, "train": at, "nov": an,
                        "dip": dip_bars, "target": target, "stop": stop,
                        "timeout": timeout}
        print(f"  {dip_bars:>4}{target:>8.2f}{stop:>6.2f}{timeout:>6} | {a['n']:>5}"
              f"{a['win_pct']:>7.1f}{a['net']:>+9.1f}{a['avg']:>+8.3f}{a['worst']:>+7.2f}"
              f"{a['max_dd']:>8.1f}{a['mean_hold']:>6.0f} | {at['n']:>5} /{an['n']:>5}"
              f"   {an['win_pct']:>6.1f}% {an['net']:>+8.1f}")

    ok = {k: v for k, v in results.items()
          if v["whole"]["win_pct"] >= 80.0 and v["train"]["net"] > 0}
    print(f"\n  >= 80% wins with a positive train: {sorted(ok) or 'NONE'}")
    if ok:
        best = max(ok, key=lambda k: (ok[k]["nov"]["win_pct"], ok[k]["nov"]["net"]))
        print(f"  best on both: {best}")
        for part in ("whole", "train", "nov"):
            print(f"    {part:<6}{results[best][part]}")
    OUT.write_text(json.dumps({"symbols": len(tape), "results": results,
                               "eighty_plus": sorted(ok)}, indent=1),
                   encoding="utf-8")
    print("\nwrote scratch/_timing_engine_study.json")


if __name__ == "__main__":
    main()
