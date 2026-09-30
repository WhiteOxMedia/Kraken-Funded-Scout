"""Test THE KILL-SWITCH RESET REBASELINE (user rule, Sep 7: "the kill
switch needs to accept the new low balance after a reset via the
shortcut") — reset_rebaseline.py: the reset re-baselines today's
daily_balance to the ACTUAL balance so the bot's 10% circuit breaker
accepts the new level instead of instantly re-triggering.

HERMETIC + END-TO-END (CodeRabbit/Copilot, Sep 8): the whole main()
runs against scratch copies of the DB, the lock, the bot log and the
seam module — no live file is touched. Five outcomes are graded by
BEHAVIOR: a successful rebaseline clears the lock; a FAILED write keeps
the lock unless the user explicitly confirms (Copilot's [high]: a reset
must not silently clear the lock on a day the rebaseline didn't
happen); no balance source keeps it; a non-interactive run is a
decline (the safe state is the switch left ON); EOF is a decline."""
import contextlib
import gc
import io
import os
import shutil
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
import reset_rebaseline as rr

MST = ZoneInfo("America/Denver")
today = datetime.now(MST).strftime("%Y-%m-%d")

# ── 0. the live day (READ-ONLY, non-brittle): the fix shipped live ────
# clock-audit: ok — LIVE-PROBE (read-only, existence-gated pin — documented non-brittle) (clockbombs census 4)
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

# ── the hermetic seam: scratch DB + lock + bot log ────────────────────
SCRATCH = Path(tempfile.mkdtemp(prefix="resetrebaseline_"))
DB = SCRATCH / "signals.db"  # leak-audit: ok (a sandbox local — SCRATCH is mkdtemp — not a module swap)
LOCK = SCRATCH / "kill_switch.lock"
LOG = SCRATCH / "bot.log"  # leak-audit: ok (a sandbox local, not a module swap)
_sb_db, _sb_ks = sb.DB_PATH, sb.KILL_SWITCH_FILE
_rr_db, _rr_log = rr.DB_PATH, rr.BOT_LOG
sb.DB_PATH, sb.KILL_SWITCH_FILE = DB, LOCK
rr.DB_PATH, rr.BOT_LOG = DB, LOG

# ── 1/2. the incident math (the breaker's contract) ───────────────────
day_start = 63.33
now_bal = 23.34
loss = (day_start - now_bal) / day_start * 100
check("the incident: the breaker reads the flatten as a -63% day",
      abs(loss - 63.1) < 0.5, f"{loss:.1f}%")
check("the incident: the breaker TRIGGERS (>= 10%)", loss >= 10.0)
check("the incident: reset alone does NOT fix it (the baseline is untouched)",
      day_start > now_bal)


def _close_stray_conns():
    """signal_bridge's `with _get_db()` blocks commit but never CLOSE —
    on Windows an unclosed connection keeps the file handle alive and
    the scratch DB cannot be rebuilt. Close any live connection still
    pointing at the scratch DB (the bridge's per-call lifetime is its
    own hygiene issue — flagged, not fixed here)."""
    for o in gc.get_objects():
        if not isinstance(o, sqlite3.Connection):
            continue
        try:
            dbs = o.execute("PRAGMA database_list").fetchall()
            for _seq, _name, f in (
                tuple(r) if not hasattr(r, "keys") else tuple(r[j] for j in range(3))
                for r in dbs):
                if f and SCRATCH in Path(str(f)).parents or \
                   (f and Path(str(f)) == DB):
                    o.close()
                    break
        except Exception:
            pass


