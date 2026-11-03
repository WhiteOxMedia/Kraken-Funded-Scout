"""Which signal is pending and why?"""
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
# clock-audit: ok — LIVE-PROBE (manual bot-day report — reports on the live system by design) (clockbombs census 4)
conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT signal_id, timestamp, pair, action, status, source, confidence "
    "FROM signals WHERE status='pending' ORDER BY timestamp DESC").fetchall()
lines = [f"pending signals: {len(rows)}"]
for r in rows:
    lines.append(f"  {r['signal_id']} {r['timestamp'][:19]} {r['pair']} {r['action']} "
                 f"{r['source']} {r['confidence']}%")
conn.close()
out = BASE / "pending_check.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
