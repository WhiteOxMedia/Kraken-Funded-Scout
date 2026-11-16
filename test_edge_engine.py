"""Tests for the edge engine's evidence rules, not its profitability."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import edge_engine as ee


def _frame(n=320):
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 0.8, n))
    open_ = close + rng.normal(0, 0.2, n)
    high = np.maximum(open_, close) + rng.uniform(0, 0.6, n)
    low = np.minimum(open_, close) - rng.uniform(0, 0.6, n)
    idx = pd.date_range("2026-08-01", periods=n, freq="h", tz="UTC")
    df = pd.DataFrame({"open": open_, "high": high, "low": low,
                       "close": close, "volume": rng.uniform(100, 1000, n)},
                      index=idx)
    return df


def test_features_at_a_bar_do_not_change_when_future_bars_change():
    df = _frame()
    atr = ee.atr14(df)
    before = ee.frame_features(df, atr)
    i = 250
    df2 = df.copy()
    df2.iloc[i + 1:, :] = df2.iloc[i + 1:, :] * 3.0
    after = ee.frame_features(df2, ee.atr14(df2))
    for key in before:
        a, b = before[key][i], after[key][i]
        if np.isfinite(a) and np.isfinite(b):
            assert abs(a - b) < 1e-9, key
        else:
            assert not np.isfinite(a) and not np.isfinite(b), key


def test_split_rows_is_time_exclusive_at_the_end_boundary():
    rows = [
        {"ts": pd.Timestamp("2026-08-17T00:00Z"), "net": 1.0},
        {"ts": pd.Timestamp("2026-09-18T00:00Z"), "net": 2.0},
        {"ts": pd.Timestamp("2026-10-19T00:00Z"), "net": 3.0},
        {"ts": pd.Timestamp("2026-11-10T00:00Z"), "net": 4.0},
    ]
    w = ee.split_rows(rows)
    assert [len(x) for x in w] == [1, 1, 1]
    assert w[0][0]["net"] == 1.0
    assert w[1][0]["net"] == 2.0
    assert w[2][0]["net"] == 3.0


def test_stats_require_a_positive_lower_bound_for_robustness():
    s = ee._stats([1.0] * 30)
    assert s.n == 30 and s.mean == 1.0 and s.lower is not None
    assert s.lower == 1.0 and s.positive
    noisy = ee._stats([1.0, -1.0] * 15)
    assert not noisy.positive


def test_classify_chain_only_promotes_three_clean_passes():
    assert ee.classify_chain(3, False, [])[0] == "promoted"
    assert ee.classify_chain(2, False, [])[0] == "watch"
    assert ee.classify_chain(3, True, [])[0] == "rejected"
    assert ee.classify_chain(0, False, [])[0] == "rejected"


def test_candidate_ids_are_unique_and_controls_exist():
    specs = ee.candidate_specs()
    ids = [c.id for c in specs]
    assert len(ids) == len(set(ids))
    assert "market.all" in ids
    assert "entry.trend" in ids
    assert "risk_on.break20" in ids


def test_atr_rank_uses_only_the_rows_at_that_timestamp():
    rows = []
    for p, v in (("A", 1.0), ("B", 2.0), ("C", 3.0)):
        rows.append({"ts": pd.Timestamp("2026-09-01T00:00Z"),
                     "f": {"atr_pct": v}})
    ee.fill_atr_rank(rows)
    assert [round(r["f"]["atr_rank"], 3) for r in rows] == [0.167, 0.5, 0.833]


def test_registry_promotes_and_demotes_from_evidence(tmp_path: Path):
    path = tmp_path / "edge_registry.json"
    report = {
        "dataset": {"pairs": 1},
        "candidates": [{
            "id": "a.b", "label": "a + b", "status": "promoted",
            "status_reason": "all three", "chain": {"final": 1200.0},
            "w1": {}, "w2": {}, "w3": {},
        }],
    }
    doc = ee.update_registry(report, path)
    assert doc["active"] == "a.b"
    assert json.loads(path.read_text())["active"] == "a.b"
    report["candidates"][0].update(
        status="rejected", status_reason="death line",
        chain={"final": 900.0})
    doc = ee.update_registry(report, path)
    assert doc["active"] is None
