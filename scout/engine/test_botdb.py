"""Bot's own Postgres: what positions does IT think are open? + recent trades."""
import os
import sys
from pathlib import Path
import scratch_space   # scratch policy: nothing volatile lands in the repo root

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

lines = []
try:
    from sqlalchemy import create_engine, text
    url = os.environ.get("DB195374BC_DATABASE_URL") or os.environ.get("DB1005AA9E_DATABASE_URL")
    if not url:
        from config import DB_URL
        url = DB_URL
    eng = create_engine(url)
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, pair, entry_volume, entry_price, entry_timestamp, is_open "
            "FROM positions ORDER BY id DESC LIMIT 10")).fetchall()
        lines.append("bot Postgres positions (latest 10):")
        for r in rows:
            lines.append(f"  #{r[0]} {r[1]} {r[2]} @ {r[3]} opened {str(r[4])[:16]} open={r[5]}")
        rows = conn.execute(text(
            "SELECT side, pair, volume, price, cost, timestamp FROM trades "
            "WHERE timestamp >= '2026-08-23' ORDER BY timestamp DESC LIMIT 20")).fetchall()
        lines.append("bot trades today:")
        for r in rows:
            lines.append(f"  {str(r[5])[:16]} {r[0]:<4} {r[1]:<10} {r[2]} @ {r[3]} (${r[4]:.2f})")
except Exception as exc:
    lines.append(f"ERROR: {exc}")

out = BASE / "botdb_check.txt"
scratch_space.report_temp('botdb_check.txt').write_text("\n".join(lines), encoding="utf-8")
print("done")
