"""run_fleet.py — THE ONE-COMMAND FLEET (Sep 26).

WHY THIS EXISTS: "run everything" was a remembered incantation — 23
drives in three batches at birth, then eyeballing which RESULT lines
were green. This is the one command that runs every drive in one pass
(the count is DISCOVERED, never stored — 28 today and growing),
triages any failure by RERUNNING it (a failure that passes on the
rerun is a flake; a failure that repeats is a regression), and prints
the coverage table — so a green fleet is EVIDENCE, not recollection.

WHAT IT RUNS
  Every test_*.py in the repo root (discovered, so new drives join
  automatically). RESULT-style drives are parsed for their
  "RESULT: N passed, M failed" line and their FAIL check ids;
  scenario-style drives (no RESULT line) are graded by exit code.

CLASSIFICATION (the flake-vs-regression triage)
  PASS        green on the first run
  SCENARIO    exit 0 with no RESULT line (prose-style drives)
  SKIP        the drive skipped itself (e.g. the live-server drive's
              down-probe). One cooldown retry (default 65s — the stats
              rate limiter's window) before accepting the skip: a
              fleet run must not silently skip a drive that raced a
              rate limiter it caused itself.
  FLAKE       failed the first run, PASSED the rerun. The first run's
              failing checks are listed — if the rerun's story is
              "different checks fail every time", that is instability,
              not a product regression.
  REGRESSION  failed BOTH runs. The failing checks from both runs are
              compared: the same checks twice = a stable, real
              failure; divergent checks = a regression that is ALSO
              unstable. Exit code 1 either way.

USAGE
  .venv/Scripts/python.exe run_fleet.py                 # full fleet
  .venv/Scripts/python.exe run_fleet.py --list          # show drives
  .venv/Scripts/python.exe run_fleet.py --only push prefs   # subset
  .venv/Scripts/python.exe run_fleet.py --timeout 600 --retries 1
  .venv/Scripts/python.exe run_fleet.py --fail-fast         # debug loop

--FAIL-FAST stops the fleet at the FIRST regression and prints that
drive's full output (the confirming run's stdout+stderr), then lists
every drive it did not run — the tight write-test loop. Flakes do NOT
stop it (they are warnings). The pre-commit gate keeps the full-fleet
default: a landing commit deserves the whole ledger.

Exit code 0 = no regressions (flakes are warnings, listed loudly).
Exit code 1 = at least one REGRESSION (or a drive that crashed both
runs). The runner itself touches nothing — every drive owns its own
sandbox, and the live-db hermeticity guarantees stay inside drives.

TIMING (fleet_timings.json + fleet_timings.jsonl, both gitignored)
  Every run writes a summary artifact: verdict counts, git identity,
  wall/drive/median/p90/max seconds, the ten-second offenders, and the
  five slowest drives. The .jsonl is one line per run — so median/p90
  DRIFT is recoverable after any log is deleted, and `--history` prints
  it (a delta only against the previous run of the SAME drive set: a
  subset run is a different measurement, never a regression).
  --no-timings suppresses the write; a write failure never fails the
  fleet.

HISTORY (fleet_history.jsonl, gitignored — it churns every run)
  Every classified drive appends one JSON line {ts, fleet, drive,
  status, checks, passed, dur, note}. `--history` reads the ledger
  and prints per-drive run counts, FLAKE and REGRESSION rates, and
  the last-seen status — so "this drive flakes every third run" stops
  being folklore and becomes a number. Corrupt lines are skipped with
  a warning, never fatal: telemetry must not take the fleet down.
"""
import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

# THE SHARED VOCABULARY (drivekit.py): every argv, every regex and
# the rerun triangle below are drivekit's — the fleet is its biggest
# consumer, not its owner, so a future runner can contract-free reuse
# the exact same grammar.
import drivekit  # noqa: E402

HERE = Path(__file__).resolve().parent
HISTORY = HERE / "fleet_history.jsonl"
# THE TIMING ARTIFACT (two views of one run, both gitignored):
#   fleet_timings.json   the latest run, complete and pretty — inspects well
#   fleet_timings.jsonl  one summary line per run — the DRIFT ledger
# The per-drive ledger above records each drive's seconds; neither it nor
# the printed table survives a deleted console log, so median/p90 drift
# used to be unrecoverable after the fact.
TIMINGS = HERE / "fleet_timings.json"
TIMINGS_LEDGER = HERE / "fleet_timings.jsonl"
KEEP_TIMINGS = 200          # ledger rotation: newest N lines stay


