"""Remove the two duplicate bot_strategy bridge records (mis-stamped -1h,
same buy as the scout_auto originals). Backup first; verify after."""
import sys, shutil, sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / "signals.db"

shutil.copy2(DB, BASE / "signals.db.bak-20260824-dupes")
print("backup written: signals.db.bak-20260824-dupes")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# safety: only delete a bot_strategy 'filled' row when a scout_* 'filled' row
# exists for the SAME pair (proven duplicate of the same buy)
rows = conn.execute(
    "SELECT signal_id, pair FROM signals WHERE source='bot_strategy' AND status='filled'").fetchall()
deleted = []
for r in rows:
    orig = conn.execute(
        "SELECT 1 FROM signals WHERE pair=? AND source LIKE 'scout%' AND status='filled' LIMIT 1",
        (r["pair"],)).fetchone()
    if orig:
        conn.execute("DELETE FROM signals WHERE signal_id=?", (r["signal_id"],))
        deleted.append(f"{r['pair']} id={r['signal_id']}")
conn.commit()

left = conn.execute(
    "SELECT signal_id, pair, source, timestamp FROM signals WHERE status='filled' AND action='buy'").fetchall()
print(f"deleted {len(deleted)}: {deleted}")
print(f"remaining open buy records: {len(left)}")
for l in left:
    print(f"  {l['timestamp'][:19]} {l['pair']} {l['source']}")
conn.close()
