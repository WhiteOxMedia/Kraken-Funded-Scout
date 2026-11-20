"""Checks for coin_history: a coin's capacity is measured, and its HISTORY votes.

  1. capacity is 1% of the median bar dollars - the shipped order rule
  2. a measurement creates the record, the day entry and the tier flags
  3. re-measuring the same day replaces that day instead of inventing a second
  4. once two days are observed the gate prices the HISTORY median, not today
  5. a one-day history still prices today's capacity and says it is thin
  6. an unmeasured coin is refused (a capacity on a guess is worse)
  7. an order past the capacity is refused with both numbers
  8. a fillable order is ok and reports its utilisation
  9. fillable() splits the coins and orders them by capacity
 10. summary() counts who can absorb $1k/$2.5k/$5k/the whole balance
 11. a corrupt file reads as an empty history
 12. trend_pct names a shrinking coin
 13. the tournament's rule needs as many fillable coins as the book has slots
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import coin_history as chist

sys.path.insert(0, str(Path(__file__).resolve().parent / "scratch"))


def _m(median_bar_usd: float, bars: int = 2000) -> dict:
    return {"pair": "X", "median_bar_usd": median_bar_usd, "bars": bars}


def test_capacity_is_one_percent_of_the_median_bar():
    assert chist.capacity_usd(100_000.0) == pytest.approx(1_000.0)
    assert chist.capacity_usd(2_300.0) == pytest.approx(23.0)
    assert chist.capacity_usd(None) == 0.0
    assert chist.PARTICIPATION == 0.01


def test_a_measurement_creates_the_record_and_its_tiers(tmp_path):
    p = str(tmp_path / "ch.json")
    t = chist.observe({"BTC/USD": _m(120_000.0)}, day="2026-11-17", path=p)
    rec = chist.coin("BTC", t)
    assert rec["capacity_usd"] == pytest.approx(1_200.0)
    assert rec["days"] == 1
    assert rec["history"][0]["day"] == "2026-11-17"
    assert rec["tiers"] == {"1000": True, "2500": False, "5000": False,
                            "10000": False}
    # a $23 coin clears no tier at all: that is the median contest pair
    t2 = chist.observe({"DUST/USD": _m(2_300.0)}, day="2026-11-17", path=p)
    assert chist.coin("DUST", t2)["tiers"] == {"1000": False, "2500": False,
                                               "5000": False, "10000": False}


def test_the_same_day_replaces_its_entry(tmp_path):
    p = str(tmp_path / "ch.json")
    chist.observe({"BTC": _m(100_000.0)}, day="2026-11-17", path=p)
    t = chist.observe({"BTC": _m(110_000.0)}, day="2026-11-17", path=p)
    rec = chist.coin("BTC", t)
    assert rec["days"] == 1
    assert len(rec["history"]) == 1
    assert rec["capacity_usd"] == pytest.approx(1_100.0)
    assert t["days_seen"] == 1


def test_two_days_price_the_history_median_not_today(tmp_path):
    p = str(tmp_path / "ch.json")
    chist.observe({"X": _m(100_000.0)}, day="2026-11-16", path=p)
    t = chist.observe({"X": _m(20_000.0)}, day="2026-11-17", path=p)
    rec = chist.coin("X", t)
    assert rec["capacity_usd"] == pytest.approx(200.0)      # today
    assert rec["capacity_median_usd"] == pytest.approx(600.0)
    g = chist.response_gate("X", 500.0, t)
    assert g["verdict"] == "ok"
    assert g["capacity_usd"] == pytest.approx(600.0)
    assert g["capacity_basis"] == "history median"
    assert g["utilisation_pct"] == pytest.approx(83.3, abs=0.1)


def test_a_one_day_history_prices_today_and_says_it_is_thin(tmp_path):
    p = str(tmp_path / "ch.json")
    t = chist.observe({"X": _m(100_000.0)}, day="2026-11-17", path=p)
    g = chist.response_gate("X", 500.0, t)
    assert g["verdict"] == "thin"
    assert g["thin_sample"] is True
    assert g["capacity_basis"] == "today"
    assert g["capacity_usd"] == pytest.approx(1_000.0)


def test_an_unmeasured_coin_is_refused():
    g = chist.response_gate("NOPE", 100.0, chist._blank())
    assert g["verdict"] == "unmeasured"
    assert g["measured"] is False
    assert "worse than a refusal" in g["why"]


def test_an_order_past_the_capacity_is_refused_with_both_numbers(tmp_path):
    p = str(tmp_path / "ch.json")
    t = chist.observe({"X": _m(2_300.0)}, day="2026-11-17", path=p)
    g = chist.response_gate("X", 2_500.0, t)
    assert g["verdict"] == "refuse"
    assert "$2,500" in g["why"] and "$23" in g["why"]
    assert g["utilisation_pct"] == pytest.approx(10869.6, abs=1.0)


def test_a_fillable_order_is_ok(tmp_path):
    p = str(tmp_path / "ch.json")
    t = chist.observe({"X": _m(1_000_000.0)}, day="2026-11-17", path=p)
    g = chist.response_gate("X", 5_000.0, t)
    assert g["verdict"] in ("ok", "thin")
    assert g["utilisation_pct"] == pytest.approx(50.0)


def test_fillable_splits_the_coins_and_orders_them_by_capacity(tmp_path):
    p = str(tmp_path / "ch.json")
    t = chist.observe({"BIG": _m(500_000.0), "SMALL": _m(2_300.0),
                       "MID": _m(300_000.0)}, day="2026-11-16", path=p)
    t = chist.observe({"BIG": _m(500_000.0), "SMALL": _m(2_300.0),
                       "MID": _m(300_000.0)}, day="2026-11-17", path=p)
    keep, refused = chist.fillable(["BIG", "MID", "SMALL"], 2_500.0, t)
    assert [g["pair"] for g in keep] == ["BIG", "MID"]
    assert list(refused) == ["SMALL"]
    assert "refuse" in refused["SMALL"]["verdict"]


def test_summary_counts_the_tiers(tmp_path):
    p = str(tmp_path / "ch.json")
    m = {"BTC/USD": _m(1_400_000.0), "ETH/USD": _m(1_100_000.0),
         "SOL/USD": _m(600_000.0), "XRP/USD": _m(300_000.0),
         "DUST/USD": _m(2_300.0)}
    chist.observe(m, day="2026-11-16", path=p)
    t = chist.observe(m, day="2026-11-17", path=p)
    s = chist.summary(t)
    assert s["coins"] == 5
    assert s["tiers"]["1000"]["pairs"] == 4          # everything but DUST
    assert s["tiers"]["2500"]["pairs"] == 4
    assert s["tiers"]["5000"]["pairs"] == 3
    assert s["tiers"]["10000"]["pairs"] == 2         # BTC and ETH only
    assert "DUST" not in s["tiers"]["1000"]["names"]


def test_a_corrupt_file_reads_as_an_empty_history(tmp_path):
    p = tmp_path / "ch.json"
    p.write_text("{not json", encoding="utf-8")
    t = chist.load(str(p))
    assert t["coins"] == {}
    assert chist.response_gate("BTC", 100.0, t)["verdict"] == "unmeasured"


def test_trend_names_a_shrinking_coin(tmp_path):
    p = str(tmp_path / "ch.json")
    chist.observe({"X": _m(100_000.0)}, day="2026-11-16", path=p)
    t = chist.observe({"X": _m(25_000.0)}, day="2026-11-17", path=p)
    rec = chist.coin("X", t)
    assert rec["trend_pct"] == pytest.approx(-75.0)
    assert rec["capacity_worst_usd"] == pytest.approx(250.0)


def _tape(volume_first: float, volume_last: float, days: int = 8,
          switch: float = 0.75):
    """Hourly bars whose volume switches once: a coin that grew or shrank.

    The switch sits late (3/4 of the tape) so the LAST day is a spike/fade day
    while most observed days sit on the other level - that is the shape the
    history has to catch and a single day's bar cannot.
    """
    import numpy as np
    import pandas as pd

    idx = pd.date_range("2026-11-08", periods=24 * days, freq="h", tz="UTC")
    vol = np.full(len(idx), float(volume_first))
    vol[int(len(idx) * switch):] = float(volume_last)
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                         "volume": vol}, index=idx)


def test_backfill_rebuilds_the_history_from_the_tapes_own_past(tmp_path):
    """One observation would price today's bar - exactly what universe_for asks."""
    p = str(tmp_path / "ch.json")
    tape = {"GROW": _tape(1_000.0, 1_000_000.0), "SHRINK": _tape(1_000_000.0, 1_000.0)}
    t = chist.backfill(tape, days=7, path=p)
    g, s = chist.coin("GROW", t), chist.coin("SHRINK", t)
    assert len(g["history"]) == 7 and g["days"] == 7
    assert len(s["history"]) == 7
    # each day is its OWN median: five low days then three high ones is a
    # spike (today's bar is the best day), and the reverse is a fade - the
    # history, not today's bar, is what decides
    assert g["capacity_usd"] == pytest.approx(10_000.0)     # today: $1M bars
    assert g["capacity_median_usd"] == pytest.approx(10.0)  # days: $1k bars
    assert s["capacity_usd"] == pytest.approx(10.0)
    assert s["capacity_median_usd"] == pytest.approx(10_000.0)
    assert g["trend_pct"] > 0 > s["trend_pct"]


