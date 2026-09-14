"""Test the CONFIRMATION WINDOW (user rule, Aug 31 — the 'Already ran'
collision): the champion alert marks the rotation when it fires; the QC
and the card check the same gate; so the pick the alert JUST made read
"Already ran! Cold!" minutes later. Within ROTATION_CONFIRM_WINDOW_MIN,
the QC and the card treat the lockout as CONFIRMING THE LIVE ALERT —
allowed, reason names the alert's time.

THE LOCKOUT RETIRED (user rule, Sep 10): the gate ALWAYS allows now —
so the strict-branch checks below pin the RETIREMENT (no "already ran"
verdict can come back), and the confirm-window wording survives only as
the QC's confirming-green display marker."""
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
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


import background_scanner as bs

MST = ZoneInfo("America/Denver")


def set_lockout(asset, minutes_ago, rotation=None):
    """Write a lockout record as if the alert fired N minutes ago."""
    now = datetime.now(MST)
    rot = rotation or bs._rotation_window(now)[2]
    rec = {"date": now.strftime("%Y-%m-%d"), "rotation": rot,
           "fired_at": (now - timedelta(minutes=minutes_ago)).isoformat()}
    state = {asset: rec}
    tmp = bs.ROTATION_LOCKOUT_FILE + ".test"
    Path(tmp).write_text(json.dumps(state), encoding="utf-8")
    return tmp


# ── 1. the window math (sandbox the lockout file) ────────────────────
_real_file = bs.ROTATION_LOCKOUT_FILE
import os

tmp = set_lockout("TESTA", minutes_ago=6)
bs.ROTATION_LOCKOUT_FILE = tmp
try:
    ok, reason, re_, fine = bs.rotation_gate("TESTA")
    check("THE LOCK IS RETIRED: fired 6 min ago -> ALLOWED, no strict verdict",
          ok is True and "already ran" not in reason, reason)
    check("...flagged as a re-entry (same fire, but the ledger says so)",
          re_ is True)

    ok, reason, re_, fine = bs.rotation_gate("TESTA", confirm_window_min=15)
    check("WINDOW: locked 6 min ago -> ALLOWED (confirming the live alert)",
          ok is True, reason)
    check("the reason names the alert's time and age",
          "fired at" in reason and "min ago" in reason, reason)
    check("not flagged as a re-entry (it's the SAME fire)",
          re_ is False and fine == 0.0)

    ok, reason, re_, fine = bs.rotation_gate("TESTA", confirm_window_min=15)
    check("the HYPE pattern: 6 min after 'Buy HYPE!' the QC confirms it",
          ok is True and "confirming" in reason)
finally:
    bs.ROTATION_LOCKOUT_FILE = _real_file
    os.remove(tmp)

tmp = set_lockout("TESTB", minutes_ago=20)
bs.ROTATION_LOCKOUT_FILE = tmp
try:
    ok, reason, re_, fine = bs.rotation_gate("TESTB", confirm_window_min=15)
    check("WINDOW expired: 20 min old -> ALLOWED anyway (the cap is off), re-entry",
          ok is True and re_ is True, reason)
finally:
    bs.ROTATION_LOCKOUT_FILE = _real_file
    os.remove(tmp)

tmp = set_lockout("TESTC", minutes_ago=6)
bs.ROTATION_LOCKOUT_FILE = tmp
try:
    ok, reason, re_, fine = bs.rotation_gate("TESTC", confirm_window_min=0)
    check("confirm_window_min=0: allowed as a plain re-entry (nothing blocks)",
          ok is True and re_ is True)
finally:
    bs.ROTATION_LOCKOUT_FILE = _real_file
    os.remove(tmp)

check("no lockout -> fresh (unchanged)",
      bs.rotation_gate("NEVERSEEN") == (True, "", False, 0.0))

# ── 2. the wiring: who gets the window, who stays strict ─────────────
sc = open("background_scanner.py", encoding="utf-8").read()
ap = open("app.py", encoding="utf-8").read()
check("constant exists and is 15 min", bs.ROTATION_CONFIRM_WINDOW_MIN == 15.0)
check("QC passes the window",
      "confirm_window_min=getattr(_bs5, \"ROTATION_CONFIRM_WINDOW_MIN\", 0.0)" in ap)
check("card filter passes the window (the pick stays visible for CHECK)",
      "_bs6.rotation_gate(h[\"asset\"], confirm_window_min=_cwin)" in ap)
check("QC + card reload on the new marker (old console processes)",
      ap.count('hasattr(_bs5, "ROTATION_CONFIRM_WINDOW_MIN")') >= 1
      and ap.count('hasattr(_bs6, "ROTATION_CONFIRM_WINDOW_MIN")') >= 1)
check("the scanner's send_alert lock branch is retired-marked (kept, dead)",
      0 < sc.find("ROTATION LOCK: %s %s") < len(sc)
      and "dead since the Sep 10 retirement" in sc)
check("the mover funnel's lockout hook is retired; its tide hook stands",
      "MOVER SKIP: %s — %s" not in sc and "MOVER SKIP: %s — tide OUT" in sc)

# ── 3. the QC verdict carries the confirmation ───────────────────────
check("GO toast says it's confirming the alert",
      "🔁 {_rot_reason}" in ap and "_confirm_txt" in ap)
check("the RE-ENTRY/FRESH LED gains the CONFIRMING state",
      "🔁 CONFIRMING THE ALERT" in ap)
check("'Already ran' rejection display kept (dead since the retirement, honest machinery)",
      "⛔ ALREADY RAN THIS ROTATION" in ap and "cold until the next rotation" in ap)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_confirmwindow_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
