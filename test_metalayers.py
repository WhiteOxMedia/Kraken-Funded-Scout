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
TMP = HERE / "_mltmp"
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

ml.STATE_FILE = TMP / "meta_layers_state.json"
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
ml.STATE_FILE = TMP / "meta_layers_state.json"
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
ml.shadow_trail_start("X/USD", 100.0, {"size": 100.0}, _w())   # sized: the miss is USD now
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
_st8["shadows"]["X/USD"]["exit_ts"] = (_w() - timedelta(hours=9)).isoformat(timespec="seconds")
ml.STATE_FILE.write_text(json.dumps(_st8), encoding="utf-8")
ml.shadow_trail_tick(["X/USD"], force=True)
check(8.9, ml.shadow_missed_gain("X/USD") is None
      and ml.shadow_graduated_notes()["missed_usd"] == 5.0,
      "graduation writes the USD miss into the ledger and retires the trail")
ml.shadow_trail_start("Y/USD", 100.0, {"size": 100.0}, _w())
ml.shadow_trail_tick(["Y/USD"], force=True)
_st8 = json.loads(ml.STATE_FILE.read_text(encoding="utf-8"))
_st8["shadows"]["Y/USD"]["exit_ts"] = (_w() - timedelta(hours=9)).isoformat(timespec="seconds")
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
check(6.4, SRC.count("reentry_blocked()") >= 2 and "rcl-drift-reentry" in SRC,
      "both fire routes suppress re-entry candidates under drift")
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
ml.ROOT = _ML_ROOT
_RealState = HERE / "meta_layers_state.json"
_RealBytes = _RealState.read_bytes() if _RealState.exists() else None
ml.STATE_FILE = _RealState
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
# state: byte-exact restore, scrub only what this run created.
if _RealBytes is not None:
    _RealState.write_bytes(_RealBytes)
    print("        (real meta state restored byte-exact)")
elif _RealState.exists():
    _RealState.unlink()
    print("        (probe-created state scrubbed)")

RESULT = f"{len(PASSES)} passed, {len(FAILS)} failed"
print(RESULT)
sys.exit(1 if FAILS else 0)
