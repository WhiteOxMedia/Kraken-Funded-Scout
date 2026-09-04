"""Signal comparison: Aug 19-20 (last Wed/Thu) vs Aug 24-25 (current system).
Raw signals + $20 replay outcomes. Excludes the old duplicate bot_strategy
records (the Aug-24-morning bug inflated counts ~2x)."""
import sys, sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")

conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row
lines = [f"run: {datetime.now(MST).strftime('%a %I:%M %p')} Denver", ""]

DAYS = ["2026-08-19", "2026-08-20", "2026-08-24", "2026-08-25"]

# candles for the $20 replay
cc = sqlite3.connect(BASE / "ohlcv_cache.db")
cc.row_factory = sqlite3.Row
candles = {}
for r in cc.execute("SELECT pair, ts, high FROM ohlcv"):
    candles.setdefault(r["pair"], []).append((int(r["ts"]), float(r["high"])))
for p in candles:
    candles[p].sort()
cc.close()

def replay_hit(pair, ts_iso, entry):
    if entry <= 0 or pair not in candles:
        return None
    try:
        t = datetime.fromisoformat(ts_iso)
    except Exception:
        return None
    t_ms = int(t.timestamp() * 1000)
    target = entry * 1.004
    for ts, high in candles[pair]:
        if ts < t_ms:
            continue
        if ts <= t_ms + 4 * 3600 * 1000:
            if high >= target:
                return True
        else:
            break
    return False

stats = {}
for day in DAYS:
    rows = conn.execute(
        "SELECT pair, action, confidence, entry_price, timestamp, status, source "
        "FROM signals WHERE action='buy' AND timestamp LIKE ? "
        "AND source != 'bot_strategy' ORDER BY timestamp", (day + "%",)).fetchall()
    n = len(rows)
    pairs = {r["pair"] for r in rows}
    confs = [float(r["confidence"] or 0) for r in rows]
    hi = sum(1 for c in confs if c >= 89)
    mid = sum(1 for c in confs if c >= 60)
    srcs = {}
    for r in rows:
        srcs[r["source"]] = srcs.get(r["source"], 0) + 1
    hits = tried = 0
    for r in rows:
        h = replay_hit(r["pair"], r["timestamp"], float(r["entry_price"] or 0))
        if h is not None:
            tried += 1
            hits += int(h)
    # clustering: busiest hour
    hours = {}
    for r in rows:
        hours[r["timestamp"][11:13]] = hours.get(r["timestamp"][11:13], 0) + 1
    peak = max(hours.values()) if hours else 0
    stats[day] = dict(n=n, pairs=len(pairs), hi=hi, mid=mid, srcs=srcs,
                      hits=hits, tried=tried, peak=peak,
                      med=sorted(confs)[len(confs)//2] if confs else 0)
    d = datetime.fromisoformat(day)
    lines.append(f"=== {d.strftime('%a %m/%d')} ===")
    lines.append(f"  buy signals: {n}  distinct coins: {len(pairs)}  peak hour: {peak} signals")
    lines.append(f"  confidence: median {stats[day]['med']:.0f}%  >=89%: {hi}  >=60%: {mid}")
    lines.append(f"  $20 within 4h: {hits}/{tried} ({hits/tried:.0%})" if tried else "  $20 replay: no data")
    lines.append(f"  sources: {srcs}")

# assets most-signaled then vs now
lines.append("")
for label, days in [("TOP COINS Aug 19-20", ["2026-08-19", "2026-08-20"]),
                    ("TOP COINS Aug 24-25", ["2026-08-24", "2026-08-25"])]:
    cnt = {}
    for day in days:
        for r in conn.execute("SELECT pair FROM signals WHERE action='buy' AND timestamp LIKE ? "
                              "AND source != 'bot_strategy'", (day + "%",)):
            cnt[r["pair"]] = cnt.get(r["pair"], 0) + 1
    top = sorted(cnt.items(), key=lambda x: -x[1])[:8]
    lines.append(f"{label}: " + ", ".join(f"{p.split('/')[0]}x{c}" for p, c in top))

conn.close()
out = BASE / "signal_compare.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
