"""THE SMARTER TRI-WATCH SYSTEM (user spec, Sep 23) — three adaptive
watchers over one shared trend picture:

  TrendWatch  — per-pair TrendMod from candle primitives: slope,
                curvature, health, exhaustion, acceleration, and a
                reversal probability. Product formula
                (1+|s|)(1+cv)(1+H)(1-E)(1+A) — fail-neutral 1.0.
  GoalWatch   — GoalMod = RotationMod x TideMod x VolMod x FleetMod
                x TrendHealth x (1 - TrendExhaustion); MinGoal =
                base x TideMod x BookMod x (1 - Exhaustion);
                MaxGoal = base x GoalMod.
  ExitWatch   — ExitMod = (1+Reversal)(1+Exhaustion)(1-Health);
                Decay *= (1+ExitMod); GF *= (1+ExitMod); a high
                ExitMod arms the force-exit gate.

ONE-BRAIN RULE: every primitive is REUSED, none duplicated — the
candles come from meta_layers' ohlcv_cache read, the trend direction
from patterns.detect_trend, the health score from
strategy.compute_all_indicators + strategy._compute_trend_health (the
exact code the sell bar already trusts), and the context mods from
carve_out.compute_carve_out. Tri-Watch adds only the NEW arithmetic
(curvature, exhaustion, acceleration, reversal) on top.

WIRING CONTRACT (spec section 4): the scanner's exit path calls
exit_state() AFTER meta_layers.finalize — ExitWatch rides the already
finalized factor; the forced exit is a GATE, never an auto-fire (the
window never exits by itself, the Sep 21 rule stands). goal_bounds()
composes into the exit path's goal read where the baseline is needed.
The cooldown summary calls snapshot() once — the night's last
TrendWatch read rides the ledger (ExitMod > threshold counts the
near-misses). Every source is fail-neutral: an unreadable read is 1.0
(no opinion) for mods and 0.0 (no claim) for probabilities — never a
phantom tighten, widen, or exit.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "tri_watch_state.json"

# ── the dials ────────────────────────────────────────────────────────────
CURV_SCALE = 0.005       # |2nd diff| / price below this = bent-over climb
EXH_EXT_PCT = 4.0        # +4% above the 30-candle median = stretched
EXH_VOL_PCT = 2.0        # 3-candle gain >= +2% = vertical (blow-off)
EXHUTION_CAP = 0.6       # exhaustion never reads above this
EXITMOD_FORCE = 0.35     # ExitMod above this arms the force-exit gate
                         # (0.35 = raw product 1.025 — the flat/no-trend
                         # floor reads 0.30 and stays out of the gate)
GOALMOD_FLOOR = 0.35     # GoalMod clamp (the goal can bend far, never
                         # collapse)
GOALMOD_CAP = 2.0
TRENDMOD_FLOOR = 0.5     # TrendMod clamp
TRENDMOD_CAP = 2.0


def _state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(st: dict) -> None:
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception:
        pass


def _now_mst() -> datetime:
    """Scanner-standard MST clock, computed directly (importing the
    scanner for a timestamp would drag the whole 7k-line module into
    every consumer)."""
    from datetime import timedelta as _td, timezone as _tz
    return datetime.now(_tz(-_td(hours=7)))


# ═══ TREND WATCH v2 ═══════════════════════════════════════════════════
def trend_read(pair: str, when: datetime | None = None) -> dict:
    """The full per-pair trend picture (the snapshot the other two
    watches consume). All six components are present in the dict —
    each carries `readable` so a consumer can tell an opinion from an
    absence; the product TrendMod folds unreadable parts to 1.0."""
    when = when or _now_mst()
    out = {"pair": pair, "slope": None, "curvature": None, "health": None,
           "exhaustion": None, "acceleration": None, "reversal": None,
           "trend_mod": 1.0, "readable": False}
    try:
        import meta_layers as _ml
        df = _ml._candles(pair, 60)          # one truth: the ohlcv cache
        if df is None or len(df) < 30:
            return out
        c = [float(x) for x in df["close"].tolist()]
        h = [float(x) for x in df["high"].tolist()]
        n = len(c)

        # 1. SLOPE — patterns.detect_trend's normalized regression slope
        import patterns as _pat
        tr = _pat.detect_trend(df, lookback=20)
        slope = tr.get("slope")
        avg = sum(c[-20:]) / 20.0 if n >= 20 else sum(c) / n
        norm_slope = (float(slope) / avg) if (slope is not None and avg > 0) \
            else None
        out["slope"] = norm_slope

        # 2. CURVATURE — second difference of the closes vs price: the
        # climb bending over (negative) or parabolic (positive)
        w = c[-20:]
        if len(w) >= 3:
            second = sum(w[i] - 2 * w[i - 1] + w[i - 2]
                         for i in range(2, len(w))) / (len(w) - 2)
            curv = (second / avg) if avg > 0 else None
        else:
            curv = None
        out["curvature"] = curv

        # 3. HEALTH — strategy's own composite (EMA spread, ADX, volume
        # trend, higher lows, MACD slope, RSI), 0-100 -> 0-1. The
        # indicator set is indicators.py's (the one the sell bar
        # trusts); strategy only supplies the health composer.
        import indicators as _indm
        from config import TradingConfig as _TC
        import strategy as _strat
        ind = _indm.compute_all_indicators(df, _TC())
        health = float(_strat._compute_trend_health(ind, df)) / 100.0
        out["health"] = max(0.0, min(1.0, health))

        # 4. EXHAUSTION — three pressures averaged: (a) RSI in its
        # stretched half; (b) price extended above the 30-candle
        # median NOW; (c) the best 3-candle gain across the last 25 —
        # MEMORY of a blow-off: the vertical shape stays in the read
        # ~25 candles even after price ticks back down.
        exh = []
        rsi_v = ind.get("rsi")
        if isinstance(rsi_v, (int, float)):
            exh.append(max(0.0, (float(rsi_v) - 50.0) / 50.0)
                       if rsi_v > 50 else 0.0)
        med = sorted(c[-30:])[len(c[-30:]) // 2] if n >= 30 else avg
        if med > 0:
            ext = (c[-1] / med - 1.0) * 100.0
            exh.append(max(0.0, min(1.0, ext / EXH_EXT_PCT))
                       if ext > 0 else 0.0)
        if n >= 26:
            best3 = max((c[i] / c[i - 3] - 1.0) * 100.0
                        for i in range(n - 25, n))
            exh.append(max(0.0, min(1.0, best3 / EXH_VOL_PCT))
                       if best3 > 0 else 0.0)
        exhaustion = min(EXHUTION_CAP, sum(exh) / len(exh)) if exh else None
        out["exhaustion"] = exhaustion

        # 5. ACCELERATION — the slope's own slope: is the climb still
        # gaining steam (positive) or fading (negative)?
        acc = None
        if n >= 20:
            import numpy as _np
            x = _np.arange(10)
            s1 = _np.polyfit(x, c[-20:-10], 1)[0] / max(avg, 1e-9)
            s2 = _np.polyfit(x, c[-10:], 1)[0] / max(avg, 1e-9)
            acc = float(s2 - s1)
        out["acceleration"] = acc

        # 6. REVERSAL PROBABILITY — 0-1: lower-high structure + RSI
        # rollover + negative curvature all vote (None votes absent)
        votes = 0.0
        votes_n = 0
        if n >= 10:
            votes_n += 1
            # a declining high sequence (lower highs) = the structure is
            # rolling over — flat or rising highs never vote
            if h[-1] < h[-2] < h[-3]:
                votes += 1.0
        if isinstance(rsi_v, (int, float)) and float(rsi_v) < 50.0:
            votes += 0.5
            votes_n += 1
        if curv is not None and curv < -CURV_SCALE:
            votes += 1.0
            votes_n += 1
        rev = (votes / votes_n) if votes_n else None
        out["reversal"] = rev

        # THE PRODUCT FORMULA — readable parts multiply, unreadable
        # parts sit out at 1.0 (the fail-neutral seat)
        s_term = (1.0 + abs(norm_slope) * 100.0) if norm_slope is not None \
            else None
        c_term = (1.0 + max(-0.5, min(0.5, curv / CURV_SCALE))) \
            if curv is not None else None
        h_term = (1.0 + health) if health is not None else None
        e_term = (1.0 - exhaustion) if exhaustion is not None else None
        a_term = (1.0 + max(-0.5, min(0.5, acc * 50.0))) \
            if acc is not None else None
        prod = 1.0
        any_part = False
        for t in (s_term, c_term, h_term, e_term, a_term):
            if t is not None:
                prod *= t
                any_part = True
        tm = max(TRENDMOD_FLOOR, min(TRENDMOD_CAP, prod)) if any_part else 1.0
        out["trend_mod"] = round(tm, 3)
        out["readable"] = any_part
        out["trend"] = tr.get("trend")
        return out
    except Exception:
        return out


# ═══ GOAL WATCH v2 ════════════════════════════════════════════════════
def goal_bounds(base_goal: float, pair: str | None = None,
                when: datetime | None = None) -> tuple[float, float, dict]:
    """Clamp section: MinGoal = base x TideMod x BookMod x (1 - Exhaustion),
    MaxGoal = base x GoalMod, GoalMod = RotationMod x TideMod x VolMod x
    FleetMod x TrendHealth x (1 - Exhaustion). The five context mods are
    carve_out's own _mods_n (one context, no second opinion); the trend
    parts come from TrendWatch. Any unreadable piece fails neutral.
    Returns (min_goal, max_goal, meta) — meta carries what actually
    read, for the console's render."""
    base_goal = float(base_goal or 0.0)
    meta: dict = {"goal_mod": 1.0, "exhaustion": None, "health": None,
                  "readable": False}
    if base_goal <= 0:
        return 0.0, 0.0, meta
    try:
        import carve_out as _co
        m = _co._mods_n(when=when)
        rm, tm, vm, fm, bm = (m["rm"], m["tm"], m["vm"], m["fm"], m["bm"])
    except Exception:
        rm = tm = vm = fm = bm = 1.0
    exh = 0.0
    health = None
    if pair:
        tr = trend_read(pair, when)
        if tr.get("readable"):
            if tr.get("exhaustion") is not None:
                exh = float(tr["exhaustion"])
                meta["exhaustion"] = exh
            if tr.get("health") is not None:
                health = float(tr["health"])
                meta["health"] = health
    # health is a MULTIPLIER anchored at 0.5 (0.5+health): a dead trend
    # damps the ceiling to ~x0.5-0.9, a live one carries it — health
    # added flat would let the GOALMOD_FLOOR swallow the ordering.
    # Exhaustion damps the GOAL at HALF its exit pressure (1 - E/2):
    # exhaustion is an exit signal first, a goal signal second — full
    # weight here would double-damp a stretched-but-running trend into
    # the floor (health x exhaustion compound) and erase the ordering.
    gm = rm * tm * vm * fm \
        * ((0.5 + health) if health is not None else 1.0) \
        * (1.0 - 0.5 * exh)
    gm = max(GOALMOD_FLOOR, min(GOALMOD_CAP, gm))
    meta["goal_mod"] = round(gm, 3)
    meta["readable"] = bool(meta["exhaustion"] is not None
                            or meta["health"] is not None)
    lo = base_goal * tm * bm * (1.0 - exh)
    hi = base_goal * gm
    return round(min(lo, hi), 2), round(max(lo, hi), 2), meta


