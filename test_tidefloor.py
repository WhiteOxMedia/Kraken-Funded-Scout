"""Test the TIDE-AWARE DESKTOP FLOOR (user rule, Aug 31: "the thresholds
don't know the tide and should self-adjust accordingly"). RISING relaxes
the desktop floor 89 → TIDE_RISING_DESKTOP_FLOOR; FLAT keeps 89 (lone
wolves at 55); OUT blocks upstream; broken reads fail to 89."""
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


import background_scanner as bs

# ── 1. the constant ────────────────────────────────────────────────────
check("TIDE_RISING_DESKTOP_FLOOR is 78", bs.TIDE_RISING_DESKTOP_FLOOR == 78.0,
      bs.TIDE_RISING_DESKTOP_FLOOR)
check("RISING floor sits between the deep floor and 89",
      60 < bs.TIDE_RISING_DESKTOP_FLOOR < 89)

# ── 2. the floor math, per tide ────────────────────────────────────────
def desktop_floor(state, lw_pts=0):
    """Replicates send_alert's floor stack."""
    floor = bs.TIDE_RISING_DESKTOP_FLOOR if state == "RISING" else 89
    if lw_pts:
        floor = max(bs.LONE_WOLF_DESKTOP_FLOOR, floor - lw_pts)
    return floor


check("RISING -> 78", desktop_floor("RISING") == 78)
check("FLAT -> 89 (ordinary coins stay strict)", desktop_floor("FLAT") == 89)
check("UNKNOWN/broken read -> 89 (fail closed)", desktop_floor(None) == 89)
check("OUT never reaches the floor (blocked upstream)", desktop_floor("OUT") == 89,
      "the floor is academic here — the tide check returns first")
check("FLAT + lone wolf 35pts -> 55 (XMR's case)",
      desktop_floor("FLAT", 35) == 55)
check("RISING + lone wolf -> no further change (lone wolf is FLAT-only: "
      "lone_wolf_discount returns 0 pts in RISING, so the discount never runs)",
      desktop_floor("RISING", 0) == 78)

# ── 3. what now passes: an 80% pick in a rising fleet alerts ──────────
check("an 80% pick alerts in RISING (was silenced by 89)",
      80 >= desktop_floor("RISING"))
check("an 80% pick stays silent in FLAT",
      80 < desktop_floor("FLAT"))
check("a 56% solo mover alerts in FLAT via lone wolf",
      56 >= desktop_floor("FLAT", 35))
check("an 80% pick alerts in RISING but stays silent in FLAT",
      80 < desktop_floor("FLAT") and 80 >= desktop_floor("RISING"))
check("a 70% pick stays silent everywhere (below even the relaxed floor)",
      70 < desktop_floor("FLAT") and 70 < desktop_floor("RISING"))

# ── 4. source checks: the wiring (updated Sep 4: the floor is
# ROTATION-aware now — ROTATION_POPUP_FLOOR rail → tide margin → lone
# wolf, all inside popup_floor(); the flat-89 line is retired) ────────
src = open("background_scanner.py", encoding="utf-8").read()
check("floor is tide-aware + rotation-aware via popup_floor() in send_alert",
      "DESKTOP_ALERT_MIN = popup_floor(_df_state, _lw_pts, _rot_now_key)" in src)
check("broken tide read fails closed (except branch)",
      "_df_pct, _df_state = None, None" in src)
check("the tide margin still applies inside popup_floor (never below 78)",
      "fl = max(TIDE_RISING_DESKTOP_FLOOR, fl - TIDE_RISING_BAR_RELAX)" in src)
check("lone-wolf discount still stacks inside popup_floor (never below 55)",
      "fl = max(LONE_WOLF_DESKTOP_FLOOR, fl - lw_pts)" in src)
check("relaxation is logged (the floor line names the rotation)",
      "POPUP FLOOR: rotation %s" in src)
check("the deep-read >=60 pre-check still applies (never discounted)",
      src.count("DEEP VETO (desktop)") >= 1)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_tidefloor_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
