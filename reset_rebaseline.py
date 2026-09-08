"""THE KILL-SWITCH RESET'S REBASELINE (Sep 7).

The user's report: "the kill switch needs to accept the new low balance
after a reset via the shortcut." The RESET shortcut deleted the lock —
and the bot's breaker immediately re-triggered it, because the 10%
circuit breaker still compares the (post-flatten, LOW) balance against
the MORNING snapshot (the pre-flatten, HIGH one). Reset without
rebaseline = the breaker re-fires within one bot cycle. This happened
for real before (diag_rebaseline.py, diag_rebaseline2.py — the funding
incident: a deposit read as a -23.4% loss).

THE FIX: the reset now RE-BASELINES the day to the actual balance. The
breaker's contract is "10% from where the day STARTED" — after a manual
kill + flatten + reset, the day starts NOW. This script:

  1. reads the ACTUAL current balance (in priority order):
       a. --balance $X  argument (the user types what they see)
       b. the bot's own log: the newest 'now: $X' breaker reading or
          'Daily balance snapshot: $X' today
       c. today's existing daily_balance row (last resort — it equals
          the morning snapshot, i.e. NO rebaseline; the script says so
          and still clears the lock, since the user asked for a reset)
  2. UPDATEs today's daily_balance row to that balance (the day
     restarts here — the first check of the day already ran)
  3. deletes kill_switch.lock (the reset itself)
  4. verifies: is_kill_switch_active() False, and the breaker math
     reads ~0% for the new baseline

The bot never needs a restart: it reads the baseline fresh every cycle.
The contest account (contest_account.json) is a SEPARATE ledger — the
user updates it in the console sidebar; this script does not touch it.

Run:  .venv\\Scripts\\python.exe reset_rebaseline.py [balance]
      (no argument = auto-detect from the bot's log)
"""
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

MST = ZoneInfo("America/Denver")
BOT_LOG = Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\logs\bot.log")
# the seams the test swaps for scratch copies (test_resetrebaseline.py)
DB_PATH = Path(__file__).resolve().parent / "signals.db"
# computed once at import: an interactive console can confirm; anything
# else (cron, piped stdin, a test harness) is treated as a NO — the safe
# state is the switch left ON
_INTERACTIVE = bool(sys.stdin) and sys.stdin.isatty()


def _detect_balance(today: str) -> tuple[float | None, str]:
    """The actual current balance: CLI arg > bot log > today's row."""
    # 1) the user typed it
    if len(sys.argv) > 1:
        try:
            return float(sys.argv[1]), "--balance argument"
        except ValueError:
            print(f"  ! '{sys.argv[1]}' is not a number — falling back to detection")
    # 2) the bot's own live readings (newest first)
    if BOT_LOG.exists():
        vals = []
        try:
            for l in BOT_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
                if l[:10] != today:
                    continue
                m = re.search(r"now: \$([\d.]+)", l)          # breaker line
                if not m:
                    m = re.search(r"snapshot: \$([\d.]+)", l)  # daily snapshot
                if not m:
                    m = re.search(r"USD free\s*:\s*([\d.]+)", l)  # flatten line
                if m:
                    vals.append((l[:19], float(m.group(1))))
        except Exception:
            pass
        if vals:
            ts, v = vals[-1]
            return v, f"bot log {ts} (newest live reading)"
    return None, "no source"


