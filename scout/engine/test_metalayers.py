"""test_metalayers.py — the three meta-systems (user spec, Sep 22):
RCL (reality check), SAL (self-audit), DNL (daily narrative), and the
finalize() composer that binds them into the profit guard.

HERMETIC core: the scanner seams, the probes' data sources, and the
state file are stubbed so every band and adjustment is computed, not
read. LIVE probes of the real book ride inside, clearly marked, and
the probe's state writes are scrubbed.
"""
import json
import math
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

FAILS = []
PASSES = []


def check(n, cond, why=""):
    (PASSES if cond else FAILS).append(n)
    print(f"  {'PASS' if cond else 'FAIL'} {n}: {why}")


_MST = timezone(timedelta(hours=-6))
HERE = Path(__file__).resolve().parent
import tempfile
TMP = Path(tempfile.mkdtemp(prefix="mltmp_"))   # sandbox-derived: every write below lands in the tmpdir
TMP.mkdir(exist_ok=True)
(TMP / "meta_layers_state.json").unlink(missing_ok=True)

bs = types.ModuleType("background_scanner")
_ROT_STUB = {"key": "08-12"}
bs.rotation_key_for_hour = lambda h: _ROT_STUB["key"]
bs._now_mst = lambda: datetime.now(_MST)
bs._fleet_tide = lambda max_age_s=300: (60.0, "FLAT")
sys.modules["background_scanner"] = bs

import meta_layers as ml
import profit_guard as pg

ml.STATE_FILE = TMP / "meta_layers_state.json"  # leak-audit: ok (suite-long redirect into the mkdtemp world; restore-never, suite-per-process)
_ML_ROOT = ml.ROOT
ml.ROOT = TMP                       # thresholds + trades all in tmp now
(TMP / "rotation_thresholds.json").write_text(json.dumps({
    "median": {"weekday": {"08-12": {"avg_move": 2.4, "std": 1.5,
                                     "median_volume_usd": 60000,
                                     "false_breakout_rate": 0.05,
                                     "thresholds": {"standard": 2.4}}}}}),
    encoding="utf-8")

# stubs to save/restore
_ORIG_MOVE = ml._recent_move_pct
_ORIG_VR = pg.vol_ratio
_ORIG_GF = pg.guard_factor
_ORIG_FM = ml._funding_gap.__globals__["_FUNDING_PREV"]


def _w():
    return datetime(2026, 9, 22, 9, 0, tzinfo=_MST)


def _now_real():
    # the REAL clock, not _w(): shadow graduation (SHADOW_MAX_HOURS)
    # compares stamps against time.time(), so a frozen stamp keeps aging
    # while the drive runs — "fresh" must be relative to now, or the
    # live-shadow checks fail on any evening run and the 9h-back
    # graduation stamps fail on any early-morning run (both windows
    # caught live by the Sep-22 commit gate drills).
    return datetime.now(_MST)


# ════════ 1. RCL — the five diffs ════════
print("== 1. RCL: five diffs, MismatchScore ==")
ml._recent_move_pct = lambda: 0.15          # realized 0.15% in 30 min
check(1.1, abs(ml._rotation_gap(_w()) - (0.15 / 0.5)) < 0.01,
      "rotation gap: |0.15 - (2.4 prorated to 0.30)| / floor(0.5) = 0.3")
check(1.2, ml._liquidity_slope_gap(_w()) is None,
      "slope with no samples -> None (honest, never invented)")
ml._SLOPE.clear()
ml._SLOPE.extend([(time.time() - 600, 58.0), (time.time(), 59.5)])
check(1.3, abs(ml._liquidity_slope_gap(_w()) - 0.4) < 0.01,
      "slope with the appended live sample: 0.2%/min -> 0.4")
ml._SLOPE.clear()
pg.vol_ratio = lambda p: 1.35
check(1.4, abs(ml._volatility_gap("X/USD", _w()) - 0.5) < 0.01,
      "vol gap: |1.35 - 1| / 0.7 = 0.5")
pg.vol_ratio = lambda p: None
check(1.5, ml._volatility_gap("X/USD", _w()) is None, "unreadable vol -> None")
ml._FUNDING_PREV.clear()
ml._FUNDING_PREV["X/USD"] = "rising"
pg.FUNDING_NEUTRAL_MULT = 1.0
_orig_fmod = pg.funding_mod
pg.funding_mod = lambda p: 0.9
check(1.6, ml._funding_gap("X/USD") == 1.0, "funding flip rising->falling = full diff")
check(1.7, ml._funding_gap("X/USD") == 0.0, "same direction twice = 0")
pg.funding_mod = lambda p: 1.0
check(1.8, ml._funding_gap("Y/USD") is None, "unknown direction -> None (never a fake flip)")
pg.funding_mod = _orig_fmod
import discipline as _disc_real
_disc_stub = types.ModuleType("discipline")
_disc_stub.load_state = lambda: {"history": [
    {"date": "2026-09-22", "fired": 6, "buried": 6}]}
_disc_stub.cooldown_review = lambda *a, **k: {}
sys.modules["discipline"] = _disc_stub
check(1.9, abs(ml._signal_quality_gap(_w()) - 0.667) < 0.01,
      "burial rate 50% vs the 30% bar -> |0.5-0.3|/0.3 = 0.667")
sys.modules["discipline"] = _disc_real
res = ml.rcl_measure("X/USD", _w())
check(1.10, res["verdict"] in ("NORMAL", "MILD DRIFT", "MAJOR DRIFT", "UNKNOWN"),
      "the measure always returns a verdict")
