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
# rule 3 is a MEASURED holdout now, not a drift-total subtraction
_dependent = {"base": 0.97, "loss": 0.97,
              "worst": {"regime": 1, "p_pass": 0.0, "trades": 1400}}
check("rule 3 fires when holding a regime out costs the pass",
      cc.disqualify(comp(), healthy_regs, _dependent)[2][1] is True)
check("rule 3 names the measured loss",
      "-97 pts" in cc.disqualify(comp(), healthy_regs, _dependent)[2][2])
_mild = {"base": 0.97, "loss": 0.05,
         "worst": {"regime": 2, "p_pass": 0.92, "trades": 1800}}
check("rule 3 does NOT fire for a small measured loss",
      cc.disqualify(comp(), healthy_regs, _mild)[2][1] is False)
check("rule 3 does NOT fire when the holdout was never measured",
      cc.disqualify(comp(), healthy_regs, None)[2][1] is False)
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

# ── the LIVE controller: the four decisions and the arming contract ───
import json
import tempfile
from datetime import datetime, timedelta, timezone

_tmp = Path(tempfile.mkdtemp(prefix="_cctl_"))
_series = _tmp / "series.json"
_verdict = _tmp / "verdict.json"
_cstate = _tmp / "state.json"


def _write_series(mean, tail_mean=None, n=3000, seed=4):
    rng = np.random.default_rng(seed)
    nets = rng.normal(mean, 0.5, n)
    if tail_mean is not None:
        nets[-500:] = rng.normal(tail_mean, 0.5, 500)
    nets = np.round(nets, 4).tolist()
    ts = [1_790_000_000_000 + 60_000 * i for i in range(n)]
    _series.write_text(json.dumps({"pace": 47.4, "tolls": {"BTC": 0.0002},
                                   "configs": {"wide": {"cfg": [3.0, 0.4, 0.1, 0.0],
                                                        "nets": nets, "ts": ts}}}),
                       encoding="utf-8")


def _ctl():
    return cc.ContestController(series_path=str(_series), verdict_path=str(_verdict),
                                state_path=str(_cstate))


_write_series(0.07, tail_mean=-0.30)   # the tape's own shape: good months, bad tail
if _cstate.exists():
    _cstate.unlink()
_real_armed = cc.armed
cc.armed = lambda: False
c = _ctl()
ok, why, frac = c.allow("BTC/USD")
check("disarmed: every entry is allowed", ok is True)
check("disarmed: no size override is handed out", frac is None)
check("disarmed: the reason says advisory", "ADVISORY" in why)
check("disarmed: no halt is latched to disk", not _cstate.exists())
check("disarmed: the cycle still reports its computed stop",
      c.snapshot(force=True)["trade"] is False)
check("disarmed: sizing passes the ticket through untouched",
      c.size_usd(500.0, "BTC/USD")[0] == 500.0)

cc.armed = lambda: True
c = _ctl()
ok, why, frac = c.allow("BTC/USD")
check("armed: a significantly negative edge refuses the entry", ok is False)
check("armed: the refusal names the edge", "no new entries" in why or "stop" in why)
check("armed: the halt is persisted", _cstate.exists() and json.loads(
    _cstate.read_text(encoding="utf-8"))["halted"] is True)

_write_series(0.07, tail_mean=0.30)   # the tail recovers
c = _ctl()
ok, why, frac = c.allow("BTC/USD")
check("armed: a fresh halt is NOT lifted by one good window", ok is False)
st = json.loads(_cstate.read_text(encoding="utf-8"))
st["halted_at"] = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
_cstate.write_text(json.dumps(st), encoding="utf-8")
c = _ctl()
ok, why, frac = c.allow("BTC/USD")
check("armed: the halt lifts after the hold with a recovered edge", ok is True and bool(frac))
check("armed: the resumed size is inside the schedule bounds",
      frac is not None and 0.05 <= frac <= cc.SIZE_CEIL)
