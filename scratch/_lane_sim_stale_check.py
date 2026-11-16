"""_lane_sim_stale_check.py — the five-policy sim/chain diff AFTER the stale fix.

Same comparison as before, but `_lane_sim.simulate` now force-closes a position
whose pair stopped printing (why="stale") at the pair's last printed close
instead of holding the single slot forever.  Prints the stale closes so the
released slots are visible, then the sim's window returns against the engine
chain's for the same policy.

Read-only.  Usage: python scratch/_lane_sim_stale_check.py
"""
from __future__ import annotations

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

PIDS = (
    "L.dip.risk_off.breakeven.6.any.us",
    "L.dip.risk_off.fixed.9.any.us",
    "L.dip.risk_off.fixed.6.any.us",
    "L.range_reclaim.trend.fixed.9.any.us",
    "L.range_reclaim.trend.fixed.6.any.us",
)


def main() -> int:
    reg = json.loads((ROOT / "edge_registry.json").read_text(encoding="utf-8"))
    cfg = TradingConfig()
    pairs = ee.load_pairs()
    rows = ep.build_base_rows(pairs)
    ee.fill_atr_rank(rows)
    lo = pd.Timestamp(ee.WINDOWS[0][0], tz="UTC")
    hi = max(df.index[-2] for df in pairs.values())
    # the chain stops at its last window bound (2026-11-10); the tape runs
    # to 2026-11-16, so the sim is run twice: to the tape end, and bounded
    # to the chain end (apple to apple on the same bars)
    chain_end = pd.Timestamp(ee.WINDOWS[-1][1], tz="UTC")
    print(f"rows={len(rows)} pairs={len(pairs)} tape->{hi}\n", flush=True)

    root_sums = []
    for pid in PIDS:
        spec = ((reg.get("candidates") or {}).get(pid) or {}).get("spec") or {}
        pol = ep.policy_from_spec(spec)
        if pol is None:
            print(f"{pid}: not executable", flush=True)
            continue
        sim = ls.simulate(pol, rows, pairs, cfg, pol.exit_mode, lo, hi)
        sim_c = ls.simulate(pol, rows, pairs, cfg, pol.exit_mode, lo, chain_end)
        wr = [round(float(x), 3) if x is not None else None
              for x in ls.window_returns(ee.WINDOWS, sim["marks"])]
        wrc = [round(float(x), 3) if x is not None else None
               for x in ls.window_returns(ee.WINDOWS, sim_c["marks"])]
        n = len(sim["trades"])
        w = sum(1 for t in sim["trades"] if t["pnl"] > 0)
        stale = [t for t in sim["trades"] if t["why"] == "stale"]
        ch = ep.chain_result(pol, ep.selected_for(
            pol, rows, ep.Resolver(pairs, cfg)))
        cw = [w2.get("return_pct") for w2 in ch.get("windows") or []]
        delta = [None if a is None or b is None else round(a - b, 3)
                 for a, b in zip(wr, cw)]
        print(f"{pid}")
        print(f"  sim   {wr}  {n} trades, {w} wins, final "
              f"${sim['equity']:,.2f}, passes={sim['passes']}, "
              f"deaths={sim['deaths']}, open={bool(sim['open'])}")
        print(f"  chain {cw}  {ch.get('trades')} trades, "
              f"{ch.get('wins')} wins, final ${ch.get('final'):,.2f}")
        print(f"  delta {delta}   (stale closes: {len(stale)})")
        d_c = [None if a is None or b is None else round(a - b, 3)
               for a, b in zip(wrc, cw)]
        print(f"  sim bounded to the chain end {wrc}  "
              f"{len(sim_c['trades'])} trades  delta {d_c}")
        for t in stale:
            print(f"    stale {t['ts']} {t['pair']} pnl={t['pnl']}")
        print(f"  exits {sim['why']}", flush=True)
        root_sums.append((pid, delta))
    worst = max((max(abs(x) for x in d if x is not None)
                 for _p, d in root_sums), default=0.0)
    print(f"\nworst |sim - chain| window delta: {worst:.3f} pts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
