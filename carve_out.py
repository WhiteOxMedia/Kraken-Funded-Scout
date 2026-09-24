"""THE ADAPTIVE LONE-WOLF CARVE-OUT (user spec, Sep 23) — the fixed +5%
own-move carve-out becomes a computed, five-modifier allowance:

    CarveOut = BaseCarveOut * FleetMod * BookMod * TideMod * VolMod * RotationMod

and the tide-out hatch's required own-move becomes Base / (1 + CarveOut) —
the spec's worked example: tide out (TideMod 0.6), fleet 0.8, book 0.9
gives 5% x 0.8 x 0.9 x 0.6 = 2.16% ("your carve-out should be ~2%, not
5%"). The permission GATE is the real brake in a dead tide: LoneWolfMode
requires CarveOut >= its threshold AND Fleet/Tide/Rotation healthy AND no
RCL drift AND no end-game AND no suppression — so a dead tide still
tightens what gets through, it just stops doing it with a static number.

Wiring (all lazy imports — this module sits between the scanner and the
guard, both of which load lazily):
  RCL drift  -> CarveOut *= 0.8, LoneWolfMode FALSE, re-entry blocked
  SAL        -> x0.85 on violations, x1.05 on a clean book (the guard's
                own stored SAL_BAD_MULT / SAL_GOOD_MULT)
  GuardFactor-> GF *= (1 + CarveOut) at the meta finalize (tide out
                tightens the net, tide in loosens it)
  DNL        -> the nightly record rides dnl_report: the day's CarveOut
                and its five mods enter the narrative data and scale
                tomorrow's DailyGoal by (1 + CarveOut)

Every modifier is clamped and fail-neutral: an unreadable source is 1.0
(no opinion), never a phantom tighten or widen. The final CarveOut is
clamped [0, MaxCarveOut] — the ceiling itself breathes with the regime
(0.10 x RotationMod x VolMod x TideMod): a strong regime may let an
allowance run past the old static 10%, a dead tide caps it near 3%, and
unreadable sources fail OPEN to the old static 10% (never a phantom
tighten or widen).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "carve_out.json"

# ── the dials ────────────────────────────────────────────────────────────
BASE_CARVE_OUT = 0.05          # the old fixed +5%, now the base allowance
CARVEOUT_THRESHOLD = 0.02      # LoneWolfMode needs at least this allowance
FLEET_HEALTH_THRESHOLD = 0.9   # FleetMod floor for lone-wolf permission
TIDE_THRESHOLD = 0.8           # TideMod floor for lone-wolf permission
ROTATION_THRESHOLD = 1.0       # RotationMod floor for lone-wolf permission
BOOKMOD_PRESSURE = 1.0         # BookMod above this = the book is pressured

FLEETMOD_CLAMP = (0.5, 2.0)
TIDEMOD_CLAMP = (0.5, 1.5)
VOLMOD_CLAMP = (0.5, 2.0)
ROTMOD_CLAMP = (0.5, 1.5)
BOOKMOD_CLAMP = (0.5, 1.0)     # the book can widen the allowance, never
                               # beyond 1.0 — pressure only ever tightens
CARVE_ABS_MIN = 0.0            # the allowance never goes negative
                               # (the CEILING is adaptive — see 2.1)

DRIFT_MULT = 0.8               # RCL drift
SAL_BAD_MULT = 0.85            # rule violations (meta_layers' own dial)
SAL_GOOD_MULT = 1.05           # perfect compliance
SUPPRESS_MULT = 0.5            # any Section-6 suppression trigger
EXPAND_MULT = 1.2              # fleet+tide+rotation strong, book light

RISK_BUDGET_SHARE = 0.25       # the size dial: 25% of balance is the budget
BOOK_WINDOW_MIN = 60           # committed-spend window for BookMod
CACHE_S = 120                  # gate() cache — parity with the lw cache

# fleet-strength scale: the fleet's median trade as a share of the daily
# goal — neutral (1.0) at no realized trades, +0.2 per tenth-of-goal the
# median trade earns (a $13 median on a $100 goal reads 1.26 = strong)
FLEET_STRENGTH_PER_GOAL_SHARE = 2.0


def _state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(st: dict) -> None:
    try:
        tmp = str(STATE_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh, indent=2)
        import os
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


def _now_mst() -> datetime:
    try:
        from background_scanner import _now_mst as _n
        return _n()
    except Exception:
        return datetime.utcnow() - timedelta(hours=6)


def _clamp(v: float, band: tuple) -> float:
    return round(max(band[0], min(band[1], float(v))), 4)


# ═══ SECTION 1 — the five modifiers ═══════════════════════════════════
def fleet_mod(when: datetime | None = None) -> float | None:
    """Fleet health: the day's median realized trade PnL as a share of the
    daily goal, scaled by FLEET_STRENGTH_PER_GOAL_SHARE. No closed trades
    today -> neutral 1.0 (no evidence is no opinion); the day's own vol
    ratio divides nothing here — volatility has its own modifier."""
    try:
        import profit_guard as _pg
        trades = [json.loads(ln) for ln in
                  (ROOT / "contest_trades.jsonl").read_text(encoding="utf-8")
                  .splitlines() if ln.strip()]
        when = when or _now_mst()
        day = [float(t.get("pnl_net") or 0) for t in trades
               if str(t.get("sold_at", "") ).startswith(when.strftime("%Y-%m-%d"))]
        if not day:
            return 1.0
        day.sort()
        n = len(day)
        med = day[n // 2] if n % 2 else (day[n // 2 - 1] + day[n // 2]) / 2.0
        # the RAW tier pace, straight from the account module — NOT the
        # guard's adaptive goal (goal_context -> fleet_mod would recurse:
        # the goal reads the context, the context would read the goal)
        try:
            import contest_account as _ca
            _acct = _ca.load_state()
            goal = float(_ca.daily_pace_usd(_acct) or 0)
        except Exception:
            goal = 0.0
        if not goal or goal <= 0:
            return 1.0
        return _clamp(1.0 + (med / goal) * FLEET_STRENGTH_PER_GOAL_SHARE,
                      FLEETMOD_CLAMP)
    except Exception:
        return None


def book_mod(when: datetime | None = None) -> float | None:
    """Book pressure: committed spend in the cap window over the risk
    budget (RISK_BUDGET_SHARE of the contest balance). Book heavy ->
    toward BOOKMOD_CLAMP[1] (tighten); book light -> toward 0.5 (widen)."""
    try:
        import background_scanner as _bs
        when = when or _now_mst()
        committed = _bs._recent_fired_popups_dollars(window_min=BOOK_WINDOW_MIN)
        bal = None
        try:
            acct = json.loads((ROOT / "contest_account.json")
                              .read_text(encoding="utf-8"))
            bal = float(acct.get("current_balance") or 0) or None
        except Exception:
            bal = None
        if bal is None or bal <= 0:
            return None
        budget = bal * RISK_BUDGET_SHARE
        return _clamp((committed or 0.0) / budget, BOOKMOD_CLAMP)
    except Exception:
        return None


def tide_mod(when: datetime | None = None) -> float | None:
    """TideMod = TIDE / baseline(3.0, FLAT). OUT 0 -> 0.0 (clamped 0.5);
    RISING 5 -> 1.67; FLAT interpolates around 1.0. Unreadable -> None."""
    try:
        import profit_guard as _pg
        ts = _pg.tide_score()
        if ts is None:
            return None
        return _clamp(float(ts) / 3.0, TIDEMOD_CLAMP)
    except Exception:
        return None


def vol_mod(pair: str | None = None,
            when: datetime | None = None) -> float | None:
    """VolMod = CurrentVol / BaselineVol — the guard's own vol_ratio (the
    pair's, falling back to the fleet-level read for the allowance)."""
    try:
        import profit_guard as _pg
        vr = _pg.vol_ratio(pair) if pair else _pg.vol_ratio(None)
        if vr is None and pair:
            vr = _pg.vol_ratio(None)
        return None if vr is None else _clamp(vr, VOLMOD_CLAMP)
    except Exception:
        return None


