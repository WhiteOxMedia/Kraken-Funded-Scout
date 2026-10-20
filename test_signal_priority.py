"""
test_signal_priority.py — tests for signal_priority.py growth-rate ranking.

Covers:
- Data model + update
- Insufficient history returns 0
- Volatility formula
- Basic liquidity
- Enhanced liquidity with spreads
- Priority score combination
- Ranking order
- Top / Mid / Low classification
"""

import pytest
from signal_priority import (
    N,
    W_LIQUIDITY,
    W_VOLATILITY,
    Signal,
    build_signal,
    batch_update,
    classify_movers,
    compute_liquidity,
    compute_priority,
    compute_volatility,
    rank_signals,
)


# ---------------------------------------------------------------------------
# Data model / update
# ---------------------------------------------------------------------------

def test_signal_default():
    s = Signal(signal_id="s1")
    assert s.signal_id == "s1"
    assert s.prices == []
    assert s.volumes == []
    assert s.spreads == []
    assert s.timestamps == []


def test_signal_update_appends():
    s = Signal(signal_id="s1")
    s.update(price=10.0, volume=5.0, spread=0.1, timestamp="t1")
    s.update(price=11.0, volume=6.0, timestamp="t2")
    assert s.prices == [10.0, 11.0]
    assert s.volumes == [5.0, 6.0]
    assert s.spreads == [0.1, None]
    assert s.timestamps == ["t1", "t2"]


def test_spread_optional():
    s = Signal(signal_id="s1")
    s.update(price=1.0, volume=1.0)
    assert s.spreads == [None]


# ---------------------------------------------------------------------------
# Insufficient history
# ---------------------------------------------------------------------------

def test_compute_priority_returns_zero_when_too_few_prices():
    # Priority is zero when volatility OR liquidity cannot be computed.
    # With only 2 prices (< N) we have no volatility, but we do have some
    # volume, so the liquidity path still contributes. Use an empty volume
    # history instead.
    s = build_signal("s1", prices=[10.0, 11.0], volumes=[])
    assert compute_priority(s) == 0.0


def test_compute_volatility_returns_zero_when_too_few_prices():
    s = build_signal("s1", prices=[10.0, 11.0])
    assert compute_volatility(s) == 0.0

    s.prices.append(12.0)
    assert compute_volatility(s) == 0.0

    # Fill up to N
    while len(s.prices) < N:
        s.prices.append(s.prices[-1] + 1.0)
    assert len(s.prices) >= N
    assert compute_volatility(s) > 0


def test_compute_volatility_zero_first_price():
    s = Signal(signal_id="s1")
    for _ in range(N):
        s.prices.append(0.0)
        s.volumes.append(1.0)
    s.prices[-1] = 5.0
    assert compute_volatility(s) == 0.0


# ---------------------------------------------------------------------------
# Volatility formula
# ---------------------------------------------------------------------------

def test_volatility_formula():
    # prices[-N] = P_first, prices[-1] = P_last
    s = Signal(signal_id="s1")
    s.prices = [100.0] * N
    s.prices[-1] = 120.0
    assert compute_volatility(s) == pytest.approx(abs(120.0 - 100.0) / 100.0)
    assert compute_volatility(s) == pytest.approx(0.2)


def test_volatility_recent_window_only():
    s = Signal(signal_id="s1")
    # older prices don't count
    s.prices = list(range(1, 50))
    s.prices[-1] = 200.0
    p_first = s.prices[-N]
    p_last = s.prices[-1]
    expected = abs(p_last - p_first) / p_first
    assert compute_volatility(s) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Liquidity
# ---------------------------------------------------------------------------

def test_basic_liquidity_is_sum_of_recent_volumes():
    s = Signal(signal_id="s1")
    s.volumes = [10.0] * (N + 5)
    s.prices = [10.0] * (N + 5)
    assert compute_liquidity(s) == pytest.approx(10.0 * N)


def test_basic_liquidity_empty():
    s = Signal(signal_id="s1")
    assert compute_liquidity(s) == 0.0


def test_enhanced_liquidity_with_spreads():
    s = Signal(signal_id="s1")
    s.prices = [10.0] * (N + 2)
    s.volumes = [100.0] * (N + 2)
    s.spreads = [2.0] * (N + 2)
    base_liq = 100.0 * N
    expected = base_liq / 2.0
    assert compute_liquidity(s) == pytest.approx(expected)


def test_enhanced_liquidity_avg_spread_zero_skipped():
    s = Signal(signal_id="s1")
    s.prices = [10.0] * (N + 2)
    s.volumes = [100.0] * (N + 2)
    s.spreads = [0.0] * (N + 2)
    # avg_spread == 0 => enhanced path skipped
    assert compute_liquidity(s) == pytest.approx(100.0 * N)


def test_enhanced_liquidity_none_spreads_ignored():
    s = Signal(signal_id="s1")
    s.prices = [10.0] * (N + 2)
    s.volumes = [100.0] * (N + 2)
    s.spreads = [None] * (N + 2)
    assert compute_liquidity(s) == pytest.approx(100.0 * N)


def test_liquidity_uses_last_n_volumes_only():
    s = Signal(signal_id="s1")
    s.prices = [10.0] * (N + 5)
    s.volumes = [1.0] * 5 + [100.0] * N
    assert compute_liquidity(s) == pytest.approx(100.0 * N)


# ---------------------------------------------------------------------------
# Priority score combination
# ---------------------------------------------------------------------------

