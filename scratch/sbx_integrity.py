"""Sandbox integrity check: every _mutation_sbx/repo .py must byte-match
the worktree (CRLF-normalized) except the four HEAD-blob overlay files.
--fix: copy the worktree bytes over every mismatched file (a kill between
mutation and restore leaves a mutant behind — the next window's baselines
would lie); --fix also exits 1 when it had to repair, 0 when clean."""
import os
import shutil
import sys

OV = {"hooks/pre-commit", "test_commitgate.py", "diag_mutation_sweep.py",
      "artifacts/mutation_sweep/vacuous_triage.md"}
SKIP = {".git", ".venv", "__pycache__", "logs", "_mutation_sbx", "scratch",
        "node_modules", "_recon", "speech_queue", "watch", "battery_history"}

bad, checked = [], 0
for root, dirs, files in os.walk("."):
    dirs[:] = [d for d in dirs if d not in SKIP]
    for f in files:
        if not f.endswith(".py"):
            continue
        p = os.path.join(root, f)
        rel = os.path.relpath(p, ".").replace(os.sep, "/")
        if rel.startswith("./"):
            rel = rel[2:]
        rp = os.path.join("_mutation_sbx", "repo", rel)
        if not os.path.exists(rp):
            continue
        checked += 1
        a = open(p, "rb").read().replace(b"\r\n", b"\n")
        b = open(rp, "rb").read().replace(b"\r\n", b"\n")
        if a != b and rel not in OV:
            bad.append(rel)
print(f"checked {checked} sandbox .py files vs worktree; "
      f"mismatches outside overlay whitelist: {bad}")
if "--fix" in sys.argv and bad:
    for rel in bad:
        shutil.copyfile(rel, os.path.join("_mutation_sbx", "repo", rel))
        print("restored from worktree:", rel)
    sys.exit(1)
sys.exit(0)
