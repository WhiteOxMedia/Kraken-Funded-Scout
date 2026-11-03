r"""diag_benchfinal.py - the bench's final verdict snapshot.

SANDBOX ASSERTION GUARD: an earlier draft of this diag dropped its
sandbox assignments (a truncated heredoc) and ran report_now against
PRODUCTION ledgers. The assert below makes that failure class loud.

Proves, on a sandboxed report_now() arc (today = 2026-W37):
  1. hand-seeded history: W32-34 bleed (the fit in force), W35-36 win
     (benched: the quantile ships $1.00/fire, the fit's counterfactual
     $3.00/fire), plus a hand-persisted real flip event (W34, evidence
     W32-34) and a benched flips file. The staged W37 wins too.
  2. the gate flips OUT back at W37 - and the flip-back EVENT RECORD
     carries bench_final: weeks 3, to_date -60.0, COSTING.
  3. the flip-back row in the audit trail renders the preserved
     verdict; the flip row carries nothing.
  4. dedupe: re-running the pass cannot double-persist the event.
  5. no fabricated verdicts (no flip on the trail -> no snapshot).
Run: .venv\Scripts\python.exe -X utf8 diag_benchfinal.py
"""
import json
import shutil
from pathlib import Path

import tide_learn as tl
import tidebar_weekly as tbw
import app as app_mod

BASE = Path(__file__).parent
S = BASE / "_test_benchfinal_dir"
shutil.rmtree(S, ignore_errors=True)
S.mkdir()

NAMES = {"OUT_FILE": "w.txt", "JSON_FILE": "s.json",
         "SNAP_OUTCOMES_FILE": "so.jsonl", "TRADES_FILE": "t.jsonl",
         "HISTORY_FILE": "h.jsonl", "FLIP_FILE": "f.json",
         "FLIPGRADE_FILE": "fg.txt", "FLIPGRADE_JSON": "fg.json",
         "FLIP_EVENTS_FILE": "e.jsonl"}
_saved_tbw = {k: getattr(tbw, k) for k in NAMES}
for _k, _n in NAMES.items():
    setattr(tbw, _k, S / _n)
assert all(str(getattr(tbw, _k)).startswith(str(S)) for _k in NAMES), \
    {k: str(getattr(tbw, k)) for k in NAMES}

MODEL = {"err": None, "hours": 500, "generated_ts": "t",
         "window": ["a", "b"],
         "states": {"OUT": {"payer_rate": 0.109, "hours": 479,
                            "bar": 92.0, "bar_src": "outcomes",
                            "outcome_fit": {"n": 316}},
                    "FLAT": {"payer_rate": 0.105, "hours": 551,
                             "bar": 92.0, "bar_src": "quantile"},
                    "RISING": {"payer_rate": 0.23, "hours": 153,
                               "bar": None, "bar_src": "quantile",
                               "why": "thin"}}}

PASS = 0
def check(name, cond, detail=""):
    global PASS
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        print("      detail:", str(detail)[:900])
    PASS += bool(cond)

def _rec(wk, inf_pf, oth_pf, src):
    return {"week": wk, "generated_ts": "t", "states": {"OUT": {
        "in_force": {"fires": 10, "payers": 1,
                     "usd": round(10 * inf_pf, 2),
                     "usd_per_fire": round(inf_pf, 2)},
        "other": {"fires": 10, "payers": 1,
                  "usd": round(10 * oth_pf, 2),
                  "usd_per_fire": round(oth_pf, 2)},
        "other_src": src}}}