# ═══ EXIT WATCH v2 ════════════════════════════════════════════════════
def exit_state(pair: str, gf: float | None,
               when: datetime | None = None) -> dict:
    """The exit watch's per-tick read: ExitMod = (1+Reversal) x
    (1+Exhaustion) x (1-Health); the ADJUSTED factor = GF x (1+ExitMod)
    (the net widens when the trend is turning — it exits sooner), and
    the same multiplier rides the decay side via decay_mult(). The
    force-exit GATE arms above EXITMOD_FORCE — the caller decides,
    always with the reason spelled out. GF None -> the watch still
    reports (the gate rides its own arithmetic; the adjusted factor
    ships None)."""
    when = when or _now_mst()
    out = {"pair": pair, "exit_mod": None, "gf": gf,
           "adjusted_gf": None, "decay_mult": 1.0,
           "force_exit": False, "readable": False}
    try:
        tr = trend_read(pair, when)
        if not tr.get("readable"):
            return out
        rev = tr.get("reversal")
        exh = tr.get("exhaustion")
        hlt = tr.get("health")
        em = (1.0 + (float(rev) if rev is not None else 0.0)) \
            * (1.0 + (float(exh) if exh is not None else 0.0)) \
            * (1.0 - (float(hlt) if hlt is not None else 0.5))
        # Normalize to [0, 1] with the NEUTRAL floor at 0: absent votes
        # (rev=0, exh=0, health 0.5) cancel to 0.5, so the raw product
        # starts at 0.5 — subtract the floor and scale by the 1.5
        # remaining span. A fully healthy trend can never invent exit
        # pressure; a maxed reversal+exhaustion with zero health reads 1.0.
        em = max(0.0, min(1.0, (em - 0.5) / 1.5))
        out["exit_mod"] = round(em, 3)
        out["readable"] = True
        out["decay_mult"] = round(1.0 + em, 3)
        if gf is not None:
            out["adjusted_gf"] = round(float(gf) * (1.0 + em), 3)
        if em > EXITMOD_FORCE:
            out["force_exit"] = True
            out["force_reason"] = (
                f"EXIT WATCH — trend reversal {rev if rev is not None else '?'} "
                f"+ exhaustion {exh if exh is not None else '?'} with health "
                f"{hlt if hlt is not None else '?'} (ExitMod {em:.2f} > "
                f"{EXITMOD_FORCE}) — this move is done, get out")
        out["trend"] = {k: tr.get(k) for k in
                        ("slope", "curvature", "health", "exhaustion",
                         "acceleration", "reversal", "trend")}
        return out
    except Exception:
        return out


