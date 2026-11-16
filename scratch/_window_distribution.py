"""_window_distribution.py — is Nov 12-16 a bad window, or a dead signal?

Replays the 66 candidates that have forward trades over EVERY non-overlapping
4-day window of the tape with the engine's exit path (`edge_policy.Resolver`,
close fills), one slot and each policy's own lane sizing (6% or 9% of a fresh
$10k per window), then ranks the lane's own window inside that distribution.

A second arm mirrors every policy to the short side on the SAME entry bars:
same signal, opposite exposure.  If the mirror is strongly positive while the
long arm is flat, the signal carries information and the long-only side was
wrong for this market.  If both are ~zero, the entries carry no edge at all.

Read-only.  Usage: python scratch/_window_distribution.py [--days N]
"""
from __future__ import annotations

import collections
import dataclasses
import json
import statistics
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import edge_engine as ee                                     # noqa: E402
import edge_policy as ep                                     # noqa: E402
from config import TradingConfig                             # noqa: E402

START = 10_000.0
LANE_START = pd.Timestamp("2026-11-12 15:27", tz="UTC")   # the lane's last reset
WINDOW_HOURS = 96                                          # 4 days


def chain(sel: list, lo: pd.Timestamp, hi: pd.Timestamp,
          size: float) -> dict:
    """One candidate's single-slot book through [lo, hi), engine exits."""
    equity, busy, mix = START, None, collections.Counter()
    trades = wins = 0
    for r, res in sel:
        t = r["ts"]
        if t < lo or t >= hi:
            continue
        if busy is not None and t < busy:      # one slot: a position is open
            continue
        exit_ts = pd.Timestamp(res["exit_ts"])
        if exit_ts >= hi:                      # still open at the window close
            continue
        pnl = equity * size * float(res["net"]) / 100.0
        equity += pnl
        busy = exit_ts
        trades += 1
        if pnl > 0:
            wins += 1
        mix[res["why"]] += 1
    return {"trades": trades, "wins": wins, "net": equity - START,
            "ret_pct": (equity / START - 1.0) * 100.0, "exits": mix}


def agg(rows: list[dict]) -> dict:
    n = sum(r["trades"] for r in rows)
    w = sum(r["wins"] for r in rows)
    rets = [r["ret_pct"] for r in rows]
    mix = collections.Counter()
    for r in rows:
        mix.update(r["exits"])
    return {"cands": len(rows), "trades": n, "wins": w,
            "win_rate": round(w / n, 3) if n else None,
            "net": round(sum(r["net"] for r in rows), 2),
            "mean": round(statistics.mean(rets), 3) if rets else None,
            "median": round(statistics.median(rets), 3) if rets else None,
            "share_pos": round(sum(1 for x in rets if x > 0) / len(rets), 2)
            if rets else None,
            "exits": dict(mix)}


def pct_rank(values: list[float], x: float) -> float:
    """Share of `values` strictly below `x` (0 = worst, 1 = best)."""
    return round(sum(1 for v in values if v < x) / len(values), 3)


