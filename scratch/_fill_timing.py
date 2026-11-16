"""_fill_timing.py — how much of the backtest edge is the free close fill?

Chains every registry candidate that is still in play (promoted / watch /
research) twice: once filling at the signal bar's close (the shipped
assumption) and once at the next bar's open (what a decision made at that
close can actually reach).  Same rows, same exits, same windows.

Read-only.  Usage: python scratch/_fill_timing.py [--all]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import edge_engine as ee                                     # noqa: E402
import edge_policy as ep                                     # noqa: E402
from config import TradingConfig                             # noqa: E402

ELIGIBLE = ("promoted", "watch", "research")


def main() -> int:
    every = "--all" in sys.argv
    reg = json.loads((ROOT / "edge_registry.json").read_text(encoding="utf-8"))
    cands = reg.get("candidates") or {}
    ids = [pid for pid, row in cands.items()
           if every or row.get("status") in ELIGIBLE]
    pairs = ee.load_pairs()
    rows = ep.build_base_rows(pairs)
    ee.fill_atr_rank(rows)
    cfg = TradingConfig()
    res_close = ep.Resolver(pairs, cfg, fill="close")
    res_open = ep.Resolver(pairs, cfg, fill="next_open")
    res_close.use_cache = True
    res_open.use_cache = True
    print(f"rows={len(rows)} candidates={len(ids)}", flush=True)

    out = []
    for k, pid in enumerate(ids, 1):
        spec = (cands.get(pid) or {}).get("spec") or {}
        pol = ep.policy_from_spec(spec)
        if pol is None:
            continue
        a = ep.chain_result(pol, ep.selected_for(pol, rows, res_close))
        b = ep.chain_result(pol, ep.selected_for(pol, rows, res_open))
        out.append({
            "id": pid, "status": (cands.get(pid) or {}).get("status"),
            "close_final": a.get("final"), "open_final": b.get("final"),
            "close_passes": a.get("passes"), "open_passes": b.get("passes"),
            "close_trades": a.get("trades"), "open_trades": b.get("trades"),
            "close_wr": a.get("win_rate"), "open_wr": b.get("win_rate"),
            "delta": (b.get("final") or 0) - (a.get("final") or 0),
        })
        if k % 25 == 0:
            print(f"  {k}/{len(ids)}", flush=True)

    out.sort(key=lambda r: r["delta"])
    print(f"\n{'policy':44s} {'status':9s} {'close':>10s} {'next-open':>10s} "
          f"{'delta':>9s} {'passes':>7s}")
    for r in out:
        print(f"{r['id'][:44]:44s} {str(r['status'])[:9]:9s} "
              f"{r['close_final']:10,.2f} {r['open_final']:10,.2f} "
              f"{r['delta']:+9.2f} "
              f"{r['close_passes']}/3->{r['open_passes']}/3")

    deltas = sorted(r["delta"] for r in out)
    n = len(deltas)
    keep = sum(1 for r in out if r["open_passes"] >= 3)
    lost = sum(1 for r in out if r["close_passes"] >= 3 > r["open_passes"])
    worse = sum(1 for r in out if r["delta"] < 0)
    print(f"\ncandidates={n}  worse under next-open={worse} "
          f"({worse / n:.0%})  median delta=${deltas[n // 2]:+,.2f}  "
          f"mean delta=${sum(deltas) / n:+,.2f}")
    print(f"still 3/3 under next-open={keep}  "
          f"lost 3/3 to fill timing={lost}")
    print(f"total chained P&L close=${sum(r['close_final'] for r in out):,.0f} "
          f"next-open=${sum(r['open_final'] for r in out):,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
