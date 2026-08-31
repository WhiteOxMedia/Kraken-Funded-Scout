"""Historical tide reconstruction (user rule, Aug 31: "can't wait till
Sept 7 — use last week's data and adjust the bottom threshold").

The tide stamp starts at the next restart, but the ingredients already
exist: signals.db has every executed buy + outcome, and ohlcv_cache.db
has the 1h candles. This diag rebuilds the fleet tide (breadth above
EMA21, same math as _fleet_tide) AT EACH TRADE'S HOUR and buckets the
outcomes: RISING vs FLAT vs OUT — win rate + P&L per bucket.

Run: .venv\\Scripts\\python.exe diag_tidehistory.py  → _tidehistory_check.txt
"""
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

import background_scanner as bs  # TIDE_RISING_MIN / TIDE_OUT_MAX stay in sync
from indicators import ema

MST = ZoneInfo("America/Denver")
EMA_PERIOD = 21
MIN_CANDLES = 25   # _fleet_tide's per-pair minimum
MIN_FLEET = 10     # _fleet_tide's fleet minimum

lines = []


def out(s=""):
    print(s)
    lines.append(s)


# ── load the candle cache: per-pair ascending (ts_ms, close) ──────────
cc = sqlite3.connect("ohlcv_cache.db")
series = {}
for pair, ts, close in cc.execute("SELECT pair, ts, close FROM ohlcv ORDER BY pair, ts"):
    series.setdefault(pair, []).append((int(ts), float(close)))
cc.close()
out(f"candle cache: {len(series)} pairs, {sum(len(v) for v in series.values())} candles")


def tide_at(when_ms: int) -> tuple[float | None, str]:
    """Fleet breadth at a past hour — the same math as _fleet_tide, sliced
    at the trade's time (only candles the trade could have seen)."""
    import bisect
    above = total = 0
    for pair, candles in series.items():
        ts_list = [c[0] for c in candles]
        i = bisect.bisect_right(ts_list, when_ms)
        if i < MIN_CANDLES:
            continue
        closes = pd.Series([c[1] for c in candles[:i]])
        trend = float(ema(closes, EMA_PERIOD).iloc[-1])
        total += 1
        if closes.iloc[-1] > trend:
            above += 1
    if total < MIN_FLEET:
        return None, "NODATA"
    pct = 100.0 * above / total
    return pct, bs._tide_class(pct)


# ── load executed buys ────────────────────────────────────────────────
sc = sqlite3.connect("signals.db")
sc.row_factory = sqlite3.Row
trades = sc.execute("""
    SELECT pair, confidence, source, executed_at, pnl, status
    FROM signals
    WHERE action='buy' AND executed_at IS NOT NULL
    ORDER BY executed_at
""").fetchall()
sc.close()

now = datetime.now(MST)
rows = []
for r in trades:
    try:
        t = datetime.fromisoformat(str(r["executed_at"]))
        if t.tzinfo is None:
            t = t.replace(tzinfo=MST)
    except Exception:
        continue
    pct, state = tide_at(int(t.timestamp() * 1000))
    rows.append({
        "when": t, "pair": r["pair"], "conf": float(r["confidence"] or 0),
        "source": r["source"] or "", "pnl": (float(r["pnl"]) if r["pnl"] is not None else None),
        "status": r["status"], "tide": state, "breadth": pct,
    })

out(f"\nexecuted buys reconstructed: {len(rows)}")


def bucket_report(title: str, subset: list[dict]):
    out(f"\n{'=' * 66}\n{title}\n{'=' * 66}")
    out(f"{'tide':8} {'trades':>6} {'W':>3} {'L':>3} {'0':>3} {'win%':>6} {'total$':>8} {'avg$':>7}")
    for state in ("RISING", "FLAT", "OUT", "NODATA"):
        grp = [x for x in subset if x["tide"] == state]
        if not grp:
            continue
        closed = [x for x in grp if x["status"] == "closed" and x["pnl"] is not None]
        wins = [x for x in closed if x["pnl"] > 0]
        losses = [x for x in closed if x["pnl"] < 0]
        zeros = [x for x in closed if x["pnl"] == 0]
        decided = len(wins) + len(losses)
        winpct = 100.0 * len(wins) / decided if decided else 0.0
        total = sum(x["pnl"] for x in closed)
        avg = total / len(closed) if closed else 0.0
        out(f"{state:8} {len(grp):>6} {len(wins):>3} {len(losses):>3} {len(zeros):>3} "
            f"{winpct:>5.0f}% {total:>8.2f} {avg:>7.2f}")


bucket_report("ALL EXECUTED BUYS (every era)", rows)
week = [x for x in rows if x["when"] >= now - timedelta(days=7)]
bucket_report("LAST 7 DAYS", week)

out(f"\n{'=' * 66}\nPER-TRADE (oldest first) — 0 = pnl $0 (rotation reset, not an outcome)\n{'=' * 66}")
out(f"{'executed (MST)':17} {'pair':12} {'tide':8} {'fleet%':>6} {'conf':>5} {'pnl':>8}  src")
for x in rows:
    pnl = f"{x['pnl']:+.2f}" if x["pnl"] is not None else "   —   "
    br = f"{x['breadth']:.0f}" if x["breadth"] is not None else "—"
    out(f"{x['when'].strftime('%m-%d %H:%M'):17} {x['pair'][:12]:12} {x['tide']:8} {br:>6} "
        f"{x['conf']:>5.0f} {pnl:>8}  {x['source']}")

Path("_tidehistory_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("DONE")
