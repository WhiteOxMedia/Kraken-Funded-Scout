"""trendride_verdict.py — does the 82%-win shape DIE, on the repo's own death line?

Correction this file exists for: the death line is not a fixed buffer measured from
the current balance. It is `start_balance * (1 - MAX_LOSS_PCT/100)` = $9,700, it
never moves, and the room above it GROWS with profit. So the test is not "is the
peak-to-trough drawdown bigger than $300" — that question has no meaning. The test is:

    run the balance path forward, sized by the repo's own rule, and look at the
    lowest point it ever reaches. Is that point above the halt line ($9,850) and
    above the death line ($9,700)?

Everything here is imported from the existing code — `contest_account.sized_entry_usd`
(the ONE sizing rule, Nov 11), `ca.MAX_LOSS_PCT`, `death_floor.death_line/halt_line`.
No second copy of the arithmetic is defined in this file.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import contest_account as ca          # the ONE death-line + sizing source of truth
import death_floor as df
from trendride_study import COST, NOV1, load, prep, score, simulate

OUT = os.path.join(HERE, "_trendride_verdict.json")

# the candidate the refine grid produced, and the neighbours worth checking
CANDIDATES = [
    ("e3_band", 2.0, 0.4, 0.15),
    ("e3_band", 2.5, 0.4, 0.15),
    ("e3_band", 1.5, 0.4, 0.15),
    ("e3_band", 3.0, 0.25, 0.15),
]
SIZE_FRACS = (0.01, 0.02, 0.03, 0.05, 0.10)


def month_of(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m")


def death_line_context() -> dict:
    """The live numbers, from the repo's own account state."""
    st = ca.load_state()
    start = float(st["start_balance"])
    bal = float(st["current_balance"])
    return {"start": start, "balance": bal,
            "death_line": round(df.death_line(start), 2),
            "halt_line": round(df.halt_line(start), 2),
            "buffer_now": round(bal - df.death_line(start), 2)}


def run_path(nets, ctx, size_frac):
    """Walk the trades in time order, sizing each with the repo's own rule."""
    bal = ctx["balance"]
    start = ctx["start"]
    low = bal
    low_at = None
    halted = False
    dd_pct = float(getattr(ca, "DEFAULT_WINDOW_DD_PCT", 5.0))
    per_month = {}
    for ts, net, _bars in nets:
        notional = ca.sized_entry_usd(bal, dd_pct, start, size_frac)
        pnl = notional * net / 100.0
        bal += pnl
        m = month_of(ts)
        per_month[m] = round(per_month.get(m, 0.0) + pnl, 2)
        if bal < low:
            low, low_at = bal, ts
        if bal <= ctx["halt_line"]:
            halted = True
    return {
        "size_frac": size_frac,
        "final_balance": round(bal, 2),
        "profit_usd": round(bal - ctx["balance"], 2),
        "min_balance": round(low, 2),
        "min_at": month_of(low_at) if low_at else None,
        "min_headroom_to_halt_usd": round(low - ctx["halt_line"], 2),
        "min_headroom_to_death_usd": round(low - ctx["death_line"], 2),
        "halted": halted,
        "per_month_usd": per_month,
    }


def main():
    ctx = death_line_context()
    d = load()
    prepped = {sym: prep(bars) for sym, bars in d["pairs"].items()}

    report = {"context": ctx, "candidates": {}}
    print(f"death line ${ctx['death_line']:,.2f} (3% of ${ctx['start']:,.0f}, never moves) | "
          f"halt line ${ctx['halt_line']:,.2f} | balance ${ctx['balance']:,.2f} | "
          f"buffer ${ctx['buffer_now']:,.2f}")

    for (var, stop, act, give) in CANDIDATES:
        key = f"{var}|S{stop}|A{act}|G{give}"
        trades = []
        for sym, p in prepped.items():
            trades += simulate(p, p["entries"][var], stop, act, give, False)
        trades.sort(key=lambda t: t[0])
        nets = [(t[0], t[1], t[2]) for t in trades]
        nov = [t for t in nets if t[0] >= NOV1]

        paths = [run_path(nets, ctx, f) for f in SIZE_FRACS]
        report["candidates"][key] = {
            "overall": score(trades),
            "november": score([(t[0], t[1], t[2]) for t in trades if t[0] >= NOV1]),
            "paths": paths,
        }

        o, nv = score(trades), score([(t[0], t[1], t[2]) for t in trades if t[0] >= NOV1])
        print(f"\n=== {key} ===")
        print(f"  {o['n']} trades | win {o['win_pct']}% | net {o['mean_net_pct']:+.4f}%/trade "
              f"| Nov win {nv.get('win_pct')}% net {nv.get('mean_net_pct'):+.4f}%")
        for p in paths:
            print(f"  size {p['size_frac']*100:4.0f}% of balance: profit "
                  f"${p['profit_usd']:+9.2f} | lowest balance ${p['min_balance']:,.2f} "
                  f"| ${p['min_headroom_to_halt_usd']:+9.2f} above the halt line "
                  f"{'HALTED' if p['halted'] else 'never halted'}")

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)

    print("\n=== verdict on the repo's own rule ===")
    for key, r in report["candidates"].items():
        safe = [p for p in r["paths"] if not p["halted"]]
        best = max(safe, key=lambda p: p["profit_usd"]) if safe else None
        if best:
            print(f"  {key}\n    largest size that never touches the halt line: "
                  f"{best['size_frac']*100:.0f}% of balance -> "
                  f"${best['profit_usd']:+.2f} over the sample, "
                  f"lowest point ${best['min_balance']:,.2f} "
                  f"(${best['min_headroom_to_halt_usd']:+,.2f} of room left)")
        else:
            print(f"  {key}: every size tested touches the halt line")
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
