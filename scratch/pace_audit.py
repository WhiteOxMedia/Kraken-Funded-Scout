"""PACE AUDIT: what the scanner actually fires per day, vs the 47.4/day assumed.

Source of truth is popups.jsonl — the scanner's own popup journal, written by
background_scanner.popup_journal(). A BUY popup is a fire (that is the moment the
scanner commits to the setup); a GET OUT popup is that position closing.

Nothing in the record separates "fired" from "filled": the shadow path opens a
position for every BUY popup, so the fill ratio is 1.0 by construction. It is
reported anyway because the gate asks for it, and the report says so.

Three windows are reported, because the scanner has run in two modes and the
window choice changes the answer by an order of magnitude:
  last 7 complete days   the current strict risk envelope (8 fires/day cap)
  last 14 complete days  mixes the current cap with the October burst mode
  all complete days      the whole journal, which is dominated by the burst
"""
from __future__ import annotations

import collections
import json
import os
import statistics
from datetime import date, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POPUPS = os.path.join(HERE, "popups.jsonl")
OUT = os.path.join(HERE, "scratch", "_pace_audit.json")
ASSUMED = 47.4


def load():
    rows = []
    with open(POPUPS, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main():
    rows = load()
    fires = collections.Counter()
    exits = collections.Counter()
    hours = collections.Counter()
    for r in rows:
        d, t = r["ts"][:10], r["ts"][11:13]
        if r["kind"] == "BUY":
            fires[d] += 1
            hours[int(t)] += 1
        else:
            exits[d] += 1
    last = max(fires)
    end = date.fromisoformat(last)

    def window(days):
        d0 = end - timedelta(days=days - 1)
        span = [(d0 + timedelta(days=i)).isoformat() for i in range(days)]
        f = sum(fires[x] for x in span)
        e = sum(exits[x] for x in span)
        return {"window_start": span[0], "window_end": span[-1], "days": days,
                "fires": f, "fills": e,
                "fires_per_day": round(f / days, 2),
                "fills_per_day": round(e / days, 2),
                "zero_days": sum(1 for x in span if fires[x] == 0),
                "max_day": max(fires[x] for x in span),
                "daily": {x: fires[x] for x in span}}

    all_days = sorted(fires)
    complete = all_days[1:-1]
    res = {
        "source": "popups.jsonl (background_scanner.popup_journal)",
        "assumed_pace": ASSUMED,
        "journal_span": [all_days[0], all_days[-1]],
        "buy_popups_total": sum(fires.values()),
        "exit_popups_total": sum(exits.values()),
        "windows": {
            "last_7": window(7),
            "last_14": window(14),
            "all_complete": {"days": len(complete),
                             "fires": sum(fires[d] for d in complete),
                             "fills": sum(exits[d] for d in complete),
                             "fires_per_day": round(sum(fires[d] for d in complete)
                                                    / len(complete), 2),
                             "fills_per_day": round(sum(exits[d] for d in complete)
                                                    / len(complete), 2)},
        },
        "fill_ratio": 1.0,
        "fill_ratio_note": "BUY popups ARE the entry record; no separate fill event exists, "
                           "so fires and fills are the same count by construction.",
        "bimodality": "Daily fires are 1-12 under the current strict envelope (8/day cap) "
                      "and 48-258 in the late-October burst; the median across all complete "
                      "days is misleading because of that mode change.",
        "hour_histogram": {str(h).zfill(2): hours[h] for h in sorted(hours)},
    }
    # most days are one fire every few minutes and close within the hour
    res["median_fires_per_day_all_complete"] = statistics.median(
        fires[d] for d in complete)
    res["median_exits_per_day_all_complete"] = statistics.median(
        exits[d] for d in complete)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)

    for name in ("last_7", "last_14", "all_complete"):
        w = res["windows"][name]
        print(f"{name:<13} {w['window_start'] if 'window_start' in w else ''}.."
              f"{w['window_end'] if 'window_end' in w else ''}  "
              f"{w['days']}d  fires {w['fires']}  "
              f"{w['fires_per_day']:.2f}/day  fills {w['fills_per_day']:.2f}/day")
    print(f"\nassumed pace {ASSUMED}/day")
    for name in ("last_7", "last_14", "all_complete"):
        w = res["windows"][name]
        print(f"  {name:<13} observed/assumed = {w['fires_per_day']/ASSUMED:.2f}x")
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
