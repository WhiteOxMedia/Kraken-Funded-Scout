"""orderflow_study.py — does ANY pre-entry signal in the repo's data predict the
next 30-60 minutes by more than the 0.08% round trip? (Nov 15)

Substrate
  scratch/kraken_5m_long_183d.json — 30 pairs, 1,544,445 5m bars, May 14 – Nov 13,
  volume present on 100% of bars. This is the only historical substrate in the repo
  with usable order-flow content.

What the repo does NOT have (checked, not assumed)
  funding, open interest, spread history, book/depth history — none are stored.
  execution_tiers.measure_book() reads a live book to price an order and never
  persists it; app.py's SPREAD is the modelled 10bps constant; config.max_spread_pct
  is a live filter. So those four families cannot be tested on history at all.

Signals tested (all pre-entry, computable from bars <= t)
  ofi3, ofi12   signed-volume imbalance: sum((2*CLV-1)*vol)/sum(vol) — the standard
                order-flow-imbalance proxy, CLV = (c-l)/(h-l)
  vdelta12      up-volume minus down-volume share over 12 bars
  volz          volume vs its 24h mean, in sd
  absorb        high volume with a small body (absorption at the level)
  dipvol        volume on down bars / volume on up bars over 6 bars (capitulation)
  wick3         (lower wick - upper wick)/range over 3 bars (buying tail)
  vwapdev       close vs 24h VWAP
  rangex        current range vs 24h mean range
  ret6, ret12   trailing 30/60-minute return (momentum baselines)

Method
  TRAIN = May 14 – Oct 31.  HOLDOUT = Nov 1 – Nov 13 (never used to fit anything).
  Quintile edges are computed on TRAIN only, then applied unchanged to HOLDOUT.
  Horizon 6 bars (30 min) and 12 bars (60 min), entry and exit at the close, so a
  trade costs exactly the 0.080% round trip. Entries are strided by the horizon, so
  the t-stats are not inflated by overlapping windows.

A signal clears only if, on NOVEMBER, long-the-top-quintile nets > 0 with t >= 2,
its train quintiles are monotone in the same direction, and the sign holds in at
least 4 of the 6 months. Nothing is wired into overwatch_timing unless it clears.
"""
from __future__ import annotations

import json
import math
import os
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
TAPE = os.path.join(HERE, "kraken_5m_long_183d.json")
OUT = os.path.join(HERE, "_orderflow_study.json")

COST = 0.080            # round trip, percent
NOV1 = 1767225600 + 0   # placeholder, replaced below with the real Nov 1 epoch
HORIZONS = {"h30": 6, "h60": 12}


def month_key(ts: float) -> str:
    import datetime as dt
    return dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m")


def load() -> dict:
    with open(TAPE, encoding="utf-8") as fh:
        d = json.load(fh)
    return d


