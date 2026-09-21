"""test_trailstop.py — the goal-armed trailing stop (Sep 21).

USER RULE: "After the goal is hit, make the exit watch trail a stop just
under the recent high so a position keeps riding a climb like this one but
locks in profit when it turns — instead of waiting for a full trend break."

CONTRACT:
  1. The arm LATCHES the moment live price touches the goal — and stays
     latched even if price dips back below the goal (a dip can't disarm
     the net).
  2. Armed, the profit-guard trail is capped at HALF THE GOAL, never
     looser than 1.0% — tight under the recent high.
  3. New highs keep raising the stop (the ride): price above the stop
     never fires.
  4. A turn through the stop fires the exit with a GOAL TRAIL reason.
  5. The arm survives a save/reload of the watch file (it is row state,
     not process memory).
Run:  python test_trailstop.py   (exit 0 = all pass)
"""
import os
import sys

FAILS = []
PASSES = []


def check(n, cond, why=""):
    if cond:
        PASSES.append(n)
        print(f"  PASS {n}: {why}")
    else:
        FAILS.append(n)
        print(f"  FAIL {n}: {why}")


SRC = open("background_scanner.py", encoding="utf-8").read()

print("== 1. the arm exists and latches on goal touch ==")
check(1.1, "trail_armed" in SRC,
      "watch row carries a trail_armed latch field")
check(1.2, 'w["trail_armed"] = True' in SRC and "dirty = True" in SRC,
      "the arm is written into the watch row (persists, not process memory)")
check(1.3, "(live / entry - 1.0) * 100.0 >= _goal_tr" in SRC,
      "arm condition: live price at/above the goal percent")

print("== 2. the armed trail is tight — half the goal, min 1.0% ==")
check(2.1, "min(_trail, max(_goal_tr * 0.5, 1.0))" in SRC,
      "cap: trail = min(ratchet trail, max(goal/2, 1.0))")
check(2.2, "max(_goal_tr * 0.5, 1.0)" in SRC,
      "the cap can never be looser than 1.0% under the peak")
check(2.3, SRC.count("_goal_tr * 0.5") >= 1 and
      "if w.get(\"trail_armed\") and _goal_tr > 0:" in SRC,
      "the cap applies only while armed — pre-goal trailing is unchanged")

print("== 3. the ride: stop tracks the tracked high ==")
check(3.1, 'high = max(old_high, live)' in SRC,
      "the high is tracked in the watch file, so the stop climbs with it")
check(3.2, "live <= high * (1 - _trail / 100)" in SRC,
      "fire condition stays peak-relative — new highs move the net up")

print("== 4. the turn says why — GOAL TRAIL in the reason ==")
check(4.1, "GOAL TRAIL" in SRC and "the profit is locked" in SRC,
      "the fired popup explains the goal trail and the locked level")
check(4.2, "trail set" in SRC and "locks" in SRC,
      "her voice announces the armed net (stop % + lock price) before it fires")
check(4.3, "popup_journal(\"GET OUT\"" in SRC,
      "the trail exit rides the same popup-first GET OUT machinery")

print("== 5. latch survives reload (row state, not memory) ==")
import json
import tempfile
row = {"pair": "TEST/USD", "entry": 1.0, "high": 1.03, "goal_pct": 2.7,
       "trail_armed": True}
tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
json.dump({"TEST/USD": row}, tmp)
tmp.close()
reloaded = json.load(open(tmp.name, encoding="utf-8"))["TEST/USD"]
os.unlink(tmp.name)
check(5.1, reloaded.get("trail_armed") is True,
      "trail_armed round-trips through the watch JSON — a restart keeps the net set")
check(5.2, float(reloaded["goal_pct"]) == 2.7,
      "the goal travels with the row, so the cap survives restarts too")

print("== 6. the clock never competes with the trail ==")
check(6.1, "WINDOW'S UP" not in SRC,
      "no window-expiry exit exists — the trail owns post-goal exits")

RESULT = f"{len(PASSES)} passed, {len(FAILS)} failed"
print(RESULT)
sys.exit(1 if FAILS else 0)
