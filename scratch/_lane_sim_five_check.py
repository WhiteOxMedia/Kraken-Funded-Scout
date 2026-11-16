"""Does a dead pair's unresolved position explain the fixed-mode divergence?

For each flagged policy: (a) which position is open at the tape end, (b) rerun
lane sim AND engine chain with that pair removed from the tape entirely - if
the window returns then agree, the divergence is the dead-pair slot, not the
lane's decisions.
"""
import sys
sys.path.insert(0, '.')
sys.path.insert(0, 'scratch')
import json
import pandas as pd
import edge_engine as ee
import edge_policy as ep
import _lane_sim as ls
from config import TradingConfig

PIDS = ("L.dip.risk_off.fixed.9.any.us", "L.dip.risk_off.fixed.6.any.us",
        "L.range_reclaim.trend.fixed.9.any.us",
        "L.range_reclaim.trend.fixed.6.any.us")
reg = json.load(open('edge_registry.json', encoding='utf-8'))
cfg = TradingConfig()

for pid in PIDS:
    spec = ((reg['candidates'].get(pid) or {}).get('spec') or {})
    pol = ep.policy_from_spec(spec)
    pairs = ee.load_pairs()
    rows = ep.build_base_rows(pairs)
    ee.fill_atr_rank(rows)
    lo = pd.Timestamp(ee.WINDOWS[0][0], tz="UTC")
    hi = max(df.index[-2] for df in pairs.values())
    sim = ls.simulate(pol, rows, pairs, cfg, pol.exit_mode, lo, hi)
    pos = sim['open']
    print(f"{pid}\n  open at end: {pos['pair'] if pos else None} "
          f"entry_i={pos['i'] if pos else None} "
          f"ts={pairs[pos['pair']].index[pos['i']] if pos else None} "
          f"pair stops at {pairs[pos['pair']].index[-1] if pos else None} "
          f"({len(pairs[pos['pair']]) - 1 - pos['i'] if pos else 0} bars left)")
    bad = pos['pair'] if pos else None
    if bad:
        pairs2 = ee.load_pairs()
        del pairs2[bad]
        rows2 = ep.build_base_rows(pairs2)
        ee.fill_atr_rank(rows2)
        hi2 = max(df.index[-2] for df in pairs2.values())
        sim2 = ls.simulate(pol, rows2, pairs2, cfg, pol.exit_mode, lo, hi2)
        wr = [round(float(x), 3) if x is not None else None
              for x in ls.window_returns(ee.WINDOWS, sim2['marks'])]
        ch = ep.chain_result(pol, ep.selected_for(pol, rows2,
                                                 ep.Resolver(pairs2, cfg)))
        cw = [w.get('return_pct') for w in ch.get('windows') or []]
        print(f"  without {bad}: sim {wr} ({len(sim2['trades'])} trades, "
              f"open={bool(sim2['open'])})")
        print(f"  {' ' * len('without ' + bad)}  chain {cw} "
              f"({ch.get('trades')} trades)")
        delta = [None if a is None or b is None else round(a - b, 3)
                 for a, b in zip(wr, cw)]
        print(f"  delta {delta}")