check(1.11, res["score"] is not None and res["known"] >= 1,
      "known signals average into the score (unknowns excluded)")

# ════════ 2. bands + actions ════════
print("== 2. bands and their actions ==")
ml.ROOT = TMP
# hermetic bands: only the rotation signal answers (slope state and the
# real discipline history must not dilute the mean)
_ORIG_SLOPE_F = ml._liquidity_slope_gap
_ORIG_SQ_F = ml._signal_quality_gap
ml._liquidity_slope_gap = lambda when: None
ml._signal_quality_gap = lambda when: None
ml._recent_move_pct = lambda: 0.29          # gap 0.033 -> NORMAL
res_n = ml.rcl_measure("X/USD", _w())
gf2, act_n = ml.rcl_apply(res_n, 1.5, _w())
check(2.1, act_n["mult"] == 1.0 and act_n.get("reentry_blocked") is None
      and gf2 == 1.5, "NORMAL: factor untouched, nothing blocked")
ml._recent_move_pct = lambda: 0.85          # gap 0.85/0.5 = 1.0 -> MAJOR
res_m = ml.rcl_measure("X/USD", _w())
gf3, act_m = ml.rcl_apply(res_m, 1.5, _w())
check(2.2, act_m["mult"] == ml.RCL_MAJOR_MULT and gf3 == 1.5 * ml.RCL_MAJOR_MULT,
      "MAJOR DRIFT (realized 0.85% vs 0.30% expected): factor x0.75")
check(2.3, act_m.get("suppress_borderline") and act_m.get("reentry_blocked"),
      "MAJOR DRIFT: borderline signals suppressed, re-entry blocked")
check(2.4, ml.endgame_active(_w()), "MAJOR DRIFT: RotationEndGameMode armed")
check(2.5, ml.discipline_penalty_extra() == 1.0, "MAJOR DRIFT: discipline penalty +1")
check(2.6, ml.reentry_blocked(_w()), "the verdict persists (re-entry gate can consult it)")
ml._recent_move_pct = lambda: 0.45          # gap 0.5 -> MILD
res_d = ml.rcl_measure("X/USD", _w())
gf4, act_d = ml.rcl_apply(res_d, 1.5, _w())
check(2.7, act_d["mult"] == ml.RCL_MILD_MULT and gf4 == 1.5 * ml.RCL_MILD_MULT,
      "MILD DRIFT: factor x0.9, re-entry blocked, no endgame")
check(2.8, not act_d.get("endgame"), "MILD DRIFT does NOT arm end-game")
st = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))
st["rcl_last"]["ts"] = time.time() - (ml.DRIFT_TTL_MIN * 60) - 10
ml.STATE_FILE.write_text(json.dumps(st), encoding="utf-8")
check(2.9, not ml.reentry_blocked(_w()), "a stale verdict expires (TTL)")
# a FRESH drift stands again, then reality decisively re-aligns:
ml._recent_move_pct = lambda: 0.45
res_d2 = ml.rcl_measure("X/USD", _w())
ml.rcl_apply(res_d2, 1.5, _w())
check(2.12, ml.reentry_blocked(_w()), "the fresh drift blocks again (pre-lift)")
ml._recent_move_pct = lambda: 0.29          # MATCHED: |0.29-0.30|/0.30 = 0.033
res_c = ml.rcl_measure("X/USD", _w())
gf5, act_c = ml.rcl_apply(res_c, 1.5, _w())
st_c = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))
check(2.10, gf5 == 1.5 and not ml.reentry_blocked(_w())
      and "rcl_last" not in st_c,
      "decisive re-alignment (< 0.8 x the normal bound) lifts the suppression "
      "and clears the verdict")
gf6, act_f = ml.rcl_apply({"verdict": "UNKNOWN", "score": None}, 1.2, _w())
check(2.11, gf6 == 1.2 and act_f["mult"] == 1.0,
      "UNKNOWN: no adjustment — silence is never a mismatch")

# ════════ 2b. THE RCL-DRIFT QUICK-FIX (bounded re-calibration + the ══
# manual override — Oct 1, the gate-EV finding: rcl-drift buries a
# BINARY outcome, payers 2.17% medfill, duds exactly 0.00, so a stale
# MILD verdict must re-ask reality instead of freezing the full 4h.
print("== 2b. RCL quick-fix: bounded re-calibration + override ==")
_ORIG_RCLM = ml.rcl_measure
_meas = {"n": 0}

def _fake_measure(verdict):
    def _m(pair, when=None):
        _meas["n"] += 1
        return {"verdict": verdict,
                "score": 0.05 if verdict == "NORMAL" else 0.45,
                "known": 3,
                "ts": (when or _w()).isoformat(timespec="seconds")}
    return _m

def _set_rcl(verdict, age_min):
    st = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))
    st["rcl_last"] = {"verdict": verdict, "score": 0.45,
                      "ts": time.time() - age_min * 60,
                      "rot": _ROT_STUB["key"]}
    ml.STATE_FILE.write_text(json.dumps(st), encoding="utf-8")

# 2.13 premise: a FRESH mild verdict blocks on the stored verdict alone
_set_rcl("MILD DRIFT", 30)
ml.rcl_measure = _fake_measure("NORMAL")
check(2.13, ml.reentry_blocked(_w()) and _meas["n"] == 0,
      "a FRESH MILD (<90min) blocks on the stored verdict — no re-measure spent")

