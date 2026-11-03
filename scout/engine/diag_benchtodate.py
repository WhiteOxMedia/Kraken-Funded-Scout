r"""diag_benchtodate.py — the cumulative bench cost-to-date.

Proves, on a SANDBOXED ledger (production files redirected via the
module's own globals, same convention as the other diags):
  1. _event_aftermath now carries usd_total on BOTH sides (realized =
     counterfactual dollars pooled over the same post-decision weeks).
  2. _open_bench_entries adds to_date = realized_usd - counterfactual_usd
     and appends the cumulative clause to its text.
  3. the sidecar (_flipgrade.json) carries both, on the open window.
  4. the audit trail's CURRENTLY BENCHED strip renders the shared
     entries - with the to-date clause - and never re-counts.
  5. the plate renders the sidecar's text (the to-date clause rides in).
All paths redirected; production files are never touched.
Run: .venv\Scripts\python.exe -X utf8 diag_benchtodate.py
"""
import json
import os
from pathlib import Path

import tidebar_weekly as tbw
import app as app_mod

BASE = Path(__file__).parent
_p = lambda n: BASE / n   # the module's constants are Path objects

# sandbox: 3-week ledger, OUT flipped at W37 (evidence W37).
# The governed window (open) = W39..W41. Each week: the quantile
# realized 10 fires at $2.00/fire = $20; the fit's counterfactual side
# realized 10 fires at $3.00/fire = $30. Over 2 post-decision weeks:
#   realized $40 vs counterfactual $60  ->  to_date = -$20 (COSTING)
H = [
    {"week": "2026-W37", "generated_ts": "t", "states": {"OUT": {
        "in_force": {"fires": 10, "usd": 20.0, "usd_per_fire": 2.0},
        "other": {"fires": 10, "usd": 30.0, "usd_per_fire": 3.0},
        "other_src": "outcome-fitted"}}},
    {"week": "2026-W39", "generated_ts": "t", "states": {"OUT": {
        "in_force": {"fires": 10, "usd": 20.0, "usd_per_fire": 2.0},
        "other": {"fires": 10, "usd": 30.0, "usd_per_fire": 3.0},
        "other_src": "outcome-fitted"}}},
    {"week": "2026-W41", "generated_ts": "t", "states": {"OUT": {
        "in_force": {"fires": 10, "usd": 20.0, "usd_per_fire": 2.0},
        "other": {"fires": 10, "usd": 30.0, "usd_per_fire": 3.0},
        "other_src": "outcome-fitted"}}},
]
EV = [{"ts": "2026-09-25 09:00 UTC", "state": "OUT", "action": "flip",
       "week": "2026-W37", "losing_weeks": 3, "roll_delta": -1.28,
       "evidence": [{"week": "2026-W37", "fit_edge": -1.52}]}]
FLIPS = {"OUT": {"flipped": True}, "FLAT": {"flipped": False},
         "RISING": {"flipped": False}}

h_sbx, e_sbx, f_sbx, j_sbx = (_p("_sbx_btod_hist.jsonl"),
                              _p("_sbx_btod_events.jsonl"),
                              _p("_sbx_btod_flips.json"),
                              _p("_sbx_btod_fg.json"))
with open(h_sbx, "w", encoding="utf-8") as w:
    w.write("\n".join(json.dumps(r) for r in H) + "\n")
with open(e_sbx, "w", encoding="utf-8") as w:
    w.write("\n".join(json.dumps(e) for e in EV) + "\n")
with open(f_sbx, "w", encoding="utf-8") as w:
    w.write(json.dumps(FLIPS))

_saved = (tbw.HISTORY_FILE, tbw.FLIP_EVENTS_FILE, tbw.FLIP_FILE,
          tbw.FLIPGRADE_FILE, tbw.FLIPGRADE_JSON)
tbw.HISTORY_FILE = h_sbx
tbw.FLIP_EVENTS_FILE = e_sbx
tbw.FLIP_FILE = f_sbx
tbw.HISTORY_FILE = h_sbx   # _load_history reads the Path constant
tbw.FLIPGRADE_FILE = j_sbx.with_suffix(".txt")
tbw.FLIPGRADE_JSON = j_sbx

PASS = 0
def check(name, cond, detail=""):
    global PASS
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}")
    if not cond:
        print("      detail:", str(detail)[:700])
    PASS += bool(cond)