def test_the_history_median_refuses_an_order_todays_bar_would_allow(tmp_path):
    """The whole point of the day median: a spike is not a capacity."""
    p = str(tmp_path / "ch.json")
    t = chist.backfill({"SPIKE": _tape(1_000.0, 900_000.0)}, days=7, path=p)
    rec = chist.coin("SPIKE", t)
    # today's own day would take $9,000; the eight observed days take $10
    assert rec["capacity_usd"] == pytest.approx(9_000.0)
    g = chist.response_gate("SPIKE", 1_000.0, t)
    assert g["capacity_basis"] == "history median"
    assert g["capacity_usd"] == pytest.approx(10.0)
    assert g["verdict"] == "refuse"
    assert "$10" in g["why"]


def test_backfill_keeps_the_measured_cost_of_the_pair(tmp_path):
    p = str(tmp_path / "ch.json")
    costs = {"BTC": {"rt_pct": 0.0021, "verdict": "ok", "measured": True}}
    t = chist.backfill({"BTC": _tape(1_000.0, 1_000.0)}, days=3, costs=costs,
                       path=p)
    rec = chist.coin("BTC", t)
    assert rec["cost_verdict"] == "ok"
    assert rec["cost_rt_pct"] == pytest.approx(0.0021)


def _qualifying_row(order: float = 1_200.0, slots: int = 4) -> dict:
    """A candidate in the artifact's own shape that clears a +12% week."""
    return {
        "id": "PROBE.dip.trend.scalp.6.any.us", "entry_max": 0.5,
        "mover": "any", "sell_high": None, "size": 0.12, "slots": slots,
        "breadth": 10, "bank_r": None, "trim_rollover": False,
        "risk_cap_pct": 2.0, "book_usd": order * slots,
        "order_usd": order, "guard_ok": True, "guard_cash": True,
        "forward_evidence": True, "forward_ret_pct": 3.4,
        "died_windows": [False, False, False], "weeks_12": 1,
        "best_week": 12.6, "win_rate": 0.55, "per_trade_points": 0.09,
        "trades_per_week": 2.1, "entry_timing_score": 0.92,
        "sell_high_score": 0.61, "timing_score": 0.78,
        "pairs_traded_list": ["A/USD"], "stand_down_pct": 1.0,
    }


