"""exit_on_scanner.py — does the trend-ride exit transfer to the scanner's selection?

The 82.3%/84.1% shape was measured on band entries. This holds the EXIT fixed
(`overwatch_timing.manage`: 2% stop, trail arms at +0.4%, gives back 0.15%, no target)
and swaps only the entry source, two ways:

  A. contest_gate_log.jsonl — the scanner's kept gate fires. This file is only today
     (Nov 15, 07:22-16:59, 215 fires): there is no history in it and it carries no
     price, so entries are priced at the open of the next 5m bar fetched from Kraken's
     public API. Fires younger than the observation window cannot be resolved and are
     reported as such, not scored as losses.

  B. contest_trades.jsonl — the scanner's ACTUAL entries over May 14 - Nov 13, with the
     real fill price and time (sold_at - held_min). Re-priced with the trend-ride exit.
     This is the honest transfer test: identical selections, two exits (the journal's
     realized one and the trend-ride one). November is reported separately.

    python scratch/exit_on_scanner.py
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import overwatch_timing as ot
from trendride_study import prep

OUT = os.path.join(HERE, "_exit_on_scanner.json")
NOV1 = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc).timestamp()


# ── the exit, exactly as shipped ───────────────────────────────────────────
def ride(entry, highs, lows, start, max_bars=None):
    """Run ot.manage from bar `start`. Returns (net_pct, bars, reason) or None."""
    peak, stop = highs[start], ot.initial_stop(entry)
    upto = len(highs) if max_bars is None else min(len(highs), start + max_bars)
    for k in range(start, upto):
        peak, stop, exit_px, reason = ot.manage(entry=entry, peak=peak, stop=stop,
                                                bar_high=highs[k], bar_low=lows[k])
        if exit_px is not None:
            return ot.net_pct(entry, exit_px), k - start + 1, reason
    return None


def summarize(label, nets, extra=""):
    if not nets:
        print(f"  {label:<22} n=0")
        return {"n": 0}
    wins = sum(1 for x in nets if x > 0)
    sd = st.pstdev(nets) if len(nets) > 1 else 0.0
    d = {"n": len(nets), "win_pct": round(100 * wins / len(nets), 1),
         "mean_net_pct": round(st.fmean(nets), 4),
         "total_net_pct": round(sum(nets), 1),
         "median_hold_h": None,
         "t": round(st.fmean(nets) / (sd / len(nets) ** 0.5), 2) if sd else None}
    print(f"  {label:<22} n={d['n']:>5}  win {d['win_pct']:>5.1f}%  "
          f"net {d['mean_net_pct']:+.4f}%  t {d['t']}  {extra}")
    return d


# ── A. the gate log's kept fires ────────────────────────────────────────────
def part_gate_log():
    path = os.path.join(ROOT, "contest_gate_log.jsonl")
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    fires = [r for r in rows if r.get("decision") == "fire"]
    by_pair = {}
    for r in fires:
        by_pair.setdefault(r["pair"], []).append(r)
    lo = min(r["ts"] for r in fires)
    hi = max(r["ts"] for r in fires)

    import ccxt
    ex = ccxt.kraken({"enableRateLimit": True})
    nets, holds, unresolved, failed = [], [], 0, {}
    per_pair = {}
    for pair, fs in by_pair.items():
        try:
            ohlcv = ex.fetch_ohlcv(pair, timeframe="5m", limit=300)
        except Exception as exc:
            failed[pair] = str(exc).split("\n")[0][:70]
            continue
        if not ohlcv:
            failed[pair] = "no candles"
            continue
        ts = [b[0] / 1000.0 for b in ohlcv]
        hi_ = [b[2] for b in ohlcv]
        lo_ = [b[3] for b in ohlcv]
        op = [b[1] for b in ohlcv]
        pp = per_pair.setdefault(pair, [])
        for f in fs:
            t = dt.datetime.fromisoformat(f["ts"]).timestamp()
            i = bisect.bisect_right(ts, t)
            if i >= len(ts):                      # bar not formed yet
                unresolved += 1
                continue
            entry = op[i]
            got = ride(entry, hi_, lo_, i)
            if got is None:
                unresolved += 1
            else:
                nets.append(got[0])
                holds.append(got[1] * 5)
                pp.append(got[0])
    return {
        "log_rows": len(rows), "fires": len(fires), "pairs": len(by_pair),
        "window": [lo, hi],
        "resolved": len(nets), "unresolved": unresolved,
        "failed_pairs": failed,
        "all": summarize("gate fires (today)", nets),
        "median_hold_min": (round(st.median(holds)) if holds else None),
        "range": [round(min(nets), 3), round(max(nets), 3)] if nets else None,
        "fires_per_pair": sorted(((k, len(v)) for k, v in per_pair.items()),
                                 key=lambda kv: -kv[1]),
        "pairs_scored": {k: {"n": len(v), "win_pct": round(100 * sum(1 for x in v if x > 0) / len(v), 1),
                             "mean_net_pct": round(st.fmean(v), 3)}
                         for k, v in sorted(per_pair.items()) if v},
        "pairs_positive": sum(1 for v in per_pair.values() if v and st.fmean(v) > 0),
        "pairs_with_trades": sum(1 for v in per_pair.values() if v),
    }


# ── B. the journal's own scanner entries, re-priced ─────────────────────────
def part_journal():
    tape = json.load(open(os.path.join(HERE, "kraken_5m_long_183d.json")))
    rows = [json.loads(l) for l in open(os.path.join(ROOT, "contest_trades.jsonl"),
                                        encoding="utf-8") if l.strip()]

    prepared = {}
    for sym, b in tape["pairs"].items():
        pr = prep(b)                      # the study's own loader/cleaner
        prepared[sym] = (pr["ts"], pr["o"], pr["h"], pr["l"], pr["c"])

    getnets, realized, holds = [], [], []
    train, nov = [], []
    used, skipped = 0, 0
    for r in rows:
        pair = r.get("pair") or ""
        sym = pair.split("/")[0]
        if sym not in prepared:
            skipped += 1
            continue
        ts, op, hi_, lo_, cl = prepared[sym]
        entry = r.get("entry")
        if not isinstance(entry, (int, float)) or entry <= 0:
            skipped += 1
            continue
        try:
            sold = dt.datetime.fromisoformat(r["sold_at"]).timestamp()
            held = float(r.get("held_min") or 0) * 60.0
        except (KeyError, ValueError, TypeError):
            skipped += 1
            continue
        t_entry = sold - held
        i = bisect.bisect_right(ts, t_entry) - 1
        if i < 0 or i >= len(ts) - 2:
            skipped += 1
            continue
        start = i + 1                     # the fill happened inside bar i
        if start >= len(ts) - 2:
            skipped += 1
            continue
        got = ride(entry, hi_, lo_, start)
        if got is None:
            skipped += 1
            continue
        used += 1
        getnets.append(got[0])
        holds.append(got[1] * 5)
        if t_entry >= NOV1:
            nov.append(got[0])
        else:
            train.append(got[0])
        size = r.get("size")
        if isinstance(size, (int, float)) and size > 0 and isinstance(r.get("pnl_gross"), (int, float)):
            g = 100.0 * r["pnl_gross"] / size
            if -50 < g < 50:
                realized.append(g)

    return {
        "rows": len(rows), "used": used, "skipped": skipped,
        "all": summarize("journal entries + ride", getnets),
        "train": summarize("  May-Oct", train),
        "november": summarize("  November", nov),
        "realized": summarize("journal's OWN exit", realized),
        "median_hold_min": round(st.median(holds)) if holds else None,
        "worst_pct": round(min(getnets), 3) if getnets else None,
        "best_pct": round(max(getnets), 3) if getnets else None,
    }


def main():
    out = {"A_gate_log": part_gate_log(), "B_journal": part_journal()}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)

    a, b = out["A_gate_log"], out["B_journal"]
    print(f"\nA: gate log {a['log_rows']} rows / {a['fires']} fires / {a['pairs']} pairs, "
          f"{a['window'][0][:16]} to {a['window'][1][:16]}")
    print(f"   resolved {a['resolved']}, still open {a['unresolved']}, "
          f"median hold {a['median_hold_min']} min, range {a['range']}")
    print(f"\nB: journal {b['rows']} rows, {b['used']} re-priced, {b['skipped']} skipped")
    print(f"   trend-ride median hold {b['median_hold_min']} min, "
          f"range {b['worst_pct']}% to {b['best_pct']}%")
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
