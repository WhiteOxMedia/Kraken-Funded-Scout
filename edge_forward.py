"""EDGE FORWARD — the paper contest that proves a selector on data it never saw.

`edge_engine.py` is a walk-forward SEARCH.  This module is the missing second
half: it starts a $10,000 paper contest for every `promoted`, `watch` or
`research` selector in `edge_registry.json` and only ever trades bars that
arrive AFTER the state cursor.  No historical row is replayed into a forward
result.

The paper contest uses the same arithmetic as the live account:
  * 9% of current equity per entry (config.contest_size_frac),
  * the live ATR stop and 2.5R target (bt.live_levels),
  * 0.08% round-trip cost on notional,
  * one position at a time per selector,
  * +12% from the current paper start is a PASS; -3% is a DEATH.
After a pass the paper start rebases to the new equity, so the selector has to
keep passing.  A death retires it.  This is forward evidence, not an order and
not a change to the live contest ledger.

Run:
    python -X utf8 edge_forward.py --run
    python -X utf8 edge_forward.py --watch --interval 300
    python -X utf8 edge_forward.py --status
    python -X utf8 edge_forward.py --reset
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import edge_engine as ee
import edge_policy as ep

BASE = Path(__file__).resolve().parent
STATE = BASE / "edge_forward_state.json"
TRADES = BASE / "edge_forward_trades.jsonl"
REPORT = BASE / "edge_forward_report.json"
FEE_RT_PCT = 0.08
SIZE_FRAC = 0.09
START = 10_000.0
WIN = 1.12
DEATH = 0.97
MAX_HOLD = 24 * 14
ELIGIBLE = ("promoted", "watch", "research")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path, default):
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, type(default)) else default
    except (OSError, ValueError, TypeError):
        return default


def _write_json(path: Path, doc) -> None:
    tmp = str(path) + f".tmp.{os.getpid()}"
    Path(tmp).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def first_after(index, last) -> int:
    """First integer position strictly after `last` in a DatetimeIndex."""
    if not last:
        return len(index) - 1
    return int(index.searchsorted(pd.Timestamp(last), side="right"))


def _candidates() -> dict:
    reg = _read_json(ee.REGISTRY, {})
    out = {}
    for cid, row in (reg.get("candidates") or {}).items():
        if row.get("status") not in ELIGIBLE:
            continue
        pol = ep.policy_from_spec(row.get("spec") or {})
        if pol is None:
            # forward the legacy entry-only registry only if it still has a
            # complete policy spec; a malformed row is never guessed at
            continue
        out[cid] = {
            "spec": pol,
            "status_at_start": row.get("status"),
            "label": row.get("label", cid),
        }
    return out


def _new_state(cid: str, label: str) -> dict:
    return {
        "id": cid, "label": label,
        "equity": START, "start": START,
        "passes": 0, "deaths": 0,
        "trades": 0, "wins": 0, "net": 0.0,
        "open": {}, "retired": False,
        "created_at": _now(),
    }


def _init_state(candidates: dict, old: dict) -> dict:
    states = old.get("candidates") if isinstance(old.get("candidates"), dict) else {}
    for cid, c in candidates.items():
        if cid not in states:
            states[cid] = _new_state(cid, c["label"])
        states[cid]["label"] = c["label"]
    return {
        "schema": 1, "updated_at": _now(),
        "last_bar": old.get("last_bar") or {},
        "candidates": states,
    }


def exit_fill_policy(pos: dict, o: float, h: float, l: float, c: float,
                     bars: int, max_hold: int = MAX_HOLD):
    """Resolve one held bar for a side-aware policy exit.

    Fixed is the shipped 2.5R target.  Breakeven moves the stop to entry
    after +1R.  Chandelier has no fixed target and trails at 1.5 ATR under
    the peak after +1R.  Stop always wins on an ambiguous candle.
    """
    side = pos.get("side", "long")
    mode = pos.get("exit_mode", "fixed")
    entry = float(pos["entry"]); atr = float(pos.get("atr") or 0.0)
    stop = float(pos["stop"]); tp = float(pos["tp"])
    risk = float(pos.get("risk") or (atr * 1.5))
    peak = float(pos.get("peak") or entry)
    if side == "long":
        if o <= stop:
            return o, "stop-gap", peak
        if math.isfinite(tp) and o >= tp:
            return o, "target-gap", peak
        if l <= stop:
            return stop, "stop", peak
        if math.isfinite(tp) and h >= tp:
            return tp, "target", peak
        if mode == "breakeven" and h >= entry + risk:
            stop = max(stop, entry)
        if mode == "chandelier":
            peak = max(peak, h)
            if h >= entry + risk:
                stop = max(stop, peak - atr * 1.5)
    else:
        if o >= stop:
            return o, "stop-gap", peak
        if math.isfinite(tp) and o <= tp:
            return o, "target-gap", peak
        if h >= stop:
            return stop, "stop", peak
        if math.isfinite(tp) and l <= tp:
            return tp, "target", peak
        if mode == "breakeven" and l <= entry - risk:
            stop = min(stop, entry)
        if mode == "chandelier":
            peak = min(peak, l)
            if l <= entry - risk:
                stop = min(stop, peak + atr * 1.5)
    pos["stop"] = stop
    pos["peak"] = peak
    if bars >= max_hold:
        return c, "time", peak
    return None


def exit_fill(o: float, h: float, l: float, c: float, stop: float,
              tp: float, bars: int, max_hold: int = MAX_HOLD):
    """Resolve one held bar; stop wins on an ambiguous candle.

    Returns (fill_price, reason) or None when the position stays open.
    Gaps fill at the bar's open, exactly as the backtest labs assume.
    """
    if o <= stop:
        return o, "stop-gap"
    if o >= tp:
        return o, "target-gap"
    if l <= stop:
        return stop, "stop"
    if h >= tp:
        return tp, "target"
    if bars >= max_hold:
        return c, "time"
    return None


def _book(cs: dict, pnl: float, px: float, why: str, ts, pair: str,
          entry: float, size: float) -> dict:
    """Apply one closed paper trade to a candidate's running contest."""
    cs["equity"] = float(cs["equity"]) + float(pnl)
    cs["trades"] = int(cs.get("trades") or 0) + 1
    cs["wins"] = int(cs.get("wins") or 0) + (1 if pnl > 0 else 0)
    cs["net"] = round(float(cs.get("net") or 0.0) + float(pnl), 2)
    trade = {
        "ts": str(ts), "pair": pair, "entry": round(entry, 8),
        "exit": round(px, 8), "why": why, "size": round(size, 2),
        "pnl": round(pnl, 4), "equity": round(cs["equity"], 2),
        "id": cs["id"],
    }
    with open(TRADES, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(trade) + "\n")
    if cs["equity"] <= float(cs["start"]) * DEATH:
        cs["deaths"] = int(cs.get("deaths") or 0) + 1
        cs["retired"] = True
    elif cs["equity"] >= float(cs["start"]) * WIN:
        cs["passes"] = int(cs.get("passes") or 0) + 1
        cs["start"] = float(cs["equity"])     # continuous: keep passing
    return trade