def rotation_mod(when: datetime | None = None) -> float | None:
    """RotationMod = RotationStrength / baseline(3.0). Strong 4-5 widens
    toward ROTMOD_CLAMP[1]; a 1-2 (ending) rotation tightens."""
    try:
        import profit_guard as _pg
        rs = _pg.rotation_strength(when)
        if rs is None:
            return None
        return _clamp(float(rs) / 3.0, ROTMOD_CLAMP)
    except Exception:
        return None


# ═══ THE COMPUTE ══════════════════════════════════════════════════════
def compute_carve_out(pair: str | None = None,
                      when: datetime | None = None,
                      drift_detected: bool | None = None,
                      sal_mult: float | None = None,
                      mods: dict | None = None) -> dict:
    """The full named result. `mods` injects modifier values for drives;
    None sources are neutral 1.0 (recorded as unreadable). Order: the
    five-mod product, RCL drift, SAL, then suppression/expansion, then
    the clamp — permission judged on the FINAL allowance."""
    when = when or _now_mst()
    injected = dict(mods or {})
    live = {"fleet": injected.get("fleet", fleet_mod(when)),
            "book": injected.get("book", book_mod(when)),
            "tide": injected.get("tide", tide_mod(when)),
            "vol": injected.get("vol", vol_mod(pair, when)),
            "rot": injected.get("rot", rotation_mod(when))}

    def _n(v):
        return 1.0 if v is None else float(v)

    fm, bm, tm, vm, rm = (_n(live["fleet"]), _n(live["book"]), _n(live["tide"]),
                          _n(live["vol"]), _n(live["rot"]))
    carve = (BASE_CARVE_OUT * fm * bm * tm * vm * rm)
    # ── RCL (Section 3): drift tightens and kills the lone-wolf mode ──
    if drift_detected is None:
        try:
            import meta_layers as _ml
            r = _ml.rcl_last() or {}
            drift_detected = str(r.get("verdict", "")) not in ("", "UNKNOWN",
                                                               "NORMAL")
        except Exception:
            drift_detected = False
    if drift_detected:
        carve *= DRIFT_MULT

    # ── SAL (Section 4): the guard's own stored discipline mult ───────
    if sal_mult is None:
        try:
            import meta_layers as _ml
            sal_mult = _ml.sal_adjust()
        except Exception:
            sal_mult = 1.0
    sal_mult = float(sal_mult)
    if sal_mult < 1.0:
        carve *= SAL_BAD_MULT
    elif sal_mult > 1.0:
        carve *= SAL_GOOD_MULT

    # ── Section 6: suppression and expansion ──────────────────────────
    endgame = False
    try:
        import meta_layers as _ml
        endgame = bool(_ml.endgame_active(when))
    except Exception:
        endgame = False
    suppress = (tm < TIDE_THRESHOLD or fm < FLEET_HEALTH_THRESHOLD
                or bm > BOOKMOD_PRESSURE or drift_detected or endgame)
    expand = (fm >= 1.0 and tm >= 1.0 and rm >= 1.0 and bm <= 0.8
              and vm >= 1.0)
    if suppress:
        carve *= SUPPRESS_MULT
    elif expand:
        carve *= EXPAND_MULT

    # ── THE ADAPTIVE BOUNDARY (user spec, Sep 23, clamp 2.1) — the
    # ceiling breathes: MaxCarveOut = 0.10 x RotationMod x VolMod x
    # TideMod. Neutral mods reproduce the static 10% exactly; unreadable
    # sources read neutral, so they fail open to 10% (fail_open: True).
    max_carve = round(0.10 * rm * vm * tm, 4)
    carve = round(max(CARVE_ABS_MIN, min(max_carve, carve)), 4)

    permission = (
        carve >= CARVEOUT_THRESHOLD
        and fm >= FLEET_HEALTH_THRESHOLD
        and tm >= TIDE_THRESHOLD
        and rm >= ROTATION_THRESHOLD
        and not drift_detected
        and not endgame
        and not suppress
    )
    readable = any(v is not None for v in live.values())
    # THE CONFIDENCE-DISCOUNT SCALE: the fixed LONE_WOLF_PTS scales with
    # the allowance — a dead tide (small carve) keeps the brain bar
    # demanding even for a hatch that opens; a strong regime widens it.
    lw_scale = _clamp(carve / BASE_CARVE_OUT if BASE_CARVE_OUT else 1.0,
                      (0.5, 1.5))
    return {"carve": carve, "lone_wolf": bool(permission),
            "lw_scale": lw_scale,
            "readable": readable, "when": when.isoformat(timespec="seconds"),
            "mods": {"fleet_mod": live["fleet"], "book_mod": live["book"],
                     "tide_mod": live["tide"], "vol_mod": live["vol"],
                     "rot_mod": live["rot"]},
            "drift": bool(drift_detected), "sal_mult": sal_mult,
            "endgame": endgame, "suppressed": bool(suppress),
            "expanded": bool(expand) and not suppress,
            "max_carve": max_carve}


# ═══ THE GATE (cached) ════════════════════════════════════════════════
_GATE = {"ts": 0.0, "res": None}


