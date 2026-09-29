r"""LIVE_CONTRACT_TEMPLATE.py - the fill-in template for registering a
pool drive as a Scout battery live-contract entry (Sep 26).

HOW TO USE: copy this file to the scout root as test_<name>.py (the
name must start with test_ - the battery discovers it automatically),
fill in the three ALL-CAPS constants, and add the wrapper's stem to
LIVE_ENTRIES in run_battery.py (live entries never run in --fast;
full and --slow lanes run them). That is the whole registration.

THE CONVENTION THIS ENCODES (established by test_pool_prefs_live.py,
the first entry): execute the REAL drive in ITS OWN repo through THAT
repo's venv, stream its contract output verbatim, and map its honest
self-skip onto the battery's SKIP vocabulary. The skip mapping MUST
key on the full signature - the drive EXITED 0, a final
"RESULT: 0 passed, 0 failed" line, AND a "SKIP:" note in the output -
because a green-but-empty run is not a skip, a skip that exits 0 is
not a PASS, and a run that prints the skip signature and then crashes
is a FAILURE, never a rewritten 42 (the first wrapper version gated on
the return code alone and turned a confirmed-down server into a green
checkmark: the exact silently-green outcome this template exists to
prevent). Prerequisites fail the entry too: a missing drive or venv
returns 1 (SKIP is reserved for the drive's own server-down verdict).
A hung drive is the same: subprocess.TimeoutExpired maps to rc 1 with
a named FAIL line and whatever the drive printed before it hung - a
bare traceback in the battery ledger is a contract violation
(CodeRabbit minor on the bf9c8da review, Sep 28).
Paths are RESOLVED against the caller's working directory before the
child runs - a sibling-relative POOL_DRIVE would otherwise break the
moment the child chdir'd into its parent's folder (CodeRabbit triage,
Sep 26).

Fill in:
  POOL_DRIVE  - absolute-or-sibling-relative path to the target drive
  POOL_PY     - path to the drive's OWN repo venv python
  SKIP_NOTE   - one human-readable line for the battery ledger's SKIP row
"""
import subprocess
import sys
from pathlib import Path

POOL_DRIVE = Path(r"C:\Users\birds\Workspace\monero_project\test_TODO.py")
POOL_PY = Path(r"C:\Users\birds\Workspace\monero_project\.venv\Scripts\python.exe")
SKIP_NOTE = "pool server down (wrapper self-skip)"

SKIP_EXIT = 42          # run_battery maps exit 42 (+ SKIP: line) to SKIP


def main() -> int:
    # RESOLVE against the CALLER's cwd (CodeRabbit triage, Sep 26):
    # the child chdirs to the drive's folder, so a sibling-relative
    # path like 'pool/test_drive.py' would be re-based and break
    drive = POOL_DRIVE.resolve()
    pool_py = POOL_PY.resolve()
    # PREREQUISITES FAIL, NOT SKIP: the battery must show FAIL when the
    # live contract cannot run at all - SKIP stays the drive's own
    # honest server-down verdict.
    if not drive.exists():
        print(f"FAIL: drive not found at {drive}", file=sys.stderr)
        return 1
    if not pool_py.exists():
        print(f"FAIL: venv not provisioned ({pool_py}) - run setup_dev.py there",
              file=sys.stderr)
        return 1
    try:
        p = subprocess.run(
            [str(pool_py), "-X", "utf8", str(drive)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300, cwd=str(drive.parent))
    except subprocess.TimeoutExpired as e:
        # A hung pool drive is a FAIL DETAIL, never a bare traceback: the
        # battery ledger must show a named verdict (CodeRabbit minor,
        # bf9c8da, Sep 28). Whatever the drive printed before it hung
        # rides along as evidence.
        print(f"FAIL: drive exceeded its 300s timeout - {drive}",
              file=sys.stderr)
        partial = e.stdout or ""
        if isinstance(partial, bytes):   # TimeoutExpired can carry the
            partial = partial.decode(    # raw bytes even under text=True
                "utf-8", errors="replace")   # (CodeRabbit minor, Sep 28)
        for line in (partial or "").splitlines():
            print(line, flush=True)
        return 1
    out = (p.stdout or "")
    for line in out.splitlines():
        print(line, flush=True)          # the drive's contract, verbatim
    err = (p.stderr or "").strip()
    if err:
        print(err, flush=True)
    # THE SKIP SIGNATURE (all three required - see the docstring):
    # exited 0, the 0/0 RESULT line, and a SKIP note in the output.
    # A 0/0 WITHOUT the note is a VACUOUS PASS, never a skip and
    # never a success (Sep 29 review minor): a drive that ran
    # nothing and asserted nothing fails loudly here.
    last = [l for l in out.splitlines() if l.startswith("RESULT:")]
    if (p.returncode == 0
            and last and last[-1].strip() == "RESULT: 0 passed, 0 failed"
            and "SKIP:" in out):
        print(f"SKIP: {SKIP_NOTE}")
        return SKIP_EXIT
    if p.returncode == 0 and last \
            and last[-1].strip() == "RESULT: 0 passed, 0 failed":
        print("FAIL: the drive reported 0 passed, 0 failed with no "
              "SKIP note — a vacuous run is a failure, not a pass")
        return 1
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
