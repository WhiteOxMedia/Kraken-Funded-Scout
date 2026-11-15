"""The six measurable stops, replayed at the fast-stop cadence.

For each stopped-out close with 5m bars, the breach bar bounds the outcome: the
stop was crossed somewhere inside it, and the worst the price can have been is
the bar's low. Booking one cadence after the crossing therefore costs

    stop_dollars + (stop_px - bar_low) * min(1, cadence / 300) * units + fees

i.e. the intended stop plus the fraction of the bar's descent that elapses
before the check looks. At 300s that is the whole bar (a 5-minute check booking
at the low); at 20s it is one fifteenth. The 18-minute column is the ledger's
ACTUAL booking, not a model.

This is a bound, not a point estimate: a 5m bar cannot say where inside it the
crossing happened, so the mid-cadence figures inherit that uncertainty. The
20-second column is the most favourable end of the same bound.
"""
import bisect
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from exit_band_replay import load_ledger, load_store, ts, fnum, replay  # noqa: E402

CADENCES = ((20, "20s"), (60, "60s (1m)"), (300, "300s (5m)"))


def main():
    bars = load_store()
    rows = [r for r in load_ledger()
            if str(r.get("exit_reason") or "").startswith("STOPPED OUT")]
    head = "%-9s %8s" % ("pair", "stop$")
    for _s, label in CADENCES:
        head += " %11s" % label
    head += " %12s" % "18m (actual)"
    print(head)
    totals = {label: 0.0 for _s, label in CADENCES}
    total_actual = 0.0
    n = 0
    for r in rows:
        tick = r["pair"].split("/")[0]
        if tick not in bars:
            continue
        m = re.search(r"stop is -\$([\d.]+)", str(r.get("exit_reason")))
        if not m:
            continue
        entry = fnum(r["entry"]); size = fnum(r["size"]); cost = fnum(r.get("cost_rt"))
        stop_d = float(m.group(1))
        units = size / entry
        stop_px = entry - stop_d / units
        tss, closes, lows = bars[tick]
        t_exit = ts(r["sold_at"])
        i0 = bisect.bisect_left(tss, t_exit - int(fnum(r.get("held_min")) * 60))
        i1 = bisect.bisect_left(tss, t_exit)
        j = None
        for k in range(i0, min(i1 + 1, len(tss))):
            if lows[k] <= stop_px:
                j = k
                break
        if j is None:
            continue
        descent = (stop_px - lows[j]) * units      # $ available to lose after the crossing
        line = "%-9s %8.0f" % (r["pair"], stop_d)
        for secs, label in CADENCES:
            loss = stop_d + descent * min(1.0, secs / 300.0) + cost
            totals[label] += loss
            line += " %11.2f" % loss
        actual = -fnum(r["pnl_net"])
        total_actual += actual
        line += " %12.2f" % actual
        print(line)
        n += 1
    print("\nn=%d rows" % n)
    for _s, label in CADENCES:
        print("  %-10s total $%7.2f   saving vs the 18-minute booking $%7.2f"
              % (label, totals[label], total_actual - totals[label]))
    print("  %-10s total $%7.2f   (the ledger's actual booking)"
          % ("18m", total_actual))


if __name__ == "__main__":
    main()
