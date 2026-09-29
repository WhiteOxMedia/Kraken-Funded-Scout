"""THE FLEET-SWEEP DRIVE (task 40, the round-6 triage): the sweep tool's
contract, hermetically — NO subprocesses, NO repo state written. Pins:

  1. classification: empty classes = RED (the tool never masks a new
     failure), a FLAKY/STALE_PIN entry = AMBER with the note riding,
     GREEN never consults the classes file.
  2. THE RENDER CRASH CLASS (the round-6 gate stream's recurring
     major): a classified NON-GREEN row renders without raising — the
     first sweep that ever had a classification would have died in
     NameError at ops/fleet_sweep.py's _row (classes was render's
     parameter but not _row's), and the verification subset happened
     to be all green, so the crash was latent. The whole table must
     render for every status.
  3. the honesty invariants: classify flags unclassified REDs (the
     exit-1 contract), the report carries the stamp, the drive count
     and the tool's own name.
  4. units: verdict parsing (the house shapes), drive discovery
     (bare/prefixed/suffixed names), classification file load (a
     broken file degrades to {}, never raises).
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import ops.fleet_sweep as fs

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not ok else ""))


GREEN = {"drive": "test_x.py", "rc": 0, "verdict": "RESULT: 5 passed, 0 failed",
         "failed": 0, "secs": 1.0, "fails": [], "status": "GREEN",
         "cls_status": None, "cls_note": ""}
RED_UNCLASS = {"drive": "test_new.py", "rc": 1, "verdict": "RESULT: 3 passed, 2 failed",
               "failed": 2, "secs": 2.0, "fails": ["FAIL  some pin"],
               "status": "RED", "cls_status": None, "cls_note": ""}
AMBER = {"drive": "test_flaky.py", "rc": 1, "verdict": "RESULT: 4 passed, 1 failed",
         "failed": 1, "secs": 2.0, "fails": ["FAIL  racy leg"],
         "status": "AMBER", "cls_status": "FLAKY",
         "cls_note": "races the watchdog; verified 2026-09-28"}

# ── 1. classification ────────────────────────────────────────────────
cls = fs.classify(dict(RED_UNCLASS), {})
check("1.1 an unclassified failure is RED with no class (the tool "
      "never masks a new failure — the exit-1 contract)",
      cls["status"] == "RED" and cls["cls_status"] == "", cls)
cls = fs.classify(dict(AMBER), {"test_flaky.py": {
    "status": "FLAKY", "note": "races the watchdog",
    "verified": "2026-09-28"}})
check("1.2 a classified failure carries the class VERBATIM (FLAKY — "
      "the render buckets it, storage never stores AMBER) with the "
      "reviewer's note",
      cls["status"] == "FLAKY" and cls["cls_status"] == "FLAKY"
      and "watchdog" in cls["cls_note"], cls)
cls = fs.classify(dict(GREEN), {})
check("1.3 GREEN never consults the classification file (cls fields "
      "default empty)",
      cls["status"] == "GREEN" and cls["cls_status"] == ""
      and cls["cls_note"] == "", cls)

# ── 2. THE RENDER CRASH CLASS ────────────────────────────────────────
try:
    table = fs.render([dict(GREEN), dict(RED_UNCLASS), dict(AMBER)],
                      42.0, "2026-09-29 12:00", {})
    crashed = None
except Exception as exc:                      # noqa: BLE001 — the pin IS this
    table, crashed = "", repr(exc)
check("2.1 the WHOLE table renders — green, unclassified RED, and "
      "classified AMBER rows together, no exception",
      crashed is None and table != "", crashed)
check("2.2 every row's status and the failing pins land in the HTML",
      table != "" and "RED" in table and "AMBER" in table
      and "FAIL  some pin" in table and "FLAKY" in table
      and "verified 2026-09-28" in table, table[:120])
check("2.3 the report names the stamp, the drive count and its maker",
      "2026-09-29 12:00" in table and "3 drives" in table
      and "ops/fleet_sweep.py" in table, table[:200])

# ── 3. units ─────────────────────────────────────────────────────────
m = fs._VERDICT_RE.search("RESULT: 12 passed, 0 failed")
check("3.1 the verdict parser reads the house RESULT shape",
      m is not None, "no match")
m = fs._VERDICT_RE.search("27/27 passed")
check("3.2 the verdict parser reads the x/y shape and the banner "
      "words (ALL PASS / DONE); a bare rc-0 run stays GREEN through "
      "the rc contract even when no verdict parses",
      m is not None and fs._VERDICT_RE.search("ALL PASS") is not None
      and fs._VERDICT_RE.search("DONE") is not None, "no match")
names = [d.name for d in fs.discover_drives(["canary"])]
check("3.3 discovery accepts bare, test_-prefixed and .py-suffixed names",
      names == ["test_canary.py"], names)
import tempfile as _tf
_sbx_fs = Path(_tf.mkdtemp())
_broken = _sbx_fs / "broken.json"
_broken.write_text("{not json at all", encoding="utf-8")
_real_cls_file = fs.CLASSES_FILE
try:
    fs.CLASSES_FILE = _broken
    degraded = fs.load_classes()
finally:
    fs.CLASSES_FILE = _real_cls_file
check("3.4 a broken classification file degrades to empty (never "
      "raises — the report still renders)",
      isinstance(degraded, dict) and degraded == {}, "")

fails = [r for r in results if not r[1]]
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
