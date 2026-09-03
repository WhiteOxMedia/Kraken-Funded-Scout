"""Position roll audit: what happened to ASTER, when did APT take the seat?"""
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

conn = sqlite3.connect(Path(__file__).parent / "signals.db")
conn.row_factory = sqlite3.Row
out = []
for r in conn.execute(
    "SELECT pair, action, confidence, source, status, timestamp, executed_at, "
    "execution_price, closed_at, pnl, execution_reason, highest_price "
    "FROM signals WHERE pair IN ('ASTER/USD','APT/USD') "
    "AND timestamp > '2026-09-02 08:00' ORDER BY rowid"
):
    out.append(f"{r['timestamp'][:19]} {r['pair']:<10} {r['action']} conf={r['confidence']} "
               f"src={r['source']} status={r['status']} "
               f"exec={r['executed_at'] or ''} px={r['execution_price'] or ''} "
               f"high={r['highest_price'] or ''} closed={r['closed_at'] or ''} "
               f"pnl={r['pnl']} reason={r['execution_reason'] or ''}")
conn.close()
print("\n".join(out) if out else "no rows")

