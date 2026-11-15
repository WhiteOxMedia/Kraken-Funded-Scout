"""trendride_verdict.py — is the 82%-win config real, or a two-way-split coincidence?

The refine grid produced a shape that is positive on train AND November:
e3_band (lower-band pullback in an uptrend), stop 2.0%, trail arms at +0.4%,
gives back 0.15%, no trend-break exit -> 82.3% wins, train +0.017%, Nov +0.038%.

A two-way split is a weak test: fit nothing, but still easy to get lucky. This dumps
the full monthly record, the November t-stat, the loss streak, and an equity curve
sized like the contest ($2,000 a trade against the $300 cushion) so the death-line
question is answered in dollars rather than percentages.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trendride_study import COST, NOV1, load, prep, score, simulate

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_trendride_verdict.json")
SIZE = 2000.0          # dollars per trade, the contest's usual ticket
CUSHION = 300.0        # the death-line cushion

CANDIDATES = [
    ("e3_band", 2.0, 0.4, 0.15),
    ("e3_band", 2.5, 0.4, 0.15),
    ("e3_band", 1.5, 0.4, 0.15),
    ("e3_band", 3.0, 0.25, 0.15),
]


def month_of(ts):
    return dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m")


def main():
    d = load()
    prepped = {sym: prep(bars) for sym, bars in d["pairs"].items()}

    report = {}
    for (var, stop, act, give) in CANDIDATES:
        key = f"{var}|S{stop}|A{act}|G{give}"
        trades = []
        for sym, p in prepped.items():
            trades += simulate(p, p["entries"][var], stop, act, give, False)
        trades.sort(key=lambda t: t[0])

        months = {}
        for t in trades:
            months.setdefault(month_of(t[0]), []).append(t[1])
        monthly = {m: {"n": len(v), "win": round(100.0 * sum(1 for x in v if x > 0) / len(v), 1),
                       "net": round(st.fmean(v), 4), "total_pct": round(sum(v), 1)}
                   for m, v in sorted(months.items())}

        nov = [t[1] for t in trades if t[0] >= NOV1]
        allnets = [t[1] for t in trades]
        # month-by-month sign consistency
        pos_months = sum(1 for m in monthly.values() if m["net"] > 0)

        # losing streak
        streak = worst_streak = 0
        for x in allnets:
            if x <= 0:
                streak += 1
                worst_streak = max(worst_streak, streak)
            else:
                streak = 0

        # equity in dollars, one ticket at a time
        eq = 0.0
        peak = 0.0
        maxdd = 0.0
        for x in allnets:
            eq += SIZE * x / 100.0
            peak = max(peak, eq)
            maxdd = max(maxdd, peak - eq)

        report[key] = {
            "overall": score(trades),
            "november": score([(t[0], t[1], t[2]) for t in trades if t[0] >= NOV1]),
            "monthly": monthly,
            "positive_months": f"{pos_months}/{len(monthly)}",
            "worst_loss_streak": worst_streak,
            "equity_usd": round(eq, 2),
            "max_drawdown_usd": round(maxdd, 2),
            "cushion_breached": maxdd > CUSHION,
        }

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)

    for key, r in report.items():
        o, nv = r["overall"], r["november"]
        print(f"\n=== {key} ===")
        print(f"  all: n={o['n']} win={o['win_pct']}% net={o['mean_net_pct']:+.4f}%/trade "
              f"total={o['total_net_pct']:+.1f}% hold={o['median_hold_h']}h "
              f"worst={o['worst']:+.2f}% best={o['best']:+.2f}%")
        print(f"  nov: n={nv['n']} win={nv['win_pct']}% net={nv['mean_net_pct']:+.4f}% "
              f"t={nv['t']} total={nv['total_net_pct']:+.1f}%")
        print(f"  equity ${r['equity_usd']:+.2f} on ${SIZE:.0f} tickets | "
              f"max DD ${r['max_drawdown_usd']:.2f} | cushion ${CUSHION:.0f} "
              f"{'BREACHED' if r['cushion_breached'] else 'held'}")
        print(f"  positive months {r['positive_months']} | worst loss streak {r['worst_loss_streak']}")
        for m, v in r["monthly"].items():
            flag = "+" if v["net"] > 0 else "-"
            print(f"    {m}  n={v['n']:5d} win={v['win']:5.1f}% net={v['net']:+.4f}% {flag}")

    print("\n=== survivors: win>=80% overall, positive train AND November, monthly consistent ===")
    keep = []
    for key, r in report.items():
        o, nv = r["overall"], r["november"]
        pos = int(r["positive_months"].split("/")[0])
        tot = int(r["positive_months"].split("/")[1])
        if o["win_pct"] >= 80 and nv["mean_net_pct"] > 0 and pos >= tot - 1:
            keep.append(key)
            print(f"  CLEARS: {key}  win {o['win_pct']}%  nov {nv['mean_net_pct']:+.4f}% "
                  f"(t={nv['t']})  months {r['positive_months']}")
    if not keep:
        print("  NONE clears all three")
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
