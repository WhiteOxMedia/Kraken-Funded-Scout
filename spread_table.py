"""THE SPREAD TABLE — what a round trip actually costs, per pair, averaged over days.

`contest_account.COST_ROUNDTRIP_PCT` (0.08%) is the platform's published figure, and
the live measurement disagreed with it by up to 27x: 0.0002% on BTC, 0.039% on WLD,
2.18% at the p99 of a 57-pair snapshot. A single flat constant is therefore wrong in
both directions — it overcharges the majors and undercharges the thin pairs, which is
exactly the direction that kills an account.

Two rules this module exists to enforce:

1. A THRESHOLD READS THE PAIR'S OWN COST. Every caller that prices a round trip
   (pnl_net_usd, hard_death_entry_usd, the profit-lock floors) asks for the pair.
2. ONE SNAPSHOT IS NOT A MEASUREMENT. A quote read once is a moment, not a spread:
   the book widens at the session open, at funding, and in a flush. The sampler
   accumulates every observation into a DAILY mean and folds finished days into a
   multi-day average, so a threshold never rests on whichever snapshot happened to be
   taken first.

Storage (spread_table.json, beside this module):

    {"updated_at": iso, "day": "YYYY-MM-DD", "days_seen": int,
     "pairs": {"BTC": {"avg_pct": 0.0021, "days": 4,
                       "day_pct": 0.0019, "day_n": 60,
                       "n": 480, "min_pct": .., "max_pct": .., "updated_at": iso}}}

`avg_pct` is the mean of finished daily means (equal weight per day, so one busy day
cannot swamp the table), `day_pct` is today's running mean. Unknown pairs fall back
to 0.08% — the platform's own number, never a guess at zero.

    python spread_table.py --sample          # one snapshot, folded into today
    python spread_table.py --loop --every 15 # keep sampling across the day
    python spread_table.py --report          # what the thresholds currently read
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

try:                                   # the repo's clock, when it is importable
    from contest_account import MST
except Exception:                      # standalone use (a bare checkout, a test)
    from zoneinfo import ZoneInfo
    MST = ZoneInfo("America/Denver")

TABLE_FILE = Path(__file__).parent / "spread_table.json"
DEFAULT_RT_PCT = 0.08                  # the platform's published round trip
MAX_PLAUSIBLE_PCT = 5.0                # beyond this the quote is broken, not the pair
MIN_PLAUSIBLE_PCT = 0.0


def _now():
    return datetime.now(MST)


def _day_key(now=None) -> str:
    return (now or _now()).strftime("%Y-%m-%d")


def _clean(pair: str) -> str:
    """'BTC/USD' -> 'BTC'. The tape, the journals and ccxt all disagree on this."""
    return str(pair or "").split("/")[0].split("-")[0].strip().upper()


def load_table(path: Path | None = None) -> dict:
    p = Path(path) if path else TABLE_FILE
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("pairs"), dict):
            return d
    except (OSError, ValueError):
        pass
    return {"updated_at": None, "day": None, "days_seen": 0, "pairs": {}}


def save_table(table: dict, path: Path | None = None) -> None:
    p = Path(path) if path else TABLE_FILE
    tmp = str(p) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(table, fh, indent=1)
    os.replace(tmp, p)


def roundtrip_pct(pair: str | None = None, table: dict | None = None) -> float:
    """The pair's measured round trip, or the platform's 0.08% when it is unknown.

    A pair with no finished day yet uses today's running mean (better than nothing,
    and honest while it is only one day); a pair with neither uses the constant.
    """
    if not pair:
        return DEFAULT_RT_PCT
    t = table if table is not None else load_table()
    row = (t.get("pairs") or {}).get(_clean(pair))
    if not isinstance(row, dict):
        return DEFAULT_RT_PCT
    for key in ("avg_pct", "day_pct"):
        v = row.get(key)
        if isinstance(v, (int, float)) and MIN_PLAUSIBLE_PCT < float(v) < MAX_PLAUSIBLE_PCT:
            return float(v)
    return DEFAULT_RT_PCT


def half_spread_pct(pair: str | None = None, table: dict | None = None) -> float:
    """One side of the crossing — the cost of paying the spread once."""
    return roundtrip_pct(pair, table) / 2.0


def _fold_day(table: dict, row: dict) -> None:
    """Fold today's running mean into the multi-day average (equal weight/day)."""
    day_pct, day_n = row.pop("__fold_pct", None), row.pop("__fold_n", None)
    if day_pct is None or not day_n:
        return
    days = int(row.get("days") or 0)
    prev = row.get("avg_pct")
    if not isinstance(prev, (int, float)) or days <= 0:
        row["avg_pct"] = round(float(day_pct), 6)
        row["days"] = 1
        return
    row["avg_pct"] = round((float(prev) * days + float(day_pct)) / (days + 1), 6)
    row["days"] = days + 1


