"""THE PER-COIN TIDE MEMORY (Sep 10) — each coin's own tide history,
the false-breakout pattern applied to the tide.

THE QUESTION (user, Sep 10: "each coin has a history"): the tide gates
treat every coin alike, but the deep store says coins carry PERSONAL
tide habits — some pay in OUT hours while the fleet sinks, some only
pay when the fleet rises.

THE EVIDENCE (split-half, extended 1h store, popup fleet, 4h horizon):
per-coin payer-rate edge PERSISTS across the window's halves —
  OUT    rho = +0.74  (1st-half top-5 edges carried +8.2pts in the
                       2nd half; the bottom-5 carried -10.2pts)
  FLAT   rho = +0.51
  RISING rho = +0.24  (weak — and the fleet already carries rising
                       coins, so RISING gets NO memory adjustment,
                       the same logic as the lone wolf's "RISING
                       gets nothing")
Strongest exactly where the gates bite hardest. The memory is real,
not noise.

THE MODEL (mirrors volatility_engine's false-breakout bar):
per coin x tide state, measure the payer rate (fwd 4h >= +1.5%) over
the popup fleet's hours in that state; the EDGE is the coin's rate
minus the fleet's rate in the same hours. The adjustment is
  adj = clip(edge * EDGE_SCALE, -EDGE_MAX_PTS, +EDGE_MAX_PTS)
positive = this coin pays in this tide more than the fleet = EASE its
bar by that many points (negative = tighten). A coin needs
MIN_STATE_HOURS of measured coin-hours per state before it earns an
adjustment — thin history stays at 0 (fail-closed, like reliable=False
in the volatility engine).

CONSUMERS: popup_floor(pair=...) — the desktop/QC floor — and the
bot-side asset bar (Gate 2), each floored so memory alone can never
drop a bar below its own hard floor. Fail-closed everywhere: no store,
thin history, a broken read — no adjustment.

THE LIVE LAYER (Sep 10, learns BEYOND the store): the store is a
snapshot that ages; the funnel's own popup outcomes are fresh daily.
A background driver re-scores recent popup-path events (hindsight
scorer: features + outcome for every suppression AND every fired
popup) and the memory blends that live evidence on top of the store
edge: fired popups that PAY strengthen the coin's edge, buried popups
that would have paid RESTORE it (mfe >= the popup goal counts as a
payer, exactly the store's definition). Live evidence decays (7-day
half-life), ramps in with sample size (a coin needs ~4 decayed events
for full effect), and never leaves the ±3 band — the store keeps the
baseline, live evidence refines it. Fail-closed: no outcomes file,
no tide on the record, a broken read — the store edge stands alone.
"""
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parent

# the false-breakout pattern's dials
HORIZON_H = 4          # the popup horizon (one rotation)
GOAL_PCT = 1.5         # "pays like a popup"
MIN_STATE_HOURS = 30   # coin-hours of history a coin-state needs
EDGE_SCALE = 10.0      # a 0.10 rate edge = 1.0 pts of bar adjustment
EDGE_MAX_PTS = 3.0     # the adjustment never leaves [-3, +3]
ADJUSTED_STATES = ("OUT", "FLAT")   # RISING rides the fleet (rho +0.24)
CACHE_TTL_S = 1800.0   # re-measure every 30 min (the store refreshes daily)

# the live layer's dials (popup outcomes, Sep 10)
LIVE_FILE = ROOT / "snapshot_outcomes.jsonl"
LIVE_REFRESH_S = 1800.0    # the driver re-scores popup outcomes every 30 min
LIVE_HALF_LIFE_H = 24 * 7.0   # a week: stale outcomes fade toward the store
LIVE_MIN_FLEET_W = 3.0     # decayed live events a state needs before live nudges
LIVE_RAMP_W = 4.0          # decayed events for the FULL live effect (ramp = w/4)

_cache = {"data": None, "ts": 0.0, "err": None}
_lock = threading.Lock()


def _measure(states, ffwd, now=None) -> dict:
    """Per tide state: the fleet's payer rate and each coin's edge over
    that state's hours. Pure function — the suite grades this directly.
    Returns {state: {fleet, hours, coins: {pair: {hours, rate, edge,
    adj}}}}."""
    out = {}
    for st in ("OUT", "FLAT", "RISING"):
        hrs = states.index[states == st]
        hrs = [h for h in hrs if h in ffwd.dropna(how="all").index]
        if len(hrs) < MIN_STATE_HOURS:
            out[st] = {"fleet": None, "hours": len(hrs), "coins": {},
                       "why": f"only {len(hrs)}h of history (need >= "
                              f"{MIN_STATE_HOURS})"}
            continue
        f = ffwd.loc[hrs]
        payer = f >= GOAL_PCT / 100.0   # NaN comparisons are False —
        seen = f.notna().sum()          # so weight by MEASURED hours
        seen_total = int(seen.sum())
        if seen_total == 0:
            out[st] = {"fleet": None, "hours": len(hrs), "coins": {},
                       "why": "no measured coin-hours"}
            continue
        # the fleet's payer rate over MEASURED coin-hours (payer.sum()
        # counts True only where the coin traded — NaN hours never
        # count as non-payers)
        fleet_rate = float(payer.sum().sum() / seen_total)
        coins = {}
        for pair in f.columns:
            n = int(seen[pair])
            if n < MIN_STATE_HOURS:
                continue   # thin history earns nothing (fail-closed)
            rate = float(payer[pair].sum() / n)
            edge = rate - fleet_rate
            adj = max(-EDGE_MAX_PTS, min(EDGE_MAX_PTS, edge * EDGE_SCALE))
            coins[pair] = {"hours": n, "rate": round(rate, 4),
                           "edge": round(edge, 4), "adj": round(adj, 2)}
        out[st] = {"fleet": round(fleet_rate, 4), "hours": len(hrs),
                   "coins": coins, "why": None}
    return out


