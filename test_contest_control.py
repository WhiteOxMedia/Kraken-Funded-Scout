"""Test the CONTEST CONTROL SYSTEM's math and its five disqualifications.

No tape, no MC over 17k trades: these are the invariants the controller's answers
rest on, checked in milliseconds.

Run: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe test_contest_control.py
"""
import sys
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import contest_control as cc

lines = []
PASS = FAIL = 0


def check(label, ok):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + label)
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(("PASS  " if ok else "FAIL  ") + label)


# ── theta, the barrier and the closed form ────────────────────────────
check("theta halves when the ticket doubles",
      abs(cc.theta_of(0.07, 0.52, 0.02) - cc.theta_of(0.07, 0.52, 0.01) / 2) < 1e-9)
check("theta is <= 0 at or below zero drift", cc.theta_of(0.0, 0.5) == 0.0
      and cc.theta_of(-0.01, 0.5) < 0.0)
check("p_free rises with theta",
      cc.p_free(1) < cc.p_free(5) < cc.p_free(20) < 0.999)
check("p_free is 0 for non-positive theta", cc.p_free(0.0) == 0.0)

# ── the block sampler ─────────────────────────────────────────────────
a = np.round(np.random.default_rng(1).normal(0.07, 0.5, 3000), 4).tolist()
r1 = cc.block_mc(a, 0.01, 4000, 50, paths=200, seed=5)
r2 = cc.block_mc(a, 0.01, 4000, 50, paths=200, seed=5)
check("block_mc is deterministic for a given seed", r1 == r2)
check("block_mc probabilities are in [0,1] and mutually exclusive",
      0 <= r1["pass"] <= 1 and 0 <= r1["death"] <= 1 and r1["pass"] + r1["death"] <= 1)
neg = cc.block_mc([-1.0] * 500, 0.05, 2000, 50, paths=100, seed=5)
check("a permanently losing series dies, never passes",
      neg["death"] == 1.0 and neg["pass"] == 0.0)
check("a doubling series passes, never dies",
      cc.block_mc([5.0] * 200, 0.01, 500, 50, paths=50, seed=5)["pass"] == 1.0)

# ── schedules are state functions, not constants ──────────────────────
sch = cc.schedules(0.01)
bal = np.array([1.00, 1.05, 1.10])
flat = sch["const"](bal, np.zeros(3), 0)
by_buf = sch["s(buffer)"](bal, np.zeros(3), 0)
check("the constant schedule ignores state", float(flat.min()) == float(flat.max()))
check("s(buffer) grows with the balance", by_buf[0] < by_buf[1] < by_buf[2])
hot = sch["s(theta)"](bal, np.array([0.20, 0.05, 0.0]), 0)
check("s(theta) shrinks when the path's own recent drift falls", hot[0] > hot[1] > hot[2])
near_death = np.array([0.971, 1.12])
p = sch["s(p_pass)"](near_death, np.array([0.07, 0.07]), 0)
check("s(p_pass) is smaller near the death line than near the target", p[0] < p[1])
check("every schedule stays inside the clip bounds",
      all(0.0 < float(f(bal, np.full(3, 0.07), 0).min()) for f in sch.values()))

# ── the five disqualifications ────────────────────────────────────────
def comp(**kw):
    base = {"mu": 0.07, "p_death_block": 0.0, "p_pass_block": 0.9,
            "p_pass_365": 0.9, "p_pass_cost_x1.5": 0.85,
            "p_pass_mu_minus10": 0.8, "p_pass_mu_plus10": 0.95}
    base.update(kw)
    return base


healthy_regs = [{"share_of_drift": 0.5, "total_drift": 10.0},
                {"share_of_drift": 0.5, "total_drift": 10.0}]
check("a healthy component is clear",
      not any(f[1] for f in cc.disqualify(comp(), healthy_regs)))
check("rule 1 fires on a non-positive edge",
      cc.disqualify(comp(mu=0.0), healthy_regs)[0][1] is True)
check("rule 2 fires past the death tolerance",
      cc.disqualify(comp(p_death_block=0.05), healthy_regs)[1][1] is True)
dependent = [{"share_of_drift": 1.2, "total_drift": 10.0},
             {"share_of_drift": -0.2, "total_drift": -2.0}]
check("rule 3 fires when removing the best regime leaves nothing",
      cc.disqualify(comp(), dependent)[2][1] is True)
check("rule 3 does NOT fire merely because one regime dominates",
      cc.disqualify(comp(), [{"share_of_drift": 0.95, "total_drift": 10.0},
                             {"share_of_drift": 0.05, "total_drift": 0.5}])[2][1] is False)
check("rule 4 fires when 1.5x the toll halves the pass",
      cc.disqualify(comp(p_pass_365=0.9, **{"p_pass_cost_x1.5": 0.3}), healthy_regs)[3][1] is True)
check("rule 5 fires when a 10% drift move crosses the 50% decision line",
      cc.disqualify(comp(p_pass_mu_minus10=0.45, p_pass_mu_plus10=0.55),
                    healthy_regs)[4][1] is True)
check("rule 5 stays clear when the decision does not move",
      cc.disqualify(comp(p_pass_mu_minus10=0.85, p_pass_mu_plus10=0.95),
                    healthy_regs)[4][1] is False)

# ── completions per day, the scalar the pick is made on ───────────────
slow = {"p_pass_block": 1.0, "p_death_block": 0.0, "median_pass_days": 400.0}
fast = {"p_pass_block": 1.0, "p_death_block": 0.0, "median_pass_days": 200.0}
dies = {"p_pass_block": 1.0, "p_death_block": 0.5, "median_pass_days": 200.0}
check("faster completion scores higher", cc.completion_rate(fast) > cc.completion_rate(slow))
check("death is netted out of the completion rate",
      cc.completion_rate(dies) == cc.completion_rate(fast) / 2)

print("\n".join(lines))
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
