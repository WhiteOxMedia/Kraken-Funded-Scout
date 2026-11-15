"""EXIT EDGE — which exit choice actually pays on the champion path? (Nov 15)

Same method that settled the ranking question: chronological, no lookahead,
paired, charged the journal's median round-trip cost, split fit / untouched
holdout.

  * decision points and the fired coin are exactly the entry study's: the
    scanner's own `evaluate_strategy` over the 5m tape aggregated to 1h, at each
    point the highest-confidence BUY is the one that fires;
  * each fired entry records its FULL forward path — every 5m high, low and
    close for four hours after the entry bar — so every exit variant is marked
    over the SAME entries and the SAME windows;
  * variants: take-profit distance, stop distance, hold horizon, and the
    combination. Stops are only ever TIGHTENED (a wider stop is not tested as a
    candidate, and never shipped), horizons are only shortened or extended
    inside the same four-hour path, and the shipped exit is the baseline.

The shipped exit, as the scanner's own code expresses it for a fired row:
goal_pct = 3.0 (the popup's shipped goal), hold = 180 min (the shipped window),
and the hard stop = the contest's 3% guard band. Shop variants are compared to
exactly that, net of cost, on the same entries.

Run: .venv/Scripts/python.exe -X utf8 scratch/exit_edge_study.py [--days 45] [--step 6]
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch"))

import config as cfgmod          # noqa: E402
import scanner as sc             # noqa: E402
from entry_edge_study import (  # noqa: E402  (same tape, same hourly build)
    LOOKBACK, TRADES, hourly, load_tape)

OUT = ROOT / "scratch" / "_exit_entries.jsonl"
RESULT = ROOT / "scratch" / "_exit_result.json"
PATH_MIN = 240.0           # how much forward path each entry records

SHIPPED = {"name": "shipped (tp 3.0 / stop 3.0 / hold 180)",
           "tp": 3.0, "sl": 3.0, "hold": 180.0}

VARIANTS = [
    SHIPPED,
    {"name": "hold 60", "tp": 3.0, "sl": 3.0, "hold": 60.0},
    {"name": "hold 120", "tp": 3.0, "sl": 3.0, "hold": 120.0},
    {"name": "hold 240", "tp": 3.0, "sl": 3.0, "hold": 240.0},
    {"name": "tp 1.5", "tp": 1.5, "sl": 3.0, "hold": 180.0},
    {"name": "tp 2.0", "tp": 2.0, "sl": 3.0, "hold": 180.0},
    {"name": "tp 4.0", "tp": 4.0, "sl": 3.0, "hold": 180.0},
    {"name": "tp 5.0", "tp": 5.0, "sl": 3.0, "hold": 180.0},
    {"name": "stop 2.0 (tighter)", "tp": 3.0, "sl": 2.0, "hold": 180.0},
    {"name": "stop 1.5 (tighter)", "tp": 3.0, "sl": 1.5, "hold": 180.0},
    {"name": "tp 2.0 + hold 120", "tp": 2.0, "sl": 3.0, "hold": 120.0},
    {"name": "tp 2.0 + stop 2.0", "tp": 2.0, "sl": 2.0, "hold": 180.0},
]


def _stamps(bars):
    return [int(b[0]) for b in bars]


def path_at(bars_5m, ts, minutes):
    """Every 5m (high, low, close) from the entry bar through `minutes`."""
    st = _stamps(bars_5m)
    i0 = bisect.bisect_left(st, ts)
    i1 = bisect.bisect_right(st, ts + minutes * 60)
    if i0 >= len(bars_5m) or i1 <= i0:
        return None
    seg = bars_5m[i0:i1]
    return {"entry": float(bars_5m[i0][1]),
            "highs": [float(b[2]) for b in seg],
            "lows": [float(b[3]) for b in seg],
            "closes": [float(b[4]) for b in seg],
            "stamps": st[i0:i1]}


def simulate(p, tp_pct, sl_pct, hold_min, cost_pct):
    """Walk the path bar by bar: stop first (protective), then take, then time."""
    entry = p["entry"]
    tp = entry * (1.0 + tp_pct / 100.0)
    sl = entry * (1.0 - sl_pct / 100.0)
    end_ts = p["stamps"][0] + hold_min * 60
    last = entry
    for hi, lo, cl, ts in zip(p["highs"], p["lows"], p["closes"], p["stamps"]):
        if ts > end_ts:
            break
        last = cl
        # the protective leg is judged before the target: a bar that touches
        # both is a loss here, never a manufactured win
        if lo <= sl:
            return (sl / entry - 1.0) * 100.0 - cost_pct, "stop"
        if hi >= tp:
            return (tp / entry - 1.0) * 100.0 - cost_pct, "take"
    return (last / entry - 1.0) * 100.0 - cost_pct, "time"


def collect(days, step_h, cost_pct):
    tape = load_tape()
    cfg = cfgmod.TradingConfig()
    cfg.mtf_enabled = False
    cfg.liquidity_filter_enabled = False
    cfg.deep_scan_enabled = False
    hourly_tape = {}
    for sym, bars in tape.items():
        if len(bars) > 500:
            hb = hourly(bars)
            hourly_tape[sym] = (hb, [int(b[0]) for b in hb])
    end = max(b[-1][0] for b in tape.values())
    start = end - days * 86400

    points = 0
    ts = int(start // 3600 * 3600)
    with open(OUT, "w", encoding="utf-8") as out:
        while ts <= end:
            best = None
            for sym, (hb, hst) in hourly_tape.items():
                i = bisect.bisect_right(hst, ts * 1000) - 1
                if i < 60:
                    continue
                window = hb[max(0, i - LOOKBACK + 1):i + 1]
                try:
                    sig = sc.evaluate_strategy(sc._ohlcv_to_df(window), cfg)
                except Exception:
                    continue
                if sig.action != "buy":
                    continue
                if best is None or sig.confidence > best[0]:
                    best = (sig.confidence, sym)
            if best is not None:
                p = path_at(tape[best[1]], ts, PATH_MIN)
                if p and p["closes"]:
                    points += 1
                    out.write(json.dumps({"t": ts, "pair": best[1],
                                          "conf": best[0], "path": p}) + "\n")
            ts += step_h * 3600
    print(f"fired entries recorded: {points}", flush=True)
    return points


def ci(diffs):
    n = len(diffs)
    if n < 2:
        return (statistics.mean(diffs) if diffs else 0.0), 0.0, 0.0
    m = statistics.mean(diffs)
    se = statistics.stdev(diffs) / math.sqrt(n)
    return m, m - 1.96 * se, m + 1.96 * se


def wilson(k, n, z=1.96):
    if not n:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def analyse(rows, cost_pct, fit_frac):
    for r in rows:
        base, _ = simulate(r["path"], SHIPPED["tp"], SHIPPED["sl"],
                           SHIPPED["hold"], cost_pct)
        r["base"] = base
    cut = int(len(rows) * fit_frac)
    fit, hold = rows[:cut], rows[cut:]
    print(f"\n{'='*104}\nEXIT EDGE: {len(rows)} fired entries | fit {len(fit)} | "
          f"holdout {len(hold)} (chronological, no overlap) | cost {cost_pct:.3f}%\n{'='*104}")
    print(f"{'variant':<26}{'fit n':>6}{'fit d':>9}{'fit CI':>17}"
          f"{'hold n':>7}{'hold d':>9}{'hold CI':>17}{'hold win%':>10}{'hold net/$1':>12}")

    out = {}
    for v in VARIANTS:
        fit_d, hold_d, hold_net, hold_win = [], [], [], []
        for split, dlist, netl in ((fit, fit_d, None), (hold, hold_d, hold_net)):
            for r in split:
                got, _how = simulate(r["path"], v["tp"], v["sl"], v["hold"], cost_pct)
                dlist.append(got - r["base"])
                if netl is not None:
                    netl.append(got)
                    hold_win.append(got > 0)
        md, lo, hi = ci(fit_d)
        mh, loh, hih = ci(hold_d)
        win = 100.0 * sum(hold_win) / len(hold_win) if hold_win else 0.0
        net_per_dollar = statistics.mean(hold_net) / 100.0 if hold_net else 0.0
        clears = (v is not SHIPPED and len(hold_d) >= 15 and loh > 0 and mh > 0)
        out[v["name"]] = {"fit_n": len(fit_d), "fit_diff": round(md, 4),
                          "fit_ci": [round(lo, 4), round(hi, 4)],
                          "hold_n": len(hold_d), "hold_diff": round(mh, 4),
                          "hold_ci": [round(loh, 4), round(hih, 4)],
                          "hold_win_pct": round(win, 1),
                          "hold_net_per_dollar": round(net_per_dollar, 5),
                          "clears": bool(clears)}
        print(f"{v['name']:<26}{len(fit_d):>6}{md:>+9.3f}"
              f"{lo:>+8.3f}..{hi:<+7.3f}{len(hold_d):>7}{mh:>+9.3f}"
              f"{loh:>+8.3f}..{hih:<+7.3f}{win:>10.1f}{net_per_dollar:>12.5f}")

    shipped = out[SHIPPED["name"]]
    print(f"\nshipped baseline on the holdout: win {shipped['hold_win_pct']:.1f}% "
          f"net/$1 {shipped['hold_net_per_dollar']:+.5f}")
    winners = [k for k, v in out.items() if v["clears"]]
    print(f"variants clearing the shipped exit out of sample: {winners or 'NONE'}")
    RESULT.write_text(json.dumps({"shipped": SHIPPED["name"], "variants": out,
                                  "winners": winners, "fit_frac": fit_frac,
                                  "entries": len(rows), "cost_pct": cost_pct},
                                 indent=1), encoding="utf-8")
    print("wrote scratch/_exit_result.json")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--step", type=float, default=6.0)
    ap.add_argument("--fit", type=float, default=0.6)
    ap.add_argument("--analyse-only", action="store_true")
    args = ap.parse_args()

    costs = []
    if TRADES.exists():
        for line in TRADES.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                if float(r["size"]) > 0:
                    costs.append(float(r["cost_rt"]) / float(r["size"]) * 100.0)
            except Exception:
                continue
    cost_pct = statistics.median(costs) if costs else 0.08
    print(f"cost {cost_pct:.3f}%/trade (median cost_rt/size)")

    if not args.analyse_only:
        collect(args.days, args.step, cost_pct)
    rows = [json.loads(l) for l in OUT.read_text(encoding="utf-8").splitlines() if l.strip()]
    analyse(rows, cost_pct, args.fit)


if __name__ == "__main__":
    main()
