"""_drop_0408.py — does dropping the 04-08 rotation help the two 9% lanes?

Arms per lane:
  4 rots   US_ROTS = (1, 2, 3, 4)   the gate as it ships now
  3 rots   US_ROTS = (2, 3, 4)      drop the only rotation whose added hours
                                    lost money (04-08, -0.25%/trade)
  control  "us_local"               the retired UTC 13-24 window, for reference

`hour_rule("us")` resolves US_ROTS from module globals at call time, so the arm
is a straight swap of `ep.US_ROTS` before building the policy.  Each rotation is
also chained alone under the 4-rot gate so the arm comparison can be read
against where the loss actually sits.

Read-only. Usage: python scratch/_drop_0408.py
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

ROTS = ("00-04", "04-08", "08-12", "12-16", "16-20", "20-24")
LANES = ("L.dip.risk_off.breakeven.9.any.us",
         "L.range_reclaim.trend.fixed.9.any.us")
ARMS = (("4 rots (1,2,3,4)", (1, 2, 3, 4)),
        ("3 rots (2,3,4)  ", (2, 3, 4)))


def run(pol, rows, resolver):
    sel = ep.selected_for(pol, rows, resolver)
    ch = ep.chain_result(pol, sel)
    by_rot = {k: 0 for k in ROTS}
    for r, _ in sel:
        by_rot[ROTS[int(r["f"]["rot"])]] += 1
    return pol, sel, ch, by_rot


def show(tag, sel, ch, by_rot):
    ws = [w.get("return_pct") for w in ch.get("windows") or []]
    print(f"  {tag}: signals={len(sel):>4} trades={ch.get('trades'):>4} "
          f"final={ch.get('final'):>9.2f} passes={ch.get('passes')}/3 "
          f"died={ch.get('died')} dd={ch.get('max_drawdown_pct')}% "
          f"windows={ws}")
    print(f"      rotations={ {k: v for k, v in by_rot.items() if v} }")


def main() -> int:
    reg = json.loads((ROOT / "edge_registry.json").read_text(encoding="utf-8"))
    cands = reg.get("candidates") or {}
    pairs = ee.load_pairs()
    rows = ep.build_base_rows(pairs)
    resolver = ep.Resolver(pairs, TradingConfig())
    resolver.use_cache = True
    print(f"rows {len(rows)} pairs {len(pairs)}  current US_ROTS={ep.US_ROTS}")

    for pid in LANES:
        spec = (cands.get(pid) or {}).get("spec") or {}
        print(f"\n{pid}   (registry status: "
              f"{(cands.get(pid) or {}).get('status', '-')})")
        arms = {}
        for tag, rots in ARMS:
            ep.US_ROTS = rots
            pol = ep.policy_from_spec(spec)
            _, sel, ch, by_rot = run(pol, rows, resolver)
            arms[tag] = (sel, ch)
            show(tag, sel, ch, by_rot)
        ep.US_ROTS = (1, 2, 3, 4)
        cpol = ep.policy_from_spec({**spec, "hour_gate": "us_local"})
        _, csel, cch, cby = run(cpol, rows, resolver)
        show("control (us_local)", csel, cch, cby)

        a = arms["4 rots (1,2,3,4)"][1]
        b = arms["3 rots (2,3,4)  "][1]
        wa = [w.get("return_pct") for w in a.get("windows") or []]
        wb = [w.get("return_pct") for w in b.get("windows") or []]
        if len(wa) == len(wb) == 3:
            print(f"  drop-04-08 minus 4-rot, per window: "
                  f"{[round(y - x, 3) for x, y in zip(wa, wb)]}")

        print("  each rotation chained alone under the 4-rot gate:")
        for k in ROTS:
            sub = [(r, res) for r, res in arms["4 rots (1,2,3,4)"][0]
                   if ROTS[int(r["f"]["rot"])] == k]
            if not sub:
                continue
            ch = ep.chain_result(ep.policy_from_spec(spec), sub)
            ws = [w.get("return_pct") for w in ch.get("windows") or []]
            print(f"    {k}: trades={ch.get('trades'):>3} "
                  f"final={ch.get('final'):>9.2f} passes={ch.get('passes')}/3 "
                  f"died={ch.get('died')} windows={ws}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
