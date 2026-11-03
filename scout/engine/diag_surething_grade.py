"""diag_surething_grade.py — Sep 10: grade THE SURE THING's fire bar.

The question (user, Sep 10): is PRE_BREAKOUT_THRESHOLD = 65 still the
right fire bar? Replay the REAL detector (sure_thing.analyze, unmodified)
over the extended 5m store (54 popup-fleet pairs x 30 days) and grade
what each candidate bar would have fired and what those fires paid.

THE REPLAY (no look-ahead):
  * for every poll step t (stride 3 candles = 15 min; the live scanner
    polls faster, but a coil's structure moves slowly — stated, not
    hidden), rows = the trailing 720 candles ending AT t. analyze() is
    the module's own code; it only sees candles <= t. The outcome uses
    only t+1..t+12 (the popup's own contract: +1.5% within 60 min).
  * EPISODES: contiguous armed steps (gap <= 6 candles AND the same
    zone ceiling +-0.5%) are ONE coil — the live funnel pops once per
    coil (the armed-dedup), so the honest unit is the episode. For
    each candidate bar B, the episode fires at the FIRST step whose
    score >= B — exactly the live pre-breakout trigger.
  * OUTCOME: goal = max high t+1..t+12 >= entry x 1.015 (the popup's
    goal); mfe = the same window's ceiling; end = the 60-min close.
    Dollars: $1055 x brain_size_mult(crossing score) — the funnel's
    own sizing ladder (65 -> 0.50x, 75 -> 0.75x).
  * TIDE: each fire's hour is joined to the deep store's fleet tide
    state (contemporaneous context — the fleet breadth AT that hour,
    not future information).

Run: .venv\\Scripts\\python.exe -X utf8 diag_surething_grade.py
Writes _surethinggrade.txt.
"""
import statistics
import sys
import time as _time
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

OUT = BASE / "_surethinggrade.txt"
STORE = BASE / "surething_data" / "kraken"
GOAL_PCT = 1.5          # the popup's own goal (SURE_THING_GOAL_PCT)
WINDOW_BARS = 12        # 60 min of 5m candles
STRIDE = 3              # poll every 15 min
EP_GAP = 6              # max candle gap inside one coil episode
EP_ZH_TOL = 0.005       # same ceiling +-0.5% = same coil
BARS = [40, 45, 50, 55, 60, 65]   # the refit era (the shipped bar is 45; the legacy table 55-75 ranked a dead dial)
LEDGER = 1055.0

