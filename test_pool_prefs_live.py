"""test_pool_prefs_live.py - THE POOL'S PREFS LIVE CONTRACT, THROUGH
THE BATTERY (Sep 26).

A thin, explicit discovery-wrapper entry: the REAL prefs live-server
drive lives in the sibling monero_project checkout (the Scout repo's
run_battery never network-imports across repos - cross-repo suites
stay ad-hoc by design), so this entry EXECUTES it there - through the
POOL's own venv (the drive imports the pool's fastapi/starlette; the
scout venv does not carry them) - and maps the two honest outcomes
onto the battery's vocabulary:

  pool server up   -> the drive runs its real-HTTP contract and its
                      "RESULT: N passed, M failed" line parses as the
                      wrapper's own (exit 0 when green)
  pool server down -> the drive self-skips with
                      "RESULT: 0 passed, 0 failed"; the wrapper turns
                      that into exit 42, and run_battery's SKIP_RE
                      ledgers it as SKIP - visible, never a FAIL,
                      never silently green

Anything else (real failures, a missing checkout, a broken pool venv)
keeps the underlying exit code and output: FAIL, loudly.

Sibling layout (both repos side by side in the workspace):
  ../monero_project/test_prefs_live_server.py
  ../monero_project/.venv/Scripts/python.exe
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
POOL = HERE.parent / "monero_project"
DRIVE = POOL / "test_prefs_live_server.py"
POOL_PY = POOL / ".venv" / "Scripts" / "python.exe"

# the wrapper's only exit code of its own: 42 == "the target skipped
# itself" (the battery maps it to SKIP). Every other exit propagates.
SKIP_EXIT = 42


def main() -> int:
    if not DRIVE.exists():
        print(f"SKIP: pool checkout not found at {POOL}")
        return SKIP_EXIT
    if not POOL_PY.exists():
        print(f"SKIP: pool venv not provisioned ({POOL_PY.name} missing) - "
              "run python setup_dev.py in the pool checkout")
        return SKIP_EXIT
    p = subprocess.run(
        [str(POOL_PY), "-X", "utf8", str(DRIVE)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, cwd=str(POOL))
    out = (p.stdout or "")
    for line in out.splitlines():
        print(line, flush=True)          # the drive's contract, verbatim
    err = (p.stderr or "").strip()
    if err:
        print(err, flush=True)
    # THE SELF-SKIP SIGNATURE: the drive's honest skip exits 0 with
    # "RESULT: 0 passed, 0 failed" AND a "SKIP:" note — both required,
    # so a green-but-empty run can never be confused with a skip and
    # vice versa. (Found by live test: gating on returncode != 0 let
    # the skip through as a green PASS — the exact silently-green
    # outcome this wrapper exists to prevent.)
    last = [l for l in out.splitlines() if l.startswith("RESULT:")]
    if (last and last[-1].strip() == "RESULT: 0 passed, 0 failed"
            and "SKIP:" in out):
        return SKIP_EXIT
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