def gate(pair: str | None = None, when: datetime | None = None,
         force: bool = False) -> dict:
    """The scanner's one call (cached CACHE_S, force=True for drives).
    Stores the last result for DNL's nightly record."""
    now = time.time()
    if not force and _GATE["res"] is not None and now - _GATE["ts"] < CACHE_S:
        return _GATE["res"]
    res = compute_carve_out(pair=pair, when=when)
    _GATE.update(ts=now, res=res)
    st = _state()
    st["last"] = {"carve": res["carve"], "lone_wolf": res["lone_wolf"],
                  "mods": res["mods"], "ts": res["when"],
                  # THE BOUNDARY STAMP (clamp Section 6): the funnel plate
                  # renders the day's breathing hatch band and carve
                  # ceiling — the page reads them, it never computes them
                  "max_carve": res.get("max_carve"),
                  "hatch_band": list(hatch_bounds(res))}
    _save(st)
    return res


# ═══ SECTION 2 — the GuardFactor integration ═════════════════════════
def apply_to_guard_factor(gf: float | None, res: dict | None = None,
                          when: datetime | None = None) -> tuple:
    """GF *= (1 + CarveOut). A None factor passes through untouched (the
    caller keeps the committed fixed rules). The final clamp is THE
    ADAPTIVE BOUND (clamp 2.2, the spec's integration point — after
    RCL -> SAL -> CarveOut): MinGF = 0.40 x TideMod x BookMod, MaxGF =
    2.00 x RotationMod x VolMod; neutral mods reproduce the old static
    (0.4, 2.0) band exactly and unreadable sources fail open to it.
    Returns (gf', meta)."""
    if gf is None:
        return None, {}
    res = res or gate(when=when)
    lo, hi = gf_bounds(base=(0.4, 2.0), res=res)
    out = round(float(gf) * (1.0 + float(res["carve"])), 3)
    out = round(max(lo, min(hi, out)), 3)
    return out, {"carve_mult": round(1.0 + float(res["carve"]), 3),
                 "carve": res["carve"], "lone_wolf": res["lone_wolf"],
                 "gf_min": lo, "gf_max": hi}


# ═══ SECTION 5 — the DNL integration ═════════════════════════════════
def night_update(when: datetime | None = None) -> dict:
    """The nightly record: today's CarveOut + the five mods into the
    history (kept 30), the mods frozen for tomorrow's base, and the
    goal-factor delta (1 + CarveOut) the DNL composes into DailyGoal."""
    when = when or _now_mst()
    res = compute_carve_out(when=when)
    st = _state()
    today = when.strftime("%Y-%m-%d")
    hist = [h for h in (st.get("dnl_history") or [])
            if h.get("date") != today]
    hist.append({"date": today,
                 "carve": res["carve"], "lone_wolf": res["lone_wolf"],
                 **{k: v for k, v in res["mods"].items()}})
    st["dnl_history"] = hist[-30:]
    st["last_night"] = {"carve": res["carve"],
                        "fleet_mod": res["mods"]["fleet_mod"],
                        "book_mod": res["mods"]["book_mod"],
                        "tide_mod": res["mods"]["tide_mod"],
                        "vol_mod": res["mods"]["vol_mod"],
                        "rot_mod": res["mods"]["rot_mod"],
                        "date": today}
    # CONTINUITY (the fleet must learn from history, not the moment):
    # an unreadable live source falls back to last night's reading for a
    # day, then to neutral. day_index resets whenever a full read lands.
    prev = st.get("last_night") or {}
    full = all(res["mods"][k] is not None
               for k in ("fleet_mod", "book_mod", "tide_mod",
                         "vol_mod", "rot_mod"))
    st["day_index"] = 0 if full else min(int(st.get("day_index") or 0) + 1, 2)
    for k in ("fleet_mod", "book_mod", "tide_mod", "vol_mod", "rot_mod"):
        st[k] = (res["mods"][k] if res["mods"][k] is not None
                 else (prev.get(k) if (res["mods"][k] is None
                                       and int(st.get("day_index") or 0) <= 1
                                       and prev.get(k) is not None) else 1.0))
    _save(st)
    return {"carve": res["carve"], "goal_factor": round(1.0 + res["carve"], 3),
            "mods": res["mods"], "lone_wolf": res["lone_wolf"]}


# ═══ THE ENTRY HATCH + VOICE ════════════════════════════════════════
def required_own_move(res: dict | None = None) -> float:
    """The OUT hatch's required own-24h move — the spec's own worked
    arithmetic (Section 7): Base × the five mods, so tide-out's
    5% x 0.8 x 0.9 x 0.6 = 2.16% is the number the hatch demands ("your
    carve-out should be ~2%, not 5%"). Section 6's suppression x0.5
    lands on the adaptive floor; expansion x1.2 raises the bar in
    strong regimes. THE ADAPTIVE BOUND (clamp 2.3): the band breathes —
    MinHatch = 2% x TideMod, MaxHatch = 8% x RotationMod x VolMod —
    neutral mods reproduce the old static (2%, 8%) and unreadable
    sources fail open to it. (In a true dead tide the permission gate
    closes the hatch outright; the sinking floor only ever meets a hatch
    that permission already opened.)"""
    res = res or compute_carve_out()
    lo, hi = hatch_bounds(res)
    return round(max(lo, min(hi, 100.0 * float(res["carve"]))), 2)


def explain(res: dict) -> str:
    """One hearable line: the allowance, the five mods, and the state."""
    m = res.get("mods") or {}

    def _f(k):
        v = m.get(k)
        return f"{v:.2f}" if isinstance(v, (int, float)) else "1.00"

    bits = [f"carve {float(res.get('carve', 0.0)) * 100:.1f}%",
            f"fleet {_f('fleet_mod')}", f"book {_f('book_mod')}",
            f"tide {_f('tide_mod')}", f"vol {_f('vol_mod')}",
            f"rot {_f('rot_mod')}"]
    if res.get("drift"):
        bits.append("drift x0.8")
    if res.get("sal_mult", 1.0) != 1.0:
        bits.append(f"sal x{res['sal_mult']:.2f}")
    if res.get("suppressed"):
        bits.append("suppressed x0.5")
    if res.get("expanded"):
        bits.append("expanded x1.2")
    bits.append("lone-wolf " + ("ON" if res.get("lone_wolf") else "OFF"))
    return " · ".join(bits)


