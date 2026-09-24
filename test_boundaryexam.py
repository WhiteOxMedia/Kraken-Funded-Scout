"""Test THE BOUNDARY CROSS-EXAMINATION + CORRECTION MODULE (user spec,
Sep 23, sections 3-5) — the boundary system audits itself every 6
rotations, hands WARN/FAIL to the correction overlays, and the
cooldown tick drives the schedule.

Graded here:
  1. wiring — the tick rides the cooldown chain after the carve-out's
     night update; the DNL night record carries BoundaryCorrections
  2. the trigger threshold — fewer than 6 readable nights never
     produces a verdict (evidence, not absence)
  3. the PASS path — consistent bands on a mixed window PASS, and a
     neutral window passes VACUOUSLY (a question whose context never
     arose is not a warning)
  4. the FAIL path — out-of-band nights fail their families, the
     verdict FAILs at >=2 failed families, the correction module
     installs the right expiring overlays
  5. the overlay machinery — unknown keys rejected, mults clamped,
     context gates (strong/weak), expiry by date, PASS clears
  6. the scheduler — first exam at 6, +3 after each, restart-proof
     (ledger-derived, no counter), fail-neutral on dark state
  7. fail-neutral — an unreadable meta state yields None, never a
     verdict from nothing

Hermetic: carve_out/meta_layers state redirected to a temp dir; the
discipline verdict read is stubbed to empty (no live verdict history
is touched). Run: .venv\\Scripts\\python.exe -X utf8 test_boundaryexam.py
"""
import json
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
TMP = Path(tempfile.mkdtemp(prefix="_bxtest_"))

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))

import carve_out as co
co.STATE_FILE = TMP / "carve_out.json"
import meta_layers as ml
ml.ROOT = TMP
ml.STATE_FILE = TMP / "meta_layers_state.json"

# the exam's decay family reads discipline's verdict history — the
# drive stubs it empty (no live verdict store is touched)
import discipline as _disc
_disc.verdict_history = lambda days=14: []

NOW = co._now_mst()


def _src(name):
    return (BASE / name).read_text(encoding="utf-8").replace("\r\n", "\n")


co_src = _src("carve_out.py")
bs_src = _src("background_scanner.py")
ml_src = _src("meta_layers.py")


def _reset():
    (TMP / "carve_out.json").write_text("{}", encoding="utf-8")
    (TMP / "meta_layers_state.json").write_text("{}", encoding="utf-8")


def _band(rm, tm, vm, fm, bm, bad=False):
    """A night's clamp_bounds exactly as the six helpers would compute
    them from the context (bad=True seeds out-of-band values)."""
    if bad:
        return {"max_carve_out": 0.15,          # 0.10 expected -> oob
                "gf_bounds": [0.4 * tm * bm, 1.0],   # hi < 2*rm*vm -> oob
                "hatch_bounds": [3.0, 8.0 * rm * vm],  # lo > 2*tm -> oob
                "decay_bounds": [0.7 * tm, 1.2],       # hi > 1.0*rm -> oob
                "penalty_bounds": [0.6, 2.0 * rm * vm * fm],  # lo oob
                "dailygoal_bounds": [80.0, 120.0],
                "readable": True,
                "context": {"fleet_mod": fm, "book_mod": bm,
                            "tide_mod": tm, "vol_mod": vm, "rot_mod": rm}}
    return {"max_carve_out": round(0.10 * rm * vm * tm, 4),
            "gf_bounds": [round(0.40 * tm * bm, 3),
                          round(2.00 * rm * vm, 3)],
            "hatch_bounds": [round(2.0 * tm, 3), round(8.0 * rm * vm, 3)],
            "decay_bounds": [round(0.7 * tm, 3), round(1.0 * rm, 3)],
            "penalty_bounds": [round(0.50 * tm * bm, 3),
                               round(2.00 * rm * vm * fm, 3)],
            "dailygoal_bounds": [80.0, 120.0],
            "readable": True,
            "context": {"fleet_mod": fm, "book_mod": bm,
                        "tide_mod": tm, "vol_mod": vm, "rot_mod": rm}}


