#!/usr/bin/env python3
"""Check the pre-registered arming gate for the Overwatch controller.

The gate is data (overwatch_arming_gate.json), not code: this script only reads that
file and the artifacts it names, and reports PASS / FAIL / BLOCKED per criterion.

    python arming_gate.py            # evaluate; exit 0 only when every criterion PASSes
    python arming_gate.py --lock     # record the gate file's sha256 (pre-registration)
    python arming_gate.py --selftest # prove the checker both passes and fails a known set

BLOCKED means the evidence does not exist yet, which is the normal state of a gate
written before the work. It is not a pass, and it never counts as one.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GATE_FILE = os.path.join(HERE, "overwatch_arming_gate.json")
LOCK_FILE = os.path.join(HERE, "overwatch_arming_gate.lock")


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        return {"__error__": f"unreadable JSON: {exc}"}


def _dig(doc, dotted, default=None):
    cur = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _result(status, detail):
    return {"status": status, "detail": detail}


def check_c1(gate, base):
    v = _load(os.path.join(base, "contest_control_verdict.json"))
    if v is None:
        return _result("BLOCKED", "contest_control_verdict.json missing")
    if "__error__" in v:
        return _result("FAIL", v["__error__"])
    mode = v.get("label_mode")
    worthy = _dig(v, "1_best_config.contest_worthy")
    bad = _dig(v, "1_best_config.disqualified_on") or []
    if mode != "prior_only":
        return _result("FAIL", f"verdict was built with label_mode={mode!r}, "
                               f"not 'prior_only'")
    if not worthy:
        return _result("FAIL", f"best config not contest-worthy (disqualified on: {bad})")
    if any(str(x).startswith("3.") for x in bad):
        return _result("FAIL", "rule 3 still fires on the best config")
    return _result("PASS", f"causal labels in use; {_dig(v, '1_best_config.name')} "
                           f"clear of rule 3")


def check_c2(gate, base):
    d = _load(os.path.join(base, "scratch", "_regime_label_test.json"))
    if d is None:
        return _result("BLOCKED", "scratch/_regime_label_test.json missing")
    if "__error__" in d:
        return _result("FAIL", d["__error__"])
    req = gate["criteria"]["C2_label_robustness"]["required"]
    if int(d.get("k", 0)) != int(req["k"]):
        return _result("FAIL", f"k={d.get('k')} != pre-registered {req['k']}")
    if int(d.get("paths", 0)) < int(req["paths_min"]):
        return _result("FAIL", f"paths={d.get('paths')} < {req['paths_min']}")
    arms = d.get("arms") or {}
    if not arms:
        return _result("FAIL", "no config arms recorded")
    rows, bad = [], []
    for name, entry in arms.items():
        got = (entry.get("arms") or {})
        prior = _dig(got, "prior_only.holdout.loss")
        same = _dig(got, "same_day.holdout.loss")
        shuffled = [s["holdout"]["loss"] for s in (got.get("shuffled") or [])]
        if prior is None:
            bad.append(f"{name}: prior-only arm missing")
            continue
        if not shuffled and not any("shuffled" in e.get("arms", {})
                                    for e in arms.values()):
            bad.append("no shuffled control anywhere")
        if prior > req["prior_only_loss_max"]:
            bad.append(f"{name}: causal loss {prior:.3f} > {req['prior_only_loss_max']}")
        floor = f", shuffled floor {min(shuffled):.3f}-{max(shuffled):.3f}" if shuffled else ""
        rows.append(f"{name}: same-day {same:.3f} causal {prior:.3f}{floor}")
    if bad:
        return _result("FAIL", "; ".join(bad))
    return _result("PASS", " | ".join(rows))


def check_c3(gate, base):
    v = _load(os.path.join(base, "contest_control_verdict.json"))
    if v is None:
        return _result("BLOCKED", "contest_control_verdict.json missing")
    bad = _dig(v, "1_best_config.disqualified_on")
    if bad is None:
        return _result("FAIL", "verdict has no disqualified_on field")
    if bad:
        return _result("FAIL", f"fires: {bad}")
    return _result("PASS", "no disqualifier fires on the best config")


def check_c4(gate, base):
    v = _load(os.path.join(base, "contest_control_verdict.json"))
    if v is None:
        return _result("BLOCKED", "contest_control_verdict.json missing")
    p = v.get("3_p_death")
    lim = gate["criteria"]["C4_death_tolerance"]["required"]["3_p_death_max"]
    if not isinstance(p, (int, float)):
        return _result("FAIL", "3_p_death missing")
    if p > lim:
        return _result("FAIL", f"P(death) {p*100:.2f}% > {lim*100:.1f}%")
    return _result("PASS", f"P(death) {p*100:.2f}% <= {lim*100:.1f}%")


def check_c5(gate, base):
    d = _load(os.path.join(base, "scratch", "_pace_audit.json"))
    if d is None:
        return _result("BLOCKED", "scratch/_pace_audit.json missing (audit not run)")
    if "__error__" in d:
        return _result("FAIL", d["__error__"])
    req = gate["criteria"]["C5_pace"]["required"]
    # the audit reports several windows; the gate prices the CURRENT mode, which is
    # the last seven complete days, and says so rather than picking the best one
    if "days" not in d and isinstance(d.get("windows"), dict):
        d = dict(d, **d["windows"].get("last_7", {}), _window="last_7")
    missing = [f for f in req["fields"] if f not in d]
    if missing:
        return _result("FAIL", f"pace audit missing fields: {missing}")
    days, fires, fills = float(d["days"]), float(d["fires"]), float(d["fills"])
    if days < req["days_min"]:
        return _result("FAIL", f"{days:.1f} days < {req['days_min']}")
    per_day = fires / days if days else 0.0
    if per_day < req["fires_per_day_min"]:
        return _result("FAIL", f"{per_day:.1f} fires/day < {req['fires_per_day_min']}")
    ratio = fills / fires if fires else 0.0
    if ratio < req["fills_per_fire_min"]:
        return _result("FAIL", f"fills/fires {ratio:.2f} < {req['fills_per_fire_min']}")
    v = _load(os.path.join(base, "contest_control_verdict.json")) or {}
    p_pass = v.get("2_p_pass") or 0.0
    p_death = v.get("3_p_death") or 0.0
    med = _dig(v, "1_best_config.median_pass_days") or 0.0
    if not med:
        return _result("FAIL", "no median_pass_days in the verdict to price the pace")
    # median_days scales inversely with pace: re-price the observed pace, not the assumed one
    med_adj = med * (47.4 / per_day)
    exp365 = p_pass * (1 - p_death) * (365.0 / med_adj)
    if exp365 < req["expected_completions_365d_min"]:
        return _result("FAIL", f"expected completions in 365d {exp365:.2f} < "
                               f"{req['expected_completions_365d_min']} "
                               f"(pace {per_day:.1f}/day -> median {med_adj:.0f}d)")
    return _result("PASS", f"{per_day:.1f} fires/day over {days:.0f}d "
                           f"({d.get('_window', 'reported window')}), fills {ratio:.0%}, "
                           f"expected {exp365:.2f} completions/365d")


def check_c6(gate, base):
    d = _load(os.path.join(base, "scratch", "_shadow_audit.json"))
    if d is None:
        return _result("BLOCKED", "scratch/_shadow_audit.json missing (period not run)")
    req = gate["criteria"]["C6_shadow_period"]["required"]
    if float(d.get("consecutive_days", 0)) < req["consecutive_days_min"]:
        return _result("FAIL", f"{d.get('consecutive_days')} days < "
                               f"{req['consecutive_days_min']}")
    if int(d.get("decisions", 0)) < req["decisions_min"]:
        return _result("FAIL", f"{d.get('decisions')} decisions < {req['decisions_min']}")
    if int(d.get("tracebacks", 0)) > req["tracebacks_max"]:
        return _result("FAIL", f"{d.get('tracebacks')} tracebacks")
    if d.get("halted") is not False:
        return _result("FAIL", "a halt is latched; disarm/review before arming")
    return _result("PASS", f"{d['consecutive_days']}d, {d['decisions']} decisions, "
                           f"0 tracebacks, no halt")


def check_c7(gate, base):
    d = _load(os.path.join(base, "scratch", "_controller_backtest.json"))
    if d is None:
        return _result("BLOCKED", "scratch/_controller_backtest.json missing (not run)")
    req = gate["criteria"]["C7_controller_backtest"]["required"]
    have = [k for k in ("cost_channel", "benefit_channel") if d.get(k)]
    if len(have) < 2:
        return _result("FAIL", f"only {have or 'no'} channel(s) measured; both are required")
    if int(d.get("paths", 0)) < req["paths_min"]:
        return _result("FAIL", f"paths={d.get('paths')} < {req['paths_min']}")
    if int(d.get("trades", 0)) < req["trades_min"]:
        return _result("FAIL", f"trades={d.get('trades')} < {req['trades_min']}")
    net = d.get("net_completions_delta")
    if net is None:
        return _result("FAIL", "net_completions_delta missing")
    if net < req["net_completions_delta_min"]:
        return _result("FAIL", f"controller costs {abs(net):.4g} completions/day")
    return _result("PASS", f"net {net:+.4g} completions/day")


CHECKS = {
    "C1_rule3_causal": check_c1,
    "C2_label_robustness": check_c2,
    "C3_all_rules_clear": check_c3,
    "C4_death_tolerance": check_c4,
    "C5_pace": check_c5,
    "C6_shadow_period": check_c6,
    "C7_controller_backtest": check_c7,
}


def evaluate(gate, base):
    out = {}
    for key in gate["criteria"]:
        fn = CHECKS.get(key)
        out[key] = _result("FAIL", "no checker for this criterion") if fn is None \
            else fn(gate, base)
    return out


def _lock(gate_path=GATE_FILE):
    with open(gate_path, "rb") as fh:
        h = hashlib.sha256(fh.read()).hexdigest()
    with open(LOCK_FILE, "w", encoding="utf-8") as fh:
        fh.write(h + "\n")
    return h


def _locked_hash():
    try:
        with open(LOCK_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except FileNotFoundError:
        return None


def _print(gate, results, tampered):
    print(f"ARMING GATE v{gate.get('version')} — {gate.get('gate')}")
    print(f"authored {gate.get('authored')}\n")
    for key, r in results.items():
        print(f"  {r['status']:<8} {key:<26} {r['detail']}")
    statuses = [r["status"] for r in results.values()]
    ready = all(s == "PASS" for s in statuses)
    print()
    if tampered:
        print("GATE FILE CHANGED since --lock: this is no longer a pre-registration. "
              "Bump the version and re-lock, and say so in the commit.")
    print("ARMED-READY: every criterion passed." if ready else
          "NOT ARMED-READY: " + ", ".join(f"{k}={v['status']}"
                                          for k, v in results.items()
                                          if v["status"] != "PASS"))
    return 0 if ready else 1


def selftest():
    """Prove the checker's verdicts, in a throwaway directory."""
    gate = _load(GATE_FILE)
    tmp = tempfile.mkdtemp(prefix="_gate_")
    os.makedirs(os.path.join(tmp, "scratch"))
    v = {"label_mode": "prior_only", "2_p_pass": 0.97, "3_p_death": 0.004,
         "1_best_config": {"name": "wide", "contest_worthy": True,
                           "disqualified_on": [], "median_pass_days": 221.0}}
    lab = {"k": 3, "paths": 900, "arms": {"wide": {"arms": {
        "same_day": {"holdout": {"loss": 0.977}},
        "prior_only": {"holdout": {"loss": 0.004}},
        "shuffled": [{"holdout": {"loss": 0.02}}, {"holdout": {"loss": 0.19}}]}}}}
    pace = {"days": 7, "fires": 280, "fills": 210,
            "window_start": "2026-11-16", "window_end": "2026-11-23"}
    shadow = {"consecutive_days": 15, "decisions": 500, "tracebacks": 0, "halted": False}
    bt = {"cost_channel": {"delta": -0.002}, "benefit_channel": {"delta": +0.003},
          "net_completions_delta": 0.001, "paths": 200, "trades": 900}
    for name, doc, sub in (("contest_control_verdict.json", v, ""),
                           ("_regime_label_test.json", lab, "scratch"),
                           ("_pace_audit.json", pace, "scratch"),
                           ("_shadow_audit.json", shadow, "scratch"),
                           ("_controller_backtest.json", bt, "scratch")):
        with open(os.path.join(tmp, sub, name), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
    ok = evaluate(gate, tmp)
    failures = [k for k, r in ok.items() if r["status"] != "PASS"]
    # and every one of them must FAIL when its evidence is wrong
    v_bad = dict(v, label_mode="same_day",
                 **{"1_best_config": dict(v["1_best_config"],
                                          disqualified_on=["3. single-regime dependence"],
                                          contest_worthy=False)})
    with open(os.path.join(tmp, "contest_control_verdict.json"), "w", encoding="utf-8") as fh:
        json.dump(v_bad, fh)
    bad = evaluate(gate, tmp)
    caught = [k for k in ("C1_rule3_causal", "C3_all_rules_clear")
              if bad[k]["status"] == "FAIL"]
    # missing artifacts must be BLOCKED, never PASS
    for f in os.listdir(os.path.join(tmp, "scratch")):
        os.remove(os.path.join(tmp, "scratch", f))
    gone = evaluate(gate, tmp)
    blocked = [k for k, r in gone.items() if r["status"] == "BLOCKED"]
    checks = [
        ("all criteria PASS on complete, well-formed evidence", not failures),
        ("a leaked label mode is caught by C1", "C1_rule3_causal" in caught),
        ("a disqualifier is caught by C3", "C3_all_rules_clear" in caught),
        ("missing evidence is BLOCKED, not assumed", len(blocked) >= 3),
    ]
    for label, cond in checks:
        print(f"{'PASS' if cond else 'FAIL'}  {label}")
    return 0 if all(c for _l, c in checks) else 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest" in argv:
        return selftest()
    if "--lock" in argv:
        print("locked", _lock())
        return 0
    gate = _load(GATE_FILE)
    if gate is None:
        print("arming gate file missing:", GATE_FILE)
        return 2
    locked = _locked_hash()
    tampered = False
    if locked:
        with open(GATE_FILE, "rb") as fh:
            tampered = hashlib.sha256(fh.read()).hexdigest() != locked
    results = evaluate(gate, HERE)
    return _print(gate, results, tampered)


if __name__ == "__main__":
    sys.exit(main())
