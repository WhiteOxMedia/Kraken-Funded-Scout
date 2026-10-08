"""THE ADAPTIVE PROFIT GUARD (user spec, Sep 22) — the fixed trailing rules
are replaced by a context-adaptive factor that reads the market the
scanner already lives in, and learns from each day's results.

THE TEN FORMULAS (user spec, verbatim intent):

  1. EnvScore   = w_r * RotationStrength + w_t * TIDE
       RotationStrength 1-5 from the volatility engine's day-schedule cell
       (expected move + liquidity + breakout quality); TIDE 0-5 from the
       fleet breadth (0=OUT, 3=FLAT, 5=RISING). w_r > w_t: rotation leads.
  2. DiscMod    = 1 - (DisciplinePenalty / 10)          (typ. 0.7-1.0)
       Penalty from the live discipline score's own gap to 100.
  3. MLT_Corr   = MoneyLeftOnTable / DailyGoal
       Positive: yesterday left money -> the guard was too tight -> loosen.
       Near zero: the guard was fine. Negative: the guard SAVED losses ->
       tighten. The learning loop.
  4. VolMod     = CurrentVol / BaselineVol              (clamped 0.7-1.4)
  5. AssetMemory= avg_run_length(asset) / fleet_avg_run_length
                                                                  (clamped 0.7-1.4)
  6. FundingMod = 1.1 rising | 0.9 falling | 1.0 otherwise
  7. DecayMod   = exp(-t / tau), tau = 90 min, floored at 0.7
  8. GuardFactor= EnvScore * DiscMod * (1 + MLT_Corr) * VolMod
                          * AssetMemory * FundingMod * DecayMod   (clamped 0.5-2.0)
  9. TrailPct   = BaseTrailPct * GuardFactor
     LockInPct   = BaseLockInPct / GuardFactor
 10. The cooldown loop recomputes all of it once per MST day and stores
     GuardFactor for tomorrow's learning (restart-proof, date-marked).

BINDING INTO THE EXIT WATCH (how 9 lands on the live position):
  The running trail  = the ratchet's own peak-aware width * GF — weak
  context tightens it immediately, strength gives the climb room.
  The ARMED cap      = goal * GOAL_TRAIL_ARM_FRAC * (BASE_LOCK_IN /
  lockin_pct) — and since lockin_pct = BASE_LOCK_IN / GF, the cap is
  goal * GOAL_TRAIL_ARM_FRAC * GF: strength means the armed net waits for
  the FULL goal (later lock-in, let it run), weakness means the net
  grabs at a fraction of it (earlier lock-in). GF = 1 reproduces
  yesterday's half-goal contract exactly — perfect continuity.

MISSING-INPUT HONESTY: every modifier fails soft to `None` when its
data source is unreadable, and a `None` modifier is simply neutral
(1.0) in the product — except EnvScore and MLT_Corr, the two halves of
the learning loop: if BOTH are unreadable the factor is not `readable`
and the exit watch keeps the committed fixed rules rather than invent
a context. One readable half still yields a readable factor.
"""
import hashlib
import json
import math
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ── dials ────────────────────────────────────────────────────────────────
W_ROTATION = 0.6          # rotation slightly higher than TIDE (user spec)
W_TIDE = 0.4
ENV_NEUTRAL = 3.0         # RS 3 / TIDE 3 = a normal rotation. The spec's
                          # bands (GF>1.2 run, 0.9-1.2 normal, <0.9 tighten)
                          # only behave as written if EnvScore is scaled so
                          # a NORMAL context is 1.0 — raw, a typical
                          # rotation scores ~3 and the factor would pin the
                          # 2.0 clamp and never tighten.
DISC_PENALTY_DIVISOR = 10.0
GUARD_CLAMP = (0.5, 2.0)  # <0.9 tighten / 0.9-1.2 normal / >1.2 run
GIVEBACK_DOMINANT_PENALTY = 2.0  # the giveback-dominance dock (Oct 5,
# user ask): when the day's biggest leave was money REACHED and handed
# back, discipline_penalty docks this much MORE — dilution-proof (flat,
# never context-weighted: weighting would re-introduce the dilution),
# floored at the machine's own tier tighten trigger. +2.0 = 20 score
# points of sting; DiscMod 0.8 -> 0.6 on an 80-score day.
VOL_MOD_CLAMP = (0.7, 1.4)
ASSET_MEMORY_CLAMP = (0.7, 1.4)
FUNDING_RISING_MULT = 1.1
FUNDING_FALLING_MULT = 0.9
FUNDING_NEUTRAL_MULT = 1.0
DECAY_TAU_MIN = 90.0
DECAY_FLOOR = 0.7
VOL_WINDOW = 100          # the guard's DIRECT candle window (Sep 23:
                          # deepened 20 -> 100 — a stabler realized ATR;
                          # the "deep history" half stays indirect, the
                          # baseline still comes from the rotation cell's
                          # learned per-cell std over weeks of candles)
BASE_LOCK_IN_PCT = 1.0    # the lock width the armed cap normalizes against

# THE STATE PATH IS RESOLVED PER CALL (Sep 24, the 8.8 lesson): the old
# module-level `STATE_FILE = ROOT / ...` froze the REAL path at import,
# so every drive's `pg.ROOT = sandbox` redirect silently kept reading
# and writing the LIVE production state — the probe passed only while
# the live stamp was stale, and failed the day the scanner stamped a
# fresh factor (the metalayers latent-bomb class, caught red-handed).
# _state_file() now prefers an explicitly-set STATE_FILE (the LIVE
# probes pin it to the real file and restore it byte-exact) and
# otherwise derives from the CURRENT ROOT — production resolves to the
# same file it always did; a redirected probe world is finally hermetic.


