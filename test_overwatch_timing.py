"""test_overwatch_timing.py — the trend-ride engine and its shadow gate (Nov 15).

The engine is the shape the user asked for: buy the band pullback in an uptrend,
cut the loser at the stop, never cap the winner, ratchet the stop behind the peak.
The gate exists because that shape wins 82.3% and still drew down $2,677 against a
$300 cushion. These checks hold both halves.
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


def rising(n=300, step=0.05):
    return [100.0 + i * step for i in range(n)]


# ── 1. the entry: lower-band pullback inside an uptrend ─────────────────────
cl = rising()
lower, mid = ot.band_levels(cl)
o = cl[:-1] + [lower * 1.004]
hi = cl[:-1] + [lower * 1.012]
lo = cl[:-1] + [lower * 0.999]
c = cl[:-1] + [lower * 1.010]
ok, why = ot.entry_ok(c, hi, lo, o)
check("1.1 a band touch that closes back up in an uptrend enters", ok is True, why)

falling = [200.0 - i * 0.05 for i in range(300)]
f_lower, _ = ot.band_levels(falling)
ok2, why2 = ot.entry_ok(falling[:-1] + [f_lower * 1.01],
                        falling[:-1] + [f_lower * 1.02],
                        falling[:-1] + [f_lower * 0.99],
                        falling[:-1] + [f_lower * 1.0])
check("1.2 a band touch below the 200-EMA is refused (no uptrend)", ok2 is False,
      why2)
check("1.3 why it refused names the trend", "trend" in why2, why2)

ok3, why3 = ot.entry_ok(cl, cl, [x * 0.999 for x in cl], cl)
check("1.4 an uptrend with no band touch is refused", ok3 is False, why3)
ok4, why4 = ot.entry_ok(cl[:50], cl[:50], cl[:50], cl[:50])
check("1.5 too little history is a refusal, never a guess", ok4 is False, why4)

# ── 2. the exit: stop first, then a ratchet that only rises ─────────────────
pk, stp, ex = ot.manage(entry=100.0, peak=100.0, stop=98.0, bar_high=99.0,
                        bar_low=97.9, bar_close=98.4)
check("2.1 a losing trade leaves at the stop", ex == 98.0, ex)

pk, stp, ex = ot.manage(entry=100.0, peak=100.0, stop=98.0, bar_high=101.0,
                        bar_low=100.5, bar_close=100.9)
check("2.2 once the trade earns the activation the stop ratchets up",
      ex is None and stp > 100.0, (ex, stp))
armed_stop = stp
pk2, stp2, ex2 = ot.manage(entry=100.0, peak=pk, stop=stp, bar_high=100.2,
                           bar_low=100.95, bar_close=101.0)
check("2.3 the ratchet never moves down", stp2 >= armed_stop, (stp2, armed_stop))

pk3, stp3, ex3 = ot.manage(entry=100.0, peak=100.0, stop=98.0, bar_high=110.0,
                           bar_low=100.5, bar_close=109.0)
check("2.4 the upside is never capped (a 10% run is still open)",
      ex3 is None and pk3 == 110.0, (ex3, pk3))
check("2.5 and the stop follows the peak, keeping the profit",
      stp3 > 109.0, stp3)

# ── 3. the arithmetic ───────────────────────────────────────────────────────
check("3.1 a 1% gain nets 0.92% after the round trip",
      abs(ot.net_pct(100.0, 101.0) - 0.92) < 1e-9, ot.net_pct(100.0, 101.0))
check("3.2 the stop is 2% below entry",
      abs(ot.initial_stop(100.0) - 98.0) < 1e-9, ot.initial_stop(100.0))
check("3.3 a win rate needs a stop this wide to keep 80% wins",
      ot.STOP_PCT >= 4 * ot.GIVE_PCT / 2, (ot.STOP_PCT, ot.GIVE_PCT))

# ── 4. the shape measured, and why it is not armed ──────────────────────────
check("4.1 the study records the 82.3% win rate", ot.STUDY["win_pct"] == 82.3)
check("4.2 and the positive November expectancy",
      ot.STUDY["november"]["mean_net_pct"] > 0, ot.STUDY["november"])
check("4.3 and the drawdown that disqualifies it",
      ot.STUDY["max_drawdown_usd_2k"] > 8 * ot.STUDY["cushion_usd"])
check("4.4 November's t is inside noise, so the edge is not proven",
      abs(ot.STUDY["november"]["t"]) < 2.0)
check("4.5 real orders are OFF", ot.ARMED is False)

empty_arm, empty_why = ot.should_arm({})
check("4.6 with no shadow record the gate refuses and says what is missing",
      empty_arm is False and "0/200 shadow trades" in empty_why, empty_why)

proven = {"trades": 250, "win_pct": 85.0, "mean_net_pct": 0.10,
          "max_drawdown_usd": 100.0}
met_arm, met_why = ot.should_arm(proven)
check("4.7 even a passing shadow record does not self-arm: that is a human call",
      met_arm is False and "human decision" in met_why, met_why)

bad = {"trades": 250, "win_pct": 70.0, "mean_net_pct": -0.02,
       "max_drawdown_usd": 400.0}
bad_arm, bad_why = ot.should_arm(bad)
check("4.8 a failing shadow record is refused on all three counts",
      bad_arm is False and "win" in bad_why and "DD" in bad_why, bad_why)

verdict = ot.shadow_verdict(proven)
check("4.9 the verdict plate carries state, numbers and reason",
      verdict["state"] == "shadow" and verdict["win_pct"] == 85.0
      and "why" in verdict and verdict["armed"] is False)

print("\n".join(lines))
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
