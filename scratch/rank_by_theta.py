"""rank_by_theta.py — rank the measured configurations by PASS PROBABILITY.

The contest scores one thing: balance reaches start*1.12 before start*0.97, no deadline.
For a multiplicative walk between two absorbing barriers that is gambler's ruin:

    a = ln(1.12) = 0.11333        the win
    b = ln(1/0.97) = 0.030459     the death line
    P = (1 - exp(-theta*b)) / (1 - exp(-theta*(a+b)))      theta = 2*mu/sigma^2

with mu, sigma the per-trade log return of EQUITY. At size s with ticket edge e and
ticket sd sigma_e:  mu ~ s*e/100,  sigma ~ s*sigma_e/100  =>  theta = 200e/(s*sigma_e^2).

Two consequences drive the whole ranking:
  * theta falls as 1/s. Bigger tickets do NOT raise pass probability; they divide it.
  * with no deadline P -> 1 for any theta > 0, so the honest question is P inside a
    horizon. Both are reported: the time-free theta and a bootstrap MC at 90/180/365
    days at the measured trade rate.

Sources: _live_trade_nets.json (the 15,877 band trades, per-trade net %), and the
aggregate means measured for the other lanes.
"""
from __future__ import annotations

import json
import math
import os
import statistics as st
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

A = math.log(1.12)
B = math.log(1.0 / 0.97)
PER_DAY = 87.0                       # the tape's own pace: 15,877 over 183 days
HORIZONS = (90, 180, 365)
OUT = os.path.join(HERE, "_theta_ranking.json")


def theta_of(e_pct, s, sigma_e):
    mu = s * e_pct / 100.0
    sig = s * sigma_e / 100.0
    if sig <= 0:
        return 0.0
    return 2.0 * mu / sig ** 2


def p_closed(theta):
    if abs(theta) < 1e-12:
        return B / (A + B)
    return (1 - math.exp(-theta * B)) / (1 - math.exp(-theta * (A + B)))


def mc(nets, s, n_trades, paths=3000, seed=11):
    """Bootstrap the measured per-trade net% distribution, walk the barriers.

    The empirical distribution carries the real 2% stop tail, which a normal
    approximation would miss.
    """
    arr = np.asarray(nets, dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(paths, n_trades))
    step = np.log1p(s * arr[idx] / 100.0)
    L = np.cumsum(step, axis=1)
    up, dn = L >= A, L <= -B
    fu = np.where(up.any(1), up.argmax(1), n_trades + 1)
    fd = np.where(dn.any(1), dn.argmax(1), n_trades + 1)
    return float((fu < fd).mean()), float((fd < fu).mean()), float((fu == fd).mean())


def main():
    nets = json.load(open(os.path.join(HERE, "_live_trade_nets.json")))
    band = [float(x) for x in nets["all"]]
    e_band = st.fmean(band)
    sd_band = st.pstdev(band)
    cheap = [x + 0.059 for x in band]          # 0.021% toll instead of 0.08%
    e_cheap = st.fmean(cheap)
    nov = [float(x) for x in nets["november"]]
    e_nov = st.fmean(nov)

    print(f"per-trade, band + trend-ride: e {e_band:+.4f}%  sd {sd_band:.4f}%  "
          f"n {len(band):,}")
    print(f"  same trades at the measured cheap-pair toll (0.021%): "
          f"e {e_cheap:+.4f}%")
    print(f"  November slice: e {e_nov:+.4f}%")

    # ── 1. theta, time-free ────────────────────────────────────────────────
    rows = []
    for label, e, series in (("band+ride, 0.08 toll", e_band, band),
                             ("band+ride, cheap toll", e_cheap, cheap)):
        for s in (0.005, 0.01, 0.02, 0.05, 0.50, 1.00):
            th = theta_of(e, s, sd_band)
            rows.append({"config": label, "size": s, "e_pct": round(e, 4),
                         "theta": round(th, 1), "p_time_free": round(p_closed(th), 3),
                         "series": series})

    # lanes measured only in aggregate: same exit => same sd is the fair assumption
    for label, e in (("scanner+ride (May-Oct)", 0.0086),
                     ("scanner+ride (all)", -0.0107),
                     ("scanner+ride (Nov)", -0.0672),
                     ("band+ride (train only)", 0.0355),
                     ("scanner own exit (net)", -0.1168),
                     ("go-big all-in, gap-realised", -0.0416)):
        s = 0.01 if "own exit" in label or "scanner+ride" in label or "train" in label else 1.0
        th = theta_of(e, s, sd_band)
        rows.append({"config": label, "size": s, "e_pct": tuple.__class__ and round(e, 4),
                     "theta": round(th, 1),
                     "p_time_free": round(p_closed(th), 3) if th > 0 else 0.0,
                     "series": None})

    rows.sort(key=lambda r: -r["theta"])
    print("\n1. RANK BY THETA (time-free pass probability)")
    print(f"   {'#':>2} {'config':<26} {'size':>6} {'e%':>8} {'theta':>9} {'P':>7}")
    for i, r in enumerate(rows, 1):
        print(f"   {i:>2} {r['config']:<26} {r['size']*100:>5g}% "
              f"{r['e_pct']:>+8.4f} {r['theta']:>9.1f} {r['p_time_free']*100:>6.1f}%")

    # ── 2. the same ranking inside a horizon, on the real distribution ─────
    print("\n2. P(pass) INSIDE A HORIZON, bootstrapped on the 15,877 real trades")
    print(f"   pace {PER_DAY:.0f} trades/day")
    horizons = {}
    for label, series in (("band+ride cheap toll", cheap),
                          ("band+ride 0.08 toll", band)):
        for s in (0.01, 0.02, 0.05):
            for d in HORIZONS:
                n = int(min(PER_DAY * d, 20000))
                p, dth, none = mc(series, s, n)
                horizons[f"{label}|{s*100:g}%|{d}d"] = {
                    "n_trades": n, "pass": round(p, 3), "death": round(dth, 3),
                    "neither": round(none, 3)}
    for k, v in horizons.items():
        print(f"   {k:<28} n={v['n_trades']:>5}  pass {v['pass']*100:>5.1f}%  "
              f"death {v['death']*100:>4.1f}%  neither {v['neither']*100:>5.1f}%")

    best = rows[0]
    print(f"\nBEST BY THETA: {best['config']} at {best['size']*100:g}% "
          f"-> theta {best['theta']:.0f}")
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"a": A, "b": B, "ranked": [{k: v for k, v in r.items()
                                               if k != "series"} for r in rows],
                   "horizons": horizons,
                   "band_e": round(e_band, 4), "band_sd": round(sd_band, 4),
                   "cheap_e": round(e_cheap, 4)}, fh, indent=1)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