try:
    real_cand = tbw._week_candidates
    real_learn = tl.learn
    real_q = tl._funnel_score_quantiles
    real_grade = tbw._grade
    real_fitbar = tl._outcome_fit_bar
    # deterministic dollars: the LOW bar (86, the quantile) realizes
    # $1.00/fire, the HIGH bar (92, the refit) $3.00/fire - so the
    # staged week's fit edge is +2.00 in either regime.
    tbw._grade = lambda cands, bar: (
        None if bar is None else
        {"bar": bar, "fires": 10, "payers": 1, "rate": 0.1,
         "usd": 10.0 if bar < 90 else 30.0,
         "usd_per_fire": 1.0 if bar < 90 else 3.0})
    tbw._week_candidates = lambda: ([
        ("OUT", 88.0, True)] * 10 + [
        ("OUT", 93.0, True)] * 10 + [
        ("FLAT", 88.0, True)], None)
    # the stub models the REAL learner's flips-awareness: a benched
    # state ships the QUANTILE (bar 86) - not the MODEL's bar_src
    def _learn_stub(*a, **k):
        m = dict(MODEL)
        if ((tbw._load_flips() or {}).get("OUT") or {}).get("flipped"):
            m["states"]["OUT"] = dict(m["states"]["OUT"],
                                      bar=86.0, bar_src="quantile")
        return m
    tl.learn = _learn_stub
    tl._funnel_score_quantiles = lambda: [50.0] * 60
    # the fit's counterfactual bar: 92 (the $3.00 seat) - the real fit
    # bar on constant-50 scores would sit at 80 and collapse the edge
    tl._outcome_fit_bar = lambda st, sc, arr_cache=None: (
        {"bar": 92.0, "ev_o": 3.0, "ev_q": 1.0, "n": 100,
         "payer_rate": 0.1, "state": st} if st == "OUT" else
        {"bar": 92.0, "ev_o": 2.0, "ev_q": 2.0, "n": 100,
         "payer_rate": 0.105, "state": st})

    # the seed: W32-34 the fit bleeds (in force, un-benched), W35-36
    # the bench's winning weeks (the quantile ships, the fit's
    # counterfactual out-earns it), and the hand-persisted real flip.
    tbw.HISTORY_FILE.write_text("".join(
        json.dumps(_rec(f"2026-W{n}", 1.0, 3.0, "quantile"
                        if n <= 34 else "outcome-fitted")) + "\n"
        for n in (32, 33, 34, 35, 36)), encoding="utf-8")
    tbw.FLIP_FILE.write_text(json.dumps(
        {"OUT": {"flipped": True, "since": "t"},
         "FLAT": {"flipped": False}, "RISING": {"flipped": False}}),
        encoding="utf-8")
    tbw._persist_flip_events(
        {"OUT": {"action": "flip", "losing_weeks": 3, "roll_delta": -2.0,
                 "evidence": [{"week": f"2026-W{n}", "fit_edge": -2.0}
                              for n in (32, 33, 34)]}},
        "2026-W34", "t")
    assert len(tbw._load_flip_events()) == 1

    tbw.report_now()   # stages W37 (a winning week); the gate flips back

    evs = tbw._load_flip_events()
    fb = next((e for e in evs if e.get("action") == "flip_back"), None)
    check("the gate flipped OUT back at W37 (the pass's own week)",
          fb is not None and fb.get("week") == "2026-W37"
          and not fb.get("sim"), evs)

    bf = (fb or {}).get("bench_final")
    check("the flip-back record carries the bench's FINAL verdict",
          isinstance(bf, dict) and bf.get("verdict") == "COSTING", fb)

    # OPEN window: the flip's newest evidence week is W34, so the open
    # aftermath reads W35-W37: realized (the quantile) 3x$10 = $30,
    # counterfactual (the fit) 3x$30 = $90 -> to_date -$60.00.
    check("the snapshot graded the OPEN window (W35-37: -60.0, COSTING)",
          bf.get("to_date") == -60.0 and bf.get("weeks") == 3, bf)
    check("the text carries the cumulative clause (down $60.00)",
          "to date the bench is down $60.00 cumulative"
          in (bf.get("text") or ""), bf)

    aud = app_mod._render_flip_audit()
    check("the flip-back row renders the preserved final verdict",
          "the bench's final live verdict" in aud
          and "the bench was COSTING when it ended" in aud, aud[-900:])
    check("no OPEN bench strip (OUT is un-benched now)",
          "CURRENTLY BENCHED" not in aud, aud[-400:])
    check("the flip row carries no final verdict",
          aud.count("the bench's final live verdict") == 1, aud)

    n = len(tbw._load_flip_events())
    tbw.report_now()
    check("re-running the pass cannot double-persist the event (the "
          "snapshot rides the dedupe)",
          len(tbw._load_flip_events()) == n,
          (n, len(tbw._load_flip_events())))

    ev2 = {"FLAT": {"action": "flip_back", "winning_weeks": 2,
                    "roll_delta": 1.0,
                    "evidence": [{"week": "2026-W39", "fit_edge": 1.0}]}}
    tbw._attach_bench_final(ev2, [])
    check("no flip on the trail / thin ledger -> no snapshot, never fake",
          "bench_final" not in ev2["FLAT"], ev2)
finally:
    tbw._week_candidates = real_cand
    tl.learn = real_learn
    tl._funnel_score_quantiles = real_q
    tl._outcome_fit_bar = real_fitbar
    tbw._grade = real_grade
    for k, v in _saved_tbw.items():
        setattr(tbw, k, v)
    shutil.rmtree(S, ignore_errors=True)

print(f"\n{'ALL GREEN' if PASS == 9 else 'FAILURES PRESENT'} - {PASS}/9 checks")
raise SystemExit(0 if PASS == 9 else 1)