def main() -> int:
    days = 4
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    window = pd.Timedelta(days=days)

    lane = [json.loads(l) for l in
            (ROOT / "edge_forward_trades.jsonl").read_text(encoding="utf-8")
            .splitlines() if l.strip()]
    ids = sorted({t["id"] for t in lane})
    reg = json.loads((ROOT / "edge_registry.json").read_text(encoding="utf-8"))
    cands = reg.get("candidates") or {}

    cfg = TradingConfig()
    pairs = ee.load_pairs()
    rows = ep.build_base_rows(pairs)
    ee.fill_atr_rank(rows)
    tape_end = max(df.index[-2] for df in pairs.values())
    tape_start = rows[0]["ts"]
    print(f"candidates={len(ids)} rows={len(rows)} pairs={len(pairs)}")
    print(f"tape {tape_start} -> {tape_end}\n", flush=True)

    # non-overlapping grid anchored at the tape end, so the newest window is
    # the lane's own window (to within a few hours)
    bounds = []
    hi = tape_end
    while hi - window >= tape_start:
        bounds.append((hi - window, hi))
        hi -= window
    bounds.reverse()
    bounds.append((LANE_START, tape_end))        # the lane window, exactly

    resolver = ep.Resolver(pairs, cfg, fill="close")
    resolver.use_cache = True

    long_rows = {i: [] for i in range(len(bounds))}
    short_rows = {i: [] for i in range(len(bounds))}
    missing = 0
    for pid in ids:
        spec = (cands.get(pid) or {}).get("spec") or {}
        pol = ep.policy_from_spec(spec)
        if pol is None:
            missing += 1
            continue
        mirror = dataclasses.replace(pol, side="short")
        sel_l = ep.selected_for(pol, rows, resolver)
        sel_s = ep.selected_for(mirror, rows, resolver)
        for i, (lo, hi) in enumerate(bounds):
            long_rows[i].append(chain(sel_l, lo, hi, pol.size_frac))
            short_rows[i].append(chain(sel_s, lo, hi, pol.size_frac))
        print(f"  {pid[:50]:50s} long {len(sel_l):4d} sel / "
              f"short {len(sel_s):4d} sel", flush=True)

    print(f"\nwindows={len(bounds)} (last = lane {LANE_START} -> {tape_end})"
          f"  unparsable specs={missing}")
    print(f"{'window':44s} {'long mean%':>10s} {'win%':>6s} {'n':>5s} "
          f"{'short mean%':>11s} {'win%':>6s} {'n':>5s}  Δ(short-long)")
    long_mean, short_mean, deltas = [], [], []
    for i, (lo, hi) in enumerate(bounds):
        a, b = agg(long_rows[i]), agg(short_rows[i])
        long_mean.append(a["mean"])
        short_mean.append(b["mean"])
        deltas.append(round(b["mean"] - a["mean"], 3))
        tag = "  <- LANE" if i == len(bounds) - 1 else ""
        print(f"{lo:%m-%d %H:%M} -> {hi:%m-%d %H:%M}{'':22s} "
              f"{a['mean']:>9.2f}% {a['win_rate'] * 100:>5.0f}% {a['trades']:>5d} "
              f"{b['mean']:>10.2f}% {b['win_rate'] * 100:>5.0f}% {b['trades']:>5d} "
              f"{deltas[-1]:>+8.2f}{tag}")

    grid = slice(0, len(bounds) - 1)                 # everything but the lane
    gl, gs = long_mean[grid], short_mean[grid]
    lane_l, lane_s = long_mean[-1], short_mean[-1]
    al, as_ = agg(long_rows[len(bounds) - 1]), agg(short_rows[len(bounds) - 1])
    print(f"\n=== ranking the lane window among {len(gl)} grid windows ===")
    print(f"  long : {lane_l:+.2f}% mean per candidate  "
          f"percentile {pct_rank(gl, lane_l):.0%}  "
          f"(grid median {statistics.median(gl):+.2f}%, "
          f"range {min(gl):+.2f}..{max(gl):+.2f})")
    print(f"  long : {al['win_rate']:.1%} pooled win rate over {al['trades']} "
          f"trades, {al['share_pos']:.0%} of candidates positive")
    print(f"  short: {lane_s:+.2f}% mean per candidate  "
          f"percentile {pct_rank(gs, lane_s):.0%}  "
          f"(grid median {statistics.median(gs):+.2f}%, "
          f"range {min(gs):+.2f}..{max(gs):+.2f})")
    print(f"  short: {as_['win_rate']:.1%} pooled win rate over {as_['trades']} "
          f"trades, {as_['share_pos']:.0%} of candidates positive")
    print(f"  lane-window delta short-long {lane_s - lane_l:+.2f} pts")
    print(f"\n=== mirror arm across the whole tape ===")
    print(f"  grid mean long {statistics.mean(gl):+.2f}%  "
          f"short {statistics.mean(gs):+.2f}%  "
          f"mean Δ {statistics.mean(deltas[:-1]):+.2f} pts")
    print(f"  windows where short beats long: "
          f"{sum(1 for x in deltas[:-1] if x > 0)}/{len(deltas) - 1}")
    print(f"  grid pooled exits long {agg([r for i in range(len(bounds) - 1) for r in long_rows[i]])['exits']}")
    print(f"  grid pooled exits short {agg([r for i in range(len(bounds) - 1) for r in short_rows[i]])['exits']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
