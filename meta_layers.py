"""THE THREE META-SYSTEMS (user spec, Sep 22) — RCL, SAL, DNL.

  RCL — THE REALITY CHECK LAYER: compares EXPECTED market behavior (the
  volatility engine's calibrated cells) against ACTUAL behavior, five
  signals, one MismatchScore. Mild drift tightens the guard 0.9 and
  kills re-entry candidates; major drift tightens 0.75, suppresses
  borderline signals, activates RotationEndGameMode, and raises the
  discipline penalty. Stale assumptions never trade.

  SAL — THE SELF-AUDIT LAYER: after every auto-close, counts rule
  violations (did the exit follow its own trail? did the row carry the
  provenance the rules require?) and feeds tomorrow's guard: >=3
  violations -> GF x0.85 and rotation/TIDE confidence docked; 0 ->
  the discipline penalty decays x0.9 and GF x1.05. The bot stays honest.

  DNL — THE DAILY NARRATIVE LAYER: at cooldown, LAST, the day's numbers
  become the seven questions (learned / surprised / worked / didn't /
  adjusted / make more next time / tomorrow's focus), stored with a
  learning factor that scales tomorrow's DailyGoal.

WIRING CONTRACT (spec section 4): the exit watch calls finalize() so
  Final GF = GF_after_RCL * SAL_adjustment, RCL runs BEFORE the factor
  is finalized, SAL adjusts for tomorrow, DNL runs last at cooldown,
  and End-Game Mode overrides the trail (x0.7) and lock-in (x1.3).

Every layer fails soft: an unreadable signal is UNKNOWN, never a
silent mismatch; an unreadable layer is absent, never a crash.
"""
import json
import math
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "meta_layers_state.json"

# ── dials ────────────────────────────────────────────────────────────────
MISMATCH_NORMAL = 0.3
MISMATCH_MAJOR = 0.6
RCL_MILD_MULT = 0.9
RCL_MAJOR_MULT = 0.75
SAL_BAD_MULT = 0.85           # >= 3 violations
SAL_GOOD_MULT = 1.05          # 0 violations
SAL_PENALTY_WEIGHT = 0.5      # each violation adds this to the penalty
ENDGAME_TRAIL_MULT = 0.7
ENDGAME_LOCKIN_MULT = 1.3
DNL_GOAL_FACTOR_CAP = 0.5     # the learning factor's clamp
DRIFT_TTL_MIN = 240           # a drift verdict goes stale after 4 hours
# RCL-DRIFT QUICK-FIX (Oct 1, the gate-EV finding: rcl-drift buries a
# BINARY outcome — payers 2.17% medfill, duds exactly 0.00): a MILD
# verdict re-asks reality after 90 min instead of freezing the full
# 4h on evidence that may have re-aligned (bounded re-calibration).
# MAJOR keeps the full freeze — the tight net protects profits.
DRIFT_TTL_MILD_MIN = 90
SLOPE_WINDOW_MIN = 15         # liquidity-slope sample window
ROTATION_PROBE_MIN = 30       # the realized-move probe window (minutes)
ROTATION_LEN_MIN = 240        # the calibration's expected move is per 4h
DIFF_CLAMP = (0.0, 1.0)


def _now_mst():
    from background_scanner import _now_mst as _f
    return _f()


def _state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


_SAVE_DROPS = {"rcl_last"}   # keys a writer may legitimately delete


def _save(st: dict) -> None:
    """Merge-save: overlay `st` onto the file's CURRENT contents instead
    of rewriting the whole file from a stale snapshot. Every writer here
    mutates a fresh `_state()` read, but a NESTED writer (note_endgame
    inside the drift branch, a graduation inside a tick) can land between
    this function's read and its write — the old whole-file rewrite
    erased exactly such a key twice (the drive's Sep-22 findings).
    Keys in _SAVE_DROPS are intentional deletions: absent from `st`
    means removed, not resurrected."""
    try:
        cur = _state()
    except Exception:
        cur = {}
    merged = {k: v for k, v in cur.items() if k not in _SAVE_DROPS}
    merged.update(st)
    # ATOMIC (the cross-writer sweep, Sep 28): the merge-save already
    # closed the clobber door; this closes the torn-write door — the
    # fail-soft readers would read a half-written file as {} and the
    # whole learning state would vanish without a trace.
    # THE RETRY CONTRACT (Oct 2, the last storm-shape writer): a
    # pid-suffixed per-writer tmp + atomic_io's bounded backoff — a
    # reader holding the target at the replace instant (the console's
    # guard read via carve_out) is the WinError 5/32 family, and the
    # old single raw replace lost the whole learning state silently.
    from atomic_io import replace_atomic
    tmp = str(STATE_FILE) + f".tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2)
    replace_atomic(tmp, STATE_FILE)


# ═══════════════ SECTION 1 — THE REALITY CHECK LAYER ═══════════════
def _rotation_gap(when: datetime) -> float | None:
    """|live avg_move - calibrated standard threshold| / threshold, the
    engine's own cell vs its own day-schedule calibration."""
    try:
        doc = json.loads((ROOT / "rotation_thresholds.json").read_text(encoding="utf-8"))
        from background_scanner import rotation_key_for_hour
        sched = "weekend" if when.weekday() >= 5 else "weekday"
        rk = rotation_key_for_hour(when.hour)
        live = (doc.get("median") or {}).get(sched, {}).get(rk, {})
        # the live cell IS the file's cell — the honest 'actual' is the
        # recent realized move, so grade today's realized rotation pace
        # against the calibration:
        realized = _recent_move_pct()
        if realized is None:
            return None
        # the calibration's expected move is per FULL ROTATION — prorate
        # it to the probe window or a calm market reads as permanent
        # drift (the live-probe lesson, Sep 22: 30-min realized ~0.3%
        # against a 4h expected 2.1% is apples-to-oranges)
        exp_full = float((live.get("thresholds") or {}).get("standard")
                         or live.get("avg_move") or 0)
        if exp_full <= 0:
            return None
        exp = exp_full * (ROTATION_PROBE_MIN / ROTATION_LEN_MIN)
        return round(min(1.0, abs(realized - exp) / max(exp, 0.5)), 3)
    except Exception:
        return None


def _recent_move_pct() -> float | None:
    """The fleet's realized 30-min move breadth: mean |close/close_30m_ago
    - 1| across cached pairs. Unreadable -> None."""
    try:
        import sqlite3
        import pandas as _pd
        cc = sqlite3.connect(ROOT / "ohlcv_cache.db")
        try:
            df = _pd.read_sql_query(
                "SELECT pair, ts, close FROM ohlcv WHERE ts >= "
                f"(SELECT MAX(ts) FROM ohlcv) - {ROTATION_PROBE_MIN * 60000} "
                "ORDER BY pair, ts", cc)
        finally:
            cc.close()
        if df.empty:
            return None
        g = df.groupby("pair")["close"]
        moves = (g.last() / g.first() - 1.0).abs().dropna()
        if moves.empty:
            return None
        return round(float(moves.mean()) * 100.0, 3)
    except Exception:
        return None


_SLOPE: list[tuple[float, float]] = []     # [(ts, breadth_pct)]


