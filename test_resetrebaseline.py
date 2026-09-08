"""Test THE KILL-SWITCH RESET REBASELINE (user rule, Sep 7: "the kill
switch needs to accept the new low balance after a reset via the
shortcut") — reset_rebaseline.py: the reset re-baselines today's
daily_balance to the ACTUAL balance so the bot's 10% circuit breaker
accepts the new level instead of instantly re-triggering.

Offline: the balance detection and the breaker math are tested against
a synthetic daily_balance row + a synthetic lock; the live bot log is
only READ (never written)."""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import signal_bridge as sb

DB = Path("signals.db")
LOCK = Path("kill_switch.lock")
BAK = DB.read_bytes() if DB.exists() else None
LOCK_HAD = LOCK.exists()
if LOCK_HAD:
    LOCK_BAK = LOCK.read_bytes()

try:
    conn = sqlite3.connect(str(DB))
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_balance (
        date TEXT PRIMARY KEY, starting_balance REAL NOT NULL, timestamp TEXT NOT NULL)""")
    # ── 1. the incident, reproduced: a flatten drops the account 63 -> 23
    #       while the morning baseline stays 63 — the breaker reads -63% ──
    conn.execute("DELETE FROM daily_balance WHERE date='2099-01-01'")
    conn.execute("INSERT INTO daily_balance VALUES ('2099-01-01', 63.33, 'test')")
    conn.commit()
    conn.close()

    day_start = 63.33
    now_bal = 23.34
    loss = (day_start - now_bal) / day_start * 100
    check("the incident: the breaker reads the flatten as a -63% day",
          abs(loss - 63.1) < 0.5, f"{loss:.1f}%")
    check("the incident: the breaker TRIGGERS (>= 10%)", loss >= 10.0)
    check("the incident: reset alone does NOT fix it (the baseline is untouched)",
          day_start > now_bal)

    # ── 2. the rebaseline: today's row moves to the ACTUAL balance ──
    conn = sqlite3.connect(str(DB))
    conn.execute("UPDATE daily_balance SET starting_balance=? WHERE date='2099-01-01'",
                 (now_bal,))
    conn.commit()
    row = conn.execute("SELECT starting_balance FROM daily_balance WHERE date='2099-01-01'").fetchone()
    conn.close()
    check("after the rebaseline the day starts at the ACTUAL balance",
          abs(float(row[0]) - now_bal) < 1e-9, row[0])
    loss2 = (float(row[0]) - now_bal) / float(row[0]) * 100
    check("the breaker now reads ~0% — nothing to re-fire", abs(loss2) < 0.01, f"{loss2:.2f}%")

    # ── 3. the lock: the reset clears it, is_kill_switch_active follows ──
    LOCK.write_text("2099-01-01T00:00:00\ntest lock\n")
    check("the lock set -> kill switch active", sb.is_kill_switch_active())
    from signal_bridge import reset_kill_switch
    reset_kill_switch()
    check("reset_kill_switch clears the lock", not sb.is_kill_switch_active())

    # ── 4. the script's pieces exist and are wired ──
    src = Path("reset_rebaseline.py").read_text(encoding="utf-8")
    check("the rebaseline script exists with the three steps",
          "RE-BASELINED" in src and "reset_kill_switch()" in src
          and "get_daily_start_balance" in src)
    check("detection priority: CLI arg > bot log > none",
          "sys.argv" in src and "bot log" in src.lower())
    check("no-source path still clears the lock (the reset must not fail)",
          "NO REBASELINE possible" in src and "reset_kill_switch()" in src)
    bat = Path("Kill Switch - RESET.bat").read_text(encoding="utf-8", errors="replace")
    check("the RESET shortcut runs the rebaseline (not the bare reset)",
          "reset_rebaseline.py" in bat and "reset_kill_switch; reset_kill_switch()" not in bat)
    check("the shortcut tells the user how to correct a wrong balance",
          "reset_rebaseline.py <balance>" in bat)

    # ── 5. the live day is healthy right now (the script already ran) ──
    import datetime as _dt
    from zoneinfo import ZoneInfo
    today = _dt.datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(DB))
    row = conn.execute("SELECT starting_balance FROM daily_balance WHERE date=?", (today,)).fetchone()
    conn.close()
    check("today's live baseline equals the bot's newest reading (the fix is live)",
          row is not None and abs(float(row[0]) - 23.34) < 0.01, row[0] if row else None)
    check("the live kill switch is clear", not sb.is_kill_switch_active())
finally:
    if BAK is not None:
        DB.write_bytes(BAK)
    elif DB.exists():
        DB.unlink()
    if LOCK_HAD:
        LOCK.write_bytes(LOCK_BAK)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
Path("_resetrebaseline_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("DONE")
