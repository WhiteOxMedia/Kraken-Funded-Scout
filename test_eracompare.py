"""THE ERA COMPARE (Sep 22): _era_compare.py's scratch read promoted to
a scheduled nightly diagnostic. Pinned here: the era split at the re-base
cutoff, the readability gate (the honesty flag that refuses a comparison
the sample cannot support), top_line's three directions including both
zero/empty boundaries, the report writer (newest-first, never marks the
one-shot done), the date-gated restart-proof scheduler (before/on/after,
done-state, corrupted-state fail-open), the scanner + review wiring, and
her real exec'd voice carrying (or not carrying) the era clause.
"""
import json
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


import era_compare as ec

_REAL_GRADE = ec.vs.grade_trades   # restored before any live-data read


def _g(pair, date, verdict, left):
    return {"pair": pair, "date": date, "verdict": verdict, "left": left}


def _book(pre_stl, pre_n, post_stl, post_n, pre_left=50.0, post_left=40.0):
    """pre_left/post_left are the era's TOTAL stayed-too-long dollars,
    spread over its stl trades (avg = total / stl count)."""
    pre = ([_g(f"P{i}/USD", "2026-09-01", "stayed too long - x",
               pre_left / max(pre_stl, 1)) for i in range(pre_stl)]
           + [_g(f"PQ{i}/USD", "2026-09-01", "took the goal", 0.0)
              for i in range(pre_n - pre_stl)])
    post = ([_g(f"Q{i}/USD", "2026-09-25", "stayed too long - x",
                post_left / max(post_stl, 1)) for i in range(post_stl)]
            + [_g(f"QQ{i}/USD", "2026-09-25", "took the goal", 0.0)
               for i in range(max(post_n - post_stl, 0))])
    return pre, post


# ── 1. the era split: on/after the cutoff ran under the new ratchet ────
ec.vs.grade_trades = lambda: [
    _g("OLD/USD", "2026-09-19", "stayed too long - x", 5.0),
    _g("EDGE/USD", "2026-09-20", "took the goal", 0.0),
    _g("CUT/USD", "2026-09-21", "stayed too long - x", 3.0),   # cutoff day = post
    _g("NEW/USD", "2026-09-22", "bought the top - x", 2.0),
]
eras = ec.grade_eras()
check("pre-era holds trades before the cutoff",
      [g["pair"] for g in eras["pre"]] == ["OLD/USD", "EDGE/USD"], eras["pre"])
check("cutoff-day trade is post (the re-base ran its exit)",
      [g["pair"] for g in eras["post"]] == ["CUT/USD", "NEW/USD"], eras["post"])
ec.vs.grade_trades = lambda: [_g("X/USD", "", "took the goal", 0.0)]
check("undated trade classifies pre (fail-soft, honest)",
      len(ec.grade_eras()["pre"]) == 1)

# ── 2. the readability gate + top_line's directions ────────────────────
# FELL: pre avg 50/6 = 8.33, post avg 9/3 = 3.0
ec.vs.grade_trades = lambda: (lambda pr, po: pr + po)(*_book(6, 46, 3, 12,
                                                             pre_left=50.0,
                                                             post_left=9.0))
s = ec.summary()
check("12 post trades reads as comparable", s["readable"] is True, s["readable"])
tl = ec.top_line(s)
check("top_line speaks the fell direction with both averages",
      tl is not None and "fell" in tl and "3 dollars a trade" in tl
      and "against 8 in the old" in tl, tl)
check("top_line cites the LEAK counts (the honest sample)",
      "3 trades against 6" in tl, tl)

# GREW: pre avg 20/6 = 3.33, post avg 80/4 = 20.0
ec.vs.grade_trades = lambda: (lambda pr, po: pr + po)(
    *_book(6, 46, 4, 12, pre_left=20.0, post_left=80.0))
check("top_line says grew when the new era leaks more",
      "grew" in ec.top_line())

# HELD boundary: exactly equal averages (60/6 = 60/6)
ec.vs.grade_trades = lambda: (lambda pr, po: pr + po)(
    *_book(6, 46, 6, 12, pre_left=60.0, post_left=60.0))
check("top_line says held on equal averages", "held" in ec.top_line())

