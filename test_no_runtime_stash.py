"""GUARD (RUNTIME_INVARIANTS.md, rule 1): runtime state is never stashed.

Fails the suite if:
  - any ACTIVE git hook contains a stash invocation
  - any repo script (py/ps1/bat/cmd) contains a stash invocation
  - any git alias invokes stash
  - the stash ref exists (the stash list must stay empty)

The 2026-09-21 incident: the pre-commit hook's auto-stash dance reset the
live watch ledger to empty twice, left a live position untracked mid-session
with no exit checks, and its silent pop failure leaked 17 stashes.
"""
import os
import re
import subprocess
import sys

STASH_NEEDLE = re.compile(r"git[ \t\r\n]+stash|\"stash\"|'stash'")
HOOK_NAMES = [
    "applypatch-msg", "commit-msg", "fsmonitor-watchman", "post-update",
    "pre-applypatch", "pre-commit", "pre-merge-commit", "pre-push",
    "pre-rebase", "pre-receive", "prepare-commit-msg", "push-to-checkout",
    "update",
]
SCRIPT_EXTS = (".py", ".ps1", ".bat", ".cmd")
# The enforcement layer itself may OBSERVE stash state (its ref existence,
# the list) without ever INVOKING it: this suite, safe_commit.py (refuses to
# run while a stash exists) and runtime_tripwire.py (alerts on the ref).
# Exemption is by name; anything else matching is a violation.
SELF_EXEMPT = {"test_no_runtime_stash.py", "safe_commit.py", "runtime_tripwire.py"}

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail if not ok else ''}".rstrip())


def active_hook_dirs():
    """The default .git/hooks plus any core.hooksPath directories."""
    dirs = [os.path.join(".git", "hooks")]
    try:
        hp = subprocess.run(["git", "config", "--get", "core.hooksPath"],
                            capture_output=True, text=True).stdout.strip()
        if hp:
            dirs.insert(0, hp)
    except Exception:
        pass
    return [d for d in dirs if os.path.isdir(d)]


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


# ── 1. no hook invokes a stash command ────────────────────────────────
hook_hits = []
for d in active_hook_dirs():
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".sample"):
            continue
        path = os.path.join(d, fn)
        if not os.path.isfile(path):
            continue
        for i, line in enumerate(read_text(path).splitlines(), 1):
            if STASH_NEEDLE.search(line) and not line.lstrip().startswith("#"):
                hook_hits.append(f"{path}:{i}")
check("no active git hook invokes a stash command", not hook_hits,
      "; ".join(hook_hits[:5]))

# ── 2. no repo script invokes a stash command ─────────────────────────
script_hits = []
for root, dirs, files in os.walk("."):
    dirs[:] = [x for x in dirs if x not in (".git", ".venv", "__pycache__", "logs", "speech_queue")]
    for fn in files:
        if not fn.endswith(SCRIPT_EXTS) or fn in SELF_EXEMPT or fn.startswith("test_"):
            continue
        path = os.path.join(root, fn)
        for i, line in enumerate(read_text(path).splitlines(), 1):
            if STASH_NEEDLE.search(line) and not line.lstrip().startswith("#"):
                script_hits.append(f"{path}:{i}")
check("no repo script invokes a stash command", not script_hits,
      "; ".join(script_hits[:5]))

# ── 3. no git alias invokes stash ─────────────────────────────────────
alias_hits = []
try:
    out = subprocess.run(["git", "config", "--get-regexp", r"^alias\."],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "stash" in line.lower():
            alias_hits.append(line)
except Exception:
    pass
check("no git alias invokes stash", not alias_hits, "; ".join(alias_hits[:3]))

# ── 4. the stash ref must not exist (the list stays empty) ────────────
ref_exists = os.path.exists(os.path.join(".git", "refs", "stash"))
reflog_exists = os.path.exists(os.path.join(".git", "logs", "refs", "stash"))
try:
    listing = subprocess.run(["git", "stash", "list"], capture_output=True,
                             text=True).stdout.strip()
except Exception:
    listing = "<git unavailable>"
check("the stash list is empty (no leaked snapshots)",
      not ref_exists and not reflog_exists and not listing,
      f"ref={ref_exists} reflog={reflog_exists} list={listing[:60]!r}")

n_pass = sum(1 for _, ok, _ in results if ok)
print(f"\nRESULT: {n_pass} passed, {len(results) - n_pass} failed")
sys.exit(0 if n_pass == len(results) else 1)
