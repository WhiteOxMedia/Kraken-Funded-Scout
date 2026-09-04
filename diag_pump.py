"""Reconstruct the missed PUMP exit ~10:30 AM: what did the system know?"""
import sys, sqlite3, json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
lines = []

# 1. bridge signals mentioning PUMP today
conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row
try:
    rows = conn.execute(
        "SELECT timestamp, pair, action, status, source, confidence, entry_price, take_profit "
        "FROM signals WHERE pair LIKE '%PUMP%' AND timestamp >= '2026-08-24' ORDER BY timestamp").fetchall()
    lines.append(f"bridge signals for PUMP today: {len(rows)}")
    for r in rows:
        lines.append(f"  {r['timestamp']} {r['action']} {r['status']} src={r['source']} conf={r['confidence']} entry={r['entry_price']} tp={r['take_profit']}")
except Exception as e:
    lines.append(f"signals query failed: {e}")

# 2. tournament winners mentioning PUMP today
try:
    rows = conn.execute(
        "SELECT timestamp, pair, action, confidence FROM tournament_winners "
        "WHERE pair LIKE '%PUMP%' AND timestamp >= '2026-08-24' ORDER BY timestamp").fetchall()
    lines.append(f"")
    lines.append(f"tournament winners for PUMP today: {len(rows)}")
    for r in rows:
        lines.append(f"  {r['timestamp']} {r['action']} {r['confidence']:.0f}%")
except Exception as e:
    lines.append(f"winners query failed: {e}")
conn.close()

# 3. PUMP hourly candles around 10:00-12:00 from the engine cache
try:
    c2 = sqlite3.connect(BASE / "ohlcv_cache.db")
    c2.row_factory = sqlite3.Row
    tabs = [r[0] for r in c2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    lines.append(f"")
    lines.append(f"ohlcv tables: {tabs}")
    for t in tabs:
        cols = [r[1] for r in c2.execute(f"PRAGMA table_info({t})").fetchall()]
        lines.append(f"  {t} cols: {cols}")
    # try common layouts
    for t in tabs:
        cols = [r[1] for r in c2.execute(f"PRAGMA table_info({t})").fetchall()]
        if "pair" in cols and "ts" in cols:
            rows = c2.execute(
                f"SELECT ts, open, high, low, close FROM {t} WHERE pair LIKE '%PUMP%' "
                f"AND ts >= 1782044400 ORDER BY ts LIMIT 8").fetchall()
            lines.append(f"  PUMP candles in {t} (ts>=~08:00 Denver today): {len(rows)}")
            for r in rows:
                dt = datetime.fromtimestamp(r["ts"], MST)
                lines.append(f"    {dt.strftime('%H:%M')} O={r['open']:.5f} H={r['high']:.5f} L={r['low']:.5f} C={r['close']:.5f}")
    c2.close()
except Exception as e:
    lines.append(f"ohlcv query failed: {e}")

# 4. console state / any watch-adjacent files
lines.append("")
for f in ["console_state.json", "scanner_state.json", "payout_track.json"]:
    p = BASE / f
    if p.exists():
        lines.append(f"{f}: {p.read_text(encoding='utf-8')[:300]}")

out = BASE / "pump_trace.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
