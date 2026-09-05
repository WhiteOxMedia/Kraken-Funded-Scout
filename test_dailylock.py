"""Test THE DAILY PROFIT LOCK (user rule, Sep 4: "do you see a profit
guard that moves the 3% death-line rule with the contest balance to
protect the daily progress?" — user choice: DAILY RESET, "each day locks
its own progress, fresh room tomorrow", protect ~65% of the day's gain).

THE PLATFORM LINE NEVER MOVES (Kraken: max loss is always against the
STARTING balance, never the peak — re-audited Sep 4 against the Sep 3
article). The lock is a USER-SIDE effective floor layered ABOVE it once
the day's peak is up: floor = day-start + 65% of the day's gain. The
entry guard (3% guard), the light's hard stop, the exit check's stop,
and the QC all key off the SMALLER of the two buffers. Resets with the
Denver day. Legacy state (no daily fields) fails open to the platform
line alone."""
import sys
from datetime import datetime, timedelta
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


import contest_account as ca

MST = ZoneInfo("America/Denver")
TODAY = datetime.now(MST).strftime("%Y-%m-%d")
LINE = 9700.0    # the 10k platform line


def state(cur=10000.0, start=10000.0, day_start=None, peak=None, day=None):
    return {
        "tier": 10000, "start_balance": start, "current_balance": cur,
        "daily_peak_date": day if day is not None else TODAY,
        "daily_start_balance": day_start if day_start is not None else cur,
        "daily_peak_balance": peak if peak is not None else cur,
    }


# ── 1. the floor math ─────────────────────────────────────────────────
check("legacy state (no daily fields) fails open to the platform line",
      ca.daily_lock_floor({"tier": 10000, "start_balance": 10000.0,
                           "current_balance": 10400.0}) == LINE)
check("yesterday's peak doesn't lock today (daily reset)",
      ca.daily_lock_floor(state(10400.0, peak=10400.0, day_start=10000.0,
                                day="2026-09-03")) == LINE)
check("a flat day (no gain) locks nothing",
      ca.daily_lock_floor(state(10000.0, day_start=10000.0, peak=10000.0)) == LINE)
check("a down day locks nothing (the platform line stands)",
      ca.daily_lock_floor(state(9900.0, day_start=10000.0, peak=10000.0)) == LINE)
check("up $400 on the day: floor = day-start + 65% of the gain ($10,260)",
      abs(ca.daily_lock_floor(state(10400.0, day_start=10000.0, peak=10400.0))
          - 10260.0) < 0.01,
      ca.daily_lock_floor(state(10400.0, day_start=10000.0, peak=10400.0)))
check("up $200: floor $10,130 (protects $130, allows $70 giveback)",
      abs(ca.daily_lock_floor(state(10200.0, day_start=10000.0, peak=10200.0))
          - 10130.0) < 0.01)
check("the PEAK rules, not the current: a giveback doesn't lower the floor",
      abs(ca.daily_lock_floor(state(10100.0, day_start=10000.0, peak=10400.0))
          - 10260.0) < 0.01)
check("the lock never sits below the platform line",
      ca.daily_lock_floor(state(9710.0, day_start=9700.0, peak=9710.0)) >= LINE - 0.01)
check("THE PLATFORM LINE NEVER MOVES (the audit invariant)",
      ca.death_line(state(10400.0, peak=10400.0, day_start=10000.0)) == LINE)

# ── 2. the buffers + which floor binds ────────────────────────────────
check("effective buffer = the smaller of the two (lock active)",
      abs(ca.effective_buffer(state(10400.0, day_start=10000.0, peak=10400.0))
          - 140.0) < 0.01,
      ca.effective_buffer(state(10400.0, day_start=10000.0, peak=10400.0)))
check("effective buffer = the platform room when the lock is inactive",
      abs(ca.effective_buffer(state(10000.0)) - 300.0) < 0.01)
check("binding floor: 'daily-lock' when the day is up",
      ca.binding_floor(state(10400.0, day_start=10000.0, peak=10400.0))[0] == "daily-lock")
check("binding floor: 'platform' when the lock is inactive",
      ca.binding_floor(state(10000.0))[0] == "platform")

# ── 3. the entry guard shrinks with the lock ──────────────────────────
check("no state passed = platform-only math (the tests' deterministic path)",
      abs(ca.max_entry_usd(10000.0, 3.0, 10000.0) - 3400.0) < 1.0)
_locked = state(10400.0, day_start=10000.0, peak=10400.0)
_cap_locked = ca.max_entry_usd(10400.0, 3.0, 10000.0, state=_locked)
check("lock active: up $400, the 3% dd cap shrinks to ~$1,587 (vs ~$6,460 platform-only)",
      abs(_cap_locked - (140.0 * 0.34 / 0.03)) < 1.0, _cap_locked)
