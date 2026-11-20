"""coin_history.py — THE PER-COIN RESPONSE, and the history that measures it (Nov 18).

THE FILLABILITY WALL. The Go Big analysis measured what the contest book can
actually buy. The rule is the shipped one — an order may be at most 1% of the
pair's median bar dollars — so on the 17 Nov tape, at $10,000, only 11 of the
54 contest pairs could absorb a $1,000 order (10% of equity), 7 could absorb
$2,500, 5 could absorb $5,000, and only BTC and ETH the whole balance. The
tape's median pair (over all ~200) absorbed $23.
(`--report` prints the counts for the current tape, which is why the numbers
live in the file and not here.) That is why "idle" attempted ~446 all-ins per
book and only
~43 cleared 10% of equity, why the reserve arm landed 2 of its 6, and why the
preempt arm — forced to sell something it could not buy back — paid the 0.08%
twice and produced 144 deaths.

So a coin's ability to take the order is a per-coin property, measured per
coin, and it is not stable: bar dollars move with the tape. This module owns
that record and its HISTORY, and answers one question for callers:

    response_gate(pair, order_usd) -> can this coin absorb THIS order?

THE STORE (coin_history.json, beside this module):

    {"updated_at": iso, "day": "YYYY-MM-DD", "days_seen": int,
     "participation": 0.01,
     "coins": {"BTC": {
         "median_bar_usd": 1_400_000.0,   # today's own median bar dollars
         "capacity_usd": 14_000.0,        # 1% of it — the biggest compliant order
         "capacity_median_usd": 13_100.0, # median of the daily capacities (THE gate)
         "capacity_worst_usd": 11_800.0,  # the thinnest day seen
         "days": 3, "bars": 2000,
         "cost_rt_pct": 0.0021, "cost_verdict": "ok", "cost_measured": true,
         "tiers": {"1000": true, "2500": true, "5000": true, "10000": false},
         "trend_pct": -12.5,              # latest vs first capacity, %
         "history": [{"day": "...", "median_bar_usd": ..., "capacity_usd": ...,
                      "bars": 2000, "source": "tape", "tape_max_ts": iso,
                      "cost_rt_pct": ...}],
         "updated_at": iso}}}

WHY THE HISTORY DECIDES, NOT THE MOMENT (the same rule `spread_table` lives
by): one observation is a moment, not a measurement. Each history entry is one
DAY's own median bar dollars and `backfill()` rebuilds the last BACKFILL_DAYS
of them from the tape's own past, so the record has a regime before anything
trades. Until a coin has MIN_DAYS days the gate prices TODAY's capacity but
says so (`thin_sample`); after that it prices the MEDIAN of the days — one
thin session cannot inflate a coin's capacity, and a coin whose bar dollars
have been shrinking is priced at what it sustained, with `trend_pct` carried
so a shrinking coin is visible instead of silently cheaper.

A coin with no measurement is REFUSED, never assumed cheap: the platform's
fallback is a price, not a capacity.

    python coin_history.py --measure        # fold the contest cache + spreads in
    python coin_history.py --report         # the per-coin response table
    python coin_history.py --order 2500     # who can absorb $2,500, and why not
"""
from __future__ import annotations

import json
import statistics
import time
from datetime import datetime
from pathlib import Path

from spread_table import _clean

ROOT = Path(__file__).parent
FILE = ROOT / "coin_history.json"

PARTICIPATION = 0.01        # the shipped order rule: <= 1% of median bar dollars
MIN_DAYS = 2                # observed days before the history (not today) prices
MAX_DAYS_KEPT = 30          # the history is a regime record, not an archive
BACKFILL_DAYS = 8           # day boundaries rebuilt from the tape's own past
MIN_DAY_BARS = 12           # bars a day needs before its median is a measure
TAIL_BARS = 2000            # the window `universe_for` medians over
TIERS = (1_000.0, 2_500.0, 5_000.0, 10_000.0)


def _now() -> datetime:
    return datetime.now().astimezone()


def _day_key(now: datetime | None = None) -> str:
    return (now or _now()).strftime("%Y-%m-%d")


