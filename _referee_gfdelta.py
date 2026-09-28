"""_referee_gfdelta.py - THE REFEREE, RE-RUN AT PER-DAY GF (Sep 28).

The half-goal-arm thread's one deferred refinement, now that the guard
factor keeps its own history (profit_guard._append_history, the
factor_history() seam, commit 9f87266): every study so far priced its
simulated widths at ONE frozen factor - the state file's current value
- while the live machinery ACTUALLY runs each day at that day's factor.
The bracket bounded the error; this study MEASURES it once the series
is long enough to matter (~2 weeks of accrued rows, the operator's
ask).

THE WALK: the referee's EXACT branch structure (_livearm_referee.py,
459c02b - the parity rows prove the copy byte-faithful in effect), with
one change: every GF site inside the bar loop (ratchet width, armed
cap, lock_w) reads the factor that was LIVE on THAT BAR's MST day -
the writer's day key (self_adjust once per America/Denver day). The
factor a ride feels is the one standing while it rides, so the
refinement is bar-level, not per-trade.

ROWS:
  LIVE / hybrid          the referee's frozen-GF baseline (the state's
                         factor on every bar) - MUST reproduce the
                         committed numbers (+39.50 / +138.96); any
                         drift aborts the study before it reports.
  LIVE per-day GF /
  hybrid per-day GF      the same walks at each bar's real day factor,
                         chained backward before the series starts.

THE GATES:
  parity   live/hybrid frozen rows within +-0.5 of the committed
           referee numbers, else ABORT (exit 3) - a drift here would
           indict the copy, not the refinement.
  span     the series must cover >= MIN_DAYS distinct days (14, ~2
           weeks). Before that the study prints NOT-YET with the
           current span and exits 0 - the ask was to re-run "once ~2
           weeks accrue", so running early is honest refusal, not
           failure.

Read-only; pandas + the scout venv. ASCII-pure output.
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import _verdict_study as vs  # noqa: E402
import _gf_series            # noqa: E402  (the gate constants live there)
from _gf_series import GFSeries  # noqa: E402  (THE seam, shared)

HERE = Path(__file__).parent
ARM_FRAC = 0.5
RT_PCT = 0.08

GF_FROZEN = float(json.loads(
    (HERE / "profit_guard_state.json").read_text(encoding="utf-8")
).get("factor") or 1.0)

# the series seam (single source of the day mapping across the arc;
# frozen from the state file = the parity baseline, exactly what the
# committed referee priced)
GFS = GFSeries(frozen=GF_FROZEN)


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


def walk(g, policy, gf_mode):
    """The referee's EXACT walk (_livearm_referee.py), one change: the
    GF site is a per-bar lookup (gf_mode 'day') or the frozen state
    factor (gf_mode 'frozen' - the parity baseline). policy in
    {'live','hybrid'}; returns (exit_px, which, amb, floored) or None
    (censored / no data)."""
    entry, goal = g["entry"], g["goal"]
    bank_px = entry * (1 + goal / 100.0)
    floor_px = entry * (1 + 2 * RT_PCT / 100.0)
    arm_at = goal * (ARM_FRAC if policy == "live" else 0.9)
    armed = False
    peak, stop = entry, None
    amb = floored = 0
    try:
        w = _bars(g["pair"], g["en_ms"], g["ex_ms"])
    except Exception:
        return None
    if w.empty:
        return None
    for ts, hi, lo, op in zip(w["date"], w["high"], w["low"], w["open"]):
        gf = GFS.at(ts) if gf_mode == "day" else GF_FROZEN
        touch_goal = hi >= bank_px
        touch_stop = stop is not None and lo <= stop
        if touch_goal and touch_stop:
            amb += 1
        if touch_goal:                       # bank-first
            return bank_px, "bank", amb, floored
        if hi > peak:
            peak = hi
        if policy == "live":
            if not armed and goal > 0 and \
                    (peak / entry - 1.0) * 100.0 >= arm_at:
                armed = True
        wdt = ratchet_w(entry, peak) * gf
        if policy == "live" and armed:
            cap = max(goal * ARM_FRAC, 1.0) * gf
            lock_w = max(0.0, (1.0 - floor_px / peak) * 100.0) * gf
            cap = min(cap, lock_w) if lock_w > 0 else cap
            wdt = min(wdt, cap)
        if policy == "hybrid":
            if peak >= entry * (1 + arm_at / 100.0):
                stop = peak * (1 - wdt / 100.0)   # raw ratchet from arm
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


def main():
    """The executable study: rows, parity gate, span gate, delta.
    Exit 0 (report / NOT-YET), 3 (parity drift -> fix the copy)."""
    print(f"guard factor (frozen, state): {GFS.frozen:g}   "
          f"series days: {GFS.span_days}   gate: >= "
          f"{_gf_series.MIN_DAYS}")
    if GFS.span:
        print(f"series: {GFS.span} (earliest..latest), "
              f"latest factor {GFS.frozen:g}")

    graded = vs.grade_trades()
    act = sum(vs.net(g["entry"], g["exit"], g["size"]) for g in graded)
    print(f"trades: {len(graded)} (frozen at load)")

    res = {}
    print(f"\n{'policy':<22} | {'banks':>5} | {'bank $':>9} | {'rides':>5} | "
          f"{'ride $':>9} | {'ctrl $':>9} | {'amb':>3} | {'floor x':>7} | "
          f"{'TOTAL':>8}")
    print("-" * 100)
    for name, pol, mode in (("LIVE half-goal arm", "live", "frozen"),
                            ("hybrid 90%-arm", "hybrid", "frozen"),
                            ("LIVE per-day GF", "live", "day"),
                            ("hybrid per-day GF", "hybrid", "day")):
        banks = rides = amb = floored_n = 0
        dbank = dride = ctrl = 0.0
        missing = 0
        for g in graded:
            r = walk(g, pol, mode)
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
        res[(pol, mode)] = {"bank": dbank, "ride": dride, "ctrl": ctrl,
                            "total": dbank + dride, "amb": amb,
                            "floor": floored_n, "missing": missing}
        print(f"{name:<22} | {banks:>5} | {dbank:>9.2f} | {rides:>5} | "
              f"{dride:>9.2f} | {ctrl:>9.2f} | {amb:>3} | {floored_n:>7} | "
              f"{dbank + dride:>8.2f}")
    print(f"{'ACTUAL (the ladder)':<22} | {'-':>5} | {'-':>9} | {'-':>5} | "
          f"{'-':>9} | {'-':>9} | {'-':>3} | {'-':>7} | {act:>8.2f}")
    print(f"\nbar-level GF mapping: {GFS.n_series} bars on a series "
          f"day, {GFS.n_fallback} chained/fallback")

    # -- gate 1: the frozen rows must BE the referee ---------------------
    print("\n== PARITY vs the committed referee (459c02b) ==")
    print("referee: LIVE +39.50 (ctrl -126.67, floor 62) | "
          "HYBRID +138.96 (ctrl -68.59, floor 0)")
    lv, hy = res[("live", "frozen")], res[("hybrid", "frozen")]
    ok_live = abs(lv["total"] - 39.50) < 0.5 and abs(lv["ctrl"] + 126.67) < 0.5
    ok_hyb = abs(hy["total"] - 138.96) < 0.5 and abs(hy["ctrl"] + 68.59) < 0.5
    print(f"live reproduces:  {'OK' if ok_live else 'DRIFT'}   "
          f"hybrid reproduces: {'OK' if ok_hyb else 'DRIFT'}")
    if not (ok_live and ok_hyb):
        print("ABORT: the copy drifts from the committed referee - the "
              "delta below would indict the copy, not the refinement. "
              "Fix the walk first (see _floored_90arm.py's Sep 27 lesson).")
        return 3

    # -- gate 2: the series span -----------------------------------------
    lv_d, hy_d = res[("live", "day")], res[("hybrid", "day")]
    if not GFS.ready():
        print(f"\n== NOT YET ==")
        print(GFS.notyet_line() + ".")
        print("the per-day rows above are the chain-back preview (every "
              "pre-series bar rides the newest factor), not the answer - "
              "re-run when the span clears the gate.")
        print("the referee's verdict stands on the frozen factor until "
              "then; the bracket (b57aed4) remains the standing bound.")
        return 0

    # -- the delta, derived ----------------------------------------------
    print(f"\n== THE DELTA (series covers {GFS.span_days} days, "
          f"{GFS.span}) ==")
    d_live = lv_d["total"] - lv["total"]
    d_hyb = hy_d["total"] - hy["total"]
    print(f"LIVE   : frozen {lv['total']:+.2f} -> per-day {lv_d['total']:+.2f} "
          f"(delta {d_live:+.2f}; ctrl {lv['ctrl']:+.2f} -> "
          f"{lv_d['ctrl']:+.2f}, floor {lv['floor']} -> {lv_d['floor']})")
    print(f"hybrid : frozen {hy['total']:+.2f} -> per-day {hy_d['total']:+.2f} "
          f"(delta {d_hyb:+.2f}; ctrl {hy['ctrl']:+.2f} -> {hy_d['ctrl']:+.2f})")
    print(f"verdict shape: the per-day rows are the referee's answer at "
          f"the factors the machinery ACTUALLY ran; the frozen rows "
          f"(and the bracket) approximate them.")
    worse = (lv_d["total"] < act) != (lv["total"] < act)
    print(f"the LIVE-vs-ladder ordering {'FLIPS' if worse else 'holds'} "
          f"under the refinement ({'per-day' if worse else 'frozen'}: "
          f"{lv_d['total'] if worse else lv['total']:+.2f} vs {act:+.2f})")
if __name__ == "__main__":
    sys.exit(main())
