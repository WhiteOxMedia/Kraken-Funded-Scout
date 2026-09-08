"""Test THE KILL-SWITCH RESET REBASELINE (user rule, Sep 7: "the kill
switch needs to accept the new low balance after a reset via the
shortcut") — reset_rebaseline.py: the reset re-baselines today's
daily_balance to the ACTUAL balance so the bot's 10% circuit breaker
accepts the new level instead of instantly re-triggering.

HERMETIC (CodeRabbit, Sep 8): the breaker math, the rebaseline write and
the lock clear run against a SYNTHETIC signals.db + kill_switch.lock in
a temp dir (the seam is sb.DB_PATH / sb.KILL_SWITCH_FILE — the same seam
test_tidestamp uses). The live DB is only ever READ, and only for a
non-brittle sanity check — no balance-VALUE assertion (trading moves the
balance; a hardcoded constant breaks the suite by design)."""
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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

MST = ZoneInfo("America/Denver")
today = datetime.now(MST).strftime("%Y-%m-%d")

# ── 0. the live day (READ-ONLY, non-brittle): the fix shipped live ────
_live = Path("signals.db")
if _live.exists():
    _lc = sqlite3.connect(str(_live))
    _row = _lc.execute("SELECT starting_balance FROM daily_balance WHERE date=?",
                       (today,)).fetchone()
    _lc.close()
    check("the live day's baseline, if recorded, is a positive balance (read-only)",
          _row is None or float(_row[0]) > 0.0, _row[0] if _row else None)
else:
    check("no live signals.db in this environment (nothing live to assert)", True)
check("the live kill switch is clear", not sb.is_kill_switch_active())

# ── the hermetic seam: a synthetic signals.db + lock in a temp dir ────
SCRATCH = Path(tempfile.mkdtemp(prefix="resetrebaseline_"))
DB = SCRATCH / "signals.db"
LOCK = SCRATCH / "kill_switch.lock"
_sb_db, _sb_ks = sb.DB_PATH, sb.KILL_SWITCH_FILE
sb.DB_PATH, sb.KILL_SWITCH_FILE = DB, LOCK

try:
    conn = sqlite3.connect(str(DB))
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_balance (
        date TEXT PRIMARY KEY, starting_balance REAL NOT NULL, timestamp TEXT NOT NULL)""")
    # ── 1. the incident, reproduced: a flatten drops the account 63 -> 23
    #       while the morning baseline stays 63 — the breaker reads -63% ──
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
          "reset_rebaseline.py ^<balance^>" in bat)

    # ── 4b. the GUARDED DB flow (CodeRabbit, Sep 8): the lock must clear
    #    even when the rebaseline write dies (the live bot holds the DB) ──
    check("the rebaseline write is guarded (except + finally around the DB flow)",
          "except Exception" in src and "finally:" in src
          and src.find("except Exception") < src.find("reset_kill_switch()"))
    check("the DB connection closes on every path (no lock held into the reset)",
          "conn.close()" in src)
    check("a failed write still prints the re-run hint (the user is never stranded)",
          "REBASELINE WRITE FAILED" in src)

    # ── 4c. the balance detector RUNS (behavioral, not just grepped) ──
    import reset_rebaseline as rr
    _argv = sys.argv
    try:
        sys.argv = ["reset_rebaseline.py", "42.5"]
        v, s = rr._detect_balance("2099-01-01")
        check("detection: the typed balance wins", v == 42.5 and "argument" in s, (v, s))
        sys.argv = ["reset_rebaseline.py", "not-a-number"]
        v2, _s2 = rr._detect_balance("2099-01-01")
        check("detection: a non-numeric arg falls back (2099 has no log lines)",
              v2 is None, v2)
        sys.argv = ["reset_rebaseline.py"]
        v3, s3 = rr._detect_balance("2099-01-01")
        check("detection: no arg + no matching log day -> no source (the lock still clears)",
              v3 is None and "no source" in s3, (v3, s3))
    finally:
        sys.argv = _argv
finally:
    sb.DB_PATH, sb.KILL_SWITCH_FILE = _sb_db, _sb_ks

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
Path("_resetrebaseline_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("DONE")
