"""test_hostinger_verifier.py — THE CORRECTIONS-GATE PINS (Sep 26 review
triage).

verify_hostinger_corrections.py grades Hostinger's corrected files
before they touch production. The newest review batch raised four
majors against it; the drive pins the contract each one produced:

  1. INVENTORY CONTRACT (major :73, the entry)   the docstring promises
     "corrected versions of ANY OF" the listed files. A correction that
     touches only admin_panel.html must be GRADED (exit 0 when clean) —
     the old entry refused it with "no corrected index.html"; an empty
     directory still refuses (nothing to grade).
  2. EVERY REFERENCED ID GRADED (major :115)   a corrected JS that
     gains getElementById("brandNew") - an id in NO html - must FAIL
     check 3.2 (the old `r in cur_ids` logic only knew production ids,
     so the new-reference hole passed silently).
  3. LINK OCCURRENCES, NOT DISTINCT URLS (major :57, admin branch)   a
     corrected admin panel that drops ONE of production's three /scout/
     anchors must FAIL check 3.5 - the URL surviving elsewhere (as a
     set member) proves nothing.

Each scenario drives the real gate as a subprocess against fixture
directories and asserts its exact verdict (rc + specific check lines).
Prints "RESULT: N passed, M failed".
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATE = HERE / "verify_hostinger_corrections.py"
PROD = HERE / "pool"

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


def run_gate(d):
    r = subprocess.run([sys.executable, str(GATE), str(d)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return r.returncode, r.stdout


def fresh_dir():
    return Path(tempfile.mkdtemp(prefix="hostinger_gate_"))


print("== 1. THE INVENTORY CONTRACT (admin-only correction is graded) ==")
# an empty directory: nothing to grade -> rc 1, no crash
_d0 = fresh_dir()
rc, out = run_gate(_d0)
check("1.1", rc == 1 and "nothing to grade" in out,
      f"empty correction dir -> honest refusal (rc {rc})")
# admin-only set: graded (index graded against itself), clean copy passes
_d1 = fresh_dir()
(_d1 / "static").mkdir()
shutil.copy(PROD / "templates" / "admin_panel.html",
            _d1 / "admin_panel.html")
rc, out = run_gate(_d1)
check("1.2", rc == 0 and "grading index against itself" in out,
      f"admin-only correction is GRADED and passes clean (rc {rc})")

print("== 2. A NEW JS-REFERENCED ID MUST FAIL 3.2 ==")
# the review's exact shape: corrected JS gains a reference to an id that
# NO html (corrected or otherwise) carries. The corrected set supplies
# index + admin_panel copies of production (the gate's id pool spans
# every corrected html), so the ONLY dangling reference is the new one.
_d2 = fresh_dir()
(_d2 / "static").mkdir()
shutil.copy(PROD / "templates" / "index.html", _d2 / "index.html")
shutil.copy(PROD / "templates" / "admin_panel.html",
            _d2 / "admin_panel.html")
app = (PROD / "static" / "app.js").read_text(encoding="utf-8")
(_d2 / "static" / "app.js").write_text(
    app + '\ngetElementById("brandNewHook");\n', encoding="utf-8")
rc, out = run_gate(_d2)
check("2.1", rc == 1 and "FAIL  3.2" in out and "brandNewHook" in out,
      f"a dangling NEW id fails the hook check (rc {rc})")
check("2.2", "adminActiveMiners" not in out.split("DANGLING")[-1][:120],
      "the id pool spans the supplied corrected html (no false danglers "
      "from the admin panel's own ids)")

print("== 3. ONE DROPPED /scout/ ANCHOR MUST FAIL 3.5 ==")
admin = (PROD / "templates" / "admin_panel.html").read_text(encoding="utf-8")
if admin.count('href="/scout/"') >= 2:
    _d3 = fresh_dir()
    (_d3 / "static").mkdir()
    dropped = admin.replace('href="/scout/"', 'href="/dropped-anchor/"', 1)
    (_d3 / "admin_panel.html").write_text(dropped, encoding="utf-8")
    rc, out = run_gate(_d3)
    check("3.1", rc == 1 and "FAIL  3.5" in out,
          "an occurrence drop fails the admin link check even though the "
          "URL survives as a set member")
else:
    check("3.1", False, "fixture precondition lost: production admin "
          "panel no longer repeats /scout/ - rebuild this scenario")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
