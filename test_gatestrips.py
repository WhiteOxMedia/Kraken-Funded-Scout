"""Drive of the two new console surfaces (app.py):

1. the WATCHING card's TRAIL row — the enforced stop, computed from the
   scanner's own _ratchet_trail_pct + the latched goal-trail cap, per
   watched pair;
2. the MONEY LEFT ON THE TABLE strip's per-gate relief block — each
   gate's live journal_release_pts, ranked, fail-soft on a broken state.

The REAL function bodies are exec'd from app.py source (the repo's
headless pattern) into ONE shared globals namespace (so fixture
redirects bind), with st / modules stubbed into recorders and
slice-integrity asserted before anything runs.
"""
import sys
import types
from datetime import datetime, timedelta, timezone
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

# ── the scanner stub: _ratchet_trail_pct mirrors the LIVE re-base ──────
_bs = types.ModuleType("background_scanner")
_bs._ratchet_trail_pct = lambda entry, high, size: (
    1.5 if (high / entry - 1.0) * 100.0 >= 2.0
    else (2.0 if (high / entry - 1.0) * 100.0 >= 1.0 else 3.0))
_bs.CONTEST_TRAIL_PCT = 3.0
sys.modules["background_scanner"] = _bs

# ── the discipline stub: per-scenario state ─────────────────────────────
_disc = types.ModuleType("discipline")
_disc.load_state = lambda: {"burial_ledger": []}
_disc.journal_release_pts = lambda gate, now=None: 0.0
sys.modules["discipline"] = _disc

# ── slice the REAL function bodies from app.py ─────────────────────────
APP = Path(__file__).parent / "app.py"
src = APP.read_text(encoding="utf-8").replace("\r\n", "\n")
lines = src.split("\n")


def _extract_to(name):
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"def {name}("))
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("def "):
            end = j
            break
    return "\n".join(lines[start:end])


pick_src = _extract_to("_pick_card")
strip_src = _extract_to("_render_suppression_strip")

# slice integrity FIRST — a truncated slice silently proves nothing
check("slice: _pick_card reaches the trail block",
      "_ratchet_trail_pct" in pick_src and "TRAIL " in pick_src)
check("slice: _pick_card reaches the watch branch",
      "WATCH: you're in a trade" in pick_src)
check("slice: strip reaches the relief block",
      "RELEASE FUEL" in strip_src)
check("slice: _pick_card runs to its WAITING-plate tail (the true end)",
      "next pick lands here" in pick_src)

# ── ONE shared namespace: every name the bodies reference ──────────────
NOW = datetime.now(timezone.utc).astimezone()
NS = {
    "st": STUB,
    "datetime": datetime,
    "timedelta": timedelta,
    "timezone": timezone,
    "_now_mst": lambda: NOW,
    "_asset": lambda p: p.split("/")[0],
    "_fmt_price": lambda p: f"{p:,.5f}" if p < 10 else f"{p:,.2f}",
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
    "_load_watch": lambda: {},   # replaced per scenario below
}
exec(pick_src, NS)
exec(strip_src, NS)


# the REAL price formatter, sliced from app.py — the drive never
# re-implements house formatting it is asserting against
def _extract_fn(name):
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"def {name}("))
    end = next(j for j in range(start + 1, len(lines)) if lines[j].startswith("def "))
    return "\n".join(lines[start:end])


exec(_extract_fn("_fmt_price"), NS)
_pick_card = NS["_pick_card"]
_render_suppression_strip = NS["_render_suppression_strip"]