def _timings_archive():
    """The archive sits beside the ledger so repointing the ledger (the
    drives do, into a temp dir) repoints the archive with it."""
    return TIMINGS_LEDGER.with_name(
        TIMINGS_LEDGER.name.replace(".jsonl", ".archive.jsonl"))


def discover():
    return sorted(HERE.glob("test_*.py"))


def run_drive(path, timeout):
    """One drive run -> a result dict (no classification). The raw
    subprocess, the regex answer sheet, and the exit-124-on-timeout
    contract are drivekit.run_child — the exact fleet argv ever since
    the vocabulary moved into drivekit.py."""
    return drivekit.run_child(path, timeout, utf8_flag=True, cwd=HERE)


def grade_first(r):
    """The first-run verdict before any triage (drivekit.grade_first's
    exact fleet shape: a scenario-style drive grades by its exit code,
    and the zero-check hardening is the battery entry's, not this
    fleet's — its coverage table shows the counts it saw)."""
    return drivekit.grade_first(r, scenario_rc=(0,), scenario_label="PASS",
                                honest_zero_check=False)


def run_classified(path, args):
    """Run one drive with the rerun triage. Returns (status, info)."""
    r1 = run_drive(path, args.timeout)
    first = grade_first(r1)

    if first == "PASS":
        n = "{} checks".format(r1["passed"]) if r1["passed"] is not None \
            else "scenario"
        return "PASS", {"checks": n, "dur": r1["dur"], "note": ""}

    if first == "SKIP":
        # 429-aware: a fleet run trips the stats rate limiter itself;
        # give the skipped live drive one cooldown retry before
        # accepting the skip (the drive stays green on server-less
        # boxes — that honest skip must survive too).
        time.sleep(args.skip_cooldown)
        r2 = run_drive(path, args.timeout)
        if grade_first(r2) == "SKIP":
            return "SKIP", {"checks": "0/0", "dur": r1["dur"] + r2["dur"],
                            "note": "skipped twice (server down or limiter)"}
        r1 = r2
        first = grade_first(r1)
        if first == "PASS":
            n = "{} checks".format(r1["passed"]) if r1["passed"] is not None \
                else "scenario"
            return "PASS", {"checks": n, "dur": r1["dur"],
                            "note": "after cooldown retry (was skipped)"}

    # FAILED first run -> the rerun triage decides flake vs regression
    status, prev, r1, attempt = drivekit.triage(
        r1, lambda: run_drive(path, args.timeout), retries=args.retries,
        scenario_rc=(0,), scenario_label="PASS", honest_zero_check=False)
    if status == "FLAKE":
        n = "{} checks".format(r1["passed"]) if r1["passed"] is not None \
            else "scenario"
        return "FLAKE", {
            "checks": n, "dur": prev["dur"] + r1["dur"],
            "note": "failed first run ({}), passed rerun #{}".format(
                ",".join(prev["fails"][:4]) or ("exit " + str(prev["rc"])),
                attempt)}

    # stable failure: same checks both runs = clean regression;
    # divergent checks = a regression that is ALSO unstable
    if status == "SKIP":
        return "SKIP", {"checks": "0/0", "dur": r1["dur"],
                        "note": "flipped to skip on rerun — investigate"}
    return "REGRESSION", {
        "checks": ("{} checks".format(r1["passed"])
                   if r1["passed"] is not None else "no RESULT line"),
        "dur": r1["dur"],
        "note": "stable failure: " + (",".join(r1["fails"][:5])
                                      or r1["why"] or r1["tail"][-90:]),
        # the confirming run's full output — --fail-fast prints it
        "out": r1["out"]}


def record_history(rows, fleet_id):
    """One JSON line per classified drive. Appends even for
    regressions — a history that only remembers green runs would
    flatter the fleet. A single write() per line keeps concurrent
    gate runs from interleaving mid-line."""
    try:
        with HISTORY.open("a", encoding="utf-8") as f:
            for name, status, info in rows:
                checks = info["checks"]
                passed = (int(checks.split()[0])
                          if checks.endswith("checks") else 0)
                f.write(json.dumps({
                    "ts": fleet_id,
                    "drive": name, "status": status,
                    "checks": checks, "passed": passed,
                    "dur": round(info["dur"], 2),
                    "note": (info["note"] or "")[:160],
                }) + "\n")
    except OSError as exc:
        print("  (history not written: {})".format(exc))


def percentile(values, q):
    """Nearest-rank percentile — no interpolation, so a p90 is always a
    real drive's real time and never a synthetic point between two."""
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(1, math.ceil(q / 100.0 * len(ordered)))
    return ordered[min(k, len(ordered)) - 1]


