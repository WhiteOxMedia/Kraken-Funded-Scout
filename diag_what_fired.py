"""What fired for PENGU just now? And did anything else speak?"""
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)

conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT timestamp, pair, action, status, source, confidence, entry_price, execution_reason "
    "FROM signals WHERE timestamp >= '2026-08-26T22:00' ORDER BY timestamp DESC LIMIT 15").fetchall()
print(f"now: {now.strftime('%a %I:%M %p')} — records since 10:00 PM:")
for r in rows:
    print(f"  {r['timestamp'][:19]} {r['pair']:<12} {r['action']} {r['status']:<9} "
          f"src={r['source']:<12} conf={r['confidence']:.0f} "
          f"exec={(r['execution_reason'] or '')[:60]}")
conn.close()
