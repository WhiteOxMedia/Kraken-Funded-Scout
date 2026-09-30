"""AppTest smoke: app boots, dopamine plates render.

Sep 10: the bracket's 'No good pick this time' stopped being just
words — _no_pick_advice() grades the funnel's recent rejects
(popup_suppressions.jsonl, last 90 min) so her verdict carries the
closest calls (champions, movers, breakout coils — anything the
scanner buried), the busiest gates, and the tide's price when the
tide is the bouncer. Graded here on a synthetic log: the learned-bar
gap parsing (coil records carry the bar in the detail text), the
90-minute window, the fail-open paths."""
import ast
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

# ── THE PREFS SANDBOX (Sep 12): the console's show-sims and verdict
#    choices now MIRROR into a JSON preference file, so a test's
#    widget interaction must never touch the production file. Set
#    BEFORE any `import app` / AppTest boot: app.py's _prefs_path()
#    reads this env first (the config.py pattern), and every freshly
#    exec'd AppTest script module re-reads it at module level.
import os as _os

def _prefs_env_reset():
    _os.environ.pop("SCOUT_PREFS_FILE", None)

_SCOUT_PREFS_REAL = _os.environ.get("SCOUT_PREFS_FILE")

def _prefs_env_set(p):
    _os.environ["SCOUT_PREFS_FILE"] = str(p)  # leak-audit: ok — restored in the suite's tail finally

_prefs_dir = tempfile.mkdtemp()
_prefs_env_set(Path(_prefs_dir) / "prefs.json")

lines = []
results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))

try:
    import app as app_mod

    fn = getattr(app_mod, "_no_pick_advice", None)
    check("the no-pick diagnostic helper exists", callable(fn))

    # ── synthetic log: champions, movers, breakout coils, an old row ──
    d = tempfile.mkdtemp()
    now = datetime.now().astimezone()
    rows = [
        # a champion near-miss: 2 pts below the floor (closest)
        {"ts": (now - timedelta(minutes=10)).isoformat(), "pair": "PUMP/USD",
         "gate": "brain-floor", "quick": 84.0, "floor": 86.0,
         "detail": "the brain sized it"},
        # a BREAKOUT COIL: the bar lives in the detail text (no floor field)
        {"ts": (now - timedelta(minutes=20)).isoformat(), "pair": "XPL/USD",
         "gate": "tide-out", "quick": 89.5,
         "detail": "breakout coil — the OUT tide's price: score 89.5 vs "
                   "learned bar 94; journal: not champion-grade"},
        # a far-from-bar reject: must NOT appear in the closest list
        {"ts": (now - timedelta(minutes=30)).isoformat(), "pair": "BTC/USD",
         "gate": "tide-out", "quick": 52.0,
         "detail": "breakout coil — the OUT tide's price: score 52.0 vs "
                   "learned bar 94; journal: no"},
        # outside the 90-minute window: must be ignored entirely
        {"ts": (now - timedelta(minutes=120)).isoformat(), "pair": "OLD/USD",
         "gate": "tide-out", "quick": 90.0, "floor": 94.0, "detail": "too old"},
    ]
    f = Path(d) / "popup_suppressions.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    real_Path = app_mod.Path

    class _FakeParent:
        def __truediv__(self, name):
            return f

    class FakePath:
        def __init__(self, p):
            self._p = p
        @property
        def parent(self):
            return _FakeParent()

    app_mod.Path = FakePath
    try:
        advice = fn()
    finally:
        app_mod.Path = real_Path

    check("the closest call names the champion near-miss (2 pts)",
          "PUMP 84% vs 86%" in advice, advice)
    check("the breakout coil's learned-bar gap is parsed from the detail text",
          "XPL 90% vs 94%" in advice, advice)
    check("a far-from-bar reject is not a close call",
          "BTC" not in advice.split("busiest")[0], advice)
    check("rows outside the 90-minute window are ignored",
          "OLD" not in advice, advice)
    check("the busiest gates are counted",
          "busiest gates:" in advice and "tide-out" in advice, advice)
    check("the fail-open path says so instead of crashing",
          isinstance(fn(), str))
finally:
    pass

# ── THE TIDE BAR VERDICT plate (Sep 11): the weekly outcome-vs-quantile
# hindsight surfaced on MASTER — rendered from the JSON sidecar the
# weekly driver writes, never from parsing the prose report.
try:
    _tv = getattr(app_mod, "_tidebar_verdict", None)
    check("the tide bar verdict renderer exists on MASTER", callable(_tv))
    # sandbox the sidecar so the pin never depends on the live pass
    # jf_path injection: the suite renders from a SANDBOX sidecar and
    # never touches the production _tidebarweekly.json
    _sbx_json = BASE / "_sbx_tidebar_verdict.json"
    # the trail too: the diag's sim events now reach the plate's
    # embedded chart — the pins render from a sandboxed events file,
    # never the production trail (the suite's own rule)
    import tidebar_weekly as _tbw_mod
    _real_evs_plate = _tbw_mod.FLIP_EVENTS_FILE
    _ev_sbx_plate = BASE / "_sbx_plate_events.jsonl"
    _ev_sbx_plate.write_text("", encoding="utf-8")
    _tbw_mod.FLIP_EVENTS_FILE = _ev_sbx_plate
    _fake = {
        "generated_ts": "2026-09-11T14:24:00+00:00",
        "flips": {"RISING": {"flipped": True, "since": "2026-09-11"}},
        "trend": {"OUT": [0.5, -0.8, None, -0.91],
                  "FLAT": [1.2, 1.6, 2.5, 3.16],
                  "RISING": []},
        "fills_trend": {"OUT": [None, 12.0, None, -8.0],
                        "FLAT": [0.0, 0.0, 5.5, 5.5],
                        "RISING": []},
        "flip_marks": {"OUT": [None, None, "flip@2026-W36", None],
                       "FLAT": [None, "flip_back@2026-W35", None, None],
                       "RISING": []},
        "states": {
            "OUT": {"n": 347,
                    "in_force": {"bar": 80.0, "fires": 64, "payers": 14,
                                 "rate": 0.219, "usd": 197.8,
                                 "usd_per_fire": 3.09},
                    "other": {"bar": 87.4, "fires": 30, "payers": 7,
                              "rate": 0.233, "usd": 120.0,
                              "usd_per_fire": 4.0},
                    "other_src": "quantile"},
            "FLAT": {"n": 115,
                     "in_force": {"bar": 89.0, "fires": 7, "payers": 4,
                                  "rate": 0.571, "usd": 63.3,
                                  "usd_per_fire": 9.04},
                     "other": {"bar": 80.0, "fires": 14, "payers": 4,
                               "rate": 0.286, "usd": 63.3,
                               "usd_per_fire": 4.52},
                     "other_src": "outcome-fitted"},
            "RISING": None},
        "fills": {"states": {"FLAT": {"fills": 1, "pnl": 5.5, "winners": 1},
                             "RISING": {"fills": 0, "pnl": 0.0, "winners": 0,
                                        "unfilled": 19, "unfilled_payers": 18,
                                        "usd_if_paid": 79.26}},
                  "coverage_from": "2026-09-11T10:30:05+00:00"}}
    try:
        _sbx_json.write_text(json.dumps(_fake), encoding="utf-8")
        html = _tv(jf_path=_sbx_json)
        for probe in ("TIDE BAR VERDICT", "OUT ", "FLAT ", "Δ +", "$+3.09/fire",
                      "outcome-fitted"):
            check(f"the plate renders [{probe}]", probe in html, html[:400])
        # the per-state REAL FILLS block, colored by realized P&L
        for probe in ("REAL FILLS — the fires that actually traded:",
                      "FLAT <span class='dim'>1 filled</span> "
                      "<span style='color:#4be38a;'>realized $+5.50</span>",
                      "realized $+0.00",          # flat P&L renders dim-gray
                      "18 paid on paper",          # the size ladder's gated fires
                      "$79.26 the size ladder gated",
                      "fire-event coverage begins 2026-09-11 10:30",
                      "TOTAL:</span> 1 filled, "
                      "<span style='color:#4be38a;'>realized $+5.50</span>"):
            check(f"the fills block renders [{probe[:42]}]", probe in html,
                  html[html.find("REAL FILLS"):][:500] if "REAL FILLS" in html else html[:400])
        check("an ungraded state is named, not dropped",
              "RISING" in html and "no grade this week" in html, html[:400])
        check("a flipped state wears the BENCHED badge",
              "BENCHED" in html and "flip-back" in html, html[:400])
        # ── THE TREND sparklines (the rolling pooled fit edge, one per
        #    state, per-char sign coloring, shared max-abs scale) ──────
        check("the sparkline chars are sized by the shared max-abs scale",
              "<span style='color:#4be38a;' title='+0.50'>▁</span>" in html
              and "<span style='color:#4be38a;' title='+3.16'>█</span>" in html,
              html[html.find("trend"):][:600] if "trend" in html else html[:400])
        check("each sparkline char carries its own sign color",
              "<span style='color:#ff6b5e;' title='-0.80'>▂</span>" in html
              and "<span style='color:#ff6b5e;' title='-0.91'>▂</span>" in html,
              html[html.find("OUT <span class='dim'>trend"):][:400]
              if "OUT <span class='dim'>trend" in html else html[:400])
        check("a one-sided week is a dim gap, not a zero",
              "<span style='color:#9aa0a6;'>·</span>" in html, html[:400])
        check("the trend line names the latest pooled edge",
              "(pooled edge, latest -0.91)" in html
              and "(pooled edge, latest +3.16)" in html, html[:600])
        check("an empty trend series renders no line",
              html.count("trend</span>") == 2, html.count("trend</span>"))
        # the old-sidecar probe: no flip_marks key -> unmarked lines
        _no_marks = dict(_fake)
        _no_marks.pop("flip_marks", None)
        _sbx_json.write_text(json.dumps(_no_marks), encoding="utf-8")
        try:
            _tv.clear()
        except Exception:
            pass
        _no_marks = _tv(jf_path=_sbx_json)
        # ── THE FILLS SPARK (Sep 11): each trend line's realized
        #    fill P&L from the history records, beneath it ──────────
        check("the fills spark renders under its trend line",
              "&nbsp;&nbsp;<span class='dim'>fills</span>" in html,
              html[html.find("trend"):][:600] if "trend" in html else html[:400])
        check("a flat-zero week is a visible grey baseline bar",
              "<span style='color:#9aa0a6;' title='+0.00'>▁</span>" in html,
              html[html.find("fills"):][:400] if "fills" in html else html[:400])
        check("the fills spark names the latest realized P&L",
              "(realized P&L, latest $+5.50)" in html
              and "(realized P&L, latest $-8.00)" in html, html[:800])
        check("a negative fill week wears the red sign color",
              "<span style='color:#ff6b5e;' title='-8.00'>▅</span>" in html,
              html[html.find("OUT <span class='dim'>trend"):][:500]
              if "OUT <span class='dim'>trend" in html else html[:400])
        # ── THE REGIME GLYPHS (Sep 11): flip / flip-back weeks are
        #    stamped with distinct marks on the trend sparkline ──────
        check("a flip week stamps its glyph in amber (a one-sided week "
              "carries no edge in the hover — honest)",
              "<span style='color:#ffb347;' title='▼ auto-flip "
              "2026-W36 — the quantile ships'>▼</span>" in html,
              html[html.find("OUT <span class='dim'>trend"):][:600]
              if "OUT <span class='dim'>trend" in html else html[:400])
        check("a flip-back week stamps its glyph in green with its "
              "week's edge",
              "<span style='color:#4be38a;' title='▲ flip-back "
              "2026-W35 — the fit returns · edge +1.60'>▲</span>" in html,
              html[html.find("FLAT <span class='dim'>trend"):][:600]
              if "FLAT <span class='dim'>trend" in html else html[:400])
        check("a marked week's glyph replaces the bar (one char per week)",
              html.count("▼</span>") == 1 and html.count("▲</span>") == 1,
              (html.count("▼"), html.count("▲")))
        check("an old sidecar without flip_marks renders unmarked lines",
              "▼" not in _no_marks and "▲" not in _no_marks
              and "trend</span>" in _no_marks, _no_marks[:300])
        # ── THE REGIME WATCH (Sep 11): the slope names the pressure ──
        _rw = getattr(app_mod, "_regime_watch", None)
        check("the regime-watch helper exists", callable(_rw))
        check("a live state's falling edge names the bench slide "
              "with its streak and its gate forecast (streak >= need: "
              "the pool is necessarily negative — the next pass fires)",
              _rw({"OUT": [0.5, -0.4, -0.9, -1.4]}, {})
              == "OUT sliding toward a bench (-1.40, 3 losing weeks, "
                 "gate due now)",
              _rw({"OUT": [0.5, -0.4, -0.9, -1.4]}, {}))
        check("a benched state's rising edge names the flip-back climb "
              "with its forecast",
              _rw({"OUT": [-1.0, 0.2, 0.8, 1.5]}, {"OUT": {"flipped": True}})
              == "OUT climbing toward a flip-back (+1.50, 3 winning "
                 "weeks, gate due now)",
              _rw({"OUT": [-1.0, 0.2, 0.8, 1.5]},
                  {"OUT": {"flipped": True}}))
        check("a single losing week reads singular and forecasts the "
              "REMAINING fresh weeks (need - streak, the streak keeps "
              "extending under the projection)",
              _rw({"OUT": [0.5, -0.9]}, {})
              == "OUT sliding toward a bench (-0.90, 1 losing week, "
                 "~2 wk to gate)",
              _rw({"OUT": [0.5, -0.9]}, {}))
        check("a wrong-side edge forecasts the zero-crossing weeks plus "
              "the fresh gate weeks",
              "~4 wk to gate" in _rw({"OUT": [2.0, 1.5, 1.0, 0.5]}, {}),
              _rw({"OUT": [2.0, 1.5, 1.0, 0.5]}, {}))
        check("a far gate caps honestly at 8+ (no false precision)",
              "8+ wk to gate" in _rw({"OUT": [2.0, 1.75, 1.5, 1.25]}, {}),
              _rw({"OUT": [2.0, 1.75, 1.5, 1.25]}, {}))
        check("the wrong-way slopes name nothing (no machinery action "
              "to warn about)",
              _rw({"OUT": [-1.0, -0.2, 0.5, 1.0]}, {}) == ""
              and _rw({"OUT": [1.5, 0.8, 0.2, -0.5]},
                      {"OUT": {"flipped": True}}) == "",
              (_rw({"OUT": [-1.0, -0.2, 0.5, 1.0]}, {}),
               _rw({"OUT": [1.5, 0.8, 0.2, -0.5]},
                   {"OUT": {"flipped": True}})))
        check("steady slopes and thin series stay silent",
              _rw({"OUT": [1.0, 1.01, 0.99]}, {}) == ""
              and _rw({"OUT": [-1.0]}, {}) == ""
              and _rw(None, {}) == "", "silence")
        check("mixed pressures join with the separator",
              _rw({"OUT": [0.5, -0.4, -0.9, -1.4],
                   "RISING": [-1.0, 0.2, 0.8, 1.5]},
                  {"RISING": {"flipped": True}})
              == "OUT sliding toward a bench (-1.40, 3 losing weeks, "
                 "gate due now)"
                 " · RISING climbing toward a flip-back "
                 "(+1.50, 3 winning weeks, gate due now)", "joined")
        check("the plate carries the REGIME WATCH line",
              "REGIME WATCH:</span>" in html
              and "OUT sliding toward a bench (-0.91, 2 losing weeks, "
                  "~1 wk to gate)"
              in html, html[html.find("REGIME WATCH"):][:300]
              if "REGIME WATCH" in html else html[:400])
        # ── THE VERDICT CENSUS CHIP (Sep 12): the machinery's record
        #    beside the regime watch — the trail census's twin ───────
        check("an empty flip ledger renders the honest census shape "
              "(no fake 0 · 0 · 0)",
              "VERDICTS:</span> <span style='color:#9aa0a6;'>the flip "
              "ledger is empty — no decisions yet</span>" in html,
              html[html.find("VERDICTS"):][:300]
              if "VERDICTS" in html else html[:400])
        check("the census chip sits beside the regime watch",
              0 < html.find("VERDICTS:")
              and html.find("REGIME WATCH:") < html.find("VERDICTS:")
              and html.find("VERDICTS:") - html.find("REGIME WATCH:")
              < 400, (html.find("REGIME WATCH:"),
                      html.find("VERDICTS:")))
        # the populated chip: the same flip pair the trail's census
        # pins count (1 PAYING · 1 COSTING), against its own sandbox
        # history — the chip classifies real records, never air
        _h_sbx_chip = BASE / "_sbx_plate_history.jsonl"
        _h_sbx_chip.write_text("\n".join([
            json.dumps({"week": "2026-W37", "generated_ts": "t",
                        "states": {"OUT": {
                            "in_force": {"fires": 10, "payers": 3,
                                         "rate": 0.3, "usd": 10.0,
                                         "usd_per_fire": 1.0},
                            "other": {"fires": 10, "payers": 3,
                                      "rate": 0.3, "usd": 20.0,
                                      "usd_per_fire": 2.0},
                            "other_src": "quantile"}}}),
            json.dumps({"week": "2026-W40", "generated_ts": "t",
                        "states": {"OUT": {
                            "in_force": {"fires": 10, "payers": 3,
                                         "rate": 0.3, "usd": 30.0,
                                         "usd_per_fire": 3.0},
                            "other": {"fires": 10, "payers": 3,
                                      "rate": 0.3, "usd": 10.0,
                                      "usd_per_fire": 1.0},
                            "other_src": "outcome-fitted"}}}),
            json.dumps({"week": "2026-W41", "generated_ts": "t",
                        "states": {"OUT": {
                            "in_force": {"fires": 10, "payers": 3,
                                         "rate": 0.3, "usd": 10.0,
                                         "usd_per_fire": 1.0},
                            "other": {"fires": 10, "payers": 3,
                                      "rate": 0.3, "usd": 30.0,
                                      "usd_per_fire": 3.0},
                            "other_src": "quantile"}}}),
        ]) + "\n", encoding="utf-8")
        _ev_sbx_plate.write_text("\n".join([
            json.dumps({"action": "flip", "state": "OUT",
                        "week": "2026-W39", "ts": "t",
                        "losing_weeks": 3, "roll_delta": -1.28,
                        "evidence": [
                            {"week": "2026-W37", "fit_edge": -0.82},
                            {"week": "2026-W38", "fit_edge": -1.52},
                            {"week": "2026-W39", "fit_edge": -1.52}]}),
            json.dumps({"action": "flip_back", "state": "OUT",
                        "week": "2026-W40", "ts": "t",
                        "winning_weeks": 2, "roll_delta": 0.21,
                        "evidence": [
                            {"week": "2026-W39", "fit_edge": 0.5},
                            {"week": "2026-W40", "fit_edge": 0.6}]})])
            + "\n", encoding="utf-8")
        _real_hist_chip = _tbw_mod.HISTORY_FILE
        _tbw_mod.HISTORY_FILE = _h_sbx_chip
        try:
            _tv.clear()
            _chip_html = _tv(jf_path=_sbx_json)
            check("the populated trail's chip counts the graded pair "
                  "(1 PAYING · 1 COSTING · 0 pending), red on COSTING",
                  "VERDICTS:</span> <span style='color:#ff6b5e;'>"
                  "1 PAYING · 1 COSTING · 0 pending</span>" in _chip_html,
                  _chip_html[_chip_html.find("VERDICTS"):][:300]
                  if "VERDICTS" in _chip_html else _chip_html[:400])
            # THE RECKONING TALLY (Sep 12): once real flip-backs carry
            # their borrow_reckoning stamps, the chip appends how the
            # borrow's calls graded — same line, same stream. (The pin
            # above is also the no-tally proof: these two unstamped
            # flip-backs leave the census alone.)
            _ev_sbx_plate.write_text(
                _ev_sbx_plate.read_text(encoding="utf-8").rstrip("\n")
                + "\n" + "\n".join([
                    json.dumps({"action": "flip_back", "state": "RISING",
                                "week": "2026-W41", "ts": "t",
                                "winning_weeks": 2, "roll_delta": 0.3,
                                "borrow_reckoning": {
                                    "status": "agree",
                                    "borrowed": "COSTING"},
                                "evidence": []}),
                    json.dumps({"action": "flip_back", "state": "FLAT",
                                "week": "2026-W42", "ts": "t",
                                "winning_weeks": 2, "roll_delta": 0.3,
                                "borrow_reckoning": {
                                    "status": "agree",
                                    "borrowed": "COSTING"},
                                "evidence": []})]) + "\n",
                encoding="utf-8")
            _tv.clear()
            _chip_html2 = _tv(jf_path=_sbx_json)
            # the appended flip_backs join the classification too (FLAT
            # un-benched -> COSTING; the empty-evidence twin pends) —
            # the pin asserts the machine's true rendered line
            check("the chip's census carries the reckoning tally once "
                  "stamped flip-backs exist (…· 2 agreed · 0 disagreed)",
                  "1 PAYING · 2 COSTING · 1 pending · 2 agreed · "
                  "0 disagreed" in _chip_html2,
                  _chip_html2[_chip_html2.find("VERDICTS"):][:300]
                  if "VERDICTS" in _chip_html2 else _chip_html2[:400])
        finally:
            _tbw_mod.HISTORY_FILE = _real_hist_chip
        _h_sbx_chip.unlink(missing_ok=True)
        _ev_sbx_plate.write_text("", encoding="utf-8")
        _tv.clear()
        # ── THE PLATE'S WARN STRIP (Sep 12): the pass's own warning ──
        check("no bench_warn in the sidecar -> no strip (no fake urgency)",
              "⚠️ BENCH PRESSURE" not in html)
        _wb = dict(_fake, bench_warn={
            "state": "OUT",
            "phrase": "OUT sliding toward a bench (-0.91, 2 losing "
                      "weeks, ~1 wk to gate)"})
        _sbx_json.write_text(json.dumps(_wb), encoding="utf-8")
        try:
            _tv.clear()
        except Exception:
            pass
        _wb_html = _tv(jf_path=_sbx_json)
        check("the plate shows the pass's own warning, phrase verbatim",
              "⚠️ BENCH PRESSURE: OUT sliding toward a bench (-0.91, "
              "2 losing weeks, ~1 wk to gate)" in _wb_html
              and "#ffb347" in _wb_html, _wb_html[-500:])
        # the old-sidecar fallback: no trend key -> no sparklines, no raise
        _no_trend = dict(_fake)
        _no_trend.pop("trend", None)
        _sbx_json.write_text(json.dumps(_no_trend), encoding="utf-8")
        try:
            _tv.clear()
        except Exception:
            pass
        html2 = _tv(jf_path=_sbx_json)
        check("a trend-less (old) sidecar renders the plate without sparklines",
              "TIDE BAR VERDICT" in html2 and "trend</span>" not in html2,
              html2[:300])
        check("a trend-less sidecar shows the no-pressure fallback",
              "no state under bench / flip-back pressure" in html2,
              html2[html2.find("REGIME WATCH"):][:300]
              if "REGIME WATCH" in html2 else html2[:400])
        _spk = getattr(app_mod, "_spark_html", None)
        check("the sparkline helper exists and degrades on empty input",
              callable(_spk) and _spk(None) == ("", 1.0)
              and _spk([None, None]) == ("", 1.0), _spk)
        # a string-shaped fills record (the not-graded path) renders its why
        _fake["fills"] = "no fired-popup records"
        _sbx_json.write_text(json.dumps(_fake), encoding="utf-8")
        try:
            _tv.clear()
        except Exception:
            pass
        html_str = _tv(jf_path=_sbx_json)
        check("a not-graded fills record is named on the plate",
              "REAL FILLS: not graded" in html_str
              and "no fired-popup records" in html_str, html_str[-400:])
        _sbx_json.unlink(missing_ok=True)
        try:
            _tv.clear()   # the ttl cache would serve the previous render
        except Exception:
            pass
        html_missing = _tv(jf_path=_sbx_json)
        check("a missing sidecar degrades to the quiet fallback",
              "has not run yet" in html_missing, html_missing[:200])
    finally:
        _sbx_json.unlink(missing_ok=True)

    # ── the click-through: the full history expander ────────────────
    _rh = getattr(app_mod, "_render_tidebar_history", None)
    check("the history click-through exists on MASTER", callable(_rh))
    try:
        # the SANDBOX report: rf_path keeps the suite off the production
        # file entirely (a kill mid-test once left fixture text behind)
        _sbx_txt = BASE / "_sbx_tidebar_history.txt"
        _sbx_txt.write_text(
            "=== THE TIDE BAR WEEKLY — outcome bar vs the quantile ===\n"
            "graded at: 2026-09-11 (newest first)\n…older sections…\n",
            encoding="utf-8")
        from streamlit.testing.v1 import AppTest as _AT
        _at2 = _AT.from_string("""
import sys
sys.path.insert(0, r"%s")
import app
app._render_tidebar_history(max_bytes=100000, rf_path=r"%s")
""" % (str(BASE), str(_sbx_txt)))
        _at2.run()
        _exp = [e for e in _at2.expander
                if "FULL TIDE BAR HISTORY" in (e.label or "")]
        check("the expander is on the page (newest-first label)",
              bool(_exp), [e.label for e in _at2.expander])
        if _exp:
            _body = " ".join((c.value or "") for c in _exp[0].code)
            check("the expander carries the report text",
                  "outcome bar vs the quantile" in _body, _body[:200])
        # the page-stall cap: a huge file truncates with the honest marker
        _sbx_txt.write_text("X" * 150_000, encoding="utf-8")
        _at3 = _AT.from_string("""
import sys
sys.path.insert(0, r"%s")
import app
app._render_tidebar_history(max_bytes=1000, rf_path=r"%s")
""" % (str(BASE), str(_sbx_txt)))
        _at3.run()
        _exp3 = [e for e in _at3.expander
                 if "FULL TIDE BAR HISTORY" in (e.label or "")]
        _body3 = " ".join((c.value or "") for c in _exp3[0].code) \
            if _exp3 else ""
        check("a huge history is capped with the honest marker",
              "history capped for the page" in _body3, _body3[:200])
    finally:
        _sbx_txt.unlink(missing_ok=True)

    # ── the ISO-week picker ─────────────────────────────────────────
    _sp = getattr(app_mod, "_tidebar_week_sections", None)
    check("the week-section splitter exists", callable(_sp))
    _txt2 = ("=== THE TIDE BAR WEEKLY — outcome bar vs the quantile ===\n"
             "graded at: 2026-09-18 (2026-W38, trailing 7 days…)\n"
             "OUT  (10 candidates this week; in force: quantile bar 86)\n"
             "  outcome-fitted bar 82: 10 fires, 3 payers (30.0%), $47.47 "
             "($+4.75/fire)   [in-force Δ$/fire +3.16]\n"
             "FLAT  (97 candidates this week; in force: outcomes bar 80)\n"
             "  quantile  bar 86: 46 fires, 14 payers (30.4%), $221.55 "
             "($+4.82/fire)   [in-force Δ$/fire -0.82]\n"
             "mid\n"
             "=== THE TIDE BAR WEEKLY — outcome bar vs the quantile ===\n"
             "graded at: 2026-09-11 (2026-W37, trailing 7 days…)\n"
             "RISING  (90 candidates this week; in force: outcomes bar 87)\n"
             "  quantile  bar 86: 7 fires, 4 payers (57.1%), $63.30 "
             "($+9.04/fire)   [in-force Δ$/fire +1.51]\n"
             "old\n"
             "=== THE TIDE BAR WEEKLY — outcome bar vs the quantile ===\n"
             "untagged trailing text")
    _secs = _sp(_txt2)
    check("sections split on the report header with their ISO week",
          [lbl for lbl, _ in _secs] == ["2026-W38", "2026-W37",
                                        "unmarked (early sections)"],
          [lbl for lbl, _ in _secs])
    check("each section keeps its own body",
          "mid" in _secs[0][1] and "old" in _secs[1][1]
          and "untagged trailing text" in _secs[2][1], _secs)
    # the self-heal: an untagged section carrying its own timestamp
    # labels by the DERIVED ISO week (the backfill contract)
    _txt3 = ("=== THE TIDE BAR WEEKLY — outcome bar vs the quantile ===\n"
             "week ending: 2026-09-11 13:00 UTC  (trailing 7 days)\nbody\n")
    check("a dated-untagged section labels by its derived ISO week",
          [lbl for lbl, _ in _sp(_txt3)] == ["2026-W37"],
          [lbl for lbl, _ in _sp(_txt3)])
    _txt4 = ("=== THE TIDE BAR WEEKLY — outcome bar vs the quantile ===\n"
             "week ending: 1999-12-31 23:59 UTC\nbody\n")
    check("a pre-2000 date echo stays unmarked (not a real report)",
          [lbl for lbl, _ in _sp(_txt4)] == ["unmarked (early sections)"],
          [lbl for lbl, _ in _sp(_txt4)])
    try:
        # the sandbox report again (rf_path): never the production file
        _sbx_txt2 = BASE / "_sbx_tidebar_picker.txt"
        _sbx_txt2.write_text(_txt2, encoding="utf-8")
        from streamlit.testing.v1 import AppTest as _AT
        _at4 = _AT.from_string("""
import sys
sys.path.insert(0, r"%s")
import streamlit as st
import app
with st.expander("x"):
    pass
app._render_tidebar_history(max_bytes=100000, rf_path=r"%s")
""" % (str(BASE), str(_sbx_txt2)))
        _at4.run()
        _sel = [s for s in _at4.selectbox if s.label == "ISO week"]
        check("the ISO-week picker is on the page",
              bool(_sel), [s.label for s in _at4.selectbox])
        if _sel:
            # AppTest's .options surfaces the FORMATTED labels (what she
            # sees — the verdict deltas still render); the widget's
            # stored value is the raw semantic tag (set_value proves it)
            check("the options are newest-first with the All-weeks "
                  "default and the deltas RENDER (stored values stay "
                  "semantic tags — nothing counted is stored)",
                  _sel[0].options == ["All weeks (newest first)",
                                      "2026-W38  ·  OUT -3.16  FLAT -0.82",
                                      "2026-W37  ·  RISING +1.51",
                                      "unmarked (early sections)"]
                  and _sel[0].index == 0, _sel[0].options)
            # isolate a week: set the SEMANTIC tag and re-run
            _sel[0].set_value("2026-W37").run()
            _expw = [e for e in _at4.expander
                     if "FULL TIDE BAR HISTORY" in (e.label or "")]
            _bodyw = " ".join((c.value or "") for c in _expw[0].code) \
                if _expw else ""
            check("a picked week renders ONLY that week's sections",
                  "old" in _bodyw and "mid" not in _bodyw, _bodyw[:300])
        # ── the picker's verdict deltas: _week_pooled_edges ─────────────
        _wpe = getattr(app_mod, "_week_pooled_edges", None)
        check("the week-edge parser exists", callable(_wpe))
        _txt5 = (
            "=== THE TIDE BAR WEEKLY — outcome bar vs the quantile ===\n"
            "graded at: 2026-09-18 (2026-W38, trailing 7 days…)\n"
            "OUT  (10 candidates this week; in force: quantile bar 86)\n"
            "  IN FORCE  bar 86: 6 fires, 3 payers (50.0%), $47.47 ($+7.91/fire)\n"
            "  outcome-fitted bar 82: 10 fires, 3 payers (30.0%), $47.47 "
            "($+4.75/fire)   [in-force Δ$/fire +3.16]\n"
            "FLAT  (97 candidates this week; in force: outcomes bar 80)\n"
            "  IN FORCE  bar 80: 82 fires, 23 payers (28.0%), $328.37 "
            "($+4.00/fire)\n"
            "  quantile  bar 86: 46 fires, 14 payers (30.4%), $221.55 "
            "($+4.82/fire)   [in-force Δ$/fire -0.82]\n"
            "=== THE TIDE BAR WEEKLY — outcome bar vs the quantile ===\n"
            "graded at: 2026-09-11 (2026-W37, trailing 7 days…)\n"
            "RISING  (90 candidates this week; in force: outcomes bar 87)\n"
            "  IN FORCE  bar 87: 6 fires, 4 payers (66.7%), $63.30 "
            "($+10.55/fire)\n"
            "  quantile  bar 86: 7 fires, 4 payers (57.1%), $63.30 "
            "($+9.04/fire)   [in-force Δ$/fire +1.51]\n"
            "=== THE TIDE BAR WEEKLY — outcome bar vs the quantile ===\n"
            "graded at: 2026-09-11 12:00 (2026-W37, trailing 7 days)\n"
            "OUT  (5 candidates this week; in force: quantile bar 86)\n"
            "  IN FORCE  bar 86: 4 fires, 2 payers (50.0%), $30.00 ($+7.50/fire)\n"
            "  outcome-fitted bar 82: 8 fires, 2 payers (25.0%), $30.00 "
            "($+3.75/fire)   [in-force Δ$/fire +3.75]\n")
        _edges = _wpe(_sp(_txt5))
        check("a flipped-regime section's delta normalizes to the fit edge "
              "(sign flipped)",
              _edges["2026-W38"].get("OUT") == -3.16, _edges)
        check("a fit-live section's delta passes through unchanged",
              _edges["2026-W38"].get("FLAT") == -0.82, _edges)
        check("a week's edge comes from its NEWEST section (the file is "
              "newest-first)",
              set(_edges["2026-W37"]) == {"RISING"}
              and _edges["2026-W37"]["RISING"] == 1.51, _edges)
        check("one-sided states contribute nothing (no zero fakes)",
              "FLAT" not in _edges["2026-W37"], _edges)
        # ── the sides parser: fires + payer rate for BOTH bars ─────
        _wss = getattr(app_mod, "_week_side_stats", None)
        check("the week-sides parser exists", callable(_wss))
        _sides = _wss(_sp(_txt5))
        check("a flipped-regime week maps each LINE to its bar (the "
              "label, not the seat)",
              [k for k in _sides["2026-W38"]["OUT"]] == ["quantile", "fit"]
              and _sides["2026-W38"]["OUT"]["fit"][0] == 10
              and round(_sides["2026-W38"]["OUT"]["fit"][1] * 100, 1) == 30.0
              and _sides["2026-W38"]["OUT"]["quantile"][0] == 6
              and round(_sides["2026-W38"]["OUT"]["quantile"][1] * 100,
                        1) == 50.0, _sides["2026-W38"].get("OUT"))
        check("a fit-live week maps the IN FORCE line to the fit side",
              _sides["2026-W38"]["FLAT"]["fit"][0] == 82
              and round(_sides["2026-W38"]["FLAT"]["fit"][1] * 100, 1) == 28.0
              and _sides["2026-W38"]["FLAT"]["quantile"][0] == 46
              and round(_sides["2026-W38"]["FLAT"]["quantile"][1] * 100,
                        1) == 30.4, _sides["2026-W38"].get("FLAT"))
        check("a week's sides come from its NEWEST section (same policy "
              "as the edge parser)",
              set(_sides["2026-W37"]) == {"RISING"}, _sides["2026-W37"])
        _txt6 = (
            "=== THE TIDE BAR WEEKLY — outcome bar vs the quantile ===\n"
            "graded at: 2026-09-04 (2026-W34, trailing 7 days…)\n"
            "OUT  (10 candidates this week; in force: quantile bar 86)\n"
            "  IN FORCE  bar 86: 6 fires, 3 payers, $47.47 ($+7.91/fire)\n"
            "  outcome-fitted bar 82: 10 fires, 3 payers (30.0%), $47.47 "
            "($+4.75/fire)   [in-force Δ$/fire +1.50]\n")
        check("a week with an unparseable rate on one side yields nothing "
              "(the chart degrades to an edge-only hover)",
              "2026-W34" not in _wss(_sp(_txt6)), _wss(_sp(_txt6)))
    finally:
        _sbx_txt2.unlink(missing_ok=True)
    # INVARIANTS (Sep 11): the history pins once overwrote the REAL
    # _tidebarweekly.txt with fixture text — a battery killed between
    # write and restore left the fixture behind and production history
    # was lost. The renderers now take injected paths; these pins keep
    # the suite honest about it.
    check("no production data file was left behind by the pins",
          not (BASE / "_sbx_tidebar_history.txt").exists()
          and not (BASE / "_sbx_tidebar_picker.txt").exists())
    check("the production report file carries no fixture text",
          # clock-audit: ok — LIVE-PROBE (negative cleanliness: the file
          # must carry NO fixture text; the read is the assertion, the
          # injected-path pins above own the sandbox) (hermeticity sweep)
          "untagged trailing text" not in (BASE / "_tidebarweekly.txt")
          .read_text(encoding="utf-8"))
    check("the history renderer accepts an injected sandbox path",
          "rf_path" in app_mod._render_tidebar_history.__doc__)
    check("the verdict plate accepts an injected sidecar path",
          "jf_path" in app_mod._tidebar_verdict.__doc__)
    _tbw_mod.FLIP_EVENTS_FILE = _real_evs_plate
    _ev_sbx_plate.unlink(missing_ok=True)
