"""Test THE DAILY PROFIT LOCK — REMOVED (user rule, Sep 5: "remove the
daily lock and everything to do with that idea, it is causing missed
trades. leaving money on the table" — then: "Keep the 'Hail Mary'").

THE RECORD: the lock was a user-side ratcheting floor (day-start + 65%
of the day's gain) layered above the platform line. On Sep 5 it froze
the live contest: balance $9,855 under a $10,012 lock floor = negative
effective buffer = $0 max entry on every gate, both Hail Mary windows
already burned — missed trades, money left on the table. THE REMOVAL:
the platform's -3% death line (Kraken's own rule, from START, never
moves) is the ONLY floor again. THE HAIL MARY STAYS (user's call,
same day): re-anchored to account danger — it fires when the contest
is under break-even on a champion-grade setup. This suite asserts the
lock is gone everywhere and the platform model stands."""
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


import contest_account as ca
import scratch_space   # scratch policy: nothing volatile lands in the repo root

LINE = 9700.0    # the 10k platform line
ca_src = Path("contest_account.py").read_text(encoding="utf-8")
bs_src = Path("background_scanner.py").read_text(encoding="utf-8")
ap_src = Path("app.py").read_text(encoding="utf-8")

# ── 1. the lock's API is GONE (the Hail Mary STAYS — user, Sep 5:
# "Keep the 'Hail Mary'") ──────────────────────────────────────────────
for _fn in ("daily_lock_floor", "effective_buffer", "binding_floor",
            "touch_daily_peak"):
    check(f"contest_account.{_fn} removed", not hasattr(ca, _fn))
check("DAILY_PROTECT_FRAC removed", not hasattr(ca, "DAILY_PROTECT_FRAC"))
check("the lock's bite-fraction constant removed "
      "(the Hail Mary window constant STAYS)",
      not hasattr(ca, "HAIL_MARY_LOCK_BITE_FRAC")
      and ca.HAIL_MARY_WINDOW_MIN == 60)
check("the Hail Mary functions are back (kept, lock-free)",
      all(hasattr(ca, _f) for _f in ("hail_mary_active", "hail_mary_available",
                                     "activate_hail_mary", "hail_mary_minutes_left")))

# ── 2. THE PLATFORM MODEL STANDS (the only floor) ─────────────────────
check("death_line: -3% from start, never moves",
      ca.death_line({"tier": 10000, "start_balance": 10000.0,
                     "current_balance": 10400.0}) == LINE)
check("buffer: balance - line",
      abs(ca.buffer({"tier": 10000, "start_balance": 10000.0,
                     "current_balance": 10400.0}) - 700.0) < 0.01)
# THE SEP 5 CASE: a day that peaked +$18 then fell to $9,855 — the lock
# would have read a NEGATIVE buffer; the platform math reads $155 of room
_sept5 = {"tier": 10000, "start_balance": 10000.0,
          "current_balance": 9855.11,
          # legacy lock fields still in the JSON are IGNORED
          "daily_peak_date": "2026-09-05", "daily_start_balance": 10000.69,
          "daily_peak_balance": 10018.61,
          "hail_mary_until": "2026-09-05T14:48:20-06:00",
          "hail_mary_count": 2}
check("THE SEP 5 CASE: legacy lock fields in state are ignored — "
      "buffer = $155.11 platform room (not negative)",
      abs(ca.buffer(_sept5) - 155.11) < 0.01)
check("THE SEP 5 CASE: the entry guard sizes from the platform line "
      "(~$1,758 at a 3% drawdown, not $0)",
      abs(ca.max_entry_usd(9855.11, 3.0, 10000.0) - 1757.95) < 1.0)
check("at the platform line = $0 (the death line never yields)",
      ca.max_entry_usd(9700.0, 2.0, 10000.0) == 0.0
      and ca.max_entry_usd(9500.0, 2.0, 10000.0) == 0.0)
check("a good day up at $10,400 sizes off $700 of platform room "
      "(the peak never tightened it)",
      abs(ca.max_entry_usd(10400.0, 3.0, 10000.0) - 7933.0) < 1.0)

# ── 3. save_state scrubs the legacy lock fields (the Hail Mary fields ride) ──
import tempfile, json, os
with tempfile.TemporaryDirectory() as _td:
    _old = ca.STATE_FILE
    try:
        ca.STATE_FILE = Path(_td) / "contest_account.json"
        ca.STATE_FILE.write_text(json.dumps(_sept5), encoding="utf-8")
        s = ca.save_state(10000, 9855.11)
        on_disk = json.loads(ca.STATE_FILE.read_text(encoding="utf-8"))
        check("the daily-lock keys are scrubbed on the next write",
              "daily_peak_date" not in on_disk
              and "daily_start_balance" not in on_disk
              and "daily_peak_balance" not in on_disk,
              sorted(on_disk.keys()))
        check("the Hail Mary window fields ride along (kept)",
              on_disk.get("hail_mary_count") == 2
              and "hail_mary_until" in on_disk)
    finally:
        ca.STATE_FILE = _old

# ── 4. the scanner is lock-free ───────────────────────────────────────
check("no daily-lock speech pass in the light",
      "Up {_gain_dl" not in bs_src and "_ca_dl" not in bs_src)
check("the light's hard stop = half the PLATFORM buffer",
      "_ca_l.buffer(_acct_l) * 0.5" in bs_src
      and "effective_buffer" not in bs_src)
check("send_alert reads load_state (no peak touch)",
      "_acct = _ca.load_state()" in bs_src
      and "touch_daily_peak" not in bs_src)
check("the popup's guard line names only THE 3% GUARD",
      "THE 3% GUARD: this coin's worst-case" in bs_src
      and "THE 3% GUARD + THE DAILY LOCK" not in bs_src)
check("the death-line suppression gate stays (the platform block lives)",
      '"death-line"' in bs_src)
check("no daily-lock suppression gate remains",
      '"daily-lock"' not in bs_src and "DAILY LOCK" not in bs_src)
check("the mover path: no lock state, platform guard only",
      "state=_m_acct" not in bs_src and "binding_floor" not in bs_src)

# ── 5. the console is lock-free (the Hail Mary plates stay) ───────────
check("the NOW strip has no DAY FLOOR plate",
      "DAY FLOOR" not in ap_src)
check("the QC's at-the-line verdict names THE DEATH LINE",
      "NO GREEN — AT THE DEATH LINE" in ap_src)
check("the QC's at-the-line toast is death-line only (no lock branch)",
      "AT THE DEATH LINE" in ap_src
      and "AT THE DAILY LOCK" not in ap_src
      and "Protect the day's progress" not in ap_src)
check("no 'the lock yields' language anywhere (the Hail Mary speaks the "
      "emergency hour now)",
      "the lock yields" not in ap_src and "the lock yields" not in bs_src)
check("the exit check's stop = half the PLATFORM buffer",
      "_ca_ec.buffer(_acct_ec) * 0.5" in ap_src)
check("the New Here tour names only the death line in the guards",
      "the -3% \ndeath line, the tide, the trend" in ap_src.replace("\n            ", "\n")
      or "death line, the tide, the trend" in ap_src)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
scratch_space.report_temp('_dailylock_check.txt').write_text("\n".join(lines), encoding="utf-8")
print("DONE")
