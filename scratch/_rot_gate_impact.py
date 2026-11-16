"""_rot_gate_impact.py — what the rotation-aligned "us" gate changes.

The retired gate was `13 <= hour < 24` on UTC candles. This measures the
difference for the ACTIVE registry policy, on the cached 1h tape:

  old  13:00-23:59 UTC (07-18 Denver in summer, 06-17 in winter)
  new  Denver rotations 04-08, 08-12, 12-16, 16-20

Reports per-rotation signal counts for both, proves the new set is a superset
of the old one, and chains both through window 3.

Read-only. Usage: python scratch/_rot_gate_impact.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import edge_engine as ee                                     # noqa: E402
import edge_policy as ep                                     # noqa: E402
from config import TradingConfig                             # noqa: E402

ROTS = ("00-04", "04-08", "08-12", "12-16", "16-20", "20-24")


def main() -> int:
    reg = json.loads((ROOT / "edge_registry.json").read_text(encoding="utf-8"))
    pid = reg.get("active")
    spec = (reg.get("candidates") or {}).get(pid, {}).get("spec") or {}
    pol = ep.policy_from_spec(spec)
    if pol is None:
        print(f"active policy {pid!r} is not executable", file=sys.stderr)
        return 2
    print(f"active {pid}  ({pol.label})")

    pairs = ee.load_pairs()
    rows = ep.build_base_rows(pairs)
    resolver = ep.Resolver(pairs, TradingConfig())
    resolver.use_cache = True
    print(f"rows {len(rows)} pairs {len(pairs)}", flush=True)

    def shared_ok(f) -> bool:
        return (ep._safe(ep.regime_rule(pol.regime), f)
                and ep._safe(ep.vol_rule(pol.vol_gate), f))

    def select(pred) -> list:
        out = []
        for r in rows:
            f = r["f"]
            if pol.entry not in r["entries"] or not shared_ok(f) or not pred(f):
                continue
            res = resolver.resolve(r["pair"], r["i"], pol.side, pol.exit_mode)
            if res is not None:
                out.append((r, res))
        return out

    old = select(lambda f: 13 <= int(f["hour"]) < 24)
    new = select(lambda f: f["rot"] in ep.US_ROTS)
    old_ids = {(r["pair"], r["i"]) for r, _ in old}
    new_ids = {(r["pair"], r["i"]) for r, _ in new}

    def by_rot(subset) -> dict:
        out = {k: 0 for k in ROTS}
        for r, _ in subset:
            out[ROTS[int(r["f"]["rot"])]] += 1
        return out

    print(f"\nold gate: {len(old)} signals   {by_rot(old)}")
    print(f"new gate: {len(new)} signals   {by_rot(new)}")
    print(f"old rows the new gate DROPS: {len(old_ids - new_ids)}")
    print(f"new rows the rotation gate ADDS: {len(new_ids - old_ids)}")
    print(f"rotations reached by the old gate: "
          f"{sorted({ROTS[int(r['f']['rot'])] for r, _ in old})}")
    # ── WHERE THE ADDED HOURS LOSE ────────────────────────────────────────
    wbounds = [(pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC"))
               for a, b in ee.WINDOWS]
    added = [(r, res) for r, res in new
             if (r["pair"], r["i"]) not in old_ids]
    print()
    print(f"added rows only ({len(added)}) — the hours the old UTC window clipped:")
    for k in ROTS:
        sub = [(r, res) for r, res in added if ROTS[int(r["f"]["rot"])] == k]
        if not sub:
            continue
        nets = [float(res["net"]) for _, res in sub]
        per_w = []
        for lo, hi in wbounds:
            w = [float(res["net"]) for r, res in sub if lo <= r["ts"] < hi]
            per_w.append(round(sum(w), 2) if w else None)
        print(f"  {k}: n={len(sub):>4} mean {float(np.mean(nets)):+6.2f}% "
              f"sum {sum(nets):+9.2f}  w1/w2/w3 net% {per_w}")

    print()
    print("per rotation under the new gate (each rotation chained alone):")
    for k in ROTS:
        sub = [(r, res) for r, res in new if ROTS[int(r["f"]["rot"])] == k]
        if not sub:
            continue
        ch = ep.chain_result(pol, sub)
        ws = ch.get("windows") or []
        print(f"  {k}: signals={len(sub):>4} trades={ch.get('trades'):>4} "
              f"final={ch.get('final')} passes={ch.get('passes')}/3 "
              f"died={ch.get('died')} "
              f"windows={[w.get('return_pct') for w in ws]}")

    for tag, sel in (("old", old), ("new", new)):
        ch = ep.chain_result(pol, sel)
        ws = ch.get("windows") or []
        w3 = ws[2] if len(ws) > 2 else {}
        print(f"chain {tag}: trades={ch.get('trades')} final={ch.get('final')} "
              f"passes={ch.get('passes')}/3 died={ch.get('died')} "
              f"w3={w3.get('return_pct')}%  "
              f"windows={[w.get('return_pct') for w in ws]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