def _promotion_row(tmp_path, caps: dict, order: float, slots: int):
    """Drive the REAL capacity decision from a synthetic coin history."""
    import _timing_tournament as tt

    p = str(tmp_path / "hist.json")
    measures = {pair: {"median_bar_usd": float(cap) / chist.PARTICIPATION,
                       "bars": 24} for pair, cap in caps.items()}
    chist.observe(measures, day="2026-11-16", path=p)
    t = chist.observe(measures, day="2026-11-17", path=p)
    keep, refused = chist.fillable(sorted(caps), order, t)
    cr = tt.capacity_response(order, slots, 10, [g["pair"] for g in keep],
                              {g["pair"]: g for g in keep}, refused)
    row = _qualifying_row(order=order, slots=slots)
    row["coin_response"] = cr
    return row, cr


def test_a_qualifying_candidate_is_REFUSED_when_the_book_cannot_fill(tmp_path):
    """The +12% branch, driven through the real promotion function.

    Two of the three coins absorb the $1,200 order; the book holds 4 slots, so
    only half its intended positions have a coin to sit in. Before Nov 18 this
    row was promoted on its +12% week alone.
    """
    import _timing_tournament as tt

    row, cr = _promotion_row(tmp_path, {"A/USD": 2_000.0, "B/USD": 1_500.0,
                                        "C/USD": 300.0}, order=1_200.0, slots=4)
    assert cr["fillable_pairs"] == 2 and cr["capacity_ok"] is False
    nxt = tt.select_champion([row], [row])
    assert nxt["champion"] is None
    assert nxt["status"].startswith("none (capacity:")
    assert "needs 4" in nxt["status"] and "only 2 absorb" in nxt["status"]
    assert "$1,200" in nxt["status"]
    assert nxt["promoted"] == []
    assert nxt["capacity_refused_promoted"] == [row]