def _liquidity_slope_gap(when: datetime) -> float | None:
    """The fleet breadth's slope (%/min) vs the calm-rotation expectation
    of ~0. A fast flood or drain is reality moving without us."""
    try:
        from background_scanner import _fleet_tide
        pct, _state_s = _fleet_tide()
        if pct is None:
            return None
        now = time.time()
        _SLOPE.append((now, float(pct)))
        keep = now - SLOPE_WINDOW_MIN * 60
        while _SLOPE and _SLOPE[0][0] < keep:
            _SLOPE.pop(0)
        if len(_SLOPE) < 2:
            return None
        (t0, p0), (t1, p1) = _SLOPE[0], _SLOPE[-1]
        slope = (p1 - p0) / max(t1 - t0, 1) * 60.0          # % per minute
        return round(min(1.0, abs(slope) / 0.5), 3)          # 0.5%/min = full diff
    except Exception:
        return None


def _volatility_gap(pair: str, when: datetime) -> float | None:
    """Current realized vol vs the cell baseline (the guard's own
    VolMod's inputs, re-expressed as a normalized difference)."""
    try:
        import profit_guard as _pg
        vr = _pg.vol_ratio(pair)
        if vr is None:
            return None
        return round(min(1.0, abs(vr - 1.0) / 0.7), 3)
    except Exception:
        return None


_FUNDING_PREV: dict[str, str] = {}


def _funding_gap(pair: str) -> float | None:
    """Funding direction vs its previous known direction: a flip is a
    full mismatch. Unknown both times -> None (never invented)."""
    try:
        import profit_guard as _pg
        cur = _pg.funding_mod(pair)
        if cur == _pg.FUNDING_NEUTRAL_MULT:
            return None                       # unknown direction — honest
        direction = "rising" if cur > 1.0 else "falling"
        prev = _FUNDING_PREV.get(pair)
        _FUNDING_PREV[pair] = direction
        if prev is None:
            return None
        return 0.0 if prev == direction else 1.0
    except Exception:
        return None


def _signal_quality_gap(when: datetime) -> float | None:
    """Today's popup quality vs the design bar: the burial rate among
    today's fired candidates vs the 30% the funnel tolerates."""
    try:
        import discipline as _disc
        st = _disc.load_state()
        today = when.strftime("%Y-%m-%d")
        fires = burials = 0
        for h in st.get("history", []):
            if str(h.get("date", "")).startswith(today):
                fires += int(h.get("fired", 0) or 0)
                burials += int(h.get("buried", 0) or 0)
        if fires + burials == 0:
            return None
        rate = burials / (fires + burials)
        return round(min(1.0, abs(rate - 0.3) / 0.3), 3)
    except Exception:
        return None


def rcl_measure(pair: str, when: datetime | None = None) -> dict:
    """The five diffs + the MismatchScore. `known` counts the signals
    that actually answered; the score is the mean of the KNOWN ones."""
    when = when or _now_mst()
    diffs = {
        "rotation": _rotation_gap(when),
        "liquidity_slope": _liquidity_slope_gap(when),
        "volatility": _volatility_gap(pair, when),
        "funding": _funding_gap(pair),
        "signal_quality": _signal_quality_gap(when),
    }
    known = [v for v in diffs.values() if v is not None]
    score = round(sum(known) / len(known), 3) if known else None
    if score is None:
        verdict = "UNKNOWN"
    elif score < MISMATCH_NORMAL:
        verdict = "NORMAL"
    elif score < MISMATCH_MAJOR:
        verdict = "MILD DRIFT"
    else:
        verdict = "MAJOR DRIFT"
    return {"score": score, "verdict": verdict, "diffs": diffs,
            "known": len(known), "ts": when.isoformat(timespec="seconds")}


def rcl_apply(res: dict, gf: float | None, when: datetime | None = None) -> tuple[float | None, dict]:
    """The bands' actions: returns (adjusted GF, action dict). Major
    drift also arms RotationEndGameMode and docks the discipline
    penalty; mild drift blocks re-entry candidates. Persists the
    verdict with a TTL so the rest of the engine can consult it."""
    when = when or _now_mst()
    verdict = res.get("verdict", "UNKNOWN")
    act: dict = {"rcl": verdict, "mult": 1.0}
    if verdict == "MILD DRIFT":
        act["mult"] = RCL_MILD_MULT
        act["reentry_blocked"] = True
    elif verdict == "MAJOR DRIFT":
        act["mult"] = RCL_MAJOR_MULT
        act["reentry_blocked"] = True
        act["suppress_borderline"] = True
        act["endgame"] = True
        # (CodeRabbit major) once per VERDICT, not once per tick: the
        # exit watch calls finalize every tick, and the old per-call
        # penalty hit its 3.0 cap in three ticks while endgame_events
        # counted hundreds for one drift episode.
        _prev = (_state().get("rcl_last") or {})
        _same = (_prev.get("verdict") == "MAJOR DRIFT"
                 and _prev.get("rot") == _rot_key_of(when))
        if not _same:
            st = _state()
            pen = float(st.get("discipline_penalty_extra", 0.0)) + 1.0
            # THE ADAPTIVE PENALTY CAP (user spec, Sep 23, clamp 2.5): the
            # per-violation docked weight clamps to MaxPenalty = 2.00 x
            # RotationMod x VolMod x FleetMod (MinPenalty = 0.50 x TideMod
            # x BookMod governs the +1.0 dock floor). Neutral mods ->
            # cap 2.0; unreadable sources fail open to the committed rail.
            try:
                import carve_out as _co_p
                _plo, _phi = _co_p.penalty_bounds(when=when)
                pen = min(max(pen, _plo), _phi)
            except Exception:
                pen = min(pen, 3.0)
            st["discipline_penalty_extra"] = round(min(pen, 3.0), 2)
            _save(st)
            note_endgame(when)    # RotationEndGameMode arms WITH the verdict
    if verdict in ("MILD DRIFT", "MAJOR DRIFT"):
        # re-read: note_endgame just wrote, and a stale local snapshot
        # would clobber its key (the drive's finding, Sep 22)
        st = _state()
        st["rcl_last"] = {"verdict": verdict, "score": res.get("score"),
                          "ts": time.time(),
                          "rot": _rot_key_of(when)}
        _save(st)
    elif verdict == "NORMAL":
        # reality re-aligned: lift the re-entry suppression — but only on
        # a DECISIVELY calm reading (< 0.8 x the normal bound), so a
        # verdict flickering at the 0.30 boundary cannot flap the gate.
        # End-Game Mode stays armed until the rotation rolls (protective
        # hysteresis — the tight net protects profits).
        sc = res.get("score")
        if sc is not None and sc < MISMATCH_NORMAL * 0.8:
            st = _state()
            if st.pop("rcl_last", None) is not None:
                _save(st)
    gf2 = None if gf is None else round(gf * act["mult"], 3)
    return gf2, act


def _rot_key_of(when: datetime) -> str:
    try:
        from background_scanner import rotation_key_for_hour
        return rotation_key_for_hour(when.hour)
    except Exception:
        return ""


# The candidate pair currently asking (reentry_blocked_for binds it):
# the bounded re-measure is pair-scoped (rcl_measure's volatility and
# funding gaps are pair-specific), so the drift question must be asked
# about the asset asking to re-enter.
pair_hint: str | None = None


