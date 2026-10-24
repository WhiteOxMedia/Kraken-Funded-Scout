"""test_fleet_timings.py — THE TIMING ARTIFACT, PINNED (Oct 22).

CHARTER: the coverage table dies with the console. When a fleet log is
deleted (it happens), the answer to "is the gate getting slower?" died
with it — median and p90 were unrecorded folklore. run_fleet.py now
writes two gitignored artifacts per run:

  fleet_timings.json    the latest run, complete and pretty
  fleet_timings.jsonl   one summary line per run — the DRIFT ledger

`--history` prints the drift, comparing a run only against the previous
run of the SAME drive set (a `--only` subset is a different measurement,
never a regression).

This drive pins the CONTRACT without running the fleet — the numbers are
synthetic, the paths are a temp dir, and nothing in the repo is touched:

  1. PERCENTILES: nearest-rank, so a p90 is a real observation rather
     than a point interpolated between two. Empty and single-value lists
     are defined, not accidental.
  2. THE SUMMARY: counts, check total, drive seconds, median/p90/max,
     the >10s offenders, the slowest five, the set signature, the
     partial flag, and the git identity.
  3. THE ARTIFACTS: one ledger line per run, latest JSON rewritten
     atomically, append order preserved, corrupt lines tolerated.
  4. TELEMETRY NEVER FAILS THE FLEET: an unwritable path prints a note
     and returns False instead of raising.
  5. THE ARTIFACTS STAY OUT OF GIT: both names are ignored, so a run
     cannot dirty the tree.
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_fleet  # noqa: E402

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}" + (f"  ({why})" if why and not cond else ""))


def row(name, status, dur, checks="3 checks", note=""):
    return (name, status, {"checks": checks, "dur": dur, "note": note})


print("== 1. PERCENTILES (nearest-rank) ==")
check(1.1, run_fleet.percentile([1, 2, 3, 4], 50) == 2,
      f"p50 of 1..4 = {run_fleet.percentile([1, 2, 3, 4], 50)} (expected 2)")
check(1.2, run_fleet.percentile([1, 2, 3, 4], 90) == 4,
      f"p90 of 1..4 = {run_fleet.percentile([1, 2, 3, 4], 90)} (expected 4)")
check(1.3, run_fleet.percentile(list(range(1, 11)), 90) == 9,
      f"p90 of 1..10 = {run_fleet.percentile(list(range(1, 11)), 90)} (expected 9)")
check(1.4, run_fleet.percentile([], 90) == 0 and run_fleet.percentile([7], 90) == 7,
      "empty/single-value lists are not defined")
check(1.5, run_fleet.percentile([9, 1, 5], 50) == 5,
      "input order leaked into the percentile (must sort first)")

print("== 2. THE SUMMARY ==")
rows = [row("test_fast.py", "PASS", 0.2),
        row("test_mid.py", "PASS", 6.0),
        row("test_slow.py", "PASS", 52.2),
        row("test_bad.py", "REGRESSION", 12.0, checks="no RESULT line",
            note="stable failure: 4.4")]
s = run_fleet.fleet_summary(rows, "2026-10-22T00:00:00Z", wall_s=75.5,
                            timeout=300, only=None)
d, t = s["drives"], s["timing"]
check(2.1, d["total"] == 4 and d["pass"] == 3 and d["regression"] == 1,
      f"counts: {d}")
check(2.2, d["checks"] == 9, f"check total {d['checks']} (3+3+3+0 expected)")
check(2.3, abs(t["drive_total_s"] - 70.4) < 0.01, f"drive total {t['drive_total_s']}")
check(2.4, t["max_s"] == 52.2 and t["max_drive"] == "test_slow.py",
      f"max: {t['max_s']} on {t['max_drive']}")
check(2.5, t["median_s"] == 6.0,
      f"median over [0.2, 6, 52.2, 12] = {t['median_s']} (expected 6.0)")
check(2.6, t["over_10s"] == ["test_slow.py", "test_bad.py"],
      f">10s offenders: {t['over_10s']}")
check(2.7, [x["drive"] for x in s["slowest"]][:2] == ["test_slow.py", "test_bad.py"],
      f"slowest order: {[x['drive'] for x in s['slowest']]}")
check(2.8, s["set"] == "full" and s["partial"] is False,
      f"set signature: {s['set']!r} partial={s['partial']!r}")
check(2.9, (s["git"].get("commit") or "") != "",
      "git identity is empty in a checkout with HEAD")

sub = run_fleet.fleet_summary(rows, "2026-10-22T00:00:01Z", 10.0, 300,
                              only=["push", "prefs"], partial=True)
check("2.10", sub["set"] == "only:push,prefs" and sub["partial"] is True,
      f"subset/partial signature: {sub['set']!r} {sub['partial']!r}")

print("== 3. THE ARTIFACTS ==")
_tmp = Path(tempfile.mkdtemp(prefix="fleet_timings_"))
_saved = (run_fleet.TIMINGS, run_fleet.TIMINGS_LEDGER)
run_fleet.TIMINGS = _tmp / "fleet_timings.json"
run_fleet.TIMINGS_LEDGER = _tmp / "fleet_timings.jsonl"
try:
    check(3.1, run_fleet.write_timings(s) is True, "write_timings refused a good path")
    check(3.2, run_fleet.TIMINGS.exists() and run_fleet.TIMINGS_LEDGER.exists(),
          "one of the two artifacts is missing")
    latest = json.loads(run_fleet.TIMINGS.read_text(encoding="utf-8"))
    check(3.3, latest["timing"]["median_s"] == 6.0 and latest["ts"] == s["ts"],
          "the latest-run JSON does not carry the run it was given")

    run_fleet.write_timings(sub)
    lines = [l for l in run_fleet.TIMINGS_LEDGER.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    check(3.4, len(lines) == 2, f"ledger has {len(lines)} lines (expected 2)")
    check(3.5, json.loads(lines[1])["set"] == "only:push,prefs",
          "append order is not preserved")
    check(3.6, json.loads(run_fleet.TIMINGS.read_text(encoding="utf-8"))["set"]
          == "only:push,prefs", "the latest JSON was not rewritten by the second write")
    check(3.7, not (run_fleet.TIMINGS.with_suffix(".json.tmp")).exists(),
          "the atomic tmp file was left behind")

    with run_fleet.TIMINGS_LEDGER.open("a", encoding="utf-8") as f:
        f.write("{not json\n")
    entries, corrupt = run_fleet.read_timings()
    check(3.8, len(entries) == 2 and corrupt == 1,
          f"corrupt line handling: {len(entries)} entries, {corrupt} corrupt")

    # valid JSON that is not an object: appending it used to hand every
    # later .get() a list and crash the report
    with run_fleet.TIMINGS_LEDGER.open("a", encoding="utf-8") as f:
        f.write("[1,2]\n")
    entries, corrupt = run_fleet.read_timings()
    check(3.9, len(entries) == 2 and corrupt == 2,
          f"non-object line handling: {len(entries)} entries, {corrupt} corrupt")

    # ...and a well-formed line with malformed FIELDS must not crash the
    # drift report either: telemetry is never load-bearing
    with run_fleet.TIMINGS_LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-10-22T00:00:02Z", "set": "full",
                            "timing": "oops", "drives": [1]}) + "\n")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_fleet.show_timing_drift()
    check("3.10", "FLEET TIMING" in buf.getvalue(),
          f"a malformed timing block crashed the report: {buf.getvalue()[:200]}")
finally:
    run_fleet.TIMINGS, run_fleet.TIMINGS_LEDGER = _saved

print("== 4. TELEMETRY NEVER FAILS THE FLEET ==")
blocked = _tmp / "blocked"
blocked.mkdir()
run_fleet.TIMINGS = _tmp / "never.json"
run_fleet.TIMINGS_LEDGER = blocked          # a directory, not a file
try:
    ok = run_fleet.write_timings(s)
except Exception as exc:                    # noqa: BLE001 - the point of the check
    ok = f"raised {exc!r}"
check(4.1, ok is False, f"an unwritable ledger did not fail soft: {ok!r}")
check(4.2, not (_tmp / "never.json").exists(),
      "a failed ledger write still produced a latest-run file")
run_fleet.TIMINGS, run_fleet.TIMINGS_LEDGER = _saved

print("== 5. THE ARTIFACTS STAY OUT OF GIT ==")
ignore = (HERE / ".gitignore").read_text(encoding="utf-8")
for name in ("fleet_timings.json", "fleet_timings.jsonl"):
    check(f"5.{name}", name in ignore, f"{name} is not in .gitignore")
source = (HERE / "run_fleet.py").read_text(encoding="utf-8")
check("5.flag", "--no-timings" in source and "write_timings(summary)" in source,
      "the run does not write the artifact (or cannot be told not to)")

n_fail = results.count(False)
print(f"\nRESULT: {len(results) - n_fail} passed, {n_fail} failed")
sys.exit(1 if n_fail else 0)
