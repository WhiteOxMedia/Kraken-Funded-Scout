"""THE SIMULATION HARNESS (task 5, Sep 28 spec): replay historical
rotations through the proportional rule with a parameter sweep, and
report the risk shape of every (ALPHA, DELTA_MAX, DAILY_CAP) cell.

The replay reads the machine's real history — popup_suppressions.jsonl
joined to ohlcv_cache.db through the SAME join as task 1 (first
refusal per pair per rotation, 7h max-high peaks) — so the sweep prices
the rule against what actually happened, not a toy distribution.

Metrics per parameter cell (the spec's three):
    net_delta        the sum of deltas the rule would have applied,
                     as a distribution across the replayed rotations
                     (mean, p5, p95 — bootstrap over rotations)
    worst_single_book_loss
                     the largest loosening a single rotation would
                     have earned — the most the guard could have been
                     wrong once, at once
    auto_revert_freq how often the time box expired mid-streak (a
                     proxy for whipsaw: the rule flipped the guard
                     and the market answered before it expired)

Acceptance (the spec's): the report names the recommended conservative
defaults (ALPHA=0.05, DELTA_MAX=0.10) and shows the worst single-book
loss under every parameter set.

Usage: uv run python simulate_sweep.py [--rotations 30] [--out DIR]
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# the sweep grid (the spec's, verbatim)
ALPHAS = [0.01, 0.03, 0.05]
DELTAS = [0.05, 0.10, 0.15]
CAPS = [0.10, 0.20]
B_MIN = 5
R_MIN = 0.25
BOX_ROTATIONS = 3
BOOTSTRAP_N = 500

# THE FOCUSED 24h EXTRACT (the Sep-28 late ask): alpha {0.03, 0.05} x
# delta_max {0.05, 0.10}, daily cap at the standing default — the four
# cells the operator reads before arming the canary.
FOCUSED_ALPHAS = [0.03, 0.05]
FOCUSED_DELTAS = [0.05, 0.10]
FOCUSED_CAP = 0.20

RECOMMENDED = {"alpha": 0.05, "delta_max": 0.10, "daily_cap": 0.20}


def load_history(limit_rotations: int) -> list[dict]:
    """One record per rotation: its measured burials with peaks (the
    task-1 join) + the payer count. Rotations with zero measurable
    burials carry [] — they are real replay steps (the rule idles).
    The grouping key is the FULL rotation: a bare window ('12-16') is
    dated by the row's own ts — a bare-window journal never collapses
    into six ever-merged buckets (the isolated review's major)."""
    import per_gate_export as pge
    supp = ROOT / "popup_suppressions.jsonl"
    cache = ROOT / "ohlcv_cache.db"
    rows = []
    if supp.exists():
        for line in supp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("pair"):
                rows.append(d)

    def _full_stamp(rot: str) -> bool:
        p = rot.split("-")
        return len(p) >= 5 and pge._is_date("-".join(p[:3]))

    by_rot: dict[str, list] = {}
    for r in rows:
        rot = str(r.get("rot") or "?")
        t_day = str(r.get("ts") or "")[:10]
        key = rot if _full_stamp(rot) else f"{t_day}-{rot}"
        by_rot.setdefault(key, []).append(r)
    rotations = sorted(by_rot)[-limit_rotations:]
    deduped = {rot: pge.dedup_first(by_rot[rot]) for rot in rotations}
    # peaks for every distinct candidate across the window
    import sqlite3
    con = sqlite3.connect(cache.as_uri() + "?mode=ro", uri=True,
                          timeout=2.0) if cache.exists() else None
    try:
        for rot, rrows in deduped.items():
            for r in rrows:
                r["max_run_pct"] = None
                if con is None:
                    continue
                try:
                    t = datetime.fromisoformat(str(r.get("ts") or ""))
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=pge._now_mst().tzinfo)
                    pk = pge.compute_peak(con, str(r["pair"]),
                                          int(t.timestamp() * 1000), 1.5)
                    r["max_run_pct"] = pk["max_run_pct"]
                except Exception:
                    continue
    finally:
        if con is not None:
            con.close()
    return [{"rot": rot,
             "B": sum(1 for r in deduped[rot]
                      if r["max_run_pct"] is not None),
             "P": sum(1 for r in deduped[rot]
                      if (r["max_run_pct"] or -99) >= 1.5)}
            for rot in rotations]


