"""drivekit.py — THE DRIVE-RUNNER VOCABULARY (Oct 28).

One home for the three contracts every drive-runner in this repo
carries, so run_fleet.py and the battery entry (and any future
runner) cannot drift apart:

  THE RESULT VOCABULARY: the fleet's exact parse — one `RESULT: N
  passed, M failed` line per drive output, FAIL check ids by prefix,
  SKIP by marker — plus run_child(), which turns one spawned drive
  into the parsed-but-unclassified run dict both callers consume.
  The regex is NOT line-anchored on purpose: the battery's
  child-echo lowercase rule ("child result:") and any future runner
  that echoes sub-results must keep a sub-run's line from
  masquerading as the runner's own fleet line.

  THE CHILD ENCODING CONTRACT: children run with PYTHONIOENCODING=
  utf-8 in their environment and are decoded utf-8 with errors
  REPLACED — a drive's Unicode facts (em-dashes, check names) must
  survive any console they land in; a runner that only works under
  some consoles is not a runner. configure_console() is the parent
  side of the same contract: the runner's own stdout/stderr speak
  utf-8/replace before it prints any child facts.

  THE RERUN TRIAGE: grade the first run; a failure that passes on
  the rerun is a FLAKE (a warning, listed with the failing run's
  checks), a failure that repeats is a REGRESSION (the confirming
  run's evidence is the classification's). triage() is the loop the
  fleet and the battery share; the caller keeps only its own
  presentation strings and status labels.

Nothing here knows which drives exist: a runner supplies paths,
timeouts, and its own scenario/zero-check policy through grade_first's
arguments; this module supplies the shared grammar.
"""
import os
import re
import subprocess
import sys
import time

# ── THE RESULT VOCABULARY ──────────────────────────────────────────
# The fleet's exact case-sensitive token. NOT line-anchored on
# purpose (see the module docstring); a runner that echoes sub-
# results must mangle the token first (the battery lowercases it).
RESULT_RE = re.compile(r"RESULT:\s*(\d+)\s*passed,\s*(\d+)\s*failed")
FAIL_ID_RE = re.compile(r"^FAIL\s+(\S+)", re.M)
SKIP_RE = re.compile(r"^\s*SKIP:", re.M)

TIMEOUT_RC = 124          # the classic "timed out" exit code


def parse_result(text):
    """(passed, failed) from a run's output text — (None, None) when
    there is no RESULT line (= prose/scenario-style, exit-graded)."""
    m = RESULT_RE.search(text)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def fail_ids(text):
    """The distinct FAIL check ids a run admitted, sorted."""
    return sorted(set(FAIL_ID_RE.findall(text)))


def is_skip(text):
    """True when the run printed the SKIP marker."""
    return bool(SKIP_RE.search(text))


# ── THE CHILD ENCODING CONTRACT ────────────────────────────────────
def configure_console():
    """The parent side: a runner's own stdout/stderr speak utf-8 with
    errors replaced, whatever the console (cp1252 on Windows loses
    em-dashes otherwise). A runner calls this before printing."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def child_env():
    """The child side: PYTHONIOENCODING=utf-8, so a drive's own prints
    survive the pipe before any parent decodes them."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return env


# ── THE RUN ────────────────────────────────────────────────────────
def run_child(path, timeout, interpreter=None, utf8_flag=False, cwd=None):
    """One child run -> a run dict (parsed, NOT graded):

        rc / dur / out / err / why   — the raw run (out = stdout,
                                       err = stderr, why = timeout note)
        passed / failed              — parse_result() of combined output
        fails / skip                 — fail_ids() and is_skip() of combined
        tail / name                  — the last 3 lines, the path stem

    `interpreter` is the argv prefix (the battery passes its venv
    python; the fleet passes [sys.executable, "-X", "utf8"], or simply
    sets utf8_flag=True for the same argv). A timeout exits TIMEOUT_RC
    carrying whatever stdout survived — it may name the check the
    timeout ate — and `why` explains it.
    """
    argv = list(interpreter) if interpreter else [sys.executable]
    if utf8_flag:
        argv += ["-X", "utf8"]
    argv.append(str(path))
    kwargs = {}
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    t0 = time.time()
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, env=child_env(), **kwargs)
        rc, out = proc.returncode, proc.stdout or ""
        err, why = proc.stderr or "", ""
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or b""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        rc, out, err = TIMEOUT_RC, raw or "", ""
        why = "timed out after {}s".format(timeout)
    dur = time.time() - t0
    combined = out + err
    passed, failed = parse_result(combined)
    return {
        "name": getattr(path, "stem", str(path)),
        "rc": rc, "dur": dur, "out": out, "err": err, "why": why,
        "passed": passed, "failed": failed,
        "fails": fail_ids(combined),
        "skip": is_skip(combined),
        "tail": "\n".join(combined.strip().splitlines()[-3:]),
    }


