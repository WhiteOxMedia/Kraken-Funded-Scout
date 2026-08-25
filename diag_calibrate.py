"""Calibrate the anecdote-born gates against the FULL signal history.
Every knob measured against real prices; data picks the number or kills it.
Cohorts: chase-off-1h-base, trend state at signal, entry-age delay."""
import sys, sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)

import ccxt
ex = ccxt.kraken()

# ── signals: full history, deduped, conf>=60, entry known ──────────
conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT pair, confidence, entry_price, timestamp FROM signals "
    "WHERE action='buy' AND source != 'bot_strategy' AND confidence >= 60 "
    "AND entry_price > 0 AND timestamp >= '2026-08-18' ORDER BY timestamp").fetchall()
conn.close()
latest = {}
for r in rows:  # dedupe: keep latest per pair per hour
    key = (r["pair"], r["timestamp"][:13])
    latest[key] = dict(r)
sigs = list(latest.values())
lines = [f"calibration run {now.strftime('%a %I:%M %p')} — {len(sigs)} signals "
         f"(deduped per pair-hour, conf>=60)", ""]

# ── candles: 15m (7.5d, API) + 1h (cache) per pair ─────────────────
pairs = sorted({s["pair"] for s in sigs})
c15, c1h = {}, {}
cc = sqlite3.connect(BASE / "ohlcv_cache.db")
cc.row_factory = sqlite3.Row
for p in pairs:
    try:
        ohl = ex.fetch_ohlcv(p, "15m", limit=720)
        c15[p] = [(o[0], o[1], o[2], o[3], o[4]) for o in ohl]  # ts,o,h,l,c
    except Exception:
        c15[p] = []
    r2 = cc.execute("SELECT ts, open, high, low, close FROM ohlcv WHERE pair=? ORDER BY ts",
                    (p,)).fetchall()
    c1h[p] = [(int(r["ts"]), r["open"], r["high"], r["low"], r["close"]) for r in r2]
cc.close()

from indicators import ema

def trend_at(pair, ts_ms):
    """UP / BASING / FALLING on 1h candles up to ts (same logic as the gate)."""
    cl = [c for c in c1h.get(pair, []) if c[0] <= ts_ms]
    if len(cl) < 25:
        return None
    closes = [c[4] for c in cl]
    import pandas as pd
    s = pd.Series(closes)
    es, el = float(ema(s, 9).iloc[-1]), float(ema(s, 21).iloc[-1])
    px = closes[-1]
    if es < el and px < el:
        return "FALLING"
    if es > el:
        return "UP"
    return "BASING"

def hit20_from(pair, ts_ms, entry):
    """$20 (+0.4%) hit within 4h from a delayed entry? (15m highs)"""
    if entry <= 0:
        return None
    tgt = entry * 1.004
    for c in c15.get(pair, []):
        if c[0] < ts_ms:
            continue
        if c[0] > ts_ms + 4 * 3600_000:
            break
        if c[2] >= tgt:  # high
            return True
    return False

def close_at(pair, ts_ms):
    """First 15m close at/after ts (the 'entry at this delay' price)."""
    for c in c15.get(pair, []):
        if c[0] >= ts_ms:
            return c[4]
    return None

def base_1h(pair, ts_ms):
    """min low of the 4x15m candles ending at the signal (the chase base)."""
    prior = [c for c in c15.get(pair, []) if c[0] <= ts_ms][-4:]
    if len(prior) < 4:
        return None
    return min(c[3] for c in prior)

chase_cohorts = {}
trend_cohorts = {}
age_cohorts = {0: [0, 0], 15: [0, 0], 30: [0, 0], 45: [0, 0]}
for s in sigs:
    try:
        ts = datetime.fromisoformat(s["timestamp"])
    except Exception:
        continue
    ts_ms = int(ts.timestamp() * 1000)
    p = s["pair"]
    if not c15.get(p):
        continue
    entry = float(s["entry_price"])
    # chase cohort
    b = base_1h(p, ts_ms)
    if b and b > 0:
        ch = (entry - b) / b * 100
        for lo, hi in [(0, 1), (1, 2), (2, 3), (3, 5), (5, 100)]:
            if lo <= ch < hi:
                key = f"{lo}-{hi}%"
                d = chase_cohorts.setdefault(key, [0, 0])
                h = hit20_from(p, ts_ms, entry)
                if h is not None:
                    d[0] += int(h); d[1] += 1
    # trend cohort
    t = trend_at(p, ts_ms)
    if t:
        d = trend_cohorts.setdefault(t, [0, 0])
        h = hit20_from(p, ts_ms, entry)
        if h is not None:
            d[0] += int(h); d[1] += 1
    # entry-age cohorts: entering LATER at the price that existed then
    for age, (dh, dn) in age_cohorts.items():
        late_px = close_at(p, ts_ms + age * 60_000)
        if late_px:
            h = hit20_from(p, ts_ms + age * 60_000, late_px)
            if h is not None:
                age_cohorts[age] = [dh + int(h), dn + 1]

def table(title, cohorts):
    lines.append(title)
    for k, (h, n) in cohorts.items():
        if n:
            lines.append(f"  {k:<10} n={n:<4} $20-hit {h / n:.0%}")
    lines.append("")

table("CHASE off 1h base at signal time (guard currently blocks >2%):", chase_cohorts)
table("TREND at signal time (guard currently blocks FALLING):", trend_cohorts)
lines.append("ENTRY DELAY: $20 hit if you entered N minutes AFTER the signal:")
for age, (h, n) in age_cohorts.items():
    if n:
        lines.append(f"  +{age:>2} min   n={n:<4} $20-hit {h / n:.0%}")
lines.append("")

out = BASE / "calibration_report.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