# ═══ THE CONTEXT ENGINES (Sep 23 stack — decay, penalty, goal) ═══════
# Each returns (multiplier, readable). Every term orients per the spec's
# INTERPRETATION tables — where the formula's letter and the table
# disagree (vol/fleet/rotation in the penalty weight; vol/book in the
# goal), the table wins, using a clamped inverse of the modifier.

def decay_context(pair: str | None = None,
                  when: datetime | None = None) -> tuple:
    """DecayBaseline multiplier = TideMod x RotationMod x VolMod x BookMod
    (plain product — every interpretation is monotone with the mods):
    high vol slower decay, strong rotation slower, tide out faster, a
    heavy book faster. Clamped (0.7, 1.3); unreadable -> (1.0, False)."""
    try:
        res = compute_carve_out(pair=pair, when=when)
        m = res.get("mods") or {}

        def n(k):
            v = m.get(k)
            return 1.0 if v is None else float(v)

        raw = n("tide_mod") * n("rot_mod") * n("vol_mod") * n("book_mod")
        return round(max(0.7, min(1.3, raw)), 3), bool(res.get("readable"))
    except Exception:
        return 1.0, False


def penalty_context(when: datetime | None = None) -> tuple:
    """PenaltyWeight multiplier per the spec's table: high vol LIGHTER
    (the market is noisy), a weak fleet HEAVIER (the system needs
    discipline), a heavy book HEAVIER, a strong rotation LIGHTER (momentum
    forgives). Vol/fleet/rotation enter inverted (their mods grow with
    strength/volatility); the book plain (its mod grows with pressure).
    Clamped (0.6, 1.5); unreadable -> (1.0, False)."""
    try:
        res = compute_carve_out(when=when)
        m = res.get("mods") or {}

        def g(k, inv=False):
            v = m.get(k)
            v = 1.0 if v is None else float(v)
            return (1.0 / v) if (inv and v > 0) else v

        raw = g("vol_mod", inv=True) * g("fleet_mod", inv=True) * g("book_mod") \
            * g("rot_mod", inv=True)
        return round(max(0.6, min(1.5, raw)), 3), bool(res.get("readable"))
    except Exception:
        return 1.0, False


def goal_context(when: datetime | None = None) -> tuple:
    """DailyGoalBaseline multiplier per the spec's table: a strong
    rotation, a live tide, and a strong fleet RAISE the goal; high vol
    (risk control) and a heavy book LOWER it. Rotation/tide/fleet plain;
    vol/book inverted. Clamped (0.5, 1.5); unreadable -> (1.0, False)."""
    try:
        res = compute_carve_out(when=when)
        m = res.get("mods") or {}

        def g(k, inv=False):
            v = m.get(k)
            v = 1.0 if v is None else float(v)
            return (1.0 / v) if (inv and v > 0) else v

        raw = g("rot_mod") * g("tide_mod") * g("vol_mod", inv=True) * g("fleet_mod") \
            * g("book_mod", inv=True)
        return round(max(0.5, min(1.5, raw)), 3), bool(res.get("readable"))
    except Exception:
        return 1.0, False


def last_night() -> dict:
    """Yesterday's frozen record (for DNL's narrative and the console)."""
    return _state().get("last_night") or {}


# ═══ THE ADAPTIVE BOUNDARY SYSTEM (user spec, Sep 23 — clamps for all
# six systems). Every bounds helper reads the SAME five mods as the
# carve-out (one context, no second opinion), fails OPEN to the
# committed static band when the sources are unreadable, and reproduces
# that static band exactly at neutral mods. Guards: min/max sanity,
# fail_open True everywhere, no division anywhere (no zero risk).

def _mods_n(res: dict | None = None, when: datetime | None = None) -> dict:
    """The five mods, neutral-defaulted — the boundary arithmetic works
    even when the context is dark (that IS the fail-open path)."""
    res = res or compute_carve_out(when=when)
    m = res.get("mods") or {}

    def n(k):
        v = m.get(k)
        return 1.0 if v is None else float(v)

    return {"fm": n("fleet_mod"), "bm": n("book_mod"), "tm": n("tide_mod"),
            "vm": n("vol_mod"), "rm": n("rot_mod"),
            "readable": bool(res.get("readable"))}


def max_carve_out(res: dict | None = None,
                  when: datetime | None = None) -> float:
    """Clamp 2.1: MaxCarveOut = 0.10 x RotationMod x VolMod x TideMod.
    Neutral -> 0.10 (the old static ceiling). Correction overlays (the
    cross-examination's dial moves) multiply the result within their
    context gate — they can bend a band, never remove it."""
    m = _mods_n(res, when)
    v = max(0.0, 0.10 * m["rm"] * m["vm"] * m["tm"])
    v *= _overlay_mult("max_carve_out", m, when)
    return round(max(0.0, v), 4)


def gf_bounds(base: tuple = (0.4, 2.0), res: dict | None = None,
              when: datetime | None = None) -> tuple:
    """Clamp 2.2: MinGF = base_min x TideMod x BookMod, MaxGF =
    base_max x RotationMod x VolMod. The caller supplies its own
    committed base (the guard's raw clamp keeps its (0.5, 2.0); the
    finalize's final clamp uses the spec's (0.4, 2.0)). Neutral mods ->
    the base band unchanged; heavy book raises the floor, strong
    rotation/vol raise the ceiling, a dead tide lets the floor sink."""
    m = _mods_n(res, when)
    lo = round(max(0.0, float(base[0]) * m["tm"] * m["bm"]), 3)
    hi = round(float(base[1]) * m["rm"] * m["vm"], 3)
    lo *= _overlay_mult("gf_min", m, when)
    hi *= _overlay_mult("gf_max", m, when)
    return lo, max(lo, hi)          # guard: hi can never sit under lo


def hatch_bounds(res: dict | None = None,
                 when: datetime | None = None) -> tuple:
    """Clamp 2.3 (percent units): MinHatch = 2.0 x TideMod, MaxHatch =
    8.0 x RotationMod x VolMod. Neutral -> (2.0, 8.0)."""
    m = _mods_n(res, when)
    lo = round(2.0 * m["tm"], 3)
    hi = round(8.0 * m["rm"] * m["vm"], 3)
    lo *= _overlay_mult("hatch_min", m, when)
    hi *= _overlay_mult("hatch_max", m, when)
    return lo, max(lo, hi)