def _mods(rm, tm, vm, fm, bm):
    """A mods dict in compute_carve_out's inject form — the five RAW
    modifier values (unreadable = the key is absent or None; the guard's
    clamp never runs on injected values)."""
    return {"fleet": fm, "book": bm, "tide": tm, "vol": vm, "rot": rm}


CTX_NEUTRAL = _mods(1.0, 1.0, 1.0, 1.0, 1.0)
CTX_MIXED = [_mods(1.0, 1.0, 1.0, 1.0, 1.0), _mods(1.3, 0.8, 1.2, 1.2, 1.2),
             _mods(0.8, 0.7, 0.9, 0.8, 1.0), _mods(1.05, 1.1, 0.9, 1.0, 0.9),
             _mods(0.95, 0.9, 1.05, 1.1, 1.0), _mods(1.1, 1.05, 0.95, 0.9, 0.85)]


def _ctx(over):
    """Inject form carrying ONE override (the rest neutral)."""
    return _mods(over.get("rm", 1.0), over.get("tm", 1.0),
                 over.get("vm", 1.0), over.get("fm", 1.0),
                 over.get("bm", 1.0))


def _seed(ctxs, bad=False, pace=100.0, realized=80.0, reentry=1, endgame=1,
          n_trades=3, start=None):
    dnl = {}
    d0 = start or (NOW - timedelta(days=len(ctxs) + 1))
    for i, ctx in enumerate(ctxs):
        date = (d0 + timedelta(days=i)).strftime("%Y-%m-%d")
        rm = ctx.get("rot", 1.0)
        tm = ctx.get("tide", 1.0)
        vm = ctx.get("vol", 1.0)
        fm = ctx.get("fleet", 1.0)
        bm = ctx.get("book", 1.0)
        dnl[date] = {"_data": {
            "clamp_bounds": _band(rm, tm, vm, fm, bm, bad=bad),
            "daily_pace": pace, "realized": realized,
            "n_trades": n_trades,
            "reentry_blocks": reentry, "endgame_events": endgame}}
    ml._save({"dnl": dnl})


# ═══ 1. wiring ═══════════════════════════════════════════════════════
check("1.1 the tick rides the cooldown chain AFTER the night update",
      "_co_x.cross_examine" in bs_src
      and bs_src.index("_co_n.night_update") < bs_src.index(
          "_co_x.cross_examine"))
check("1.2 the tick logs the verdict and the per-family evidence",
      "BOUNDARY CROSS-EXAM" in bs_src and "_exam.get(\"families\")" in bs_src)
check("1.3 WARN/FAIL feeds the correction module in the tick",
      "_co_x.cross_examine_corrections(_exam" in bs_src)
check("1.4 the tick reschedules every cooldown (+3 after an exam)",
      "_co_x._schedule_exam(now)" in bs_src
      and "_EXAM_RESCHEDULE" in co_src)
check("1.5 the DNL night record carries BoundaryCorrections",
      '"BoundaryCorrections"' in ml_src
      and "_co_c.boundary_corrections()" in ml_src)
check("1.6 the corrections record lands OUTSIDE the carve-record gate",
      ml_src.index('"BoundaryCorrections"')
      > ml_src.index('report["_data"]["clamp_bounds"] = {"readable": False}'))
check("1.7 the overlay safety rule is structural (mults clamped)",
      "max(_OVERLAY_MIN, min(_OVERLAY_MAX" in co_src
      and "_OVERLAY_KEYS" in co_src)

# ═══ 2. the trigger threshold ════════════════════════════════════════
_reset()
_seed(CTX_MIXED[:5])
check("2.1 fewer than 6 readable nights -> no verdict (None)",
      co.cross_examine(NOW) is None)