# ═══════════════════════════════════════════════════════════════════════
# 1. THE TRAIL ROW
# ═══════════════════════════════════════════════════════════════════════
WATCH_FIXTURE = {
    # peak +0.5% -> base 3.0% (ratchet not engaged yet)
    "AAA/USD": {"entry": 100.0, "high": 100.5, "size": 470.0,
                "goal_pct": 2.0, "added": NOW.isoformat(),
                "window_min": 90, "trail_armed": False, "why": ["x"]},
    # peak +1.4% -> 2.0%
    "BBB/USD": {"entry": 100.0, "high": 101.4, "size": 470.0,
                "goal_pct": 2.0, "added": NOW.isoformat(),
                "window_min": 90, "trail_armed": False, "why": ["x"]},
    # peak +2.5% -> 1.5%
    "CCC/USD": {"entry": 100.0, "high": 102.5, "size": 470.0,
                "goal_pct": 2.0, "added": NOW.isoformat(),
                "window_min": 90, "trail_armed": False, "why": ["x"]},
    # armed + peak +2.5%: cap = max(2.0*0.5, 1.0) = 1.0% beats the 1.5%
    "DDD/USD": {"entry": 100.0, "high": 102.5, "size": 470.0,
                "goal_pct": 2.0, "added": NOW.isoformat(),
                "window_min": 90, "trail_armed": True, "why": ["x"]},
}
NS["_load_watch"] = lambda: WATCH_FIXTURE

try:
    _pick_card(None, None)
except Exception as exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    check("_pick_card ran clean", False, repr(exc))
else:
    check("_pick_card ran clean", True)

trail_rows = [m for m in STUB.markdowns if "TRAIL " in m]
check("one trail row per watched pair", len(trail_rows) == 4, len(trail_rows))


def _row_for(asset):
    return next((m for m in trail_rows if f"{asset} " in m), "")


row_aaa, row_bbb = _row_for("AAA"), _row_for("BBB")
row_ccc, row_ddd = _row_for("CCC"), _row_for("DDD")

check("base band shows 3.0% (peak +0.5% below the +1% step)",
      "stop 3.0%" in row_aaa, row_aaa[:160])
check("ratchet engaged shows 2.0% (peak +1.4%)",
      "stop 2.0%" in row_bbb, row_bbb[:160])
check("ratchet engaged shows 1.5% (peak +2.5%)",
      "stop 1.5%" in row_ccc, row_ccc[:160])
check("armed cap beats the band (armed +2.5% peak -> 1.0%)",
      "stop 1.0%" in row_ddd, row_ddd[:160])
check("armed row says the net is set", "the net is set" in row_ddd)
check("disarmed rows never claim the net",
      bool(row_ccc) and "the net is set" not in row_ccc)
check("stop price rendered via the real formatter (100.5 * 0.97 -> $97.48)",
      "$97.48" in row_aaa, row_aaa[:220])

# ── the no-trail-yet case: a pair still at/below entry ─────────────────
WATCH_FIXTURE = {
    "EEE/USD": {"entry": 100.0, "high": 99.2, "size": 470.0,
                "goal_pct": 2.0, "added": NOW.isoformat(),
                "window_min": 90, "why": ["x"]},
}
STUB.markdowns.clear()
try:
    _pick_card(None, None)
except Exception as exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    check("below-entry case ran clean", False, repr(exc))
else:
    check("below-entry case ran clean", True)
eee = next((m for m in STUB.markdowns if "EEE" in m and "TRAIL " in m), "")
check("no-trail-yet line shows for a pair that never cleared entry",
      "none yet" in eee, eee[:160])

# ═══════════════════════════════════════════════════════════════════════
# 2. THE RELIEF BLOCK
# ═══════════════════════════════════════════════════════════════════════
import json as _json
import tempfile as _tf

_tmp = Path(_tf.mkdtemp())
(_tmp / "popup_suppressions.jsonl").write_text(
    "\n".join(_json.dumps({"ts": f"2026-09-21T1{i}:00:00-06:00",
                           "pair": f"P{i}/USD", "gate": g,
                           "detail": "d", "quick": 80.0})
              for i, g in enumerate(["tide-out", "deep-veto",
                                     "downward-trend"])),
    encoding="utf-8")
_rel = {"tide-out": 0.25, "deep-veto": 0.67, "downward-trend": 0.0}
_disc.journal_release_pts = lambda gate, now=None: _rel.get(gate, 0.0)
_disc.load_state = lambda: {"burial_ledger": [
    {"gates": {"tide-out": {"n": 2}, "deep-veto": {"n": 1}}, "duds": {}},
    {"gates": {"downward-trend": {"n": 3}}, "duds": {}}]}
