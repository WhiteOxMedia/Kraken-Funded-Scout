"""test_sweep_wiring.py - THE SWEEP-DISCIPLINE WIRING DRIVE (Oct 30).

The sweep discipline lives in tools/ (the Oct 29-30 root reorg):
sweep_db.py (the CLI), sweep_lib.py (the lib), the task registrar +
the two Task Scheduler wrappers. Four DIFFERENT consumers each assume
that location through a different mechanism, and no single failure
makes all of them loud on the same day:

  1. pool/scheduler.py inserts <repo>/tools into sys.path and imports
     sweep_lib + register_sweep_report_task from there,
  2. run_sweep_report_task.ps1 derives the repo from ITS OWN location
     ($MyInvocation) and invokes .venv python on tools/sweep_db.py,
  3. run_sweep_report.cmd cd's one directory up (%~dp0) and invokes
     tools\\sweep_db.py,
  4. register_sweep_report_task.py registers the wrapper by its own
     HERE (= tools/).

This drive pins the whole wiring so a future move (back to the root,
or into some other subdir) fails the fleet at the gate, loudly:

  1. THE FILES ARE IN tools/ (and ONLY there):
     the five discipline files exist under tools/ and none of them
     live at the repo root anymore - a root-side copy would shadow or
     fork the discipline depending on sys.path order, so ANY split
     fails this check.
  2. THE SCHEDULER'S sys.path CONTRACT IS REAL:
     statically: pool/scheduler.py still inserts <repo>/tools and
     imports sweep_lib. Dynamically, hermetically: a subprocess that
     replicates the scheduler's two inserts (_ROOT, then _ROOT/tools)
     imports both names and reports their __file__ - both MUST resolve
     under tools/. This is the loud check for "moved the files but
     forgot the consumers".
  3. THE WRAPPERS ASSUME THIS LEVEL:
     the ps1 must still derive $tools from $MyInvocation and reach the
     CLI at $repo/tools/sweep_db.py (it ONLY works housed one level
     down); the .cmd must still cd exactly one directory up and invoke
     tools\\sweep_db.py; the registrar must still point WRAPPER at its
     own directory's run_sweep_report_task.ps1.

Read-only: it never runs the CLI against the live DB (that contract
is test_sweep_cli's), never resolves a DB path, and never imports the
scheduler itself (its import-time work is not this drive's subject).
Skips honestly if pool/scheduler.py is absent. Prints
"RESULT: N passed, M failed".
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEDULER = HERE / "pool" / "scheduler.py"
TOOL_ROOT = HERE / "tools"
DISCIPLINE = ["sweep_db.py", "sweep_lib.py", "register_sweep_report_task.py",
              "run_sweep_report_task.ps1", "run_sweep_report.cmd"]
results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


if not SCHEDULER.is_file():
    print("SKIP: pool/scheduler.py not present - the wiring drive pins "
          "the pool checkout only")
    print("RESULT: 0 passed, 0 failed")
    sys.exit(0)

# ── 1. THE FILES ARE IN tools/ (AND ONLY THERE) ────────────────────
missing = [f for f in DISCIPLINE if not (TOOL_ROOT / f).is_file()]
check("1a the five discipline files live in tools/",
      not missing, f"absent from tools/: {missing}")

split = [f for f in DISCIPLINE if (HERE / f).exists()]
check("1b the repo root holds no discipline copy",
      not split, f"a root-side split shadows the sys.path order: {split}")

check("1c the CLI the wrappers call exists at tools/sweep_db.py",
      (TOOL_ROOT / "sweep_db.py").is_file(), "the CLI is the ps1 and cmd "
      "wrapper's hard-coded invocation point")

# ── 2. THE SCHEDULER'S sys.path CONTRACT ───────────────────────────
sched_src = SCHEDULER.read_text(encoding="utf-8", errors="replace")
check("2a the scheduler still inserts <repo>/tools into sys.path",
      'str(_ROOT / "tools")' in sched_src,
      "the tools/ insert is load-bearing: drop it and the weekly "
      "orphan leg loses sweep_lib + register_sweep_report_task")
check("2b the scheduler imports sweep_lib from that path",
      "import sweep_lib" in sched_src,
      "the import statement relied on by the weekly orphan leg")

probe = (
    "import sys; sys.path.insert(0, r'{root}'); "
    "sys.path.insert(0, r'{tools}'); "
    "import sweep_lib, register_sweep_report_task; "
    "print(sweep_lib.__file__); print(register_sweep_report_task.__file__)"
).format(root=str(HERE), tools=str(TOOL_ROOT))
r = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                   text=True, encoding="utf-8", timeout=60, cwd=str(HERE))
if r.returncode == 0 and r.stdout.count("\n") >= 2:
    resolved = [line.strip() for line in r.stdout.strip().splitlines()[:2]]
    off_tools = [p for p in resolved
                 if not p.startswith(str(TOOL_ROOT))]
    check("2c the scheduler's actual import resolves sweep_lib + the "
          "registrar from tools/",
          not off_tools, f"imported from outside tools/: {off_tools}")
else:
    check("2c the scheduler's actual import resolves sweep_lib + the "
          "registrar from tools/", False,
          f"the probe failed (rc={r.returncode}): {r.stderr.strip()[:200]}")

# ── 3. THE WRAPPERS ASSUME THIS LEVEL ─────────────────────────────
ps1 = (TOOL_ROOT / "run_sweep_report_task.ps1").read_text(
    encoding="utf-8", errors="replace")
check("3a the ps1 wrapper self-locates via its own invocation path",
      "Split-Path -Parent $MyInvocation.MyCommand.Path" in ps1
      and "Split-Path -Parent $tools" in ps1,
      "the wrapper reaches the repo one level up from its own location - "
      "move it and $repo drifts")
check("3b the ps1 wrapper invokes the CLI at <repo>/tools/sweep_db.py",
      "'tools'" in ps1 and "sweep_db.py" in ps1 and
      "Join-Path" in ps1,
      "the wrapper's CLI pin must point at the tools/ home")

cmd = (TOOL_ROOT / "run_sweep_report.cmd").read_text(
    encoding="utf-8", errors="replace")
check("3c the cmd wrapper cd's exactly one directory up",
      'cd /d "%~dp0.."' in cmd,
      '%~dp0.. only lands on the repo root when the .cmd itself is '
      "housed in tools/ (or any single level down)")
check("3d the cmd wrapper invokes tools\\sweep_db.py",
      "tools\\sweep_db.py" in cmd,
      "the .cmd's invocation must name the tools/ home")

reg = (TOOL_ROOT / "register_sweep_report_task.py").read_text(
    encoding="utf-8", errors="replace")
check("3e the task registrar points WRAPPER at its own directory's "
      "run_sweep_report_task.ps1",
      'WRAPPER = HERE / "run_sweep_report_task.ps1"' in reg,
      "the registered scheduled task runs the wrapper straight out of "
      "tools/ - a move breaks the registration, not just the file")

n_pass = results.count(True)
n_fail = results.count(False)
print(f"RESULT: {n_pass} passed, {n_fail} failed")
sys.exit(1 if n_fail else 0)
