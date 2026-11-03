"""Why were the 92% BTC/ETH signals rejected? And did the desktop fire?"""
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
# clock-audit: ok — LIVE-PROBE (manual bot-day report — reports on the live system by design) (clockbombs census 4)
conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT timestamp, pair, action, status, source, confidence, reason, executed_at "
    "FROM signals WHERE timestamp >= '2026-08-24T08:25' ORDER BY timestamp DESC LIMIT 10").fetchall()
lines = []
for r in rows:
    lines.append(f"{r['timestamp'][:19]} {r['pair']:<12} {r['action']:<4} {r['status']:<9} "
                 f"{r['source']:<12} {r['confidence']}% reason={str(r['reason'])[:100]}")
# open positions now
rows = conn.execute(
    "SELECT pair, executed_at, execution_price, status FROM signals WHERE status='filled' AND action='buy'").fetchall()
lines.append("")
lines.append(f"open positions: {len(rows)}")
for r in rows:
    lines.append(f"  {r['pair']} since {r['executed_at'][:16]} @ {r['execution_price']}")
conn.close()
out = BASE / "why_rejected.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