def test_the_SAME_candidate_promotes_when_the_book_is_fundable(tmp_path):
    """Identical +12% row, identical order, two slots instead of four.

    Two slots at the $1,200 ticket is a $2,400 book: its thin coin takes
    $2,500, so the pair covers the $5,000 all-in ($5,000) and the book stays
    under the ceiling.
    """
    import _timing_tournament as tt

    row, cr = _promotion_row(tmp_path, {"A/USD": 3_000.0, "B/USD": 2_500.0,
                                        "C/USD": 300.0}, order=1_200.0, slots=2)
    assert cr["capacity_ok"] is True
    assert cr["headroom_pct"] == pytest.approx(208.3)   # rounded to 0.1%
    assert cr["all_in_cover_usd"] == pytest.approx(5_000.0)
    assert cr["book_usd"] == pytest.approx(2_400.0) and cr["ceiling_ok"] is True
    nxt = tt.select_champion([row], [row])
    assert nxt["status"] == "promoted"
    assert nxt["champion"] is row
    assert nxt["champion"]["coin_response"]["headroom_pct"] == pytest.approx(208.3)
    assert len(nxt["promoted"]) == 1


def test_a_book_that_cannot_fund_the_GO_BIG_all_in_is_refused(tmp_path):
    """A $900 ticket every coin absorbs is not the question any more.

    Two slots x the thin coin's $1,300 covers $2,600 of the $5,000 all-in: the
    book can seat its positions but cannot fund an all-in, so it is refused and
    the refusal names the shortfall rather than the seats.
    """
    import _timing_tournament as tt

    row, cr = _promotion_row(tmp_path, {"A/USD": 1_400.0, "B/USD": 1_300.0},
                             order=900.0, slots=3)
    assert cr["fillable_pairs"] == 2 < 3      # seats fail first here
    row2, cr2 = _promotion_row(tmp_path, {"D/USD": 1_400.0, "E/USD": 1_300.0},
                              order=900.0, slots=2)
    assert cr2["fillable_pairs"] == 2 and cr2["seats_ok"] is True
    assert cr2["all_in_cover_usd"] == pytest.approx(2_600.0)
    assert cr2["all_in_fundable"] is False and cr2["capacity_ok"] is False
    nxt = tt.select_champion([row2], [row2])
    assert nxt["champion"] is None
    assert "cover only $2,600" in nxt["status"]
    assert tt._capacity_clause(row2) == "all-in cover"


def test_a_book_over_the_death_line_ceiling_is_never_champion(tmp_path):
    """The stretch families are priced, not promotable: a $10,000 book is one
    3% gap from the death line, however well it fills."""
    import _timing_tournament as tt

    caps = {f"C{i}/USD": 2_000.0 for i in range(10)}
    row, cr = _promotion_row(tmp_path, caps, order=1_250.0, slots=8)
    assert cr["fillable_pairs"] >= 8 and cr["book_usd"] == pytest.approx(10_000.0)
    assert cr["ceiling_ok"] is False and cr["all_in_fundable"] is True
    assert cr["capacity_ok"] is False
    nxt = tt.select_champion([row], [row])
    assert nxt["champion"] is None and "death-line ceiling" in nxt["status"]
    assert tt._capacity_clause(row) == "book ceiling"


def test_a_candidate_with_no_capacity_record_cannot_promote(tmp_path):
    """No evidence is not permission: a missing block refuses, never passes."""
    import _timing_tournament as tt

    row = _qualifying_row()
    nxt = tt.select_champion([row], [row])
    assert nxt["champion"] is None
    assert nxt["status"].startswith("none (capacity:")


