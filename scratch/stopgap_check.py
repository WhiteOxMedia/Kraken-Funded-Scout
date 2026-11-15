"""Scratch: stop-fill decomposition on the 5m store (corrected signs)."""
import json
import re
import datetime as dt
import statistics

d = json.load(open("scratch/kraken_5m_long_183d_aligned.json", encoding="utf-8"))
bars = d["pairs"]

tr = []
for line in open("contest_trades.jsonl", encoding="utf-8", errors="ignore"):
    line = line.strip()
    if line:
        try:
            tr.append(json.loads(line))
        except ValueError:
            pass
stopped = [r for r in tr if str(r.get("exit_reason") or "").startswith("STOPPED OUT")]


def stop_dollars(txt):
    m = re.search(r"stop is -\$([\d.]+)", txt)
    return float(m.group(1)) if m else None


print("%-11s %6s %8s %8s %8s %6s %7s %9s %8s" % (
    "pair", "stop$", "held", "entry", "breach", "lag", "stop%", "actual$", "over$"))
tot_over = 0.0
tot_act = 0.0
tot_planned = 0.0
tot_fee = 0.0
lags = []
measured = 0
for r in stopped:
    tick = r["pair"].split("/")[0]
    if tick not in bars:
        continue
    measured += 1
    entry = float(r["entry"])
    exitp = float(r["exit"])
    size = float(r["size"])
    units = size / entry
    stopd = stop_dollars(str(r["exit_reason"]))
    stop_px = entry - stopd / units
    t = dt.datetime.fromisoformat(str(r["sold_at"])).astimezone(dt.timezone.utc)
    held = float(r.get("held_min") or 0)
    fire = t - dt.timedelta(minutes=held)
    breach = None
    for b in bars[tick]:
        if b[0] < int(fire.timestamp()):
            continue
        if b[0] > int(t.timestamp()):
            break
        if b[3] <= stop_px:
            breach = b
            break
    lag = (t.timestamp() - breach[0]) / 60 if breach else None
    over = (stop_px - exitp) * units
    act = -float(r["pnl_net"])
    tot_over += over
    tot_act += act
    tot_planned += stopd
    tot_fee += float(r.get("cost_rt") or 0)
    if lag is not None:
        lags.append(lag)
    print("%-11s %6.0f %8.0f %8s %8s %6s %7.2f %9.2f %8.2f" % (
        r["pair"], stopd, held, fire.strftime("%H:%M"),
        dt.datetime.utcfromtimestamp(breach[0]).strftime("%H:%M") if breach else "-",
        ("%.0f" % lag) if lag is not None else "-",
        (stopd / size) * 100, act, over))

print("\nmeasured rows: %d of %d" % (measured, len(stopped)))
print("decomposition of the loss on those rows:")
print("  intended stop cost  $%.2f" % tot_planned)
print("  fees (cost_rt)      $%.2f" % tot_fee)
print("  price travel        $%.2f  = %.0f%% of the loss (gross: overshoot / net: actual)"
      % (tot_over, 100 * tot_over / tot_act))
print("  total               $%.2f  vs actual loss $%.2f  (residual $%+.2f)"
      % (tot_planned + tot_fee + tot_over, tot_act, tot_act - (tot_planned + tot_fee + tot_over)))
if lags:
    print("detection lag (min): min %.0f  median %.0f  max %.0f"
          % (min(lags), statistics.median(lags), max(lags)))
