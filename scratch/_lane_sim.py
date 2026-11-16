"""_lane_sim.py — the paper lane's own logic on history, diffed against the chain.

Why: the live paper lane reports 6-9% win rates while the backtests report
25-50%.  Either the lane counts/exits differently (artefact) or the forward
weeks were simply a bad regime.  This replays the lane's decisions on bars it
never traded, using the lane's own exit function, sizing, fee and retirement
rules, then prints the engine chain's numbers for the same policy and period.

Faithful to `edge_forward`:
  * entry at the CLOSE of the signal bar, one position per candidate, slots at
    9% of current equity, 0.08% round-trip fee on notional,
  * exits resolved by edge_forward.exit_fill_policy (breakeven moves the stop to
    entry after +1R; stop wins an ambiguous candle; 336-bar time exit),
  * no trade on a pair's still-forming last bar,
  * a pair that stops printing closes at its last printed close with
    why="stale" once it is `edge_forward.STALE_AFTER_HOURS` behind the tape,
    so a delisted pair cannot hold the single slot forever,
  * +12% rebases the paper start, -3% retires the candidate.

Read-only.  Usage: python scratch/_lane_sim.py [policy_id] [--fixed]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backtest_12pct_compare as bt                          # noqa: E402
import edge_engine as ee                                     # noqa: E402
import edge_forward as ef                                    # noqa: E402
import edge_policy as ep                                     # noqa: E402
from config import TradingConfig                             # noqa: E402

PID = "L.dip.risk_off.breakeven.9.any.us"
START = ef.START
WIN = ef.WIN
DEATH = ef.DEATH
FEE_PCT = ef.FEE_RT_PCT
SIZE = ef.SIZE_FRAC


def window_returns(bounds, marks) -> list[float | None]:
    """Return % for each chain window from a list of (ts, equity) marks."""
    out = []
    for lo, hi in bounds:
        lo_t, hi_t = pd.Timestamp(lo, tz="UTC"), pd.Timestamp(hi, tz="UTC")
        start = None
        end = None
        for ts, eq in marks:
            if start is None and ts > lo_t:
                start = eq
            if ts <= hi_t:
                end = eq
        out.append(None if start is None or end is None
                   else round((end / start - 1.0) * 100.0, 3))
    return out


def simulate(pol, rows, pairs, cfg, mode: str, start_ts, end_ts):
    """One carried paper account through `rows`, lane-semantics, one slot."""
    arr = {p: (df["open"].to_numpy(float), df["low"].to_numpy(float),
               df["high"].to_numpy(float), df["close"].to_numpy(float),
               {ts: i for i, ts in enumerate(df.index)})
           for p, df in pairs.items()}
    by_ts: dict = {}
    for r in rows:
        if r["ts"] < start_ts or (end_ts is not None and r["ts"] > end_ts):
            continue
        by_ts.setdefault(r["ts"], []).append(r)
    for bunch in by_ts.values():
        bunch.sort(key=lambda r: r["pair"])

    equity, mark, trades, why_hist = START, START, [], {}
    pos, retired, passes, deaths = None, False, 0, 0
    last_bar = {p: (df.index[-1], float(df["close"].iloc[-1]))
                for p, df in pairs.items() if len(df)}
    marks: list = []                                    # (ts, equity) clock
    clock = sorted({ts for df in pairs.values() for ts in df.index
                    if ts >= start_ts and (end_ts is None or ts <= end_ts)})
    def close_pos(px: float, why: str, ts) -> None:
        """Book the open position out at `px` and free the single slot."""
        nonlocal pos, equity, mark, passes, deaths, retired
        fee = float(pos["size"]) * FEE_PCT / 100.0
        pnl = float(pos["qty"]) * (px - float(pos["entry"])) - fee
        equity += pnl
        why_hist[why] = why_hist.get(why, 0) + 1
        trades.append({"ts": str(ts), "entry_ts": str(pos.get("entry_ts")),
                       "pair": pos["pair"], "why": why,
                       "pnl": round(pnl, 2), "equity": round(equity, 2)})
        if equity <= mark * DEATH:
            deaths += 1
            retired = True
        elif equity >= mark * WIN:
            passes += 1
            mark = equity
        pos = None

    for ts in clock:
        if pos is not None:
            op, lo, hi, cl, idx = arr[pos["pair"]]
            j = idx.get(ts)
            if j is not None and j > pos["j"] and j < len(cl) - 1:
                bars = j - pos["i"]
                # signature is (pos, open, high, low, close, bars)
                fill = ef.exit_fill_policy(pos, op[j], hi[j], lo[j], cl[j],
                                           bars)
                pos["j"] = j
                if fill is not None:
                    close_pos(fill[0], fill[1], ts)
            if pos is not None:
                # the pair has gone silent: nothing it prints can close this
                lb_ts, lb_px = last_bar[pos["pair"]]
                if lb_ts < ts - pd.Timedelta(hours=ef.STALE_AFTER_HOURS):
                    close_pos(lb_px, "stale", lb_ts)
        if pos is None and not retired:
            for r in by_ts.get(ts, ()):
                if not pol.rule(r["f"]):
                    continue
                entry = float(r["f"]["close"])
                atr = float(r["f"]["atr"])
                stop, tp = bt.live_levels(entry, atr, cfg)
                if stop is None or stop >= entry:
                    continue
                if mode == "chandelier":
                    tp = float("inf")
                size = equity * float(pol.size_frac)
                pos = {"pair": r["pair"], "i": r["i"], "j": r["i"],
                       "entry_ts": ts,
                       "entry": entry, "stop": stop, "tp": tp, "size": size,
                       "qty": size / entry, "atr": atr,
                       "risk": abs(entry - stop), "side": "long",
                       "exit_mode": mode, "peak": entry}
                break
        marks.append((ts, equity))
    return {"trades": trades, "why": why_hist, "equity": round(equity, 2),
            "passes": passes, "deaths": deaths, "retired": retired,
            "marks": marks, "open": pos}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pid = args[0] if args else PID
    reg = json.loads((ROOT / "edge_registry.json").read_text(encoding="utf-8"))
    spec = ((reg.get("candidates") or {}).get(pid) or {}).get("spec") or {}
    pol = ep.policy_from_spec(spec)
    if pol is None:
        print(f"{pid} is not executable", file=sys.stderr)
        return 2
    cfg = TradingConfig()
    print(f"{pid}\n  {pol.label}  exit={pol.exit_mode} size={pol.size_frac}")

    pairs = ee.load_pairs()
    rows = ep.build_base_rows(pairs)
    ee.fill_atr_rank(rows)
    print(f"  rows={len(rows)} pairs={len(pairs)}", flush=True)

    lo = pd.Timestamp(ee.WINDOWS[0][0], tz="UTC")
    hi = max(df.index[-2] for df in pairs.values())

    for mode in (pol.exit_mode, "fixed"):
        sim = simulate(pol, rows, pairs, cfg, mode, lo, hi)
        n = len(sim["trades"])
        w = sum(1 for t in sim["trades"] if t["pnl"] > 0)
        wr = window_returns(ee.WINDOWS, sim["marks"])
        print(f"\n  LANE SIM [{mode}]: trades={n} wins={w} "
              f"({w / n:.0%}) final=${sim['equity']:,.2f} "
              f"passes={sim['passes']} deaths={sim['deaths']} "
              f"retired={sim['retired']} "
              f"open={((sim.get('open') or {}).get('pair'), (sim.get('open') or {}).get('i')) if sim.get('open') else None}")
        print(f"    exits={sim['why']}")
        print(f"    windows={wr}")

    sel = ep.selected_for(pol, rows, ep.Resolver(pairs, cfg))
    ch = ep.chain_result(pol, sel)
    ws = ch.get("windows") or []
    print(f"\n  ENGINE CHAIN: trades={ch.get('trades')} "
          f"wins={ch.get('wins')} ({ch.get('win_rate'):.0%}) "
          f"final=${ch.get('final'):,.2f} passes={ch.get('passes')}/3 "
          f"died={ch.get('died')}")
    print(f"    windows={[w.get('return_pct') for w in ws]}")
    print(f"    per-window trades/wins="
          f"{[(w.get('trades'), w.get('wins')) for w in ws]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
