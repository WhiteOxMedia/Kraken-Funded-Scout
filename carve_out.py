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
clamped [0, 10%] — a reading can never let a candidate through with more
than a tenth of extra allowance.
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
CARVE_CLAMP = (0.0, 0.10)      # the final allowance never exceeds 10%

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

    carve = _clamp(carve, CARVE_CLAMP)

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
            "expanded": bool(expand) and not suppress}


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
                  "mods": res["mods"], "ts": res["when"]}
    _save(st)
    return res


# ═══ SECTION 2 — the GuardFactor integration ═════════════════════════
def apply_to_guard_factor(gf: float | None, res: dict | None = None,
                          when: datetime | None = None) -> tuple:
    """GF *= (1 + CarveOut). A None factor passes through untouched (the
    caller keeps the committed fixed rules). Returns (gf', meta)."""
    if gf is None:
        return None, {}
    res = res or gate(when=when)
    out = round(float(gf) * (1.0 + float(res["carve"])), 3)
    out = round(max(0.4, min(2.0, out)), 3)
    return out, {"carve_mult": round(1.0 + float(res["carve"]), 3),
                 "carve": res["carve"], "lone_wolf": res["lone_wolf"]}


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
    lands on the 2% floor (still ~2%); expansion x1.2 raises the bar in
    strong regimes — a solo move means more when the whole fleet runs.
    Floors at 2%, ceilings at 8% — the dial never leaves solo-runner
    territory."""
    res = res or compute_carve_out()
    return round(max(2.0, min(8.0, 100.0 * float(res["carve"]))), 2)


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