def capacity_usd(median_bar_usd: float | None,
                 participation: float = PARTICIPATION) -> float:
    """The biggest order this coin's median bar can absorb under the rule."""
    try:
        v = float(median_bar_usd)
    except (TypeError, ValueError):
        return 0.0
    return round(max(v, 0.0) * float(participation), 2)


def _blank(participation: float = PARTICIPATION) -> dict:
    return {"updated_at": None, "day": None, "days_seen": 0,
            "participation": float(participation), "coins": {}}


def load(path: Path | str | None = None) -> dict:
    """The stored history; a missing or corrupt file is an EMPTY history."""
    p = Path(path) if path else FILE
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("coins"), dict):
            return d
    except (OSError, ValueError):
        pass
    return _blank()


def save(table: dict, path: Path | str | None = None) -> None:
    """Write atomically; a held handle is waited out, never silently lost.

    The rename can fail with WinError 5 while the sync client or another reader
    holds the file for a moment. A store that gives up there loses the day's
    observations, so the replace is retried briefly and only then raises.
    """
    p = Path(path) if path else FILE
    tmp = Path(str(p) + ".tmp")
    tmp.write_text(json.dumps(table, indent=1), encoding="utf-8")
    last: Exception | None = None
    for _ in range(8):
        try:
            tmp.replace(p)
            return
        except PermissionError as exc:      # a lock, not a bad table
            last = exc
            time.sleep(0.25)
    raise last if last else RuntimeError(f"could not save {p}")


def coin(pair: str | None, table: dict | None = None) -> dict | None:
    t = table if table is not None else load()
    rec = (t.get("coins") or {}).get(_clean(pair or ""))
    return rec if isinstance(rec, dict) else None


def _fold(table: dict, measures: dict, costs: dict, dkey: str,
          stamp: datetime, source: str | None, tape_max_ts: str | None,
          participation: float) -> None:
    """Fold one DAY's measurements into the table IN MEMORY (no I/O).

    The day entry is REPLACED, not appended twice, so re-measuring a day (a
    tournament re-run, a second sample) cannot inflate `days`. Each record
    keeps the day's own numbers, the multi-day median and worst day, the
    measured cost snapshot, the tier flags and the trend.
    """
    table["participation"] = float(participation)
    if table.get("day") != dkey:
        table["day"] = dkey
    # DISTINCT days, not folds: a backfill re-feeds the same eight days on every
    # run, and counting folds made the store claim 14 days of history on a
    # tape that has 8. The counter follows the set it is named after.
    fed = [str(d) for d in (table.get("days_fed") or [])]
    if dkey not in fed:
        fed.append(dkey)
        table["days_fed"] = sorted(fed)[-MAX_DAYS_KEPT:]
    table["days_seen"] = len(table.get("days_fed") or [])
    coins = table.setdefault("coins", {})
    costs = costs or {}
    for pair, m in (measures or {}).items():
        p = _clean(pair)
        if not p:
            continue
        try:
            median_bar = float(m.get("median_bar_usd"))
        except (AttributeError, TypeError, ValueError):
            continue
        if median_bar <= 0:
            continue
        bars = int(m.get("bars") or 0)
        cap = capacity_usd(median_bar, participation)
        c = costs.get(pair) or costs.get(p) or {}
        entry = {"day": dkey, "median_bar_usd": round(median_bar, 2),
                 "capacity_usd": cap, "bars": bars, "source": source,
                 "tape_max_ts": tape_max_ts,
                 "cost_rt_pct": c.get("rt_pct"),
                 "cost_verdict": c.get("verdict"),
                 "cost_measured": c.get("measured")}
        rec = coins.setdefault(p, {"history": []})
        hist = [h for h in (rec.get("history") or [])
                if isinstance(h, dict) and h.get("day") != dkey]
        hist.append(entry)
        hist = sorted(hist, key=lambda h: str(h.get("day")))[-MAX_DAYS_KEPT:]
        caps = [float(h.get("capacity_usd") or 0.0) for h in hist]
        rec.update({
            "median_bar_usd": entry["median_bar_usd"],
            "capacity_usd": cap,
            "capacity_median_usd": round(statistics.median(caps), 2),
            "capacity_worst_usd": round(min(caps), 2),
            "bars": bars,
            "days": len(hist),
            "history": hist,
            "cost_rt_pct": entry["cost_rt_pct"],
            "cost_verdict": entry["cost_verdict"],
            "cost_measured": entry["cost_measured"],
            "tiers": {f"{int(t)}": bool(cap >= t) for t in TIERS},
            "trend_pct": (round((caps[-1] / caps[0] - 1.0) * 100.0, 2)
                          if len(caps) > 1 and caps[0] > 0 else None),
            "updated_at": stamp.isoformat(),
        })
    table["updated_at"] = stamp.isoformat()