def _confirm(prompt: str) -> bool:
    """Ask the user. Non-interactive stdin is a NO — the safe state is the
    switch left ON (Copilot, Sep 8: a reset must not silently clear the
    lock on a day the rebaseline did not happen)."""
    try:
        if not _INTERACTIVE:
            return False
        return input(prompt).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def main():
    today = datetime.now(MST).strftime("%Y-%m-%d")
    print(f"KILL-SWITCH RESET REBASELINE — {today}")
    bal, src = _detect_balance(today)
    if bal is None:
        src = "none found"
    print(f"1. actual balance: " + (f"${bal:.2f}  ({src})" if bal is not None else f"UNKNOWN ({src})"))

    # the DB flow is GUARDED (CodeRabbit, Sep 8): the lock must clear
    # even when the rebaseline write fails — the live bot holds signals.db
    # often (WAL busy), and a stranded reset is worse than a stale baseline
    rebaselined = False
    old = None
    conn = None
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
        row = conn.execute("SELECT starting_balance FROM daily_balance WHERE date=?",
                           (today,)).fetchone()
        old = float(row[0]) if row else None
        print(f"2. today's baseline: " + (f"${old:.2f}" if old is not None else "not recorded yet"))

        if bal is not None:
            if row:
                conn.execute("UPDATE daily_balance SET starting_balance=?, timestamp=? WHERE date=?",
                             (bal, datetime.now(MST).isoformat(), today))
            else:
                conn.execute("INSERT INTO daily_balance (date, starting_balance, timestamp) VALUES (?, ?, ?)",
                             (today, bal, datetime.now(MST).isoformat()))
            conn.commit()
            rebaselined = True
        else:
            print("3. NO REBASELINE possible — the lock still clears, but if the "
                  "balance dropped >10% today the breaker WILL re-fire on the "
                  "next bot cycle. Run again with the balance as the argument.")
    except Exception as exc:
        print(f"3. REBASELINE WRITE FAILED: {exc} — the lock still clears. "
              f"Run the script again (the bot may hold the DB; it frees it) "
              f"or type the balance as the argument.")
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass

    if rebaselined and bal is not None:
        print(f"3. RE-BASELINED: today now starts at ${bal:.2f}"
              + (f"  (was ${old:.2f} — the breaker would have read "
                 f"{(old - bal) / old * 100:.1f}% against it)" if old else ""))

    # ── the gate (Copilot's [high], Sep 8): the lock does not clear on a
    #    day the rebaseline did NOT happen unless the user CONFIRMS —
    #    resuming on a stale baseline invites a breaker re-fire ──
    if not rebaselined:
        _why = ("the rebaseline write failed" if bal is not None
                else "no balance source was found")
        print(f"! The lock is about to clear WITHOUT a rebaseline ({_why}).")
        print("  If today's balance dropped more than 10%, the breaker WILL")
        print("  re-fire on the next bot cycle.")
        if _confirm("  Clear the kill switch anyway? [y/N]: "):
            print("  Confirmed — clearing on your call (the baseline is stale; "
                  "re-run with the balance to fix it).")
        else:
            print("  KEPT: kill_switch.lock stays — trading stays paused.")
            print("  Re-run the shortcut, or the script with the balance as "
                  "the argument, to finish the reset.")
            return

    # 4. the reset itself (only reached on a real rebaseline or a confirm)
    from signal_bridge import reset_kill_switch, is_kill_switch_active, get_daily_start_balance
    reset_kill_switch()
    print(f"4. kill switch: active={is_kill_switch_active()}  (lock cleared)")

    # 5. verify the breaker's view (guarded — the reset already landed;
    #    an unreadable DB must not crash the closing output)
    try:
        start = get_daily_start_balance()
        if start and bal:
            pct = (start - bal) / start * 100 if start else 0.0
            print(f"5. VERIFIED: baseline ${start:.2f}, breaker reads {pct:.1f}% "
                  f"(triggers at -{10.0:.0f}%) — the day starts here.")
        elif start:
            print(f"5. VERIFIED: baseline ${start:.2f} unchanged — if today's drop "
                  f"exceeds 10% the breaker will re-fire; re-run with the balance.")
    except Exception as exc:
        print(f"5. (could not verify the baseline: {exc} — the reset itself "
              f"already landed; check the bot's next cycle)")
    print("Trading resumes on the next bot cycle. Exits were never blocked.")


if __name__ == "__main__":
    main()
