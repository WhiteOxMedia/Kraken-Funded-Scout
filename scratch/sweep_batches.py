"""Foreground batch runner for the committed-tree fleet re-run (Sep 30).

Architecture that survives this environment: NO background processes.
Each invocation = one foreground tool call that judges a pre-packed
batch of drives, sized from the committed journal's measured durations
to fit a 600s tool window with headroom. Sequential calls walk the
fleet; --resume tracks what's done in the journal; each window heals
the sandbox (a kill between mutation and restore leaves a mutant),
re-applies the 4 HEAD blobs (make_sandbox copies the WORKING tree,
which carries the staged task-61 work — the overlay makes every batch
judge 91c1828), and clears the sweep's report file.

Usage: python scratch/sweep_batches.py <batch_number>   # 0-based
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.getcwd())
import diag_mutation_sweep as dms   # noqa: E402

JOURNAL = "artifacts/mutation_sweep/journal.json"
REPORT = "artifacts/mutation_sweep/mutation_sweep_report.txt"
OVERLAY = ("hooks/pre-commit", "test_commitgate.py",
           "diag_mutation_sweep.py",
           "artifacts/mutation_sweep/vacuous_triage.md")
BUDGET = 470   # seconds of sweep work per window (fits 600s call)


def batch_plan() -> list[list[str]]:
    """Pack the not-yet-judged drives into BUDGET-sized batches,
    durations from the committed journal (fallback 120s each)."""
    ref = json.load(open(os.path.expandvars(
        r"%LOCALAPPDATA%\Temp\journal_committed_backup.json"),
        encoding="utf-8"))["results"]
    HEAVY_PHASED = {"test_apptest.py"}   # window-hostile: judged by
                                         # scratch/apptest_phase.py

    def cost(drive: str) -> float:
        e = ref.get(drive, {})
        if "baseline" not in e:
            return 150.0
        c = e["baseline"].get("secs", 60.0)
        for v in e.get("mutation", {}).values():
            c += v.get("secs", 0.0)
        return c

    done = set()
    if os.path.exists(JOURNAL):
        done = set(json.load(open(JOURNAL, encoding="utf-8"))["results"])
    all_drives = sorted(p.name for p in dms.BASE.glob("test_*.py"))
    todo = [d for d in all_drives
            if d not in done and d not in HEAVY_PHASED]
    batches, cur, t = [], [], 0.0
    for d in todo:
        c = cost(d)
        if cur and t + c > BUDGET:
            batches.append(cur)
            cur, t = [], 0.0
        cur.append(d)
        t += c
    if cur:
        batches.append(cur)
    return batches


def main() -> int:
    n = int(sys.argv[1])
    batches = batch_plan()
    if n >= len(batches):
        print(f"batch {n}: nothing to do ({len(batches)} batches planned)")
        return 0
    drives = batches[n]

    # heal the sandbox, then make HEAD the judged tree
    subprocess.run([sys.executable, "-X", "utf8",
                    "scratch/sbx_integrity.py", "--fix"])
    if not os.path.exists(os.path.join("_mutation_sbx", "repo",
                                       "diag_mutation_sweep.py")):
        print("sandbox missing — building first")
        dms.make_sandbox()
    for f in OVERLAY:
        blob = subprocess.run(["git", "show", "HEAD:" + f],
                              capture_output=True).stdout
        if not blob:
            print(f"OVERLAY FAILED: {f}")
            return 1
        p = os.path.join("_mutation_sbx", "repo", f)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(blob)
    if os.path.exists(REPORT):
        os.remove(REPORT)

    print(f"batch {n}: {len(drives)} drives, ~{sum(0 for _ in drives)}s")
    env = {**os.environ, "SWEEP_REUSE_SBX": "1"}
    r = subprocess.run(
        [os.path.abspath(os.path.join(".venv", "Scripts", "python.exe")),
         "-X", "utf8", "diag_mutation_sweep.py",
         "--drives", ",".join(drives), "--resume", "--keep-sbx"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=560, env=env)
    print((r.stdout or "")[-1800:])
    print((r.stderr or "")[-400:])

    done_n = len(json.load(open(JOURNAL, encoding="utf-8"))["results"])
    print(f"journal now {done_n}/162")
    if done_n >= 162:
        subprocess.run([os.path.abspath(os.path.join(
            ".venv", "Scripts", "python.exe")), "-X", "utf8",
            "diag_mutation_sweep.py", "--all", "--resume", "--keep-sbx"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env)
        print("FLEET COMPLETE — report written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
