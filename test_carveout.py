"""Test THE ADAPTIVE CARVE-OUT (user spec, Sep 23) — the fixed +5%
lone-wolf carve-out, the fixed decay slope, the fixed penalty weight,
and the fixed daily-goal baseline all become context engines.

Graded here:
  1. wiring — every consumer imports carve_out, every fail-open falls
     back to the old constants (a broken module never widens the risk)
  2. the five-mod math — the spec's worked example (5% x 0.8 x 0.9 x
     0.6 = ~2%), suppression, expansion, drift, SAL, clamps
  3. the entry hatch — required_own_move floors 2 / ceils 8, and the
     permission gate kills lone-wolf mode in a dead tide
  4. the context engines — decay slower/faster, penalty lighter/heavier,
     goal higher/lower, each oriented per the spec's interpretation
  5. GuardFactor integration — GF x (1 + CarveOut) at the finalize,
     AFTER RCL and SAL, with the None-pass-through intact
  6. the RCL kill — a standing drift verdict voids the re-entry candidate
  7. the DNL record — night_update freezes the five mods and rides the
     narrative data; goal_context composes into tomorrow's multiplier

Run: .venv\\Scripts\\python.exe -X utf8 test_carveout.py
"""
import sys
import math
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))

import carve_out as co
import profit_guard as pg
import meta_layers as ml
import scratch_space   # scratch policy: nothing volatile lands in the repo root

def _src(name):
    return (BASE / name).read_text(encoding="utf-8").replace("\r\n", "\n")

bs_src = _src("background_scanner.py")
ml_src = _src("meta_layers.py")
pg_src = _src("profit_guard.py")
co_src = _src("carve_out.py")
ap_src = _src("app.py")

# ═══ 1. wiring ═══════════════════════════════════════════════════════
check("1.1 carve_out.py exists with the five mods",
      all(t in co_src for t in ("def fleet_mod", "def book_mod",
                                "def tide_mod", "def vol_mod",
                                "def rotation_mod")))
check("1.2 the discount reads the adaptive gate (scanner)",
      "import carve_out as _co" in bs_src
      and "required_own_move" in bs_src
      and "lw_scale" in bs_src)
check("1.3 the discount FAILS OPEN to the old tiers",
      "req, _lw_scale, _mode = LONE_WOLF_MOVE_MIN, 1.0, False" in bs_src)
check("1.4 the movers hatch uses the adaptive requirement AND the mode",
      "_m_req" in bs_src and "_m_mode" in bs_src
      and "LONE_WOLF_MOVE_MIN, False" in bs_src)
check("1.5 decay_mod takes the context (guard)",
      "def decay_mod(w: dict | None = None,\n              context:" in pg_src
      and "decay_context" in pg_src)
check("1.6 the penalty weight scales (guard)",
      "penalty_context" in pg_src and "_extra *= _w" in pg_src)
check("1.7 the goal composes the context (guard)",
      "goal_context" in pg_src and "pace *= _gm" in pg_src)
check("1.8 DNL's multiplier composes the goal context (meta)",
      "_goal_mult if _gr else 1.0" in ml_src)
check("1.9 the finalize applies the carve AFTER RCL/SAL (meta)",
      "apply_to_guard_factor" in ml_src
      and ml_src.index("rcl_apply") < ml_src.index("apply_to_guard_factor")
      and ml_src.index("sal_adjust") < ml_src.index("apply_to_guard_factor"))
check("1.10 the re-entry check kills on drift (meta)",
      "RCL drift stands" in ml_src and "ReEntryCandidate = FALSE" in ml_src)
check("1.11 the night update runs after DNL (scanner)",
      bs_src.index("_mln.dnl_report") < bs_src.index("_co_n.night_update"))
check("1.12 the suppression texts name the ADAPTIVE requirement",
      bs_src.count("required_own_move") >= 2
      and "lone wolf needs >= +{LONE_WOLF_MOVE_MIN:.0f}% " not in bs_src)
check("1.13 the DNL data carries the carve record (meta)",
      '"carve_out"' in ml_src and "carve_mods" in ml_src)

# ═══ 2. the five-mod math ════════════════════════════════════════════
NOW = co._now_mst()

def _carve(mods, drift=False, sal=1.0):
    return co.compute_carve_out(when=NOW, drift_detected=drift,
                                sal_mult=sal, mods=mods)

# the spec's WORKED EXAMPLE (Section 7): 5% x 0.8 x 0.9 x 0.6
r = _carve({"fleet": 0.8, "book": 0.9, "tide": 0.6, "vol": 1.0, "rot": 1.0})
check("2.1 the spec example: the product then the suppression x0.5",
      abs(r["carve"] - (0.05 * 0.8 * 0.9 * 0.6 * 0.5)) < 1e-9, r["carve"])
check("2.2 the spec example reads ~2% at the hatch (the ADAPTIVE floor: MinHatch = 2 x TideMod = 1.2 in this dead tide)",
      abs(co.required_own_move(r) - 1.2) < 1e-9, co.required_own_move(r))
