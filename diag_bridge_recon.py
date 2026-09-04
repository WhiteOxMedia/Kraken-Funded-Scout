"""Reconcile bridge 'filled' records vs actual open bot positions."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import sqlite3
lines = []

conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT signal_id, pair, action, status, source, confidence, timestamp, entry_price "
    "FROM signals WHERE status='filled' AND action='buy' ORDER BY timestamp DESC LIMIT 12").fetchall()
lines.append(f"bridge filled/open buy records: {len(rows)} (showing up to 12)")
for r in rows:
    lines.append(f"  {r['timestamp'][:19]} {r['pair']:<12} src={r['source']:<14} conf={r['confidence']:.0f} "
                 f"entry={r['entry_price']} id={r['signal_id']}")

# count pending too
p = conn.execute("SELECT COUNT(*) c FROM signals WHERE status='pending'").fetchone()
lines.append(f"pending records: {p['c']}")
conn.close()

out = BASE / "bridge_reconcile.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
