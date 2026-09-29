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
import scratch_space   # scratch policy: nothing volatile lands in the repo root

# Redirect the state file BEFORE anything reads it (import time already
# bound the name; rebind and every helper sees the temp file).
# (testleaks triage, Sep 23: deliberate pre-import-world swap with no
# restore — the suite-per-process model bounds it, and the suite's whole
# purpose is exercising the state machine, never the live account.)
_tmpdir = tempfile.mkdtemp(prefix="_contestwin_")
STATE = Path(_tmpdir) / "contest_account.json"
ca.STATE_FILE = STATE  # leak-audit: ok (tempfile-derived sandbox; the suite exercises the state machine, never the live account)
# the LOCK file binds at import from the real STATE_FILE -- rebind it too,
# or the suite would lock/unlock the LIVE repo's lockfile
cr_lock = Path(_tmpdir) / "contest_account.json.lock"
ca.STATE_LOCK_FILE = cr_lock  # leak-audit: ok (tempfile-derived sandbox; the rebind is deliberate — see comment above)

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

# (Sep 27 ledger-leak fix) the WIPE must follow the SANDBOX: seed a canary
# row in the sandbox watch file. Before the fix, new_contest's wipe was
# hardcoded to the repo root, so this suite's new_contest() calls wiped
# the LIVE watch ledger — the OP/ONDO rows the battery's prod-guard
# caught vanishing with no close receipts.
_watch_sb = ca.STATE_FILE.parent / "contest_watch.json"
_watch_sb.write_text(json.dumps({"CANARY/USD": {"entry": 1.0}}),
                     encoding="utf-8")
# snapshot the live repo watch (ms window — a legitimate concurrent close
# could remove a row in principle; the drive's runtime makes that a
# coin-flip at worst, and the canary check below is the deterministic pin)
_repo_watch = BASE / "contest_watch.json"
_repo_before = None
if _repo_watch.exists():
    try:
        _repo_before = _repo_watch.read_text(encoding="utf-8")
    except OSError:
        _repo_before = None

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
_cw_after = json.loads(_watch_sb.read_text(encoding="utf-8"))
check("new_contest's wipe follows the SANDBOX state file and lands "
      "TOMBSTONES, not a bare empty book — the canary is GONE from the "
      "book (a writer mid-RMW can no longer overlay its stale rows "
      "back over the wipe; None is the deletion convention the "
      "merge-save consumes)",
      bool(_cw_after)
      and all(v is None for v in _cw_after.values())
      and _cw_after.get("CANARY/USD", "MISSING") is None)
_repo_after = None
if _repo_watch.exists():
    try:
        _repo_after = _repo_watch.read_text(encoding="utf-8")
    except OSError:
        _repo_after = None
_bw = json.loads(_repo_before) if _repo_before else {}
_aw = json.loads(_repo_after) if _repo_after else {}
check("the LIVE repo watch lost no rows while the suite ran (no leak)",
      not (set(_bw) - set(_aw)))

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
check("the watch-add site stamps the contest window",
      app_src.count('"contest_id": _ca.current_contest_id()') == 1)
# Sep 22: popups are the only writer, so the SCANNER's auto-track add
# must stamp too — its own GET OUT exit judges w.get("contest_id")
bs_src_cw = (BASE / "background_scanner.py").read_text(encoding="utf-8")
check("the scanner's auto-track add stamps the window (legacy-degrade)",
      "import contest_account as _ca_at" in bs_src_cw
      and '"contest_id": _ca_stamp' in bs_src_cw)
check("a foreign-window sale speaks honestly (balance untouched)",
      "OLD-CONTEST trade" in app_src)
# (Sep 27) the wipe must resolve beside the CURRENT state file — the
# repo-root hardcode was the live-ledger leak (see section 6's canary)
ca_src_l = (BASE / "contest_account.py").read_text(encoding="utf-8")
check("the watch wipe resolves beside the CURRENT state file (no repo-root hardcode)",
      'STATE_FILE.parent / "contest_watch.json"' in ca_src_l
      and 'Path(__file__).parent / "contest_watch.json"' not in ca_src_l)

# ── 10. THE STATE LOCK (Copilot round 3, Sep 9) ───────────────────────

ca_src = (BASE / "contest_account.py").read_text(encoding="utf-8")
check("all three writers run under the state lock (save/new_contest/apply)",
      ca_src.count("with _StateLock() as _lk:") == 3)

# BUSY: a fresh LIVE holder's lock survives the retries -> honest skip
# (the fixture carries OUR pid: a dead writer's lock would be taken over
# instantly, which is the STALE test below -- that behavior is correct,
# and the first draft of this fixture got it wrong by using a dead pid)
import os as _os
import uuid as _uuid
now_iso = datetime.now(ca.MST).isoformat()
# the REAL payload format: pid|iso|uuid (the fixture must lie about
# nothing -- a bare pid|iso fixture would let an engine payload change
# slip through every lock test)
def _lock_payload(pid):
    return f"{pid}|{now_iso}|{_uuid.uuid4().hex}"
# the window mints FRESH here (s3/s4 above re-minted) and BEFORE the
# BUSY fixture locks the file: the lock tests need a CURRENT window,
# and with the raise-on-busy rule a mint under a held lock would raise
_here = ca.new_contest(1000)["contest_id"]
cr_lock.write_text(_lock_payload(_os.getpid()), encoding="utf-8")
_before = ca.load_state()["current_balance"]
applied, bal, note = ca.apply_pnl(1.0, _here)
check("a contested lock BUSY-skips honestly (logged, not applied)",
      (not applied) and "busy" in note and abs(bal - _before) < 1e-9)

# fixture hygiene: the BUSY fixture's lock file stays on disk by design
# (apply_pnl is a caller of the lock, not its owner) -- clear it, or
# the next section's new_contest hits a contested lock (the bug that
# failed the STALE + fallback checks below: the mint no-op'd, every
# later trade refused as foreign-window)
cr_lock.unlink(missing_ok=True)
# a busy USER action (new contest / balance edit) RAISES -- it must
# never drop silently (the caller says "try again"; the old silent
# skip returned a state that was never written)
cr_lock.write_text(_lock_payload(_os.getpid()), encoding="utf-8")
_busy_raised = False
try:
    ca.new_contest(1000)
except RuntimeError:
    _busy_raised = True
check("a busy new_contest RAISES (user actions never drop silently)",
      _busy_raised)
cr_lock.unlink(missing_ok=True)

# STALE: a dead run's lock ages out -> takeover, the trade lands
old_iso = datetime.now(ca.MST).isoformat()
cr_lock.write_text(f"999999|2026-09-08T00:00:00-06:00|{_uuid.uuid4().hex}", encoding="utf-8")
applied, bal, note = ca.apply_pnl(3.0, _here)
check("a STALE lock is taken over (the trade applies)",
      applied and bal > _before)
check("the lockfile is released after the write",
      not ca.STATE_LOCK_FILE.exists())

# FALLBACK KEY: None dedupe keys are unique per call (never a constant)
applied, bal, _ = ca.apply_pnl(2.0, _here)
applied2, bal2, _ = ca.apply_pnl(4.0, _here)
check("missing dedupe keys stay UNIQUE per call (both trades apply)",
      applied and applied2 and abs(bal2 - (bal + 4.0)) < 1e-9)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
out = BASE / "_contestwindow_test.txt"
scratch_space.report_temp('_contestwindow_test.txt').write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines[-3:]))
sys.exit(1 if FAIL else 0)
