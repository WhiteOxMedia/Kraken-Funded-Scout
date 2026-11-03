"""Why is the bot holding only one asset? Bridge state + today's full signal trail."""
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import scratch_space   # scratch policy: nothing volatile lands in the repo root

BASE = Path(__file__).resolve().parent
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)
lines = [f"now: {now.strftime('%a %I:%M %p')} Denver"]

# clock-audit: ok — LIVE-PROBE (manual bot-day report — reports on the live system by design) (clockbombs census 4)
conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row

# open positions now
rows = conn.execute(
    "SELECT pair, executed_at, execution_price, highest_price FROM signals "
    "WHERE status='filled' AND action='buy'").fetchall()
lines.append(f"\nOPEN positions: {len(rows)}")
for r in rows:
    lines.append(f"  {r['pair']} since {r['executed_at'][:16]} @ {r['execution_price']}")

# full trail today: every signal with status
rows = conn.execute(
    "SELECT timestamp, pair, action, status, source, confidence FROM signals "
    "WHERE timestamp >= '2026-08-23' ORDER BY timestamp ASC").fetchall()
lines.append(f"\nfull signal trail today ({len(rows)} signals):")
for r in rows:
    lines.append(f"  {r['timestamp'][11:16]}  {r['pair']:<10} {r['action']:<4} {r['status']:<9} "
                 f"{r['source']:<14} {r['confidence']}%")

# rejected reasons today — check if there's a reason column
cols = [c[1] for c in conn.execute("PRAGMA table_info(signals)").fetchall()]
if "rejection_reason" in cols or "reason" in cols:
    rcol = "rejection_reason" if "rejection_reason" in cols else "reason"
    rows = conn.execute(
        f"SELECT timestamp, pair, {rcol} FROM signals WHERE timestamp >= '2026-08-23' "
        f"AND status='rejected'").fetchall()
    lines.append("\nrejection reasons today:")
    for r in rows:
        lines.append(f"  {r[0][11:16]} {r[1]}: {r[2]}")

conn.close()
out = BASE / "bot_day_check.txt"
scratch_space.report_temp('bot_day_check.txt').write_text("\n".join(lines), encoding="utf-8")
print("done")
