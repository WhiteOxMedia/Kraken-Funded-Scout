"""THE CONTEST CONTROL SYSTEM — the Overwatch's only objective is P(pass).

Objective: maximise P(hit +12% before -3%) over REPEATED attempts.

That objective changes what a controller is allowed to believe:

  * entries and exits are REPLACEABLE, so nothing here defends a rule. The exit
    configs are portfolio components scored on their contribution to pass/death/
    time, and a component that loses is dropped;
  * edge estimates are WRONG, so every estimate carries an interval and a
    significance test, and a result that survives only at its point estimate is
    not a result;
  * costs drift, so the toll is read live (spread_table) and every verdict is
    re-run at 1.5x the toll;
  * regimes change, so history is partitioned and the controller reports which
    partition today resembles — never which direction it will go.

The controller answers five questions and nothing else (see `verdict`).

    python contest_control.py --build     # cache the trade series (once, ~5 min)
    python contest_control.py --report    # the five answers
    python contest_control.py --detail    # the full dashboard behind them
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "scratch"))

SERIES_FILE = os.path.join(HERE, "scratch", "_cc_series.json")
VERDICT_FILE = os.path.join(HERE, "contest_control_verdict.json")

A = math.log(1.12)                    # +12% barrier, log space
B = math.log(1.0 / 0.97)              # -3% barrier, log space
# The contest is UNDATED, so the primary horizon is long enough that what is
# left is death risk rather than speed; 365d is kept as the secondary read so a
# config that is merely fast cannot pose as one that is safe.
HORIZON_DAYS = 730
EVAL_DAYS = 730
SECONDARY_DAYS = 365

# The components are (stop, arm, give, take) on the band entries. Champion and
# GO BIG are named in the portfolio but have no authoritative per-trade series on
# this tape, so they are scored as UNMEASURED rather than guessed at.
CONFIGS = {
    "tight":    (1.0, 0.2, 0.08, 0.0),
    "mid":      (1.5, 0.2, 0.08, 0.0),
    "shipped":  (2.0, 0.4, 0.15, 0.0),
    "wide":     (3.0, 0.4, 0.10, 0.0),
}

DEATH_TOLERANCE = 0.02        # block-bootstrap P(death) above this is disqualified
SINGLE_REGIME_SHARE = 0.80    # one regime carrying this much drift is dependence
COST_SHOCK_MULT = 1.5         # the cost sensitivity re-runs at 1.5x the toll
DRIFT_HAIRCUT = 0.5           # the drift sensitivity re-runs at half the measured mu
REGIME_PASS_LOSS = 0.25       # losing one regime may cost this much P(pass), no more


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


# ─────────────────────────────────────────────────────────────────────
# 0. the series (built once, cached)
# ─────────────────────────────────────────────────────────────────────
def build_series(force=False):
    """Per-config net trade series on the cheap universe, at the table's tolls."""
    if os.path.exists(SERIES_FILE) and not force:
        return _load_json(SERIES_FILE, {})
    import spread_table as ST
    import scratch.exit_take_sweep as TS
    from trendride_study import load, prep

    snap = TS.tokens()
    tolls = {}
    for sym, v in snap.items():
        row = (ST.load_table().get("pairs") or {}).get(sym) or {}
        tolls[sym] = float(row.get("avg_pct") or row.get("day_pct") or v)
    entries = [e for e in json.load(open(TS.ENTRIES)) if e[0] in tolls]
    d = load()
    sr = {}
    for sym in tolls:
        if sym not in d["pairs"]:
            continue
        p = prep(d["pairs"][sym])
        sr[sym] = {"h": p["h"], "l": p["l"], "c": p["c"],
                   "index": {t: i for i, t in enumerate(p["ts"])}}
    entries = sorted([e for e in entries if e[0] in sr], key=lambda r: r[1])
    pace = len(entries) / 183.0
    out = {"tolls": tolls, "pace": pace, "configs": {}}
    for name, cfg in CONFIGS.items():
        nets, tss, _h, _c = TS.simulate(entries, sr, tolls, *cfg)
        out["configs"][name] = {"cfg": list(cfg), "nets": nets, "ts": tss}
        print(f"  built {name}: {len(nets):,} trades")
    with open(SERIES_FILE, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    return out


def series(name, data=None):
    d = data if data is not None else _load_json(SERIES_FILE, {})
    c = (d.get("configs") or {}).get(name)
    if not c:
        return None
    return np.asarray(c["nets"], float), np.asarray(c["ts"], float), d


# ─────────────────────────────────────────────────────────────────────
# A. EDGE MONITOR — mu, sigma, theta, P(pass), P(death), and the zero test
# ─────────────────────────────────────────────────────────────────────
def theta_of(mu, sd, size=0.01):
    """theta = 2*mu/sigma^2 at a ticket size (halves when the ticket doubles)."""
    num, den = 2 * (size * mu / 100.0), (size * sd / 100.0) ** 2
    return num / den if den > 0 else 0.0


def p_free(theta, a=A, b=B):
    """Gambler's-ruin pass probability for drift theta, barriers a (up) / b (down)."""
    if theta <= 0:
        return 0.0
    return (1.0 - math.exp(-theta * b)) / (1.0 - math.exp(-theta * (a + b)))


def block_mc(nets, size, n, block, paths=900, seed=7, edge_mult=1.0, toll_shift=0.0):
    """P(pass), P(death) under a block resample. block=0 -> iid (control only).

    Chunked over paths: a two-year horizon is 34,600 steps x paths, and the
    full matrix does not fit comfortably in memory.
    """
    a = np.asarray(nets, float) * edge_mult + toll_shift
    if a.size == 0:
        return {"pass": 0.0, "death": 0.0, "median_n": None}
    rng = np.random.default_rng(seed)
    won_n = death_n = 0
    pass_t = []
    step_paths = max(1, min(150, paths))
    done = 0
    while done < paths:
        m = min(step_paths, paths - done)
        if block <= 1:
            idx = rng.integers(0, a.size, size=(m, n))
        else:
            nb = int(math.ceil(n / block))
            starts = rng.integers(0, max(a.size - block, 1), size=(m, nb))
            idx = (starts[:, :, None] + np.arange(block)[None, None, :]
                   ).reshape(m, nb * block)[:, :n]
        L = np.cumsum(np.log1p(size * a[idx] / 100.0), axis=1)
        up, dn = L >= A, L <= -B
        fu = np.where(up.any(1), up.argmax(1), n + 1)
        fd = np.where(dn.any(1), dn.argmax(1), n + 1)
        won = fu < fd
        won_n += int(won.sum())
        death_n += int((fd < fu).sum())
        if won.any():
            pass_t.extend(fu[won].tolist())
        done += m
    return {"pass": won_n / paths, "death": death_n / paths,
            "median_n": float(np.median(pass_t)) if pass_t else None}


def edge_monitor(nets, ts, pace, windows=(500, 2000, None)):
    """Rolling edge with an interval. Win rate is NOT reported: it does not predict
    pass, death or theta on this tape (see `win_rate_predicts`)."""
    a = np.asarray(nets, float)
    out = {}
    for w in windows:
        x = a if w is None else a[-min(w, a.size):]
        n = len(x)
        mu, sd = float(x.mean()), float(x.std(ddof=1)) if n > 1 else 0.0
        se = sd / math.sqrt(n) if n > 1 else 0.0
        t = mu / se if se > 0 else 0.0
        th = theta_of(mu, sd)
        out[str(w or "all")] = {
            "n": n, "mu": round(mu, 5), "sd": round(sd, 5),
            "se": round(se, 5), "t": round(t, 2),
            "ci": [round(mu - 1.96 * se, 5), round(mu + 1.96 * se, 5)],
            "theta": round(th, 1), "p_free": round(p_free(th), 4),
            "zero_indistinguishable": bool(se > 0 and abs(t) < 1.96),
        }
    return out


def win_rate_predicts(nets, ts):
    """Does win rate predict mu? (It cannot: per-trade mean is win%*payoff.)"""
    a = np.asarray(nets, float)
    if a.size < 100:
        return None
    w = 200
    wins, mus = [], []
    for i in range(w, a.size, max(w // 2, 1)):
        chunk = a[i - w:i]
        wins.append(float((chunk > 0).mean()))
        mus.append(float(chunk.mean()))
    if len(wins) < 10 or np.std(wins) == 0 or np.std(mus) == 0:
        return 0.0
    return float(np.corrcoef(wins, mus)[0, 1])


# ─────────────────────────────────────────────────────────────────────
# B. REGIME CLASSIFIER — similarity only, never direction
# ─────────────────────────────────────────────────────────────────────
def _daily_features(nets, ts, tolls_by_pair=None):
    """One row per calendar day: drift, vol, breadth, participation."""
    a = np.asarray(nets, float)
    t = np.asarray(ts, float)
    day = (t // 86400000).astype(np.int64) if t.max() > 1e11 else (t // 86400).astype(np.int64)
    rows = []
    for d in np.unique(day):
        m = day == d
        x = a[m]
        if x.size < 3:
            continue
        rows.append({"day": int(d), "drift": float(x.mean()), "vol": float(x.std()),
                     "breadth": float((x > 0).mean()), "part": int(x.size)})
    return rows


def _standardize(rows, keys):
    M = np.array([[r[k] for k in keys] for r in rows], float)
    mu, sd = M.mean(0), M.std(0)
    sd[sd == 0] = 1.0
    return (M - mu) / sd, mu, sd


def _kmeans(X, k, seed=3, iters=60):
    rng = np.random.default_rng(seed)
    C = X[rng.choice(len(X), size=min(k, len(X)), replace=False)]
    lab = np.zeros(len(X), int)
    for _ in range(iters):
        d = ((X[:, None, :] - C[None, :, :]) ** 2).sum(2)
        new = d.argmin(1)
        if (new == lab).all():
            break
        lab = new
        for j in range(len(C)):
            if (lab == j).any():
                C[j] = X[lab == j].mean(0)
    return lab, C


def regime_classifier(nets, ts, k=3):
    """Partition history; report which partition the recent days resemble and how
    those partitions actually behaved. No directional forecast is made."""
    rows = _daily_features(nets, ts)
    if len(rows) < 6:
        return {"regimes": [], "today": None, "note": "too few days to partition"}
    keys = ("drift", "vol", "breadth", "part")
    X, mu, sd = _standardize(rows, keys)
    lab, C = _kmeans(X, k)
    regs = []
    for j in range(k):
        idx = [i for i, l in enumerate(lab) if l == j]
        if not idx:
            continue
        day_drift = [rows[i]["drift"] * rows[i]["part"] for i in idx]
        regs.append({
            "id": int(j), "days": len(idx),
            "drift": round(float(np.mean([rows[i]["drift"] for i in idx])), 5),
            "vol": round(float(np.mean([rows[i]["vol"] for i in idx])), 5),
            "breadth": round(float(np.mean([rows[i]["breadth"] for i in idx])), 3),
            "part": round(float(np.mean([rows[i]["part"] for i in idx])), 1),
            "total_drift": round(float(sum(day_drift)), 3),
            "share_of_drift": None,
        })
    tot = sum(r["total_drift"] for r in regs) or 1.0
    for r in regs:
        r["share_of_drift"] = round(r["total_drift"] / tot, 3)
    recent = X[-5:].mean(0)
    dist = [float(np.linalg.norm(recent - c)) for c in C]
    j = int(np.argmin(dist))
    sim = 1.0 / (1.0 + dist[j])
    return {"regimes": regs, "today": {
        "regime": j, "similarity": round(sim, 3),
        "distance": round(dist[j], 3),
        "closest_drift": regs[j]["drift"] if j < len(regs) else None,
        "note": "similarity only — this is not a direction forecast"}}


# ─────────────────────────────────────────────────────────────────────
# C. CAPITAL SCHEDULER — size as a function of state, evaluated as a schedule
# ─────────────────────────────────────────────────────────────────────
def _resample(nets, n, block, paths, seed=7):
    a = np.asarray(nets, float)
    rng = np.random.default_rng(seed)
    if block <= 1:
        idx = rng.integers(0, a.size, size=(paths, n))
    else:
        nb = int(math.ceil(n / block))
        starts = rng.integers(0, max(a.size - block, 1), size=(paths, nb))
        idx = (starts[:, :, None] + np.arange(block)[None, None, :]
               ).reshape(paths, nb * block)[:, :n]
    return a[idx]


def schedule_mc(nets, sched, n, block=50, paths=600, seed=7, s0=0.01):
    """Run a SIZE SCHEDULE (a function of the live state) over block-resampled paths.

    sched(kind, bal, recent_mu, theta_ref) -> ticket fraction. Everything the
    schedule sees is causal: the balance so far and the path's own recent mean.
    """
    M = _resample(nets, n, block, paths, seed)          # (paths, n)
    P = M.shape[0]
    bal = np.ones(P)
    alive = np.ones(P, bool)
    passed = np.zeros(P, bool)
    dead = np.zeros(P, bool)
    t_pass = np.full(P, np.nan)
    W = 50
    cs = np.zeros((P, n + 1))
    np.cumsum(M, axis=1, out=cs[:, 1:])
    thr = A
    for k in range(n):
        if not alive.any():
            break
        lo = max(0, k - W)
        recent = (cs[:, k + 1] - cs[:, lo]) / max(k + 1 - lo, 1)
        frac = sched(bal, recent, k)
        step = np.clip(frac, 0.0, 1.0) * M[:, k] / 100.0
        bal = np.where(alive, bal * (1.0 + step), bal)
        lb = np.log(bal)
        hit_up = alive & (lb >= thr)
        hit_dn = alive & (lb <= -B)
        t_pass[hit_up] = k + 1
        passed |= hit_up
        dead |= hit_dn
        alive &= ~(hit_up | hit_dn)
    alive_any = ~passed & ~dead
    return {"pass": float(passed.mean()), "death": float(dead.mean()),
            "alive": float(alive_any.mean()),
            "median_pass_trades": float(np.nanmedian(t_pass)) if passed.any() else None}


def schedules(s0=0.01):
    """The schedules evaluated: constants (for reference) and state functions."""
    def const(bal, recent, k):
        return np.full_like(bal, s0)

    def by_buffer(bal, recent, k):
        # scale with room above the death line, unbounded above until the clip:
        # 1.0 at a fresh start (room = the full 3% buffer), shrinking to the
        # floor as the buffer is spent and rising as profit widens it.
        room = np.maximum(bal - 0.97, 0.0) / 0.03
        return s0 * np.clip(0.5 + 0.5 * room, 0.25, 2.0)

    def by_theta(bal, recent, k):
        # scale with the path's OWN recent drift (causal), not a global estimate
        ref = 0.05                                       # measured base, %
        r = np.clip(recent / ref, 0.0, 2.0) if ref else 1.0
        return s0 * np.clip(r, 0.25, 2.0)

    def by_pass(bal, recent, k):
        # gambler's ruin with the live distances, at the live theta
        sd = 0.52                                        # measured, %
        th = np.maximum(2 * (s0 * recent / 100.0) / ((s0 * sd / 100.0) ** 2), 1e-9)
        up = np.maximum(A - np.log(bal), 1e-6)
        dn = np.maximum(np.log(bal) + B, 1e-6)
        p = (1 - np.exp(-th * dn)) / (1 - np.exp(-th * (up + dn)))
        return s0 * np.clip(p / 0.5, 0.25, 2.0)

    return {"const": const, "s(buffer)": by_buffer, "s(theta)": by_theta,
            "s(p_pass)": by_pass}


# ─────────────────────────────────────────────────────────────────────
# D. COST INTELLIGENCE
# ─────────────────────────────────────────────────────────────────────
def cost_intel():
    import spread_table as ST
    t = ST.load_table()
    rows = []
    for pair, r in (t.get("pairs") or {}).items():
        g = ST.cost_gate(pair, table=t)
        avg, day = float(r.get("avg_pct") or 0), float(r.get("day_pct") or 0)
        lo, hi = r.get("min_pct"), r.get("max_pct")
        shock = (day / avg) if avg > 0 and day > 0 else None
        rows.append({"pair": pair, "rt": g["rt_pct"], "avg": avg, "day": day,
                     "stability": round((hi - lo) / avg, 2) if (avg and hi and lo) else None,
                     "shock": round(shock, 2) if shock else None,
                     "cost_of_edge": round(g["rt_pct"] / ST.GROSS_EDGE_PCT, 3),
                     "verdict": g["verdict"], "why": g["why"]})
    rows.sort(key=lambda r: -r["rt"])
    refused = [r["pair"] for r in rows if r["verdict"] == "refuse"]
    thin = [r["pair"] for r in rows if r["verdict"] == "thin"]
    shocking = [r["pair"] for r in rows if (r["shock"] or 0) >= 1.5]
    return {"pairs": rows, "refused": refused, "thin": thin,
            "shocks": shocking, "edge_pct": ST.GROSS_EDGE_PCT}


# ─────────────────────────────────────────────────────────────────────
# E/F. ROBUSTNESS + PORTFOLIO
# ─────────────────────────────────────────────────────────────────────
def worst_run(nets, size):
    """The tape's own worst stretch, at this ticket: max drawdown in log space."""
    a = np.asarray(nets, float)
    if a.size == 0:
        return None
    L = np.cumsum(np.log1p(size * a / 100.0))
    peak = np.maximum.accumulate(np.concatenate([[0.0], L]))
    dd = peak - np.concatenate([[0.0], L])
    return {"max_dd_log": round(float(dd.max()), 5),
            "max_dd_pct_eq": round(float((1 - np.exp(-dd.max())) * 100), 2),
            "death_line_pct": round((1 - math.exp(-B)) * 100, 2)}


def component(name, nets, ts, pace, tolls, size=0.01):
    n = int(pace * EVAL_DAYS)
    base = block_mc(nets, size, n, 50)
    iid = block_mc(nets, size, min(n, int(pace * SECONDARY_DAYS)), 0)
    short = block_mc(nets, size, int(pace * SECONDARY_DAYS), 50)
    # the sensitivity runs stay at the SHORT horizon: they answer "does the
    # answer move", which a two-year horizon blurs by saturating at 100%
    ns = int(pace * SECONDARY_DAYS)
    hi_cost = block_mc(nets, size, ns, 50,
                       toll_shift=-0.5 * float(np.mean(list(tolls.values()))))
    half_mu = block_mc(nets, size, ns, 50, edge_mult=DRIFT_HAIRCUT)
    # rule 5's "small perturbation": +-10% on the drift, not +-50%
    mu_lo = block_mc(nets, size, ns, 50, edge_mult=0.90)
    mu_hi = block_mc(nets, size, ns, 50, edge_mult=1.10)
    replay = replay_tape(nets, ts, size)
    a = np.asarray(nets, float)
    mu, sd = float(a.mean()), float(a.std(ddof=1))
    return {
        "name": name, "n": int(a.size), "mu": round(mu, 5), "sd": round(sd, 5),
        "theta": round(theta_of(mu, sd), 1),
        "p_pass_block": round(base["pass"], 4), "p_death_block": round(base["death"], 4),
        "p_pass_iid": round(iid["pass"], 4), "p_death_iid": round(iid["death"], 4),
        "p_pass_365": round(short["pass"], 4), "p_death_365": round(short["death"], 4),
        "median_pass_trades": base["median_n"],
        "median_pass_days": (round(base["median_n"] / pace, 1)
                             if base["median_n"] else None),
        "horizon_days": HORIZON_DAYS,
        "p_pass_cost_x1.5": round(hi_cost["pass"], 4),
        "p_death_cost_x1.5": round(hi_cost["death"], 4),
        "p_pass_half_drift": round(half_mu["pass"], 4),
        "p_death_half_drift": round(half_mu["death"], 4),
        "p_pass_mu_minus10": round(mu_lo["pass"], 4),
        "p_pass_mu_plus10": round(mu_hi["pass"], 4),
        "worst_run": worst_run(nets, size),
        "replay": replay,
    }


def replay_tape(nets, ts, size=0.01):
    """The single real tape, one path — the ground truth every model is checked on."""
    a = np.asarray(nets, float)
    if a.size == 0:
        return {"verdict": "no series"}
    L = np.cumsum(np.log1p(size * a / 100.0))
    pu = int(np.argmax(L >= A)) if (L >= A).any() else -1
    pd = int(np.argmax(L <= -B)) if (L <= -B).any() else -1
    verdict = ("PASS" if pu >= 0 and (pd < 0 or pu < pd)
               else "DEATH" if pd >= 0 else "ALIVE")
    return {"verdict": verdict, "pass_at_trade": pu, "death_at_trade": pd,
            "final_log": round(float(L[-1]), 4)}


def regime_holdout(nets, ts, pace, size=0.01, paths=900, seed=13):
    """Leave one regime out, at a time, and RE-MEASURE the pass probability.

    Drift totals cannot answer this: a regime can be worth little in summed
    points per trade and still be the only stretch on which the tape ever
    reaches +12%, because reaching it is a path property (ordering, drawdown
    budget), not a sum of means. So each holdout is a genuine block resample of
    the remaining trades and the loss is `P(pass | all) - P(pass | without j)`.

    Returns None when the tape has fewer than two regimes to hold out.
    """
    a = np.asarray(nets, float)
    labels, _rows = trade_regime_labels(nets, ts)
    ids = sorted({int(x) for x in labels if x >= 0})
    if a.size == 0 or len(ids) < 2:
        return None
    n = int(pace * SECONDARY_DAYS)
    _b = block_mc(a, size, n, 50, paths=paths, seed=seed)
    base = float(_b["pass"])
    holdouts = []
    for j in ids:
        sub = a[labels != j]
        if sub.size < 100:
            holdouts.append({"regime": j, "trades": int(sub.size),
                             "p_pass": None, "loss": None})
            continue
        _r = block_mc(sub, size, n, 50, paths=paths, seed=seed)
        p = float(_r["pass"])
        holdouts.append({"regime": j, "trades": int(sub.size),
                         "p_pass": round(p, 4), "loss": round(base - p, 4),
                         "p_death": round(float(_r["death"]), 4),
                         "se": round(math.sqrt(max(p * (1 - p), 1e-12) / paths), 4)})
    meas = [h for h in holdouts if h["loss"] is not None]
    worst = min(meas, key=lambda h: h["p_pass"]) if meas else None
    return {"base": round(base, 4), "base_death": round(float(_b["death"]), 4),
            "holdouts": holdouts, "worst": worst,
            "loss": round(worst["loss"], 4) if worst else 0.0,
            "horizon_days": SECONDARY_DAYS, "paths": paths}


def disqualify(comp, regimes, holdout=None):
    """The five automatic disqualifications. Returns [(rule, fired, why)].

    Rule 3 is MEASURED, not a drift-share test: each regime is held out of the
    tape in turn and the pass probability is re-simulated. It fires when losing
    any single regime costs more than REGIME_PASS_LOSS of P(pass) — the config's
    completion does not survive its own record losing one stretch.
    """
    def fired(rule, cond, why):
        return (rule, bool(cond), why)
    ho = holdout if holdout is not None else comp.get("regime_holdout")
    worst = (ho or {}).get("worst") or {}
    loss = (ho or {}).get("loss") or 0.0
    out = [
        fired("1. edge <= cost", comp["mu"] <= 0,
              f"mu {comp['mu']:+.4f}%/trade is at or below zero"),
        fired("2. block death > tolerance", comp["p_death_block"] > DEATH_TOLERANCE,
              f"P(death) {comp['p_death_block']*100:.1f}% > {DEATH_TOLERANCE*100:.0f}%"),
        fired("3. single-regime dependence", bool(ho) and loss >= REGIME_PASS_LOSS,
              (f"holding out regime {worst.get('regime')} takes P(pass) "
               f"{(ho or {}).get('base', 0)  * 100:.0f}% -> "
               f"{worst.get('p_pass', 0) * 100:.0f}% "
               f"(-{loss * 100:.0f} pts) on the {worst.get('trades')} remaining "
               f"trades") if ho else "not measured: no regime holdout"),
        fired("4. profit dies under realistic costs",
              comp["p_pass_cost_x1.5"] < 0.5 * max(comp["p_pass_365"], 1e-9)
              and comp["p_pass_365"] > 0,
              f"P(pass,365d) {comp['p_pass_365']*100:.0f}% -> "
              f"{comp['p_pass_cost_x1.5']*100:.0f}% at 1.5x toll"),
        # a SMALL perturbation is +-10% on the drift. The answer is the decision
        # (worth attempting / not), so a flip is P(pass) crossing 50%.
        fired("5. answer flips under a small perturbation",
              (comp["p_pass_mu_minus10"] < 0.5) != (comp["p_pass_mu_plus10"] < 0.5),
              f"mu +-10% moves P(pass) {comp['p_pass_mu_minus10']*100:.0f}% -> "
              f"{comp['p_pass_mu_plus10']*100:.0f}% (crosses 50%)"),
    ]
    return out


def portfolio(data):
    tolls = data.get("tolls") or {}
    pace = float(data.get("pace") or 47.4)
    comps, reg = [], None
    for name in CONFIGS:
        got = series(name, data)
        if not got:
            continue
        nets, ts, _ = got
        c = component(name, nets, ts, pace, tolls)
        if reg is None:
            reg = regime_classifier(nets, ts)
        c["regime_holdout"] = regime_holdout(nets, ts, pace)
        c["disqualified"] = disqualify(c, reg["regimes"], c["regime_holdout"])
        comps.append(c)
    # named lanes with no authoritative series: UNMEASURED, not guessed
    for lane in ("champion lane", "GO BIG lane"):
        comps.append({"name": lane, "n": 0, "unmeasured": True,
                      "why": "no authoritative per-trade series on this tape"})
    return comps, reg or {"regimes": [], "today": None}


# ─────────────────────────────────────────────────────────────────────
# the five answers
# ─────────────────────────────────────────────────────────────────────
def completion_rate(c):
    """Contest completions per calendar day — the scalar the objective actually is.

    With repeated attempts and no deadline, P(pass) alone saturates at 1 for every
    positive-drift config and stops discriminating. What discriminates is how many
    finished contests a day of trading buys: pass probability per attempt divided
    by the days an attempt takes, with failure netted out.
    """
    days = c.get("median_pass_days") or 1e9
    return (c["p_pass_block"] * (1.0 - c["p_death_block"])) / max(days, 1e-9)


def _pick(comps):
    """Best by completion rate, subject to no disqualification; if every
    component is disqualified the best is still named — but marked not
    contest-worthy, because 'the best of a disqualified field' is not a
    recommendation."""
    pool = [c for c in comps if not c.get("unmeasured")]
    if not pool:
        return None, False
    for c in pool:
        c["completions_per_day"] = round(completion_rate(c), 8)
    pool.sort(key=lambda c: -c["completions_per_day"])
    for c in pool:
        if not any(f[1] for f in c["disqualified"]):
            return c, True
    return pool[0], False


def trade_regime_labels(nets, ts, k=3):
    """One regime label per TRADE (from its day's features), plus the partition.

    Days with fewer than three trades are not clustered, so their trades carry -1
    and stay in every subset — dropping them would silently change the universe.
    """
    rows = _daily_features(nets, ts)
    if len(rows) < 6:
        return np.full(len(nets), -1), []
    keys = ("drift", "vol", "breadth", "part")
    X, _mu, _sd = _standardize(rows, keys)
    lab, _C = _kmeans(X, k)
    dmap = {rows[i]["day"]: int(lab[i]) for i in range(len(rows))}
    t = np.asarray(ts, float)
    day = (t // 86400000).astype(np.int64) if t.max() > 1e11 else (t // 86400).astype(np.int64)
    return np.array([dmap.get(int(d), -1) for d in day]), rows


def sensitivity_ranking(nets, ts, pace, mean_toll=0.0257, size=0.01,
                        paths=600, seed=11):
    """Rank the assumptions by how far P(pass) ACTUALLY moves when each one varies.

    No labels, no hand-written magnitudes: every candidate is a set of re-runs of
    the same block simulation at the short horizon (where P(pass) still has room to
    move), and its sensitivity is max-minus-min of the simulated P(pass) across its
    own variants. The candidate with the widest swing dominates the answer.

      edge        mu at each end of its own 95% CI
      costs       the toll at 0.75x and 1.5x the measured mean
      regime mix  leave-one-regime-out: drop each partition's trades in turn
      drift       the measured mu at 0.5x and 1.0x (how wrong the estimate may be)
    """
    a = np.asarray(nets, float)
    if a.size < 100:
        return {"winner": None, "candidates": []}
    n = int(pace * SECONDARY_DAYS)
    mu = float(a.mean())
    se = float(a.std(ddof=1) / math.sqrt(a.size))
    base = block_mc(nets, size, n, 50, paths=paths, seed=seed)["pass"]
    out = []

    # 1. the edge, at both ends of its own confidence interval — an absolute
    #    shift in points per trade, so it is defined even when mu <= 0
    p_lo = block_mc(nets, size, n, 50, paths=paths, seed=seed,
                    toll_shift=-1.96 * se)["pass"]
    p_hi = block_mc(nets, size, n, 50, paths=paths, seed=seed,
                    toll_shift=+1.96 * se)["pass"]
    out.append({"id": "edge", "assumption": "the edge estimate (mu at its 95% CI)",
                "variants": [round(p_lo, 4), round(p_hi, 4)],
                "labels": [f"mu {mu-1.96*se:+.4f}%", f"mu {mu+1.96*se:+.4f}%"],
                "swing": round(abs(p_hi - p_lo), 4), "base": round(base, 4)})

    # 2. the toll: every trade repriced at 0.75x and 1.5x the pair's measured cost
    #    (mean_toll is the table's average across the traded pairs)
    p_cheap = block_mc(nets, size, n, 50, paths=paths, seed=seed,
                       toll_shift=+0.25 * mean_toll)["pass"]
    p_dear = block_mc(nets, size, n, 50, paths=paths, seed=seed,
                      toll_shift=-0.50 * mean_toll)["pass"]
    out.append({"id": "costs", "assumption": "costs (toll 0.75x -> 1.5x)",
                "variants": [round(p_cheap, 4), round(p_dear, 4)],
                "labels": ["toll -25%", "toll +50%"],
                "swing": round(abs(p_cheap - p_dear), 4), "base": round(base, 4)})

    # 3. the regime mix: drop each partition in turn
    labels, _rows = trade_regime_labels(nets, ts)
    ids = sorted({int(x) for x in labels if x >= 0})
    if len(ids) >= 2:
        vals = []
        for j in ids:
            sub = a[labels != j]
            if sub.size >= 100:
                vals.append((round(float(block_mc(sub, size, n, 50, paths=paths,
                                                  seed=seed)["pass"]), 4), j))
        if len(vals) >= 2:
            ps = [v[0] for v in vals]
            out.append({"id": "regime", "assumption": "regime mix (leave one regime out)",
                        "variants": ps,
                        "labels": [f"without regime {j}" for _p, j in vals],
                        "swing": round(max(ps) - min(ps), 4), "base": round(base, 4)})

    # 4. how wrong the drift itself may be
    p_half = block_mc(nets, size, n, 50, paths=paths, seed=seed,
                      edge_mult=DRIFT_HAIRCUT)["pass"]
    out.append({"id": "drift", "assumption": "drift (mu at 0.5x -> 1.0x)",
                "variants": [round(p_half, 4), round(base, 4)],
                "labels": ["half the measured mu", "measured mu"],
                "swing": round(abs(base - p_half), 4), "base": round(base, 4)})

    out.sort(key=lambda c: -c["swing"])
    return {"winner": out[0]["assumption"] if out else None,
            "winner_id": out[0]["id"] if out else None,
            "candidates": out, "horizon_days": SECONDARY_DAYS,
            "base_p_pass": round(base, 4)}


def verdict(data=None):
    data = data or _load_json(SERIES_FILE, {})
    comps, reg = portfolio(data)
    best, worthy = _pick(comps)
    got = series(best["name"], data) if best else None
    mon = edge_monitor(*got[:2], float(data.get("pace") or 47.4)) if got else {}
    sched = {}
    if got:
        n = int(float(data.get("pace") or 47.4) * SECONDARY_DAYS)
        for label, fn in schedules().items():
            sched[label] = schedule_mc(got[0], fn, n)
    sens = {"winner": None, "candidates": []}
    if got:
        _tolls = list((data.get("tolls") or {}).values())
        sens = sensitivity_ranking(got[0], got[1], float(data.get("pace") or 47.4),
                                   mean_toll=float(np.mean(_tolls)) if _tolls else 0.0257)
    dom = sens.get("winner")
    dom_why = None
    if sens.get("candidates"):
        top = sens["candidates"][0]
        dom_why = (f"P(pass) {top['variants'][0]*100:.1f}% -> "
                   f"{top['variants'][-1]*100:.1f}% across its variants "
                   f"(swing {top['swing']*100:.1f} pts, base {top['base']*100:.1f}%)")
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "1_best_config": ({"name": best["name"], "cfg": CONFIGS.get(best["name"]),
                           "theta": best["theta"], "mu": best["mu"],
                           "contest_worthy": worthy,
                           "disqualified_on": [f[0] for f in best["disqualified"] if f[1]]}
                          if best else None),
        "2_p_pass": best["p_pass_block"] if best else None,
        "3_p_death": best["p_death_block"] if best else None,
        "4_dominant_uncertainty": {"assumption": dom, "evidence": dom_why},
        "5_next_experiment": None,
        "dashboard": {"components": comps, "regimes": reg.get("regimes") or [],
                      "today": reg.get("today"),
                      "edge_monitor": mon, "schedules": sched,
                      "sensitivity": sens,
                      "cost": cost_intel()},
    }
    out["5_next_experiment"] = _experiment(out)
    with open(VERDICT_FILE, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    return out


def _experiment(v):
    # keyed on the MEASURED winner's id, never on words in its label
    sens = (v.get("dashboard") or {}).get("sensitivity") or {}
    dom = sens.get("winner_id") or ""
    if dom == "edge":
        return ("Extend the tape forward and re-estimate mu on NEW days only "
                "(walk-forward), because the current edge rests on one 183-day "
                "sample whose CI is the binding uncertainty. Concretely: hold the "
                "config fixed, log the next N=500 live-shadow trades, and re-run "
                "this report; the answer changes iff the new CI excludes zero.")
    if dom == "costs":
        return ("Run the spread sampler through a full week and re-price every pair "
                "at its 7-day mean; the 1.5x-toll run is the binding sensitivity, so "
                "the experiment is measuring whether the toll really is 1.5x on the "
                "pairs the config actually trades.")
    if dom == "drift":
        return ("Split the tape in half by calendar and re-fit mu on the first half, "
                "then test the second half; that isolates whether the drift is a "
                "property of the strategy or of the sample.")
    if dom == "regime":
        top = (sens.get("candidates") or [{}])[0]
        lo = min(top.get("variants") or [0.0])
        hi = max(top.get("variants") or [0.0])
        return ("The answer does not survive losing one stretch of tape: dropping a "
                f"single regime moves P(pass) {lo*100:.0f}% -> {hi*100:.0f}%. So the "
                "experiment is not another rule — it is collecting tape until a NEW "
                "regime appears outside this 183-day sample, then re-running this "
                "ranking. Until that tape exists the pass is a claim about a regime "
                "composition the record has not repeated.")
    return ("Re-run the ranking on a held-out stretch of tape and confirm which "
            "assumption moved; the winner here was not one of the known four.")


def print_verdict(v, detail=False):
    b = v["1_best_config"]
    print("1. BEST CONTEST CONFIG")
    if b:
        print(f"   {b['name']}  stop/arm/give/take = {b['cfg']}   theta {b['theta']}   "
              f"mu {b['mu']:+.4f}%/trade")
        if not b["contest_worthy"]:
            print(f"   NOT CONTEST-WORTHY — disqualified on: "
                  f"{'; '.join(b['disqualified_on'])}")
    print(f"2. ESTIMATED P(pass)  {(v['2_p_pass'] or 0)*100:.1f}%")
    print(f"3. ESTIMATED P(death) {(v['3_p_death'] or 0)*100:.1f}%")
    print(f"4. DOMINANT UNCERTAINTY: {v['4_dominant_uncertainty']['assumption']}")
    print(f"   {v['4_dominant_uncertainty']['evidence']}")
    for c in ((v["dashboard"].get("sensitivity") or {}).get("candidates") or [])[1:]:
        print(f"   (next: {c['assumption']} — {c['swing']*100:.1f} pts)")
    print(f"5. NEXT EXPERIMENT: {v['5_next_experiment']}")
    if not detail:
        return
    d = v["dashboard"]
    print("\n--- EDGE MONITOR " + "-" * 44)
    for k, m in (d.get("edge_monitor") or {}).items():
        print(f"  last {k:<5} n={m['n']:<5} mu {m['mu']:+.5f}  sd {m['sd']:.5f}  "
              f"t {m['t']:+.2f}  CI [{m['ci'][0]:+.5f},{m['ci'][1]:+.5f}]  "
              f"theta {m['theta']:.0f}  p_free {m['p_free']:.3f}"
              f"{'  <-- INDISTINGUISHABLE FROM ZERO' if m['zero_indistinguishable'] else ''}")
    print("\n--- REGIMES " + "-" * 49)
    for r in d.get("regimes") or []:
        print(f"  regime {r['id']}: {r['days']:>3}d  drift {r['drift']:+.5f}  vol {r['vol']:.4f}  "
              f"breadth {r['breadth']:.2f}  part {r['part']:.0f}  drift share {r['share_of_drift']:+.2f}")
    print(f"  TODAY resembles: {d.get('today')}")
    print("\n--- SCHEDULES (365d, block-50) " + "-" * 26)
    for k, s in (d.get("schedules") or {}).items():
        print(f"  {k:<10} pass {s['pass']*100:5.1f}%  death {s['death']*100:4.1f}%  "
              f"median pass at {s['median_pass_trades']}")
    print("\n--- COMPONENTS " + "-" * 47)
    for c in d["components"]:
        if c.get("unmeasured"):
            print(f"  {c['name']:<14} UNMEASURED — {c['why']}")
            continue
        bad = [f[0] for f in c["disqualified"] if f[1]]
        print(f"  {c['name']:<14} n={c['n']:<5} theta {c['theta']:>6.0f}  "
              f"pass {c['p_pass_block']*100:5.1f}%  death {c['p_death_block']*100:4.1f}%  "
              f"median {str(c.get('median_pass_days')):>6}d  "
              f"comp/day {c.get('completions_per_day', 0):.5f}  "
              f"replay {c['replay']['verdict']:<5} "
              + (f" DISQUALIFIED: {'; '.join(bad)}" if bad else " CLEAR"))
    print("\n--- SENSITIVITY (each assumption re-run, ranked by swing) " + "-" * 8)
    for c in (d.get("sensitivity") or {}).get("candidates") or []:
        print(f"  {c['swing']*100:5.1f} pts  {c['assumption']:<42} "
              + "  ".join(f"{v*100:.1f}%" for v in c["variants"]))
    print("\n--- REGIME HOLDOUT (rule 3, re-simulated) " + "-" * 22)
    for c in d["components"]:
        ho = c.get("regime_holdout")
        if not ho:
            continue
        print(f"  {c['name']:<14} all {ho['base']*100:5.1f}%  "
              + "  ".join(f"-r{h['regime']} {(h['p_pass'] or 0)*100:5.1f}%"
                          for h in ho["holdouts"])
              + f"   worst loss {ho['loss']*100:.0f} pts")
    print("\n--- COST " + "-" * 51)
    c = d["cost"]
    print(f"  refused ({len(c['refused'])}): {', '.join(c['refused'][:12])}")
    print(f"  thin ({len(c['thin'])}): {', '.join(c['thin'])}")
    print(f"  spread shocks >=1.5x today: {', '.join(c['shocks']) or 'none'}")


# ─────────────────────────────────────────────────────────────────────
# THE LIVE CONTROLLER — the Overwatch's four decisions, per scanner cycle
# ─────────────────────────────────────────────────────────────────────
CTL_STATE_FILE = os.path.join(HERE, "contest_control_state.json")
CTL_TTL_S = 900.0            # how long a computed snapshot is reused
STOP_T = -1.96               # short-window t below this: the edge is NEGATIVE
RESUME_T = 0.0               # short-window t above this: restart is allowed
RESUME_HOLD_S = 3 * 3600.0   # ...and not before this much time has passed
SIZE_FLOOR, SIZE_CEIL = 0.25, 2.0


def _ctl_state():
    return _load_json(CTL_STATE_FILE, {})


def _write_ctl_state(doc, path=None):
    target = str(path or CTL_STATE_FILE)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    os.replace(tmp, target)


def armed() -> bool:
    """The single arming flag. False means ADVISORY: every decision is computed
    and logged, and none of them is allowed to change what the scanner does."""
    try:
        import overwatch_timing as ot
        return bool(getattr(ot, "ARMED", False))
    except Exception:
        return False


class ContestController:
    """Decides trade / size / stop / restart for the running scanner.

    The controller is a READER of evidence and a WRITER of one small state file:
    it never places, sizes or cancels an order itself. Its four answers are:

      trade?    cost gate + edge sign + the halt flag
      size      a schedule of the state (theta proxy, pass proxy), not a constant
      stop      the short-window edge is significantly NEGATIVE (t < -1.96)
      restart   the halt was taken, the short window has turned non-negative, and
                a minimum hold has elapsed (hysteresis: no flapping)

    Inertia is the contract: with overwatch_timing.ARMED False every method still
    computes and logs, and `allow` stays True / `size_frac` stays None.
    """

    def __init__(self, series_path=None, verdict_path=None, state_path=None):
        self.series_path = series_path or SERIES_FILE
        self.verdict_path = verdict_path or VERDICT_FILE
        self.state_path = state_path or CTL_STATE_FILE
        self._snap = None
        self._at = 0.0

    # -- evidence -----------------------------------------------------
    def _series(self):
        d = _load_json(self.series_path, {})
        v = _load_json(self.verdict_path, {})
        best = ((v.get("1_best_config") or {}).get("name")) or "wide"
        got = (d.get("configs") or {}).get(best)
        if not got:
            for name in ("wide", "shipped", "mid", "tight"):
                got = (d.get("configs") or {}).get(name)
                if got:
                    best = name
                    break
        if not got:
            return None, best, d
        return (np.asarray(got["nets"], float), np.asarray(got["ts"], float)), best, d

    def snapshot(self, force=False):
        now = time.time()
        if self._snap is not None and not force and (now - self._at) < CTL_TTL_S:
            return self._snap
        got, name, data = self._series()
        st = _load_json(self.state_path, {})
        snap = {"at": now, "config": name, "armed": armed(),
                "halted": bool(st.get("halted")), "halted_at": st.get("halted_at"),
                "halt_why": st.get("why"),
                "trade": True, "size_mult": 1.0, "why": "no series — advisory only",
                "short_t": None, "short_mu": None, "mid_t": None}
        if got is None:
            self._snap, self._at = snap, now
            return snap
        nets, ts = got
        mon = edge_monitor(nets, ts, float(data.get("pace") or 47.4),
                           windows=(500, 2000, None))
        short, mid = mon["500"], mon["2000"]
        snap.update({"short_t": short["t"], "short_mu": short["mu"],
                     "mid_t": mid["t"], "mid_mu": mid["mu"],
                     "all_mu": mon["all"]["mu"],
                     "theta": mon["all"]["theta"],
                     "edge_zero": short["zero_indistinguishable"]})

        # STOP: the short window is significantly negative, not merely soft
        if short["t"] <= STOP_T and not snap["halted"]:
            st.update({"halted": True,
                       "halted_at": datetime.now(timezone.utc).isoformat(),
                       "why": f"short-window edge t={short['t']:+.2f} "
                              f"(mu {short['mu']:+.4f}%/trade) — stop trading"})
            if snap["armed"]:
                _write_ctl_state(st, self.state_path)   # inert while disarmed
            snap.update({"halted": True, "halted_at": st["halted_at"],
                        "halt_why": st["why"]})
        # RESTART: turned non-negative, and held out long enough to trust it
        elif snap["halted"]:
            since = 0.0
            try:
                since = now - datetime.fromisoformat(str(st.get("halted_at"))).timestamp()
            except (TypeError, ValueError):
                since = RESUME_HOLD_S
            if short["t"] > RESUME_T and since >= RESUME_HOLD_S:
                st.update({"halted": False, "resumed_at": datetime.now(timezone.utc).isoformat(),
                           "why": f"short-window edge recovered t={short['t']:+.2f} "
                                  f"— resume trading"})
                if snap["armed"]:
                    _write_ctl_state(st, self.state_path)
                snap.update({"halted": False, "halt_why": st["why"]})

        # SIZE: a schedule of the state, never a constant
        ref = abs(float(snap.get("all_mu") or 0.0)) or 1e-9
        mult = float(np.clip(float(mid["mu"]) / ref, SIZE_FLOOR, SIZE_CEIL))
        if snap["halted"]:
            mult = 0.0
        snap["size_mult"] = round(mult, 3)
        if snap["halted"]:
            snap.update({"trade": False, "why": snap["halt_why"] or "halted"})
        elif short["t"] <= STOP_T:
            snap.update({"trade": False,
                         "why": f"short-window edge t={short['t']:+.2f} — no new entries"})
        else:
            snap.update({"trade": True,
                         "why": f"mid-window mu {mid['mu']:+.4f}%, "
                                f"short t {short['t']:+.2f}, size x{mult:.2f}"})
        self._snap, self._at = snap, now
        return snap

    # -- decisions ----------------------------------------------------
    def allow(self, pair):
        """(allow, why, size_frac|None). Advisory (and permissive) unless ARMED."""
        snap = self.snapshot()
        try:
            import spread_table as _stg
            gate = _stg.cost_gate(pair)
        except Exception:
            gate = {"verdict": "unmeasured", "why": "cost table unavailable"}
        why_extra = "" if gate["verdict"] in ("ok", "thin", "unmeasured") else gate["why"]
        if not snap["armed"]:
            return True, f"ADVISORY (ARMED=False): {snap['why']}" + (
                f" | {why_extra}" if why_extra else ""), None
        if gate["verdict"] == "refuse":
            return False, f"cost gate: {gate['why']}", 0.0
        if not snap["trade"]:
            return False, snap["why"], 0.0
        frac = snap["size_mult"] * (0.5 if gate["verdict"] == "thin" else 1.0)
        return True, snap["why"], round(max(frac, 0.05), 3)

    def size_usd(self, usd, pair=None):
        """Scale a base ticket by the schedule; never below 5% or above 2x."""
        ok, _why, frac = self.allow(pair)
        if not ok or frac is None:
            return float(usd), "unchanged"
        return round(max(0.0, float(usd)) * float(frac), 2), f"x{frac:g}"


_CTL = None


def controller() -> ContestController:
    global _CTL
    if _CTL is None:
        _CTL = ContestController()
    return _CTL


def scanner_gate(pair):
    """The scanner's one call: {allow, why, size_frac, armed}."""
    c = controller()
    allow, why, frac = c.allow(pair)
    return {"allow": bool(allow), "why": why, "size_frac": frac,
            "armed": armed()}


def scanner_cycle():
    """Per-cycle refresh + the one line the scanner logs."""
    snap = controller().snapshot(force=True)
    return snap


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if "--build" in argv:
        build_series(force="--force" in argv)
    v = verdict()
    print_verdict(v, detail="--detail" in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