check("2.2b the adaptive floor reproduces the static 2% at neutral mods",
      co.hatch_bounds(res={"readable": True, "mods": {"fleet_mod": 1.0,
          "book_mod": 1.0, "tide_mod": 1.0, "vol_mod": 1.0,
          "rot_mod": 1.0}}) == (2.0, 8.0), co.hatch_bounds())
check("2.3 a dead tide NEVER grants lone-wolf mode",
      r["lone_wolf"] is False and r["suppressed"] is True)
NEUTRAL = {"fleet": None, "book": None, "tide": 1.0, "vol": None, "rot": 1.0}
r2 = _carve(NEUTRAL)
check("2.4 neutral mods -> the base allowance, mode ON, scale 1.0",
      abs(r2["carve"] - 0.05) < 1e-9 and r2["lone_wolf"] is True
      and abs(r2["lw_scale"] - 1.0) < 1e-9, r2)
r3 = _carve(NEUTRAL, drift=True)
check("2.5 drift tightens (x0.8) and kills the mode",
      r3["lone_wolf"] is False and r3["drift"] is True
      and r3["carve"] < r2["carve"])
r4 = _carve(NEUTRAL, sal=0.85)
check("2.6 SAL violations tighten (x0.85)",
      abs(r4["carve"] - (0.05 * 0.85)) < 1e-9, r4["carve"])
r5 = _carve(NEUTRAL, sal=1.05)
check("2.7 a clean book widens (x1.05)",
      abs(r5["carve"] - (0.05 * 1.05)) < 1e-9, r5["carve"])
r6 = _carve({"fleet": 1.2, "book": 0.7, "tide": 1.2, "vol": 1.2, "rot": 1.2})
check("2.8 strong everything + a light book EXPANDS (x1.2)",
      r6["expanded"] is True and r6["suppressed"] is False
      and abs(r6["carve"] - 0.05 * 1.2 * 0.7 * 1.2 * 1.2 * 1.2 * 1.2) < 2e-4,
      r6["carve"])
r7 = _carve({"fleet": 2.0, "book": 0.5, "tide": 1.5, "vol": 2.0, "rot": 1.5})
check("2.9 the allowance never exceeds its ADAPTIVE ceiling",
      r7["carve"] <= r7["max_carve"] + 1e-9
      and abs(r7["max_carve"] - 0.10 * 1.5 * 2.0 * 1.5) < 1e-9,
      (r7["carve"], r7["max_carve"]))
check("2.9b the ceiling breathes past the old static 10% only in a strong regime",
      r7["max_carve"] > 0.10, r7["max_carve"])
check("2.9c the ceiling fails OPEN to the static 10% on a dark context",
      abs(co.max_carve_out(res={"readable": False, "mods": {
          "fleet_mod": None, "book_mod": None, "tide_mod": None,
          "vol_mod": None, "rot_mod": None}}) - 0.10) < 1e-9,
      co.max_carve_out(res={"readable": False, "mods": {
          "fleet_mod": None, "book_mod": None, "tide_mod": None,
          "vol_mod": None, "rot_mod": None}}))
check("2.10 the discount scale clamps 0.5-1.5",
      r7["lw_scale"] <= 1.5 and r3["lw_scale"] >= 0.5,
      (r7["lw_scale"], r3["lw_scale"]))

# ═══ 3. the entry hatch ══════════════════════════════════════════════
check("3.1 the hatch never leaves its regime's band (a dead tide's floor sinks to 2 x TideMod; the old static 2% is the neutral case)",
      co.required_own_move(r7) <= co.hatch_bounds(res=r7)[1]
      and co.required_own_move(r) >= co.hatch_bounds(res=r)[0],
      (co.required_own_move(r), co.hatch_bounds(res=r)))
r8 = _carve({"fleet": 1.2, "book": 0.6, "tide": 1.4, "vol": 1.3, "rot": 1.4})
check("3.2 a strong regime RAISES the bar (a solo move means less there)",
      co.required_own_move(r8) > co.required_own_move(r2),
      (co.required_own_move(r8), co.required_own_move(r2)))
r9 = _carve({"fleet": 0.7, "book": None, "tide": 1.0, "vol": None, "rot": 1.0})
check("3.3 a weak fleet suppresses and closes the hatch",
      r9["suppressed"] is True and r9["lone_wolf"] is False)

# ═══ 4. the context engines ══════════════════════════════════════════
def _with_res(mods, readable=True):
    return {"carve": 0.05, "lone_wolf": True, "readable": readable,
            "when": NOW.isoformat(timespec="seconds"),
            "mods": {"fleet_mod": mods.get("fleet"), "book_mod": mods.get("book"),
                     "tide_mod": mods.get("tide"), "vol_mod": mods.get("vol"),
                     "rot_mod": mods.get("rot")},
            "drift": False, "sal_mult": 1.0, "endgame": False,
            "suppressed": False, "expanded": False, "lw_scale": 1.0}

