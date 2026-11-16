"""_margin_rule.py — who still qualifies once the promotion bar has a margin?

Arms, all on the same rows and exits:

  close      the shipped chain, each window must clear PROMOTION_MIN_WINDOW_PCT
  next-open  the same chain filled at the next bar's open (the noise band)
  gate(2,3,4)   and no-gate: only for the six policies promoted before the rule

Reports, for every promoted/watch/research candidate, the window margin it
actually cleared and whether it survives the fill arm.

Read-only.  Usage: python scratch/_margin_rule.py
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
BEFORE = ("L.dip.risk_off.breakeven.9.any.us",
          "L.dip.risk_off.breakeven.6.any.us",
          "L.dip.risk_off.fixed.9.any.us",
          "L.dip.risk_off.fixed.6.any.us",
          "L.range_reclaim.trend.fixed.9.any.us",
          "L.range_reclaim.trend.fixed.6.any.us")


def min_window(ch: dict):
    vals = [w.get("return_pct") for w in ch.get("windows") or []
            if w.get("return_pct") is not None]
    return min(vals) if vals else None


def main() -> int:
    reg = json.loads((ROOT / "edge_registry.json").read_text(encoding="utf-8"))
    cands = reg.get("candidates") or {}
    ids = [pid for pid, v in cands.items() if v.get("status") in ELIGIBLE]
    pairs = ee.load_pairs()
    rows = ep.build_base_rows(pairs)
    ee.fill_atr_rank(rows)
    cfg = TradingConfig()
    close_r = ep.Resolver(pairs, cfg, fill="close")
    open_r = ep.Resolver(pairs, cfg, fill="next_open")
    close_r.use_cache = True
    open_r.use_cache = True
    print(f"rows={len(rows)} candidates={len(ids)} "
          f"margin={ep.PROMOTION_MIN_WINDOW_PCT}%", flush=True)

    out = []
    for pid in ids:
        spec = (cands.get(pid) or {}).get("spec") or {}
        pol = ep.policy_from_spec(spec)
        if pol is None:
            continue
        a = ep.stress_passes(pol, rows, close_r)
        b = ep.stress_passes(pol, rows, open_r)
        out.append({"id": pid, "status": cands[pid].get("status"),
                    "close_passes": a.get("passes"),
                    "open_passes": b.get("passes"),
                    "min_window": min_window(a),
                    "open_min_window": min_window(b)})
        if len(out) % 20 == 0:
            print(f"  {len(out)}/{len(ids)}", flush=True)

    keep = [r for r in out if r["close_passes"] >= 3 and r["open_passes"] >= 3]
    margin_only = [r for r in out if r["close_passes"] >= 3]
    print(f"\nclear the margin at the close: {len(margin_only)}")
    print(f"clear the margin AND next-open fills: {len(keep)}")
    for r in sorted(keep, key=lambda r: -(r["min_window"] or 0)):
        print(f"  {r['id'][:46]:46s} {r['status']:9s} "
              f"min window {r['min_window']:+.2f}% "
              f"(next-open {r['open_min_window']:+.2f}%)")

    print("\nthe six promoted before the rule:")
    for pid in BEFORE:
        spec = (cands.get(pid) or {}).get("spec") or {}
        pol = ep.policy_from_spec(spec)
        if pol is None:
            continue
        a = ep.stress_passes(pol, rows, close_r)
        b = ep.stress_passes(pol, rows, open_r)
        ep.US_ROTS = (2, 3, 4)
        narrow = ep.stress_passes(ep.policy_from_spec(spec), rows, close_r)
        ep.US_ROTS = (0, 1, 2, 3, 4, 5)
        wide = ep.stress_passes(ep.policy_from_spec(spec), rows, close_r)
        ep.US_ROTS = (1, 2, 3, 4)
        anyp = ep.policy_from_spec({**spec, "hour_gate": "any"})
        nogate = ep.stress_passes(anyp, rows, close_r)
        print(f"  {pid[:44]:44s} close {a['passes']}/3 "
              f"next-open {b['passes']}/3 gate(2,3,4) {narrow['passes']}/3 "
              f"all-rots {wide['passes']}/3 no-gate {nogate['passes']}/3  "
              f"min window {min_window(a):+.2f}% / {min_window(b):+.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
