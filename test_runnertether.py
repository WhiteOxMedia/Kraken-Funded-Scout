"""test_runnertether.py — hermetic pins for the runner-tether backtest
(diag_runnertether).

These pins prove the ARMED tether counterfactual on synthetic bars —
no production file is read:

  1. never-confirmed runs claim nothing (the original exit stands)
  2. a confirmed runner exits on the trail break at the trail price
  3. a confirmed runner that never breaks trails to the horizon's
     last close
  4. the giveback-after-confirm case prices its honest cost
  5. the horizon clamp and the empty/zero guards
  6. the study's constants: the trail is the guard's own giveback
     (1.5%), the horizon is the re-goal cadence (8 bars), and the
     fix is ARMED (the naive tether-everything world is dead on disk)
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

import diag_runnertether as drt  # noqa: E402
import scratch_space   # scratch policy: nothing volatile lands in the repo root

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        lines.append(f"PASS  {name}")
    else:
        FAIL += 1
        lines.append(f"FAIL  {name}  [{detail}]")


# exit at 100.00 — every world scales off it.
# 1. never confirms: closes drift 100.5 -> 100.8, no +2% high.
bars = [(100.8, 100.5), (100.6, 100.7), (100.5, 100.4)]
g = drt.price_tether(bars, 100.0)
check("arm rule: a coin that never confirms +2% claims NOTHING (the "
      "original exit stands, delta 0)",
      g["armed"] is False and g["tether_exit"] is None,
      json.dumps(g))

# 2. confirmed, then the trail breaks: high 104.5 arms it (>= 102),
#    close 101.8 <= 104.5 * 0.985 = 102.93 -> exit at that close.
bars = [(101.0, 100.5), (104.5, 101.8), (102.0, 101.0)]
g = drt.price_tether(bars, 100.0)
check("runner: the armed tether exits at the trail-break close "
      "(+1.8% over the fixed-goal sell)",
      g["armed"] is True and abs(g["tether_exit"] - 101.8) < 1e-9,
      json.dumps(g))

# 3. confirmed, never breaks: rides to the horizon's last close.
bars = [(104.0, 103.5), (105.0, 104.6), (105.5, 105.2)]
g = drt.price_tether(bars, 100.0)
check("runner: a confirmed run that never gives back 1.5% rides to "
      "the horizon's last close",
      g["armed"] is True and abs(g["tether_exit"] - 105.2) < 1e-9
      and "horizon end" in g["detail"], json.dumps(g))

# 4. confirmed, then dies: the giveback is the honest cost. Bar +1
#    arms at 102.5 but its close (101.9) holds the trail (level 100.96);
#    bar +2 gaps to 99.5 — through the trail and BELOW the original exit.
bars = [(102.5, 101.9), (100.8, 99.5)]
g = drt.price_tether(bars, 100.0)
check("giveback: a confirmed run that dies prices its cost "
      "(trail break BELOW the original exit)",
      g["armed"] is True and abs(g["tether_exit"] - 99.5) < 1e-9,
      json.dumps(g))

# 5. the guards.
g = drt.price_tether([], 100.0)
check("guards: no bars after the exit claims nothing",
      g["armed"] is False and g["tether_exit"] is None, json.dumps(g))
g = drt.price_tether([(110.0, 109.0)], 0.0)
check("guards: a zero exit price claims nothing",
      g["armed"] is False, json.dumps(g))
bars = [(110.0, 109.5)]
g = drt.price_tether(bars, 100.0, horizon=1)
check("horizon: the clamp rides only the first bar",
      g["armed"] is True and abs(g["tether_exit"] - 109.5) < 1e-9,
      json.dumps(g))

# 6. the constants and the armed design, pinned against drift.
check("constants: the trail is the guard's own giveback (1.5%), the "
      "horizon is the re-goal cadence (8 bars), the runner run is 2%",
      drt.TRAIL == 0.015 and drt.HORIZON_BARS == 8
      and drt.RUNNER_RUN == 0.02)
check("the fix is ARMED: the naive tether-everything world (which "
      "rode this month's losers down) is dead on disk",
      drt.price_tether.__defaults__ is not None
      and drt.price_tether.__defaults__[2] == drt.ARM_RUN
      and "ARMED" in drt.price_tether.__doc__)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
(scratch_space.report_temp('_runnertether_check.txt')).write_text(
    "\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
sys.exit(1 if FAIL else 0)