def replay(history: list[dict], alpha: float, delta_max: float,
           daily_cap: float) -> dict:
    """One pass of the rule over history with the given parameters.
    The daily cap is per REPLAYED DAY here (rotation-stamped); in the
    live machine it is per wall-clock day."""
    applied: list[float] = []          # deltas the rule applied
    net = 0.0
    worst = 0.0
    day_used: dict[str, float] = {}
    active_box = 0
    reverts = 0
    boxes = 0
    for h in history:
        B, P = h["B"], h["P"]
        if active_box > 0:
            active_box -= 1
            if active_box == 0:
                reverts += 1
        if B < B_MIN:
            applied.append(0.0)
            continue
        r = P / B
        if r < R_MIN:
            applied.append(0.0)
            continue
        delta = -min(delta_max, alpha * r * math.log(1 + B))
        # THE CAP IS PER WALL-CLOCK DAY (the isolated review's major):
        # a full stamp carries the date in its first 10 chars; a dated
        # bare-window key ('2026-09-28-12-16') too. The window half
        # would let the same day's four rotations spend 4x the cap.
        day = str(h["rot"])[:10]
        if day_used.get(day, 0.0) + abs(delta) > daily_cap + 1e-9:
            applied.append(0.0)
            continue
        day_used[day] = day_used.get(day, 0.0) + abs(delta)
        applied.append(delta)
        net += delta
        worst = min(worst, delta)      # most negative = biggest loosen
        if active_box <= 0:
            active_box = BOX_ROTATIONS + 1   # this rotation + 3 more
            boxes += 1
    return {"applied": applied, "net_delta": net, "worst": worst,
            "reverts": reverts, "boxes": boxes,
            "applied_count": sum(1 for a in applied if a < 0)}


def bootstrap_ci(values: list[float], n: int = BOOTSTRAP_N,
                 seed: int = 7) -> tuple[float, float]:
    """Percentile bootstrap CI (p5, p95) of the mean, seeded (the
    report must be reproducible)."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        sample = [rng.choice(values) for _ in range(len(values))]
        means.append(sum(sample) / len(sample))
    means.sort()
    return (means[int(0.05 * len(means))], means[int(0.95 * len(means))])


def sweep(history: list[dict]) -> list[dict]:
    """The full grid. Every cell reports the spec's three metrics."""
    out = []
    for alpha, dmax, cap in itertools.product(ALPHAS, DELTAS, CAPS):
        res = replay(history, alpha, dmax, cap)
        lo, hi = bootstrap_ci(res["applied"])
        out.append({
            "alpha": alpha, "delta_max": dmax, "daily_cap": cap,
            "net_delta": round(res["net_delta"], 4),
            "ci_p5": round(lo, 4), "ci_p95": round(hi, 4),
            "worst_single_book_loss": round(res["worst"], 4),
            "auto_revert_freq": (round(res["reverts"] / res["boxes"], 3)
                                 if res["boxes"] else 0.0),
            "applied_count": res["applied_count"],
            "recommended": (alpha, dmax, cap)
            == (RECOMMENDED["alpha"], RECOMMENDED["delta_max"],
                RECOMMENDED["daily_cap"]),
        })
    return out


def _chart(cells: list[dict], history: list[dict]) -> str:
    """The report's charts as inline SVG — no chart lib, no network."""
    maxwd = max((abs(c["worst_single_book_loss"]) for c in cells),
                default=1.0) or 1.0
    bars = []
    bw, gap, h = 14, 6, 160
    x = 10
    for c in cells:
        bh = max(2, int(abs(c["worst_single_book_loss"]) / maxwd * (h - 30)))
        col = "#7fd0a0" if c["recommended"] else "#d0a77f"
        bars.append(
            f"<rect x='{x}' y='{h - 20 - bh}' width='{bw}' height='{bh}' "
            f"fill='{col}'><title>a={c['alpha']} dmax={c['delta_max']} "
            f"cap={c['daily_cap']}: worst {c['worst_single_book_loss']}"
            f"</title></rect>")
        x += bw + gap
    hdr = (f"<svg width='{x + 10}' height='{h}' "
           f"font-family='monospace' font-size='9'>"
           f"<text x='10' y='12'>worst single-book loss per cell "
           f"(green = recommended defaults)</text>"
           + "".join(bars) + "</svg>")
    hist_rows = "".join(
        f"<div>{h['rot']}: B={h['B']} P={h['P']}</div>"
        for h in history)
    return (f"<h3>worst single-book loss across the sweep</h3>{hdr}"
            f"<h3>the replayed history</h3>{hist_rows}")


