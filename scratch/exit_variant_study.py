"""EXIT VARIANTS — should the trend-reversal close be replaced? (Nov 15)

`scratch/exit_leak_study.py` found the leak: the EXIT WATCH trend-reversal close
costs $94.68 of the $166 exit drag (15–60m mass, plus the worst per-trade cell at
60–180m), while holding to the 180m window gains $166 but doubles the 1% tail.

This grades the candidate replacements on exactly that cohort, against the SHIPPED
exit (the journal's own booked P&L), with the same round-trip charge, on a
chronological fit split and a disjoint holdout:

  shipped   what the live exit actually booked at the moment it closed
  hold180   no exit at all: the close at entry + 180m
  stop180   the hard stop (−3% from entry, unchanged) or the 180m close
  trail180  the profit guard's trail (arms at half the goal, 1.5% off the peak)
            and the same hard stop, or the 180m close

A variant only earns the change if, on the HOLDOUT, it beats the shipped $ per
trade AND does not deepen the 1% tail beyond the shipped exit's tail. The hard
stop is identical in every variant by construction — nothing here can relax it.

Run: .venv/Scripts/python.exe -X utf8 scratch/exit_variant_study.py
"""
from __future__ import annotations

import bisect
import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch"))

from entry_edge_study import load_tape  # noqa: E402

TRADES = ROOT / "contest_trades.jsonl"
RESULT = ROOT / "scratch" / "_exit_variant_result.json"
WINDOW_MIN = 180.0
STOP_PCT = 3.0          # the shipped contest guard
TRAIL_PCT = 1.5         # the shipped profit-guard trail width
ARM_FRAC = 0.5          # arms at half the goal


def parse_ts(s):
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def is_cohort(r):
    return (r.get("exit_reason") or "").upper().startswith("EXIT WATCH")


def simulate(bars, stamps, opened, goal_pct, cost_pct, mode):
    """Walk the 5m tape from the entry bar. Returns (pct_net, worst_mae_pct)."""
    i0 = bisect.bisect_left(stamps, opened)
    i1 = bisect.bisect_right(stamps, opened + WINDOW_MIN * 60)
    if i0 >= len(bars) or i1 <= i0 + 1:
        return None
    entry = float(bars[i0][1])
    if entry <= 0:
        return None
    stop = entry * (1.0 - STOP_PCT / 100.0)
    peak = entry
    armed = False
    mae = 0.0
    for bar in bars[i0:i1]:
        hi, lo, cl = float(bar[2]), float(bar[3]), float(bar[4])
        mae = min(mae, lo / entry - 1.0)
        if mode in ("stop180", "trail180"):
            if lo <= stop:                       # the hard stop, unchanged
                return (stop / entry - 1.0) * 100.0 - cost_pct, mae * 100.0
        if mode == "trail180":
            peak = max(peak, hi)
            if not armed and (peak / entry - 1.0) * 100.0 >= goal_pct * ARM_FRAC:
                armed = True
            if armed and cl <= peak * (1.0 - TRAIL_PCT / 100.0):
                return (cl / entry - 1.0) * 100.0 - cost_pct, mae * 100.0
    return (float(bars[i1 - 1][4]) / entry - 1.0) * 100.0 - cost_pct, mae * 100.0


def q(vals, f):
    v = sorted(vals)
    return v[min(len(v) - 1, int(f * (len(v) - 1)))] if v else 0.0


def summarise(rows, label):
    out = {}
    print(f"    {'variant':<10}{'n':>5}{'booked%':>10}{'$ total':>10}{'hit%':>7}"
          f"{'mean MAE':>10}{'1% worst':>10}{'5% worst':>10}{'$/trade':>10}")
    for mode in ("shipped", "hold180", "stop180", "trail180"):
        pct = [r[mode] for r in rows]
        usd = [r[mode] / 100.0 * r["size"] for r in rows]
        mae = [r["mae_" + mode] for r in rows] if mode != "shipped" else [r["mae_shipped"] for r in rows]
        d = {"n": len(pct),
             "booked_pct": round(statistics.mean(pct), 4),
             "usd": round(sum(usd), 2),
             "hit": round(100.0 * sum(1 for x in pct if x > 0) / len(pct), 1),
             "mae": round(statistics.mean(mae), 4),
             "p1": round(q(pct, 0.01), 3), "p5": round(q(pct, 0.05), 3),
             "per_trade": round(statistics.mean(usd), 3)}
        out[mode] = d
        print(f"    {label+' '+mode:<10}{d['n']:>5}{d['booked_pct']:>+10.3f}{d['usd']:>+10.2f}"
              f"{d['hit']:>7.1f}{d['mae']:>+10.3f}{d['p1']:>+10.2f}{d['p5']:>+10.2f}"
              f"{d['per_trade']:>+10.3f}")
    return out