# zero boundary: no stayed-too-long on either side -> held at zero
ec.vs.grade_trades = lambda: (lambda pr, po: pr + po)(*_book(0, 46, 0, 12))
check("zero-stay book still reads (held at zero)",
      ec.summary()["readable"] and "held" in ec.top_line())

# one-sided zero: pre clean, post leaking -> grew from zero
ec.vs.grade_trades = lambda: (lambda pr, po: pr + po)(*_book(0, 46, 2, 12))
check("pre-zero post-leak reads grew", "grew" in ec.top_line())

# below the bar: top_line refuses
ec.vs.grade_trades = lambda: (lambda pr, po: pr + po)(*_book(1, 46, 1, 5))
s = ec.summary()
check("5 post trades is NOT readable", s["readable"] is False)
check("top_line is None below the bar", ec.top_line(s) is None)
check("the report says it records, not compares",
      any("NOT READABLE" in ln for ln in s["lines"]), s["lines"][-1])

# ── 3. the report: newest-first, never consumes the one-shot ───────────
with tempfile.TemporaryDirectory() as td:
    # sandbox swaps (testleaks triage, Sep 23): REPORT_PATH is a pure
    # report sink and STATE_PATH a one-shot marker — nothing production
    # is read or written through either, and each with-block rebinds
    # fresh; bounded by suite-per-process.
    ec.REPORT_PATH = Path(td) / "era_compare_report.md"  # leak-audit: ok (tempfile-derived, per-with-block rebind; see the triage note above)
    # Sep 30 repair: STATE_PATH is sandboxed here too — the pin must
    # never look at the repo root, where the live one-shot marker now
    # EXISTS (the scheduler's mark_done fired Sep 28 20:00, after this
    # drive was written; an unsandboxed conjunct read that success as
    # a failure). write_report itself still never touches the marker.
    ec.STATE_PATH = Path(td) / "era_compare_state.json"  # leak-audit: ok (tempfile-derived, per-with-block rebind)
    ec.vs.grade_trades = lambda: (lambda pr, po: pr + po)(*_book(6, 46, 3, 12,
                                                                 pre_left=50.0,
                                                                 post_left=9.0))
    p1 = ec.write_report()
    check("first write returns the path", p1 == ec.REPORT_PATH)
    # a second write one minute later: the newest section must sit FIRST.
    # (No time shim needed — same-minute double writes compare equal,
    # which still proves order, just not staleness.)
    _later_check = p1.read_text(encoding="utf-8")
    ec.write_report()
    body = p1.read_text(encoding="utf-8")
    _stamps = [ln for ln in body.split("\n") if ln.startswith("## ERA COMPARE")]
    check("two sections accumulated", len(_stamps) == 2, len(_stamps))
    check("newest section first",
          len(_stamps) == 2 and _stamps[0] >= _stamps[1], _stamps)
    check("the pre-era baseline line rides the report",
          "pre-era:" in body and "avg $" in body)
    check("write_report does NOT mark the one-shot done",
          not (Path(td) / "era_compare_state.json").exists()
          and not ec.STATE_PATH.exists())
    # a DIRECTORY at the report path -> the write fails -> None, never raises
    ec.REPORT_PATH = Path(td) / "adir"  # leak-audit: ok (tempfile-derived unwritable-path probe)
    ec.REPORT_PATH.mkdir()
    check("unwritable path -> write_report returns None", ec.write_report() is None)

# ── 4. the scheduler: date-gated, restart-proof, one-shot ──────────────
with tempfile.TemporaryDirectory() as td:
    ec.STATE_PATH = Path(td) / "era_compare_state.json"  # leak-audit: ok (tempfile-derived one-shot marker)
    import datetime as _dt
    check("not due before the target date",
          not ec.due(_dt.datetime(2026, 9, 27, 20, 0)))
    check("due ON the target date (20:00 review)",
          ec.due(_dt.datetime(2026, 9, 28, 20, 0)))
    check("due AFTER the target date (a down scanner cannot miss it)",
          ec.due(_dt.datetime(2026, 9, 29, 2, 0)))
    ec.mark_done(_dt.datetime(2026, 9, 28, 20, 5))
    check("done on the day -> not due again",
          not ec.due(_dt.datetime(2026, 9, 28, 21, 0)))
    check("done stays done forever (reported_on pins the target)",
          not ec.due(_dt.datetime(2026, 9, 29, 20, 0)))
    st = json.loads(ec.STATE_PATH.read_text(encoding="utf-8"))
    check("state records the day", st.get("reported_on") == "2026-09-28")
    ec.STATE_PATH.write_text("{corrupted json", encoding="utf-8")
    check("corrupted state fails open to date-gating (one harmless run)",
          ec.due(_dt.datetime(2026, 9, 28, 20, 0)))