def _signal(df: pd.DataFrame, i: int, cfg) -> tuple[bool, float]:
    from strategy import evaluate_strategy
    try:
        sig = evaluate_strategy(df.iloc[max(0, i - 259):i + 1], cfg)
        if sig.action == "buy" and (sig.confidence or 0) >= cfg.min_confidence:
            return True, float(sig.confidence or 0)
    except Exception:
        pass
    return False, 0.0


def _collect_new(pairs: dict, state: dict, cfg) -> tuple[dict, dict]:
    """Signals on closed bars after each pair's cursor.

    Returns ({pair: [(ts, i, features, quick)]}, new_cursor_map).  The final
    cached row is treated as still forming and is never traded.
    """
    atrs = {p: ee.atr14(df) for p, df in pairs.items()}
    feats = {p: ee.frame_features(df, atrs[p]) for p, df in pairs.items()}
    ranks = ee._panel_ranks(pairs)
    old = state.get("last_bar") or {}
    found: dict[str, list] = {}
    cursor: dict[str, str] = {}
    for p, df in pairs.items():
        if len(df) < 210:
            continue
        last = old.get(p)
        # searchsorted, not get_loc: a pair that was stale when the cursor
        # was set may have no bar exactly at the floor timestamp.
        first = first_after(df.index, last)
        # never trade the last, still-forming bar.  The policy itself decides
        # the entry; the old strategy buy gate would make short policies short
        # a strategy buy, which is not the same trade at all.
        for i in range(max(210, first), len(df) - 1):
            found.setdefault(p, []).append(
                (df.index[i], i, ee.features_at(feats[p], ranks,
                                                df.index[i], i), 0.0))
        # A stale pair must not move its cursor BACKWARDS when it finally
        # receives a bar; keep the later of the old cursor and this frame.
        prev = pd.Timestamp(last) if last else pd.Timestamp.min.tz_localize("UTC")
        cursor[p] = str(max(prev, df.index[-2]))
    # cross-sectional ATR rank, computed within each timestamp so a
    # squeeze selector sees the same ordering in forward as it did in search
    buckets: dict = {}
    for p, rows in found.items():
        for ts, _i, f, _q in rows:
            buckets.setdefault(ts, []).append(f)
    for fs in buckets.values():
        vals = sorted(f["atr_pct"] for f in fs
                      if math.isfinite(f["atr_pct"]))
        if len(vals) < 3:
            continue
        for f in fs:
            v = f["atr_pct"]
            if not math.isfinite(v):
                continue
            lo = sum(1 for x in vals if x < v)
            eq = sum(1 for x in vals if x == v)
            f["atr_rank"] = (lo + 0.5 * eq) / len(vals)
    return found, cursor


