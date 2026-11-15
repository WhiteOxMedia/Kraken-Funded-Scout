"""Live, order-free measurement of one fast-stop tick against Kraken.

Builds the exchange exactly as the scanner does (ccxt.kraken with
enableRateLimit) and reads the real contest watch via the scanner's own
_load_contest_watch. When the book is flat (every row tombstoned, as it is
today) it measures over the scanner's own trading universe instead, because
that is the widest set a live book can hold (53 of 54 pairs were watched in the
last month of the ledger). Every network call is counted and timed through a
proxy, so the numbers below are the calls production would make.

READ-ONLY BY CONSTRUCTION: the proxy exposes only fetch_tickers / fetch_ticker
and refuses every other attribute, so no order, balance or cancel call can even
be reached from here.

    python -X utf8 scratch/fast_stop_live_probe.py [--rounds N] [--pairs N]
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import ccxt  # noqa: E402

import background_scanner as bs  # noqa: E402
from config import TradingConfig  # noqa: E402

READ_ONLY = {"fetch_tickers", "fetch_ticker"}


class CountingVenue:
    """Delegates the two read-only price calls and counts them."""

    def __init__(self, real):
        self._real = real
        self.calls = []          # dicts: name, symbols, secs, ok, error, rows

    def fetch_tickers(self, symbols=None):
        return self._call("fetch_tickers", symbols)

    def fetch_ticker(self, symbol):
        return self._call("fetch_ticker", [symbol] if symbol else None)

    def _call(self, name, symbols):
        t0 = time.perf_counter()
        err = None
        out = None
        try:
            if symbols is None:
                out = getattr(self._real, name)()
            else:
                out = getattr(self._real, name)(symbols)
        except Exception as exc:                       # noqa: BLE001
            err = repr(exc)[:200]
        self.calls.append({"name": name,
                           "symbols": len(symbols) if symbols else None,
                           "secs": time.perf_counter() - t0,
                           "ok": out is not None,
                           "error": err,
                           "rows": len(out) if isinstance(out, dict) else None})
        if err:
            raise RuntimeError(err)
        return out

    def __getattr__(self, item):                        # everything else is a bug
        raise AttributeError("the fast path asked for %r — not a price read" % item)


def show(calls, px, pairs, dur, keep, total_single):
    bulk = [c for c in calls if c["name"] == "fetch_tickers"]
    single = [c for c in calls if c["name"] == "fetch_ticker"]
    missing = [p for p in pairs if p not in px]
    for c in calls[:6]:
        print("      %-13s symbols=%-4s rows=%-4s %7.3fs%s"
              % (c["name"], c["symbols"], c["rows"], c["secs"],
                 "" if c["ok"] else "  ERROR " + str(c["error"])))
    if len(calls) > 6:
        print("      ... %d more calls" % (len(calls) - 6))
    print("      prices back %d/%d  missing %s"
          % (len(px), len(pairs), (", ".join(missing[:6]) +
                                   (" ..." if len(missing) > 6 else "")) or "none"))
    print("      calls: batch=%d per-pair=%d   wall %.3fs of a %ss beat (headroom %+.1fs)"
          % (len(bulk), len(single), dur, bs.FAST_STOP_TICK_S,
             bs.FAST_STOP_TICK_S - dur))
    if bulk:
        b = bulk[0]
        print("      batch covered the watch set: %s"
              % ("YES" if (b["rows"] or 0) >= len(pairs) else
                 "PARTIAL — %s rows returned" % b["rows"]))
    return keep if (bulk and not single and not missing) else keep + 1


def main():
    rounds = 1
    if "--rounds" in sys.argv:
        rounds = int(sys.argv[sys.argv.index("--rounds") + 1])

    watch = bs._load_contest_watch() or {}
    pairs = [p for p, w in watch.items() if isinstance(w, dict)]
    source = "the live watch file (open book)"
    if not pairs:
        pairs = list(TradingConfig().trading_pairs)
        source = ("the scanner's trading universe — the live watch file is FLAT "
                  "(all %d keys tombstoned)" % len(watch))
    if "--pairs" in sys.argv:
        n = int(sys.argv[sys.argv.index("--pairs") + 1])
        pairs = pairs[:n]
        source += " [trimmed to %d]" % n

    print("watch file : %s" % bs.CONTEST_WATCH_JSON)
    print("pair set   : %d pairs from %s" % (len(pairs), source))
    print("pairs      : %s" % ", ".join(pairs))

    ex = ccxt.kraken({"enableRateLimit": True})
    print("venue      : kraken  rateLimit=%sms  enableRateLimit=True"
          % getattr(ex, "rateLimit", "?"))
    print("beat       : FAST_STOP_TICK_S=%ss  enabled=%s"
          % (bs.FAST_STOP_TICK_S, bs.FAST_STOP_ENABLED))

    worst = 0.0
    total_calls = 0
    bad = 0
    for i in range(rounds):
        print("\n--- tick %d ----------------------------------------------------" % (i + 1))
        v = CountingVenue(ex)
        t0 = time.perf_counter()
        px = bs._fast_prices(v, pairs)
        dur = time.perf_counter() - t0
        total_calls += len(v.calls)
        worst = max(worst, dur)
        bad = show(v.calls, px, pairs, dur, bad, None)

    # the alternative source: the venue's whole book in one request
    print("\n--- alternative source: fetch_tickers() with no symbols ---------")
    v = CountingVenue(ex)
    t0 = time.perf_counter()
    try:
        book = v.fetch_tickers()
        dur = time.perf_counter() - t0
        have = sum(1 for p in pairs if (book.get(p) or {}).get("last"))
        print("      one call, %d markets, %d of our %d pairs present, %.3fs"
              % (len(book or {}), have, len(pairs), dur))
    except Exception as exc:                           # noqa: BLE001
        print("      FAILED: %r" % (exc,))

    # the whole tick, judge included, over synthetic rows (timing only)
    print("\n--- full _fast_stop_scan over %d synthetic rows (judge cost) -----"
          % len(pairs))
    fake = {p: {"entry": 100.0, "size": 100.0, "hard_stop_usd": 3.0}
            for p in pairs}
    v = CountingVenue(ex)
    t0 = time.perf_counter()
    hit, px = bs._fast_stop_scan(v, fake)
    dur = time.perf_counter() - t0
    print("      %.3fs  calls=%d  prices=%d  breached=%s"
          % (dur, len(v.calls), len(px), hit or "none"))

    print("\n=== summary over %d tick(s) ====================================" % rounds)
    print("venue calls per tick : %.1f" % (total_calls / float(rounds)))
    print("worst tick duration  : %.3fs (beat %.0fs, headroom %+.1fs)"
          % (worst, bs.FAST_STOP_TICK_S, bs.FAST_STOP_TICK_S - worst))
    print("ticks that fell back to per-pair reads: %d of %d" % (bad, rounds))
    print("effective cadence    : one breach read every %.0fs while a tick costs %.1fs"
          % (bs.FAST_STOP_TICK_S, worst))
    return 0


class BatchDown(CountingVenue):
    """The venue refuses the batch: the real per-pair endpoint, live."""

    def fetch_tickers(self, symbols=None):
        self.calls.append({"name": "fetch_tickers", "symbols": len(symbols or []),
                           "secs": 0.0, "ok": False, "error": "simulated outage",
                           "rows": 0})
        raise RuntimeError("simulated outage")


def beat(seconds, ex, pairs):
    """Run the REAL watchdog against the live venue and time its beats.

    The closer and the death floor are stubbed to recorders for the duration,
    so no level of this run can place, cancel or change an order; the watchdog,
    the tick, the batched price read and the judge are the production ones.
    """
    import threading

    seed = bs._fast_prices(CountingVenue(ex), pairs)
    if not seed:
        print("no prices at all — cannot build a live book; nothing measured")
        return 2
    # entry == the price we just saw, so nothing is breached at t0
    watch = {p: {"pair": p, "entry": px, "size": 100.0, "hard_stop_usd": 3.0,
                 "added": "2026-11-15T12:00:00-07:00"}
             for p, px in seed.items()}
    sent = []
    orig = (bs._load_contest_watch, bs.death_floor_gate, bs._run_light_pass)
    bs._load_contest_watch = lambda: dict(watch)
    bs.death_floor_gate = lambda *a, **k: {}
    bs._run_light_pass = lambda *a, **k: sent.append(k)
    stop = threading.Event()
    th = threading.Thread(target=bs._fast_stop_watchdog, args=(ex, None, stop), daemon=True)
    seen = []
    t_start = time.time()
    try:
        th.start()
        while time.time() - t_start < seconds:
            clk = dict(bs._FAST_STOP_CLOCK)
            if not seen or clk.get("at") != seen[-1][0]:
                seen.append((clk.get("at"), clk.get("ticks"), clk.get("secs"),
                             clk.get("priced")))
            time.sleep(0.2)
    finally:
        stop.set()
        th.join(timeout=5.0)
        bs._load_contest_watch, bs.death_floor_gate, bs._run_light_pass = orig

    seen = [s for s in seen if s[0]]
    print("\n=== live beat over %.0fs ============================================="
          % seconds)
    print("book       : %d pairs, entry == the observed price" % len(watch))
    for at, n, secs, priced in seen:
        print("  tick %-3s at %s  tick duration %-6s  prices %s"
              % (n, time.strftime("%H:%M:%S", time.localtime(at)), secs, priced))
    gaps = [round(b[0] - a[0], 2) for a, b in zip(seen, seen[1:])]
    print("intervals  : %s" % gaps)
    if gaps:
        print("mean       : %.2fs   worst %.2fs   (beat %.0fs)"
              % (sum(gaps) / len(gaps), max(gaps), bs.FAST_STOP_TICK_S))
    print("closes attempted by this run: %d (recorder only, no orders)" % len(sent))
    return 0


if __name__ == "__main__":
    if "--failbatch" in sys.argv:
        w = bs._load_contest_watch() or {}
        ps = [p for p, r in w.items() if isinstance(r, dict)] \
            or list(TradingConfig().trading_pairs)
        v = BatchDown(ccxt.kraken({"enableRateLimit": True}))
        t0 = time.perf_counter()
        px = bs._fast_prices(v, ps)
        dur = time.perf_counter() - t0
        print("batch REFUSED by a simulated outage, %d pairs watched" % len(ps))
        print("  per-pair reads : %d (cap %d)"
              % (len([c for c in v.calls if c["name"] == "fetch_ticker"]),
                 bs.FAST_STOP_FALLBACK_MAX))
        print("  priced         : %d/%d" % (len(px), len(ps)))
        print("  tick duration  : %.2fs of a %.0fs beat (headroom %+.1fs)"
              % (dur, bs.FAST_STOP_TICK_S, bs.FAST_STOP_TICK_S - dur))
        print("  slowest single : %.2fs" % max([c["secs"] for c in v.calls] or [0.0]))
        sys.exit(0)
    if "--beat" in sys.argv:
        secs = int(sys.argv[sys.argv.index("--beat") + 1])
        w = bs._load_contest_watch() or {}
        ps = [p for p, r in w.items() if isinstance(r, dict)] \
            or list(TradingConfig().trading_pairs)
        sys.exit(beat(secs, ccxt.kraken({"enableRateLimit": True}), ps))
    sys.exit(main())
