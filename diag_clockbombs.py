"""diag_clockbombs.py — clock-freeze time bombs in the drives.

THE CLASS (the metalayers shadow bomb, Sep 23): a drive pins a frozen
date/hour while the code under test stamps or compares with the REAL
wall clock, so the suite only passes at certain hours/dates — and the
battery stays green while a check silently stops grading.

THE TIDEMEMORY BOMB (found by the +40d sweep, fixed same day): a fixture
stamped with datetime.now() while the decay math received a time.time()
now_ts — two clock sources in one check. In production they agree; under
any clock perturbation the "fresh" payers silently age past the decay
and the check stops grading what it says it grades.

WHAT THIS DIAG ENFORCES (cheap, every battery run):
  1. MIXED COMPARISON — a frozen literal compared directly against a
     real-clock call in one expression (hard fail).
  2. DUAL-CLOCK STAMPS — a fixture helper stamps one record from
     datetime.now() while another clock call sits in the same file's
     check context (fail — the tidememory shape). Sentinel
     `# clock-audit: ok` documents the reviewed exceptions.
  3. HARNESS CANARY — a synthetic suite is exec'd under a +40d epoch
     shift via _faketime_light.py; if the shifted run cannot make a
     clock-sensitive check fail, the environment broke and this diag
     fails loudly rather than green-lighting a dead sweep.

Behavioral faketime verification of specific suites (the deep sweep)
stays a manual scratch job: _faketime_light.py <shift> <suite.py>.
"""
import ast
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
FROZEN_RE = re.compile(r"20\d\d-\d\d-\d\d|datetime\(\s*20\d\d\s*,")
CLOCK_EXPR = re.compile(
    r"(datetime\.now\(|date\.today\(|datetime\.today\(|time\.time\(\)"
    r"|\.now\(\)|\.today\(\)|_now_mst\(\))")

findings = []
suites = sorted(ROOT.glob("test_*.py"))
for py in suites:
    try:
        src = py.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except SyntaxError:
        continue
    lines = src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            seg = [node.left, *node.comparators]
            has_frozen = any(FROZEN_RE.search(ast.unparse(o)) for o in seg)
            has_clock = any(CLOCK_EXPR.search(ast.unparse(o)) for o in seg)
            if has_frozen and has_clock:
                near = "\n".join(lines[max(node.lineno - 3, 0):node.lineno])
                if "clock-audit: ok" in near:
                    continue
                findings.append(
                    f"{py.name}:{node.lineno} MIXED COMPARISON: "
                    f"{ast.unparse(node)[:90]}")
        # the dual-clock stamp: a datetime.now(...)-based fixture stamp
        # inside a file whose checks feed time.time() now_ts — the
        # tidememory shape. Cheap proxy: both call forms in one file
        # with a `now_ts=` kwarg passing time.time.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "now" \
                and "datetime" in ast.unparse(node.func.value):
            for ln_off, ln_txt in enumerate(
                    lines[node.lineno - 1:node.lineno + 12], start=node.lineno):
                if "now_ts=time.time()" in ln_txt.replace(" ", "") \
                        or "now_ts=time.time()" in re.sub(r"\s+", "", ln_txt):
                    blk = "\n".join(lines[max(node.lineno - 4, 0):node.lineno])
                    if "clock-audit: ok" in blk:
                        continue
                    findings.append(
                        f"{py.name}:{node.lineno} DUAL-CLOCK STAMP: "
                        f"datetime.now fixture feeding a time.time now_ts")

# ── 3. the harness canary: the sweep must be ALIVE in this environment ──
# The canary asserts its shifted clock is >= 3,400,000s ahead of the
# marker epoch captured (unshifted) at generation — a +40d shift moves
# time.time() by 3,456,000s, an unshifted run by ~0. Reaching it proves
# the harness actually shifts child processes.
canary = ROOT / "_clockbomb_canary.py"
_marker = time.time()
canary.write_text(
    f"import time\n"
    f"ok = time.time() - {_marker:.0f} >= 3_400_000\n"
    "print('RESULT: 1 passed, 0 failed' if ok else 'RESULT: 0 passed, 1 failed')\n"
    "import sys; sys.exit(0 if ok else 1)\n", encoding="utf-8")
try:
    p = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "_faketime_light.py"),
         "+40d", str(canary)],
        capture_output=True, timeout=60)
    if p.returncode != 0 or b"0 passed" in p.stdout:
        findings.append(
            "HARNESS CANARY: the faketime shift did not reach the canary "
            "— clock-sweep evidence is dead in this environment")
finally:
    canary.unlink(missing_ok=True)

if findings:
    print("CLOCK-FREEZE TIME BOMBS — drives that may only pass at certain hours:")
    for f in findings:
        print(f"  {f}")
    print(f"\nRESULT: 0 passed, {len(findings)} failed")
    sys.exit(1)
print(f"CLOCK BOMBS: none across {len(suites)} drives "
      "(mixed-comparison census, dual-clock stamps, harness canary)")
print("RESULT: 1 passed, 0 failed")
sys.exit(0)
