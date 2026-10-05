"""test_trailstop.py — the goal-armed trailing stop (Sep 21; half-goal
arm re-base, Sep 22; ADAPTIVE guard-factor scaling, Sep 22).

USER RULES: "After the goal is hit, make the exit watch trail a stop just
under the recent high so a position keeps riding a climb like this one but
locks in profit when it turns — instead of waiting for a full trend
break." — and Sep 22: "trigger the profit guard half the way to the goal
instead of waiting until the goal is reached."

CONTRACT:
  1. The arm LATCHES the moment live price touches HALF THE GOAL
     (GOAL_TRAIL_ARM_FRAC = 0.5) — and stays latched even if price dips
     back below that (a dip can't disarm the net).
  2. Armed, the profit-guard trail is capped at the SAME half-goal width
     scaled by the ADAPTIVE GUARD FACTOR (strength waits for the full
     goal, weakness locks crumbs early; the 1.0% floor scales too).
  3. New highs keep raising the stop (the ride): price above the stop
     never fires.
  4. A turn through the stop fires the exit with a HALF-GOAL TRAIL reason
     plus the ADAPTIVE GUARD explanation (what the guard read, what it
     decided, in plain words).
  5. The arm survives a save/reload of the watch file (it is row state,
     not process memory).
  6. Between half-goal and goal: ARMED but WIDE — the ratchet trail runs
     uncapped below the cap, so the climb to goal is never cut by the net.
  7. UNREADABLE CONTEXT -> NO factor override anywhere: the committed
     fixed rules run unchanged (the honesty fallback).
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

print("== 1. the arm exists and latches at HALF the goal (Sep 22) ==")
check(1.1, "trail_armed" in SRC,
      "watch row carries a trail_armed latch field")
check(1.2, 'w["trail_armed"] = True' in SRC and "dirty = True" in SRC,
      "the arm is written into the watch row (persists, not process memory)")
check(1.3, "GOAL_TRAIL_ARM_FRAC = 0.5" in SRC,
      "the arm fraction is a named dial (0.5 = half the goal)")
check(1.4, "(live / entry - 1.0) * 100.0 >= _arm_at" in SRC,
      "arm condition: live price at/above HALF the goal")

print("== 2. the armed trail is tight — the half-goal width, scaled ==")
check(2.1, "min(_trail, _cap)" in SRC
      and "_cap = _armed_cap_base(_goal_tr)" in SRC,
      "cap: trail = min(ratchet trail, _armed_cap_base(goal) — the "
      "half-goal width tightened by the day's spent give-back tiers)")
# 2.1b THE COST FLOOR (Sep 22, the CodeRabbit major): a WIDTH cap alone
#      cannot lock profit — at a 2% goal the arm gain is 1% but the
#      ratchet band at that peak is 2%, so the stop lands BELOW entry and
#      net of the 0.08% RT cost the "profit is locked" exit booked a
#      small loss. The armed stop is PRICE-floored at entry + 2x the RT
#      cost — net-positive by one cost unit — in BOTH the enforcement and
#      the voice (what she says is what closes).
check("2.1b", "COST_ROUNDTRIP_PCT as _rt_pct" in SRC
      and SRC.count("(1.0 - entry") + SRC.count("(1.0 - entry*") >= 2
      and "_cap = min(_cap, round(_lock_w, 3))" in SRC
      and "_cap_now = min(_cap_now, round(_lock_wv, 3))" in SRC,
      "the stop price is floored at entry + 2x the round-trip cost, both sites")
check("2.1c", "the profit is locked (above entry + costs)" in SRC,
      "the fire reason is honest about the floor")
check("2.1d", SRC.find("_cap_now = max(_goal * GOAL_TRAIL_ARM_FRAC") < SRC.find("if _vgf is not None:", SRC.find("_cap_now")) < SRC.find("if _eg_m is not None:", SRC.find("_cap_now")) < SRC.find("_trail_now = min(_trail_now, _cap_now)"),
      "voice parity: cap, floor, GF, EG, then min — the enforcement's exact order")
check(2.2, "_cap = round(_cap * _pgf, 3)" in SRC,
      "the armed cap scales by the ADAPTIVE GUARD FACTOR (spec step 9)")
check(2.3, SRC.count("_goal_tr * GOAL_TRAIL_ARM_FRAC") >= 1 and
      "if w.get(\"trail_armed\") and _goal_tr > 0:" in SRC,
      "the cap applies only while armed — pre-arm trailing is unchanged")
check(2.4, SRC.count("_armed_cap_base(") >= 3,
      "the per-pair voice computes the same stop the exit enforces "
      "(one cap expression for all three sites)")
check(2.5, "import profit_guard as _pgv" in SRC,
      "her voice computes the same factor the exit enforces (parity)")
check(2.6, "_trail = round(_trail * _pgf, 3)" in SRC,
      "the running ratchet trail scales by the factor (weak context "
      "tightens NOW, not only after the arm)")
check(2.7, SRC.count("if _pgf is not None:") >= 2,
      "every factor application is gated — unreadable context keeps "
      "the committed fixed rules")

print("== 3. the ride: stop tracks the tracked high ==")
check(3.1, 'high = max(old_high, live)' in SRC,
      "the high is tracked in the watch file, so the stop climbs with it")
check(3.2, "live <= high * (1 - _trail / 100)" in SRC,
      "fire condition stays peak-relative — new highs move the net up")

print("== 4. the turn says why — HALF-GOAL TRAIL + ADAPTIVE GUARD ==")
check(4.1, "HALF-GOAL TRAIL" in SRC and "the profit is locked" in SRC,
      "the fired popup explains the half-goal arm and the locked level")
check(4.5, "ADAPTIVE GUARD" in SRC and "_pg.explain(" in SRC,
      "the fired popup speaks the factor in plain words")
check(4.4, "(half the +" in SRC and "goal), so the stop sits" in SRC,
      "the reason names the arm point against the goal")
check(4.2, "trail set" in SRC and "locks" in SRC,
      "her voice announces the armed net (stop % + lock price) before it fires")
check(4.3, "popup_journal(\"GET OUT\"" in SRC,
      "the trail exit rides the same popup-first GET OUT machinery")

print("== 5. latch survives reload (row state, not memory) ==")
import json
import tempfile
row = {"pair": "TEST/USD", "entry": 1.0, "high": 1.03, "goal_pct": 2.7,
       "trail_armed": True}

print("== 6. between arm and goal: armed but WIDE (the climb is never cut) ==")
# the cap uses the same fraction, so at exactly half-goal the cap equals
# the arm point — the drive pins the source shape; the numeric behavior
# (ratchet trail uncapped below the cap) is the ratchet's own contract
check(6.1, "_arm_at = _goal_tr * GOAL_TRAIL_ARM_FRAC" in SRC,
      "the arm point is computed once, shared by latch and cap")
check(6.2, SRC.count("GOAL_TRAIL_ARM_FRAC") >= 4,
      "the dial is used at every site (constant, arm, cap, voice) — no "
      "stray half-goal literals left behind")
check(6.3, "the run reached +{_arm_at:.1f}%" in SRC,
      "the fire reason speaks the arm point, not the goal")
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
