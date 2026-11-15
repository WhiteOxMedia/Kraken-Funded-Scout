"""ENTRY TIMING — does the shipped entry buy low enough? (Nov 15)

Every real entry in `contest_trades.jsonl` is replayed on the stored 5m closes and
re-entered three other ways, with the SAME exit rule for all four (the shipped
shape: hard stop −3%, the profit-guard trail, or the 180m close) so that only the
entry timing moves:

  shipped    enter on the signal bar's close — what the scanner does
  next5m     enter on the following 5m close (one bar of confirmation)
  pullback   wait up to 12 bars (60m) for a bar that trades into the prior hour's
             low decile; take the close at the deadline if it never comes
  ext_cap    skip the trade entirely when the signal bar is already more than
             1.0% above the session base (the prior hour's low) — the chase guard

Charged the journal's own round trip on every variant; the hard stop is −3% in
all four, by construction. Reported on a chronological fit split and a disjoint
holdout, against the SHIPPED entry, with the sample size stated: a variant wins
only if it improves $ per trade on the holdout without worsening either tail.

Run: .venv/Scripts/python.exe -X utf8 scratch/entry_timing_variants.py
"""
from __future__ import annotations

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
RESULT = ROOT / "scratch" / "_entry_variant_result.json"
WINDOW_MIN = 180.0
STOP_PCT = 3.0
TRAIL_PCT = 1.5
ARM_FRAC = 0.5
BASE_LOOKBACK = 12        # bars of 5m = the prior hour
PULLBACK_BARS = 12        # how long a pullback entry may wait
EXT_CAP_PCT = 1.0         # max extension above the session base
PULLBACK_DECILE = 0.10    # "low decile" of the prior hour's range


def parse_ts(s):
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def exit_from(bars, stalls, i0, entry, goal_pct, cost_pct):
    """The shipped exit shape from a given entry bar: stop, trail, or 180m."""
    stop = entry * (1.0 - STOP_PCT / 100.0)
    i1 = bisect.bisect_right(stalls, stalls[i0] + WINDOW_MIN * 60)
    peak, armed, mae = entry, False, 0.0
    for bar in bars[i0:i1]:
        hi, lo, cl = float(bar[2]), float(bar[3]), float(bar[4])
        mae = min(mae, lo / entry - 1.0)
        if lo <= stop:
            return (stop / entry - 1.0) * 100.0 - cost_pct, mae * 100.0
        peak = max(peak, hi)
        if not armed and (peak / entry - 1.0) * 100.0 >= goal_pct * ARM_FRAC:
            armed = True
        if armed and cl <= peak * (1.0 - TRAIL_PCT / 100.0):
            return (cl / entry - 1.0) * 100.0 - cost_pct, mae * 100.0
    return (float(bars[i1 - 1][4]) / entry - 1.0) * 100.0 - cost_pct, mae * 100.0


def base_and_decile(bars, i0):
    lo_i = max(0, i0 - BASE_LOOKBACK)
    seg = bars[lo_i:i0]
    if not seg:
        return None, None
    lo = min(float(b[3]) for b in seg)
    hi = max(float(b[2]) for b in seg)
    if not (hi > lo):
        return lo, lo
    return lo, lo + (hi - lo) * PULLBACK_DECILE


def run_variant(bars, stalls, i0, mode, goal, cost_pct):
    """Returns (booked_pct, mae_pct) or None when the variant does not trade."""
    if i0 + 1 >= len(bars):
        return None
    if mode == "shipped":
        j = i0
    elif mode == "next5m":
        j = i0 + 1
    elif mode == "ext_cap":
        base, _ = base_and_decile(bars, i0)
        px = float(bars[i0][1])
        if base and base > 0 and (px / base - 1.0) * 100.0 > EXT_CAP_PCT:
            return None                       # skipped: the chase
        j = i0
    elif mode == "pullback":
        j = None
        _, decile = base_and_decile(bars, i0)
        for k in range(i0, min(i0 + PULLBACK_BARS, len(bars) - 1)):
            if decile is not None and float(bars[k][3]) <= decile:
                j = k
                break
        if j is None:
            j = min(i0 + PULLBACK_BARS, len(bars) - 2)
    else:
        raise SystemExit(mode)
    entry = float(bars[j][1])
    if entry <= 0:
        return None
    return exit_from(bars, stalls, j, entry, goal, cost_pct)


def q(vals, f):
    v = sorted(vals)
    return v[min(len(v) - 1, int(f * (len(v) - 1)))] if v else 0.0


