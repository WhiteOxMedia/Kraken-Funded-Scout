"""contest_math.py — the contest's constraint as an equation, then solved.

Not an entry study. This takes the account's own constants and the engine's measured
per-trade distribution and answers: can this thing reach +12% before it touches -3%?

THE EQUATION
------------
Balance walks multiplicatively. One trade at size s (fraction of balance) with net
edge e (% of the ticket, after the 0.08% round trip) grows the balance by

    g = s * e / 100            mu = ln(1+g)     (log units)

Barriers, in log units, do not move:

    a = ln(T/B0)    T = start * 1.12      the win
    b = ln(B0/D)    D = start * 0.97      the death line

Time:    N trades  ->  a = N*mu       =>  N = a/mu,   e = 100*a/(N*s)
Odds:    P(win) = (1 - exp(-theta*b)) / (1 - exp(-theta*(a+b))),  theta = 2*mu/sigma^2
         and sigma = s*sigma_e/100, so  theta = 200*e / (s * sigma_e^2)

Neither leg alone decides it; both must hold. Run:

    python scratch/_dump_nets.py      # once: the measured per-trade nets
    python scratch/contest_math.py
"""
from __future__ import annotations

import json
import math
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import contest_account as ca

OUT = os.path.join(HERE, "_contest_math.json")


def barriers():
    s = ca.load_state()
    start = float(s["start_balance"])
    b0 = float(s["current_balance"])
    death = ca.death_line(s)
    target = start * (1.0 + ca.PROFIT_TARGET_PCT / 100.0)
    return b0, start, death, target, math.log(target / b0), math.log(b0 / death)


def _p(theta, a, b):
    if abs(theta) < 1e-12:
        return b / (a + b)
    return (1 - math.exp(-theta * b)) / (1 - math.exp(-theta * (a + b)))


def theta_for(p, a, b):
    """Smallest theta with P(win) >= p (bisection, bracket widened to fit)."""
    lo, hi = 1e-9, 1.0
    while _p(hi, a, b) < p and hi < 1e12:
        hi *= 2.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if _p(mid, a, b) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def time_edge(a, n_trades, s):
    """Edge (% of ticket) that reaches the target in n_trades at size s."""
    if n_trades <= 0 or s <= 0:
        return float("inf")
    return 100.0 * a / (n_trades * s)


def odds_edge(theta, s, sigma_e):
    return theta * s * sigma_e ** 2 / 200.0


def pass_prob(e, s, a, b, sigma_e):
    mu = math.log(1 + s * e / 100.0)
    var = (s * sigma_e / 100.0) ** 2
    if var <= 0:
        return 1.0 if mu > 0 else 0.0
    return _p(2 * mu / var, a, b)


def monte_carlo(nets, s, a, b, paths=4000, max_trades=20000, seed=7):
    """Independent check of the closed form: bootstrap real trades and walk them."""
    import numpy as np
    arr = np.asarray(nets, dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(paths, max_trades))
    L = np.cumsum(np.log1p(s * arr[idx] / 100.0), axis=1)
    up, dn = L >= a, L <= -b
    first_up = np.where(up.any(1), up.argmax(1), max_trades + 1)
    first_dn = np.where(dn.any(1), dn.argmax(1), max_trades + 1)
    p_up = float((first_up < first_dn).mean())
    p_dn = float((first_dn < first_up).mean())
    return p_up, p_dn, 1.0 - p_up - p_dn


