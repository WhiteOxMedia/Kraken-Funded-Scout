"""Drive of the night-to-night verdict tracker (user, Sep 21):

  discipline.verdict_history() / top_verdict_streak() — the nightly
    top-verdict trail from persisted DAY rows (top_verdict field, with
    the usd_left component fallback for legacy rows), and the
    consecutive-same-top streak;
  cooldown_review — carries verdict_streak in its return, DAY row, and
    speaks the week warning at VERDICT_STREAK_WARN_N;
  app.py's review plate — the night-to-night trail rows (newest first,
    green/red delta between same-class nights) inside the expander, the
    week warning on the page, and silence when there is no trail.

The REAL bodies are exec'd from source (the repo's headless pattern),
with st / discipline / state stubbed into recorders.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    print(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    PASS, FAIL = PASS + (1 if ok else 0), FAIL + (0 if ok else 1)


# ── the discipline stub for the app-side exec ───────────────────────────
_disc = types.ModuleType("discipline")
_disc.load_state = lambda: DISC_STATE
_disc.verdict_history = lambda days=14: []
_disc.top_verdict_streak = lambda: (0, "", 0.0)
_disc.verdict_top_three = lambda: None
_disc.VERDICT_STREAK_WARN_N = 7
_disc.VERDICT_TOP3_STREAK_N = 3
sys.modules["discipline"] = _disc

DISC_STATE = {"history": []}

# ═════════════════════════════════════════1. the helper math ═══════════
# import the REAL discipline module — its helpers run against a temp state
import tempfile
import json as _json
import os

_tmp = Path(tempfile.mkdtemp())
STATE_PATH = _tmp / "discipline_state.json"


def _write_state(hist):
    STATE_PATH.write_text(_json.dumps({"history": hist}), encoding="utf-8")


# discipline's REAL module — loaded under its own spec because the
# stub for the app-side exec squats the "discipline" name in sys.modules
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "discipline_real", Path(__file__).parent / "discipline.py")
dc_real = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dc_real)

_orig_state_path = dc_real.STATE_FILE
dc_real.STATE_FILE = STATE_PATH

DAY = ("2026-09-{:02d}", )
_hist = []
for d, tv in [("2026-09-10", ("stayed too long - gave back over half the peak", 64.78)),
              ("2026-09-11", ("stayed too long - gave back over half the peak", 61.20)),
              ("2026-09-12", ("stayed too long - gave back over half the peak", 40.00)),
              ("2026-09-13", ("bought the top - the coin never ran", 42.52)),
              ("2026 STL", None)]:
    pass  # placeholder guard (never used)

_hist = [
    {"date": "2026-09-10", "rot": "DAY",
     "top_verdict": ["stayed too long - gave back over half the peak", 64.78],
     "total_left": 704.0, "usd_left": {"system_burial": 704.0}},
    {"date": "2026-09-11", "rot": "DAY",
     "top_verdict": ["stayed too long - gave back over half the peak", 61.20],
     "total_left": 700.0, "usd_left": {"system_burial": 700.0}},
    {"date": "2026-09-12", "rot": "DAY",
     "top_verdict": ["stayed too long - gave back over half the peak", 40.00],
     "total_left": 50.0, "usd_left": {"system_burial": 50.0}},
    {"date": "2026-09-13", "rot": "DAY",
     "top_verdict": ["bought the top - the coin never ran", 42.52],
     "total_left": 50.0, "usd_left": {"system_burial": 50.0}},
    # a legacy night: no top_verdict, the component map decides
    {"date": "2026-09-14", "rot": "DAY",
     "total_left": 64.0, "usd_left": {"early_exit": 22.77, "giveback": 41.0}},
    # a clean night: nothing leaks -> excluded
    {"date": "2026-09-15", "rot": "DAY", "total_left": 0.0, "usd_left": {}},
    # a no-review night: no DAY row at all -> skipped
]
_write_state(_hist)

trail = dc_real.verdict_history()
check("trail: one entry per reviewed night (clean + no-review skipped)",
      len(trail) == 5, trail)
check("trail: oldest -> newest", [t[0] for t in trail]
      == sorted(t[0] for t in trail), [t[0] for t in trail])
check("trail: names carry the short form for the streak, long for display",
      trail[0][1].startswith("stayed too long"), trail[0][1])

n, name, usd = dc_real.top_verdict_streak()
check("streak: counts consecutive same-top nights ending today",
      n == 1 and name == "gave back after the goal", (n, name))
check("streak: latest cost rides the return", usd == 41.0, usd)

# a 7-night streak fires the threshold
_hist7 = [{"date": f"2026-09-{d:02d}", "rot": "DAY",
           "top_verdict": ["stayed too long - gave back over half the peak", 60.0]}
          for d in range(8, 15)]
_write_state(_hist7)
n7, name7, _ = dc_real.top_verdict_streak()
check("streak: seven same-top nights reach the warning threshold",
      n7 == 7 and name7 == "stayed too long", (n7, name7))
check("threshold constant is the user's week", dc_real.VERDICT_STREAK_WARN_N == 7)

# legacy fallback: a night with no top_verdict still enters the trail
_hist_leg = [{"date": "2026-09-14", "rot": "DAY",
              "total_left": 64.0, "usd_left": {"early_exit": 22.77}}]
_write_state(_hist_leg)
leg = dc_real.verdict_history()
check("trail: legacy rows enter via the usd_left component map",
      len(leg) == 1 and leg[0][1] == "exited too soon", leg)

# broken state: silent
dc_real.STATE_FILE = _tmp / "missing.json"
check("trail: missing state is silent", dc_real.verdict_history() == [])
# back to the shared temp fixture file — the later sections write their
# own scenarios into it
dc_real.STATE_FILE = STATE_PATH

# ═══════════════════════════════════════2. the review wiring ═══════════
src = Path(__file__).parent.joinpath("discipline.py").read_text(encoding="utf-8")
check("review: result carries verdict_streak", '"verdict_streak"' in src
      and "top_verdict_streak()" in src)
check("voice: the week-warning clause exists",
      "has topped the book" in src and "the dial is waiting" in src)
check("voice: warning gates on the threshold constant",
      ">= VERDICT_STREAK_WARN_N" in src)

# ═══════════════════════════════════════3. the console surfaces ════════
class _Stub:
    def __init__(self):
        self.markdowns = []
        self.captions = []
        self.expander_open = []

    class _Exp:
        def __init__(self, outer, label, key=None):
            self.outer, self.label, self.key = outer, label, key

        def __enter__(self):
            self.outer.expander_open.append((self.label, self.key))
            return self

        def __exit__(self, *a):
            return False

    def expander(self, label, expanded=False, key=None):
        return _Stub._Exp(self, label, key)

    def markdown(self, html, **k):
        self.markdowns.append(str(html))

    def caption(self, txt):
        self.captions.append(str(txt))


STUB = _Stub()

APP = Path(__file__).parent / "app.py"
asrc = APP.read_text(encoding="utf-8").replace("\r\n", "\n")
alines = asrc.split("\n")


def _extract_to(name):
    start = next(i for i, ln in enumerate(alines) if ln.startswith(f"def {name}("))
    end = next(j for j in range(start + 1, len(alines)) if alines[j].startswith("def "))
    return "\n".join(alines[start:end])


pick_src = _extract_to("_pick_card")
strip_src = _extract_to("_render_suppression_strip")
review_src = _extract_to("_render_cooldown_review")

check("slice: review body reaches the trail render",
      "the top verdict, night to night" in review_src)
check("slice: review body reaches the page-level warning",
      "THE WEEK WARNING" in review_src)
check("slice: review body runs to its expander tail",
      "cooldown review's own verdicts" in review_src)

NS = {
    "st": STUB,
    "datetime": __import__("datetime").datetime,
    "timedelta": __import__("datetime").timedelta,
    "timezone": __import__("datetime").timezone,
    "_now_mst": lambda: __import__("datetime").datetime
        .now(__import__("datetime").timezone.utc).astimezone(),
    "_asset": lambda p: p.split("/")[0],
    "_fmt_price": lambda p: f"${p:,.2f}",
    "_tide_line": lambda: "TIDE: TEST",
    "live_price": lambda pair: 100.0,
    "current_threshold": lambda: ("T", "Mon", 89, 89, False),
    "plate": lambda title, body: f"<plate>{title}{body}</plate>",
    "lcd": lambda rows, tone="": "<lcd>" + "".join(rows) + "</lcd>",
    "_plate_call": lambda *a, **k: None,
    "HEADLINE_STALE_TTL_S": 30,
    "Path": Path,
    "logger": types.SimpleNamespace(debug=lambda *a, **k: None),
    "__file__": str(APP),
    "_load_watch": lambda: {},
    "_vcol": lambda name: ("#ff6b5e" if "stayed too long" in str(name)
                           or "held too long" in str(name) else "#ffc36b"),
    "_disc": _disc,
}
exec(review_src, NS)
_render_cooldown_review = NS["_render_cooldown_review"]

# scenario A: a 7-night streak + a real trail -> warning on the page,
# trail inside the expander
_disc.verdict_history = lambda days=14: [
    ("2026-09-08", "stayed too long - gave back over half the peak", 64.0),
    ("2026-09-09", "stayed too long - gave back over half the peak", 61.0),
    ("2026-09-10", "stayed too long - gave back over half the peak", 55.0),
    ("2026-09-11", "stayed too long - gave back over half the peak", 50.0),
    ("2026-09-12", "stayed too long - gave over half the peak", 45.0),
    ("2026-09-13", "stayed too long - gave back over half the peak", 40.0),
    ("2026-09-14", "stayed too long - gave back over half the peak", 35.0),
]
_disc.top_verdict_streak = lambda: (7, "stayed too long", 35.0)
DISC_STATE = {"history": [
    {"date": "2026-09-14", "rot": "DAY", "score": 72.0,
     "top_verdict": ["stayed too long - gave back over half the peak", 35.0],
     "total_left": 40.0, "usd_left": {"system_burial": 40.0},
     "assets": [{"asset": "FARTCOIN", "kind": "sold",
                 "verdict": "took the goal - the discipline shape",
                 "usd_left": 0.0, "detail": "peak +3.3% vs goal 2.7%"}]},
    {"date": "2026-09-13", "rot": "DAY", "score": 70.0,
     "top_verdict": ["stayed too long - gave back over half the peak", 40.0],
     "total_left": 45.0, "usd_left": {"system_burial": 45.0}},
]}
_disc.load_state = lambda: DISC_STATE

STUB.markdowns.clear()
try:
    _render_cooldown_review()
except Exception as exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    check("plate ran clean (streak scenario)", False, repr(exc))
else:
    check("plate ran clean (streak scenario)", True)

page_md = STUB.markdowns          # everything before the expander context
warn = next((m for m in page_md if "has topped the book" in m), "")
check("warning renders on the page (7-night streak)",
      bool(warn) and "7 nights" in warn, warn[:120])

trail_md = next((m for m in page_md if "night to night" in m), "")
check("trail renders inside the expander (after the strip)",
      bool(trail_md) and "stayed too long" in trail_md, trail_md[:120])
check("trail rows newest first",
      trail_md.find("09-14") < trail_md.find("09-08"))
check("delta computed between same-class nights (61 - 64 = -3)",
      "Δ -3" in trail_md or "Δ −3" in trail_md, trail_md[:200])

# scenario B: a 2-night streak -> the warning stays silent
STUB.markdowns.clear()
_disc.top_verdict_streak = lambda: (2, "stayed too long", 61.0)
try:
    _render_cooldown_review()
except Exception as exc:  # noqa: BLE001
    check("plate ran clean (short-streak scenario)", False, repr(exc))
else:
    check("plate ran clean (short-streak scenario)", True)
check("warning silent below the threshold",
      not any("has topped the book" in m for m in STUB.markdowns))
check("trail still renders below the threshold",
      any("night to night" in m for m in STUB.markdowns))

# scenario C: no trail at all -> the whole block stays silent
STUB.markdowns.clear()
_disc.verdict_history = lambda days=14: []
_disc.top_verdict_streak = lambda: (0, "", 0.0)
try:
    _render_cooldown_review()
    check("no trail -> silence (no night-to-night, no warning)",
          not any("night to night" in m or "has topped the book" in m
                  for m in STUB.markdowns))
except Exception as exc:  # noqa: BLE001
    check("no trail -> silence", False, repr(exc))

# ═══════════════════════════════════════4. the COLOR streak (5-night) ═
# the real helpers: a mixed RED week (3 stayed-too-long + 2 bought-the-top)
# is a class streak of 2 but a COLOR streak of 5 — the coarser signal
_hist_red = [{"date": f"2026-09-{d:02d}", "rot": "DAY",
              "top_verdict": [v, 50.0], "total_left": 60.0,
              "usd_left": {"system_burial": 60.0}}
             for d, v in [
                 (9, "stayed too long - gave back over half the peak"),
                 (10, "stayed too long - gave back over half the peak"),
                 (11, "stayed too long - gave back over half the peak"),
                 (12, "bought the top - the coin never ran"),
                 (13, "bought the top - the coin never ran")]]
_write_state(_hist_red)
csn, csc, csu = dc_real.verdict_color_streak()
check("color streak: five mixed RED nights reach 5",
      csn == 5 and csc == "red", (csn, csc))
k_n, k_name, _ = dc_real.top_verdict_streak()
check("color streak is coarser than the class streak (class = 2 there)",
      k_n == 2 and k_name == "bought the top", (k_n, k_name))
check("color threshold is the user's five",
      dc_real.VERDICT_COLOR_STREAK_WARN_N == 5)
check("color table mirrors the strip (goal green, funnel violet, unknown grey)",
      dc_real.verdict_color("took the goal - x") == "green"
      and dc_real.verdict_color("buried by the funnel") == "violet"
      and dc_real.verdict_color("mystery verdict") == "grey")
_hist_green = [{"date": f"2026-09-{d:02d}", "rot": "DAY",
                "top_verdict": ["took the goal - the discipline shape", 0.0],
                "total_left": 0.0, "usd_left": {}} for d in range(8, 15)]
_write_state(_hist_green)
check("green never warns (a week of goals is good news)",
      dc_real.verdict_color_streak() == (0, "green", 0.0))

# her voice: the REAL _speak_cooldown_review with a stubbed server
bs_stub = types.ModuleType("background_scanner")
_SPOKEN = []
bs_stub._speak_server = lambda text, rate=1: _SPOKEN.append(str(text))
sys.modules["background_scanner"] = bs_stub

lines_d = Path(__file__).parent.joinpath("discipline.py") \
    .read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
_s = next(i for i, ln in enumerate(lines_d)
          if ln.startswith("def _speak_cooldown_review("))
_e = next(j for j in range(_s + 1, len(lines_d)) if lines_d[j].startswith("def "))
# exec with the REAL module's globals — the function reads the
# VERDICT_*_WARN_N constants, and its fail-soft catch-all would swallow
# a bare-namespace NameError into silence (a false PASS)
NSD = dict(vars(dc_real))
exec("\n".join(lines_d[_s:_e]), NSD)
_speak = NSD["_speak_cooldown_review"]

_base = {"total_left": 40.0, "top_gate": "deep-veto", "lesson": ""}

_SPOKEN.clear()
_speak(dict(_base, verdict_streak=None,
            verdict_color_streak={"n": 5, "color": "red", "usd": 50.0}),
       20.0, 10.0, 2, "flat", False)
check("voice: the color clause fires at five nights",
      any("red leak has topped the strip 5 nights" in s for s in _SPOKEN),
      _SPOKEN)

_SPOKEN.clear()
_speak(dict(_base, verdict_streak=None, verdict_color_streak=None),
       20.0, 10.0, 2, "flat", False)
check("voice: no color clause without the streak",
      not any("topped the strip" in s for s in _SPOKEN))

_SPOKEN.clear()
_speak(dict(_base,
            verdict_streak={"n": 7, "name": "stayed too long", "usd": 60.0},
            verdict_color_streak={"n": 5, "color": "red", "usd": 50.0}),
       20.0, 10.0, 2, "flat", False)
check("voice: class and color clauses coexist when both fire",
      any("topped the book 7 nights" in s for s in _SPOKEN)
      and any("topped the strip 5 nights" in s for s in _SPOKEN), _SPOKEN)

src_d = "\n".join(lines_d)
check("wiring: the DAY row and the review result carry the color streak",
      src_d.count('"verdict_color_streak"') >= 3)

# ═══════════════════════════════════════5. the top three (3 nights) ════
# the REAL verdict_top_three: the study's own summary order, zeros
# dropped, short names, voice-safe types — the study stubbed at its seam
_vs_stub = types.ModuleType("_verdict_study")
_vs_stub.summary = lambda: [
    ("stayed too long - gave back over half the peak", 6, 64.78),
    ("bought the top - the coin never ran", 3, 42.52),
    ("exited too soon - the run continued without us", 2, 0.0),
]
sys.modules["_verdict_study"] = _vs_stub
t3 = dc_real.verdict_top_three()
check("top three: the study's ranking, zeros excluded, short names",
      t3 == [("stayed too long", 6, 64.78), ("bought the top", 3, 42.52)],
      t3)
_vs_stub.summary = lambda: []
check("top three: a clean book is None", dc_real.verdict_top_three() is None)
_vs_stub.summary = lambda: (_ for _ in ()).throw(RuntimeError("ledger gone"))
check("top three: a broken study fails soft to None",
      dc_real.verdict_top_three() is None)
check("the threshold is the user's three nights",
      dc_real.VERDICT_TOP3_STREAK_N == 3)

# her voice: the top three replaces the single mention on a streak night
_SPOKEN.clear()
_speak(dict(_base, top_three=[("stayed too long", 6, 64.78),
                              ("bought the top", 3, 42.52)],
            top_verdict=("stayed too long - gave back over half the peak",
                         64.78),
            verdict_streak=None, verdict_color_streak=None),
       20.0, 10.0, 2, "flat", False)
_t3s = next((s for s in _SPOKEN if "top three on the book" in s), "")
check("voice: the top three clause fires with name, dollars and count",
      "stayed too long 65 dollars" in _t3s
      and "bought the top 43 dollars" in _t3s
      and "6 trades" in _t3s, _t3s)
check("voice: the top three replaces the single mention (no #1 twice)",
      _t3s and not any("costliest pattern on the book" in s
                       for s in _SPOKEN), _SPOKEN)

_SPOKEN.clear()
_speak(dict(_base, top_three=[("stayed too long", 6, 64.78)],
            top_verdict=("stayed too long - gave back over half the peak",
                         64.78),
            verdict_streak=None, verdict_color_streak=None),
       20.0, 10.0, 2, "flat", False)
check("voice: a lone entry falls back to the single mention",
      any("costliest pattern on the book is stayed too long" in s
          for s in _SPOKEN)
      and not any("top three on the book" in s for s in _SPOKEN), _SPOKEN)

_SPOKEN.clear()
_speak(dict(_base, top_three=None,
            top_verdict=("stayed too long - gave back over half the peak",
                         64.78),
            verdict_streak=None, verdict_color_streak=None),
       20.0, 10.0, 2, "flat", False)
check("voice: below the streak the single mention stands alone",
      any("costliest pattern on the book is stayed too long" in s
          for s in _SPOKEN)
      and not any("top three on the book" in s for s in _SPOKEN), _SPOKEN)

_SPOKEN.clear()
_speak(dict(_base, top_three=[("stayed too long", 6, 64.78),
                              ("bought the top", 3, 42.52)],
            verdict_streak={"n": 7, "name": "stayed too long", "usd": 60.0},
            verdict_color_streak={"n": 5, "color": "red", "usd": 50.0}),
       20.0, 10.0, 2, "flat", False)
check("voice: the top three coexists with the streak clauses",
      any("top three on the book" in s for s in _SPOKEN)
      and any("topped the book 7 nights" in s for s in _SPOKEN)
      and any("topped the strip 5 nights" in s for s in _SPOKEN), _SPOKEN)

check("wiring: top_three rides return + DAY row, gated by the streak",
      src_d.count('"top_three"') >= 3
      and "VERDICT_TOP3_STREAK_N" in src_d
      and ">= VERDICT_TOP3_STREAK_N" in src_d)

# ═══════════════════════════════6. the trail proposal (week warning) ═══
# the REAL trail_proposal against the study stubbed at its seam: the
# control guard and the per-trade selection must both bite
_vs_stub.grade_trades = staticmethod(lambda: [
    {"verdict": "stayed too long - gave back over half the peak"},
    {"verdict": "stayed too long - gave back over half the peak"},
    {"verdict": "took the goal - the discipline shape"}])
_vs_stub.TABLE = {   # P: (leak (d, n), ctrl (d, n))
    1.0: ((10.0, 2), (-4.0, 1)),   # best leak per BUT breaks the control
    1.5: ((6.0, 2), (2.0, 1)),     # the winner (per 3.0, control safe)
    2.0: ((2.0, 2), (0.0, 1)),     # positive but smaller per
    3.0: ((-2.0, 2), (0.0, 1)),    # negative edge -> never proposed
}
def _sim_armed_stub(trades, P):
    leak, ctrl = _vs_stub.TABLE[P]
    return leak if len(trades) == 2 else ctrl
_vs_stub._sim_armed = staticmethod(_sim_armed_stub)
prop = dc_real.trail_proposal("stayed too long")
check("proposal: picks the best per-trade width the control allows",
      prop is not None and prop["width"] == 1.5 and abs(prop["per"] - 3.0) < 1e-9,
      prop)
check("proposal: the control-guarded width is excluded (a good dial "
      "must not break took-the-goal)",
      prop is not None and prop["width"] != 1.0)
check("proposal: the spoken clause carries width, dollars and count",
      prop is not None and "1.5% armed trail" in prop["proposal"]
      and "3 dollars more per trade" in prop["proposal"]
      and "2 trades" in prop["proposal"],
      prop and prop["proposal"])
_vs_stub.grade_trades = staticmethod(lambda: [
    {"verdict": "took the goal - the discipline shape"}])
check("proposal: a pattern with no trades is None (nothing to defend)",
      dc_real.trail_proposal("stayed too long") is None)
_vs_stub.grade_trades = staticmethod(
    lambda: (_ for _ in ()).throw(RuntimeError("ledger gone")))
check("proposal: a broken study fails soft to None",
      dc_real.trail_proposal("stayed too long") is None)
_vs_stub.grade_trades = staticmethod(lambda: [
    {"verdict": "stayed too long - gave back over half the peak"},
    {"verdict": "stayed too long - gave back over half the peak"}])
_vs_stub.TABLE = {   # P: (leak (d, n), ctrl (d, n)) — every leak edge negative
    1.0: ((-5.0, 2), (0.0, 1)),
    1.5: ((-3.0, 2), (0.0, 1)),
    2.0: ((-1.0, 2), (0.0, 1)),
    3.0: ((-2.0, 2), (0.0, 1)),
}
check("proposal: no positive edge on any width -> None (she stays silent)",
      dc_real.trail_proposal("stayed too long") is None)
_vs_stub._sim_armed = staticmethod(lambda trades, P: (0, 0))
check("proposal: sims with no usable candles -> None (no divide-by-zero)",
      dc_real.trail_proposal("stayed too long") is None)

# her voice: the proposal clause rides the week warning, or stays silent.
# The exec'd voice reads its globals from the NSD snapshot taken at exec
# time — so the patch must land on NSD, not on the module attribute.
_orig_prop_nsd = NSD["trail_proposal"]
try:
    NSD["trail_proposal"] = lambda name: {
        "proposal": "the sim says a 1.5% armed trail would have kept "
                    "3 dollars more per trade across 2 trades"}
    _SPOKEN.clear()
    _speak(dict(_base, verdict_streak={"n": 7, "name": "stayed too long",
                                       "usd": 60.0},
                verdict_color_streak=None),
           20.0, 10.0, 2, "flat", False)
    check("voice: the week warning carries the sim's specific dial",
          any("topped the book 7 nights" in s for s in _SPOKEN)
          and any("1.5% armed trail" in s for s in _SPOKEN), _SPOKEN)

    NSD["trail_proposal"] = lambda name: None
    _SPOKEN.clear()
    _speak(dict(_base, verdict_streak={"n": 7, "name": "stayed too long",
                                       "usd": 60.0},
                verdict_color_streak=None),
           20.0, 10.0, 2, "flat", False)
    check("voice: an undefended warning stays alone (no empty proposal)",
          any("the dial is waiting" in s for s in _SPOKEN)
          and not any("armed trail" in s for s in _SPOKEN), _SPOKEN)

    NSD["trail_proposal"] = _orig_prop_nsd
    _SPOKEN.clear()
    _speak(dict(_base, verdict_streak={"n": 2, "name": "stayed too long",
                                       "usd": 60.0},
                verdict_color_streak=None),
           20.0, 10.0, 2, "flat", False)
    check("voice: below the warning threshold no proposal is sought",
          not any("armed trail" in s for s in _SPOKEN)
          and not any("the dial is waiting" in s for s in _SPOKEN), _SPOKEN)
finally:
    NSD["trail_proposal"] = _orig_prop_nsd

check("wiring: the proposal rides the warning, gated on the streak",
      src_d.count("trail_proposal(") >= 2
      and "TRAIL_SIM_ABLE" in src_d)

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
