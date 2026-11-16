"""drift_gate.py — suspend on measured drift instead of on win rate.

The shipped suspension is a win-rate read (rolling < 70%) plus a streak counter (8
losers -> 24h). Both are proxies for the thing that matters: whether the next trade has
positive expectation. This measures the direct object — a rolling per-trade mean, or its
t-stat — and asks one question: does it make November non-negative without eating the
May-October edge?

The gate is path-dependent and honest: it sees only the trades it actually took, in
entry order. A skipped signal updates nothing. Three families:

    mean     suspend while the rolling mean of the last N taken trades is < 0
    tstat    suspend while the rolling t of those N is < 0 (mean not distinguishable
             from zero)
    winrate  the shipped rule: rolling win% < 70 over 50, or 8 straight losers -> 24h

Plus an ORACLE that is allowed to skip every trade dated in November, which is the
ceiling any causal gate could reach, and the ungated baseline.

    python scratch/drift_gate.py
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from trendride_study import NOV1, load, prep
import scratch.exit_theta_sweep as OLD
import scratch.exit_take_sweep as NEW

OUT = os.path.join(HERE, "_drift_gate.json")


def series_shipped():
    """Shipped exit, all 30 pairs, the 0.08 toll — the tape that gives -0.1294% Nov."""
    entries = OLD.build_entries()
    d = load()
    sr = {}
    for sym, bars in d["pairs"].items():
        p = prep(bars)
        sr[sym] = {"h": p["h"], "l": p["l"], "c": p["c"],
                   "index": {t: i for i, t in enumerate(p["ts"])}}
    nets, tss, _h, _c = OLD.simulate(entries, sr, 2.0, 0.4, 0.15)
    return nets, tss, "shipped 2.0/0.4/0.15, all pairs, 0.08 toll"


def series_best():
    """Best-theta exit on the cheap pairs with measured tolls."""
    tolls = NEW.tokens()
    entries = [e for e in json.load(open(NEW.ENTRIES)) if e[0] in tolls]
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
    nets, tss, _h, _c = NEW.simulate(entries, sr, tolls, 1.0, 0.2, 0.08, 0.0)
    return nets, tss, "best 1.0/0.2/0.08, cheap pairs, measured tolls"


def run_gate(nets, tss, rule: str, n: int, *, mode: str = "shadow",
             cooldown_h: float = 24.0, margin: float = 0.0):
    """Walk the trades in entry order, deciding to take each one.

    The first cut updated its window only on TAKEN trades, so a negative window froze
    and suspended forever (50 trades then nothing). Two live-implementable repairs:

      shadow    keep the window fed by the outcomes of skipped signals too - that needs
                no capital, the same trick the timing_shadow loop already uses - and
                take only when the window is non-negative.
      cooldown  the window is blind while suspended (no capital, no data), so a trip
                suspends for a fixed time and then resumes blind.

    Either way the decision only ever uses trades that closed before the signal.
    """
    from collections import deque
    win = deque(maxlen=n)
    taken, streak = [], 0
    suspended_until = -1.0

    def bad_now():
        if len(win) < n:
            return False
        m = sum(win) / len(win)
        if rule == "mean":
            return m < -margin
        sd = float(np.std(win))
        return (m / (sd / math.sqrt(len(win))) if sd > 0 else 0.0) < 0.0

    for x, t in zip(nets, tss):
        if rule == "winrate":
            if t < suspended_until:
                continue
            if len(win) >= 50:
                w = sum(1 for y in win if y > 0) / len(win)
                if w < 0.70 or streak >= 8:
                    suspended_until = t + cooldown_h * 3600
                    streak = 0
                    win.clear()        # a trip restarts the read, else it halts forever
                    continue
            taken.append((x, t))
            win.append(x)
            streak = streak + 1 if x <= 0 else 0
            continue

        if t < suspended_until:
            if mode == "shadow":
                win.append(x)          # hypothetical outcome: no capital was used
            continue
        if suspended_until > 0 and t >= suspended_until and not win:
            suspended_until = -1.0
        if bad_now():
            suspended_until = t + cooldown_h * 3600
            if mode == "cooldown":
                win.clear()            # starved window: resume with a fresh read
            elif mode == "shadow":
                win.append(x)
            continue
        taken.append((x, t))
        win.append(x)
    return taken


def f4(x):
    return "n/a" if x is None else f"{x:+.4f}"


def stats(taken):
    if not taken:
        return {"n": 0}
    a = np.asarray([x for x, _ in taken])
    t = np.asarray([tt for _, tt in taken])
    nov = a[t >= NOV1]
    train = a[t < NOV1]
    return {"n": len(a), "win_pct": round(100.0 * float((a > 0).mean()), 1),
            "mean_pct": round(float(a.mean()), 4),
            "train_mean": round(float(train.mean()), 4) if train.size else None,
            "train_n": int(train.size),
            "nov_mean": round(float(nov.mean()), 4) if nov.size else None,
            "nov_n": int(nov.size),
            "total_pct": round(float(a.sum()), 1)}


def main():
    out = {}
    for label, (nets, tss, desc) in (("shipped", series_shipped()),
                                     ("best", series_best())):
        print(f"\n{label.upper()}  {desc}   n={len(nets):,}")
        base = stats([(x, t) for x, t in zip(nets, tss)])
        print(f"  {'baseline (no gate)':<28} n={base['n']:>5}  win {base['win_pct']:>5.1f}%  "
              f"mean {base['mean_pct']:+.4f}%  train {f4(base['train_mean'])}%  "
              f"Nov {f4(base['nov_mean'])}%")
        rows = {"baseline": base}
        for n in (50, 100, 200, 400):
            for rule in ("mean", "tstat"):
                got = run_gate(nets, tss, rule, n, mode="shadow")
                s = stats(got)
                key = f"{rule}_{n}_shadow"
                rows[key] = s
                print(f"  {key:<28} n={s['n']:>5}  win {s['win_pct']:>5.1f}%  "
                      f"mean {s['mean_pct']:+.4f}%  train {s['train_mean']:+.4f}%  "
                      f"Nov {f4(s['nov_mean'])}%  (Nov n {s['nov_n']})")
        for cd in (24.0, 72.0):
            got = run_gate(nets, tss, "mean", 100, mode="cooldown", cooldown_h=cd)
            s = stats(got)
            key = f"mean_100_cool{cd:g}h"
            rows[key] = s
            print(f"  {key:<28} n={s['n']:>5}  win {s['win_pct']:>5.1f}%  "
                  f"mean {s['mean_pct']:+.4f}%  train {s['train_mean']:+.4f}%  "
                  f"Nov {f4(s['nov_mean'])}%  (Nov n {s['nov_n']})")
        for margin in (0.02, 0.05):
            got = run_gate(nets, tss, "mean", 100, mode="shadow", margin=margin)
            s = stats(got)
            key = f"mean_100_shadow_m{margin:g}"
            rows[key] = s
            print(f"  {key:<28} n={s['n']:>5}  win {s['win_pct']:>5.1f}%  "
                  f"mean {s['mean_pct']:+.4f}%  train {f4(s['train_mean'])}%  "
                  f"Nov {f4(s['nov_mean'])}%  (Nov n {s['nov_n']})")
        for rule, tag in (("winrate", "win70_50"), ("winrate", "win70_50_close")):
            pass
        wr = stats(run_gate(nets, tss, "winrate", 50))
        rows["winrate_shipped_rule"] = wr
        print(f"  {'winrate+8streak (shipped)':<28} n={wr['n']:>5}  "
              f"win {wr['win_pct']:>5.1f}%  mean {wr['mean_pct']:+.4f}%  "
              f"train {f4(wr['train_mean'])}%  Nov {f4(wr['nov_mean'])}%")
        orc = stats([(x, t) for x, t in zip(nets, tss) if t < NOV1])
        rows["oracle_skip_november"] = orc
        print(f"  {'ORACLE skip November':<28} n={orc['n']:>5}  "
              f"mean {orc['mean_pct']:+.4f}%  train {f4(orc['train_mean'])}%  "
              f"Nov {orc['nov_mean']} (0 trades)")
        out[label] = {"desc": desc, "rows": rows}

        # the question, stated as a number
        for k, v in rows.items():
            if v.get("nov_mean") is not None and v["nov_mean"] >= 0 and v["train_mean"]:
                print(f"  >>> {k} makes November non-negative ({v['nov_mean']:+.4f}%) "
                      f"at train {v['train_mean']:+.4f}% vs baseline "
                      f"{base['train_mean']:+.4f}%")

    json.dump(out, open(OUT, "w"), indent=1)
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
