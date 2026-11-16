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


def disqualify(comp, regimes):
    """The five automatic disqualifications. Returns [(rule, fired, why)].

    Rule 3 asks the question behind the rule rather than a drift-share number:
    if the best regime is removed, does the REMAINDER still have positive drift?
    A config whose total drift goes negative without its best stretch is
    dependent on that stretch, whatever its share of the total.
    """
    def fired(rule, cond, why):
        return (rule, bool(cond), why)
    share = max((r["share_of_drift"] for r in regimes), default=0.0)
    total = sum(r["total_drift"] for r in regimes) if regimes else 0.0
    rest = total - (share * total if total else 0.0)
    out = [
        fired("1. edge <= cost", comp["mu"] <= 0,
              f"mu {comp['mu']:+.4f}%/trade is at or below zero"),
        fired("2. block death > tolerance", comp["p_death_block"] > DEATH_TOLERANCE,
              f"P(death) {comp['p_death_block']*100:.1f}% > {DEATH_TOLERANCE*100:.0f}%"),
        fired("3. single-regime dependence", bool(regimes) and rest <= 0,
              f"best regime carries {share*100:.0f}% of drift; without it the "
              f"remainder is {rest:+.3f}"),
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
        c["disqualified"] = disqualify(c, reg["regimes"])
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


def _dominant_uncertainty(best, mon, rob):
    """Which assumption, if wrong, moves the answer most."""
    cands = []
    if mon:
        w = mon.get("500") or {}
        if w.get("zero_indistinguishable"):
            cands.append(("the edge estimate itself (CI includes zero)",
                          f"t={w.get('t')} on the last {w.get('n')} trades"))
        else:
            cands.append(("the edge estimate", f"t={w.get('t')} on the last {w.get('n')} trades"))
    cands.append(("costs", f"P(pass) {best['p_pass_block']*100:.0f}% -> "
                           f"{best['p_pass_cost_x1.5']*100:.0f}% at 1.5x toll"))
    cands.append(("drift", f"P(pass) {best['p_pass_block']*100:.0f}% -> "
                           f"{best['p_pass_half_drift']*100:.0f}% at half the measured mu"))
    cands.append(("regime mix", "the worst run is "
                                f"{best['worst_run']['max_dd_pct_eq']:.2f}% against a "
                                f"{best['worst_run']['death_line_pct']:.2f}% line"))
    # magnitude = the swing the assumption produces in P(pass)
    swing = {
        "the edge estimate": abs(best["p_pass_block"] - best["p_pass_half_drift"]),
        "the edge estimate itself (CI includes zero)": abs(best["p_pass_block"] - best["p_pass_half_drift"]),
        "costs": abs(best["p_pass_block"] - best["p_pass_cost_x1.5"]),
        "drift": abs(best["p_pass_block"] - best["p_pass_half_drift"]),
        "regime mix": abs(best["p_pass_block"] - best["p_pass_half_drift"]),
    }
    return max(cands, key=lambda c: swing.get(c[0], 0.0))


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
    dom, dom_why = _dominant_uncertainty(best, mon, comps) if best else (None, None)
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
                      "cost": cost_intel()},
    }
    out["5_next_experiment"] = _experiment(out)
    with open(VERDICT_FILE, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    return out


def _experiment(v):
    dom = (v["4_dominant_uncertainty"] or {}).get("assumption") or ""
    if "edge" in dom:
        return ("Extend the tape forward and re-estimate mu on NEW days only "
                "(walk-forward), because the current edge rests on one 183-day "
                "sample whose CI is the binding uncertainty. Concretely: hold the "
                "config fixed, log the next N=500 live-shadow trades, and re-run "
                "this report; the answer changes iff the new CI excludes zero.")
    if "cost" in dom:
        return ("Run the spread sampler through a full week and re-price every pair "
                "at its 7-day mean; the 1.5x-toll run is the binding sensitivity, so "
                "the experiment is measuring whether the toll really is 1.5x on the "
                "pairs the config actually trades.")
    if "drift" in dom:
        return ("Split the tape in half by calendar and re-fit mu on the first half, "
                "then test the second half; that isolates whether the drift is a "
                "property of the strategy or of the sample.")
    return ("Replay the tape with the regime partitions held out one at a time "
            "(leave-one-regime-out) and re-measure P(pass); that tests whether the "
            "answer survives losing its best stretch.")


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
    print("\n--- COST " + "-" * 51)
    c = d["cost"]
    print(f"  refused ({len(c['refused'])}): {', '.join(c['refused'][:12])}")
    print(f"  thin ({len(c['thin'])}): {', '.join(c['thin'])}")
    print(f"  spread shocks >=1.5x today: {', '.join(c['shocks']) or 'none'}")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if "--build" in argv:
        build_series(force="--force" in argv)
    v = verdict()
    print_verdict(v, detail="--detail" in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
