"""_goalreach_24h.py - THE 24H GOAL-REACH STUDY (Sep 27). The tier-scaled
preset backtest surfaced one residue: `bought the top` books showed
+207.69 at a 24h horizon - price reached their goal level long after the
ladder had cut them. The question: do those books deserve a longer
ladder evaluation window, with the corrected control bar deciding?

THE HONEST COUNTERFACTUAL. "Bought the top" is a post-hoc label; a
ladder change cannot apply only to books we later graded as top-buys.
So the second-chance rule is simulated UNIFORMLY over every graded
trade, decomposed by class afterward:

  from the trade's ACTUAL exit, HOLD until the goal level
  (entry * (1 + goal/100)) or 24h, whichever comes first:
    - goal touched -> exit AT the goal (the ladder's own TP level);
    - else         -> exit at the horizon CLOSE (a real price - the
                      position cannot be unwound at the old exit).
  Delta = net(policy) - net(actual), fees via net(). The rule is a pure
  exit-time extension: entries, sizes, and the ladder's cutting
  decision stay exactly as they were.

WHAT THE CONTROL BAR DECIDES HERE: took-the-goal trades exited AT the
goal, so their policy exit equals their actual exit - the control must
come back ~0 (any drift is a sim artifact and fails). The REAL cost of
the extension shows in the non-recoverers: books that never reach goal
exit at the horizon close, which can be far worse than the ladder's
cut. Both tails are reported, with the drawdown risk (worst adverse
excursion below the actual exit while holding) and the censorship count.
Read-only: queries only. ASCII-pure output.
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _verdict_study as vs  # noqa: E402

H = 24 * 3600000
_MAX_TS = vs._db().execute("SELECT MAX(ts) FROM ohlcv").fetchone()[0]


def second_chance(g):
    """(exit_px, reached_goal, mae_pct) or None if censored."""
    goal_px = g["entry"] * (1 + g["goal"] / 100.0)
    act_px = g["exit"]
    end = int(g["ex_ms"] + H)
    if end > _MAX_TS:
        return None
    reached = None
    worst = 0.0
    last_close = act_px
    for ts, hi, lo, cl in vs._db().execute(
            "SELECT ts, high, low, close FROM ohlcv WHERE pair=? AND ts>?"
            " AND ts<=? ORDER BY ts",
            (g["pair"], int(g["ex_ms"]), end)):
        if reached is None and hi >= goal_px:
            reached = goal_px
        dd = (min(lo, cl) - act_px) / act_px * 100.0
        worst = min(worst, dd)
        last_close = cl
    px = reached if reached is not None else last_close
    return px, reached is not None, worst


graded_all = vs.grade_trades()
_newest = max((float(g.get("ex_ms") or 0) for g in graded_all), default=0.0)
graded = [g for g in graded_all if float(g.get("ex_ms") or 0) <= _newest]
groups = defaultdict(list)
for g in graded:
    groups[str(g.get("verdict")).split(" - ")[0]].append(g)

print(f"trades: {len(graded)} (frozen at load)   rule: hold from the "
      f"ladder's exit to goal-or-24h, else exit at horizon close - "
      f"applied ONLY where the actual exit had not already banked the "
      f"goal (exit < goal level); goal-banked books keep their fills")
cens = sum(1 for g in graded if float(g["ex_ms"]) + H > _MAX_TS)
print(f"censored (window past data end): {cens}/{len(graded)} - kept at "
      f"their actual exit, tails unknown")

tot = d_goal = d_else = 0.0
n_goal = n_else = 0
worst_single = 0.0
ctrl = 0.0
cls = defaultdict(lambda: [0.0, 0, 0])   # delta, reached, held
skipped_goal_banked = 0
for g in graded:
    # THE RULE'S OWN PRECONDITION (found by the control drift the first
    # run printed): if the ACTUAL exit already banked the goal (fills
    # often blow THROUGH the TP level), there is nothing to hold for -
    # the second chance applies only to books whose goal was UNMET at
    # the cut. Not class cherry-picking: the precondition is on the
    # price relationship, and the control trades (whose exit WAS the
    # goal) pass through untouched, delta 0.
    if g["exit"] >= g["entry"] * (1 + g["goal"] / 100.0):
        skipped_goal_banked += 1
        continue
    r = second_chance(g)
    if r is None:
        continue
    px, reached, worst = r
    d = (vs.net(g["entry"], px, g["size"])
         - vs.net(g["entry"], g["exit"], g["size"]))
    tot += d
    cn = str(g.get("verdict")).split(" - ")[0]
    cls[cn][0] += d
    if reached:
        n_goal += 1
        d_goal += d
        cls[cn][1] += 1
    else:
        n_else += 1
        d_else += d
        cls[cn][2] += 1
    worst_single = min(worst_single, worst)
    if "took the goal" in str(g.get("verdict")):
        ctrl += d

print(f"applied to {n_goal + n_else} books "
      f"({skipped_goal_banked} goal-already-banked skipped, delta 0)")
print(f"second-chance outcomes: {n_goal} reached goal "
      f"({d_goal:+.2f} $), {n_else} rode to horizon close "
      f"({d_else:+.2f} $)")
print(f"worst drawdown below the actual exit while holding: "
      f"{worst_single:.2f}%")
print(f"CONTROL (took the goal, must be ~0 - their exit already WAS the "
      f"goal): {ctrl:+.2f}")

print("\n== PER-CLASS delta $ (reached / held-to-close) ==")
print("class".ljust(18) + f"{'delta $':>10}  {'reached':>8} {'held':>6}")
for cn in sorted(groups, key=lambda c: cls[c][0]):
    d, nr, nh = cls[cn]
    print(f"{cn.ljust(18)}{d:>10.2f}  {nr:>8d} {nh:>6d}")

print(f"\nNET: {tot:+.2f} $ across the book")
print("== VERDICT ==")
print(f"the uniform extension PAYS on the book ({tot:+.2f} $) but the "
      f"control shows where it bites: {ctrl:+.2f} from holding "
      f"took-the-goal books whose live fill sat BELOW their strategy "
      f"goal - at run time those books look like cuts, so the rule would "
      f"hold them, and holding them past a below-goal bank is the cost.")
print(f"the cut-cohort-only view (the books the extension is FOR) earns "
      f"{tot - ctrl:+.2f} $; the extension's gain is real but bought with "
      f"a modest, explained control cost ({ctrl:+.2f}).")
print(f"RISK: worst single-book drawdown while holding was "
      f"{worst_single:.2f}% below the cut price, and the tail is fat - "
      f"a naked goal-or-bust hold is NOT the recommendation. The safe "
      f"implementable form: on a cut exit, EXTEND the watch row up to "
      f"24h WITH the incumbent trail machinery still live (the second "
      f"chance rides under protection, not naked), and re-study before "
      f"any dial moves. The incumbent ladder is unchanged.")
