"""diag_firstflipback.py — THE FIRST REAL FLIP-BACK, END TO END (Sep 13).

No real flip-back has landed in production yet (all 7 trail rows are
seeded sims), so this probe drives the REAL machinery over a sandboxed
ledger — the suite's own _mkweek shape and sandbox discipline — until
one genuinely fires, then verifies every link of the chain in the
PRODUCTION PASS ORDER (evaluate → bench_final → reckoning →
save_flips → persist, then the next grade reads it):

  1. FIRE: _evaluate_flips fires a real flip_back (ww=2, rd>0, bar read)
  2. STAMP: the event dict carries decision-time pool (roll_delta),
     streak (winning_weeks), and the resolved bar (pool_bar)
  3. PERSIST (production order): the flip@W39 lands first, then
     _attach_bench_final snapshots the bench's closing live verdict,
     then the flip_back persists — deduped on (state, action, week);
     _real_flip_events reads them back as REAL events (sims never
     grade — these must not be one)
  4. GRADED: the real grade_ledger embeds the event in both dial
     families — poolgate (0.25/0.50 both clear rd=+1.38 -> no denial)
     and poolgate_streak (FBW=3 delays ww=2, FBW=4 denies it)
  5. RECKONING: the borrowed COSTING verdict the flip-back closed
     under meets the realized dollars — the pass hook stamps
     borrow_reckoning once (here it DISAGREES: the re-arm paid),
     warns into the log, and is idempotent; the monthly grade
     tallies it and the audit trail renders it loud
  6. SIDECAR: _write_flipgrade_json travels the dial blocks
  7. PLATE: _flipgrade_verdict renders the graded verdict
Run: .venv\\Scripts\\python.exe -X utf8 diag_firstflipback.py
"""
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
os.environ["SCOUT_PREFS_FILE"] = str(
    Path(tempfile.mkdtemp()) / "prefs.json")   # app import never touches prefs

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("[PASS] " if ok else "[FAIL] ") + name)
    if not ok:
        print("       detail:", detail)

import tidebar_weekly as tbw

# ── sandbox: every production file the machinery touches ──────────
sbx = Path(tempfile.mkdtemp())
_real = {k: getattr(tbw, k) for k in
         ("HISTORY_FILE", "FLIPGRADE_FILE", "FLIP_EVENTS_FILE",
          "FLIP_FILE", "FLIPGRADE_JSON", "POOLMIN_LEARN_FILE",
          "POOLMIN_DEF_FILE", "POOLMIN_STAGED_FILE",
          "RECKONING_WINDOW_FILE")}
