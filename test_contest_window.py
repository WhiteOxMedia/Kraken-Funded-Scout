"""Test the contest WINDOW (user rule, Sep 9): "the contest account
balance just shows what contest we are in now ... the current contest
balance self-adjusts but knows the contest window it's in".

The engine rules graded here, against a TEMP state file:
  - the account file carries contest_id (the window stamp)
  - new_contest() is the ONLY path that resets the balance, and it
    stamps a FRESH id (a stale console can no longer re-fire a reset by
    re-rendering -- the Sep 9 bug that snapped the 1k account back to a
    phantom 10k and re-widened the sizing gates)
  - save_state() NEVER touches the window stamp
  - apply_pnl() lands a trade on ITS OWN window's balance; a foreign-
    window trade is refused (logged, not applied); a legacy record with
    no stamp applies as before (the 77-row convention: no stamp = the
    world it ran in)
Run: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe test_contest_window.py
"""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import contest_account as ca

# Redirect the state file BEFORE anything reads it (import time already
# bound the name; rebind and every helper sees the temp file).
_tmpdir = tempfile.mkdtemp(prefix="_contestwin_")
STATE = Path(_tmpdir) / "contest_account.json"
ca.STATE_FILE = STATE

lines = []
PASS = FAIL = 0


def check(label, ok):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + label)
    if ok:
        PASS += 1
    else:
        FAIL += 1


def read_state():
    return json.loads(STATE.read_text(encoding="utf-8"))


# ── 1. the window stamp ───────────────────────────────────────────────

s = ca.new_contest(1000)
check("new_contest(1000) starts a 1k window at the tier start",
      s["tier"] == 1000 and s["current_balance"] == 1000.0)
check("the window carries a contest_id stamp",
      bool(s.get("contest_id")))

# ── 2. P&L lands on its OWN window ────────────────────────────────────

here = ca.current_contest_id()
applied, bal, note = ca.apply_pnl(-45.78, here)
check("this window's loss moves the balance",
      applied and abs(bal - (1000.0 - 45.78)) < 1e-9)
applied, bal, _ = ca.apply_pnl(20.0, here)
check("this window's profit moves it too",
      applied and abs(bal - 974.22) < 1e-9)

# ── 3. a FOREIGN window's trade never touches the balance ─────────────

applied, bal, note = ca.apply_pnl(-500.0, "2026-09-06T09:00:00-06:00")
check("an old contest's loss is REFUSED (logged, not applied)",
      (not applied) and abs(bal - 974.22) < 1e-9 and "another contest" in note)
applied, bal, note = ca.apply_pnl(5000.0, "2026-09-06T09:00:00-06:00")
check("an old contest's PROFIT is refused just the same",
      (not applied) and abs(bal - 974.22) < 1e-9)

# ── 4. legacy records (no stamp) apply as before ──────────────────────

applied, bal, note = ca.apply_pnl(10.0, None)
check("a legacy record (no contest_id) still applies -- the world it ran in",
      applied and abs(bal - 984.22) < 1e-9)

# ── 4b. REPLAY SAFETY (Copilot, Sep 9): the same trade cannot land twice

applied, bal, note = ca.apply_pnl(25.0, here, dedupe_key="TIA/USD|2026-09-09T10:00")
check("a deduped trade applies once",
      applied and abs(bal - 1009.22) < 1e-9)
applied, bal, note = ca.apply_pnl(25.0, here, dedupe_key="TIA/USD|2026-09-09T10:00")
check("...and a REPLAYED key is refused (two consoles can't double-book)",
      (not applied) and "already applied" in note
      and abs(bal - 1009.22) < 1e-9)
applied, bal, _ = ca.apply_pnl(5.0, here, dedupe_key="OTHER/USD|2026-09-09T10:05")
check("a different trade's key still applies",
      applied and abs(bal - 1014.22) < 1e-9)

# ── 5. save_state never resets the window ─────────────────────────────

ca.save_state(1000, 1234.0)
st = read_state()
check("a manual balance save keeps the SAME contest_id",
      st["contest_id"] == here and abs(st["current_balance"] - 1234.0) < 1e-9)
check("a manual save keeps the same start_balance (the death line stands)",
      abs(st["start_balance"] - 1000.0) < 1e-9)

# ── 6. a NEW contest resets balance + stamp + Hail Mary ───────────────

# seed a Hail Mary window so we can prove the reset clears it
ca.save_state(1000, 500.0)
st = read_state()
st["hail_mary_until"] = datetime.now(ca.MST).isoformat()
st["hail_mary_count"] = 2
ca._write_state(st)

s2 = ca.new_contest(10000)
check("new_contest(10000) resets to the FULL 10k start",
      s2["current_balance"] == 10000.0 and s2["start_balance"] == 10000.0)
check("the new window stamps a FRESH contest_id",
      s2["contest_id"] != here)
st = read_state()
check("the new window clears the old Hail Mary state",
      not st.get("hail_mary_until") and not st.get("hail_mary_count"))
check("the old window's trades are now foreign (refused)",
      ca.apply_pnl(-1.0, here)[0] is False)

# ── 7. the death line follows the WINDOW, not a stale console ─────────

s3 = ca.new_contest(1000)
ca.save_state(1000, 980.0)
check("the 1k death line is start*0.97 = $970 (this window's line)",
      abs(ca.death_line() - 970.0) < 1e-9)
check("buffer is measured from THIS window's start",
      abs(ca.buffer() - 10.0) < 1e-9)

# ── 8. tier validation: garbage falls back to the default ─────────────

s4 = ca.new_contest(999999)
check("an unknown tier falls back to the default tier (10k)",
      s4["tier"] == ca.DEFAULT_TIER)

# ── 9. THE APP WIRING (structure checks on app.py's source) ──────────

app_src = (BASE / "app.py").read_text(encoding="utf-8")
check("the tier select fires the reset ONLY on user interaction (on_change)",
      "on_change=_on_tier_change" in app_src
      and 'key="challenge_tier"' not in app_src)
check("the tier widget key is WINDOW-SCOPED (a new contest remounts it fresh)",
      "_tier_key = f\"challenge_tier_" in app_src)
check("the balance input has NO explicit key (a stale widget could undo a booked trade)",
      "key=_bal_key" not in app_src)
check("the SOLD pass dedupes on pair + added stamp (replay-safe)",
      'dedupe_key=f"{_sp}|{_sw.get(\'added\')}"' in app_src)
check("the old rerun-reset block is GONE (no 'if tier != _cs.get' reset)",
      'if tier != _cs.get("tier"):' not in app_src)
check("the SOLD path routes through the window-aware apply_pnl seam",
      "_ca_s.apply_pnl(\n" in app_src.replace("\r\n", "\n")
      or "apply_pnl(" in app_src)
check("the SOLD pass carries the trade's own window stamp",
      '_sw.get("contest_id")' in app_src)
check("the ledger row records whether the balance actually moved",
      '"applied": bool(_applied)' in app_src)
check("both watch-add sites stamp the contest window",
      app_src.count('"contest_id": _ca.current_contest_id()') == 2)
check("a foreign-window sale speaks honestly (balance untouched)",
      "OLD-CONTEST trade" in app_src)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
out = BASE / "_contestwindow_test.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines[-3:]))
sys.exit(1 if FAIL else 0)