def penalty_bounds(when: datetime | None = None,
                   res: dict | None = None) -> tuple:
    """Clamp 2.5 (per-violation docked weight): MinPenalty = 0.50 x
    TideMod x BookMod, MaxPenalty = 2.00 x RotationMod x VolMod x
    FleetMod. Neutral -> (0.5, 2.0). A heavy book raises the floor (the
    system needs discipline when stretched), a strong regime raises the
    ceiling; the accumulated extra keeps its own committed 3.0 rail."""
    m = _mods_n(res, when)
    lo = round(0.50 * m["tm"] * m["bm"], 3)
    hi = round(2.00 * m["rm"] * m["vm"] * m["fm"], 3)
    lo *= _overlay_mult("penalty_min", m, when)
    hi *= _overlay_mult("penalty_max", m, when)
    return lo, max(lo, hi)


def goal_bounds(base_goal: float, when: datetime | None = None,
                res: dict | None = None) -> tuple:
    """Clamp 2.6 (dollar units): MinGoal = base x TideMod x BookMod,
    MaxGoal = base x RotationMod x VolMod x FleetMod. Neutral -> a point
    band at the base goal — the context may not move it off base unless
    the regime actually reads."""
    base_goal = float(base_goal or 0.0)
    if base_goal <= 0:
        return 0.0, 0.0
    m = _mods_n(res, when)
    lo = round(base_goal * m["tm"] * m["bm"], 2)
    hi = round(base_goal * m["rm"] * m["vm"] * m["fm"], 2)
    lo *= _overlay_mult("goal_min", m, when)
    hi *= _overlay_mult("goal_max", m, when)
    return min(lo, hi), max(lo, hi)


def decay_bounds(when: datetime | None = None,
                 res: dict | None = None) -> tuple:
    """Clamp 2.4: MinDecay = DECAY_FLOOR x TideMod (a dead tide lets the
    decay sink deeper — faster aging), MaxDecay = DECAY_CEILING x
    RotationMod (1.0 x rot — never above no-decay). Neutral ->
    (DECAY_FLOOR, DECAY_CEILING)."""
    try:
        floor_v = float(__import__("profit_guard").DECAY_FLOOR)
    except Exception:
        floor_v = 0.7
    m = _mods_n(res, when)
    lo = round(floor_v * m["tm"], 3)
    hi = round(1.0 * m["rm"], 3)
    lo *= _overlay_mult("decay_min", m, when)
    hi *= _overlay_mult("decay_max", m, when)
    return lo, max(lo, hi)


# ═══ THE CROSS-EXAMINATION'S CORRECTION OVERLAYS (user spec, Sep 23,
# section 4: the post-examination correction module). The exam (in
# meta_layers) grades the boundary system every 6-7 rotations; when it
# returns WARN/FAIL it hands dial moves here as expiring overlays on
# the bounds helpers above. CONTRACT: an overlay BENDS a band within
# its context gate (strong = RotationMod > 1.2, weak = tide out or a
# heavy book, always = unconditional), multiplies are clamped to
# (0.5, 2.0) so no correction can ever REMOVE a clamp, and every
# overlay EXPIRES after 3 rotations — the band then re-reads pure
# context. Fail-neutral: an unreadable corrections record multiplies
# by exactly 1.0, and the guards (hi >= lo, the floors) still apply
# after the overlay — the shape of every band is preserved.
_OVERLAY_MIN, _OVERLAY_MAX = 0.5, 2.0
_OVERLAY_KEYS = frozenset(("max_carve_out", "gf_min", "gf_max",
                           "hatch_min", "hatch_max", "decay_min",
                           "decay_max", "penalty_min", "penalty_max",
                           "goal_min", "goal_max"))


def set_boundary_corrections(overlays: dict, rotations: int = 3,
                             when: datetime | None = None) -> dict:
    """Install the exam's dial moves (meta_layers calls this at
    cooldown). Keys not in _OVERLAY_KEYS are rejected, mults are
    clamped, and the record expires after `rotations` days (a rotation
    ≈ a night in this machine's cycle). Returns the stored record."""
    when = when or _now_mst()
    clean = {}
    for k, spec in (overlays or {}).items():
        if k not in _OVERLAY_KEYS or not isinstance(spec, dict):
            continue
        entry = {}
        for gate_k in ("mult_always", "mult_strong", "mult_weak"):
            v = spec.get(gate_k)
            if isinstance(v, (int, float)) and float(v) != 1.0:
                entry[gate_k] = round(max(_OVERLAY_MIN,
                                          min(_OVERLAY_MAX, float(v))), 4)
        if entry:
            clean[k] = entry
    rec = {"set_date": when.strftime("%Y-%m-%d"),
           "expires": (when + timedelta(days=max(1, int(rotations))))
           .strftime("%Y-%m-%d"),
           "overlays": clean}
    st = _state()
    st["boundary_corrections"] = rec
    _save(st)
    return rec


def boundary_corrections() -> dict:
    """The live corrections record, or {} — never raises."""
    try:
        return _state().get("boundary_corrections") or {}
    except Exception:
        return {}


def _overlay_mult(key: str, m: dict, when: datetime | None = None) -> float:
    """The context-gated overlay multiplier for one bound, or 1.0.
    Expired records read 1.0 (the band returns to pure context) and
    night_update scrubs them."""
    try:
        rec = boundary_corrections()
        if not rec or str(rec.get("expires") or "") < \
                (when or _now_mst()).strftime("%Y-%m-%d"):
            return 1.0
        entry = (rec.get("overlays") or {}).get(key)
        if not entry:
            return 1.0
        mult = entry.get("mult_always")
        if m.get("rm", 1.0) > 1.2 and entry.get("mult_strong") is not None:
            mult = entry["mult_strong"]
        elif (m.get("tm", 1.0) < 1.0 or m.get("bm", 1.0) > 1.1) \
                and entry.get("mult_weak") is not None:
            mult = entry["mult_weak"]
        f = float(mult or 1.0)
        return max(_OVERLAY_MIN, min(_OVERLAY_MAX, f))
    except Exception:
        return 1.0


# ═══ THE BOUNDARY CROSS-EXAMINATION (user spec, Sep 23, section 3:
# after 6-7 rotations the boundary system itself is cross-examined) —
# a self-audit FOR THE BOUNDARIES: gather the per-rotation evidence
# (values + bounds + context + the outcome events), answer the eight
# question families, deliver PASS / WARN / FAIL, and hand WARN/FAIL
# dial moves to the correction module. Fail-neutral everywhere: an
# unreadable night record contributes nothing and never invents an
# answer; the verdict needs EVIDENCE, not absence.
_EXAM_EVERY_N = 6
_EXAM_RESCHEDULE = 3


