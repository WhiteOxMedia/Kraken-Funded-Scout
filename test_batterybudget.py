"""THE MEASURED-BUDGET CONTRACT (Sep 23, the costdrivers WARN).

Pins run_battery.budget_for to its measured-slow-suite contract:

  1. The flat PER_ENTRY_TIMEOUT stays the default — never-measured suites
     and fresh clones (no lanes manifest) keep tight hang detection.
  2. A suite with a measured lane time earns
     max(override, MEASURED_BUDGET_MULT x mean), capped at
     MAX_MEASURED_BUDGET — in EVERY mode: costdrivers' honest ~280s run
     must never again WARN against a budget its own history contradicts.
  3. The override map keeps its floor (apptest's 900) and the cap bounds
     the ratchet (a suite slowing 10x re-baselines its budget — the cap
     is the ceiling on that loop).

All mean-dependent checks run against a SANDBOX manifest (the real
manifest is runtime state whose values move with the machine) — the
only live-manifest check is the presence one, skipped honestly when the
manifest is absent. Prints "RESULT: N passed, M failed"; fast by design.
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_battery as rb

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    mark = "PASS" if cond else "FAIL"
    print(f"{mark}  {n}" + (f"  ({why})" if why and not cond else ""))


print("== 1. constants: the dials exist and say what they mean ==")
check(1.1, rb.MEASURED_BUDGET_MULT == 3.0, "the multiplier dial moved")
check(1.2, rb.MAX_MEASURED_BUDGET == 1800, "the 30-min cap moved")
check(1.3, rb.PER_ENTRY_TIMEOUT == 420, "the flat default moved unexpectedly")

print("== 2. sandbox: the budget math, proven deterministically ==")
_mf = rb.LANES_MANIFEST
try:
    with tempfile.TemporaryDirectory(prefix="battbudget_") as td:
        sbx = Path(td) / "lanes.json"

        # 2.1 no manifest at all (fresh clone): flat default
        rb.LANES_MANIFEST = sbx   # absent file
        check(2.1, rb.budget_for("test_costdrivers") == rb.PER_ENTRY_TIMEOUT,
              "a missing manifest must fail-soft to the flat default")

        # 2.2 measured mean: MULT x mean wins over the flat default
        sbx.write_text(json.dumps(
            {"test_costdrivers": {"secs": 280.9, "runs": 5}}), encoding="utf-8")
        check(2.2, rb.budget_for("test_costdrivers") == 842,
              f"3x280.9 should be 842, got {rb.budget_for('test_costdrivers')}")

        # 2.3 the override keeps its floor: max(override, mult x mean)
        sbx.write_text(json.dumps(
            {"test_apptest": {"secs": 100, "runs": 2}}), encoding="utf-8")
        check(2.3, rb.budget_for("test_apptest") == 900,
              "a small mean must not LOWER the override's 900")

        # 2.4 the override can be raised: 3x400 > 900
        sbx.write_text(json.dumps(
            {"test_apptest": {"secs": 400, "runs": 2}}), encoding="utf-8")
        check(2.4, rb.budget_for("test_apptest") == 1200,
              "a grown mean must raise the override (the growth direction)")

        # 2.5 the cap bounds the ratchet
        sbx.write_text(json.dumps(
            {"test_apptest": {"secs": 700, "runs": 2}}), encoding="utf-8")
        check(2.5, rb.budget_for("test_apptest") == rb.MAX_MEASURED_BUDGET,
              "3x700 must cap at MAX_MEASURED_BUDGET")

        # 2.6 the WARN band regains its meaning: honest run << 50% of budget
        # (restore the costdrivers fixture — 2.5's manifest holds only apptest)
        sbx.write_text(json.dumps(
            {"test_costdrivers": {"secs": 280.9, "runs": 5}}), encoding="utf-8")
        check(2.6, 280.9 < 0.5 * rb.budget_for("test_costdrivers"),
              "the honest mean must sit under the WARN band again")
finally:
    rb.LANES_MANIFEST = _mf   # the real manifest, always restored

print("== 4. auto-chunk: the pause/resume contract (Sep 24) ==")
_rf = rb.RESUME_FILE
try:
    with tempfile.TemporaryDirectory(prefix="battchunk_") as td:
        rb.RESUME_FILE = Path(td) / "resume.json"

        # 4.1 the pause round-trips (mode key, chunk number, wall, rows)
        rb._resume_save("full:trend", "trend", 2,
                        [["test", "test_trendbridge", "PASS", 3.2, "12/12"]],
                        [], 615.0)
        _d = rb._resume_load()
        check(4.1, _d.get("mode") == "full:trend" and _d.get("chunk") == 2
              and _d.get("wall_acc") == 615.0
              and _d.get("rows")[0][1] == "test_trendbridge",
              "the pause must round-trip (mode, chunk, wall_acc, rows)")

        # 4.2 no pause file loads empty (fresh clone)
        rb.RESUME_FILE = Path(td) / "absent.json"
        check(4.2, rb._resume_load() == {}, "no pause file must load empty")

        # 4.3 a corrupt pause fails soft — never crashes the battery
        rb.RESUME_FILE = Path(td) / "resume.json"
        rb.RESUME_FILE.write_text("{broken", encoding="utf-8")
        check(4.3, rb._resume_load() == {},
              "a corrupt pause must fail soft to empty")

        # 4.4 a completed sweep retires the pause (file gone)
        rb.RESUME_FILE = Path(td) / "resume.json"
        rb._resume_save("full", "", 1, [], [], 10.0)
        rb._resume_clear()
        check(4.4, rb._resume_load() == {},
              "_resume_clear must remove the pause")

        # 4.5 the new flags parse (and never shift the positional filter)
        _f, _p = rb._parse_flags(["--chunk=90", "log", "", "trend"])
        check(4.5, _f == {"chunk": 90} and _p == ["log", "", "trend"],
              "--chunk must parse without shifting positionals")
        _f, _p = rb._parse_flags(["--fresh", "--chunk=0"])
        check(4.6, _f == {"fresh": True, "chunk": 0},
              "--fresh and --chunk=0 must parse")

        # 4.7 the default window is the 600s terminal reality
        check(4.7, rb.CHUNK_DEFAULT == 600, "the default chunk window moved")
finally:
    rb.RESUME_FILE = _rf   # the real pause file, always restored

# the pause/resume wiring lives inline in main() — pinned by source, the
# same way the drive census pins inline production logic
print("== 5. auto-chunk wiring: source pins on main() ==")
_src = Path("run_battery.py").read_text(encoding="utf-8")
check(5.1, 'if r[2] != "PAUSED"' in _src,
      "the paused entry's marker row must be dropped AND its name not "
      "counted done — it belongs to the next chunk")
check(5.2, "chunk_limit = 0" in _src,
      "the resumed tail must run without further pausing (fresh budget)")
check(5.3, '_rec["chunks"]' in _src and "_resume_clear()" in _src,
      "a completed chunked sweep retires the pause and stamps its chunk "
      "count on the one history record")
check(5.4, '(lane or "full")' in _src,
      "a chunked full run must keep the 'full' mode in its history record "
      "(baseline-comparable)")
check(5.5, 'paused = {} if lane == "fast"' in _src,
      "--fast must never pause (and leaves a waiting pause alone)")
check(5.6, "mode_key = (lane or" in _src and 'f":{_flt}"' in _src,
      "the pause must be keyed by mode+filter so a different command "
      "never hijacks another sweep's pause")
check(5.7, "return 2" in _src,
      "a paused invocation must exit rc=2 (no summary — the completed "
      "sweep assembles it)")
check(5.8, "if _saving:" in _src
      and '_saving = lane != "fast" and not _foreign_block' in _src,
      "resume saves must route through the single _saving dial: --fast "
      "never writes pause state, and a run facing a foreign pause never "
      "saves its own progress over the waiting sweep's state")

print("== 3. live: the real manifest speaks when it exists ==")
_live = rb._lanes_load().get("test_costdrivers")
if _live and _live.get("secs"):
    check(3.1, rb.budget_for("test_costdrivers")
          > rb.PER_ENTRY_TIMEOUT,
          "costdrivers is measured — its budget must exceed the flat 420")
    check(3.2, _live["secs"] < 0.5 * rb.budget_for("test_costdrivers"),
          "the live mean must sit under the WARN band (the original sin)")
else:
    print("SKIP  3.x  (no measured costdrivers in the manifest — fresh "
          "clone; checks 2.2-2.6 carry the contract)")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
