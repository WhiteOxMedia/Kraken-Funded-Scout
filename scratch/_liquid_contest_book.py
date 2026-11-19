"""Rebuild the 10-policy book FROM the contest's liquid universe.

Universe = the challenge's own 54 pairs, restricted to those where a $2,400
order is <= 1% of the pair's median hourly dollar volume (pre-2026-10-19 volume,
so the screen cannot see the test month). Signals are GENERATED only on those
pairs, with next-open fills — nothing is filtered after the fact.
"""
import json
from types import SimpleNamespace
import numpy as np, pandas as pd
import contest_backfill as cb, edge_engine as ee, edge_policy as ep
from config import TradingConfig

CUT = pd.Timestamp("2026-10-19", tz="UTC")
ORDER = 2400.0
BOOK = ["L.pullback.risk_on.chandelier.9.any.us", "L.ignition.risk_on.fixed.9.any.any",
        "L.dip.risk_off.fixed.9.any.us", "L.pullback.risk_on.breakeven.9.any.any",
        "L.dip.risk_off.chandelier.9.any.us", "L.pullback.risk_off.breakeven.9.any.any",
        "L.dip.trend.chandelier.6.any.us", "L.ignition.risk_on.breakeven.9.any.any",
        "L.pullback.trend.fixed.6.any.any", "L.range_reclaim.risk_off.fixed.9.any.us"]

pairs = cb.load_cache(symbols=cb.contest_pairs())
print(f"contest universe {len(cb.contest_pairs())} pairs, cached {len(pairs)}", flush=True)
dvol = {}
for p, df in pairs.items():
    d = df[df.index < CUT]
    v = (d["close"].astype(float) * d["volume"].astype(float)).to_numpy(float)
    dvol[p] = float(np.nanmedian(v)) if len(v) else 0.0
UNI = sorted([p for p, m in dvol.items() if m * 0.01 >= ORDER])
print(f"\nliquid contest universe (${ORDER:,.0f} <= 1% of pre-cutoff median hourly $): "
      f"{len(UNI)}/{len(pairs)}", flush=True)
for p in sorted(dvol, key=lambda x: -dvol[x])[:14]:
    tag = "KEEP" if p in UNI else "    "
    print(f"  {tag} {p:12} ${dvol[p]:>11,.0f}/h  1% ${dvol[p]*0.01:>8,.0f}", flush=True)

print("\nbuilding rows on the contest panel (breadth/ranks = the contest book's own)...", flush=True)
# THE PANEL MUST SPEAK THE REGISTRY'S BTC NAME. edge_engine._panel_ranks
# reads closes.get("BTC/USD") for btc24; the contest cache speaks bare
# symbols, so btc24 came back as zeros and BOTH polarity gates were shut.
panel = {("BTC/USD" if p == "BTC" else p): df for p, df in pairs.items()}
rows = ep.build_base_rows(panel)
for r in rows:
    if r["pair"] == "BTC/USD":
        r["pair"] = "BTC"
live_btc = sum(1 for r in rows if r["f"].get("btc24"))
print(f"   rows with a live btc24: {live_btc:,}", flush=True)
print(f"   market rows {len(rows):,}", flush=True)
rows = [r for r in rows if r["pair"] in set(UNI)]
print(f"   rows on liquid pairs {len(rows):,}", flush=True)

cfg = SimpleNamespace(atr_multiplier=1.5)
res = ep.Resolver(pairs, cfg, fill="next_open")
res.use_cache = True
R = json.load(open("edge_registry.json", encoding="utf-8"))
out, total = {}, 0
print(f"\n{'policy':52} {'signals':>8} {'trades':>7} {'mean%':>8}", flush=True)
for pid in BOOK:
    sp = R["candidates"][pid]["spec"]
    pol = ep.make_policy(sp["side"], sp["entry"], sp["regime"], sp["exit"],
                         float(sp["size_frac"]), sp["vol_gate"], sp["hour_gate"])
    sel = ep.selected_for(pol, rows, res)
    tr = [(str(r["ts"]), str(x["exit_ts"]), float(x["net"]), r["pair"], int(x.get("hold") or 0))
          for r, x in sel if x is not None]
    out[pid] = tr
    total += len(tr)
    m = sum(t[2] for t in tr) / len(tr) if tr else 0.0
    print(f"{pid:52} {len(sel):>8,} {len(tr):>7,} {m:>8.3f}", flush=True)
json.dump(out, open("scratch/_books_liquid_contest.json", "w"))
print(f"\ntotal trades {total:,}", flush=True)
pre = [t for pid in out for t in out[pid] if pd.Timestamp(t[0]) < CUT]
nets = [t[2] for t in pre]
print(f"pre-cutoff trades {len(pre):,}  mean {np.mean(nets) if nets else 0:+.3f}%  "
      f"share liquid book kept: {total/max(1,len(rows)):.1%} of liquid rows", flush=True)