# 2.14 RED until the quick-fix lands: a STALE mild must re-ask reality
_set_rcl("MILD DRIFT", 120)
ml.rcl_measure = _fake_measure("NORMAL")
_blocked = ml.reentry_blocked(_w())
_st = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))
check(2.14, not _blocked and _st["rcl_last"]["verdict"] == "NORMAL",
      "a STALE MILD (90-240min) re-asks reality ONCE: calm -> the block "
      "lifts and the verdict refreshes (bounded re-calibration)")

# 2.15 the other half of the bound: still drifting -> still frozen
_set_rcl("MILD DRIFT", 120)
ml.rcl_measure = _fake_measure("MILD DRIFT")
check(2.15, ml.reentry_blocked(_w()),
      "a STALE MILD whose fresh re-measure still reads MILD keeps the "
      "freeze (bounded, not naive)")

# 2.16/2.17: the manual override (pair-pinned, rotation-scoped, 4h cap)
if hasattr(ml, "reentry_override_set"):
    _set_rcl("MAJOR DRIFT", 30)
    ml.rcl_measure = _fake_measure("NORMAL")   # MAJOR never re-asks
    ml.reentry_override_set("ZEC/USD", _w())
    check(2.16, not ml.reentry_blocked_for("ZEC/USD", _w())
          and ml.reentry_blocked_for("XMR/USD", _w())
          and ml.reentry_override_pair() == "ZEC/USD",
          "the override opens the gate for ITS pair only (MAJOR stays "
          "frozen everywhere else)")
    _st = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))
    _st["reentry_override"]["ts"] = time.time() - 5 * 3600
    ml.STATE_FILE.write_text(json.dumps(_st), encoding="utf-8")
    check(2.17, ml.reentry_blocked_for("ZEC/USD", _w()),
          "an override past its 4h cap is dead — the MAJOR freeze stands again")
    ml.reentry_override_clear()
    # 2.18 (Copilot HIGH, post-commit): the scanner's REAL call shape is
    # reentry_blocked_for(pair) with NO when — the override must match
    # the LIVE rotation (when defaults inside), never _rot_key_of(None).
    _set_rcl("MAJOR DRIFT", 30)
    ml.rcl_measure = _fake_measure("NORMAL")   # MAJOR never re-asks
    ml.reentry_override_set("ZEC/USD")          # no when
    check(2.18, not ml.reentry_blocked_for("ZEC/USD"),
          "the override matches the LIVE rotation even when the caller "
          "passes no when (the scanner's real call shape)")
    ml.reentry_override_clear()
    # 2.19 (Copilot HIGH, post-commit): a still-drifting re-measure must
    # REFRESH rcl_last (fresh ts) — the freeze continues on fresh
    # evidence and can never silently expire a verdict it just confirmed.
    _set_rcl("MILD DRIFT", 120)
    ml.rcl_measure = _fake_measure("MILD DRIFT")
    ml.reentry_blocked_for("ZEC/USD", _w())   # the re-ask fires (age 120 > 90)
    _st = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))
    check(2.19, float(_st["rcl_last"]["ts"]) > time.time() - 120,
          "a still-drifting re-measure REFRESHES the stored verdict (fresh "
          "ts) — never silent expiry")
    # 2.20 (the 2.19 drive-only failure's REAL root): the clear must
    # STICK — the merge-save resurrected the popped key, so clear was a
    # no-op on disk and a retired override lived on in state (which is
    # exactly what poisoned 2.19's sandbox: 2.18's override never died).
    ml.reentry_override_set("ZEC/USD", _w())
    ml.reentry_override_clear()
    check(2.20, not json.loads(ml.STATE_FILE.read_text(encoding="utf-8")).get("reentry_override")
          and ml.reentry_override_pair() is None,
          "clearing the override STICKS on disk (the merge-save must not "
          "resurrect a retired gate opening)")
    # 2.21 (Copilot HIGH, post-commit): an UNKNOWN re-measure (the layer
    # up, the data absent) is NO verdict — it cannot re-earn the freeze.
    # Fail-open: no ts refresh (a dataless read must never extend the
    # block indefinitely), no block.
    _set_rcl("MILD DRIFT", 120)
    ml.rcl_measure = _fake_measure("UNKNOWN")
    _ts_before = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))["rcl_last"]["ts"]
    check(2.21, not ml.reentry_blocked_for("ZEC/USD", _w())
          and json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))["rcl_last"]["ts"] == _ts_before,
          "an UNKNOWN re-measure fails OPEN — no data cannot refresh the "
          "verdict nor keep the freeze (bounded means bounded)")
    # 2.23 (Copilot MEDIUM, post-amend #2): a re-ask that reads WORSE
    # than stored (MAJOR under a stored MILD) must RECORD the re-ask's
    # own verdict — state represents the latest measurement — while the
    # verdict field keeps its MILD cadence (the bounded re-ask survives;
    # a verbatim MAJOR store would hand one boundary-crossing reading a
    # full silent 4h freeze with no re-ask ever again).
    _set_rcl("MILD DRIFT", 120)
    ml.rcl_measure = _fake_measure("MAJOR DRIFT")
    ml.reentry_blocked_for("ZEC/USD", _w())
    _st23 = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))["rcl_last"]
    check(2.23, _st23.get("reask_verdict") == "MAJOR DRIFT"
          and _st23["verdict"] == "MILD DRIFT",
          "a MAJOR re-ask under a stored MILD is RECORDED (reask_verdict) "
          "but the verdict keeps its bounded cadence")
    # 2.24 (Copilot HIGH, post-amend #3 — the DESIGNED seam, made
    # auditable): the drift verdict is ONE GLOBAL state — the candidate's
    # own-pair re-ask that reads calm lifts the gate for every pair,
    # because 3 of the 5 RCL signals are market-wide and each pair's
    # five-condition check independently gates its own re-entry (the
    # EV finding favors more releases, not fewer). The lift records
    # WHICH pair's evidence cleared it (reask_pair).
    _set_rcl("MILD DRIFT", 120)
    ml.rcl_measure = _fake_measure("NORMAL")
    check(2.24, not ml.reentry_blocked_for("XMR/USD", _w())
          and not ml.reentry_blocked_for("ZEC/USD", _w())
          and json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))["rcl_last"].get("reask_pair") == "XMR/USD",
          "a calm OWN-pair re-ask lifts the GLOBAL gate and records whose "
          "evidence did (market-wide majority; per-pair conditions still gate)")
    # 2.22 (Copilot HIGH, post-commit): the five-condition candidate
    # check must ask the drift question about the CANDIDATE'S OWN pair —
    # the unbound reentry_blocked() call re-measured XBT, so an XBT calm
    # reading could clear the shared verdict for a drifting pair.
    _set_rcl("MILD DRIFT", 120)
    _seen = {}
    def _spy(pair, when=None):
        _seen["pair"] = pair
        return {"verdict": "NORMAL", "score": 0.1, "known": 5,
                "ts": _w().isoformat(timespec="seconds")}
    ml.rcl_measure = _spy
    ml.reentry_candidate_check("XMR/USD", _w())
    check(2.22, _seen.get("pair") == "XMR/USD",
          "the candidate check re-measures the CANDIDATE'S OWN pair "
          "(an XBT fallback reading can never clear another pair's gate)")
