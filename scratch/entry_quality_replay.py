"""ENTRY-QUALITY REPLAY (Nov 15) — do low entries actually pay?

Runs over the closes already on disk and asks one question: when the scout bought
near the low of the prior hour's range, did the trade pay better than when it
bought near the high?

Where the numbers come from, stated plainly:

  outcomes   contest_trades.jsonl — every real close (entry, exit, pnl_net,
             held_min, sold_at). Nothing simulated.
  timing     the local 5m tape (scratch/kraken_5m_long_183d_aligned.json).
             The stored rows carry no entry_quality (the field is new, and
             `_timing_fields` postdates every close in this file), so position in
             the prior hour is REBUILT from bars: (entry - low) / (high - low)
             over the twelve 5m bars strictly before the entry bar — the same
             definition `entry_quality` uses, which is why it is a fair stand-in
             and also why it is not the field itself.

Run:  .venv/Scripts/python.exe -X utf8 scratch/entry_quality_replay.py

Prints sample counts, the bucket table, the low-vs-high contrast with confidence
intervals, a split-half check, and a plain verdict including the sample size the
effect would need. It never writes to any live state.
"""
from __future__ import annotations

import bisect
import json
import math
import random
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The aligned archive first (29 pairs, 183 days), then the coverage backfill that
# closed the gap for the 26 other traded pairs (scratch/fetch_missing_tape.py).
# Both are the repo's own Kraken-Futures-charts 5m tape; merging by timestamp
# means a symbol in both keeps every bar either archive served.
ARCHIVES = [ROOT / "scratch" / "kraken_5m_long_183d_aligned.json",
            ROOT / "scratch" / "kraken_5m_cover_2026-11-15.json"]
TRADES = ROOT / "contest_trades.jsonl"
PRIOR_BARS = 12          # twelve 5m bars = the prior hour, same as TIMING_RANGE_CANDLES
MIN_BARS = 6             # a window thinner than this is not a range


def load_tape():
    """Every 5m bar the repo has, merged across archives by timestamp."""
    merged: dict[str, dict[int, list]] = {}
    origin = {}
    for path in ARCHIVES:
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        for sym, bars in (raw.get("pairs") or {}).items():
            slot = merged.setdefault(sym, {})
            origin.setdefault(sym, path.name)
            for b in bars:
                slot.setdefault(int(b[0]), b)
        for sym in (raw.get("refused") or {}):
            origin.setdefault(sym, "refused by venue")
    pairs = {}
    for sym, by_ts in merged.items():
        rows = [by_ts[t] for t in sorted(by_ts)]
        pairs[sym] = {"ts": [int(b[0]) for b in rows],
                      "hi": [float(b[2]) for b in rows],
                      "lo": [float(b[3]) for b in rows]}
    return pairs, origin


def iso_epoch(s):
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def position(pair_bars, at_ts, price):
    """(price - low) / (high - low) over the bars strictly before at_ts."""
    ts = pair_bars["ts"]
    # An entry the tape cannot actually bracket is NOT positioned: past the last
    # bar the "prior hour" would be a stale window from days earlier, and that is
    # a fabricated measurement, not a thin one. Refuse both ends explicitly.
    if not ts or at_ts > ts[-1] + 300 or at_ts < ts[0]:
        return None
    i = bisect.bisect_left(ts, at_ts)          # first bar at/after the entry
    if i <= MIN_BARS or i < PRIOR_BARS:
        return None
    lo_i, hi_i = i - PRIOR_BARS, i
    if hi_i - lo_i < MIN_BARS:
        return None
    hi = max(pair_bars["hi"][lo_i:hi_i])
    lo = min(pair_bars["lo"][lo_i:hi_i])
    if not (hi > lo) or price <= 0:
        return None
    return max(0.0, min(1.0, (price - lo) / (hi - lo)))


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def boot_diff(a, b, n=4000, seed=7):
    """Bootstrap CI for mean(a) - mean(b)."""
    if not a or not b:
        return None
    rnd = random.Random(seed)
    diffs = []
    na, nb = len(a), len(b)
    for _ in range(n):
        sa = sum(a[rnd.randrange(na)] for _ in range(na)) / na
        sb = sum(b[rnd.randrange(nb)] for _ in range(nb)) / nb
        diffs.append(sa - sb)
    diffs.sort()
    return diffs[int(0.025 * n)], diffs[int(0.975 * n)]


def need_n(p, delta, power=0.84, z=1.96):
    """Closes per arm to resolve a win-rate gap of `delta` from baseline `p`."""
    if delta <= 0:
        return None
    return math.ceil(2 * (z + power) ** 2 * p * (1 - p) / (delta ** 2))


def bucket(eq):
    for cut, name in ((0.2, "0.0-0.2 low"), (0.4, "0.2-0.4"),
                      (0.6, "0.4-0.6"), (0.8, "0.6-0.8")):
        if eq < cut:
            return name
    return "0.8-1.0 high"


