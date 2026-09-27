"""_guardfree_trail.py — THE GUARD-FREE TIGHT TRAIL (Sep 27 question):
the preset backtest showed the conservative preset's MEDICINE (its 5%
trail saving +114.16 at 4h / +208.58 at 48h on `stayed too long`)
riding with its POISON (the fixed +6% guard bled −70.73 from
`took the goal`). The question: keep the ladder intact — the bank at
the FULL goal stays live (no fixed guard below it, so the poison is
gone BY CONSTRUCTION) — and tighten only the post-goal trail to a flat
1.0% (armed at 90% of the goal, the machinery's shape). Does the
stayed-too-long medicine survive without the guard?

Same walk as _hybrid_trail_sweep: bank-first bar ordering (a bar whose
high reaches the goal banks at the goal), armed trail at width w off
the running peak, untriggered trades keep the ladder's ACTUAL exit,
fees via net(), horizon exit + AFTER_MS. Two width policies compared
per verdict class: FLAT 1.0 (the variant) and the INCUMBENT RATCHET
(3.0 base, 2.0 at +1% peak, 1.5 at +2%) — the fair comparator this
must beat on the ride cohort without touching the control.
Read-only: queries only.
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _verdict_study as vs  # noqa: E402

ARM_FRAC = 0.9


def walk(g, width_fn):
    """(exit_px, 'bank'|'trail') or None (untriggered -> actual exit)."""
    goal = g["entry"] * (1 + g["goal"] / 100.0)
    arm = g["entry"] * (1 + g["goal"] * ARM_FRAC / 100.0)
    peak, stop = g["entry"], None
    for ts, hi, lo in vs._db().execute(
            "SELECT ts, high, low FROM ohlcv WHERE pair=? AND ts>=? "
            "AND ts<=? ORDER BY ts",
            (g["pair"], int(g["en_ms"]), int(g["ex_ms"] + vs.AFTER_MS))):
        if hi >= goal:                       # the FULL-goal bank is live
            return goal, "bank"
        if hi > peak:
            peak = hi
            if peak >= arm:
                stop = peak * (1 - width_fn(g["entry"], peak) / 100.0)
        if stop is not None and lo <= stop:
            return stop, "trail"
    return None


def ratchet_w(entry, peak):
    pp = (peak / entry - 1.0) * 100.0
    return 1.5 if pp >= 2.0 else (2.0 if pp >= 1.0 else 3.0)


POLICIES = [("flat 1.0", lambda e, p: 1.0),
            ("ratchet (incumbent)", ratchet_w)]

graded = vs.grade_trades()
groups = defaultdict(list)
for g in graded:
    groups[str(g.get("verdict")).split(" - ")[0]].append(g)
print(f"trades: {len(graded)}   horizon: exit+{vs.AFTER_MS // 3600000}h   "
      f"fees: net() RT_COST   arm: {ARM_FRAC:.0%} of goal")
print(f"\n{'policy':<21} | {'ride x':>6} | {'delta $':>9} | {'per-trade':>9} | "
      f"{'banks':>5} | {'ctrl $':>8} | per-class delta $")
print("-" * 100)
for name, fn in POLICIES:
    tot = rides = banks = 0
    tot, rides, banks = 0.0, 0, 0
    ctrl = 0.0
    cls = defaultdict(float)
    for g in graded:
        ex = walk(g, fn)
        if not ex:
            continue
        px, which = ex
        d = (vs.net(g["entry"], px, g["size"])
             - vs.net(g["entry"], g["exit"], g["size"]))
        banks += which == "bank"
        if which == "trail":
            tot += d
            rides += 1
            cn = str(g.get("verdict")).split(" - ")[0]
            cls[cn] += d
            if "took the goal" in str(g.get("verdict")):
                ctrl += d
    detail = "  ".join(f"{k}={v:+.2f}" for k, v in sorted(cls.items()))
    print(f"{name:<21} | {rides:>6d} | {tot:>9.2f} | {tot / rides if rides else 0:>9.2f} | "
          f"{banks:>5d} | {ctrl:>8.2f} | {detail}")

print("\n== VERDICT ==")
print("the question: does stayed-too-long keep its savings with the "
      "guard poison gone, and what does the tight trail cost the control?")
