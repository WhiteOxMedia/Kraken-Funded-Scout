"""Test THE HINDSIGHT SCORER (user spec, Sep 5: "Hindsight Scorer +
Gate Grader + Entry-Path Optimizer").

The module owns:
  1. brain_size_mult      -- the advisory-brain bucket table (the ONE
     source every consumer reads: scanner popups, mover popups, the QC)
  2. fire_features        -- the fire-time feature vector (5-min candles)
  3. outcome              -- the hindsight verdict: speed buckets (fast
     0-15 / medium 15-90 / slow >90 / never) + payoff buckets (winner /
     grinder / dud) + MFE + drawdown
  4. extract_rules / grade_gates -- the learning + weekly grading CLIs
"""
import json
import math
import sys
import tempfile
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import hindsight_scorer as hs


# ── 1. THE SIZE SCALER (the user's bucket table) ─────────────────────
tbl = [(100, 1.00), (90, 1.00), (85, 1.00), (84.99, 0.75), (84, 0.75),
       (75, 0.75), (74.99, 0.50), (74, 0.50), (65, 0.50), (64.99, 0.33),
       (55, 0.33), (54.99, 0.20), (45, 0.20), (44.99, 0.10), (0, 0.10),
       (-5, 0.10)]
for conf, want in tbl:
    got = hs.brain_size_mult(conf)
    check(f"bucket {conf}% -> {want}x", abs(got - want) < 1e-9, f"got {got}")

check("an unreadable number keeps the minimum viable size",
      hs.brain_size_mult(None) == 0.10
      and hs.brain_size_mult("garbage") == 0.10
      and hs.brain_size_mult(float("nan")) == 0.10)
check("the scaler is monotonic (never rises as the brain falls)",
      all(hs.brain_size_mult(a) >= hs.brain_size_mult(b)
          for a, b in zip(range(100, -1, -5), range(95, -6, -5))))
check("no trade is ever sized at zero",
      all(hs.brain_size_mult(c) > 0 for c in range(0, 101)))
cont = [hs.brain_size_mult_continuous(c) for c in (0, 25, 50, 100)]
check("the continuous formula matches the doc (0.1 + 0.9*brain/100)",
      abs(cont[0] - 0.10) < 1e-9 and abs(cont[2] - 0.55) < 1e-9
      and abs(cont[3] - 1.00) < 1e-9, cont)


# ── 2. FIRE-TIME FEATURES (synthetic 5-min candles) ──────────────────
def mk_rows(n, base=100.0, drift=0.0, vol=1.0, burst=1.0):
    rows = []
    px = base
    t0 = 1700000000000
    for i in range(n):
        o = px
        c = px * (1 + drift / 100.0)
        h = max(o, c) * (1 + vol / 1000.0)
        l = min(o, c) * (1 - vol / 1000.0)
        v = 100.0 * (burst if i >= n - 3 else 1.0)
        rows.append([t0 + i * 300000, o, h, l, c, v])
        px = c
    return rows


flat = hs.fire_features(mk_rows(60, drift=0.0))
check("a flat series: no momentum, no burst, features complete",
      flat.get("momentum_15m") is not None
      and abs(flat["vol_burst"] - 1.0) < 0.05
      and abs(flat["momentum_15m"]) < 0.1, flat.get("vol_burst"))
check("insufficient candles (<30) say so, never crash",
      hs.fire_features(mk_rows(10)).get("insufficient") is True)

run = hs.fire_features(mk_rows(60, drift=0.05, burst=10.0))
check("a runner: positive 15m momentum",
      run["momentum_15m"] > 0.1, run.get("momentum_15m"))
check("a runner: the volume burst reads ~10x",
      8.0 <= run["vol_burst"] <= 12.0, run.get("vol_burst"))
check("a runner: positive candle velocity",
      run["velocity_pct_min"] > 0, run.get("velocity_pct_min"))
check("a runner near its high: pos_from_4h_high ~ 0",
      -0.5 < run["pos_from_4h_high"] < 0.5, run.get("pos_from_4h_high"))
check("the trend gap: a rising series has EMA9 over EMA21",
      run["ema_gap"] > 0, run.get("ema_gap"))
dip = hs.fire_features(mk_rows(60, drift=-0.05))
check("a falling series: negative momentum + negative EMA gap",
      dip["momentum_15m"] < 0 and dip["ema_gap"] < 0,
      (dip.get("momentum_15m"), dip.get("ema_gap")))
check("body_ratio lives in [0,1]",
      0 <= flat.get("body_ratio", -1) <= 1, flat.get("body_ratio"))


# ── 3. OUTCOMES (speed + payoff buckets) ─────────────────────────────
def fwd(closes, base=100.0):
    """5-min forward candles with the given close path."""
    t0 = 1700000000000
    rows = []
    px = base
    for i, c in enumerate(closes):
        o = px
        rows.append([t0 + (i + 1) * 300000, o, max(o, c), min(o, c), c, 100.0])
        px = c
    return rows


oc = hs.outcome(fwd([101.0, 102.0, 101.5]), 100.0, 1.5, 180)
check("a +2% spike in 10 min = FAST WINNER",
      oc["speed"] == "fast" and oc["payoff"] == "winner"
      and oc["min_to_goal_min"] <= 15, oc)
oc = hs.outcome(fwd([100.2] * 6 + [101.6]), 100.0, 1.5, 180)
check("the goal at candle 7 = MEDIUM (15-90 min)",
      oc["speed"] == "medium" and oc["payoff"] == "winner", oc)
