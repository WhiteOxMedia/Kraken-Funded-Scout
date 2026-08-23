"""Recent scout tournament winners from Postgres."""
import os
import sys
from pathlib import Path

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
            "SELECT timestamp, pair, action, confidence FROM scout_signals "
            "ORDER BY timestamp DESC LIMIT 12")).fetchall()
        lines.append("recent scout_signals (tournament winners etc.):")
        for r in rows:
            lines.append(f"  {r[0]}  {r[1]:<10} {r[2]:<4} {r[3]}%")
except Exception as exc:
    lines.append(f"ERROR: {exc}")

out = BASE / "winners_check.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
