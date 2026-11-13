"""Pins for the live 5m edge: selection, the six-bar window, and both exits."""
from __future__ import annotations

from datetime import datetime, timezone

import bounce_live as bl

BASE = 1_790_000_100          # epoch seconds, aligned to the 5m grid


# --- scan_frames -----------------------------------------------------------

def _frame(prev, close, *, qv=100_000.0, open_=None, ema9=None, ema21=None):
    return {
        "open": [prev, close if open_ is None else open_],
        "close": [prev, close],
        "qv": [0.0, qv],
        "ts": [(BASE - 300) * 1000, BASE * 1000],
        "ema9": list(ema9 if ema9 is not None else [close, close]),
        "ema21": list(ema21 if ema21 is not None else [close, close]),
    }


def _pepe_frame(*, close=101.5, open_=100.5, ema9=(99.0, 101.0),
                ema21=(101.0, 100.5)):
    return _frame(100.0, close, open_=open_, ema9=ema9, ema21=ema21)


def test_bounce_is_the_first_qualifying_frame_in_order():
    """Not the deepest drop: the first asset that clears both filters."""
    frames = {"BTC": _frame(100.0, 98.0, qv=60_000.0),   # -2.0%
              "ETH": _frame(100.0, 95.9, qv=60_000.0),   # -4.1%
              "ZEC": _frame(100.0, 93.0, qv=60_000.0)}   # -7.0%
    sig = bl.scan_frames(frames)
    assert sig["asset"] == "ETH"
    assert sig["kind"] == "bounce"
    assert sig["drop_pct"] < -4.0
    assert sig["carry"] >= bl.MIN_CARRY_USD


def test_bounce_needs_both_the_drop_and_the_carry():
    assert bl.scan_frames({"BTC": _frame(100.0, 96.1, qv=60_000.0)}) is None
    assert bl.scan_frames({"BTC": _frame(100.0, 95.5, qv=49_000.0)}) is None
    edge = bl.scan_frames({"BTC": _frame(100.0, 95.5, qv=50_000.0)})
    assert edge["kind"] == "bounce"


def test_scan_frames_is_silent_without_a_setup():
    assert bl.scan_frames({}) is None
    assert bl.scan_frames({"BTC": _frame(100.0, 100.4)}) is None


def test_pepe_needs_a_fresh_cross_on_a_green_bar():
    sig = bl.scan_frames({"PEPE": _pepe_frame()})
    assert sig["asset"] == "PEPE" and sig["kind"] == "pepe"
    assert 85.0 < sig["conviction"] <= 100.0
    # Same cross, but the bar closed red.
    assert bl.scan_frames({"PEPE": _pepe_frame(open_=102.0)}) is None
    # EMA9 already above EMA21: no fresh cross.
    assert bl.scan_frames({"PEPE": _pepe_frame(
        ema9=(100.5, 101.0), ema21=(100.0, 100.5))}) is None


def test_pepe_conviction_is_capped():
    sig = bl.scan_frames({"PEPE": _pepe_frame(close=106.0)})
    assert sig["conviction"] == 100.0


def test_bounce_outranks_pepe_even_when_pepe_is_listed_first():
    frames = {"PEPE": _pepe_frame(),
              "ZEC": _frame(100.0, 95.5, qv=60_000.0)}
    assert bl.scan_frames(frames)["asset"] == "ZEC"


# --- exit_signal -----------------------------------------------------------

class _Exchange:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def fetch_ohlcv(self, pair, timeframe, limit=120):
        self.calls.append((pair, timeframe))
        return [list(r) for r in self.rows]


def _rows(n=30, price=100.0):
    return [[(BASE + i * 300) * 1000, price, price, price, price, 500.0]
            for i in range(n)]


def _row(idx, *, open_, high, low, close):
    return [(BASE + idx * 300) * 1000, open_, high, low, close, 500.0]


def _watch(*, entry=100.0, size=1000.0, stop_usd=20.0, goal_usd=30.0,
           entry_idx=20, go_big=False, exit_ts=None):
    """$98 stop / $103 take on a $100 entry with $1,000 at risk."""
    ts = BASE + entry_idx * 300 + 10          # 10s into the entry bar
    return {"entry": entry, "size": size, "hard_stop_usd": stop_usd,
            "goal_usd": goal_usd, "go_big": go_big,
            "opened_ts": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "bounce_exit_ts": exit_ts}


def test_stop_is_live_on_the_entry_bar():
    rows = _rows()
    rows[20] = _row(20, open_=97.0, high=99.0, low=96.0, close=98.0)
    out = bl.exit_signal(_Exchange(rows), "BTC/USD", _watch(),
                         now=BASE + 21 * 300)
    assert out.startswith("STOPPED OUT") and "opened" in out


