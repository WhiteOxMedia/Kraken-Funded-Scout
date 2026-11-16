"""_stability.py — how much of each promotion is the exact configuration?

Every promoted policy is re-chained under perturbations of the four things the
verdict silently depends on:

  gate    US_ROTS swapped for (2,3,4), (1,2,3), (3,4), (2,3,4,5) and no gate
  window  all three boundaries shifted +-1/2/3 days, and each internal boundary
          shifted alone +-2/4 days (which trades land in which window)
  order   random 10% of the selected signals dropped, N draws: the one-slot
          chain depends on which trades actually get taken
  fee     round trip 0.04 / 0.06 / 0.10 / 0.12 % instead of 0.08

A variant passes only if it still qualifies all three windows.  Stability =
passing variants / all variants.  Ranking by stability answers "which
promotion would survive being configured slightly differently", which is not
the same question as "which one made the most money".

Read-only.  Usage: python scratch/_stability.py [--draws N]
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import edge_engine as ee                                     # noqa: E402
import edge_policy as ep                                     # noqa: E402
from config import TradingConfig                             # noqa: E402

GATES = ((1, 2, 3, 4), (2, 3, 4), (1, 2, 3), (3, 4), (2, 3, 4, 5))
FEES = (0.04, 0.06, 0.10, 0.12)
SHIFTS = (-3, -2, -1, 1, 2, 3)
BOUND_SHIFTS = (-4, -2, 2, 4)
DROP_FRAC = 0.10


def _d(x: pd.Timestamp, days: int) -> str:
    return (x + pd.Timedelta(days=days)).strftime("%Y-%m-%d")


def windows_shifted(days: int, which: str | None = None):
    """All three boundaries, or one internal boundary, moved by `days`."""
    a, b = ee.WINDOWS[0]
    _, c = ee.WINDOWS[1]
    d = ee.WINDOWS[2][1]
    t = [pd.Timestamp(a), pd.Timestamp(b), pd.Timestamp(c), pd.Timestamp(d)]
    if which is None:
        t = [x + pd.Timedelta(days=days) for x in t]
    elif which == "b1":
        t[1] = t[1] + pd.Timedelta(days=days)
    else:
        t[2] = t[2] + pd.Timedelta(days=days)
    out = [(t[0].strftime("%Y-%m-%d"), t[1].strftime("%Y-%m-%d")),
           (t[1].strftime("%Y-%m-%d"), t[2].strftime("%Y-%m-%d")),
           (t[2].strftime("%Y-%m-%d"), t[3].strftime("%Y-%m-%d"))]
    return tuple(out)


def clear_exits(rows: list[dict]) -> None:
    for r in rows:
        r.pop("exits", None)


def frac(sub: list[dict]) -> float | None:
    return None if not sub else sum(
        1 for r in sub if r["passes"] >= 3) / len(sub)


def main() -> int:
    draws = 40
    if "--draws" in sys.argv:
        draws = int(sys.argv[sys.argv.index("--draws") + 1])
    rng = random.Random(20261116)
    reg = json.loads((ROOT / "edge_registry.json").read_text(encoding="utf-8"))
    cands = reg.get("candidates") or {}
    ids = sorted(k for k, v in cands.items() if v.get("status") == "promoted")
    pairs = ee.load_pairs()
    rows = ep.build_base_rows(pairs)
    ee.fill_atr_rank(rows)
    resolver = ep.Resolver(pairs, TradingConfig())
    resolver.use_cache = True
    base_fee, base_windows, base_rots = ee.FEE_RT_PCT, ee.WINDOWS, ep.US_ROTS
    print(f"rows={len(rows)} promoted={len(ids)} draws={draws}", flush=True)

    report: dict[str, list[dict]] = {}
    for pid in ids:
        spec = (cands.get(pid) or {}).get("spec") or {}
        pol = ep.policy_from_spec(spec)
        if pol is None:
            continue
        out: list[dict] = []

        def record(kind: str, label: str, ch: dict) -> None:
            out.append({"kind": kind, "label": label,
                        "passes": ch.get("passes"), "final": ch.get("final"),
                        "windows": [w.get("return_pct")
                                    for w in ch.get("windows") or []]})

        base_sel = ep.selected_for(pol, rows, resolver)
        record("base", "0.08 / (1,2,3,4) / nominal",
               ep.chain_result(pol, base_sel))

        for rots in GATES[1:]:
            ep.US_ROTS = rots
            record("gate", str(rots),
                   ep.chain_result(pol, ep.selected_for(pol, rows, resolver)))
        ep.US_ROTS = base_rots
        any_pol = ep.policy_from_spec({**spec, "hour_gate": "any"})
        record("gate", "any (no rotation gate)",
               ep.chain_result(any_pol,
                               ep.selected_for(any_pol, rows, resolver)))

        for d in SHIFTS:
            ee.WINDOWS = windows_shifted(d)
            record("window", f"all bounds {d:+d}d",
                   ep.chain_result(pol, base_sel))
        for which in ("b1", "b2"):
            for d in BOUND_SHIFTS:
                ee.WINDOWS = windows_shifted(d, which)
                record("window", f"{which} {d:+d}d",
                       ep.chain_result(pol, base_sel))
        ee.WINDOWS = base_windows

        for _ in range(draws):
            keep = [x for x in base_sel if rng.random() > DROP_FRAC]
            record("order", "drop 10%", ep.chain_result(pol, keep))

        for fee in FEES:
            ee.FEE_RT_PCT = fee
            clear_exits(rows)
            resolver.cache.clear()
            record("fee", f"fee {fee:.2f}",
                   ep.chain_result(pol, ep.selected_for(pol, rows, resolver)))
        ee.FEE_RT_PCT = base_fee
        clear_exits(rows)
        resolver.cache.clear()

        report[pid] = out
        print(f"  {pid[:46]:46s} stability {sum(1 for r in out if r['passes'] >= 3)}"
              f"/{len(out)}", flush=True)

    ep.US_ROTS = base_rots
    ee.WINDOWS = base_windows
    (ROOT / "scratch" / "_stability.json").write_text(
        json.dumps(report, indent=1), encoding="utf-8")

    def worst(ws):
        vals = [w for w in (ws or []) if w is not None]
        return min(vals) if vals else 0.0

    print(f"\n{'rank':>4s} {'policy':46s} {'stab':>6s} {'gate':>6s} {'window':>7s} "
          f"{'order':>6s} {'fee':>6s} {'base':>6s} {'worst-w':>8s}")
    ranked = sorted(report.items(),
                    key=lambda kv: (frac([r for r in kv[1] if r["kind"] != "base"]),
                                    worst(kv[1][0]["windows"])), reverse=True)
    for rank, (pid, rs) in enumerate(ranked, 1):
        variants = [r for r in rs if r["kind"] != "base"]
        kinds = {k: [r for r in rs if r["kind"] == k]
                 for k in ("gate", "window", "order", "fee")}
        print(f"{rank:>4d} {pid[:46]:46s} {frac(variants):6.0%} "
              f"{frac(kinds['gate']):6.0%} {frac(kinds['window']):7.0%} "
              f"{frac(kinds['order']):6.0%} {frac(kinds['fee']):6.0%} "
              f"{rs[0]['passes']}/3 {worst(rs[0]['windows']):+8.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