def reentry_blocked(when: datetime | None = None) -> bool:
    # (NOTE: reentry_blocked_for below is the scanner's entry point; it
    # binds the candidate's pair AND defaults `when` INSIDE the locked
    # section, so the override's rotation match and the re-measure both
    # see one consistent snapshot.)
    """TRUE while a fresh drift verdict stands (spec: ReEntryCandidate
    = FALSE). Stale verdicts expire — and a MILD verdict past
    DRIFT_TTL_MILD_MIN re-asks reality ONCE (the Oct 1 bounded
    re-calibration): a calm reading lifts the suppression and refreshes
    the stored verdict, a still-drifting reading continues the freeze
    on fresh evidence. MAJOR keeps the full DRIFT_TTL_MIN freeze. The
    operator override (reentry_override_set) can open the gate under
    its own sign-off; the RCL being DOWN never blocks (fail-open, the
    scanner's long-standing rule)."""
    r = _state().get("rcl_last") or {}
    if not r:
        return False
    age_s = time.time() - float(r.get("ts") or 0)
    if age_s > DRIFT_TTL_MIN * 60:
        return False
    if r.get("verdict") not in ("MILD DRIFT", "MAJOR DRIFT"):
        return False
    if (r.get("verdict") == "MILD DRIFT"
            and age_s > DRIFT_TTL_MILD_MIN * 60):
        # BOUNDED RE-CALIBRATION: one fresh measure decides — ask about
        # the candidate's pair (XBT as the market-wide fallback when
        # unbound, e.g. the exit cycle's generic probe).
        try:
            fresh = rcl_measure(pair_hint or "XBT/USD", when)
        except Exception:
            return False     # the layer being down never blocks
        if fresh.get("verdict") == "NORMAL":
            st = _state()
            st["rcl_last"] = dict(r, verdict="NORMAL",
                                  score=fresh.get("score"),
                                  reask_pair=pair_hint or "XBT/USD")
            _save(st)
            return False
        if fresh.get("verdict") == "UNKNOWN":
            # A dataless re-read is NO verdict — the layer being up but
            # blind cannot re-earn the freeze: no ts refresh (a no-data
            # answer must never extend the block indefinitely — the
            # bounded in bounded re-calibration) and no block (fail-open,
            # the layer's long-standing rule; the Copilot HIGH: the old
            # fall-through treated UNKNOWN as still-drifting).
            return False
        # STILL DRIFTING: refresh the stored verdict's ts so the
        # freeze continues on FRESH evidence (the Copilot HIGH: the
        # original verdict would hit its 4h TTL and expire silently
        # even though the re-measure just confirmed the drift). The
        # re-ask's OWN verdict rides along as reask_verdict (the
        # Copilot MEDIUM: state must represent the latest measurement)
        # while the verdict field keeps its MILD cadence — storing a
        # verbatim MAJOR would hand one boundary-crossing reading a
        # full silent 4h freeze with no re-ask ever again.
        st = _state()
        st["rcl_last"] = dict(r, score=fresh.get("score"),
                              ts=time.time(),
                              reask_verdict=fresh.get("verdict"),
                              reask_pair=pair_hint or "XBT/USD")
        _save(st)
    if (reentry_override_active(when)
            and pair_hint is not None
            and pair_hint == reentry_override_pair()):
        return False        # the override opens ITS pair only
    return True


_REENTRY_LOCK = threading.Lock()   # pair_hint is shared state; the bind
# + measure + override check must be one atomic decision (the Copilot
# MEDIUM: concurrent candidate checks could rebind it mid-flight)


def reentry_blocked_for(pair: str | None,
                        when: datetime | None = None) -> bool:
    """reentry_blocked with the candidate's pair bound (the scanner's
    candidate path calls THIS): the bounded re-measure is pair-scoped.
    `when` defaults INSIDE the lock so the override's rotation match
    uses the live clock (the Copilot HIGH: _rot_key_of(None) returned
    '' and the override could never match), and the whole decision is
    atomic against concurrent candidate checks."""
    with _REENTRY_LOCK:
        global pair_hint
        pair_hint = pair
        try:
            return reentry_blocked(when or _now_mst())
        finally:
            pair_hint = None


def reentry_override_active(when: datetime | None = None) -> bool:
    """The MANUAL OVERRIDE (Oct 1 quick-fix): an operator-signed gate
    opening. Stands for one rotation window max (4h cap), one pair
    only, and NEVER auto-fires — reentry_override_set is called from
    the console button (the two-person rule and the liquidity gate live
    there), and the scanner logs every allowed pass through it."""
    when = when or _now_mst()   # _rot_key_of(None) is '' — a no-when
    # probe must ask the LIVE rotation (same Copilot catch class as
    # reentry_blocked_for's when-defaulting)
    ov = _state().get("reentry_override") or {}
    if not ov or time.time() - float(ov.get("ts") or 0) > 4 * 3600:
        return False
    return _rot_key_of(when) == ov.get("rot")


def reentry_override_pair() -> str | None:
    ov = _state().get("reentry_override") or {}
    return ov.get("pair")


def reentry_override_set(pair: str, when: datetime | None = None) -> dict:
    """Set the override. Liquidity gating (book_imbalance > 0.6,
    ask_tob > 5 x tranche) is enforced at the CALLER before sign-off —
    this function is the record, not the judgment."""
    st = _state()
    st["reentry_override"] = {"pair": pair, "ts": time.time(),
                              "rot": _rot_key_of(when or _now_mst())}
    _save(st)
    return st["reentry_override"]


def reentry_override_clear() -> None:
    # Write a FALSY TOMBSTONE, never pop: _save merges onto the file's
    # current contents, so a popped key is resurrected by the very save
    # meant to delete it (the clear was a no-op on disk — pin 2.19's
    # drive-only failure exposed it), and putting the key in _SAVE_DROPS
    # would let ANY concurrent writer erase a live operator override.
    # {} reads as absent to every reader (the `or {}` idiom) without
    # handing the merge a key to resurrect.
    st = _state()
    st["reentry_override"] = {}
    _save(st)


def endgame_active(when: datetime | None = None) -> bool:
    """RotationEndGameMode: armed by major drift, dies with its rotation."""
    st = _state()
    eg = st.get("endgame") or {}
    rot = eg.get("rot")
    if not rot:
        return False
    when = when or _now_mst()
    if _rot_key_of(when) != rot:
        return False                      # the rotation rolled over
    return True


def rcl_last() -> dict | None:
    r = _state().get("rcl_last") or {}
    return r or None