# ── 1. the environment ───────────────────────────────────────────────────
def rotation_strength(when: datetime | None = None) -> int | None:
    """RotationStrength 1-5 from the volatility engine's live day cell:
    the median expected move sets the base (1 + move/1.5), then +1 for
    deep liquidity (>= $50k median volume), -1 for thin (< $5k), +1 for
    clean breakouts (false-breakout rate <= 0.10), -1 for choppy
    (>= 0.30). Clamped [1, 5]. Unreadable file/weekend/cell -> None."""
    try:
        when = when or _now_mst()
        sched = "weekend" if when.weekday() >= 5 else "weekday"
        doc = json.loads((ROOT / "rotation_thresholds.json").read_text(encoding="utf-8"))
        from background_scanner import rotation_key_for_hour
        cell = (doc.get("median") or {}).get(sched, {}).get(rotation_key_for_hour(when.hour))
        if not cell:
            return None
        rs = 1 + cell.get("avg_move", 0.0) / 1.5
        vol_usd = float(cell.get("median_volume_usd") or 0)
        if vol_usd >= 50_000:
            rs += 1
        elif vol_usd < 5_000:
            rs -= 1
        fbr = float(cell.get("false_breakout_rate") or 0)
        if fbr <= 0.10:
            rs += 1
        elif fbr >= 0.30:
            rs -= 1
        return int(max(1, min(5, round(rs))))
    except Exception:
        return None


def tide_score() -> float | None:
    """TIDE as 0-5: OUT 0, FLAT 3, RISING 5 — and the live breadth inside
    FLAT interpolates 2-4. Unreadable fleet -> None."""
    try:
        from background_scanner import _fleet_tide, TIDE_RISING_MIN, TIDE_OUT_MAX
        pct, state = _fleet_tide()
        if state == "RISING":
            return 5.0
        if state == "OUT":
            return 0.0
        if state == "FLAT":
            span = TIDE_RISING_MIN - TIDE_OUT_MAX
            frac = (float(pct) - TIDE_OUT_MAX) / span if span > 0 else 0.5
            return round(2.0 + max(0.0, min(1.0, frac)) * 2.0, 2)
        return None
    except Exception:
        return None


_ENV_CACHE = {"ts": 0.0, "v": None}


def env_score(when: datetime | None = None, force: bool = False) -> float | None:
    """EnvScore = w_r * RotationStrength + w_t * TIDE (0-5). Either half
    may be None (the other carries it); both None -> None. Cached 5 min
    unless force."""
    if not force:
        c = _env_cache_get(when or _now_mst())
        if c is not None:
            return c
    rs = rotation_strength(when)
    ts = tide_score()
    if rs is None and ts is None:
        return None
    parts = []
    if rs is not None:
        parts.append((W_ROTATION, float(rs)))
    if ts is not None:
        parts.append((W_TIDE, ts))
    wsum = sum(w for w, _ in parts)
    if wsum <= 0:
        return None
    v = round(sum(w * val for w, val in parts) / wsum, 3)
    _ENV_CACHE.update(ts=time.time(), v=v)
    return v


# ── the slow facts (discipline score, money left, pace) come from the
# discipline ledger's own day readout — a log/ledger scan, NOT a per-tick
# read, so it is cached 30 minutes against yesterday's date ────────────────
_daily_cache = {"ts": 0.0, "data": None}


def _daily_facts(when: datetime | None = None) -> dict | None:
    """Yesterday's cooldown_review readout (score / total_left /
    daily_pace) — the same numbers the nightly review speaks. Fail-soft
    None; cached so the guard never re-scans ledgers per tick."""
    when = when or _now_mst()
    y = when - timedelta(days=1)
    key = y.strftime("%Y-%m-%d")
    if (_daily_cache["data"] and _daily_cache["data"].get("date") == key
            and time.time() - _daily_cache["ts"] < 1800):
        return _daily_cache["data"]
    try:
        import discipline as _disc
        r = _disc.cooldown_review(None, when=y, speak=False, dry_run=True)
        if r and r.get("date") == key:
            _daily_cache.update(ts=time.time(), data=r)
            return r
    except Exception:
        pass
    return None


def _env_cache_get(when: datetime) -> float | None:
    """EnvScore recomputed at most every 5 minutes (the tide read itself
    caches 5; the thresholds file is static intraday)."""
    global _ENV_CACHE
    try:
        if _ENV_CACHE["v"] is not None and time.time() - _ENV_CACHE["ts"] < 300:
            return _ENV_CACHE["v"]
    except Exception:
        _ENV_CACHE = {"ts": 0.0, "v": None}
    return None


# ── 2. the discipline modifier ───────────────────────────────────────────
def _giveback_dominant_usd(usd: dict | None) -> bool:
    """THE ONE DOMINANCE RULE both giveback consumers share (the
    discipline dock and the MLT stand-down): giveback >= every other
    usd_left component AND >= the tier tighten trigger. Fail-soft
    False — an unreadable trigger never moves the guard on a lie."""
    try:
        _gb = float((usd or {}).get("giveback") or 0.0)
        if _gb <= 0:
            return False
        if _gb < max((float(v) or 0.0) for v in (usd or {}).values()):
            return False
        import contest_account as _ca_gbd
        return _gb >= float(_ca_gbd.giveback_thresholds()[0])
    except Exception:
        return False


