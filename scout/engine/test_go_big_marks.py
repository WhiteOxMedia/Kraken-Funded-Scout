"""GO BIG's marks are a share of the contest start, not fixed dollars.

The validated 100 / 500 is what 1% / 5% of $10,000 happens to be.  These pins
hold the shape at every tier: the stop is a third of the death line and the
take is about 40% of the target, whatever the start balance is.
"""
from __future__ import annotations

import pytest

import contest_account as ca

STARTS = [1_000, 5_000, 10_000, 25_000, 50_000, 100_000]


def test_the_validated_marks_are_the_10k_tier():
    assert ca.go_big_marks(10_000) == (100.0, 500.0)


@pytest.mark.parametrize("start", STARTS)
def test_stop_is_a_third_of_the_death_line(start):
    stop, _ = ca.go_big_marks(start)
    death = start * ca.MAX_LOSS_PCT / 100.0
    assert stop == pytest.approx(death / 3.0, rel=0.005)


@pytest.mark.parametrize("start", STARTS)
def test_take_is_about_40_percent_of_the_target(start):
    _, take = ca.go_big_marks(start)
    target = start * ca.PROFIT_TARGET_PCT / 100.0
    assert 0.38 <= take / target <= 0.45


@pytest.mark.parametrize("start", STARTS)
def test_plan_derives_the_marks_when_none_are_passed(start):
    plan = ca.go_big_plan(float(start), float(start), supported=True)
    stop, take = ca.go_big_marks(start)
    assert plan["ok"] is True
    assert plan["stop_usd"] == pytest.approx(stop)
    assert plan["take_usd"] == pytest.approx(take)


def test_explicit_marks_still_override_and_zero_take_means_momentum():
    plan = ca.go_big_plan(10_000, 10_000, supported=True,
                          stop_usd=40.0, take_usd=0.0)
    assert plan["ok"] is True
    assert plan["stop_usd"] == 40.0
    assert plan["take_usd"] == 0.0


def test_no_absolute_dollar_constants_are_left_behind():
    """One source of truth: the old fixed 50/20 must not survive as an API."""
    assert not hasattr(ca, "GO_BIG_STOP_USD")
    assert not hasattr(ca, "GO_BIG_TAKE_USD")