# ═══════════════ SECTION 2 — THE SELF-AUDIT LAYER ═══════════════
def audit_close(row: dict, watch: dict | None = None,
                when: datetime | None = None) -> dict:
    """Run after a trade closes. Counts RuleViolations from the checks
    that are machine-verifiable from the row itself, applies the spec's
    adjustments for tomorrow, and returns the audit. Unverifiable
    checks record 'n/a' — only real violations count, never silence."""
    when = when or _now_mst()
    watch = watch or {}
    checks = {
        "DidBotFollowTrailPct": _check_trail_followed(row, watch),
        "DidGuardTriggerCorrectly": _check_guard_reason(row),
        "DidReEntryLogicApply": "n/a" if row.get("is_reentry") is None
                                else bool(row.get("is_reentry") is not None),
        "DidRotationAwarenessApply": bool(row.get("exit_reason") or row.get("note")),
        "DidShadowTrailUpdate": "n/a",
        "DidCooldownRecompute": "n/a",
        "DidEndGameModeApply": "n/a",
        "DidContinuationDetectorApply": "n/a",
    }
    violations = sum(1 for v in checks.values() if v is False)
    st = _state()
    today = when.strftime("%Y-%m-%d")
    audits = st.setdefault("sal_audits", {})
    # the day's violations ACCUMULATE across closes — at most two checks
    # are machine-verifiable on any one row, so a per-close >=3 could
    # never fire (the drive's finding, Sep 22); three sloppy closes in
    # a day is the honesty signal
    rec = audits.get(today) or {"closes": 0, "violations": 0}
    rec["closes"] = int(rec.get("closes", 0)) + 1
    rec["violations"] = int(rec.get("violations", 0)) + violations
    audits[today] = rec
    st["sal_last"] = {"date": today, "violations": violations,
                      "day_violations": rec["violations"],
                      "checks": checks, "ts": when.isoformat(timespec="seconds")}
    if rec["violations"] >= 3:
        st["sal_adjust"] = SAL_BAD_MULT
        st["confidence_dock"] = {"rotation": -1, "tide": -1,
                                 "from": today}
    elif violations == 0:
        st["sal_adjust"] = SAL_GOOD_MULT
        prev_pen = float(st.get("discipline_penalty_extra", 0.0))
        st["discipline_penalty_extra"] = round(prev_pen * 0.9, 3)
    _save(st)
    return st["sal_last"]


def _check_trail_followed(row: dict, watch: dict) -> bool | None:
    """A PROFIT GUARD close must have exited at/under its own stop."""
    reason = str(row.get("exit_reason") or "")
    if "PROFIT GUARD" not in reason:
        return None                        # not the guard's close — n/a
    try:
        entry = float(row.get("entry") or 0)
        exitp = float(row.get("exit") or 0)
        high = float(watch.get("high") or 0)
        goal = float(row.get("goal_pct") or watch.get("goal_pct") or 0)
        if not (entry and exitp and high):
            return None
        import profit_guard as _pg
        res = _pg.guard_factor(row.get("pair", ""), watch)
        if not res.get("readable"):
            return None
        gf = float(res["factor"])
        cap = max(goal * _pg.GOAL_TRAIL_ARM_FRAC, 1.0) * gf
        stop = high * (1 - cap / 100.0)
        return exitp <= stop * 1.0015      # tiny tolerance for the tick gap
    except Exception:
        return None


def _check_guard_reason(row: dict) -> bool | None:
    """An auto-close must say why (every gate names its reason)."""
    if row.get("closed_by") == "manual":
        return None
    return bool(str(row.get("exit_reason") or "").strip())


def sal_adjust() -> float:
    """Tomorrow's SAL multiplier as stored (1.0 until an audit lands)."""
    return float(_state().get("sal_adjust", 1.0))


def discipline_penalty_extra() -> float:
    """The RCL/SAL-docked extra penalty (0-3) the guard adds."""
    return float(_state().get("discipline_penalty_extra", 0.0))


def dock_silent_rotation(n_buried: int, when: datetime | None = None,
                         n_payers: int = 0,
                         rot_key: str | None = None) -> dict:
    """THE SILENT-ROTATION DOCK (user ask, Sep 28: "when a rotation
    happens but no popups fired it should ask why + self adjust"),
    PROPORTIONAL (user ask, Sep 28: "dock proportional to the measured
    opportunity cost"): the flat +0.5 was a FLAG, not a size — a day
    burying one candidate stung exactly like a day burying 27 payers.
    The dock is now measured: 0.5 per payer x the burial side's own
    PAYER-RATE (payers / buried), rounded to 0.1, so the magnitude
    follows the opportunity cost; the honest drought still docks
    NOTHING; burials that didn't pay are the gate being RIGHT and dock
    nothing beyond the drought. Same SAL channel, same 3.0 lifetime
    cap, same x0.9 cooldown decay. Per-rotation cap 2.0 keeps a "docked
    penalty" from equaling a "fired and lost" in one sting (payers
    > 4). Deduped per rotation key (a restart at the boundary cannot
    double-sting). Returns the dock summary for the drive; never
    raises.

    DEFAULT SEMANTICS (Sep 28): n_payers=0 means "the burials were
    duds — the gate was right" and docks NOTHING. That is the
    conservative direction for live money: a boundary whose candle
    reads failed (the coin test fails closed to False) under-docks —
    it never false-loosens the funnel on unmeasured data. Callers
    that MEASURED must pass what they measured — discipline's
    boundary always does."""
    when = when or _now_mst()
    try:
        if not n_buried or int(n_buried) <= 0:
            return {"docked": 0.0,
                    "reason": "honest drought — nothing docked"}
        n_payers = max(0, min(int(n_payers), int(n_buried)))
        dock = round(0.5 * n_payers * (n_payers / int(n_buried)), 1)
        dock = min(dock, 2.0)
        st = _state()
        # the dedup key carries the DATE (CodeRabbit, Sep 28) and the
        # EVALUATED rotation (the isolated review's follow-up, Sep 28):
        # the boundary fires AT 16:00 ABOUT 12-16 — deriving the key
        # from `when`'s hour labels the dock with the window that is
        # BEGINNING, not the one being judged. discipline passes the
        # rot it evaluated; `when` stays the fallback.
        key = f"{when.strftime('%Y-%m-%d')} " \
              f"{rot_key or _rot_key_of(when)}"
        sr = st.get("silent_docks") or {}
        if sr.get("rot") == key:
            return {"docked": 0.0, "reason": f"already docked for {key}"}
        if dock <= 0:
            st["silent_docks"] = {"rot": key, "buried": int(n_buried),
                                  "payers": n_payers, "docked": 0.0,
                                  "ts": when.isoformat(timespec="seconds")}
            _save(st)
            return {"docked": 0.0, "penalty":
                    float(st.get("discipline_penalty_extra", 0.0)), "rot": key,
                    "buried": int(n_buried), "payers": n_payers,
                    "reason": "burials were duds — the gate was right; no dock"}
        pen = min(float(st.get("discipline_penalty_extra", 0.0)) + dock, 3.0)
        st["discipline_penalty_extra"] = round(pen, 2)
        st["silent_docks"] = {"rot": key, "buried": int(n_buried),
                              "payers": n_payers, "docked": dock,
                              "ts": when.isoformat(timespec="seconds")}
        _save(st)
        return {"docked": dock, "penalty": pen, "rot": key,
                "buried": int(n_buried), "payers": n_payers}
    except Exception:
        return {"docked": 0.0, "reason": "dock failed soft"}


