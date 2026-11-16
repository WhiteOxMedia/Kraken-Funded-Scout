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


# ── 4. the shape measured, and the death line that sizes it ─────────────────
check("4.1 the study records the 82.3% win rate", ot.STUDY["win_pct"] == 82.3)
check("4.2 and the positive November expectancy",
      ot.STUDY["november"]["mean_net_pct"] > 0, ot.STUDY["november"])
check("4.3 the death line is -3% of the STARTING balance and never moves",
      ot.STUDY["death_line_usd"] == 9700.0)
acct = {"start_balance": 10000.0, "current_balance": 9981.95855906339}
ctx = ot.death_context(acct)
check("4.4 the context is the repo's own death_floor/halt_line arithmetic",
      abs(ctx["death_line"] - 9700.0) < 1e-6 and abs(ctx["halt_line"] - 9850.0) < 1e-6,
      ctx)
check("4.5 the live headroom is balance minus the halt line (it grows with profit)",
      abs(ctx["headroom"] - 131.95855906339) < 1e-6, ctx["headroom"])
check("4.6 one ticket is 1% of the balance, via contest_account.sized_entry_usd",
      abs(ot.ticket_usd(10000.0, 10000.0) - 100.0) < 1e-6,
      ot.ticket_usd(10000.0, 10000.0))
check("4.7 at 1% the path never halted and kept its room",
      ot.STUDY["paths"]["1pct"]["halted"] is False
      and ot.STUDY["paths"]["1pct"]["room_usd"] > 0)
check("4.8 at 5% it earns more and nearly dies: size is the risk control",
      ot.STUDY["paths"]["5pct"]["profit_usd"] > ot.STUDY["paths"]["1pct"]["profit_usd"]
      and ot.STUDY["paths"]["5pct"]["room_usd"] < 0)
check("4.9 real orders are OFF", ot.ARMED is False)

empty_arm, empty_why = ot.should_arm({}, acct)
check("4.10 with no shadow record the gate refuses and says what is missing",
      empty_arm is False and "0/200 shadow trades" in empty_why, empty_why)

at_floor, floor_why = ot.should_arm({}, {"start_balance": 10000.0,
                                         "current_balance": 9850.0})
check("4.11 at the halt line nothing new is risked, whatever the record says",
      at_floor is False and "halt line" in floor_why, floor_why)

proven = {"trades": 250, "win_pct": 85.0, "mean_net_pct": 0.10,
          "min_headroom_usd": 200.0}
met_arm, met_why = ot.should_arm(proven, acct)
check("4.12 a passing record still needs the single execution path, not a second trader",
      met_arm is False and "single execution path" in met_why, met_why)

bad = {"trades": 250, "win_pct": 70.0, "mean_net_pct": -0.02,
       "min_headroom_usd": 40.0}
bad_arm, bad_why = ot.should_arm(bad, acct)
check("4.13 a failing record is refused on every count",
      bad_arm is False and "win" in bad_why and "room" in bad_why, bad_why)

verdict = ot.shadow_verdict(proven)
check("4.14 the plate carries the death line, the room and the size",
      verdict["state"] == "shadow" and verdict["win_pct"] == 85.0
      and verdict["death_line_usd"] == 9700.0
      and verdict["size_frac"] == 0.01 and verdict["armed"] is False)

print(chr(10).join(lines))
print(f"{chr(10)}RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
