"""Test the fleet-sweep report contract (the Oct 6 triage). THE
SELECTOR MAJOR: a wrong drive selector used to skip quietly, run an
EMPTY sweep, overwrite fleet_sweep_latest.html, and exit 0 - a wrong
selector must refuse before anything is written (exit 2). THE
FAILED-COUNT MAJORS (the round-7 cure, Oct 1) are pinned here too: a
partial pass ratio is t-p failures, and a verdict that exists and lies
is NONSENSE (-2), never the absent verdict (-1) that rides the rc
contract to GREEN."""
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import scratch_space   # scratch policy: nothing volatile lands in the repo root

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))

_spec = importlib.util.spec_from_file_location(
    "fleet_sweep_under_test", str(BASE / "ops" / "fleet_sweep.py"))
fs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fs)

check("2a the failed-count contract: a partial ratio is t-p failures",
      fs._parse_failed_count("40/43 passed") == 3)
check("2b a verdict that exists and lies is NONSENSE (-2), never GREEN",
      fs._parse_failed_count("0/0 passed") == -2
      and fs._parse_failed_count("99/27 passed") == -2)
check("2c no verdict is UNKNOWN (-1): the rc contract governs",
      fs._parse_failed_count("") == -1
      and fs._parse_failed_count(None) == -1)

_real_out = fs.OUT_DIR
_tmp = Path(tempfile.mkdtemp(prefix="fsweep_refusal_"))
try:
    fs.OUT_DIR = _tmp
    _argv = sys.argv
    try:
        sys.argv = ["fleet_sweep.py", "definitely_not_a_drive_xyz"]
        _rc = fs.main()
    finally:
        sys.argv = _argv
    _wrote = False
    if _tmp.exists():
        _wrote = any(p.name.startswith("fleet_sweep") for p in _tmp.iterdir())
    check("1a an unresolved selector refuses BEFORE writing artifacts: exit 2, nothing written",
          _rc == 2 and not _wrote,
          "rc=" + str(_rc) + " wrote=" + str(_wrote))
finally:
    fs.OUT_DIR = _real_out
    shutil.rmtree(_tmp, ignore_errors=True)

fails = [r for r in results if not r[1]]
scratch_space.report_temp("opsfleetsweep_check.txt").write_text(
    chr(10).join("PASS  " + n if ok else "FAIL  " + n + "  [" + str(d) + "]"
                 for n, ok, d in results),
    encoding="utf-8")
print("ALL PASS" if not fails else str(len(fails)) + " FAILURES")
raise SystemExit(1 if fails else 0)
