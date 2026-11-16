"""_forward_vs_backtest.py — does the paper lane agree with the engine replay?

Same rules both arms: one position at a time, 9% of current equity per entry,
0.08% round-trip fee, bars from 2026-11-12 23:00 UTC (the paper lane's first
forward trade) onward.

  forward  the rows edge_forward_trades.jsonl actually booked
  replay   ep.Resolver on the same window, chronological, one slot

If the two agree, the forward record is a real out-of-sample test of the policy
rather than an artefact of a different exit path.

Read-only. Usage: python scratch/_forward_vs_backtest.py [policy_id]
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
from config import TradingConfig                             # noqa: E402

PID = "L.dip.risk_off.breakeven.9.any.us"
START_TS = pd.Timestamp("2026-11-12 23:00", tz="UTC")
START = 10_000.0
SIZE = 0.09
FEE_PCT = 0.08


def forward_rows(pid: str) -> list[dict]:
    out = []
    for line in (ROOT / "edge_forward_trades.jsonl").open(encoding="utf-8"):
        t = json.loads(line)
        if t.get("id") == pid:
            out.append(t)
    return out


def replay(pol, rows, resolver) -> list[dict]:
    events = [r for r in rows if pol.rule(r["f"])
              and pd.Timestamp(r["ts"]) >= START_TS]
    events.sort(key=lambda r: r["ts"])
    equity, out, held_until = START, [], None
    for r in events:
        ts = pd.Timestamp(r["ts"])
        if held_until is not None and ts <= held_until:
            continue
        res = resolver.resolve(r["pair"], r["i"], pol.side, pol.exit_mode)
        if res is None:
            continue
        entry, px = float(r["f"]["close"]), float(res["px"])
        size = equity * SIZE
        qty = size / entry
        pnl = qty * (px - entry) - size * FEE_PCT / 100.0
        equity += pnl
        held_until = pd.Timestamp(res["exit_ts"])
        out.append({"ts": str(ts), "pair": r["pair"], "why": res.get("why"),
                    "pnl": round(pnl, 2), "equity": round(equity, 2)})
    return out


def show(tag: str, trades: list[dict]) -> None:
    wins = sum(1 for t in trades if t["pnl"] > 0)
    net = sum(t["pnl"] for t in trades)
    print(f"  {tag}: trades={len(trades)} wins={wins} net=${net:+.2f} "
          f"final=${START + net:.2f}")
    for t in trades:
        print(f"    {t['ts'][:16]} {t['pair']:10s} {str(t['why']):11s} "
              f"{t['pnl']:+7.2f}  eq={t['equity']:.2f}")


def main() -> int:
    pid = sys.argv[1] if len(sys.argv) > 1 else PID
    reg = json.loads((ROOT / "edge_registry.json").read_text(encoding="utf-8"))
    spec = ((reg.get("candidates") or {}).get(pid) or {}).get("spec") or {}
    pol = ep.policy_from_spec(spec)
    print(f"{pid}  ({pol.label})  window from {START_TS}")
    pairs = ee.load_pairs()
    rows = ep.build_base_rows(pairs)
    resolver = ep.Resolver(pairs, TradingConfig())
    resolver.use_cache = True
    fwd = forward_rows(pid)
    rep = replay(pol, rows, resolver)
    show("forward (paper lane)", fwd)
    show("replay  (engine)     ", rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