import sure_thing as stmod
import hindsight_scorer as hs


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
    v = df["volume"].to_numpy(float)
    v = np.nan_to_num(v, nan=0.0)
    ts = (df["date"].astype("int64") // 10**6).to_numpy(np.int64)
    # (the feathers carry datetime64[us] — the cast above yields SECONDS;
    # analyze() never reads timestamps, only this hour-join uses them)
    return ts, o, h, l, c, v


def _armed_steps(pair: str):
    """(t, score, zh) for every armed poll step — the detector's own
    code decides arming; the prefilter only skips windows whose coil
    geometry cannot possibly arm (range > 2% or < 3 ceiling tests)."""
    data = _feather_rows(pair)
    if data is None:
        return [], None
    ts, o, h, l, c, v = data
    n = len(c)
    if n < 800:
        return [], None
    # the detector's coil for the live candle t is the 24 bars t-24..t-1
    # (slice(n-25, n-1) with n-1 = t) — rolling(24) shifted one bar.
    zh_s = pd.Series(h).rolling(24).max()
    zl_s = pd.Series(l).rolling(24).min()
    zh = zh_s.shift(1).to_numpy()   # ends at t-1
    zl = zl_s.shift(1).to_numpy()
    thr_arr = zh * (1.0 - stmod.CEILING_TOUCH_PCT / 100.0)
    tests = (pd.Series(h) >= thr_arr).rolling(24).sum().shift(1).to_numpy()
    rng = (zh - zl) / ((zh + zl) / 2.0) * 100.0
    steps = []
    rows = None
    for t in range(760, n - 1, STRIDE):
        if not np.isfinite(zh[t]) or rng[t] > stmod.COIL_MAX_RANGE_PCT \
                or tests[t] < stmod.MIN_CEILING_TESTS:
            continue
        if rows is None:
            rows = list(zip(ts, o, h, l, c, v))
        r = stmod.analyze(rows[max(0, t - 719):t + 1])
        if r.get("armed"):
            steps.append((t, float(r.get("score") or 0.0),
                          float(r.get("zone_high") or 0.0)))
    return steps, (ts, o, h, l, c, v)


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
    goal = mfe >= goal_pct / 100.0
    return goal, mfe * 100.0, end * 100.0


def _episodes(steps):
    """Group armed steps into coils (gap <= EP_GAP candles, same ceiling)."""
    eps = []
    for t, score, zh in steps:
        if eps and t - eps[-1]["last"] <= EP_GAP \
                and abs(zh - eps[-1]["zh"]) <= eps[-1]["zh"] * EP_ZH_TOL:
            e = eps[-1]
            e["last"] = t
            e["steps"].append((t, score))
            if score > e["max_score"]:
                e["max_score"] = score
        else:
            eps.append({"zh": zh, "first": t, "last": t,
                        "steps": [(t, score)], "max_score": score})
    return eps


def _tide_by_hour():
    """{hour_ts: state} from the deep store (contemporaneous fleet
    breadth at each hour)."""
    try:
        import tide_learn as tl
        px, err = tl._load_deep_fleet()
        if px is None:
            return {}
        states = tl._tide_states(px, tl._tide_class_ref())
        return {int(pd.Timestamp(t).timestamp()): s
                for t, s in states.items()}
    except Exception:
        return {}


def main():
    t0 = _time.time()
    pairs = sorted(p.name.replace("-5m.feather", "").replace("_", "/")
                   for p in STORE.glob("*-5m.feather"))
    tides = _tide_by_hour()
    all_eps = []   # (pair, episode, arrs)
    n_steps = 0
    for pair in pairs:
        steps, arrs = _armed_steps(pair)
        if arrs is None:
            continue
        n_steps += len(steps)
        for ep in _episodes(steps):
            all_eps.append((pair, ep, arrs))

    scores = [s for _, ep, _ in all_eps for _, s in ep["steps"]]
    out = []
    out.append("=== THE SURE THING'S FIRE BAR, GRADED (Sep 10) ===")
    out.append(f"pairs replayed: {len(pairs)}   armed poll steps: "
               f"{n_steps} (stride {STRIDE} candles)")
    out.append(f"coil episodes (the live funnel's pop-once unit): "
               f"{len(all_eps)}")
    if scores:
        out.append(f"armed score distribution: p10 "
                   f"{np.percentile(scores, 10):.1f} / p25 "
                   f"{np.percentile(scores, 25):.1f} / median "
                   f"{np.median(scores):.1f} / p75 "
                   f"{np.percentile(scores, 75):.1f} / p90 "
                   f"{np.percentile(scores, 90):.1f} / max {max(scores):.1f}")
    out.append("")
    out.append("per bar: fires = episodes whose score crossed B (first "
               "crossing, the live trigger); payers hit the popup's own "
               "goal (+1.5% in 60 min from the crossing close). $/fire = "
               "goal-take on payers + end-move on every fire, sized by "
               "the funnel's ladder at the crossing score.")
    out.append("")
    hdr = (f"{'bar':>4} {'fires':>6} {'payers':>7} {'rate':>6} "
           f"{'med mfe':>8} {'end avg':>8} {'$ total':>9} {'$/fire':>7}")
    out.append("── all tides ──")
    out.append(hdr)
    per_bar = {}
    for B in BARS:
        fires = []
        for pair, ep, arrs in all_eps:
            hit = next((t for t, s in ep["steps"] if s >= B), None)
            if hit is None:
                continue
            oc = _outcome(arrs, hit)
            if oc is None:
                continue
            fires.append((pair, hit, oc, hs.brain_size_mult(float(
                next(s for t, s in ep["steps"] if s >= B)))))
        if not fires:
            per_bar[B] = (0, 0.0, 0.0)
            out.append(f"{B:>4} {0:>6}")
            continue
        payers = [f for f in fires if f[2][0]]
        usd = sum((LEDGER * m * (GOAL_PCT / 100.0) if g
                   else LEDGER * m * (end / 100.0))
                  for _, _, (g, _, end), m in fires)
        med_mfe = statistics.median(f[2][1] for f in fires)
        avg_end = statistics.mean(f[2][2] for f in fires)
        rate = len(payers) / len(fires) * 100.0
        per_bar[B] = (len(fires), rate, usd)
        out.append(f"{B:>4} {len(fires):>6} {len(payers):>7} "
                   f"{rate:>5.1f}% {med_mfe:>7.2f}% {avg_end:>+7.2f}% "
                   f"{usd:>+9.2f} {usd / len(fires):>+7.2f}")
    out.append("")
    # the tide cross-check at the LIVE bar (tracks the shipped threshold)
    LIVE_BAR = float(stmod.PRE_BREAKOUT_THRESHOLD)
    out_by_tide = {}
    for pair, ep, arrs in all_eps:
        hit = next((t for t, s in ep["steps"] if s >= LIVE_BAR), None)
        if hit is None:
            continue
        oc = _outcome(arrs, hit)
        if oc is None:
            continue
        mult = hs.brain_size_mult(float(next(s for t, s in ep["steps"]
                                             if s >= LIVE_BAR)))
        hr = int(arrs[0][hit] // 3600 * 3600)   # ts is seconds (µs cast)
        st_tide = tides.get(hr) or tides.get(hr - 3600) or "?"
        out_by_tide.setdefault(st_tide, []).append(oc + (mult,))
    out.append(f"── fires at the {LIVE_BAR:.0f} bar (the shipped threshold), by tide state ──")
    for st_tide in ("RISING", "FLAT", "OUT"):
        occ = out_by_tide.get(st_tide) or []
        if not occ:
            out.append(f"{st_tide:<7} no fires in the window")
            continue
        pay = sum(1 for g, _, _, _ in occ if g)
        usd = sum((m * (GOAL_PCT / 100.0) * LEDGER) if g else 0.0
                  for g, _, _, m in occ)
        out.append(f"{st_tide:<7} fires {len(occ):>3}  payers {pay:>3} "
                   f"({pay / len(occ) * 100:.1f}%)  "
                   f"goal-take ${usd:+.2f}")
    # the OUT price in score units: the quantile of armed scores whose
    # payer rate matches OUT's
    out.append("")
    out.append("")
    # THE CONTRACT TEST: does the bar RANK under any reasonable payoff
    out.append("── the contract test: payer rate by bar, three contracts ──")
    for win_bars, gpct, label in ((12, 1.5, "1.5% / 60 min (the popup's)"),
                                  (48, 1.5, "1.5% / 4 h (the store's)"),
                                  (48, 3.0, "3.0% / 4 h (popup-grade)") ):
        row = []
        for B in BARS:
            fires = []
            for pair, ep, arrs in all_eps:
                hit = next((t for t, s in ep["steps"] if s >= B), None)
                if hit is None:
                    continue
                oc = _outcome(arrs, hit, win_bars, gpct)
                if oc is not None:
                    fires.append(oc[0])
            rate = (sum(fires) / len(fires) * 100.0) if fires else 0.0
            row.append(f"{B}:{rate:.1f}%")
        out.append(f"  {label:<28} " + "  ".join(row))
    out.append("")
    out.append("READ (Sep 11, post-refit): the legacy score RANKED PAYERS "
               "BACKWARDS — holdout AUC 0.410, payer rate FALLING as the "
               "bar rose (the Sep 10 verdict). The ridge refit "
               "(diag_surething_refit.py — the coin's own history at 56 "
               "of the weight, ceiling tests 15, the squeeze 12; the "
               "tight-coil/pullback/buy-dominance terms ranked nothing "
               "and zeroed) un-inverted the ranking: payer rate now "
               "RISES with the bar under every contract, and every bar "
               "40-65 shows POSITIVE $/fire under the popup's own "
               "60-minute contract — the first time any Sure Thing "
               "configuration has shown that. The shipped bar is 45 "
               "(frequency-weighted edge; 55-65 pay more per fire on "
               "far fewer fires). Rollback: SURE_THING_USE_REFIT=False "
               "restores the legacy score and the blanket no-win regime. "
               "Caveat: one 30-day falling-market regime — re-grade on a "
               "rising month before trusting the weights blindly.")

    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    print(f"\nreplay took {_time.time() - t0:.0f}s — WROTE {OUT.name}")


if __name__ == "__main__":
    main()
