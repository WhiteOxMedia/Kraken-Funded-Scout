"""Pins diag_cooldowncheck to its contract: the six agenda steps run IN
ORDER, each graded from the REAL machinery's output (never re-implemented),
fail-soft at every seam, and the verdict names never file contents.

(Named test_agendacheck: test_cooldowncheck.py is the other workstream's
Sep-4 cooldown-fresh-eyes drive — this drive's first draft collided with
it and was renamed; the original was restored byte-identical.)

Sections:
  1. constants/anchors        LAST_GOOD shape, REPORT_CLASSES home contract
  2. _fresh_purges            the recency window honors the injected now
  3. _root_report_violations  the summary classes are violations at a root,
                              silent in their reports/ home
  4. _reborn_rule_d           a RULE D row whose name is back at a root
                              flags; absent names stay quiet
  5. source pins              the order rule (step N text precedes step N+1
                              in main), the seam functions exist, the diag
                              is a runner not a reimplementation, DIAG_RUN
                              registration
  6. LIVE run                 the real diag runs end-to-end and its own
                              verdict agrees with the drive's reading

Prints "RESULT: N passed, M failed"; hermetic except section 6.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import diag_cooldowncheck as dcc
import diag_hygiene as dh

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


print("== 1. anchors: the policy the diag executes ==")
check(1.1, isinstance(dcc.LAST_GOOD, str) and len(dcc.LAST_GOOD) >= 7,
      "the drift anchor is a commit hash")
check(1.2, all("*" in pat or pat.endswith(".jsonl")
               for pat in dcc.REPORT_CLASSES),
      "the report classes are glob patterns (name classes, not contents)")
_hy = (HERE / "COOLDOWN_AGENDA.md").read_text(encoding="utf-8")
check(1.3, "TOMORROW MORNING CHECKLIST" in _hy
      and _hy.count("Run stalewatch") >= 1
      and "RULE D alignment" in _hy,
      "the agenda the diag executes still carries the six ordered steps")

print("== 2. _fresh_reports: the recency window honors injected time ==")
try:
    with tempfile.TemporaryDirectory(prefix="cooldown_purge_") as td:
        tdp = Path(td)
        (tdp / "kraken_stale_purge_old.txt").write_text("names only\n",
                                                        encoding="utf-8")
        (tdp / "kraken_stale_purge_new.txt").write_text("names only\n",
                                                        encoding="utf-8")
        now = time.time()
        os.utime(tdp / "kraken_stale_purge_old.txt", (now - 3600,) * 2)
        os.utime(tdp / "kraken_stale_purge_new.txt", (now - 10,) * 2)
        fresh = dcc._fresh_reports("kraken_stale_purge_*.txt",
                                   reports_dir=tdp, now=now)
        check(2.1, fresh == ["kraken_stale_purge_new.txt"],
              "only the file inside the window is fresh")
        check(2.2, len(dcc._fresh_reports("kraken_stale_purge_*.txt",
                                          window_s=7200, reports_dir=tdp,
                                          now=now)) == 2,
              "a wider window admits both — the window is the dial")
finally:
    pass

print("== 3. _root_report_violations: home contract by name class ==")
try:
    with tempfile.TemporaryDirectory(prefix="cooldown_rep_") as td:
        root_a = Path(td) / "repo_a"
        root_b = Path(td) / "repo_b"
        rep = root_a / "reports"
        rep.mkdir(parents=True)
        root_b.mkdir()
        # the four classes: silent in reports/, flagged at a root
        (rep / "hygiene_summary_1.txt").write_text("v\n", encoding="utf-8")
        (rep / "kraken_stale_purge_1.txt").write_text("v\n", encoding="utf-8")
        (rep / "hygiene_swept_ledger.jsonl").write_text("", encoding="utf-8")
        (rep / "scratch_census_1.txt").write_text("v\n", encoding="utf-8")
        # root copies across BOTH roots: every one is a violation
        (root_a / "hygiene_summary_2.txt").write_text("v\n", encoding="utf-8")
        (root_b / "kraken_stale_purge_2.txt").write_text("v\n",
                                                         encoding="utf-8")
        # a non-class root file is NOT a violation (name-class precision)
        (root_a / "unrelated_report.txt").write_text("v\n", encoding="utf-8")
        v = dcc._root_report_violations([root_a, root_b])
        check(3.1, sorted(v) == ["hygiene_summary_2.txt",
                                 "kraken_stale_purge_2.txt"],
              f"both roots' class copies flag, non-class stays silent: {v}")
        # a RECUR-ring-free ledger: step 5's quiet path via the same seam
        rows = dh._ledger_load(rep / "hygiene_swept_ledger.jsonl")
        check(3.2, rows == [], "an empty ledger loads empty (fail-soft seam)")
finally:
    pass

print("== 4. _reborn_rule_d: the stuck-loop signal ==")
try:
    with tempfile.TemporaryDirectory(prefix="cooldown_reborn_") as td:
        root_a = Path(td) / "repo_a"
        root_a.mkdir()
        (root_a / "scout.db").write_bytes(b"")     # a fossil REBORN
        rows = [
            {"name": "scout.db", "rule": "RULE D", "ts": "t"},
            {"name": "popups.jsonl.lock", "rule": "RULE D", "ts": "t"},
            {"name": "_sbx_ghost.jsonl", "rule": "RULE A", "ts": "t"},
        ]
        reborn = dcc._reborn_rule_d(rows, [root_a])
        check(4.1, reborn == ["scout.db"],
              "the RULE D name present at a root flags; absent and "
              "non-RULE-D names stay quiet")
        (root_a / "scout.db").unlink()
        check(4.2, dcc._reborn_rule_d(rows, [root_a]) == [],
              "fossil removed -> quiet again (the recovery path)")
finally:
    pass

print("== 5. source pins: order, seams, one-brain, registration ==")
_src = (HERE / "diag_cooldowncheck.py").read_text(encoding="utf-8")
_rb = (HERE / "run_battery.py").read_text(encoding="utf-8")
# 5.1 the order rule: the six lowercase "step N:" section comments sit in
# ascending order in main (helper docstrings say "Step N:" capitalized —
# case + colon separates the structural markers from prose)
_order = [_src.find(f"step {n}:") for n in range(1, 7)]
check(5.1, all(_order[i] != -1 for i in range(6))
      and _order == sorted(_order),
      "steps 1..6 appear in ascending order in the source — the mandatory "
      "sequence is structural, not conventional")
# 5.2 the diag is a RUNNER, not a reimplementation
check(5.2, '_run("kraken_stalewatch.py"' in _src
      and '_run("diag_hygiene.py"' in _src
      and "dh.census_writers()" in _src and "dh._ledger_load()" in _src
      and "dh.KNOWN_DEAD_ARTIFACTS" in _src,
      "stalewatch and hygiene run as real processes; census/ledger/RULE D "
      "grade through diag_hygiene's own functions — one brain")
# 5.3 the output contract: PASS/FAIL per step, names never contents
check(5.3, "'PASS' if cond else 'FAIL'" in _src
      and "step {step}" in _src,
      "the per-step PASS/FAIL line shape is pinned")
check(5.4, "names only" in _src or "names/ages only" in _src,
      "the names-never-contents rule is stated in the diag's own contract")
# 5.5 fail-soft seams: subprocess timeouts exist on both spawns
check(5.5, '_run("kraken_stalewatch.py", 120)' in _src
      and '_run("diag_hygiene.py", 180)' in _src,
      "both spawned steps carry a timeout — a hung child cannot hang "
      "the checklist")
# 5.6 the registration (the battery's rotation owns the diag)
check(5.6, '"diag_cooldowncheck"' in _rb,
      "diag_cooldowncheck is registered in run_battery's DIAG_RUN")
# 5.7 the scope line exists (the agenda's own triage, honored in output)
check(5.7, "COMMITTED state" in _src and "outside this verdict" in _src,
      "the RULE D scope line (committed state; deferred files named) is "
      "part of the printed verdict")
# 5.8 the Sep-24 review hardening: a timed-out step is a FAIL, not a
# crash, and step 4 demands this run's hygiene summary actually LANDED
check(5.8, "TimeoutExpired" in _src and "returncode=-3" in _src
      and "TIMED OUT after" in _src,
      "a hung child reports its step FAIL and the checklist continues to "
      "the remaining steps")
check(5.9, 'hygiene_summary_*.txt' in _src
      and "this run's hygiene summary landed" in _src,
      "step 4 grades the positive evidence of reporting, not just the "
      "absence of root copies (rc 0 is not evidence of reporting)")

print("== 6. LIVE: the real diag runs and its verdict agrees ==")
_r = subprocess.run([str(dcc.PY), "-X", "utf8",
                     str(HERE / "diag_cooldowncheck.py")],
                    cwd=str(HERE), capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=420)
_live = _r.stdout or ""
check(6.1, _r.returncode == 0, f"live diag rc={_r.returncode}")
check(6.2, "COOLDOWN CHECKLIST: all" in _live,
      "the live run prints the all-green verdict line")
_n_checks = _src.count("    check(")   # the calls inside main()
check(6.3, _live.count("PASS  step") == _n_checks
      and "FAIL  step" not in _live,
      f"all {_n_checks} per-step checks PASS in the live run, zero FAILs "
      "(derived from the source — a new check updates the pin itself)")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")


def main():
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    sys.exit(main())
