"""test_overwatch_timing.py — the trend-ride engine and the death-line gate (Nov 15).

The engine: buy the band pullback in an uptrend, cut the loser at the stop, never cap
the winner, ratchet the stop behind the peak. The risk question is the -3% death line
from the STARTING balance — it never moves and the buffer above it grows with every
win — so every number here is checked against contest_account and death_floor rather
than against a copy of their arithmetic.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import contest_account as ca
import death_floor as df
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
lower = ot.band_lower(cl)
o = cl[:-1] + [lower * 1.004]
hi = cl[:-1] + [lower * 1.012]
lo = cl[:-1] + [lower * 0.999]
c = cl[:-1] + [lower * 1.010]
ok, why = ot.entry_ok(c, hi, lo, o)
check("1.1 a band touch that closes back up in an uptrend enters", ok is True, why)

falling = [200.0 - i * 0.05 for i in range(300)]
f_lower = ot.band_lower(falling)
ok2, why2 = ot.entry_ok(falling[:-1] + [f_lower * 1.01],
                        falling[:-1] + [f_lower * 1.02],
                        falling[:-1] + [f_lower * 0.99],
                        falling[:-1] + [f_lower * 1.0])
check("1.2 a band touch below the 200-EMA is refused (no uptrend)", ok2 is False, why2)
check("1.3 and the refusal names the trend", "trend" in why2, why2)

ok3, why3 = ot.entry_ok(cl, cl, [x * 0.999 for x in cl], cl)
check("1.4 an uptrend with no band touch is refused", ok3 is False, why3)
ok4, why4 = ot.entry_ok(cl[:50], cl[:50], cl[:50], cl[:50])
check("1.5 too little history is a refusal, never a guess", ok4 is False, why4)

# ── 2. the exit: stop first, then a ratchet that only rises ─────────────────
peak, stop, exit_px, reason = ot.manage(entry=100.0, peak=100.0, stop=98.0,
                                        bar_high=99.0, bar_low=97.9)
check("2.1 a losing trade leaves at the stop, labelled a stop",
      exit_px == 98.0 and reason == "stop", (exit_px, reason))

peak, stop, exit_px, reason = ot.manage(entry=100.0, peak=100.0, stop=98.0,
                                        bar_high=101.0, bar_low=100.5)
check("2.2 once the trade earns the activation the stop ratchets up",
      exit_px is None and stop > 100.0, (exit_px, stop))
armed_stop = stop

peak2, stop2, exit_px2, _ = ot.manage(entry=100.0, peak=peak, stop=stop,
                                      bar_high=100.2, bar_low=100.95)
check("2.3 the ratchet never moves down", stop2 >= armed_stop, (stop2, armed_stop))

peak3, stop3, exit_px3, _ = ot.manage(entry=100.0, peak=100.0, stop=98.0,
                                      bar_high=110.0, bar_low=100.5)
check("2.4 the upside is never capped (a 10% run is still open)",
      exit_px3 is None and peak3 == 110.0, (exit_px3, peak3))
check("2.5 and the stop follows the peak, keeping the profit", stop3 > 109.0, stop3)

peak4, stop4, exit_px4, reason4 = ot.manage(entry=100.0, peak=110.0, stop=stop3,
                                            bar_high=110.0, bar_low=stop3 - 0.01)
check("2.6 a ratcheted exit is labelled a trail, not a stop",
      exit_px4 == stop3 and reason4 == "trail", reason4)

# ── 3. arithmetic owned by contest_account ─────────────────────────────────
check("3.1 the cost is contest_account's own number, not a restatement",
      ot.COST_PCT == ca.COST_ROUNDTRIP_PCT, (ot.COST_PCT, ca.COST_ROUNDTRIP_PCT))
check("3.2 a 1% gain nets that cost out",
      abs(ot.net_pct(100.0, 101.0) - (1.0 - ca.COST_ROUNDTRIP_PCT)) < 1e-9,
      ot.net_pct(100.0, 101.0))
check("3.3 the stop is STOP_PCT below the entry",
      abs(ot.initial_stop(100.0) - (100.0 - ot.STOP_PCT)) < 1e-9,
      ot.initial_stop(100.0))
check("3.4 the engine carries no dead switches or unused helpers",
      not hasattr(ot, "TREND_BREAK") and not hasattr(ot, "_ema_series"))

# ── 4. the death line, from the modules that own it ────────────────────────
acct = {"start_balance": 10000.0, "current_balance": 9981.95855906339}
ctx = ot.death_context(acct)
check("4.1 the death line is -3% of the start and arrives from death_floor",
      ctx["death_line"] == df.death_line(10000.0) == 9700.0, ctx["death_line"])
check("4.2 the halt line is the band death_floor defines",
      ctx["halt_line"] == df.halt_line(10000.0) == 9850.0, ctx["halt_line"])
check("4.3 the buffer is contest_account.buffer() — the number that grows with wins",
      abs(ctx["buffer"] - ca.buffer(acct)) < 1e-9
      and abs(ctx["buffer"] - 281.95855906339) < 1e-6, ctx["buffer"])
check("4.4 the headroom is death_floor's own distance to the halt line",
      ctx["headroom"] == df.state(acct)["headroom_to_halt_usd"], ctx["headroom"])
check("4.5 one ticket is SIZE_FRAC of the balance via sized_entry_usd",
      abs(ot.ticket_usd(10000.0, 10000.0) - 100.0) < 1e-6,
      ot.ticket_usd(10000.0, 10000.0))

check("4.6 the study records the 82.3% win rate", ot.STUDY["win_pct"] == 82.3)
check("4.7 and the positive November expectancy",
      ot.STUDY["november"]["mean_net_pct"] > 0, ot.STUDY["november"])
check("4.8 at 1% the path never halted and kept its room",
      ot.STUDY["paths"]["1pct"]["halted"] is False
      and ot.STUDY["paths"]["1pct"]["room_usd"] > 0)
check("4.9 at 5% it earns more and nearly dies: size is the risk control",
      ot.STUDY["paths"]["5pct"]["profit_usd"] > ot.STUDY["paths"]["1pct"]["profit_usd"]
      and ot.STUDY["paths"]["5pct"]["room_usd"] < 0)
check("4.10 real orders are OFF", ot.ARMED is False)

empty_arm, empty_why = ot.should_arm({}, ctx)
check("4.11 with no record the gate refuses and names what is missing",
      empty_arm is False and "0/200 shadow trades" in empty_why, empty_why)

at_floor, floor_why = ot.should_arm({}, ot.death_context(
    {"start_balance": 10000.0, "current_balance": 9850.0}))
check("4.12 at the halt line nothing new is risked, whatever the record says",
      at_floor is False and "halt line" in floor_why, floor_why)

thin, thin_why = ot.should_arm({"trades": 250, "win_pct": 90.0, "mean_net_pct": 0.1,
                                "min_headroom_usd": 200.0},
                               ot.death_context({"start_balance": 10000.0,
                                                 "current_balance": 9930.0}))
check("4.13 a buffer under the floor stops it, even on a winning record",
      thin is False and "buffer" in thin_why, thin_why)

proven = {"trades": 250, "win_pct": 85.0, "mean_net_pct": 0.10,
          "min_headroom_usd": 200.0}
met_arm, met_why = ot.should_arm(proven, ctx)
check("4.14 a passing record still needs the single execution path",
      met_arm is False and "single execution path" in met_why, met_why)

bad = {"trades": 250, "win_pct": 70.0, "mean_net_pct": -0.02, "min_headroom_usd": 40.0}
bad_arm, bad_why = ot.should_arm(bad, ctx)
check("4.15 a failing record is refused on every count",
      bad_arm is False and "win" in bad_why and "room" in bad_why, bad_why)

verdict = ot.shadow_verdict(proven, ctx)
check("4.16 the plate carries the buffer, the lines, the room and the size",
      verdict["state"] == "shadow" and verdict["buffer_usd"] == 281.96
      and verdict["death_line_usd"] == 9700.0 and verdict["halt_line_usd"] == 9850.0
      and verdict["record_low_room_usd"] == 200.0 and verdict["size_frac"] == 0.01
      and verdict["armed"] is False, verdict)

print("\n".join(lines))
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