def main():
    pairs, origin = load_tape()
    rows, skipped = [], {"no_tape_pair": 0, "no_time": 0, "no_numbers": 0,
                         "no_window": 0, "stored_eq": 0, "stored_timing_eq": 0}
    missing_syms = {}
    window_miss: dict[str, dict[str, int]] = {}
    with open(TRADES, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            sym = str(r.get("pair") or "").split("/")[0]
            bars = pairs.get(sym)
            if bars is None:
                skipped["no_tape_pair"] += 1
                missing_syms[sym] = missing_syms.get(sym, 0) + 1
                continue
            try:
                entry, exit_ = float(r.get("entry")), float(r.get("exit"))
                net, held = float(r.get("pnl_net")), float(r.get("held_min") or 0)
            except Exception:
                skipped["no_numbers"] += 1
                continue
            sold = iso_epoch(r.get("sold_at"))
            if sold is None:
                skipped["no_time"] += 1
                continue
            # stored fields first (none exist yet), reconstruction after
            eq, src = None, None
            if isinstance(r.get("entry_quality"), (int, float)) and r.get("entry_quality_v"):
                eq, src = float(r["entry_quality"]), "entry_quality"
                skipped["stored_eq"] += 1
            elif isinstance(r.get("timing_eq"), (int, float)):
                eq, src = float(r["timing_eq"]), "timing_eq"
                skipped["stored_timing_eq"] += 1
            else:
                eq = position(bars, sold - held * 60.0, entry)
                src = "rebuilt"
            if eq is None:
                skipped["no_window"] += 1
                # say WHICH kind of miss, so the residual is a number and a reason
                at = sold - held * 60.0
                ts_b = bars["ts"]
                if not ts_b:
                    why = "tape empty"
                elif at < ts_b[0]:
                    why = "entry before the tape's first bar"
                elif at > ts_b[-1]:
                    why = "entry after the tape's last bar"
                else:
                    j = bisect.bisect_left(ts_b, at)
                    w_hi = max(bars["hi"][max(0, j - PRIOR_BARS):j] or [0.0])
                    w_lo = min(bars["lo"][max(0, j - PRIOR_BARS):j] or [0.0])
                    why = ("window has no range (degenerate tape: high == low)"
                           if w_hi <= w_lo else "window thinner than 6 bars")
                window_miss.setdefault(why, {}).setdefault(sym, 0)
                window_miss[why][sym] += 1
                continue
            rows.append({"pair": r.get("pair"), "eq": eq, "src": src, "net": net,
                         "pct": (exit_ / entry - 1.0) * 100.0 if entry > 0 else None,
                         "win": net > 0, "sold": sold, "held": held})

    total = sum(1 for line in open(TRADES, encoding="utf-8") if line.strip())
    print("== sample")
    print(f"closes on disk           {total}")
    print(f"usable with a timing pos {len(rows)}  ({100 * len(rows) / total:.1f}%)")
    print(f"tape archives            {', '.join(p.name for p in ARCHIVES if p.exists())}"
          f"  ({len(pairs)} symbols on disk)")
    print(f"  of which stored entry_quality {skipped['stored_eq']}"
          f", stored timing_eq {skipped['stored_timing_eq']}"
          f", rebuilt from the 5m tape {sum(1 for r in rows if r['src'] == 'rebuilt')}")
    print(f"excluded                 {skipped}")
    for why, syms in window_miss.items():
        n = sum(syms.values())
        print(f"  no window: {why}  x{n}  (" + ", ".join(
            f"{s} x{c}" for s, c in sorted(syms.items(), key=lambda kv: -kv[1])[:6])
            + (" ..." if len(syms) > 6 else "") + ")")
    if missing_syms:
        print("  residual, by symbol: " + ", ".join(
            f"{s} x{n}" for s, n in sorted(missing_syms.items(), key=lambda kv: -kv[1])))
        for s in sorted(missing_syms):
            print(f"    {s:<9} x{missing_syms[s]:<4} {origin.get(s, 'no archive at all')}")
    if not rows:
        print("\nVERDICT: no usable closes — nothing to resolve.")
        return 1

    vals = [r["eq"] for r in rows]
    print(f"eq spread: min {min(vals):.3f}  median {statistics.median(vals):.3f}"
          f"  max {max(vals):.3f}")

    print("\n== realized outcome by where the entry sat in its prior hour")
    print(f"{'bucket':<14}{'n':>5}{'win%':>7}{'95% CI':>16}"
          f"{'mean%':>8}{'med%':>8}{'net $':>11}")
    groups = {}
    for r in rows:
        groups.setdefault(bucket(r["eq"]), []).append(r)
    for name in ("0.0-0.2 low", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0 high"):
        g = groups.get(name) or []
        if not g:
            print(f"{name:<14}{0:>5}      -")
            continue
        wins = sum(1 for r in g if r["win"])
        lo, hi = wilson(wins, len(g))
        pcts = [r["pct"] for r in g if r["pct"] is not None]
        print(f"{name:<14}{len(g):>5}{100 * wins / len(g):>6.1f}%"
              f"{100 * lo:>7.1f}-{100 * hi:<6.1f}"
              f"{statistics.mean(pcts):>7.2f}{statistics.median(pcts):>8.2f}"
              f"{sum(r['net'] for r in g):>11.2f}")

    def arm(sel):
        g = [r for r in rows if sel(r["eq"])]
        pcts = [r["pct"] for r in g if r["pct"] is not None]
        return g, pcts

    for label, low_sel, high_sel in (
            ("quintile: <=0.25 vs >=0.75", lambda e: e <= 0.25, lambda e: e >= 0.75),
            ("thirds:   <=0.33 vs >=0.67", lambda e: e <= 0.33, lambda e: e >= 0.67)):
        lo_g, lo_p = arm(low_sel)
        hi_g, hi_p = arm(high_sel)
        if not lo_g or not hi_g:
            print(f"\n{label}: empty arm — not resolvable")
            continue
        lw = sum(1 for r in lo_g if r["win"])
        hw = sum(1 for r in hi_g if r["win"])
        print(f"\n== low vs high — {label}")
        print(f"low  n={len(lo_g):<5} win {100 * lw / len(lo_g):5.1f}%  "
              f"mean {statistics.mean(lo_p):+.2f}%  net {sum(r['net'] for r in lo_g):+.2f}")
        print(f"high n={len(hi_g):<5} win {100 * hw / len(hi_g):5.1f}%  "
              f"mean {statistics.mean(hi_p):+.2f}%  net {sum(r['net'] for r in hi_g):+.2f}")
        d = lw / len(lo_g) - hw / len(hi_g)
        llo, lhi = wilson(lw, len(lo_g))
        hlo, hhi = wilson(hw, len(hi_g))
        print(f"win-rate difference {100 * d:+.1f}pp  "
              f"(low {100 * llo:.1f}-{100 * lhi:.1f} / high {100 * hlo:.1f}-{100 * hhi:.1f})")
        ci = boot_diff(lo_p, hi_p)
        print(f"mean-pnl difference {statistics.mean(lo_p) - statistics.mean(hi_p):+.3f}pp  "
              f"bootstrap 95% CI {('%.3f to %.3f' % ci) if ci else 'n/a'}")
        # split-half: does the sign survive on both halves of the ledger?
        med = statistics.median([r["sold"] for r in rows])
        for half, selh in (("H1", lambda r: r["sold"] <= med), ("H2", lambda r: r["sold"] > med)):
            a = [r for r in lo_g if selh(r)]
            b = [r for r in hi_g if selh(r)]
            if a and b:
                print(f"  {half}: low n={len(a):<4} win {100 * sum(r['win'] for r in a) / len(a):5.1f}%"
                      f"  high n={len(b):<4} win {100 * sum(r['win'] for r in b) / len(b):5.1f}%"
                      f"  diff {100 * (sum(r['win'] for r in a) / len(a) - sum(r['win'] for r in b) / len(b)):+.1f}pp")
        base = hw / len(hi_g)
        print(f"  n per arm for +5pp at 80% power: {need_n(base, 0.05)}"
              f"   for +8pp: {need_n(base, 0.08)}"
              f"   for the {100 * abs(d):.1f}pp observed: {need_n(base, abs(d))}")

    print("\n== verdict")
    lo_g, lo_p = arm(lambda e: e <= 0.25)
    hi_g, hi_p = arm(lambda e: e >= 0.75)
    if lo_g and hi_g:
        lw = sum(1 for r in lo_g if r["win"]) / len(lo_g)
        hw = sum(1 for r in hi_g if r["win"]) / len(hi_g)
        llo, lhi = wilson(int(lw * len(lo_g)), len(lo_g))
        hlo, hhi = wilson(int(hw * len(hi_g)), len(hi_g))
        resolvable = (llo > hhi) or (hlo > lhi)
        print(f"low-entry win {100 * lw:.1f}% (n={len(lo_g)}) vs high-entry "
              f"{100 * hw:.1f}% (n={len(hi_g)}); the intervals "
              f"{'do NOT overlap' if resolvable else 'OVERLAP'}.")
        if not resolvable:
            print("NOT RESOLVABLE at this sample: the difference is smaller than the noise.")
        print(f"mean pnl {statistics.mean(lo_p):+.2f}% vs {statistics.mean(hi_p):+.2f}%"
              f" = {statistics.mean(lo_p) - statistics.mean(hi_p):+.2f}pp per trade.")
    print("Caveat: the timing number here is REBUILT from the 5m tape (no stored close "
          "carries entry_quality yet). It measures the same quantity the tournament "
          "would rank on, but it is not the field itself.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
