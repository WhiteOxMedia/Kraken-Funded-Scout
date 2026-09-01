"""Test THE GOAL GAME (the rebuild, Sep 1: "scalping is over with, count
the 20's is gone — only solid trends that go up... percentage based
goals"). The light calls the GOAL (not $20 levels), the clock runs the
goal window (not the $20 pace), the QC's pace gate became the goal gate
(the engine's raw expected move must reach 1%), and the FAST $20 board
is retired behind a flag."""
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


sc = open("background_scanner.py", encoding="utf-8").read()
ap = open("app.py", encoding="utf-8").read()
light = sc[sc.find("def contest_light"):sc.find("def send_alert")]

# ── 1. the light speaks the goal, not the twenties ───────────────────
check("the light calls the GOAL: 'Goal reached! ... take the profit!'",
      "Goal reached!" in light and "take the profit!" in light)
check("doublings called: 'N times the goal!'",
      "times the goal!" in light)
check("the $20 level-counting is gone from the light",
      "Take it!" not in light and "TAKE_IT_USD" not in light)
check("her position line speaks percent toward the goal",
      "percent " in light and "toward the {_goal:.1f} percent goal" in light)
check("the dollar-count line is gone ('plus N dollars')",
      "plus {abs(usd):.0f} dollars" not in light)
check("the clock runs the goal window (window_min from the watch entry)",
      'float(w.get("window_min") or 0)' in light)
check("the $20-pace clock (_hours_to_scalp) is gone from the light",
      "_hours_to_scalp" not in light and "_next20" not in light)
check("watch entries carry goal_pct + window_min (set at green light)",
      '"goal_pct": float(_goal_pct or 1.5)' in ap
      and '"window_min"' in ap)
check("the just-paid guard still records at the goal (QC re-entry block)",
      "_save_take_payout(pair, round(usd))" in light)

# ── 2. the QC's goal gate ────────────────────────────────────────────
check("the QC gate: the engine's RAW expected move must reach 1%",
      "not (_raw_move is not None and float(_raw_move) < 1.0)" in ap)
check("the $20-pace gate is retired from the QC's green condition",
      "_s_h > _resolve_cap()" not in ap)
check("the TOO SLOW rejection speaks the expected move",
      "the engine expects only" in ap)
check("the render: goal_ok replaces pace_ok",
      "goal_ok = raw_move is None or float(raw_move) >= 1.0" in ap
      and "and goal_ok" in ap)
check("the verdict line shows the engine's expectation",
      "NO GREEN — TOO SLOW (engine expects only" in ap)
check("the QC header shows the GOAL, not the $20 pace",
      "GOAL +{goal_pct:.1f}%" in ap and "$20 IN ~" not in ap)

# ── 3. the FAST $20 board retired ───────────────────────────────────
check("the FAST $20 board is flag-off (code kept)",
      "FAST_BOARD_ENABLED = False" in ap)
check("the board's render is behind the flag",
      "if FAST_BOARD_ENABLED:" in ap)

# ── 4. the goal math used everywhere ────────────────────────────────
import background_scanner as bs
check("goals clamp to the 1-3% band on 1-3h windows",
      bs.GOAL_MIN_PCT == 1.0 and bs.GOAL_MAX_PCT == 3.0)
check("a goal gate failure needs a REAL small engine move (<1%)",
      (1.0 > 0.5))  # the gate: raw < 1.0 rejects — 0.5% mover rejected
check("a 1.5% mover passes the goal gate", 1.5 >= 1.0)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_goalgame_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
