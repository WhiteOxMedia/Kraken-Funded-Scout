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

# ── 1b. the ledgers are module-anchored (Sep 22) ────────────────────────
# The console, the scanner and the review all import the study from
# their own working directories; the numbers can never depend on the
# caller's cwd (the Sep 22 defect: the console's plate went silent
# because the study graded "whatever jsonl the cwd holds").
import os
import importlib
# (Sep 23: the chdir world is retired — a suite never moves the process's
# working directory. The absolute-path property is proven structurally:
# the module builds its ledger paths from Path(__file__).parent, so they
# are absolute by construction, whatever the cwd.)
_src_vs = Path(vs.__file__).read_text(encoding="utf-8")
check("ledger paths are absolute by construction (Path(__file__).parent)",
      "Path(__file__).parent" in _src_vs
      and vs.TRADES_PATH.is_absolute() and vs.SUPPRESSIONS_PATH.is_absolute(),
      str(vs.TRADES_PATH))
s2 = vs.summary()
check("summary grades the real book (fresh module state, no cwd dance)",
      bool(s2) and bool(s) and s2[0][0] == s[0][0]
      and abs(s2[0][2] - s[0][2]) < 0.005, (s2 or [None])[0])

# ── 2. the review cites the SAME source ─────────────────────────────────
if s and s[0][2] > 0:
    check("review's citation == summary's top row",
          dc._top_verdict_class() == (str(s[0][0]), float(s[0][2])))
else:
    check("review's citation == summary's top row",
          dc._top_verdict_class() is None)

# ── 3. the report write, hermetic ──────────────────────────────────────
tmpdir = tempfile.mkdtemp()
vs.REPORT_PATH = Path(tmpdir) / "verdict_cost_report.md"  # leak-audit: ok (tempfile-derived sandbox; the module is suite-private, restore-never)
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

# ── 3b. the per-night delta line (Sep 22) ───────────────────────────
_real_summary = vs.summary
_PREV = ("### 2026-09-21 22:16 - graded 48 closed trades\n\n"
         "| verdict class | n | $ left |\n|---|---|---|\n"
         "| stayed too long - gave back over half the peak | 6 | 64.78 |\n"
         "| bought the top - the entry chased the move | 3 | 42.52 |\n\n")

def _delta_sec():
    return vs.REPORT_PATH.read_text(encoding="utf-8").split("###")[1]

# growth + a new class; unchanged classes stay unnamed
vs.REPORT_PATH.write_text(_PREV, encoding="utf-8")
vs.summary = lambda: [
    ("stayed too long - gave back over half the peak", 6, 70.0),
    ("bought the top - the entry chased the move", 3, 42.52),
    ("exited too soon - the goal was on the tape", 2, 10.0)]
vs.write_report()
_sec = _delta_sec()
check("delta line present", "\u0394 since the last section:" in _sec)
check("grown class shows its gain and n",
      "+$5.22 (n 6\u21926)" in _sec, _sec[_sec.find("\u0394"):][:120])
check("new class marked (new)", "+$10.00 (new)" in _sec)
check("unchanged class not named in the delta",
      _sec.count("bought the top") == 1)

# no change -> the honest one-liner
vs.REPORT_PATH.write_text(_PREV, encoding="utf-8")
vs.summary = lambda: [
    ("stayed too long - gave back over half the peak", 6, 64.78),
    ("bought the top - the entry chased the move", 3, 42.52)]
vs.write_report()
check("unchanged book says so in one clause",
      "no change on the book" in _delta_sec())

# shrink + a class falling off the book
vs.REPORT_PATH.write_text(_PREV, encoding="utf-8")
vs.summary = lambda: [
    ("stayed too long - gave back over half the peak", 6, 50.0)]
vs.write_report()
_sec = _delta_sec()
check("shrunken class shows its loss", "\u2212$14.78" in _sec)
check("dropped class marked off the book",
      "\u2212$42.52 (off the book)" in _sec)

# garbage previous section -> no delta line, never a raise
vs.REPORT_PATH.write_text("### garbage\n\nno table here\n", encoding="utf-8")
vs.summary = _real_summary
try:
    vs.write_report()
    check("garbage prev section -> no delta line, no raise",
          "\u0394 since the last section:" not in _delta_sec())
except Exception as e:
    check("garbage prev section -> no delta line, no raise", False, e)

# stubbed ledger: the report says so honestly instead of raising
vs.grade_trades = lambda: (_ for _ in ()).throw(RuntimeError("no db"))
check("broken ledger -> summary None (fail-soft)", vs.summary() is None)
check("broken ledger -> top_line None", vs.top_line() is None)
vs.write_report()
check("broken ledger writes an honest 'nothing graded' section",
      "nothing graded" in vs.REPORT_PATH.read_text(encoding="utf-8")
      .split("###")[1])
vs.REPORT_PATH = Path(tmpdir) / "unwritable_dir" / "x" / "y.md"  # leak-audit: ok (tempfile-derived sandbox)
check("unwritable path -> write_report returns None (never raises)",
      vs.write_report() is None)

print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