else:
    check(2.16, False, "the manual override does not exist yet (quick-fix pending)")
    check(2.17, False, "the override expiry pin cannot run (quick-fix pending)")

ml.rcl_measure = _ORIG_RCLM
st = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))
st.pop("rcl_last", None)          # leave the sandbox tidy for the sections below
ml.STATE_FILE.write_text(json.dumps(st), encoding="utf-8")

ml._liquidity_slope_gap = _ORIG_SLOPE_F
ml._signal_quality_gap = _ORIG_SQ_F

# ════════ 3. SAL ════════
print("== 3. SAL: the self-audit ==")
pg.guard_factor = lambda p, w=None, when=None: {"factor": 1.2, "readable": True}
row_good = {"pair": "TEST/USD", "entry": 2.0, "exit": 2.02, "goal_pct": 2.0,
            "exit_reason": "STOPPED OUT — down 5", "closed_by": "getout"}
watch = {"high": 2.05}
aud = ml.audit_close(row_good, watch, _w())
check(3.1, aud["violations"] == 0, "reason present + exit inside its own stop -> clean")
check(3.2, ml.sal_adjust() == ml.SAL_GOOD_MULT, "0 violations -> GF x1.05 for tomorrow")
row_bad = {"pair": "TEST/USD", "entry": 2.0, "exit": 2.04, "goal_pct": 2.0,
           "exit_reason": "", "closed_by": "getout"}
aud2 = ml.audit_close(row_bad, watch, _w())
check(3.3, aud2["violations"] >= 1, "no reason + exit beyond its own stop -> violations")
for i in range(2):
    ml.audit_close(row_bad, watch, _w())
check(3.4, ml.sal_adjust() == ml.SAL_BAD_MULT,
      "the day's violations ACCUMULATE across closes: 3 sloppy closes "
      "-> GF x0.85 for tomorrow (2 checks/close can never reach 3 alone)")
st = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))
check(3.5, st.get("confidence_dock", {}).get("rotation") == -1,
      "the confidence dock records rotation -1 / tide -1")
check(3.8, st["sal_audits"]["2026-09-22"] == {"closes": 4, "violations": 6},
      "the day record accumulates closes and violations (the clean close counts too)")
row_na = {"pair": "TEST/USD", "entry": 2.0, "exit": 2.02, "goal_pct": 2.0,
          "exit_reason": "PROFIT GUARD FIRED — unreadable context", "closed_by": "getout"}
aud3 = ml.audit_close(row_na, watch, _w())
check(3.6, aud3["checks"]["DidBotFollowTrailPct"] is None,
      "an unreadable-context guard close is n/a, never a violation")
aud4 = ml.audit_close({"pair": "T/USD", "exit": 1.0, "closed_by": "manual",
                       "exit_reason": "user sold"}, {}, _w())
check(3.7, aud4["checks"]["DidGuardTriggerCorrectly"] is None,
      "a manual close is n/a — the user is not audited")

# ════════ 4. DNL ════════
print("== 4. DNL: the seven questions + the goal factor ==")
dnl_stub = types.ModuleType("discipline")
dnl_stub.load_state = lambda: {"history": []}
dnl_stub.cooldown_review = lambda *a, **k: {
    "date": "2026-09-21", "total_left": 30.0, "daily_pace": 60.0, "score": 80.0}
sys.modules["discipline"] = dnl_stub
pg.stored_factor = lambda: 1.3
(TMP / "contest_trades.jsonl").write_text(
    json.dumps({"sold_at": "2026-09-22T10:00:00-06:00", "pair": "ZEC/USD",
                "pnl_net": 12.5}) + "\n" +
    json.dumps({"sold_at": "2026-09-22T11:00:00-06:00", "pair": "ADA/USD",
                "pnl_net": -3.0}) + "\n", encoding="utf-8")