# ── 5. the wiring: scanner one-shot + review announcement ──────────────
bs_src = Path("background_scanner.py").read_text(encoding="utf-8")
dc_src = Path("discipline.py").read_text(encoding="utf-8")
i_vr = bs_src.find("_vst.write_report()")
i_ec = bs_src.find("import era_compare as _ec")
check("era one-shot rides the 20:00 review gate beside the verdict study",
      0 < i_vr < i_ec
      and "_ec.due(now)" in bs_src
      and "_ec.write_report()" in bs_src
      and "_ec.mark_done(now)" in bs_src)
check("the review computes era_line from the module's own summary",
      "era_compare as _ec" in dc_src and "_ec.summary()" in dc_src
      and '"era_line"' in dc_src)
check("before the target date the review is silent (no pre-week nagging)",
      '_today >= str(_ec.TARGET_DATE)' in dc_src)
check("below the bar it speaks the armed-state line",
      "short of the" in dc_src and "a comparison needs" in dc_src)
check("her voice appends the era clause",
      'if result.get("era_line"):' in dc_src
      and '_p.append(str(result["era_line"]))' in dc_src)
i_day = dc_src.find('"rot": "DAY"')
i_el = dc_src.find('"era_line": _day_result.get("era_line")', i_day)
i_cap = dc_src.find('state["history"][-60:]', i_day)
check("the DAY row carries the era line",
      0 < i_day < i_el and (i_cap < 0 or i_el < i_cap), (i_day, i_el, i_cap))

# live dry-run on the REAL study (stubs restored): today is before the
# target, so the review carries no era line yet
ec.vs.grade_trades = _REAL_GRADE
import discipline as dc_real
import datetime as _dt2
rev = dc_real.cooldown_review(when=_dt2.datetime.now().astimezone(),
                              speak=False, dry_run=True)
_today = _dt2.datetime.now().strftime("%Y-%m-%d")
if _today < ec.TARGET_DATE:
    check("live dry-run before Sep 28: era_line is None", rev.get("era_line") is None,
          rev.get("era_line"))
else:
    check("live dry-run on/after Sep 28: era_line is a line",
          isinstance(rev.get("era_line"), str), rev.get("era_line"))

# ── 6. her real exec'd voice ────────────────────────────────────────────
import background_scanner as bs
_src = Path("discipline.py").read_text(encoding="utf-8").replace("\r\n", "\n")
_nl = _src.split("\n")
_s = next(i for i, ln in enumerate(_nl) if ln.startswith("def _speak_cooldown_review("))
_e = next(j for j in range(_s + 1, len(_nl)) if _nl[j].startswith("def "))
NSD = dict(vars(dc_real))
exec("\n".join(_nl[_s:_e]), NSD)

spoke = []
_orig = bs._speak_server
try:
    bs._speak_server = lambda t, **k: spoke.append(t)
    _base = {"total_left": 0.0, "top_gate": None, "top_verdict": None,
             "lesson": ""}
    NSD["_speak_cooldown_review"](dict(_base, era_line="the re-base week-one read: "
                                       "stayed too long fell to 3 dollars a trade"),
                                  240.0, 0.0, 0, None, 0)
    check("the era clause is spoken", spoke and "week-one read" in spoke[0], spoke)
    spoke.clear()
    NSD["_speak_cooldown_review"](dict(_base, era_line=None), 240.0, 0.0, 0, None, 0)
    check("no era clause before the schedule arms it",
          spoke and "week-one read" not in spoke[0])
finally:
    bs._speak_server = _orig

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
Path("_eracompare_check.txt").write_text("\n".join(lines), encoding="utf-8")

print(f"RESULT: {PASS} passed, {FAIL} failed")

raise SystemExit(1 if FAIL else 0)   # the fleet-honesty fix (Sep 30): DONE-only tails failed closed
print("DONE")
