"""_hybrid_trail_sweep.py — THE TP-PRESERVING HYBRID (Sep 27): the
fixed-width armed trail re-simmed with the goal bank INTACT. The first
sweep (_trail_k_sweep.py) let the armed trail replace the ladder from
the arm bar onward, so trades that were going to BANK the goal exited
at the stop instead — every width broke the took-the-goal control
(-43.53 / -86.89 / -131.28 / -145.54 $). This hybrid keeps the bank
live and replaces only the ladder's post-goal RIDE leg, which is the
machinery's own shape (TP +10% banks the goal; the ratchet/momentum
legs manage the ride after).

Sim rules, per graded trade, over the study horizon (exit + AFTER_MS,
1h candles, fees via net()):
  - BARS ARE WALKED ONCE; within each bar, GUARD FIRST: if the bar's
    high reaches the goal level, the trade BANKS at the goal and stops
    (a banked trade can never trail out — the control guard becomes
    structurally satisfiable at every width);
  - if not banked, the armed trail runs exactly like _sim_armed: it
    arms when the peak reaches goal*0.90, width P off the running
    peak, exits on a low touching the stop;
  - UNTRIGGERED trades keep the ladder's ACTUAL exit (the policy never
    out-decided the ladder);
  - VERDICT COHORT: only the trades the policy actually exits by trail
    (the armed near-miss runs that went back below 90% without banking
    — where a ride leg legitimately has a say). Control cohort: the
    took-the-goal trades — with bank-first they are UNTOUCHABLE, so
    their control line is trivially zero and reported as such.
Delta = hypothetical net - actual net (negative means the hybrid would
have realized LESS than the ladder did; the ladder's own post-goal
ride legs are the incumbent this must beat).
Read-only: queries only.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _verdict_study as vs  # noqa: E402

ARM_FRAC = 0.9  # _sim_armed's own arm bar (90% of goal)


def hybrid_exit(g, P):
    """(exit_px, 'bank'|'trail') or None (untriggered -> actual exit)."""
    goal = g["entry"] * (1.0 + g["goal"] / 100.0)
    arm = g["entry"] * (1.0 + g["goal"] * ARM_FRAC / 100.0)
    peak, stop = g["entry"], None
    for ts, hi, lo in vs._db().execute(
            "SELECT ts, high, low FROM ohlcv WHERE pair=? AND ts>=? "
            "AND ts<=? ORDER BY ts",
            (g["pair"], int(g["en_ms"]), int(g["ex_ms"] + vs.AFTER_MS))):
        if hi >= goal:                       # GUARD FIRST: the bank is live
            return goal, "bank"
        if hi > peak:
            peak = hi
            if peak >= arm:
                stop = peak * (1 - P / 100.0)
        if stop is not None and lo <= stop:
            return stop, "trail"
    return None


graded = vs.grade_trades()
ctrl = [g for g in graded if "took the goal" in str(g.get("verdict"))]
print(f"trades: {len(graded)} (control cohort: {len(ctrl)})   "
      f"horizon: exit+{vs.AFTER_MS // 3600000}h   fees: net() RT_COST")
print(f"\n{'width':>6} | {'trail x':>7} | {'delta $':>9} | {'per-trade':>9} | "
      f"{'banks left':>10} | {'ctrl $':>8} | {'bank-delta $':>13}")
rows = {}
for P in (1.0, 1.5, 2.0, 3.0):
    tot, n = 0.0, 0
    banks = 0
    dbank = 0.0
    dc = 0.0
    for g in graded:
        ex = hybrid_exit(g, P)
        if ex is None:
            continue
        px, which = ex
        d = vs.net(g["entry"], px, g["size"]) - vs.net(g["entry"], g["exit"], g["size"])
        banks += which == "bank"
        # CODE RABBIT MAJOR (Sep 27): bank exits are policy exits too —
        # a bank can fill BELOW the ladder's actual exit (the ladder
        # rode past goal on some books). Report them separately AND
        # count them in the control so a clean control cannot hide a
        # bank-at-a-loss cohort.
        if which == "bank":
            dbank += d
        if "took the goal" in str(g.get("verdict")):
            dc += d
        if which == "trail":
            tot += d
            n += 1
    per = tot / n if n else 0.0
    rows[P] = (tot, n, per, banks, dc)
    print(f"{P:>6.1f} | {n:>7d} | {tot:>9.2f} | {per:>9.2f} | "
          f"{banks:>10d} | {dc:>8.2f} | bank-delta {dbank:+.2f}")

print("\n== THE INCUMBENT through the same hybrid walk: the LIVE RATCHET "
      "(3.0 base, 2.0 at peak >= +1%, 1.5 at +2%) ==")

def hybrid_ratchet(g):
    """Bank-first walk, post-arm ride = the live ratchet width."""
    goal = g["entry"] * (1 + g["goal"] / 100.0)
    arm = g["entry"] * (1 + g["goal"] * ARM_FRAC / 100.0)
    peak, stop = g["entry"], None
    for ts, hi, lo in vs._db().execute(
            "SELECT ts, high, low FROM ohlcv WHERE pair=? AND ts>=? "
            "AND ts<=? ORDER BY ts",
            (g["pair"], int(g["en_ms"]), int(g["ex_ms"] + vs.AFTER_MS))):
        if hi >= goal:
            return goal, "bank"
        if hi > peak:
            peak = hi
            pp = (peak / g["entry"] - 1.0) * 100.0
            w = 1.5 if pp >= 2.0 else (2.0 if pp >= 1.0 else 3.0)
            if peak >= arm:
                stop = peak * (1 - w / 100.0)
        if stop is not None and lo <= stop:
            return stop, "trail"
    return None

_rt, _rn, _rb, _rc, _rdb = 0.0, 0, 0, 0.0, 0.0
for g in graded:
    ex = hybrid_ratchet(g)
    if not ex:
        continue
    px, which = ex
    d = vs.net(g["entry"], px, g["size"]) - vs.net(g["entry"], g["exit"], g["size"])
    _rb += which == "bank"
    # corrected control accounting: EVERY simulated exit counts
    if "took the goal" in str(g.get("verdict")):
        _rc += d
    if which == "bank":
        _rdb += d
    if which == "trail":
        _rt += d
        _rn += 1
print(f"ratchet: trail x {_rn}, delta {_rt:+.2f} $ "
      f"({_rt / _rn if _rn else 0:+.2f}/trade), banks {_rb}, "
      f"ctrl {_rc:+.2f}, bank-delta {_rdb:+.2f}")

print("\n== VERDICT (RELATIVE BAR, corrected accounting: a width must hold "
      "a positive ride edge WITHOUT costing more control than the "
      f"incumbent ratchet's own under the identical walk, {_rc:+.2f}) ==")
ok = {P: r for P, r in rows.items()
      if r[1] and r[2] > 0 and r[4] >= _rc - 1e-9}
if not ok:
    print("no width holds a positive ride edge without a worse control "
          "cost than the incumbent ratchet's own; the ratchet stands")
else:
    best = max(ok, key=lambda P: ok[P][2])
    tot, n, per, banks, dc = ok[best]
    print(f"best hybrid width: {best:.1f} — +{per:.2f} $/trade over {n} "
          f"near-miss rides ({tot:.2f} $ total), {banks} goals banked, "
          f"control {dc:+.2f} vs incumbent {_rc:+.2f}")
