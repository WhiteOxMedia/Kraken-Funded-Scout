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
key on the full signature - a final "RESULT: 0 passed, 0 failed" line
AND a "SKIP:" note in the output - because a green-but-empty run is
not a skip, and a skip that exits 0 is not a PASS (the first wrapper
version gated on the return code alone and turned a confirmed-down
server into a green checkmark: the exact silently-green outcome this
template exists to prevent).

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
    if not POOL_DRIVE.exists():
        print(f"SKIP: drive not found at {POOL_DRIVE}")
        return SKIP_EXIT
    if not POOL_PY.exists():
        print(f"SKIP: venv not provisioned ({POOL_PY}) - run setup_dev.py there")
        return SKIP_EXIT
    p = subprocess.run(
        [str(POOL_PY), "-X", "utf8", str(POOL_DRIVE)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, cwd=str(POOL_DRIVE.parent))
    out = (p.stdout or "")
    for line in out.splitlines():
        print(line, flush=True)          # the drive's contract, verbatim
    err = (p.stderr or "").strip()
    if err:
        print(err, flush=True)
    # THE SKIP SIGNATURE (both required - see the docstring):
    last = [l for l in out.splitlines() if l.startswith("RESULT:")]
    if (last and last[-1].strip() == "RESULT: 0 passed, 0 failed"
            and "SKIP:" in out):
        print(f"SKIP: {SKIP_NOTE}")
        return SKIP_EXIT
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