def observe(measures: dict, costs: dict | None = None,
            day: str | None = None, path: Path | str | None = None,
            source: str | None = None,
            tape_max_ts: str | None = None,
            participation: float = PARTICIPATION) -> dict:
    """Fold ONE measurement of {pair: {"median_bar_usd", "bars"}} and save."""
    stamp = _now()
    table = load(path)
    _fold(table, measures, costs or {}, day or _day_key(stamp), stamp, source,
          tape_max_ts, participation)
    save(table, path)
    return table


def backfill(pairs: dict, costs: dict | None = None,
             days: int = BACKFILL_DAYS, path: Path | str | None = None,
             source: str | None = "tape",
             participation: float = PARTICIPATION) -> dict:
    """Rebuild each coin's history from the TAPE'S OWN PAST, day by day.

    A history of length one cannot price a history: with a single observation
    the gate would price the same window `universe_for` already asks (a 2000-bar
    median barely moves day to day), so the coin-history rule could never
    disagree with it and would be decoration. Each of the last `days` days is
    therefore measured from THAT DAY'S OWN bars - the day's median bar dollars -
    and folded in oldest first; a day with fewer than MIN_DAY_BARS bars is not a
    day. The gate then prices the MEDIAN of those days against the book's
    order, which on this tape is a different, usually smaller universe than the
    83-day window: at $10k it admits 10 coins at $600, 6 at $1,200, 5 at
    $2,000-2,500, 3 at $5,000 and 2 at the whole balance, where the long window
    admits 14/8/6/5/2.
    """
    rows = [d for d in (pairs or {}).values() if len(d) >= 3]
    if not rows:
        return load(path)
    max_ts = max(df.index.max() for df in rows)
    # ONE pass over the tape, grouped by day, folded IN MEMORY and saved once:
    # a save per pair per day is 400 rewrites of a growing file, which is both
    # slow and a lock fight with whatever else is reading it.
    by_day: dict = {}
    for p, df in (pairs or {}).items():
        dv = df["close"] * df["volume"]
        try:
            med = dv.groupby(dv.index.normalize()).median().tail(int(days))
            cnt = dv.groupby(dv.index.normalize()).count().tail(int(days))
        except Exception:
            continue
        for day, m in med.items():
            n = int(cnt.loc[day])
            if n < MIN_DAY_BARS or not (m > 0):
                continue
            by_day.setdefault(day.strftime("%Y-%m-%d"), {})[p] = {
                "median_bar_usd": float(m), "bars": n}
    stamp = _now()
    table = load(path)
    for dkey in sorted(by_day):
        _fold(table, by_day[dkey], costs or {}, dkey, stamp, source,
              str(max_ts), participation)
    if by_day:
        save(table, path)
    return table


def measure_from_tape(pairs: dict, costs: dict | None = None, **kw) -> dict:
    """Measure every pair's median bar dollars from a loaded tape, then fold.

    This is the only place a capacity is DERIVED: the median of `close x volume`
    over the same TAIL_BARS window `edge_policy.universe_for` uses, so the
    history and the live universe cannot disagree about what a bar is worth.
    """
    import pandas as pd

    measures = {}
    for p, df in (pairs or {}).items():
        try:
            dv = float(pd.Series(df["close"] * df["volume"]).tail(TAIL_BARS).median())
        except Exception:
            continue
        if dv > 0:
            measures[p] = {"median_bar_usd": dv, "bars": int(len(df))}
    return observe(measures, costs=costs, **kw)