except Exception as exc:
    _tbw_mod.FLIP_EVENTS_FILE = _real_evs_plate
    _ev_sbx_plate.unlink(missing_ok=True)
    check("the tide bar verdict plate pins ran", False, exc)

# ── the MASTER ledger chart: weekly fit edges over time ────────────
try:
    import tidebar_weekly as _tbw_mod
    _real_hist = _tbw_mod.HISTORY_FILE
    _hb = _real_hist.read_bytes() if _real_hist.exists() else None
    _sandbox = BASE / "_sbx_tidebar_history.jsonl"
    # the prose backfill reads the PRODUCTION weekly txt when no
    # txt_path is given — a production txt drift (new prose-only weeks)
    # would silently change the pinned point/hover counts. Every bare
    # call gets a never-created txt: the ledger is the only input, so
    # these pins are hermetic by construction.
    _nb_txt = BASE / "_sbx_chart_no_backfill.txt"
    # the events trail is sandboxed for the whole chart block: the
    # diag's sim events now reach the chart (the hollow dialect), and
    # pins must never read the production trail
    _real_evs_chart = _tbw_mod.FLIP_EVENTS_FILE
    _ev_sbx_chart = BASE / "_sbx_chart_block_events.jsonl"
    _ev_sbx_chart.write_text("", encoding="utf-8")
    _tbw_mod.FLIP_EVENTS_FILE = _ev_sbx_chart
    def _hline(week, di, do, src="quantile"):
        return json.dumps({"week": week, "generated_ts": week,
            "states": {"OUT": {
                "in_force": {"fires": 10, "payers": 3, "rate": 0.3,
                             "usd": 10 * di, "usd_per_fire": di},
                "other": {"fires": 10, "payers": 3, "rate": 0.3,
                          "usd": 10 * do, "usd_per_fire": do},
                "other_src": src}}})
    # W36: fit live (+1.0 edge) · W37: FLIPPED regime, raw Δ +1.0 but the
    # fit actually LOST (canonical -2.0) · W38: fit live, losing (-0.5)
    # W39: the fit back (+2.0) · W40: the quantile ships again (-0.5)
    # W41: still losing (-0.5) — the re-flip's decision week
    _sandbox.write_text(
        "\n".join([_hline("2026-W36", 2.0, 1.0),
                   _hline("2026-W37", 3.0, 1.0, src="outcome-fitted"),
                   _hline("2026-W38", 1.0, 1.5),
                   _hline("2026-W39", 3.0, 1.0, src="outcome-fitted"),
                   _hline("2026-W40", 1.0, 1.5),
                   _hline("2026-W41", 1.0, 1.5)]) + "\n",
        encoding="utf-8")
    try:
        _tbw_mod.HISTORY_FILE = _sandbox
        _chart = app_mod._tidebar_ledger_chart(_nb_txt)
        check("the ledger chart draws a path per graded state",
              _chart.count("<path ") == 1 * 3 // 3
              and "#4be38a" in _chart, len(_chart))
        check("a flipped-regime week plots its CANONICAL fit edge "
              "(sign normalized through the real helper)",
              "2026-W37 OUT fit edge -2.00" in _chart, _chart[:600])
        check("every point carries a hover title with its week AND both "
              "sides' fires and payer rates",
              "2026-W36 OUT fit edge +1.00 — fit 10 fires · 30% pay | "
              "quantile 10 fires · 30% pay</title>" in _chart
              and "2026-W38 OUT fit edge -0.50 — fit 10 fires · 30% pay | "
                  "quantile 10 fires · 30% pay</title>" in _chart,
              _chart[:900])
        check("an ungraded state draws no line (honest gap)",
              "#4da3ff" not in _chart and "#ffb347" not in _chart
              and "— FLAT" not in _chart, _chart[:600])
        check("the zero line and the legend render",
              "stroke-dasharray='4 4'" in _chart
              and "fit−quantile edge by week" in _chart
              and "— OUT</span>" in _chart, _chart[:600])
        _tbw_mod.HISTORY_FILE = BASE / "_sbx_missing_ledger.jsonl"
        check("an empty/missing ledger renders nothing (fail-open)",
              app_mod._tidebar_ledger_chart(_nb_txt) == "")
        # ── THE BENCHED SPANS (Sep 11): shaded regime history ──────
        _tbw_mod.HISTORY_FILE = _sandbox   # back from the missing-ledger probe
        _real_evs_c = _tbw_mod.FLIP_EVENTS_FILE
        _ev_sbx_c = BASE / "_sbx_chart_events.jsonl"
        _ev_sbx_c.write_text(
            json.dumps({"state": "OUT", "action": "flip",
                        "week": "2026-W37", "roll_delta": -1.5,
                        "evidence": [
                            {"week": "2026-W36", "fit_edge": -0.8},
                            {"week": "2026-W37", "fit_edge": -2.0}]}) + "\n"
            + json.dumps({"state": "OUT", "action": "flip_back",
                          "week": "2026-W39", "roll_delta": 0.5,
                          "evidence": [
                              {"week": "2026-W38", "fit_edge": 1.0},
                              {"week": "2026-W50", "fit_edge": 1.0}]}) + "\n"
            # the RE-FLIP (regime churn): the fit re-benched at W41 —
            # a state's second flip wears ▼▼ and the 'benched again' story
            + json.dumps({"state": "OUT", "action": "flip",
                          "week": "2026-W41", "roll_delta": -0.9,
                          "evidence": [
                              {"week": "2026-W41", "fit_edge": -0.9}]}) + "\n",
            encoding="utf-8")
        _tbw_mod.FLIP_EVENTS_FILE = _ev_sbx_c
        try:
            _sh = app_mod._tidebar_ledger_chart(_nb_txt)
            check("a benched span shades its weeks under the line",
                  _sh.count("<rect ") == 2
                  and "fill-opacity='0.08'" in _sh
                  and "OUT benched 2026-W37..2026-W38 — the quantile "
                      "shipped" in _sh
                  and "OUT benched 2026-W41..2026-W41 — the quantile "
                      "shipped" in _sh, _sh.count("<rect"))
            check("a shaded chart carries the legend note",
                  "shaded = benched" in _sh, _sh[-300:])
            # ── THE FLIP-EVENT MARKERS: evidence weeks get glyphs ───
            # the ledger window is W36..W38: the flip's evidence lands
            # 2 ▼ (W36, W37); the flip_back's W38 ▲ lands, its W50
            # evidence is out of window and skipped
            check("a flip's evidence weeks each carry a ▼ — floating "
                  "marker AND on-point stamp both name the week",
                  _sh.count(">▼<") == 4
                  and _sh.count("▼ auto-flip 2026-W36") == 2
                  and _sh.count("▼ auto-flip 2026-W37") == 2, _sh[:1800])
            # ── THE RE-FLIP GLYPH (Sep 12): the second flip wears ▼▼ ──
            check("a RE-FLIP's evidence carries the doubled ▼▼ with the "
                  "'benched again' story (marker + on-point stamp)",
                  _sh.count(">▼▼<") == 2
                  and _sh.count("▼▼ re-flip 2026-W41 — OUT benched again "
                                "on pooled edge -0.90") == 2, _sh[:2200])
            check("a flip-back's in-window evidence carries a ▲ (its "
                  "out-of-window week is skipped) — marker + stamp",
                  _sh.count(">▲<") == 2
                  and "▲ flip-back 2026-W38" in _sh
                  and "2026-W50" not in _sh, _sh[:1800])
            # ── THE REGIME STAMPS (Sep 12): the chart wears the
            #    sparkline's own glyphs, ON the point ───────────────
            check("a flip week's point wears the sparkline's ▼ (decision, "
                  "the week's own data, and the click hint in one hover)",
                  "▼ auto-flip 2026-W37 — OUT benched on pooled edge "
                  "-1.50 · edge -2.00 — fit 10 fires · 30% pay | "
                  "quantile 10 fires · 30% pay — click for the "
                  "evidence</title>" in _sh, _sh[:2200])
            check("a flip-back week's point wears the ▲ at its own edge",
                  "▲ flip-back 2026-W38 — the fit returned (pooled edge "
                  "+0.50) · edge -0.50 — fit 10 fires · 30% pay | "
                  "quantile 10 fires · 30% pay — click for the "
                  "evidence</title>" in _sh, _sh[:2200])
            check("the glyph replaces the plain circle on its week — "
                  "unmarked W39/W40 keep circles, marked weeks none",
                  _sh.count("<circle") == 2 and _sh.count(">▼<") == 4
                  and _sh.count(">▲<") == 2, _sh.count("<circle"))
            check("the stamp rides the ACTION colors (amber ▼ for the "
                  "fit's bench), not the state's line color",
                  "fill='#ffb347'" in _sh, _sh.count("#ffb347"))
            check("the markers carry the decision story in their hover",
                  "OUT benched on pooled edge -1.50" in _sh
                  and "the fit returned (pooled edge +0.50)" in _sh,
                  _sh[:1800])
            check("a marked chart names the glyphs in its caption",
                  "▼/▼▼/▲ = the weeks that decided a bench (▼▼ = "
                  "re-flip)" in _sh
                  and "on-line ▼/▲ mirror the sparkline glyphs" in _sh,
                  _sh[-400:])
            # ── THE CLICK-THROUGH (Sep 12): every regime glyph hrefs its
            #    decision's audit-trail row (the trail's dedupe key as
            #    the anchor id) ──────────────────────────────────────
            import re as _re
            _hrefs = _re.findall(r"href='(#[^']+)'", _sh)
            _trail = app_mod._render_flip_audit()
            _ids = set(_re.findall(r"<a id='([^']+)'></a>", _trail))
            check("every chart glyph hrefs an anchor that exists on the "
                  "audit trail (floats AND on-point stamps)",
                  len(_hrefs) == 8 and len(set(_hrefs)) == 3
                  and all(h[1:] in _ids for h in _hrefs)
                  and _ids == {"flip-OUT-flip-2026-W37",
                               "flip-OUT-flip_back-2026-W39",
                               "flip-OUT-flip-2026-W41"},
                  (len(_hrefs), sorted(_ids), sorted(set(_hrefs)),
                   all(h[1:] in _ids for h in _hrefs)))
            check("the trail's verb names the regime churn: the second "
                  "flip reads RE-FLIPPED, the first stays AUTO-FLIPPED",
                  "OUT RE-FLIPPED to the quantile (regime churn)" in _trail
                  and "OUT AUTO-FLIPPED to the quantile" in _trail,
                  [l for l in _trail.split("<br>") if "to the quantile" in l][:2])
            check("glyph hovers carry the click hint and the caption "
                  "names the click-through",
                  _sh.count("— click for the evidence") == 8
                  and "every glyph clicks through to its decision's "
                      "evidence" in _sh,
                  _sh.count("click for the evidence"))
            # ── THE GAP-WEEK STAMP: a flip whose evidence lands on a
            #    week the state never graded stamps the ZERO line ────
            _ev_gap = BASE / "_sbx_chart_events_gap.jsonl"
            _ev_gap.write_text(
                json.dumps({"state": "OUT", "action": "flip",
                            "week": "2026-W37", "roll_delta": -1.5,
                            "evidence": [
                                {"week": "2026-W36", "fit_edge": -0.8},
                                {"week": "2026-W37", "fit_edge": -2.0}]}) + "\n"
                + json.dumps({"state": "FLAT", "action": "flip",
                              "week": "2026-W37", "roll_delta": -0.9,
                              "evidence": [
                                  {"week": "2026-W37", "fit_edge": -1.1}]})
                + "\n", encoding="utf-8")
            _tbw_mod.FLIP_EVENTS_FILE = _ev_gap
            try:
                _sg = app_mod._tidebar_ledger_chart(_nb_txt)
                check("a flip on a never-graded week stamps the zero line "
                      "(marker hover + point stamp, honestly edgeless)",
                      _sg.count("▼ auto-flip 2026-W37 — FLAT benched on "
                                "pooled edge -0.90 — click for the "
                                "evidence</title>") == 2
                      and _sg.count(">▼<") == 6 and _sg.count(">▲<") == 0
                      and _sg.count("<path ") == 1,
                      (_sg.count(">▼<"), _sg.count(">▲<"),
                       _sg.count("<path ")))
                check("an all-gap state with no events stays invisible",
                      "RISING" not in _sg, _sg.count("RISING"))
            finally:
                _tbw_mod.FLIP_EVENTS_FILE = _real_evs_c
                _ev_gap.unlink(missing_ok=True)
            _tbw_mod.FLIP_EVENTS_FILE = _ev_sbx_c
        finally:
            _tbw_mod.FLIP_EVENTS_FILE = _real_evs_c
            _ev_sbx_c.unlink(missing_ok=True)
        _plain = app_mod._tidebar_ledger_chart(_nb_txt)
        check("no flip events -> an unshaded, unmarked chart (and no "
              "legend notes)",
              "<rect " not in _plain and "shaded = benched" not in _plain
              and "decided a bench" not in _plain and ">▼<" not in _plain,
              _plain.count("<rect"))
        # ── THE HOLLOW DIALECT (Sep 12): the diag's simulated worked
        #    examples DO mark the chart — as OUTLINES with a simulated
        #    hover tag — so they teach without reading as lived ────
        _ev_sim = BASE / "_sbx_chart_events_sim.jsonl"
        _ev_sim.write_text(
            json.dumps({"state": "OUT", "action": "flip",
                        "week": "2026-W37", "roll_delta": -1.5,
                        "evidence": [
                            {"week": "2026-W36", "fit_edge": -0.8},
                            {"week": "2026-W37", "fit_edge": -2.0}]}) + "\n"
            + json.dumps({"state": "RISING", "action": "flip",
                          "week": "2026-W37", "roll_delta": -0.7,
                          "sim": True,
                          "evidence": [
                              {"week": "2026-W36", "fit_edge": -1.1},
                              {"week": "2026-W37", "fit_edge": -1.2}]}) + "\n"
            + json.dumps({"state": "FLAT", "action": "flip_back",
                          "week": "2026-W38", "roll_delta": 0.4,
                          "sim": True,
                          "evidence": [
                              {"week": "2026-W38", "fit_edge": 0.9}]}) + "\n"
            # the RE-ARM (sim): FLAT's second flip-back — ▲▲, 'returned
            # again'; a sim flip-back never counts the real stream's
            # ordinals (per-stream counters). Distinct decision week:
            # the (state, action, week) dedupe would swallow a twin.
            + json.dumps({"state": "FLAT", "action": "flip_back",
                          "week": "2026-W39", "roll_delta": 0.4,
                          "sim": True,
                          "evidence": [
                              {"week": "2026-W39", "fit_edge": 0.9}]}) + "\n",
            encoding="utf-8")
        _tbw_mod.FLIP_EVENTS_FILE = _ev_sim
        try:
            _shs = app_mod._tidebar_ledger_chart(_nb_txt)
            check("sim floats render HOLLOW (fill none, state-colored "
                  "stroke) while the real flip floats stay solid",
                  _shs.count("fill='none' stroke='#ffb347'") == 4
                  and _shs.count("fill='#4be38a' font-size='11'") == 2,
                  (_shs.count("fill='none' stroke='#ffb347'"),
                   _shs.count("fill='#4be38a' font-size='11'")))
            check("sim point stamps are hollow outlines too (the sparkline "
                  "glyph shape, outline-painted, in the state's color)",
                  _shs.count("fill='none' stroke='#ffb347' "
                             "stroke-width='1.5' font-size='9'") == 2
                  and _shs.count("fill='none' stroke='#4da3ff' "
                                 "stroke-width='1.5' font-size='9'") == 2,
                  (_shs.count("stroke-width='1.5' font-size='9'"),))
            check("the real flip's stamps stay SOLID action-amber beside "
                  "the hollow sims on the same weeks",
                  _shs.count("fill='#ffb347' font-size='9'") == 2
                  and _shs.count("fill='none' stroke='#4be38a' "
                                 "stroke-width='1.5' font-size='9'") == 0,
                  _shs.count("font-size='9'"))
            check("every sim marker's hover carries the simulated tag",
                  "— RISING benched on pooled edge -0.70 · simulated "
                  "worked example" in _shs
                  and _shs.count("· simulated worked example") == 8,
                  _shs.count("simulated worked example"))
            # ── THE RE-ARM GLYPH (Sep 12): a state's SECOND flip-back
            #    wears ▲▲ ('the fit returned again'), per stream — the
            #    sim's re-arm never touches the real stream's ordinals
            check("a RE-ARM's evidence carries the doubled ▲▲ with the "
                  "'returned again' story, hollow (sim) per the dialect",
                  _shs.count(">▲▲<") == 2
                  and _shs.count("▲▲ re-arm 2026-W39 — the fit returned "
                                 "again (pooled edge +0.40) · simulated "
                                 "worked example") == 2, _shs[:2200])
            # ── THE MIRRORED HIDE (Sep 12): the chart obeys the trail's
            #    toggle — hollow sim glyphs drop (floating markers AND
            #    on-point stamps), lived glyphs stay, the caption notes
            #    the hidden sims instead of pretending they never were ──
            _shv = app_mod._tidebar_ledger_chart(_nb_txt, hide_sims=True)
            check("the chart mirrors the hide toggle: hollow sim glyphs "
                  "drop (markers + stamps), lived glyphs stay (the real "
                  "OUT flip keeps 2 floats + 2 stamps = 4 ▼), the "
                  "caption carries the mirror note",
                  _shv.count(">▼<") == 4
                  and _shv.count(">▲<") == 0
                  and _shv.count(">▲▲<") == 0
                  and _shv.count("· simulated worked example") == 0
                  and "hollow ▼/▲ = simulated worked examples" not in _shv
                  and "simulated glyphs hidden by the trail's toggle" in _shv,
                  (_shv.count(">▼<"), _shv.count(">▲<"),
                   _shv.count(">▲▲<"),
                   _shv.count("· simulated worked example")))
            check("the sim-bearing caption names the hollow dialect",
                  "hollow ▼/▲ = simulated worked examples" in _shs,
                  _shs[-400:])
            check("a sim glyph's href lands on the SIMULATION row (the "
                  "-sim anchor suffix), a real glyph's on the lived row",
                  "href='#flip-RISING-flip-2026-W37-sim'" in _shs
                  and "href='#flip-OUT-flip-2026-W37'" in _shs
                  and "flip-RISING-flip-2026-W37-sim" in
                  " ".join(_re.findall(r"<a id='([^']+)'></a>",
                                      app_mod._render_flip_audit())),
                  [h for h in _re.findall(r"href='(#[^']+)'", _shs)])
            check("a real event wins its slot over a colliding sim "
                  "(the lived decision is never overdrawn)",
                  not [m for m in app_mod._flip_markers(
                      ["2026-W36", "2026-W37", "2026-W38"])
                      if m["state"] == "OUT" and m["week"] == "2026-W36"
                      and m.get("sim")],
                  [m for m in app_mod._flip_markers(
                      ["2026-W36", "2026-W37", "2026-W38"])])
        finally:
            _tbw_mod.FLIP_EVENTS_FILE = _real_evs_c
            _ev_sim.unlink(missing_ok=True)
        # ── THE PROSE BACKFILL (Sep 11): the picker's parser extends
        #    the chart before the ledger's first record ─────────────
        _sbx_txt3 = BASE / "_sbx_chart_prose.txt"
        # a week the ledger doesn't know: W35, QUANTILE live, Δ +1.50
        # (in-force − other) -> the canonical fit edge is −1.50 — the
        # parser's regime flip, exercised end to end
        _sbx_txt3.write_text(
            "=== THE TIDE BAR WEEKLY — outcome bar vs the quantile ===\n"
            "graded at: 2026-09-25 (2026-W35, trailing 7 days…)\n"
            "OUT  (10 candidates this week; in force: quantile bar 86)\n"
            "  IN FORCE  bar 86: 10 fires, 3 payers (30.0%), $100.00 "
            "($+10.00/fire)\n"
            "  quantile  bar 86: 10 fires, 3 payers (30.0%), $85.00 "
            "($+8.50/fire)   [in-force Δ$/fire +1.50]\n"
            + _txt2,   # the picker fixture: W38, W37 (with bodies), untagged
            encoding="utf-8")
        _bf = app_mod._tidebar_ledger_chart(txt_path=_sbx_txt3)
        check("a prose-only week extends the chart with its edge and its "
              "sides (sign-normalized through the regime flip)",
              "2026-W35 OUT fit edge -1.50 — fit 10 fires · 30% pay | "
              "quantile 10 fires · 30% pay (from the prose report)" in _bf,
              _bf[:900])
        check("the backfilled week plots BEFORE the ledger's first week",
              _bf.find("2026-W35") < _bf.find("2026-W36"), _bf[:800])
        check("ledger weeks keep their provenance-free hovers",
              "2026-W36 OUT fit edge +1.00 — fit 10 fires · 30% pay | "
              "quantile 10 fires · 30% pay</title>" in _bf
              and "2026-W36 OUT fit edge +1.00 (from" not in _bf, _bf[:600])
        check("the caption names the backfill",
              "1 earlier week backfilled from the prose report" in _bf,
              _bf[-400:])
        # a week in BOTH sources: the ledger stays authoritative
        _sbx_txt4 = BASE / "_sbx_chart_prose_dup.txt"
        _sbx_txt4.write_text(
            "=== THE TIDE BAR WEEKLY — outcome bar vs the quantile ===\n"
            "graded at: 2026-09-25 (2026-W36, trailing 7 days…)\n"
            "OUT  (10 candidates this week; in force: quantile bar 86)\n"
            "  IN FORCE  bar 86: 10 fires, 3 payers (30.0%), $100.00 "
            "($+10.00/fire)\n"
            "  quantile  bar 86: 10 fires, 3 payers (30.0%), $85.00 "
            "($+8.50/fire)   [in-force Δ$/fire +1.50]\n"
            + _txt2, encoding="utf-8")
        _dup = app_mod._tidebar_ledger_chart(txt_path=_sbx_txt4)
        check("a week in both sources keeps the LEDGER's edge (and no "
              "prose tag)",
              "2026-W36 OUT fit edge +1.00 — fit 10 fires · 30% pay | "
              "quantile 10 fires · 30% pay</title>" in _dup
              and "(from the prose report)" not in _dup
              and "backfilled" not in _dup, _dup[:600])
        _sbx_txt3.unlink(missing_ok=True)
        _sbx_txt4.unlink(missing_ok=True)
        _nofile = app_mod._tidebar_ledger_chart(
            txt_path=BASE / "_sbx_missing_prose.txt")
        check("a missing prose file changes nothing",
              "backfilled" not in _nofile
              and _nofile == _plain, len(_nofile))
    finally:
        _tbw_mod.HISTORY_FILE = _real_hist
        _tbw_mod.FLIP_EVENTS_FILE = _real_evs_chart
        _ev_sbx_chart.unlink(missing_ok=True)
        if _hb is not None:
            _real_hist.write_bytes(_hb)
        elif _real_hist.exists():
            _real_hist.unlink(missing_ok=True)
        _sandbox.unlink(missing_ok=True)
        (BASE / "_sbx_missing_ledger.jsonl").unlink(missing_ok=True)