def _advance_open(pairs: dict, state: dict, events: list) -> None:
    """Mark every open paper trade against bars it has not seen yet."""
    for cs in (state.get("candidates") or {}).values():
        if cs.get("retired"):
            continue
        for pair in list(cs.get("open") or {}):
            pos = cs["open"][pair]
            df = pairs.get(pair)
            if df is None:
                continue
            try:
                start = df.index.get_loc(pd.Timestamp(pos["entry_ts"])) + 1
            except (KeyError, ValueError):
                start = 0
            for j in range(max(0, start), len(df) - 1):
                bars = j - df.index.get_loc(pd.Timestamp(pos["entry_ts"]))
                fill = exit_fill_policy(
                    pos, float(df["open"].iloc[j]),
                    float(df["high"].iloc[j]),
                    float(df["low"].iloc[j]),
                    float(df["close"].iloc[j]), bars)
                if fill is None:
                    continue
                px, why, _peak = fill
                fee = float(pos["size"]) * FEE_RT_PCT / 100.0
                if pos.get("side", "long") == "long":
                    pnl = float(pos["qty"]) * (px - float(pos["entry"])) - fee
                else:
                    pnl = float(pos["qty"]) * (float(pos["entry"]) - px) - fee
                _book(cs, pnl, px, why, df.index[j], pair,
                      float(pos["entry"]), float(pos["size"]))
                del cs["open"][pair]
                break