def _git_identity():
    """sha/branch/dirty of the tree that produced these numbers. Best
    effort: telemetry must never be the reason a fleet run fails."""
    def _q(*a):
        try:
            p = subprocess.run(["git", "-C", str(HERE), *a],
                               capture_output=True, text=True, timeout=15,
                               check=False)
        except (OSError, subprocess.SubprocessError):
            return ""
        return p.stdout.strip() if p.returncode == 0 else ""
    commit = _q("rev-parse", "HEAD")
    return {"commit": commit, "commit_short": commit[:12] or None,
            "branch": _q("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(_q("status", "--porcelain"))}


def fleet_summary(rows, fleet_id, wall_s, timeout, only=None,
                  partial=False):
    """The machine-readable shape of one run: verdict counts plus the
    timing distribution that the printed table cannot preserve."""
    secs = [float(i["dur"]) for _, _, i in rows]
    by = {}
    for _, status, _ in rows:
        by[status] = by.get(status, 0) + 1
    checks = sum(int(i["checks"].split()[0]) for _, _, i in rows
                 if i["checks"].endswith("checks"))
    slow = sorted(rows, key=lambda r: -float(r[2]["dur"]))[:5]
    return {
        "schema": 1,
        "ts": fleet_id,
        # a subset run is a DIFFERENT measurement: drift is only compared
        # against runs with the same set signature
        "set": "only:" + ",".join(only) if only else "full",
        "partial": bool(partial),
        "timeout": timeout,
        "git": _git_identity(),
        "drives": {"total": len(rows), "pass": by.get("PASS", 0),
                   "flake": by.get("FLAKE", 0), "skip": by.get("SKIP", 0),
                   "regression": by.get("REGRESSION", 0), "checks": checks},
        "timing": {
            "wall_s": round(wall_s, 2),
            "drive_total_s": round(sum(secs), 2),
            "median_s": round(percentile(secs, 50), 2),
            "p90_s": round(percentile(secs, 90), 2),
            "max_s": round(max(secs), 2) if secs else 0.0,
            "max_drive": slow[0][0] if slow else None,
            "over_10s": [n for n, _s, i in rows if float(i["dur"]) > 10],
        },
        "slowest": [{"drive": n, "secs": round(float(i["dur"]), 2)}
                    for n, _s, i in slow],
        "rows": [{"drive": n, "status": s, "checks": i["checks"],
                  "secs": round(float(i["dur"]), 2)}
                 for n, s, i in rows],
    }


def rotate_timings(keep=KEEP_TIMINGS):
    """Keep the newest `keep` ledger lines; move the rest to the archive.

    Policy: the ACTIVE ledger is what `--history` reads, so it stays a
    bounded, fast file (200 runs of one-line summaries). Overflow is
    APPENDED to fleet_timings.archive.jsonl — never deleted — so the
    long history survives for a later audit.

    Safety: the archive append happens FIRST, then the ledger is replaced
    atomically (tmp + replace). A crash or a full disk leaves the ledger
    untouched with the overflow already archived, so the worst case is a
    duplicated line, never a lost or truncated one. Returns the number of
    lines moved."""
    if keep <= 0:
        return 0
    try:
        raw = TIMINGS_LEDGER.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    lines = [ln for ln in raw if ln.strip()]
    if len(lines) <= keep:
        return 0
    overflow, keepset = lines[:-keep], lines[-keep:]
    try:
        with _timings_archive().open("a", encoding="utf-8") as f:
            f.write("\n".join(overflow) + "\n")
        tmp = TIMINGS_LEDGER.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(keepset) + "\n", encoding="utf-8")
        tmp.replace(TIMINGS_LEDGER)
    except OSError as exc:
        print("  (timing ledger not rotated: {})".format(exc))
        return 0
    return len(overflow)


def write_timings(summary, keep=None):
    """Append the drift ledger line, rotate, then rewrite the latest-run
    JSON atomically (tmp + replace) so a reader never sees half a file."""
    try:
        with TIMINGS_LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(summary, separators=(",", ":")) + "\n")
        rotate_timings(KEEP_TIMINGS if keep is None else keep)
        tmp = TIMINGS.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(summary, indent=1), encoding="utf-8")
        tmp.replace(TIMINGS)
        return True
    except OSError as exc:
        print("  (timing artifact not written: {})".format(exc))
        return False


