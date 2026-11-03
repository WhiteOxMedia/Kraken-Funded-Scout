"""diag_cooldowncheck.py - THE MORNING CHECKLIST, AS ONE COMMAND (Sep 24).

The cooldown agenda (COOLDOWN_AGENDA.md, c789950) orders six steps at the
start of every operational day. This diag runs them IN ORDER and prints a
PASS/FAIL line per step, so the checklist is executed by the same machinery
it mandates rather than re-implemented beside it (one brain: the real
kraken_stalewatch and diag_hygiene processes run; this diag only GRADES).

  1. stalewatch        the purge runs (selftest green), summaries land in
                       reports/ (name/age/timestamp only, never contents)
  2. hygiene census    the audit + sweep run green; remaining root writers
                       are NAMED (the deferred backlog), never dumped
  3. scratch routing   no scratch-shaped stray sits at either root after
                       the sweep — no new root churn (the root _*.txt
                       check summaries are the census-tracked writer
                       backlog, NOT new strays: the enforceable test is
                       "no NEW root churn", per the agenda's own triage)
  4. report integrity  the audit-summary classes (hygiene summaries,
                       purge summaries, swept ledger, scratch census)
                       live in reports/ — none appear in a repo root
  5. swept-ledger      the trailing window carries no name at RECUR_AT
                       occurrences (no recurring scratch class, no
                       regenerate-able litter loop) and no RULE D row's
                       name has been REBORN at a root (no stuck loop)
  6. RULE D            every KNOWN_DEAD_ARTIFACTS name is absent from
                       both roots, and HEAD is a descendant of the last
                       known good commit. SCOPE (per the agenda's triage):
                       this verdict covers COMMITTED state — the other
                       thread's uncommitted hunks are outside it, and the
                       detail line says so with the deferred count.

Runs the stalewatch/hygiene cycle as a side effect (that IS step 1-2) —
run it any time of day; it is idempotent and everything it touches writes
only into reports/ (the policy's allowed summary classes).

Run:  .venv\\Scripts\\python.exe -X utf8 diag_cooldowncheck.py
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import diag_hygiene as dh

SCOUT = Path(__file__).resolve().parent
PY = SCOUT / ".venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = Path(sys.executable)

# the last known good commit (step 6's anchor). POLICY: bump this after
# every clean full-battery baseline — it is the "no drift since" mark.
LAST_GOOD = "c789950"

# the audit-summary classes: reports/ is their home, a root is a violation
REPORT_CLASSES = ("hygiene_summary_*.txt", "kraken_stale_purge_*.txt",
                  "hygiene_swept_ledger.jsonl", "scratch_census_*.txt")

results = []


def check(step, name, cond, detail=""):
    results.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  step {step} — {name}"
          + (f"  [{detail}]" if detail and not cond else
             (f"  ({detail})" if detail else "")))


def _run(script, timeout):
    """Spawn one checklist step. A timed-out child is a FAIL, not a
    crash: the timeout is reported as the step's result and the checklist
    carries on to the remaining steps (a dead step must not silence the
    other five — the Sep-24 review finding)."""
    try:
        return subprocess.run(
            [str(PY), "-X", "utf8", str(SCOUT / script)],
            cwd=str(SCOUT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        import types
        return types.SimpleNamespace(
            returncode=-3,
            stdout=f"TIMED OUT after {timeout}s — step could not finish")


def _fresh_reports(pattern: str, window_s: float = 300.0,
                   now: float | None = None,
                   reports_dir: Path | None = None) -> list[str]:
    """Summary-class files written within the last window_s seconds —
    the evidence that a step's machinery REPORTED into reports/ and did
    not merely exit 0. (reports_dir param: the drive's sandbox seam.)"""
    now = time.time() if now is None else now
    d = reports_dir or (SCOUT / "reports")
    return [p.name for p in sorted(d.glob(pattern))
            if now - p.stat().st_mtime < window_s]


def _root_report_violations(roots=None) -> list[str]:
    """Step 4: audit-summary-class files sitting at a repo root — the
    classes whose home is reports/ (a root copy is a stray report)."""
    roots = [SCOUT, dh.BOT] if roots is None else list(roots)
    return [p.name for pat in REPORT_CLASSES
            for root in roots for p in root.glob(pat)]


def _reborn_rule_d(rows: list, roots=None) -> list[str]:
    """Step 5: RULE D ledger rows whose name EXISTS again at a root —
    a fossil a rebirth loop keeps regenerating (the stuck-loop signal)."""
    roots = [SCOUT, dh.BOT] if roots is None else list(roots)
    return sorted({str(r.get("name")) for r in rows
                   if r.get("rule") == "RULE D"
                   and any((root / str(r.get("name"))).exists()
                           for root in roots)})


def main() -> int:
    print("=" * 64)
    print("  THE MORNING CHECKLIST (COOLDOWN_AGENDA.md) — in order")
    print("=" * 64)

    # ── step 1: stalewatch ────────────────────────────────────────────
    sw = _run("kraken_stalewatch.py", 120)
    out1 = sw.stdout or ""
    check(1, "stalewatch purge ran clean (rc=0)", sw.returncode == 0,
          f"rc={sw.returncode}")
    check(1, "stalewatch selftest green inside the run",
          "selftest" in out1 and not re.search(r"FAIL\s+selftest", out1))
    purges = _fresh_reports("kraken_stale_purge_*.txt")
    check(1, "purge summaries land in reports/ (names/ages only)",
          bool(purges),
          f"{len(purges)} fresh purge summary file(s) in reports/")

    # ── step 2: hygiene census (the audit + sweep ARE the census) ────
    hy = _run("diag_hygiene.py", 180)
    out2 = hy.stdout or ""
    check(2, "hygiene audit + sweep ran clean (rc=0)", hy.returncode == 0,
          f"rc={hy.returncode}")
    check(2, "HYGIENE: CLEAN after the sweep", "HYGIENE: CLEAN" in out2)
    cens = dh.census_writers()
    deferred = sorted({fname for fname, _ln, _n, _sh in cens})
    print(f"        census: {len(deferred)} root-writer file(s) deferred "
          "(names only): " + (", ".join(deferred[:6])
          + (f", +{len(deferred) - 6} more" if len(deferred) > 6 else "")
          if deferred else "none"))

    # ── step 3: scratch routing — no NEW root churn ──────────────────
    strays, _counts = dh.audit()          # read-only, post-sweep state
    check(3, "no scratch-shaped stray at either root (no new churn)",
          not strays, f"strays: {[f.name for f, _ in strays][:6]}")

    # ── step 4: report integrity — the summary classes live in reports/
    # AND this run's hygiene summary actually LANDED (a silent write
    # failure would otherwise pass a root-only check — rc 0 is not
    # evidence of reporting)
    stray_reports = _root_report_violations()
    check(4, "audit summaries live in reports/, none at a root",
          not stray_reports, f"root copies: {stray_reports[:6]}")
    fresh_sum = _fresh_reports("hygiene_summary_*.txt")
    check(4, "this run's hygiene summary landed in reports/",
          bool(fresh_sum), "no fresh hygiene summary after the step-2 run")

    # ── step 5: swept-ledger health ───────────────────────────────────
    rows = dh._ledger_load()
    rec = dh._ledger_recurring(rows, {r.get("name") for r in rows})
    check(5, "no recurring scratch class (nothing at RECUR_AT in the "
          "trailing window)", not rec, f"recurring: {rec}")
    reborn = _reborn_rule_d(rows)
    check(5, "no RULE D row reborn at a root (no stuck loop)",
          not reborn, f"reborn: {reborn}")

    # ── step 6: RULE D alignment ──────────────────────────────────────
    fossils = sorted(n for n in dh.KNOWN_DEAD_ARTIFACTS
                     if (SCOUT / n).exists() or (dh.BOT / n).exists())
    check(6, "every KNOWN_DEAD_ARTIFACTS name absent from both roots",
          not fossils, f"present: {fossils}")
    anc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", LAST_GOOD, "HEAD"],
        cwd=str(SCOUT), capture_output=True)
    check(6, f"HEAD is a descendant of the last known good ({LAST_GOOD})",
          anc.returncode == 0, f"rc={anc.returncode}")
    print(f"        scope: alignment confirmed for COMMITTED state; "
          f"{len(deferred)} deferred writer file(s) left to their owner "
          "(uncommitted hunks are outside this verdict)")

    print("-" * 64)
    passed, failed = results.count(True), results.count(False)
    verdict = ("COOLDOWN CHECKLIST: all "
               f"{len(results)} checks green — resume normal duties"
               if not failed else
               f"COOLDOWN CHECKLIST: {failed} check(s) RED — triage before "
               "the operational cycle (the order rule stands)")
    print(verdict)
    print(f"RESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