_seed(CTX_MIXED)
ex0 = co.cross_examine(NOW)
check("2.2 six readable nights -> the exam runs",
      ex0 is not None and ex0.get("rotations") == 6, ex0)

# ═══ 3. the PASS path ════════════════════════════════════════════════
check("3.1 consistent bands on a mixed context window PASS",
      ex0.get("FinalVerdict") == "PASS", ex0.get("fail_families"))
check("3.2 the PASS shape answers the eight families",
      len(ex0.get("families") or {}) == 8 and ex0.get("Summary")
      and ex0.get("BoundaryBehavior") and ex0.get("ClampBehavior")
      and ex0.get("ContinuationBehavior") and ex0.get("ReEntryBehavior")
      and ex0.get("DNLBehavior") and ex0.get("FinalVerdict") == "PASS")
corr_pass = co.cross_examine_corrections(ex0, NOW)
check("3.3 PASS installs no corrections",
      corr_pass.get("applied") == [] and not corr_pass.get("cleared"))
# the vacuous regression: a fully neutral window (no strong rotation,
# no weak conditions, NO TRADES AT ALL) must PASS, not warn
_reset()
_seed([CTX_NEUTRAL] * 6, realized=0.0, reentry=0, endgame=0, pace=100.0,
      n_trades=0)
exn = co.cross_examine(NOW)
check("3.4 a neutral window passes vacuously (no spurious WARN)",
      exn is not None and exn.get("FinalVerdict") == "PASS",
      (exn or {}).get("warn_families"))
check("3.5 the vacuous answers say so honestly",
      "never arose" in str((exn or {}).get("families", {})
                           .get("carve_out", {}).get("evidence")))

# ═══ 4. the FAIL path ════════════════════════════════════════════════
_reset()
_seed([CTX_NEUTRAL] * 6, bad=True, realized=10.0)
exb = co.cross_examine(NOW)
check("4.1 out-of-band nights FAIL their families",
      exb.get("FinalVerdict") == "FAIL", exb.get("fail_families"))
check("4.2 the failed families are the out-of-band ones + the missed pace",
      set(exb.get("fail_families") or []) == {
          "carve_out", "guard_factor", "hatch", "decay", "penalty",
          "daily_goal"},
      exb.get("fail_families"))
ff = exb.get("families") or {}
check("4.3 the daily-goal family catches the unrealistic pace",
      "daily_goal" in (exb.get("warn_families") or [])
      or ff.get("daily_goal", {}).get("ok") is False, ff.get("daily_goal"))
_corr = co.cross_examine_corrections(exb, NOW)
check("4.4 FAIL installs the overlays",
      set(_corr.get("applied") or []) >= {
          "max_carve_out", "gf_min", "gf_max", "hatch_min", "hatch_max",
          "decay_min", "decay_max", "penalty_min", "penalty_max"},
      _corr.get("applied"))
check("4.5 the overlays expire after 3 rotations",
      _corr.get("expires") == (NOW + timedelta(days=3))
      .strftime("%Y-%m-%d"), _corr)
_rec = co.boundary_corrections()
check("4.6 the record is stored in the carve-out state",
      _rec.get("set_date") == NOW.strftime("%Y-%m-%d")
      and _rec.get("overlays", {}).get("max_carve_out", {})
      .get("mult_strong") == 1.05, _rec)

# ═══ 5. the overlay machinery ════════════════════════════════════════
# context-gated application: strong rotation widens the carve ceiling
res_strong = co.compute_carve_out(
    when=NOW, mods=_ctx({"rm": 1.3}))
mc = co.max_carve_out(res=res_strong, when=NOW)
check("5.1 the strong gate applies (MaxCarveOut x1.05 in strength)",
      abs(mc - round(0.10 * 1.3 * 1.05, 4)) < 1e-6, mc)
res_weak = co.compute_carve_out(
    when=NOW, mods=_ctx({"bm": 1.2}))   # a HEAVY book (raw inject — no
                                        # clamp): the weak gate is
                                        # tide-out OR book > 1.1