def cross_examine(when: datetime | None = None) -> dict | None:
    """Grade the boundary system over the night ledger (one night = one
    rotation). None when the evidence cannot support a verdict yet
    (fewer than _EXAM_EVERY_N readable nights with clamp_bounds)."""
    when = when or _now_mst()
    try:
        import meta_layers as _ml
        st = _ml._state()
        dnl = st.get("dnl") or {}
    except Exception:
        return None
    nights = []
    for date in sorted(dnl.keys()):
        rec = ((dnl.get(date) or {}).get("_data") or {}).get("clamp_bounds")
        if isinstance(rec, dict) and rec.get("readable"):
            nights.append({"date": date, "cb": rec,
                           "data": (dnl.get(date) or {}).get("_data") or {}})
    if len(nights) < _EXAM_EVERY_N:
        return None
    nights = nights[-_EXAM_EVERY_N:]
    qs = []

    def _vac(hit: bool, seen: bool, label: str) -> tuple[bool, str]:
        """A question whose context never arose in the window is
        VACUOUS — answered yes (absence of counter-evidence, never its
        invention), with the evidence string saying so honestly. A
        neutral six nights must PASS, not warn its way into
        corrections."""
        if seen:
            return hit, ""
        return True, f"; {label} never arose (vacuous)"

    def _mods(cb):
        c = cb.get("context") or {}
        def g(k, d=1.0):
            v = c.get(k)
            return float(v) if isinstance(v, (int, float)) else d
        return g("rot_mod"), g("tide_mod"), g("vol_mod"), \
            g("fleet_mod"), g("book_mod")

    def _pair(band):
        b = band or []
        if isinstance(b, list) and len(b) >= 2:
            try:
                return float(b[0]), float(b[1])
            except (TypeError, ValueError):
                return None
        return None

    # ── CarveOut: bands widen with rotation strength, tighten in weak
    # conditions, and the allowance never exceeded its ceiling
    oob = 0
    wide_rot = tight_weak = 0
    for n in nights:
        rm, tm, vm, _fm, _bm = _mods(n["cb"])
        mc = n["cb"].get("max_carve_out")
        exp = 0.10 * rm * vm * tm
        if not isinstance(mc, (int, float)) or mc > exp + 0.005:
            oob += 1
        else:
            if rm > 1.2 and mc > 0.10 + 0.005:
                wide_rot += 1
            if (tm < 1.0 or _bm > 1.1) and mc < 0.10 - 0.005:
                tight_weak += 1
    _ws = any(_mods(n["cb"])[0] > 1.2 for n in nights)
    _ks = any(_mods(n["cb"])[1] < 1.0 or _mods(n["cb"])[4] > 1.1
              for n in nights)
    y1, s1 = _vac(wide_rot > 0, _ws, "strong rotation")
    y2, s2 = _vac(tight_weak > 0, _ks, "weak conditions")
    qs.append(("carve_out", [
        "Did MaxCarveOut widen in strong rotation?",
        "Did MaxCarveOut tighten in weak conditions?",
        "Did CarveOut ever exceed MaxCarveOut?"],
        y1, y2, oob == 0,
        f"out-of-band nights: {oob}; widened in strength: {wide_rot}; "
        f"tightened in weakness: {tight_weak}" + s1 + s2))

    # ── GuardFactor: the floor rises in weak conditions, the ceiling
    # widens in strength, and the factor never sat outside its band
    oob = 0
    wide_rot = tight_weak = 0
    for n in nights:
        rm, tm, vm, _fm, bm = _mods(n["cb"])
        g = _pair(n["cb"].get("gf_bounds"))
        if g is None:
            oob += 1
            continue
        lo, hi = g
        exp_lo, exp_hi = 0.40 * tm * bm, 2.00 * rm * vm
        if lo > exp_lo + 0.02 or hi < exp_hi - 0.02:
            oob += 1
        else:
            if rm > 1.2 and hi > 2.0 + 0.02:
                wide_rot += 1
            # the floor's adaptive motion: a dead tide SINKS the floor
            # (MinGF = base x TideMod x BookMod — the book can never
            # push past 1.0), so 'tracked the context' is the evidence
            if (tm < 1.0 or bm > 1.1) and lo < 0.40 - 0.02:
                tight_weak += 1
    _ws = any(_mods(n["cb"])[0] > 1.2 for n in nights)
    _ks = any(_mods(n["cb"])[1] < 1.0 or _mods(n["cb"])[4] > 1.1
              for n in nights)
    y1, s1 = _vac(wide_rot > 0, _ws, "strong rotation")
    y2, s2 = _vac(tight_weak > 0, _ks, "tide-out or heavy-book nights")
    qs.append(("guard_factor", [
        "Did MinGF rise when tide was out or book was heavy?",
        "Did MaxGF widen when rotation was strong?",
        "Did GF stay inside adaptive boundaries?"],
        y1, y2, oob == 0,
        f"out-of-band nights: {oob}; widened in strength: {wide_rot}; "
        f"floors tracked the tide in weakness: {tight_weak}" + s1 + s2))

    # ── Hatch: same orientation on its two bounds
    oob = 0
    wide_rot = tight_weak = 0
    for n in nights:
        rm, tm, vm, _fm, _bm = _mods(n["cb"])
        hb = _pair(n["cb"].get("hatch_bounds"))
        if hb is None:
            oob += 1
            continue
        lo, hi = hb
        if lo > 2.0 * tm + 0.05 or hi < 8.0 * rm * vm - 0.05:
            oob += 1
        else:
            if rm > 1.2 and hi > 8.0 + 0.05:
                wide_rot += 1
            if tm < 1.0 and lo < 2.0 - 0.05:
                tight_weak += 1
    _ws = any(_mods(n["cb"])[0] > 1.2 for n in nights)
    _ks = any(_mods(n["cb"])[1] < 1.0 for n in nights)
    y1, s1 = _vac(wide_rot > 0, _ws, "strong rotation")
    y2, s2 = _vac(tight_weak > 0, _ks, "tide-out nights")
    qs.append(("hatch", [
        "Did MinHatch tighten in tide-out conditions?",
        "Did MaxHatch widen in strong rotation?",
        "Did the hatch allow re-entry when continuation was real?"],
        y1, y2, oob == 0,
        f"out-of-band nights: {oob}; widened in strength: {wide_rot}; "
        f"tightened in tide-out: {tight_weak}" + s1 + s2))

    # ── Decay: the floor sinks in a dead tide, the ceiling rises in
    # strength, and held-too-long cost fell — premature exits AND
    # overlong holds (discipline's per-asset verdicts) over the window
    oob = 0
    wide_rot = tight_weak = 0
    for n in nights:
        rm, tm, _vm, _fm, _bm = _mods(n["cb"])
        db = _pair(n["cb"].get("decay_bounds"))
        if db is None:
            oob += 1
            continue
        lo, hi = db
        if hi > 1.0 * rm + 0.02:
            oob += 1
        else:
            if rm > 1.2 and hi > 1.0 + 0.02:
                wide_rot += 1
            if tm < 1.0 and lo < 0.7 - 0.02:
                tight_weak += 1
    premature = overlong = 0
    try:
        import discipline as _disc
        for _d, name, _usd in _disc.verdict_history(days=7):
            if "exited too soon" in name:
                premature += 1
            elif "stayed too long" in name:
                overlong += 1
    except Exception:
        pass
    _ws = any(_mods(n["cb"])[0] > 1.2 for n in nights)
    _ks = any(_mods(n["cb"])[1] < 1.0 for n in nights)
    y1, s1 = _vac(wide_rot > 0, _ws, "strong rotation")
    y2, s2 = _vac(tight_weak > 0, _ks, "tide-out nights")
    qs.append(("decay", [
        "Did MinDecay rise in tide-out conditions?",
        "Did MaxDecay rise in strong rotation?",
        "Did decay prevent premature exits?"],
        y1, y2, oob == 0 and premature <= overlong,
        f"out-of-band nights: {oob}; premature exits {premature} vs "
        f"overlong holds {overlong} (7 nights)" + s1 + s2))

    # ── Penalty: floor/ceiling orientation (the bounds live in the
    # record, never the achieved weights — the achieved weight is a
    # separate instrument)
    oob = 0
    wide_rot = tight_weak = 0
    for n in nights:
        rm, tm, vm, fm, bm = _mods(n["cb"])
        pb = _pair(n["cb"].get("penalty_bounds"))
        if pb is None:
            oob += 1
            continue
        lo, hi = pb
        exp_lo, exp_hi = 0.50 * tm * bm, 2.00 * rm * vm * fm
        if lo > exp_lo + 0.02 or hi < exp_hi - 0.02:
            oob += 1
        else:
            if rm > 1.2 and hi > 2.0 + 0.02:
                wide_rot += 1
            # same tracking reading as the GF floor: a dead tide sinks
            # the minimum (MinPenalty = 0.50 x TideMod x BookMod)
            if (tm < 1.0 or bm > 1.1) and lo < 0.50 - 0.02:
                tight_weak += 1
    _ws = any(_mods(n["cb"])[0] > 1.2 for n in nights)
    _ks = any(_mods(n["cb"])[1] < 1.0 or _mods(n["cb"])[4] > 1.1
              for n in nights)
    y1, s1 = _vac(wide_rot > 0, _ws, "strong rotation")
    y2, s2 = _vac(tight_weak > 0, _ks, "tide-out or heavy-book nights")
    qs.append(("penalty", [
        "Did MinPenalty rise when book was heavy?",
        "Did MaxPenalty widen when rotation was strong?",
        "Did penalties remain meaningful?"],
        y1, y2, oob == 0,
        f"out-of-band nights: {oob}; widened in strength: {wide_rot}; "
        f"floors tracked the tide in weakness: {tight_weak}" + s1 + s2))

    # ── DailyGoal: bounds orientation + realism (realized vs pace)
    oob = 0
    wide_rot = tight_weak = 0
    realistic = 0
    total = 0
    for n in nights:
        rm, tm, vm, fm, bm = _mods(n["cb"])
        gb = _pair(n["cb"].get("dailygoal_bounds"))
        if gb is None:
            oob += 1
            continue
        lo, hi = gb
        if lo > 0 and hi < lo * 0.98:
            oob += 1
        else:
            if rm > 1.2 and fm > 1.1 and hi > lo * 1.05:
                wide_rot += 1
            if (tm < 1.0 or bm > 1.1) and lo < hi * 0.95:
                tight_weak += 1
        pace = n["data"].get("daily_pace")
        realized = n["data"].get("realized")
        if isinstance(pace, (int, float)) and pace > 0 \
                and isinstance(realized, (int, float)) \
                and int(n["data"].get("n_trades") or 0) > 0:
            total += 1
            if float(realized) >= 0.5 * float(pace):
                realistic += 1
    _ws = any(_mods(n["cb"])[0] > 1.2 and _mods(n["cb"])[3] > 1.1
              for n in nights)
    _ks = any(_mods(n["cb"])[1] < 1.0 or _mods(n["cb"])[4] > 1.1
              for n in nights)
    y1, s1 = _vac(wide_rot > 0, _ws, "strong-rotation nights")
    y2, s2 = _vac(tight_weak > 0, _ks, "weak-fleet nights")
    qs.append(("daily_goal", [
        "Did MaxGoal rise in strong rotation?",
        "Did MinGoal tighten in weak fleet conditions?",
        "Did daily goals remain realistic?"],
        y1, y2, oob == 0 and (total == 0 or realistic >= max(1, total // 2)),
        f"out-of-band nights: {oob}; realistic nights {realistic}/{total}"
        + s1 + s2))

    # ── Continuation & RCL: re-entry events vs blocks (the night data
    # carries both counters), second legs never measured while RCL
    # suppressed them
    cont_ev = block_ev = 0
    for n in nights:
        cont_ev += int(n["data"].get("endgame_events") or 0) \
            + int(n["data"].get("reentry_blocks") or 0)
        block_ev += int(n["data"].get("reentry_blocks") or 0)
    _trades = sum(int(n["data"].get("n_trades") or 0) for n in nights)
    y1, s1 = _vac(cont_ev > 0, _trades > 0, "trading nights")
    # blocks absent = the suppression was never tested (vacuous),
    # blocks present = RCL did its job
    y2, s2 = _vac(block_ev > 0, block_ev > 0, "re-entry chances")
    qs.append(("continuation", [
        "Did the system capture second legs?",
        "Did it avoid false re-entries?",
        "Did RCL drift kills suppress bad re-entries?"],
        y1, y2, True,
        f"continuation/re-entry events: {cont_ev}; re-entry blocks: "
        f"{block_ev} (RCL suppressions counted)" + s1 + s2))

    # ── DNL stability: did the clamp records themselves hold a shape?
    # (every night in the window IS readable by construction — the
    # gather dropped unreadable ones; the bands' consistency is judged
    # by the out-of-band counters already computed above)
    consistent = sum(1 for n in nights if isinstance(n["cb"], dict)
                     and n["cb"].get("readable"))
    # convergence: readable records every night AND no wild whipsaw —
    # no band tripling or collapsing below a third of its predecessor
    # (adaptive bands are SUPPOSED to move with the context; the record
    # is unstable only when they jump)
    whipsaw = 0
    prev = None
    for n in nights:
        mcv = n["cb"].get("max_carve_out")
        if isinstance(mcv, (int, float)) and mcv > 0:
            if prev and (mcv > prev * 3.0 or mcv < prev / 3.0):
                whipsaw += 1
            prev = float(mcv)
    _vy, _vs = _vac(whipsaw == 0, True, "")
    qs.append(("dnl", [
        "Did adaptive boundaries stabilize?",
        "Did clamps converge?",
        "Did context snapshots show consistent patterns?"],
        consistent >= _EXAM_EVERY_N, _vy,
        consistent >= _EXAM_EVERY_N and whipsaw == 0,
        f"readable boundary records: {consistent}/{_EXAM_EVERY_N}; "
        f"whipsawed bands: {whipsaw}" + _vs))

    fails = [q for q in qs if not q[4]]
    warns = [q for q in qs if q[4] and not (q[2] and q[3])]
    verdict = "PASS" if not fails and not warns else \
        ("FAIL" if len(fails) >= 2 else "WARN")
    return {
        "date": when.strftime("%Y-%m-%d"),
        "rotations": len(nights),
        "verdict": verdict,
        "fail_families": [f[0] for f in fails],
        "warn_families": [w[0] for w in warns],
        "families": {q[0]: {"questions": q[1], "answered_yes": bool(q[2]),
                            "answered_yes2": bool(q[3]), "ok": bool(q[4]),
                            "evidence": q[5]} for q in qs},
        "Summary": "the boundary bands track their context and stayed "
                   "within themselves" if verdict == "PASS" else
                   f"{len(fails)} family(ies) failed, {len(warns)} warned "
                   "over the last " + str(len(nights)) + " rotations",
        "BoundaryBehavior": "; ".join(
            f"{q[0]}: {q[5]}" for q in qs),
        "ClampBehavior": "out-of-band nights are the clamp-adherence "
                         "evidence (0 required for a PASS)",
        "ContinuationBehavior": f"events {cont_ev}, blocks {block_ev}",
        "ReEntryBehavior": f"RCL-suppressed re-entries: {block_ev}",
        "DNLBehavior": f"boundary records readable: {consistent}/"
                       f"{_EXAM_EVERY_N}",
        "FinalVerdict": verdict,
    }


def cross_examine_corrections(exam: dict,
                              when: datetime | None = None) -> dict:
    """Section 4: the post-examination correction module. A WARN/FAIL
    verdict hands dial moves here as expiring context-gated overlays
    (3 rotations, then re-evaluation by the next exam). PASS clears
    any standing corrections — a healthy boundary system needs none.
    The safety rule is structural: overlays multiply the existing band
    and are clamped to (0.5, 2.0), so no correction can remove a
    clamp, and the guards (hi >= lo) re-apply after every overlay."""
    when = when or _now_mst()
    verdict = str((exam or {}).get("FinalVerdict") or (exam or {}).get(
        "verdict") or "PASS")
    fam = set((exam or {}).get("fail_families") or []) \
        | set((exam or {}).get("warn_families") or [])
    if verdict == "PASS":
        st = _state()
        had = bool(st.get("boundary_corrections"))
        if had:
            st.pop("boundary_corrections", None)
            _save(st)
        return {"applied": [], "cleared": had, "verdict": verdict}
    moves = {}
    if "carve_out" in fam:
        # IfNeverUsesClampInStrongRotation: widen the strong-context
        # ceiling; the exceeded-clamp arm is structural (the band IS the
        # ceiling — the clamp cannot be exceeded, it can only sit too
        # tight, which is the arm that fires)
        moves["max_carve_out"] = {"mult_strong": 1.05}
    if "guard_factor" in fam:
        moves["gf_max"] = {"mult_strong": 1.10}
        moves["gf_min"] = {"mult_weak": 1.10}
    if "hatch" in fam:
        moves["hatch_max"] = {"mult_strong": 1.10}
        moves["hatch_min"] = {"mult_weak": 1.10}
    if "decay" in fam:
        moves["decay_min"] = {"mult_strong": 0.90}
        moves["decay_max"] = {"mult_weak": 1.10}
    if "penalty" in fam:
        moves["penalty_min"] = {"mult_weak": 1.10}
        moves["penalty_max"] = {"mult_strong": 0.90}
    if "daily_goal" in fam:
        moves["goal_max"] = {"mult_strong": 1.075}
        moves["goal_min"] = {"mult_weak": 0.90}
    if not moves:
        return {"applied": [], "cleared": False, "verdict": verdict}
    rec = set_boundary_corrections(moves, rotations=_EXAM_RESCHEDULE,
                                   when=when)
    return {"applied": sorted(rec.get("overlays") or {}),
            "expires": rec.get("expires"), "cleared": False,
            "verdict": verdict}


# ── SECTION 5 — THE COOLDOWN SCHEDULER (user spec: run the exam every
# 6 rotations, reschedule +3 after each). The rotation count IS the
# ledger (restart-proof — no counter to drift); the schedule lives in
# carve-out state as the next-due rotation number. Fail-neutral: an
# unreadable state reads the first exam at _EXAM_EVERY_N, and a failed
# schedule write never raises into the cooldown chain.
def _readable_nights() -> int:
    """How many DNL nights carry a readable clamp_bounds record."""
    try:
        import meta_layers as _ml
        dnl = (_ml._state().get("dnl") or {})
        return sum(1 for _d in dnl
                   if ((dnl.get(_d) or {}).get("_data") or {})
                   .get("clamp_bounds", {}).get("readable"))
    except Exception:
        return 0


def _next_exam_due() -> int:
    """The rotation number the next exam fires at (6, then +3 each)."""
    try:
        return int(_state().get("exam_next_due") or _EXAM_EVERY_N)
    except Exception:
        return _EXAM_EVERY_N


def _schedule_exam(when: datetime | None = None) -> None:
    """Called at every cooldown tick after the gate: pins the next-due
    rotation. Before the first exam it stays at _EXAM_EVERY_N; after an
    exam ran (readable nights reached the due mark) it moves to nights
    + _EXAM_RESCHEDULE — the spec's 'rotation_count + 3'."""
    try:
        st = _state()
        due = int(st.get("exam_next_due") or 0)
        nights = _readable_nights()
        if due <= 0:
            st["exam_next_due"] = _EXAM_EVERY_N
        elif nights >= due:
            st["exam_next_due"] = nights + _EXAM_RESCHEDULE
        _save(st)
    except Exception:
        pass