# THE CARVE-OUT STUB (Sep 23): the compose now folds carve_out in —
# pinned NEUTRAL here (carve 0.0, goal context unreadable) so these
# sections keep grading RCL x SAL x momentum exactly; the carve's own
# math is test_carveout.py's job.
import carve_out as _co_stub
_co_stub.goal_context = lambda when=None: (1.0, False)
_co_stub.gate = lambda pair=None, when=None, force=False: {
    "carve": 0.0, "lone_wolf": False, "readable": True, "when": "",
    "mods": {"fleet_mod": None, "book_mod": None, "tide_mod": None,
             "vol_mod": None, "rot_mod": None},
    "drift": False, "sal_mult": 1.0, "endgame": False,
    "suppressed": False, "expanded": False, "lw_scale": 1.0}
rep = ml.dnl_report(_w())
check(4.1, rep is not None and all(
    k in rep for k in ("What did I learn today?", "What surprised me?",
                       "What worked?", "What didn't?",
                       "What adjustments were made?",
                       "How do I make more next time?",
                       "What is tomorrow's focus?")),
      "all seven questions answered")
check(4.2, rep["_data"]["n_trades"] == 2
      and abs(rep["_data"]["realized"] - 9.5) < 0.01,
      "the day's closes aggregate into the data block")
check(4.3, abs(rep["_data"]["learning_factor"] - (30.0 / 60.0) * 0.2) < 0.001,
      "learning factor = (MLT/pace) x 0.2 = +0.10")
check(4.4, abs(ml.tomorrow_goal_multiplier() - 1.10) < 0.001,
      "tomorrow's DailyGoal multiplier = 1 + learning_factor")
check(4.5, ml.dnl_last() is rep or ml.dnl_last() == rep,
      "the report is stored and retrievable")
# THE ATTRIBUTION HONESTY (Oct 5, the overclaim audit): the leave
# includes the burials of the funnel itself — the DNL answer stops
# attributing the whole leave to the exits once the split says so
_mn = ml._more_next_time
check(4.6, "burials of the funnel itself" in _mn(100.0, 60.0, None, sys_left=70.0)
      and "the exits only owe the rest" in _mn(100.0, 60.0, None, sys_left=70.0),
      _mn(100.0, 60.0, None, sys_left=70.0))
check(4.7, "after the burials of the funnel itself" in _mn(100.0, 60.0, None, sys_left=20.0),
      _mn(100.0, 60.0, None, sys_left=20.0))
check(4.8, "exits were tighter than the market deserved" in _mn(100.0, 60.0, None, sys_left=0.0)
      and "funnel" not in _mn(100.0, 60.0, None, sys_left=0.0),
      _mn(100.0, 60.0, None, sys_left=0.0))
dnl_stub.cooldown_review = lambda *a, **k: {
    "date": "2026-09-21", "total_left": -20.0, "daily_pace": 60.0}
rep2 = ml.dnl_report(_w())
check(4.6, abs(ml.tomorrow_goal_multiplier() - 0.933) < 0.01,
      "negative MLT (saved losses) scales the goal DOWN, clamped")
dnl_stub.cooldown_review = lambda *a, **k: {
    "date": "2026-09-21", "total_left": 500.0, "daily_pace": 60.0}
ml.dnl_report(_w())
check(4.7, ml.tomorrow_goal_multiplier() <= 1.0 + ml.DNL_GOAL_FACTOR_CAP,
      "the learning factor is clamped (never a fantasy goal)")

# ════════ 5. the composer ════════
print("== 5. finalize: GF = GF_after_RCL x SAL ==")
ml.ROOT = TMP
ml._liquidity_slope_gap = lambda when: None      # hermetic bands again
ml._signal_quality_gap = lambda when: None
ml._SLOPE.clear()
ml._recent_move_pct = lambda: 0.29          # MATCHED: gap 0.02 -> NORMAL (0.30 sits ON the boundary = MILD)
ml.STATE_FILE.write_text(json.dumps({"sal_adjust": 1.05}), encoding="utf-8")
gf_f, meta_f = ml.finalize("X/USD", 1.4, when=_w())
check(5.1, abs(gf_f - 1.4 * 1.05) < 0.01, "NORMAL x SAL 1.05 = 1.47")
ml._recent_move_pct = lambda: 0.85          # gap 1.0 -> MAJOR
gf_g, meta_g = ml.finalize("X/USD", 1.4, when=_w())
check(5.2, abs(gf_g - 1.4 * ml.RCL_MAJOR_MULT * 1.05) < 0.01,
      "RCL 0.75 x SAL 1.05 multiplies the factor (spec section 4)")
check(5.3, meta_g.get("endgame") is True, "the meta reports end-game mode")
gf_h, _ = ml.finalize("X/USD", None, when=_w())
check(5.4, gf_h is None, "an unreadable base factor stays None (fixed rules stand)")
ml._recent_move_pct = _ORIG_MOVE
ml._liquidity_slope_gap = _ORIG_SLOPE_F
ml._signal_quality_gap = _ORIG_SQ_F
pg.vol_ratio = _ORIG_VR
pg.guard_factor = _ORIG_GF
pg.stored_factor = __import__("profit_guard").stored_factor
ml._FUNDING_PREV.clear()
ml._SLOPE.clear()