def read_timings():
    """Parsed ledger, oldest first. Corrupt lines are skipped with a
    count — telemetry must never take the fleet down."""
    if not TIMINGS_LEDGER.exists():
        return [], 0
    entries, corrupt = [], 0
    for line in TIMINGS_LEDGER.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            corrupt += 1
            continue
        # valid JSON that is not an object (`[]`, `"x"`) is corruption
        # too — appending it would make every later .get() raise
        if not isinstance(e, dict):
            corrupt += 1
            continue
        entries.append(e)
    return entries, corrupt


def show_timing_drift(last=10):
    """MEDIAN/P90 DRIFT: the fleet's own runtime over the recent runs,
    with a delta only against the previous run of the SAME drive set
    (a subset run is a different measurement, not a regression)."""
    entries, corrupt = read_timings()
    if not entries:
        print("\nno timing history yet — run the fleet first ({} missing)"
              .format(TIMINGS_LEDGER.name))
        return
    print("\nFLEET TIMING (last {} run(s)){}".format(
        min(last, len(entries)),
        ", {} corrupt line(s) skipped".format(corrupt) if corrupt else ""))
    # ASCII only: this table is printed on cp1252 Windows consoles too,
    # where a Greek delta raises UnicodeEncodeError and takes --history
    # down with it.
    print("{:<20} {:<14} {:>6} {:>8} {:>8} {:>7} {:>7} {:>8}  {}".format(
        "RUN", "SET", "DRIVES", "WALL", "TOTAL", "MEDIAN", "P90", "MAX", "DELTA"))
    print("-" * 96)
    prev_by_set = {}
    for e in entries[-last:]:
        # shape-guarded: a hand-edited or half-written line must not take
        # the whole report down (telemetry is never load-bearing)
        t = e.get("timing") if isinstance(e.get("timing"), dict) else {}
        dr = e.get("drives") if isinstance(e.get("drives"), dict) else {}
        sig = (e.get("set"), e.get("timeout"))
        delta = "-"
        prev = prev_by_set.get(sig)
        if prev is not None:
            d = t.get("wall_s", 0) - prev
            delta = "{:+.1f}s".format(d)
        prev_by_set[sig] = t.get("wall_s", 0)
        print("{:<20} {:<14} {:>6} {:>7.1f}s {:>7.1f}s {:>6.1f}s {:>6.1f}s "
              "{:>7.1f}s  {}{}".format(
                  e.get("ts", "?"), (e.get("set") or "full")[:14],
                  dr.get("total", 0),
                  t.get("wall_s", 0), t.get("drive_total_s", 0),
                  t.get("median_s", 0), t.get("p90_s", 0),
                  t.get("max_s", 0), delta,
                  "  (partial)" if e.get("partial") else ""))


def show_history():
    """THE FLAKE-RATE LEDGER: per-drive run counts and outcome rates
    across every recorded fleet run."""
    if not HISTORY.exists():
        print("no history yet — run the fleet first ({} missing)"
              .format(HISTORY.name))
        return 0
    per = {}
    corrupt = 0
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
            d = per.setdefault(e["drive"], {"runs": 0, "FLAKE": 0,
                                            "REGRESSION": 0,
                                            "SKIP": 0, "last": ""})
            d["runs"] += 1
            if e["status"] in ("FLAKE", "REGRESSION", "SKIP"):
                d[e["status"]] += 1
            d["last"] = e["status"]
        except (ValueError, KeyError):
            corrupt += 1
    if not per:
        print("history is empty")
        return 0
    print("{:<34} {:>5} {:>7} {:>12} {:>6}  {}".format(
        "DRIVE", "RUNS", "FLAKE%", "REGRESSION%", "SKIP", "LAST"))
    print("-" * 84)
    for name, d in sorted(per.items(),
                          key=lambda kv: (-(kv[1]["FLAKE"] + kv[1]["REGRESSION"]),
                                          kv[0])):
        fr = 100.0 * d["FLAKE"] / d["runs"]
        rr = 100.0 * d["REGRESSION"] / d["runs"]
        print("{:<34} {:>5} {:>6.1f}% {:>11.1f}% {:>6}  {}".format(
            name, d["runs"], fr, rr, d["SKIP"], d["last"]))
    total = sum(d["runs"] for d in per.values())
    print("-" * 84)
    print("{} drive(s), {} classified runs{}".format(
        len(per), total,
        ", {} corrupt line(s) skipped".format(corrupt) if corrupt else ""))
    show_timing_drift()
    return 0


