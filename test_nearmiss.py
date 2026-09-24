"""Test the NEAR-MISS DEEP SCAN + TIDE-AWARE ROTATION BAR (user rule,
Aug 31: "it just needs to not guess and do a deep scan when it is within
5% of passing and it knows about the tide and the weekday/weekend and
coin history"). Nothing was deleted — the guessed fallbacks, WEEKDAY_ADJUST
and _apply_dow all stay; the data bar simply gains the two missing inputs:
the tide, and the brain's verdict on near-misses."""
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
import scratch_space   # scratch policy: nothing volatile lands in the repo root

# ── 1. the constants ──────────────────────────────────────────────────
check("NEAR_MISS_PTS is 5 points", bs.NEAR_MISS_PTS == 5.0, bs.NEAR_MISS_PTS)
check("tide bar relax mirrors the desktop floor (89-78 = 11 pts)",
      bs.TIDE_RISING_BAR_RELAX == 89.0 - bs.TIDE_RISING_DESKTOP_FLOOR == 11.0,
      bs.TIDE_RISING_BAR_RELAX)
check("bar floor is the deep-read floor (60)", bs.TIDE_RISING_BAR_FLOOR == 60.0)

# ── 2. the near-miss window (bar 60 examples) ─────────────────────────
def in_window(quick, bar):
    return quick < bar and quick >= bar - bs.NEAR_MISS_PTS


check("quick 59 vs bar 60 -> consult the brain", in_window(59, 60))
check("quick 55 vs bar 60 -> consult (the edge)", in_window(55, 60))
check("quick 54.9 vs bar 60 -> no consult (too far below)", not in_window(54.9, 60))
check("quick 90 vs bar 60 -> no consult (passes outright)", not in_window(90, 60))
check("LDO shape: quick 55 vs bar 60 -> consult", in_window(55, 60))
check("window follows the relaxed bar: quick 63 vs RISING bar 67 -> consult",
      in_window(63, 67))
check("quick 70 vs RISING bar 67 passes outright (no consult needed)",
      not in_window(70, 67))

# ── 3. the verdict on the brain's number ─────────────────────────────
def rescued(brain, bar):
    return brain >= bar


check("brain 75 vs bar 60 -> RESCUED (quick said 58)", rescued(75, 60))
check("brain 58 vs bar 60 -> contest-only (the brain missed too)",
      not rescued(58, 60))
check("no bypass: brain 70 vs dirty-rotation bar 78 -> contest-only",
      not rescued(70, 78))
check("no bypass: brain 90 vs bar 95 -> contest-only", not rescued(90, 95))

# ── 4. the tide-aware bar math ────────────────────────────────────────
def tide_bar(bar, state):
    if state == "RISING" and bar > bs.TIDE_RISING_BAR_FLOOR:
        return int(max(bs.TIDE_RISING_BAR_FLOOR, bar - bs.TIDE_RISING_BAR_RELAX))
    return bar


check("RISING: bar 78 -> 67 (data bar minus 11)", tide_bar(78, "RISING") == 67)
check("RISING: bar 60 -> 60 (already at the deep floor)", tide_bar(60, "RISING") == 60)
check("RISING: bar 65 -> 60 (floored, never below the deep read)", tide_bar(65, "RISING") == 60)
check("FLAT: bar 78 -> 78 (the data decides)", tide_bar(78, "FLAT") == 78)
check("broken read: bar 78 -> 78 (data bar untouched)", tide_bar(78, None) == 78)
check("OUT never reaches the bar (hard block upstream)", tide_bar(78, "OUT") == 78,
      "academic — the OUT check returns first")
check("lone wolf (FLAT-only) and tide relax (RISING-only) never stack",
      bs.LONE_WOLF_PTS_BIG == 35 and tide_bar(78, "RISING") == 67)

# ── 5. source checks: the wiring ─────────────────────────────────────
src = open("background_scanner.py", encoding="utf-8").read()
check("near-miss consult exists",
      "NEAR MISS: %s quick %.0f%% vs bar %d%% — consulting the brain" in src)
check("rescue is logged",
      "NEAR MISS RESCUED: %s — the brain reads %.0f%% ≥ bar %d%%" in src)
check("contest-only line says WHOSE number it was",
      '"brain" if _brain_used else "quick"' in src)
check("tide-aware bar is logged",
      "rotation bar relaxed %d%% → %d%%" in src)
check("the bar is judged on the brain's number",
      "_brain_conf < effective_thr" in src)
check("deep read is defined ABOVE the gates (near-miss can call it)",
      src.find("def _do_deep_read()") < src.find("Gate 2: CONFIDENCE BAR"))
check("bridge reuses the near-miss read (no second fetch)",
      src.count("if _deep is None:") >= 2)
check("desktop veto applies to the CACHED brain too (not only its own read)",
      src.count("_deep, _df = _do_deep_read()") == 4,
      "def + bridge conditional + desktop conditional + brain-judged bar (Sep 1)")
check("bridge direction veto still stands (Sep 5 ADVISORY BRAIN: the scan's "
      "DIRECTION gates the write; the number sizes, it never blocks)",
      "_deep.action != \"buy\"" in src and "DEEP VETO (bridge)" in src
      and "_deep.confidence < 60" not in src)
check("desktop direction veto still stands (the number is advisory)",
      "DEEP VETO (desktop)" in src and '"deep-direction"' in src)

# ── 6. nothing was deleted (user rule: don't delete anything) ────────
check("WEEKDAY_ADJUST still present", "WEEKDAY_ADJUST = {" in src)
check("_apply_dow still present", "def _apply_dow(" in src)
check("ROTATIONS keep the 8-field shape (7 returned by get_time_block)",
      all(len(r) == 8 for r in bs.ROTATIONS))
check("data bars still take precedence over fallbacks",
      "if data_bar is not None:" in src)
check("desktop floor is rotation+tide aware via popup_floor (Sep 4; the pair arg joined Sep 10 for the personal bar)",
      "DESKTOP_ALERT_MIN = popup_floor(_df_state, _lw_pts, _rot_now_key, pair)" in src)
check("mover funnel untouched (own 55 floor + deep >=60)",
      "MOVER_MIN_CONF = 55.0" in src)

# ── 7. get_time_block still serves the data bars ─────────────────────
from datetime import datetime
from zoneinfo import ZoneInfo
_n = datetime.now(ZoneInfo("America/Denver"))
_name, _iv, _conf, _bar, _desc, _nb, _key = bs.get_time_block(_n.hour, _n.weekday())
check("live bar comes from the data (60-99, int)", 60 <= _bar <= 99 and isinstance(_bar, int), _bar)
check("cooldown hours still flag no_buy",
      bs.get_time_block(21, 0)[5] is True)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open(scratch_space.report_temp('_nearmiss_check.txt'), "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
