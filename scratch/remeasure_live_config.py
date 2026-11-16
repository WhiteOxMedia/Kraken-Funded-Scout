"""remeasure_live_config.py — the numbers the RUNNING code produces, not the study's.

STUDY was advertising the study's band (EMA20 mid, sd around the SMA) at 8,304 trades
/ 82.3% wins / November +0.0379%. The live engine implements `overwatch_timing.entry_ok`
with `band_lower` — the textbook SMA20 mid — called on a 320-bar window, and `manage`
with no hold cap. This script measures exactly that and nothing else:

    entry   ot.entry_ok(closes[-320:], ...)   per bar, as timing_shadow/timing_live call it
    exit    ot.manage(...) until the stop/trail fires, no hold cap
    cost    ot.net_pct (contest_account.COST_ROUNDTRIP_PCT)
    split   TRAIN May 14 - Oct 31   |   NOVEMBER Nov 1 - Nov 13
    paths   the same trades walked forward from the live balance at 1/2/5% tickets,
            priced by contest_account.sized_entry_usd, against death_floor.halt_line
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import overwatch_timing as ot
from trendride_study import NOV1, load, prep
from trendride_verdict import death_line_context, run_path

OUT = os.path.join(HERE, "_live_config_measure.json")
WINDOW = 320
SIZE_FRACS = (0.01, 0.02, 0.05)


def live_entries(p):
    """Bars where ot.entry_ok fires on the 320-bar window the live engine fetches.

    The filter is the live band itself (SMA mid), which entry_ok then re-checks, so
    no live entry can be skipped by the pre-filter.
    """
    o, h, l, c = p["o"], p["h"], p["l"], p["c"]
    n = len(c)
    found = []
    for i in range(201, n - 2):
        # cheap necessary condition: the bar must reach the lower SMA band
        win = c[i - 19:i + 1]
        mid = sum(win) / 20.0
        lower = mid - ot.BB_MULT * (sum((x - mid) ** 2 for x in win) / 20.0) ** 0.5
        if l[i] > lower:
            continue
        w = max(0, i + 1 - WINDOW)
        ok, _why = ot.entry_ok(c[w:i + 1], h[w:i + 1], l[w:i + 1], o[w:i + 1])
        if ok:
            found.append(i)
    return found


def trade(p, i):
    """One live trade: enter at the next open, run ot.manage with no hold cap."""
    o, h, l, c = p["o"], p["h"], p["l"], p["c"]
    n = len(c)
    j = i + 1
    if j >= n:
        return None
    entry = o[j]
    if entry <= 0:
        return None
    peak, stop = h[j], ot.initial_stop(entry)
    for k in range(j, n):
        peak, stop, exit_px, reason = ot.manage(entry=entry, peak=peak, stop=stop,
                                                bar_high=h[k], bar_low=l[k])
        if exit_px is not None:
            return (p["ts"][j], ot.net_pct(entry, exit_px), k - j + 1, reason)
    return (p["ts"][j], ot.net_pct(entry, c[-1]), n - j, "open")


def tstat(xs):
    if len(xs) < 2:
        return 0.0
    sd = st.pstdev(xs)
    return st.fmean(xs) / (sd / math.sqrt(len(xs))) if sd > 0 else 0.0


def stats(trades):
    if not trades:
        return {"n": 0}
    nets = [t[1] for t in trades]
    wins = sum(1 for x in nets if x > 0)
    return {
        "n": len(nets),
        "win_pct": round(100.0 * wins / len(nets), 1),
        "mean_net_pct": round(st.fmean(nets), 4),
        "total_net_pct": round(sum(nets), 1),
        "median_hold_h": round(st.median([t[2] for t in trades]) / 12.0, 2),
        "worst_pct": round(min(nets), 2),
        "best_pct": round(max(nets), 2),
        "t": round(tstat(nets), 2),
    }


def main():
    d = load()
    trades = []
    per_pair = {}
    for sym, bars in d["pairs"].items():
        p = prep(bars)
        idx = live_entries(p)
        got = [t for t in (trade(p, i) for i in idx) if t]
        per_pair[sym] = len(got)
        trades += got
    trades.sort(key=lambda t: t[0])

    train = [t for t in trades if t[0] < NOV1]
    nov = [t for t in trades if t[0] >= NOV1]

    months = {}
    for t in trades:
        m = dt.datetime.fromtimestamp(t[0], dt.timezone.utc).strftime("%Y-%m")
        months.setdefault(m, []).append(t[1])
    monthly = {m: {"n": len(v), "win": round(100 * sum(1 for x in v if x > 0) / len(v), 1),
                   "net": round(st.fmean(v), 4)} for m, v in sorted(months.items())}

    streak = worst = 0
    for t in trades:
        if t[1] <= 0:
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0

    ctx = death_line_context()
    flat = [(t[0], t[1], t[2]) for t in trades]   # run_path takes 3-tuples
    paths = {f"{int(f*100)}pct": run_path(flat, ctx, f) for f in SIZE_FRACS}

    out = {"config": "e3_band|SMA20 mid (live)|S2.0|A0.4|G0.15|window320|no cap",
           "window": WINDOW, "window_note": "the live window; the study used the full series",
           "all": stats(trades), "train": stats(train), "november": stats(nov),
           "monthly": monthly, "worst_loss_streak": worst,
           "positive_months": f"{sum(1 for v in monthly.values() if v['net'] > 0)}/{len(monthly)}",
           "entries_per_pair": per_pair,
           "death_context": ctx, "paths": paths}

    print(f"trades {out['all']['n']}  win {out['all']['win_pct']}%  "
          f"net {out['all']['mean_net_pct']:+.4f}%  t {out['all']['t']}")
    print(f"train   {out['train']}")
    print(f"nov     {out['november']}")
    print(f"months  {out['positive_months']} positive, worst streak {worst}")
    for m, v in monthly.items():
        print(f"   {m}  n={v['n']:5d} win={v['win']:5.1f}% net={v['net']:+.4f}%")
    print(f"\ndeath line ${ctx['death_line']:,.2f} | halt ${ctx['halt_line']:,.2f} | "
          f"balance ${ctx['balance']:,.2f}")
    for name, p in paths.items():
        print(f"  size {name}: profit ${p['profit_usd']:+9.2f} | lowest "
              f"${p['min_balance']:,.2f} | ${p['min_headroom_to_halt_usd']:+9.2f} "
              f"above halt {'HALTED' if p['halted'] else 'never halted'}")

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
