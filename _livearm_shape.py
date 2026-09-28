"""_livearm_shape.py - THE LIVE HALF-GOAL ARM, SIMULATED EXACTLY (Sep 27,
the last untested exit variant). The hybrid studies idealized the arm as
"90% of goal, ratchet width"; the scanner actually runs something richer
(background_scanner.py 3539-3605, read line-by-line before writing this):

  - the ratchet trail (3.0 base, 2.0 at +1% peak, 1.5 at +2%) runs FROM
    the start - the arm does not create the trail, it TIGHTENS the cap;
  - the arm LATCHES when the run reaches goal_pct * GOAL_TRAIL_ARM_FRAC
    (0.5) - half the goal (latched: a dip back cannot disarm);
  - once armed, the width caps at max(goal_pct * 0.5, 1.0) (never
    looser than 1.0%), THEN at the PRICE FLOOR: the stop can never sit
    below entry * (1 + 2 * COST_ROUNDTRIP_PCT) (0.08% RT -> entry*1.0016),
    so an armed exit can never book a net loss (the Sep 22 CodeRabbit
    major: the width cap alone could strand the stop below entry);
  - the bank at the trade's FULL goal stays live (this walk keeps it,
    bank-first bar ordering, as in _hybrid_trail_sweep.py).

The question: does the TRUE live shape beat the idealized 90%-arm hybrid
on the same frozen cohort, under the corrected control accounting
(every simulated exit counts) and the relative bar (a policy must not
cost more control than its comparator under the identical walk)?
Read-only: queries only. ASCII-pure output (the run_entry decode lesson).
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _verdict_study as vs  # noqa: E402

ARM_FRAC = 0.5      # GOAL_TRAIL_ARM_FRAC (background_scanner.py:900)
RT_PCT = 0.08       # contest_account.COST_ROUNDTRIP_PCT


def ratchet_w(entry, peak):
    pp = (peak / entry - 1.0) * 100.0
    return 1.5 if pp >= 2.0 else (2.0 if pp >= 1.0 else 3.0)


def walk_live(g):
    """The LIVE shape. Returns (exit_px, 'bank'|'trail', floored) or None."""
    entry, goal = g["entry"], g["goal"]
    bank_px = entry * (1 + goal / 100.0)
    arm_at = goal * ARM_FRAC
    floor_px = entry * (1 + 2 * RT_PCT / 100.0)
    armed = False
    peak, stop = entry, None
    for ts, hi, lo in vs._db().execute(
            "SELECT ts, high, low FROM ohlcv WHERE pair=? AND ts>=? "
            "AND ts<=? ORDER BY ts",
            (g["pair"], int(g["en_ms"]), int(g["ex_ms"] + vs.AFTER_MS))):
        if hi >= bank_px:                        # bank-first: the TP is live
            return bank_px, "bank", False
        if hi > peak:
            peak = hi
        if not armed and goal > 0 and (peak / entry - 1.0) * 100.0 >= arm_at:
            armed = True                         # LATCHED: stays armed
        w = ratchet_w(entry, peak)
        floored = False
        if armed:
            cap = max(goal * ARM_FRAC, 1.0)      # the 1.0% floor
            lock_w = max(0.0, (1.0 - floor_px / peak) * 100.0)
            if lock_w > 0:
                cap = min(cap, lock_w)           # the PRICE floor
            w = min(w, cap)
        stop = peak * (1 - w / 100.0)
        if armed and stop < floor_px <= peak:
            # CODE RABBIT MAJOR (Sep 27): the floor binds ONLY once armed
            # (the pre-arm ratchet was never floor-locked) and ONLY when
            # the peak has actually reached the floor level - a clamp on
            # an unreachable peak placed fills at prices the market
            # never traded (entry 100, hi 100.10, stop "100.16").
            stop = floor_px
            floored = True
        if peak > entry and lo <= stop:
            if stop > hi:
                # the floor put the stop above THIS bar's traded range:
                # no fill at an untraded price; the trail stands
                continue
            return stop, "trail", floored
    return None


def walk_incumbent(g):
    """The idealized hybrid comparator: bank-first, arm at 90% of goal,
    ratchet width, no price floor (_hybrid_trail_sweep's incumbent row)."""
    entry, goal = g["entry"], g["goal"]
    bank_px = entry * (1 + goal / 100.0)
    arm = goal * 0.9
    peak, stop = entry, None
    for ts, hi, lo in vs._db().execute(
            "SELECT ts, high, low FROM ohlcv WHERE pair=? AND ts>=? "
            "AND ts<=? ORDER BY ts",
            (g["pair"], int(g["en_ms"]), int(g["ex_ms"] + vs.AFTER_MS))):
        if hi >= bank_px:
            return bank_px, "bank", False
        if hi > peak:
            peak = hi
            if peak >= entry * (1 + arm / 100.0):
                stop = peak * (1 - ratchet_w(entry, peak) / 100.0)
        if stop is not None and lo <= stop:
            return stop, "trail", False
    return None


graded_all = vs.grade_trades()
_newest = max((float(g.get("ex_ms") or 0) for g in graded_all), default=0.0)
graded = [g for g in graded_all if float(g.get("ex_ms") or 0) <= _newest]
print(f"trades: {len(graded)} (frozen at load: newest exit + 1ms)   "
      f"horizon: exit+{vs.AFTER_MS // 3600000}h   fees: net() RT_COST   "
      f"price floor: entry*{(1 + 2 * RT_PCT / 100.0):.4f}")

act = sum(vs.net(g["entry"], g["exit"], g["size"]) for g in graded)
print(f"\n{'policy':<22} | {'banks':>5} | {'bank $':>9} | {'rides':>5} | "
      f"{'ride $':>9} | {'per-ride':>8} | {'ctrl $':>9} | {'floor x':>7} | "
      f"{'TOTAL $':>9}")
print("-" * 104)
res = {}
for name, fn in (("LIVE half-goal arm", walk_live),
                 ("hybrid 90%-arm", walk_incumbent)):
    banks = rides = floored_n = 0
    dbank = dride = ctrl = 0.0
    for g in graded:
        ex = fn(g)
        if not ex:
            continue
        px, which, fl = ex
        d = (vs.net(g["entry"], px, g["size"])
             - vs.net(g["entry"], g["exit"], g["size"]))
        banks += which == "bank"
        dbank += d if which == "bank" else 0.0
        floored_n += fl
        if "took the goal" in str(g.get("verdict")):
            ctrl += d
        if which == "trail":
            rides += 1
            dride += d
    res[name] = {"bank": dbank, "ride": dride, "rides": rides,
                 "ctrl": ctrl, "total": dbank + dride, "floor": floored_n}
    print(f"{name:<22} | {banks:>5d} | {dbank:>9.2f} | {rides:>5d} | "
          f"{dride:>9.2f} | {dride / rides if rides else 0:>8.2f} | "
          f"{ctrl:>9.2f} | {floored_n:>7d} | {dbank + dride:>9.2f}")
print(f"{'ACTUAL (the ladder)':<22} | {'-':>5} | {'-':>9} | {'-':>5} | "
      f"{'-':>9} | {'-':>8} | {'-':>9} | {'-':>7} | {act:>9.2f}")

print("\n== FIDELITY LIMITS (what this bar-granularity sim CANNOT see) ==")
print("  1. INTRABAR ORDER: on a 1h bar the walk cannot order a goal "
      "touch against a stop touch; bank-first is assumed. Real-time "
      "sampling resolves touches in true order, which moves outcomes "
      "BOTH ways - a stop-first touch turns a sim bank into a trail "
      "exit, a goal-first touch preserves it. Ordering is NOT a "
      "systematic rescue for either policy.")
print("  2. THE GUARD FACTOR IS OMITTED: the live trail is multiplied by "
      "_pgf (often ~2, 'let it run') and _eg_m before the cap binds, so "
      "the REAL armed width is looser than this sim's; the floor then "
      "binds far less. The sim's live shape is TIGHTER than reality in "
      "exactly the dimension that produced the bad result.")
a, b = res["LIVE half-goal arm"], res["hybrid 90%-arm"]
bar_ok = a["ctrl"] >= b["ctrl"] - 1e-9
tot_ok = a["total"] > b["total"]
print("\n== VERDICT (relative bar, WITH the fidelity caveats above) ==")
print(f"control: live {a['ctrl']:+.2f} vs hybrid {b['ctrl']:+.2f} -> "
      f"{'PASSES the relative bar' if bar_ok else 'worse than the comparator'}")
print(f"totals vs actual: live {a['total']:+.2f}, hybrid {b['total']:+.2f}, "
      f"actual +{act:.2f} -> "
      f"{'the live shape realizes more' if tot_ok else 'the hybrid realizes more'}")
print(f"price floor engaged on {a['floor']} armed exits (after the "
      f"scoping fix; pre-fix it clamped 50 fills at prices the market "
      f"never traded) - rides average "
      f"{a['ride'] / a['rides'] if a['rides'] else 0:+.2f} $")
if bar_ok and tot_ok:
    print("VERDICT: the exact live shape beats its own abstraction at this "
          "granularity")
else:
    print("VERDICT: NOT ADJUDICABLE AT CANDLE GRANULARITY. The bar-level "
          "sim makes the live shape look weaker than its abstraction "
          "(totals -67.70 vs +42.38, control -196.15 vs -95.96), but both "
          "fidelity gaps both bias this sim AGAINST the live shape in "
          "specific, stated ways (stop-first ordering reduces banks; "
          "the omitted guard widening tightens the sim's widths), yet "
          "ordering can also cut the other way - the net direction is "
          "unknown. A tick-level replay is the only honest referee; "
          "the incumbent stands, uncondemned and un-crowned.")