_res = _with_res({"fleet": 1.2, "book": 0.8, "tide": 1.2, "vol": 1.2, "rot": 1.2})
_orig = co.compute_carve_out
co.compute_carve_out = lambda pair=None, when=None, **k: _res
dc, dr = co.decay_context()
check("4.1 high vol + strong rotation + live tide SLOW the decay (>1 is slower)",
      dr and dc > 1.0, dc)
pc, pr = co.penalty_context()
check("4.2 strong fleet/rotation/vol LIGHTEN the penalty (<1)",
      pr and pc < 1.0, pc)
gc, gr = co.goal_context()
check("4.3 strong rotation/tide/fleet RAISE the goal; vol/book LOWER — "
      "net here is the plain terms beating the inversions",
      gr and gc != 1.0, gc)
_res2 = _with_res({"fleet": 0.8, "book": 1.2, "tide": 0.8, "vol": 0.8, "rot": 0.8})
co.compute_carve_out = lambda pair=None, when=None, **k: _res2
dc2, _ = co.decay_context()
check("4.4 tide out + a heavy book FASTEN the decay (<1)",
      dc2 < 1.0, dc2)
pc2, _ = co.penalty_context()
check("4.5 a weak fleet + a heavy book HEAVEN the penalty (>1)",
      pc2 > 1.0, pc2)
gc2, _ = co.goal_context()
check("4.6 tide out + vol + a heavy book LOWER the goal (<1)",
      gc2 < 1.0, gc2)
co.compute_carve_out = lambda pair=None, when=None, **k: (
    {**_res, "readable": False})
_, dr3 = co.decay_context()
check("4.7 an unreadable context is fail-neutral (1.0, False)",
      dr3 is False)
co.compute_carve_out = _orig

# ═══ 5. GuardFactor integration ══════════════════════════════════════
gf2, meta2 = co.apply_to_guard_factor(1.0, _with_res(NEUTRAL), NOW)
check("5.1 GF x (1 + CarveOut) at the finalize",
      abs(gf2 - (1.0 * (1.0 + 0.05))) < 1e-9 and
      abs(meta2["carve_mult"] - 1.05) < 1e-9, (gf2, meta2))
gfn, metan = co.apply_to_guard_factor(None, _with_res(NEUTRAL), NOW)
check("5.2 a None GF passes through untouched (fixed rules stand)",
      gfn is None and metan == {})
check("5.3 the finalize's tail clamp is the ADAPTIVE band (the static 0.4/2.0 are only the no-carve fallback)",
      'meta.get("gf_min")' in ml_src and 'meta.get("gf_max")' in ml_src
      and "max(_lo, min(max(_lo, _hi), final))" in ml_src)

# ═══ 6. the RCL kill ═════════════════════════════════════════════════
_orig_blocked = ml.reentry_blocked
ml.reentry_blocked = lambda when=None: True
try:
    re = ml.reentry_candidate_check("TEST/USD", NOW)
finally:
    ml.reentry_blocked = _orig_blocked
check("6.1 a standing drift verdict voids the re-entry candidate",
      re["eligible"] is False and re["conditions"].get("rcl_drift") is True, re)

# ═══ 7. the DNL record ═══════════════════════════════════════════════
_night = co.night_update(NOW)
_st = co._state()
check("7.1 night_update freezes the five mods for tomorrow",
      all(_st.get(k) is not None for k in
          ("fleet_mod", "book_mod", "tide_mod", "vol_mod", "rot_mod")), _night)
check("7.2 the history keeps the day (30-day cap shape)",
      any(h.get("date") == NOW.strftime("%Y-%m-%d")
          for h in (_st.get("dnl_history") or [])))
check("7.3 the goal factor rides the allowance",
      abs(_night["goal_factor"] - round(1.0 + _night["carve"], 3)) < 5e-3,
      (_night["goal_factor"], _night["carve"]))

# ═══ 8. the honesty audit (user ask: 'zero fixed constants left') ════
check("8.1 the old +5% survives ONLY as the base of the formula",
      "BASE_CARVE_OUT = 0.05" in co_src
      and "LONE_WOLF_MOVE_MIN, 1.0, False" in bs_src)
check("8.2 the decay slope is now context-scaled (the fixed tau is the base)",
      "eff_tau" in pg_src and "decay_context" in pg_src)
check("8.3 the penalty extra is now context-weighted",
      "_extra *= _w" in pg_src)
check("8.4 the goal baseline is now context-scaled",
      "pace *= _gm" in pg_src and "goal_context" in ml_src)
check("8.5 the console's breakdown can never import the carve (one-brain)",
      "_guard_breakdown_rows" in ap_src)

fails = [r for r in results if not r[1]]
(scratch_space.report_temp('_carveout_check.txt')).write_text(
    "\n".join(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{d}]" if d and not ok else "")
              for name, ok, d in results)
    + f"\n\nRESULT: {len(results) - len(fails)} passed, {len(fails)} failed\n",
    encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
