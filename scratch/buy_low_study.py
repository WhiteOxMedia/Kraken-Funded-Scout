"""BUY LOW, SELL HIGH — graded on the data already on disk (Nov 15).

Two questions, answered with numbers rather than opinions:

  A. THE SIGNAL. For every stored close, reconstruct how cheap the entry was —
     where the price sat in the prior 12 hourly bars (the live `entry_quality`
     definition) and where it sat in that day's range — and band the realized
     outcome by it. If cheap entries do not pay better, the ranking has nothing
     to prefer.

  B. THE DECISION. At historical decision points, run the scanner's own
     `evaluate_strategy` over every pair's 1h tape, take the buy candidates, and
     ask the real question: the coin the bracket actually picks (highest
     confidence) versus the coin this ranking prefers (cheapest near-equal).
     Both are then marked to the SAME forward horizon from the same tape, net of
     the round-trip cost the journal actually paid. That is the counterfactual
     the stored closes cannot express, because only the fired coin has a close.

Run:  .venv/Scripts/python.exe -X utf8 scratch/buy_low_study.py [--days N] [--step H]
"""
from __future__ import annotations

import bisect
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import buy_low as bl          # noqa: E402
import config as cfgmod      # noqa: E402
import scanner as sc          # noqa: E402

ARCHIVES = [ROOT / "scratch" / "kraken_5m_long_183d_aligned.json",
            ROOT / "scratch" / "kraken_5m_cover_2026-11-15.json"]
TRADES = ROOT / "contest_trades.jsonl"
DAYS = 45
STEP_H = 6
LOOKBACK = 120          # hourly bars the scanner evaluates on (>=30 required)


def load_tape():
    merged: dict[str, dict[int, list]] = {}
    for path in ARCHIVES:
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        for sym, bars in (raw.get("pairs") or {}).items():
            slot = merged.setdefault(sym, {})
            for b in bars:
                slot.setdefault(int(b[0]), b)
    out = {}
    for sym, by_ts in merged.items():
        out[sym] = [by_ts[t] for t in sorted(by_ts)]
    return out


def hourly(bars):
    """5m bars -> 1h bars (o/h/l/c/v), ascending, no gaps filled."""
    buckets = {}
    for b in bars:
        h = int(b[0]) // 3600 * 3600
        cur = buckets.get(h)
        if cur is None:
            buckets[h] = [b[0], b[1], b[2], b[3], b[4], b[5]]
        else:
            cur[2] = max(cur[2], b[2])
            cur[3] = min(cur[3], b[3])
            cur[4] = b[4]
            cur[5] = cur[5] + b[5]
    return [[h * 1000] + buckets[h][1:] for h in sorted(buckets)]


_STAMPS: dict[int, tuple[int, list]] = {}


def _stamps(bars):
    """Epoch-second stamps for a tape list, computed once per list."""
    key = id(bars)
    hit = _STAMPS.get(key)
    if hit is not None and hit[0] == len(bars):
        return hit[1]
    idx = [int(b[0]) for b in bars]
    _STAMPS[key] = (len(bars), idx)
    return idx


def cheap_at(bars_5m, ts):
    """cheapness at `ts` from the tape, same definition the live read uses.

    `ts` is epoch SECONDS, which is what the archives store (not ms).
    """
    stamps = _stamps(bars_5m)
    hi_i = bisect.bisect_right(stamps, ts) - 1
    if hi_i is None or hi_i < 12:
        return None
    px = float(bars_5m[hi_i][4])
    win = bars_5m[hi_i - 11:hi_i + 1]
    hi = max(float(b[2]) for b in win)
    lo = min(float(b[3]) for b in win)
    if not (hi > lo):
        return None
    pos_hour = (px - lo) / (hi - lo)
    # the day's range: from the UTC midnight bar
    day_start = int(ts) // 86400 * 86400
    d0 = bisect.bisect_left(stamps, day_start)
    day = bars_5m[d0:hi_i + 1]
    pos_day = None
    if len(day) >= 12:
        dhi = max(float(b[2]) for b in day)
        dlo = min(float(b[3]) for b in day)
        if dhi > dlo:
            pos_day = (px - dlo) / (dhi - dlo)
    parts = [p for p in (pos_hour, pos_day) if p is not None]
    cheap = 1.0 - sum(parts) / len(parts)
    return {"cheap": cheap, "pos_hour": pos_hour, "pos_day": pos_day,
            "px": px}


def fwd_pct(bars_5m, ts, minutes):
    """(exit/entry - 1) over `minutes` from the bar at/after ts."""
    stamps = _stamps(bars_5m)
    i0 = bisect.bisect_left(stamps, ts)
    if i0 >= len(bars_5m):
        return None
    j = bisect.bisect_left(stamps, ts + minutes * 60)
    if j >= len(bars_5m) or float(bars_5m[i0][4]) <= 0:
        return None
    return (float(bars_5m[j][4]) / float(bars_5m[i0][4]) - 1.0) * 100.0