def fresh_db(with_row=True):
    """A scratch signals.db carrying (or not) today's baseline row."""
    if DB.is_dir():
        shutil.rmtree(DB, ignore_errors=True)
    elif DB.exists():
        _close_stray_conns()
        DB.unlink()
    conn = sqlite3.connect(str(DB))
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_balance (
        date TEXT PRIMARY KEY, starting_balance REAL NOT NULL, timestamp TEXT NOT NULL)""")
    if with_row:
        conn.execute("INSERT INTO daily_balance VALUES (?, ?, 'test')", (today, day_start))
    conn.commit()
    conn.close()


def run_main(argv=(), stdin_text=None):
    """Run the REAL main() with seams swapped. stdin_text -> interactive
    with that input; None -> non-interactive (the cron/piped case)."""
    old_argv = list(sys.argv)
    old_inter = rr._INTERACTIVE
    old_stdin = sys.stdin
    buf = io.StringIO()
    try:
        sys.argv = ["reset_rebaseline.py", *argv]
        if stdin_text is None:
            rr._INTERACTIVE = False
            sys.stdin = io.StringIO("")
        else:
            rr._INTERACTIVE = True
            sys.stdin = io.StringIO(stdin_text)
        with contextlib.redirect_stdout(buf):
            rr.main()
    finally:
        sys.argv = old_argv
        rr._INTERACTIVE = old_inter
        sys.stdin = old_stdin
    return buf.getvalue()


try:
    # ── S1. the happy path: the bot log speaks, the write lands, the
    #    lock clears, the breaker's view is verified ──
    fresh_db()
    LOG.write_text(f"{today} 11:00:00 [INFO] breaker: day ${day_start:.2f}, "
                   f"now: ${now_bal:.2f}\n", encoding="utf-8")
    out = run_main()
    check("S1: the log's live reading is detected as the balance",
          f"${now_bal:.2f}" in out and "bot log" in out, out[:120])
    check("S1: the day is RE-BASELINED to the actual balance",
          "RE-BASELINED" in out, out[:200])
    check("S1: the lock cleared (trading resumes)",
          not sb.is_kill_switch_active() and not LOCK.exists())
    check("S1: the breaker's view is verified ~0%",
          "VERIFIED" in out and "0.0%" in out, out[-160:])

    # ── S2. the failed write + the user DECLINES (Copilot's [high]) ──
    fresh_db()
    DB.unlink()
    os.makedirs(DB)          # a directory where the DB belongs: connect dies
    LOCK.write_text(f"{today}T00:00:00\nthe incident\n", encoding="utf-8")
    out = run_main(stdin_text="n\n")
    check("S2: the failed write is reported, not swallowed",
          "REBASELINE WRITE FAILED" in out, out[:200])
    check("S2: the user is warned the lock is about to clear on a stale baseline",
          "WITHOUT a rebaseline" in out and "re-fire" in out, out[:300])
    check("S2: DECLINED -> the lock STAYS (trading stays paused)",
          sb.is_kill_switch_active() and LOCK.exists())
    check("S2: the output says KEPT and names the fix",
          "KEPT" in out and "balance as" in out, out[-200:])

    # ── S3. the failed write + the user CONFIRMS: their call ──
    out = run_main(stdin_text="y\n")
    check("S3: confirmed -> the lock clears (the user's explicit call)",
          not sb.is_kill_switch_active() and not LOCK.exists())
    check("S3: the output names the stale baseline consequence",
          "Confirmed" in out and "stale" in out, out[-220:])

    # ── S4. no balance source + non-interactive: the safe NO ──
    fresh_db()               # the row exists but the log has no line
    if LOG.exists():
        LOG.unlink()
    LOCK.write_text(f"{today}T00:00:00\nno source\n", encoding="utf-8")
    out = run_main()         # stdin_text=None -> non-interactive (cron/piped)
    check("S4: no source is stated honestly",
          "UNKNOWN" in out and "none found" in out
          and "no balance source was found" in out, out[:260])
    check("S4: non-interactive counts as a NO -> the lock STAYS",
          sb.is_kill_switch_active() and LOCK.exists())

    # ── S5. EOF on the prompt (Ctrl-Z / closed pipe): also a NO ──
    out = run_main(stdin_text="")
    check("S5: EOF is a decline -> the lock STAYS",
          sb.is_kill_switch_active() and LOCK.exists() and "KEPT" in out,
          out[-160:])

    # ── 6. the detector's pieces, called directly ──
    LOG.write_text(f"{today} 11:00:00 [INFO] breaker: now: $23.34\n", encoding="utf-8")
    _argv = sys.argv
    try:
        sys.argv = ["reset_rebaseline.py", "42.5"]
        v, s = rr._detect_balance(today)
        check("detection: the typed balance wins", v == 42.5 and "argument" in s, (v, s))
        sys.argv = ["reset_rebaseline.py", "not-a-number"]
        v2, _s2 = rr._detect_balance(today)
        check("detection: a non-numeric arg falls back to the log",
              v2 == 23.34 and "bot log" in _s2, (v2, _s2))
        sys.argv = ["reset_rebaseline.py"]
        v3, s3 = rr._detect_balance("2099-01-01")
        check("detection: no arg + no matching log day -> no source (the gate decides)",
              v3 is None and "no source" in s3, (v3, s3))
        # Copilot's [medium]: garbage balances are refused, never written
        for _bad in ("0", "-5", "nan", "inf"):
            sys.argv = ["reset_rebaseline.py", _bad]
            vb, sb_ = rr._detect_balance(today)
            check(f"detection: '{_bad}' is refused (falls back to the log)",
                  vb == 23.34 and "bot log" in sb_, (vb, sb_))
    finally:
        sys.argv = _argv

    # ── 7. the wiring ──
    src = Path("reset_rebaseline.py").read_text(encoding="utf-8")
    check("the rebaseline script exists with the three steps",
          "RE-BASELINED" in src and "reset_kill_switch()" in src
          and "get_daily_start_balance" in src)
    check("the gate exists (the lock does not clear un-confirmed on a failed rebaseline)",
          "def _confirm" in src and "KEPT" in src
          and src.find("if not rebaselined") < src.find("reset_kill_switch()"))
    check("the gate is fail-closed (EOF/KeyboardInterrupt/non-interactive = no)",
          "EOFError" in src and "isatty" in src)
    check("the DB connection closes on every path (no lock held into the reset)",
          "conn.close()" in src and "finally:" in src)
    bat = Path("Kill Switch - RESET.bat").read_text(encoding="utf-8", errors="replace")
    check("the RESET shortcut runs the rebaseline (not the bare reset)",
          "reset_rebaseline.py" in bat and "reset_kill_switch; reset_kill_switch()" not in bat)
    check("the shortcut tells the user how to correct a wrong balance",
          "reset_rebaseline.py ^<balance^>" in bat)
    check("the shortcut's closing message matches the actual lock state",
          "if exist \"kill_switch.lock\"" in bat
          and "THE RESET DID NOT COMPLETE" in bat)
finally:
    sb.DB_PATH, sb.KILL_SWITCH_FILE = _sb_db, _sb_ks
    rr.DB_PATH, rr.BOT_LOG = _rr_db, _rr_log
    _close_stray_conns()
    shutil.rmtree(SCRATCH, ignore_errors=True)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
Path("_resetrebaseline_check.txt").write_text("\n".join(lines), encoding="utf-8")

print(f"RESULT: {PASS} passed, {FAIL} failed")

raise SystemExit(1 if FAIL else 0)   # the fleet-honesty fix (Sep 30): DONE-only tails failed closed
print("DONE")
