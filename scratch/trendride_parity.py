"""trendride_parity.py — do the LIVE engine functions still reproduce the study?

The study (scratch/trendride_study.py) reported e3_band|S2.0|A0.4|G0.15 at
8,304 trades and 82.3% wins. The live engine (overwatch_timing.py) is supposed to be
the same strategy. This harness runs the live code — ot.entry_ok, ot.initial_stop,
ot.manage, ot.net_pct — over the same tape and compares, so a silent divergence cannot
hide behind a stale number.

Three arms, each isolating one layer:

  A  study code, study entries        the control: must reproduce 8,304 / 82.3%
  B  live manage, study entries       isolates the EXIT engine
  C  live entry_ok + live manage      what actually runs in timing_shadow

Live entry_ok is called exactly as timing_shadow calls it: on a 320-bar window ending
at the bar that just closed. The study used the whole series.
"""
from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import overwatch_timing as ot
from trendride_study import (COST, MAX_HOLD, NOV1, load, prep, score, simulate)

OUT = os.path.join(HERE, "_trendride_parity.json")
WINDOW = 320                 # the bars timing_shadow fetches
TARGET_TRADES, TARGET_WIN = 8304, 82.3


def live_trades(p, idx, cap=True):
    """Study's exit loop, live's rules. Used to replay study entries with live manage."""
    o, h, l, c = p["o"], p["h"], p["l"], p["c"]
    n = len(c)
    out = []
    for i in idx:
        j = i + 1
        if j >= n:
            continue
        entry = o[j]
        if entry <= 0:
            continue
        peak, stop = h[j], ot.initial_stop(entry)
        exit_px = None
        held = 0
        last = min(n, j + MAX_HOLD) if cap else n
        for k in range(j, last):
            held = k - j + 1
            peak, stop, exit_px, _reason = ot.manage(entry=entry, peak=peak, stop=stop,
                                                     bar_high=h[k], bar_low=l[k])
            if exit_px is not None:
                break
        if exit_px is None:
            kk = (min(n - 1, j + MAX_HOLD - 1) if cap else n - 1)
            exit_px, held = c[kk], kk - j + 1
        out.append((p["ts"][j], ot.net_pct(entry, exit_px), held))
    return out


def live_entries(p):
    """Bars where the REAL ot.entry_ok fires, called on a 320-bar window like live.

    Candidate bars are those touching either band definition (the live SMA band or the
    study's EMA-mid band); that is a superset, so no live entry can be missed, and
    entry_ok itself makes the final call on every one of them.
    """
    o, h, l, c = p["o"], p["h"], p["l"], p["c"]
    e20, e200, band_ema = p["ema20"], p["e200"], p["band_ema"]
    n = len(c)
    found, rejected = [], []
    for i in range(201, n - 2):
        # cheap superset filter: the bar must at least reach one of the two bands
        sma_band = ot.band_lower(c[max(0, i - 19):i + 1])
        if sma_band is None or l[i] > max(sma_band, band_ema[i]):
            continue
        w = max(0, i + 1 - WINDOW)
        ok, _why = ot.entry_ok(c[w:i + 1], h[w:i + 1], l[w:i + 1], o[w:i + 1])
        if ok:
            found.append(i)
        else:
            rejected.append(i)
    return found, rejected


def main():
    d = load()
    report = {"window": WINDOW, "target": {"trades": TARGET_TRADES, "win_pct": TARGET_WIN},
              "pairs": {}}
    arms = {"A_study_study": [], "B_live_exit": [], "C_live_both": [],
            "D_live_nocap": []}
    study_n = live_n = 0

    for sym, bars in d["pairs"].items():
        p = prep(bars)
        # The study's band is NOT the live band: it puts the EMA20 at the mid and
        # takes the sd around the SMA, while band_lower() uses the SMA as the mid.
        # Rebuild the study's version here so both are available for the filter.
        c, e20 = p["c"], p["e20"]
        band = [0.0] * len(c)
        for i in range(19, len(c)):
            win = c[i - 19:i + 1]
            m = sum(win) / 20.0
            band[i] = e20[i] - 2.0 * (sum((x - m) ** 2 for x in win) / 20.0) ** 0.5
        p["band_ema"] = band
        p["ema20"] = e20

        study_idx = p["entries"]["e3_band"]
        live_idx, rejected = live_entries(p)

        a = simulate(p, study_idx, ot.STOP_PCT, ot.ACTIVATE_PCT, ot.GIVE_PCT, False)
        b = live_trades(p, study_idx)
        cc = live_trades(p, live_idx)
        dd = live_trades(p, live_idx, cap=False)
        arms["A_study_study"] += a
        arms["B_live_exit"] += b
        arms["C_live_both"] += cc
        arms["D_live_nocap"] += dd
        study_n += len(study_idx)
        live_n += len(live_idx)

        report["pairs"][sym] = {"study_entries": len(study_idx),
                                "live_entries": len(live_idx),
                                "live_rejected_candidates": len(rejected)}

    print(f"target: {TARGET_TRADES} trades, {TARGET_WIN}% wins\n")
    print(f"entry counts      study {study_n}   live {live_n}   delta {live_n - study_n:+d}")
    out = {}
    for name, trades in arms.items():
        s = score(trades)
        nov = score([t for t in trades if t[0] >= NOV1])
        out[name] = {"all": s, "nov": nov}
        print(f"\n{name}")
        print(f"  trades {s['n']:6d}  win {s['win_pct']:5.1f}%  "
              f"net {s['mean_net_pct']:+.4f}%  hold {s['median_hold_h']}h")
        print(f"  Nov    n={nov['n']:5d}  win {nov.get('win_pct')}%  "
              f"net {nov.get('mean_net_pct')}%")
    report["arms"] = out
    report["entry_counts"] = {"study": study_n, "live": live_n}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)

    a = out["A_study_study"]["all"]
    cc_ = out["C_live_both"]["all"]
    print("\n=== verdict ===")
    print(f"  control A reproduces: trades {a['n']} (target {TARGET_TRADES}), "
          f"win {a['win_pct']}% (target {TARGET_WIN}%)")
    same = (cc_["n"] == TARGET_TRADES and abs(cc_["win_pct"] - TARGET_WIN) < 0.05)
    print(f"  live engine reproduces: {'YES' if same else 'NO'} — trades {cc_['n']}, "
          f"win {cc_['win_pct']}%")
    if not same:
        print(f"  shifted by {cc_['n'] - TARGET_TRADES:+d} trades / "
              f"{cc_['win_pct'] - TARGET_WIN:+.1f}pp of win rate")
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
