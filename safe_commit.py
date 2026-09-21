"""SAFE COMMIT WRAPPER (RUNTIME_INVARIANTS.md).

Usage (instead of plain `git commit`):
    python safe_commit.py -m "message" [-m ...] [--no-verify] [...]

Refuses to commit when:
  - a git index lock exists (another git process holds the index)
  - the stash list is non-empty (stash use is banned while the scanner runs)
  - the watch ledger was written within the last 30 seconds (a position row
    is mid-write — the scanner could be auto-tracking or auto-closing RIGHT
    NOW; committing runtime state at that instant risks shipping a torn row)
  - the scanner lock names a live python pid AND the watch ledger changed in
    the last 5 minutes (still warm; requires --force to override)

Passes all remaining arguments through to git commit. The normal pre-commit
review still runs (unless --no-verify).
"""
import json
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
WATCH = os.path.join(BASE, "contest_watch.json")
LOCK = os.path.join(BASE, "scanner.lock")
GRACE_HARD = 30.0    # seconds — refuse outright
GRACE_WARM = 300.0   # seconds — refuse unless --force


def alive_python(pid):
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True).stdout
        return "python" in out.lower()
    except Exception:
        return False


def fail(msg):
    # ASCII only: the refusal must print even on a cp1252 console (an
    # unprintable character must never stop the guard from refusing)
    print("  [REFUSED] safe_commit: " + msg)
    print("     commit refused (RUNTIME_INVARIANTS.md). Re-run in a quiet "
          "moment, or use plain git commit if you truly must.")
    sys.exit(1)


def main():
    if "--force" in sys.argv:
        sys.argv.remove("--force")
        forced = True
    else:
        forced = False

    if not sys.argv[1:]:
        fail("no commit arguments given")

    if os.path.exists(os.path.join(BASE, ".git", "index.lock")):
        fail("an index lock exists — another git process is mid-operation")

    if os.path.exists(os.path.join(BASE, ".git", "refs", "stash")):
        fail("the stash list is non-empty — audit and clear stashes first")

    age = (time.time() - os.path.getmtime(WATCH)) if os.path.exists(WATCH) else None
    rows = []
    if os.path.exists(WATCH):
        try:
            with open(WATCH, "r", encoding="utf-8") as f:
                rows = [k for k, v in json.load(f).items() if v]
        except Exception:
            rows = ["<unreadable>"]

    if age is not None and age < GRACE_HARD:
        fail(f"the watch ledger was written {age:.0f}s ago "
             f"({len(rows)} open row(s): {', '.join(rows[:4])}) — mid-write")

    if age is not None and age < GRACE_WARM and not forced:
        pid = None
        try:
            with open(LOCK) as f:
                pid = int(f.read().strip().split("|")[0])
        except Exception:
            pass
        if pid and alive_python(pid):
            fail(f"the scanner (pid {pid}) is live and the watch ledger "
                 f"changed {age:.0f}s ago ({len(rows)} open row(s)) — "
                 "use --force only if you are sure")

    cmd = ["git", "commit", *sys.argv[1:]]
    print("  [OK] safe_commit: quiet enough — handing off to git commit ...")
    sys.exit(subprocess.run(cmd, cwd=BASE).returncode)


if __name__ == "__main__":
    main()