def test_stop_beats_take_when_one_bar_covers_both():
    rows = _rows()
    rows[21] = _row(21, open_=100.0, high=104.0, low=97.0, close=103.0)
    out = bl.exit_signal(_Exchange(rows), "BTC/USD", _watch(),
                         now=BASE + 22 * 300)
    assert "STOPPED OUT" in out and "TAKE IT" not in out


def test_take_reads_the_bar_high():
    rows = _rows()
    rows[21] = _row(21, open_=100.0, high=104.0, low=99.0, close=103.5)
    out = bl.exit_signal(_Exchange(rows), "BTC/USD", _watch(),
                         now=BASE + 22 * 300)
    assert out.startswith("TAKE IT") and "high" in out


def test_bounce_reads_only_the_first_six_bars():
    rows = _rows()
    rows[26] = _row(26, open_=100.0, high=110.0, low=100.0, close=109.0)
    ex = _Exchange(rows)
    assert bl.exit_signal(ex, "BTC/USD", _watch(), now=BASE + 27 * 300) is None
    # PEPE has no six-bar cutoff; the same bar still takes it.
    late = bl.exit_signal(ex, "BTC/USD", _watch(go_big=True),
                          now=BASE + 27 * 300)
    assert late.startswith("TAKE IT")


def test_pepe_momentum_exits_on_a_closed_close_below_ema9():
    rows = _rows()
    rows[21] = _row(21, open_=100.0, high=100.0, low=96.0, close=96.0)
    w = _watch(stop_usd=50.0, goal_usd=50.0, go_big=True)   # $95 / $105
    out = bl.exit_signal(_Exchange(rows), "PEPE/USD", w, now=BASE + 22 * 300)
    assert out.startswith("PEPE GO BIG EXIT") and "EMA9" in out


def test_momentum_is_pepe_only():
    rows = _rows()
    rows[21] = _row(21, open_=100.0, high=100.0, low=96.0, close=96.0)
    w = _watch(stop_usd=50.0, goal_usd=50.0)                # bounce, $95/$105
    assert bl.exit_signal(_Exchange(rows), "BTC/USD", w,
                          now=BASE + 22 * 300) is None


def test_pepe_momentum_waits_for_the_bar_to_close():
    rows = _rows()
    rows[21] = _row(21, open_=100.0, high=100.0, low=96.0, close=96.0)
    w = _watch(stop_usd=50.0, goal_usd=50.0, go_big=True)
    in_progress = BASE + 21 * 300 + 100
    assert bl.exit_signal(_Exchange(rows), "PEPE/USD", w,
                          now=in_progress) is None


def test_momentum_never_fires_from_the_entry_bar_close():
    rows = _rows()
    for i in range(20, 30):
        rows[i] = _row(i, open_=100.0, high=100.0, low=96.0, close=96.0)
    w = _watch(stop_usd=50.0, goal_usd=50.0, go_big=True)   # $95 stop
    assert bl.exit_signal(_Exchange(rows), "PEPE/USD", w,
                          now=BASE + 23 * 300) is None


def test_exit_signal_reads_nothing_without_a_sized_position():
    ex = _Exchange(_rows())
    assert bl.exit_signal(ex, "BTC/USD", {"entry": 0.0, "size": 0.0},
                          now=BASE + 21 * 300) is None
    assert ex.calls == []


# --- ticker_exit -----------------------------------------------------------

def test_ticker_fallback_reads_stop_and_take():
    w = _watch()
    assert bl.ticker_exit(w, 98.0, now=BASE).startswith("STOPPED OUT")
    assert bl.ticker_exit(w, 97.0, now=BASE).startswith("STOPPED OUT")
    assert bl.ticker_exit(w, 103.0, now=BASE).startswith("TAKE IT")
    assert bl.ticker_exit(w, 101.0, now=BASE) is None
    assert bl.ticker_exit(w, 0.0, now=BASE) is None
    assert bl.ticker_exit({"entry": 0.0, "size": 0.0}, 101.0,
                          now=BASE) is None


def test_ticker_fallback_still_runs_the_six_bar_time_exit():
    w = _watch(exit_ts=BASE + 1800)
    assert bl.ticker_exit(w, 101.0, now=BASE + 1799) is None
    assert "TIME EXIT" in bl.ticker_exit(w, 101.0, now=BASE + 1800)
    # A stop already hit at the deadline is still reported as the stop.
    assert bl.ticker_exit(w, 97.0, now=BASE + 1800).startswith("STOPPED OUT")
