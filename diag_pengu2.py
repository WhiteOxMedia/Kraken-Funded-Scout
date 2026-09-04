"""PENGU pace + the bot's round-trip result on the 15:39 signal."""
import sys, sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)

lines = []
conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row
r = conn.execute(
    "SELECT timestamp, status, entry_price, execution_price, executed_at, closed_at, pnl "
    "FROM signals WHERE pair='PENGU/USD' ORDER BY timestamp DESC LIMIT 1").fetchone()
if r:
    lines.append(f"bridge PENGU: created {r['timestamp'][:19]}  status={r['status']}")
    lines.append(f"  signal entry {r['entry_price']}  filled @ {r['execution_price']} at {str(r['executed_at'])[:19]}")
    lines.append(f"  closed {str(r['closed_at'])[:19]}  pnl={r['pnl']}")
conn.close()

from background_scanner import rotation_key_for_hour, _schedule_for_dow, SCALP_TARGET_PCT
from volatility_engine import load_rotation_thresholds
doc = load_rotation_thresholds(max_age_s=900)
rot, sched = rotation_key_for_hour(now.hour), _schedule_for_dow(now.weekday())
cell = doc.get("universe", {}).get("PENGU/USD", {}).get(sched, {}).get(rot) or {}
avg = float(cell.get("avg_move") or 0)
lines.append(f"PENGU engine {rot} {sched}: avg_move={avg:.2f}%  "
             f"$20-ETA={(SCALP_TARGET_PCT/avg*4 if avg else 0):.1f}h  "
             f"false_breakout={float(cell.get('false_breakout_rate') or 0):.0%}")

out = BASE / "pengu_pace.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
