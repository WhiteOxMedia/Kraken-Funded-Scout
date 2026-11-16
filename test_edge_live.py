"""Tests for the registry -> live entry gate.

These are behavioural tests, not profitability tests: no promoted policy must
be a no-op, and a promoted policy must be the only one that can authorize a
registry entry.
"""
from __future__ import annotations

import json
from pathlib import Path

import edge_live as el
import edge_policy as ep


LONG_SPEC = {
    "side": "long", "entry": "trend", "regime": "all", "exit": "fixed",
    "size_frac": 0.09, "vol_gate": "any", "hour_gate": "any",
}
LONG_F = {
    "close": 100.0, "ema21": 95.0, "ema9": 99.0, "ema_up": True,
    "above200": True, "atr": 2.0, "atr_pct": 0.02, "atr_rank": 0.5,
    "btc24": 1.0, "breadth": 0.6, "ret1": 0.1, "ret4": 0.2,
    "ret24": 1.0, "rsi": 55.0, "rsi_prev": 50.0, "vol_surge": 1.0,
    "hour": 14, "rot": 3, "range_pos": 0.5,
}


def _registry(path: Path, rows: list[dict]) -> None:
    path.write_text(json.dumps({"schema": 2, "active": rows[0]["id"]
                                if rows else None,
                                "candidates": {r["id"]: r for r in rows}}))


def _row(cid: str, status: str = "promoted", final: float = 11000.0,
         spec: dict | None = None) -> dict:
    return {"id": cid, "status": status, "label": cid,
            "spec": dict(spec or LONG_SPEC),
            "chain": {"final": final, "passes": 3, "died": False}}


def _forward(path: Path, rows: list[dict]) -> Path:
    path.write_text(json.dumps({"rows": rows}))
    return path


def _lane(cid: str, net: float = 148.29, trades: int = 2,
          wins: int = 1) -> dict:
    return {"id": cid, "net": net, "trades": trades, "wins": wins,
            "equity": 10000.0 + net}


def test_no_promoted_policy_is_an_inactive_no_op(tmp_path: Path):
    path = tmp_path / "reg.json"
    _registry(path, [])
    plan = el.live_plan("BTC/USD", features=LONG_F, registry_path=path)
    assert plan["active"] is False and plan["allow"] is True
    assert plan["size_frac"] is None

    _registry(path, [_row("r", "research")])
    plan = el.live_plan("BTC/USD", features=LONG_F, registry_path=path)
    assert plan["active"] is False and plan["allow"] is True


def test_a_promoted_policy_produces_the_intended_entry(tmp_path: Path):
    path = tmp_path / "reg.json"
    spec = dict(LONG_SPEC, size_frac=0.06)
    _registry(path, [_row("live.policy", spec=spec)])
    fwd = _forward(tmp_path / "fwd.json", [_lane("live.policy")])
    plan = el.live_plan("BTC/USD", features=LONG_F, registry_path=path,
                        account={"start_balance": 10000,
                                 "current_balance": 10050},
                        forward_path=fwd)
    assert plan["active"] is True and plan["allow"] is True
    assert plan["size_frac"] == 0.06 and plan["side"] == "long"
    assert plan["policy"] == "live.policy"
    # the paper lane's numbers travel with the decision
    assert plan["forward"]["net"] == 148.29 and plan["forward"]["trades"] == 2


def test_highest_ranked_promoted_policy_wins_deterministically(tmp_path: Path):
    path = tmp_path / "reg.json"
    _registry(path, [_row("b", final=10500), _row("a", final=12000)])
    fwd = _forward(tmp_path / "fwd.json", [_lane("a"), _lane("b")])
    plan = el.live_plan("BTC/USD", features=LONG_F, registry_path=path,
                        forward_path=fwd)
    assert plan["policy"] == "a"
    assert el.select(el._read(path))["id"] == "a"


def test_demotion_stops_policy_entries(tmp_path: Path):
    path = tmp_path / "reg.json"
    _registry(path, [_row("was-live")])
    fwd = _forward(tmp_path / "fwd.json", [_lane("was-live")])
    assert el.live_plan("BTC/USD", features=LONG_F, registry_path=path,
                        forward_path=fwd)["active"] is True
    _registry(path, [_row("was-live", "rejected")])
    plan = el.live_plan("BTC/USD", features=LONG_F, registry_path=path)
    assert plan["active"] is False and plan["allow"] is True
    assert plan["policy"] is None


def test_spot_short_is_refused_even_at_three_of_three(tmp_path: Path):
    path = tmp_path / "reg.json"
    short = dict(LONG_SPEC, side="short", entry="downtrend")
    _registry(path, [_row("short", spec=short)])
    plan = el.live_plan("BTC/USD", features=LONG_F, registry_path=path)
    assert plan["active"] is True and plan["allow"] is False
    assert "short" in plan["reason"]