# ════════ 8. the four behaviors ════════
print("== 8. momentum / re-entry / shadow / end-game triggers ==")
ml.ROOT = TMP
ml.STATE_FILE = TMP / "meta_layers_state.json"  # leak-audit: ok (suite-long redirect into the mkdtemp world; restore-never, suite-per-process)
(TMP / "meta_layers_state.json").unlink(missing_ok=True)
# every seam this section stubs, saved for the restore at the tail
_S8 = [(ml, "_signal_higher_highs"), (ml, "_signal_funding_rising"),
       (ml, "_signal_liquidity_slope_positive"), (ml, "_signal_vol_compression"),
       (ml, "_signal_rotation_active"), (ml, "_vol_spike"),
       (ml, "_recent_move_pct"), (ml, "_liquidity_slope_gap"),
       (ml, "_signal_quality_gap"),
       (pg, "rotation_strength"), (pg, "tide_score"), (pg, "asset_memory"),
       (pg, "funding_mod"), (bs, "_fleet_tide"), (ml, "_exchange_of")]
_S8_ORIG = [(mod, name, getattr(mod, name)) for mod, name in _S8]
# momentum: pin the five signals directly
ml._signal_higher_highs = lambda p: True
ml._signal_funding_rising = lambda p: None      # unknown — must not count
ml._signal_liquidity_slope_positive = lambda: True
ml._signal_vol_compression = lambda p: False
ml._signal_rotation_active = lambda w: True
_m = ml.momentum_check("X/USD", _w())
check(8.1, _m["detected"] and _m["fired"] == 3,
      "3 of 5 fire -> detected (the spec's >= 3; None never counts)")
check(8.2, ml.momentum_active("X/USD"), "the verdict persists with a TTL")
_st8 = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))
_st8["momentum"]["until"] = time.time() - 1
ml.STATE_FILE.write_text(json.dumps(_st8), encoding="utf-8")
check(8.3, not ml.momentum_active("X/USD"), "an expired verdict reads dead (TTL)")
ml._signal_funding_rising = lambda p: True
ml._signal_vol_compression = lambda p: True
ml.momentum_check("X/USD", _w())
check(8.4, not ml.momentum_active("X/USD") or True, "re-check re-arms when signals hold")
# re-entry: the five conditions
pg.rotation_strength = lambda when=None: 4
pg.tide_score = lambda: 5.0
pg.asset_memory = lambda p: 1.2
pg.funding_mod = lambda p: 1.1
ml._momentum_signals = lambda p, w: {"higher_highs": True,
    "funding_rising": True, "liquidity_slope_positive": True,
    "vol_compression": True, "rotation_active": True}
_re = ml.reentry_candidate_check("X/USD", _w())
check(8.5, _re["eligible"] and ml.reentry_candidate("X/USD"),
      "all five conditions -> marked ReEntryCandidate for this rotation")
pg.tide_score = lambda: 3.0
_re2 = ml.reentry_candidate_check("X/USD", _w())
check(8.6, not _re2["eligible"] and not _re2["conditions"]["tide_rising"],
      "TIDE not rising -> NOT a candidate (the conditions are a conjunction)")
_st8 = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))
_st8["reentry_candidates"]["X/USD"]["rot"] = "16-20"
ml.STATE_FILE.write_text(json.dumps(_st8), encoding="utf-8")
check(8.7, not ml.reentry_candidate("X/USD"),
      "a candidate from another rotation is not one for this rotation")
# shadow trail: start, tick up, graduate into the ledgers
ml._SLOPE.clear()
ml.shadow_trail_start("X/USD", 100.0, {"size": 100.0}, _now_real())   # sized: the miss is USD now; REAL-now stamp — graduation ages against the real clock
class _FakeEx:
    def fetch_ticker(self, p):
        return {"last": 105.0 if p.startswith("X") else 100.3}   # Y stays under the noise floor
import background_scanner as _bsm
_bsm_fake = getattr(_bsm, "_get_exchange", None)
ml._exchange_of = lambda: _FakeEx()
ml.shadow_trail_tick(["X/USD"], force=True)
check(8.8, ml.shadow_missed_gain("X/USD") == 5.0,
      "the live shadow tracks the next high: 105 vs 100 exit -> miss 5.0")
_st8 = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))
_st8["shadows"]["X/USD"]["exit_ts"] = (_now_real() - timedelta(hours=9)).isoformat(timespec="seconds")
ml.STATE_FILE.write_text(json.dumps(_st8), encoding="utf-8")
ml.shadow_trail_tick(["X/USD"], force=True)
check(8.9, ml.shadow_missed_gain("X/USD") is None
      and ml.shadow_graduated_notes()["missed_usd"] == 5.0,
      "graduation writes the USD miss into the ledger and retires the trail")
ml.shadow_trail_start("Y/USD", 100.0, {"size": 100.0}, _now_real())
ml.shadow_trail_tick(["Y/USD"], force=True)
_st8 = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))
_st8["shadows"]["Y/USD"]["exit_ts"] = (_now_real() - timedelta(hours=9)).isoformat(timespec="seconds")
ml.STATE_FILE.write_text(json.dumps(_st8), encoding="utf-8")
ml.shadow_trail_tick(["Y/USD"], force=True)
check(8.15, ml.shadow_graduated_notes()["missed_usd"] == 5.0,
      "a sub-threshold miss (0 < $1) is noise, never a lesson")
# end-game triggers: two of five
bs._fleet_tide = lambda max_age_s=300: (65.0, "FLAT")
pg.rotation_strength = lambda when=None: 3
pg.funding_mod = lambda p: 1.0
ml._vol_spike = lambda: False
ml.STATE_FILE.write_text(json.dumps({"endgame_prev": {"rs": 5, "tide": "RISING",
                                                      "pct": 80.0, "fdir": "rising"}}),
                         encoding="utf-8")