def response_gate(pair: str | None, order_usd: float,
                  table: dict | None = None) -> dict:
    """Can THIS coin absorb THIS order? (verdict, capacity, why).

    `refuse` is a named refusal — the caller skips the coin and says which two
    numbers refused it, exactly as `spread_table.cost_gate` does for the toll.
    `thin` still trades: the capacity is real but has fewer than MIN_DAYS
    observed days behind it, so it is today's number, not a history.
    `unmeasured` refuses: a capacity on a guess is worse than a refusal.
    """
    p = _clean(pair or "")
    order = max(0.0, float(order_usd or 0.0))
    rec = coin(p, table)
    if not rec:
        return {"pair": p, "measured": False, "order_usd": round(order, 2),
                "capacity_usd": None, "days": 0, "utilisation_pct": None,
                "thin_sample": False, "trend_pct": None, "verdict": "unmeasured",
                "why": (f"no measured bar dollars for {p or 'this coin'} — a "
                        "capacity on a guess is worse than a refusal")}
    days = int(rec.get("days") or 0)
    today = rec.get("capacity_usd")
    hist = rec.get("capacity_median_usd")
    use_hist = bool(days >= MIN_DAYS and isinstance(hist, (int, float)))
    cap = float(hist if use_hist else (today or 0.0))
    base = {"pair": p, "measured": True, "order_usd": round(order, 2),
            "capacity_usd": round(cap, 2), "days": days,
            "capacity_basis": "history median" if use_hist else "today",
            "capacity_worst_usd": rec.get("capacity_worst_usd"),
            "utilisation_pct": (round(order / cap * 100.0, 1) if cap > 0 else None),
            "thin_sample": days < MIN_DAYS,
            "trend_pct": rec.get("trend_pct"),
            "cost_verdict": rec.get("cost_verdict")}
    if cap <= 0:
        base.update(verdict="refuse",
                    why=f"{p} has measured bar dollars but no capacity")
    elif order > cap + 1e-9:
        base.update(verdict="refuse", why=(
            f"order ${order:,.0f} is {order / cap:.1f}x the ${cap:,.0f} this coin "
            f"absorbs ({PARTICIPATION:.0%} of a ${cap / PARTICIPATION:,.0f} "
            f"median bar, {days}d history)"))
    else:
        base.update(verdict="thin" if days < MIN_DAYS else "ok", why=(
            f"order ${order:,.0f} is {order / cap * 100.0:.0f}% of the "
            f"${cap:,.0f} this coin absorbs ({days}d history)"))
    return base


def fillable(pairs, order_usd: float, table: dict | None = None) -> tuple:
    """(fillable pairs by capacity desc, {pair: refusal}) for one order size."""
    t = table if table is not None else load()
    keep, refused = [], {}
    for p in pairs or []:
        g = response_gate(p, order_usd, t)
        if g["verdict"] == "refuse" or g["verdict"] == "unmeasured":
            refused[p] = g
        else:
            keep.append(g)
    keep.sort(key=lambda g: -float(g["capacity_usd"] or 0.0))
    return keep, refused


def summary(table: dict | None = None, balance: float = 10_000.0) -> dict:
    """The wall in one dict: how many coins absorb each size, and their names."""
    t = table if table is not None else load()
    coins = {p: r for p, r in (t.get("coins") or {}).items()
             if isinstance(r, dict)}
    caps = {p: float(r.get("capacity_median_usd")
                     if (int(r.get("days") or 0) >= MIN_DAYS
                         and isinstance(r.get("capacity_median_usd"),
                                        (int, float)))
                     else (r.get("capacity_usd") or 0.0))
            for p, r in coins.items()}
    tiers = {}
    for t_ in TIERS + (float(balance),):
        names = sorted([p for p, c in caps.items() if c >= t_],
                       key=lambda p: -caps[p])
        tiers[f"{int(t_)}"] = {"pairs": len(names), "names": names}
    live = [c for c in caps.values() if c > 0]
    depths = [int(r.get("days") or 0) for r in coins.values()]
    return {"coins": len(coins),
            # the depth the gate actually prices (per-coin history entries),
            # beside the distinct days the store has ever been fed
            "days": max(depths) if depths else 0,
            "min_days": min(depths) if depths else 0,
            "days_seen": int(t.get("days_seen") or 0),
            "day": t.get("day"), "updated_at": t.get("updated_at"),
            "median_capacity_usd": (round(statistics.median(live), 2)
                                    if live else None),
            "tiers": tiers}