check("the guard's worst case still eats only ~34% of the LOCK buffer",
      abs(_cap_locked * 0.03 - 140.0 * 0.34) < 0.5)
check("under the lock floor: NO new risk (cap 0)",
      ca.max_entry_usd(10200.0, 3.0, 10000.0,
                       state=state(10200.0, day_start=10000.0, peak=10400.0)) == 0.0)
check("at the platform line (no lock): still 0 — the old rule unchanged",
      ca.max_entry_usd(9700.0, 3.0, 10000.0) == 0.0)

# ── 4. the day rolls + the peak ratchets ─────────────────────────────
import os
_bak = ca.STATE_FILE.read_text(encoding="utf-8") if ca.STATE_FILE.exists() else None
try:
    # a known prev state (yesterday's close = 10,000) so the assertions are
    # deterministic regardless of the live file
    ca._write_state({"tier": 10000, "start_balance": 10000.0,
                     "current_balance": 10000.0,
                     "updated_at": datetime.now(MST).isoformat()})
    s = ca.save_state(10000, 10050.0)
    check("save_state creates the daily fields (the balance-write chokepoint)",
          s.get("daily_peak_date") == TODAY
          and abs(float(s.get("daily_start_balance") or 0) - 10000.0) < 0.01
          and abs(float(s.get("daily_peak_balance") or 0) - 10050.0) < 0.01)
    s2 = ca.save_state(10000, 10020.0)   # balance DROPS — the peak holds
    check("a balance drop never lowers the peak (the lock ratchets one way)",
          abs(float(s2.get("daily_peak_balance") or 0) - 10050.0) < 0.01)
    s3 = ca.save_state(10000, 10080.0)   # balance RISES — the peak follows
    check("a balance rise ratchets the peak",
          abs(float(s3.get("daily_peak_balance") or 0) - 10080.0) < 0.01)
    # a stale day rolls over on the next touch
    _stale = dict(s3)
    _stale["daily_peak_date"] = "2026-09-03"
    _stale["daily_peak_balance"] = 10500.0
    ca._write_state(_stale)
    s4 = ca.touch_daily_peak()
    check("a new Denver day resets the lock (fresh room today)",
          s4.get("daily_peak_date") == TODAY
          and abs(float(s4.get("daily_start_balance")) - 10080.0) < 0.01
          and abs(float(s4.get("daily_peak_balance")) - 10080.0) < 0.01)
    check("touch is idempotent (a second touch changes nothing)",
          ca.touch_daily_peak() == s4)
finally:
    if _bak is not None:
        ca.STATE_FILE.write_text(_bak, encoding="utf-8")
    elif ca.STATE_FILE.exists():
        os.remove(ca.STATE_FILE)

# ── 5. the wiring — every guard keys off the effective buffer ────────
sc = Path("background_scanner.py").read_text(encoding="utf-8")
ap = Path("app.py").read_text(encoding="utf-8")
check("the light's hard stop uses the effective buffer",
      "_ca_l.effective_buffer(_acct_l) * 0.5" in sc)
check("the light speaks the lock when it ratchets",
      "The floor moves to" in sc and "DAILY LOCK" in sc)
check("the light touches the peak every pass (the day rolls even without trades)",
      "_ca_dl.touch_daily_peak()" in sc)
check("send_alert's guard touches the peak + names the binding floor",
      "_ca.touch_daily_peak()" in sc and "binding_floor(_acct)" in sc)
check("the popup's SPEND line names BOTH guards when the lock binds",
      "THE 3% GUARD + THE DAILY LOCK" in sc)
check("the mover popup's guard is lock-aware",
      "binding_floor(_m_acct)" in sc and "state=_m_acct" in sc)
check("the QC guard is lock-aware + stores the binding floor",
      "state=_acct" in ap and '"binding_floor"' in ap)
check("the QC's no-green names WHICH floor binds",
      "{str(result.get('binding_floor') or 'PLATFORM').upper()}" in ap
      and "NO GREEN — AT THE" in ap)
check("the QC's at-the-line toast has the daily-lock branch",
      "AT THE DAILY LOCK" in ap and "Protect the day's progress" in ap)
check("the exit check's stop uses the effective buffer",
      "_ca_ec.effective_buffer(_acct_ec) * 0.5" in ap)
check("the master panel shows the DAY FLOOR when the lock is active",
      "DAY FLOOR" in ap)
check("the death-line functions are untouched (the platform model stands)",
      "def death_line" in Path("contest_account.py").read_text(encoding="utf-8")
      and '"start_balance"' in Path("contest_account.py").read_text(encoding="utf-8"))

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
Path("_dailylock_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("DONE")