except Exception as exc:
    check("the ledger chart pins ran", False, exc)

# ── the flip audit trail view ──────────────────────────────────────
try:
    import tidebar_weekly as _tbw_mod
    _real_evs = _tbw_mod.FLIP_EVENTS_FILE
    _real_fl = _tbw_mod.FLIP_FILE
    _ev_sbx = BASE / "_sbx_flip_events.jsonl"
    _fl_sbx = BASE / "_sbx_flip_file.json"
    _ev_sbx.write_text(
        "\n".join([
            json.dumps({"action": "flip", "state": "OUT",
                        "week": "2026-W39", "ts": "2026-09-25 09:00 UTC",
                        "losing_weeks": 3, "roll_delta": -1.28,
                        "evidence": [
                            {"week": "2026-W37", "fit_edge": -0.82},
                            {"week": "2026-W38", "fit_edge": -1.52},
                            {"week": "2026-W39", "fit_edge": -1.52}]}),
            json.dumps({"action": "flip_back", "state": "OUT",
                        "week": "2026-W45", "ts": "2026-11-06 09:00 UTC",
                        "winning_weeks": 2, "roll_delta": 0.21,
                        "bench_final": {
                            "weeks": 5, "to_date": -45.5,
                            "verdict": "COSTING",
                            "verdict_col": "#ff6b5e",
                            "text": "over the 5 weeks of the bench, the "
                                    "quantile realized $+2.00/fire (50 "
                                    "fires) vs the fit's $+2.91/fire over "
                                    "the same weeks — to date the bench is "
                                    "down $45.50 cumulative"},
                        "evidence": [
                            {"week": "2026-W44", "fit_edge": 0.78},
                            {"week": "2026-W45", "fit_edge": 0.98}]})])
        + "\n", encoding="utf-8")
    _fl_sbx.write_text(json.dumps({"OUT": {"flipped": True}}),
                       encoding="utf-8")
    # the aftermath reads the HISTORY ledger — sandboxed so the pins
    # never touch production records (and the degrade is testable)
    _h_sbx = BASE / "_sbx_audit_history.jsonl"
    _real_hist_aud = _tbw_mod.HISTORY_FILE
    _h_sbx.write_text("\n".join([
        # W37: the decision week (flipped regime — the quantile lived)
        json.dumps({"week": "2026-W37", "generated_ts": "t", "states": {
            "OUT": {"in_force": {"fires": 10, "payers": 3, "rate": 0.3,
                                 "usd": 20.0, "usd_per_fire": 2.0},
                    "other": {"fires": 10, "payers": 3, "rate": 0.3,
                              "usd": 10.0, "usd_per_fire": 1.0},
                    "other_src": "outcome-fitted"}}}),
        # W40: still INSIDE the bench (the flip-back only lands at W45),
        # so the quantile remains in force — regime-consistent with the
        # flip's governed window reaching it
        json.dumps({"week": "2026-W40", "generated_ts": "t", "states": {
            "OUT": {"in_force": {"fires": 10, "payers": 3, "rate": 0.3,
                                 "usd": 20.0, "usd_per_fire": 2.0},
                    "other": {"fires": 10, "payers": 3, "rate": 0.3,
                              "usd": 10.0, "usd_per_fire": 1.0},
                    "other_src": "outcome-fitted"}}})]) + "\n",
        encoding="utf-8")
    _ev_sbx_sim = BASE / "_sbx_flip_events_sim.jsonl"
    _ev_only_sims = BASE / "_sbx_flip_events_onlysims.jsonl"
    try:
        _tbw_mod.FLIP_EVENTS_FILE = _ev_sbx
        _tbw_mod.FLIP_FILE = _fl_sbx
        _tbw_mod.HISTORY_FILE = _h_sbx
        _aud = app_mod._render_flip_audit()
        check("the audit view renders the flip event red with its verb",
              "OUT AUTO-FLIPPED to the quantile" in _aud
              and "#ff6b5e" in _aud
              and "2026-09-25 09:00 UTC" in _aud
              and "3 weeks, pooled edge -1.28" in _aud, _aud[:500])
        check("the evidence weeks render beside the event",
              "evidence: 26-W37 -0.82 26-W38 -1.52 26-W39 -1.52" in _aud,
              _aud[_aud.find("evidence:"):][:200])
        check("the flip-back renders green with its counterfactual evidence",
              "FLIP-BACK to live duty" in _aud and "#4be38a" in _aud
              and "26-W44 +0.78 26-W45 +0.98" in _aud, _aud[:600])
        check("a state sitting benched right now is visible",
              "CURRENTLY BENCHED: OUT" in _aud, _aud[:600])
        # ── THE LIVE AFTERMATH (Sep 12): the open bench graded in
        #    dollars BEFORE its flip-back closes it ─────────────────
        check("the benched strip grades the OPEN bench in dollars (the "
              "shared math: realized quantile vs fit counterfactual)",
              "live aftermath: over the 1 weeks since, the quantile "
              "realized $+2.00/fire (10 fires) vs the fit's $+1.00/fire"
              in _aud and "the bench is PAYING" in _aud, _aud[-700:])
        check("the live verdict renders in the verdict colors",
              "#4be38a" in _aud.split("CURRENTLY BENCHED")[1], _aud[-300:])
        check("the strip carries the bench's CUMULATIVE cost-to-date "
              "(realized $20 - counterfactual $10 = $10 up in the "
              "shared entry text)",
              "to date the bench is " in _aud.split("CURRENTLY BENCHED")[1]
              and ("up $10.00 cumulative" in _aud
                   or "down $10.00 cumulative" in _aud),
              _aud[_aud.find("CURRENTLY BENCHED"):][:600])
        # newest first: the flip-back must appear before the flip
        check("events render newest first",
              _aud.find("FLIP-BACK") < _aud.find("AUTO-FLIPPED"), _aud[:600])
        # ── THE AFTERMATH on the trail: realized vs counterfactual ──
        check("the flip's aftermath grades the realized quantile vs the "
              "fit's counterfactual (the decision's own bar, post-decision "
              "weeks only)",
              "aftermath: in the 1 weeks after, the quantile realized "
              "$+2.00/fire (10 fires) vs the fit's $+1.00/fire" in _aud
              and "the decision PAID" in _aud, _aud[:1200])
        check("the flip-back's aftermath is honestly pending (its evidence "
              "weeks are the newest history — nothing graded after)",
              "aftermath: pending — no post-decision weeks graded yet"
              in _aud, _aud[_aud.find("FLIP-BACK"):][:600])
        # ── THE BENCH'S FINAL VERDICT (Sep 12): the flip-back's record
        #    preserves the bench's OPEN live verdict at the moment it
        #    ended — cumulative dollars, rendered even though the state
        #    is un-benched now ────────────────────────────────────────
        check("the flip-back row preserves the bench's FINAL verdict "
              "(the to-date dollars, frozen at the moment it ended)",
              "the bench's final live verdict" in _aud
              and "the bench was COSTING when it ended" in _aud
              and "down $45.50 cumulative" in _aud, _aud[-900:])
        _tbw_mod.HISTORY_FILE = BASE / "_sbx_audit_missing.jsonl"
        _aud_m = app_mod._render_flip_audit()
        check("a missing history ledger degrades the aftermath to pending "
              "(the event rows still render)",
              "aftermath: pending" in _aud_m
              and "AUTO-FLIPPED" in _aud_m, _aud_m[:600])
        # ── THE VERDICT FILTER (Sep 12): the trail browsable by its own
        #    outcome — graded aftermaths classify the row; a pending
        #    decision borrows its state's LIVE bench verdict (a flip
        #    whose bench is paying is a PAYING call). The dropdown's
        #    hide side is driven through explicit kwargs (the probe
        #    context has no widget), the same discipline as hide_sims.
        _tbw_mod.HISTORY_FILE = _h_sbx   # back to the graded ledger
        check("every row wears its verdict chip and the census names "
              "the machinery's record — classification matches the "
              "aftermath's own dollars (the graded PAID flip is PAYING; "
              "its pending twin stays pending, never borrowed while "
              "un-benched)",
              "verdicts: 1 PAYING · 0 COSTING · 1 pending" in _aud
              and "OUT AUTO-FLIPPED to the quantile</b> "
                  "<span style='color:#4be38a;font-size:12px;'>PAYING</span>"
              in _aud
              and "FLIP-BACK to live duty</b> "
                  "<span style='color:#9aa0a6;font-size:12px;'>pending</span>"
              in _aud, _aud[:900])
        _aud_pay = app_mod._render_flip_audit(verdict="PAYING")
        check("the PAYING filter shows the graded flip and hides the "
              "pending flip-back (anchor stub keeps its chart glyph "
              "resolvable, note names the hidden count)",
              "OUT AUTO-FLIPPED to the quantile" in _aud_pay
              and "FLIP-BACK to live duty" not in _aud_pay
              and "the bench's final live verdict" not in _aud_pay
              and "<a id='flip-OUT-flip_back-2026-W45'></a>" in _aud_pay
              and "1 decision hidden by the verdict filter" in _aud_pay,
              _aud_pay[:900])
        _aud_pend = app_mod._render_flip_audit(verdict="pending")
        check("the pending filter isolates the flip-back with its "
              "preserved bench_final intact",
              "FLIP-BACK to live duty" in _aud_pend
              and "the bench's final live verdict" in _aud_pend
              and "OUT AUTO-FLIPPED" not in _aud_pend
              and "<a id='flip-OUT-flip-2026-W39'></a>" in _aud_pend,
              _aud_pend[:900])
        _aud_cost = app_mod._render_flip_audit(verdict="COSTING")
        check("an empty verdict view says so honestly (no rows, all "
              "anchors stubbed, the census still readable)",
              "no decisions classified COSTING yet" in _aud_cost
              and "AUTO-FLIPPED" not in _aud_cost
              and "FLIP-BACK" not in _aud_cost
              and _aud_cost.count("<a id='flip-") == 2
              and "verdicts: 1 PAYING" in _aud_cost, _aud_cost[:900])
        check("every verdict mode keeps the benched strip and the "
              "live aftermath (state meta is never filtered)",
              all("CURRENTLY BENCHED: OUT" in v and "the bench is PAYING" in v
                  for v in (_aud_pay, _aud_pend, _aud_cost)),
              [_aud_pay.count("CURRENTLY BENCHED"),
               _aud_pend.count("CURRENTLY BENCHED"),
               _aud_cost.count("CURRENTLY BENCHED")])
        check("the live aftermath pends honestly on a missing ledger too",
              "live aftermath: pending — no post-decision weeks graded yet"
              in _aud_m and "CURRENTLY BENCHED: OUT" in _aud_m, _aud_m[-400:])
        _tbw_mod.HISTORY_FILE = _h_sbx
        # ── THE FBW=3 CLAUSE (Sep 12): a real flip-back whose aftermath
        #    grades COSTING earns its own question on the row — would
        #    ONE more re-arm week (FBW=3) have saved it? One fixture
        #    exercises the REAL helper end to end (a never-arms deny,
        #    arc 3's shape); the other statuses' row wording is pinned
        #    with the helper stubbed (its math lives in the weekly
        #    suite). Flip rows, PAYING flip-backs and sims never get a
        #    clause. ─────────────────────────────────────────────────
        _ev_sbx_fb = BASE / "_sbx_flip_events_fb3.jsonl"
        _h_sbx_fb = BASE / "_sbx_audit_history_fb3.jsonl"
        _ev_sbx_fb.write_text(
            "\n".join([
                json.dumps({"action": "flip", "state": "OUT",
                            "week": "2026-W39", "ts": "t",
                            "losing_weeks": 3, "roll_delta": -1.28,
                            "evidence": [
                                {"week": "2026-W37", "fit_edge": -0.82},
                                {"week": "2026-W38", "fit_edge": -1.52},
                                {"week": "2026-W39", "fit_edge": -1.52}]}),
                json.dumps({"action": "flip_back", "state": "OUT",
                            "week": "2026-W40", "ts": "t",
                            "winning_weeks": 2, "roll_delta": 0.21,
                            "evidence": [
                                {"week": "2026-W39", "fit_edge": 0.5},
                                {"week": "2026-W40", "fit_edge": 0.6}]})])
            + "\n", encoding="utf-8")
        # W37/W40 the flipped regime (the quantile in force), W41 the
        # re-armed fit bleeding $1.00 vs the quantile's $3.00 — the
        # re-arm COSTS and its edge never wins again: never-arms
        _h_sbx_fb.write_text("\n".join([
            json.dumps({"week": "2026-W37", "generated_ts": "t",
                        "states": {"OUT": {
                            "in_force": {"fires": 10, "payers": 3,
                                         "rate": 0.3, "usd": 20.0,
                                         "usd_per_fire": 2.0},
                            "other": {"fires": 10, "payers": 3,
                                      "rate": 0.3, "usd": 10.0,
                                      "usd_per_fire": 1.0},
                            "other_src": "outcome-fitted"}}}),
            json.dumps({"week": "2026-W40", "generated_ts": "t",
                        "states": {"OUT": {
                            "in_force": {"fires": 10, "payers": 3,
                                         "rate": 0.3, "usd": 30.0,
                                         "usd_per_fire": 3.0},
                            "other": {"fires": 10, "payers": 3,
                                      "rate": 0.3, "usd": 10.0,
                                      "usd_per_fire": 1.0},
                            "other_src": "outcome-fitted"}}}),
            json.dumps({"week": "2026-W41", "generated_ts": "t",
                        "states": {"OUT": {
                            "in_force": {"fires": 10, "payers": 3,
                                         "rate": 0.3, "usd": 10.0,
                                         "usd_per_fire": 1.0},
                            "other": {"fires": 10, "payers": 3,
                                      "rate": 0.3, "usd": 30.0,
                                      "usd_per_fire": 3.0},
                            "other_src": "quantile"}}}),
            json.dumps({"week": "2026-W42", "generated_ts": "t",
                        "states": {"OUT": {
                            "in_force": {"fires": 10, "payers": 3,
                                         "rate": 0.3, "usd": 10.0,
                                         "usd_per_fire": 1.0},
                            "other": {"fires": 10, "payers": 3,
                                      "rate": 0.3, "usd": 30.0,
                                      "usd_per_fire": 3.0},
                            "other_src": "quantile"}}}),
        ]) + "\n", encoding="utf-8")
        _real_evs_fb, _real_hist_fb = (_tbw_mod.FLIP_EVENTS_FILE,
                                       _tbw_mod.HISTORY_FILE)
        _tbw_mod.FLIP_EVENTS_FILE = _ev_sbx_fb
        _tbw_mod.HISTORY_FILE = _h_sbx_fb
        try:
            _aud_fb = app_mod._render_flip_audit()
            check("a COSTING flip-back grades the aftermath and names it "
                  "on the row (the re-arm's own dollars)",
                  "aftermath: in the 2 weeks after, the fit realized "
                  "$+1.00/fire (20 fires) vs the quantile's $+3.00/fire"
                  in _aud_fb and "the decision COST" in _aud_fb,
                  _aud_fb[:900])
            check("the fbw3 clause renders the REAL never-arms deny on "
                  "the costing row (no third winning week, the decision's "
                  "own avoided cost: $2.00/fire over 20 fires = $40.00)",
                  "the extra re-arm week (FBW=3) would have denied this "
                  "re-arm entirely (no third winning week) — saved "
                  "$+40.00 ($+2.00/fire over the 2 weeks after)"
                  in _aud_fb,
                  _aud_fb[_aud_fb.find("FLIP-BACK"):][:1200])
            check("the clause sits between the aftermath and the bench's "
                  "final verdict on the row",
                  _aud_fb.find("aftermath: in the 2 weeks after")
                  < _aud_fb.find("the extra re-arm week (FBW=3)")
                  < _aud_fb.find("the bench's final live verdict")
                  if "the bench's final live verdict" in _aud_fb else True,
                  _aud_fb[:1400])
            check("the flip row (PAYING) never earns a clause",
                  _aud_fb.count("the extra re-arm week (FBW=3)") == 1
                  and _aud_fb.find("the extra re-arm week (FBW=3)")
                  > _aud_fb.find("FLIP-BACK to live duty"),
                  _aud_fb.count("the extra re-arm week (FBW=3)"))
            check("the census counts the graded pair (1 PAYING · 1 "
                  "COSTING)",
                  "verdicts: 1 PAYING · 1 COSTING · 0 pending"
                  in _aud_fb,
                  _aud_fb[-400:])
            # THE BORROW'S RECKONING (Sep 12): the real flip-back's
            # verdict was borrowed while pending (at closure the state
            # is un-benched -> COSTING) — the row states the grader's
            # agreement once the aftermath lands.
            check("the borrow's reckoning renders AGREES on the graded "
                  "costing flip-back",
                  "the borrow's reckoning: the verdict was borrowed while "
                  "pending and the realized aftermath AGREES — the borrow "
                  "told the truth (COSTING)" in _aud_fb,
                  _aud_fb[_aud_fb.find("FLIP-BACK"):][:1800])
            check("a flip (PAYING) row never earns a reckoning (only the "
                  "closure is graded)",
                  _aud_fb.count("the borrow's reckoning") == 1,
                  _aud_fb.count("the borrow's reckoning"))
            # THE RECKONING TALLY (Sep 12): the trail's census line
            # tallies the PERSISTED stamps — this stamp is exactly what
            # the pass hook writes once the aftermath grades (agree:
            # borrowed COSTING, graded COSTING). The row's own reckoning
            # renders fresh per pass, so the stamp changes only the tally.
            _fb_ev_path = _tbw_mod.FLIP_EVENTS_FILE
            _fb_ev_path.write_text(
                _fb_ev_path.read_text(encoding="utf-8").rstrip("\n")
                + "\n" + json.dumps({
                    "action": "flip_back", "state": "FLAT",
                    "week": "2026-W41", "ts": "t",
                    "winning_weeks": 2, "roll_delta": 0.3,
                    "borrow_reckoning": {"status": "agree",
                                         "borrowed": "COSTING"},
                    "evidence": []}) + "\n", encoding="utf-8")
            _aud_fb2 = app_mod._render_flip_audit()
            check("the trail's census line tallies the persisted stamp "
                  "beside the counts (the FLAT closure borrows COSTING: "
                  "…2 COSTING · 0 pending · 1 agreed · 0 disagreed)",
                  "verdicts: 1 PAYING · 2 COSTING · 0 pending · "
                  "1 agreed · 0 disagreed" in _aud_fb2,
                  _aud_fb2[-500:])
            # the other statuses' ROW WORDING, helper stubbed (the real
            # math is pinned in the weekly suite): they ride THIS trail —
            # the COSTING flip-back is what earns a clause at all. (The
            # W45 fixture's flip-back is honestly pending, so a stubbed
            # helper there renders nothing — the earlier red run's
            # lesson.)
            _real_fbw = _tbw_mod.fbw3_counterfactual
            _real_rk = _tbw_mod.borrow_reckoning
            try:
                _tbw_mod.fbw3_counterfactual = (
                    lambda *a, **k: {
                        "status": "denied", "arm_week": "2026-W45",
                        "delayed_fires": 20, "delayed_per_fire": 3.50,
                        "saved_per_fire": 1.00, "saved_total": 20.0})
                _aud_d = app_mod._render_flip_audit()
                check("a positive deny names the later arm week and its "
                      "save in dollars",
                      "the extra re-arm week (FBW=3) would have held the "
                      "bench one more week, re-arming at 2026-W45 — would "
                      "have saved $+20.00 ($+1.00/fire over the delayed "
                      "arm's 20 fires)" in _aud_d, _aud_d[:1400])
                _tbw_mod.fbw3_counterfactual = (
                    lambda *a, **k: {
                        "status": "denied", "arm_week": "2026-W45",
                        "delayed_fires": 20, "delayed_per_fire": 0.50,
                        "saved_per_fire": -2.00, "saved_total": -40.0})
                _aud_dn = app_mod._render_flip_audit()
                check("a negative deny says the extra week would have "
                      "cost — no fake savings",
                      "the extra re-arm week (FBW=3) would have re-armed "
                      "later at 2026-W45 and paid $40.00 LESS — the extra "
                      "week would have cost here" in _aud_dn,
                      _aud_dn[:1400])
                _tbw_mod.fbw3_counterfactual = (
                    lambda *a, **k: {"status": "same-week"})
                _aud_sw = app_mod._render_flip_audit()
                check("a same-week replay credits the pool guard, not the "
                      "week count",
                      "the extra re-arm week (FBW=3) re-arms on the same "
                      "week — the pool guard, not the week count, held "
                      "this arm; nothing to save" in _aud_sw,
                      _aud_sw[:1400])
                _tbw_mod.fbw3_counterfactual = (
                    lambda *a, **k: {"status": "delayed",
                                     "arm_week": "2026-W44"})
                _aud_dl = app_mod._render_flip_audit()
                check("an unpriced delayed arm stays honest",
                      "the extra re-arm week (FBW=3) delays the re-arm to "
                      "2026-W44 — too few weeks have traded since to "
                      "price it" in _aud_dl, _aud_dl[:1400])
                # THE RECKONING'S DISAGREEMENT (Sep 12): format-only pin
                # via the stubbed grader — a wrong borrow reads LOUD, in
                # amber, with the dollars clause verbatim.
                _tbw_mod.borrow_reckoning = (
                    lambda *a, **k: {
                        "status": "disagree", "borrowed": "COSTING",
                        "graded": "PAYING", "gap_per_fire": 2.0,
                        "text": "stub says the dollars went the other way"})
                _aud_dis = app_mod._render_flip_audit()
                check("a wrong borrow renders DISAGREES loudly with the "
                      "clause and the amber color",
                      "the borrow's reckoning: DISAGREES — stub says the "
                      "dollars went the other way" in _aud_dis
                      and "color:#ffb347" in _aud_dis, _aud_dis[:1800])
            finally:
                _tbw_mod.fbw3_counterfactual = _real_fbw
                _tbw_mod.borrow_reckoning = _real_rk
        finally:
            _tbw_mod.FLIP_EVENTS_FILE = _real_evs_fb
            _tbw_mod.HISTORY_FILE = _real_hist_fb
        _ev_sbx_fb.unlink(missing_ok=True)
        _h_sbx_fb.unlink(missing_ok=True)
        # ── the SIM worked example on the trail (Sep 11): the diag's
        #    replay decisions render under a SIMULATION badge with their
        #    embedded aftermath; the real-ledger math never touches them
        # ── the sim event fixtures (the badge/arc/toggle pins below) ─
        _ev_sbx_sim.write_text(
            _ev_sbx.read_text(encoding="utf-8")
            + json.dumps({"sim": True, "state": "OUT", "action": "flip",
                          "week": "2026-W39",
                          "ts": "simulated replay (2026-W39)",
                          "sim_source": "diag_flipgrade.py seeded replay "
                                        "(the bench PAYS)",
                          "losing_weeks": 3, "roll_delta": -0.99,
                          "evidence": [{"week": "2026-W37",
                                        "fit_edge": -0.52}],
                          "sim_aftermath": {
                              "status": "graded", "weeks": 6,
                              "realized": {"bar": "quantile", "fires": 10,
                                           "usd_per_fire": 4.8},
                              "counterfactual": {"bar": "fit", "fires": 10,
                                                 "usd_per_fire": 3.6},
                              "verdict": "the decision PAID"}}) + "\n"
            + json.dumps({"sim": True, "state": "OUT",
                          "action": "flip_back", "week": "2026-W45",
                          "ts": "simulated replay (2026-W45)"}) + "\n",
            encoding="utf-8")
        _tbw_mod.FLIP_EVENTS_FILE = _ev_sbx_sim
        _aud_s = app_mod._render_flip_audit()
        check("a sim event renders under the SIMULATION badge",
              _aud_s.count("[SIMULATION") == 2, _aud_s[:900])
        check("a sim's badge carries its ARC NAME from sim_source's "
              "parenthesized tail",
              "[SIMULATION (the bench PAYS) — the diag's worked example"
              in _aud_s, _aud_s[:1200])
        check("a sim WITHOUT a sim_source keeps the bare badge (no "
              "fabricated arc)",
              _aud_s.count("[SIMULATION — the diag's worked example") == 1,
              _aud_s.count("[SIMULATION"))
        check("a sim's embedded aftermath renders with the simulated "
              "label (never the real-ledger math)",
              "aftermath (simulated): in the 6 weeks after, the quantile "
              "realized $+4.80/fire (10 fires) vs the fit's $+3.60/fire"
              in _aud_s
              and "the decision PAID" in _aud_s, _aud_s[:1400])
        check("a sim without an embedded aftermath stays honestly pending "
              "(the replay's weeks never lived in the ledger)",
              "aftermath (simulated): pending — the replay's weeks never "
              "lived in the ledger" in _aud_s, _aud_s[_aud_s.rfind(
                  "FLIP-BACK"):][:600])
        # THE RECKONING TALLY (Sep 12): a stamped SIM never counts —
        # the tally reads lived decisions only, so the census shows alone
        _ev_sbx_sim.write_text(
            _ev_sbx_sim.read_text(encoding="utf-8").rstrip("\n")
            + "\n" + json.dumps({
                "sim": True, "action": "flip_back", "state": "FLAT",
                "week": "2026-W44", "ts": "t",
                "borrow_reckoning": {"status": "agree",
                                     "borrowed": "COSTING"}}) + "\n",
            encoding="utf-8")
        _aud_s2 = app_mod._render_flip_audit()
        check("the tally never counts sims — a stamped sim renders and "
              "still leaves the census alone",
              _aud_s2.count("[SIMULATION") == 3
              and "· 1 agreed" not in _aud_s2,
              _aud_s2[_aud_s2.find("verdicts:"):][:200]
              if "verdicts:" in _aud_s2 else _aud_s2[:300])
        check("the real events' aftermath is untouched by the sims",
              "aftermath: in the 1 weeks after, the quantile realized "
              "$+2.00/fire" in _aud_s, _aud_s[:1600])
        # ── THE SHOW/HIDE TOGGLE (Sep 12): the hide side renders the
        #    LIVED history in isolation — sims drop, an honest hidden-
        #    count note stays, real rows keep their evidence ────────
        _aud_h = app_mod._render_flip_audit(hide_sims=True)
        check("hiding sims drops every sim row (badge, arc, aftermath)",
              "[SIMULATION" not in _aud_h
              and "aftermath (simulated)" not in _aud_h,
              _aud_h.count("SIMULATION"))
        check("hiding sims keeps the real rows' evidence intact",
              "AUTO-FLIPPED" in _aud_h and "FLIP-BACK" in _aud_h
              and "evidence: " in _aud_h
              and "aftermath: in the 1 weeks after" in _aud_h,
              _aud_h[:800])
        check("the hide side carries the honest hidden-count note",
              "3 simulated worked examples hidden — "
              "'show worked examples' beside this restores them" in _aud_h,
              _aud_h[-400:])
        # a trail of ONLY sims hides to the honest one-liner, not to ""
        _ev_only_sims.write_text(
            json.dumps({"sim": True, "state": "OUT", "action": "flip",
                        "week": "2026-W39", "ts": "t",
                        "sim_source": "diag (the bench PAYS)"}) + "\n",
            encoding="utf-8")
        _tbw_mod.FLIP_EVENTS_FILE = _ev_only_sims
        _aud_o = app_mod._render_flip_audit(hide_sims=True)
        check("a sims-only trail hides to the honest one-liner (never "
              "empty, never an error)",
              "1 simulated worked example hidden — no lived bench events "
              "yet; show sims to read the worked examples" in _aud_o
              and "[SIMULATION" not in _aud_o, _aud_o[:400])
        _ev_only_sims.unlink(missing_ok=True)
        _tbw_mod.FLIP_EVENTS_FILE = _ev_sbx
        _ev_sbx_sim.unlink(missing_ok=True)
        _tbw_mod.FLIP_EVENTS_FILE = BASE / "_sbx_no_events.jsonl"
        # a flipped state whose trail lost its events: the strip stays,
        # the live aftermath pends with the precise reason
        _fl_bare = BASE / "_sbx_flips_bare.json"
        _fl_bare.write_text(json.dumps({"OUT": {"flipped": True}}) + "\n",
                            encoding="utf-8")
        _tbw_mod.FLIP_FILE = _fl_bare
        _aud_b = app_mod._render_flip_audit()
        check("a benched state with no flip event pends its live "
              "aftermath with the precise reason",
              "live aftermath: pending — no flip event on the trail yet"
              in _aud_b and "CURRENTLY BENCHED: OUT" in _aud_b, _aud_b[-400:])
        _fl_bare.unlink(missing_ok=True)
        _tbw_mod.FLIP_FILE = BASE / "_sbx_no_flips.json"
        check("an empty trail renders the quiet one-liner",
              "audit trail is empty" in app_mod._render_flip_audit())
    finally:
        _tbw_mod.FLIP_EVENTS_FILE = _real_evs
        _tbw_mod.FLIP_FILE = _real_fl
        _tbw_mod.HISTORY_FILE = _real_hist_aud
        for f in (_ev_sbx, _fl_sbx, _h_sbx, _ev_sbx_sim, _ev_only_sims,
                  BASE / "_sbx_no_events.jsonl",
                  BASE / "_sbx_no_flips.json"):
            f.unlink(missing_ok=True)