def discipline_penalty(when: datetime | None = None) -> float | None:
    """DisciplinePenalty 0-10 from the discipline score's own gap to 100
    (a 100 discipline night costs nothing; 30 costs 7). Unreadable
    -> None. THE ADAPTIVE WEIGHT (user spec, Sep 23): the meta layers'
    docked extra scales with carve_out.penalty_context — high vol or a
    strong rotation forgives, a weak fleet or a heavy book bites harder
    — so the same violation costs differently per regime."""
    facts = _daily_facts(when)
    if not facts or facts.get("score") is None:
        return None
    try:
        score = float(facts["score"])
        pen = max(0.0, min(10.0, (100.0 - score) / 10.0))
        # the meta layers dock the penalty too (user spec: RCL major drift
        # and SAL violations raise DisciplinePenalty) — fail-soft, capped
        try:
            import meta_layers as _ml
            _extra = float(_ml.discipline_penalty_extra() or 0.0)
            if _extra > 0:
                try:
                    import carve_out as _co
                    _w, _r = _co.penalty_context(when)
                    if _r:
                        _extra *= _w
                except Exception:
                    pass
                pen += _extra
        except Exception:
            pass
        # THE GIVEBACK DOMINANCE DOCK (user ask, Oct 5): the day's
        # biggest leave being money reached and handed back tightens
        # tomorrow's guard even when the score's other components
        # dilute the sting — the score forgives, the giveback does not.
        # THE ONE RULE (_giveback_dominant_usd) is shared with the MLT
        # stand-down, so the two giveback consumers can never drift.
        if _giveback_dominant_usd(facts.get("usd_left")):
            pen += GIVEBACK_DOMINANT_PENALTY
        return round(min(pen, 10.0), 3)
    except Exception:
        return None


def disc_mod_from_penalty(penalty: float | None) -> float | None:
    """DiscMod = 1 - (penalty / 10); None penalty -> None (neutral)."""
    if penalty is None:
        return None
    return round(max(0.0, min(1.0, 1.0 - float(penalty) / DISC_PENALTY_DIVISOR)), 3)


# ── 3. the money-left correction (the learning loop) ────────────────────
def money_left_on_table(when: datetime | None = None) -> float | None:
    """Yesterday's money left on the table, USD, from the discipline
    ledger's own day readout (the same number the nightly review speaks).
    THE FUNNEL'S OWN LEAVE IS NOT THE GUARD'S TO LOOSEN FOR (user ask,
    Oct 6 - the audit's named residual): when the day's component map
    exists, the numerator reads only the leave the EXITS could have
    recovered - total_left minus the gates' own system_burial dollars
    (money no exit ever touched, which no exit tuning recovers),
    floored at zero so the funnel never TIGHTENS the guard either; a
    map-less row keeps the legacy whole-leave read (fail-soft).
    Unreadable -> None."""
    facts = _daily_facts(when)
    if not facts or facts.get("total_left") is None:
        return None
    try:
        mlt = round(float(facts["total_left"]), 2)
        _usd = facts.get("usd_left")
        if isinstance(_usd, dict):
            mlt = max(0.0, round(
                mlt - float(_usd.get("system_burial") or 0.0), 2))
        return mlt
    except Exception:
        return None


def daily_goal_usd(when: datetime | None = None) -> float | None:
    """The tier's realistic daily pace — the same number the all-good
    pace clock uses, via the same readout — now THE ADAPTIVE BASELINE
    (user spec, Sep 23): x goal_context (strong rotation/tide/fleet
    raise it; high vol and a heavy book lower it) when the context
    reads. DNL's learning factor composes separately (tomorrow's
    goal = base x context x (1 + learning_factor)). Unreadable -> None."""
    facts = _daily_facts(when)
    if not facts or facts.get("daily_pace") is None:
        return None
    try:
        pace = float(facts["daily_pace"])
        if pace <= 0:
            return None
        try:
            import carve_out as _co
            _gm, _gr = _co.goal_context(when)
            if _gr:
                pace *= _gm
            # THE ADAPTIVE GOAL BAND (user spec, Sep 23, clamp 2.6): the
            # adaptive pace clamps to MinGoal = base x TideMod x BookMod /
            # MaxGoal = base x RotationMod x VolMod x FleetMod — the
            # context may move the goal off base only within the regime's
            # own band. Unreadable sources fail open (no clamp applied).
            if _gr:
                _glo, _ghi = _co.goal_bounds(float(facts["daily_pace"]),
                                             when=when)
                if _glo > 0 and _ghi >= _glo:
                    pace = max(_glo, min(_ghi, pace))
        except Exception:
            pass
        return round(pace, 2)
    except Exception:
        return None


def mlt_corr(when: datetime | None = None) -> float | None:
    """MLT_Corr = MoneyLeftOnTable / DailyGoal. Positive: loosen (the
    guard was too tight). Negative: tighten (it saved losses). Neither
    half readable -> None.
    THE STAND-DOWN (user ask, Oct 5: the guard cannot be pulled both
    ways at once): on a GIVEBACK-DOMINANT day the money-left
    correction yields — the same dollars cannot argue "the exits
    choked, loosen" here while the giveback dominance dock argues
    "the exits leaked, tighten" in DiscMod. One day, one exit verdict:
    the biggest leave names the channel that answers, and the learning
    loop waits for a day the leave is not the giveback's to tell."""
    facts = _daily_facts(when)
    if facts and _giveback_dominant_usd(facts.get("usd_left")):
        return None
    mlt = money_left_on_table(when)
    goal = daily_goal_usd(when)
    if mlt is None or goal is None or goal <= 0:
        return None
    return round(mlt / goal, 3)