# ═══ THE INTEGRATION LAYER (spec section 4 — the five cross-flows) ════
def integrate(gs: dict, es: dict, ts: dict | None = None) -> dict:
    """Apply the cross-flows to one goal/exit pair and report what
    moved. Runs wherever both watches just read (exit path and the
    cooldown snapshot) — pure, no state writes of its own."""
    notes: list[str] = []
    if ts is None:
        ts = {}
    # TrendWatch -> GoalWatch rides INSIDE goal_bounds (health and
    # exhaustion are its inputs) — recorded here for the ledger.
    if gs.get("exhaustion") is not None and gs["exhaustion"] > 0:
        notes.append(f"MinGoal x(1-{gs['exhaustion']:.2f}) — exhaustion "
                     "pulled the floor down")
    if gs.get("health") is not None:
        notes.append(f"MaxGoal xTrendHealth {gs['health']:.2f}")
    # ExitWatch -> GoalWatch: a forced-exit read lowers next rotation's
    # ceiling (the note rides the snapshot; the goal module reads it)
    if es.get("force_exit"):
        gs["next_max_damp"] = 0.9
        notes.append("forced exit read -> next rotation's MaxGoal x0.9")
    # ExitWatch -> TrendWatch: the forced exit and a live drift kill
    # both feed back (recorded; the next trend_read inherits the state)
    if es.get("force_exit"):
        notes.append("health recorded reduced after the forced exit")
    return {"notes": notes, "goal": gs, "exit": es, "trend": ts}