def report(table: dict | None = None) -> str:
    t = table if table is not None else load()
    s = summary(t)
    if not s["coins"]:
        return ("coin history empty — no coin has a measured capacity. Run: "
                "python coin_history.py --measure")
    lines = [f"coin history: {s['coins']} coins, {s['days']} day(s) of "
             f"history (min {s['min_days']}, {s['days_seen']} fed), "
             f"day {s['day']}, updated {s['updated_at']}",
             f"  median capacity ${s['median_capacity_usd'] or 0:,.2f} "
             f"(1% of the median bar)"]
    for tier, v in s["tiers"].items():
        lines.append(f"  absorbs ${int(tier):>6,}: {v['pairs']:>2} of "
                     f"{s['coins']:<2}  {' '.join(v['names'][:12])}"
                     f"{' …' if len(v['names']) > 12 else ''}")
    for p, r in sorted((t.get("coins") or {}).items(),
                       key=lambda kv: -(kv[1].get("capacity_usd") or 0)):
        lines.append(
            f"  {p:<8} cap ${float(r.get('capacity_usd') or 0):>10,.2f} "
            f"hist ${float(r.get('capacity_median_usd') or 0):>10,.2f} "
            f"({r.get('days') or 0}d, {r.get('bars') or 0} bars) "
            f"trend {r.get('trend_pct')}% cost {r.get('cost_verdict')}")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="per-coin capacity history")
    ap.add_argument("--measure", action="store_true",
                    help="fold the cache's median bar dollars into the history")
    ap.add_argument("--report", action="store_true", help="print the table")
    ap.add_argument("--order", type=float, default=None,
                    help="print which coins can absorb this order")
    ap.add_argument("--all", action="store_true",
                    help="measure the whole tape, not the contest's assets")
    a = ap.parse_args(argv)
    if a.measure:
        import edge_engine as ee
        import spread_table as st
        pairs = ee.load_pairs()
        tape = ee.tape_stamp()
        if not a.all:
            # THE CONTEST'S 54 ASSETS, NOT THE TAPE'S 197: only these can be
            # bought, so only these have a capacity that matters.
            from config import TradingConfig
            want = {st._clean(p) for p in TradingConfig().trading_pairs}
            pairs = {p: d for p, d in pairs.items()
                     if st._clean(p) in want}
        costs = {p: st.cost_gate(p) for p in pairs}
        # BACKFILL, NOT ONE SAMPLE: the gate prices the median of the observed
        # days, so a single reading would leave the history with nothing to
        # say. Each day boundary is measured from the bars that existed then.
        t = backfill(pairs, costs=costs, source="tape")
        print(f"measured {len(t.get('coins') or {})} coins on the "
              f"{tape.get('max_ts')} tape -> {FILE.name}")
    if a.order is not None:
        t = load()
        keep, refused = fillable(sorted((t.get("coins") or {})), a.order, t)
        print(f"${a.order:,.0f}: {len(keep)} coin(s) can absorb it")
        for g in keep:
            print(f"  {g['pair']:<8} cap ${g['capacity_usd']:>10,.2f} "
                  f"({g['capacity_basis']}, {g['days']}d) {g['verdict']}")
        for p, g in sorted(refused.items()):
            print(f"  {p:<8} REFUSED  {g['why']}")
    if not (a.measure or a.order is not None):
        print(report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
