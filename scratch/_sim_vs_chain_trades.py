"""_sim_vs_chain_trades.py — where do the lane sim and the engine chain diverge?

After the stale fix the two harnesses still disagree by 0.8-1.3 pts in window 3
on the four fixed policies.  This lists one policy's trades from both sides and
prints the first entries only one side took, plus per-pair counts, so the
remaining difference can be attributed rather than guessed at.

Read-only.  Usage: python scratch/_sim_vs_chain_trades.py [policy_id]
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch"))

import edge_engine as ee                                     # noqa: E402
import edge_policy as ep                                     # noqa: E402
import _lane_sim as ls                                       # noqa: E402
from config import TradingConfig                             # noqa: E402

PID = "L.dip.risk_off.fixed.9.any.us"


def main() -> int:
    pid = sys.argv[1] if len(sys.argv) > 1 else PID
    reg = json.loads((ROOT / "edge_registry.json").read_text(encoding="utf-8"))
    spec = ((reg.get("candidates") or {}).get(pid) or {}).get("spec") or {}
    pol = ep.policy_from_spec(spec)
    cfg = TradingConfig()
    pairs = ee.load_pairs()
    rows = ep.build_base_rows(pairs)
    ee.fill_atr_rank(rows)
    lo = pd.Timestamp(ee.WINDOWS[0][0], tz="UTC")
    chain_end = pd.Timestamp(ee.WINDOWS[-1][1], tz="UTC")

    sim = ls.simulate(pol, rows, pairs, cfg, pol.exit_mode, lo, chain_end)
    # the sim records the EXIT bar in "ts"; compare on entry bars
    sim_keys = {(pd.Timestamp(t["entry_ts"]), t["pair"]): t for t in sim["trades"]}

    sel = ep.selected_for(pol, rows, ep.Resolver(pairs, cfg))
    sel.sort(key=lambda pr: pd.Timestamp(pr[0]["ts"]))
    chain_keys = {}
    busy_until = None
    for r, res in sel:
        t = pd.Timestamp(r["ts"])
        if t < lo or t >= chain_end:
            continue
        if busy_until is not None and t < busy_until:
            continue
        exit_ts = pd.Timestamp(res["exit_ts"])
        if exit_ts >= chain_end:
            continue
        chain_keys[(t, r["pair"])] = {"why": res["why"], "net": res["net"],
                                      "entry_ts": str(t)}
        busy_until = exit_ts

    print(f"{pid}: sim={len(sim_keys)} chain={len(chain_keys)}")
    only_sim = sorted(set(sim_keys) - set(chain_keys))
    only_chain = sorted(set(chain_keys) - set(sim_keys))
    print(f"  only sim: {len(only_sim)}   only chain: {len(only_chain)}")
    for k in only_sim[:8]:
        print(f"    sim-only   {k[0]} {k[1]} why={sim_keys[k]['why']} "
              f"pnl={sim_keys[k]['pnl']}")
    for k in only_chain[:8]:
        print(f"    chain-only {k[0]} {k[1]} why={chain_keys[k]['why']} "
              f"net={chain_keys[k]['net']:.3f}%")
    both = set(sim_keys) & set(chain_keys)
    diff_why = collections.Counter(
        (sim_keys[k]["why"], chain_keys[k]["why"]) for k in both)
    print(f"  both={len(both)}  why pairs sim/chain: {dict(diff_why)}")
    print(f"  pairs sim: {collections.Counter(k[1] for k in sim_keys).most_common(6)}")
    print(f"  pairs chain: "
          f"{collections.Counter(k[1] for k in chain_keys).most_common(6)}")
    bounds = ([pd.Timestamp(a, tz="UTC") for a, _ in ee.WINDOWS]
              + [chain_end])
    for k in range(3):
        lo_w, hi_w = bounds[k], bounds[k + 1]
        s_pnl = sum(v["pnl"] for v in sim_keys.values()
                    if lo_w <= pd.Timestamp(v["entry_ts"]) < hi_w)
        c_pnl = sum(v["net"] for v in chain_keys.values()
                    if lo_w <= pd.Timestamp(v["entry_ts"]) < hi_w)
        print(f"  window {k + 1}: sim booked pnl sum=${s_pnl:,.2f} "
              f"({sum(1 for v in sim_keys.values() if lo_w <= pd.Timestamp(v['entry_ts']) < hi_w)} trades)"
              f"  chain net% sum={c_pnl:,.2f}% "
              f"({sum(1 for v in chain_keys.values() if lo_w <= pd.Timestamp(v['entry_ts']) < hi_w)} trades)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