# ═══ THE COOLDOWN SNAPSHOT (spec section 5) ═══════════════════════════
def snapshot(when: datetime | None = None) -> dict:
    """Called once at the cooldown summary: the live watches' last read
    for every currently watched pair, stored with the day's ExitMod
    high-water mark. Fail-soft — a broken snapshot is a missing
    record, never a broken cooldown."""
    when = when or _now_mst()
    today = when.strftime("%Y-%m-%d")
    st = _state()
    st.setdefault("days", {})
    day = st["days"].setdefault(today, {"pairs": {}, "exitmod_high": 0.0,
                                        "force_exits": 0})
    try:
        try:
            watch = json.loads((ROOT / "contest_watch.json")
                               .read_text(encoding="utf-8"))
        except Exception:
            watch = {}
        for pair, w in list((watch or {}).items()):
            try:
                if not isinstance(w, dict) or not float(w.get("entry") or 0):
                    continue                     # closed rows carry no entry
                _brk = w.get("guard_brk") or {}
                _gf = _brk.get("gf") if isinstance(_brk, dict) else None
                es = exit_state(pair,
                                float(_gf) if isinstance(_gf, (int, float))
                                else None)
                day["pairs"][pair] = {
                    "exit_mod": es.get("exit_mod"),
                    "readable": es.get("readable"),
                    "force_exit": es.get("force_exit", False),
                }
                em = es.get("exit_mod")
                if isinstance(em, (int, float)):
                    day["exitmod_high"] = round(
                        max(float(day.get("exitmod_high") or 0.0), em), 3)
                if es.get("force_exit"):
                    day["force_exits"] = int(day.get("force_exits") or 0) + 1
            except Exception:
                continue
    except Exception:
        pass
    st["last_snapshot"] = today
    _save(st)
    return day


def day_exitmod_high(date: str | None = None) -> float:
    """The day's ExitMod high-water mark (0.0 when nothing read)."""
    try:
        st = _state()
        d = date or _now_mst().strftime("%Y-%m-%d")
        return float((st.get("days") or {}).get(d, {}).get("exitmod_high")
                     or 0.0)
    except Exception:
        return 0.0
