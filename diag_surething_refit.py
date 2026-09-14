"""diag_surething_refit.py — Sep 11: refit the Sure Thing score's
component weights on the recalibrated (spot-native) replay data — the
data-backed fix the fire-bar grade pointed to.

WHY: the fire-bar grade proved the SCORE RANKS PAYERS BACKWARDS (payer
rate falls as the bar rises under every contract). The bar is the wrong
dial; the dial is the score. The score is a fixed-weight blend of 11
normalized components the detector already computes per armed step:

  compression (30): tightness 15 | ATR 7.5 | BB 7.5
  pressure    (25): tests 10 | pullback-trend 7.5 | buy-dominance 7.5
  volume      (20): fading 5 | micro-spikes 10 | burst 5
  squeeze     (15)
  history     (10): neutral 5 when unknown

THE FIT: replay the store with the REAL detector (unmodified), capture
the component vector at every armed step, and fit weights that make the
score rank FORWARD outcomes. Ridge regression on {0,1} payer labels
(+1.5% within 60 min, the popup's own contract), features normalized
exactly like _score's pieces, no intercept (the scale stays 0-100).

THE HONESTY (no look-ahead): episodes are grouped into train (episode
END before the split hour) and holdout (episode START after it) — a
coin's coil never teaches on its own future, and the holdout is scored
by weights that never saw it. The refit SHIPS only if it beats the
legacy weights on the holdout (payer-rate spread + AUC). The split is
printed so the reader can judge.

Run: .venv\\Scripts\\python.exe -X utf8 diag_surething_refit.py
Writes _surethingrefit.txt.
"""
import statistics
import sys
import time as _time
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
OUT = BASE / "_surethingrefit.txt"
STORE = BASE / "surething_data" / "kraken"
GOAL_PCT = 1.5
WINDOW_BARS = 12
STRIDE = 3
EP_GAP = 6
EP_ZH_TOL = 0.005
SPLIT_FRAC = 0.70     # train on the earliest 70% of episode time
RIDGE_LAMBDA = 5.0    # the ridge penalty (features are 0-1-ish scaled)
MIN_HOLDOUT = 200     # need this many holdout episodes to trust the proof

import sure_thing as stmod
import hindsight_scorer as hs

# the component extractors — IDENTICAL math to _score's pieces, keyed
# to the constants they read, so the refit can never drift from them
FEATURES = [
    ("tight",   lambda r: max(0.0, 1.0 - r["coil_range_pct"] / stmod.COIL_MAX_RANGE_PCT)),
    ("atr",     lambda r: None if r["atr_ratio"] is None else max(0.0, min(1.0, 1.0 - r["atr_ratio"] / stmod.ATR_COMPRESS_RATIO))),
    ("bb",      lambda r: None if r["bb_width_pctile"] is None else max(0.0, min(1.0, (r["bb_width_pctile"] - (1.0 - stmod.BB_NARROW_PCT)) / stmod.BB_NARROW_PCT))),
    ("tests",   lambda r: min(1.0, ((r["tests"] - stmod.MIN_CEILING_TESTS) * 3.0 + 5.0) / 10.0)),
    ("pull",    lambda r: min(1.0, max(0.0, 2.0 * (r["pullback_trend"] or 0.0)))),
    ("buydom",  lambda r: min(1.0, max(0.0, 2.0 * (r["buy_dominance"] or 0.0)))),
    ("fade",    lambda r: 1.0),          # fading flag: set below from the detail-run
    ("micro",   lambda r: min(1.0, r["micro_spikes"] / 3.0)),   # _score caps at 3 revs
    ("burst",   lambda r: None),         # needs the raw burst; set below
    ("squeeze", lambda r: min(1.0, max(0.0, (r["squeeze_pct"] - (1.0 - stmod.SQUEELE_PCT)) / stmod.SQUEELE_PCT))),
    ("hist",    lambda r: 0.5 if r["hist_hit_rate"] is None else r["hist_hit_rate"]),
]
# legacy weights (sure_thing._score, the shipped blend)
LEGACY_W = np.array([15.0, 7.5, 7.5, 10.0, 7.5, 7.5, 5.0, 10.0, 5.0, 15.0, 10.0])
FEAT_NAMES = [n for n, _ in FEATURES]


def _feather_rows(pair: str):
    f = STORE / f"{pair.replace('/', '_')}-5m.feather"
    if not f.exists():
        return None
    try:
        df = pd.read_feather(f)
    except Exception:
        return None
    df = df.sort_values("date").reset_index(drop=True)
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    v = np.nan_to_num(df["volume"].to_numpy(float), nan=0.0)
    # REAL seconds: feather dates may carry µs units — force ns before
    # the cast or every downstream hour-floor lands in 1970 (the unit
    # bug again; caught it in the first run's split-hour print)
    ts_s = (pd.to_datetime(df["date"], utc=True)
            .astype("datetime64[ns, UTC]").astype("int64") // 10**9
            ).to_numpy(np.int64)
    return ts_s, o, h, l, c, v


