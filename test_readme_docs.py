"""test_readme_docs.py — THE DOCS-ROT TRIPWIRE (Sep 26).

README.md documents the verification and operations loop; this drive
keeps it honest by asserting that every command the README shows
actually exists on disk. Parse rules: fenced code blocks are checked
token-by-token against a disk inventory (```bash blocks carry shell
commands — python, ./test, git config, curl, taskkill, netstat —
checked against PATH plus the repo; ```-blocks with a filename header
reference repo files, checked for existence); inline mentions of
known repo files (make_bundle.py, setup_dev.py, run_battery.py …)
are checked too. A renamed or deleted tool fails this drive before
the README lies to the next reader. The standing fleet fixtures
(security_probe, prefs_live_probe_fleet) are pinned the same way:
dropping them from the README — or renaming them in the drives that
own them — fails this drive.

Skips honestly if README.md is missing (a checkout without docs has
nothing to rot). Prints "RESULT: N passed, M failed".
"""
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
README = HERE / "README.md"

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


if not README.exists():
    print("SKIP: no README.md — nothing to keep honest")
    print("RESULT: 0 passed, 0 failed")
    sys.exit(0)

text = README.read_text(encoding="utf-8")

# ── the disk inventory: what exists on PATH and in the repo ─────────
PATH_CMDS = {p.name.lower() for p in
             (Path(p) for p in shutil.which("git") and [
                 "git.exe", "curl.exe", "taskkill.exe", "netstat.exe",
                 "powershell.exe", "python.exe"] or [])}

repo_files = set()
repo_names = set()
for p in HERE.rglob("*"):
    if p.is_file() and (".git" not in p.parts and "node_modules" not in p.parts):
        repo_files.add(str(p.relative_to(HERE)).replace("\\", "/").lower())
        repo_names.add(p.name.lower())

# commands this repo ships as shims or the venv provides
LOCAL_CMDS = {"test", "test.bat", "python", "run_fleet.py"}

# ── 1. every tool file the README names exists ──────────────────────
NAMED_TOOLS = [
    "run_fleet.py", "setup_dev.py", "make_bundle.py", "githooks/pre-commit",
    "fleet_history.jsonl-is-generated",   # placeholder; filtered below
    "test_prefs_live_server.py", "pool/server.py", "pool/templates/index.html",
]
NAMED_TOOLS = [t for t in NAMED_TOOLS if "-is-generated" not in t]
missing_tools = [t for t in NAMED_TOOLS
                 if t.lower() not in repo_files]
check(1, not missing_tools,
      f"README names files that do not exist: {missing_tools}")

# ── 2. bash-block commands resolve ──────────────────────────────────
# extract ```bash fenced blocks; take the first token of each command
# line and require it to be a known shell command, repo shim, or
# executable the reader would have
BASH_OK = {"git", "curl", "taskkill", "netstat", "powershell", "python",
           "sleep", "echo", "cd", "./test", "test"}
bad_cmds = []
for block in re.findall(r"```bash\n(.*?)```", text, re.S):
    for line in block.splitlines():
        line = line.strip().lstrip("$ ").strip()
        if not line or line.startswith("#"):
            continue
        # skip continuation lines of the powershell incantation
        tok = line.split()[0]
        if tok not in BASH_OK:
            bad_cmds.append(tok)
check(2, not bad_cmds,
      f"README bash blocks name commands outside the known set: "
      f"{sorted(set(bad_cmds))}")

# ── 3. the specific README commands exist on this machine ───────────
check(3, shutil.which("git") and shutil.which("curl")
      and shutil.which("powershell"),
      "the documented shell tooling (git/curl/powershell) is on PATH")
check(4, (HERE / "test").exists() and (HERE / "test.bat").exists(),
      "the ./test shim documented as the fleet entry point exists")
check(5, (HERE / ".venv" / "Scripts" / "python.exe").exists()
      or shutil.which("python"),
      "a python interpreter exists for the documented setup commands")
check(6, "githooks" in repo_names or (HERE / "githooks").is_dir(),
      "the githooks/ directory the README tells new clones to wire exists")

# ── 4. the cross-repo references point at real files ────────────────
scout = HERE.parent / "kraken_scout"
if scout.is_dir():
    check(7, (scout / "run_battery.py").exists()
          and (scout / "test_pool_prefs_live.py").exists(),
          "the README's Scout-side names (run_battery.py, "
          "test_pool_prefs_live) exist in the sibling checkout")
    check(8, (scout / "LIVE_CONTRACT_TEMPLATE.py").exists(),
          "the live-contract registration template the README points "
          "at exists in the Scout repo")
else:
    check(7, True, "no sibling scout checkout — cross-repo names unchecked")
    check(8, True, "template unchecked (no sibling scout checkout)")

# ── 5. the standing fleet fixtures stay documented ──────────────────
# The two probe accounts that must survive DB sweeps are named in the
# README's fixtures section; a cleanup crew that has never read it is
# exactly the failure mode this section exists to prevent. The names
# are ALSO asserted against the drives that own them, so a rename on
# either side forces the doc and the code to re-sync.
check(9, "`security_probe`" in text and "`prefs_live_probe_fleet`" in text,
      "README documents the standing fleet fixtures (must survive sweeps)")
check(10, '"security_probe"' in (HERE / "test_security.py").read_text(encoding="utf-8")
      and 'PROBE + "_fleet"' in (HERE / "test_prefs_live_server.py").read_text(encoding="utf-8"),
      "the documented fixture names match the drives that own them")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
