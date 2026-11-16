"""_forward_replay.py — is the lane's 9% win rate plumbing or regime?

Replays every candidate that has a paper-lane trade over the lane's own window
(Nov 12 15:27 -> the last closed bar) through the ENGINE's exit path
(`edge_policy.Resolver`, close fills), one slot, 9% of current equity per
entry, then compares pooled win rate, net dollars and exit mix
against the 536 `edge_forward_trades.jsonl` rows.

If the replay also loses, the window was bad.  If it makes money on the same
policies and bars, the lane's plumbing is the difference.

Read-only.  Usage: python scratch/_forward_replay.py [--top N]
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import edge_engine as ee                                     # noqa: E402
import edge_policy as ep                                     # noqa: E402
from config import TradingConfig                             # noqa: E402

LANE_START = pd.Timestamp("2026-11-12 15:27", tz="UTC")   # the lane's last reset
SIZE = 0.09
START = 10_000.0
SCRATCH_BAND = 0.15          # same band as edge_forward's summary
GAP = {"stop-gap": "stop", "target-gap": "target"}


def replay(pol, rows, resolver, size: float = SIZE):
    """The lane's arithmetic (one slot, 9% of equity) with the engine's exits."""
    sel = ep.selected_for(pol, rows, resolver)
    equity, busy, trades = START, None, []
    for r, res in sel:
        ts = r["ts"]
        if busy is not None and ts <= busy:      # one slot: a position is open
            continue
        pnl = equity * size * float(res["net"]) / 100.0
        equity += pnl
        busy = res.get("exit_ts")
        trades.append({"ts": ts, "pair": r["pair"], "why": res.get("why"),
                       "net": float(res["net"]), "pnl": pnl,
                       "gross": float(res["net"])})
    return equity, trades


def summarize(trades: list[dict], net_key: str = "pnl") -> dict:
    n = len(trades)
    wins = sum(1 for t in trades if t[net_key] > 0)
    scratch = sum(1 for t in trades
                  if t[net_key] <= 0 and abs(float(t.get("gross", 0.0))) <= SCRATCH_BAND)
    mix = collections.Counter(GAP.get(t["why"], t["why"]) for t in trades)
    return {"trades": n, "wins": wins, "scratch": scratch,
            "win_rate": round(wins / n, 3) if n else None,
            "net": round(sum(t[net_key] for t in trades), 2), "exits": dict(mix)}


def main() -> int:
    top = 99
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])

    lane = [json.loads(l) for l in
            (ROOT / "edge_forward_trades.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    by_id: dict = collections.defaultdict(list)
    for t in lane:
        by_id[t["id"]].append({**t, "gross": (float(t["exit"]) / float(t["entry"]) - 1.0)
                              * 100.0})
    print(f"lane trades={len(lane)} candidates={len(by_id)}")
    lane_all = [t for ts in by_id.values() for t in ts]

    reg = json.loads((ROOT / "edge_registry.json").read_text(encoding="utf-8"))
    cands = reg.get("candidates") or {}
    pairs = ee.load_pairs()
    rows = ep.build_base_rows(pairs)
    ee.fill_atr_rank(rows)
    rows = [r for r in rows if r["ts"] >= LANE_START]
    resolver = ep.Resolver(pairs, TradingConfig(), fill="close")
    resolver.use_cache = True
    print(f"window rows={len(rows)} (from {LANE_START})", flush=True)

    table, totals = [], {"lane": [], "replay": [], "replay_spec": [],
                         "replay_closed": [], "replay_spec_closed": []}
    for cid in sorted(by_id):
        spec = (cands.get(cid) or {}).get("spec") or {}
        pol = ep.policy_from_spec(spec)
        if pol is None:
            print(f"  !! {cid}: no executable spec", flush=True)
            continue
        _eq, tr = replay(pol, rows, resolver)
        _eq2, tr_spec = replay(pol, rows, resolver, pol.size_frac)
        la = summarize(by_id[cid], net_key="pnl")
        rp = summarize(tr)
        totals["lane"] += by_id[cid]
        totals["replay"] += tr
        totals["replay_spec"] += tr_spec
        totals["replay_closed"] += [t for t in tr if t["why"] != "time"]
        totals["replay_spec_closed"] += [t for t in tr_spec if t["why"] != "time"]
        table.append({"id": cid, "size": pol.size_frac,
                      "lane_trades": la["trades"], "lane_win": la["win_rate"],
                      "lane_net": la["net"],
                      "replay_trades": rp["trades"], "replay_win": rp["win_rate"],
                      "replay_net": rp["net"],
                      "lane_exits": la["exits"], "replay_exits": rp["exits"]})
        print(f"  {cid[:44]:44s} lane {la['trades']:2d}t "
              f"{la['win_rate'] or 0:.0%} ${la['net']:+8.2f} | replay "
              f"{rp['trades']:2d}t {rp['win_rate'] or 0:.0%} ${rp['net']:+8.2f}",
              flush=True)

    A = summarize(lane_all, net_key="pnl")
    B = summarize(totals["replay"])
    print("\n=== AGGREGATE: same candidates, same window ===")
    for name, s in (("paper lane", A),
                    ("replay 9% (all)", B),
                    ("replay 9%, closed", summarize(totals["replay_closed"])),
                    ("replay spec-size", summarize(totals["replay_spec"])),
                    ("replay spec, closed", summarize(totals["replay_spec_closed"]))):
        print(f"  {name:19s} trades={s['trades']:3d} wins={s['wins']:3d} "
              f"win_rate={s['win_rate']:.1%} scratch={s['scratch']:2d} "
              f"net=${s['net']:+9.2f}  exits={s['exits']}")
    print(f"\n  win-rate gap   {B['win_rate'] - A['win_rate']:+.1%}")
    print(f"  net gap        ${B['net'] - A['net']:+.2f}")

    print(f"\n=== per candidate (worst lane, top {top}) ===")
    for r in sorted(table, key=lambda r: r["lane_net"])[:top]:
        print(f"  {r['id'][:40]:40s} size {r['size']:.0%}  "
              f"lane {r['lane_trades']:2d}t/{r['lane_win'] or 0:.0%}/"
              f"${r['lane_net']:+8.2f}  replay {r['replay_trades']:2d}t/"
              f"{r['replay_win'] or 0:.0%}/${r['replay_net']:+8.2f}")
    both = [r for r in table if r["lane_net"] < 0 and r["replay_net"] < 0]
    rev = [r for r in table if r["lane_net"] < 0 and r["replay_net"] > 0]
    print(f"\n  both under water: {len(both)}/{len(table)}   "
          f"lane lost but replay made money: {len(rev)}/{len(table)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