def channel_verdict(when: datetime | None = None, res: dict | None = None) -> dict | None:
    """THE GUARD'S OWN WORDS (user ask, Oct 6): one fact naming which channel answered
    today - the giveback's verdict (the dock tightens while the correction yields,
    1826235) or the leave's verdict (the exits-recoverable numerator, 0cb27f2 - the
    funnel's burial dollars are nobody's loosen). THE ONE RULE: it reads the same shared
    dominance rule the dock and the stand-down read, the same penalty the dock composes
    into, and the same exits-recoverable numerator the composite multiplies - and it may
    claim only what the guard FELT (the second opinion's mediums + P2s): a score-less day
    never docks, a clamped penalty holds the dock at the cap, a correction that rounds to
    zero is neutral, no goal means no correction, the ceiling is PER-PAIR (the meta band's
    gf_max can sit below the global 2.0) as is the floor (a dock cannot tighten width the
    floor has already spent), a carried composite was never recomputed, and every verdict
    carries an honest "felt" flag. Unreadable day -> None (an honest silence - the plate
    draws, it never invents)."""
    try:
        facts = _daily_facts(when)
        if not facts or facts.get("total_left") is None:
            return None
        _readable = bool((res or {}).get("readable"))
        _gf = (res or {}).get("factor")
        _ceiling = (res or {}).get("gf_max")
        if not isinstance(_ceiling, (int, float)) or _ceiling <= 0:
            _ceiling = GUARD_CLAMP[1]   # the per-pair band's ceiling,
                                        # else the global clamp
        _ceiling = min(_ceiling, GUARD_CLAMP[1])   # and the clamp
                                                   # binds ABOVE
                                                   # any band
        _at_ceiling = bool(_readable and isinstance(_gf, (int, float))
                           and _gf >= _ceiling)
        _floor = (res or {}).get("gf_min")
        if not isinstance(_floor, (int, float)) or _floor <= 0:
            _floor = GUARD_CLAMP[0]     # the per-pair band's floor,
                                        # else the global clamp
        _floor = max(_floor, GUARD_CLAMP[0])   # the global clamp
                                               # binds BELOW any
                                               # band floor
        _at_floor = bool(_readable and isinstance(_gf, (int, float))
                         and _gf <= _floor)
        if not _readable:   # the fixed rules stand - no factor was
            return None     # computed, so nothing was felt
        _carried = str((res or {}).get("carried_from") or "")
        if _carried:   # a carried composite was NOT recomputed today:
            return {"channel": "giveback" if _giveback_dominant_usd(
                facts.get("usd_left")) else "leave", "felt": False,
                "verdict": "the net is carried from '" + _carried + "' - the loop halves are unreadable, nothing was recomputed, no channel was felt today"}
        if _giveback_dominant_usd(facts.get("usd_left")):
            _pen = discipline_penalty(when)   # the dock only exists
            if _pen is None:                  # where a score reached it
                return None
            if _at_floor:
                return {"channel": "giveback", "felt": False,
                        "verdict": "the giveback's verdict: the net sits at the guard's floor - the dock cannot tighten the width it has left"}
            if _at_ceiling:
                return {"channel": "giveback", "felt": False,
                        "verdict": "the giveback's verdict: the dock tightened into the guard's ceiling - the cap holds the width"}
            if _pen >= 10.0:
                return {"channel": "giveback", "felt": False,
                        "verdict": "the giveback's verdict: the penalty clamped at its cap"}
            return {"channel": "giveback", "felt": True,
                    "verdict": "the giveback's verdict: the guard was docked while the correction yields"}
        _usd = facts.get("usd_left")
        if isinstance(_usd, dict):
            _total = round(float(facts.get("total_left") or 0.0), 2)
            _bur = round(float(_usd.get("system_burial") or 0.0), 2)
            _leave = max(0.0, round(_total - _bur, 2))
            if _leave <= 0.0:
                return {"channel": "leave", "felt": False,
                        "verdict": "the funnel's own leave is not the guard's to loosen for - no exit channel answered"}
            _mlt = mlt_corr(when)
            if _mlt is None:                  # no goal -> no correction
                return None                   # exists to loosen with
            if _mlt <= 0.0:
                return {"channel": "leave", "felt": False,
                        "verdict": "the leave's verdict: the correction holds NEUTRAL - no felt loosen today"}
            if _at_ceiling:
                return {"channel": "leave", "felt": False,
                        "verdict": "the leave's verdict: the exits-recoverable leave earned a loosen, but the net already sat at the guard's ceiling - nothing more to move"}
            if _at_floor:
                return {"channel": "leave", "felt": False,
                        "verdict": "the leave's verdict: the exits-recoverable leave earned a loosen, but the net sits pinned at the guard's floor - the width cannot tell"}
            return {"channel": "leave", "felt": True,
                    "verdict": f"the leave's verdict: ${_leave:,.2f} of exits-recoverable leave loosened the guard"}
        return None
    except Exception:
        return None
def vol_ratio(pair: str) -> float | None:
    """CurrentVol / BaselineVol: the pair's realized VOL_WINDOW-candle
    volatility over the engine's per-cell std for its rotation.
    Unreadable -> None. Clamped VOL_MOD_CLAMP at the consumer."""
    try:
        import sqlite3
        import pandas as _pd
        from indicators import atr as _atr
        cc = sqlite3.connect(ROOT / "ohlcv_cache.db")
        try:
            df = _pd.read_sql_query(
                "SELECT ts, open, high, low, close, volume FROM ohlcv WHERE pair=? "
                "ORDER BY ts DESC LIMIT ?", cc, params=(pair, VOL_WINDOW))
        finally:
            cc.close()   # never leak the handle (Windows locks the file)
        if len(df) < VOL_WINDOW:
            return None
        df = df.iloc[::-1].reset_index(drop=True)
        cur = float(_atr(df, 14).iloc[-1]) / max(float(df["close"].iloc[-1]), 1e-9)
        when = _now_mst()
        sched = "weekend" if when.weekday() >= 5 else "weekday"
        doc = json.loads((ROOT / "rotation_thresholds.json").read_text(encoding="utf-8"))
        from background_scanner import rotation_key_for_hour
        cell = (doc.get("median") or {}).get(sched, {}).get(rotation_key_for_hour(when.hour))
        base_std = float((cell or {}).get("std") or 0)
        if base_std <= 0:
            return None
        return round(cur / (base_std / 100.0), 3)
    except Exception:
        return None


# ── 5. the asset's own behavior memory ───────────────────────────────────
def _run_lengths() -> dict[str, float]:
    """Per-asset mean run length in minutes from the closed-trade book."""
    f = ROOT / "contest_trades.jsonl"
    if not f.exists():
        return {}
    per: dict[str, list[float]] = {}
    fleet: list[float] = []
    for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
            hm = float(r.get("held_min") or 0)
            pair = str(r.get("pair") or "")
            if hm > 0 and pair:
                a = pair.split("/")[0].upper()
                per.setdefault(a, []).append(hm)
                fleet.append(hm)
        except Exception:
            continue
    if not fleet:
        return {}
    fleet_avg = sum(fleet) / len(fleet)
    out = {a: sum(v) / len(v) for a, v in per.items()}
    out["__fleet__"] = fleet_avg
    return out


