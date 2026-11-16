"""_poll_cadence.py — what the 300s polling clock costs the forward lane.

The lane can only enter at the LATEST CLOSED BAR of a pass (`_open_new` filters
`events` to `max(ts)`), and its cursor (`_collect_new`) jumps every pair to the
newest closed bar afterwards.  So the only bars a policy can ever trade are the
ones that were "latest" at some pass.  This measures, on the real tape:

  * bars advanced per pass (the cursor gap),
  * signal bars that were never the latest bar at any pass ("never offered"),
    i.e. what the cadence itself costs - the one-slot rule is separate.

Pass clock: the lane sleeps 300s and then works; the log holds 1,110 passes and
spans the lane's run, which pins the mean interval.  Both the measured mean and
a strict 300s clock are run so the answer does not depend on which is right.

Read-only.  Usage: python scratch/_poll_cadence.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import edge_engine as ee                                     # noqa: E402
import edge_policy as ep                                     # noqa: E402

LANE_START = pd.Timestamp("2026-11-12 14:46", tz="UTC")
LANE_END = pd.Timestamp("2026-11-16 14:18", tz="UTC")
PASSES_IN_LOG = 1110
BAR = pd.Timedelta(hours=1)


def main() -> int:
    span_s = (LANE_END - LANE_START).total_seconds()
    mean_interval = span_s / PASSES_IN_LOG
    print(f"lane span {span_s / 3600:.1f}h over {PASSES_IN_LOG} passes "
          f"-> mean interval {mean_interval:.0f}s "
          f"(sleep is 300s, so ~{mean_interval - 300:.0f}s of work per pass)")

    reg = json.loads((ROOT / "edge_registry.json").read_text(encoding="utf-8"))
    cands = reg.get("candidates") or {}
    ids = [k for k, v in cands.items()
           if v.get("status") in ("promoted", "watch", "research")]
    pairs = ee.load_pairs()
    rows = ep.build_base_rows(pairs)
    ee.fill_atr_rank(rows)

    bars = sorted({ts for df in pairs.values() for ts in df.index})
    bars = [ts for ts in bars if LANE_START - 2 * BAR <= ts <= LANE_END]
    by_ts: dict = {}
    for r in rows:
        if LANE_START <= r["ts"] <= LANE_END:
            by_ts.setdefault(r["ts"], []).append(r)

    def run_clock(interval_s: float) -> dict:
        """Walk the pass clock; return offered bars and the gap histogram."""
        offered: set = set()
        gaps: dict = {}
        t = LANE_START
        prev_latest = None
        passes = 0
        while t <= LANE_END:
            closed = [b for b in bars if b + BAR <= t]
            if closed:
                latest = closed[-1]
                offered.add(latest)
                if prev_latest is not None:
                    gap = int((latest - prev_latest) / BAR)
                    gaps[gap] = gaps.get(gap, 0) + 1
                prev_latest = latest
            passes += 1
            t += pd.Timedelta(seconds=interval_s)
        return {"offered": offered, "gaps": gaps, "passes": passes}

    for label, interval in (("measured", mean_interval), ("strict 300s", 300.0)):
        clock = run_clock(interval)
        gaps = clock["gaps"]
        total = sum(gaps.values())
        skipped = sum(max(0, g - 1) * n for g, n in gaps.items())
        print(f"\n{label}: {clock['passes']} passes, {len(clock['offered'])} "
              f"distinct bars offered")
        print(f"  bars advanced per pass: "
              f"{ {g: n for g, n in sorted(gaps.items())} }")
        print(f"  passes that skipped a bar (gap>=2): "
              f"{sum(n for g, n in gaps.items() if g >= 2)}/{total} "
              f"({sum(n for g, n in gaps.items() if g >= 2) / total:.2%}); "
              f"skipped bars {skipped}")

        lost_all, sig_all, worst = 0, 0, []
        for pid in ids:
            spec = (cands.get(pid) or {}).get("spec") or {}
            pol = ep.policy_from_spec(spec)
            if pol is None:
                continue
            sig = [ts for ts in by_ts
                   if any(pol.rule(r["f"]) for r in by_ts[ts])]
            if not sig:
                continue
            lost = [ts for ts in sig if ts not in clock["offered"]]
            sig_all += len(sig)
            lost_all += len(lost)
            worst.append((len(lost), len(sig), pid))
        worst.sort(reverse=True)
        print(f"  signal bars across {len(worst)} candidates: {sig_all}; "
              f"never offered by the clock: {lost_all} "
              f"({lost_all / sig_all:.2%})")
        for n_lost, n_sig, pid in worst[:4]:
            if n_lost:
                print(f"    {pid[:46]:46s} {n_lost}/{n_sig} lost")

    # the active policy specifically
    pid = "L.dip.risk_off.breakeven.9.any.us"
    pol = ep.policy_from_spec((cands.get(pid) or {}).get("spec") or {})
    if pol is not None:
        sig = sorted(ts for ts in by_ts
                     if any(pol.rule(r["f"]) for r in by_ts[ts]))
        clock = run_clock(mean_interval)
        lost = [ts for ts in sig if ts not in clock["offered"]]
        print(f"\n{pid}: {len(sig)} signal bars in the lane window, "
              f"{len(lost)} never offered, {len(sig) - len(lost)} offered; "
              f"the lane entered 10 (one slot).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
