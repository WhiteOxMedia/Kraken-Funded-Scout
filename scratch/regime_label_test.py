"""THREE-ARM TEST: is the regime-dependence finding real, causal, or machinery?

Arm A (same-day)   the labels contest_control uses today: a day's own drift, vol,
                   breadth and participation classify that day.
Arm B (prior-only) the same four features averaged over the previous three days, so
                   nothing from the day being labelled enters its label.
Arm C (shuffled)   Arm A's label multiset, permuted across days: identical cluster
                   sizes and identical label histogram, no relationship to the tape.

k is fixed at 3 for every arm before any of them is run.

For each arm the rule-3 holdout is re-run exactly as shipped (50-block resample,
365d horizon, 900 paths), and the loss is base P(pass) minus P(pass | regime j out).
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import contest_control as cc

K = 3
PATHS = 900
SEED = 13
SERIES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "scratch", "_cc_series.json")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_regime_label_test.json")


def day_index(ts):
    t = np.asarray(ts, float)
    return (t // 86400000).astype(np.int64) if t.max() > 1e11 else (t // 86400).astype(np.int64)


def labels_to_trades(day_to_label, ts):
    day = day_index(ts)
    return np.array([day_to_label.get(int(d), -1) for d in day])


def arm_same_day(nets, ts):
    """Arm A: the shipped label path."""
    return cc.trade_regime_labels(nets, ts, k=K)[0]


def arm_prior_only(nets, ts, lag=3):
    """Arm B: classify day d from days d-1..d-lag only."""
    rows = cc._daily_features(nets, ts)
    feats = []
    for i, r in enumerate(rows):
        prev = rows[max(0, i - lag):i]
        if not prev:
            continue
        feats.append({"day": r["day"],
                      "drift": float(np.mean([p["drift"] for p in prev])),
                      "vol": float(np.mean([p["vol"] for p in prev])),
                      "breadth": float(np.mean([p["breadth"] for p in prev])),
                      "part": float(np.mean([p["part"] for p in prev]))})
    if len(feats) < 6:
        return np.full(nets.size, -1), feats
    keys = ("drift", "vol", "breadth", "part")
    X, _mu, _sd = cc._standardize(feats, keys)
    lab, _C = cc._kmeans(X, K)
    dmap = {feats[i]["day"]: int(lab[i]) for i in range(len(feats))}
    return labels_to_trades(dmap, ts), feats


def arm_shuffled(nets, ts, seed=0):
    """Arm C: Arm A's labels, permuted across the days that carry one."""
    day = day_index(ts)
    rows = cc._daily_features(nets, ts)
    keys = ("drift", "vol", "breadth", "part")
    X, _mu, _sd = cc._standardize(rows, keys)
    lab, _C = cc._kmeans(X, K)
    perm = np.random.default_rng(seed).permutation(lab)
    dmap = {rows[i]["day"]: int(perm[i]) for i in range(len(rows))}
    return labels_to_trades(dmap, ts)


def holdouts(nets, ts, labels, base):
    out = []
    for j in sorted({int(x) for x in labels if x >= 0}):
        sub = nets[labels != j]
        if sub.size < 100:
            out.append({"regime": j, "trades": int(sub.size), "p_pass": None,
                        "p_death": None, "loss": None})
            continue
        r = cc.block_mc(sub, 0.01, int(47.4 * cc.SECONDARY_DAYS), 50,
                        paths=PATHS, seed=SEED)
        out.append({"regime": j, "trades": int(sub.size),
                    "p_pass": round(float(r["pass"]), 4),
                    "p_death": round(float(r["death"]), 4),
                    "loss": round(base - float(r["pass"]), 4)})
    meas = [h for h in out if h["loss"] is not None]
    worst = min(meas, key=lambda h: h["p_pass"]) if meas else None
    return {"holdouts": out, "worst": worst,
            "loss": worst["loss"] if worst else 0.0}


def regime_profile(nets, labels):
    prof = []
    for j in sorted({int(x) for x in labels if x >= 0}):
        m = labels == j
        prof.append({"regime": j, "trades": int(m.sum()),
                     "mu": round(float(nets[m].mean()), 4),
                     "share_of_drift": round(float(nets[m].sum() / nets.sum()), 3)})
    return prof


def main():
    data = json.load(open(SERIES, encoding="utf-8"))
    res = {"k": K, "paths": PATHS, "horizon_days": cc.SECONDARY_DAYS,
           "label_loss_tolerance": cc.REGIME_PASS_LOSS, "arms": {}}
    for name in ("tight", "mid", "shipped", "wide"):
        got = data["configs"].get(name)
        if not got:
            continue
        nets = np.asarray(got["nets"], float)
        ts = np.asarray(got["ts"], float)
        base = float(cc.block_mc(nets, 0.01, int(47.4 * cc.SECONDARY_DAYS), 50,
                                 paths=PATHS, seed=SEED)["pass"])
        entry = {"base_p_pass": round(base, 4), "arms": {}}
        a_lab = arm_same_day(nets, ts)
        entry["arms"]["same_day"] = {"labels": a_lab, "holdout": holdouts(nets, ts, a_lab, base),
                                     "profile": regime_profile(nets, a_lab)}
        b_lab, _feats = arm_prior_only(nets, ts)
        entry["arms"]["prior_only"] = {"labels": b_lab, "holdout": holdouts(nets, ts, b_lab, base),
                                       "profile": regime_profile(nets, b_lab)}
        if name == "wide":
            shuffles = []
            for s in range(5):
                c_lab = arm_shuffled(nets, ts, seed=s)
                shuffles.append({"seed": s, "holdout": holdouts(nets, ts, c_lab, base),
                                 "profile": regime_profile(nets, c_lab)})
            entry["arms"]["shuffled"] = shuffles
        # keep the tracer's own copy of the numbers, not the arrays
        for arm in entry["arms"].values():
            if isinstance(arm, dict):
                arm.pop("labels", None)
        res["arms"][name] = entry
        print(f"\n=== {name}  base P(pass) {base*100:.1f}%", flush=True)
        for arm_name in ("same_day", "prior_only"):
            h = entry["arms"][arm_name]["holdout"]
            print(f"  {arm_name:<11} loss {h['loss']*100:5.1f} pts  " +
                  "  ".join(f"-r{x['regime']} {(x['p_pass'] or 0)*100:5.1f}%/"
                            f"{(x['p_death'] or 0)*100:4.1f}d" for x in h["holdouts"]),
                  flush=True)
        if "shuffled" in entry["arms"]:
            losses = [s["holdout"]["loss"] for s in entry["arms"]["shuffled"]]
            print(f"  shuffled    loss {min(losses)*100:.1f}-{max(losses)*100:.1f} pts "
                  f"(median {np.median(losses)*100:.1f})", flush=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
