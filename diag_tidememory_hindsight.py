"""diag_tidememory_hindsight.py — Sep 10: grade THE PER-COIN TIDE
MEMORY's hindsight in DOLLARS.

The question (user, Sep 10): replay the funnel's real popup burials
against the extended store and report what the per-coin adjustments
would have RELEASED or BLOCKED, in dollars.

THE HONEST REPLAY (no look-ahead):
  * for every graded burial (snapshot_outcomes.jsonl, outcome from
    hindsight_scorer), the memory is REBUILT truncated BEFORE the
    event's hour — the store model and the live popup-outcome cut
    only know candles and outcomes from before that moment. A coin's
    adjustment at burial time cannot have learned from its own burial.
  * THE BINDER: an event is only replayable where the memory actually
    prices the gate. Tide-out events carry either the priced bar
    ("quick X vs personal/learned bar N" — the desktop gate) or the
    blanket text ("all ships sink together" — the BOT bridge's OUT
    wall, the two-ledgers rule: the bot never trades OUT). The memory
    shifts the priced bar; it does not touch the wall. Non-tide gates
    (deep veto, trend, climb) are not the memory's gates at all.
  * counterfactual on priced burials: real bar vs the same bar
    adjusted by the replayed memory; release when quick >= the
    adjusted bar (the gates' >=), never below the wolf floor 55.
  * dollars: position = $1055 (today's ledger — the only contest_size
    the records share) x brain_size_mult (the funnel's own ladder);
    the trade pays end_move_pct (3h close-to-close, the popup's hold
    style); mfe_pct is the sell-the-top ceiling.

Run: .venv\\Scripts\\python.exe -X utf8 diag_tidememory_hindsight.py
Writes _tidememory_hindsight.txt.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

OUT = BASE / "_tidememory_hindsight.txt"
OUTCOMES = BASE / "snapshot_outcomes.jsonl"
WOLF_FLOOR = 55.0

import tide_memory as tm          # the memory under test
import tide_learn as tl           # the store loader + tide states
import hindsight_scorer as hs     # the sizing ladder


def _rows():
    rows = []
    for line in OUTCOMES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def _parse_ts(ts):
    return datetime.fromisoformat(str(ts)).timestamp()


# ── the replayed memory (truncated, no look-ahead) ───────────────────
_store_cache = {}


def _store_adj_at(cutoff_ts: float, pair: str, state: str) -> float:
    """The per-coin store adjustment as it stood just BEFORE cutoff_ts:
    the deep fleet truncated to candles before the cutoff, tide states
    and payer edges recomputed on that truncated window."""
    hour_key = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc)\
        .replace(minute=0, second=0, microsecond=0).isoformat()
    if hour_key not in _store_cache:
        px, err = tl._load_deep_fleet()
        model = {}
        if px is not None:
            px = px[px.index.astype("int64") // 10**9 < cutoff_ts]
            try:
                import config as _cfgmod
                pp = [p for p in _cfgmod.DEFAULT_CONFIG.trading_pairs
                      if p in px.columns]
            except Exception:
                pp = []
            fpx = px[pp] if len(pp) >= tl.FLEET_MIN else px
            states = tl._tide_states(px, tl._tide_class_ref())
            ffwd = fpx.shift(-tm.HORIZON_H) / fpx - 1.0
            model = tm._measure(states, ffwd)
        _store_cache[hour_key] = model
    model = _store_cache[hour_key]
    st = (model.get(state) or {}) if state else {}
    coin = (st.get("coins") or {}).get(pair)
    return float(coin["adj"]) if coin else 0.0


_LIVE_ROWS = _rows()


def _live_pts_at(cutoff_ts: float, pair: str, state: str) -> float:
    """The live blend's points from popup outcomes recorded strictly
    before the cutoff (for these Sep 9-10 events it is mostly cold:
    the outcomes file only began Sep 9)."""
    recs = [r for r in _LIVE_ROWS
            if r.get("tide") and r.get("outcome")
            and _parse_ts(r["ts"]) < cutoff_ts]
    m = tm._live_measure(recs, now_ts=cutoff_ts)
    st = m.get(state) or {}
    lc = (st.get("coins") or {}).get(pair)
    if st.get("fleet") is None or not lc or lc.get("w", 0) <= 0:
        return 0.0
    ramp = min(1.0, lc["w"] / tm.LIVE_RAMP_W)
    return max(-tm.EDGE_MAX_PTS,
               min(tm.EDGE_MAX_PTS, lc["edge"] * ramp))


def _replayed_adj(ts, pair, state):
    """The ONE-TAX memory at event time (the live rule, truncated)."""
    store = _store_adj_at(ts, pair, state)
    return max(-tm.EDGE_MAX_PTS,
               min(tm.EDGE_MAX_PTS, store + _live_pts_at(ts, pair, state)))


# ── the binder: what actually buried this event ──────────────────────
def _binder(r):
    """(kind, bar) — 'priced' (the desktop bar from the detail text),
    'wall' (the bot bridge's blanket OUT block), or other/None."""
    gate = r.get("gate")
    if gate != "tide-out":
        return "other:" + str(gate), None
    d = str(r.get("detail", ""))
    m = re.search(r"vs (?:personal|learned) bar (\d+)", d)
    if m:
        return "priced", float(m.group(1))
    if "all ships sink" in d:
        return "wall", None
    return "other:tide-out", None


def _size(r):
    brain = r.get("brain")
    if brain is None:
        brain = r.get("quick")
    mult = hs.brain_size_mult(brain)
    return 1055.0 * mult, mult


def _usd(e, key):
    return e["size"] * e[key] / 100.0


def main():
    rows = _rows()
    events, wall = [], []
    skipped = 0
    for r in rows:
        kind, bar = _binder(r)
        quick, oc = r.get("quick"), r.get("outcome") or {}
        st = tm._parse_tide(r.get("tide"))
        if kind != "priced" or quick is None or st not in ("OUT", "FLAT"):
            if kind == "wall" and st == "OUT" and quick is not None:
                wall.append({"pair": r.get("pair"), "ts": r.get("ts"),
                             "quick": float(quick),
                             "st": st, "size": _size(r)[0],
                             "end_pct": float(oc.get("end_move_pct") or 0.0),
                             "mfe_pct": float(oc.get("mfe_pct") or 0.0),
                             "payoff": oc.get("payoff")})
            elif kind != "priced":
                skipped += 1
            continue
        ts = _parse_ts(r["ts"])
        adj = _replayed_adj(ts, str(r.get("pair")), st)
        on_bar = max(WOLF_FLOOR, bar - adj)
        released = float(quick) >= on_bar and float(quick) < bar
        size, mult = _size(r)
        events.append({"pair": r.get("pair"), "ts": r.get("ts"),
                       "state": st, "quick": float(quick), "bar": bar,
                       "adj": adj, "on_bar": on_bar, "released": released,
                       "size": size, "mult": mult,
                       "end_pct": float(oc.get("end_move_pct") or 0.0),
                       "mfe_pct": float(oc.get("mfe_pct") or 0.0),
                       "payoff": oc.get("payoff")})

    out = []
    out.append("=== THE TIDE MEMORY'S HINDSIGHT, IN DOLLARS (Sep 10) ===")
    out.append(f"graded popup-path events: {len(rows)}")
    out.append(f"  priced by the memory's gate (desktop bar): {len(events)}")
    out.append(f"  behind the BOT bridge's OUT wall (two-ledgers rule, "
               f"the memory does not price it): {len(wall)}")
    out.append(f"  buried by gates the memory does not own (deep veto, "
               f"trend, climb, blowoff): {skipped}")
    out.append("")
    out.append("convention: position = $1055 x the brain's size ladder; "
               "the trade pays end_move_pct (3h close) — mfe_pct is the "
               "sell-the-top ceiling. No look-ahead: the memory is "
               "rebuilt truncated before each event's hour.")
    out.append("")

    rel = [e for e in events if e["released"]]
    rel_usd_end = sum(_usd(e, "end_pct") for e in rel)
    rel_usd_mfe = sum(_usd(e, "mfe_pct") for e in rel)
    out.append(f"── RELEASED by the memory ({len(rel)} of {len(events)} "
               f"priced burials) ──")
    out.append(f"{'pair':<12} {'tide':<6} {'quick':>5} {'bar':>4} -> "
               f"{'on':>4} {'adj':>5} {'size$':>6} {'end$':>7} {'mfe$':>7} "
               f"{'payoff':<8}")
    for e in sorted(events, key=lambda x: _usd(x, "end_pct"), reverse=True):
        mark = "  <== RELEASED" if e["released"] else ""
        out.append(f"{e['pair']:<12} {e['state']:<6} {e['quick']:>5.1f} "
                   f"{e['bar']:>4.0f} -> {e['on_bar']:>4.0f} "
                   f"{e['adj']:>+5.2f} {e['size']:>6.0f} "
                   f"{_usd(e, 'end_pct'):>+7.2f} {_usd(e, 'mfe_pct'):>+7.2f} "
                   f"{str(e['payoff']):<8}{mark}")
    out.append("")
    out.append(f"TOTAL released: end-move ${rel_usd_end:+.2f}   "
               f"(sell-the-top ceiling ${rel_usd_mfe:+.2f})")
    # the near-misses: what a bigger memory would have released
    near = [e for e in events if not e["released"]
            and 0 < e["on_bar"] - e["quick"] <= 3.0]
    out.append(f"near-misses (gap <= 3 pts even after the memory): "
               f"{len(near)}")
    for e in near[:5]:
        out.append(f"   {e['pair']:<12} quick {e['quick']:.1f} vs adjusted "
                   f"bar {e['on_bar']:.0f} (gap "
                   f"{e['on_bar'] - e['quick']:.1f}) — would have paid "
                   f"{_usd(e, 'end_pct'):+.2f} at close")
    out.append("")

    out.append("── BLOCKED by the memory ──")
    neg = [e for e in events if e["adj"] < -0.05]
    out.append(f"0 events. A negative adjustment tightens a bar that "
               f"already buried — it can never un-bury. "
               f"({len(neg)} priced burials DID carry a tightening "
               f"adjustment; none was a fired popup, because this "
               f"window contains no fired records yet.)")
    out.append("")

    out.append("── THE WALL, informational (NOT the memory's money) ──")
    out.append("These burials are the bot bridge's blanket OUT block — "
               "the two-ledgers rule (the bot never trades OUT). The "
               "memory deliberately does not price that gate. For "
               "scale only: had the desktop's personal bar ruled there "
               "too (base 92, the value in force through the window), "
               "the quick scores that would have cleared it:")
    clears = [w for w in wall
              if w["quick"] >= max(86.0, min(97.0, 92.0 - _replayed_adj(
                  _parse_ts(w["ts"]), w["pair"], w["st"])))]
    goal_take = sum(w["size"] * 0.015 for w in clears
                    if w["payoff"] == "winner")
    out.append(f"  {len(clears)} of {len(wall)} wall burials would have "
               f"cleared the personal bar — end-move "
               f"${sum(_usd2(w) for w in clears):+.2f}, popup-style "
               f"goal-take (winners exit at the 1.5% goal) "
               f"${goal_take:+.2f}, mfe ceiling "
               f"${sum(w['size'] * w['mfe_pct'] / 100.0 for w in clears):+.2f}")
    out.append("  That is the two-ledgers ruling's price tag, not the "
               "memory's — changing it is a user decision, not a gate.")
    out.append("")
    out.append("READ: the per-coin memory's dollar effect in this window "
               "is second-order by design — it moves bars at most 3 "
               "pts, and every bar-priced burial in the window missed "
               "by more than its coin's adjustment (closest: see the "
               "table). The memory's job is the margin, not the wall; "
               "the walls are the wolf carve, the deep veto, and the "
               "bot bridge's OUT rule.")

    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    print(f"\nWROTE {OUT.name}")


def _usd2(w):
    return w["size"] * w["end_pct"] / 100.0


if __name__ == "__main__":
    main()