def asset_memory(pair: str) -> float | None:
    """avg_run_length(asset) / fleet_avg_run_length: an asset that runs
    long earns a wider net; a dud that dies early earns a tight one.
    Unknown asset (no closed trades) -> 1.0 neutral, not None — absence
    of history is not unreadability. No book at all -> None."""
    try:
        rl = _run_lengths()
        fleet = rl.get("__fleet__")
        if not fleet or fleet <= 0:
            return None
        a = pair.split("/")[0].upper()
        if a not in rl:
            return 1.0
        return round(rl[a] / fleet, 3)
    except Exception:
        return None


# ── 6. the funding-rate direction ────────────────────────────────────────
def funding_mod(pair: str) -> float:
    """1.1 funding rising, 0.9 falling, 1.0 otherwise/unknown. The
    direction needs two funding prints; most spot universes have none —
    honest 1.0 there, never an invented signal. Cached 10 min per pair
    (a network read, not a per-tick one)."""
    now = time.time()
    c = _FUNDING_CACHE.get(pair)
    if c and now - c[1] < 600:
        return c[0]
    v = _funding_mod_fresh(pair)
    _FUNDING_CACHE[pair] = (v, now)
    return v


_FUNDING_CACHE: dict[str, tuple[float, float]] = {}


def _funding_mod_fresh(pair: str) -> float:
    try:
        from background_scanner import _get_exchange
        ex = _get_exchange()
        fr = ex.fetch_funding_rate(pair)
        prev = float(fr.get("previousFundingRate") or 0)
        cur = float(fr.get("fundingRate") or 0)
        if cur > prev:
            return FUNDING_RISING_MULT
        if cur < prev:
            return FUNDING_FALLING_MULT
        return FUNDING_NEUTRAL_MULT
    except Exception:
        return FUNDING_NEUTRAL_MULT


# ── 7. the time-in-trade decay ───────────────────────────────────────────
def decay_mod(w: dict | None = None,
              context: tuple | None = None) -> float:
    """DecayMod = exp(-t / tau) over minutes in trade, floored at
    DECAY_FLOOR: the older the position, the less it deserves the wide
    net. THE ADAPTIVE BASELINE (user spec, Sep 23): `context` is
    (multiplier, readable) from carve_out.decay_context — the tau slope
    scales with tide/rotation/vol/book (tide out or a heavy book decays
    FASTER; high vol or a strong rotation decays SLOWER) — the decay is
    contextual, not fixed. None/False -> the fixed slope (fail-neutral)."""
    try:
        added = (w or {}).get("opened_ts") or (w or {}).get("added")
        if not added:
            return 1.0
        _d = datetime.fromisoformat(str(added))
        if _d.tzinfo is None:          # legacy naive stamp -> MST
            _d = _d.replace(tzinfo=_now_mst().tzinfo)
        t_min = (datetime.now(_d.tzinfo) - _d).total_seconds() / 60.0
        # the context scales the SLOPE: a multiplier < 1 stretches tau
        # (slower decay), > 1 shrinks it (faster decay) — never past the
        # DECAY_FLOOR, and only when the context actually read
        base = math.exp(-max(t_min, 0.0) / DECAY_TAU_MIN)
        if context and context[1] and context[0]:
            eff_tau = max(DECAY_TAU_MIN / float(context[0]), 5.0)
            base = math.exp(-max(t_min, 0.0) / eff_tau)
        # THE ADAPTIVE RAIL (user spec, Sep 23, clamp 2.4): the floor
        # itself breathes — MinDecay = DECAY_FLOOR x TideMod — so a dead
        # tide lets old positions decay deeper and a live tide holds the
        # rail higher. Unreadable tide -> the static DECAY_FLOOR rail
        # (fail-open, exactly the committed behavior).
        _rail = DECAY_FLOOR
        try:
            import carve_out as _co_d
            _lo, _hi = _co_d.decay_bounds()   # no when arg here: the live
                                              # clock is the decay's own
            if _lo < _rail:
                _rail = _lo
        except Exception:
            _rail = DECAY_FLOOR
        return round(max(_rail, base), 3)
    except Exception:
        return 1.0


# ── 8. the composite ─────────────────────────────────────────────────────
_OV_CACHE = {"key": None, "val": None}


def _operator_factor() -> float | None:
    """The operator's single-variable calibration (user ask, Oct 7):
    the state file's operator_factor, when present and numeric, IS the
    guard factor (clamped to GUARD_CLAMP by the caller). The learned
    parts keep computing and reporting underneath, the nightly
    self_adjust keeps owning factor/history from the MACHINE's own
    recompute (it reads with respect_override=False), and DELETING the
    key returns control to the machine instantly. Cached on
    (mtime, size): guard_factor runs per candidate on every scan."""
    try:
        sf = _state_file()
        si = sf.stat()
        key = (si.st_mtime, si.st_size)
        if _OV_CACHE["key"] == key:
            return _OV_CACHE["val"]
        v = _state().get("operator_factor")
        # fail-soft includes NON-FINITE and BOOLS: json.loads accepts a
        # NaN/Inf literal (a nan would slip through the clamp), and a
        # JSON false IS an int in Python (False -> 0.0 -> clamp 0.5 -
        # the tightest guard, silently; Copilot, Oct 7). Only a finite
        # non-bool number is an operator decision.
        val = (float(v) if isinstance(v, (int, float))
               and not isinstance(v, bool)
               and math.isfinite(float(v)) else None)
        _OV_CACHE.update(key=key, val=val)
        return val
    except Exception:
        return None


