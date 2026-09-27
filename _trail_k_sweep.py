"""_trail_k_sweep.py — THE ARMED-TRAIL WIDTH SWEEP over the graded ledger
(the Sep 27 request: run TRAIL_SIM_ABLE = (1.0, 1.5, 2.0, 3.0) against
recent history and recommend the width with the evidence).

Uses _verdict_study's own machinery so the numbers are the study's
numbers: grade_trades() grades the REAL trade ledger (Sep 3 -> today),
_sim_armed(rows, P) sims the post-goal-armed trail (arms at 90% of the
trade's goal — the live machinery's shape) at a FIXED width P and
returns (sum delta $, n) vs what each trade ACTUALLY realized, fees
included via net(). A dial is only defensible if
  (a) total and per-trade delta are positive, and
  (b) the took-the-goal CONTROL is not made worse (a good dial must not
      break what already works),
evaluated per verdict class (one class's medicine must not be another's
poison). The live ratchet (3% -> 2% @ +1% -> 1.5% @ +2%) is reported as
its own row over every graded trade for context.
Read-only: OHLCV + ledgers are queried, nothing is written.
"""
import sys
from collections import defaultdict

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
import _verdict_study as vs  # noqa: E402

graded = vs.grade_trades()
print(f"graded trades: {len(graded)}")

groups = defaultdict(list)
ctrl = []
for g in graded:
    name = str(g.get("verdict")).split(" - ")[0]
    groups[name].append(g)
    if "took the goal" in str(g.get("verdict")):
        ctrl.append(g)

print("\n== FIXED-WIDTH ARMED TRAIL (arms at 90% of goal; delta $ vs actual) ==")
print(f"{'width':>6} | {'total $':>9} | {'n':>3} | {'per-trade':>9} | "
      f"{'ctrl $':>9} | {'ctrl n':>6} | guard")
results = {}
for P in (1.0, 1.5, 2.0, 3.0):
    tot_d = tot_n = 0
    per_class = {}
    for name in sorted(groups):
        d, n = vs._sim_armed(groups[name], P)
        per_class[name] = (round(d, 2), n)
        tot_d += d
        tot_n += n
    dc, nc = vs._sim_armed(ctrl, P)
    guard = "OK" if (nc == 0 or dc >= 0) else "BROKEN"
    results[P] = {"total": tot_d, "n": tot_n, "per": (tot_d / tot_n) if tot_n else 0.0,
                  "ctrl": (round(dc, 2), nc), "classes": per_class, "guard": guard}
    print(f"{P:>6.1f} | {tot_d:>9.2f} | {tot_n:>3d} | {tot_d / tot_n if tot_n else 0:>9.2f} | "
          f"{dc:>9.2f} | {nc:>6d} | {guard}")

print("\n== PER-CLASS BREAKOUT (delta $, n) ==")
names = sorted(groups)
hdr = "width |" + "|".join(f" {n[:14]:>14} " for n in names)
print(hdr)
for P in (1.0, 1.5, 2.0, 3.0):
    cells = []
    for n in names:
        d, k = results[P]["classes"][n]
        cells.append(f" {d:>7.2f}/{k:<3d} ")
    print(f"{P:>5.1f} |" + "|".join(cells))

print("\n== THE LIVE RATCHET (3%->2%@+1%->1.5%@+2%, Sep-21 re-base) for context ==")
live_d = live_n = 0
for g in graded:
    ex = vs._ratchet_exit(g, "live")
    if ex is None:
        continue
    live_d += vs.net(g["entry"], ex[0], g["size"]) - vs.net(g["entry"], g["exit"], g["size"])
    live_n += 1
print(f"hypothetical delta vs actual: {live_d:.2f} $ over {live_n} touched trades")

elig = {P: r for P, r in results.items() if r["guard"] == "OK" and r["per"] > 0}
print("\n== VERDICT ==")
if not elig:
    print("no width clears the guard + positive-edge bar; the incumbent stands")
else:
    bestP = max(elig, key=lambda P: elig[P]["per"])
    print(f"best defensible width: {bestP:.1f} "
          f"({elig[bestP]['per']:.2f} $/trade over {elig[bestP]['n']} touched trades; "
          f"control {elig[bestP]['ctrl']})")
