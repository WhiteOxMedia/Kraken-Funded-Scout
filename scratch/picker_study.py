"""PICKER STUDY — does timing the champion beat crowning the loudest? (Nov 15)

The crown rule is the last unmeasured choice in the tournament. This rebuilds the
live crown pool at each recorded decision point (buys >= min_conf, top six by
confidence — exactly `crown_cycle`'s pool) and measures, for EVERY candidate:

  * `eq`     — position in its prior hour range (the gate's measurement)
  * `ext4h`  — how far it has already run in the last four hours (extension)
  * forward return over the live hold horizon, net of the 0.080% round trip
  * the worst adverse excursion inside that horizon (drawdown toward the
    death-line), and whether the trade won

then compares pickers over the same cycles:

  live_today  the shipped rule — loudest buy; if its eq < 0.85 the gate vetoes
              and the cycle trades nothing (recorded as 0 return, 0 drawdown)
  gated_conf  loudest candidate that passes the gate (always trades)
  least_ext   the least-extended gate-passer (earliest in its move)
  score_k     highest (confidence - k * ext4h) among gate-passers, k tuned on fit

FIT span picks k (and the rule family); the HOLDOUT span is reported untouched.
A variant ships only if it beats live_today on the holdout on forward return AND
hit rate without a worse drawdown tail.

Run: .venv/Scripts/python.exe -X utf8 scratch/picker_study.py
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
from entry_edge_study import LOOKBACK, TRADES, hourly, load_tape  # noqa: E402

OUT = ROOT / "scratch" / "_picker_points.jsonl"
RESULT = ROOT / "scratch" / "_picker_result.json"
HOLD_MIN = 180.0        # the shipped hold window
GATE_MIN = 0.85
CROWN_POOL = 6


def path(bars, ts, minutes):
    st = [int(b[0]) for b in bars]
    i0 = bisect.bisect_left(st, ts)
    i1 = bisect.bisect_right(st, ts + minutes * 60)
    if i0 >= len(bars) or i1 <= i0 + 1:
        return None
    seg = bars[i0:i1]
    entry = float(seg[0][1])
    if entry <= 0:
        return None
    return {"entry": entry,
            "closes": [float(b[4]) for b in seg],
            "lows": [float(b[3]) for b in seg],
            "stamps": st[i0:i1]}


def metrics(p, cost_pct):
    entry = p["entry"]
    out = float(p["closes"][-1]) / entry - 1.0
    mae = float(min(p["lows"])) / entry - 1.0
    return (out * 100.0 - cost_pct, mae * 100.0)


def collect(days, step_h, cost_pct, min_conf):
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
            cands = []
            for sym, (hb, hst) in hourly_tape.items():
                i = bisect.bisect_right(hst, ts * 1000) - 1
                if i < 60:
                    continue
                window = hb[max(0, i - LOOKBACK + 1):i + 1]
                try:
                    df = sc._ohlcv_to_df(window)
                    sig = sc.evaluate_strategy(df, cfg)
                    eq = sc._entry_quality(df)
                except Exception:
                    continue
                if sig.action != "buy" or sig.confidence < min_conf:
                    continue
                p = path(tape[sym], ts, HOLD_MIN)
                if p is None:
                    continue
                fwd, mae = metrics(p, cost_pct)
                # extension = how much of its recent move is already spent
                cl = [float(c) for c in df["close"]]
                ext4 = (cl[-1] / cl[-5] - 1.0) * 100.0 if len(cl) > 5 else 0.0
                cands.append({"pair": sym, "conf": round(sig.confidence, 2),
                              "eq": None if eq is None else round(eq, 4),
                              "ext4h": round(ext4, 3),
                              "fwd": round(fwd, 5), "mae": round(mae, 5)})
            pool = sorted(cands, key=lambda c: c["conf"])[-CROWN_POOL:]
            if pool:
                points += 1
                out.write(json.dumps({"t": ts, "pool": pool}) + "\n")
            ts += step_h * 3600
    print(f"decision points: {points}", flush=True)
    return points


# ── pickers: each returns a candidate dict or None (None == no trade) ───────
def pick_live_today(pool):
    c = max(pool, key=lambda c: c["conf"])
    if c["eq"] is None or c["eq"] < GATE_MIN:
        return None
    return c


def _passing(pool):
    return [c for c in pool if c["eq"] is not None and c["eq"] >= GATE_MIN]


def pick_gated_conf(pool):
    ok = _passing(pool)
    return max(ok, key=lambda c: c["conf"]) if ok else None


def pick_least_ext(pool):
    ok = _passing(pool)
    return min(ok, key=lambda c: c["ext4h"]) if ok else None


def pick_score(k):
    def _p(pool):
        ok = _passing(pool)
        return max(ok, key=lambda c: c["conf"] - k * max(0.0, c["ext4h"])) if ok else None
    return _p


PICKERS = {"live_today": pick_live_today, "gated_conf": pick_gated_conf,
           "least_ext": pick_least_ext}
K_GRID = (0.2, 0.5, 1.0, 2.0, 4.0)


def stats(picks):
    """picks: list of candidate-or-None per cycle."""
    trades = [p for p in picks if p is not None]
    rets = [p["fwd"] if p else 0.0 for p in picks]
    n = len(picks)
    if not trades:
        return {"n": n, "trades": 0, "ret": 0.0, "hit": 0.0, "mae": 0.0,
                "ret_trade": 0.0, "worst": 0.0}
    hit = 100.0 * sum(1 for p in trades if p["fwd"] > 0) / len(trades)
    mean = statistics.mean(rets)
    se = statistics.stdev(rets) / math.sqrt(n) if n > 1 else 0.0
    return {"n": n, "trades": len(trades), "ret": round(mean, 5),
            "ret_ci": [round(mean - 1.96 * se, 5), round(mean + 1.96 * se, 5)],
            "ret_trade": round(statistics.mean([p["fwd"] for p in trades]), 5),
            "hit": round(hit, 1),
            "mae": round(statistics.mean([p["mae"] for p in trades]), 4),
            "worst": round(min(p["mae"] for p in trades), 4)}


def report(rows):
    def run(picker, span):
        return stats([picker(r["pool"]) for r in span])
    print(f"{'picker':<16}{'span':<9}{'cycles':>7}{'trades':>7}{'ret/cycle':>11}"
          f"{'95% CI':>20}{'hit%':>7}{'ret/trade':>11}{'mean MAE':>10}{'worst MAE':>11}")
    out = {}
    for name, fn in list(PICKERS.items()):
        for label, span in (("fit", rows[:len(rows) * 6 // 10]),
                            ("holdout", rows[len(rows) * 6 // 10:])):
            s = run(fn, span)
            out.setdefault(name, {})[label] = s
            print(f"{name:<16}{label:<9}{s['n']:>7}{s['trades']:>7}{s['ret']:>11.5f}"
                  f"{str(s['ret_ci']):>20}{s['hit']:>7.1f}{s['ret_trade']:>11.5f}"
                  f"{s['mae']:>10.3f}{s['worst']:>11.3f}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--step", type=float, default=6.0)
    ap.add_argument("--min-conf", type=int, default=48)
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
    print(f"cost {cost_pct:.3f}%/trade, hold {HOLD_MIN:.0f}m, gate {GATE_MIN}")

    if not args.analyse_only:
        collect(args.days, args.step, cost_pct, args.min_conf)
    rows = [json.loads(l) for l in OUT.read_text(encoding="utf-8").splitlines() if l.strip()]
    cut = len(rows) * 6 // 10
    fit, hold = rows[:cut], rows[cut:]

    print("\n" + "=" * 104)
    print(f"PICKER STUDY: {len(rows)} cycles | fit {len(fit)} | holdout {len(hold)} | "
          f"cost {cost_pct:.3f}% | hold {HOLD_MIN:.0f}m")
    print("=" * 104)
    out = report(rows)

    # tune k on FIT only
    best_k, best_ret = None, -9e9
    print("\n-- score_k grid, tuned on the FIT span only --")
    for k in K_GRID:
        s_fit = stats([pick_score(k)(r["pool"]) for r in fit])
        s_hold = stats([pick_score(k)(r["pool"]) for r in hold])
        out.setdefault(f"score_{k}", {"fit": s_fit, "holdout": s_hold})
        print(f"  k={k:<5} fit ret/cycle {s_fit['ret']:+.5f} hit {s_fit['hit']:.1f}% "
              f"({s_fit['trades']} trades) | holdout ret/cycle {s_hold['ret']:+.5f} "
              f"hit {s_hold['hit']:.1f}% ({s_hold['trades']} trades)")
        if s_fit["ret"] > best_ret and s_fit["trades"] >= 20:
            best_k, best_ret = k, s_fit["ret"]
    print(f"  fit picks k={best_k}")

    chosen = f"score_{best_k}"
    base = out["live_today"]["holdout"]
    cand = out[chosen]["holdout"]
    print("\n-- holdout comparison vs live_today --")
    print(f"  live_today  ret/cycle {base['ret']:+.5f} CI {base['ret_ci']} "
          f"hit {base['hit']:.1f}% trades {base['trades']}/{base['n']} "
          f"worst MAE {base['worst']:.2f}%")
    for name in ("gated_conf", "least_ext", chosen):
        s = out[name]["holdout"]
        better_ret = s["ret"] > base["ret"]
        better_hit = s["hit"] >= base["hit"]
        safer = s["worst"] >= base["worst"]
        print(f"  {name:<12} ret/cycle {s['ret']:+.5f} CI {s['ret_ci']} "
              f"hit {s['hit']:.1f}% trades {s['trades']}/{s['n']} "
              f"worst MAE {s['worst']:.2f}%  -> ret {'UP' if better_ret else 'down'}, "
              f"hit {'up' if better_hit else 'down'}, tail {'better/equal' if safer else 'WORSE'}")
    winners = [n for n in ("gated_conf", "least_ext", chosen)
               if out[n]["holdout"]["ret"] > base["ret"]
               and out[n]["holdout"]["hit"] >= base["hit"]
               and out[n]["holdout"]["worst"] >= base["worst"]]
    print(f"\nholdout winners: {winners or 'NONE'}")
    RESULT.write_text(json.dumps({"cycles": len(rows), "fit": len(fit),
                                  "holdout": len(hold), "cost_pct": cost_pct,
                                  "hold_min": HOLD_MIN, "k_chosen_on_fit": best_k,
                                  "results": out, "winners": winners}, indent=1),
                      encoding="utf-8")
    print("wrote scratch/_picker_result.json")


if __name__ == "__main__":
    main()