def observe(quotes: dict, path: Path | None = None, now=None) -> dict:
    """Fold one snapshot of {pair: (bid, ask)} into the table.

    A quote is only accepted when both sides exist, are positive, are properly
    ordered, and imply a spread inside the plausible band: a crossed or stale book
    would otherwise poison a day's mean with a number no fill ever saw.
    """
    stamp = now or _now()
    day = _day_key(stamp)
    table = load_table(path)
    if table.get("day") != day:
        for row in (table.get("pairs") or {}).values():
            if isinstance(row, dict) and row.get("day_pct") is not None:
                row["__fold_pct"] = row["day_pct"]
                row["__fold_n"] = row.get("day_n") or 0
                _fold_day(table, row)
                row["day_pct"], row["day_n"] = None, 0
        table["day"] = day
        table["days_seen"] = int(table.get("days_seen") or 0) + 1

    pairs = table.setdefault("pairs", {})
    taken = 0
    for pair, q in (quotes or {}).items():
        try:
            bid, ask = float(q[0]), float(q[1])
        except (TypeError, ValueError, IndexError):
            continue
        if not (bid > 0 and ask > 0 and ask >= bid):
            continue
        mid = (bid + ask) / 2.0
        pct = 100.0 * (ask - bid) / mid
        if not (MIN_PLAUSIBLE_PCT < pct < MAX_PLAUSIBLE_PCT):
            continue
        row = pairs.setdefault(_clean(pair), {
            "avg_pct": None, "days": 0, "day_pct": None, "day_n": 0,
            "n": 0, "min_pct": None, "max_pct": None, "updated_at": None})
        n = int(row.get("day_n") or 0)
        prev = row.get("day_pct")
        row["day_pct"] = round(pct if prev is None else (prev * n + pct) / (n + 1), 6)
        row["day_n"] = n + 1
        row["n"] = int(row.get("n") or 0) + 1
        lo, hi = row.get("min_pct"), row.get("max_pct")
        row["min_pct"] = round(pct if lo is None else min(lo, pct), 6)
        row["max_pct"] = round(pct if hi is None else max(hi, pct), 6)
        row["updated_at"] = stamp.isoformat()
        taken += 1
    table["updated_at"] = stamp.isoformat()
    save_table(table, path)
    return {"day": day, "observed": taken, "pairs": len(pairs)}


def fetch_quotes(pairs, exchange=None) -> dict:
    """{pair: (bid, ask)} from Kraken's PUBLIC ticker — never the caller's client."""
    own = exchange is None
    ex = exchange
    if own:
        import ccxt
        ex = ccxt.kraken({"enableRateLimit": True})
    out = {}
    for p in pairs:
        try:
            t = ex.fetch_ticker(p)
            out[p] = (t.get("bid"), t.get("ask"))
        except Exception:
            continue
    return out


def tracked_pairs() -> list[str]:
    """The pairs the table already knows, as ccxt symbols."""
    return sorted(f"{k}/USD" for k in (load_table().get("pairs") or {}))


def sample_once(pairs=None, path: Path | None = None, now=None) -> dict:
    """One snapshot of the given pairs (default: every pair already tracked)."""
    ps = list(pairs or tracked_pairs())
    if not ps:
        return {"observed": 0, "pairs": 0, "why": "no pairs to sample"}
    return observe(fetch_quotes(ps), path=path, now=now)


def report(path: Path | None = None) -> str:
    t = load_table(path)
    rows = t.get("pairs") or {}
    if not rows:
        return (f"spread table empty — every pair prices at the {DEFAULT_RT_PCT}% "
                "fallback. Run: python spread_table.py --sample")
    lines = [f"spread table: {len(rows)} pairs, {t.get('days_seen') or 0} day(s) seen, "
             f"day {t.get('day')}, updated {t.get('updated_at')}"]
    for pair, r in sorted(rows.items(), key=lambda kv: -(kv[1].get("avg_pct") or 0)):
        lines.append(
            f"  {pair:<10} rt {float(r.get('avg_pct') or 0):7.4f}%  "
            f"({r.get('days') or 0}d)  today {float(r.get('day_pct') or 0):7.4f}% "
            f"n={r.get('day_n') or 0:<4} all n={r.get('n') or 0:<5} "
            f"range {float(r.get('min_pct') or 0):.4f}..{float(r.get('max_pct') or 0):.4f}")
    return "\n".join(lines)


def main(argv=None):
    import argparse
    import time as _t
    ap = argparse.ArgumentParser(description="per-pair spread sampler")
    ap.add_argument("--sample", action="store_true", help="one snapshot now")
    ap.add_argument("--loop", action="store_true", help="sample on an interval")
    ap.add_argument("--every", type=float, default=15.0, help="minutes between samples")
    ap.add_argument("--pairs", default="", help="comma list (default: tracked pairs)")
    ap.add_argument("--report", action="store_true", help="print the table")
    a = ap.parse_args(argv)
    ps = [p.strip() for p in a.pairs.split(",") if p.strip()] or None
    if a.report:
        print(report())
    if a.sample:
        print(sample_once(ps))
    if a.loop:
        while True:
            try:
                print(_now().isoformat(), sample_once(ps))
            except Exception as exc:
                print(_now().isoformat(), "sample failed:", exc)
            _t.sleep(max(a.every, 1.0) * 60.0)
    if not (a.report or a.sample or a.loop):
        print(report())


if __name__ == "__main__":
    main()
