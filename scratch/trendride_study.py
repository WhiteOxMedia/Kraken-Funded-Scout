"""trendride_study.py — cut the loser fast, ride the winner until the trend breaks.

The 216-shape grid failed because it forced every trade to end at a fixed target:
winners and losers were both capped, so the only thing left to trade was noise minus
cost. This study implements the opposite rule, the one the user actually asked for:

    enter on a pullback in an uptrend  ->  sell into strength
    a losing trade gets out QUICK      ->  hard stop, small
    a winning trade is NOT capped      ->  trail it, stay in while the trend holds
    leave when the trend breaks        ->  close below the fast EMA, or the trail

That inverts the payoff: many small wins, few larger losses. Win rate and expectancy
stop fighting each other, which is what the fixed-target grid could never do.

Entry variants (long only; the contest cannot short), all pre-entry from bars <= i:
  e1_reclaim   above EMA200, close below EMA20 (a pullback), and the bar closes up
  e2_reject    above EMA200, EMA20 > EMA200, the bar pierces EMA20 and closes back up
  e3_band      above EMA200, low touches the lower Bollinger band, close back inside
  e4_3down     above EMA200, three down closes, then a close above the prior high

Exits, checked per bar in this order:
  1. hard stop  low <= entry*(1-S)               -> out at the stop
  2. trail      low <= peak*(1-k*ATR%)           -> out at the trail
  3. trend      close < EMA20 (the trend broke)  -> out at the close
  4. hold cap   bars_held >= MAX_HOLD            -> out at the close

Entry fills at the NEXT bar's open (no lookahead), cost 0.080% round trip.

Split: TRAIN May 14 - Oct 31, HOLDOUT Nov 1 - Nov 13. Layout is fitted on train,
but every config is reported on BOTH so nothing can be cherry-picked on November.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
TAPE = os.path.join(HERE, "kraken_5m_long_183d.json")
OUT = os.path.join(HERE, "_trendride_study.json")

COST = 0.080
NOV1 = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc).timestamp()
MAX_HOLD = 288            # 24h of 5m bars: "as long as it needs", but bounded


def load():
    with open(TAPE, encoding="utf-8") as fh:
        return json.load(fh)


def ema(x, span):
    a = 2.0 / (span + 1.0)
    out = [0.0] * len(x)
    if not x:
        return out
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1.0 - a) * out[i - 1]
    return out


def prep(bars):
    ts = [b[0] for b in bars]
    o = [float(b[1]) for b in bars]
    h = [float(b[2]) for b in bars]
    l = [float(b[3]) for b in bars]
    c = [float(b[4]) for b in bars]
    n = len(c)

    e20 = ema(c, 20)
    e200 = ema(c, 200)

    # ATR(14) as a percentage of price
    atr_pct = [0.0] * n
    trs = []
    for i in range(n):
        if i == 0:
            trs.append(h[i] - l[i])
        else:
            trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    run = 0.0
    for i in range(n):
        run += trs[i]
        if i >= 14:
            run -= trs[i - 14]
        w = min(i + 1, 14)
        atr_pct[i] = (run / w) / c[i] * 100.0 if c[i] > 0 else 0.0

    # lower Bollinger band (20, 2)
    std20 = [0.0] * n
    for i in range(19, n):
        win = c[i - 19:i + 1]
        m = sum(win) / 20.0
        std20[i] = math.sqrt(sum((x - m) ** 2 for x in win) / 20.0)
    boll = [e20[i] - 2.0 * std20[i] for i in range(n)]

    entries = {k: [] for k in ("e1_reclaim", "e2_reject", "e3_band", "e4_3down")}
    for i in range(201, n - 2):
        up_trend = c[i] > e200[i]
        if not up_trend:
            continue
        if c[i] > e20[i - 1] and c[i - 1] < e20[i - 1] and c[i] > c[i - 1]:
            entries["e1_reclaim"].append(i)
        if e20[i] > e200[i] and l[i] <= e20[i] and c[i] > e20[i] and c[i] > o[i]:
            entries["e2_reject"].append(i)
        if l[i] <= boll[i] and c[i] > boll[i] and c[i] > o[i]:
            entries["e3_band"].append(i)
        # three down closes, then a bar that takes out the prior bar's high
        if i >= 3 and c[i - 1] < c[i - 2] < c[i - 3] and c[i] > h[i - 1]:
            entries["e4_3down"].append(i)
    return dict(ts=ts, o=o, h=h, l=l, c=c, e20=e20, e200=e200,
                atr_pct=atr_pct, entries=entries)


def simulate(p, idx, stop_pct, activate_pct, give_pct, use_trend_break,
             max_hold=MAX_HOLD):
    """One config on one pair: fast stop, ratcheting trail, trend-break exit.

    The trail does NOT exist until the trade has earned `activate_pct`, because a
    trail measured from the entry bar's high is tighter than any sane stop and exits
    the first wobble (that is what made the first version win only 30%). Once armed,
    the stop ratchets to peak*(1-give_pct) and only ever moves up, so a winner keeps
    what it has made instead of giving it all back.
    """
    o, h, l, c = p["o"], p["h"], p["l"], p["c"]
    e20, atr = p["e20"], p["atr_pct"]
    n = len(c)
    out = []
    for i in idx:
        j = i + 1                       # fill at the next open
        if j >= n:
            continue
        entry = o[j]
        if entry <= 0:
            continue
        hard = entry * (1.0 - stop_pct / 100.0)
        stop = hard
        peak = entry
        armed = False
        exit_px = None
        held = 0
        for k in range(j, min(n, j + max_hold)):
            held = k - j + 1
            if l[k] <= stop:                        # 1. the stop/trail
                exit_px = stop
                break
            if h[k] > peak:
                peak = h[k]
            gain = (peak / entry - 1.0) * 100.0
            if gain >= activate_pct:
                armed = True
                if armed:
                    cand = peak * (1.0 - give_pct / 100.0)
                    if cand > stop:
                        stop = cand
            if use_trend_break and c[k] > entry and c[k] < e20[k]:
                exit_px = c[k]                         # 2. the trend broke
                break
        if exit_px is None:                            # 3. the hold cap
            kk = min(n - 1, j + max_hold - 1)
            exit_px = c[kk]
            held = kk - j + 1
        net = (exit_px / entry - 1.0) * 100.0 - COST
        out.append((p["ts"][j], net, held))
    return out


def score(trades):
    if not trades:
        return {"n": 0}
    nets = [t[1] for t in trades]
    wins = sum(1 for x in nets if x > 0)
    mean = st.fmean(nets)
    sd = st.pstdev(nets) if len(nets) > 1 else 0.0
    return {
        "n": len(nets),
        "win_pct": round(100.0 * wins / len(nets), 1),
        "mean_net_pct": round(mean, 4),
        "total_net_pct": round(sum(nets), 1),
        "median_hold_h": round(st.median([t[2] for t in trades]) / 12.0, 2),
        "t": round(mean / (sd / math.sqrt(len(nets))), 2) if sd > 0 else 0.0,
        "worst": round(min(nets), 2),
        "best": round(max(nets), 2),
    }


def main():
    d = load()
    grid = []
    for stop in (0.6, 1.0, 1.5):
        for act in (0.25, 0.5):
            for give in (0.35, 0.7):
                for tb in (True, False):
                    grid.append((stop, act, give, tb))

    results = {}
    for sym, bars in d["pairs"].items():
        p = prep(bars)
        for var, idx in p["entries"].items():
            for (stop, act, give, tb) in grid:
                key = f"{var}|S{stop}|A{act}|G{give}|tb{int(tb)}"
                tr = simulate(p, idx, stop, act, give, tb)
                b = results.setdefault(key, {"all": [], "train": [], "nov": []})
                b["all"] += tr
                for t in tr:
                    (b["nov"] if t[0] >= NOV1 else b["train"]).append(t)

    rows = []
    for key, b in results.items():
        rows.append({"config": key, "all": score(b["all"]),
                     "train": score(b["train"]), "nov": score(b["nov"])})
    rows.sort(key=lambda r: -r["nov"].get("win_pct", 0))

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)

    print(f"{'config':40s} {'n':>6s} {'win%':>5s} {'net%':>8s} {'tot':>7s} "
          f"{'hold':>5s} {'worst':>6s} {'best':>6s} | {'NovWin':>6s} {'NovNet':>7s} {'NovTot':>7s} {'t':>6s}")
    for r in rows:
        a, nv, tr = r["all"], r["nov"], r["train"]
        print(f"{r['config']:40s} {a['n']:6d} {a['win_pct']:5.1f} "
              f"{a['mean_net_pct']:+8.3f} {a['total_net_pct']:+7.1f} {a['median_hold_h']:5.1f} "
              f"{a['worst']:+6.2f} {a['best']:+6.2f} | {nv.get('win_pct',0):6.1f} "
              f"{nv.get('mean_net_pct',0):+7.3f} {nv.get('total_net_pct',0):+7.1f} "
              f"{nv.get('t',0):+6.2f}")

    print("\n=== configs with >=80% wins on the whole sample ===")
    any80 = [r for r in rows if r["all"].get("win_pct", 0) >= 80.0]
    for r in any80[:12]:
        print(f"  {r['config']:40s} win {r['all']['win_pct']:5.1f}%  "
              f"net {r['all']['mean_net_pct']:+.3f}%/trade  total {r['all']['total_net_pct']:+.1f}%  "
              f"Nov win {r['nov'].get('win_pct',0):5.1f}% net {r['nov'].get('mean_net_pct',0):+.3f}%")
    if not any80:
        print("  NONE reaches 80% wins")

    print("\n=== configs profitable on BOTH train and November ===")
    both = [r for r in rows if r["train"].get("mean_net_pct", -9) > 0
            and r["nov"].get("mean_net_pct", -9) > 0]
    for r in both[:12]:
        print(f"  {r['config']:40s} train {r['train']['mean_net_pct']:+.3f}%  "
              f"Nov {r['nov']['mean_net_pct']:+.3f}% (n={r['nov']['n']})")
    if not both:
        print("  NONE is positive on both")
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
