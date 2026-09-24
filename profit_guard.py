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
import json
import math
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

STATE_FILE = ROOT / "profit_guard_state.json"


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
    Unreadable -> None."""
    facts = _daily_facts(when)
    if not facts or facts.get("total_left") is None:
        return None
    try:
        return round(float(facts["total_left"]), 2)
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
        except Exception:
            pass
        return round(pace, 2)
    except Exception:
        return None


def mlt_corr(when: datetime | None = None) -> float | None:
    """MLT_Corr = MoneyLeftOnTable / DailyGoal. Positive: loosen (the
    guard was too tight). Negative: tighten (it saved losses). Neither
    half readable -> None."""
    mlt = money_left_on_table(when)
    goal = daily_goal_usd(when)
    if mlt is None or goal is None or goal <= 0:
        return None
    return round(mlt / goal, 3)


# ── 4. the volatility modifier ───────────────────────────────────────────
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
        return round(max(DECAY_FLOOR, base), 3)
    except Exception:
        return 1.0


# ── 8. the composite ─────────────────────────────────────────────────────
def guard_factor(pair: str, w: dict | None = None,
                 when: datetime | None = None) -> dict:
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
    return {"factor": factor, "readable": loop_readable or bool(carried),
            "carried_from": carried[0] if carried else None,
            "env": env, "disc_mod": dm, "mlt_corr": mlt, "vol_mod":
            (round(max(VOL_MOD_CLAMP[0], min(VOL_MOD_CLAMP[1], vr)), 3)
             if vr is not None else None),
            "asset_mem": (round(max(ASSET_MEMORY_CLAMP[0],
                                    min(ASSET_MEMORY_CLAMP[1], am)), 3)
                          if am is not None else None),
            "funding_mod": fm, "decay_mod": dec}


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
def _state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(st: dict) -> None:
    STATE_FILE.write_text(json.dumps(st, indent=2), encoding="utf-8")


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
    res = guard_factor("", None, when=now)
    # (CodeRabbit minor) a CARRIED factor is not today's adjustment: if
    # the loop halves are unreadable the stored factor renews itself
    # every night, and the 2-day freshness check could never expire.
    if not res.get("readable") or res.get("carried_from"):
        return None    # nothing freshly computed — try again next cycle, no stamp
    _save({"adjusted_on": today, "factor": res["factor"], "parts": {
        k: res.get(k) for k in ("env", "disc_mod", "mlt_corr", "vol_mod",
                                "asset_mem", "funding_mod", "decay_mod")},
        "ts": now.isoformat(timespec="seconds")})
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
    g = res["factor"]
    mood = ("let it run" if g > 1.2 else
            "tighten" if g < 0.9 else "normal trailing")
    return (f"guard factor {g:.2f} ({', '.join(bits) or 'neutral parts'}) "
            f"— {mood}").strip() + (f" [{pair}]" if pair else "")