def test_the_measured_tables_are_tracked_not_runtime():
    """STATUS PIN (RUNTIME_INVARIANTS.md): measurements are tracked, not state.

    `coin_history.json` is the same kind of file as `spread_table.json` - a
    measurement the sampler writes and thresholds/gates read, never written by
    the running scanner while trading - so it is committed with the change that
    used it and named nowhere in the runtime guards. A later session that
    gitignores it, `git rm --cached`s it, or adds it to a runtime list fails
    here instead of silently shipping a clone that cannot reproduce a champion.
    """
    import subprocess

    names = ["spread_table.json", "coin_history.json"]
    root = chist.ROOT
    out = subprocess.run(["git", "ls-files", "--", *names], cwd=str(root),
                         capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    tracked = set(out.stdout.split())
    for n in names:
        assert n in tracked, f"{n} must be tracked like the other measured tables"

    ignore = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    for n in names:
        assert n not in [x.strip() for x in ignore], f"{n} is in .gitignore"

    import runtime_tripwire as trip
    assert not (set(names) & set(trip.RUNTIME_FILES)), \
        "a measured table is listed as runtime state in runtime_tripwire"
    hooks = (root / "test_hooks_ignore_runtime_state.py").read_text(
        encoding="utf-8")
    for n in names:
        assert n not in hooks, \
            f"{n} is named in the hook guard's runtime literals"


def test_the_picker_owns_no_selection_or_capacity_logic():
    """REGRESSION: one selection path. The picker must not grow a second one."""
    import inspect

    import _champion_pick as cp
    import _timing_tournament as tt

    src = inspect.getsource(cp)
    for forbidden in ("def coin_gate", "def _fill", "def _key",
                      "def select_champion", "def capacity_response"):
        assert forbidden not in src, f"{forbidden} is duplicated in the picker"
    assert "fillable(" not in src, "the picker re-derives capacity itself"
    assert "len(keep)" not in src and "capacity_ok =" not in src
    assert cp.tt.select_champion is tt.select_champion
    assert cp.tt.capacity_response is tt.capacity_response


def test_the_picker_writes_the_champion_select_champion_returns(
        tmp_path, monkeypatch):
    """The picker's champion is the tournament's output, not a re-computation.

    A spy wraps the REAL `select_champion` and hands back a different champion
    than the real pick would make. If the picker recomputes anything, the file
    it writes disagrees with the spy and this fails.
    """
    import json as _json

    import _champion_pick as cp

    first = _qualifying_row()
    first["id"] = "PROBE.first.buy"
    first["timing_score"] = 0.90
    second = _qualifying_row()
    second["id"] = "PROBE.second.buy"
    second["timing_score"] = 0.10
    art = {"finalists": [first, second], "stage1_rows": [first, second],
           "champion": None, "ordering": {}}
    art_path = tmp_path / "artifact.json"
    art_path.write_text(_json.dumps(art), encoding="utf-8")
    monkeypatch.setattr(cp, "ART", art_path)
    monkeypatch.setattr(cp, "OUT", tmp_path / "champions.json")

    calls = []
    real = cp.tt.select_champion

    def spy(ext, rows, _real=real):
        calls.append((len(ext), len(rows)))
        out = _real(ext, rows)
        out["champion"] = second          # deliberately NOT the real pick
        out["status"] = "promoted"
        return out

    monkeypatch.setattr(cp.tt, "select_champion", spy)
    assert cp.main() == 0
    assert calls == [(2, 2)], "the picker did not call the tournament's selector"
    written = _json.loads((tmp_path / "champions.json").read_text(encoding="utf-8"))
    assert written["champion_status"] == "promoted"
    assert written["champion"]["config"]["id"] == "PROBE.second.buy"


def test_the_promotion_threshold_is_untouched_by_the_probe():
    """The probe never lowers the bar: TARGET is still the contest's 12%."""
    import _timing_tournament as tt

    assert tt.TARGET == 12.0
    assert tt.CHAMPION_ENTRY_Q == 0.5 and tt.CHAMPION_EXIT_Q == 0.5


def test_the_tournament_rule_needs_as_many_coins_as_slots(tmp_path):
    """Seven coins can take $2,500: a 4-slot book is fundable, a 10-slot is not."""
    import _timing_tournament as tt

    p = str(tmp_path / "ch.json")
    m = {f"C{i}": _m(300_000.0) for i in range(7)}
    chist.observe(m, day="2026-11-16", path=p)
    t = chist.observe(m, day="2026-11-17", path=p)
    keep, _ = chist.fillable(sorted(m), 2_500.0, t)
    assert len(keep) == 7
    assert tt.coin_ok({"coin_response": {"capacity_ok": len(keep) >= 4}})
    assert not tt.coin_ok({"coin_response": {"capacity_ok": len(keep) >= 10}})
    assert not tt.coin_ok({})