def guard_factor(pair: str, w: dict | None = None,
                 when: datetime | None = None,
                 respect_override: bool = True) -> dict:
    """The full composite with every part named. The slow halves of the
    learning loop (EnvScore, DiscMod, MLT_Corr) read yesterday's stored
    context when the live read is unreadable — the loop's whole point is
    that yesterday's performance carries today — and `readable` is False
    only when BOTH the live loop halves and any stored factor are gone,
    in which case the exit watch keeps the committed fixed rules."""
    env = env_score(when)
    dm = disc_mod_from_penalty(discipline_penalty(when))
    mlt = mlt_corr(when)
    vr = vol_ratio(pair)
    am = asset_memory(pair)
    fm = funding_mod(pair)
    # THE MOMENTUM CONTINUATION HANDOFF (user spec, Sep 22): while the
    # detector says the move isn't done, the time-in-trade decay stands
    # DOWN — an aging position in a living trend is not a stale one
    try:
        import meta_layers as _ml
        dec = 1.0 if _ml.momentum_active(pair) else decay_mod(
            w, __import__("carve_out", fromlist=["x"]).decay_context(pair, when))
    except Exception:
        dec = decay_mod(w, __import__("carve_out", fromlist=["x"]).decay_context(pair, when))

    def _n(v, neutral=1.0):
        return neutral if v is None else float(v)

    loop_readable = env is not None or mlt is not None
    carried = None
    if not loop_readable:
        st = _state()
        sf = st.get("factor")
        _ao = str(st.get("adjusted_on") or "")
        if isinstance(sf, (int, float)) and sf > 0 and _ao:
            try:   # carry only a FRESH factor (<= 2 days old) — the loop
                   # references yesterday, not last month
                _today = (when or _now_mst()).date()
                if (_today - datetime.strptime(_ao, "%Y-%m-%d").date()).days <= 2:
                    carried = (_ao, float(sf))
            except Exception:
                carried = None
    if carried:
        factor = carried[1]
    else:
        # EnvScore normalized to its neutral (see ENV_NEUTRAL); (1 +
        # MLT_Corr): an unreadable MLT is NO correction (neutral 0), not
        # a phantom +1 doubling — the one term whose neutral is zero
        env_term = (float(env) / ENV_NEUTRAL) if env is not None else 1.0
        factor = env_term * _n(dm) * (1.0 + _n(mlt, 0.0)) * _n(vr) * _n(am) * _n(fm) * _n(dec)
        factor = round(max(GUARD_CLAMP[0], min(GUARD_CLAMP[1], factor)), 3)
    # THE OPERATOR OVERRIDE (Oct 7, the neutral-point ask): an explicit
    # operator_factor in the state file IS the factor — the one variable
    # the operator owns. Clamped to GUARD_CLAMP, provenance-flagged in
    # the result, named by explain(); the parts still compute and ride
    # the result, and the nightly record reads with respect_override
    # False so the learning never sees the override.
    override = None
    if respect_override:
        _ovf = _operator_factor()
        if _ovf is not None:
            override = round(max(GUARD_CLAMP[0],
                                 min(GUARD_CLAMP[1], _ovf)), 3)
            factor = override
    # THE DECAY RAIL for the plate (clamp 2.4's UI): the effective floor
    # this cycle's decay enforced — the plate renders it, never recomputes
    # it (one-brain). Same min() semantics as decay_mod's rail.
    try:
        import carve_out as _co_r
        _rail_lo = min(DECAY_FLOOR, float(_co_r.decay_bounds(when=when)[0]))
    except Exception:
        _rail_lo = DECAY_FLOOR
    return {"factor": factor,
            "readable": loop_readable or bool(carried)
            or override is not None,
            "operator_override": override,
            "carried_from": carried[0] if carried else None,
            "env": env, "disc_mod": dm, "mlt_corr": mlt, "vol_mod":
            (round(max(VOL_MOD_CLAMP[0], min(VOL_MOD_CLAMP[1], vr)), 3)
             if vr is not None else None),
            "asset_mem": (round(max(ASSET_MEMORY_CLAMP[0],
                                    min(ASSET_MEMORY_CLAMP[1], am)), 3)
                          if am is not None else None),
            "funding_mod": fm, "decay_mod": dec, "decay_rail": _rail_lo}


# ── 9. the trailing rules ────────────────────────────────────────────────
def trail_pct(base_trail_pct: float, res: dict) -> float | None:
    """TrailPct = BaseTrailPct * GuardFactor — None when the factor is
    not readable (the caller keeps its committed fixed rules)."""
    if not res.get("readable"):
        return None
    return round(float(base_trail_pct) * float(res["factor"]), 3)


def lockin_pct(res: dict) -> float | None:
    """LockInPct = BaseLockInPct / GuardFactor — the armed net's width
    normalizer: strength -> the net waits for the full goal, weakness ->
    it grabs early (see the module docstring's binding)."""
    if not res.get("readable"):
        return None
    return round(BASE_LOCK_IN_PCT / max(float(res["factor"]), 1e-6), 3)


# ── 10. the daily self-adjustment loop ───────────────────────────────────
def _state_file() -> Path:
    sf = globals().get("STATE_FILE")
    if sf is not None:
        return Path(sf)          # an explicit pin (the LIVE probes) wins
    return ROOT / "profit_guard_state.json"   # resolved NOW, not at import


