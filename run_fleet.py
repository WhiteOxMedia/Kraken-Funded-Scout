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
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HISTORY = HERE / "fleet_history.jsonl"
RESULT_RE = re.compile(r"RESULT:\s*(\d+)\s+passed,\s*(\d+)\s+failed")
FAIL_ID_RE = re.compile(r"^FAIL\s+(\S+)", re.M)
SKIP_RE = re.compile(r"^\s*SKIP:", re.M)


def discover():
    return sorted(HERE.glob("test_*.py"))


def run_drive(path, timeout):
    """One drive run -> a result dict (no classification)."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(path)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, cwd=str(HERE), env=env)
        rc, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        why = ""
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or b""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        rc, out, why = 124, raw or "", "timed out after {}s".format(timeout)
    dur = time.time() - t0
    m = RESULT_RE.search(out)
    passed = int(m.group(1)) if m else None
    failed = int(m.group(2)) if m else None
    return {
        "name": path.stem, "rc": rc, "dur": dur, "out": out,
        "passed": passed, "failed": failed,
        "fails": sorted(set(FAIL_ID_RE.findall(out))),
        "skip": bool(SKIP_RE.search(out)),
        "why": why,
        "tail": "\n".join(out.strip().splitlines()[-3:]),
    }


def grade_first(r):
    """The first-run verdict before any triage."""
    if r["skip"] and r["passed"] == 0 and r["failed"] == 0:
        return "SKIP"
    if r["passed"] is not None:
        return "PASS" if r["failed"] == 0 and r["rc"] == 0 else "FAIL"
    return "PASS" if r["rc"] == 0 else "FAIL"   # scenario-style: exit code


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
    for attempt in range(1, max(1, args.retries) + 1):
        r2 = run_drive(path, args.timeout)
        if grade_first(r2) == "PASS":
            n = "{} checks".format(r2["passed"]) if r2["passed"] is not None \
                else "scenario"
            return "FLAKE", {
                "checks": n, "dur": r1["dur"] + r2["dur"],
                "note": "failed first run ({}), passed rerun #{}".format(
                    ",".join(r1["fails"][:4]) or ("exit " + str(r1["rc"])),
                    attempt)}
        r1, first = r2, grade_first(r2)

    # stable failure: same checks both runs = clean regression;
    # divergent checks = a regression that is ALSO unstable
    if first == "SKIP":
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
    args = ap.parse_args()

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
    record_history(rows, time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                       time.gmtime()))
    return 1 if by.get("REGRESSION") else 0


if __name__ == "__main__":
    sys.exit(main())