def main():
    nets = json.load(open(os.path.join(HERE, "_live_trade_nets.json")))
    all_nets = [float(x) for x in nets["all"]]
    nov = [float(x) for x in nets["november"]]
    b0, start, death, target, a, b = barriers()

    mean = st.fmean(all_nets)
    sigma_e = st.pstdev(all_nets)
    wins = [x for x in all_nets if x > 0]
    losses = [x for x in all_nets if x <= 0]
    w = len(wins) / len(all_nets)
    avg_win = st.fmean(wins) if wins else 0.0
    avg_loss = abs(st.fmean(losses)) if losses else 0.0
    gross = mean + ca.COST_ROUNDTRIP_PCT
    nov_mean = st.fmean(nov) if nov else 0.0
    mu1 = math.log(1 + 0.01 * mean / 100)

    out = {
        "balance_usd": round(b0, 2), "start_usd": start,
        "target_usd": round(target, 2), "death_line_usd": round(death, 2),
        "a_log": round(a, 5), "b_log": round(b, 5),
        "win_to_die_ratio": round(a / b, 2),
        "measured": {
            "n": len(all_nets), "mean_net_pct": round(mean, 4),
            "sd_net_pct": round(sigma_e, 4),
            "gross_before_cost_pct": round(gross, 4),
            "cost_share_of_gross_pct": round(100 * ca.COST_ROUNDTRIP_PCT / gross, 1),
            "win_pct": round(100 * w, 1),
            "avg_win_pct": round(avg_win, 3), "avg_loss_pct": round(-avg_loss, 3),
            "mu_per_trade_at_1pct": mu1,
            "november_mean_pct": round(nov_mean, 4),
        },
        "trades_to_target_at_1pct": (int(a / mu1) if mu1 > 0 else None),
    }

    # ── the time leg: edge needed to arrive inside a horizon ─────────────
    grid = {}
    for s in (0.005, 0.01, 0.02, 0.05):
        for per_day in (5, 20, 87):
            for days in (30, 90, 180):
                n = per_day * days
                e_t = time_edge(a, n, s)
                grid[f"s={s*100:g}%|{per_day}/d|{days}d"] = {
                    "trades": n,
                    "edge_needed_pct": round(e_t, 4),
                    "x_measured": round(e_t / mean, 1),
                }
    out["grid"] = grid

    # ── the odds leg ─────────────────────────────────────────────────────
    odds = {}
    for p in (0.50, 0.80, 0.90, 0.95):
        th = theta_for(p, a, b)
        odds[f"P={int(p*100)}%"] = {
            "theta": round(th, 4),
            "edge_needed_pct": {f"{s*100:g}%": round(odds_edge(th, s, sigma_e), 4)
                                for s in (0.005, 0.01, 0.02, 0.05)},
        }
    out["odds"] = odds

    # ── the engine as measured ───────────────────────────────────────────
    out["engine"] = {
        "edge_pct": round(mean, 4), "sd_pct": round(sigma_e, 4),
        "theta_at_1pct": round(200 * mean / (0.01 * sigma_e ** 2), 2),
        "pass_prob_any_time_at_1pct": round(pass_prob(mean, 0.01, a, b, sigma_e), 3),
        "days_at_87_per_day": round(a / mu1 / 87, 0) if mu1 > 0 else None,
        "days_at_20_per_day": round(a / mu1 / 20, 0) if mu1 > 0 else None,
    }

    # ── the lever: what payoff would be needed, win rate and stop held ───
    lev = {}
    for label, s_, per_day, days in (("30d,1%,20/d", .01, 20, 30),
                                     ("30d,1%,87/d", .01, 87, 30),
                                     ("90d,1%,20/d", .01, 20, 90),
                                     ("30d,5%,20/d", .05, 20, 30)):
        e_req = time_edge(a, per_day * days, s_)
        aw = (e_req + (1 - w) * avg_loss) / w
        lev[label] = {"edge_needed_pct": round(e_req, 4),
                      "avg_win_needed_pct": round(aw, 3),
                      "x_current_avg_win": round(aw / avg_win, 2)}
    out["levers"] = lev

    f_break = mean / (mean - nov_mean) if mean > nov_mean else float("nan")
    out["november_flip"] = {
        "nov_mean": round(nov_mean, 4),
        "break_even_nov_share": round(f_break, 3),
        "share_that_must_stay_good": round(1 - f_break, 3),
    }

    # ── closed form vs bootstrap ────────────────────────────────────────
    mc = {}
    for s_ in (0.01, 0.02):
        pu, pd, pn = monte_carlo(all_nets, s_, a, b)
        mc[f"{s_*100:g}%"] = {"analytic": round(pass_prob(mean, s_, a, b, sigma_e), 3),
                              "mc": {"up": round(pu, 3), "down": round(pd, 3),
                                     "unresolved": round(pn, 3)}}
    out["mc_validation"] = mc

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)

    print(f"barriers: B0 ${b0:,.2f}  target ${target:,.2f} (+{100*a:.2f}% log)  "
          f"death ${death:,.2f} (-{100*b:.2f}% log)")
    print(f"          win:die ratio {a/b:.2f} : 1")
    print(f"\nengine per trade: mean {mean:+.4f}%  sd {sigma_e:.4f}%  "
          f"gross {gross:+.4f}%  cost = "
          f"{100*ca.COST_ROUNDTRIP_PCT/gross:.0f}% of gross")
    print(f"  win {100*w:.1f}%  avg win {avg_win:+.3f}%  avg loss {-avg_loss:+.3f}%"
          f"  (Nov mean {nov_mean:+.4f}%)")
    print(f"\ntrades to target at 1%: {out['trades_to_target_at_1pct']:,}"
          f"  -> {out['engine']['days_at_87_per_day']:.0f} days at 87/day, "
          f"{out['engine']['days_at_20_per_day']:.0f} at 20/day")
    print(f"theta at 1% {out['engine']['theta_at_1pct']:.0f} -> pass probability "
          f"{out['engine']['pass_prob_any_time_at_1pct']:.1%} given enough time")

    print("\nedge needed to arrive in time (% of ticket, net):")
    print(f"  {'size':>6} {'pace':>6} " + " ".join(f"{d:>9}d" for d in (30, 90, 180)))
    for s in (0.005, 0.01, 0.02, 0.05):
        for per_day in (5, 20, 87):
            row = [f"{grid[f's={s*100:g}%|{per_day}/d|{d}d']['edge_needed_pct']:>10.4f}"
                   for d in (30, 90, 180)]
            print(f"  {s*100:>5g}% {per_day:>5}/d " + " ".join(row))
    print(f"  (measured is {mean:+.4f}%)")

    print("\nedge needed for a pass probability (odds only, time ignored):")
    for k, v in odds.items():
        print(f"  {k:>6}  theta {v['theta']:>9.2f}   " +
              "  ".join(f"{s}: {e:+.4f}%" for s, e in v["edge_needed_pct"].items()))

    print("\nthe payoff lever, win rate and stop held fixed:")
    for k, v in lev.items():
        print(f"  {k:>12}: edge {v['edge_needed_pct']:+.4f}%  ->  avg winner "
              f"{v['avg_win_needed_pct']:+.3f}%  ({v['x_current_avg_win']}x the "
              f"current {avg_win:+.3f}%)")
    print(f"\nNov drift {nov_mean:+.4f}%: the walk is positive only while fewer than "
          f"{f_break:.1%} of trades come from that regime")

    print("\nclosed form vs bootstrap of the real trades:")
    for k, v in mc.items():
        print(f"  size {k:>4}: analytic {v['analytic']:.1%}   mc up "
              f"{v['mc']['up']:.1%} / down {v['mc']['down']:.1%} / neither "
              f"{v['mc']['unresolved']:.1%}")

    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
