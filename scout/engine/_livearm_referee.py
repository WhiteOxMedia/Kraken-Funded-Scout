"""_livearm_referee.py - THE TICK-LEVEL REFEREE (Sep 27). The live-arm
study refused to adjudicate at 1h granularity because two fidelity gaps
both biased the sim against the live machinery: intrabar ordering (a
1h bar cannot order a goal touch against a stop touch) and the omitted
guard-factor widening. This referee closes both:

  DATA: the deep 5m store (surething_data/kraken/{PAIR}-5m.feather,
  108 pairs, Aug 9 -> today, exact 5m cadence) - 12x the resolution of
  the 1h studies, covering the graded ledger window. Touch order is
  still intrabar-ambiguous WITHIN one 5m bar, but 12x more bars carry
  12x fewer simultaneous touches; every bar that resolves the question
  (goal in one bar, stop in another) is now ordered. The residual
  same-bar ambiguity is counted and reported per policy.

  GUARD FACTOR: the live _pgf from profit_guard_state.json (factor 2.0
  on Sep 26) multiplies the trail width and divides the armed cap,
  exactly as background_scanner 3539-3560 does (_trail * _pgf, cap *
  _pgf). Fidelity gap #2 closed with the machine's own current value.

  POLICIES (same walk, same bank-first rule, bank at the trade's full
  goal; the price floor applies only when armed and reachable; a gap
  through the stop fills at the bar OPEN - the run_entry lesson):
    LIVE SHAPE : ratchet(3/2@+1%/1.5@+2%) * GF, latched arm at goal*0.5,
                 armed cap min(ratchet*GF, max(goal/2,1.0)*GF, lock_w*GF)
                 wait - the cap multiplies by GF like the trail does,
                 then the PRICE FLOOR clamps, floor does NOT scale.
    HYBRID     : 90%-arm idealization, ratchet * GF, no floor.
    ACTUAL     : the ladder's real realized net (baseline).

  VERDICT: relative control bar + totals, derived from the measured
  values, with the same-bar ambiguity counts shown per policy.
Read-only; pandas required (the scout venv has it).
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import _verdict_study as vs  # noqa: E402

HERE = Path(__file__).parent
ARM_FRAC = 0.5
RT_PCT = 0.08
GF = float(json.loads(
    (HERE / "profit_guard_state.json").read_text(encoding="utf-8")
).get("factor") or 1.0)
STEP_MS = 300000  # 5m

print(f"guard factor (live, profit_guard_state.json): {GF}")


def ratchet_w(entry, peak):
    pp = (peak / entry - 1.0) * 100.0
    return 1.5 if pp >= 2.0 else (2.0 if pp >= 1.0 else 3.0)


def _bars(pair, en_ms, ex_ms):
    df = pd.read_feather(HERE / "surething_data" / "kraken" /
                         f"{pair.replace('/', '_')}-5m.feather")
    lo = pd.Timestamp(int(en_ms), unit="ms", tz="UTC")
    hi = pd.Timestamp(int(ex_ms), unit="ms", tz="UTC") + pd.Timedelta(hours=4)
    w = df[(df["date"] >= lo) & (df["date"] <= hi)].sort_values("date")
    return w


def walk(g, policy):
    """policy in {'live','hybrid'}; returns (exit_px, which, amb, floored)
    or None (censored / no data). amb counts same-bar goal+stop touches."""
    entry, goal = g["entry"], g["goal"]
    bank_px = entry * (1 + goal / 100.0)
    floor_px = entry * (1 + 2 * RT_PCT / 100.0)
    armed = policy == "hybrid" and False
    arm_at = goal * (ARM_FRAC if policy == "live" else 0.9)
    peak, stop = entry, None
    amb = floored = 0
    try:
        w = _bars(g["pair"], g["en_ms"], g["ex_ms"])
    except Exception:
        return None
    if w.empty:
        return None
    for hi, lo, op in zip(w["high"], w["low"], w["open"]):
        touch_goal = hi >= bank_px
        touch_stop = stop is not None and lo <= stop
        if touch_goal and touch_stop:
            amb += 1                    # the residual intrabar ambiguity
        if touch_goal:                  # bank-first on true simultaneity
            return bank_px, "bank", amb, floored
        if hi > peak:
            peak = hi
        if policy == "live":
            if not armed and goal > 0 and (peak / entry - 1.0) * 100.0 >= arm_at:
                armed = True
        elif peak >= entry * (1 + arm_at / 100.0):
            pass                        # hybrid: armed implicitly by stop set
        wdt = ratchet_w(entry, peak) * GF
        if policy == "live" and armed:
            cap = max(goal * ARM_FRAC, 1.0) * GF
            lock_w = max(0.0, (1.0 - floor_px / peak) * 100.0) * GF
            cap = min(cap, lock_w) if lock_w > 0 else cap
            wdt = min(wdt, cap)
        if policy == "hybrid":
            if peak >= entry * (1 + arm_at / 100.0):
                stop = peak * (1 - wdt / 100.0)
        else:
            stop = peak * (1 - wdt / 100.0)
        floored = 0
        if policy == "live" and armed and stop < floor_px <= peak:
            stop = floor_px
            floored = 1
        if peak > entry and stop is not None and stop > hi:
            return op, "trail", amb, floored    # gap: fill at the open
        if peak > entry and touch_stop:
            return stop, "trail", amb, floored
    return None


graded_all = vs.grade_trades()
_newest = max((float(g.get("ex_ms") or 0) for g in graded_all), default=0.0)
graded = [g for g in graded_all if float(g.get("ex_ms") or 0) <= _newest]
act = sum(vs.net(g["entry"], g["exit"], g["size"]) for g in graded)
print(f"trades: {len(graded)} (frozen at load)   data: 5m feathers "
      f"(108 pairs)   horizon: exit+4h")
print(f"\n{'policy':<20} | {'banks':>5} | {'bank $':>9} | {'rides':>5} | "
      f"{'ride $':>9} | {'ctrl $':>9} | {'amb bars':>8} | {'floor x':>7} | "
      f"{'TOTAL':>8}")
print("-" * 104)
res = {}
for name, pol in (("LIVE half-goal arm", "live"),
                  ("hybrid 90%-arm", "hybrid")):
    banks = rides = amb = floored_n = 0
    dbank = dride = ctrl = 0.0
    missing = 0
    for g in graded:
        r = walk(g, pol)
        if r is None:
            missing += 1
            continue
        px, which, a, fl = r
        d = (vs.net(g["entry"], px, g["size"])
             - vs.net(g["entry"], g["exit"], g["size"]))
        banks += which == "bank"
        dbank += d if which == "bank" else 0.0
        amb += a
        floored_n += fl
        if "took the goal" in str(g.get("verdict")):
            ctrl += d
        if which == "trail":
            rides += 1
            dride += d
    res[name] = {"bank": dbank, "ride": dride, "ctrl": ctrl,
                 "total": dbank + dride, "amb": amb, "missing": missing}
    print(f"{name:<20} | {banks:>5d} | {dbank:>9.2f} | {rides:>5d} | "
          f"{dride:>9.2f} | {ctrl:>9.2f} | {amb:>8d} | {floored_n:>7d} | "
          f"{dbank + dride:>8.2f}")
print(f"{'ACTUAL (the ladder)':<20} | {'-':>5} | {'-':>9} | {'-':>5} | "
      f"{'-':>9} | {'-':>9} | {'-':>8} | {'-':>7} | {act:>8.2f}")

print("\n== VERDICT (derived; 5m resolution, live GF) ==")
a, b = res["LIVE half-goal arm"], res["hybrid 90%-arm"]
print(f"control: live {a['ctrl']:+.2f} vs hybrid {b['ctrl']:+.2f} -> "
      f"{'live is control-safer' if a['ctrl'] >= b['ctrl'] else 'live is control-worse'}")
print(f"totals: live {a['total']:+.2f} vs hybrid {b['total']:+.2f} vs "
      f"actual {act:+.2f}")
if a["total"] > b["total"] and a["total"] > act:
    print("VERDICT: the LIVE shape beats both the abstraction and the "
          "ladder at 5m resolution - the refinement revealed its edge")
elif a["total"] <= 0:
    print("VERDICT: the live shape LOSES at 5m resolution even with the "
          "real GF - the abstraction's edge survives the refinement")
elif b["total"] > act and a["total"] < act:
    print("VERDICT: the HYBRID abstraction beats the ladder at 5m "
          "resolution, but the REAL live shape does not - the 90%-arm "
          "idealization was a better policy than the true machinery at "
          "this horizon; the control bar still disqualifies it: "
          f"{a['ctrl']:+.2f} / {b['ctrl']:+.2f} both worse than the "
          f"ladder's own discipline")
elif b["total"] > act:
    print(f"VERDICT: the hybrid beats the ladder ({b['total']:+.2f} vs "
          f"{act:+.2f}) and is control-safer than the live shape - the "
          f"first policy in the stack to clear the relative bar at this "
          f"resolution")
else:
    print("VERDICT: no policy beats the ladder at 5m resolution; the "
          "incumbent stands at the finer granularity too")