try:
    weeks_list = [r["week"] for r in H]

    # 1 - the shared math now carries dollars on both sides
    am = tbw._event_aftermath(H, weeks_list, EV[0], "OUT", next_ev=None)
    check("aftermath carries usd_total on both sides (40 vs 60)",
          am["status"] == "graded"
          and am["realized"] == {"bar": "quantile", "fires": 20,
                                 "usd_per_fire": 2.0, "usd_total": 40.0}
          and am["counterfactual"] == {"bar": "fit", "fires": 20,
                                       "usd_per_fire": 3.0,
                                       "usd_total": 60.0}, am)

    # 2 - the shared entries: to_date + the cumulative clause
    flips, entries = tbw._open_bench_entries(H, EV)
    en = entries[0] if entries else {}
    check("the shared entry carries to_date = realized - counterfactual",
          en.get("to_date") == -20.0, entries)
    check("the text names the cumulative clause (down $20.00)",
          "to date the bench is down $20.00 cumulative"
          in (en.get("text") or ""), en.get("text"))
    check("the verdict stays COSTING (per-fire math unchanged)",
          en.get("verdict") == "COSTING" and en.get("verdict_col") == "#ff6b5e",
          en)

    # 3 - the sidecar carries both, on the open window
    tbw._write_flipgrade_json("2026-09-12T12:00:00+00:00")
    j = json.loads(Path(j_sbx).read_text(encoding="utf-8"))
    ob = (j.get("open_bench") or [{}])[0]
    check("the sidecar ships to_date on the open bench",
          ob.get("to_date") == -20.0 and "down $20.00 cumulative"
          in (ob.get("text") or ""), ob)

    # 4 - the audit strip renders the SHARED entries (with to-date)
    aud = app_mod._render_flip_audit()
    check("the audit strip renders the shared to-date clause",
          "live aftermath: over the 2 weeks since" in aud
          and "to date the bench is down $20.00 cumulative" in aud, aud[-800:])
    check("the strip keeps the verdict paint (the bench is COSTING)",
          "the bench is COSTING" in aud, aud[-400:])

    # 5 - the plate renders the sidecar's text (the clause rides in).
    # A minimal hand-built grade section (the plate parses the txt;
    # the sidecar's strip is what this check pins).
    Path(tbw.FLIPGRADE_FILE).write_text(
        "=== THE FLIP GRADE — the bench machinery judged on its "
        "own ledger ===\n"
        "graded at: 2026-09-12 12:00 UTC  (2 weeks: "
        "2026-W38..2026-W39)\n"
        "OUT (1 two-sided weeks; 1 benched)\n"
        "  pooled (2w): fit 10 fires $+3.00/fire vs quantile 10 fires "
        "$+2.00/fire — fit edge +1.00\n",
        encoding="utf-8")
    plate = app_mod._flipgrade_verdict(txt_path=tbw.FLIPGRADE_FILE,
                                       json_path=j_sbx)
    check("the plate renders the sidecar's to-date clause",
          "to date the bench is down $20.00 cumulative" in plate, plate[:1200])

    # 6 - a pending bench: to_date None, no fake dollars
    with open(f_sbx, "w", encoding="utf-8") as w:
        w.write(json.dumps(
            {"OUT": {"flipped": True}, "FLAT": {"flipped": True}}))
    _f2, entries2 = tbw._open_bench_entries(H, EV)
    flat = next((e for e in entries2 if e["state"] == "FLAT"), {})
    check("a benched state with no flip event pends with to_date None",
          flat.get("verdict") is None and flat.get("to_date") is None
          and "no flip event on the trail yet" in flat.get("text", ""),
          flat)
finally:
    (tbw.HISTORY_FILE, tbw.FLIP_EVENTS_FILE, tbw.FLIP_FILE,
     tbw.FLIPGRADE_FILE, tbw.FLIPGRADE_JSON) = _saved
    for p in (h_sbx, e_sbx, f_sbx, j_sbx, j_sbx.with_suffix(".txt")):
        try:
            os.remove(p)
        except OSError:
            pass

print(f"\n{'ALL GREEN' if PASS == 9 else 'FAILURES PRESENT'} - {PASS}/9 checks")
raise SystemExit(0 if PASS == 9 else 1)
