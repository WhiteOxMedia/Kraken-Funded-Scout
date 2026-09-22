"""Drive of the nightly diagnostic layer: the study's summary/top_line/
write_report, the review's citation through them, and the report write.
Hermetic — the report path is redirected to a temp file, no live logs
touched; the grade_trades used is the real one unless stubbed."""
import sys
import types
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    print(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    PASS, FAIL = PASS + (1 if ok else 0), FAIL + (0 if ok else 1)


import _verdict_study as vs
import discipline as dc

# ── 1. summary: the single source of truth ──────────────────────────────
s = vs.summary()
print("      real summary ->", s[:2] if s else None)
check("summary ranks costliest first",
      bool(s) and all(s[i][2] >= s[i + 1][2] for i in range(len(s) - 1)))
check("summary rows are (name, n, usd) triples",
      bool(s) and all(isinstance(r[0], str) and isinstance(r[1], int)
                      and isinstance(r[2], float) for r in s))
tl = vs.top_line()
check("top_line is voice-safe (short name, dollars, n)",
      bool(tl) and " - " not in tl and "dollars across" in tl, tl)

# ── 2. the review cites the SAME source ─────────────────────────────────
if s and s[0][2] > 0:
    check("review's citation == summary's top row",
          dc._top_verdict_class() == (str(s[0][0]), float(s[0][2])))
else:
    check("review's citation == summary's top row",
          dc._top_verdict_class() is None)

# ── 3. the report write, hermetic ──────────────────────────────────────
tmpdir = tempfile.mkdtemp()
vs.REPORT_PATH = Path(tmpdir) / "verdict_cost_report.md"
check("first write returns the path", vs.write_report() == vs.REPORT_PATH)
first = vs.REPORT_PATH.read_text(encoding="utf-8")
check("first section grades the ledger", "### " in first
      and "graded" in first and "verdict class" in first)
_after2 = vs.REPORT_PATH.read_text(encoding="utf-8")
_r2 = vs.write_report()
_after2 = vs.REPORT_PATH.read_text(encoding="utf-8")
check("second write PREPENDS (newest first)",
      _r2 == vs.REPORT_PATH and _after2.count("### ") == 2
      and _after2.startswith("### "))

# stubbed ledger: the report says so honestly instead of raising
vs.grade_trades = lambda: (_ for _ in ()).throw(RuntimeError("no db"))
check("broken ledger -> summary None (fail-soft)", vs.summary() is None)
check("broken ledger -> top_line None", vs.top_line() is None)
vs.write_report()
check("broken ledger writes an honest 'nothing graded' section",
      "nothing graded" in vs.REPORT_PATH.read_text(encoding="utf-8")
      .split("###")[1])
vs.REPORT_PATH = Path(tmpdir) / "unwritable_dir" / "x" / "y.md"
check("unwritable path -> write_report returns None (never raises)",
      vs.write_report() is None)

print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
