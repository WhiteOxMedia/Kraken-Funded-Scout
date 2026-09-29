"""THE CANARY DRIVE (task 6): the dial flips to canary and back on the
machine's REAL dial file seam (the sandbox moves it via the module
globals the drive swaps), the spec's thresholds evaluate honestly, and
run_canary DEFAULTS TO A PURE SIMULATION (the four-review major): no
flip, no rollback — production state (the live cohort doc with its
params cell, the operator's dial) survives a simulated run byte-exact,
and a simulated breach only REPORTS the required rollback. arm=True is
the staging exercise proven in-sandbox: real flip, and a breach executes
the machine's real rollback. The run report lands in artifacts/canary/.
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

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


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
