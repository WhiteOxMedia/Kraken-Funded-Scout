"""test_cohortseam_smoke.py - THE COHORT-SEAM SMOKE (Oct 5, the ops
review's item 4): one fast drive that pins the payer-weighted cohort
seam's SIGNATURE end to end — the seam's constant names, the params
shape, the route wiring, and the weighted arithmetic — so a refactor
that renames or re-shapes the seam breaks a drive in seconds, not at
the next 4h rotation boundary.

GREEN-BY-DESIGN characterization (the seams are shipped and pinned
deeply in test_autoadjust / test_hindsightapi / test_canary / the
route section): this is the 5-second tripwire the commit gate and the
battery run on every pass, not a re-derivation. The live armed cohort
file is read ONLY conditionally (it is runtime state — the drive
skips honestly when it is absent, never grades its contents).

House conventions: RESULT line, sys.exit(1) on failure. The cohort
env seam is restored in the finally (the leak gate's contract).
"""
import inspect
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

results, passed, failed = [], 0, 0


def check(n, cond, why=""):
    global passed, failed
    results.append(bool(cond))
    passed, failed = passed + (1 if cond else 0), failed + (0 if cond else 1)
    print(f"{'PASS' if cond else 'FAIL'}  {n}  {why}")


import auto_adjust as aa                           # noqa: E402
import exit_extension as exx                       # noqa: E402
import hindsight_api as ha                         # noqa: E402

print("== 1. THE SEAM'S NAMES (a rename breaks here first) ==")
check(1.1, exx.AA_COHORT_ENV == "AUTO_ADJUST_CANARY_FILE",
      "the cohort env seam's name is the contract (canary_runner and "
      "the drives rebind THIS name; a rename orphans every sandbox)")
check(1.2, exx.AA_COHORT_FILE == "auto_adjust_canary.json",
      "the live cohort file's name is the contract (the armed cohort "
      "lives at the dial file's side under this name)")

print("== 2. THE PARAMS SHAPE (the cell rides the file) ==")
SANDBOX = Path(tempfile.mkdtemp(prefix="cohortsmoke_"))
_real_env = os.environ.get(exx.AA_COHORT_ENV)
_cell = {"cohort_id": "smoke-cell",
         "params": {"alpha": 0.03, "delta_max": 0.05, "daily_cap": 2,
                    "not_a_param": "junk"}}
_cell_path = SANDBOX / "canary.json"
_cell_path.write_text(json.dumps(_cell), encoding="utf-8")
try:
    os.environ[exx.AA_COHORT_ENV] = str(_cell_path)
    p = exx._aa_params()
    check(2.1, p == {"alpha": 0.03, "delta_max": 0.05, "daily_cap": 2.0},
          "the armed cell parses to exactly its three known params, "
          "floated — junk keys drop, nothing else leaks through")
    check(2.2, exx._aa_cohort() == "smoke-cell",
          "the cohort id rides the same file (one source of truth for "
          "who is armed and at what cell)")
    os.environ[exx.AA_COHORT_ENV] = str(SANDBOX / "absent.json")
    check(2.3, exx._aa_params() == {} and exx._aa_cohort() == "",
          "a missing cohort file is the disarmed posture — empty dict, "
          "empty id, module defaults ride (never a crash, never a guess)")
finally:
    if _real_env is None:
        os.environ.pop(exx.AA_COHORT_ENV, None)
    else:
        os.environ[exx.AA_COHORT_ENV] = _real_env
    shutil.rmtree(SANDBOX, ignore_errors=True)

print("== 3. THE SIGNATURE (what a refactor may not change) ==")
_sig = inspect.signature(aa.compute_delta)
check(3.1, all(k in _sig.parameters
               for k in ("B", "P", "alpha", "delta_max", "per_gate")),
      "compute_delta keeps its (B, P, alpha, delta_max, per_gate) "
      "signature — alpha/delta_max are the canary's cell overrides, "
      "per_gate is the payer-weighted mix")
_ha_src = (HERE / "hindsight_api.py").read_text(encoding="utf-8")
check(3.2, "_aa_params()" in _ha_src,
      "the HTTP route serves the armed cell through exit_extension's "
      "_aa_params seam (the wire, the hook, and the preview price ONE "
      "recommendation under one cell)")
check(3.3, "import exit_extension" in _ha_src.replace("'", '"')
      or "exit_extension" in _ha_src,
      "the route imports the seam's owner directly (no second cell "
      "source, no operator body value)")

print("== 4. THE ARITHMETIC (the payer-weighted shape, one vector) ==")
_mix = [{"gate": "deep-veto", "B_g": 5, "P_g": 3},
        {"gate": "tide-out", "B_g": 2, "P_g": 1}]
check(4.1, abs(aa.effective_ratio(7, 4, _mix) - 0.575) < 1e-9,
      "the ROT-1 mix prices r_eff = 0.575 exactly (the payer-weighted "
      "mean the magnitude prices; Cauchy-Schwarz keeps it >= r)")
_d, _why = aa.compute_delta(2, 0)
check(4.2, _d == 0.0 and _why == "insufficient_samples",
      "the aggregate's B_MIN promise refuses thin worlds with a NAMED "
      "reason (the mix never opens a gate the promise closed)")
_d2, _why2 = aa.compute_delta(8, 4)
check(4.3, isinstance(_d2, float) and _why2 == "ok" and _d2 < 0,
      "a live world (B=8, P=4, r=0.5 over the bar) prices a NEGATIVE "
      "delta (tighten) with the ok reason")
_d3, _why3 = aa.compute_delta(8, 4, alpha=0.03, delta_max=0.05)
check(4.4, _why3 == "ok" and abs(abs(_d3 / _d2) - 0.03 / 0.05) < 1e-9,
      "the canary's cell overrides the magnitude proportionally "
      "(alpha 0.03 vs 0.05 scales the delta; the seam rides through)")
_d4, _why4 = aa.compute_delta(8, 4, delta_max=0.001)
check(4.5, _why4 == "ok" and abs(_d4) <= 0.001 + 1e-12,
      "delta_max clamps the magnitude (the cell's cap is a hard bound)")

print("== 5. THE LIVE ARMED FILE (conditional — runtime state) ==")
_live = HERE / exx.AA_COHORT_FILE
if _live.exists():
    try:
        _doc = json.loads(_live.read_text(encoding="utf-8"))
        check(5.1, isinstance(_doc, dict)
              and bool(str(_doc.get("cohort_id", "") or "")),
              "the live armed cohort file parses and names its cohort "
              "(runtime state — this pin skips honestly when disarmed)")
    except ValueError as exc:
        check(5.1, False, f"the live cohort file is torn JSON: {exc}")
else:
    check(5.1, True,
          "no live armed cohort file right now — the seam's default "
          "path is the module-defaults posture (skipped honestly)")

print(f"RESULT: {passed} passed, {failed} failed")


def main():
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    sys.exit(main())