def test_promoted_row_without_three_clean_passes_is_refused(tmp_path: Path):
    path = tmp_path / "reg.json"
    row = _row("stale.policy")
    row["chain"]["passes"] = 2
    _registry(path, [row])
    plan = el.live_plan("BTC/USD", features=LONG_F, registry_path=path)
    assert plan["active"] is True and plan["allow"] is False
    assert "three clean" in plan["reason"]


def test_exhausted_death_buffer_preempts_a_registry_entry(tmp_path: Path):
    path = tmp_path / "reg.json"
    _registry(path, [_row("live.policy")])
    fwd = _forward(tmp_path / "fwd.json", [_lane("live.policy")])
    plan = el.live_plan(
        "BTC/USD", features=LONG_F, registry_path=path, forward_path=fwd,
        account={"start_balance": 10000, "current_balance": 9700})
    assert plan["active"] is True and plan["allow"] is False
    assert "death-line" in plan["reason"]


def test_a_negative_paper_lane_refuses_live_routing(tmp_path: Path):
    path = tmp_path / "reg.json"
    _registry(path, [_row("bleeding.policy")])
    fwd = _forward(tmp_path / "fwd.json",
                   [_lane("bleeding.policy", net=-160.42, trades=10, wins=0)])
    plan = el.live_plan("BTC/USD", features=LONG_F, registry_path=path,
                        forward_path=fwd)
    assert plan["active"] is True and plan["allow"] is False
    assert plan["size_frac"] is None
    assert "forward paper lane is -160.42" in plan["reason"]
    assert "10 trades" in plan["reason"] and "0 wins" in plan["reason"]
    assert plan["forward"]["net"] == -160.42
    assert plan["forward"]["trades"] == 10


def test_missing_forward_evidence_refuses_live_routing(tmp_path: Path):
    path = tmp_path / "reg.json"
    _registry(path, [_row("unproven.policy")])
    # a report that exists but has no row for this policy is still no evidence
    fwd = _forward(tmp_path / "fwd.json", [_lane("some.other.policy")])
    plan = el.live_plan("BTC/USD", features=LONG_F, registry_path=path,
                        forward_path=fwd)
    assert plan["allow"] is False and plan["forward"] is None
    assert "no forward paper evidence" in plan["reason"]

    # and a lane that exists but never closed a trade is no evidence either
    fwd2 = _forward(tmp_path / "fwd2.json",
                    [{"id": "unproven.policy", "net": 0.0, "trades": 0}])
    plan2 = el.live_plan("BTC/USD", features=LONG_F, registry_path=path,
                         forward_path=fwd2)
    assert plan2["allow"] is False
    assert "no forward paper evidence" in plan2["reason"]


def test_status_surfaces_the_paper_lane_beside_the_active_policy(
        tmp_path: Path):
    path = tmp_path / "reg.json"
    _registry(path, [_row("live.policy")])
    fwd = _forward(tmp_path / "fwd.json",
                   [_lane("live.policy", net=-160.42, trades=10, wins=0)])
    st = el.status(path, fwd)
    assert st["active"] == "live.policy"
    assert st["forward"]["net"] == -160.42
    assert st["forward"]["trades"] == 10
    assert st["forward_ok"] is False
    assert "-160.42" in st["forward_refusal"]
    assert st["promoted_rows"][0]["forward"]["net"] == -160.42
    assert st["forward_bar"]["min_net"] == el.FORWARD_MIN_NET


def test_policy_surface_is_long_only_with_exits_gates_and_two_sizes():
    policies = ep.all_policies()
    assert len(policies) == 1728
    assert {p.side for p in policies} == {"long"}
    assert {p.exit_mode for p in policies} == set(ep.EXIT_MODES)
    assert {p.size_frac for p in policies} == set(ep.SIZE_FRACS)
    assert any(p.vol_gate == "low" for p in policies)
    assert any(p.hour_gate == "us" for p in policies)
    assert any(p.regime == "risk_off" for p in policies)
    assert len({p.id for p in policies}) == len(policies)


def test_spec_round_trips_without_trusting_the_id():
    spec = dict(LONG_SPEC, entry="break20", exit="chandelier", size_frac=0.06)
    pol = ep.policy_from_spec(spec)
    assert pol is not None
    assert pol.side == "long" and pol.exit_mode == "chandelier"
    assert pol.size_frac == 0.06


def test_short_spec_is_not_a_policy():
    short = dict(LONG_SPEC, side="short", entry="trend")
    assert ep.policy_from_spec(short) is None