def wilson(k, n, z=1.96):
    if not n:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def band(cheap):
    if cheap >= 0.75:
        return "bought at the low (>=0.75 cheap)"
    if cheap >= 0.6:
        return "0.60-0.75"
    if cheap >= 0.4:
        return "0.40-0.60"
    if cheap >= 0.25:
        return "0.25-0.40"
    return "extended (<=0.25 cheap)"


def part_a(tape):
    rows, skipped = [], {"no_pair": 0, "no_window": 0, "no_numbers": 0}
    with open(TRADES, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                sym = str(r["pair"]).split("/")[0]
                entry, exit_ = float(r["entry"]), float(r["exit"])
                size, net = float(r["size"]), float(r["pnl_net"])
                sold = datetime.fromisoformat(r["sold_at"]).timestamp()
                held = float(r["held_min"] or 0)
            except Exception:
                skipped["no_numbers"] += 1
                continue
            bars = tape.get(sym)
            if not bars:
                skipped["no_pair"] += 1
                continue
            c = cheap_at(bars, sold - held * 60.0)
            if c is None:
                skipped["no_window"] += 1
                continue
            rows.append({**c, "net": net, "size": size,
                         "pct": (exit_ / entry - 1.0) * 100.0 if entry > 0 else None,
                         "win": net > 0})
    print("== A. THE SIGNAL — stored closes by how cheap the entry was")
    print(f"closes on disk {sum(1 for _ in open(TRADES, encoding='utf-8'))}  "
          f"usable {len(rows)}  excluded {skipped}")
    if not rows:
        return
    groups = defaultdict(list)
    for r in rows:
        groups[band(r["cheap"])].append(r)
    print(f"\n{'band':<32}{'n':>5}{'win%':>7}{'95% CI':>15}{'mean%':>8}{'net $':>10}{'net/$1':>9}")
    for name in ("bought at the low (>=0.75 cheap)", "0.60-0.75", "0.40-0.60",
                 "0.25-0.40", "extended (<=0.25 cheap)"):
        g = groups.get(name) or []
        if not g:
            continue
        w = sum(1 for r in g if r["win"])
        lo, hi = wilson(w, len(g))
        pcts = [r["pct"] for r in g if r["pct"] is not None]
        spent = sum(r["size"] for r in g) or 1.0
        print(f"{name:<32}{len(g):>5}{100 * w / len(g):>6.1f}%"
              f"{100 * lo:>7.1f}-{100 * hi:<6.1f}"
              f"{statistics.mean(pcts):>8.2f}{sum(r['net'] for r in g):>10.2f}"
              f"{(sum(r['net'] for r in g) / spent):>9.5f}")
    lo_g = groups.get("bought at the low (>=0.75 cheap)") or []
    hi_g = groups.get("extended (<=0.25 cheap)") or []
    if lo_g and hi_g:
        print(f"\nlow vs extended: win {100 * sum(1 for r in lo_g if r['win']) / len(lo_g):.1f}% "
              f"(n={len(lo_g)}) vs {100 * sum(1 for r in hi_g if r['win']) / len(hi_g):.1f}% "
              f"(n={len(hi_g)}); net/$1 "
              f"{sum(r['net'] for r in lo_g) / max(1, sum(r['size'] for r in lo_g)):+.5f} vs "
              f"{sum(r['net'] for r in hi_g) / max(1, sum(r['size'] for r in hi_g)):+.5f}")


def part_b(tape, days=DAYS, step_h=STEP_H):
    """Paired replay: confidence champion vs cheapness champion, same horizon."""
    cfg = cfgmod.TradingConfig()
    cfg.mtf_enabled = False
    cfg.liquidity_filter_enabled = False
    cfg.deep_scan_enabled = False

    # hourly series once per pair (the interval the scanner evaluates on)
    hourly_tape = {sym: (hb, [int(b[0]) for b in hb])
                   for sym, bars in tape.items() if len(bars) > 500
                   for hb in [hourly(bars)]}
    end = max(b[-1][0] for b in tape.values())        # epoch seconds
    start = end - days * 86400

    # the cost the journal actually paid, as a percentage of size
    costs = []
    with open(TRADES, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
                if float(r["size"]) > 0:
                    costs.append(float(r["cost_rt"]) / float(r["size"]) * 100.0)
            except Exception:
                continue
    cost_pct = statistics.median(costs) if costs else 0.0
    horizon = 60.0

    points = 0
    rows = []
    paired_all = []
    ts = int(start // 3600 * 3600)
    while ts <= end:
        cands = []
        for sym, rec in hourly_tape.items():
            hb, stamps = rec
            i = bisect.bisect_right(stamps, ts * 1000) - 1
            if i < 60:
                continue
            window = hb[max(0, i - LOOKBACK + 1):i + 1]
            try:
                df = sc._ohlcv_to_df(window)
                sig = sc.evaluate_strategy(df, cfg)
            except Exception:
                continue
            if sig.action != "buy":
                continue
            c = cheap_at(tape[sym], ts)
            if c is None:
                continue
            cands.append({"pair": sym, "conf": float(sig.confidence),
                          "cheap": c["cheap"], "px": c["px"]})
        if len(cands) >= 2:
            points += 1
            conf_pick = max(cands, key=lambda c: c["conf"])
            near = [c for c in cands
                    if c["conf"] >= conf_pick["conf"] - bl.BUY_LOW_CONF_WINDOW]
            cheap_pick = max(near, key=lambda c: c["cheap"])
            fc = fwd_pct(tape[conf_pick["pair"]], ts, horizon)
            cc = fwd_pct(tape[cheap_pick["pair"]], ts, horizon)
            if fc is not None and cc is not None:
                paired_all.append({"t": ts, "fired": conf_pick["pair"],
                                   "pref": cheap_pick["pair"],
                                   "fired_pct": fc, "pref_pct": cc})
            if cheap_pick["pair"] != conf_pick["pair"]:
                if fc is not None and cc is not None:
                    rows.append({"t": ts, "fired": conf_pick["pair"],
                                 "conf": conf_pick["conf"], "fired_cheap": conf_pick["cheap"],
                                 "pref": cheap_pick["pair"], "pref_conf": cheap_pick["conf"],
                                 "pref_cheap": cheap_pick["cheap"],
                                 "fired_pct": fc, "pref_pct": cc})
        ts += step_h * 3600

    print(f"\n== B. THE DECISION — {days}d, every {step_h}h, horizon {horizon:.0f}m, "
          f"cost {cost_pct:.3f}%/trade")
    print(f"decision points with >=2 buy candidates: {points}")
    print(f"points where the ranking would have changed the coin: {len(rows)}")
    # THE PAIRED TEST OVER EVERY POINT, not only the ones that differ: when the
    # ranking agrees with the bracket the difference is exactly zero, which is
    # the honest contribution of that decision to the total.
    if paired_all:
        d_all = [p["pref_pct"] - p["fired_pct"] for p in paired_all]
        m = statistics.mean(d_all)
        se_all = (statistics.stdev(d_all) / math.sqrt(len(d_all))
                  if len(d_all) > 1 else 0.0)
        print(f"\nall {len(paired_all)} points, preferred-minus-fired: {m:+.3f}pp "
              f"+/- {1.96 * se_all:.3f} (95%)")
        same = sum(1 for p in paired_all if p["pref"] == p["fired"])
        print(f"  (the ranking agreed with the bracket at {same} of them)")
    if not rows:
        print("nothing to grade — the two champions never disagreed")
        return
    for label, key in (("what fired (highest confidence)", "fired_pct"),
                       ("what the ranking preferred", "pref_pct")):
        vals = [r[key] - cost_pct for r in rows]
        wins = sum(1 for v in vals if v > 0)
        lo, hi = wilson(wins, len(vals))
        print(f"  {label:<34} n={len(vals):<4} win {100 * wins / len(vals):5.1f}% "
              f"({100 * lo:.1f}-{100 * hi:.1f})  mean {statistics.mean(vals):+.3f}%  "
              f"net/$1 {statistics.mean(vals) / 100:+.5f}")
    d = [r["pref_pct"] - r["fired_pct"] for r in rows]
    mean_d = statistics.mean(d)
    se = statistics.stdev(d) / math.sqrt(len(d)) if len(d) > 1 else 0.0
    print(f"  paired difference (preferred - fired): {mean_d:+.3f}pp "
          f"+/- {1.96 * se:.3f} (95%)  n={len(d)}")
    if mean_d - 1.96 * se > 0:
        print("  => the ranking ADDED return on this sample (CI above zero).")
    elif mean_d + 1.96 * se < 0:
        print("  => the ranking COST return on this sample (CI below zero).")
    else:
        print("  => not resolvable at this sample: the interval straddles zero.")
    print(f"  cost included: {cost_pct:.3f}% per trade (median cost_rt/size in the journal)")


def main():
    days = DAYS
    step = STEP_H
    args = sys.argv[1:]
    if "--days" in args:
        days = int(args[args.index("--days") + 1])
    if "--step" in args:
        step = int(args[args.index("--step") + 1])
    tape = load_tape()
    print(f"tape: {len(tape)} symbols")
    part_a(tape)
    part_b(tape, days=days, step_h=step)
    print("\nCaveat: B is a replay on the tape, not the booted scanner — the "
          "strategy sees 120 hourly bars and both arms exit on the same fixed "
          "horizon, so it prices the CHOICE, not the exit rules. A is the "
          "stored closes themselves.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
