"""Checks for go_big_week: "about once a week" is a calendar, not a hope.

  1. an empty ledger allows the first all-in
  2. a fire 3 days ago refuses, and says how long the slot is shut
  3. a fire 8 days ago allows it again
  4. an open all-in refuses a second one (one at a time)
  5. the same pair cannot take the next week's slot immediately
  6. note_fire/note_exit round-trip through the state file, atomically
  7. a corrupt state file reads as "never fired" (fail toward ordinary sizing)
  8. contest_account.go_big_supported enforces the same calendar, and keeps
     the pre-Nov-18 behavior when a caller has no timestamps
"""
from __future__ import annotations

import json
import os
import time

import pytest

import contest_account as ca
import go_big_week as gw

DAY = 86400.0
# anchored on the real clock: `load_state` self-heals an open record using
# time.time(), so a fixture far in the future would never look stale
NOW = float(int(time.time()))


def _buy(**kw):
    """A GO BIG call that passes every non-calendar gate."""
    args = dict(action="buy", tide_state="RISING", tide_pct=62.0,
                carry_usd=10_000.0, balance=10_000.0, conviction=91.0)
    args.update(kw)
    return args


def test_empty_ledger_allows_the_first_all_in():
    ok, why = gw.can_fire(now=NOW, state=gw._blank())
    assert ok, why
    assert "open" in why


def test_three_days_ago_refuses_and_names_the_wait():
    s = gw._blank()
    s["last_fire_ts"] = NOW - 3 * DAY
    ok, why = gw.can_fire(now=NOW, state=s, pair="ETH/USD")
    assert not ok
    assert "4.0d" in why


def test_eight_days_ago_allows_it_again():
    s = gw._blank()
    s["last_fire_ts"] = NOW - 8 * DAY
    ok, why = gw.can_fire(now=NOW, state=s, pair="ETH/USD")
    assert ok, why


def test_an_open_all_in_refuses_a_second():
    s = gw.note_fire("SOL/USD", 10_000.0, now=NOW - 30 * DAY,
                     state=gw._blank())
    ok, why = gw.can_fire(now=NOW, state=s)
    assert not ok
    assert "open on SOL/USD" in why


def test_the_same_pair_waits_the_week_after_its_fire():
    s = gw._blank()
    s["last_fire_ts"] = NOW - 8 * DAY
    s["fires"] = [{"ts": NOW - 8 * DAY, "pair": "BTC/USD", "usd": 10_000.0}]
    ok, why = gw.can_fire(now=NOW, state=s, pair="BTC/USD")
    assert not ok
    assert "BTC/USD" in why
    ok2, _ = gw.can_fire(now=NOW, state=s, pair="ETH/USD")
    assert ok2


def test_fire_and_exit_round_trip_through_the_file(tmp_path):
    p = str(tmp_path / "week.json")
    gw.note_fire("XRP/USD", 9_876.5, why="BUY 91% in a RISING tide",
                 now=NOW, path=p)
    s = gw.load_state(p)
    assert s["open"]["pair"] == "XRP/USD"
    assert gw.last_fire_ts(p) == NOW
    gw.note_exit(pnl_usd=312.25, net_pct=3.16, now=NOW + 2 * DAY, path=p)
    s = gw.load_state(p)
    assert s["open"] is None
    assert s["closed"][0]["pnl_usd"] == 312.25
    assert s["closed"][0]["held_days"] == pytest.approx(2.0)
    assert not os.path.exists(p + ".tmp")


def test_reconcile_closes_an_open_all_in_when_the_book_is_flat(tmp_path):
    """The exit monitor may sell in a module that never heard of this file."""
    p = str(tmp_path / "week.json")
    gw.note_fire("ETH/USD", 10_000.0, now=NOW, path=p)
    assert gw.load_state(p)["open"] is not None
    s = gw.reconcile(book_open=False, now=NOW + 3600.0, path=p)
    assert s["open"] is None
    assert gw.load_state(p)["open"] is None
    assert gw.can_fire(now=NOW + 3600.0 + 7 * DAY, state=gw.load_state(p))[0]


def test_a_write_that_never_became_a_position_does_not_spend_the_week(tmp_path):
    """The bridge write is an intent; a refused pending signal costs nothing."""
    p = str(tmp_path / "week.json")
    gw.note_fire("ETH/USD", 10_000.0, now=NOW, path=p)
    s = gw.reconcile(book_open=False, now=NOW + 60.0, path=p)
    assert s["open"] is None
    assert s["last_fire_ts"] is None
    assert s["fires"] == []
    assert gw.can_fire(now=NOW + 61.0, state=s)[0]


def test_reconcile_leaves_a_live_all_in_open(tmp_path):
    p = str(tmp_path / "week.json")
    gw.note_fire("ETH/USD", 10_000.0, now=NOW, path=p)
    s = gw.reconcile(book_open=True, now=NOW + 3600.0, path=p)
    assert s["open"]["pair"] == "ETH/USD"


def test_a_stale_open_cannot_block_forever(tmp_path):
    p = str(tmp_path / "week.json")
    gw.note_fire("ETH/USD", 10_000.0, now=NOW - 20 * DAY, path=p)
    s = gw.load_state(p)                      # self-heals on read
    assert s["open"] is None
    assert s["closed"][0]["stale"] is True
    assert gw.can_fire(now=NOW, state=s)[0]


def test_a_corrupt_ledger_reads_as_never_fired(tmp_path):
    p = tmp_path / "week.json"
    p.write_text("{not json", encoding="utf-8")
    assert gw.load_state(str(p)) == gw._blank()
    ok, _ = gw.can_fire(now=NOW, state=gw.load_state(str(p)))
    assert ok


def test_go_big_supported_enforces_the_same_calendar():
    ok, why = ca.go_big_supported(**_buy(), now_ts=NOW,
                                  last_fire_ts=NOW - 3 * DAY)
    assert not ok
    assert "weekly GO BIG slot opens" in why
    ok2, why2 = ca.go_big_supported(**_buy(), now_ts=NOW,
                                    last_fire_ts=NOW - 8 * DAY)
    assert ok2, why2


def test_no_timestamps_keeps_the_pre_nov18_behavior():
    ok, why = ca.go_big_supported(**_buy())
    assert ok, why
    assert ca.go_big_week_ok(None, NOW - DAY) == (True,
                                                  "no GO BIG on record — "
                                                  "the weekly slot is open")


def test_the_plan_still_sizes_the_whole_balance():
    """The calendar must not have changed the money: it is still 100%."""
    plan = ca.go_big_plan(10_400.0, 10_000.0, supported=True)
    assert plan["ok"] is True
    assert plan["spend_usd"] == 10_400.0
    assert plan["go_big"] is True


def test_status_reports_the_next_slot(tmp_path):
    p = str(tmp_path / "week.json")
    gw.note_fire("ETH/USD", 10_000.0, now=NOW, path=p)
    st = gw.status(now=NOW + DAY, path=p)
    assert st["days_since"] == pytest.approx(1.0)
    assert st["next_eligible_ts"] == NOW + 7 * DAY
    assert st["open"]["pair"] == "ETH/USD"
    assert json.dumps(st)  # JSON-safe for the console
