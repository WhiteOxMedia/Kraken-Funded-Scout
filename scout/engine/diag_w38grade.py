"""diag_w38grade.py — REHEARSE THE FIRST REAL FLIP GRADE (2026-W38).

W38 does not exist yet (the boundary is Monday), so this diag builds
the smallest ledger that clears FLIPGRADE_MIN_WEEKS=2 honestly: the
REAL W37 record cloned verbatim from production plus a coherent,
correctly-shaped W38 record (quantile in force, the fit as other per
record's own other_src), then runs the REAL grade_ledger() end to end
and asserts every artifact against hand-computed dollars:

  pooled per-state $/fire over both weeks (fit vs quantile),
  bench/flip-back proximity from the edge streaks,
  the flip event's aftermath over its GOVERNED window (W38 only —
  the evidence weeks end at W37),
  the _flipgrade.txt prepend + previous-run retention,
  event dedupe (daily passes cannot double-count),
  sims never grading.

Sandbox-only: history, grade file, and events trail are redirected;
production is read (the real W37 record) but never written.
Run: .venv\\Scripts\\python.exe -X utf8 diag_w38grade.py
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
import tidebar_weekly as tbw  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


sbx = ROOT / "_test_w38_dir"
shutil.rmtree(sbx, ignore_errors=True)
sbx.mkdir(exist_ok=True)
real = {k: getattr(tbw, k) for k in
        ("HISTORY_FILE", "FLIPGRADE_FILE", "FLIP_EVENTS_FILE",
         "FLIPGRADE_JSON", "FLIP_FILE", "RECKONING_WINDOW_FILE")}
try:
    tbw.HISTORY_FILE = sbx / "_tidebarweekly_history.jsonl"
    tbw.FLIPGRADE_FILE = sbx / "_flipgrade.txt"
    tbw.FLIP_EVENTS_FILE = sbx / "tide_bar_flip_events.jsonl"
    # a completed grade records its reckoning window row — sandboxed
    tbw.FLIPGRADE_JSON = sbx / "_flipgrade.json"
    tbw.FLIP_FILE = sbx / "tide_bar_flips.json"
    tbw.RECKONING_WINDOW_FILE = sbx / "tide_bar_reckoning_window.jsonl"

    # the REAL W37 record, verbatim
    w37 = json.loads(
        (ROOT / "_tidebarweekly_history.jsonl").read_text(encoding="utf-8")
        .strip().splitlines()[0])
    assert w37["week"] == "2026-W37", \
        "expected the real W37 record first in the production ledger"

    # a coherent W38: the quantile ships everywhere (bar_src quantile),
    # every grade two-sided (other = the fit, per other_src), FLAT's
    # thin stream still one-sided — exactly what Monday will look like
    w38 = {
        "week": "2026-W38",
        "generated_ts": "2026-09-14 01:45 UTC",
        "states": {
            "OUT": {"n": 410,
                    "in_force": {"bar": 86.0, "fires": 62, "payers": 17,
                                 "rate": 0.2742, "usd": 226.30,
                                 "usd_per_fire": 3.65},
                    "other": {"bar": 80.0, "fires": 104, "payers": 27,
                              "rate": 0.2596, "usd": 372.32,
                              "usd_per_fire": 3.58},
                    "other_src": "outcome-fitted"},
            "FLAT": {"n": 44,
                     "in_force": {"bar": 86.0, "fires": 5, "payers": 2,
                                  "rate": 0.4, "usd": 31.64,
                                  "usd_per_fire": 6.33},
                     "other": None, "other_src": "outcome-fitted"},
            "RISING": {"n": 180,
                       "in_force": {"bar": 87.0, "fires": 20, "payers": 6,
                                    "rate": 0.3, "usd": 94.95,
                                    "usd_per_fire": 4.75},
                       "other": {"bar": 86.0, "fires": 21, "payers": 6,
                                 "rate": 0.2857, "usd": 94.95,
                                 "usd_per_fire": 4.52},
                       "other_src": "quantile"}},
        "fills": {"OUT": {"fills": 2, "pnl": 11.5}}}
    tbw.HISTORY_FILE.write_text(
        json.dumps(w37) + "\n" + json.dumps(w38) + "\n", encoding="utf-8")

    # a REAL-shaped flip event for OUT (evidence weeks inside the window;
    # persisted through the module's own deduped writer)
    ev = {"action": "flip", "week": "2026-W38", "ts": "2026-09-14T02:00:00Z",
          "evidence": [{"week": "2026-W35", "edge": -1.2},
                       {"week": "2026-W36", "edge": -1.5},
                       {"week": "2026-W37", "edge": -0.9}],
          "pooled": -1.2}
    tbw._persist_flip_events({"OUT": ev}, "2026-W38", ev["ts"])

    # pre-seed the grade file: the prepend must keep the previous run
    tbw.FLIPGRADE_FILE.write_text(
        "=== THE FLIP GRADE — previous run ===\nold body\n", encoding="utf-8")

    s = tbw.grade_ledger()

    check("the grade RAN over 2 weeks (the MIN_WEEKS hold is cleared)",
          s.get("weeks") == 2 and not s.get("held"), s)
    check("the summary carries per-state grades",
          isinstance(s.get("states", {}).get("OUT"), dict)
          and "edges" in s["states"]["OUT"]
          and s["states"]["OUT"].get("pooled") == -0.25, s.get("states"))

    txt = tbw.FLIPGRADE_FILE.read_text(encoding="utf-8")
    check("the file was PREPENDED (newest first), the previous run kept",
          txt.startswith("=== THE FLIP GRADE") and "previous run" in txt,
          txt[:120])
    check("the header names the real week span (W37..W38)",
          "2026-W37..2026-W38" in txt, txt.splitlines()[1][:120])

    # hand-computed OUT pool over the two weeks, BY SEAT (the canonical
    # axis pools each bar's seat via the record's own other_src — W37's
    # fit is in force, W38's fit sits on the other side post-flip):
    #   fit      86+104=190 fires, $312.54+$372.32=$684.86 -> $+3.60/fire
    #   quantile 50+62=112 fires, $205.72+$226.30=$432.02 -> $+3.86/fire
    #   edge -0.25; week edges -0.48 (real W37) and -0.07 -> streak 2/3
    check("OUT pooled dollars match the hand computation (seat-mapped)",
          "fit 190 fires $+3.60/fire" in txt
          and "quantile 112 fires $+3.86/fire" in txt
          and "fit edge -0.25" in txt, txt)
    check("OUT week edges are the real W37 edge and W38's (-0.48 -0.07)",
          "edges by week: -0.48 -0.07" in txt,
          [l for l in txt.splitlines() if "edges by week" in l])
    check("OUT bench proximity counts BOTH losing weeks (2/3, 1 to go)",
          "bench proximity: 2/3" in txt and "1 to go" in txt,
          [l for l in txt.splitlines() if "proximity" in l])

    # hand-computed RISING pool:
    #   fit      14+20=34 fires, $63.30+$94.95=$158.25 -> +$4.65/fire
    #   quantile 15+21=36 fires, $158.25 -> +$4.40/fire ; edge +0.26
    check("RISING pooled dollars match the hand computation",
          "34 fires $+4.65/fire" in txt and "36 fires $+4.40/fire" in txt
          and "fit edge +0.26" in txt, txt)

    # aftermath: the flip's evidence ends at W37 -> the governed window
    # is W38 only; realized = the benched quantile (in force) 62 fires
    # $3.65; counterfactual = the fit (other) 104 fires $3.58 -> PAID
    check("the flip event's aftermath grades its governed window in "
          "dollars (W38 only, the quantile paid, PAID)",
          "⚡ flip at 2026-W38: in the 1 weeks after, the quantile paid "
          "$+3.65/fire (62 fires) vs the fit's counterfactual "
          "$+3.58/fire — the decision PAID" in txt,
          [l for l in txt.splitlines() if "⚡" in l])

    check("FLAT's one-sided weeks degrade honestly (not graded)",
          "FLAT: no two-sided weeks — not graded" in txt,
          [l for l in txt.splitlines() if "FLAT" in l])

    added = tbw._persist_flip_events({"OUT": ev}, "2026-W38", ev["ts"])
    check("event persistence dedupes (daily passes can't double-count)",
          added == 0, added)

    # sims never grade: reset to a fresh section, add a sim event, and
    # the re-grade must still show exactly the real flip's aftermath
    sim = dict(ev, action="flip_back", week="2026-W38", sim=True)
    tbw.persist_sim_events([sim])
    tbw.FLIPGRADE_FILE.write_text("", encoding="utf-8")
    tbw.grade_ledger()
    txt3 = tbw.FLIPGRADE_FILE.read_text(encoding="utf-8")
    check("a sim event never enters the grade (real decisions only)",
          txt3.count("⚡") == 1 and "flip_back" not in txt3,
          [l for l in txt3.splitlines() if "⚡" in l])
finally:
    tbw.HISTORY_FILE = real["HISTORY_FILE"]
    tbw.FLIPGRADE_FILE = real["FLIPGRADE_FILE"]
    tbw.FLIP_EVENTS_FILE = real["FLIP_EVENTS_FILE"]
    shutil.rmtree(sbx, ignore_errors=True)

fails = [r for r in results if not r[1]]
print(f"W38-GRADE REHEARSAL: {len(results) - len(fails)} passed, "
      f"{len(fails)} failed")
for name, ok, det in results:
    if not ok:
        print(f"FAIL  {name}  [{str(det)[:400]}]")
print("done")
sys.exit(1 if fails else 0)
