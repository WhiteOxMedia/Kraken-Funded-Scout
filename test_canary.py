"""THE CANARY DRIVE (task 6): the dial flips to canary and back on the
machine's REAL dial file seam (the sandbox moves it via the module
globals the drive swaps), the spec's thresholds evaluate honestly, and
run_canary DEFAULTS TO A PURE SIMULATION (the four-review major): no
flip, no rollback — production state (the live cohort doc with its
params cell, the operator's dial) survives a simulated run byte-exact,
and a simulated breach only REPORTS the required rollback. arm=True is
the staging exercise proven in-sandbox: real flip, and a breach executes
the machine's real rollback — and it REFUSES the LIVE cohort file (the
three-review family's last door) unless arm_confirm=True, hermetically
pinned via the resolved-path guard. The run report lands in
artifacts/canary/ (the CANARY_RUNS_DIR seam).
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import canary_runner as cr
import auto_adjust as cr_aa

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


# ── THE LIVE-COHORT GUARD (the three-review family's last door, the
# once-and-for-all fix): BEFORE any sandbox rebinding, with the module
# pointed at the REAL paths, arm=True against the LIVE cohort must
# REFUSE — a named exception, production untouched, nothing armed, and
# the refusal names the escape hatches (the env seam, arm_confirm).
# On the live machine the real cohort file exists (gitignored); in the
# gate's worktree it does not — the guard fires on the RESOLVED PATH
# comparison either way, which is exactly the contract.
try:
    cr.run_canary(["BTC"], {"B": 12, "r": 0.45}, cycles=2, arm=True)
    _refused = False
    _ref_exc = ""
except cr.CanaryArmRefused as _exc:
    _refused = True
    _ref_exc = str(_exc)
check("arm=True REFUSES the live cohort (a named CanaryArmRefused — "
      "the three-review family dies here, not in production)",
      _refused
      and "LIVE cohort file" in _ref_exc
      and "AUTO_ADJUST_CANARY_FILE" in _ref_exc
      and "arm_confirm" in _ref_exc, _ref_exc)
_live_bytes_pre = (cr.RUNTIME_COHORT_FILE.read_bytes()
                   if cr.RUNTIME_COHORT_FILE.exists() else None)
check("the refusal touches nothing: the live cohort file is "
      "byte-unchanged (or still absent) after the refused arm — the "
      "drill died at the guard, not in production",
      (cr.RUNTIME_COHORT_FILE.read_bytes()
       if cr.RUNTIME_COHORT_FILE.exists() else None) == _live_bytes_pre,
      _live_bytes_pre is not None)
# THE CONFIRM DOOR (hermetic): with RUNTIME_COHORT_FILE rebound to a
# temp twin, the guard fires on the RESOLVED-PATH comparison (not the
# ROOT path — a sandbox cannot dodge it by rebinding the module
# constant alone), and arm_confirm=True is the only way through.
_twin_dir = Path(tempfile.mkdtemp())
_twin = _twin_dir / "auto_adjust_canary.json"
_real_runtime_path = cr.RUNTIME_COHORT_FILE
_real_cohort_file_saved = cr.COHORT_FILE
# the confirm-door run writes a REAL report — keep it in the leg's own
# sandbox (this leg runs before the drive's main runs-dir setup)
_runs_env_real = os.environ.get(cr.RUNS_ENV)
os.environ[cr.RUNS_ENV] = str(_twin_dir)
try:
    cr.RUNTIME_COHORT_FILE = _twin
    cr.COHORT_FILE = _twin
    _refused2 = False
    try:
        cr.run_canary(["BTC"], {"B": 12, "r": 0.45}, cycles=2, arm=True)
    except cr.CanaryArmRefused:
        _refused2 = True
    check("the guard compares RESOLVED PATHS: rebinding the runtime "
          "path re-arms the guard even for a sandboxed module path",
          _refused2)
    rec_live = cr.run_canary(["BTC"], {"B": 12, "r": 0.45}, cycles=2,
                             arm=True, arm_confirm=True)
    check("arm_confirm=True is the DELIBERATE live re-arm — the only "
          "door past the guard: the flip lands and the run report "
          "stamps armed_live_cohort=True for the audit trail",
          rec_live["status"] in ("rolled_back", "pass_continue")
          and rec_live.get("armed_live_cohort") is True
          and rec_live.get("dial") == {"dial": "canary", "cohort_id":
                                       f"canary-{rec_live['run_id']}"},
          (rec_live["status"], rec_live.get("armed_live_cohort")))
finally:
    cr.RUNTIME_COHORT_FILE = _real_runtime_path
    cr.COHORT_FILE = _real_cohort_file_saved
    if _runs_env_real is None:
        os.environ.pop(cr.RUNS_ENV, None)
    else:
        os.environ[cr.RUNS_ENV] = _runs_env_real
    shutil.rmtree(_twin_dir, ignore_errors=True)

# ── the sandbox: BOTH files moved (the two-file design) — the dial
# file via the module global, the cohort file beside it ──────────────
_sbx = Path(tempfile.mkdtemp())
_dial = _sbx / "exit_extension.dial.json"
_cohort = _sbx / "auto_adjust_canary.json"
_runs = _sbx / "runs"
_real_dial_file = cr.DIAL_FILE
_real_cohort_file = cr.COHORT_FILE
_real_runs_env = os.environ.get(cr.RUNS_ENV)
os.environ[cr.RUNS_ENV] = str(_runs)   # the reports ride the sandbox too
cr.DIAL_FILE = _dial          # the module reads the module global
cr.COHORT_FILE = _cohort      # the cohort rides its own file


def _dial_doc():
    try:
        return json.loads(_dial.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _cohort_doc():
    try:
        return json.loads(_cohort.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# THE COHORT SEAM (the round-7 major): AUTO_ADJUST_CANARY_FILE — the
# env exit_extension has honored since the two-file design — must move
# the RUNNER's reads AND writes too. The old import-frozen path made a
# sandbox that set the env flip the LIVE cohort file anyway (the write
# followed the module constant, the HOOK's read followed the env): the
# isolation break. One env move now sandboxes the whole two-module pair.
_sbx_env = Path(tempfile.mkdtemp())
_env_cohort = _sbx_env / "auto_adjust_canary.json"
_real_cohort_env = os.environ.get(cr.COHORT_ENV)
# the LIVE machine carries the real armed cohort file (gitignored, so
# the gate's staged worktree does not) — the pin is byte-exact either way
_live_bytes = (_real_cohort_file.read_bytes()
               if _real_cohort_file.exists() else None)
try:
    os.environ[cr.COHORT_ENV] = str(_env_cohort)
    _flipped_env = cr.flip_canary("env-cohort", params={"alpha": 0.03})
    check("the cohort env seam moves the runner's WRITE: flip_canary "
          "arms the env-pointed file and NEVER touches the module-path "
          "cohort (a sandboxed runner cannot flip the live cohort)",
          _flipped_env["dial"] == "canary"
          and json.loads(_env_cohort.read_text(encoding="utf-8"))
          .get("cohort_id") == "env-cohort"
          and json.loads(_env_cohort.read_text(encoding="utf-8"))
          .get("params", {}).get("alpha") == 0.03
          and (_real_cohort_file.read_bytes()
               if _real_cohort_file.exists() else None) == _live_bytes)
    check("the cohort env seam moves the runner's READ: read_cohort "
          "answers from the env-pointed file (rollback can stop what "
          "the env points at)",
          cr.read_cohort() == "env-cohort")
    _rb_env = cr.rollback()
    check("the seam rollback lands on the env-pointed file (the "
          "sandbox cohort disarms; the module path still untouched)",
          _rb_env["rolled_back"] is True
          and json.loads(_env_cohort.read_text(encoding="utf-8"))
          .get("disarmed") is True
          and (_real_cohort_file.read_bytes()
               if _real_cohort_file.exists() else None) == _live_bytes)
finally:
    if _real_cohort_env is None:
        os.environ.pop(cr.COHORT_ENV, None)
    else:
        os.environ[cr.COHORT_ENV] = _real_cohort_env
    shutil.rmtree(_sbx_env, ignore_errors=True)

try:
    # THE TWO-FILE CONTRACT (the structural fix): the flip arms the
    # cohort file; the exit-extension dial file is NEVER created or
    # written — arming the canary cannot silently disable the feature
    # because the controls no longer share a file.
    flipped = cr.flip_canary("canary-1")
    cdoc = _cohort_doc()
    check("the flip arms the cohort on ITS OWN file (cohort id + armed "
          "at), and the exit-extension dial file is NEVER touched",
          flipped["dial"] == "canary"
          and cdoc.get("cohort_id") == "canary-1"
          and cdoc.get("armed_at")
          and not _dial.exists(), (cdoc, _dial.exists()))
    check("read_dial reports canary from the cohort file while the "
          "dial file stays untouched",
          cr.read_dial() == "canary" and not _dial.exists(),
          (cr.read_dial(), _dial.exists()))
    # an operator arm on the feature master coexists: the flip preserves
    # the dial file byte-exact (nothing writes it)
    _dial.write_text(json.dumps({"EXIT_EXTENSION_ENABLED": "1"}),
                     encoding="utf-8")
    cr.flip_canary("canary-2")
    check("an operator '1' on the feature master survives a cohort flip "
          "byte-exact (two controls, two files)",
          _dial_doc() == {"EXIT_EXTENSION_ENABLED": "1"}
          and _cohort_doc().get("cohort_id") == "canary-2",
          (_dial_doc(), _cohort_doc()))
    check("the dial reads back through the machine's reader",
          cr.read_dial() == "canary", cr.read_dial())

    # a healthy cohort: the seeded cycles produce a passing evaluation
    seed = {"B": 12, "r": 0.45}
    cohort = ["BTC", "ETH", "SOL", "AVAX", "LINK"]
    cyc = cr.collect_cycles(cohort, 50, seed, seed=11)
    check("the collector produced 50 seeded cycles with B and P",
          len(cyc) == 50
          and all(c["B"] >= 0 and 0 <= c["P"] <= c["B"] for c in cyc),
          (cyc[0], cyc[-1]))
    ev = cr.evaluate(cyc, None)
    check("a healthy run passes all three thresholds (net in band, "
          "worst loss within cap, hit rate stable)",
          not ev["breach"] and ev["net_in_band"]
          and ev["worst_single_book_loss"] >= cr.WORST_LOSS_CAP
          and ev["hit_rate_stable"], ev)

    # the run: pass path writes the report — and BY DEFAULT A PURE
    # SIMULATION (the four-review major): production state is never
    # touched. A pre-seeded live-style cohort (armed_at + a params cell
    # the gate selected) must survive the run BYTE-EXACT: a simulated
    # run that flipped would both re-arm the live cohort AND strip the
    # params cell (flip_canary writes a fresh doc). Note the seeded
    # cohort uses a stable id — the pin proves the id did not rotate.
    _dial.write_text(json.dumps({"EXIT_EXTENSION_ENABLED": "1"}),
                     encoding="utf-8")
    cr.flip_canary("live-cohort-do-not-touch",
                   params={"alpha": 0.03, "delta_max": 0.05,
                           "daily_cap": 0.2})
    _live_before = _cohort.read_text(encoding="utf-8")
    rec = cr.run_canary(cohort, seed, cycles=30, seed=11)
    rp = Path(rec["report"])
    check("a simulated run (default arm=False) touches NO production "
          "state: no flip (dial None), the live-style cohort doc "
          "survives byte-exact (id, armed_at, AND the params cell the "
          "gate selected), the operator's dial '1' survives, and the "
          "report still lands with the sign-off next step",
          rec["status"] == "pass_simulated" and rec["dial"] is None
          and rec["rollback"] is None
          and _cohort.read_text(encoding="utf-8") == _live_before
          and _cohort_doc().get("params", {}).get("alpha") == 0.03
          and _dial_doc() == {"EXIT_EXTENSION_ENABLED": "1"}
          and rp.exists() and "sign-off" in rec.get("next", "")
          and rp.is_relative_to(_runs)
          and not (BASE / "artifacts" / "canary"
                   / f"{rec['run_id']}.json").exists(),
          (rec["status"], rec.get("dial")))

    # THE SIMULATED BREACH: the cohort's payer-rate collapses in the
    # second half (the regime shift). SIMULATED, so the rollback is
    # only REQUIRED, never executed — the live cohort stays untouched.
    # Forcing duds EVERYWHERE can't breach (the rule refuses to loosen
    # on a low-ratio stream — the machine being right), so the drill
    # poisons the back half only.
    rec2 = cr.run_canary(cohort, {"B": 12, "r": 0.45}, cycles=30,
                         seed=11,
                         breach_injection={"second_half_P": 0})
    check("a simulated breach REPORTS the required rollback but never "
          "executes it: status breach_simulated, rollback_required, "
          "record['rollback'] None, the live cohort STILL armed and "
          "byte-exact, the operator's master arm '1' untouched",
          rec2["status"] == "breach_simulated"
          and rec2.get("rollback_required") is True
          and rec2["rollback"] is None
          and cr.read_cohort() == "live-cohort-do-not-touch"
          and _cohort.read_text(encoding="utf-8") == _live_before
          and cr.read_dial() == "canary"
          and _dial_doc() == {"EXIT_EXTENSION_ENABLED": "1"},
          (rec2["status"], cr.read_cohort(), cr.read_dial()))
    check("the breach evaluation names its reasons (a breach is never "
          "a bare boolean)",
          rec2["evaluation"]["breach"]
          and len(rec2["evaluation"]["reasons"]) >= 1,
          rec2["evaluation"]["reasons"])
    check("the breach report records the run with the commit sha "
          "(auditable)",
          Path(rec2["report"]).exists() and rec2["commit_sha"],
          rec2["commit_sha"])

    # THE STAGING LEG (arm=True in the sandbox): a REAL arm — flip, and
    # a breach executes the machine's real rollback. The drill re-arms
    # AFTER the simulation legs, so the simulated breach above provably
    # never touched the cohort this leg flips.
    rec3 = cr.run_canary(cohort, {"B": 12, "r": 0.45}, cycles=30,
                         seed=11, arm=True,
                         breach_injection={"second_half_P": 0})
    check("arm=True is the staging exercise: a REAL flip arms the "
          "sandbox cohort (dial canary, id rotated) and a breach "
          "executes the REAL rollback — the cohort disarms, the "
          "operator's own master arm '1' SURVIVES (a cohort rollback "
          "never silently disables the exit-extension feature)",
          rec3["status"] == "rolled_back"
          and rec3["dial"] == {"dial": "canary",
                               "cohort_id": f"canary-{rec3['run_id']}"}
          and rec3["rollback"]["rolled_back"] is True
          and cr.read_cohort() == ""
          and cr.read_dial() == "1"
          and _dial_doc() == {"EXIT_EXTENSION_ENABLED": "1"},
          (rec3["status"], cr.read_cohort(), cr.read_dial()))
    check("the armed breach report records the rollback event with the "
          "commit sha (auditable)",
          Path(rec3["report"]).exists() and rec3["commit_sha"],
          rec3["commit_sha"])

    # a fresh flip after the staging rollback (the operator re-arms
    # cleanly)
    cr.flip_canary("canary-2")
    check("the operator re-arms a new cohort after a rollback (on the "
          "cohort file — the dial file still byte-exact)",
          _cohort_doc().get("cohort_id") == "canary-2"
          and cr.read_dial() == "canary"
          and _dial_doc() == {"EXIT_EXTENSION_ENABLED": "1"},
          (_cohort_doc(), _dial_doc()))
    rb = cr.rollback()
    check("the explicit rollback stops the COHORT (the dial file and "
          "the operator's own master arm are never touched)",
          rb["rolled_back"] and rb.get("cohort") == "disarmed"
          and cr.read_cohort() == ""
          and cr.read_dial() == "1"
          and _dial_doc() == {"EXIT_EXTENSION_ENABLED": "1"},
          (rb, cr.read_dial(), _dial_doc()))

    # the evaluate honors the worst-loss cap independently
    ev_cap = cr.evaluate(cyc, prior_band=(0.0, 0.0), worst_cap=-0.001)
    check("a too-tight worst-loss cap breaches by itself (the "
          "threshold is real, not decorative)",
          ev_cap["breach"]
          and any("single_book_loss" in r for r in ev_cap["reasons"]),
          ev_cap["reasons"])

    # ── THE GUARD-FACTOR VALIDATION (Sep 30, "the bot follows the
    # factor now — drills that cannot fail validate nothing"):
    # (a) the band must be certified by EXTERNAL evidence — a prior
    # band supplied by the caller; a self-derived CI proves nothing
    # (the canary grading its own homework, the recovered CR major);
    # (b) the worst cap comes from the CELL the gate armed (a cell AT
    # its own dmax is the cap echoed back — canary_enable's doctrine —
    # so the cell cap sits strictly inside at 0.8×dmax);
    # (c) the run WALKS the guard factor the bot actually follows —
    # per-cycle deltas through the machine's own clamp [MIN_GF, MAX_GF]
    # and the daily-cap rail — and breaches when the cell would drive
    # the bot's calibration to the floor.
    big = {"cycle": 99, "rot": "canary-big", "B": 200, "P": 90,
           "buried": [], "paid": []}          # r=0.45, ln(201): the
    # delta reaches the cell's dmax — the shape the cap must answer
    runaway = [dict(big, cycle=i) for i in range(50)]

    ev_noband = cr.evaluate(cyc, None)
    check("a self-derived CI can never certify the run: with no prior "
          "band supplied the verdict is INCONCLUSIVE (outside_band "
          "False, band_certified False, the stale-certification hole "
          "closed) and the reason is named",
          ev_noband["outside_band"] is False
          and ev_noband["band_certified"] is False
          and any("band not certified" in r for r in ev_noband["reasons"]),
          ev_noband)
    ev_bad = cr.evaluate(cyc, prior_band=(0.0, 0.0))
    check("a supplied band the run falls OUTSIDE of is a hard breach "
          "even when every other threshold is quiet (the certifier has "
          "teeth)",
          ev_bad["outside_band"] is True and ev_bad["band_certified"]
          and ev_bad["breach"]
          and any("outside band" in r for r in ev_bad["reasons"]),
          ev_bad)
    ev_good = cr.evaluate(cyc, prior_band=(-0.2, 0.0))
    check("a supplied band the run sits inside certifies cleanly (and "
          "net_in_band stays the honest report field)",
          ev_good["outside_band"] is False and ev_good["band_certified"]
          and not any("outside band" in r for r in ev_good["reasons"]),
          ev_good)

    ev_cell = cr.evaluate([big], prior_band=(-5.0, 5.0),
                          params={"alpha": 0.03, "delta_max": 0.05,
                                  "daily_cap": 0.2})
    check("the worst cap comes from the CELL the gate selected, "
          "strictly inside its own dmax (0.8x — a cell AT its dmax is "
          "the cap echoed back, not evidence): the cap can actually "
          "fire for a 0.05 cell",
          ev_cell["worst_cap"] == 0.04 and ev_cell["breach"]
          and any("single_book_loss" in r for r in ev_cell["reasons"]),
          (ev_cell.get("worst_cap"), ev_cell.get("reasons")))
    check("the evaluation records the cell it judged (the armed "
          "parameters are provably live in the verdict)",
          ev_cell["params_cell"] == {"alpha": 0.03, "delta_max": 0.05,
                                     "daily_cap": 0.2},
          ev_cell.get("params_cell"))

    ev_floor = cr.evaluate(runaway, prior_band=(-5.0, 5.0),
                           params={"alpha": 0.10, "delta_max": 0.10,
                                   "daily_cap": 2.0})
    check("the guard-factor walk BREACHES when the cell would drive "
          "the bot's calibration to the floor (pinned live factor, "
          "the daily cap deliberately unconstrained so the CLAMP rail "
          "is what stops the walk at MIN_GF)",
          ev_floor["guard_factor_breach"] is True
          and ev_floor["guard_factor"]["end"] == cr_aa.MIN_GF
          and ev_floor["guard_factor"]["applied"] == 15
          and any("guard factor" in r for r in ev_floor["reasons"]),
          (ev_floor.get("guard_factor"), ev_floor.get("reasons")))
    ev_walk = cr.evaluate(cyc, prior_band=(-5.0, 5.0))
    check("the healthy walk reports the factor the bot follows: start "
          "inside the machine's clamp, end never above start (the "
          "rule only loosens), applies bounded by the rule-firing "
          "cycles AND by the daily-cap rail (the cap refuses rotations "
          "once the day's budget is spent), and the healthy run never "
          "drives the factor to the floor",
          0.5 <= ev_walk["guard_factor"]["start"] <= 2.0
          and ev_walk["guard_factor"]["end"] <= ev_walk["guard_factor"]["start"]
          and 0 < ev_walk["guard_factor"]["applied"]
          <= ev_walk["applied_cycles"]
          and ev_walk["guard_factor"]["end"] > cr_aa.MIN_GF
          and ev_walk["guard_factor_breach"] is False,
          (ev_walk.get("guard_factor"), ev_walk.get("applied_cycles")))
    ev_caprail = cr.evaluate([big], prior_band=(-5.0, 5.0),
                             params={"alpha": 0.03, "delta_max": 0.10,
                                     "daily_cap": 0.01})
    check("the walk rides the DAILY-CAP rail (a 0.01 cap refuses the "
          "cycle's loosening entirely — the machine refuses, it never "
          "truncates behind the caller's back)",
          ev_caprail["guard_factor"]["applied"] == 0
          and ev_caprail["guard_factor"]["end"]
          == ev_caprail["guard_factor"]["start"],
          ev_caprail.get("guard_factor"))
    check("a breaching walk fails the run even with a certified band "
          "and quiet caps (the factor verdict is its own threshold)",
          ev_floor["band_certified"] and ev_floor["breach"]
          and any("guard factor" in r for r in ev_floor["reasons"]),
          ev_floor["reasons"])
finally:
    cr.DIAL_FILE = _real_dial_file
    cr.COHORT_FILE = _real_cohort_file
    if _real_runs_env is None:
        os.environ.pop(cr.RUNS_ENV, None)
    else:
        os.environ[cr.RUNS_ENV] = _real_runs_env
    shutil.rmtree(_sbx, ignore_errors=True)

fails = [r for r in results if not r[1]]
rep = BASE / "artifacts" / "canary" / "_canary_drive.txt"
rep.parent.mkdir(parents=True, exist_ok=True)
rep.write_text("\n".join(f"{'PASS' if ok else 'FAIL'}  {n}" +
                         (f"  [{d}]" if d and not ok else "")
                         for n, ok, d in results)
               + f"\n\nRESULT: {len(results) - len(fails)} passed, "
                 f"{len(fails)} failed\n", encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