def summarise(rows, label, modes):
    out = {}
    print(f"    {'variant':<12}{'n':>5}{'skipped':>8}{'booked%':>10}{'$ total':>10}"
          f"{'$/trade':>10}{'hit%':>7}{'mean MAE':>10}{'1%':>8}{'5%':>8}")
    for mode in modes:
        vals = [(r, r[mode]) for r in rows]
        traded = [(r, v) for r, v in vals if v is not None]
        if not traded:
            continue
        pct = [v[0] for _, v in traded]
        usd = [v[0] / 100.0 * r["size"] for r, v in traded]
        mae = [v[1] for _, v in traded]
        d = {"n_traded": len(traded), "skipped": len(vals) - len(traded),
             "booked_pct": round(statistics.mean(pct), 4),
             "usd": round(sum(usd), 2),
             "per_trade": round(sum(usd) / len(usd), 4),
             "hit": round(100.0 * sum(1 for x in pct if x > 0) / len(pct), 1),
             "mae": round(statistics.mean(mae), 4),
             "p1": round(q(pct, 0.01), 3), "p5": round(q(pct, 0.05), 3)}
        out[mode] = d
        print(f"    {label+' '+mode:<12}{d['n_traded']:>5}{d['skipped']:>8}"
              f"{d['booked_pct']:>+10.3f}{d['usd']:>+10.2f}{d['per_trade']:>+10.3f}"
              f"{d['hit']:>7.1f}{d['mae']:>+10.3f}{d['p1']:>+8.2f}{d['p5']:>+8.2f}")
    return out


def main():
    tape = load_tape()
    snaps = {s: ([int(b[0]) for b in bars]) for s, bars in tape.items()}
    trades = [json.loads(l) for l in TRADES.read_text(encoding="utf-8").splitlines() if l.strip()]
    latest = max(max(v) for v in snaps.values())
    start = latest - 45 * 86400
    modes = ("shipped", "next5m", "pullback", "ext_cap")

    recs = []
    for r in trades:
        sym = (r.get("pair") or "").split("/")[0]
        if sym not in tape:
            continue
        sold = parse_ts(r.get("sold_at") or "")
        if sold is None or sold < start:
            continue
        try:
            held = float(r.get("held_min") or 0.0)
            size = float(r.get("size") or 0.0)
            goal = float(r.get("goal_pct") or 5.0)
            cost_pct = 100.0 * (float(r.get("cost_rt") or 0.0) / size) if size else 0.0
        except (TypeError, ValueError):
            continue
        if not (size > 0 and held > 0):
            continue
        opened = sold - held * 60.0
        bars, st = tape[sym], snaps[sym]
        if opened + WINDOW_MIN * 60.0 > st[-1]:
            continue
        i0 = bisect.bisect_left(st, opened)
        if i0 >= len(bars) - 2:
            continue
        rec = {"sym": sym, "size": size, "t": opened}
        ok = False
        for mode in modes:
            got = run_variant(bars, st, i0, mode, goal, cost_pct)
            rec[mode] = got
            ok = ok or got is not None
        if ok:
            recs.append(rec)

    recs.sort(key=lambda r: r["t"])
    cut = int(len(recs) * 0.6)
    fit, hold = recs[:cut], recs[cut:]
    print(f"\n{'='*108}\nENTRY TIMING — {len(recs)} replayed entries | fit {len(fit)} | "
          f"holdout {len(hold)} | exit held identical for every variant\n{'='*108}")
    a = summarise(fit, "fit", modes)
    b = summarise(hold, "hold", modes)

    base = b["shipped"]
    print("\n  holdout verdict (needs better $/trade AND no worse 1% / 5% tail than shipped):")
    verdicts = {}
    for mode in ("next5m", "pullback", "ext_cap"):
        d = b[mode]
        better = d["per_trade"] > base["per_trade"]
        tail_ok = d["p1"] >= base["p1"] and d["p5"] >= base["p5"]
        verdicts[mode] = {"better_per_trade": better, "tail_ok": tail_ok,
                          "wins": better and tail_ok, "holdout_n": d["n_traded"]}
        print(f"    {mode:<9} n={d['n_traded']:<4} ${d['per_trade']:+.3f}/trade vs "
              f"${base['per_trade']:+.3f} ({'better' if better else 'worse'}); "
              f"1%/5% {d['p1']:+.2f}/{d['p5']:+.2f} vs {base['p1']:+.2f}/{base['p5']:+.2f} "
              f"({'ok' if tail_ok else 'WORSE'}) -> {'WIN' if better and tail_ok else 'reject'}")
    winners = [k for k, v in verdicts.items() if v["wins"]]
    thin = [k for k, v in verdicts.items() if v["holdout_n"] < 30]
    print(f"\n  winners: {winners or 'NONE'}")
    print(f"  holdout n < 30 (indeterminate sample): {thin or 'none'}")
    RESULT.write_text(json.dumps({"entries": len(recs), "fit": a, "holdout": b,
                                  "verdicts": verdicts, "winners": winners,
                                  "thin_holdout": thin}, indent=1), encoding="utf-8")
    print("wrote scratch/_entry_variant_result.json")


if __name__ == "__main__":
    main()