def _open_new(events: dict, state: dict, candidates: dict, cfg) -> None:
    """One position per selector, entered at the latest closed signal bar.

    Signals older than the most recent closed bar are skipped: after any
    downtime the book must not fill at prices that are already history.
    """
    import backtest_12pct_compare as bt
    for cid, c in candidates.items():
        cs = state["candidates"].get(cid)
        if not cs or cs.get("retired") or cs.get("open"):
            continue
        flat = [(ts, p, i, f, q) for p, rows in events.items()
                for ts, i, f, q in rows]
        if not flat:
            continue
        latest = max(x[0] for x in flat)
        flat = [x for x in flat if x[0] == latest]
        flat.sort(key=lambda x: x[1])
        for ts, p, i, f, _q in flat:
            if not c["spec"].rule(f):
                continue
            entry = float(f["close"])
            atr = float(f["atr"])
            if not (entry > 0 and atr > 0):
                continue
            stop, tp = bt.live_levels(entry, atr, cfg)
            if stop is None or stop >= entry:
                continue
            side = c["spec"].side
            mode = c["spec"].exit_mode
            if side == "short":
                risk = entry - stop
                stop, tp = entry + risk, entry - risk * float(
                    cfg.risk_reward_ratio)
                if mode == "chandelier":
                    tp = float("-inf")  # no fixed target
            elif mode == "chandelier":
                tp = float("inf")
            size = float(cs["equity"]) * float(c["spec"].size_frac)
            cs["open"][p] = {
                "entry_ts": str(ts), "entry": entry, "stop": stop, "tp": tp,
                "size": size, "qty": size / entry, "atr": atr,
                "risk": abs(entry - stop), "side": side,
                "exit_mode": mode, "peak": entry,
            }
            break


def run_once(reset: bool = False) -> dict:
    from config import TradingConfig

    cfg = TradingConfig()
    pairs = ee.load_pairs()
    state = _read_json(STATE, {})
    if reset:
        state = {}
    candidates = _candidates()
    state = _init_state(candidates, state)
    if reset or not state.get("last_bar"):
        # Start every pair at the SAME closed-bar floor.  A stale pair must
        # not later backfill a month of history into the forward book.
        floor = max(df.index[-2] for df in pairs.values())
        state["last_bar"] = {p: str(floor) for p in pairs}
        candidates = _candidates()
    _advance_open(pairs, state, [])
    events, cursor = _collect_new(pairs, state, cfg)
    _open_new(events, state, candidates, cfg)
    state["last_bar"].update(cursor)
    state["updated_at"] = _now()
    _write_json(STATE, state)
    report = {"generated_at": state["updated_at"],
              "data_through": str(max(d.index.max() for d in pairs.values())),
              "candidates": state["candidates"]}
    _write_json(REPORT, report)
    return report


def status() -> dict:
    doc = _read_json(REPORT, {})
    return doc or {"candidates": {}, "note": "no forward run yet"}


def _summary(cs: dict) -> dict:
    n = int(cs.get("trades") or 0)
    return {
        "id": cs.get("id"), "label": cs.get("label"),
        "equity": round(float(cs.get("equity") or 0), 2),
        "start": round(float(cs.get("start") or 0), 2),
        "passes": int(cs.get("passes") or 0),
        "deaths": int(cs.get("deaths") or 0),
        "trades": n, "wins": int(cs.get("wins") or 0),
        "net": round(float(cs.get("net") or 0), 2),
        "retired": bool(cs.get("retired")),
        "open": list((cs.get("open") or {}).keys()),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="forward paper contest")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--interval", type=float, default=300.0)
    args = ap.parse_args(argv)
    if args.status:
        print(json.dumps(status(), indent=1)[:12000])
        return 0
    if not (args.run or args.watch):
        print("use --run, --watch or --status")
        return 2
    while True:
        try:
            rep = run_once(reset=args.reset)
            args.reset = False
            rows = [_summary(c) for c in rep["candidates"].values()]
            rows.sort(key=lambda r: (r["deaths"], -r["passes"], -r["equity"]))
            print(json.dumps(rows, indent=1), flush=True)
        except Exception:
            if not args.watch:
                raise
            import traceback
            traceback.print_exc()
        if not args.watch:
            return 0
        time.sleep(max(60.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
