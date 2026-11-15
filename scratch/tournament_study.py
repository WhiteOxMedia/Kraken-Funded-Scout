"""TOURNAMENT: which champion rule actually picks the better mover? (Nov 15)

The entry study proved the *ranking preference* carries a forward edge
(`pos_hour`, holdout +0.1108%). This asks the next question the live loop
actually decides: **who gets crowned**. Today the crown is `max(confidence)`
among the eligible buys — i.e. the coin that has already moved hardest. The
alternative is the coin sitting lowest in its own hour range (`entry_quality`,
"pos_hour") — the buy-low, buy-timed pick.

Same discipline: chronological, no lookahead, one champion per decision point,
the crown pool exactly as the live code builds it (top six buys by confidence),
forward return from the same 5m tape (entry close → +60m close) net of the
journal's median round trip, paired against the shipped champion, fit / holdout.

Run: .venv/Scripts/python.exe -X utf8 scratch/tournament_study.py
"""
from __future__ import annotations

import argparse
import bisect
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch"))

import config as cfgmod            # noqa: E402
import scanner as sc              # noqa: E402
from entry_edge_study import LOOKBACK, TRADES, hourly, load_tape, paired_ci  # noqa: E402

OUT = ROOT / "scratch" / "_tourney_points.jsonl"
RESULT = ROOT / "scratch" / "_tourney_result.json"
HORIZON_MIN = 60.0
CROWN_POOL = 6          # the live crown pool: top six buys by confidence


def fwd_net(bars, ts, minutes, cost_pct):
    st = [int(b[0]) for b in bars]
    i0 = bisect.bisect_left(st, ts)
    if i0 >= len(bars):
        return None
    entry = float(bars[i0][1])
    j = bisect.bisect_right(st, ts + minutes * 60) - 1
    if j <= i0 or entry <= 0:
        return None
    return (float(bars[j][4]) / entry - 1.0) * 100.0 - cost_pct


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
                except Exception:
                    continue
                if sig.action != "buy" or sig.confidence < min_conf:
                    continue
                try:
                    eq = sc._entry_quality(df)
                except Exception:
                    eq = None
                fwd = fwd_net(tape[sym], ts, HORIZON_MIN, cost_pct)
                if fwd is None:
                    continue
                cands.append({"pair": sym, "conf": round(sig.confidence, 2),
                              "pos_hour": None if eq is None else round(eq, 4),
                              "fwd": round(fwd, 5)})
            pool = sorted(cands, key=lambda c: c["conf"])[-CROWN_POOL:]
            if pool:
                points += 1
                out.write(json.dumps({"t": ts, "pool": pool}) + "\n")
            ts += step_h * 3600
    print(f"decision points with a buy: {points}", flush=True)
    return points


def crown(pool, rule):
    if rule == "conf":                       # shipped
        return max(pool, key=lambda c: c["conf"])
    timed = [c for c in pool if c["pos_hour"] is not None]
    if rule == "pos_hour":                   # cheapest in its own hour range
        return min(timed, key=lambda c: c["pos_hour"]) if timed \
            else max(pool, key=lambda c: c["conf"])
    if rule == "pos_hour_top3":              # timed, but only among the loudest 3
        top3 = sorted(pool, key=lambda c: c["conf"])[-3:]
        t3 = [c for c in top3 if c["pos_hour"] is not None]
        return min(t3, key=lambda c: c["pos_hour"]) if t3 \
            else max(pool, key=lambda c: c["conf"])
    raise SystemExit(f"unknown rule {rule}")


RULES = ["conf", "pos_hour", "pos_hour_top3"]