def main():
    tape = load_tape()
    stamps = {s: [int(b[0]) for b in bars] for s, bars in tape.items()}
    rows = [json.loads(l) for l in TRADES.read_text(encoding="utf-8").splitlines() if l.strip()]
    end = max(max(v) for v in stamps.values())
    start = end - 45 * 86400

    recs = []
    for r in rows:
        if not is_cohort(r):
            continue
        pair = r.get("pair") or ""
        sym = pair.split("/")[0]
        if sym not in tape:
            continue
        sold = parse_ts(r.get("sold_at") or "")
        if sold is None or sold < start:
            continue
        try:
            held = float(r.get("held_min") or 0.0)
            size = float(r.get("size") or 0.0)
            pnl = float(r.get("pnl_net") or 0.0)
            goal = float(r.get("goal_pct") or 5.0)
            cost_pct = 100.0 * (float(r.get("cost_rt") or 0.0) / size) if size else 0.0
        except (TypeError, ValueError):
            continue
        if not (size > 0 and held > 0):
            continue
        opened = sold - held * 60.0
        if opened + WINDOW_MIN * 60.0 > stamps[sym][-1]:
            continue
        res = {}
        ok = True
        for mode in ("hold180", "stop180", "trail180"):
            got = simulate(tape[sym], stamps[sym], opened, goal, cost_pct, mode)
            if got is None:
                ok = False
                break
            res[mode], res["mae_" + mode] = got
        if not ok:
            continue
        # the shipped exit's own drawdown: worst low up to the bar it closed on
        i0 = bisect.bisect_left(stamps[sym], opened)
        i1 = bisect.bisect_right(stamps[sym], opened + held * 60.0)
        entry_close = float(tape[sym][i0][1]) if i0 < len(tape[sym]) else 0.0
        if entry_close <= 0 or i1 <= i0:
            continue
        recs.append({"sym": sym, "size": size, "held": held, "goal": goal,
                     "t": opened,
                     "shipped": 100.0 * pnl / size,
                     "mae_shipped": (min(float(b[3]) for b in tape[sym][i0:i1])
                                     / entry_close - 1.0) * 100.0,
                     **res})

    recs.sort(key=lambda r: r["t"])
    cut = int(len(recs) * 0.6)
    fit, hold = recs[:cut], recs[cut:]
    print(f"\n{'='*104}\nEXIT VARIANTS — cohort: EXIT WATCH trend-reversal closes, "
          f"{len(recs)} trades | fit {len(fit)} | holdout {len(hold)}\n{'='*104}")
    a = summarise(fit, "fit")
    b = summarise(hold, "hold")

    base = b["shipped"]
    verdicts = {}
    print("\n  holdout verdict (needs better $/trade AND a 1% tail no worse than shipped):")
    for mode in ("hold180", "stop180", "trail180"):
        d = b[mode]
        better = d["per_trade"] > base["per_trade"]
        tail_ok = d["p1"] >= base["p1"]
        verdicts[mode] = {"better_per_trade": better, "tail_ok": tail_ok,
                          "wins": better and tail_ok}
        print(f"    {mode:<9} ${d['per_trade']:+.3f}/trade vs ${base['per_trade']:+.3f} "
              f"({'better' if better else 'worse'}); 1% tail {d['p1']:+.2f}% vs "
              f"{base['p1']:+.2f}% ({'ok' if tail_ok else 'WORSE'}) -> "
              f"{'WIN' if better and tail_ok else 'reject'}")
    winners = [k for k, v in verdicts.items() if v["wins"]]
    print(f"\n  winners: {winners or 'NONE — keep the shipped exit'}")
    RESULT.write_text(json.dumps({"cohort": "EXIT WATCH trend-reversal",
                                  "trades": len(recs), "fit": a, "holdout": b,
                                  "verdicts": verdicts, "winners": winners},
                                 indent=1), encoding="utf-8")
    print("wrote scratch/_exit_variant_result.json")


if __name__ == "__main__":
    main()
