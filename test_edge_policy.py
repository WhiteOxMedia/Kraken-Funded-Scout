"""Tests for the full-policy replay and its unchanged promotion gate."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import edge_engine as ee
import edge_policy as ep


def _policy(side="long", size=1.0):
    return ep.Policy("x", "x", side, "trend", "all", "fixed", size,
                     "any", "any", lambda f: True)


def _one_per_window(net: float, n: int = 12):
    """n serial chained trades in each of the three contest windows."""
    out = []
    for start in ("2026-09-01", "2026-09-20", "2026-10-20"):
        base = pd.Timestamp(start, tz="UTC")
        for k in range(n):
            ts = base + pd.Timedelta(hours=2 * k)
            out.append((
                {"ts": ts, "pair": "A", "i": 0,
                 "f": {}, "entries": ["trend"]},
                {"exit_ts": ts + pd.Timedelta(hours=1), "net": net},
            ))
    return out


def test_three_clean_profitable_windows_promote_a_long_policy():
    ch = ep.chain_result(_policy(size=0.09), _one_per_window(1.0))
    assert ch["passes"] == 3 and ch["died"] is False
    assert ch["status"] == "promoted"
    assert ch["contest_passes"] == 0
    assert ch["trades"] == 36 and ch["win_rate"] == 1.0
    assert ch["max_drawdown_pct"] == 0.0
    assert ch["death_window"] is None


def test_contest_target_is_reported_but_a_tiny_sample_never_promotes():
    ch = ep.chain_result(_policy(size=0.09),
                         _one_per_window(50.0, n=3))
    assert ch["contest_passes"] == 3
    assert ch["passes"] == 0
    assert ch["status"] == "rejected"
    assert "below" in ch["status_reason"]


def test_the_policy_surface_has_no_short_policies():
    assert {p.side for p in ep.all_policies()} == {"long"}


def test_a_death_rejects_even_a_later_pass():
    selected = _one_per_window(1.0)
    selected[12] = (selected[12][0],
                    {"exit_ts": selected[12][0]["ts"] + pd.Timedelta(hours=1),
                     "net": -5.0})
    for k in range(12, 24):
        selected[k] = (selected[k][0],
                       {"exit_ts": selected[k][0]["ts"] + pd.Timedelta(hours=1),
                        "net": -5.0})
    ch = ep.chain_result(_policy(size=0.09), selected)
    assert ch["died"] is True
    assert ch["status"] == "rejected"
    assert ch["death_window"]


def test_two_profitable_windows_are_watch_not_promoted():
    selected = _one_per_window(1.0)[:24]
    ch = ep.chain_result(_policy(size=0.09), selected)
    assert ch["passes"] == 2
    assert ch["status"] == "watch"


# ── the rotation-aligned "us" gate (Nov 16) ─────────────────────────────

def _features_df(start: str, n: int = 260) -> pd.DataFrame:
    idx = pd.date_range(f"{start} 00:00", periods=n, freq="h", tz="UTC")
    close = pd.Series(100.0 + np.arange(n) * 0.1, index=idx)
    return pd.DataFrame({"open": close, "high": close + 0.2,
                         "low": close - 0.2, "close": close,
                         "volume": 1.0}, index=idx)


def _features_at(df: pd.DataFrame, ts) -> dict:
    fa = ee.frame_features(df, ee.atr14(df))
    ranks = {k: pd.Series(0.0, index=df.index)
             for k in ("btc24", "breadth", "xs24", "xs72", "xs168")}
    stamp = pd.Timestamp(ts)
    i = int(df.index.get_loc(stamp if stamp.tz else stamp.tz_localize("UTC")))
    return ee.features_at(fa, ranks, df.index[i], i)


def test_the_rotation_is_the_denver_local_4h_block_not_the_utc_hour():
    aug, nov = _features_df("2026-08-20"), _features_df("2026-11-20")
    # 11:00 UTC is 05:00 MDT in August and 04:00 MST in November: the same
    # rotation, despite the same UTC hour
    assert _features_at(aug, "2026-08-20 11:00")["rot"] == 1
    assert _features_at(nov, "2026-11-20 11:00")["rot"] == 1
    # 04:00 UTC is 22:00 / 21:00 local -> the 20-24 rotation in both seasons
    assert _features_at(aug, "2026-08-20 04:00")["rot"] == 5
    assert _features_at(nov, "2026-11-20 04:00")["rot"] == 5


def test_the_us_gate_admits_exactly_the_rotations_the_old_utc_window_reached():
    rule = ep.hour_rule("us")
    reached = set()
    for month in (8, 11):
        for h in range(24):
            ts = pd.Timestamp(f"2026-{month:02d}-20 {h:02d}:00", tz="UTC")
            if 13 <= ts.hour < 24:          # the retired gate
                reached.add(int(ts.tz_convert("America/Denver").hour) // 4)
    assert reached == set(ep.US_ROTS) == {1, 2, 3, 4}
    # the new gate admits every hour of those rotations and nothing else
    for month in (8, 11):
        for h in range(24):
            ts = pd.Timestamp(f"2026-{month:02d}-20 {h:02d}:00", tz="UTC")
            rot = int(ts.tz_convert("America/Denver").hour) // 4
            assert rule({"rot": rot}) is (rot in reached)


def test_the_us_gate_never_admits_00_04_or_20_24_whatever_the_utc_hour():
    rule = ep.hour_rule("us")
    for rot in (0, 5):
        assert rule({"rot": rot, "hour": 14}) is False
    for rot in ep.US_ROTS:
        assert rule({"rot": rot, "hour": 3}) is True


def test_a_feature_dict_without_a_rotation_fails_closed():
    assert ep.hour_rule("us")({}) is False
    assert ep.hour_rule("us")({"hour": 14}) is False
    assert ep.hour_rule("any")({}) is True


# ── "us_local": the control arm (the retired gate, kept verbatim) ────────

def _local_hour(day: str, h: int) -> int:
    return pd.Timestamp(f"{day} {h:02d}:00", tz="UTC") \
        .tz_convert("America/Denver").hour


def test_us_local_is_the_retired_window_and_drifts_with_dst():
    rule = ep.hour_rule("us_local")
    for month in (8, 11):
        day = f"2026-{month:02d}-20"
        df = _features_df(day)
        admitted = [h for h in range(24)
                    if rule(_features_at(df, f"{day} {h:02d}:00"))]
        assert admitted == list(range(13, 24))
        local = sorted({_local_hour(day, h) for h in admitted})
        # 07:00-18:00 under MDT in August, 06:00-17:00 under MST in November
        assert local == (list(range(7, 18)) if month == 8
                         else list(range(6, 17)))


def test_us_local_is_a_strict_subset_of_the_rotation_gate():
    rot, loc = ep.hour_rule("us"), ep.hour_rule("us_local")
    for month in (8, 11):
        df = _features_df(f"2026-{month:02d}-20")
        for h in range(24):
            f = _features_at(df, f"2026-{month:02d}-20 {h:02d}:00")
            if loc(f):
                assert rot(f), (month, h)


def test_us_local_is_loadable_but_never_a_search_candidate():
    spec = {"side": "long", "entry": "dip", "regime": "trend",
            "exit": "chandelier", "size_frac": 0.06, "vol_gate": "any",
            "hour_gate": "us_local"}
    pol = ep.policy_from_spec(spec)
    assert pol is not None and pol.id.endswith(".us_local")
    surface = ep.all_policies()
    assert len(surface) == 1728
    assert set(ep.SEARCH_HOUR_KEYS) < set(ep.HOUR_KEYS)
    assert not any(p.hour_gate == "us_local" for p in surface)
    # a row without an hour cannot slip through a selection path
    assert ep._safe(ep.hour_rule("us_local"), {}) is False


# ── fill timing: the signal bar's close vs the next bar's open ───────────

def _fill_frame(n: int = 40) -> pd.DataFrame:
    idx = pd.date_range("2026-09-01 00:00", periods=n, freq="h", tz="UTC")
    close = pd.Series(100.0 + np.arange(n) * 0.01, index=idx)
    op = close.shift(1).fillna(100.0)
    op.iloc[31] = float(close.iloc[30]) * 1.01     # the fill bar gaps up
    hi = pd.concat([op, close], axis=1).max(axis=1) + 0.05
    lo = pd.concat([op, close], axis=1).min(axis=1) - 0.05
    return pd.DataFrame({"open": op, "high": hi, "low": lo,
                         "close": close, "volume": 1.0})


def _fill_cfg():
    return type("Cfg", (), {"atr_multiplier": 1.5, "risk_reward_ratio": 2.5})()


def test_next_open_fills_at_the_bar_after_the_signal():
    df = _fill_frame()
    pairs = {"A": df}
    a = ep.Resolver(pairs, _fill_cfg()).resolve("A", 30, "long", "fixed")
    b = ep.Resolver(pairs, _fill_cfg(), fill="next_open").resolve(
        "A", 30, "long", "fixed")
    assert a["entry"] == float(df["close"].iloc[30])
    assert b["entry"] == float(df["open"].iloc[31])
    assert b["stop"] < b["entry"] < b["tp"]
    # the gap up is paid for, not assumed away
    assert b["net"] < a["net"]


def test_the_fill_modes_do_not_share_a_cached_exit():
    df = _fill_frame()
    pairs = {"A": df}
    rows = [{"ts": df.index[30], "pair": "A", "i": 30, "f": {},
             "entries": ["trend"]}]
    pol = ep.make_policy("long", "trend", "all", "fixed", 0.06, "any",
                         "any")
    a = ep.selected_for(pol, rows, ep.Resolver(pairs, _fill_cfg()))[0][1]
    b = ep.selected_for(pol, rows, ep.Resolver(pairs, _fill_cfg(),
                                              fill="next_open"))[0][1]
    assert a["entry"] != b["entry"]


def test_an_unknown_fill_mode_is_refused():
    with pytest.raises(ValueError):
        ep.Resolver({"A": _fill_frame()}, _fill_cfg(), fill="midpoint")
