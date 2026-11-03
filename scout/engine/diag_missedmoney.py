# diag_missedmoney.py -- Sep 5 hindsight audit:
# every popup the gates buried today, entry at the suppression moment,
# what a 1-3h goal window would have actually paid (Kraken 5-min candles).
import json
import time
import requests

SUP = r"C:\Users\birds\Workspace\kraken_scout\popup_suppressions.jsonl"
OUT = r"C:\Users\birds\Workspace\kraken_scout\missedmoney.txt"

rows = []
with open(SUP, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("ts", "").startswith("2026-09-05"):
            rows.append(r)

out = []
out.append("today suppressions: %d" % len(rows))

gates = {}
for r in rows:
    gates[r["gate"]] = gates.get(r["gate"], 0) + 1
out.append("by gate: " + ", ".join("%s=%d" % (k, v) for k, v in sorted(gates.items(), key=lambda x: -x[1])))

# first suppression per pair (the moment the scanner first said NO)
first = {}
for r in rows:
    p = r["pair"]
    if p not in first:
        first[p] = r

out.append("unique buried pairs: %d" % len(first))
out.append("")

S = requests.Session()
S.headers.update({"User-Agent": "scout-diag"})


def fetch_ohlcv(kpair, since):
    url = "https://api.kraken.com/0/public/OHLC"
    r = S.get(url, params={"pair": kpair, "interval": 5, "since": since}, timeout=20)
    data = r.json().get("result", {})
    key = [k for k in data if k != "last"]
    if not key:
        return []
    return data[key[0]]


def parse_ts(ts):
    # 2026-09-05T00:34:56.353430-06:00
    import datetime
    return datetime.datetime.fromisoformat(ts).timestamp()


results = []
for pair in sorted(first.keys()):
    r = first[pair]
    t0 = parse_ts(r["ts"])
    try:
        candles = fetch_ohlcv(pair.replace("/", ""), int(t0) - 7200)
    except Exception as e:
        out.append("%-12s FETCH FAIL %s" % (pair, e))
        continue
    if not candles:
        out.append("%-12s NO CANDLES" % pair)
        continue
    # entry candle: last candle opening at/before t0
    base = [c for c in candles if c[0] <= t0]
    if not base:
        out.append("%-12s NO BASE CANDLE" % pair)
        continue
    c0 = base[-1]
    entry = float(c0[4])
    fwd = [c for c in candles if c0[0] <= c[0] <= c0[0] + 3 * 3600]
    if len(fwd) < 6:
        out.append("%-12s TOO FEW FWD CANDLES" % pair)
        continue
    highs = [float(c[2]) for c in fwd]
    lows = [float(c[3]) for c in fwd]
    closes = [float(c[4]) for c in fwd]

    def mfe_max(limit_s):
        hs = [float(c[2]) for c in fwd if c[0] <= c0[0] + limit_s]
        return max(hs) / entry - 1.0 if hs else 0.0

    mfe1 = mfe_max(3600)
    mfe3 = mfe3v = max(highs) / entry - 1.0
    worst = min(lows) / entry - 1.0
    end3 = closes[-1] / entry - 1.0

    # minutes to +1% (first candle whose high reaches it)
    t1 = None
    for c in fwd:
        if float(c[2]) >= entry * 1.01:
            t1 = (c[0] - c0[0]) / 60.0
            break

    brain = r.get("brain", r.get("quick", 0))
    results.append((pair, r["gate"], brain, mfe1, mfe3, worst, end3, t1, entry))
    time.sleep(0.4)

out.append("%-11s %-11s %5s | %+7s %+7s %+7s %+7s | %s" % ("pair", "gate", "brain", "mfe1h", "mfe3h", "worst", "end3h", "minTo+1%"))
for pair, gate, brain, mfe1, mfe3, worst, end3, t1, entry in results:
    out.append("%-11s %-11s %5.0f | %+6.2f%% %+6.2f%% %+6.2f%% %+6.2f%% | %s" % (
        pair, gate, brain, mfe1 * 100, mfe3 * 100, worst * 100, end3 * 100,
        ("%.0f" % t1) if t1 is not None else "-"))

out.append("")
win1 = [r for r in results if r[4] >= 0.01]
win2 = [r for r in results if r[4] >= 0.02]
out.append("buried signals whose 3h window reached +1%%: %d/%d" % (len(win1), len(results)))
out.append("buried signals whose 3h window reached +2%%: %d/%d" % (len(win2), len(results)))
out.append("median 3h MFE: %+.2f%%" % (100 * sorted(r[4] for r in results)[len(results) // 2] if results else 0))

with open(OUT, "w", encoding="ascii", errors="replace") as f:
    f.write("\n".join(str(x) for x in out))
print("WROTE %d lines -> %s" % (len(out), OUT))