def test_priority_combines_weighted_volatility_and_liquidity():
    s = Signal(signal_id="s1")
    s.prices = [100.0] * N
    s.prices[-1] = 120.0
    s.volumes = [1000.0] * N
    s.spreads = [2.0] * N

    vol = compute_volatility(s)
    liq = compute_liquidity(s)
    expected = W_VOLATILITY * vol + W_LIQUIDITY * liq
    assert compute_priority(s) == pytest.approx(expected)


def test_weights_used():
    assert W_VOLATILITY == 0.7
    assert W_LIQUIDITY == 0.3
    assert abs((W_VOLATILITY + W_LIQUIDITY) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def test_rank_signals_sorts_descending():
    a = build_signal("a", prices=[100.0] * N, volumes=[1.0] * N)
    b = build_signal("b", prices=[100.0] * N, volumes=[100.0] * N)
    ranked = rank_signals([a, b])
    assert ranked[0][0].signal_id == "b"
    assert ranked[1][0].signal_id == "a"


def test_rank_signals_empty():
    assert rank_signals([]) == []


def test_rank_signals_preserves_signal_identity():
    s = build_signal("x", prices=[100.0] * N, volumes=[10.0] * N)
    ranked = rank_signals([s])
    assert ranked[0][0] is s


def test_rank_ties():
    a = build_signal("a", prices=[100.0] * N, volumes=[10.0] * N)
    b = build_signal("b", prices=[100.0] * N, volumes=[10.0] * N)
    ranked = rank_signals([a, b])
    assert ranked[0][1] == pytest.approx(ranked[1][1])


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def test_classify_movers_splits_three_buckets():
    signals = []
    base = 100.0
    for i in range(9):
        sig = build_signal(
            f"s{i}",
            prices=[base] * N,
            volumes=[10.0 + i] * N,
        )
        signals.append(sig)

    scored = rank_signals(signals)
    buckets = classify_movers(scored)

    assert "top" in buckets
    assert "mid" in buckets
    assert "low" in buckets
    assert len(buckets["top"]) + len(buckets["mid"]) + len(buckets["low"]) == 9


def test_classify_empty():
    assert classify_movers([]) == {"top": [], "mid": [], "low": []}


def test_top_has_highest_priority():
    signals = []
    for i in range(6):
        sig = build_signal(
            f"s{i}",
            prices=[100.0] * N,
            volumes=[10.0 * (i + 1)] * N,
        )
        signals.append(sig)
    scored = rank_signals(signals)
    buckets = classify_movers(scored)
    top_ids = [sid.signal_id for sid, _ in buckets["top"]]
    # Top bucket should contain the signals with the highest scores.
    scored_by_id = {s.signal_id: score for s, score in scored}
    by_priority = sorted(top_ids, key=lambda sid: scored_by_id[sid], reverse=True)
    assert top_ids == by_priority


def test_classify_movers_empty():
    assert classify_movers([]) == {"top": [], "mid": [], "low": []}


def test_rank_signals_single_element_order_preserved():
    s = build_signal("only", prices=[100.0] * N, volumes=[5.0] * N)
    ranked = rank_signals([s])
    assert len(ranked) == 1
    assert ranked[0][0] is s
    assert ranked[0][1] == pytest.approx(compute_priority(s))


def test_classify_top_ids_are_highest_priority():
    signals = []
    for i in range(6):
        sig = build_signal(
            f"s{i}",
            prices=[100.0] * N,
            volumes=[10.0 * (i + 1)] * N,
        )
        signals.append(sig)
    scored = rank_signals(signals)
    buckets = classify_movers(scored)
    top_ids = {sid.signal_id for sid, _ in buckets["top"]}
    # Top bucket should be exactly the highest-priority signals.
    scored_by_id = {s.signal_id: score for s, score in scored}
    all_ids_sorted = sorted(scored_by_id, key=scored_by_id.get, reverse=True)
    expected_top = set(all_ids_sorted[: len(buckets["top"])])
    assert top_ids == expected_top


# ---------------------------------------------------------------------------
# build_signal / batch_update helpers
# ---------------------------------------------------------------------------

def test_build_signal_from_arrays():
    s = build_signal(
        "s1",
        prices=[1.0, 2.0, 3.0],
        volumes=[10.0, 20.0, 30.0],
        spreads=[0.1, 0.2, 0.3],
        timestamps=["t1", "t2", "t3"],
    )
    assert s.signal_id == "s1"
    assert s.prices == [1.0, 2.0, 3.0]
    assert s.spreads == [0.1, 0.2, 0.3]


def test_batch_update_adds_new_signals():
    signals = [build_signal("a", prices=[100.0] * N, volumes=[10.0] * N)]
    batch_update(
        signals,
        [
            {"signal_id": "a", "price": 101.0, "volume": 20.0, "spread": 0.5, "timestamp": "t"},
            {"signal_id": "b", "price": 200.0, "volume": 5.0},
        ],
    )
    assert len(signals) == 2
    a = next(s for s in signals if s.signal_id == "a")
    b = next(s for s in signals if s.signal_id == "b")
    assert a.prices[-1] == 101.0
    assert a.spreads[-1] == 0.5
    assert b.spreads[-1] is None


def test_batch_update_missing_signal_id_ignored_safely():
    signals: list[Signal] = []
    batch_update(
        signals,
        [
            {"signal_id": "s1", "price": 1.0, "volume": 1.0},
            {"signal_id": "s1", "price": 2.0, "volume": 2.0},
        ],
    )
    assert len(signals) == 1
    assert signals[0].prices == [1.0, 2.0]