def build(bars):
    """Feature + forward-return columns for one pair, all numpy arrays."""
    import numpy as np
    a = np.array(bars, dtype=float)
    ts, o, h, l, c, v = a.T
    n = len(a)
    rng = np.where(h > l, h - l, np.nan)
    clv = np.clip((c - l) / rng, 0.0, 1.0)          # 0 = closed at low, 1 = at high
    sgn = 2.0 * clv - 1.0                            # signed pressure
    body = np.abs(c - o)

    def roll_mean(x, w):
        out = np.full(n, np.nan)
        cs = np.cumsum(np.nan_to_num(x))
        for i in range(w, n):
            out[i] = (cs[i] - cs[i - w]) / w
        return out

    def roll_sum(x, w):
        out = np.full(n, np.nan)
        cs = np.cumsum(np.nan_to_num(x))
        for i in range(w, n):
            out[i] = cs[i] - cs[i - w]
        return out

    vol24 = roll_mean(v, 288)
    vol24_sd = np.full(n, np.nan)
    for i in range(288, n):
        vol24_sd[i] = np.std(v[i - 288:i])
    vwap24 = roll_sum(c * v, 288) / np.maximum(roll_sum(v, 288), 1e-12)
    range24 = roll_mean(rng, 288)

    sv = np.nan_to_num(sgn) * v
    ofi3 = roll_sum(sv, 3) / np.maximum(roll_sum(v, 3), 1e-12)
    ofi12 = roll_sum(sv, 12) / np.maximum(roll_sum(v, 12), 1e-12)

    upv = roll_sum(np.where(c > o, v, 0.0), 12)
    dnv = roll_sum(np.where(c < o, v, 0.0), 12)
    vdelta12 = (upv - dnv) / np.maximum(upv + dnv, 1e-12)

    upv6 = roll_sum(np.where(c > o, v, 0.0), 6)
    dnv6 = roll_sum(np.where(c < o, v, 0.0), 6)
    # no up-volume at all means the ratio is undefined, not infinite
    dipvol = np.where(upv6 > 0, dnv6 / np.maximum(upv6, 1e-12), np.nan)

    lower = np.minimum(o, c) - l
    upper = h - np.maximum(o, c)
    wick3 = roll_sum(lower - upper, 3) / np.maximum(roll_sum(rng, 3), 1e-12)

    # A z-score is only defined where volume actually varies: on a constant-volume
    # stretch the sd is ~0 and z explodes to 1e12, which owns the top quintile and
    # hides the real high-volume bars. Undefined -> NaN -> excluded, never invented.
    sd_ok = vol24_sd > 0.02 * np.nan_to_num(vol24)
    volz = np.where(sd_ok, (v - vol24) / np.maximum(vol24_sd, 1e-12), np.nan)
    absorb = volz * (1.0 - body / np.maximum(rng, 1e-12))
    vwapdev = (c - vwap24) / np.maximum(vwap24, 1e-12) * 100.0
    rangex = rng / np.maximum(range24, 1e-12)

    feats = {
        "ofi3": ofi3, "ofi12": ofi12, "vdelta12": vdelta12, "volz": volz,
        "absorb": absorb, "dipvol": dipvol, "wick3": wick3, "vwapdev": vwapdev,
        "rangex": rangex,
    }
    # trailing returns
    for k in (6, 12):
        r = np.full(n, np.nan)
        r[k:] = (c[k:] / c[:-k] - 1.0) * 100.0
        feats[f"ret{k}"] = r

    fwd = {}
    for name, k in HORIZONS.items():
        f = np.full(n, np.nan)
        f[:-k] = (c[k:] / c[:-k] - 1.0) * 100.0
        fwd[name] = f
    return ts, feats, fwd


def tstat(xs):
    if len(xs) < 2:
        return 0.0
    sd = st.pstdev(xs)
    return (st.fmean(xs) / (sd / math.sqrt(len(xs)))) if sd > 0 else 0.0


