"""Smoke test the dopamine layer: scoreboard, win feed, fresh-win banner, now strip."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

lines = []

# closed_trades against the real bridge DB
import sqlite3
conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT pair, action, source, closed_at, pnl FROM signals "
    "WHERE status='closed' AND pnl IS NOT NULL AND closed_at IS NOT NULL "
    "ORDER BY closed_at DESC LIMIT 8").fetchall()
conn.close()
lines.append(f"closed_trades rows: {len(rows)}")
for r in rows:
    lines.append(f"  {r['pair']} pnl={r['pnl']} closed={r['closed_at']}")

# scoreboard markup (no streamlit — call the raw builders via import trick)
# today_scoreboard needs st (cache) — test the math inline instead
from datetime import datetime
from zoneinfo import ZoneInfo
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)
today_iso = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
todays = [dict(r) for r in rows if (r["closed_at"] or "") >= today_iso]
wins = [t for t in todays if t["pnl"] and t["pnl"] > 0]
losses = [t for t in todays if t["pnl"] and t["pnl"] < 0]
lines.append(f"today: {len(todays)} closed ({len(wins)} wins, {len(losses)} losses)")

# fresh win detection logic
fresh = None
for t in rows:
    if not t["pnl"] or t["pnl"] <= 0:
        continue
    try:
        c = datetime.fromisoformat(t["closed_at"])
        if c.tzinfo is None:
            c = c.replace(tzinfo=MST)
        if (now - c).total_seconds() <= 1800:
            fresh = t["pair"]
            break
    except Exception:
        continue
lines.append(f"fresh win in last 30min: {fresh or 'none'}")

out = BASE / "dopamine_check.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("ok")