# ═══════════════ SECTION 3 — THE DAILY NARRATIVE LAYER ═══════════════
def dnl_report(when: datetime | None = None) -> dict | None:
    """Collect the day, answer the seven questions, store the report,
    and derive the learning factor that scales tomorrow's DailyGoal.
    Runs LAST at cooldown. None when the day is unreadable."""
    when = when or _now_mst()
    today = when.strftime("%Y-%m-%d")
    try:
        import profit_guard as _pg
        import discipline as _disc
        y = (when - timedelta(days=1)).strftime("%Y-%m-%d")
        facts = None
        try:
            facts = _disc.cooldown_review(None, when=when - timedelta(days=1),
                                          speak=False, dry_run=True)
        except Exception:
            facts = None
        mlt = (facts or {}).get("total_left")
        pace = (facts or {}).get("daily_pace")
        gf_now = _pg.stored_factor()
        st = _state()
        rcl = rcl_last()
        sal = st.get("sal_last") or {}
        try:
            trades = [json.loads(ln) for ln in
                      (ROOT / "contest_trades.jsonl").read_text(encoding="utf-8")
                      .splitlines() if ln.strip()]
        except Exception:
            trades = []
        day_trades = [t for t in trades
                      if str(t.get("sold_at", "")).startswith(today)]
        pnl = sum(float(t.get("pnl_net") or 0) for t in day_trades)
        gf_prev = gf_now      # yesterday's stored factor IS the day's factor
        report = {
            "date": today,
            "What did I learn today?": _learned(facts, rcl, sal),
            "What surprised me?": _surprised(rcl, day_trades),
            "What worked?": _worked(day_trades, gf_now),
            "What didn't?": _didnt(day_trades, sal),
            "What adjustments were made?": _adjustments(gf_now, rcl, sal),
            "How do I make more next time?": _more_next_time(mlt, pace, rcl),
            "What is tomorrow's focus?": _focus(rcl, sal, mlt),
            "_data": {"money_left": mlt, "daily_pace": pace,
                      "guard_factor": gf_prev, "realized": round(pnl, 2),
                      "n_trades": len(day_trades),
                      "rcl": (rcl or {}).get("verdict", "UNKNOWN"),
                      "sal_violations": sal.get("violations"),
                      "reentry_blocks": st.get("reentry_blocks_today", 0),
                      "endgame_events": st.get("endgame_events", 0)},
        }
        lf = 0.0
        if isinstance(mlt, (int, float)) and isinstance(pace, (int, float)) and pace > 0:
            lf = max(-DNL_GOAL_FACTOR_CAP, min(DNL_GOAL_FACTOR_CAP,
                                               (float(mlt) / float(pace)) * 0.2))
        # THE SHADOW TRAIL'S MISS feeds the learning factor (user spec
        # section 3.4: ShadowGain -> tomorrow's GuardFactor/DailyGoal):
        # graduated shadows since last night add their miss at half the
        # MLT weight, then the counters reset (consumed by this report)
        _sh = shadow_graduated_notes()
        report["_data"]["shadow_missed_usd"] = _sh["missed_usd"]
        if _sh["missed_usd"] > 0 and isinstance(pace, (int, float)) and pace > 0:
            lf = max(-DNL_GOAL_FACTOR_CAP, min(
                DNL_GOAL_FACTOR_CAP,
                lf + (_sh["missed_usd"] / float(pace)) * 0.1))
        st["shadow_graduations"] = 0
        st["shadow_missed_usd_total"] = 0.0
        report["_data"]["learning_factor"] = round(lf, 3)
        st["dnl"] = st.get("dnl") or {}
        try:
            import carve_out as _co
            _goal_mult, _gr = _co.goal_context(when)
        except Exception:
            _goal_mult, _gr = 1.0, False
        st["tomorrow_goal_factor"] = round((1.0 + lf) * (_goal_mult if _gr else 1.0), 3)
        # THE CARVE-OUT'S NIGHTLY RECORD (user spec, Sep 23, Section 5):
        # the day's CarveOut and its five mods ride the narrative data —
        # the learning layer sees what the funnel's allowance did today
        try:
            import carve_out as _co_d
            _ln = _co_d.last_night()
            if _ln:
                report["_data"]["carve_out"] = _ln.get("carve")
                report["_data"]["carve_mods"] = {k: _ln.get(k) for k in
                                                 ("fleet_mod", "book_mod",
                                                  "tide_mod", "vol_mod",
                                                  "rot_mod")}
                # THE BOUNDARY RECORD (clamp Section 5): the day's live
                # clamp bands + the context snapshot ride the narrative
                # data — the learning loop sees not just the readings but
                # the bounds they were allowed to move within
                try:
                    import carve_out as _co_b
                    _m = _co_b._mods_n(when=when)
                    _gbase = None
                    try:
                        import profit_guard as _pg_g
                        _gfacts = _pg_g._daily_facts(when)
                        _gbase = (_gfacts or {}).get("daily_pace")
                    except Exception:
                        _gbase = None
                    report["_data"]["clamp_bounds"] = {
                        "max_carve_out": _co_b.max_carve_out(when=when),
                        "gf_bounds": list(_co_b.gf_bounds(res=None, when=when)),
                        "hatch_bounds": list(_co_b.hatch_bounds(when=when)),
                        "decay_bounds": list(_co_b.decay_bounds(when=when)),
                        "penalty_bounds": list(_co_b.penalty_bounds(when=when)),
                        "dailygoal_bounds": list(_co_b.goal_bounds(
                            _gbase or 0.0, when=when)),
                        "readable": _m["readable"],
                        "context": {"fleet_mod": _m["fm"], "book_mod": _m["bm"],
                                    "tide_mod": _m["tm"], "vol_mod": _m["vm"],
                                    "rot_mod": _m["rm"]}}
                except Exception:
                    report["_data"]["clamp_bounds"] = {"readable": False}
        except Exception:
            pass
        # THE BOUNDARY CORRECTIONS RECORD (user spec, Sep 23, section 4
        # Logging): the cross-examination's dial moves (the expiring
        # overlays) ride the same night data — the ledger shows what the
        # bands WERE and what the exam did about it. Recorded every night
        # regardless of the carve record; an empty record is an explicit
        # none, so the absence stays auditable.
        try:
            import carve_out as _co_c
            _bc = _co_c.boundary_corrections()
            report["_data"]["BoundaryCorrections"] = {
                "set_date": _bc.get("set_date"),
                "expires": _bc.get("expires"),
                "overlays": _bc.get("overlays") or {},
            }
        except Exception:
            report["_data"]["BoundaryCorrections"] = {}
        # stored LAST (the 4.5 lesson): the carve record and the boundary
        # record above mutate the report — the stored copy must carry them
        st["dnl"][today] = report
        st["dnl_last_date"] = today
        st["reentry_blocks_today"] = 0
        _save(st)
        return report
    except Exception:
        return None


def _learned(facts, rcl, sal) -> str:
    bits = []
    if facts:
        bits.append(f"score {facts.get('score')}, left ${facts.get('total_left') or 0:.0f} "
                    f"of a ${facts.get('daily_pace') or 0:.0f} pace")
    if rcl and rcl.get("verdict") not in (None, "UNKNOWN", "NORMAL"):
        bits.append(f"reality {rcl['verdict'].lower()} (mismatch {rcl.get('score')})")
    if sal.get("violations") is not None:
        bits.append(f"{sal['violations']} rule violations")
    return "; ".join(bits) or "the day kept its shape — nothing new"


def _surprised(rcl, day_trades) -> str:
    if rcl and rcl.get("verdict") == "MAJOR DRIFT":
        return f"the market stopped matching the calibration (mismatch {rcl.get('score')})"
    big = max(day_trades, key=lambda t: abs(float(t.get("pnl_net") or 0)), default=None)
    if big and abs(float(big.get("pnl_net") or 0)) > 15:
        return (f"{big.get('pair')} closed {float(big['pnl_net']):+,.2f} — "
                f"outside the day's rhythm")
    return "nothing — the day behaved"


def _worked(day_trades, gf) -> str:
    wins = [t for t in day_trades if float(t.get("pnl_net") or 0) > 0]
    if wins:
        tot = sum(float(t["pnl_net"]) for t in wins)
        return (f"{len(wins)} green closes worth {tot:+,.2f}"
                + (f" (guard {gf:.2f} let them run)" if gf and gf > 1.0 else ""))
    return "the guard held the line — nothing given back"