# ── THE LIVE LAYER ───────────────────────────────────────────────────
def _parse_tide(tide) -> str | None:
    """'OUT 22%' / 'RISING 85%' -> the state word. None when absent."""
    if not tide:
        return None
    w = str(tide).strip().split()
    return w[0].upper() if w and w[0].upper() in ("OUT", "FLAT", "RISING") else None


def _live_measure(records, now_ts=None) -> dict:
    """Popup-outcome evidence per tide state, over MEASURED coin-events.
    A payer = mfe_pct >= the popup's own goal (the store's >= +1.5%
    definition). Returns {state: {fleet, events, coins: {pair: {events,
    w, rate, edge}}}} — w is the decay-weighted event count (the ramp's
    denominator). Records missing a tide or an outcome are skipped
    (fail-closed: they can't be graded)."""
    from datetime import datetime, timezone
    now_ts = now_ts or time.time()
    evs = []
    for r in records:
        st = _parse_tide(r.get("tide"))
        oc = r.get("outcome") or {}
        mfe = oc.get("mfe_pct")
        goal = r.get("goal_pct") or 1.5
        try:
            mfe = float(mfe)
            goal = float(goal)
        except (TypeError, ValueError):
            continue   # ungraded event — no evidence either way
        ts = r.get("ts")
        try:
            age_h = max(0.0, (now_ts - datetime.fromisoformat(str(ts))
                              .timestamp()) / 3600.0)
        except Exception:
            continue
        evs.append((st, str(r.get("pair") or ""), mfe >= goal,
                    0.5 ** (age_h / LIVE_HALF_LIFE_H)))
    out = {}
    for st in ("OUT", "FLAT", "RISING"):
        s = [e for e in evs if e[0] == st and e[1]]
        if not s:
            out[st] = {"fleet": None, "events": 0, "coins": {}}
            continue
        tot_w = sum(e[3] for e in s)
        fleet = sum(e[3] for e in s if e[2]) / tot_w if tot_w > 0 else None
        coins = {}
        for pair in {e[1] for e in s}:
            ce = [e for e in s if e[1] == pair]
            w = sum(e[3] for e in ce)
            if w <= 0:
                continue
            rate = sum(e[3] for e in ce if e[2]) / w
            coins[pair] = {"events": len(ce), "w": round(w, 4),
                           "rate": round(rate, 4),
                           "edge": round(rate - fleet, 4)}
        out[st] = {"fleet": fleet, "events": len(s), "coins": coins}
    return out


def _live_records() -> list:
    """The outcomes file's records (the driver's output). Never raises."""
    try:
        import json
        rows = []
        for line in LIVE_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
        return rows
    except Exception:
        return []


def refresh_live() -> dict:
    """Re-score recent popup outcomes (hindsight scorer) and blend them
    into the cached model. Returns a small summary. Never raises."""
    import hindsight_scorer as _hs
    try:
        _hs.backfill()
    except Exception as exc:
        return {"scored": 0, "err": f"outcome re-score failed ({exc})"}
    recs = _live_records()
    live = _live_measure(recs)
    with _lock:
        data = _cache.get("data")
        if data is not None:
            data["live"] = live
            data["live_ts"] = _utcnow_iso()
        _cache["live_err"] = None
    return {"scored": len(recs),
            "events": {st: (live.get(st) or {}).get("events", 0)
                       for st in live}}


def live_driver_loop(stop=None):
    """The driver: every LIVE_REFRESH_S, re-score popup outcomes and
    refresh the blend. Daemon-safe; stops when stop.is_set()."""
    while stop is None or not stop.is_set():
        try:
            refresh_live()
        except Exception:
            pass
        if stop is not None:
            stop.wait(LIVE_REFRESH_S)
        else:
            time.sleep(LIVE_REFRESH_S)


