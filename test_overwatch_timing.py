"""test_overwatch_timing.py — the fast timing engine and its arming switch (Nov 15).

The engine exists so overwatch owns entry/exit timing. The switch exists because
the measurement says the shape loses: 216 configurations, none positive, and the
best 80%-win shape at -$726.90. These checks hold both halves.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import overwatch_timing as ot

PASS = FAIL = 0
lines = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        lines.append(f"PASS  {name}")
    else:
        FAIL += 1
        lines.append(f"FAIL  {name}  {detail}")


# ── 1. the entry ─────────────────────────────────────────────────────────────
up = [100.0 + i * 0.01 for i in range(400)]          # a clean uptrend
lows_up = [x - 0.05 for x in up]
closes_dip = up[:-1] + [up[-2] + 0.03]              # the entry bar turns up
lows_dip = lows_up[:-1] + [up[-4] - 0.30]           # and printed a fresh low
check("1.1 an uptrend with a fresh 2-bar low and a turn up enters",
      ot.entry_ok(closes_dip, lows_dip) is True)
flat = [100.0] * 400
check("1.2 a flat book does not enter (no uptrend)",
      ot.entry_ok(flat, [99.9] * 400) is False)
check("1.3 an uptrend without a fresh low does not enter",
      ot.entry_ok(up, [x + 0.5 for x in up]) is False)
check("1.4 too little history is a refusal, never a guess",
      ot.entry_ok([100.0] * 10, [99.0] * 10) is False)

# ── 2. the exit, in precedence order ────────────────────────────────────────
check("2.1 the target pays target minus cost",
      abs(ot.exit_pct(held_bars=1, entry=100.0, high=101.6, low=99.9, close=101.6)
          - (ot.TARGET_PCT - ot.COST_PCT)) < 1e-9)
check("2.2 the stop costs stop plus cost",
      abs(ot.exit_pct(held_bars=1, entry=100.0, high=100.1, low=98.7, close=98.8)
          - (-ot.STOP_PCT - ot.COST_PCT)) < 1e-9)
check("2.3 the timeout closes at the bar",
      ot.exit_pct(held_bars=ot.TIMEOUT_BARS, entry=100.0, high=100.1, low=99.9,
                  close=100.2) is not None)
check("2.4 otherwise it holds", ot.exit_pct(held_bars=1, entry=100.0, high=100.2,
                                            low=99.9, close=100.1) is None)
check("2.5 the target wins a bar that touches both barriers",
      ot.exit_pct(held_bars=1, entry=100.0, high=102.0, low=98.0, close=99.0)
      == ot.TARGET_PCT - ot.COST_PCT)

# ── 3. the arithmetic that decides the shape ────────────────────────────────
check("3.1 an 80% win rate needs a stop four times the target",
      abs(ot.required_stop(0.15, 0.80) - 0.60) < 1e-9, ot.required_stop(0.15, 0.80))
check("3.2 and the break-even target at that stop is a quarter of it",
      abs(ot.breakeven_target(0.60, 0.80) - 0.15) < 1e-9)
check("3.3 break-even targets rise as the win rate falls",
      ot.breakeven_target(1.0, 0.50) > ot.breakeven_target(1.0, 0.60))

# ── 4. it is not armed, and says why ────────────────────────────────────────
armed, why = ot.should_arm()
check("4.1 the engine is NOT armed for live", armed is False, armed)
check("4.2 the refusal names the measured zero and the 80% shape that lost",
      "0 of 216" in why and "80.5%" in why, why)
check("4.3 the study record carries the rejection",
      ot.STUDY["positive_on_train"] == 0 and ot.STUDY["positive_on_november"] == 0
      and ot.STUDY["best_80pct_shape"]["net"] < 0)
check("4.4 the shape it holds is the fast one (target above the 0.08% cost)",
      ot.TARGET_PCT > ot.COST_PCT * 5 and ot.STOP_PCT < 3.0)

print("\n".join(lines))
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