def main():
    ap = argparse.ArgumentParser(description="Run the full drive fleet "
                                 "with flake-vs-regression triage.")
    ap.add_argument("--only", nargs="*", default=None,
                    help="substring filters on drive names")
    ap.add_argument("--timeout", type=int, default=300,
                    help="per-drive timeout seconds (default 300)")
    ap.add_argument("--retries", type=int, default=2,
                    help="reruns before calling a failure a regression")
    ap.add_argument("--skip-cooldown", type=int, default=65,
                    help="seconds to wait before retrying a skipped drive")
    ap.add_argument("--list", action="store_true", help="list and exit")
    ap.add_argument("--history", action="store_true",
                    help="per-drive flake/regression rates over all "
                         "recorded runs, then exit")
    ap.add_argument("--fail-fast", action="store_true",
                    help="stop at the first regression and print that "
                         "drive's full output")
    ap.add_argument("--no-timings", action="store_true",
                    help="do not write the timing artifact "
                         "(fleet_timings.json / .jsonl)")
    args = ap.parse_args()
    t_start = time.time()

    drives = discover()
    if args.only:
        drives = [d for d in drives
                  if any(f in d.stem for f in args.only)]
    if args.list:
        for d in drives:
            print(" ", d.name)
        print("FLEET: {} drives".format(len(drives)))
        return 0
    if args.history:
        return show_history()
    if not drives:
        print("no drives matched")
        return 1

    print("THE FLEET: {} drives, rerun triage on{} timeout {}s"
          .format(len(drives),
                  ", FAIL-FAST on," if args.fail_fast else ",",
                  args.timeout))
    rows = []
    stopped_at = None
    for i, d in enumerate(drives, 1):
        print("[{}/{}] {} …".format(i, len(drives), d.name), flush=True)
        status, info = run_classified(d, args)
        rows.append((d.name, status, info))
        print("    {}  {}  ({:.1f}s)  {}".format(
            status, info["checks"], info["dur"], info["note"] or "-"),
            flush=True)
        if status == "REGRESSION" and args.fail_fast:
            stopped_at = i          # flakes and skips never stop the run
            break

    if stopped_at is not None:
        name, _st, info = rows[-1]
        print("\n" + "=" * 100)
        print("FAIL-FAST: {} REGRESSED — full output of the confirming run:"
              .format(name))
        print("=" * 100)
        print(info.get("out") or "(no captured output)")
        print("=" * 100)
        rest = [d.name for d in drives[stopped_at:]]
        if rest:
            print("FAIL-FAST: {} drive(s) not run: {}".format(
                len(rest), ", ".join(rest)))

    # THE COVERAGE TABLE
    print("\n{:<34} {:<11} {:<12} {:>7}  {}".format(
        "DRIVE", "STATUS", "CHECKS", "SECS", "NOTE"))
    print("-" * 100)
    order = {"REGRESSION": 0, "FLAKE": 1, "SKIP": 2, "PASS": 3}
    for name, status, info in sorted(rows, key=lambda x: order[x[1]]):
        print("{:<34} {:<11} {:<12} {:>7.1f}  {}".format(
            name, status, info["checks"], info["dur"],
            (info["note"] or "-")[:46]))

    by = {}
    for _, s, _ in rows:
        by[s] = by.get(s, 0) + 1
    checks_p = sum(int(i["checks"].split()[0]) for _, _, i in rows
                   if i["checks"].endswith("checks"))
    print("-" * 100)
    print("FLEET RESULT: {} drives | {} checks passed | PASS {} | FLAKE {} "
          "| SKIP {} | REGRESSION {}".format(
              len(rows), checks_p, by.get("PASS", 0), by.get("FLAKE", 0),
              by.get("SKIP", 0), by.get("REGRESSION", 0)))
    for name, status, info in rows:
        if status == "REGRESSION":
            print("  REGRESSION -> {}: {}".format(name, info["note"]))
    fleet_id = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record_history(rows, fleet_id)

    # THE TIMING ARTIFACT: the table above dies with the console; this
    # does not. Median/p90 are computed from the same rows the table
    # printed, so the two can never disagree.
    summary = fleet_summary(rows, fleet_id, time.time() - t_start,
                            args.timeout, only=args.only,
                            partial=stopped_at is not None)
    t = summary["timing"]
    print("TIMING: wall {:.1f}s | drives {:.1f}s | median {:.1f}s | p90 "
          "{:.1f}s | max {:.1f}s ({}){}".format(
              t["wall_s"], t["drive_total_s"], t["median_s"], t["p90_s"],
              t["max_s"], t["max_drive"] or "-",
              "" if args.no_timings else " | " + TIMINGS.name))
    if not args.no_timings:
        write_timings(summary)
    return 1 if by.get("REGRESSION") else 0


if __name__ == "__main__":
    sys.exit(main())