def counts(r):
    """None-normalized (passed, failed): a prose-style child counts as
    0/0 — the exit code says the rest."""
    return (r["passed"] if r["passed"] is not None else 0,
            r["failed"] if r["failed"] is not None else 0)


# ── THE FIRST-RUN VERDICT ──────────────────────────────────────────
def grade_first(r, scenario_rc=(2,), scenario_label="SCENARIO",
                honest_zero_check=True):
    """The verdict on ONE run, before any rerun:

      SKIP                       — the SKIP marker + an honest 0/0 line
                                   (a named row, never a silent green)
      REGRESSION (zero-check green) — honest-green discipline: an
                                   exit-0 RESULT line that passed zero
                                   checks is a broken drive, not a pass
      PASS                       — failed == 0 and rc == 0 (scored or
                                   scenario exits matching scenario_rc)
      FAIL                       — everything else

    `scenario_rc`/`scenario_label` shape the BASELINE: a green failed
    == 0 run that exits in scenario_rc takes scenario_label (the
    fleet passes (0,) + "PASS" — a scenario drive grades exactly like
    a scored one; the battery passes (2,) + "SCENARIO", the venv
    child's reference-exit convention). honest_zero_check is the
    battery's honest-green guard (the fleet runs False: its coverage
    table shows the counts it saw).
    """
    passed = r["passed"] if r["passed"] is not None else 0
    failed = r["failed"] if r["failed"] is not None else 0
    # 1. THE HONEST SKIP: a named row, never a silent green.
    if r["skip"] and passed == 0 and failed == 0:
        return "SKIP"
    # 2. A CLEAN EXIT: green at rc 0. A zero-check line is only a pass
    #    when the runner does not demand the honest-green discipline.
    if failed == 0 and r["rc"] == 0:
        if passed == 0 and honest_zero_check:
            return "REGRESSION (zero-check green)"
        return "PASS"
    # 3. THE SCENARIO BASELINE: clean on the runner's own exit.
    if failed == 0 and r["rc"] in scenario_rc:
        return scenario_label
    return "FAIL"


# ── THE RERUN TRIAGE ───────────────────────────────────────────────
def triage(first, run_once, retries=1, cooldown_s=0.0, scenario_rc=(2,),
           scenario_label="SCENARIO", honest_zero_check=True,
           on_rerun=None):
    """THE RERUN TRIAGE on a failed first run.

    Reruns up to `retries` times (cooldown_s between each). The first
    rerun that grades PASS stops the loop: classification becomes
    FLAKE, and because `first`'s evidence is the run the caller
    grades, prev is returned alongside last — the exact shape of the
    original vocabulary: prev's failing checks are the FLAKE note's
    evidence, last's shape is the REGRESSION note's (and the counts
    the battery re-grades the class from).

    Returns (status, prev, last, attempt) with status in FLAKE /
    SKIP / REGRESSION. A failure that repeats is a REGRESSION; a
    failure that flips to SKIP (not a pass!) keeps rerunning — only a
    green run stops the loop, per the fleet's own rule.

    The caller owns the presentation: first and prev stay in ITS
    hands for the note strings; the returned runs carry the facts.
    """
    prev, grade, attempt = first, "FAIL", 1
    for attempt in range(1, max(1, retries) + 1):
        if cooldown_s > 0:
            time.sleep(cooldown_s)
        if on_rerun is not None:
            on_rerun(attempt)
        last = run_once()
        grade = grade_first(last, scenario_rc=scenario_rc,
                            scenario_label=scenario_label,
                            honest_zero_check=honest_zero_check)
        if grade == "PASS":
            return "FLAKE", prev, last, attempt
        prev = last
    if grade == "SKIP":
        return "SKIP", prev, prev, attempt     # flipped to skip on rerun
    return "REGRESSION", prev, prev, attempt
