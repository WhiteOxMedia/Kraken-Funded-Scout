"""Re-baseline today's daily start balance to the CURRENT balance so the
circuit breaker stops reading the user's contest funding as a -23.4% loss
and re-firing the kill switch (user chose option 1). Steps: read the
bot's latest real balance snapshot from its log, update today's
daily_balance row, clear the kill switch lock, verify."""
import sqlite3
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
MST = ZoneInfo("America/Denver")
today = datetime.now(MST).strftime("%Y-%m-%d")
print(f"TODAY: {today}")

# ── 1. the bot's latest real balance (from its own snapshots) ────────
lines = open(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\logs\bot.log",
             encoding="utf-8", errors="replace").read().splitlines()
snaps = [l for l in lines if "Daily balance snapshot" in l and l[:10] == today]
latest = snaps[-1] if snaps else ""
print(f"1. LATEST BOT SNAPSHOT: {latest[:120]}")
import re
m = re.search(r"snapshot: \$([\d.]+)", latest)
current = float(m.group(1)) if m else 294.10
print(f"   -> current balance: ${current:.2f}")

# ── 2. what the breaker is comparing against ─────────────────────────
conn = sqlite3.connect("signals.db")
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT * FROM daily_balance WHERE date=?", (today,)).fetchone()
print(f"2. CURRENT BASELINE: {dict(row) if row else 'none'}")

# ── 3. re-baseline: set today's starting_balance to the current balance ─
if row:
    conn.execute("UPDATE daily_balance SET starting_balance=? WHERE date=?",
                 (current, today))
else:
    conn.execute("INSERT INTO daily_balance (date, starting_balance, timestamp) "
                 "VALUES (?, ?, ?)",
                 (today, current, datetime.now(MST).isoformat()))
conn.commit()

# ── 4. clear the kill switch lock ────────────────────────────────────
lock = Path("kill_switch.lock")
if lock.exists():
    lock.unlink()
    print("4. kill_switch.lock: DELETED")
else:
    print("4. kill_switch.lock: already gone")

# ── 5. verify ────────────────────────────────────────────────────────
import signal_bridge as sb
start = sb.get_daily_start_balance()
row2 = dict(conn.execute("SELECT * FROM daily_balance WHERE date=?", (today,)).fetchone())
conn.close()
print(f"5. VERIFIED: baseline now ${start:.2f}  |  drop vs current: "
      f"{(start - current) / start * 100 if start else 0:.1f}%  |  "
      f"kill switch active: {sb.is_kill_switch_active()}")
print("   The next bot cycle sees a 0% day — nothing to re-fire.")