try:
    tbw.HISTORY_FILE = sbx / "hist.jsonl"
    tbw.FLIPGRADE_FILE = sbx / "fg.txt"
    tbw.FLIP_EVENTS_FILE = sbx / "ev.jsonl"
    tbw.FLIP_FILE = sbx / "flips.json"
    tbw.FLIPGRADE_JSON = sbx / "fg.json"
    tbw.POOLMIN_LEARN_FILE = sbx / "learned.json"
    tbw.POOLMIN_DEF_FILE = sbx / "pmdf.json"
    tbw.POOLMIN_STAGED_FILE = sbx / "staged.json"
    # a completed grade records its reckoning window row — sandboxed
    tbw.RECKONING_WINDOW_FILE = sbx / "reckoning_window.jsonl"

    def mkweek(tag, di, do, src="quantile"):
        st = {"in_force": {"fires": 10, "payers": 3, "rate": 0.3,
                           "usd": 10 * di, "usd_per_fire": di},
              "other": {"fires": 10, "payers": 3, "rate": 0.3,
                        "usd": 10 * do, "usd_per_fire": do},
              "other_src": src}
        return {"week": tag, "generated_ts": tag, "states": {"OUT": st}}

    # W36-39: the fit loses 3 weeks running with a negative pool -> flip
    H = [mkweek("2026-W36", 2.0, 1.0),
         mkweek("2026-W37", 1.0, 1.5),
         mkweek("2026-W38", 1.0, 2.0),
         mkweek("2026-W39", 1.0, 2.5)]
    # W40-41: flipped regime, the fit wins its counterfactual 2 weeks
    # running -> the real flip-back fires at W41. W42-43: the fit is
    # LIVE again (src back to quantile) — the recovery continues and
    # PROVES DURABLE (two paying weeks), so the borrowed COSTING
    # verdict will have its reckoning graded (and disagree).
    H += [mkweek("2026-W40", 1.0, 5.0, src="outcome-fitted"),
          mkweek("2026-W41", 1.0, 5.0, src="outcome-fitted"),
          mkweek("2026-W42", 5.0, 1.0),
          mkweek("2026-W43", 5.0, 1.0)]
    tbw.HISTORY_FILE.write_text(
        "\n".join(json.dumps(r) for r in H) + "\n", encoding="utf-8")

    # ── link 1+2: the real flip_back fires with its decision stamps ──
    # THE REAL DRIVER walks one new week at a time, carrying the
    # persisted flows forward — exactly what the weekly pass does.
    walk_events = []
    flows = {}
    for i in range(1, len(H) + 1):
        evs, flows = tbw._evaluate_flips(H[:i], flows)
        e = evs.get("OUT")
        if e:
            walk_events.append((H[i - 1]["week"], e))
    check("the walk fires exactly two decisions: flip@W39, flip_back@W41",
          [(w, e["action"]) for w, e in walk_events] ==
          [("2026-W39", "flip"), ("2026-W41", "flip_back")],
          [(w, e["action"]) for w, e in walk_events])
    ev = dict(walk_events[-1][1])   # the W41 flip_back, as the pass holds it
    check("decision-time STAMP: winning_weeks=2 (the streak dial's axis)",
          ev.get("winning_weeks") == 2, ev.get("winning_weeks"))
    check("decision-time STAMP: roll_delta is the 4-week POOLED edge "
          "(+1.38: W38/W39 losses pool against W40/W41 +4 wins)",
          ev.get("roll_delta") == 1.38, ev.get("roll_delta"))
    check("the stamp cleared the bar it was read against (1.38 >= 0.0)",
          ev.get("roll_delta", 0) >= (ev.get("pool_bar") or 0.0), ev)
    check("decision-time STAMP: pool_bar=0.0 (the bar the gate read)",
          ev.get("pool_bar") == 0.0, ev.get("pool_bar"))
    check("evidence weeks ride the record",
          [w["week"] for w in ev.get("evidence", [])] ==
          ["2026-W40", "2026-W41"], ev.get("evidence"))

    check("the flip's stamp is stamped too: losing_weeks=3, roll_delta<0",
          walk_events[0][1].get("losing_weeks") == 3
          and (walk_events[0][1].get("roll_delta") or 0) < 0,
          walk_events[0][1])

    # ── link 3: persisted in the PRODUCTION PASS ORDER ────────────────
    # the flip lands in its own pass first, _attach_bench_final reads
    # the trail (the flip must already be there) BEFORE the back
    # persists, and the daily-pass double-write dedupes.
    ts = time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())
    tbw._persist_flip_events({"OUT": walk_events[0][1]}, "2026-W39", ts)
    i41 = next(i for i, r in enumerate(H) if r["week"] == "2026-W41")
    tbw._attach_bench_final({"OUT": ev}, H[:i41 + 1])
    tbw._persist_flip_events({"OUT": ev}, "2026-W41", ts)
    tbw._persist_flip_events({"OUT": ev}, "2026-W41", ts)
    rows = [json.loads(l) for l in
            tbw.FLIP_EVENTS_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    check("persisted exactly once per decision (deduped on state+action+week)",
          len(rows) == 2
          and sum(1 for r in rows if r["action"] == "flip_back") == 1, rows)
    real = tbw._real_flip_events()
    check("_real_flip_events reads them back as REAL events (not sims)",
          len(real) == 2 and not any(r.get("sim") for r in real), real)
    back = next(r for r in real if r["action"] == "flip_back")
    check("the persisted stamp survives the round-trip",
          back["winning_weeks"] == 2 and back["roll_delta"] == 1.38
          and back["pool_bar"] == 0.0, back)
    check("BENCH FINAL: the event carries the bench's closing live "
          "verdict, computed on the history AS THE PASS SAW IT",
          isinstance(back.get("bench_final"), dict)
          and back["bench_final"].get("verdict") in ("PAYING", "COSTING")
          and isinstance(back["bench_final"].get("to_date"), (int, float)),
          back.get("bench_final"))
    check("BENCH FINAL: the benched quantile was down $80 when the "
          "bench ended ($1/fire vs the fit's $5 over W40-41)",
          back["bench_final"].get("verdict") == "COSTING"
          and back["bench_final"].get("to_date") == -80.0,
          back.get("bench_final"))

    # ── link 4: the real grade_ledger grades it, both dials ───────────
    summary = tbw.grade_ledger()
    check("the grade COMPLETED (7 weeks >= FLIPGRADE_MIN_WEEKS)",
          "held" not in summary, summary.get("held"))
    pg = summary.get("poolgate") or {}
    check("POOL dial grades the real stamp: 0.25 and 0.5 both priced",
          set(pg) == {0.25, 0.5}, pg)
    check("POOL dial: rd=+1.38 clears both bars -> nothing denied",
          all((pg[k] or {}).get("denied", 0) == 0 for k in pg), pg)
    stk = summary.get("poolgate_streak") or {}
    check("STREAK dial grades the real stamp: FBW 3 and 4 both priced",
          set(stk) == {3, 4}, stk)
    check("STREAK dial: ww=2 -> FBW=3 re-arms ON W42 (a re-arm fires on "
          "its week — the lived fit went live at W42 too) so its delay "
          "prices $0; FBW=4 re-arms at W43, missing only W42",
          (stk[3] or {}).get("delayed", 0) == 1
          and (stk[3] or {}).get("net", 1) == 0.0
          and (stk[4] or {}).get("delayed", 0) == 1
          and (stk[4] or {}).get("denied", 0) == 0, stk)
    check("STREAK dial: FBW=4's denial costs the fit its W42 "
          "outperformance ($4/fire x 10 fires = $40)",
          (stk[4] or {}).get("net") == -40.0, stk[4])
    check("sidecar carries both dial verdicts",
          tbw.FLIPGRADE_JSON.exists()
          and "poolgate" in tbw.FLIPGRADE_JSON.read_text(encoding="utf-8")
          and "poolgate_streak" in tbw.FLIPGRADE_JSON.read_text(encoding="utf-8"))

    # ── link 5: THE BORROW'S RECKONING, end to end ────────────────────
    # the flip-back closed under the borrowed COSTING verdict; now its
    # governed window (W42-43) has graded. The pass hook stamps the
    # reckoning ONCE, warns into the log, and is idempotent. Here the
    # re-arm PAID ($5 vs $1) — the borrowed COSTING must DISAGREE, with
    # the dollars clause naming the read-backwards gap.
    _span = tbw._poolgate_span_savings(tbw._load_history(), "OUT",
                                       "2026-W42", None)
    check("PRE-GEOMETRY: the re-arm's window prices the fit +$4/fire "
          "over the quantile ($40/week) — the re-arm PAID",
          _span == -80.0, _span)

    class _Cap(logging.Handler):
        def __init__(self):
            super().__init__()
            self.recs = []

        def emit(self, record):
            self.recs.append(record)

    _cap = _Cap()
    tbw.logger.addHandler(_cap)
    try:
        stamped = tbw._attach_borrow_reckoning(tbw._load_history())
    finally:
        tbw.logger.removeHandler(_cap)
    check("the pass hook stamps OUT's flip-back (aftermath graded)",
          stamped == ["OUT"], stamped)
    _warns = [r.getMessage() for r in _cap.recs
              if r.levelno == logging.WARNING
              and r.getMessage().startswith("BORROW RECKONING")]
    check("one WARN lands in the log, disagreement named loud",
          len(_warns) == 1 and "OUT DISAGREES" in _warns[0], _warns)
    rk_ev = next(e for e in tbw._load_flip_events()
                 if e.get("action") == "flip_back" and not e.get("sim"))
    rk = rk_ev.get("borrow_reckoning") or {}
    check("RECKONING stamp: the borrow said COSTING, the realized "
          "verdict is PAYING -> DISAGREES",
          rk.get("status") == "disagree" and rk.get("borrowed") == "COSTING"
          and rk.get("graded") == "PAYING", rk)
    check("RECKONING stamp: the dollars clause prices the gap "
          "($+4.00/fire the borrow read backwards)",
          rk.get("gap_per_fire") == 4.0
          and "$+4.00/fire" in (rk.get("text") or ""), rk.get("text"))

    _trail_txt = tbw.FLIP_EVENTS_FILE.read_text(encoding="utf-8")
    stamped2 = tbw._attach_borrow_reckoning(tbw._load_history())
    check("idempotent: the second pass re-stamps nothing",
          stamped2 == []
          and tbw.FLIP_EVENTS_FILE.read_text(encoding="utf-8") == _trail_txt,
          stamped2)

    # the NEXT monthly grade reads the stamp: tallied, and every priced
    # dial line carries the reckoning clause
    summary2 = tbw.grade_ledger()
    rk_sum = summary2.get("poolgate_reckoning") or {}
    check("the monthly grade tallies the reckoning (1 graded, 0 agreed, "
          "1 disagreed, no cluster below 3)",
          rk_sum.get("graded") == 1 and rk_sum.get("agree") == 0
          and rk_sum.get("disagree") == 1 and rk_sum.get("cluster") is False,
          rk_sum)
    fg_txt2 = tbw.FLIPGRADE_FILE.read_text(encoding="utf-8")
    check("the priced DENY/DELAY lines carry the reckoning clause",
          "reckoning: the borrow disagreed" in fg_txt2,
          [l.strip() for l in fg_txt2.splitlines() if "disagreed" in l][:2])
    stk2 = summary2.get("poolgate_streak") or {}
    check("each dial's own denials tally the disagreement (per-dial "
          "evidence; a clean dial is never tarred)",
          all(any(ev.get("reckoning") == "disagree"
                  for ev in (stk2[fw] or {}).get("events", []))
              for fw in (3, 4)), stk2)
    pg2 = summary2.get("poolgate") or {}
    check("the pool dial stays CLEAN — nothing denied, nothing to tally",
          all(not (pg2[k] or {}).get("events") for k in pg2), pg2)

    # ── THE ROLLING REGIME WINDOW: a completed grade records its row ──
    # (the diag's two grades ran within the same minute — the ts dedupe
    # correctly treats the second's CHANGED totals as progression, not
    # a re-record of itself: the pre-stamp 0/0 row, then the 0/1 one)
    wrows = tbw._load_reckoning_window()
    check("the completed grade recorded its window row (the pre-stamp "
          "0/0, then 0 agree / 1 disagree with contribution d_disagree=1)",
          len(wrows) == 2 and wrows[0]["agree"] == 0
          and wrows[0]["disagree"] == 0
          and wrows[1]["agree"] == 0 and wrows[1]["disagree"] == 1
          and wrows[1]["d_agree"] == 0 and wrows[1]["d_disagree"] == 1,
          wrows)
    check("the grade's own window view rode the summary (2 rows — the "
          "current grade's contribution rides its own view — span=1, "
          "so one grade alone cannot flag)",
          len((summary2.get("poolgate_reckoning") or {}).get("window")
              or []) == 2
          and (summary2.get("poolgate_reckoning") or {})["cluster"] is False,
          summary2.get("poolgate_reckoning"))
    check("the txt names the regime read (THE WINDOW'S VERDICT)",
          "THE WINDOW'S VERDICT" in fg_txt2,
          [l.strip() for l in fg_txt2.splitlines()
           if "WINDOW'S VERDICT" in l][:1])
    check("ONE-OFF vs REGIME: the same disagreement burst confined to "
          "one grade never flags — but spread over two grades it does",
          tbw._reckoning_cluster_from_window(
              tbw._reckoning_window_view(
                  [{"agree": 0, "disagree": 1, "d_agree": 0,
                    "d_disagree": 1}])) is False
          and tbw._reckoning_cluster_from_window(
              tbw._reckoning_window_view(
                  [{"agree": 0, "disagree": 1, "d_agree": 0,
                    "d_disagree": 1},
                   {"agree": 1, "disagree": 2, "d_agree": 1,
                    "d_disagree": 1}])) is True,
          "span-1 False, span-2 True")
    check("HEALED GRADES SLIDE OUT: a regime flag clears once the "
          "window fills with clean grades",
          tbw._reckoning_cluster_from_window(
              tbw._reckoning_window_view(
                  [{"agree": 0, "disagree": 1, "d_agree": 0,
                    "d_disagree": 1},
                   {"agree": 2, "disagree": 2, "d_agree": 2,
                    "d_disagree": 0},
                   {"agree": 4, "disagree": 2, "d_agree": 2,
                    "d_disagree": 0},
                   {"agree": 6, "disagree": 2, "d_agree": 2,
                    "d_disagree": 0}])) is False,
          "disagreements aged out of the 4-grade view")

    # the audit trail renders the reckoning row loud, and the census
    # chip's tally agrees with the stamp
    import app as app_mod
    html_tr = app_mod._render_flip_audit(hide_sims=True)
    low_tr = (html_tr or "").lower()
    check("the audit trail renders the reckoning row (DISAGREES, orange)",
          "disagrees" in low_tr and "reckoning" in low_tr, low_tr[:300])
    check("the audit trail renders the bench's closing verdict",
          "costing when it ended" in low_tr
          and "$80.00" in (html_tr or ""), low_tr[:300])
    check("reckoning_tally: the census carries '0 agreed · 1 disagreed'",
          tbw.reckoning_tally(tbw._load_flip_events())
          == " · 0 agreed · 1 disagreed",
          tbw.reckoning_tally(tbw._load_flip_events()))

    # ── link 6+7: sidecar + plate render the graded verdict ───────────
    _fgv = app_mod._flipgrade_verdict
    html = _fgv(txt_path=str(tbw.FLIPGRADE_FILE),
                json_path=str(tbw.FLIPGRADE_JSON))
    check("the plate renders (non-empty)", bool(html), type(html))
    low = (html or "").lower()
    check("the plate shows the dial verdicts",
          "pool" in low and "streak" in low or "fbw" in low, low[:200])
finally:
    for k, v in _real.items():
        setattr(tbw, k, v)
    shutil.rmtree(sbx, ignore_errors=True)

fails = [r for r in results if not r[1]]
print()
print(f"E2E: {len(results) - len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