except Exception as exc:
    check("the flip audit pins ran", False, exc)

# ── THE FLIP GRADE plate (Sep 11): the grade's latest per-state
# verdicts — pooled dollars, proximity, aftermath — rendered from the
# NEWEST section of _flipgrade.txt, on MASTER, no file to open.
try:
    _fgv = getattr(app_mod, "_flipgrade_verdict", None)
    check("the flip grade verdict renderer exists on MASTER", callable(_fgv))
    _fg_sbx = BASE / "_sbx_flipgrade.txt"
    _fg_sbx.write_text(
        "=== THE FLIP GRADE — the bench machinery judged on its "
        "own ledger ===\n"
        "graded at: 2026-09-12 02:00 UTC  (2 weeks: "
        "2026-W37..2026-W38)\n"
        "\n"
        "OUT  (2 two-sided weeks; BENCHED — the quantile ships)\n"
        "  edges by week: -0.48 -0.07\n"
        "  pooled (2w): fit 190 fires $+3.60/fire vs quantile 112 fires "
        "$+3.86/fire — fit edge -0.25\n"
        "  flip-back proximity: 1/2 winning weeks (returns at 2 with a "
        "positive pool; 1 to go)\n"
        "  ⚡ flip at 2026-W38: in the 1 weeks after, the quantile paid "
        "$+3.65/fire (62 fires) vs the fit's counterfactual $+3.58/fire "
        "— the decision PAID\n"
        "  ⚡ flip_back at 2026-W39: aftermath pending — no post-decision "
        "weeks graded yet\n"
        "\n"
        "FLAT: no two-sided weeks — not graded\n"
        "\n=== THE FLIP GRADE — OLDER RUN ===\n"
        "graded at: 2026-09-01 12:00 UTC  (1 weeks: 2026-W37..2026-W37)\n"
        "OUT  (1 two-sided weeks; the fit is live)\n"
        "  pooled (1w): fit 86 fires $+3.63/fire vs quantile 50 fires "
        "$+4.11/fire — fit edge -0.48\n"
        "\n=== THE FLIP GRADE — even older ===\n"
        "graded at: 2026-08-25 08:00 UTC  (2 weeks: 2026-W35..2026-W36)\n",
        encoding="utf-8")
    try:
        _fg_json_absent = str(BASE / "_sbx_flipgrade_absent.json")
        _h = _fgv(txt_path=str(_fg_sbx), json_path=_fg_json_absent)
        _t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _h))
        check("the plate renders the NEWEST section (not the older runs)",
              "2 weeks graded (2026-W37..2026-W38)" in _t
              and "2026-09-12" in _t and "2026-09-01" not in _t
              and "2026-08-25" not in _t, _t[:600])
        check("the benched state's regime line renders",
              "BENCHED — the quantile ships" in _t, _t[:600])
        check("the pooled dollars render with the canonical delta",
              "fit 190 fires $+3.60/fire" in _t
              and "quantile 112 fires $+3.86/fire" in _t
              and "Δ -0.25" in _t, _t[:800])
        check("the per-week edge strip renders",
              "edges by week -0.48 · -0.07" in _t, _t[:800])
        check("the flip-back proximity renders",
              "flip-back proximity:" in _t and "1 to go" in _t, _t[:800])
        check("a graded aftermath renders with its verdict colored",
              "⚡ flip at 2026-W38" in _t and "the decision PAID" in _t
              and "#4be38a" in _h, _t[:1000])
        check("a pending aftermath renders dim (no fake verdict)",
              "⚡ flip_back at 2026-W39" in _t
              and "aftermath pending" in _t and "#9aa0a6" in _h,
              _t[:1200])
        check("a one-sided state renders its honest degrade",
              "FLAT: no two-sided weeks — not graded" in _t, _t[:1200])
        # ── THE OPEN BENCH (Sep 12): the sidecar's live verdict strip —
        #    a currently-benched state's PAID/COST on the plate itself ──
        _fg_json = BASE / "_sbx_flipgrade_ob.json"
        _fg_json.write_text(json.dumps({"generated_ts": "2026-09-12 07:57 UTC",
            "open_bench": [{"state": "OUT",
                            "base": "OUT — the quantile ships until the fit "
                                    "earns a flip-back",
                            "verdict": "PAYING", "verdict_col": "#4be38a",
                            "text": "over the 2 weeks since, the quantile "
                                   "realized $+1.00/fire (20 fires) vs the "
                                   "fit's $-1.00/fire over the same weeks"}]}),
            encoding="utf-8")
        _h_ob = _fgv(txt_path=str(_fg_sbx), json_path=str(_fg_json))
        check("the open-bench strip renders on the plate with the live "
              "verdict and its dollars",
              "CURRENTLY BENCHED (live):" in _h_ob
              and "the bench is PAYING" in _h_ob
              and "$+1.00/fire (20 fires)" in _h_ob
              and "#4be38a" in _h_ob, _h_ob[:900])
        _fg_json.write_text(json.dumps({"open_bench": [
            {"state": "FLAT", "base": "FLAT — the quantile ships",
             "verdict": None, "verdict_col": "#9aa0a6",
             "text": "pending — no post-decision weeks graded yet"}]}),
            encoding="utf-8")
        _h_pend = _fgv(txt_path=str(_fg_sbx), json_path=str(_fg_json))
        check("a pending open-bench renders its honest reason, never a "
              "fake verdict",
              "pending — no post-decision weeks graded yet" in _h_pend
              and "the bench is " not in _h_pend, _h_pend[:900])
        _fg_json.write_text("{not json", encoding="utf-8")
        _h_bad = _fgv(txt_path=str(_fg_sbx), json_path=str(_fg_json))
        check("a malformed sidecar degrades to no strip (the grade rows "
              "still render)",
              "CURRENTLY BENCHED" not in _h_bad
              and "2 weeks graded" in _h_bad, _h_bad[:400])
        # ── THE POOL-GATE DIALS (Sep 13): the sidecar's verdict blocks —
        #    the dial's monthly grade on the plate, no txt to open ──
        _fg_json.write_text(json.dumps({
            "open_bench": [],
            "poolgate_family": {"net": {"0.25": 0.0, "0.5": 40.0},
                                "baseline_ok": True, "cascades": {}},
            "poolmin_default": {"recommendation": None, "n_priced": 0,
                                "shipped": False, "mode": "walked"}}),
            encoding="utf-8")
        _h_pg = _fgv(txt_path=str(_fg_sbx), json_path=str(_fg_json))
        _t_pg = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _h_pg))
        check("the pool-gate dials strip renders the walked family net "
              "per threshold with verdict colors and the arc-3 hold",
              "POOL-GATE DIALS:" in _t_pg
              and "+0.25 $+0.00" in _t_pg and "+0.50 $+40.00" in _t_pg
              and "#4be38a" in _h_pg and "#9aa0a6" in _h_pg
              and "arc-3 holds 0.0" in _t_pg
              and "0 priced re-arm(s)" in _t_pg
              and "every rolling window positive" in _t_pg, _t_pg[:1200])
        _fg_json.write_text(json.dumps({
            "open_bench": [],
            "poolgate_family": {"net": {"0.25": 0.0, "0.5": -40.0},
                                "baseline_ok": True, "cascades": {}},
            "poolmin_default": {"recommendation": 0.5, "n_priced": 3,
                                "stable": True, "mode": "walked",
                                "proposal": {"pool_min": 0.5,
                                             "n_priced": 3,
                                             "net": -40.0}}}),
            encoding="utf-8")
        _h_pg2 = _fgv(txt_path=str(_fg_sbx), json_path=str(_fg_json))
        _t_pg2 = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _h_pg2))
        check("a MISSED threshold renders red and an arc-3 raise "
              "PROPOSAL renders its bar with the apply-by-hand note — "
              "the plate never implies the machinery moved the gate",
              "#ff6b5e" in _h_pg2
              and "arc-3 proposes a raise to" in _t_pg2
              and "+0.50" in _t_pg2 and "(apply by hand)" in _t_pg2,
              _t_pg2[:1200])
        _fg_json.write_text(json.dumps({
            "open_bench": [],
            "poolgate_family": {"net": {}, "baseline_ok": False,
                                "cascades": {}}}), encoding="utf-8")
        _h_pg3 = _fgv(txt_path=str(_fg_sbx), json_path=str(_fg_json))
        _t_pg3 = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _h_pg3))
        check("a baseline-hold renders the withheld honesty (the dial "
              "grade is not trustworthy until the walk reconciles)",
              "walk baseline held" in _t_pg3
              and "reconciled" in _t_pg3, _t_pg3[:1200])
        _fg_json.write_text(json.dumps({"open_bench": []}),
                            encoding="utf-8")
        _h_pg4 = _fgv(txt_path=str(_fg_sbx), json_path=str(_fg_json))
        check("a sidecar without the dial blocks renders no strip — a "
              "held grade cleared them with the bench dollars",
              "POOL-GATE DIALS" not in _h_pg4
              and "2 weeks graded" in _h_pg4, _h_pg4[:400])
        _fg_json.unlink(missing_ok=True)
    finally:
        _fg_sbx.unlink(missing_ok=True)
    # the missing-file degrade (json absent too — no open-bench strip)
    _h_missing = _fgv(txt_path=str(BASE / "_sbx_flipgrade_absent.txt"),
                      json_path=str(BASE / "_sbx_flipgrade_absent.json"))
    check("a missing grade file renders the quiet amber one-liner",
          "the grade has not run yet" in _h_missing,
          _h_missing[:400])
    check("the verdict plate accepts an injected open-bench sidecar",
          "json_path" in app_mod._flipgrade_verdict.__doc__)
    # ── THE CONFIRM-TO-APPLY (Sep 13): the arc-3 sweep STAGES its
    #    proposal; render_master shows the apply button for a coherent
    #    stage. All pins patch the re-price so the sandbox never lets
    #    recommend_pool_min's hold path touch the production stage
    #    file, and the stage itself lives in the sandbox. ──────────
    try:
        import tidebar_weekly as _tbw_ap
        _real_rp = _tbw_ap.recommend_pool_min
        # REPO-CLEAN (Sep 23, the diag_hygiene trips): this stage file
        # used to live at BASE/_sbx_poolmin_staged.json — a battery-kill
        # mid-suite skips every finally and strands it in the repo root,
        # and the two late stage writes sat outside the block's own
        # finally, leaking it on every CLEAN run too. The system temp
        # dir is OS-managed: no repo stray can exist, killed run or not.
        _stage = Path(tempfile.gettempdir()) / "apptest_poolmin_staged.json"
        try:
            _tbw_ap.recommend_pool_min = (
                lambda: {"recommendation": None, "n_priced": 0})
            _stage.write_text(json.dumps(
                {"pool_min": 0.5, "net": 40.0, "n_priced": 3,
                 "ts": "sandbox"}), encoding="utf-8")
            _aps = app_mod._poolgate_apply_state(staged_path=str(_stage))
            check("a stage the live sweep no longer proposes reads "
                  "STALE with its reason — a stale stage renders a "
                  "caption, never an apply button",
                  _aps.get("stale") is True and _aps.get("pool_min") == 0.5
                  and "no longer proposes" in _aps.get("stale_reason", ""),
                  _aps)
            _tbw_ap.recommend_pool_min = (
                lambda: {"recommendation": 0.5, "n_priced": 3,
                         "stable": True,
                         "proposal": {"pool_min": 0.5, "net": 40.0}})
            _apc = app_mod._poolgate_apply_state(staged_path=str(_stage))
            check("a coherent stage reads the button-shape state (no "
                  "stale key; the bar, net dollars, and priced count "
                  "ride through)",
                  "stale" not in _apc and _apc.get("pool_min") == 0.5
                  and _apc.get("net") == 40.0 and _apc.get("n_priced") == 3,
                  _apc)
        finally:
            _tbw_ap.recommend_pool_min = _real_rp
            _stage.unlink(missing_ok=True)
        try:
            _stage.write_text("not json", encoding="utf-8")
            check("an unparseable stage file reads empty — no strip",
                  app_mod._poolgate_apply_state(staged_path=str(_stage)) == {})
            _stage.write_text(json.dumps({"pool_min": 5.0}),
                              encoding="utf-8")
            check("an out-of-range staged bar reads empty — never "
                  "rendered as applyable",
                  app_mod._poolgate_apply_state(staged_path=str(_stage)) == {})
        finally:
            # these two late stage files sit OUTSIDE the block's own
            # finally above — without this hand-back every clean run
            # leaked the stray; the unlink now tidies the temp copy so
            # even %TEMP% never accumulates it
            _stage.unlink(missing_ok=True)
        check("a missing stage file reads empty",
              app_mod._poolgate_apply_state(
                  staged_path=str(BASE / "_sbx_absent_stage.json")) == {})
        # ── THE LEARNED BARS on the plate (Sep 13): the per-state pool
        #    bars travel in poolmin_learned — a state in force shows its
        #    green bar, a thin state stays dim on the global dial, and
        #    a state whose fresh verdict derives a bar the gate doesn't
        #    hold yet says it ships next pass. The block travels only on
        #    a completed grade, so absent -> no strip (hold semantics).
        #    Fresh sandbox files: the earlier block's finally unlinked
        #    its own. ────────────────────────────────────────────────
        _fg_sbx2 = BASE / "_sbx_flipgrade_lb.txt"
        _fg_sbx2.write_text(
            "=== THE FLIP GRADE — the bench machinery judged on its "
            "own ledger ===\n"
            "graded at: 2026-09-13 00:00 UTC  (2 weeks: "
            "2026-W37..2026-W38)\n\n"
            "OUT  (2 two-sided weeks; BENCHED — the quantile ships)\n"
            "  pooled (2w): fit 190 fires $+3.60/fire vs quantile "
            "112 fires $+3.86/fire — fit edge -0.25\n",
            encoding="utf-8")
        _fg_json.write_text(json.dumps({
            "open_bench": [],
            "poolgate_family": {"net": {"0.5": 40.0},
                                "baseline_ok": True, "cascades": {}},
            "poolmin_learned": {
                "map": {"OUT": 0.75},
                "learner": {
                    "OUT": {"pool_min": 0.75, "n_edges": 6,
                            "median": 0.55, "reason": ""},
                    "FLAT": {"pool_min": 0.5, "n_edges": 6,
                             "median": 0.42, "reason": ""},
                    "RISING": {"pool_min": None, "n_edges": 2,
                               "median": None,
                               "reason": "thin (2/6 edge weeks)"}}}}),
            encoding="utf-8")
        _h_lb = _fgv(txt_path=str(_fg_sbx2), json_path=str(_fg_json))
        _t_lb = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _h_lb))
        check("the learned bars render on the dials line: the in-force "
              "bar green, the derived bar dim as ships-next-pass, the "
              "thin state dim with its reason",
              "OUT +0.75" in _t_lb and "#4be38a" in _h_lb
              and "FLAT derives +0.50 (ships next pass)" in _t_lb
              and "RISING global (thin (2/6 edge weeks))" in _t_lb,
              _t_lb[:1200])
        _fg_json.write_text(json.dumps({
            "open_bench": [],
            "poolgate_family": {"net": {"0.5": 40.0},
                                "baseline_ok": True, "cascades": {}}}),
            encoding="utf-8")
        _h_nlb = _fgv(txt_path=str(_fg_sbx2), json_path=str(_fg_json))
        check("a sidecar without the learned block renders the strip "
              "from what IS present (the family bit) and no learned "
              "bits — a hold cleared the learned evidence with the "
              "other dial verdicts, never floored from stale data",
              "POOL-GATE DIALS" in _h_nlb
              and "+0.50 $+40.00" in _h_nlb
              and "+0.75" not in _h_nlb
              and "global (" not in _h_nlb
              and "ships next pass" not in _h_nlb, _h_nlb[:900])
        # ── THE FBW WALK on the plate (Sep 13): the streak dial's
        #    walked family per candidate plus its rolling-window-
        #    gated recommendation — advice for her hand, never an
        #    auto-move. Baseline-held renders the withheld honesty;
        #    absent blocks render nothing. ──────────────────────────
        _fg_json.write_text(json.dumps({
            "open_bench": [],
            "fbwgate_family": {"net": {"3": 82.5, "4": -98.4},
                               "baseline_ok": True, "cascades": {}},
            "fbw_default": {"recommendation": 3, "n_priced": 3,
                            "stable": True, "mode": "walked",
                            "proposal": {"fbw": 3, "net": 82.5}}}),
            encoding="utf-8")
        _h_fw = _fgv(txt_path=str(_fg_sbx2), json_path=str(_fg_json))
        _t_fw = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _h_fw))
        check("the fbw walk renders beside the pool dials: family "
              "nets verdict-colored and the proposal named as advice "
              "for her hand, never auto-applied",
              "fbw 3 $+82.50" in _t_fw and "fbw 4 $-98.40" in _t_fw
              and "#4be38a" in _h_fw and "#ff6b5e" in _h_fw
              and "fbw walk proposes raising the streak to 3" in _t_fw
              and "never auto-applied" in _t_fw, _t_fw[:1400])
        _fg_json.write_text(json.dumps({
            "open_bench": [],
            "fbwgate_family": {"net": {"3": 0.0},
                               "baseline_ok": True, "cascades": {}},
            "fbw_default": {"recommendation": None, "n_priced": 0,
                            "stable": None}}), encoding="utf-8")
        _h_fwh = _fgv(txt_path=str(_fg_sbx2), json_path=str(_fg_json))
        _t_fwh = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _h_fwh))
        check("the fbw walk's hold renders honestly (zero priced "
              "re-arms — a raise needs every rolling window positive)",
              "fbw walk holds 2 (0 priced re-arm(s)" in _t_fwh
              and "every rolling window positive" in _t_fwh, _t_fwh[:900])
        _fg_json.write_text(json.dumps({
            "open_bench": [],
            "fbwgate_family": {"net": {}, "baseline_ok": False,
                               "cascades": {}}}), encoding="utf-8")
        _h_fwb = _fgv(txt_path=str(_fg_sbx2), json_path=str(_fg_json))
        check("a baseline-held fbw walk renders the withheld honesty "
              "— the dial grade is not trustworthy until reconciled",
              "fbw walk baseline held" in _h_fwb
              and "reconciled" in _h_fwb, _h_fwb[:900])
        _fg_json.write_text(json.dumps({
            "open_bench": [],
            "poolgate_family": {"net": {"0.5": 40.0},
                                "baseline_ok": True, "cascades": {}}}),
            encoding="utf-8")
        _h_nfw = _fgv(txt_path=str(_fg_sbx2), json_path=str(_fg_json))
        check("a sidecar without the fbw blocks renders no fbw bits "
              "— a hold cleared the dial's evidence with the rest",
              "POOL-GATE DIALS" in _h_nfw and "fbw walk" not in _h_nfw
              and "fbw 3" not in _h_nfw, _h_nfw[:900])
        # ── THE STREAK DIAL on the plate (Sep 13): poolgate_streak's
        #    per-FBW evidence renders beside the pool dials — net
        #    dollars verdict-colored, denied/delayed counts dim. A hold
        #    clears the block; corrupt values are skipped, never fatal.
        #    ────────────────────────────────────────────────────────
        _fg_json.write_text(json.dumps({
            "open_bench": [],
            "poolgate_family": {"net": {"0.5": 40.0},
                                "baseline_ok": True, "cascades": {}},
            "poolgate_streak": {
                "3": {"denied": 1, "delayed": 0, "net": 82.5,
                      "events": []},
                "4": {"denied": 0, "delayed": 1, "net": -98.4,
                      "events": []}}}), encoding="utf-8")
        _h_st = _fgv(txt_path=str(_fg_sbx2), json_path=str(_fg_json))
        _t_st = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _h_st))
        check("the streak dial renders beside the pool dials: each "
              "graduated FBW's net dollars verdict-colored with its "
              "denied/delayed counts dim",
              "streak FBW=3 $+82.50 (1 denied · 0 delayed)" in _t_st
              and "streak FBW=4 $-98.40 (0 denied · 1 delayed)" in _t_st
              and "#4be38a" in _h_st and "#ff6b5e" in _h_st
              and _t_st.find("+0.50 $+40.00") < _t_st.find("streak FBW=3"),
              _t_st[:1200])
        _fg_json.write_text(json.dumps({
            "open_bench": [],
            "poolgate_family": {"net": {"0.5": 40.0},
                                "baseline_ok": True, "cascades": {}}}),
            encoding="utf-8")
        _h_nst = _fgv(txt_path=str(_fg_sbx2), json_path=str(_fg_json))
        check("a sidecar without the streak block renders no streak "
              "bits — a hold cleared the dial's evidence with the rest",
              "POOL-GATE DIALS" in _h_nst
              and "streak FBW" not in _h_nst, _h_nst[:900])
        _fg_json.write_text(json.dumps({
            "open_bench": [],
            "poolgate_streak": {"3": {"denied": "x", "delayed": None,
                                      "net": "oops"}}}),
            encoding="utf-8")
        _h_cst = _fgv(txt_path=str(_fg_sbx2), json_path=str(_fg_json))
        check("a corrupt streak entry is skipped, never fatal (the "
              "strip degrades, the grade rows survive)",
              "streak FBW=3" not in _h_cst
              and "2 weeks graded" in _h_cst, _h_cst[:400])
        _fg_sbx2.unlink(missing_ok=True)
        _fg_json.unlink(missing_ok=True)
    except Exception as exc:
        check("the pool-gate apply-state pins ran", False, exc)
