"""Exit-side band test: defer exits that land inside +-X of entry, replay on 5m bars.

Pre-registered shape of this test (fixed before the grid was run):
  * a deferral is decided from information available at sold_at only:
    |exit/entry - 1| <= X at the exit timestamp;
  * a deferred row is booked at the CLOSE of the first 5m bar whose close leaves
    the band, else at the close of the last bar inside a fixed 24h cap;
  * optional floor: if a bar closes at or below -FLOOR from entry, book there
    (a stand-in for a stop the book would have had);
  * fees are unchanged: the ledger's cost_rt is charged once, as it was;
  * band X is chosen on the first half by time and measured on the second.
"""
import argparse
import bisect
import datetime as dt
import json
import math
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent      # .../kraken_scout/scratch
ROOT = HERE.parent                          # .../kraken_scout
STORE = HERE / "kraken_5m_long_183d_aligned.json"
LEDGER = ROOT / "contest_trades.jsonl"
CAP_BARS = 288          # 24h at 5m
BANDS = (0.10, 0.20, 0.30, 0.50, 0.75)


def fnum(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def ts(s):
    return int(dt.datetime.fromisoformat(str(s)).timestamp())


def load_ledger():
    out = []
    for line in LEDGER.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    out.sort(key=lambda r: ts(r["sold_at"]))
    return out


def load_store():
    doc = json.loads(STORE.read_text(encoding="utf-8"))
    out = {}
    for tick, rows in doc["pairs"].items():
        out[tick] = ([r[0] for r in rows], [r[4] for r in rows], [r[3] for r in rows])
    return out


def replay(row, bars, band, floor):
    """Return (net, bars_held, how) for a deferred row, or None if not deferrable."""
    tick = row["pair"].split("/")[0]
    if tick not in bars:
        return None
    tss, closes, lows = bars[tick]
    t = ts(row["sold_at"])
    i = bisect.bisect_left(tss, t)
    if i >= len(tss):
        return None
    entry = fnum(row["entry"])
    size = fnum(row["size"])
    cost = fnum(row.get("cost_rt"))
    if entry <= 0 or size <= 0:
        return None
    for j in range(i, min(i + CAP_BARS, len(tss))):
        move = closes[j] / entry - 1.0
        if floor and move <= -floor:
            return ((closes[j] - entry) / entry * size - cost, j - i + 1, "floor")
        if abs(move) > band / 100.0:
            return ((closes[j] - entry) / entry * size - cost, j - i + 1, "band")
    j = min(i + CAP_BARS, len(tss)) - 1
    return ((closes[j] - entry) / entry * size - cost, j - i + 1, "cap")


def run(rows, bars, band, floor):
    """Return list of (net, deferred, bars_held, how, actual) per row."""
    res = []
    for r in rows:
        actual = fnum(r.get("pnl_net"))
        m = abs(fnum(r["exit"]) / fnum(r["entry"]) - 1) * 100 if fnum(r["entry"]) else 999
        if m <= band:
            got = replay(r, bars, band, floor)
            if got is not None:
                res.append((got[0], True, got[1], got[2], actual))
                continue
        res.append((actual, False, 0, "kept", actual))
    return res


def summ(res):
    if not res:
        return dict(n=0, exp=0.0, win=0.0, deferred=0, delta=0.0)
    nets = [x[0] for x in res]
    acts = [x[4] for x in res]
    return dict(
        n=len(res),
        exp=statistics.mean(nets),
        win=100 * sum(1 for v in nets if v > 0) / len(nets),
        deferred=sum(1 for x in res if x[1]),
        delta=statistics.mean(nets) - statistics.mean(acts),
        base=statistics.mean(acts),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--floor", type=float, default=0.0,
                    help="book at this adverse move from entry (percent); 0 disables")
    ap.add_argument("--audit", action="store_true")
    a = ap.parse_args()

    ledger = load_ledger()
    bars = load_store()

    if a.audit:
        gaps = []
        for tick, (tss, _, _) in bars.items():
            d = [tss[i + 1] - tss[i] for i in range(len(tss) - 1)]
            gaps.extend(x for x in d if x != 300)
        print("bars not exactly 300s apart: %d of %d (%.2f%%)"
              % (len(gaps), sum(len(v[0]) - 1 for v in bars.values()),
                 100 * len(gaps) / sum(len(v[0]) - 1 for v in bars.values())))
        if gaps:
            print("  gap sizes: median %.0fs  p95 %.0fs  max %.0fs"
                  % (statistics.median(gaps), statistics.quantiles(gaps, n=20)[17], max(gaps)))
        mism = [abs((fnum(r["pnl_gross"]) - fnum(r["pnl_net"])) - fnum(r.get("cost_rt"))) for r in ledger
                if "pnl_gross" in r and "cost_rt" in r]
        print("|gross-net - cost_rt| over %d rows: median %.4f  p95 %.4f"
              % (len(mism), statistics.median(mism), statistics.quantiles(mism, n=20)[18]))

    usable = []
    for r in ledger:
        tick = r["pair"].split("/")[0]
        if tick not in bars:
            continue
        t = ts(r["sold_at"])
        tss = bars[tick][0]
        if tss[0] <= t <= tss[-1] and fnum(r["entry"]) and fnum(r["exit"]) and fnum(r["size"]):
            usable.append(r)
    h = len(usable) // 2
    train, test = usable[:h], usable[h:]
    print("replayable closes: %d   train %d   test %d   span %s -> %s"
          % (len(usable), len(train), len(test),
             dt.datetime.fromtimestamp(ts(usable[0]["sold_at"]), dt.timezone.utc).date(),
             dt.datetime.fromtimestamp(ts(usable[-1]["sold_at"]), dt.timezone.utc).date()))
    print("floor: %s   cap: %d bars (24h)   booking: bar close"
          % (("%.2f%% below entry" % a.floor) if a.floor else "none", CAP_BARS))

    base_tr = summ([(fnum(r["pnl_net"]), False, 0, "kept", fnum(r["pnl_net"])) for r in train])
    base_te = summ([(fnum(r["pnl_net"]), False, 0, "kept", fnum(r["pnl_net"])) for r in test])
    print("\nbaseline  train exp $%+.2f win %.0f%% (n=%d)   test exp $%+.2f win %.0f%% (n=%d)"
          % (base_tr["exp"], base_tr["win"], base_tr["n"], base_te["exp"], base_te["win"], base_te["n"]))

    print("\n%-8s %9s %10s %10s %9s %10s %9s" % (
        "band", "defer_tr", "train_exp", "train_d", "defer_te", "test_exp", "test_d"))
    best = None
    for b in BANDS:
        rtr = run(train, bars, b, a.floor)
        rte = run(test, bars, b, a.floor)
        str_, ste = summ(rtr), summ(rte)
        print("%-8s %9d %10.2f %10.2f %9d %10.2f %9.2f" % (
            "+-%.2f%%" % b, str_["deferred"], str_["exp"], str_["delta"],
            ste["deferred"], ste["exp"], ste["delta"]))
        if best is None or str_["exp"] > best[1]:
            best = (b, str_["exp"])

    b = best[0]
    rte = run(test, bars, b, a.floor)
    ste = summ(rte)
    moved = [x for x in rte if x[1]]
    print("\ntrain-best band: +-%.2f%%  ->  test exp $%+.2f vs baseline $%+.2f  (delta $%+.2f, %.0f%% of rows deferred)"
          % (b, ste["exp"], base_te["exp"], ste["delta"], 100 * len(moved) / max(1, len(rte))))
    if moved:
        better = sum(1 for x in moved if x[0] > x[4])
        gains = [x[0] - x[4] for x in moved]
        print("  deferred rows: %d   better than booked: %d (%.0f%%)   worst single change $%+.2f   best $%+.2f"
              % (len(moved), better, 100 * better / len(moved), min(gains), max(gains)))
        print("  hold length (bars): median %.0f  p95 %.0f   resolved by: band %d  cap %d  floor %d"
              % (statistics.median([x[2] for x in moved]),
                 statistics.quantiles([x[2] for x in moved], n=20)[18] if len(moved) > 20 else max(x[2] for x in moved),
                 sum(1 for x in moved if x[3] == "band"), sum(1 for x in moved if x[3] == "cap"),
                 sum(1 for x in moved if x[3] == "floor")))
        print("  net change from deferrals: $%+.2f on %d rows"
              % (sum(gains), len(gains)))


if __name__ == "__main__":
    main()
