"""measure_cost_live.py — what the round trip actually costs, per pair, right now.

The 0.08% in `contest_account.COST_ROUNDTRIP_PCT` is an assumption. Two measurements:

  1. LIVE   Kraken's public ticker (no key, no client of the user's). The round trip is
            the spread actually paid: buy at the ask, sell at the bid,
                rt_pct = 100 * (ask - bid) / mid
            sampled twice, so an unstable quote is visible.

  2. JOURNAL `contest_trades.jsonl`. Note what `pnl_net_usd()` writes:
                cost_rt = size_usd * COST_ROUNDTRIP_PCT / 100
            so cost_rt is the assumption restated (ratio to 0.08% of notional comes out
            at 1.000 by construction, up to rounding). The journal's honest column is
            `pnl_gross`, which is realized price movement on a ticket.

`size` in the journal is the notional in USD (checked: pnl_gross = size * (exit/entry-1)
to rounding), NOT units — dividing by size*entry was the first version's error.

    python scratch/measure_cost_live.py
"""
from __future__ import annotations

import json
import math
import os
import statistics as st
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import contest_account as ca

OUT = os.path.join(HERE, "_cost_live.json")
SAMPLE_GAP_S = 20
AVG_WIN = 0.426      # measured engine payoff, scratch/contest_math.py
NEED_30D = 0.441     # edge needed for 30 days at 1% and 87 trades/day