def write_report(cells: list[dict], history: list[dict],
                 out: Path) -> Path:
    """The HTML report: the grid, the CI, the worst loss per cell, the
    recommended cell called out, the history named."""
    rows = "".join(
        f"<tr class='{'rec' if c['recommended'] else ''}'>"
        f"<td>{c['alpha']}</td><td>{c['delta_max']}</td>"
        f"<td>{c['daily_cap']}</td><td>{c['net_delta']:.4f}</td>"
        f"<td>[{c['ci_p5']:.4f}, {c['ci_p95']:.4f}]</td>"
        f"<td>{c['worst_single_book_loss']:.4f}</td>"
        f"<td>{c['auto_revert_freq']:.1%}</td>"
        f"<td>{c['applied_count']}</td></tr>"
        for c in cells)
    rec = [c for c in cells if c["recommended"]][0]
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>proportional self-adjust sensitivity — {datetime.now():%Y-%m-%d}</title>
<style>body{{font-family:monospace;background:#151312;color:#d8ccb4}}
table{{border-collapse:collapse}}td,th{{border:1px solid #444;padding:2px 8px}}
tr.rec{{background:#233326}}h3{{color:#bfae8f}}</style></head><body>
<h1>THE PROPORTIONAL SELF-ADJUST SWEEP (task 5)</h1>
<p>replayed {len(history)} rotations from the machine's own journals;
grid = alpha {ALPHAS} x delta_max {DELTAS} x daily_cap {CAPS} = {len(cells)} cells;
bootstrap p5/p95 of the mean applied delta ({BOOTSTRAP_N} resamples, seeded).</p>
<p>RECOMMENDED (the conservative defaults, the spec's own example):
alpha={RECOMMENDED['alpha']}, delta_max={RECOMMENDED['delta_max']:.2f},
daily_cap={RECOMMENDED['daily_cap']:.2f} — net {rec['net_delta']:+.4f},
worst single-book {rec['worst_single_book_loss']:+.4f},
revert freq {rec['auto_revert_freq']:.1%} over {rec['applied_count']} applies.</p>
<table><tr><th>alpha</th><th>delta_max</th><th>daily_cap</th>
<th>net_delta</th><th>CI(p5,p95)</th><th>worst single-book</th>
<th>auto-revert freq</th><th>applies</th></tr>{rows}</table>
{_chart(cells, history)}
<p>read: the worst single-book loss is the most the guard could have
been wrong ONCE at once (the largest loosening any one rotation earned);
the auto-revert frequency is the whipsaw proxy. A cell with a fat worst
loss or a hot revert freq buys width at the cost of stability — the
conservative defaults exist to stay out of both.</p>
</body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def focused_sweep(history: list[dict]) -> list[dict]:
    """The 2x2 focused extract, worst single-book loss per cell — the
    enablement read the checklist asks for before arming the canary."""
    out = []
    for alpha, dmax in itertools.product(FOCUSED_ALPHAS, FOCUSED_DELTAS):
        res = replay(history, alpha, dmax, FOCUSED_CAP)
        lo, hi = bootstrap_ci(res["applied"])
        out.append({
            "alpha": alpha, "delta_max": dmax,
            "daily_cap": FOCUSED_CAP,
            "net_delta": round(res["net_delta"], 4),
            "ci_p5": round(lo, 4), "ci_p95": round(hi, 4),
            "worst_single_book_loss": round(res["worst"], 4),
            "applied_count": res["applied_count"],
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rotations", type=int, default=30)
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "sim"
                                         / "reports"))
    ap.add_argument("--focused", action="store_true",
                    help="the 2x2 alpha/delta_max extract only")
    args = ap.parse_args()
    history = load_history(args.rotations)
    stamp = datetime.now().strftime("%Y-%m-%d")
    if args.focused:
        cells = focused_sweep(history)
        payload = {"focused": True, "history": history,
                   "cells": cells}
        out = Path(args.out) / f"sensitivity_focused_{stamp}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"focused extract: {len(cells)} cells, "
              f"{sum(h['B'] for h in history)} burials")
        for c in cells:
            print(f"  alpha={c['alpha']} dmax={c['delta_max']}: "
                  f"net {c['net_delta']:+.4f}, "
                  f"worst single-book "
                  f"{c['worst_single_book_loss']:+.4f}, "
                  f"{c['applied_count']} applies")
        print(f"json: {out}")
        return 0
    cells = sweep(history)
    out = Path(args.out) / f"sensitivity_{stamp}.html"
    write_report(cells, history, out)
    (out.parent / f"sensitivity_{stamp}.json").write_text(
        json.dumps({"history": history, "cells": cells,
                    "recommended": RECOMMENDED}, indent=2),
        encoding="utf-8")
    print(f"history: {len(history)} rotations, "
          f"{sum(h['B'] for h in history)} measurable burials")
    print(f"cells: {len(cells)}; recommended: {RECOMMENDED}")
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
