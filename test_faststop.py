"""THE FAST STOP PATH (Nov 15).

The stop was only read when the whole light sweep reached a row, and that sweep
takes ~9 minutes (it fetches one ticker per row at ~4s each). The six measurable
stopped-out closes were booked 5-18 minutes after their stop was crossed.

These checks hold the fast path: a breach is detected on the fast beat from ONE
batched price read, the close is delegated to the existing light pass on the
named rows only, a row that is not breached is never touched, a repeated tick
cannot book the same exit twice, and a dead market is a skipped tick rather than
an exception or a fabricated price.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import background_scanner as bs

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


def row(entry, size, stop, added="2026-11-15T12:00:00-07:00"):
    return {"pair": "AAA/USD", "entry": entry, "size": size,
            "hard_stop_usd": stop, "added": added}


class Ex:
    """A market that answers from a dict and counts its calls."""

    def __init__(self, prices, bulk=True, dead=False, bulk_covers=None):
        self.prices = prices
        self.bulk = bulk
        self.dead = dead
        self.bulk_covers = bulk_covers      # None = the batch covers everything
        self.bulk_calls = 0
        self.single_calls = 0
        self.single_pairs = []
        if not bulk:
            self.fetch_tickers = None      # the venue has no batch endpoint

    def fetch_tickers(self, pairs=None):
        self.bulk_calls += 1
        if self.dead:
            raise RuntimeError("market unavailable")
        px = self.prices
        if self.bulk_covers is not None:
            px = {p: v for p, v in self.prices.items() if p in self.bulk_covers}
        return {p: {"last": v} for p, v in px.items()}

    def fetch_ticker(self, pair):
        self.single_calls += 1
        self.single_pairs.append(pair)
        if self.dead:
            raise RuntimeError("market unavailable")
        if pair not in self.prices:
            raise KeyError(pair)
        return {"last": self.prices[pair]}


# ── prompt detection ───────────────────────────────────────────────────────
watch = {"AAA/USD": row(100.0, 1000.0, 30.0), "BBB/USD": row(50.0, 500.0, 20.0)}
ex = Ex({"AAA/USD": 96.90, "BBB/USD": 49.90})     # AAA is -$31 (past -$30), BBB is -$1
hit, px = bs._fast_stop_scan(ex, watch)
check("scan: the breached stop is found on the fast read", hit == ["AAA/USD"], hit)
check("scan: the unbreached row is left out", "BBB/USD" not in hit)
check("scan: prices came back for both rows", px == {"AAA/USD": 96.90, "BBB/USD": 49.90}, px)
check("scan: ONE batched read, no per-pair calls", (ex.bulk_calls, ex.single_calls) == (1, 0),
      (ex.bulk_calls, ex.single_calls))

# the incumbent judge triggers AT the stop (`usd <= -stop`), and the fast path
# must never be more permissive than the sweep it stands in for
edge = Ex({"AAA/USD": 97.0})                       # -$30.00 exactly
check("scan: exactly at the stop counts as breached, as the sweep judges it",
      bs._fast_stop_scan(edge, {"AAA/USD": row(100.0, 1000.0, 30.0)})[0] == ["AAA/USD"])
inside = Ex({"AAA/USD": 97.01})                    # -$29.90, a dime inside
check("scan: a dime inside the stop is left alone",
      bs._fast_stop_scan(inside, {"AAA/USD": row(100.0, 1000.0, 30.0)})[0] == [])

# ── fallback when the batch endpoint is unavailable ────────────────────────
few = Ex({"AAA/USD": 96.90}, bulk=False)
hit2, _ = bs._fast_stop_scan(few, {"AAA/USD": row(100.0, 1000.0, 30.0)})
check("scan: falls back to one-per-pair when the batch read is absent",
      hit2 == ["AAA/USD"] and few.single_calls == 1, (hit2, few.single_calls))

# ── the market is gone ─────────────────────────────────────────────────────
dead = Ex({}, dead=True)
check("dead market: no prices, no breach, no exception",
      bs._fast_stop_scan(dead, {"AAA/USD": row(100.0, 1000.0, 30.0)}) == ([], {}))

_calls = []
_orig_run = bs._run_light_pass
_orig_watch = bs._load_contest_watch
_orig_floor = bs.death_floor_gate
# THE LIVE-ARTIFACT GUARD: _run_light_pass ends with _pulse_beat("light"), which
# writes scanner_pulse.json — a drive that ran it stamped the LIVE pulse with the
# drive's pid (the 22:32 pid on the console plate was this test, not a scanner).
# Stub it, so no drive can touch the live heartbeat file.
_orig_pulse = bs._pulse_beat
try:
    bs._pulse_beat = lambda *a, **k: None
    bs._load_contest_watch = lambda: {"AAA/USD": row(100.0, 1000.0, 30.0)}
    bs.death_floor_gate = lambda *a, **k: {}
    bs._run_light_pass = lambda *a, **k: _calls.append(k)

    got = bs._fast_stop_tick(dead, None)
    check("dead market: the tick returns no breach and no prices", got == ([], {}), got)
    check("dead market: the closer is never called", _calls == [], _calls)

    # ── breach on the fast beat triggers the existing pass, narrowly ──────
    _calls.clear()
    live = Ex({"AAA/USD": 96.90})
    got2 = bs._fast_stop_tick(live, None)
    check("breach: the tick reports the pair", got2[0] == ["AAA/USD"], got2)
    check("breach: it delegates to the light pass exactly once", len(_calls) == 1, _calls)
    check("breach: the pass is narrowed to the breached row",
          _calls and _calls[0].get("only_pairs") == ["AAA/USD"], _calls)
    check("breach: the delegation is stamped as the fast stop source",
          _calls and _calls[0].get("source") == "faststop", _calls)

    # ── no double-exit across repeated ticks ─────────────────────────────
    # the real close removes the row; model that and tick twice on the same price
    _calls.clear()
    book = {"AAA/USD": row(100.0, 1000.0, 30.0)}

    def _closing_run(*a, **k):
        _calls.append(k)
        for p in (k.get("only_pairs") or []):
            book.pop(p, None)          # the close writer's row removal

    bs._run_light_pass = _closing_run
    bs._load_contest_watch = lambda: dict(book)
    bs._fast_stop_tick(live, None)
    bs._fast_stop_tick(live, None)
    check("repeat: the same breach books once across two ticks", len(_calls) == 1, len(_calls))
    check("repeat: the closed row is gone from the book", book == {}, book)

    # ── a position that is not breached is never touched ─────────────────
    _calls.clear()
    bs._load_contest_watch = lambda: {"AAA/USD": row(100.0, 1000.0, 30.0)}
    calm = Ex({"AAA/USD": 99.90})
    got3 = bs._fast_stop_tick(calm, None)
    check("calm: no breach, no close", got3[0] == [] and _calls == [], (got3, _calls))

    # ── the death floor is on the same beat ──────────────────────────────
    _calls.clear()
    bs.death_floor_gate = lambda *a, **k: {"exit_warning": True, "halted": True}
    got4 = bs._fast_stop_tick(calm, None)
    check("floor: a death-floor warning fires the exit gates", len(_calls) == 1, _calls)
    check("floor: the floor pass is the whole book, not narrowed",
          _calls and _calls[0].get("only_pairs") is None, _calls)
    check("floor: no breach is reported as a breach", got4[0] == [], got4)

    # ── scope is set for the pass and cleared after ───────────────────────
    seen = []
    bs.death_floor_gate = lambda *a, **k: {}
    bs._load_contest_watch = lambda: {"AAA/USD": row(100.0, 1000.0, 30.0)}
    real_contest_light = bs.contest_light

    def _capture_light(exchange, cfg):
        seen.append(bs._LIGHT_SCOPE.get("pairs"))

    bs.contest_light = _capture_light
    bs._run_light_pass = _orig_run
    bs._LIGHT_CLOCK["at"] = 0.0
    bs._run_light_pass(live, None, source="faststop", only_pairs=["AAA/USD"])
    check("scope: the narrow pass runs with the named rows", seen == [{"AAA/USD"}], seen)
    check("scope: the scope is cleared afterwards", bs._LIGHT_SCOPE.get("pairs") is None,
          bs._LIGHT_SCOPE)
    bs.contest_light = real_contest_light
finally:
    bs._run_light_pass = _orig_run
    bs._load_contest_watch = _orig_watch
    bs.death_floor_gate = _orig_floor
    bs._pulse_beat = _orig_pulse

# ── the sweep honours the scope and the cadence is fast ───────────────────
src = open("background_scanner.py", encoding="utf-8").read()
check("wiring: contest_light reads a narrow scope", "_only_pairs = _LIGHT_SCOPE.get(\"pairs\")" in src)
check("wiring: the row loop skips rows outside the scope",
      "if _only_pairs is not None and pair not in _only_pairs:" in src)
check("wiring: the fast thread is started next to the exit watch",
      "name=\"fast-stop\"" in src)
check("cadence: 20s by default, env-overridable",
      bs.FAST_STOP_TICK_S == 20.0 and "FAST_STOP_TICK_S" in src, bs.FAST_STOP_TICK_S)
check("cadence: strictly faster than one light tick", bs.FAST_STOP_TICK_S < bs.LIGHT_TICK_S,
      (bs.FAST_STOP_TICK_S, bs.LIGHT_TICK_S))
check("safety: the fast path closes nothing itself",
      "_run_light_pass(exchange, cfg, source=\"faststop\"" in src)

# ── THE LIVE-MEASURED BUDGET (Nov 15) ────────────────────
# Kraken served the whole 54-pair universe in ONE fetch_tickers call (0.9-2.2s,
# 54/54 rows). These hold the paths that could have broken that: a batch that
# covers only part of the book, and a batch that fails outright.
universe = {}
for i in range(40):
    universe["C%02d/USD" % i] = {"pair": "C%02d/USD" % i, "entry": 100.0,
                                 "size": 100.0, "hard_stop_usd": 3.0}
full = Ex({p: 100.0 for p in universe})
bs._fast_stop_scan(full, universe)
check("budget: a full-market batch is ONE call and no per-pair burst",
      (full.bulk_calls, full.single_calls) == (1, 0),
      (full.bulk_calls, full.single_calls))

# a short batch is repaired ONLY where it was short
partial = Ex({"C00/USD": 90.0, "C01/USD": 90.0, "C02/USD": 90.0},
             bulk_covers={"C00/USD", "C01/USD"})
bs._fast_stop_scan(partial, {p: {"pair": p, "entry": 100.0, "size": 100.0,
                                "hard_stop_usd": 3.0}
                            for p in ("C00/USD", "C01/USD", "C02/USD")})
check("budget: a partial batch re-reads only the missing row",
      (partial.bulk_calls, partial.single_pairs) == (1, ["C02/USD"]),
      (partial.bulk_calls, partial.single_pairs))

# a dead batch must not become a whole-book burst of per-pair reads
storm = Ex({p: 90.0 for p in universe}, dead=True)
storm_px = bs._fast_prices(storm, list(universe))
check("budget: a RAISED batch is not repaired per pair at all"
      " (fetch_ticker is built on fetch_tickers)",
      storm.single_calls == 0, (storm.single_calls, bs.FAST_STOP_FALLBACK_MAX))
check("budget: a dead venue yields no prices, never a fabricated one", storm_px == {}, storm_px)
check("budget: the fallback cap is small enough to fit one beat",
      0 < bs.FAST_STOP_FALLBACK_MAX <= 12, bs.FAST_STOP_FALLBACK_MAX)

noBulk = Ex({p: 90.0 for p in universe}, bulk=False)
bs._fast_prices(noBulk, list(universe))
check("budget: no batch endpoint at all is still bounded by the cap",
      noBulk.single_calls == bs.FAST_STOP_FALLBACK_MAX,
      (noBulk.single_calls, bs.FAST_STOP_FALLBACK_MAX))

check("wiring: the beat is deadline-scheduled, not sleep-after-tick",
      "next_at += period" in src and "next_at = time.time() + period" in src)

# ── the beat keeps its period when a tick costs time ─────────────────────
import threading
_orig_tick = bs._fast_stop_tick
_orig_period = bs.FAST_STOP_TICK_S
_stamps = []
try:
    def _slow_tick(ex, cfg):
        time.sleep(0.05)                 # a tick that costs a quarter of the beat
        _stamps.append(time.time())
        return [], {}

    bs._fast_stop_tick = _slow_tick
    bs.FAST_STOP_TICK_S = 0.2
    _stop = threading.Event()
    _th = threading.Thread(target=bs._fast_stop_watchdog, args=(None, None, _stop),
                           daemon=True)
    _th.start()
    time.sleep(1.1)
    _stop.set()
    _th.join(timeout=2.0)
finally:
    bs._fast_stop_tick = _orig_tick
    bs.FAST_STOP_TICK_S = _orig_period

deltas = [round(b - a, 3) for a, b in zip(_stamps, _stamps[1:])]
check("cadence: ticks keep the beat even when a tick costs time",
      len(_stamps) >= 4 and deltas and max(deltas) <= 0.28,
      (len(_stamps), deltas))

# ── a breach is acted on within one beat of the crossing ────────────────
_orig_tick2 = bs._fast_stop_tick
_orig_watch2 = bs._load_contest_watch
_orig_floor2 = bs.death_floor_gate
_orig_run2 = bs._run_light_pass
try:
    _flip = {"at": None}
    _sent = []

    class FlipEx:
        """Calm until 0.25s into the run, then past the stop."""

        def __init__(self):
            self.t0 = time.time()

        def fetch_tickers(self, pairs=None):
            if time.time() - self.t0 > 0.25:
                if _flip["at"] is None:
                    _flip["at"] = time.time()
                return {p: {"last": 96.90} for p in (pairs or [])}
            return {p: {"last": 99.90} for p in (pairs or [])}

        def fetch_ticker(self, pair):
            return self.fetch_tickers([pair])[pair]

    bs._load_contest_watch = lambda: {"AAA/USD": row(100.0, 1000.0, 30.0)}
    bs.death_floor_gate = lambda *a, **k: {}
    bs._run_light_pass = lambda *a, **k: _sent.append((time.time(), k))
    bs.FAST_STOP_TICK_S = 0.2
    _stop2 = threading.Event()
    _th2 = threading.Thread(target=bs._fast_stop_watchdog,
                            args=(FlipEx(), None, _stop2), daemon=True)
    _th2.start()
    time.sleep(1.0)
    _stop2.set()
    _th2.join(timeout=2.0)

    latency = (min(t for t, _k in _sent) - _flip["at"]) if (_sent and _flip["at"]) else None
    check("latency: a breach is acted on within one beat of the crossing",
          latency is not None and latency <= 0.2 + 0.15, latency)
    check("latency: the action is the narrow delegation, unchanged",
          bool(_sent) and _sent[0][1].get("only_pairs") == ["AAA/USD"], _sent[:1])
    check("clock: the tick records its own duration for the cadence proof",
          isinstance(bs._FAST_STOP_CLOCK.get("secs"), float)
          and bs._FAST_STOP_CLOCK["secs"] > 0.0, bs._FAST_STOP_CLOCK.get("secs"))
finally:
    bs._fast_stop_tick = _orig_tick2
    bs._load_contest_watch = _orig_watch2
    bs.death_floor_gate = _orig_floor2
    bs._run_light_pass = _orig_run2
    bs.FAST_STOP_TICK_S = _orig_period

print("\n".join(lines))
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