def analyse(points, fit_frac):
    cut = int(len(points) * fit_frac)
    fit, hold = points[:cut], points[cut:]
    print(f"\n{'='*100}\nTOURNAMENT: {len(points)} champion decisions | fit {len(fit)} | "
          f"holdout {len(hold)} | horizon {HORIZON_MIN:.0f}m, cost charged\n{'='*100}")
    out = {}
    base_fit = [crown(p["pool"], "conf")["fwd"] for p in fit]
    base_hold = [crown(p["pool"], "conf")["fwd"] for p in hold]
    print(f"{'rule':<16}{'fit n':>6}{'fit win%':>10}{'fit net':>10}{'fit d':>9}{'fit CI':>18}"
          f"{'hold n':>7}{'hold win%':>11}{'hold net':>10}{'hold d':>9}{'hold CI':>18}")
    for rule in RULES:
        f = [crown(p["pool"], rule)["fwd"] for p in fit]
        h = [crown(p["pool"], rule)["fwd"] for p in hold]
        df_ = [a - b for a, b in zip(f, base_fit)]
        dh = [a - b for a, b in zip(h, base_hold)]
        mf, lof, hif = paired_ci(df_)
        mh, loh, hih = paired_ci(dh)
        wf = 100.0 * sum(1 for v in f if v > 0) / max(1, len(f))
        wh = 100.0 * sum(1 for v in h if v > 0) / max(1, len(h))
        clears = rule != "conf" and len(h) >= 15 and loh > 0
        out[rule] = {"fit_n": len(f), "fit_win_pct": round(wf, 1),
                     "fit_net_per_dollar": round(statistics.mean(f) / 100.0, 5),
                     "fit_delta": round(mf, 4), "fit_ci": [round(lof, 4), round(hif, 4)],
                     "hold_n": len(h), "hold_win_pct": round(wh, 1),
                     "hold_net_per_dollar": round(statistics.mean(h) / 100.0, 5),
                     "hold_delta": round(mh, 4), "hold_ci": [round(loh, 4), round(hih, 4)],
                     "clears": bool(clears)}
        print(f"{rule:<16}{len(f):>6}{wf:>10.1f}{statistics.mean(f)/100:>10.5f}"
              f"{mf:>+9.3f}{lof:>+8.3f}..{hif:<+7.3f}{len(h):>7}{wh:>11.1f}"
              f"{statistics.mean(h)/100:>10.5f}{mh:>+9.3f}"
              f"{loh:>+8.3f}..{hih:<+7.3f}")
    winners = [r for r in RULES if out[r]["clears"]]
    print(f"\nrules clearing the shipped crown out of sample: {winners or 'NONE'}")
    RESULT.write_text(json.dumps({"points": len(points), "fit_frac": fit_frac,
                                  "crown_pool": CROWN_POOL, "rules": out,
                                  "winners": winners}, indent=1), encoding="utf-8")
    print("wrote scratch/_tourney_result.json")
    return out


def live_report():
    """The gate's live book: what it fired, what it refused, on what measurements.

    Reads `contest_gate_log.jsonl` (one row per judged fire, written by the
    shipped choke point) so the fit/holdout table beside it is answerable from
    live rows instead of only from the tape study.
    """
    import breakout_gate as bg
    print(f"\n{'='*92}\nLIVE GATE REPORT — {bg.GATE_LOG.name} | gate {bg.BREAKOUT_MIN:.2f} "
          f"| study v{bg.BREAKOUT_STUDY_V}: fit {bg.BREAKOUT_STUDY['fit_n']} @ "
          f"{bg.BREAKOUT_STUDY['fit_win_pct']}%, holdout {bg.BREAKOUT_STUDY['oos_n']} @ "
          f"{bg.BREAKOUT_STUDY['oos_win_pct']}% CI {bg.BREAKOUT_STUDY['oos_ci']}, "
          f"pooled {bg.BREAKOUT_STUDY['pooled_win_pct']}%\n{'='*92}")
    if not bg.GATE_LOG.exists():
        print("no live rows yet")
        return {}
    rows = [json.loads(l) for l in bg.GATE_LOG.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    out = {}
    for decision in ("fire", "veto", "unmeasured-fire"):
        sel = [r for r in rows if r.get("decision") == decision]
        eqs = [r["entry_quality"] for r in sel if r.get("entry_quality") is not None]
        out[decision] = len(sel)
        print(f"  {decision:<16} n={len(sel):>4}  mean entry_quality "
              f"{statistics.mean(eqs):.3f}" if eqs else
              f"  {decision:<16} n={len(sel):>4}  (no measurements)")
    routes = {}
    for r in rows:
        routes[r.get("route")] = routes.get(r.get("route"), 0) + 1
    print(f"  by route: {routes}")
    print("  the gate's own record on each row: gate_v, entry_quality, pooled/OOS figures"
          f" — all rows carry gate_v={rows[-1].get('gate_v')}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="report the gate's live rows beside the study table")
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--step", type=float, default=6.0)
    ap.add_argument("--fit", type=float, default=0.6)
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
    print(f"cost {cost_pct:.3f}%/trade, min_conf {args.min_conf}")

    if not args.analyse_only:
        collect(args.days, args.step, cost_pct, args.min_conf)
    points = [json.loads(l) for l in OUT.read_text(encoding="utf-8").splitlines() if l.strip()]
    analyse(points, args.fit)
    if args.live:
        live_report()


if __name__ == "__main__":
    main()