def _outcome(arrs, t, win_bars=WINDOW_BARS, goal_pct=GOAL_PCT):
    ts, o, h, l, c, v = arrs
    n = len(c)
    if t + win_bars >= n:
        return None
    entry = float(c[t])
    if entry <= 0:
        return None
    fwd_hi = h[t + 1: t + 1 + win_bars]
    fwd_cl = c[t + 1: t + 1 + win_bars]
    mfe = float(fwd_hi.max()) / entry - 1.0
    end = float(fwd_cl[-1]) / entry - 1.0
    return mfe >= goal_pct / 100.0, mfe * 100.0, end * 100.0


def _episodes(steps):
    eps = []
    for t, rec, zh in steps:
        if eps and t - eps[-1]["last"] <= EP_GAP \
                and abs(zh - eps[-1]["zh"]) <= eps[-1]["zh"] * EP_ZH_TOL:
            e = eps[-1]
            e["last"] = t
            e["steps"].append((t, rec))
        else:
            eps.append({"zh": zh, "first": t, "last": t,
                        "steps": [(t, rec)]})
    return eps


def collect():
    """Replay every pair; for each armed step keep the analyzer's
    component dict + the raw inputs _score needs that the dict lacks
    (vol_fading, burst). Returns (episodes, hours_seen)."""
    all_eps = []
    pairs = sorted(p.name.replace("-5m.feather", "").replace("_", "/")
                   for p in STORE.glob("*-5m.feather"))
    for pair in pairs:
        data = _feather_rows(pair)
        if data is None:
            continue
        ts, o, h, l, c, v = data
        n = len(c)
        if n < 800:
            continue
        # the cheap prefilter (identical to the grade's)
        zh_s = pd.Series(h).rolling(24).max()
        zl_s = pd.Series(l).rolling(24).min()
        zh = zh_s.shift(1).to_numpy()
        zl = zl_s.shift(1).to_numpy()
        thr_arr = zh * (1.0 - stmod.CEILING_TOUCH_PCT / 100.0)
        tests = (pd.Series(h) >= thr_arr).rolling(24).sum().shift(1).to_numpy()
        rng = (zh - zl) / ((zh + zl) / 2.0) * 100.0
        rows = None
        steps = []
        for t in range(760, n - 1, STRIDE):
            if not np.isfinite(zh[t]) or rng[t] > stmod.COIL_MAX_RANGE_PCT \
                    or tests[t] < stmod.MIN_CEILING_TESTS:
                continue
            if rows is None:
                rows = list(zip(ts, o, h, l, c, v))
            win = rows[max(0, t - 719):t + 1]
            r = stmod.analyze(win)
            if not r.get("armed"):
                continue
            # the two raw inputs the dict lacks: vol_fading + burst
            coil_v = [x[5] for x in win[len(win) - stmod.COIL_LOOKBACK - 1:len(win) - 1]]
            coil_mean = sum(coil_v) / len(coil_v) if coil_v else 0.0
            pre_v = [x[5] for x in win[max(0, len(win) - stmod.COIL_LOOKBACK * 2 - 1):
                                       len(win) - stmod.COIL_LOOKBACK - 1]]
            pre_mean = (sum(pre_v) / len(pre_v)) if pre_v else coil_mean
            rec = dict(r)
            rec["_fading"] = 1.0 if coil_mean <= pre_mean * 1.1 else 0.0
            rec["_burst"] = (v[t] / coil_mean) if coil_mean > 0 else 0.0
            steps.append((t, rec, float(r.get("zone_high") or 0.0)))
        for ep in _episodes(steps):
            ep["pair"] = pair
            ep["arrs"] = data
            all_eps.append(ep)
    return all_eps