except Exception as exc:
    check("the flip grade verdict pins ran", False, exc)

try:
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(BASE / "app.py"), default_timeout=120)
    at.run()
    lines.append(f"exception: {at.exception}")
    body = " ".join((m.value or "") for m in at.markdown)
    for probe in ["KRAKEN SCOUT", "TODAY", "NOW:", "Win Feed", "Today's Score", "New here"]:
        lines.append(f"{probe}: {'FOUND' if probe in body else 'missing'}")
    check("the app still boots clean (no exception)", not at.exception,
          at.exception)
    # ── THE SIMS TOGGLE PERSISTS (Sep 12): the checkbox is backed by
    #    session_state['show_flip_sims'], so her show/hide choice
    #    survives reruns instead of snapping back to 'show' on every
    #    page interaction ─────────────────────────────────────────
    try:
        _cbs = [c for c in at.checkbox
                if c.label == "show simulated worked examples"]
        check("the sims toggle renders in the booted app (the "
              "production trail carries sims)",
              bool(_cbs), f"{len(at.checkbox)} checkboxes on the page")
        if _cbs:
            _cbs[0].uncheck()
            at.run()
            _body_h = " ".join((m.value or "") for m in at.markdown)
            check("unchecking hides the sims under the stable key",
                  not at.exception
                  and "[SIMULATION" not in _body_h
                  and "simulated worked examples hidden" in _body_h,
                  (at.exception, len(_body_h)))
            # ── THE MIRROR, END TO END (Sep 12): the same session_state
            #    drives the MASTER chart's hollow glyphs — one choice,
            #    both surfaces, no second widget ──────────────────────
            check("the MASTER chart mirrors the hidden toggle (hollow "
                  "glyphs drop, the caption notes it)",
                  not at.exception
                  and "[SIMULATION" not in _body_h
                  and "simulated glyphs hidden by the trail's toggle" in _body_h,
                  _body_h.count("simulated glyphs hidden"))
            at.run()   # a PLAIN rerun: the choice must survive it
            _body_p = " ".join((m.value or "") for m in at.markdown)
            check("the hidden choice survives a rerun (session_state, "
                  "not the widget default)",
                  not at.exception
                  and "[SIMULATION" not in _body_p
                  and "simulated worked examples hidden" in _body_p,
                  len(_body_p))
            _cbs2 = [c for c in at.checkbox
                     if c.label == "show simulated worked examples"]
            _cbs2[0].check()
            at.run()
            _body_s = " ".join((m.value or "") for m in at.markdown)
            check("checking again brings the worked examples back",
                  not at.exception and "[SIMULATION" in _body_s,
                  len(_body_s))
            check("the chart's hollow glyphs return with the toggle (and "
                  "the mirror note lifts)",
                  not at.exception and "[SIMULATION" in _body_s
                  and "simulated glyphs hidden by the trail's toggle"
                  not in _body_s, len(_body_s))
            # ── THE ONE-CLICK SHOW (Sep 12): the hidden-count note's
            #    button restores the sims without hunting the checkbox ──
            _cbs3 = [c for c in at.checkbox
                     if c.label == "show simulated worked examples"]
            _cbs3[0].uncheck()
            at.run()
            _btns = [b for b in at.button
                     if b.label == "show worked examples"]
            check("the one-click 'show worked examples' button renders "
                  "beside the hidden note",
                  bool(_btns), f"{len(at.button)} buttons on the page")
            if _btns:
                _btns[0].click()
                at.run()
                _body_b = " ".join((m.value or "") for m in at.markdown)
                check("clicking the note's button restores the sims "
                      "(trail rows AND the chart's hollow glyphs)",
                      not at.exception and "[SIMULATION" in _body_b
                      and "simulated glyphs hidden by the trail's toggle"
                      not in _body_b,
                      (at.exception, len(_body_b)))
                check("the one-click show lands on the toggle too "
                      "(one choice, both surfaces)",
                      bool([c for c in at.checkbox
                            if c.label == "show simulated worked examples"
                            and c.value]),
                      [c.value for c in at.checkbox])
    except Exception as exc:
        check("the sims-toggle persistence pins ran", False, exc)
    # ── THE VERDICT FILTER PERSISTS (Sep 12): the dropdown is
    #    session_state-backed like the sims toggle — her
    #    PAYING/COSTING/pending choice survives reruns instead of
    #    snapping back to 'All verdicts' on every page interaction
    #    ──────────────────────────────────────────────────────────
    try:
        def _vf():
            # the AppTest proxy has no .get — bracket access with a
            # fail-open None keeps the pins honest on either side
            try:
                return at.session_state["verdict_filter"]
            except Exception:
                return None

        _sbs = [sb for sb in at.selectbox if sb.label == "verdict"]
        check("the verdict control renders at MASTER level (it moved "
              "out of the trail expander — near the chart it drives)",
              len(_sbs) == 1, f"{len(at.selectbox)} selectboxes on the page")
        if _sbs:
            _sbs[0].select("COSTING")
            at.run()
            _body_v = " ".join((m.value or "") for m in at.markdown)
            check("selecting COSTING filters the trail (the semantic "
                  "pick lands in session_state)",
                  not at.exception
                  and _vf() == "COSTING"
                  and "hidden by the verdict filter" in _body_v,
                  (at.exception, _vf(), len(_body_v)))
            at.run()   # a PLAIN rerun: the choice must survive it
            _body_p2 = " ".join((m.value or "") for m in at.markdown)
            check("the verdict choice survives a plain rerun "
                  "(session_state, not the widget default)",
                  not at.exception
                  and _vf() == "COSTING"
                  and "hidden by the verdict filter" in _body_p2,
                  (at.exception, _vf(), len(_body_p2)))
            _sbs2 = [sb for sb in at.selectbox if sb.label == "verdict"]
            _sbs2[0].select("All verdicts")
            at.run()
            _body_a = " ".join((m.value or "") for m in at.markdown)
            check("switching back to All verdicts unhides the rows",
                  not at.exception
                  and _vf() == "All verdicts"
                  and "hidden by the verdict filter" not in _body_a,
                  (at.exception, _vf()))
        check("ONE widget: exactly one verdict selectbox on the whole "
              "page — the trail resolves the MASTER pick without "
              "rendering its own (the duplicate-key class stays closed)",
              len([sb for sb in at.selectbox
                   if sb.label == "verdict"]) == 1,
              f"{len(at.selectbox)} selectbox(es) on the page")
        check("the trail's hidden-note points at the MASTER control "
              "(the sync is discoverable from inside the trail)",
              "the MASTER control above, beside the chart"
              in _body_p2)
    except Exception as exc:
        check("the verdict-filter persistence pins ran", False, exc)
    # ── THE ST-SHADOW GATE (Sep 12): the swallowed-widget class — a
    #    local named st poisoning st.markdown/selectbox calls inside a
    #    fail-open except, exactly what silently killed the trail's
    #    toggle once — is closed for good: the hardened AST analyzer's
