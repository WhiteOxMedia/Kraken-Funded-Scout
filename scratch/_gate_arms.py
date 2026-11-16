"""_gate_arms.py — the retired window vs the rotation gate, same policies, same tape.

  us_local  the retired UTC 13-24 window restated in Denver hours (control:
            07:00-18:00 under MDT, 06:00-17:00 under MST)
  us        the rotation-aligned gate (Denver 04-08, 08-12, 12-16, 16-20)

Chains the active policy, the two policies promoted before the rotation change
and everything the latest search promoted, under both gates, and prints the
per-window delta plus which rotations each arm selected.

Read-only. Usage: python scratch/_gate_arms.py
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
PREV = ("L.dip.trend.chandelier.6.any.us",
        "L.range_reclaim.risk_off.chandelier.6.any.us")
ARMS = ("us_local", "us")


def main() -> int:
    reg = json.loads((ROOT / "edge_registry.json").read_text(encoding="utf-8"))
    prev = json.loads((ROOT / "scratch" / "edge_registry.prev.json")
                      .read_text(encoding="utf-8"))
    cands = reg.get("candidates") or {}
    old_cands = prev.get("candidates") or {}

    ids = [p for p, c in cands.items() if c.get("status") == "promoted"]
    for pid in (reg.get("active"), *PREV):
        if pid and pid not in ids:
            ids.append(pid)

    pairs = ee.load_pairs()
    rows = ep.build_base_rows(pairs)
    resolver = ep.Resolver(pairs, TradingConfig())
    resolver.use_cache = True
    print(f"rows {len(rows)} pairs {len(pairs)} policies {len(ids)}")

    for pid in ids:
        spec = ((cands.get(pid) or {}).get("spec")
                or (old_cands.get(pid) or {}).get("spec") or {})
        status = (cands.get(pid) or {}).get("status", "-")
        print(f"\n{pid}   (registry status now: {status})")
        arms = {}
        for hg in ARMS:
            pol = ep.policy_from_spec({**spec, "hour_gate": hg})
            if pol is None:
                print(f"  {hg:>8}: spec is not executable")
                continue
            sel = ep.selected_for(pol, rows, resolver)
            ch = ep.chain_result(pol, sel)
            ws = ch.get("windows") or []
            by_rot = {k: 0 for k in ROTS}
            for r, _ in sel:
                by_rot[ROTS[int(r["f"]["rot"])]] += 1
            arms[hg] = ch
            print(f"  {hg:>8}: signals={len(sel):>4} trades={ch.get('trades'):>4} "
                  f"final={ch.get('final'):>9.2f} passes={ch.get('passes')}/3 "
                  f"died={ch.get('died')} dd={ch.get('max_drawdown_pct')}% "
                  f"windows={[w.get('return_pct') for w in ws]}")
            print(f"            rotations={ {k: v for k, v in by_rot.items() if v} }")
        a, b = arms.get("us_local"), arms.get("us")
        if a and b:
            wa = [w.get("return_pct") for w in a.get("windows") or []]
            wb = [w.get("return_pct") for w in b.get("windows") or []]
            if len(wa) == len(wb) == 3:
                print(f"  rotation minus old window-by-window: "
                      f"{[round(y - x, 3) for x, y in zip(wa, wb)]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