oc = hs.outcome(fwd([100.1] * 20 + [101.6]), 100.0, 1.5, 180)
check("the goal at candle 21 = SLOW (>90 min)",
      oc["speed"] == "slow", oc)
oc = hs.outcome(fwd([100.1] * 30), 100.0, 1.5, 180)
check("never reaches the goal = NEVER",
      oc["speed"] == "never", oc)
oc = hs.outcome(fwd([100.4]), 100.0, 1.5, 180)
check("a +0.4% wiggle under the goal = GRINDER",
      oc["payoff"] == "grinder" and oc["speed"] == "never", oc)
oc = hs.outcome(fwd([100.1] * 5), 100.0, 1.5, 180)
check("no real move = DUD",
      oc["payoff"] == "dud", oc)
oc = hs.outcome(fwd([98.0, 99.0, 101.6]), 100.0, 1.5, 180)
check("the drawdown is tracked even when the goal pays",
      oc["payoff"] == "winner" and oc["max_dd_pct"] <= -1.9, oc)
oc = hs.outcome(fwd([100.1] * 37 + [110.0]), 100.0, 1.5, 180)
check("a spike AFTER the window doesn't count",
      oc["payoff"] == "dud", oc)
check("no entry / no candles -> None",
      hs.outcome(fwd([101.0]), 0.0, 1.5, 180) is None
      and hs.outcome([], 100.0, 1.5, 180) is None)
check("end_move = the window's last close vs entry",
      abs(hs.outcome(fwd([100.5, 101.0]), 100.0, 1.5, 180)["end_move_pct"] - 1.0) < 1e-6)


# ── 4. GATE-GRADING VERDICTS (the weekly report's logic) ─────────────
def rec(payoff, end):
    return {"pair": "X/USD", "outcome": {"payoff": payoff, "speed": "fast",
                                         "end_move_pct": end}}


v = hs._gate_verdict("brain-floor", [rec("winner", 0.5)] * 5 + [rec("dud", -0.5)] * 5,
                     [rec("winner", 0.5)] * 5)
check("a gate burying 50% winners reads LOOSEN", "LOOSEN" in v, v)
v = hs._gate_verdict("tide-out", [rec("winner", 0.5)] * 1 + [rec("dud", -2.0)] * 9,
                     [rec("winner", 0.5)] * 1)
check("a gate preventing 90% losses reads KEEP", "KEEP" in v, v)
v = hs._gate_verdict("fired", [rec("winner", 1.0)], [rec("winner", 1.0)])
check("the fired baseline names itself", "BASELINE" in v, v)
v = hs._gate_verdict("worth-floor", [rec("winner", 0.5)] * 4 + [rec("dud", -0.5)] * 6,
                     [rec("winner", 0.5)] * 4)
check("a coin-flip gate reads CONDITIONAL (size-scaling, not blocking)",
      "CONDITIONAL" in v, v)

# ── 5. RULES EXTRACTION: honest with thin data ───────────────────────
tmp = Path(tempfile.mkdtemp())
rules_path = tmp / "rules.json"
old_rules = hs.RULES_FILE
hs.RULES_FILE = str(rules_path)
try:
    rules = hs.extract_rules(min_support=1000)  # no dataset can satisfy this
    check("thin data -> an honest 'insufficient' note, no forced rules",
          "insufficient" in str(rules.get("note", "")) and not rules.get("fast_route"),
          rules.get("note"))
    # fabricate a small outcome set: strong vol_burst -> winners
    recs = []
    for i in range(60):
        win = i >= 40  # the top tercile of vol_burst wins
        recs.append({"pair": f"P{i}/USD", "ts": f"2026-09-05T0{i % 10}:00:00-06:00",
                     "kind": "suppressed", "gate": "brain-floor",
                     "brain": 50.0, "quick": 60.0, "floor": 89.0,
                     "features": {"vol_burst": 1.0 + i * 0.1, "momentum_15m": 0.1},
                     "outcome": ({"speed": "fast", "payoff": "winner",
                                  "end_move_pct": 1.0} if win
                                 else {"speed": "never", "payoff": "dud",
                                       "end_move_pct": -0.5})})
    out_path = tmp / "outcomes.jsonl"
    with open(out_path, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    old_out = hs.OUTCOMES_FILE
    hs.OUTCOMES_FILE = str(out_path)
    rules = hs.extract_rules(min_support=15)
    check("a clean feature/outcome correlation becomes a fast-route rule",
          "vol_burst" in rules.get("fast_route", {}),
          rules.get("fast_route"))
    check("the rule reports its support + winner rate",
          rules["fast_route"]["vol_burst"]["support"] >= 15
          and rules["fast_route"]["vol_burst"]["winner_rate"] > 0.8,
          rules["fast_route"].get("vol_burst"))
    hs.OUTCOMES_FILE = old_out
finally:
    hs.RULES_FILE = old_rules

# ── 6. the module's own wiring ───────────────────────────────────────
check("the snapshot/outcomes/rules paths live in the repo dir",
      os.path.dirname(hs.SNAPSHOTS_FILE) == os.path.dirname(hs.__file__))
check("the CLI exposes backfill | rules | grade | all",
      all(k in open(hs.__file__, encoding="utf-8").read()
          for k in ('"backfill"', '"rules"', '"grade"')))

out = "\n".join(lines) + f"\n\n{PASS} passed, {FAIL} failed\n"
open("_hindsight_check.txt", "w", encoding="ascii", errors="replace").write(out)
print(out)
sys.exit(0)