#    own verdict (selftest + ground truth + watch-list coverage +
#    production CLEAN) rides every suite run. FLEET (Sep 13): the
#    analyzer now discovers EVERY module importing streamlit —
#    discovery, per-file ground truth, gate membership, and a
#    fleet-wide production CLEAN verdict ride every suite run.
    #    ──────────────────────────────────────────────────────────
    try:
        import diag_stshadow as _ds

        _st_detail, _st_ok = _ds.selftest()
        check("st-shadow selftest: the analyzer catches every variant "
              "(before/after-bind, walrus, global-rebind, wrong-module "
              "import; clean snippets stay clean)", _st_ok, _st_detail)
        _fleet = _ds.analyze_all()
        _by_name = {p.name: (p, imp, mod, finds, tree)
                    for p, imp, mod, finds, tree in _fleet}
        check("FLEET DISCOVERY: the sweep finds the live console (and "
              "any other streamlit-importing module) — it can never "
              "silently narrow back to one file",
              "app.py" in _by_name and len(_fleet) >= 1,
              f"discovered: {sorted(_by_name)}")
        check("GATE MEMBERSHIP: production modules are gated; "
              "diagnostics, scratch (_-prefixed) and the test harness "
              "are swept but report-only",
              (_ds._is_app_candidate(Path("app.py"))
               and not _ds._is_app_candidate(Path("diag_x.py"))
               and not _ds._is_app_candidate(Path("test_x.py"))
               and not _ds._is_app_candidate(Path("_scratch.py"))))
        check("WATCH-LIST: covers the module surface beyond widgets "
              "(st.secrets must never sail past the detector again)",
              "secrets" in _ds.ST_NAMES and _ds.ST_NAMES >= _ds.WIDGETS)
        if "app.py" in _by_name:
            _ap, _aimp, _amod, _afinds, _atree = _by_name["app.py"]
            check("st-shadow ground truth (app.py): scoped loads equal "
                  "an independent whole-tree walk",
                  sum(len(s.loads) for s in _ds.iter_scopes(_amod))
                  == len([n for n in ast.walk(_atree)
                          if isinstance(n, ast.Attribute)
                          and isinstance(n.value, ast.Name)
                          and n.value.id == "st"
                          and n.attr in _ds.ST_NAMES]))
        _gate_finds = [(p, f) for p, imp, mod, finds, tree in _fleet
                       if _ds._is_app_candidate(p) for f in finds]
        check(f"PRODUCTION FLEET CLEAN ({len(_fleet)} streamlit "
              "module(s) discovered): no scope in any gated module "
              "binds a local st and calls st widgets — the "
              "swallowed-widget class is closed fleet-wide",
              not _gate_finds,
              f"{len(_gate_finds)} finding(s): "
              + "; ".join(f"{p.name}:{ln} st.{a}"
                          for p, (_, a, ln, _, _) in _gate_finds[:6]))
        # the fleet gate must BITE: a temp project with a poisoned
        # production module and a poisoned scratch file — the first is
        # gated, the second report-only — hermetic, touches nothing
        import tempfile as _tempfile_fs
        with _tempfile_fs.TemporaryDirectory() as _fs_td:
            _fs_root = Path(_fs_td)
            (_fs_root / "poisoned.py").write_text(
                "import streamlit as st\n"
                "def render(rows):\n"
                "    for st in rows:\n"
                "        pass\n"
                "    try:\n"
                "        st.markdown(\"\".join(rows))\n"
                "    except Exception:\n"
                "        pass\n", encoding="utf-8")
            (_fs_root / "_scratch_leak.py").write_text(
                "import streamlit as st\n"
                "def go(rows):\n"
                "    for st in rows:\n"
                "        pass\n"
                "    st.markdown(\"x\")\n", encoding="utf-8")
            (_fs_root / "no_st_here.py").write_text(
                "x = 1\n", encoding="utf-8")   # never discovered
            _fs_fleet = _ds.analyze_all(root=_fs_root)
            _fs_names = {p.name for p, *_ in _fs_fleet}
            _fs_gate = [(p, f) for p, imp, mod, finds, tree in _fs_fleet
                        if _ds._is_app_candidate(p) for f in finds]
            check("FLEET BITES: a poisoned production module in a fresh "
                  "tree is discovered, gated, and flagged; a poisoned "
                  "scratch file is report-only; a streamlit-free module "
                  "is never discovered",
                  (_fs_names == {"poisoned.py", "_scratch_leak.py"}
                   and len(_fs_gate) == 1
                   and _fs_gate[0][0].name == "poisoned.py"
                   and any(a == "markdown" for _p,
                           (_lbl, a, _ln, _v, _tr) in _fs_gate)),
                  f"discovered={sorted(_fs_names)} "
                  f"gate={[(p.name, f[1], f[2]) for p, f in _fs_gate]}")
    except Exception as exc:
        check("the st-shadow gate pins ran", False, exc)
    # ── THE PREFS MIRROR (Sep 12): her show-sims and verdict choices
    #    mirror into a JSON preference file so they survive CLOSING
    #    THE TAB — not just Streamlit reruns. The booted-AppTest pins
    #    prove the live loop (interact → file → fresh session), and a
    #    sandboxed bare probe pins the guard rails (corrupt file,
    #    restore semantics, restore-guard) without touching prod ──
    try:
        import json as _json_pm
        import tempfile as _tempfile_pm
        from pathlib import Path as _PathP
        _pm_sbx = _PathP(_tempfile_pm.mkdtemp())
        _prefs_env_set(_pm_sbx / "prefs.json")   # fresh sandbox, no file

        from streamlit.testing.v1 import AppTest as _AT_PM
        _at_pm = _AT_PM.from_file(str(BASE / "app.py"),
                                  default_timeout=120)
        _at_pm.run()
        _sels_pm = [sb for sb in _at_pm.selectbox
                    if sb.label == "verdict"]
        check("prefs mirror: the booted app renders the verdict "
              "dropdown with a clean prefs sandbox",
              bool(_sels_pm), f"{len(_sels_pm)} selectboxes")
        if _sels_pm:
            _sels_pm[0].select("COSTING")
            _at_pm.run()
            _pf1 = _json_pm.loads(
                (_pm_sbx / "prefs.json").read_text(encoding="utf-8"))
            check("prefs mirror: selecting COSTING lands in the JSON "
                  "file (the choice leaves the session)",
                  _pf1.get("verdict_filter") == "COSTING", _pf1)
            # a FRESH AppTest = a new browser session: the new tab
            # restores her pick from the file
            _at_pm2 = _AT_PM.from_file(str(BASE / "app.py"),
                                       default_timeout=120)
            _at_pm2.run()
            check("prefs mirror: a FRESH session restores the verdict "
                  "pick from the file (tab-close persistence)",
                  not _at_pm2.exception
                  and _at_pm2.session_state["verdict_filter"] == "COSTING",
                  (str(_at_pm2.exception),
                   _pf1.get("verdict_filter")))
        _chs_pm = [c for c in _at_pm.checkbox
                   if c.label == "show simulated worked examples"]
        check("prefs mirror: the sims toggle renders in the booted "
              "app (the trail carries graded sims)",
              bool(_chs_pm), f"{len(_chs_pm)} checkboxes")
        if _chs_pm:
            _chs_pm[0].uncheck()
            _at_pm.run()
            _pf2 = _json_pm.loads(
                (_pm_sbx / "prefs.json").read_text(encoding="utf-8"))
            check("prefs mirror: unchecking the toggle mirrors "
                  "show_flip_sims=false into the file",
                  _pf2.get("show_flip_sims") is False, _pf2)
            _at_pm3 = _AT_PM.from_file(str(BASE / "app.py"),
                                       default_timeout=120)
            _at_pm3.run()
            check("prefs mirror: a FRESH session restores the hidden "
                  "sims (chart mirror reads the file too)",
                  not _at_pm3.exception
                  and _at_pm3.session_state["show_flip_sims"] is False
                  and app_mod._sims_hidden_mirror() is True,
                  (str(_at_pm3.exception),
                   _pf2.get("show_flip_sims")))
            _chs2_pm = [c for c in _at_pm.checkbox
                        if c.label == "show simulated worked examples"]
            _chs2_pm[0].check()   # re-query: the old element went stale
            _at_pm.run()
            _pf3 = _json_pm.loads(
                (_pm_sbx / "prefs.json").read_text(encoding="utf-8"))
            check("prefs mirror: re-checking mirrors show=true and a "
                  "fresh session restores SHOW (both directions)",
                  _pf3.get("show_flip_sims") is True,
                  _pf3)
    except Exception as exc:
        check("the prefs-mirror pins ran", False, exc)
    # ── THE PREFS GUARD RAILS (Sep 12): the restore semantics and the
    #    fail-open behavior, probed bare (no runtime) on a fresh
    #    sandbox so no test ever touches the production file ──
    try:
        _prefs_env_set(_pm_sbx / "guards.json")
        import importlib as _importlib_pm
        import app as _app_pm
        _importlib_pm.reload(_app_pm)   # re-read the sandbox env
        check("prefs guards: no file reads as {} (defaults), the "
              "mirror fails open to SHOW",
              _app_pm._prefs_load() == {}
              and _app_pm._sims_hidden_mirror() is False)
        (_pm_sbx / "guards.json").write_text("{not json", encoding="utf-8")
        check("prefs guards: a CORRUPT file reads as {} — the mirror "
              "never raises, the console never blocks on it",
              _app_pm._prefs_load() == {}
              and _app_pm._sims_hidden_mirror() is False)
        _app_pm._prefs_save({"show_flip_sims": "junk",
                             "verdict_filter": "BUSTED"})
        check("prefs guards: the file stores whatever was saved, but "
              "restore's membership/isinstance guards reject junk (a "
              "bad verdict name is not a known option; a non-bool "
              "show restores the SHOW default)",
              _app_pm._prefs_load().get("verdict_filter") == "BUSTED"
              and ("BUSTED" not in ("PAYING", "COSTING", "pending",
                                    "All verdicts"))
              and not isinstance(
                  _app_pm._prefs_load().get("show_flip_sims"), bool),
              _app_pm._prefs_load())
    except Exception as _exc_pm:
        check("the prefs guard-rail pins ran", False, _exc_pm)
    finally:
        _prefs_env_reset()   # every later pin runs unsandboxed
    # ── THE CACHE-STALENESS GATE (Sep 12): the sims-toggle class —
    # ── THE CACHE-STALENESS GATE (Sep 12): the sims-toggle class —
    #    a cache_data plate freezing session_state (or any off-arg
    #    input) behind its ttl, serving stale HTML while the toggle
    #    moved — is swept on every run: session_state reachable from
    #    any cached body (transitively through same-module calls) is
    #    tier 1 and fails the suite; frozen wall-clock stamps are
    #    tier 2, reported only. The safe pattern is the exemplar's:
    #    off-arg inputs ride the cache key as parameters.
    #    ──────────────────────────────────────────────────────────
    try:
        import diag_cachesweep as _cs

        # ── THE FLEET SWEEP (Sep 13): the sweep walks the console's
        #    WHOLE import closure (AST-only discovery, no imports), not
        #    just app.py — a cached plate freezing a read in another
        #    module is the same disease. Its first pass earned its keep:
        #    _scalp_headline reached background_scanner._now_mst
        #    through _hours_to_scalp (now severed: the clock is a
        #    required kwarg), and seven benign reads are classified in
        #    FORGIVEN_TIER2 with printed reasons.
        _t1, _t2, _cfn, _cinfo = _cs.analyze()
        _cs_detail, _cs_ok = _cs.selftest()
        check("cache-sweep selftest: the two-module fleet catches the "
              "CROSS-MODULE freeze (from-import AND alias-attribute "
              "edges), exempts fragments and clean arg-only bodies",
              _cs_ok, _cs_detail)
        check("cache-sweep fleet discovery: the console's closure is "
              "found (app.py plus every local module it pulls in) — "
              "the sweep can never silently narrow back to one file",
              "app" in _cinfo["modules"]
              and len(_cinfo["modules"]) >= 5
              and _cinfo["fns_total"] > 100,
              f"modules={_cinfo['modules'][:8]}... "
              f"fns={_cinfo['fns_total']}")
        check("PRODUCTION FLEET CLEAN: no cached plate in ANY module "
              "freezes session_state, transitively across module "
              "boundaries", not _t1,
              f"{len(_t1)} finding(s): "
              + "; ".join(f"{q} <- {w} L{ln}"
                          for q, w, ln in _t1[:6]))
        # ── TIER 3 (Sep 13, later): cached→cached call edges — the
        #    NESTED STALENESS CHAIN class. Composition is legal, so the
        #    finding is report-only; the pin demands the chains stay
        #    VISIBLE (the ttl ladder readable) and that the fleet's
        #    known real composition is exactly what the sweep sees. ──
        _cs_nested = _cinfo.get("nested")
        check("cache-sweep TIER 3: nested-staleness chains are "
              "collected and the selftest asserts the hermetic "
              "cached->cached edge",
              isinstance(_cs_nested, list) and _cs_ok,
              f"{len(_cs_nested or [])} production chain(s): "
              + "; ".join(f"{a} -> {b}"
                          for a, b in (_cs_nested or [])[:6]))
        check("TIER 3 REAL CHAINS: the fleet's shipped composition is "
              "visible — account_value sits on wallet_snapshot and "
              "closed_trades on _bridge (a chain silently appearing "
              "or vanishing must fail here)",
              ("app.account_value", "app.wallet_snapshot") in _cs_nested
              and ("app.closed_trades", "app._bridge") in _cs_nested,
              f"chains seen: {sorted(_cs_nested)}")
        _tv = _cfn.get("app._tidebar_verdict")   # fleet qname: module-qualified
        check("the safe pattern holds: _tidebar_verdict carries the "
              "sims toggle in its signature (cache key, not a frozen "
              "read)",
              _tv is not None
              and any(a.arg == "sims_hidden" for a in _tv.args.args))
        # ── THE FRESH-STAMP FIX (Sep 13): the three tier-2 sites —
        #    _scalp_headline (x2) and _today_facts — used to bake the
        #    wall clock into cached HTML: frozen 'as of' stamps, frozen
        #    card ages, a frozen payout-recency check. Now the time is
        #    DATA: a bucketed epoch rides each cache key, the payout
        #    check takes now_ts as a required kwarg, and the visible
        #    'as of' stamps render OUTSIDE the cached bodies.
        _asrc = Path(BASE / "app.py").read_text(encoding="utf-8")
        _atree = ast.parse(_asrc)
        _afns = {n.name: n for n in ast.walk(_atree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        _sh, _tf, _rb = (_afns.get("_scalp_headline"),
                         _afns.get("_today_facts"),
                         _afns.get("_recent_big_payout"))
        check("fresh-stamp: _scalp_headline and _today_facts take the "
              "bucketed now_ts in their signatures (time is cache-key "
              "data, not a frozen read)",
              _sh is not None and _tf is not None
              and any(a.arg == "now_ts" for a in _sh.args.args)
              and any(a.arg == "now_ts" for a in _tf.args.args))
        check("fresh-stamp: _recent_big_payout's clock is caller-fed "
              "(required kwarg now_ts; zero wall-clock reads in its "
              "body — the transitive tier-2 path is severed)",
              _rb is not None
              and any(a.arg == "now_ts" for a in _rb.args.kwonlyargs)
              and not _cs._time_uses(_rb))
        check("fresh-stamp: the ttl constants are the single clock "
              "(decorators and call-site buckets share one number per "
              "plate) and the 'as of' stamps render at the call sites",
              ("HEADLINE_STALE_TTL_S" in _asrc
               and "FACTS_STALE_TTL_S" in _asrc
               and _asrc.count("now_ts=int(") == 2
               and _asrc.count("as of ") >= 5))
        # ── THE PLATE CLOCKS (Sep 13, later): the remaining MASTER
        #    plates carry the same visible 'as of' — the tide verdict's
        #    stamp is captured BEFORE its cached call and rendered AFTER
        #    (outside the 5-min body, never frozen); the discipline
        #    plate and the suppression strip stamp their live re-reads. ──
        check("plate clocks: the tide verdict's stamp is captured before "
              "its cached call and rendered after it (outside the "
              "cached body — a 5-min ttl cannot freeze the clock)",
              "_tv_n = _now_mst()" in _asrc
              and _asrc.index("_tv_n = _now_mst()")
              < _asrc.index("_tidebar_verdict(\n")
              and "as of {_tv_n.strftime" in _asrc)
        check("plate clocks: the discipline plate and the suppression "
              "strip stamp their live re-reads (every MASTER plate "
              "carries a visible clock)",
              "the discipline " in _asrc
              and "_now_mst().strftime" in
              _asrc[_asrc.index("def _render_suppression_strip"):
                    _asrc.index("def _no_pick_advice")])
        check("TIER 2 FLEET CLEAN: no cached body in ANY module "
              "reaches the wall clock (the frozen-stamp class is "
              "closed fleet-wide; classified reads live in "
              "FORGIVEN_TIER2 with reasons, printed in the report)",
              not _t2,
              f"{len(_t2)} finding(s): "
              + "; ".join(f"{q} <- {w} L{ln}" for q, w, ln in _t2[:6]))
        check("the forgiven registry is honest: seven classified "
              "reads, every one naming its (callee, read) rule",
              len(_cinfo["forgiven"]) == 7
              and all(kind in ("expiry math", "ts field", "dead field")
                      and why for _qn, _r, _w, _ln, (kind, why)
                      in _cinfo["forgiven"]),
              f"{len(_cinfo['forgiven'])} forgiven: "
              + "; ".join(sorted({_r for _qn, _r, _w, _ln, _ru
                                  in _cinfo["forgiven"]})[:6]))
        # ── THE LIVE CACHE STRIP (Sep 13): staleness observable on the
        #    page, not only audited offline — per-plate HIT vs COMPUTE
        #    with the last render age, in a MASTER diagnostics expander.
        #    call→render brackets ride uncached scope; _plate_touch rides
        #    the cached bodies; a compute between call and render wins
        #    over the pending HIT.
        _plc = app_mod._PLATE_CACHE_LOG
        # the probe's proven sequence, one kind per snapshot:
        # run 1 — a clean call→render is a HIT
        app_mod._plate_call("todays-facts", ttl=120)
        app_mod._plate_render("todays-facts")
        _strip_1 = app_mod._plate_strip_lines()
        # run 2 — a compute inside the call→render window wins over the HIT
        app_mod._plate_call("tidebar-verdict", ttl=300)
        app_mod._plate_touch("tidebar-verdict", ttl=300)
        app_mod._plate_render("tidebar-verdict")
        _strip_2 = app_mod._plate_strip_lines()
        # run 3 — a later clean call→render flips back to HIT
        app_mod._plate_call("tidebar-verdict", ttl=300)
        app_mod._plate_render("tidebar-verdict")
        _strip_3 = app_mod._plate_strip_lines()
        app_mod._plate_call("flip-grade")
        app_mod._plate_render("flip-grade")
        _strip_4 = app_mod._plate_strip_lines()
        check("cache strip: HIT after a clean call→render; a compute "
              "inside the window wins over the pending HIT; the next "
              "clean call→render flips back to HIT",
              ("last was HIT" in _strip_1[1]
               and "last was compute" in _strip_2[3]
               and "last was HIT" in _strip_3[3]),
              f"hit={_strip_1[1]} || compute={_strip_2[3]} || "
              f"re-hit={_strip_3[3]}")
        check("cache strip: uncached plates read 'no cache (live)' "
              "and never claim a ttl",
              "no cache (live)" in _strip_2[4], _strip_2[4])
        check("cache strip: memory-only observability (a dict, no "
              "file writes in the touch/call/render helpers) — the "
              "strip can never dirty production",
              isinstance(_plc, dict)
              and "Path" not in __import__("inspect").getsource(
                  app_mod._plate_touch)
              and "write_text" not in __import__("inspect").getsource(
                  app_mod._plate_render))
        # ── THE HIT-RATE HEALTH METRIC (Sep 13, later): requests ride
        #    the uncached call site, computes the cached body — a plate
        #    recomputing more often than its ttl implies is 🔥 HOT.
        #    The module ledger carries live counts from the boots above,
        #    so the reconciled arithmetic runs on a SNAPSHOT-RESTORED
        #    fresh ledger; production state goes back untouched. ──
        _plc_saved = dict(app_mod._PLATE_CACHE_LOG)
        _pttls_saved = dict(app_mod._PLATE_TTLS)
        try:
            app_mod._PLATE_CACHE_LOG.clear()
            app_mod._PLATE_TTLS.clear()
            for _ in range(6):
                app_mod._plate_call("tidebar-verdict", ttl=300)
                if _ < 3:
                    app_mod._plate_touch("tidebar-verdict", ttl=300)
                app_mod._plate_render("tidebar-verdict")
            _strip_5 = app_mod._plate_strip_lines()
            check("cache strip hit-rate: 3 computes over 6 requests "
                  "reads exactly 50% hits and stays QUIET at the boundary",
                  "· 50% hits" in _strip_5[3]
                  and "HOT" not in _strip_5[3], _strip_5[3])
            app_mod._plate_call("tidebar-verdict", ttl=300)
            app_mod._plate_touch("tidebar-verdict", ttl=300)   # 4 / 7
            app_mod._plate_render("tidebar-verdict")
            _strip_6 = app_mod._plate_strip_lines()
            check("cache strip hit-rate: 4 computes over 7 requests "
                  "reads 43% and flags 🔥 HOT — recomputing more often "
                  "than the ttl implies (the health verdict she sees)",
                  "· 43% hits" in _strip_6[3]
                  and "🔥 HOT" in _strip_6[3], _strip_6[3])
            check("cache strip hit-rate: below the 5-request sample "
                  "floor no rate is claimed (one or two requests are "
                  "noise, not a verdict) and uncached plates never "
                  "claim one (live is their contract)",
                  "% hits" not in _strip_6[4]
                  and "% hits" not in _strip_6[1],
                  f"facts: {_strip_6[1]} || flip-grade: {_strip_6[4]}")
        finally:
            app_mod._PLATE_CACHE_LOG.clear()
            app_mod._PLATE_CACHE_LOG.update(_plc_saved)
            app_mod._PLATE_TTLS.clear()
            app_mod._PLATE_TTLS.update(_pttls_saved)
        _msrc = Path(BASE / "app.py").read_text(encoding="utf-8")
        _mfn = {n.name: n for n in ast.walk(ast.parse(_msrc))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        _pcall_src = ast.get_source_segment(_msrc, _mfn["_plate_call"])
        _ptouch_src = ast.get_source_segment(_msrc, _mfn["_plate_touch"])
        _pstrip_src = ast.get_source_segment(
            _msrc, _mfn["_plate_strip_lines"])
        check("cache strip hit-rate: the request counter rides the "
              "UNCACHED call (every rerun), the compute counter the "
              "cached body, and the HOT gate demands ttl + 5-request "
              "floor (the metric can never flag a live plate or a "
              "thin sample)",
              "requests\"] = rec.get(\"requests\", 0) + 1" in _pcall_src
              and "requests\"] += 1" not in _ptouch_src
              and "requests >= 5" in _pstrip_src
              and "rate < 50.0" in _pstrip_src)
        _msrc = Path(BASE / "app.py").read_text(encoding="utf-8")
        _mfn = {n.name: n for n in ast.walk(ast.parse(_msrc))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        check("cache strip: production wiring — touches inside the "
              "three cached bodies, call→render brackets around the "
              "two uncached renders, the expander on MASTER",
              ("_plate_touch(" in ast.get_source_segment(_msrc,
                 _mfn["_scalp_headline"])
               and "_plate_touch(" in ast.get_source_segment(_msrc,
                 _mfn["_today_facts"])
               and "_plate_touch(" in ast.get_source_segment(_msrc,
                 _mfn["_past_winners_core"])
               and "_plate_call(" in ast.get_source_segment(_msrc,
                 _mfn["render_master"])
               and "_render_master_diagnostics()" in _msrc))
    except Exception as exc:
        check("the cache-staleness gate pins ran", False, exc)
    # ── THE SNAP-BACK GATE (Sep 12): every stateful Streamlit widget
    #    in app.py carries a stable key — the class that killed the
    #    ISO-week picker (no key: her week choice died on every rerun;
    #    counted labels: it died again when the edges shifted). The
    #    sweep's own verdict rides every suite run.
    #    ──────────────────────────────────────────────────────────
    try:
        import diag_widgetsweep as _ws

        _ws_detail, _ws_ok = _ws.selftest()
        _ws_t1, _ws_t2, _ws_n = _ws.analyze()
        check("snap-back sweep selftest: the analyzer flags the no-key "
              "and stored-display classes, passes the blessed "
              "literal/prefix-key shapes and world-derived prefills, "
              "catches dynamic value=",
              _ws_ok, _ws_detail)
        check(f"PRODUCTION CLEAN: all {_ws_n} stateful widgets carry a "
              "stable key — the snap-back class is closed",
              not _ws_t1,
              f"{len(_ws_t1)} finding(s): "
              + "; ".join(f"line {ln} st.{w} ({why})"
                          for ln, w, why in _ws_t1[:6]))
        # ── THE FLEET SWEEP (Sep 13, the st-shadow convention): every
        #    module binding `import streamlit as st` is swept — app.py
        #    is no longer the only gated surface. secrets_utils imports
        #    streamlit too; a future console fragment or package module
        #    lands in the gate automatically. ──
        _ws_fleet = _ws.analyze_all()
        _ws_by_name = {p.name: (p, t1, t2, n)
                       for p, t1, t2, n in _ws_fleet}
        check("FLEET DISCOVERY: the sweep finds app.py and every other "
              "streamlit-importing module — it can never silently "
              "narrow back to one file",
              "app.py" in _ws_by_name and len(_ws_fleet) >= 2
              and "secrets_utils.py" in _ws_by_name,
              f"discovered: {sorted(_ws_by_name)}")
        check("GATE MEMBERSHIP: production modules are gated; "
              "diagnostics, scratch (_-prefixed) and the test harness "
              "are swept but report-only",
              (_ws._is_app_candidate(Path("app.py"))
               and not _ws._is_app_candidate(Path("diag_x.py"))
               and not _ws._is_app_candidate(Path("test_x.py"))
               and not _ws._is_app_candidate(Path("_scratch.py"))))
        _ws_gate = [(p, t1) for p, t1, _t2, _n in _ws_fleet
                    if _ws._is_app_candidate(p)]
        _ws_gate_finds = [(p.name, f) for p, t1 in _ws_gate for f in t1]
        check(f"FLEET CLEAN ({len(_ws_fleet)} streamlit module(s), "
              f"{sum(n for *_x, n in _ws_fleet)} widgets): no stateful "
              "widget in any gated module snaps back or stores a "
              "derived display string",
              not _ws_gate_finds,
              f"{len(_ws_gate_finds)} finding(s): "
              + "; ".join(f"{nm} line {f[0]} st.{f[1]}"
                          for nm, f in _ws_gate_finds[:6]))
        # ── FLEET BITES (the st-shadow drill, widgetsweep edition):
        #    discovery + gating + detection proven on a fresh tree ──
        import tempfile as _tempfile_ws
        _ws_root = Path(_tempfile_ws.mkdtemp())
        (_ws_root / "poisoned_app.py").write_text(
            "import streamlit as st\n"
            "def body():\n"
            "    m = st.radio(\"entry\", [\"\\u25c9 SAME\", "
            "\"\\u25ef NEW\"], index=0, key=\"m\")\n"
            "    w = st.checkbox(\"watch\")\n", encoding="utf-8")
        (_ws_root / "_poison.py").write_text(
            "import streamlit as st\n"
            "def body():\n"
            "    w = st.checkbox(\"watch\")\n", encoding="utf-8")
        (_ws_root / "no_st.py").write_text(
            "def body():\n    return 1\n", encoding="utf-8")
        _ws_bit = _ws.analyze_all(root=_ws_root)
        _ws_bit_names = sorted(p.name for p, *_ in _ws_bit)
        _ws_bit_gate = [f for p, t1, _t2, _n in _ws_bit
                        if _ws._is_app_candidate(p) for f in t1]
        check("FLEET BITES: a poisoned production module is "
              "discovered, gated, and flagged (no-key + stored-display); "
              "the same poison in a _scratch file is report-only; a "
              "streamlit-free module is never discovered",
              _ws_bit_names == ["_poison.py", "poisoned_app.py"]
              and len(_ws_bit_gate) >= 2
              and any("semantic" in f[2] for f in _ws_bit_gate)
              and any("no key" in f[2] for f in _ws_bit_gate),
              f"discovered={_ws_bit_names} gate_finds={_ws_bit_gate}")
        _wsrc = Path(BASE / "app.py").read_text(encoding="utf-8")
        check("the ISO-week picker keeps its blessed shape (stable "
              "tb_week_pick key, semantic stored values)",
              'key="tb_week_pick"' in _wsrc
              and "format_func=_fmt" in _wsrc,
              "picker shape drifted")
    except Exception as exc:
        check("the snap-back gate pins ran", False, exc)
    # ── THE SEMANTIC-OPTIONS PINS (Sep 13): the entry form's radios
    #    stored their glyph-decorated labels (branch logic read
    #    startswith("◉")) — the last stored-display-string class. The
    #    semantic-options + format_func idiom now owns them, the
    #    interval selectbox stores SECONDS (not '1 min' strings), and
    #    the widgetsweep's stored-display rule is tier-1 so the class
    #    is closed everywhere. ──
    try:
        check("entry form: the mode/how options are plain words",
              all(ord(s[0]) < 128 for s in
                  (app_mod._EC_MODES + app_mod._EC_HOWS)))
        check("entry form: the display labels carry the glyph "
              "decoration format_func-side",
              app_mod._ec_mode_lbl("same")
              .startswith("◉ SAME TRADE")
              and app_mod._ec_how_lbl("entry+size")
              == "◯ entry $ + size $")
        _ec_src = Path(BASE / "app.py").read_text(encoding="utf-8")
        check("entry form: the radios are RETIRED (Sep 21, user rule — "
              "the one entry control is gone) and the old glyph-prefix "
              "branch ban still holds",
              "format_func=_ec_mode_lbl" not in _ec_src
              and "format_func=_ec_how_lbl" not in _ec_src
              and 'startswith("\\u25c9")' not in _ec_src
              and 'startswith("\\u25ef")' not in _ec_src)
        check("the light's interval stores SECONDS (semantic), the "
              "'every X min' display is derived, and the old "
              "display-string-keyed widget is gone",
              'key="watch_interval_s"' in _ec_src
              and 'key="watch_interval")' not in _ec_src
              and "_render_light_panel(f\"{interval // 60} min\")"
              in _ec_src)
    except Exception as exc:
        check("the semantic-options pins ran", False, exc)
    # ── THE SILENT-SWALLOW GATE (Sep 13): no bare `except: pass` in
    #    app.py — a swallow must carry a name to log, a narrow tuple,
    #    or a documented reason. The sweep that closed the class found
    #    a dead keyring client trading unauthenticated in silence and
    #    a corrupt watch file zeroing the scoreboard with no trace.
    try:
        import diag_silentpass as _sp
        _sp_ok, _sp_d = _sp.selftest()
        check("silent-swallow selftest: bare/broad silent passes "
              "flagged, logged and narrow+commented shapes blessed",
              _sp_ok, _sp_d)
        _sp_fleet = _sp.analyze_all()
        _sp_all = [(p.name, f, n) for p, f, n in _sp_fleet]
        _sp_rot = [(nm, f) for nm, f, n in _sp_all if f]
        check(f"PRODUCTION CLEAN: all {sum(n for _, f, n in _sp_all)} "
              "exception handlers across the fleet (app.py, "
              "tidebar_weekly.py, background_scanner.py, bot_runner.py) "
              "— none swallows silently without a trace",
              not _sp_rot,
              f"{len(_sp_rot)} rotted module(s): "
              + "; ".join(f"{nm} line {ln} ({why})"
                          for nm, f in _sp_rot[:4]
                          for ln, why in f[:3]))
        check("the fleet sweep found every production module it gates "
              "(a silently-narrowed fleet would protect nothing)",
              {nm for nm, _, _ in _sp_all} >= {"app.py", "tidebar_weekly.py",
                                               "background_scanner.py",
                                               "bot_runner.py"},
              f"discovered only {_sp_all and [nm for nm, _, _ in _sp_all]}")
        check("the keyring fallback stays loud (logged broad handler "
            "so a dead credential source can never trade in silence)",
            "keyring Kraken client unavailable" in _wsrc,
            "keyring fallback drifted silent")
    except Exception as exc:
        check("the silent-swallow gate pins ran", False, exc)
    # ── THE APP-EDIT METAGATE (Sep 13): any future edit to app.py runs
    #    all four analyzers in ONE battery entry — the four structural
    #    classes that silently killed her widgets, her cache freshness,
    #    and her prefs (st-shadow, cache-sweep, snap-back, and the
    #    prefs mirror's own structural gate). Each gate rides its
    #    analyzer's selftest (the detector is trustworthy) then its
    #    production sweep; a rot ANYWHERE fails this single entry and
    #    names the gate and finding. ──
    try:
        import diag_stshadow as _mg_ss
        import diag_cachesweep as _mg_cs
        import diag_widgetsweep as _mg_ws
        import diag_prefsmirror as _mg_pm

        _mg_gates, _mg_errs = {}, []
        for _mg_name, _mg_mod in (("st-shadow", _mg_ss),
                                  ("cache-sweep", _mg_cs),
                                  ("snap-back", _mg_ws),
                                  ("prefs-mirror", _mg_pm)):
            try:
                _mg_d, _mg_ok = _mg_mod.selftest()
                if not _mg_ok:
                    _mg_errs.append(f"{_mg_name} selftest: {_mg_d}")
            except Exception as _mg_exc:
                _mg_errs.append(f"{_mg_name} selftest raised: "
                                f"{_mg_exc!r}")
        try:
            _mg_fleet = _mg_ss.analyze_all()
            _mg_gates["st-shadow"] = [
                f"{p.name}:{f[2]} st.{f[1]}"
                for p, _imp, _mod, finds, _tree in _mg_fleet
                if _mg_ss._is_app_candidate(p) for f in finds]
        except Exception as _mg_exc:
            _mg_errs.append(f"st-shadow sweep raised: {_mg_exc!r}")
        try:
            _mg_t1, _mg_t2, _mg_fn, _mg_info = _mg_cs.analyze()
            _mg_gates["cache-sweep"] = [
                f"{q} <- {w} L{ln}" for q, w, ln in _mg_t1]
        except Exception as _mg_exc:
            _mg_errs.append(f"cache-sweep sweep raised: {_mg_exc!r}")
        try:
            _mg_wfleet = _mg_ws.analyze_all()
            _mg_gates["snap-back"] = [
                f"{p.name}: st.{f[1]} L{f[0]} ({f[2]})"
                for p, t1, _t2, _n in _mg_wfleet
                if _mg_ws._is_app_candidate(p) for f in t1]
        except Exception as _mg_exc:
            _mg_errs.append(f"snap-back sweep raised: {_mg_exc!r}")
        try:
            _mg_pf, _mg_pi = _mg_pm.analyze()
            _mg_gates["prefs-mirror"] = [
                f"{tag}:{nm} — {why}" for _k, tag, nm, why in _mg_pf]
        except Exception as _mg_exc:
            _mg_errs.append(f"prefs-mirror sweep raised: {_mg_exc!r}")

        _mg_bad = {g: f for g, f in _mg_gates.items() if f}
        check("APP-EDIT METAGATE: all four app.py analyzers (st-shadow, "
              "cache-sweep, snap-back, prefs-mirror) pass their "
              "selftests and every production sweep is CLEAN in one "
              "entry",
              not _mg_bad and not _mg_errs,
              "; ".join(_mg_errs) or "; ".join(
                  f"{g}: {f[:3]}" for g, f in _mg_bad.items()))
    except Exception as exc:
        check("the app-edit metagate ran", False, exc)
    # ── THE SIZING-GATE AUDIT (v4, Sep 13): the read-before-assign
    #    audit upgraded to the st-shadow scope machinery (import
    #    aliases bind, walrus targets store) with a CALL-AWARE pass
    #    over the sizing-sensitive gates — worth tilt, the cap-dollar
    #    ceiling, the danger zone, the 3% guard — the pass that would
    #    have caught send_alert's unbound-_bal disease at the call
    #    argument, not after a contest mis-sized. Its selftest plants
    #    the bug shape and demands a flag.
    try:
        import diag_sizingaudit as _sa
        # ── the hermetic canon, pinned BY NAME (Sep 13): every fixture
        #    is a permanent case with its own predicate — a regression
        #    names its case, a new fixture must appear here, and no
        #    case can hide inside one aggregate boolean. ──
        _sa_canon = {nm for nm, *_r in _sa.SELFTEST_FIXTURES}
        check("sizing-audit CANON EXPOSED: every hermetic fixture is "
              "a named permanent case (ule/param/import/walrus/module)",
              {"fx_ule", "fx_param", "fx_import", "fx_walrus",
               "fx_module"} <= _sa_canon,
              f"canon = {sorted(_sa_canon)}")
        _sa_cases = _sa.selftest_cases()
        _sa_byname = {nm: (ok, det) for nm, _lbl, _f, ok, det in _sa_cases}
        check("sizing-audit canon case fx_ule: the planted call-arg "
              "UnboundLocalError flags at the call argument",
              _sa_byname.get("fx_ule", (False, ""))[0],
              _sa_byname.get("fx_ule", (False, "missing"))[1])
        for _sa_q in ("fx_param", "fx_import", "fx_walrus", "fx_module"):
            check(f"sizing-audit canon case {_sa_q}: the legit shape "
                  "stays quiet",
                  _sa_byname.get(_sa_q, (False, ""))[0],
                  _sa_byname.get(_sa_q, (False, "missing"))[1])
        _sa_d, _sa_ok = _sa.selftest()
        check("sizing-audit selftest aggregate: every canon case passes",
              _sa_ok and len(_sa_cases) == len(_sa.SELFTEST_FIXTURES),
              _sa_d)
        _sa_finds = _sa.audit_gates()
        check("SIZING GATES CLEAN: worth tilt, cap-dollar ceiling, "
              "danger zone, the 3% guard helpers AND the bot's "
              "order-sizing path (engine _execute_buy/carry cap/min "
              "volume, the runner's tick loop, the bridge's "
              "position_size_usd seam) — no read of any local "
              "precedes its store, call arguments included",
              not _sa_finds,
              f"{len(_sa_finds)} finding(s): "
              + "; ".join(f"{fn}:{ln} {fc}.{nm}"
                          for fn, fc, ln, nm, _w in _sa_finds[:6]))
        # the extension must SEE the bot fleet: every bot-path gate
        # name must still exist in its production file — a rename
        # would silently drop the gate out of the audit's coverage
        import ast as _ast_sa
        _sa_missing = []
        for _sa_fn, _sa_names, _sa_root in (
                (_sa.BOT_GATE_FILE, _sa.BOT_GATES, _sa.BOT_ROOT),
                (_sa.BOT_GATE_FILE_2, _sa.BOT_GATE_2, _sa.BOT_ROOT),
                (_sa.BRIDGE_GATE_FILE, _sa.BRIDGE_GATE, _sa.ROOT)):
            _sa_tree = _ast_sa.parse((_sa_root / _sa_fn)
                                     .read_text(encoding="utf-8"))
            _sa_have = {n.name for n in _ast_sa.walk(_sa_tree)
                        if isinstance(n, _ast_sa.FunctionDef)
                        and n.name in _sa_names}
            if set(_sa_names) - _sa_have:
                _sa_missing.append((_sa_fn, sorted(set(_sa_names)
                                                   - _sa_have)))
        check("the sizing audit's bot-fleet roster is live: engine, "
              "runner and bridge gates all found in production source "
              "(a rename can never silently drop a sizing gate)",
              not _sa_missing, f"gates lost from coverage: {_sa_missing}")
        # and the call-aware pass must BITE on the real engine's shape:
        # the planted ULE in a sandboxed copy flags, the real one stays quiet
        import tempfile as _sa_tf
        _sa_sbx = Path(_sa_tf.mkdtemp())
        try:
            (_sa_sbx / "bot_engine.py").write_text(
                (_sa.BOT_ROOT / "bot_engine.py")
                .read_text(encoding="utf-8")
                + "\n\ndef _execute_buy(self, signal, pair=None):\n"
                  "    worst = impact(signal.pair, spend)\n"
                  "    spend = self._balance()\n"
                  "    return worst, spend\n",
                encoding="utf-8")
            _sa_planted = _sa.audit_gates(
                files=[("bot_engine.py", _sa.BOT_GATES, _sa_sbx)])
            check("the bot-engine extension BITES: a planted call-arg "
                  "UnboundLocalError in _execute_buy is named at the "
                  "call argument (mis-sized real order, not a missed popup)",
                  any("UnboundLocalError" in why and fn == "_execute_buy"
                      for _saf, fn, _sal, _san, why in _sa_planted),
                  f"{len(_sa_planted)} finding(s)")
        finally:
            import shutil as _sa_sh
            _sa_sh.rmtree(_sa_sbx, ignore_errors=True)
    except Exception as exc:
        check("the sizing-gate audit pins ran", False, exc)
    # ── THE DIM SYNC (Sep 12): the chart's regime stamps whose
    #    decision doesn't match the ACTIVE verdict filter dim to 25%
    #    (visible, clickable, out of the eye's way) — ONE shared
    #    classification keyed by each decision's (state, action,
    #    week), so chart and trail can never disagree. Hermetic: the
    #    ledger + event loaders are sandboxed, the chart called bare
    #    with explicit verdict overrides. ──
    try:
        import tempfile as _tempfile_ds
        _ds_sbx = Path(_tempfile_ds.mkdtemp())
        _ds_saved = {k: getattr(_tbw_mod, k, None) for k in
                     ("HISTORY_FILE", "FLIP_EVENTS_FILE", "FLIP_FILE")}
        _tbw_mod.HISTORY_FILE = _ds_sbx / "h.jsonl"
        _tbw_mod.FLIP_EVENTS_FILE = _ds_sbx / "evs.jsonl"
        _tbw_mod.FLIP_FILE = _ds_sbx / "flips.json"
        # the prose backfill rides a SANDBOX txt too (CodeRabbit-adjacent
        # lesson, Sep 28): the chart backfills pre-ledger weeks from
        # _tidebarweekly.txt at its default path, and that file is LIVE
        # runtime state - the restarted scanner's weekly writer
        # regenerated it mid-day (a new W40 entry, 527 lines) and the
        # extra prose week's hover titles broke the pinned counts (10
        # <title> vs 8). Bare calls now pass txt_path=_ds_sbx prose so
        # the fixture owns the whole picture, live file untouched.
        _ds_prose = _ds_sbx / "prose.txt"
        _ds_prose.write_text("", encoding="utf-8")
        # two graded sims (PAYING flip / COSTING flip-back) + one
        # pending sim: three identities, three verdicts
        _ds_evs = [
            {"sim": True, "state": "OUT", "action": "flip",
             "week": "2026-W37", "ts": "t",
             "sim_aftermath": {
                 "status": "graded", "weeks": 2,
                 "realized": {"bar": "quantile", "fires": 10,
                              "usd_per_fire": 4.8},
                 "counterfactual": {"bar": "fit", "fires": 10,
                                    "usd_per_fire": 3.6},
                 "verdict": "the decision PAID"},
             "evidence": [{"week": "2026-W37", "fit_edge": -0.5}]},
            {"sim": True, "state": "FLAT", "action": "flip_back",
             "week": "2026-W38", "ts": "t",
             "sim_aftermath": {
                 "status": "graded", "weeks": 2,
                 "realized": {"bar": "fit", "fires": 10,
                              "usd_per_fire": 3.6},
                 "counterfactual": {"bar": "quantile", "fires": 10,
                                    "usd_per_fire": 4.8},
                 "verdict": "the decision COST — the benched side "
                            "would have paid better"},
             "evidence": [{"week": "2026-W38", "fit_edge": 0.5}]},
            {"sim": True, "state": "RISING", "action": "flip",
             "week": "2026-W39", "ts": "t", "evidence": []}]
        for e in _ds_evs:
            _tbw_mod.FLIP_EVENTS_FILE.open("a", encoding="utf-8").write(
                json.dumps(e) + "\n")
        # the ledger: three two-sided weeks (OUT + FLAT graded; RISING
        # never grades → its flip's evidence week W39 anchors on the
        # zero line). Each event renders in BOTH stamp layers
        # (floating + on-point), so 3 events = 6 stamp instances.
        for w, di, do in (("2026-W37", 3.0, 4.0), ("2026-W38", 4.0, 3.0),
                          ("2026-W39", 3.5, 3.5)):
            _tbw_mod.HISTORY_FILE.open("a", encoding="utf-8").write(
                json.dumps({
                    "week": w, "generated_ts": w,
                    "states": {
                        "OUT": {
                            "in_force": {"fires": 10, "payers": 3,
                                         "rate": 0.3, "usd": 10 * di,
                                         "usd_per_fire": di},
                            "other": {"fires": 10, "payers": 3,
                                      "rate": 0.3, "usd": 10 * do,
                                      "usd_per_fire": do},
                            "other_src": "quantile"},
                        "FLAT": {
                            "in_force": {"fires": 10, "payers": 3,
                                         "rate": 0.3, "usd": 10 * di,
                                         "usd_per_fire": di},
                            "other": {"fires": 10, "payers": 3,
                                      "rate": 0.3, "usd": 10 * do,
                                      "usd_per_fire": do},
                            "other_src": "quantile"}}}) + "\n")
        _c_none = app_mod._tidebar_ledger_chart(hide_sims=False,
                                                  txt_path=_ds_prose)
        check("dim sync: no filter dims nothing (the chart keeps its "
              "full regime history)",
              "opacity='0.25'" not in _c_none
              and "regime stamp(s) dimmed" not in _c_none)
        _c_pay = app_mod._tidebar_ledger_chart(hide_sims=False,
                                               verdict="PAYING",
                                               txt_path=_ds_prose)
        # the true rendering arithmetic, verified against the sandbox:
        # two events carry charted evidence (OUT flip / FLAT flip-back),
        # each renders in BOTH stamp layers = 4 stamps; the pending
        # event's empty evidence renders none (nothing to anchor a
        # glyph to — the trail still filters its row). Circles carry
        # their own titles, hence 8 <title> under PAYING.
        check("dim sync: PAYING dims the COSTING stamp in both layers "
              "(2 opacity attrs of 4 stamp instances), names the count "
              "honestly in the caption, keeps every href and hover",
              _c_pay.count("opacity='0.25'") == 2
              and "1 regime stamp(s) dimmed" in _c_pay
              and _c_pay.count("href=") == 4
              and _c_pay.count("<title>") == 8,
              (_c_pay.count("opacity='0.25'"),
               _c_pay.count("href="),
               _c_pay.count("<title>")))
        _c_cost = app_mod._tidebar_ledger_chart(hide_sims=False,
                                                verdict="COSTING",
                                                txt_path=_ds_prose)
        check("dim sync: COSTING dims the complement (the PAYING "
              "stamp, both layers) — the same machinery, the other "
              "side",
              _c_cost.count("opacity='0.25'") == 2
              and "1 regime stamp(s) dimmed" in _c_cost)
        _c_pend = app_mod._tidebar_ledger_chart(hide_sims=False,
                                                txt_path=_ds_prose,
                                                verdict="pending")
        check("dim sync: pending dims BOTH graded stamps (4 "
              "instances) — the note names 2, and honestly: the "
              "pending decision renders no glyph at all, so nothing "
              "of it dims",
              _c_pend.count("opacity='0.25'") == 4
              and "2 regime stamp(s) dimmed" in _c_pend)
        # ── THE SPAN DIM (Sep 13): a state's benched spans dim when
        #    EVERY one of its decisions on the picture fails the
        #    filter — the whole chart reads through one filter. A
        #    second sandbox world with REAL events: OUT's full arc
        #    (flip W39 COSTING via its 4-week governed window, "
        #    flip-back W44 PAYING) and FLAT's open bench whose flip
        #    COSTS — so PAYING must dim FLAT's span while COSTING
        #    dims nothing (every state has a matching decision). ──
        def _span_mk(w, st, inf_fit, oth_fit, src):
            inf = {"fires": 10, "payers": 3, "rate": 0.3,
                   "usd": 10 * inf_fit, "usd_per_fire": inf_fit}
            oth = {"fires": 10, "payers": 3, "rate": 0.3,
                   "usd": 10 * oth_fit, "usd_per_fire": oth_fit}
            fit, quant = ((oth, inf) if src == "outcome-fitted"
                          else (inf, oth))
            return {"week": w, "generated_ts": w,
                    "states": {st: {"in_force": fit, "other": quant,
                                    "other_src": src}}}
        _span_H = []
        for w in ("2026-W36", "2026-W37", "2026-W38", "2026-W39"):
            _span_H.append(_span_mk(w, "OUT", 1.0, 2.5, "quantile"))
        for w in ("2026-W40", "2026-W41", "2026-W42"):
            _span_H.append(_span_mk(w, "OUT", 2.5, 1.0,
                                    "outcome-fitted"))
        for w in ("2026-W43", "2026-W44"):
            _span_H.append(_span_mk(w, "OUT", 1.0, 5.0,
                                    "outcome-fitted"))
        for w in ("2026-W45", "2026-W46", "2026-W47", "2026-W48"):
            _span_H.append(_span_mk(w, "OUT", 5.0, 1.0, "quantile"))
        for w in ("2026-W38", "2026-W39", "2026-W40"):
            next(x for x in _span_H if x["week"] == w)["states"]["FLAT"] \
                = dict(next(x for x in _span_H if x["week"] == w)
                       ["states"]["OUT"])
        for w in ("2026-W41", "2026-W42", "2026-W43", "2026-W44",
                  "2026-W45", "2026-W46", "2026-W47", "2026-W48"):
            next(x for x in _span_H if x["week"] == w)["states"]["FLAT"] \
                = {"in_force": {"fires": 10, "payers": 3, "rate": 0.3,
                                "usd": 10.0, "usd_per_fire": 1.0},
                   "other": {"fires": 10, "payers": 3, "rate": 0.3,
                             "usd": 25.0, "usd_per_fire": 2.5},
                   "other_src": "outcome-fitted"}
        _tbw_mod.HISTORY_FILE.write_text(
            "\n".join(json.dumps(r) for r in _span_H) + "\n",
            encoding="utf-8")
        _E = -1.5
        _span_evs = [
            {"state": "OUT", "action": "flip", "week": "2026-W39",
             "losing_weeks": 3, "roll_delta": -1.5,
             "evidence": [{"week": "2026-W37", "fit_edge": _E},
                          {"week": "2026-W38", "fit_edge": _E},
                          {"week": "2026-W39", "fit_edge": _E}]},
            {"state": "OUT", "action": "flip_back", "week": "2026-W44",
             "winning_weeks": 2, "roll_delta": 1.25, "pool_bar": 0.0,
             "evidence": [{"week": "2026-W43", "fit_edge": 4.0},
                          {"week": "2026-W44", "fit_edge": 4.0}]},
            {"state": "FLAT", "action": "flip", "week": "2026-W40",
             "losing_weeks": 3, "roll_delta": -1.5,
             "evidence": [{"week": "2026-W38", "fit_edge": _E},
                          {"week": "2026-W39", "fit_edge": _E},
                          {"week": "2026-W40", "fit_edge": _E}]}]
        _tbw_mod.FLIP_EVENTS_FILE.write_text(
            "\n".join(json.dumps(e) for e in _span_evs) + "\n",
            encoding="utf-8")
        _tbw_mod.FLIP_FILE.write_text(json.dumps(
            {"OUT": {"flipped": False}, "FLAT": {"flipped": True}}),
            encoding="utf-8")
        _span_pay = app_mod._tidebar_ledger_chart(hide_sims=False,
                                                  verdict="PAYING",
                                                  txt_path=_ds_prose)
        check("span dim: PAYING dims FLAT's benched span with its "
              "state (fill-opacity 0.02), keeps OUT's lit (its "
              "flip-back matches), names the span in the caption, and "
              "still dims OUT's mismatched flip stamps",
              _span_pay.count("fill-opacity='0.02'") == 1
              and _span_pay.count("fill-opacity='0.08'") == 1
              and "benched span(s) dimmed" in _span_pay
              and "regime stamp(s) dimmed" in _span_pay)
        _span_cost = app_mod._tidebar_ledger_chart(hide_sims=False,
                                                   verdict="COSTING",
                                                   txt_path=_ds_prose)
        check("span dim: COSTING keeps BOTH spans lit — every state "
              "has at least one matching decision (FLAT's flip, OUT's "
              "flip), so no span dims with the whole-chart rule",
              _span_cost.count("fill-opacity='0.02'") == 0
              and _span_cost.count("fill-opacity='0.08'") == 2)
        _span_none = app_mod._tidebar_ledger_chart(hide_sims=False,
                                                   txt_path=_ds_prose)
        check("span dim: no filter renders every span at full "
              "opacity and no dim note appears",
              _span_none.count("fill-opacity='0.02'") == 0
              and _span_none.count("fill-opacity='0.08'") == 2
              and "dimmed" not in _span_none)
        check("dim sync: the mirror helper reads the session pick and "
              "falls back to the prefs file (module-level, st safe)",
              callable(app_mod._verdict_filter_mirror)
              and app_mod._verdict_filter_mirror() in
              ("PAYING", "COSTING", "pending", "All verdicts"))
    except Exception as exc:
        check("the dim-sync pins ran", False, exc)
    # ── THE ONE-PASS HEALTH COMMAND (Sep 13): health_check.py is the
    #    battery's own read-only live gate — selftest first (the
    #    detector must be trustworthy: corrupt JSON, a named bad JSONL
    #    line, duplicate real flip events, missing, lazy-absent, and
    #    probe-dead all caught), then the PRODUCTION stack itself:
    #    scanner/bot/vol/console liveness and 14/14 ledger integrity
    #    in one exit-coded pass. Production findings here name the
    #    rot loudly — they never auto-repair. ──
    try:
        import health_check as _hc
        _hc_d, _hc_st = _hc.selftest()
        check("health-command selftest: corrupt/bad-line/dup/missing "
              "caught, lazy-absent healthy, probe-dead fails",
              _hc_st, _hc_d)
        _hc_json = json.loads(subprocess.run(
            [sys.executable, "-X", "utf8", str(_hc.ROOT / "health_check.py"),
             "--json"], capture_output=True, text=True, timeout=180).stdout)
        _hc_pro = [p for p in _hc_json["processes"] if not p[1]]
        _hc_led = [l for l in _hc_json["ledgers"] if not l[1]]
        check("HEALTH: the live stack answers in one pass — scanner, "
              "bot, 2 volatility engines, console :8501, every ledger "
              "intact (read-only, exit-coded)",
              not _hc_pro and not _hc_led,
              "; ".join(f"{p[0]}: {p[2]}" for p in _hc_pro + _hc_led))
    except Exception as exc:
        check("the health-command pins ran", False, exc)
    finally:
        for k, v in _ds_saved.items():
            if v is not None:
                setattr(_tbw_mod, k, v)
        import shutil as _shutil_ds
        _shutil_ds.rmtree(_ds_sbx, ignore_errors=True)
except Exception as exc:
    lines.append(f"APPTEST FAILED: {exc}")
    check("the app still boots clean (no exception)", False, exc)

# ═══ THE BURIAL HINDSIGHT PANEL (Sep 28) — behavioral through the REAL
# helpers, with a sandboxed OHLCV db + suppression journal. The same
# join the verdict study uses, proven end-to-end: a constructed candidate
# buried at a known entry must surface with its run; a journal-less
# sandbox must fail-closed (the panel has nothing to say); a dud never
# headlines the top list. The seam is app_mod.__file__: the join derives
# BOTH files from it, so re-pointing the module moves db + journal
# wholesale (no Path patching — app.py's Path import is function-local).
try:
    import sqlite3 as _sq3
    _bh_sbx = Path(tempfile.mkdtemp())
    _bh_db = _bh_sbx / "ohlcv_cache.db"
    _con = _sq3.connect(_bh_db)
    _con.execute("CREATE TABLE ohlcv (pair TEXT, ts INTEGER, open REAL, "
                 "high REAL, low REAL, close REAL)")
    _bh_now = datetime.now().astimezone()
    _bh_t0 = int(_bh_now.timestamp() * 1000)
    # CAGE: buried at close 1.00, runs to 1.08 within the window (a payer)
    # DEEP (deep-veto): buried at 2.00, runs to 2.04 (also a payer)
    # MOOD: buried at 1.00, never moves after the refusal (a dud — and
    # MEASURABLE, its post-refusal bars exist with high == entry)
    _rows = [
        ("CAGE/USD", _bh_t0 - 3_600_000, 1.00, 1.00, 1.00, 1.00),
        ("CAGE/USD", _bh_t0, 1.00, 1.02, 1.00, 1.01),
        ("CAGE/USD", _bh_t0 + 2 * 3_600_000, 1.01, 1.08, 1.01, 1.05),
        ("DEEP/USD", _bh_t0 - 3_600_000, 2.00, 2.00, 2.00, 2.00),
        ("DEEP/USD", _bh_t0 + 3_600_000, 2.00, 2.04, 2.00, 2.02),
        ("MOOD/USD", _bh_t0 - 3_600_000, 1.00, 1.00, 1.00, 1.00),
        ("MOOD/USD", _bh_t0, 1.00, 1.00, 1.00, 1.00),
        ("MOOD/USD", _bh_t0 + 1_800_000, 1.00, 1.00, 0.999, 1.00),
    ]
    _con.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?)", _rows)
    _con.commit()
    _con.close()
    _bh_sup = _bh_sbx / "popup_suppressions.jsonl"
    _rot = "12-16"
    _bh_sup.write_text("\n".join(json.dumps(r) for r in ({
        "ts": _bh_now.isoformat(), "pair": "CAGE/USD", "gate": "tide-out",
        "quick": 71.0, "rot": _rot}, {
        "ts": (_bh_now + timedelta(minutes=1)).isoformat(),
        "pair": "DEEP/USD", "gate": "deep-veto", "quick": 94.0,
        "rot": _rot}, {
        "ts": (_bh_now + timedelta(minutes=2)).isoformat(),
        "pair": "MOOD/USD", "gate": "tide-out", "quick": 55.0,
        "rot": _rot})), encoding="utf-8")
    # the REAL path seam re-pointed at the sandbox: app.py's join
    # derives both files from __file__, so the whole module moves
    # wholesale (no Path patching — its Path import is function-local,
    # a module-level patch would never reach it)
    real_file = app_mod.__file__
    _bh_sbx2: Path | None = None

    app_mod.__file__ = str(_bh_sbx / "app.py")
    try:
        app_mod._cached_burial_hindsight.clear()
        h = app_mod._burial_hindsight()
        check("the hindsight join prices the constructed burials (3 "
              "measurable)", h.get("n") == 3, h)
        check("the payer-rate is measured (2 of 3 reached +1.5%)",
              h.get("payers15") == 2 and abs(h.get("rate15", 0) - 2 / 3)
              < 0.01, h)
        check("the by-gate plate sorts best-first (payers desc, rate "
              "tiebreak): deep-veto leads at 1 payer / rate 1.0, "
              "tide-out rides at 1 payer / rate 0.5 (its other burial "
              "was the dud)",
              h["by_gate"][0]["gate"] == "deep-veto"
              and h["by_gate"][0]["pay"] == 1
              and h["by_gate"][0]["rate"] == 1.0
              and any(g["gate"] == "tide-out" and g["pay"] == 1
                      and abs(g["rate"] - 0.5) < 0.01
                      for g in h["by_gate"]), h.get("by_gate"))
        check("the top buried payer is CAGE at +8% (max high within 7h)",
              h["top"] and h["top"][0]["pair"] == "CAGE/USD"
              and abs(h["top"][0]["run"] - 8.0) < 0.01, h.get("top"))
        check("the dud (MOOD, never moved) is not in the top list",
              all(s["pair"] != "MOOD/USD" for s in h.get("top", [])),
              h.get("top"))
        # ── THE QUICK AXIS (Sep 30, the per-gate calibration study):
        # each gate's refusals bucketed by the quick score the gate
        # refused at — the study's separation, visible monthly without
        # rerunning the study. The fixture was BUILT for this: tide-out's
        # payer (CAGE) sits at quick 71, its dud (MOOD) at 55, and
        # deep-veto's payer (DEEP) at 94.
        _tg = next(g for g in h["by_gate"] if g["gate"] == "tide-out")
        _dl = next(g for g in h["by_gate"] if g["gate"] == "deep-veto")
        _t7 = next((b for b in _tg.get("buckets", [])
                    if b.get("bucket") == "70s"), None)
        _t6 = next((b for b in _tg.get("buckets", [])
                    if b.get("bucket") == "<60"), None)
        _d9 = next((b for b in _dl.get("buckets", [])
                    if b.get("bucket") == "90+"), None)
        check("the quick axis buckets each gate's refusals (tide-out: "
              "the quick-71 payer lands in '70s', the quick-55 dud in "
              "'<60'; deep-veto: the quick-94 payer in '90+')",
              _t7 and _t7["n"] == 1 and _t7["pay"] == 1
              and _t6 and _t6["n"] == 1 and _t6["pay"] == 0
              and _d9 and _d9["n"] == 1 and _d9["pay"] == 1,
              {"tide-out": _tg.get("buckets"),
               "deep-veto": _dl.get("buckets")})
        check("each bucket carries the fill-modeled median run (the "
              "payer's ~7.6% after spread+fee, the dud's 0.0 — never a "
              "raw peak)",
              _t7 and abs(_t7["medfill"] - 7.63) < 0.05
              and _t6 and _t6["medfill"] == 0.0
              and _d9 and abs(_d9["medfill"] - 1.64) < 0.05,
              {"t7": _t7, "t6": _t6, "d9": _d9})
        check("the quick labeler is inclusive-lower (60 -> '60s', 70 -> "
              "'70s', 90 -> '90+') and a missing/garbage/NaN quick "
              "lands in '?' (kept, never dropped)",
              app_mod._quick_bucket(60) == "60s"
              and app_mod._quick_bucket(70) == "70s"
              and app_mod._quick_bucket(90) == "90+"
              and app_mod._quick_bucket(59.9) == "<60"
              and app_mod._quick_bucket(None) == "?"
              and app_mod._quick_bucket("n/a") == "?"
              and app_mod._quick_bucket(float("nan")) == "?",
              {60: app_mod._quick_bucket(60),
               70: app_mod._quick_bucket(70),
               90: app_mod._quick_bucket(90),
               "none": app_mod._quick_bucket(None),
               "nan": app_mod._quick_bucket(float("nan"))})
        # the render: the expander builds through the sandbox too (the
        # cached wrapper pointed at the same fake module file)
        app_mod._cached_burial_hindsight.clear()
        _bh_calls = []
        _real_exp = app_mod.st.expander
        class _BHExp:
            def __init__(self, label, key=None):
                _bh_calls.append(label)
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        app_mod.st.expander = _BHExp
        try:
            app_mod._render_burial_hindsight()
            check("the panel renders (the expander label names the count "
                  "and the horizon)",
                  _bh_calls and "BURIAL HINDSIGHT" in _bh_calls[0]
                  and "3 recent candidates" in _bh_calls[0], _bh_calls)
        finally:
            app_mod.st.expander = _real_exp
        # the axis is DRAWN from the aggregate's own by-gate buckets —
        # the panel never re-joins the journal (one brain, the house
        # data-contract rule)
        _bh_src = (BASE / "app.py").read_text(
            encoding="utf-8").replace("\r\n", "\n")
        _bh_render = _bh_src.split("def _render_burial_hindsight()")[1].split(
            "def _render_burial_hindsight_api")[0]
        check("the panel draws the quick axis straight from the "
              "aggregate's by-gate buckets (no second join, no second "
              "read of the journal)",
              "the quick axis" in _bh_render
              and "by_gate" in _bh_render and "medfill" in _bh_render,
              _bh_render[:160])
        # ── THE LEARNING PLATE, MIRRORED (Sep 30): the bot console's
        # learning plate rendered HERE too — fee drag, the panel's
        # MEDIAN-payer capture, the guard factor (with the walk
        # projection the boundary hook persists), and the GF trend
        # sparkline (the nightly self_adjust samples). The sandbox is
        # already live: the aggregate reads the fixture journal, the
        # factor/projection/history ride patched seams. run_lp = one
        # render, one capture — no state bleeds between renders.
        _real_sub, _real_cap = app_mod.st.subheader, app_mod.st.caption
        _real_lf = getattr(app_mod, "_learning_factor", None)
        _real_lpr = getattr(app_mod, "_learning_projection", None)
        _real_lhist = getattr(app_mod, "_learning_history", None)
        _lp_fresh = {"start": 1.96, "end": 1.91, "delta": -0.05,
                     "applied": 1, "rotations": 1, "m_fail": 0,
                     "cap_refused": 0, "at_floor": False,
                     "params": {"alpha": 0.03}, "source": "live",
                     "computed_at": _bh_now.isoformat()}
        _old_proj = dict(_lp_fresh, computed_at=(
            _bh_now - timedelta(days=3)).isoformat())
        _hist_rows = [{"day": f"2026-09-{d:02d}",
                       "factor": 2.0 - d * 0.01} for d in range(1, 30)]
        _lp_calls = []

        def run_lp(proj=_lp_fresh, hist=_hist_rows):
            _lp_calls.clear()
            app_mod.st.subheader = lambda t, **k: _lp_calls.append(
                ("sub", t))
            app_mod.st.caption = lambda t, **k: _lp_calls.append(
                ("cap", t))
            app_mod._learning_factor = lambda: 1.9647
            app_mod._learning_projection = lambda: proj
            app_mod._learning_history = hist if callable(hist) \
                else (lambda weeks=8: hist)
            try:
                app_mod._render_learning_plate()
            finally:
                app_mod.st.subheader, app_mod.st.caption = \
                    _real_sub, _real_cap

        def _boom_hist(weeks=8):
            raise RuntimeError("history probe")

        try:
            run_lp()
            _lp_caps = [t for k, t in _lp_calls if k == "cap"]
            _lp_subs = [t for k, t in _lp_calls if k == "sub"]
            check("the learning plate renders ONE plate with four "
                  "lines (subheader + fee / capture / calibration / "
                  "trend caps)",
                  len(_lp_subs) == 1 and len(_lp_caps) == 4
                  and any("Learning" in s for s in _lp_subs),
                  (_lp_subs, len(_lp_caps)))
            _fee_cap = next((c for c in _lp_caps if "$0.52" in c), "")
            check("the fee-drag line mirrors the bot's fill model "
                  "exactly ($0.52 per $100, the ≈1.49% breakeven at "
                  "the 35% capture floor, the 1.8% entry floor, $1.28 "
                  "net)",
                  "$0.52 per $100" in _fee_cap and "≈1.49%" in _fee_cap
                  and "35%" in _fee_cap and "1.8%" in _fee_cap
                  and "$1.28" in _fee_cap, _fee_cap)
            _med_cap = next((c for c in _lp_caps
                             if "median payer" in c), "")
            check("the capture line is the PANEL'S OWN median payer "
                  "(the fixture's conservative representative: DEEP at "
                  "82% of a +2.0% peak, +1.6% after spread and fee — "
                  "one number about ONE payer, CAGE's fat 95% never "
                  "inflates it)",
                  "82%" in _med_cap and "+2.0%" in _med_cap
                  and "+1.6%" in _med_cap, _med_cap)
            _gf_cap = next((c for c in _lp_caps
                            if "GF projection" in c), "")
            check("the GF line renders the live factor AND the "
                  "persisted walk projection with its honest age "
                  "(1.96× → 1.91×, 1 apply, computed 0h ago)",
                  "1.96×" in _gf_cap and "1.96× → 1.91×" in _gf_cap
                  and "1 apply" in _gf_cap
                  and "computed 0h ago" in _gf_cap, _gf_cap)
            run_lp(proj=None)
            _gf2 = [t for k, t in _lp_calls
                    if k == "cap" and "GF projection" in t][-1]
            check("a missing projection renders DORMANT (the honest "
                  "absence — the walk arms with the rotation-boundary "
                  "hook), never a made-up heading",
                  "dormant" in _gf2 and "rotation-boundary hook" in _gf2,
                  _gf2)
            run_lp(proj=_old_proj)
            _gf3 = [t for k, t in _lp_calls
                    if k == "cap" and "GF projection" in t][-1]
            check("a stale projection is NAMED stale (beyond 48h it "
                  "is history, not a forecast)",
                  "stale" in _gf3 and "72h ago" in _gf3, _gf3)
            # ── THE GF TREND SPARKLINE (Sep 30): the machine factor
            # sampled by the nightly self_adjust rows — the TREND
            # beside the current value, drawn from the seam, never
            # derived in the plate.
            run_lp()
            _tr1 = [t for k, t in _lp_calls
                    if k == "cap" and "GF trend" in t]
            check("the trend sparkline draws the daily series (blocks, "
                  "the day count, the lo–hi band, the last value and "
                  "drift)",
                  len(_tr1) == 1 and "29d" in _tr1[0] and "▁" in _tr1[0]
                  and "1.71–1.99×" in _tr1[0] and "→ 1.71×" in _tr1[0]
                  and "-0.28" in _tr1[0], _tr1)
            run_lp(hist=[])
            _tr2 = [t for k, t in _lp_calls
                    if k == "cap" and "GF trend" in t][-1]
            check("an empty series renders collecting (the nightly "
                  "rows must accumulate first)",
                  "collecting" in _tr2 and "nightly self_adjust" in _tr2,
                  _tr2)
            run_lp(hist=_boom_hist)
            _tr3 = [t for k, t in _lp_calls
                    if k == "cap" and "GF trend" in t]
            check("a broken history read degrades the trend line only "
                  "(no trend caption, the plate stands)",
                  len(_tr3) == 0, len(_tr3))
        finally:
            if _real_lf is not None:
                app_mod._learning_factor = _real_lf
            else:
                app_mod.__dict__.pop("_learning_factor", None)
            if _real_lpr is not None:
                app_mod._learning_projection = _real_lpr
            else:
                app_mod.__dict__.pop("_learning_projection", None)
            if _real_lhist is not None:
                app_mod._learning_history = _real_lhist
            else:
                app_mod.__dict__.pop("_learning_history", None)
        _m_src = (BASE / "app.py").read_text(
            encoding="utf-8").replace("\r\n", "\n")
        _i_lp = _m_src.find("\n    _render_learning_plate()")
        _i_fp = _m_src.find("\n    _render_funnel_plate()")
        _i_bh = _m_src.find("\n    _render_burial_hindsight()")
        check("the plate mounts in the MASTER flow after the funnel "
              "plate and before the burial strip (the learning sits "
              "above the forensics)",
              0 < _i_fp < _i_lp < _i_bh, (_i_fp, _i_lp, _i_bh))
        # the honest-silence path: a journal-less sandbox fail-closes
        _bh_sbx2 = Path(tempfile.mkdtemp())
        app_mod.__file__ = str(_bh_sbx2 / "app.py")
        _bh_sb2_path = Path(_bh_sbx2)
        app_mod._cached_burial_hindsight.clear()
        check("the aggregate fail-closes to silence (a journal-less "
              "sandbox returns n=0, the renderer returns early)",
              isinstance(app_mod._burial_hindsight(), dict)
              and app_mod._burial_hindsight().get("n") == 0
              and not _bh_sb2_path.joinpath("popup_suppressions.jsonl")
              .exists())
    finally:
        app_mod.__file__ = real_file
        app_mod._cached_burial_hindsight.clear()
        import shutil as _shutil_bh
        _shutil_bh.rmtree(_bh_sbx, ignore_errors=True)
        if _bh_sbx2 is not None:
            _shutil_bh.rmtree(_bh_sbx2, ignore_errors=True)
except Exception as exc:
    check("the burial-hindsight pins ran", False, exc)

try:
    # ── THE HINDSIGHT API-FED PANEL (Sep 28): the console's wire slice —
    # summary + gates GETs only, dead-API silent, per the panel spec's
    # data contract (the panel reads ONLY the endpoints).
    _src = (BASE / "app.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    _api_sec = _src.split("def _cached_api_path")[1].split("def _render_funnel_plate")[0]
    check("the API-fed section reads ONLY the four GET routes "
          "(summary + gates for the rotation, ledger + telemetry for "
          "the zones-4/5 history) through ONE fetch funnel — a single "
          "urlopen ride, nothing else touches the wire",
          "/summary" in _api_sec and "/gates" in _api_sec
          and "/ledger" in _api_sec and "/telemetry" in _api_sec
          and _api_sec.count("urlopen(") == 1, _api_sec.count("urlopen("))
    _fetcher = _src.split("def _cached_api_path")[1].split("def _render_burial_hindsight_api")[0]
    check("the fetcher can never mutate (no POST anywhere in it — the "
          "API's mutating routes stay token-gated server-side regardless)",
          "POST" not in _fetcher, _fetcher[:120])
    _mount_ledger = _src.find("    _render_burial_hindsight()\n    # 📡")
    _mount_api = _src.find("    _render_burial_hindsight_api()")
    check("the mount sits after the ledger-fed panel (the wire companion "
          "renders second, its definition after the mount)",
          0 < _mount_ledger < _mount_api
          and _src.find("def _render_burial_hindsight_api()") > _mount_api,
          (_mount_ledger, _mount_api))

    # the wire round-trip through the REAL dispatcher-shaped payloads:
    # urlopen is function-local (from urllib.request import urlopen), so
    # the module attribute is the seam — the stub answers exactly what
    # rotation_summary/rotation_gates return.
    import os as _os
    import re as _re
    import urllib.request as _ureq
    _summ = {"rotation_id": "12-16", "B": 20, "P": 7, "r": 0.35,
             "proposed_delta": -0.05, "proposed_reason": "ok",
             "current_GF": 2.0}
    _gates = [{"gate": "tide-out", "B_g": 12, "P_g": 3, "r_g": 0.25,
               "top_candidates": [{"pair": "CAGE/USD", "run": 8.0,
                                   "gate": "tide-out", "ts": "x"}]}]
    _ledger = {"rows": [
        {"day": "2026-09-29", "rotation_id": "2026-09-29-12-16",
         "status": "applied", "before": 2.0, "after": 1.95,
         "delta": -0.05, "reason": "ok",
         "evidence": "sha256:abc"},
        {"day": "2026-09-29", "rotation_id": "2026-09-29-08-12",
         "status": "queued", "before": 2.0, "after": 2.0,
         "delta": -0.08, "reason": "concentration_gate_blocks_auto_apply"}]}
    _tel = {"events": [
        {"event": "auto_adjust", "rotation_id": "2026-09-29-12-16",
         "before_gf": 2.0, "after_gf": 1.95, "delta": -0.05,
         "B": 20, "P": 7, "r": 0.35, "status": "applied",
         "applied_by": "rotation_boundary", "commit_sha": "d5fd30f",
         "evidence_checksum": "sha256:abc", "timestamp":
         "2026-09-29T18:02:00-06:00"}]}
    _reqs: list = []

    class _APIResp:
        status = 200
        def __init__(self, body):
            self._b = body
        def read(self):
            return self._b
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _fake_urlopen(url, timeout=None):
        _reqs.append(str(url))
        payload = (_gates if "/gates" in str(url)
                   else _ledger if "/ledger" in str(url)
                   else _tel if "/telemetry" in str(url)
                   else _summ)
        return _APIResp(json.dumps(payload).encode("utf-8"))

    _real_urlopen = _ureq.urlopen
    _ureq.urlopen = _fake_urlopen
    _real_exp2 = app_mod.st.expander

    class _APIExp:
        def __init__(self, label, key=None):
            _exp_labels.append(label)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    _exp_labels: list = []
    app_mod.st.expander = _APIExp
    _md: list = []
    _real_md = app_mod.st.markdown
    app_mod.st.markdown = lambda body=None, **k: _md.append(str(body))
    try:
        app_mod._cached_api_path.clear()
        app_mod._render_burial_hindsight_api()
        check("the section GETs the current rotation's summary + gates "
              "PLUS the ledger + telemetry history — the zones-1-3 "
              "routes stay rotation-scoped, zones-4/5 ride the global "
              "history endpoints, and NO mutating route is ever asked",
              len(_reqs) == 4
              and any(_re.search(r"/\d{2}-\d{2}/summary$", u) for u in _reqs)
              and any(_re.search(r"/\d{2}-\d{2}/gates$", u) for u in _reqs)
              and any(u.endswith("/api/hindsight/ledger") for u in _reqs)
              and any(u.endswith("/api/hindsight/telemetry") for u in _reqs)
              and not any(m in " ".join(_reqs) for m in ("apply", "rollback")),
              _reqs)
        check("the API feed renders the summary headline (B/P/r/GF and "
              "the proposed delta with its reason)",
              _exp_labels and "API FEED" in _exp_labels[0]
              and "B 20" in _exp_labels[0] and "P 7" in _exp_labels[0]
              and "r 35%" in _exp_labels[0]
              and "GF 2.0" in _exp_labels[0]
              and "proposed -0.05" in _exp_labels[0], _exp_labels)
        # ── ZONES 4-5 (spec, Sep 29): the ledger + the audit trail
        check("ZONE 4 renders the adjustments ledger read-only: "
              "day/rotation/status/before→after/Δ/reason from the API's "
              "ledger payload",
              any("ADJUSTMENTS LEDGER" in m for m in _md)
              and any("2026-09-29-12-16" in m and "APPLIED" in m
                      and "2.00 → 1.95" in m for m in _md)
              and any("QUEUED" in m
                      and "concentration_gate_blocks_auto_apply" in m
                      for m in _md),
              [m[:120] for m in _md if "LEDGER" in m
               or "2026-09-29-12-16" in m][:3])
        check("ZONE 5 renders the audit trail read-only: timestamp, "
              "rotation, status, GF path, B/P/r, applied_by, commit "
              "sha, evidence checksum — from the API's telemetry "
              "payload",
              any("AUDIT TRAIL" in m for m in _md)
              and any("by rotation_boundary" in m and "commit d5fd30f"
                      in m and "ev sha256:abc" in m
                      and "B20 P7" in m for m in _md),
              [m[:120] for m in _md if "AUDIT" in m
               or "rotation_boundary" in m][:3])
        # ── THE ESCAPING PINS (round-5): the ledger/telemetry payloads
        # are operator-hostile DATA — a malicious reason string or an
        # injected rotation id must render as INERT TEXT, never as
        # markup through unsafe_allow_html. Red-first: the raw f-string
        # interpolation renders <img onerror=...> verbatim and these
        # pins read it straight out of the captured markdown.
        _summ_evil = dict(_summ, proposed_reason="<script>p()</script>")
        _gates_evil = [{"gate": "<u>g</u>", "B_g": 1, "P_g": 0,
                        "r_g": 0.0,
                        "top_candidates": [
                            {"pair": "<svg onload=alert(3)>",
                             "run": 1.0}]}]
        _ledger_evil = {"rows": [
            {"day": "<img src=x>",
             "rotation_id": "<script>a()</script>",
             "status": "applied", "before": 2.0, "after": 1.95,
             "delta": -0.05, "reason": "<b>inj</b>"}]}
        _tel_evil = {"events": [
            {"event": "auto_adjust",
             "rotation_id": "<script>b()</script>",
             "before_gf": 2.0, "after_gf": 1.95, "delta": -0.05,
             "B": 1, "P": 0, "r": None, "status": "applied",
             "applied_by": "<img src=x onerror=alert(2)>",
             "commit_sha": "<script>c()</script>",
             "evidence_checksum": "sha256:<b>x</b>",
             "reason": "<i>y</i>",
             "timestamp": "2026-09-29T18:02:00-06:00"}]}

        def _evil_urlopen(url, timeout=None):
            _reqs.append(str(url))
            payload = (_gates_evil if "/gates" in str(url)
                       else _ledger_evil if "/ledger" in str(url)
                       else _tel_evil if "/telemetry" in str(url)
                       else _summ_evil)
            return _APIResp(json.dumps(payload).encode("utf-8"))

        _ureq.urlopen = _evil_urlopen
        _md.clear()
        _exp_labels.clear()
        app_mod._cached_api_path.clear()
        app_mod._render_burial_hindsight_api()
        _evil_body = "\n".join(_md + list(_exp_labels))
        check("hostile payload: every payload-derived tag renders "
              "INERT (no raw <script>/<img>/<svg> anywhere in the "
              "panel's markdown or labels — the injection cannot fire)",
              "<script" not in _evil_body and "<img" not in _evil_body
              and "<svg" not in _evil_body and "<u>" not in _evil_body,
              [m[:100] for m in _md
               if "<script" in m or "<img" in m or "<svg" in m][:2])
        check("hostile payload: the values still RENDER escaped and "
              "readable — the operator sees the hostile row without it "
              "executing (and the zones' own chrome survives)",
              "&lt;script&gt;" in _evil_body and "&lt;img" in _evil_body
              and "ADJUSTMENTS LEDGER" in _evil_body
              and "AUDIT TRAIL" in _evil_body,
              [m[:120] for m in _md if "&lt;" in m][:2])
        check("hostile payload: the panel's own chrome divs still "
              "render raw (the escaping is scoped to payload values, "
              "not the panel itself)",
              "<div style=" in _evil_body)
        _ureq.urlopen = _fake_urlopen

        # the deployment seam: HINDSIGHT_API_BASE moves the wire
        _os.environ["HINDSIGHT_API_BASE"] = "http://127.0.0.1:9999"
        _reqs.clear()
        app_mod._cached_api_path.clear()
        app_mod._render_burial_hindsight_api()
        check("HINDSIGHT_API_BASE moves the wire (the deployment's port "
              "seam — no hardcoded-only endpoint)",
              _reqs and all(u.startswith("http://127.0.0.1:9999/")
                            for u in _reqs), _reqs)
        # the dead-API path: silence, never a disturbed console
        def _dead_urlopen(url, timeout=None):
            raise OSError("connection refused")
        _ureq.urlopen = _dead_urlopen
        _os.environ.pop("HINDSIGHT_API_BASE", None)
        app_mod._cached_api_path.clear()
        _exp_labels.clear()
        app_mod._render_burial_hindsight_api()
        check("a dead API fail-closes to silence (no expander, no "
              "traceback — the ledger-fed panel stands alone)",
              not _exp_labels, _exp_labels)
    finally:
        _ureq.urlopen = _real_urlopen
        app_mod.st.expander = _real_exp2
        app_mod.st.markdown = _real_md
        _os.environ.pop("HINDSIGHT_API_BASE", None)
        app_mod._cached_api_path.clear()
except Exception as exc:
    check("the API-fed panel pins ran", False, exc)

# ═══ THE TRACKED-POSITIONS MERGE (Sep 29, her name for the INJ day: "I
# was told still tracking INJ and nothing on the page shows it"): the
# rotation-boundary voice reads the BRIDGE book; the panel counted the
# BOT DB only — a bridge fill missing from the bot DB was spoken but
# invisible. The merge is pinned behavioral: bot book is truth where
# both agree (one row, tagged bot), a bridge-only fill rides tagged
# bridge, and the NOW strip NAMES the tracked pairs.
try:
    import asyncio as _aio_tp
    _loop = _aio_tp.new_event_loop()
    _aio_tp.set_event_loop(_loop)
    _real_bot_positions = app_mod.bot_positions
    _real_bridge_positions = app_mod.bridge_positions

    # an empty bridge read must not raise where the bridge is down —
    # the merge degrades to the bot book alone
    app_mod.bot_positions = lambda: [
        {"id": 1, "pair": "BTC/USD", "base": "BTC", "vol": 0.01,
         "entry": 50000.0, "ts": "t", "source": "bot"}]
    app_mod.bridge_positions = lambda: [
        {"id": "sig-inj", "pair": "INJ/USD", "base": "INJ",
         "entry": 7.727, "ts": "t", "source": "bridge"},
        {"id": "sig-btc", "pair": "BTC/USD", "base": "BTC",
         "entry": 49000.0, "ts": "t", "source": "bridge"}]
    merged = app_mod.tracked_positions()
    _bases = [p["base"] for p in merged]
    _inj = [p for p in merged if p["base"] == "INJ"]
    check("the merge counts EVERY voice-named pair: the bridge-only "
          "INJ fill rides (the spoken-but-invisible class is dead)",
          "INJ" in _bases and len(merged) == 2, _bases)
    check("the bot book is the truth where both agree: BTC appears "
          "ONCE, tagged bot (no double-count, no bridge shadow)",
          _bases.count("BTC") == 1
          and [p for p in merged if p["base"] == "BTC"][0]["source"]
          == "bot", _bases)
    check("a bridge-only row carries source=bridge (the reconcile "
          "section can classify it)",
          bool(_inj) and _inj[0].get("source") == "bridge")
    check("the NOW strip NAMES the tracked pairs (she sees INJ where "
          "she watches)",
          "tracking 2 positions: BTC, INJ" in app_mod.now_strip(
              "x", False, 5, 61) or
          "tracking 2 positions: INJ, BTC" in app_mod.now_strip(
              "x", False, 5, 61))
finally:
    app_mod.bot_positions = _real_bot_positions
    app_mod.bridge_positions = _real_bridge_positions

try:
    out = BASE / "apptest_check.txt"
    out.write_text("\n".join(lines)
                   + "\n" + "\n".join(
                       f"{'PASS' if ok else 'FAIL'}  {name}"
                       + (f"  [{d}]" if d and not ok else "")
                       for name, ok, d in results)
                   + f"\n\nRESULT: {sum(1 for _, ok, _ in results if ok)} passed, "
                     f"{sum(1 for _, ok, _ in results if not ok)} failed\n",
                   encoding="utf-8")
    fails = [r for r in results if not r[1]]
    print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
    print("done")
    sys.exit(1 if fails else 0)
finally:
    # the SCOUT_PREFS_FILE env swap's restore — the suite hands the
    # environment back before it exits (the leak gate's contract)
    if _SCOUT_PREFS_REAL is not None:
        _os.environ["SCOUT_PREFS_FILE"] = _SCOUT_PREFS_REAL
    else:
        _os.environ.pop("SCOUT_PREFS_FILE", None)