def main():
    d = load()
    pairs = d["pairs"]
    # November 1 2026 00:00 UTC
    import datetime as dt
    nov1 = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc).timestamp()

    # train sample pool
    pool = {name: [] for name in ("ofi3", "ofi12", "vdelta12", "volz", "absorb",
                                  "dipvol", "wick3", "vwapdev", "rangex",
                                  "ret6", "ret12")}
    rows_tr = []   # (feature_name, value, fwd30, fwd60, month)
    rows_ho = []
    for sym, bars in pairs.items():
        ts, feats, fwd = build(bars)
        n = len(ts)
        for i in range(n):
            if ts[i] < d["start"] + 288 * 300:
                continue
            v = feats
            ok = all(not math.isnan(v[name][i]) for name in pool) and \
                 not math.isnan(fwd["h30"][i]) and not math.isnan(fwd["h60"][i])
            if not ok:
                continue
            mk = month_key(ts[i])
            rec = (v, i, fwd["h30"][i], fwd["h60"][i], mk)
            (rows_ho if ts[i] >= nov1 else rows_tr).append(rec)

    print(f"train blocks {len(rows_tr)}  holdout blocks {len(rows_ho)}")

    results = {}
    for name in pool:
        raw = sorted(r[0][name][r[1]] for r in rows_tr)
        lo_c, hi_c = raw[int(len(raw) * 0.01)], raw[min(len(raw) - 1,
                                                      int(len(raw) * 0.99))]
        # winsorise to the train 1st/99th pct so heavy tails cannot swallow a
        # bucket: volz and absorb otherwise put 99.99% of bars in one bin and the
        # high-volume family would go untested.
        tr_vals = sorted(min(hi_c, max(lo_c, x)) for x in raw)
        q = [tr_vals[int(len(tr_vals) * p)] for p in (0.2, 0.4, 0.6, 0.8)]

        def clip(x):
            return min(hi_c, max(lo_c, x))

        def bucket(val):
            val = clip(val)
            for j, edge in enumerate(q):
                if val < edge:
                    return j
            return 4

        per_h = {}
        for hname in HORIZONS:
            hcol = 2 if hname == "h30" else 3
            tr_q = [[] for _ in range(5)]
            for r in rows_tr:
                tr_q[bucket(r[0][name][r[1]])].append(r[hcol])
            ho_means = []
            ho_tops = []
            for r in rows_ho:
                if bucket(r[0][name][r[1]]) == 4:
                    ho_tops.append(r[hcol] - COST)
            for j in range(5):
                ho_means.append([r[hcol] - COST for r in rows_ho
                                 if bucket(r[0][name][r[1]]) == j])
            tr_net = [(st.fmean(x) - COST) if x else float("nan") for x in tr_q]
            ho_net = [st.fmean(x) if x else float("nan") for x in ho_means]
            per_h[hname] = {
                "train_q_net_pct": [round(x, 4) if not math.isnan(x) else None
                                     for x in tr_net],
                "train_spread_q5_q1": (round(tr_net[4] - tr_net[0], 4)
                                       if not (math.isnan(tr_net[4])
                                               or math.isnan(tr_net[0])) else None),
                "nov_q_net_pct": [round(x, 4) if not math.isnan(x) else None
                                  for x in ho_net],
                "nov_n": [len(x) for x in ho_means],
                "nov_top_mean_net_pct": round(st.fmean(ho_tops), 4) if ho_tops else None,
                "nov_top_t": round(tstat(ho_tops), 2),
                "nov_top_win_pct": round(100.0 * sum(1 for x in ho_tops if x > 0)
                                         / len(ho_tops), 1) if ho_tops else None,
            }
        results[name] = per_h

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1)

    # ── report ───────────────────────────────────────────────────────────────
    for hname in HORIZONS:
        print(f"\n===== horizon {hname} (net of {COST}%) =====")
        print(f"{'signal':9s} {'trainQ1..Q5':28s} {'sprd':>7s}  "
              f"{'NovQ1..Q5':28s} {'NovTop':>7s} {'t':>6s} {'win%':>5s} {'n':>6s}")
        rank = []
        for name in pool:
            r = results[name][hname]
            tr = " ".join("  n/a " if x is None else f"{x:+6.2f}"
                          for x in r["train_q_net_pct"])
            ho = " ".join("  n/a " if x is None else f"{x:+6.2f}"
                          for x in r["nov_q_net_pct"])
            tm = r["nov_top_mean_net_pct"]
            sp = r["train_spread_q5_q1"]
            print(f"{name:9s} {tr} {0 if sp is None else sp:+7.2f}  {ho} "
                  f"{0 if tm is None else tm:+7.2f} {r['nov_top_t']:+6.2f} "
                  f"{0 if r['nov_top_win_pct'] is None else r['nov_top_win_pct']:5.1f} "
                  f"{r['nov_n'][4]:6d}")
            rank.append((r["nov_top_mean_net_pct"] or -99, r["nov_top_t"], name))

        rank.sort(reverse=True)
        winners = [x for x in rank if x[0] > 0 and x[1] >= 2.0]
        print("\nclearing (Nov mean net > 0 and t >= 2):",
              [(n, round(m, 3), t) for m, t, n in winners] or "NONE")
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