def _didnt(day_trades, sal) -> str:
    losses = [t for t in day_trades if float(t.get("pnl_net") or 0) < 0]
    bits = []
    if losses:
        tot = sum(float(t["pnl_net"]) for t in losses)
        bits.append(f"{len(losses)} red closes worth {tot:,.2f}")
    v = sal.get("violations")
    if v:
        bits.append(f"{v} self-audit violations — the rules slipped")
    return "; ".join(bits) or "no red ink and no slips"


def _adjustments(gf, rcl, sal) -> str:
    bits = []
    if gf:
        bits.append(f"guard factor now {gf:.2f}")
    if rcl and rcl.get("verdict") not in (None, "UNKNOWN", "NORMAL"):
        bits.append(f"RCL {rcl['verdict'].lower()} applied")
    if sal.get("violations") is not None:
        bits.append(f"SAL x{sal_adjust():.2f} for tomorrow")
    return "; ".join(bits) or "no dials moved"


def _more_next_time(mlt, pace, rcl) -> str:
    if isinstance(mlt, (int, float)) and mlt and mlt > 0:
        return (f"${mlt:.0f} was left on the table — the exits were tighter "
                f"than the market deserved")
    if rcl and rcl.get("verdict") in ("MILD DRIFT", "MAJOR DRIFT"):
        return "stand down faster when reality drifts — the calibration is the map, not the road"
    return "keep the trail where it is — the book is paying"


def _focus(rcl, sal, mlt) -> str:
    if sal.get("violations") and sal["violations"] >= 3:
        return "follow the rules that already exist — before new ones"
    if rcl and rcl.get("verdict") == "MAJOR DRIFT":
        return "re-read the market before trusting the calibration"
    if isinstance(mlt, (int, float)) and mlt and mlt > 0:
        return "let the winners breathe — the goal is the floor, not the ceiling"
    return "repeat today with the same discipline"


def tomorrow_goal_multiplier() -> float:
    """DailyGoal = base_goal * (1 + learning_factor_from_DNL) — the
    multiplier DNL wrote last night (1.0 until the first report)."""
    return float(_state().get("tomorrow_goal_factor", 1.0))


def dnl_last() -> dict | None:
    return _state().get("dnl", {}).get(_state().get("dnl_last_date", "")) or None


# ═══════════════ THE COMPOSER — spec section 4 ═══════════════
def finalize(pair: str, gf: float | None, res: dict | None = None,
             when: datetime | None = None) -> tuple[float | None, dict]:
    """The exit watch's one call: RCL runs BEFORE the factor is
    finalized, then SAL's stored adjustment multiplies it, then THE
    CARVE-OUT (user spec, Sep 23, Section 2: GF *= 1 + CarveOut — tide
    out tightens the net, tide in loosens it), then momentum, then
    End-Game Mode's override is reported. Returns (final GF, meta)."""
    when = when or _now_mst()
    meta: dict = {}
    gf_after_rcl, act = rcl_apply(rcl_measure(pair, when), gf, when)
    meta.update(act)
    sal_m = sal_adjust()
    if sal_m != 1.0:
        meta["sal_mult"] = sal_m
    final = None if gf_after_rcl is None else round(gf_after_rcl * sal_m, 3)
    # THE ADAPTIVE CARVE-OUT (user spec, Sep 23, Section 2): the final
    # factor scales by (1 + CarveOut) — computed AFTER RCL/SAL so the
    # allowance already carries their drift and discipline adjustments
    try:
        import carve_out as _co
        if final is not None:
            _cres = _co.gate(pair=pair, when=when)
            _carm, _cmeta = _co.apply_to_guard_factor(final, _cres, when)
            # the boundary rides the meta: the finalize's final GF band
            # (gf_min/gf_max) and the day's breathing carve ceiling — the
            # stamp and the console render the range, they never compute it
            _cmeta["max_carve"] = _co.max_carve_out(res=_cres, when=when)
            if _carm is not None:
                final = _carm
                meta.update(_cmeta)
    except Exception:
        pass
    # MOMENTUM CONTINUATION feeds the factor (user spec section 3.3):
    # while the detector says the move isn't done, the net loosens 15%
    if final is not None and pair and momentum_active(pair):
        final = round(final * MOMENTUM_CONT_MULT, 3)
        meta["momentum_continuation"] = True
    if final is not None:
        # THE FINAL ADAPTIVE CLAMP (user spec, Sep 23, clamp 2.2 + the
        # integration table): the finalize's own clamp is the band —
        # MinGF/MaxGF breathe with TideMod x BookMod / RotationMod x
        # VolMod (delivered by the carve step above). No carve meta ->
        # the committed static band (fail-open).
        _lo, _hi = meta.get("gf_min"), meta.get("gf_max")
        _lo = _lo if isinstance(_lo, (int, float)) else 0.4
        _hi = _hi if isinstance(_hi, (int, float)) else 2.0
        final = round(max(_lo, min(max(_lo, _hi), final)), 3)
    if endgame_active(when):
        meta["endgame"] = True
    return final, meta


def endgame_scales() -> tuple[float, float]:
    """(trail multiplier, lock-in multiplier) — End-Game Mode's override."""
    return (ENDGAME_TRAIL_MULT, ENDGAME_LOCKIN_MULT)


def note_reentry_block(when: datetime | None = None) -> None:
    st = _state()
    st["reentry_blocks_today"] = int(st.get("reentry_blocks_today", 0)) + 1
    _save(st)


# ═══════════════ THE FOUR BEHAVIORS (user spec, Sep 22) ═══════════════
SHADOW_MAX_HOURS = 8            # how long a shadow trail tracks an exit
SHADOW_MIN_GAIN_USD = 1.0       # below this, the miss is noise, not a lesson
MOMENTUM_CONT_MULT = 1.15       # GuardFactor boost while the move lives
MOMENTUM_TTL_MIN = 120          # the verdict's freshness window
CONT_NEEDED = 3                 # >= 3 of 5 signals fire the detector
ENDGAME_TRIGGERS_NEEDED = 2     # >= 2 of 5 triggers arm end-game


# ── BEHAVIOR 2 — MOMENTUM CONTINUATION DETECTION ─────────────────────────
def _momentum_signals(pair: str, when: datetime) -> dict:
    """The five continuation signals, each True/False/None (None = the
    data could not answer; None never counts as fired)."""
    hh = _signal_higher_highs(pair)
    fr = _signal_funding_rising(pair)
    ls = _signal_liquidity_slope_positive()
    vc = _signal_vol_compression(pair)
    ra = _signal_rotation_active(when)
    return {"higher_highs": hh, "funding_rising": fr,
            "liquidity_slope_positive": ls, "vol_compression": vc,
            "rotation_active": ra}


def _candles(pair: str, n: int = 30):
    """The one candle read (full OHLCV since the Tri-Watch's health
    needs the indicator set; the older consumers touch only close/high
    and ignore the extra columns)."""
    try:
        import sqlite3
        import pandas as _pd
        cc = sqlite3.connect(ROOT / "ohlcv_cache.db")
        try:
            df = _pd.read_sql_query(
                "SELECT ts, open, high, low, close, volume FROM ohlcv "
                "WHERE pair=? ORDER BY ts DESC LIMIT ?", cc, params=(pair, n))
        finally:
            cc.close()
        return df.iloc[::-1].reset_index(drop=True) if len(df) >= 10 else None
    except Exception:
        return None


