"""Where does the edge leak: buying high, giving the gain back, or trading too rarely?

Per trade, reconstructed from the ledger and 5m OHLC (the change was not tuned on
this file — it is the only history that exists):

  entry-side travel  entry vs the low of the prior 12 bars (1 hour): how much of
                     the move the book paid away by entering late;
  entry position     the same thing as a 0..1 position inside that 12-bar range
                     (0 = at the low, 1 = at the high) — the scanner's own
                     entry_quality term;
  exit-side giveback (peak high during the hold - exit) x units: dollars handed
                     back after the run peaked;
  realised           (exit/entry - 1) x units.

Then a TIME-ordered split (first 70% of closes = tuning, last 30% = held out),
never a shuffle: any candidate entry-timing gate is chosen on tuning only and
reported on the held-out slice against the same slice's baseline.

    python -X utf8 scratch/entry_timing_diagnosis.py [--train 0.7]
"""
import argparse
import bisect
import datetime as dt
import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STORE = HERE / "kraken_5m_long_183d_aligned.json"
LEDGER = ROOT / "contest_trades.jsonl"
LOOKBACK = 12          # bars (5m) = 1h, the scanner's TIMING_RANGE_CANDLES
DAY_BARS = 288


def fnum(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def ts(s):
    return int(dt.datetime.fromisoformat(str(s)).timestamp())


def load_bars():
    doc = json.loads(STORE.read_text(encoding="utf-8"))
    out = {}
    for tick, rows in doc["pairs"].items():
        out[tick] = {"t": [r[0] for r in rows],
                     "o": [r[1] for r in rows],
                     "h": [r[2] for r in rows],
                     "l": [r[3] for r in rows],
                     "c": [r[4] for r in rows]}
    return out


def load_ledger():
    rows = []
    for line in LEDGER.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    rows.sort(key=lambda r: ts(r["sold_at"]))
    return rows


def enrich(row, bars):
    tick = row["pair"].split("/")[0]
    b = bars.get(tick)
    if not b:
        return None
    entry = fnum(row.get("entry"))
    size = fnum(row.get("size"))
    held = fnum(row.get("held_min"))
    if entry <= 0 or size <= 0 or held <= 0:
        return None
    t_sold = ts(row["sold_at"])
    t_entry = t_sold - int(held * 60)
    i0 = bisect.bisect_left(b["t"], t_entry)
    i1 = bisect.bisect_left(b["t"], t_sold)
    if i0 < LOOKBACK or i0 >= len(b["t"]) or i1 <= i0:
        return None
    lo = min(b["l"][i0 - LOOKBACK:i0])
    hi = max(b["h"][i0 - LOOKBACK:i0])
    if hi <= lo:
        return None
    eq = (entry - lo) / (hi - lo)
    eq = min(1.0, max(0.0, eq))
    lo_day = min(b["l"][max(0, i0 - DAY_BARS):i0]) if i0 > 0 else lo
    peak = max(b["h"][i0:i1 + 1])
    trough = min(b["l"][i0:i1 + 1])
    units = size / entry
    exit_px = fnum(row.get("exit"))
    if exit_px <= 0:
        exit_px = entry * (1.0 + fnum(row.get("pnl_gross")) / size)
    return {
        "pair": row["pair"], "t": t_sold,
        "pnl": fnum(row.get("pnl_net")),
        "eq": eq,
        "above_low_pct": (entry / lo - 1.0) * 100.0,
        "above_day_low_pct": (entry / lo_day - 1.0) * 100.0,
        "travel_usd": (entry - lo) * units,
        "mfe_pct": (peak / entry - 1.0) * 100.0,
        "mae_pct": (trough / entry - 1.0) * 100.0,
        "realised_pct": (exit_px / entry - 1.0) * 100.0,
        "giveback_usd": (peak - exit_px) * units,
        "giveback_pct": (peak / entry - exit_px / entry) * 100.0,
        "held_min": held,
        "reason": str(row.get("exit_reason") or "")[:40],
    }


def stats(rows):
    n = len(rows)
    if not n:
        return {"n": 0}
    wins = [r["pnl"] for r in rows if r["pnl"] > 0]
    losses = [r["pnl"] for r in rows if r["pnl"] <= 0]
    aw = statistics.mean(wins) if wins else 0.0
    al = abs(statistics.mean(losses)) if losses else 0.0
    return {
        "n": n,
        "win_rate": 100.0 * len(wins) / n,
        "payoff": (aw / al) if al else float("inf"),
        "avg_win": aw, "avg_loss": -al,
        "total": sum(r["pnl"] for r in rows),
        "expectancy": sum(r["pnl"] for r in rows) / n,
        "avg_eq": statistics.mean(r["eq"] for r in rows),
        "avg_travel": statistics.mean(r["travel_usd"] for r in rows),
        "avg_giveback": statistics.mean(r["giveback_usd"] for r in rows),
        "avg_mfe": statistics.mean(r["mfe_pct"] for r in rows),
        "avg_realised": statistics.mean(r["realised_pct"] for r in rows),
    }


def line(tag, s):
    if not s.get("n"):
        print("%-28s n=0" % tag)
        return
    print("%-28s n=%4d  win %5.1f%%  payoff %5.2f  avgWin %7.2f  avgLoss %7.2f  "
          "total %9.2f  exp %7.2f  eq %.2f  travel $%6.2f  giveback $%6.2f  "
          "MFE %+5.2f%%  realised %+5.2f%%"
          % (tag, s["n"], s["win_rate"], s["payoff"], s["avg_win"], s["avg_loss"],
             s["total"], s["expectancy"], s["avg_eq"], s["avg_travel"],
             s["avg_giveback"], s["avg_mfe"], s["avg_realised"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=float, default=0.7)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    bars = load_bars()
    ledger = load_ledger()
    rows = [x for x in (enrich(r, bars) for r in ledger) if x]
    print("ledger closes: %d   walkable with 5m bars: %d   tickers in store: %d"
          % (len(ledger), len(rows), len(bars)))
    if not rows:
        return

    print("\n=== 1. the book, whole ===")
    line("all trades", stats(rows))

    print("\n=== 2. entry-side: position inside the prior 1h range ===")
    buckets = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
    for a, b in buckets:
        sub = [r for r in rows if a <= r["eq"] < b]
        line("eq %.1f-%.1f" % (a, b), stats(sub))

    print("\n=== 3. exit-side: peak vs booked ===")
    peak_up = [r for r in rows if r["mfe_pct"] >= 1.0]
    never_up = [r for r in rows if r["mfe_pct"] < 0.5]
    line("peaked >= +1%", stats(peak_up))
    line("never got +0.5%", stats(never_up))
    if peak_up:
        gave_back = [r for r in peak_up if r["realised_pct"] < r["mfe_pct"] - 1.0]
        print("   of the %d rows that ran >= +1%%, %d (%.0f%%) booked more than a "
              "point below their peak; avg MFE %+.2f%% vs avg booked %+.2f%% "
              "(%.2f pp handed back)"
              % (len(peak_up), len(gave_back), 100.0 * len(gave_back) / len(peak_up),
                 statistics.mean(r["mfe_pct"] for r in peak_up),
                 statistics.mean(r["realised_pct"] for r in peak_up),
                 statistics.mean(r["giveback_pct"] for r in peak_up)))
    tot_gb = sum(r["giveback_usd"] for r in rows)
    tot_tr = sum(r["travel_usd"] for r in rows)
    tot_pnl = sum(r["pnl"] for r in rows)
    print("   book totals: paid-away-on-entry $%.2f | handed-back-after-peak $%.2f "
          "| net $%.2f" % (tot_tr, tot_gb, tot_pnl))

    print("\n=== 4. frequency ===")
    span_days = (rows[-1]["t"] - rows[0]["t"]) / 86400.0
    print("   %d closes over %.1f days = %.2f/day; median hold %.0f min"
          % (len(rows), span_days, len(rows) / span_days,
             statistics.median(r["held_min"] for r in rows)))

    print("\n=== 5. WALK-FORWARD (time-ordered split, never shuffled) ===")
    cut = int(len(rows) * args.train)
    train, test = rows[:cut], rows[cut:]
    print("   tuning: %s .. %s  (n=%d)" % (
        dt.datetime.fromtimestamp(train[0]["t"]).date(),
        dt.datetime.fromtimestamp(train[-1]["t"]).date(), len(train)))
    print("   held out: %s .. %s  (n=%d)" % (
        dt.datetime.fromtimestamp(test[0]["t"]).date(),
        dt.datetime.fromtimestamp(test[-1]["t"]).date(), len(test)))
    line("TUNING baseline", stats(train))
    line("HELD-OUT baseline", stats(test))

    print("\n   candidate gates (chosen on tuning only):")
    best = None
    for thr in (0.2, 0.3, 0.4, 0.5, 0.6):
        kept = [r for r in train if r["eq"] <= thr]
        s = stats(kept)
        if s.get("n", 0) < 30:
            continue
        flag = ""
        if best is None or s["expectancy"] > best[1]:
            best = (thr, s["expectancy"], s)
            flag = "  <- best on tuning"
        print("     eq<=%.1f keeps %d/%d, tuning exp %+.2f%s"
              % (thr, s["n"], len(train), s["expectancy"], flag))
    for thr in (1.0, 2.0, 3.0, 5.0):
        kept = [r for r in train if r["above_day_low_pct"] <= thr]
        s = stats(kept)
        if s.get("n", 0) < 30:
            continue
        if best is None or s["expectancy"] > best[1]:
            best = ("daylow<=%.1f" % thr, s["expectancy"], s)
            print("     above_day_low<=%.1f%% keeps %d/%d, tuning exp %+.2f  <- best on tuning"
                  % (thr, s["n"], len(train), s["expectancy"]))

    if best is None:
        print("   no candidate had 30+ tuning trades — nothing can be chosen honestly")
        return
    print("\n   chosen on tuning: %s (tuning exp %+.2f)" % (best[0], best[1]))
    if isinstance(best[0], str):
        thr_s = best[0].split("<=")[1]
        thr = float(thr_s.replace("%", ""))
        kept_test = [r for r in test if r["above_day_low_pct"] <= thr]
        kept_train = [r for r in train if r["above_day_low_pct"] <= thr]
    else:
        kept_test = [r for r in test if r["eq"] <= best[0]]
        kept_train = [r for r in train if r["eq"] <= best[0]]
    line("new rule on HELD OUT", stats(kept_test))
    line("old rule on HELD OUT", stats(test))
    st_new, st_old = stats(kept_test), stats(test)
    if st_new.get("n") and st_old.get("n"):
        print("\n   delta on held-out: win %+.1f pp, payoff %+.2f, total %+.2f, "
              "trades %+d"
              % (st_new["win_rate"] - st_old["win_rate"],
                 st_new["payoff"] - st_old["payoff"],
                 st_new["total"] - st_old["total"],
                 st_new["n"] - st_old["n"]))
        print("   tuning-vs-heldout consistency: new rule exp train %+.2f / held %+.2f"
              % (stats(kept_train)["expectancy"], st_new["expectancy"]))


if __name__ == "__main__":
    main()
