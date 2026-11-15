"""THE SETUP STAMP (Nov 6 — expectancy by family, no popup-text join).

The Nov 6 study reconstructed families by joining 2,666 closes to popup
text and parsing "TIDE:"/"CLIMB-OUT" lines; 69 closes had no join and
were unmeasurable. These checks hold the stamp that replaces it: a pure
tag builder, the family key, the watch row, both close journals, and the
report that groups them.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import background_scanner as bs
import diag_setupfamily as dsf

sc = Path("background_scanner.py").read_text(encoding="utf-8")
ap = Path("app.py").read_text(encoding="utf-8")

# ── 1. THE TAG BUILDER: pure, total, JSON-safe ───────────────────────
tags = bs._setup_tags(
    route="champion-fast", tide_state="out", tide_pct=41.0, conviction=88.4,
    brain=64.6, mult=0.5, climb={"ok": False, "fail_reason": "no runway"},
    weak="CLIMB-OUT: no runway", size=1614.0, base=1614.0,
    fast=True, reentry=True, hail=False)
check("stamp: route and tide normalize (tide upper-cased)", 
      tags["route"] == "champion-fast" and tags["tide"] == "OUT")
check("stamp: numbers round to what a group-by reads",
      tags["conviction"] == 88 and tags["brain"] == 65
      and tags["tide_pct"] == 41 and tags["mult"] == 0.5)
check("stamp: a climb dict becomes PASS/FAIL plus its reason",
      tags["climb"] == "FAIL" and tags["climb_why"] == "no runway")
check("stamp: the weak mark IS the min-size mark",
      tags["min_size"] is True and tags["weak"] == "CLIMB-OUT: no runway")
check("stamp: version is stamped so a shape change is detectable",
      tags["v"] == bs.SETUP_TAGS_VERSION == 1)
check("stamp: every value is JSON-safe (no datetimes, no dicts)",
      json.loads(json.dumps(tags)) == tags)

_empty = bs._setup_tags()
check("stamp: an empty call is all-None/False, never a raise",
      _empty["route"] is None and _empty["climb"] is None
      and _empty["min_size"] is False and _empty["size"] is None)
check("stamp: junk in never raises and never leaks a non-scalar",
      bs._setup_tags(tide_state=None, tide_pct="x", conviction=None,
                     climb="", size="n/a", base=object())["size"] is None)

# ── 2. THE FAMILY KEY ────────────────────────────────────────────────
check("family: route/tide/climb[/MIN]",
      bs._setup_family(tags) == "champion-fast/OUT/climb:FAIL/MIN")
check("family: a clean fire has no MIN marker",
      bs._setup_family(bs._setup_tags(route="mover", tide_state="FLAT",
                                      climb={"ok": True}))
      == "mover/FLAT/climb:PASS")
check("family: an unstamped row is its own bucket, never dropped",
      bs._setup_family({}) == "untagged"
      and bs._setup_family(None) == "untagged")

# ── 3. THE ROW CARRIES IT, THE CLOSE KEEPS IT ────────────────────────
_orig_watch = bs.CONTEST_WATCH_JSON
_tmpdir = tempfile.mkdtemp(prefix="setupstamp_")
_watch_path = os.path.join(_tmpdir, "watch.json")
bs.CONTEST_WATCH_JSON = _watch_path
try:
    ok = bs.auto_track_popup("ZEC/USD", 50.0, 330.0, 2.0, 180.0,
                             "BUY 74% (champion)", "champion",
                             setup=bs._setup_tags(route="champion",
                                                  tide_state="FLAT",
                                                  conviction=74,
                                                  climb={"ok": True},
                                                  size=330.0, base=3300.0))
    row = json.loads(Path(bs.CONTEST_WATCH_JSON).read_text())["ZEC/USD"]
    check("row: the fire writes its family onto the watch row",
          ok and row.get("setup", {}).get("conviction") == 74
          and row.get("setup_v") == 1)
    check("row: the row's own numbers still ride along untouched",
          row["size"] == 330.0 and row["goal_pct"] == 2.0)
    ok2 = bs.auto_track_popup("ADA/USD", 1.0, 100.0, 2.0, 180.0,
                              "BUY 70% (mover)", "mover")
    row2 = json.loads(Path(bs.CONTEST_WATCH_JSON).read_text())["ADA/USD"]
    check("row: a route that passes no stamp lands untagged, never broken",
          ok2 and row2.get("setup") == {} and row2.get("setup_v") == 0)
finally:
    bs.CONTEST_WATCH_JSON = _orig_watch

check("close: both journals write the stamp through ONE helper",
      # Nov 15: two inline copies of the same three keys was how a close
      # could lose one silently; one helper, called by both writers
      sc.count("**_family_fields(w),") == 1
      and "**_bst._family_fields(_sw)," in ap)
check("close: the helper keeps setup + setup_v and ADDS strategy_v",
      bs._family_fields({"setup": tags, "setup_v": 1,
                         "strategy_v": 7})
      == {"setup": tags, "setup_v": 1, "strategy_v": 7,
          # Nov 15: the champion's timing number and the timing decision
          # ride the same helper
          "entry_quality": None, "entry_quality_v": 0,
          "entry_timing": None, "entry_timing_v": 0,
          # Nov 15: the deep read and the spend decision ride it too
          "spend_capacity": None, "spend_rank": None, "spend_v": 0,
          # Nov 15: the buy-low score and decision ride it too
          "buy_low": None, "buy_low_rank": None, "buy_low_v": 0,
          # Nov 15: the exit study's measurement rides the close too
          "exit_study": None, "exit_study_v": 0})
check("close: a legacy row without the stamp still lands versioned",
      bs._family_fields({"entry": 1.0})
      == {"setup": {}, "setup_v": 0,
          "strategy_v": bs.STRATEGY_VERSION,
          "entry_quality": None, "entry_quality_v": 0,
          "entry_timing": None, "entry_timing_v": 0,
          "spend_capacity": None, "spend_rank": None, "spend_v": 0,
          "buy_low": None, "buy_low_rank": None, "buy_low_v": 0,
          # Nov 15: the exit study's measurement rides the close too
          "exit_study": None, "exit_study_v": 0})
check("close: strategy_v is the MVP's rule counter, not the tag version",
      bs.STRATEGY_VERSION == 1)
check("row: the fire stamp carries strategy_v onto the watch row",
      # read from the temp watch file captured above, never the live one
      json.loads(Path(_watch_path).read_text())["ZEC/USD"]
      .get("strategy_v") == bs.STRATEGY_VERSION)

# ── 3b. THE PROOF ROW: a close that carries both ─────────────────────
_proof_setup = bs._setup_tags(route="champion", tide_state="OUT",
                              conviction=61, climb={"ok": True},
                              size=900.0, base=900.0)
_proof = {"sold_at": "2026-11-15T00:00:00-07:00", "pair": "PROOF/USD",
          "entry": 10.0, "exit": 10.1, "size": 900.0,
          "pnl_net": 4.20, "closed_by": "getout",
          **bs._family_fields({"setup": _proof_setup,
                               "setup_v": bs.SETUP_TAGS_VERSION,
                               "strategy_v": bs.STRATEGY_VERSION})}
_proof_path = os.path.join(_tmpdir, "proof_trades.jsonl")
with open(_proof_path, "w", encoding="utf-8") as _pf:
    _pf.write(json.dumps(_proof) + "\n")
_back = json.loads(Path(_proof_path).read_text().strip())
check("proof: the written row carries setup + setup_v + strategy_v",
      _back["setup"]["route"] == "champion"
      and _back["setup_v"] == bs.SETUP_TAGS_VERSION
      and _back["strategy_v"] == bs.STRATEGY_VERSION, _back)
check("proof: the report groups that row instead of calling it untagged",
      "tagged: 1/1" in "\n".join(dsf.run_study(Path(_proof_path)))
      and "champion/OUT/climb:PASS" in "\n".join(
          dsf.run_study(Path(_proof_path))))
check("routes: every fire route passes a stamp",
      # Nov 6: the champion stamp is HOISTED above the fire gate (the mandate
      # judges the same family the watch records); the mover route builds its
      # stamp inline. Nov 15: the stale `_st_setup` expectation (renamed when
      # the sure-thing stamp was inlined) is dropped — the count now matches
      # the two call sites that exist: 1 hoisted + 1 inline + the definition.
      sc.count("setup=_fire_setup") == 1
      and sc.count("setup=_setup_tags(") == 1
      and sc.count("auto_track_popup(") == 3)   # 2 calls + the definition

# ── 4. THE REPORT GROUPS BY IT ───────────────────────────────────────
_rows = [
    {"pair": "A/USD", "pnl_net": 10.0,
     "setup": {"route": "champion", "tide": "FLAT", "climb": "PASS",
               "min_size": False}},
    {"pair": "B/USD", "pnl_net": -12.0,
     "setup": {"route": "champion", "tide": "FLAT", "climb": "PASS",
               "min_size": True}},
    {"pair": "C/USD", "pnl_net": -3.0,
     "setup": {"route": "champion", "tide": "FLAT", "climb": "PASS",
               "min_size": True}},
    {"pair": "D/USD", "pnl_net": -1.0},          # legacy, no stamp
]
_tp = os.path.join(_tmpdir, "trades.jsonl")
Path(_tp).write_text("\n".join(json.dumps(r) for r in _rows) + "\n",
                     encoding="utf-8")
_rep = "\n".join(dsf.run_study(Path(_tp)))
check("report: the two families split on the MIN marker",
      "champion/FLAT/climb:PASS " in _rep
      and "champion/FLAT/climb:PASS/MIN" in _rep)
check("report: the MIN family's expectancy is negative, the clean one positive",
      "   -15.00   -7.50" in _rep and "    10.00   10.00" in _rep)
check("report: legacy rows are counted, not lost",
      "untagged" in _rep and "tagged: 3/4" in _rep)
check("report: the verdict refuses to crown a 2-row family",
      "no family pays at n >= 40" in _rep)

print("\n".join(lines))
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