def _signal_higher_highs(pair: str) -> bool | None:
    """The last two completed candles both made higher highs than their
    predecessors — the climb is still making new ground."""
    df = _candles(pair, 30)
    if df is None:
        return None
    try:
        h = df["high"].tolist()
        return bool(h[-1] > h[-2] > h[-3])
    except Exception:
        return None


def _signal_funding_rising(pair: str) -> bool | None:
    try:
        import profit_guard as _pg
        m = _pg.funding_mod(pair)
        if m == _pg.FUNDING_NEUTRAL_MULT:
            return None                    # spot/no data — honest unknown
        return m > 1.0
    except Exception:
        return None


def _signal_liquidity_slope_positive() -> bool | None:
    """Fleet breadth rising over the recent samples (RCL's own slope
    series is the data source — one truth)."""
    try:
        from background_scanner import _fleet_tide
        pct, _s = _fleet_tide()
        if pct is None:
            return None
        now = time.time()
        _SLOPE.append((now, float(pct)))
        keep = now - SLOPE_WINDOW_MIN * 60
        while _SLOPE and _SLOPE[0][0] < keep:
            _SLOPE.pop(0)
        if len(_SLOPE) < 2:
            return None
        return _SLOPE[-1][1] > _SLOPE[0][1]
    except Exception:
        return None


def _signal_vol_compression(pair: str) -> bool | None:
    """Recent true ranges tightening vs the prior stretch — coiling,
    not exhausting."""
    df = _candles(pair, 30)
    if df is None:
        return None
    try:
        c = df["close"].tolist()
        rng = [abs(c[i] - c[i - 1]) / c[i] for i in range(1, len(c))]
        recent = sum(rng[-5:]) / 5.0
        prior = sum(rng[-20:-5]) / 15.0 or 1e-9
        return bool(recent < prior * 0.8)
    except Exception:
        return None


def _signal_rotation_active(when: datetime) -> bool | None:
    """The current rotation still holds buy hours (the cooldown window
    20-24 is never 'active' for continuation)."""
    try:
        from background_scanner import ROTATIONS
        rk = _rot_key_of(when)
        row = next((r for r in ROTATIONS if r[2] == rk), None)
        if row is None:
            return None
        return "NO TRADING" not in str(row[7])
    except Exception:
        return None


def momentum_check(pair: str, when: datetime | None = None) -> dict:
    """The detector: >= 3 of 5 signals -> TRUE, persisted with a TTL so
    the guard can consume it per tick without re-reading candles."""
    when = when or _now_mst()
    sig = _momentum_signals(pair, when)
    fired = sum(1 for v in sig.values() if v is True)
    cont = fired >= CONT_NEEDED
    if cont:
        st = _state()
        st["momentum"] = {"pair": pair, "until": time.time() + MOMENTUM_TTL_MIN * 60,
                          "fired": fired, "signals": sig}
        _save(st)
    return {"detected": cont, "fired": fired, "signals": sig}


def momentum_active(pair: str) -> bool:
    """The detector's persisted verdict, fresh within the TTL."""
    m = _state().get("momentum") or {}
    return (m.get("pair") == pair and time.time() < float(m.get("until") or 0))


# ── BEHAVIOR 3 — THE POST-EXIT SHADOW TRAIL ────────────────────────────
def _exchange_of():
    """The scanner's live exchange, whichever way it holds one."""
    import background_scanner as _bs
    for name in ("exchange", "_exchange", "EXCHANGE", "_ex"):
        ex = getattr(_bs, name, None)
        if ex is not None and hasattr(ex, "fetch_ticker"):
            return ex
    try:
        return _bs._get_exchange()
    except Exception:
        raise RuntimeError("no live exchange available for shadow tracking")
def shadow_trail_start(pair: str, exit_price: float, watch: dict | None = None,
                       when: datetime | None = None) -> None:
    """Begin tracking what the market did AFTER we left: the next high,
    the next pullback, the next breakout, the next liquidity surge.
    Called at every auto-close. Fail-soft — a lost shadow is a missing
    lesson, never a broken exit."""
    try:
        when = when or _now_mst()
        st = _state()
        st["shadows"] = st.get("shadows") or {}
        st["shadows"][pair] = {
            "exit_price": float(exit_price or 0),
            "exit_ts": when.isoformat(timespec="seconds"),
            "rot": _rot_key_of(when),
            # (CodeRabbit major) the miss is USD, not price delta: a
            # $0.01 coin could never graduate, BTC over-counted. The
            # position size rides the shadow so graduation computes
            # (peak/exit - 1) * size — real dollars left on the table.
            "size": float((watch or {}).get("size") or 0.0),
            "peak": float(exit_price or 0), "last": float(exit_price or 0),
            "dip": float(exit_price or 0), "breakout": False,
            "liq_surge": False}
        _save(st)
    except Exception:
        pass


def shadow_trail_tick(pairs: list[str] | None = None,
                      force: bool = False,
                      exchange=None) -> None:
    """~5-min cadence (gated; force=True for drives): update every live
    shadow (peak, pullback, breakout, liquidity surge), then graduate
    the trails that aged out — a graduation WRITES the miss into the
    ledgers the way a trade close does. `exchange`: the caller's live
    exchange object (contest_light passes its own); without one we scan
    the scanner module for it."""
    st = _state()
    if not force and time.time() - float(st.get("last_shadow_tick", 0)) < 300:
        return
    st["last_shadow_tick"] = time.time()
    shadows = st.get("shadows") or {}
    if not shadows:
        _save(st)
        return
    if pairs is None:
        pairs = list(shadows)
    try:
        from background_scanner import _fleet_tide
        pct, _s = _fleet_tide()
    except Exception:
        pct = None
    now = time.time()
    for pair in list(shadows):
        if pair not in pairs:
            continue
        sh = shadows[pair]
        try:
            ex = exchange if exchange is not None else _exchange_of()
            live = float(ex.fetch_ticker(pair)["last"])
        except Exception:
            live = None
        if live:
            sh["last"] = live
            sh["peak"] = max(float(sh.get("peak") or 0), live)
            sh["dip"] = min(float(sh.get("dip") or 9e9), live)
            if live > float(sh.get("exit_price") or 0) * 1.01:
                sh["breakout"] = True
        if pct is not None and pct >= 70.0:
            sh["liq_surge"] = True
        # graduation: the trail is older than its window
        try:
            age_h = (now - datetime.fromisoformat(sh["exit_ts"]).timestamp()) / 3600.0
        except Exception:
            age_h = 0.0
        if age_h >= SHADOW_MAX_HOURS:
            _sh_px = float(sh.get("exit_price") or 0)
            _sh_sz = float(sh.get("size") or 0.0)
            gain = ((float(sh.get("peak") or 0) / _sh_px - 1.0) * _sh_sz
                    if _sh_px > 0 and _sh_sz > 0 else 0.0)
            if gain >= SHADOW_MIN_GAIN_USD:
                st["shadow_graduations"] = st.get("shadow_graduations", 0) + 1
                st["shadow_missed_usd_total"] = round(
                    float(st.get("shadow_missed_usd_total", 0.0)) + gain, 2)
            del shadows[pair]
    st["shadows"] = shadows
    _save(st)