def journal():
    rows = []
    with open(os.path.join(os.path.dirname(HERE), "contest_trades.jsonl"),
              encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def journal_by_pair(rows):
    by = {}
    for r in rows:
        pair, size = r.get("pair"), r.get("size")
        if not pair or not isinstance(size, (int, float)) or size <= 0:
            continue
        d = by.setdefault(pair, {"n": 0, "gr": [], "cost_pct": [], "ratio": []})
        d["n"] += 1
        g = r.get("pnl_gross")
        if isinstance(g, (int, float)):
            g_pct = 100.0 * float(g) / float(size)
            if -50 < g_pct < 50:
                d["gr"].append(g_pct)
        c = r.get("cost_rt")
        if isinstance(c, (int, float)):
            d["cost_pct"].append(100.0 * float(c) / float(size))
            d["ratio"].append(float(c) / (float(size) * ca.COST_ROUNDTRIP_PCT / 100.0))
    return by


def live_spreads(pairs):
    import ccxt
    ex = ccxt.kraken({"enableRateLimit": True})
    out, bad = {}, {}
    for p in pairs:
        try:
            t = ex.fetch_ticker(p)
            bid, ask = t.get("bid"), t.get("ask")
            if not bid or not ask:
                bad[p] = "no bid/ask"
                continue
            mid = (bid + ask) / 2.0
            out[p] = {"bid": bid, "ask": ask, "mid": mid,
                      "rt_pct": 100.0 * (ask - bid) / mid}
        except Exception as exc:
            bad[p] = str(exc).split("\n")[0][:90]
    return out, bad


def classify(rt):
    if rt <= 0.04:
        return "cheap"
    if rt <= 0.08:
        return "at the assumption"
    if rt <= 0.20:
        return "expensive"
    return "untradeable"


def main():
    rows = journal()
    by = journal_by_pair(rows)

    ratios = [x for d in by.values() for x in d["ratio"] if x]
    cps = [x for d in by.values() for x in d["cost_pct"] if x]
    all_gross = [x for d in by.values() for x in d["gr"]]
    assumption_check = {
        "rows": len(ratios),
        "median_ratio_cost_rt_over_0.08pct": round(st.median(ratios), 4) if ratios else None,
        "median_implied_cost_pct": round(st.median(cps), 4) if cps else None,
        "p05_ratio": round(sorted(ratios)[int(0.05 * len(ratios))], 3) if ratios else None,
        "p95_ratio": round(sorted(ratios)[int(0.95 * len(ratios))], 3) if ratios else None,
        "journal_gross_mean_pct": round(st.fmean(all_gross), 4) if all_gross else None,
        "journal_gross_median_pct": round(st.median(all_gross), 4) if all_gross else None,
        "journal_gross_n": len(all_gross),
    }

    pairs = sorted(p for p in by if p != "FAKE/USD")
    s1, bad = live_spreads(pairs)
    time.sleep(SAMPLE_GAP_S)
    s2, _ = live_spreads(pairs)

    table = {}
    for p in pairs:
        a, b = s1.get(p), s2.get(p)
        if not a:
            table[p] = {"error": bad.get(p, "no quote")}
            continue
        rts = [a["rt_pct"]] + ([b["rt_pct"]] if b else [])
        rt = st.fmean(rts)
        j = by.get(p, {})
        gr = j.get("gr", [])
        g = st.median(gr) if gr else None
        if p == "DRV/USD":          # journal fixture: 24% on a 6% move
            g = None
        table[p] = {
            "spread_rt_pct": round(rt, 4),
            "samples": [round(x, 4) for x in rts],
            "class": classify(rt),
            "journal_n": j.get("n", 0),
            "journal_gross_pct": round(g, 3) if g is not None else None,
            "net_after_spread": round(g - rt, 3) if g is not None else None,
        }

    ok = {p: v for p, v in table.items() if "spread_rt_pct" in v}
    spreads = [v["spread_rt_pct"] for v in ok.values()]
    cheap = sorted([p for p, v in ok.items() if v["class"] == "cheap"])
    cheap_med = st.median([ok[p]["spread_rt_pct"] for p in cheap]) if cheap else None

    # realized gross, pooled per cost class — the honest pairing
    pooled = {}
    for cls in ("cheap", "at the assumption", "expensive", "untradeable"):
        xs = [g for p_, v in ok.items() if v["class"] == cls
              for g in by.get(p_, {}).get("gr", [])]
        pooled[cls] = {"n": len(xs),
                       "mean_pct": round(st.fmean(xs), 4) if xs else None,
                       "median_pct": round(st.median(xs), 4) if xs else None}
    zeros = sum(1 for r in rows if r.get("pnl_gross") in (0, 0.0))
    gross_edge = pooled["cheap"]["mean_pct"] or 0.0
    net_cheap = gross_edge - (cheap_med or 0.0)
    a_log = math.log((ca.load_state()["start_balance"] * 1.12)
                     / ca.load_state()["current_balance"])
    mu = math.log(1 + 0.01 * net_cheap / 100) if net_cheap > 0 else 0.0
    summary = {
        "pairs_quoted": len(ok),
        "pairs_failed": len(table) - len(ok),
        "median_spread_rt_pct": round(st.median(spreads), 4),
        "mean_spread_rt_pct": round(st.fmean(spreads), 4),
        "assumed_pct": ca.COST_ROUNDTRIP_PCT,
        "cheap": cheap,
        "at_assumption": sorted([p for p, v in ok.items() if v["class"] == "at the assumption"]),
        "expensive": sorted([p for p, v in ok.items() if v["class"] == "expensive"]),
        "untradeable": sorted([p for p, v in ok.items() if v["class"] == "untradeable"]),
        "spread_exceeds_avg_win": sorted([p for p, v in ok.items()
                                          if v["spread_rt_pct"] > AVG_WIN]),
        "spread_exceeds_needed_edge": sorted([p for p, v in ok.items()
                                              if v["spread_rt_pct"] > NEED_30D]),
        "cheap_median_spread_pct": round(cheap_med, 4) if cheap_med else None,
        "gross_edge_pct": round(gross_edge, 4),
        "pooled_gross_by_class": pooled,
        "zero_gross_rows": zeros,
        "net_edge_on_cheap_pct": round(net_cheap, 4),
        "trades_to_target_on_cheap": int(a_log / mu) if mu > 0 else None,
        "days_at_87_per_day_on_cheap": round(a_log / mu / 87) if mu > 0 else None,
    }

    out = {"measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "assumption_check": assumption_check, "summary": summary,
           "pairs": dict(sorted(table.items(),
                                key=lambda kv: kv[1].get("spread_rt_pct", 99)))}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)

    s = summary
    print(f"journal cost_rt / (size*0.08%) = "
          f"{assumption_check['median_ratio_cost_rt_over_0.08pct']} "
          f"(p05 {assumption_check['p05_ratio']}, p95 {assumption_check['p95_ratio']}, "
          f"n={assumption_check['rows']}) — the field is the assumption, restated")
    print(f"journal realized gross: mean {assumption_check['journal_gross_mean_pct']:+.4f}%"
          f"  median {assumption_check['journal_gross_median_pct']:+.4f}%"
          f"  (n={assumption_check['journal_gross_n']})")
    print(f"\nlive spreads — {s['pairs_quoted']} pairs, {out['measured_at']}, 2 samples")
    print(f"  median {s['median_spread_rt_pct']:.4f}%  mean {s['mean_spread_rt_pct']:.4f}%"
          f"  (assumed {s['assumed_pct']}%)")
    print(f"  cheap <0.04: {len(s['cheap'])} | at assumption: {len(s['at_assumption'])}"
          f" | expensive: {len(s['expensive'])} | untradeable >0.20: {len(s['untradeable'])}")
    print(f"  spread > avg win {AVG_WIN}%: {len(s['spread_exceeds_avg_win'])}"
          f"   spread > needed {NEED_30D}%: {len(s['spread_exceeds_needed_edge'])}")
    tt = s["trades_to_target_on_cheap"]
    dd = s["days_at_87_per_day_on_cheap"]
    print(f"  cheap pairs' median toll {s['cheap_median_spread_pct']:.4f}%  ->  "
          f"realized gross there {s['gross_edge_pct']:+.4f}%  ->  net "
          f"{s['net_edge_on_cheap_pct']:+.4f}%"
          + (f"  ->  {tt:,} trades / {dd:,} days at 87/day" if tt else
             "  ->  no positive drift: never reaches the target"))
    print(f"  rows with pnl_gross exactly 0 (unfilled/fixture): {zeros}")
    for cls, v in s["pooled_gross_by_class"].items():
        if v["n"]:
            print(f"    {cls:<18} n={v['n']:>4}  gross mean {v['mean_pct']:+.4f}%"
                  f"  median {v['median_pct']:+.4f}%")

    print(f"\n  {'pair':<14}{'rt%':>8}{'jrn':>5}{'gross%':>9}{'net%':>9}  class")
    for p, v in out["pairs"].items():
        if "spread_rt_pct" not in v:
            print(f"  {p:<14}{'--':>8}  {v.get('error', '')[:40]}")
            continue
        g = v["journal_gross_pct"]
        n = v["net_after_spread"]
        gs = f"{g:>9.3f}" if g is not None else f"{'--':>9}"
        ns = f"{n:>9.3f}" if n is not None else f"{'--':>9}"
        print(f"  {p:<14}{v['spread_rt_pct']:>8.4f}{v['journal_n']:>5}{gs}{ns}  {v['class']}")

    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
