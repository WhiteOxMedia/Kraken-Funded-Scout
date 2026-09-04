"""POPCAT + HYPE whipsaw check (user: 'fired a buy order then said to get
out then said to get back in and then said it went cold?') — reconstruct
the exact message sequence + the state behind each message."""
import sys
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
import sqlite3

MST = ZoneInfo("America/Denver")
print("NOW:", datetime.now(MST).strftime("%H:%M:%S"))
print()

# ── 1. every line she said since 11:45 ────────────────────────────────
vl = [json.loads(l) for l in open("voice_log.jsonl", encoding="utf-8") if l.strip()]
print("1. HER WORDS since 11:45 (ALL — the exact sequence the user heard):")
for d in vl:
    ts = str(d.get("ts", ""))
    if ts >= "2026-08-31T11:45":
        print(f"  {ts[11:16]}  {str(d.get('text', ''))[:150]}")
print()

# ── 2. bridge rows for both coins today ──────────────────────────────
conn = sqlite3.connect("signals.db")
conn.row_factory = sqlite3.Row
print("2. BRIDGE ROWS today (POPCAT + HYPE):")
for r in conn.execute(
    "SELECT * FROM signals WHERE (pair LIKE 'POP%' OR pair LIKE 'HYPE%') "
    "AND timestamp >= '2026-08-31' ORDER BY timestamp"
):
    d = dict(r)
    print(f"  {d['timestamp'][:19]}  {d['pair']}  {d['action']}  conf {d['confidence']}  "
          f"src {d['source']}  status {d['status']}")
    if d.get("execution_reason"):
        print(f"      outcome: {str(d['execution_reason'])[:130]}")
conn.close()
print()

# ── 3. contest watch state ────────────────────────────────────────────
try:
    cw = json.load(open("contest_watch.json", encoding="utf-8"))
    print("3. CONTEST WATCH:")
    items = cw if isinstance(cw, list) else cw.get("positions", cw)
    if isinstance(items, dict):
        items = [dict(v, pair=k) for k, v in items.items()]
    for p in items or []:
        print(f"  {p}")
except Exception as e:
    print("3. contest_watch read failed:", e)
print()

# ── 4. rotation lockout ───────────────────────────────────────────────
try:
    lk = json.load(open("rotation_lockout.json", encoding="utf-8"))
    print("4. ROTATION LOCKOUT:", json.dumps(lk, indent=1)[:600])
except Exception as e:
    print("4. lockout read failed:", e)