def shadow_graduated_notes() -> dict:
    """The graduated shadows' miss ledger, for the cooldown loop."""
    st = _state()
    return {"count": int(st.get("shadow_graduations", 0)),
            "missed_usd": float(st.get("shadow_missed_usd_total", 0.0)),
            "tight_notes": int(st.get("guard_note_tight", 0) or 0)}


def shadow_missed_gain(pair: str) -> float | None:
    """A LIVE shadow's unrealized miss: next high after exit minus the
    exit price. None when no shadow is running."""
    sh = (_state().get("shadows") or {}).get(pair)
    if not sh:
        return None
    return round(max(float(sh.get("peak") or 0)
                     - float(sh.get("exit_price") or 0), 0.0), 4)


# ── BEHAVIOR 1 — RE-ENTRY AWARENESS (the five conditions) ──────────────
def reentry_candidate_check(pair: str,
                            when: datetime | None = None) -> dict:
    """The spec's five conditions: RotationStrength >= 3, TIDE rising,
    funding positive, AssetMemory >= 1.0, MomentumContinuationDetector
    TRUE. All five -> the asset is marked ReEntryCandidate and the next
    clean setup may re-enter (the exit-suppression bypass rides the
    reentry_blocked gate: a CANDIDATE under no drift is never blocked)."""
    when = when or _now_mst()
    try:
        import profit_guard as _pg
        rs = _pg.rotation_strength(when)
        ts = _pg.tide_score()
        am = _pg.asset_memory(pair)
    except Exception:
        rs = ts = am = None
    mom = momentum_check(pair, when)
    conds = {
        "rotation_strength_ge_3": (rs is not None and rs >= 3),
        "tide_rising": (ts == 5.0),
        "funding_positive": (_signal_funding_rising(pair) is True),
        "asset_memory_ge_1": (am is not None and am >= 1.0),
        "momentum_continuation": mom["detected"],
    }
    # THE RCL KILL (user spec, Sep 23, Section 3): a standing drift
    # verdict voids the candidate outright — no lone-wolf behavior in an
    # unstable market (reentry_blocked already expires stale verdicts)
    try:
        drift = reentry_blocked_for(pair, when)
    except Exception:
        drift = False
    # (reentry_blocked_for, not the unbound reentry_blocked: the drift
    # question is about the CANDIDATE'S OWN pair — the old call re-measured
    # the XBT fallback, so an XBT calm reading could clear the shared
    # verdict and wave a genuinely-drifting pair through its later gate;
    # the Copilot HIGH, pinned by 2.22)
    if not drift:
        conds.pop("rcl_drift", None)   # an expired/overridden verdict stops naming itself
    if drift:
        conds["rcl_drift"] = True
        return {"eligible": False, "conditions": conds,
                "why": "RCL drift stands — ReEntryCandidate = FALSE"}
    eligible = all(conds.values())
    if eligible:
        st = _state()
        cands = st.get("reentry_candidates") or {}
        cands[pair] = {"ts": when.isoformat(timespec="seconds"),
                       "rot": _rot_key_of(when), "conds": conds}
        st["reentry_candidates"] = cands
        _save(st)
    return {"eligible": eligible, "conditions": conds}


def reentry_candidate(pair: str) -> bool:
    """Is this pair a marked ReEntryCandidate for the CURRENT rotation?"""
    c = (_state().get("reentry_candidates") or {}).get(pair)
    if not c:
        return False
    return c.get("rot") == _rot_key_of(_now_mst())


# ── BEHAVIOR 4 — END-GAME MODE'S OWN TRIGGERS ─────────────────────────
def endgame_trigger_check(when: datetime | None = None,
                          force: bool = False) -> dict:
    """The spec's five triggers (strength dropping, TIDE flattening,
    funding flipping, volatility spiking, breadth narrowing); >= 2 arm
    RotationEndGameMode. Cadence-gated to ~5 min (a change needs two
    separated samples; force=True for drives)."""
    when = when or _now_mst()
    st = _state()
    if not force and time.time() - float(st.get("last_endgame_tick", 0)) < 300:
        prev_state = st.get("endgame_triggers") or {"fired": 0, "signals": {}}
        return {"armed": endgame_active(when), **prev_state}
    st["last_endgame_tick"] = time.time()
    prev = st.get("endgame_prev") or {}
    try:
        import profit_guard as _pg
        from background_scanner import _fleet_tide
        rs = _pg.rotation_strength(when)
        _pct, tide = _fleet_tide()
        trig = {
            "strength_dropping": (prev.get("rs") is not None
                                  and rs is not None and rs < prev["rs"]),
            "tide_flattening": (prev.get("tide") == "RISING"
                                and tide in ("FLAT", "OUT")),
            "funding_flipping": (prev.get("fdir") is not None
                                 and prev.get("fdir") != _fleet_funding_dir()),
            "vol_spiking": _vol_spike(),
            "breadth_narrowing": (prev.get("pct") is not None and _pct is not None
                                  and _pct < prev["pct"] - 10.0),
        }
        st["endgame_prev"] = {"rs": rs, "tide": tide, "pct": _pct,
                              "fdir": _fleet_funding_dir()}
    except Exception:
        trig = {k: False for k in ("strength_dropping", "tide_flattening",
                                   "funding_flipping", "vol_spiking",
                                   "breadth_narrowing")}
    fired = sum(1 for v in trig.values() if v is True)
    st["endgame_triggers"] = {"fired": fired, "signals": trig}
    # (CodeRabbit major) save BEFORE note_endgame: the merge-save only
    # protects keys absent from the snapshot — a stale endgame.rot from
    # an earlier rotation in `st` would overwrite the fresh latch.
    _save(st)
    if fired >= ENDGAME_TRIGGERS_NEEDED and not endgame_active(when):
        note_endgame(when)
    return {"armed": endgame_active(when), "fired": fired, "signals": trig}


def _fleet_funding_dir() -> str | None:
    """The fleet's average funding direction (BTC as the bellwether —
    the fleet's cheapest honest read)."""
    try:
        import profit_guard as _pg
        m = _pg.funding_mod("BTC/USD")
        if m == _pg.FUNDING_NEUTRAL_MULT:
            return None
        return "rising" if m > 1.0 else "falling"
    except Exception:
        return None


def _vol_spike() -> bool | None:
    """Fleet realized vol spiking: the latest 30-min bucket's median move
    exceeds 2x the prior 90 minutes' median."""
    try:
        import sqlite3
        import pandas as _pd
        cc = sqlite3.connect(ROOT / "ohlcv_cache.db")
        try:
            df = _pd.read_sql_query(
                "SELECT pair, ts, close FROM ohlcv WHERE ts >= "
                "(SELECT MAX(ts) FROM ohlcv) - 7200000 ORDER BY pair, ts", cc)
        finally:
            cc.close()
        if df.empty:
            return None
        cut = df["ts"].max() - 1800000
        def _med(sub):
            g = sub.groupby("pair")["close"]
            v = (g.last() / g.first() - 1.0).abs().dropna()
            return float(v.median()) if len(v) >= 5 else None
        now_m = _med(df[df["ts"] >= cut])
        prior_m = _med(df[df["ts"] < cut])
        if not now_m or not prior_m:
            return None
        return bool(now_m > prior_m * 2.0)
    except Exception:
        return None


def note_endgame(when: datetime | None = None) -> None:
    st = _state()
    st["endgame"] = {"rot": _rot_key_of(when or _now_mst())}
    st["endgame_events"] = int(st.get("endgame_events", 0)) + 1
    _save(st)
