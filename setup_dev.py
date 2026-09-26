"""setup_dev.py — THE ONE-COMMAND NEW-CLONE SETUP (Sep 26).

A fresh clone needs exactly two things before it is a working
checkout, and both were tribal knowledge (the hook's own comment used
to say "set once per clone"):

  1. the venv          — uv.lock is the source of truth; `uv sync`
                         reproduces the working environment,
                         INCLUDING the push delivery stack (pywebpush
                         + py_vapid) and the at-rest encryption
                         layer (cryptography) — all three were once
                         undeclared ad-hoc installs that the first
                         run of this script exposed
  2. the fleet gate    — `git config core.hooksPath githooks` wires
                         githooks/pre-commit so every commit runs
                         run_fleet.py; hooks are versioned but git
                         only honors them after this per-clone setting

Then it VERIFIES the result instead of assuming: hooksPath readback,
the venv python's existence, the core pool modules importing through
the venv interpreter, and the drive fleet being discoverable.

STEPS (idempotent — safe to re-run any time; a re-run refreshes the
venv to the lock and re-asserts the hook):

  python setup_dev.py

Exit 0 = the clone is ready: ./test runs the fleet, commits run the
gate. Exit 1 = something failed; the step that failed says why.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"
HOOKS_DIR = HERE / "githooks"


def sh(cmd, **kw):
    """Run a command, returning (rc, output)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=str(HERE),
                           **kw)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except OSError as exc:
        return 127, str(exc)


def venv_python():
    """The venv interpreter, Windows layout first (the house OS), then
    POSIX — None when the venv does not exist yet."""
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python",
                ".venv/Scripts/python", ".venv/bin/python.exe"):
        p = HERE / rel
        if p.exists():
            return p
    return None


def step_venv():
    """Step 1: the virtual environment, from the lock."""
    has_uv = shutil.which("uv") is not None
    if has_uv:
        print("[1/3] venv: uv sync (uv.lock is the source of truth) …")
        rc, out = sh(["uv", "sync"])
        if rc != 0:
            print("FAIL  uv sync exited {}: {}".format(rc, out.strip()[-300:]))
            return False
        print("PASS  venv ready via uv sync")
        return True
    if venv_python() is not None:
        print("PASS  venv exists (uv not available; leaving it as-is)")
        return True
    print("[1/3] venv: uv not found — falling back to python -m venv …")
    rc, out = sh([sys.executable, "-m", "venv", str(VENV)])
    if rc != 0:
        print("FAIL  venv creation exited {}: {}".format(rc, out.strip()[-300:]))
        return False
    py = venv_python()
    if py is None:
        print("FAIL  venv created but no interpreter found in it")
        return False
    rc, out = sh([str(py), "-m", "pip", "install", "-e", str(HERE)])
    if rc != 0:
        print("FAIL  pip install -e . exited {}: {}".format(rc, out.strip()[-300:]))
        return False
    print("PASS  venv created and the project installed into it")
    return True


def step_hooks():
    """Step 2: point core.hooksPath at the versioned githooks/."""
    print("[2/3] hooks: git config core.hooksPath githooks …")
    if not (HERE / ".git").exists() and not HOOKS_DIR.exists():
        print("FAIL  not a git repository (and no githooks/ to wire)")
        return False
    rc, out = sh(["git", "config", "core.hooksPath", "githooks"])
    if rc != 0:
        print("FAIL  git config exited {}: {}".format(rc, out.strip()[-200:]))
        return False
    rc, out = sh(["git", "config", "core.hooksPath"])
    if rc != 0 or out.strip() != "githooks":
        print("FAIL  hooksPath readback: {!r}".format(out.strip()))
        return False
    print("PASS  core.hooksPath = githooks (the fleet gate rides every commit)")
    return True


def step_verify():
    """Step 3: verify the result — never assume."""
    print("[3/3] verify …")
    ok = True

    py = venv_python()
    if py is None:
        print("FAIL  no venv interpreter at .venv/")
        return False
    print("PASS  venv python: {}".format(py.relative_to(HERE)))

    # the core pool layer imports through the VENV interpreter — this
    # is the check that catches a stale/partial environment
    probe = ("import sys; sys.path.insert(0, 'pool'); "
             "import database, jackpot_engine, push_service, kraken_referral; "
             "print('DB:', database.DB_PATH.name)")
    rc, out = sh([str(py), "-X", "utf8", "-c", probe])
    if rc != 0:
        print("FAIL  core pool imports failed: {}".format(out.strip()[-300:]))
        ok = False
    else:
        print("PASS  pool core imports (database, jackpot_engine, "
              "push_service, kraken_referral) — {}".format(
                  out.strip().splitlines()[-1] if out.strip() else ""))

    # fastapi = the heavy dependency layer actually in the lock
    rc, out = sh([str(py), "-c", "import fastapi, uvicorn, keyring; print('deps ok')"])
    if rc != 0:
        print("FAIL  dependency imports failed: {}".format(out.strip()[-300:]))
        ok = False
    else:
        print("PASS  dependency layer (fastapi, uvicorn, keyring)")

    # the fleet must be discoverable — this is what the gate will run
    rc, out = sh([str(py), "-X", "utf8", str(HERE / "run_fleet.py"), "--list"])
    n = 0
    if rc == 0:
        for line in out.splitlines():
            if line.startswith("FLEET:"):
                n = line.split()[1]
    if rc != 0 or not n:
        print("FAIL  run_fleet --list did not answer: {}".format(out.strip()[-200:]))
        ok = False
    else:
        print("PASS  fleet discoverable: {} drives via run_fleet.py".format(n))

    if not ok:
        return False
    print()
    print("CLONE READY — ./test runs the fleet; commits run the gate.")
    print("  useful:  ./test --fail-fast        (debug loop)")
    print("           ./test --history         (per-drive flake rates)")
    return True


def main():
    print("=" * 60)
    print("  monero_project — new-clone setup")
    print("=" * 60)
    ok = step_venv()
    if ok:
        ok = step_hooks()
    if ok:
        ok = step_verify()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