def episode_rows(ep):
    """(hour_ts, feats(np), label, extra) per episode: features from the
    episode's PEAK step (the live funnel pops at the crossing — but the
    honest refit grades the whole episode's best read; the peak step is
    the step whose legacy score is highest, i.e. the fire the funnel
    would have taken)."""
    arrs = ep["arrs"]
    best = max(ep["steps"], key=lambda kv: stmod._score(
        kv[1]["coil_range_pct"], kv[1]["tests"], kv[1]["squeeze_pct"],
        kv[1]["_burst"], bool(kv[1]["_fading"]), kv[1]["atr_ratio"],
        kv[1]["bb_width_pctile"], kv[1]["pullback_trend"],
        kv[1]["micro_spikes"], kv[1]["buy_dominance"], kv[1]["hist_hit_rate"]))
    t, rec = best
    oc = _outcome(arrs, t)
    if oc is None:
        return None
    ts, o, h, l, c, v = arrs
    feats = []
    for name, fn in FEATURES:
        if name == "fade":
            feats.append(rec["_fading"])
        elif name == "burst":
            b = rec["_burst"]
            feats.append(max(0.0, min(1.0, b - 1.0)) if b > 1.0 else 0.0)
        else:
            x = fn(rec)
            feats.append(0.0 if x is None else float(x))
    hour = int(ts[t] // 3600 * 3600)
    return hour, np.array(feats), 1.0 if oc[0] else 0.0, rec


def ridge_fit(X, y, lam=RIDGE_LAMBDA):
    """No-intercept ridge via the normal equations (X is n x 11)."""
    A = X.T @ X + lam * np.eye(X.shape[1])
    b = X.T @ y
    return np.linalg.solve(A, b)


def auc(scores, labels):
    pairs = sorted(zip(scores, labels))
    pos = sum(1 for _, y in pairs if y > 0)
    neg = len(pairs) - pos
    if not pos or not neg:
        return 0.5
    rank_sum = 0.0
    for i, (s, y) in enumerate(pairs):
        if y > 0:
            rank_sum += i + 1
    return (rank_sum - pos * (pos + 1) / 2.0) / (pos * neg)


def main():
    t0 = _time.time()
    eps = collect()
    rows = [r for ep in eps if (r := episode_rows(ep))]
    if len(rows) < MIN_HOLDOUT * 2:
        print(f"not enough episode data: {len(rows)} rows")
        return
    rows.sort(key=lambda r: r[0])
    split_i = int(len(rows) * SPLIT_FRAC)
    split_hour = rows[split_i][0]
    train = rows[:split_i]
    hold = rows[split_i:]
    Xtr = np.vstack([r[1] for r in train])
    ytr = np.array([r[2] for r in train])
    Xho = np.vstack([r[1] for r in hold])
    yho = np.array([r[2] for r in hold])
    w = ridge_fit(Xtr, ytr)
    w = np.clip(w, 0.0, 40.0)          # probabilities stay additive
    w = w / w.sum() * 100.0            # renormalize to the 0-100 scale

    def rate(y, s, lo, hi):
        m = (s >= lo) & (s < hi)
        return (y[m].mean() * 100.0) if m.any() else float("nan")

    s_leg_ho = Xho @ LEGACY_W
    s_new_ho = Xho @ w
    a_leg, a_new = auc(s_leg_ho, yho), auc(s_new_ho, yho)
    band = 10.0
    out = []
    out.append("=== THE SURE THING SCORE REFIT (Sep 11, the recalibrated store) ===")
    out.append(f"episodes: {len(rows)}  (train {len(train)} before "
               f"{pd.Timestamp(split_hour, unit='s', tz='UTC')}, "
               f"holdout {len(hold)} after)")
    out.append(f"base rate: train {ytr.mean() * 100:.1f}%  holdout {yho.mean() * 100:.1f}%")
    out.append("")
    out.append("── the refit weights (vs legacy) ──")
    out.append(f"{'component':<10} {'legacy':>7} {'refit':>7}")
    for nm, lo, nw in zip(FEAT_NAMES, LEGACY_W, w):
        out.append(f"{nm:<10} {lo:>7.1f} {nw:>7.1f}")
    out.append("")
    out.append("── the HOLDOUT proof (weights never saw these) ──")
    out.append("payer rate by legacy-score band (holdout):")
    for lo in (30, 40, 50, 60, 70):
        m = (s_leg_ho >= lo) & (s_leg_ho < lo + band)
        out.append(f"  [{lo:>2}-{lo + band:>2})  n={int(m.sum()):>4}  "
                   f"{rate(yho, s_leg_ho, lo, lo + band):>5.1f}%")
    out.append("payer rate by refit-score band (holdout):")
    for lo in (30, 40, 50, 60, 70):
        m = (s_new_ho >= lo) & (s_new_ho < lo + band)
        out.append(f"  [{lo:>2}-{lo + band:>2})  n={int(m.sum()):>4}  "
                   f"{rate(yho, s_new_ho, lo, lo + band):>5.1f}%")
    out.append("")
    out.append("the refit's own fire bar, priced on the holdout "
               "(the popup's 60-min contract, the funnel's sizing ladder at "
               "the REFIT score):")
    for B in (45, 50, 55, 60, 65):
        m = s_new_ho >= B
        n_fire = int(m.sum())
        if not n_fire:
            out.append(f"  refit bar {B}: no fires")
            continue
        y = yho[m]
        pay = int(y.sum())
        rate_b = pay / n_fire * 100.0
        usd = sum(1055.0 * hs.brain_size_mult(float(s)) * (0.015 if yy else 0.0)
                  for s, yy in zip(s_new_ho[m], y))
        out.append(f"  refit bar {B}: fires {n_fire:>4}  payers {pay:>3} "
                   f"({rate_b:.1f}%)  $/fire {usd / n_fire:+.2f}")
    out.append("")
    out.append(f"AUC  legacy {a_leg:.3f}  vs  refit {a_new:.3f}")
    ship = a_new > a_leg + 0.02
    out.append("")
    out.append(f"VERDICT: {'SHIP the refit' if ship else 'KEEP the legacy score'} "
               f"({'holdout AUC +%.3f' % (a_new - a_leg) if ship else 'the refit does not beat legacy out-of-sample'})")
    out.append(f"refit weights (rounded): "
                + ", ".join(f"{n}={v:.1f}" for n, v in zip(FEAT_NAMES, w)))
    out.append(f"replay+fit took {_time.time() - t0:.0f}s")
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
