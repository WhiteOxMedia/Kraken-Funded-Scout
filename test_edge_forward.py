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


# ── the forward summary: scratches beside the win rate, money first ──────

def test_a_breakeven_scratch_is_counted_beside_the_win_rate(tmp_path: Path):
    import types

    ef.TRADES = tmp_path / "trades.jsonl"
    cs = ef._new_state("x.breakeven", "x / breakeven")
    for _ in range(2):                                   # two clean targets
        ef._book(cs, 60.0, 110, "target", "2026-11-12", "A/USD", 100, 900)
    for _ in range(3):                                   # breakeven: at entry
        ef._book(cs, -0.72, 100, "stop", "2026-11-12", "A/USD", 100, 900)
    for _ in range(2):                                   # real stops
        ef._book(cs, -90.0, 90, "stop", "2026-11-12", "A/USD", 100, 900)

    facts = ef._trade_facts()["x.breakeven"]
    row = ef._summary(cs, facts, types.SimpleNamespace(exit_mode="breakeven"))

    assert (row["trades"], row["wins"], row["scratch"], row["losses"]) == (
        7, 2, 3, 2)
    assert row["win_rate"] == round(2 / 7, 3)
    # the rate a reader should judge the design by: scratches are not evidence
    assert row["decided_win_rate"] == 0.5
    assert row["exits"] == {"scratch": 3, "stop": 5, "target": 2}
    assert "3/7" in row["note"] and "by design" in row["note"]
    # money and window status lead the line
    assert list(row)[:5] == ["id", "label", "equity", "net", "windows"]
    assert row["windows"]["status"].startswith("live:")
    assert row["windows"]["death_line"] == round(cs["start"] * ef.DEATH, 2)


def test_a_stop_off_entry_is_a_loss_and_an_empty_lane_reports_no_rate(
        tmp_path: Path):
    ef.TRADES = tmp_path / "trades.jsonl"
    cs = ef._new_state("x.fixed", "x / fixed")
    ef._book(cs, -4.5, 99.5, "stop", "2026-11-12", "A/USD", 100, 900)
    row = ef._summary(cs, ef._trade_facts()["x.fixed"])
    assert (row["scratch"], row["losses"]) == (0, 1)
    assert row["decided_win_rate"] == 0.0

    empty = ef._summary(ef._new_state("y.fixed", "y / fixed"), {})
    assert (empty["trades"], empty["win_rate"], empty["decided_win_rate"]) == (
        0, None, None)
    assert empty["windows"]["status"].startswith("live:")


# ── a delisted pair must not freeze the candidate's single slot ──────────

def test_a_dead_pair_is_closed_stale_so_the_slot_frees(tmp_path: Path):
    ef.TRADES = tmp_path / "trades.jsonl"
    dead_idx = pd.date_range("2026-11-12T18:00Z", periods=4, freq="1h")
    dead = pd.DataFrame({"open": [100.0, 100.5, 100.2, 99.4],
                         "high": [101.0, 101.0, 101.0, 99.5],
                         "low": [99.5, 100.0, 99.8, 98.5],
                         "close": [100.5, 100.2, 99.9, 99.0]}, index=dead_idx)
    live_idx = pd.date_range("2026-11-12T18:00Z", periods=30, freq="1h")
    live = pd.DataFrame({c: [100.0] * 30
                         for c in ("open", "high", "low", "close")},
                        index=live_idx)
    pairs = {"DEAD/USD": dead, "LIVE/USD": live}
    state = {"candidates": {}}

    dead_cs = ef._new_state("x.dead", "x / dead")
    dead_cs["open"]["DEAD/USD"] = {
        "entry_ts": str(dead_idx[1]), "entry": 100.2, "stop": 90.0,
        "tp": 200.0, "size": 900.0, "qty": 900.0 / 100.2, "atr": 2.0,
        "risk": 10.2, "side": "long", "exit_mode": "fixed", "peak": 100.2,
    }
    state["candidates"]["x.dead"] = dead_cs

    fresh_cs = ef._new_state("x.live", "x / live")
    fresh_cs["open"]["LIVE/USD"] = {
        "entry_ts": str(live_idx[-1]), "entry": 100.0, "stop": 90.0,
        "tp": 200.0, "size": 900.0, "qty": 9.0, "atr": 2.0, "risk": 10.0,
        "side": "long", "exit_mode": "fixed", "peak": 100.0,
    }
    state["candidates"]["x.live"] = fresh_cs

    ef._advance_open(pairs, state, [])

    # the delisted pair is booked at its LAST PRINTED CLOSE, not the tape end
    assert dead_cs["open"] == {}
    row = json.loads(ef.TRADES.read_text().splitlines()[-1])
    assert row["why"] == "stale"
    assert row["pair"] == "DEAD/USD"
    assert row["exit"] == 99.0
    fee = 900.0 * ef.FEE_RT_PCT / 100.0
    # net is booked rounded to the cent
    assert abs(dead_cs["net"] - (900.0 / 100.2 * (99.0 - 100.2) - fee)) < 0.01
    assert row["ts"] == str(dead_idx[-1])
    # a pair that is merely young/current keeps its position
    assert list(fresh_cs["open"]) == ["LIVE/USD"]
    assert fresh_cs["net"] == 0.0
