"""EXIT LEAK — what the shipped exits book versus holding the same horizon (Nov 15).

Every row in `contest_trades.jsonl` is a real close: entry price, exit price, size,
minutes held, net P&L, peak excursion (`mfe_pct`). This replays each one against the
recorded 5m tape to answer one question:

    what would the SAME trade have booked if the exit had simply held to the
    horizon it was actually held, instead of acting when it did?

The difference is the exit's leak (or saving). It is charged the same round trip
the journal already paid, so the comparison is net-to-net, and it is bucketed by
how the trade actually ended and by how long it ran — which is where the single
biggest leak should be visible.

Run: .venv/Scripts/python.exe -X utf8 scratch/exit_leak_study.py [--days 45]
"""
from __future__ import annotations

import argparse
import bisect
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch"))

from entry_edge_study import load_tape  # noqa: E402

TRADES = ROOT / "contest_trades.jsonl"
RESULT = ROOT / "scratch" / "_exit_leak_result.json"


def parse_ts(s):
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def close_at(bars, stamps, ts):
    i = bisect.bisect_right(stamps, ts) - 1
    if i < 0 or i >= len(bars):
        return None
    return float(bars[i][4])


def close_low_since(bars, stamps, opened, minutes):
    """Worst low inside the window, as a % from the entry close."""
    i0 = bisect.bisect_left(stamps, opened)
    i1 = bisect.bisect_right(stamps, opened + minutes * 60)
    if i0 >= len(bars) or i1 <= i0:
        return None
    entry = float(bars[i0][1])
    if entry <= 0:
        return None
    return (min(float(b[3]) for b in bars[i0:i1]) / entry - 1.0) * 100.0


def bucket_reason(r):
    why = (r.get("exit_reason") or "").upper()
    if why.startswith("EXIT WATCH"):
        return "exit watch (trend reversal)"
    if "PROFIT GUARD" in why:
        return "profit guard (trail)"
    if "STOPPED OUT" in why:
        return "hard stop"
    if "CLIMAX" in why:
        return "climax candle"
    if "TREND BROKE" in why:
        return "trend bar broke"
    if "GOAL" in why or "TAKE PROFIT" in why:
        return "take profit"
    if not why:
        return "no reason recorded"
    return "other: " + why[:26].lower()