def _state() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_state(path) -> dict:
    """A PURE READ of one explicit state file — the module default is
    never consulted, never assigned (the hermeticity sweep, Sep 28):
    cross-repo readers (_gf_series.GFSeries's sandbox hook) use this to
    open a specific file WITHOUT the old constructor dance of assigning
    the module-global STATE_FILE and never restoring it. Fail-soft like
    _state(): an unreadable path is an empty dict, not a crash."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


# THE SAVE LOCK (Copilot's second [high] on 39079b5): the merge-save's
# read → guard → write → replace sequence is the one place a save-time
# guard's verdict can still be raced — a concurrent writer landing
# between the guard and the replace overwrites it. One process-wide
# lock serializes the whole body: the heartbeat thread and the HTTP
# apply thread can no longer interleave inside _save (and the
# pid-suffixed tmp name stops being a same-process collision — two
# threads share the pid). RLock, not Lock: the box mutators hold it
# across their whole read-modify-write and re-acquire it through their
# internal pg._save calls (rollback re-expires internally; the
# heartbeat's tick→expire re-acquires on the same thread). The
# cross-process residual stays the standing merge-save contract
# (owner keys + os.replace + the retry convention).
_SAVE_LOCK = threading.RLock()


def _save(st: dict, guard=None) -> None:
    """Serialize the merge-save (see _save_locked for the contract)."""
    with _SAVE_LOCK:
        return _save_locked(st, guard)


def _save_locked(st: dict, guard=None) -> None:
    # ATOMIC + MERGE-SAVED (the cross-writer sweep, Sep 28): two writers
    # share this file — the scanner's daily self_adjust loop AND the
    # auto-adjust's apply/revert/tick — and both read-modify-write, so
    # a whole-file rewrite from a stale snapshot erases the other's key
    # (the proven clobber class: the ledger survived only because its
    # caller re-read first). Overlay st onto the file's CURRENT contents
    # (top-level keys are independent: factor, history, auto_adjust_box,
    # auto_adjust_ledger, adjusted_on) and write tmp + os.replace so a
    # torn write is never readable by the fail-soft readers (a torn
    # state file previously read as {} — the whole factor, history, box,
    # and ledger gone without a trace).
    # THE GUARD HOOK (Oct 3, the gate's [high] on 393ba92 — the
    # save-time window): a read-then-save writer races the heartbeat's
    # tick/expiry in the gap between its read and this call, and its
    # stale payload would overwrite the tick's truth (auto_adjust_box is
    # an owner key — the dead box resurrects). A caller holding a stale
    # snapshot passes guard(merged, cur): it runs HERE, against the
    # file's CURRENT contents after the overlay, before anything is
    # written — the last point where the fresh truth is visible and
    # amendable. The guard amends `merged` in place (including setting a
    # key to None: the deletion below is merge-wide for exactly that).
    sf = _state_file()
    try:
        cur = json.loads(sf.read_text(encoding="utf-8"))
        if not isinstance(cur, dict):
            cur = {}
    except Exception:
        cur = {}
    merged = dict(cur)
    merged.update(st)
    # THE SAVE-TIME GUARD runs here — after the overlay, before the
    # deletion and the write: the freshest state this write will ever
    # see.
    if guard is not None:
        guard(merged, cur)
    # THE NONE-MEANS-DELETE convention (the mirror of the scanner's
    # contest_watch merge): expire_if_due and rollback must REMOVE the
    # box, and a plain update would resurrect it from the current file.
    # A key set to None — in `st` or BY THE GUARD — is deleted from the
    # merged result; the sweep is merge-wide so a guard's deliberate
    # deletion lands exactly like a caller's.
    for k, v in list(merged.items()):
        if v is None:
            merged.pop(k, None)
    # THE PER-WRITER TMP NAME (Sep 29, the review's recurring hardening):
    # a pid-suffixed temp keeps two concurrent writers of the SAME file
    # from colliding inside the tmp window (writer A's replace could
    # otherwise move writer B's half-written tmp). Same-directory keep
    # (os.replace is atomic only on one volume); a failed write cleans
    # its own temp instead of leaving a stray.
    tmp = f"{sf}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=2)
        # THE RETRY-ON-CONTENTION CONTRACT (the fleet's cross-writer
        # convention): a lost rename retries IN-PROCESS on the named
        # contention family (WinError 5/32, errno-13 shapes) instead of
        # waiting for the next tick; the last contention error still
        # raises so fail-soft callers keep their named-failure rule.
        from atomic_io import replace_atomic
        replace_atomic(tmp, sf)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── THE GUARD-FACTOR HISTORY (Sep 27): every day's adjusted factor is
# preserved in the daily state file as `history` — an append-only,
# date-keyed array pruned to HISTORY_KEEP days. Before this, each
# self_adjust OVERWROTE the previous {factor, parts}, so the September
# studies had to bracket the hidden time series (_gf_sensitivity.py's
# sensitivity matrix) instead of reading it. Going forward the series
# is on the record: one row per adjusted MST day, oldest-pruned.
HISTORY_KEEP = 400                      # ~13 months of daily rows


def _append_history(st: dict, row: dict) -> dict:
    """Append one day's adjustment to st['history'], pruned. Dedupe on
    the day stamp (a re-run must replace its own row, never stack).
    Fail-soft by construction: the caller already built the row from a
    successful adjustment, and a malformed history degrades to a fresh
    list rather than blocking the day's adjust."""
    hist = st.get("history")
    if not isinstance(hist, list):
        hist = []
    day = row.get("day")
    hist = [h for h in hist
            if isinstance(h, dict) and h.get("day") != day]
    hist.append(row)
    hist.sort(key=lambda h: str(h.get("day") or ""))
    st["history"] = hist[-HISTORY_KEEP:]
    return st


def factor_history() -> list:
    """The preserved time series for studies: [{day, factor, parts?, ts?}]
    sorted by day ascending. Read-only; malformed entries are skipped
    (the writer never produces them, but a hand edit must not crash a
    study)."""
    st = _state()
    hist = st.get("history")
    if not isinstance(hist, list):
        return []
    return sorted((h for h in hist if isinstance(h, dict)
                   and isinstance(h.get("factor"), (int, float))),
                  key=lambda h: str(h.get("day") or ""))


def stored_factor() -> float | None:
    st = _state()
    v = st.get("factor")
    return float(v) if isinstance(v, (int, float)) else None


