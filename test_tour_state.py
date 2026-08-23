"""Recent tournament activity + why signals are/aren't firing."""
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
MST = ZoneInfo("America/Denver")
lines = []

# Bridge: today's signals + open positions
try:
    conn = sqlite3.connect(BASE / "signals.db")
    conn.row_factory = sqlite3.Row
    today = datetime.now(MST).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT timestamp, pair, action, status, source, confidence FROM signals "
        "WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT 10", (today,)).fetchall()
    lines.append("bridge signals today:")
    for r in rows:
        lines.append(f"  {r['timestamp'][11:16]}  {r['pair']:<10} {r['action']:<4} {r['status']:<9} "
                     f"{r['source']:<14} {r['confidence']}%")
    open_pos = conn.execute(
        "SELECT pair, executed_at, execution_price FROM signals WHERE status='filled' AND action='buy'").fetchall()
    lines.append(f"open positions in bridge: {len(open_pos)} (bot max = 3)")
    for p in open_pos:
        lines.append(f"  {p['pair']} since {p['executed_at'][:16]} @ {p['execution_price']}")
    conn.close()
except Exception as exc:
    lines.append(f"bridge: {exc}")

out = BASE / "tour_check.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
