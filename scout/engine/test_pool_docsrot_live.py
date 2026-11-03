"""test_pool_docsrot_live.py - THE POOL'S DOCS-ROT CONTRACT, THROUGH
THE BATTERY (Sep 26, registered from LIVE_CONTRACT_TEMPLATE.py).

The second live-contract entry (the recipe's first production use
after the CodeRabbit triage hardened the template): the pool's
docs-rot drive (test_readme_docs.py, 8 checks) pins that every
command, file, and version string the pool README names still exists
in the pool checkout. It runs HERE - through the POOL's own venv, in
ITS own checkout - so the Scout battery carries the pool's
documentation-honesty contract without ever importing across repos.

Constants follow the FIRST ENTRY's sibling-discovery style (CodeRabbit
triage, Sep 26: hardcoding this box's absolute paths would fail the
entry on any other checkout location) - POOL is derived from THIS
file's location, and the resolved paths keep their missing-prerequisite
FAIL.
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
POOL = HERE.parent / "monero_project"
DRIVE = POOL / "test_readme_docs.py"
# PER-PLATFORM VENV RESOLUTION (the pool-prefs wrapper's fix, mirrored:
# the round-3 review's minor — Windows keeps Scripts/, POSIX keeps
# bin/; a missing venv fails the pool drive honestly, a bare `python`
# would import the WRONG world)
_pool_py = POOL / ".venv" / "Scripts" / "python.exe"
if not _pool_py.exists():
    _pool_py = POOL / ".venv" / "bin" / "python"
POOL_PY = _pool_py
SKIP_NOTE = "pool docs-rot drive self-skipped (unexpected: it has no skip path)"

SKIP_EXIT = 42          # run_battery maps exit 42 (+ SKIP: line) to SKIP


def main() -> int:
    # RESOLVE against the CALLER's cwd (CodeRabbit triage, Sep 26):
    # the child chdirs to the drive's folder, so a sibling-relative
    # path like 'pool/test_drive.py' would be re-based and break
    drive = DRIVE.resolve()
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
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300, cwd=str(drive.parent))
    except subprocess.TimeoutExpired as e:
        # A hung pool drive is a FAIL DETAIL, never a bare traceback
        # (CodeRabbit minor, bf9c8da, Sep 28; template contract).
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
    # Checked FIRST: a skip IS a 0/0 RESULT, so the honest-skip branch
    # must answer before the empty-contract refusal below.
    last = [l for l in out.splitlines() if l.startswith("RESULT:")]
    if (p.returncode == 0
            and last and last[-1].strip() == "RESULT: 0 passed, 0 failed"
            and "SKIP:" in out):
        print(f"SKIP: {SKIP_NOTE}")
        return SKIP_EXIT
    # THE EMPTY-CONTRACT REFUSAL (CodeRabbit major, Sep 28, fixed same
    # day): exit 0 with a 0/0 RESULT and NO skip note is not a pass —
    # a drive that ran nothing just certified nothing. That shape is
    # FAILed loudly (the wrapper's own nonzero, the reason on stderr)
    # instead of masquerading as the child's success via a bare
    # `return p.returncode`.
    if (p.returncode == 0 and last
            and last[-1].strip() == "RESULT: 0 passed, 0 failed"):
        print("FAIL: the drive certified nothing - a 0/0 RESULT with no "
              "SKIP note is an empty contract, not a pass",
              file=sys.stderr)
        return 1
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