check("armed: the resume is recorded", json.loads(
    _cstate.read_text(encoding="utf-8"))["halted"] is False)

cc.armed = _real_armed


# ── the dominant uncertainty is MEASURED, not matched on labels ───────
_rng = np.random.default_rng(21)
_n = 2400
_nets = _rng.normal(0.06, 0.5, _n)
_nets[-600:] = _rng.normal(-0.9, 0.5, 600)      # one regime is a wreck
_ts = [1_790_000_000_000 + 300_000 * i for i in range(_n)]
_r = cc.sensitivity_ranking(_nets, _ts, 47.4, paths=120, seed=5)
_c = _r["candidates"]
check("sensitivity: every candidate is a re-run, not a label",
      len(_c) >= 3 and all("variants" in x for x in _c))
check("sensitivity: ranked by measured swing, widest first",
      [x["swing"] for x in _c] == sorted([x["swing"] for x in _c], reverse=True))
check("sensitivity: each swing IS the spread of its own re-runs",
      all(abs(x["swing"] - (max(x["variants"]) - min(x["variants"]))) < 1e-3 for x in _c))
check("sensitivity: the winner is the top of the ranking",
      _r["winner"] == _c[0]["assumption"])
check("sensitivity: the wrecked regime wins the ranking on measured effect",
      "regime" in _c[0]["assumption"]
      and _c[0]["swing"] > [x for x in _c if "costs" in x["assumption"]][0]["swing"] + 0.01)
check("sensitivity: the edge and the regime mix are NOT given the same number",
      abs([x for x in _c if "edge" in x["assumption"]][0]["swing"]
          - [x for x in _c if "regime" in x["assumption"]][0]["swing"]) > 1e-6)
check("sensitivity: a short series declines to rank rather than guessing",
      cc.sensitivity_ranking(_nets[:50], _ts[:50], 47.4)["winner"] is None)


# ── rule 3 measures the holdout instead of subtracting drift totals ───
_h_rng = np.random.default_rng(33)
_hn = 2400
_h_nets = _h_rng.normal(0.30, 0.5, _hn)
_h_nets[900:1500] = _h_rng.normal(-1.2, 0.5, 600)   # a bad stretch: holding it out HELPS
_h_ts = [1_790_000_000_000 + 420_000 * i for i in range(_hn)]
_ho = cc.regime_holdout(_h_nets, _h_ts, 47.4, paths=120, seed=9)
check("holdout: every regime is re-simulated, none inferred",
      _ho and len(_ho["holdouts"]) >= 2 and all("p_pass" in h for h in _ho["holdouts"]))
check("holdout: losses are the measured difference from the full tape",
      all(abs(h["loss"] - (_ho["base"] - h["p_pass"])) < 1e-3
          for h in _ho["holdouts"] if h["p_pass"] is not None))
check("holdout: losing the bad stretch RAISES the pass, so the loss is negative there",
      any((h["loss"] or 0) < 0 for h in _ho["holdouts"] if h["p_pass"] is not None))
check("holdout: a single-regime tape declines rather than inventing a holdout",
      cc.regime_holdout(_h_nets[:50], _h_ts[:50], 47.4) is None)

# and the rule consumes it: a tape carried by ONE regime must fire
_e_rng = np.random.default_rng(33)
_e_nets = _e_rng.normal(0.02, 0.5, _hn)
_e_nets[900:1500] = _e_rng.normal(0.9, 0.5, 600)   # the only stretch that completes
_ho_essential = cc.regime_holdout(_e_nets, _h_ts, 47.4, paths=120, seed=9)
check("holdout: the essential regime is identified by name and loss",
      _ho_essential["worst"]["p_pass"] == 0.0 and _ho_essential["loss"] >= 0.5)
_h_rule = cc.disqualify(comp(), healthy_regs, _ho_essential)[2]
check("holdout: rule 3 consumes the measurement (fires on a regime-dependent tape)",
      _h_rule[1] is True and "P(pass)" in _h_rule[2])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