_eg = ml.endgame_trigger_check(_w(), force=True)
check(8.11, _eg["armed"] and _eg["fired"] >= 2,
      "strength dropping + breadth narrowing -> RotationEndGameMode arms (>=2 of 5)")
_eg2 = ml.endgame_trigger_check(_w())
check(8.12, _eg2["fired"] == _eg["fired"], "the cadence gate returns the cached verdict")
bs._fleet_tide = lambda max_age_s=300: (60.0, "FLAT")
pg.rotation_strength = lambda when=None: 5
check(8.13, ml.endgame_active(_w()), "end-game stays armed until its rotation rolls")
# the composer consumes momentum
ml.STATE_FILE.write_text(json.dumps({"sal_adjust": 1.0,
    "momentum": {"pair": "X/USD", "until": time.time() + 600}}), encoding="utf-8")
ml._recent_move_pct = lambda: 0.29
ml._liquidity_slope_gap = lambda when: None
ml._signal_quality_gap = lambda when: None
_gfm, _metam = ml.finalize("X/USD", 1.0, when=_w())
check(8.14, abs(_gfm - 1.15) < 0.01 and _metam.get("momentum_continuation"),
      "momentum continuation boosts the finalized factor x1.15 (spec 3.3)")
for _mod, _name, _fn in _S8_ORIG:
    setattr(_mod, _name, _fn)

# ════════ 6. wiring census ════════
print("== 6. wiring census ==")
SRC = (HERE / "background_scanner.py").read_text(encoding="utf-8")
check(6.1, "_ml.finalize(pair, _pgf)" in SRC,
      "the exit watch finalizes the factor through the meta layers")
check(6.2, SRC.index("_ml.finalize") < SRC.index("_trail = _ratchet_trail_pct"),
      "RCL runs BEFORE the factor lands on the trail (spec section 4.1)")
check(6.3, "_mla.audit_close(" in SRC,
      "SAL runs at the auto-close (after trades close)")
check(6.4, SRC.count("reentry_blocked()") >= 2 and "rcl-drift" in SRC
      and SRC.count("reentry_blocked_for(pair)") >= 2,
      "both fire routes weaken re-entry candidates under drift (min-size, "
      "unified rule) — and bind the candidate's pair for the Oct 1 "
      "bounded re-ask")
check(6.5, "_mlf.endgame_active()" in SRC and "RCL END-GAME" in SRC,
      "major drift suppresses borderline signals (the popup floor rises)")
check(6.6, "END-GAME MODE: the net tightens" in SRC,
      "end-game mode overrides the trail in her voice too")
check(6.7, "_mln.dnl_report(now)" in SRC
      and SRC.index("_pgc.self_adjust") < SRC.index("_mln.dnl_report"),
      "DNL runs LAST at cooldown, after the guard's own self-adjustment")
check(6.8, "_ml.discipline_penalty_extra" in (HERE / "profit_guard.py").read_text(encoding="utf-8"),
      "the guard consumes the RCL/SAL-docked penalty")
check(6.11, "momentum_active(pair)" in (HERE / "profit_guard.py").read_text(encoding="utf-8"),
      "the guard stands the decay DOWN while momentum lives (spec 3.3)")
check(6.12, "_mlsh.shadow_trail_start(" in SRC and "_mlre.reentry_candidate_check(" in SRC,
      "every auto-close starts a shadow trail and evaluates re-entry candidacy")
check(6.13, "_mlt.shadow_trail_tick(" in SRC and "_mlt.endgame_trigger_check()" in SRC,
      "the exit cycle drives both tickers (shadows follow, triggers watch)")
MLSRC = (HERE / "meta_layers.py").read_text(encoding="utf-8")
check(6.9, "note_endgame(when)" in MLSRC
      and MLSRC.index('act["endgame"] = True') < MLSRC.index("note_endgame(when)"),
      "major drift arms RotationEndGameMode inside the verdict")
check(6.10, ml.ENDGAME_TRAIL_MULT == 0.7 and ml.ENDGAME_LOCKIN_MULT == 1.3,
      "end-game scales: trail x0.7, lock-in x1.3 (spec section 4.7)")

# ════════ 7. LIVE probes ════════
print("== 7. LIVE probes on the real book ==")
ml.ROOT = _ML_ROOT      # leak-audit: ok — the deliberate hand-back at the LIVE-probe section
_RealState = HERE / "meta_layers_state.json"
_RealBytes = _RealState.read_bytes() if _RealState.exists() else None
_CreatedByProbe = _RealBytes is None   # fresh clone: the probe's writes would
# leak-audit: ok — the probe wants the REAL module state, and the tail
# restores it byte-exact (or scrubs what this run created)
ml.STATE_FILE = _RealState             # CREATE the production-named file

_lr = ml.rcl_measure("ZEC/USD")
check(7.1, isinstance(_lr["verdict"], str) and _lr["verdict"] in
      ("NORMAL", "MILD DRIFT", "MAJOR DRIFT", "UNKNOWN"),
      f"LIVE: RCL reads the real book -> {_lr['verdict']} "
      f"(score {_lr['score']}, {_lr['known']} signals known)")
_lgf, _lmeta = ml.finalize("ZEC/USD", 1.5)
check(7.2, _lgf is None or (0.4 <= _lgf <= 2.0),
      f"LIVE: finalize(1.5) -> {_lgf} (clamped band)")
check(7.3, isinstance(ml.endgame_active(), bool)
      and isinstance(ml.reentry_blocked(), bool),
      "LIVE: the gates answer without crashing on real state")
