"""What's the 1 pending bridge signal? Age check."""
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
    "SELECT signal_id, pair, action, status, source, confidence, timestamp, valid_until "
    "FROM signals WHERE status='pending'").fetchall()
for r in rows:
    age = ""
    try:
        age = f"age {(now - datetime.fromisoformat(r['timestamp'])).total_seconds()/60:.0f}m"
    except Exception:
        pass
    print(f"{r['pair']} {r['action']} conf={r['confidence']:.0f} src={r['source']} {age} "
          f"valid_until={str(r['valid_until'])[:19]}")
conn.close()