def bucket_hold(m):
    if m < 15:
        return "<15m"
    if m < 60:
        return "15-60m"
    if m < 180:
        return "60-180m"
    return ">=180m"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45)
    args = ap.parse_args()

    tape = load_tape()
    stamps = {s: [int(b[0]) for b in bars] for s, bars in tape.items()}
    rows = [json.loads(l) for l in TRADES.read_text(encoding="utf-8").splitlines() if l.strip()]
    end = max(max(v) for v in stamps.values())
    start = end - args.days * 86400

    recs = []
    for r in rows:
        pair = r.get("pair")
        sym = pair.split("/")[0] if pair else None
        if not sym or sym not in tape:
            continue
        sold = parse_ts(r.get("sold_at") or "")
        if sold is None or sold < start:
            continue
        try:
            held = float(r.get("held_min") or 0.0)
            entry = float(r.get("entry") or 0.0)
            size = float(r.get("size") or 0.0)
            pnl = float(r.get("pnl_net") or 0.0)
        except (TypeError, ValueError):
            continue
        if not (entry > 0 and size > 0 and held > 0):
            continue
        opened = sold - held * 60.0
        pair_end = stamps[sym][-1]
        if opened + held * 60.0 > pair_end:
            continue                       # the tape cannot judge this horizon
        hold_close = close_at(tape[sym], stamps[sym], opened + held * 60.0)
        hold180_close = close_at(tape[sym], stamps[sym], opened + 180 * 60.0)
        entry_close = close_at(tape[sym], stamps[sym], opened)
        if hold_close is None or entry_close is None or entry_close <= 0:
            continue
        cost_pct = 100.0 * (float(r.get("cost_rt") or 0.0) / size) if size else 0.0
        booked_pct = 100.0 * pnl / size
        hold_pct = (hold_close / entry_close - 1.0) * 100.0 - cost_pct
        hold180_pct = ((hold180_close / entry_close - 1.0) * 100.0 - cost_pct
                       if hold180_close else None)
        recs.append({"pair": sym, "size": size, "held": held, "opened": opened,
                     "booked_pct": booked_pct, "hold_pct": hold_pct,
                     "hold180_pct": hold180_pct,
                     "booked_usd": pnl, "hold_usd": hold_pct / 100.0 * size,
                     "mfe_pct": float(r.get("mfe_pct") or 0.0),
                     "reason": bucket_reason(r), "band": bucket_hold(held),
                     "sold": r.get("sold_at")})

    if not recs:
        print("no matched trades in the window")
        return 1

    leak_usd = sum(r["booked_usd"] - r["hold_usd"] for r in recs)
    booked_usd = sum(r["booked_usd"] for r in recs)
    hold_usd = sum(r["hold_usd"] for r in recs)
    print(f"\n{'='*100}\nEXIT LEAK: {len(recs)} real closes in the last {args.days}d "
          f"matched to the tape\n{'='*100}")
    print(f"  booked      {booked_usd:+9.2f} $  ({booked_usd/len(recs):+.3f} $/trade)")
    print(f"  held same   {hold_usd:+9.2f} $  ({hold_usd/len(recs):+.3f} $/trade)")
    print(f"  LEAK        {leak_usd:+9.2f} $  (booked - held; negative = the exit cost money)")

    def table(key):
        print(f"\n  by {key}:")
        print(f"    {'bucket':<22}{'n':>5}{'booked%':>10}{'held%':>9}{'leak%':>9}"
              f"{'leak $':>10}{'booked $':>10}{'hit%':>7}{'mfe capture':>13}")
        out = {}
        groups = {}
        for r in recs:
            groups.setdefault(r[key], []).append(r)
        for b, rs in sorted(groups.items(), key=lambda kv: sum(x["booked_usd"] - x["hold_usd"] for x in kv[1])):
            n = len(rs)
            bp = statistics.mean(x["booked_pct"] for x in rs)
            hp = statistics.mean(x["hold_pct"] for x in rs)
            lk = sum(x["booked_usd"] - x["hold_usd"] for x in rs)
            bu = sum(x["booked_usd"] for x in rs)
            hit = 100.0 * sum(1 for x in rs if x["booked_pct"] > 0) / n
            cap = statistics.mean([(x["booked_pct"] / x["mfe_pct"]) for x in rs
                                   if x["mfe_pct"] > 0.2] or [0.0])
            out[b] = {"n": n, "booked_pct": round(bp, 3), "held_pct": round(hp, 3),
                      "leak_pct": round(bp - hp, 3), "leak_usd": round(lk, 2),
                      "booked_usd": round(bu, 2), "hit": round(hit, 1),
                      "mfe_capture": round(cap, 3)}
            print(f"    {b:<22}{n:>5}{bp:>+10.3f}{hp:>+9.3f}{bp-hp:>+9.3f}{lk:>+10.2f}"
                  f"{bu:>+10.2f}{hit:>7.1f}{cap:>13.2f}")
        return out

    by_reason = table("reason")
    by_band = table("band")

    print("\n  cross tab (class x band), leak $ vs holding the SAME horizon:")
    print(f"    {'class':<26}{'band':<10}{'n':>5}{'booked%':>10}{'held%':>9}{'leak%':>9}{'leak $':>10}")
    cells = {}
    for r in recs:
        cells.setdefault((r["reason"], r["band"]), []).append(r)
    for (cls, band), rs in sorted(cells.items(),
                                  key=lambda kv: sum(x["booked_usd"] - x["hold_usd"] for x in kv[1])):
        n = len(rs)
        bp = statistics.mean(x["booked_pct"] for x in rs)
        hp = statistics.mean(x["hold_pct"] for x in rs)
        lk = sum(x["booked_usd"] - x["hold_usd"] for x in rs)
        print(f"    {cls:<26}{band:<10}{n:>5}{bp:>+10.3f}{hp:>+9.3f}{bp-hp:>+9.3f}{lk:>+10.2f}")
        cells[(cls, band)] = {"n": n, "leak_usd": round(lk, 2)}

    # what if every trade simply held to the shipped 180m window?
    have180 = [r for r in recs if r.get("hold180_pct") is not None]
    if have180:
        b180 = sum(r["booked_usd"] for r in have180)
        h180 = sum(r["hold180_pct"] / 100.0 * r["size"] for r in have180)
        print("\n  counterfactual: hold EVERY trade to the shipped 180m window "
              f"({len(have180)} trades with tape coverage)")
        print(f"    booked {b180:+9.2f} $   vs   held-to-180m {h180:+9.2f} $   "
              f"(exit effect {b180 - h180:+.2f} $)")
        # the risk side of that counterfactual: tails, not just the mean
        import bisect as _b
        def q(vals, f):
            v = sorted(vals)
            return v[min(len(v) - 1, int(f * (len(v) - 1)))]
        hb = [r["booked_pct"] for r in have180]
        hh = [r["hold180_pct"] for r in have180]
        mae = []
        for r in have180:
            sym = r["pair"]
            lo = close_low_since(tape[sym], stamps[sym], r["opened"], 180)
            mae.append(lo if lo is not None else 0.0)
        print(f"    tails: booked 1%/5% worst {q(hb,0.01):+.2f}% / {q(hb,0.05):+.2f}%  |  "
              f"held 1%/5% worst {q(hh,0.01):+.2f}% / {q(hh,0.05):+.2f}%")
        print(f"    hold-to-180m worst adverse excursion inside the window: "
              f"mean {statistics.mean(mae):+.2f}%, 5% worst {q(mae,0.05):+.2f}%, "
              f"1% worst {q(mae,0.01):+.2f}%")
        print(f"    trades where holding the window beat the exit: "
              f"{sum(1 for r in have180 if r['hold180_pct'] > r['booked_pct'])}/{len(have180)} "
              f"({100*sum(1 for r in have180 if r['hold180_pct'] > r['booked_pct'])/len(have180):.0f}%)")

    worst = min(by_reason.items(), key=lambda kv: kv[1]["leak_usd"])
    print(f"\n  BIGGEST SINGLE LEAK: {worst[0]} — {worst[1]['n']} trades, "
          f"leak {worst[1]['leak_usd']:+.2f} $ "
          f"({worst[1]['leak_pct']:+.3f}%/trade: booked {worst[1]['booked_pct']:+.3f}% "
          f"vs held {worst[1]['held_pct']:+.3f}%)")
    tot_abs = sum(abs(v["leak_usd"]) for v in by_reason.values())
    print(f"  share of all exit-derived P&L swing: "
          f"{100*abs(worst[1]['leak_usd'])/tot_abs if tot_abs else 0:.0f}%")

    RESULT.write_text(json.dumps({"trades": len(recs), "days": args.days,
                                  "booked_usd": round(booked_usd, 2),
                                  "held_usd": round(hold_usd, 2),
                                  "leak_usd": round(leak_usd, 2),
                                  "by_reason": by_reason, "by_band": by_band,
                                  "biggest_leak": worst[0]}, indent=1), encoding="utf-8")
    print("\nwrote scratch/_exit_leak_result.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