NS["__file__"] = str(_tmp / "app.py")   # the strip reads its file live

STUB.markdowns.clear()
try:
    _render_suppression_strip()
except Exception as exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    check("strip ran clean", False, repr(exc))
else:
    check("strip ran clean", True)

rel_md = [m for m in STUB.markdowns if "RELEASE FUEL" in m]
check("relief block rendered once", len(rel_md) == 1, len(rel_md))
blk = rel_md[0] if rel_md else ""
check("gate with relief leads (deep-veto 0.67 before tide-out 0.25)",
      0 <= blk.find("deep-veto") < blk.find("tide-out"), blk[:200])
check("zero-relief gate present with 0.00 pts",
      "downward-trend" in blk and "0.00 pts" in blk)
check("relief numbers rendered", "0.67 pts" in blk and "0.25 pts" in blk)
check("explains the fuel contract",
      "near-bar champion" in blk and "closed" in blk)

# ── degraded state: a broken discipline state must not kill the block —
#    the log's gates are still readable, so they still render ──────────
STUB.markdowns.clear()


def _boom():
    raise RuntimeError("broken")


_disc.load_state = _boom
_disc.journal_release_pts = lambda gate, now=None: 0.5
try:
    _render_suppression_strip()
except Exception as exc:  # noqa: BLE001
    check("broken discipline state never raises", False, repr(exc))
else:
    _deg = next((m for m in STUB.markdowns if "RELEASE FUEL" in m), "")
    check("broken discipline state: block survives on the log's gates",
          bool(_deg))

# ── fail-soft: a broken journal call takes the whole block silent ──────
STUB.markdowns.clear()
_disc.load_state = lambda: {"burial_ledger": []}


def _boom_pts(gate, now=None):
    raise RuntimeError("broken pts")


_disc.journal_release_pts = _boom_pts
try:
    _render_suppression_strip()
except Exception as exc:  # noqa: BLE001
    check("broken journal call never raises", False, repr(exc))
else:
    check("broken journal call stays silent (fail-soft)",
          not any("RELEASE FUEL" in m for m in STUB.markdowns))

# ── the ease line: page level, ledger-driven, both states ──────────
STUB.markdowns.clear()
_rel = {"deep-veto": 0.52, "tide-out": 0.11, "downward-trend": 0.0}
_disc.journal_release_pts = lambda gate, now=None: _rel.get(gate, 0.0)
_disc.load_state = lambda: {"burial_ledger": [
    {"gates": {"deep-veto": {"n": 1}, "tide-out": {"n": 2},
               "downward-trend": {"n": 3}}, "duds": {}}]}
_render_suppression_strip()
_ez = next((m for m in STUB.markdowns if "asked to ease" in m), "")
check("ease line renders on the page when relief is live",
      bool(_ez) and "deep-veto 0.52 pts" in _ez and "tide-out 0.11 pts" in _ez,
      _ez[:140])
check("ease line sorts easing gates costliest first",
      0 <= _ez.find("deep-veto") < _ez.find("tide-out"), _ez[:140])
check("ease line does not name closed gates",
      "downward-trend" not in _ez, _ez[:140])

STUB.markdowns.clear()
_disc.journal_release_pts = lambda gate, now=None: 0.0
_render_suppression_strip()
_strict = next((m for m in STUB.markdowns if "funnel is strict" in m), "")
check("strict line renders when every gate is at zero", bool(_strict), _strict[:100])

# fail-soft: broken discipline state degrades to the strict line, never raises
STUB.markdowns.clear()
_disc.load_state = lambda: (_ for _ in ()).throw(RuntimeError("broken"))
try:
    _render_suppression_strip()
    check("broken state: ease line degrades without raising", True)
except Exception as exc:  # noqa: BLE001
    check("broken state: ease line degrades without raising", False, repr(exc))

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
