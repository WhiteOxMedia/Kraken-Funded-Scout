"""parity_attribution.py — which difference made the live engine trade 15,877 times?

trendride_parity.py showed: the study's own code reproduces 8,304 / 82.3%, and the
live exit engine on the study's entries reproduces it too — so `manage()` is not the
culprit. The live ENTRY path fires 15,877 times. Two things differ between the two
entry implementations, and this 2x2 attributes the shift instead of guessing:

    band mid      study: EMA20 as the mid, sd taken around the SMA
                  live : SMA20 as the mid (band_lower)
    trend EMA     study: EMA200 over the whole series from bar 0
                  live : EMA200 over the 320-bar window timing_shadow fetches

Each cell re-creates entry_ok's conditions with only those two levers varied, and the
(band=SMA, trend=window) cell must land on 15,877 for the diagnosis to be accepted.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from trendride_study import load, prep

WINDOW = 320
OUT = os.path.join(HERE, "_parity_attribution.json")


def ema_last(values, span):
    a = 2.0 / (span + 1.0)
    out = values[0]
    for x in values[1:]:
        out = a * x + (1.0 - a) * out
    return out


def counts(p, band_mode, trend_mode):
    o, h, l, c = p["o"], p["h"], p["l"], p["c"]
    e20, e200_study, band_study = p["_e20"], p["_e200"], p["_band_study"]
    n = len(c)
    got = 0
    for i in range(201, n - 2):
        if band_mode == "study":
            lower = band_study[i]
        else:
            win = c[i - 19:i + 1]
            mid = sum(win) / 20.0
            lower = mid - 2.0 * (sum((x - mid) ** 2 for x in win) / 20.0) ** 0.5
        if not (l[i] <= lower < c[i]) or c[i] <= o[i]:
            continue
        trend = e200_study[i] if trend_mode == "full" else ema_last(
            c[max(0, i + 1 - WINDOW):i + 1], 200)
        if c[i] <= trend:
            continue
        got += 1
    return got


def main():
    d = load()
    tot = {}
    for sym, bars in d["pairs"].items():
        p = prep(bars)
        c, e20 = p["c"], p["e20"]
        band = [0.0] * len(c)
        for i in range(19, len(c)):
            win = c[i - 19:i + 1]
            m = sum(win) / 20.0
            band[i] = e20[i] - 2.0 * (sum((x - m) ** 2 for x in win) / 20.0) ** 0.5
        p["_e20"], p["_e200"], p["_band_study"] = e20, p["e200"], band
        for bm in ("study", "sma"):
            for tm in ("full", "window"):
                key = f"band={bm}|trend={tm}"
                tot[key] = tot.get(key, 0) + counts(p, bm, tm)

    print("entries on the stored tape (study reported 8,304; live path fired 15,877)\n")
    for k in ("band=study|trend=full", "band=study|trend=window",
              "band=sma|trend=full", "band=sma|trend=window"):
        note = ""
        if k == "band=study|trend=full":
            note = "  <- the study's own definition"
        if k == "band=sma|trend=window":
            note = "  <- what the live engine does"
        print(f"  {k:26s} {tot[k]:7d}{note}")

    print("\nattribution")
    base = tot["band=study|trend=full"]
    print(f"  band alone  (study->sma, full trend): "
          f"{tot['band=sma|trend=full'] - base:+d}")
    print(f"  window alone (study band, 320 trend): "
          f"{tot['band=study|trend=window'] - base:+d}")
    print(f"  both: {tot['band=sma|trend=window'] - base:+d}")

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(tot, fh, indent=1)
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
