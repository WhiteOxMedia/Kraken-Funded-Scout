"""Neighborhood sweep for the validated 5m bounce leg.

PEPE GO BIG is held fixed; only the bounce parameters move.  The point is
to see whether +12% survives nearby settings, not just the exact fit.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import contest_replay as cr  # noqa: E402
from scratch.bounce_core import make_events, simulate_one_slot  # noqa: E402

TAPE = ROOT / "scratch" / "kraken_5m_long.json"
START = 10_000.0
DEATH = START * 0.97
BASE = {"liq": 10_000.0, "drop": 4.0, "hold": 6,
        "stop": 2.0, "take": 3.0}
LEVELS = {
    "liq": (5_000.0, 10_000.0, 20_000.0),
    "drop": (3.5, 4.0, 4.5),
    "hold": (4, 6, 8),
    "stop": (1.5, 2.0, 2.5),
    "take": (2.5, 3.0, 3.5),
}
WIDE = {
    "liq": (0.0, 2_500.0, 5_000.0, 7_500.0, 8_000.0, 9_000.0,
            10_000.0, 11_000.0, 12_000.0, 15_000.0, 20_000.0, 30_000.0),
    "drop": (3.0, 3.5, 3.7, 3.8, 3.9, 4.0, 4.1, 4.2, 4.3, 4.5, 5.0),
    "hold": (2, 3, 4, 5, 6, 7, 8, 10, 12),
    "stop": (1.0, 1.5, 2.0, 2.5, 3.0, 3.5),
    "take": (2.0, 2.5, 3.0, 3.5, 4.0, 5.0),
}
BOUNDS = [dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc),
          dt.datetime(2026, 10, 16, tzinfo=dt.timezone.utc),
          dt.datetime(2026, 10, 30, tzinfo=dt.timezone.utc),
          dt.datetime(2026, 11, 14, tzinfo=dt.timezone.utc)]


def _dt(x) -> dt.datetime:
    return x if isinstance(x, dt.datetime) else dt.datetime.fromisoformat(x)


def pepe_events(doc) -> list[dict]:
    original = cr._candidates
    cr._candidates = lambda f, k, b, t: [
        c for c in original(f, k, b, t) if c["asset"] == "PEPE"
    ]
    try:
        result = cr.replay(doc, min_buffer_usd=250,
                           stop_usd=100, take_usd=500)
    finally:
        cr._candidates = original
    return [{
        "asset": t["asset"], "i": t["entry_i"], "exit_i": t["exit_i"],
        "entry_ts": _dt(t["entry_ts"]), "carry": None,
        "ret": t["ret_net_pct"] / 100.0, "kind": "pepe",
    } for t in result["trades"]]


def run_config(frames, pepe, params):
    bounce = make_events(
        frames, drop=params["drop"], hold=params["hold"],
        stop_pct=params["stop"], take_pct=params["take"],
        min_carry=params["liq"],
    )
    events = sorted(bounce + pepe, key=lambda e: e["i"])
    taken, final, low = simulate_one_slot(
        events, start_balance=START, death_line=DEATH,
    )
    slices = []
    for a, b in zip(BOUNDS, BOUNDS[1:]):
        sub = [e for e in taken if a <= e["entry_ts"] < b]
        eq = START
        for e in sub:
            eq *= 1.0 + e["ret"]
        slices.append(round((eq / START - 1) * 100, 2))
    return {
        **params,
        "final": round(final, 2),
        "return": round((final / START - 1) * 100, 2),
        "low": round(low, 2),
        "trades": len(taken),
        "bounces": sum(e["kind"] != "pepe" for e in taken),
        "slices": slices,
        "pass": final >= START * 1.12 - 1e-9,
        "death_safe": low >= DEATH,
        "all_slices_positive": all(x > 0 for x in slices),
    }


def fmt(p):
    return (f"liq={p['liq']:.0f} drop={p['drop']:.1f} hold={p['hold']} "
            f"stop={p['stop']:.1f} take={p['take']:.1f}")


def main():
    doc = json.loads(TAPE.read_text(encoding="utf-8"))
    frames = cr._frames(doc)
    pepe = pepe_events(doc)

    grid = []
    for vals in itertools.product(*(LEVELS[k] for k in BASE)):
        params = dict(zip(BASE, vals))
        row = run_config(frames, pepe, params)
        row["source"] = "grid"
        grid.append(row)

    ofat = []
    for key, values in WIDE.items():
        for v in values:
            if v == BASE[key]:
                continue
            params = {**BASE, key: v}
            row = run_config(frames, pepe, params)
            row["source"] = key
            ofat.append(row)

    base = next(r for r in grid if all(r[k] == BASE[k] for k in BASE))
    print("BASE", fmt(base), "->", base["return"], "%",
          "slices", base["slices"], "low", base["low"])

    passed = [r for r in grid if r["pass"] and r["death_safe"]]
    robust = [r for r in passed if r["all_slices_positive"]]
    print(f"\nGRID {len(grid)} configs: pass={len(passed)} "
          f"({100 * len(passed) / len(grid):.0f}%), "
          f"pass+all-slices-positive={len(robust)}, "
          f"death-safe={sum(r['death_safe'] for r in grid)}")
    print("\nTOP 15 BY RETURN")
    for r in sorted(grid, key=lambda x: -x["return"])[:15]:
        print(f"  {r['return']:6.2f}%  low={r['low']:,.0f}  "
              f"bounce={r['bounces']} slices={r['slices']}  {fmt(r)}")

    print("\nMARGINAL PASS RATE BY PARAMETER VALUE (grid)")
    for key in BASE:
        print(f"  {key}:")
        for v in LEVELS[key]:
            sub = [r for r in grid if r[key] == v]
            ok = sum(r["pass"] and r["death_safe"] for r in sub)
            med = statistics.median(r["return"] for r in sub)
            print(f"    {v:>8}: {ok:>2}/{len(sub):<2} pass  "
                  f"median {med:+.2f}%  "
                  f"range {min(r['return'] for r in sub):+.2f}.."
                  f"{max(r['return'] for r in sub):+.2f}%")

    def matrix(xkey, ykey, fixed):
        print(f"\nMATRIX {xkey} x {ykey}  ({fixed})")
        print("          " + "".join(f"{y:>9}" for y in LEVELS[ykey]))
        for x in LEVELS[xkey]:
            cells = []
            for y in LEVELS[ykey]:
                r = next(q for q in grid if q[xkey] == x and q[ykey] == y
                         and all(q[k] == v for k, v in fixed.items()))
                cells.append(f"{r['return']:>8.2f}%")
            print(f"{x:>9} " + "".join(cells))

    matrix("drop", "hold", {"liq": 10_000.0, "stop": 2.0, "take": 3.0})
    matrix("stop", "take", {"liq": 10_000.0, "drop": 4.0, "hold": 6})
    matrix("liq", "drop", {"hold": 6, "stop": 2.0, "take": 3.0})

    print("\nWIDE ONE-FACTOR-AT-A-TIME")
    for key in BASE:
        for r in sorted([x for x in ofat if x["source"] == key],
                        key=lambda x: x[key]):
            print(f"  {fmt(r)} -> {r['return']:+7.2f}%  "
                  f"pass={str(r['pass']):5s} slices={r['slices']}")

    out = {"base": base, "grid": grid, "ofat": ofat}
    (ROOT / "scratch" / "sweep_bounce.json").write_text(
        json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
