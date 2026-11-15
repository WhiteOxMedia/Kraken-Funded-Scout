"""WOULD THE COIN THAT CAN CARRY THE MOST SPEND BE THE BETTER CHAMPION? (Nov 15)

Runs over the closes already on disk. Two questions, in order:

  1. DOES THE CAP BIND? "Prefer the coin that supports the highest spend" can
     only change what is traded if the entry the fire wanted was ABOVE the cap
     the guard allows. If `size` sits far below `cap` for most closes, then
     ranking on capacity only reorders coins by volatility profile — it cannot
     put more dollars to work, and the idea is a no-op dressed as an edge.
  2. DOES CAPACITY PREDICT THE CLOSE? Bucket the real closes by the capacity
     their coin allowed at the time, and compare win rate, mean close and net
     per dollar of size. If high-capacity coins do not pay better per dollar,
     preferring them is not an improvement.

Capacity is recomputed per close with the guard's own function —
`contest_account.max_entry_usd(balance_after, window_drawdown_pct(pair))` — from
the state the row itself recorded (`balance_after`). The drawdown profile is the
CURRENT volatility cell, not the one in force that day: stated as a limit, because
profiles move and a stale profile can only blur the bucket, not invent a result.

Run:  .venv/Scripts/python.exe -X utf8 scratch/spend_capacity_study.py
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import contest_account as ca  # noqa: E402

TRADES = ROOT / "contest_trades.jsonl"
BAND = 0.05          # cap within 5% of size counts as "the cap bound"


def bucket(frac_of_start):
    if frac_of_start < 0.10:
        return "cap < 10% start"
    if frac_of_start < 0.20:
        return "10-20%"
    if frac_of_start < 0.30:
        return "20-30%"
    if frac_of_start < 0.50:
        return "30-50%"
    return ">= 50% start"


def spearman(xs, ys):
    """Rank correlation, no scipy needed."""
    if len(xs) < 10:
        return None

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def main():
    rows, skipped = [], {"no_numbers": 0, "no_balance": 0, "no_cap": 0}
    with open(TRADES, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                pair = str(r.get("pair"))
                entry, exit_ = float(r["entry"]), float(r["exit"])
                size, net = float(r["size"]), float(r["pnl_net"])
                bal = float(r["balance_after"])
            except Exception:
                skipped["no_numbers"] += 1
                continue
            if bal <= 0 or size <= 0:
                skipped["no_balance"] += 1
                continue
            try:
                dd = ca.window_drawdown_pct(pair)
                cap = ca.max_entry_usd(bal, dd)
            except Exception:
                skipped["no_cap"] += 1
                continue
            if cap <= 0:
                skipped["no_cap"] += 1
                continue
            start = bal * 0 + (10000.0 if bal > 7000 else bal)  # tier-scale only for the band label
            rows.append({"pair": pair, "cap": cap, "size": size, "net": net,
                         "pct": (exit_ / entry - 1.0) * 100.0 if entry > 0 else None,
                         "win": net > 0, "dd": dd,
                         "cap_frac": cap / max(bal, 1.0),
                         "ratio": size / cap,
                         "bal": bal})

    total = sum(1 for line in open(TRADES, encoding="utf-8") if line.strip())
    print("== sample")
    print(f"closes on disk      {total}")
    print(f"usable              {len(rows)}")
    print(f"excluded            {skipped}")
    if not rows:
        print("nothing to price")
        return 1

    print("\n== 1. DOES THE CAP BIND?")
    bound = sum(1 for r in rows if r["ratio"] >= 1.0 - BAND)
    print(f"closes where size >= {100 * (1 - BAND):.0f}% of the guard's cap: "
          f"{bound} of {len(rows)} ({100 * bound / len(rows):.1f}%)")
    ratios = sorted(r["ratio"] for r in rows)
    print(f"size / cap: median {ratios[len(ratios) // 2]:.2f}, "
          f"p10 {ratios[int(0.1 * len(ratios))]:.2f}, "
          f"p90 {ratios[int(0.9 * len(ratios)) - 1]:.2f}")
    print(f"cap range: ${min(r['cap'] for r in rows):,.0f} .. "
          f"${max(r['cap'] for r in rows):,.0f}; "
          f"size range ${min(r['size'] for r in rows):,.0f} .. "
          f"${max(r['size'] for r in rows):,.0f}")

    print("\n== 2. OUTCOME BY WHAT THE COIN COULD CARRY")
    groups = defaultdict(list)
    for r in rows:
        groups[bucket(r["cap"] / 10000.0)].append(r)
    print(f"{'capacity band':<18}{'n':>5}{'win%':>7}{'mean%':>8}"
          f"{'net $':>10}{'net/$1':>9}")
    for name in ("cap < 10% start", "10-20%", "20-30%", "30-50%", ">= 50% start"):
        g = groups.get(name) or []
        if not g:
            continue
        pcts = [r["pct"] for r in g if r["pct"] is not None]
        spent = sum(r["size"] for r in g)
        print(f"{name:<18}{len(g):>5}"
              f"{100 * sum(1 for r in g if r['win']) / len(g):>6.1f}%"
              f"{statistics.mean(pcts):>8.2f}"
              f"{sum(r['net'] for r in g):>10.2f}"
              f"{(sum(r['net'] for r in g) / spent if spent else 0):>9.5f}")

    caps = [r["cap"] for r in rows]
    pcts = [r["pct"] for r in rows]
    rho = spearman(caps, pcts)
    print(f"\nrank correlation (capacity vs close %): "
          f"{('%.3f' % rho) if rho is not None else 'n/a'}  n={len(pcts)}")

    hi = [r for r in rows if r["cap"] >= statistics.median(caps)]
    lo = [r for r in rows if r["cap"] < statistics.median(caps)]
    for label, g in (("upper half of capacity", hi), ("lower half", lo)):
        p = [r["pct"] for r in g if r["pct"] is not None]
        print(f"{label:<24} n={len(g):<5} win {100 * sum(1 for r in g if r['win']) / len(g):5.1f}%"
              f"  mean {statistics.mean(p):+.2f}%  net {sum(r['net'] for r in g):+.2f}"
              f"  net/$1 {(sum(r['net'] for r in g) / sum(r['size'] for r in g)):+.5f}")

    print("\n== verdict")
    if bound / len(rows) >= 0.5:
        print("The cap BINDS on most closes: capacity is a live constraint, so "
              "preferring the high-capacity coin would change what is traded.")
    else:
        print(f"The cap binds on {100 * bound / len(rows):.1f}% of closes — the entry "
              "the fire wanted was already far below what the guard allows, so "
              "ranking on capacity cannot put more dollars to work. It can only "
              "reorder which coin is bought.")
    if rho is not None:
        strength = ("no usable relationship" if abs(rho) < 0.1 else
                    "a weak one" if abs(rho) < 0.25 else "a real one")
        print(f"Capacity vs realized close rank correlation {rho:+.3f} — {strength} "
              f"at n={len(pcts)}.")
    print("Limit: the drawdown profile used is the CURRENT volatility cell, not the "
          "one in force on the day, and a close's outcome also carries the exit "
          "rules and the sizing that actually happened — this measures the "
          "population, not the counterfactual champion swap.")
    print("The counterfactual is now recorded instead of argued: every fire stamps "
          "`spend_rank` (would the bracket have preferred a bigger-carry coin, by "
          "how many dollars) and `spend_capacity`, so the swap can be graded "
          "against the closes it would have taken.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
