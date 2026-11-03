"""Prove the new dup-check catches 'acknowledged' records.
Runs inside a transaction that ROLLS BACK — nothing persists."""
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

SCOUT = Path(r"C:\Users\birds\Workspace\kraken_scout")
MST = ZoneInfo("America/Denver")
now = datetime.now(MST).isoformat()

# clock-audit: ok — SANDBOXED (transaction-rollback drill — BEGIN/ROLLBACK leaves no residue; :41 proves it) (clockbombs census 4)
conn = sqlite3.connect(SCOUT / "signals.db")
conn.row_factory = sqlite3.Row
conn.execute("BEGIN")

# simulate: a scout buy mid-execution (the exact state during _execute_buy)
conn.execute(
    "INSERT INTO signals (signal_id, pair, action, confidence, reason, timestamp, "
    "valid_until, status, source) VALUES (?, ?, 'buy', 70, 'synthetic', ?, ?, 'acknowledged', 'scout_auto')",
    ("synthetic1", "TESTACK/USD", now, now))

# the EXACT check the fixed bot_engine runs before writing its notify record
dup = conn.execute(
    "SELECT 1 FROM signals WHERE pair=? AND action='buy' "
    "AND status NOT IN ('closed','expired','rejected') LIMIT 1",
    ("TESTACK/USD",)).fetchone()

# also prove the OLD buggy check would have MISSED it (regression context)
old_style = conn.execute(
    "SELECT 1 FROM signals WHERE pair=? AND action='buy' "
    "AND status IN ('pending','filled') LIMIT 1",
    ("TESTACK/USD",)).fetchone()

conn.rollback()  # nothing persisted
conn.close()

print("NEW check finds acknowledged record (dup suppressed):", dup is not None)
print("OLD check would have missed it (dup written):", old_style is None)

# confirm nothing persisted
# clock-audit: ok — LIVE-PROBE (existence/count pin — expects zero residue from the rollback above) (clockbombs census 4)
conn = sqlite3.connect(SCOUT / "signals.db")
conn.row_factory = sqlite3.Row
left = conn.execute("SELECT COUNT(*) c FROM signals WHERE pair='TESTACK/USD'").fetchone()["c"]
conn.close()
print("synthetic rows persisted (must be 0):", left)
