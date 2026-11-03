"""Re-baseline FIX: the first pass grabbed the midnight snapshot
($384.20) instead of the CURRENT balance. The bot's own breaker/flatten
lines are the live readings — pull the most recent 'now:' / 'USD free'
value and set today's baseline to THAT."""
import re
import sqlite3
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
MST = ZoneInfo("America/Denver")
today = datetime.now(MST).strftime("%Y-%m-%d")

lines = open(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\logs\bot.log",
             encoding="utf-8", errors="replace").read().splitlines()

# the most recent live balance reading: breaker 'now: $X' or flatten 'USD free : X'
now_vals = []
for l in lines:
    if l[:10] != today:
        continue
    m = re.search(r"now: \$([\d.]+)", l)
    if m:
        now_vals.append((l[:19], float(m.group(1)), "breaker"))
    m = re.search(r"USD free\s*:\s*([\d.]+)", l)
    if m:
        now_vals.append((l[:19], float(m.group(1)), "flatten"))
print("live balance readings today (last 4):")
for ts, v, src in now_vals[-4:]:
    print(f"   {ts}  ${v:.2f}  ({src})")
current = now_vals[-1][1] if now_vals else 294.10
print(f"-> using CURRENT = ${current:.2f}")

conn = sqlite3.connect("signals.db")
conn.execute("UPDATE daily_balance SET starting_balance=? WHERE date=?", (current, today))
conn.commit()
conn.close()

import signal_bridge as sb
from pathlib import Path as _P
lock = _P("kill_switch.lock")
if lock.exists():
    lock.unlink()
start = sb.get_daily_start_balance()
print(f"BASELINE NOW: ${start:.2f}  |  kill switch active: {sb.is_kill_switch_active()}")
print(f"The next bot cycle: balance ~${current:.2f} vs baseline ${start:.2f} "
      f"= a flat day. Nothing to re-fire.")
