"""Bot Postgres positions vs reality (Sep 4): the console's 'holding N' count
and the reconcile ghost plate read the bot's positions table. Presence-check
the DB env vars, then list open rows — never printing the URLs themselves."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
out = []

url = (os.environ.get("DB195374BC_DATABASE_URL")
       or os.environ.get("DB1005AA9E_DATABASE_URL"))
out.append(f"DB env present: {bool(url)}")

if url:
    try:
        from sqlalchemy import create_engine, text
        eng = create_engine(url)
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, pair, base_currency, entry_volume, entry_price, "
                "entry_timestamp, is_open FROM positions WHERE is_open = true "
                "ORDER BY id")).fetchall()
            out.append(f"OPEN positions: {len(rows)}")
            for r in rows:
                out.append(f"#{r[0]} {r[1]} base={r[2]} vol={r[3]} "
                           f"entry={r[4]} ts={r[5]}")
            last_closed = conn.execute(text(
                "SELECT id, pair, entry_timestamp, is_open FROM positions "
                "ORDER BY id DESC LIMIT 8")).fetchall()
            out.append("last 8 rows (newest first):")
            for r in last_closed:
                out.append(f"#{r[0]} {r[1]} ts={r[2]} open={r[3]}")
    except Exception as exc:
        out.append(f"query error: {exc}")

txt = "\n".join(out)
Path(__file__).parent.joinpath("botdb_xmr.txt").write_text(txt, encoding="utf-8")
print(txt)