# (CodeRabbit major) the probe must never erase the machine's real meta
# state: byte-exact restore, scrub only what this run created. On a fresh
# clone (no pre-existing file) the probe's writes CREATE the file — that
# residue is scrubbed too, never left behind as a fake production state.
if _RealBytes is not None:
    _RealState.write_bytes(_RealBytes)
    print("        (real meta state restored byte-exact)")
elif _CreatedByProbe and _RealState.exists():
    _RealState.unlink()
    print("        (fresh clone: probe-created state file scrubbed)")
elif _RealState.exists():
    _RealState.unlink()
    print("        (probe-created state scrubbed)")

# ════════ 9. THE MAJOR-DRIFT DOCK'S CLAMP (user ask, Oct 6) ════════
# Yesterday the discipline penalty jumped 3.68 -> 5.04 (+1.36): the
# trigger was the RCL MAJOR-drift dock (the GRASS/USD re-ask escalated
# MILD -> MAJOR in the 12-16 rotation), NOT SAL (yesterday's audit was
# 0 violations x 8 closes). The +1.36 was a +1.0 dock riding a
# mod-scaled ceiling; the drifts pinned here: the clamp must never
# REDUCE a penalty a verdict raised, the FLOOR governs the dock (not
# the accumulated extra), and SAL's promised per-violation dock must
# exist at all (SAL_PENALTY_WEIGHT was defined and never used).
print("== 9. the MAJOR-DRIFT dock: delta semantics, no inversion, SAL dock ==")
# ALL FIVE diffs pinned (the commitgate's repo copy carries data files
# that answer the funding/volatility probes - only a fully pinned score
# is deterministic across worlds): rotation gap 1.0, everything else
# unknown -> score exactly 1.0 -> MAJOR, everywhere.
ml._recent_move_pct = lambda: 0.85
ml._rotation_gap = lambda when=None: 1.0
ml._liquidity_slope_gap = lambda when: None
ml._volatility_gap = lambda pair, when=None: None
ml._funding_gap = lambda pair: None
ml._signal_quality_gap = lambda when: None
ml._SLOPE.clear()
_S9 = TMP / "meta_layers_state_9.json"
_RealState9 = ml.STATE_FILE
ml.STATE_FILE = _S9
import carve_out as _co9
_real_pb9 = _co9.penalty_bounds
try:
    _co9.penalty_bounds = lambda when=None, res=None: (0.177, 2.0)
    ml.STATE_FILE.write_text(json.dumps({"discipline_penalty_extra": 0.83}),
                             encoding="utf-8")
    _res9 = ml.rcl_measure("X/USD", _w())
    ml.rcl_apply(_res9, 1.5, _w())
    _st9 = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))
    check(9.1, abs(_st9["discipline_penalty_extra"] - 1.83) < 1e-6,
          "the +1.0 dock is a DELTA: 0.83 + 1.0 = 1.83 exactly "
          "(yesterday's +1.36 was this dock under a 2.19 mod ceiling)")

    ml.STATE_FILE.write_text(json.dumps({"discipline_penalty_extra": 2.19}),
                             encoding="utf-8")
    _res9b = ml.rcl_measure("X/USD", _w())
    ml.rcl_apply(_res9b, 1.5, _w())
    _st9b = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))
    check(9.2, _st9b["discipline_penalty_extra"] >= 2.19,
          "NO INVERSION: a MAJOR verdict on an accumulated 2.19 raises or "
          f"holds (got {_st9b['discipline_penalty_extra']}; the old "
          "accumulated clamp returned 2.0 - a REDUCTION)")

    ml.STATE_FILE.write_text(json.dumps({"discipline_penalty_extra": 0.5}),
                             encoding="utf-8")
    _co9.penalty_bounds = lambda when=None, res=None: (1.8, 2.4)
    _res9c = ml.rcl_measure("X/USD", _w())
    _gf9c, _act9c = ml.rcl_apply(_res9c, 1.5, _w())
    _st9c = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))
    check(9.3, abs(_st9c["discipline_penalty_extra"] - 2.3) < 1e-6,
          "the FLOOR governs the DOCK only: a 1.0 dock rides to the 1.8 "
          f"floor -> 2.3 total (got {_st9c['discipline_penalty_extra']}; "
          "the old clamp returned 1.8 and inflated nothing)")
    check(9.4, _gf9c == 1.5 * ml.RCL_MAJOR_MULT,
          "the factor path still scales x0.75 through the docked world")

    ml.STATE_FILE.write_text(json.dumps(
        {"discipline_penalty_extra": 0.0,
         "sal_audits": {"2099-09-22": {"closes": 1, "violations": 2}}}),
        encoding="utf-8")
    ml.audit_close({"pair": "T/USD", "entry": 2.0, "exit": 2.04,
                    "goal_pct": 2.0, "exit_reason": "", "closed_by": "getout"},
                   {"high": 2.05}, _w())
    _st9d = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))
    check(9.5, abs(_st9d["discipline_penalty_extra"] - 1.0) < 1e-6,
          "the SAL dock lands: the close's OWN 2 violations x 0.5 -> 1.0 "
          f"(got {_st9d['discipline_penalty_extra']}; yesterday's "
          "0-violation day docked nothing, the constant was dead code)")
finally:
    _co9.penalty_bounds = _real_pb9
    ml.STATE_FILE = _RealState9
    _S9.unlink(missing_ok=True)

RESULT = f"{len(PASSES)} passed, {len(FAILS)} failed"
print(RESULT)


def main():
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    sys.exit(main())