glo, ghi = co.gf_bounds(res=res_weak, when=NOW)
check("5.2 the weak gate applies (MinGF x1.10 on a heavy book)",
      abs(glo - round(0.40 * 1.0 * 1.2 * 1.10, 3)) < 1e-6 and
      abs(ghi - 2.0) < 1e-6, (glo, ghi))
res_neutral = co.compute_carve_out(
    when=NOW, mods=CTX_NEUTRAL)
mc_n = co.max_carve_out(res=res_neutral, when=NOW)
check("5.3 an ungated context reads the band pure (mult 1.0)",
      abs(mc_n - 0.10) < 1e-6, mc_n)
# unknown keys rejected, mults clamped
rec = co.set_boundary_corrections(
    {"bogus_key": {"mult_always": 1.5},
     "max_carve_out": {"mult_always": 99.0, "mult_weak": 0.1}}, 3, NOW)
check("5.4 unknown keys are rejected",
      "bogus_key" not in (rec.get("overlays") or {}), rec)
check("5.5 mults clamp to [0.5, 2.0]",
      rec["overlays"]["max_carve_out"]["mult_always"] == 2.0
      and rec["overlays"]["max_carve_out"]["mult_weak"] == 0.5, rec)
# expiry by date
co.set_boundary_corrections({"max_carve_out": {"mult_always": 1.5}},
                            3, NOW - timedelta(days=5))
mc_exp = co.max_carve_out(res=res_strong, when=NOW)
check("5.6 an expired record reads 1.0 (band returns to pure context)",
      abs(mc_exp - round(0.10 * 1.3, 4)) < 1e-6, mc_exp)
# PASS clears a standing correction
_reset()
co.set_boundary_corrections({"gf_max": {"mult_strong": 1.10}}, 3, NOW)
had = bool(co.boundary_corrections())
co.cross_examine_corrections({"FinalVerdict": "PASS"}, NOW)
check("5.7 PASS clears the standing corrections",
      had and co.boundary_corrections() == {})

# ═══ 6. the scheduler ════════════════════════════════════════════════
_reset()
check("6.1 a fresh state schedules the first exam at 6",
      co._next_exam_due() == 6)
_seed(CTX_MIXED[:5])                       # 5 readable nights
co._schedule_exam(NOW)
check("6.2 below the due mark the schedule holds",
      co._next_exam_due() == 6)
_seed(CTX_MIXED)                           # 6 readable nights
co._schedule_exam(NOW)
check("6.3 after an exam the next is nights + 3",
      co._next_exam_due() == 9)
co._schedule_exam(NOW)
check("6.4 the schedule does not compound without a new exam window",
      co._next_exam_due() == 9, co._next_exam_due())
check("6.5 the count derives from the ledger (restart-proof)",
      co._readable_nights() == 6)
_reset()
check("6.6 a dark ledger reads zero nights (fail-neutral)",
      co._readable_nights() == 0 and co._next_exam_due() == 6)

# ═══ 7. fail-neutral ═════════════════════════════════════════════════
_reset()
_orig_ml_state = ml._state
def _boom(*a, **k):
    raise RuntimeError("dark")
ml._state = _boom
try:
    exd = co.cross_examine(NOW)
finally:
    ml._state = _orig_ml_state
check("7.1 an unreadable meta state yields None, never a verdict",
      exd is None)
mc_dark = co.max_carve_out(res=res_strong, when=NOW)
check("7.2 the bounds helpers survive a dark corrections record",
      isinstance(mc_dark, float) and mc_dark > 0)

fails = [r for r in results if not r[1]]
(BASE / "_boundaryexam_check.txt").write_text(
    "\n".join(f"{'PASS' if ok else 'FAIL'}  {name}"
              + (f"  [{d}]" if d and not ok else "")
              for name, ok, d in results)
    + f"\n\nRESULT: {len(results) - len(fails)} passed, "
      f"{len(fails)} failed\n", encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
