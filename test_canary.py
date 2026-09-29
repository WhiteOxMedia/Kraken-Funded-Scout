"""THE CANARY DRIVE (task 6): the dial flips to canary and back on the
machine's REAL dial file seam (the sandbox moves it via the env override
the module already honors), the spec's thresholds evaluate honestly, a
simulated breach TRIGGERS the rollback, and the run report lands in
artifacts/canary/ — the rollback proven, not assumed."""
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


# ── the sandbox: the dial file moved via the machine's env override ──
_sbx = Path(tempfile.mkdtemp())
_dial = _sbx / "exit_extension.dial.json"
_real_dial_file = cr.DIAL_FILE
cr.DIAL_FILE = _dial          # the module reads the module global


def _dial_doc():
    try:
        return json.loads(_dial.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


try:
    # the flip: canary mode announces the cohort
    flipped = cr.flip_canary("canary-1")
    doc = _dial_doc()
    check("the flip writes the machine's dial to canary with the "
          "cohort id (the spec's opsctl set, in the machine's form)",
          flipped["dial"] == "canary"
          and doc.get("EXIT_EXTENSION_ENABLED") == "canary"
          and doc.get("cohort_id") == "canary-1", doc)
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

    # the run: pass path writes the report, dial stays canary
    rec = cr.run_canary(cohort, seed, cycles=30, seed=11)
    rp = Path(rec["report"])
    check("a passing run keeps the dial in canary and writes its "
          "report with the sign-off next step",
          rec["status"] == "pass_continue" and rp.exists()
          and cr.read_dial() == "canary"
          and "sign-off" in rec.get("next", ""), rec["status"])

    # THE BREACH: the cohort's payer-rate collapses in the second half
    # (the regime shift) -> the runner rolls the dial back OFF. The
    # rollback is the machine's real one. Forcing duds EVERYWHERE can't
    # breach (the rule refuses to loosen on a low-ratio stream — the
    # machine being right), so the drill poisons the back half only.
    rec2 = cr.run_canary(cohort, {"B": 12, "r": 0.45}, cycles=30,
                         seed=11,
                         breach_injection={"second_half_P": 0})
    check("the breach run rolls the dial back OFF (the spec's rollback "
          "command, in the machine's form)",
          rec2["status"] == "rolled_back"
          and rec2["rollback"]["rolled_back"] is True
          and cr.read_dial() == "0", (rec2["status"], cr.read_dial()))
    check("the breach evaluation names its reasons (a breach is never "
          "a bare boolean)",
          rec2["evaluation"]["breach"]
          and len(rec2["evaluation"]["reasons"]) >= 1,
          rec2["evaluation"]["reasons"])
    check("the breach report records the rollback event with the "
          "commit sha (auditable)",
          Path(rec2["report"]).exists()
          and rec2["commit_sha"], rec2["commit_sha"])

    # a fresh flip after the rollback (the operator re-arms cleanly)
    cr.flip_canary("canary-2")
    check("the operator re-arms a new cohort after a rollback",
          _dial_doc().get("cohort_id") == "canary-2"
          and cr.read_dial() == "canary", _dial_doc())
    rb = cr.rollback()
    check("the explicit rollback command works standalone",
          rb["rolled_back"] and cr.read_dial() == "0", rb)

    # the evaluate honors the worst-loss cap independently
    ev_cap = cr.evaluate(cyc, prior_band=(0.0, 0.0), worst_cap=-0.001)
    check("a too-tight worst-loss cap breaches by itself (the "
          "threshold is real, not decorative)",
          ev_cap["breach"]
          and any("single_book_loss" in r for r in ev_cap["reasons"]),
          ev_cap["reasons"])
finally:
    cr.DIAL_FILE = _real_dial_file
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