def self_adjust(now: datetime | None = None) -> dict | None:
    """The cooldown loop (step 10): recompute every modifier, write the
    trail/lock-in the factor implies, store GuardFactor for tomorrow's
    learning. ONCE per MST day, date-marked, restart-proof; a failed
    recompute is logged-by-None and retried next cycle the way the
    review's own one-shots work. Returns the stored summary or None
    (already adjusted today, or nothing readable yet)."""
    now = now or _now_mst()
    today = now.strftime("%Y-%m-%d")
    st = _state()
    if st.get("adjusted_on") == today:
        return None
    res = guard_factor("", None, when=now, respect_override=False)
    # (CodeRabbit minor) a CARRIED factor is not today's adjustment: if
    # the loop halves are unreadable the stored factor renews itself
    # every night, and the 2-day freshness check could never expire.
    if not res.get("readable") or res.get("carried_from"):
        return None    # nothing freshly computed — try again next cycle, no stamp
    row = {"adjusted_on": today, "factor": res["factor"], "parts": {
        k: res.get(k) for k in ("env", "disc_mod", "mlt_corr", "vol_mod",
                                "asset_mem", "funding_mod", "decay_mod")},
        "ts": now.isoformat(timespec="seconds")}
    # THE HISTORY APPEND (Sep 27): the daily factor joins the preserved
    # time series in the same file — tomorrow's learning row AND the
    # studies' future ground truth land in one atomic write.
    st = _append_history(st, {"day": today,
                              "factor": res["factor"],
                              "parts": row["parts"],
                              "ts": row["ts"]})
    row["history_len"] = len(st["history"])
    # OWNER-KEYS-ONLY MERGE-SAVE (the review's recurring major, closed:
    # 91907f2 → cf60cf7 → cb47bc5 → afb430d → this): the merge-save
    # overlays onto the file's CURRENT contents, so a FULL STALE
    # snapshot passed here degenerates it into a whole-file rewrite —
    # every key the snapshot carries overwrites the file's newer value
    # (the drive's mid-flight writer replayed it). This writer owns its
    # daily row and the history series and NOTHING else: the box, the
    # ledger, and any future writer's keys ride untouched on top.
    _keys = {k: row[k] for k in ("adjusted_on", "factor", "parts", "ts",
                                 "history_len")} | {"history": st["history"]}
    # THE MLT STAND-DOWN NOTE (user ask, Oct 6): the reconciliation must
    # be visible in hindsight, not just in code. A bake whose money-left
    # correction yielded to a giveback-dominant day ledgers a per-day
    # rider row in the auto_adjust_ledger - the same file hindsight's
    # /api/hindsight/ledger serves - checksum-bound to the day facts it
    # names so hindsight can re-derive it. The row is NOT an owner key
    # of this save: it rides the GUARD HOOK, appending to the file's
    # CURRENT ledger at the last point before the write, so a heartbeat
    # row landing mid-flight is kept, never overwritten.
    _guard = None
    if res.get("mlt_corr") is None:
        try:
            _facts = _daily_facts(now) or {}
            _usd = _facts.get("usd_left")
            if _giveback_dominant_usd(_usd):
                _gb = round(float((_usd or {}).get("giveback") or 0.0), 2)
                _tl = round(float(_facts.get("total_left") or 0.0), 2)
                _line = f"{_gb:.2f}/{_tl:.2f}"
                _sum = hashlib.sha256(_line.encode("utf-8")).hexdigest()[:16]
                _row = {
                    "day": today,
                    "rotation_id": f"daily-bake-{today}",
                    "status": "stood_down",
                    "before": _tl, "after": _tl, "delta": 0.0,
                    "reason": ("mlt_stood_down: giveback-dominant day "
                               f"(${_gb:,.2f} of the ${_tl:,.2f} leave is "
                               "money handed back - the correction yields, "
                               "the dock answers alone)"),
                    "evidence": f"mlt_stood_down:{_line}:{_sum}",
                    "applied_at": now.isoformat(timespec="seconds"),
                }

                def _note_guard(merged, cur, _row=_row):
                    _led = merged.setdefault("auto_adjust_ledger", [])
                    if not any(isinstance(r, dict)
                               and r.get("status") == "stood_down"
                               and r.get("day") == _row["day"]
                               for r in _led):
                        _led.append(_row)

                _guard = _note_guard
                res["stand_down_noted"] = True
        except Exception:
            res["stand_down_noted"] = False   # the note never moves the
                                              # guard: fail-soft
    # the guard kwarg rides ONLY the note path: the default call is
    # byte-identical to the pre-note save (the mid-flight writer pin
    # keeps exercising the legacy contract)
    if _guard is not None:
        _save(_keys, guard=_guard)
    else:
        _save(_keys)
    res = dict(res)
    res["history_len"] = len(st["history"])   # log-visible series growth
    return res


# ── shared ───────────────────────────────────────────────────────────────
def _now_mst():
    from background_scanner import _now_mst as _f
    return _f()


def explain(res: dict, pair: str = "") -> str:
    """One hearable line for the voice/log: what the guard reads and
    what it decided."""
    if not res.get("readable"):
        return "profit guard unreadable — the fixed trail rules stand"
    bits = []
    if res.get("env") is not None:
        bits.append(f"env {res['env']:.1f}")
    if res.get("mlt_corr") is not None:
        bits.append(f"mlt {res['mlt_corr']:+.2f}")
    if res.get("decay_mod") is not None and res["decay_mod"] < 1.0:
        bits.append(f"decay {res['decay_mod']:.2f}")
    if res.get("operator_override") is not None:
        bits.append(f"OPERATOR OVERRIDE {res['operator_override']:.2f}")
    g = res["factor"]
    mood = ("let it run" if g > 1.2 else
            "tighten" if g < 0.9 else "normal trailing")
    return (f"guard factor {g:.2f} ({', '.join(bits) or 'neutral parts'}) "
            f"— {mood}").strip() + (f" [{pair}]" if pair else "")
