"""Drive of _render_cooldown_review (app.py) — the console's read view of
the cooldown review's per-asset verdicts (user, Sep 21: "show the
per-asset verdicts and money-left breakdown in the console"). The REAL
function body is exec'd from app.py source (the repo's headless pattern)
with st stubbed into a recorder; fabricated DAY rows prove every verdict
class renders, the breakdown totals, and the empty-state silence.
"""
import re
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    print(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    PASS, FAIL = PASS + (1 if ok else 0), FAIL + (0 if ok else 1)


# ── the st stub: a recorder ─────────────────────────────────────────────
class _Stub:
    def __init__(self):
        self.expander_open = []
        self.markdowns = []
        self.captions = []
        self.pills_calls = []
        self.pills_return = None
        self.session_state = None
        self._ctx = None

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

    def pills(self, label, options, selection_mode="single", default=None,
              format_func=None, key=None):
        self.pills_calls.append({"label": label, "options": list(options),
                                 "default": default, "key": key})
        return self.pills_return


STUB = _Stub()

# ── the discipline stub: state we control per scenario ──────────────────
_disc = types.ModuleType("discipline")
_disc.load_state = lambda: {"history": []}
sys.modules["discipline"] = _disc

# ── exec the REAL function body from app.py ────────────────────────────
app_src = Path("app.py").read_text(encoding="utf-8")
m = re.search(r"\ndef _render_cooldown_review\(\).*?(?=\ndef )", app_src, re.S)
assert m, "function not found"
body = m.group(0)
ns = {
    "st": STUB, "logger": types.SimpleNamespace(
        debug=lambda *a, **k: None),
    "_disc": _disc, "_asset": lambda s: s.upper(),
    "_now_mst": lambda: types.SimpleNamespace(strftime=lambda f: "21:33"),
}
exec(compile(body, "app.py", "exec"), ns)
render = ns["_render_cooldown_review"]

DAY = {
    "rot": "DAY", "date": "2026-09-21",
    "assets": [
        {"asset": "FARTCOIN", "kind": "sold", "entry": 0.187, "exit": 0.1931,
         "peak": 0.1931, "goal_pct": 2.7, "realized": 12.75, "size": 400.6,
         "verdict": "took the goal - the discipline shape", "usd_left": 0.0},
        {"asset": "ETH", "kind": "sold", "entry": 2771.17, "exit": 2774.48,
         "peak": 2800.0, "goal_pct": 1.43, "realized": 0.19, "size": 470.3,
         "verdict": "exited too soon - the goal was on the tape",
         "usd_left": 9.4},
        {"asset": "WLD", "kind": "sold", "entry": 0.1, "exit": 0.09,
         "peak": 0.12, "goal_pct": 1.5, "realized": -20.0, "size": 1000,
         "verdict": "stayed too long - gave back over half the peak",
         "usd_left": 50.0},
        {"asset": "SUI", "kind": "held", "entry": 1.0, "exit": None,
         "peak": 1.02, "goal_pct": 1.5, "realized": 2.0, "size": 100,
         "verdict": "still holding under the goal", "usd_left": 0.0},
        {"asset": "POPCAT", "kind": "buried", "gate": "downward-trend",
         "verdict": "buried by the funnel - never offered", "usd_left": 42.0},
    ],
}

# ── 1. no DAY row: silent ───────────────────────────────────────────────
_disc.load_state = lambda: {"history": [{"rot": "0-4", "score": 70.0}]}
render()
check("no DAY row renders nothing", not STUB.expander_open)
check("no DAY row writes no markdown", not STUB.markdowns)

# ── 2. the full day: every verdict class renders ────────────────────────
_disc.load_state = lambda: {"history": [
    {"rot": "0-4", "score": 70.0}, {"rot": "DAY", **DAY}]}
render()
check("expander opens once with the stable key",
      len(STUB.expander_open) == 1 and STUB.expander_open[0][1]
      == "cooldown_review_expander",
      STUB.expander_open)
check("expander names the day", "2026-09-21" in STUB.expander_open[0][0])
html_all = "\n".join(STUB.markdowns)
for tok in ("FARTCOIN", "ETH", "WLD", "SUI", "POPCAT"):
    check(f"row rendered: {tok}", tok in html_all)
for v in ("took the goal", "exited too soon", "stayed too long",
          "still holding under the goal", "buried by the funnel"):
    check(f"verdict text on the page: {v}", v in html_all)
check("buried row names its gate", "downward-trend" in html_all)
check("buried row shows theoretical", "theoretical" in html_all)
check("peak vs goal shown", "peak +3.3% vs goal 2.7%" in html_all
      and "peak +1.0% vs goal 1.4%" in html_all, "peak lines")
check("realized dollars shown", "$12.75" in html_all)

# ── 3. the money-left breakdown ─────────────────────────────────────────
check("breakdown total is the sum", "$101 left on the table" in html_all,
      "50+42+9.4 ≈ 101")
check("class breakdown names stayed-too-long",
      "stayed too long" in html_all and "$50" in html_all)
check("class breakdown names the burial",
      "buried by the funnel" in html_all and "$42" in html_all)

# ── 4. the fresh stamp + fail-soft ──────────────────────────────────────
check("fresh stamp present", STUB.captions
      and "read live each rerun" in STUB.captions[-1])
_disc.load_state = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    render()
    check("broken state fails soft (no raise, nothing rendered)",
          not STUB.markdowns or STUB.markdowns[-1] != "")
except Exception as e:
    check("broken state fails soft (no raise, nothing rendered)", False, e)

# ── 5. the verdict-history strip ─────────────────────────────────
STUB.markdowns.clear()
_disc.load_state = lambda: {"history": [
    {"rot": "DAY", "date": "2026-09-19", "total_left": 704.2,
     "usd_left": {"system_burial": 704.2, "missed": 0.0}},   # legacy row
    {"rot": "DAY", "date": "2026-09-20", "total_left": 22.0,
     "usd_left": {"early_exit": 22.0, "system_burial": 0.0},
     "top_verdict": None},
    {"rot": "DAY", "date": "2026-09-21", "total_left": 50.0,
     "usd_left": {},
     "top_verdict": ("stayed too long - gave back over half the peak", 50.0)},
    {"rot": "DAY", "date": "2026-09-22", "total_left": 0.0,
     "usd_left": {}, "top_verdict": None},
]}
render()
strip_md = [m for m in STUB.markdowns if "day by day" in m]
check("strip renders when DAY rows exist", bool(strip_md))
s = strip_md[-1] if strip_md else ""
check("one glyph per DAY row", s.count("■") == 4, s.count("■"))
check("today's top_verdict paints red (held too long)",
      "#ff6b5e" in s)
check("legacy row derives the burial violet from its usd map",
      "#9a86ff" in s)
check("legacy early-exit row derives amber",
      s.count("#ffc36b") == 1)
check("a clean day paints green", "#4be38a" in s)
check("titles carry date, class and dollars",
      "09-21: stayed too long - gave back over half the peak - $50 left"
      in s and "09-19: buried by the funnel - $704 left" in s)
check("clean title says nothing leaked",
      "09-22: clean - nothing leaked - $0 left" in s)
check("legend present", "green clean" in s and "violet buried" in s)

# oldest -> newest: title dates appear in ascending order in the html
import re as _re
dates = _re.findall(r"title='(\d\d-\d\d):", s)
check("glyphs run oldest to newest", dates == sorted(dates), dates)

# the 30-day cap
_disc.load_state = lambda: {"history": [
    {"rot": "DAY", "date": f"2026-08-{d:02d}", "total_left": 5.0,
     "usd_left": {"missed": 5.0}, "top_verdict": None}
    for d in range(1, 41)]}
STUB.markdowns.clear()
render()
strip_md = [m for m in STUB.markdowns if "day by day" in m]
check("strip caps at 30 days", bool(strip_md)
      and strip_md[-1].count("■") == 30, strip_md[-1].count("■") if strip_md else 0)

# no DAY rows -> no strip (and no rows)
STUB.markdowns.clear()
_disc.load_state = lambda: {"history": [{"rot": "0-4", "score": 70.0}]}
render()
check("no DAY rows -> no strip",
      not any("day by day" in m for m in STUB.markdowns))

# ── 5b. TONIGHT'S PATTERN — the headline block (user, Sep 22) ─────────
# the plate reads _disc.top_verdict() so it cites the same summary()
# her 20:00 line speaks; sections 1-5 ran without the attribute (the
# stub module never had it) — silence there is the fail-soft proof.
_real_topv = getattr(_disc, "top_verdict", None)
_disc.top_verdict = lambda: {"verdict":
                             "stayed too long - gave back over half the peak",
                             "usd_left": 64.78, "n": 6}
STUB.markdowns.clear()
_disc.load_state = lambda: {"history": [
    {"rot": "0-4", "score": 70.0}, {"rot": "DAY", **DAY}]}
render()
_patt = [m for m in STUB.markdowns if "tonight's pattern" in m]
check("headline renders with the DAY row", len(_patt) == 1, len(_patt))
_hp = _patt[-1] if _patt else ""
check("headline leads the plate (before the asset rows)",
      bool(_patt) and STUB.markdowns.index(_hp)
      < next(i for i, m in enumerate(STUB.markdowns) if "FARTCOIN" in m))
check("headline names the class short label", "stayed too long" in _hp)
check("headline carries the dollar cost", "$65" in _hp, "$64.78 rounds")
check("headline carries the trade count", "6 closed trades" in _hp)
check("subline carries the class reason",
      "gave back over half the peak" in _hp)
check("headline colors by the same leak table", "#ff6b5e" in _hp)

# ── 5c. TONIGHT'S PATTERN edge cases ────────────────────────────────
STUB.markdowns.clear()
_disc.top_verdict = lambda: None
render()
check("clean book -> no headline",
      not any("tonight's pattern" in m for m in STUB.markdowns))
STUB.markdowns.clear()
def _boom():
    raise RuntimeError("boom")
_disc.top_verdict = _boom
render()
check("broken helper fails soft (no raise escapes the plate)", True)
check("plate still renders the strip beside the broken headline",
      any("day by day" in m for m in STUB.markdowns))
if _real_topv is not None:
    _disc.top_verdict = _real_topv

# ── 5d. THE DAY PICK — tappable strip days (user, Sep 22) ───────────
_disc.top_verdict = lambda: None          # silence the headline here
HIST2 = [
    {"rot": "DAY", "date": "2026-09-18", "total_left": 12.0,
     "usd_left": {},
     "top_verdict": ("bought the top - the entry chased", 12.0)},
    {"rot": "DAY", "date": "2026-09-19", "total_left": 704.2,
     "usd_left": {"system_burial": 704.2, "missed": 0.0}},
    {"rot": "DAY", "date": "2026-09-20", "total_left": 0.0,
     "usd_left": {}, "top_verdict": None,
     "assets": [{"asset": "DOGE", "kind": "sold", "entry": 0.1,
                 "exit": 0.11, "peak": 0.115, "goal_pct": 2.0,
                 "realized": 5.0, "size": 500,
                 "verdict": "took the goal - the discipline shape",
                 "usd_left": 0.0}]},
    {"rot": "DAY", "date": "2026-09-21", "total_left": 50.0,
     "usd_left": {},
     "top_verdict": ("stayed too long - gave back over half the peak", 50.0),
     "assets": [{"asset": "WLD", "kind": "sold", "entry": 0.1,
                 "exit": 0.09, "peak": 0.12, "goal_pct": 1.5,
                 "realized": -20.0, "size": 1000,
                 "verdict": "stayed too long - gave back over half the peak",
                 "usd_left": 50.0}]},
]
_disc.load_state = lambda: {"history": [
    {"rot": "0-4", "score": 70.0}] + HIST2}

STUB.markdowns.clear()
STUB.pills_return = None
render()
check("pills called with the DAY dates oldest-first",
      STUB.pills_calls and STUB.pills_calls[-1]["options"]
      == ["2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21"],
      STUB.pills_calls[-1] if STUB.pills_calls else None)
check("pills default is the latest day",
      STUB.pills_calls[-1]["default"] == "2026-09-21")
check("pills carry the stable key",
      STUB.pills_calls[-1]["key"] == "review_day_pick")
check("no selection -> no drill-down rows",
      not any("DOGE" in m for m in STUB.markdowns))

STUB.markdowns.clear()
STUB.pills_return = "2026-09-20"
render()
check("picked day renders its own rows", any("DOGE" in m for m in STUB.markdowns))
check("latest-day rows still render beside the pick",
      any("WLD" in m for m in STUB.markdowns))
check("picked day with nothing left shows no breakdown",
      not any("$0 left on the table" in m for m in STUB.markdowns))

STUB.markdowns.clear()
STUB.pills_return = "2026-09-19"
render()
check("legacy day says it predates per-asset rows",
      any("2026-09-19 predates the per-asset rows" in m for m in STUB.markdowns))
check("legacy day names its dollar total",
      any("$704 left" in m for m in STUB.markdowns))

STUB.markdowns.clear()
STUB.pills_return = "2026-09-18"
render()
check("legacy day with a spoken verdict names the worst leak",
      any("worst leak: bought the top $12" in m for m in STUB.markdowns))

STUB.markdowns.clear()
STUB.pills_return = "2026-09-21"
render()
check("picking the latest day adds no drill-down",
      sum("WLD" in m for m in STUB.markdowns) == 1)

STUB.session_state = {"review_day_pick": "1999-01-01"}
STUB.markdowns.clear()
STUB.pills_return = None
render()
check("stale session key is cleared before the picker",
      "review_day_pick" not in STUB.session_state
      and STUB.pills_calls[-1]["options"][-1] == "2026-09-21")
STUB.session_state = None

STUB.pills = None   # instance shadow: getattr(st, "pills") -> None
STUB.markdowns.clear()
try:
    render()
    check("no pills widget -> plate still renders (guard)",
          any("WLD" in m for m in STUB.markdowns))
except Exception as e:
    check("no pills widget -> plate still renders (guard)", False, e)
del STUB.pills      # the class method shows through again
_disc.top_verdict = _real_topv if _real_topv is not None else (lambda: None)

print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
