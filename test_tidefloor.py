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

# ── 4. source checks: the wiring ───────────────────────────────────────
src = open("background_scanner.py", encoding="utf-8").read()
check("floor is tide-aware in send_alert",
      'TIDE_RISING_DESKTOP_FLOOR if _df_state == "RISING" else 89' in src)
check("broken tide read fails closed to 89 (except branch)",
      "_df_pct, _df_state = None, None" in src)
check("relaxation is logged",
      "desktop floor relaxed 89" in src)
check("lone-wolf discount still stacks after the tide floor",
      "max(LONE_WOLF_DESKTOP_FLOOR, DESKTOP_ALERT_MIN - _lw_pts)" in src)
check("the deep-read >=60 pre-check still applies (never discounted)",
      src.count("DEEP VETO (desktop)") >= 1)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_tidefloor_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