def compute(now=None) -> dict:
    """The full measurement pass over the deep store (the 1h build).
    Returns {generated_ts, hours, window, states}, never raises; every
    outcome (model or failure) is cached with a fresh ts so callers
    rate-limit to one build per TTL."""
    try:
        import tide_learn as _tl
        px, err = _tl._load_deep_fleet()
        if px is None:
            _cache.update(data=None, ts=time.time(), err=err)
            return {"err": err, "states": {}, "generated_ts": None,
                    "hours": 0, "window": None}
        states = _tl._tide_states(px, _tl._tide_class_ref())
        # THE POPUP FLEET's coins only — the memory must price what the
        # popups could have monetized (the same cut tide_learn prices)
        try:
            import config as _cfgmod
            pp = [p for p in _cfgmod.DEFAULT_CONFIG.trading_pairs
                  if p in px.columns]
        except Exception:
            pp = []
        fpx = px[pp] if len(pp) >= _tl.FLEET_MIN else px
        ffwd = fpx.shift(-HORIZON_H) / fpx - 1.0
        data = {"generated_ts": (now or _utcnow_iso()),
                "hours": int(states.shape[0]),
                "window": [str(px.index[0]), str(px.index[-1])],
                "states": _measure(states, ffwd), "err": None,
                "live": _live_measure(_live_records()),
                "live_ts": _utcnow_iso()}
        _cache.update(data=data, ts=time.time(), err=None)
        return data
    except Exception as exc:
        _cache.update(data=None, ts=time.time(), err=str(exc))
        return {"err": str(exc), "states": {}, "generated_ts": None,
                "hours": 0, "window": None}


def _utcnow_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def tide_edge(pair: str, tide_state: str | None,
              max_age_s: float = CACHE_TTL_S) -> tuple[float, str]:
    """(adj_pts, why) for this coin in this tide state. adj > 0 eases
    the coin's bar (it pays in this tide), < 0 tightens it, 0 = no
    earned adjustment (thin history, unknown coin, RISING, or a broken
    store — fail-closed). A stale cache triggers ONE synchronous
    rebuild; failures are cached too (rate-limited by ts)."""
    with _lock:
        data, err = _cache["data"], _cache["err"]
        age = time.time() - _cache["ts"]
    fresh_failure = data is None and err is not None and age <= max_age_s
    if not fresh_failure and (data is None or age > max_age_s):
        compute()
        with _lock:
            data, err = _cache["data"], _cache["err"]
            age = time.time() - _cache["ts"]
    if data is None:
        return 0.0, f"no tide memory ({err or 'unavailable'})"
    if tide_state not in ADJUSTED_STATES:
        # BY DESIGN, before any lookup: RISING rides the fleet even when
        # the model measured it (rho +0.24 is not a memory worth acting on)
        return 0.0, (f"{tide_state} rides the fleet — no memory "
                     f"adjustment")
    st = (data.get("states") or {}).get(tide_state or "")
    if not st:
        return 0.0, f"no memory for {tide_state}"
    coin = (st.get("coins") or {}).get(pair)
    if not coin:
        return 0.0, (st.get("why")
                     or f"no measured memory for {pair} in {tide_state}")
    adj = float(coin.get("adj") or 0.0)
    why = (f"{pair} pays {coin['rate'] * 100:.1f}% in {tide_state} hours "
           f"(fleet {st['fleet'] * 100:.1f}%) over {coin['hours']}h "
           f"of history")
    # THE LIVE BLEND (Sep 10): the funnel's own popup outcomes refine
    # the store edge — fired popups that pay strengthen the coin's
    # edge, buried payers restore it (mfe >= the popup's goal = a
    # payer, the store's definition). Decay-weighted, ramped by
    # sample size (LIVE_RAMP_W events for the full effect), clamped
    # to the same +-3 band: the store keeps the baseline, live
    # evidence refines it. A cold live cut changes nothing.
    live = data.get("live") or {}
    lst = live.get(tide_state) or {}
    lc = (lst.get("coins") or {}).get(pair)
    if lst.get("fleet") is not None and lc and lc.get("w", 0) > 0:
        ramp = min(1.0, lc["w"] / LIVE_RAMP_W)
        live_edge = lc["edge"] * ramp
        adj = adj + max(-EDGE_MAX_PTS, min(EDGE_MAX_PTS, live_edge * EDGE_SCALE))
        adj = max(-EDGE_MAX_PTS, min(EDGE_MAX_PTS, adj))
        why += (f"; live popups {lc['rate'] * 100:.0f}% pay (fleet "
                f"{lst['fleet'] * 100:.0f}%, {lc['events']} events, "
                f"w {lc['w']:.1f}) -> {adj:+.2f} pts")
    return adj, why


def warm():
    """Refresh the cache (best-effort, never raises)."""
    try:
        compute()
    except Exception:
        pass


def snapshot():
    """The cached memory for diagnostics (never computes)."""
    with _lock:
        return {"age_s": round(time.time() - _cache["ts"], 1)
                if _cache["ts"] else None,
                "data": _cache["data"], "err": _cache["err"]}
