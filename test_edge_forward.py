"""Tests for the forward paper contest arithmetic."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import edge_forward as ef


def test_first_after_handles_a_cursor_missing_from_the_index():
    idx = pd.DatetimeIndex(["2026-11-12T18:00Z", "2026-11-12T20:00Z"])
    assert ef.first_after(idx, "2026-11-12T19:00Z") == 1
    assert ef.first_after(idx, "2026-11-12T17:00Z") == 0
    assert ef.first_after(idx, "2026-11-12T20:00Z") == 2


def test_stop_wins_on_an_ambiguous_bar_and_gaps_fill_at_open():
    assert ef.exit_fill(100, 105, 95, 101, 97, 110, 1)[1] == "stop"
    assert ef.exit_fill(96, 105, 95, 101, 97, 110, 1) == (96, "stop-gap")
    assert ef.exit_fill(100, 111, 99, 101, 97, 110, 1) == (110, "target")
    assert ef.exit_fill(100, 109, 98, 101, 97, 110, 1) is None


def test_time_exit_uses_the_close_after_the_hold():
    assert ef.exit_fill(100, 105, 99, 101, 90, 120, ef.MAX_HOLD) == (
        101, "time")


def test_a_pass_rebases_the_start_and_a_death_retires(tmp_path: Path):
    ef.TRADES = tmp_path / "trades.jsonl"
    cs = ef._new_state("x.y", "x + y")
    ef._book(cs, 1300.0, 110, "target", "2026-11-12", "A/USD", 100, 900)
    assert cs["passes"] == 1
    assert cs["start"] == 11300.0
    assert cs["retired"] is False
    ef._book(cs, -200.0, 90, "stop", "2026-11-12", "A/USD", 100, 900)
    assert cs["deaths"] == 0
    ef._book(cs, -200.0, 90, "stop", "2026-11-12", "A/USD", 100, 900)
    assert cs["deaths"] == 1
    assert cs["retired"] is True
    rows = [json.loads(x) for x in ef.TRADES.read_text().splitlines()]
    assert len(rows) == 3


def test_policy_exits_support_short_breakeven_and_chandelier():
    long = {"entry": 100.0, "stop": 97.0, "tp": 107.5, "atr": 2.0,
            "risk": 3.0, "side": "long", "exit_mode": "fixed",
            "peak": 100.0}
    assert ef.exit_fill_policy(long, 100, 108, 99, 107, 1)[:2] == (107.5, "target")
    short = dict(long, side="short", stop=103.0, tp=92.5)
    assert ef.exit_fill_policy(short, 104, 105, 102, 104, 1)[:2] == (104, "stop-gap")
    assert ef.exit_fill_policy(short, 100, 101, 90, 92, 1)[:2] == (92.5, "target")
    be = dict(long, exit_mode="breakeven")
    assert ef.exit_fill_policy(be, 100, 104, 99, 103, 1) is None
    assert be["stop"] == 100.0
    assert ef.exit_fill_policy(be, 101, 101, 99, 99, 2)[:2] == (100.0, "stop")
    ch = dict(long, exit_mode="chandelier", tp=float("inf"))
    assert ef.exit_fill_policy(ch, 100, 106, 99, 105, 1) is None
    assert ch["stop"] == 103.0
    assert ef.exit_fill_policy(ch, 104, 105, 102, 103, 2)[:2] == (103.0, "stop")


def test_open_new_ignores_signals_older_than_the_latest_bar():
    from config import TradingConfig

    class Spec:
        side = "long"
        exit_mode = "fixed"
        size_frac = 0.09

        def rule(self, f):
            return True

    old = pd.Timestamp("2026-11-12T18:00Z")
    new = pd.Timestamp("2026-11-12T20:00Z")
    f = {"close": 100.0, "atr": 2.0}
    events = {"A/USD": [(old, 0, f, {}), (new, 1, f, {})]}
    state = {"candidates": {"x": ef._new_state("x", "x")}}
    cands = {"x": {"label": "x", "spec": Spec()}}
    ef._open_new(events, state, cands, TradingConfig())
    assert state["candidates"]["x"]["open"]["A/USD"]["entry_ts"] == str(new)


def test_init_state_is_idempotent_and_keeps_equity():
    cands = {"a.b": {"label": "a + b", "spec": object()}}
    old = {"last_bar": {"A": "2026-11-12"},
           "candidates": {"a.b": ef._new_state("a.b", "a + b")}}
    old["candidates"]["a.b"]["equity"] = 10500.0
    state = ef._init_state(cands, old)
    assert state["candidates"]["a.b"]["equity"] == 10500.0
    assert state["last_bar"]["A"] == "2026-11-12"
