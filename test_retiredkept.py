"""THE RETIRED-KEPT DRIVE (Oct 3, the dead-body sweep): the fleet's
policy is RETIRE-BUT-KEEP (the don't-delete rule) — retired code stays
defined for the record and for revival. The _worth_actual_factor trap
(dcd7007) proved the cost: a kept function whose body referenced two
constants that never existed died into its own fail-soft except on
every call and returned a silent 1.0 — indistinguishable from a live
lever sitting at neutral, with no error anywhere. Nothing in the repo
executes retired code, so rot is invisible until the day someone
finally calls it.

This drive fences the whole class, four ways:
  1. THE REGISTRY: scanning every tracked production module for the
     retirement vocabulary (RETIRED / kept-per-don't-delete / no longer
     called / RETAINED as documentation) must surface EXACTLY the named
     seven — a new retired-kept function without a pin fails here, and
     a listed one deleted without a governance decision fails here too.
  2. COMPUTABILITY: every registered body EXECUTES (sandboxed where it
     reads live state) and meets its retirement contract behaviorally —
     the actual-factor pins are not the fail-soft 1.0 (the trap's exact
     fingerprint).
  3. THE ZERO-CALLER CONTRACT: no production module CALLS any registered
     entry (AST Name/Attribute loads, comments and docstrings excluded
     — the markers themselves are not callers). A silent revival —
     re-arming a retired lever without a governance decision — fails
     loudly.
  4. THE ROT CENSUS: fleet-wide, every bare Name load must bind
     somewhere in its module (globals(), stores, imports, params).
     Zero hits is the theorem dcd7007 proved by repair; this pin keeps
     it proved every run.
"""
import ast
import json
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


BUILTINS = set(dir(__builtins__)) | {
    "annotations", "__name__", "__file__", "__doc__", "__package__",
    "self", "cls", "Exception", "BaseException",
}

# THE REGISTRY (the sweep's verified output, Oct 3): every retired-kept
# def the markers name. SIX are zero-caller (kept for the record and
# revival); rotation_gate is the seventh and different: a RETIRED RULE
# whose SHELL production still deliberately calls (for the re-entry
# flag and the QC wording) — its contract is that the wiring is a
# decision, never an accident in either direction.
REGISTRY = [
    ("background_scanner", "_worth_catchup_factor"),
    ("background_scanner", "_worth_actual_factor"),
    ("background_scanner", "_popup_cap"),
    ("background_scanner", "_popup_cap_dollars"),
    ("app", "_recent_big_payout"),
    ("discipline", "_day_rot_fired"),
    ("background_scanner", "rotation_gate"),   # retired rule, LIVE shell
]
# the zero-caller contract's membership (rotation_gate has callers BY DESIGN)
ZERO_CALLER = set(REGISTRY) - {("background_scanner", "rotation_gate")}


def _tracked():
    """The gate sandbox has no .git — fall back to the root glob."""
    try:
        out = subprocess.run(["git", "ls-files", "*.py"], cwd=str(BASE),
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            names = [l.strip() for l in out.stdout.splitlines()
                     if l.strip() and "/" not in l
                     and not Path(l).name.startswith(("test_", "diag_"))
                     and Path(l).name != "scratch_space.py"]
            if names:
                return names
    except Exception:
        pass
    return sorted(p.name for p in BASE.glob("*.py")
                  if not p.name.startswith(("test_", "diag_"))
                  and p.name != "scratch_space.py")


def _marker_scan():
    """Every module-level def whose docstring carries the retirement
    vocabulary: the fleet's retired-kept record as the SOURCE tells it."""
    found = []
    for name in _tracked():
        p = BASE / name
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(n) or ""
                first = doc.splitlines()[0].lower() if doc else ""
                # a retirement marker is the body's SELF-DECLARATION —
                # the first line. Historical mentions mid-docstring
                # ("replaces the retired ...", "the retired switches")
                # are LIVE functions telling their own story, not
                # retired code.
                if (first.startswith("retired")
                        or first.startswith("retained")):
                    found.append((name.replace(".py", ""), n.name))
    return sorted(found)


def _prod_callers(module, func):
    """AST production references to func outside its own def: bare Name
    loads in other modules (or in this module outside the def body) and
    dotted attribute loads from any import site. Comments and docstrings
    never count — the retirement markers are not callers."""
    own = None
    try:
        tree = ast.parse((BASE / f"{module}.py").read_text(encoding="utf-8"))
    except Exception:
        tree = None
    if tree is not None:
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and n.name == func:
                own = {id(x) for x in ast.walk(n)}
                break
    hits = []
    for name in _tracked():
        p = BASE / name
        try:
            t = ast.parse(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        mod = name.replace(".py", "")
        for n in ast.walk(t):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) \
                    and n.id == func:
                if mod == module and own and id(n) in own:
                    continue   # the body's own internals are not callers
                hits.append(f"{mod}:{n.lineno}")
            elif isinstance(n, ast.Attribute) and n.attr == func:
                hits.append(f"{mod}:{n.lineno}")
    return hits


# ── 1. THE REGISTRY IS THE FLEET'S FULL RETIRED-KEPT RECORD ─────────
scan = _marker_scan()
check("the marker scan surfaces EXACTLY the registered seven (a new "
      "retired-kept function without a pin fails here; a listed one "
      "deleted without a governance decision fails here too)",
      scan == sorted(REGISTRY), scan)

# ── 2. COMPUTABILITY (each body executes and meets its contract) ────
import background_scanner as bs

# the retired rule with a LIVE shell: the wiring is a decision
_g = bs.rotation_gate("SWEEP-PROBE/USD", confirm_window_min=0.0)
check("RETIRED rotation_gate still computes and its retirement shape "
      "holds in any hour: always allows, the fine is dead (True, "
      "0.0)",
      bool(_g) and len(_g) == 4 and _g[0] is True and _g[3] == 0.0, _g)
check("RETIRED rotation_gate's shell stays DELIBERATELY wired (>= 1 "
      "production caller — a silent disconnection fails; removing the "
      "shell is a governance decision caught by the registry pin)",
      len(_prod_callers("background_scanner", "rotation_gate")) >= 1,
      _prod_callers("background_scanner", "rotation_gate"))

check("RETIRED _worth_catchup_factor computes: the stub body returns "
      "exactly 1.0 (the daily lock it read is gone; the original math "
      "lives in git history)",
      bs._worth_catchup_factor() == 1.0, bs._worth_catchup_factor())

# bs._popup_cap / _popup_cap_dollars: the fail-soft readers, live shape
_cap = bs._popup_cap()
check("RETIRED _popup_cap computes: int >= 1 (the live console_state "
      "read, the max(1, ...) floor holds)",
      isinstance(_cap, int) and _cap >= 1, _cap)
_dcap = bs._popup_cap_dollars()
check("RETIRED _popup_cap_dollars computes: float at or above the "
      "$20 fail-open floor (the size-adjusted ceiling machinery is "
      "alive under the retirement)",
      isinstance(_dcap, float) and _dcap >= 20.0, _dcap)

# app._recent_big_payout: the retirement contract VERBATIM — always None
try:
    import app as _app
    check("RETIRED app._recent_big_payout computes: always None (no "
          "payout record ever blocks or hides a candidate — the "
          "guard is retired, the call cannot crash)",
          _app._recent_big_payout("ZEC/USD", now_ts=time.time()) is None)
except Exception as exc:
    check("RETIRED app._recent_big_payout is importable and callable",
          False, repr(exc))

# discipline._day_rot_fired: the documentation body executes on a
# well-formed slate (the None-history shape is not its contract)
import discipline as _disc
check("RETIRED discipline._day_rot_fired computes: a no-fire date reads "
      "False (the wrong heuristic stays runnable as documentation — "
      "an EMPTY date makes every voice row match, which is exactly "
      "why it's documented as wrong)",
      _disc._day_rot_fired({"date": "1999-01-01"}) is False)

# bs._worth_actual_factor: the exemplar trap — the body must do REAL
# math (not the fail-soft 1.0) on a sandboxed healthy-10k slate
import contest_account as ca
import copy as _copy

_h = ca.load_state()
_a_bak = _copy.deepcopy(_h)
_cache_bak = dict(bs._CONTEST_SIZE_CACHE)
_path_bak = ca.STATE_FILE
_realized_bak = ca.realized_pnl_today
_sbx = BASE / "scratch" / f"_retiredkept_actual_sbx.{__import__('os').getpid()}.json"
_sbx.parent.mkdir(parents=True, exist_ok=True)
from datetime import datetime
from zoneinfo import ZoneInfo
_now_a = datetime(2026, 10, 1, 9, 15, tzinfo=ZoneInfo("America/Denver"))
try:
    # the same seam the worthdial drive certified: STATE_FILE into
    # scratch, realized stubbed, the 30s size cache busted
    ca.STATE_FILE = _sbx
    ca.realized_pnl_today = lambda *_a, **_k: 0.0
    _sbx.write_text(json.dumps({
        "tier": _a_bak.get("tier"),
        "start_balance": _a_bak.get("start_balance"),
        "current_balance": _a_bak.get("start_balance")}),
        encoding="utf-8")
    bs._CONTEST_SIZE_CACHE["size"] = 0.0
    _f = bs._worth_actual_factor(now=_now_a)
    _pace = float(ca.daily_pace_usd(_a_bak))
    _hl = max(bs.WORTH_ACTUAL_HALFLIFE_USD,
              float(_a_bak["start_balance"]) * 0.12)
    _exp = max(bs.WORTH_ACTUAL_MIN,
               min(bs.WORTH_ACTUAL_MAX,
                   1.0 + bs.WORTH_ACTUAL_SWING * (_pace / _hl)))
    check("RETIRED _worth_actual_factor COMPUTES the size-aware "
          "halflife tilt on a healthy 10k — first-principles match, "
          "NOT the silent 1.0 the trap shipped",
          abs(_f - _exp) < 1e-9 and _f != 1.0, (_f, _exp))
    check("RETIRED _worth_actual_factor is a TILT, never a quota "
          "(clamped [0.85, 1.15])",
          bs.WORTH_ACTUAL_MIN <= _f <= bs.WORTH_ACTUAL_MAX, _f)
finally:
    ca.realized_pnl_today = _realized_bak
    ca.STATE_FILE = _path_bak
    _sbx.unlink(missing_ok=True)
    bs._CONTEST_SIZE_CACHE.clear()
    bs._CONTEST_SIZE_CACHE.update(_cache_bak)

# ── 3. THE ZERO-CALLER CONTRACT (a silent revival fails loudly) ─────
for mod, fn in sorted(ZERO_CALLER):
    hits = _prod_callers(mod, fn)
    check(f"RETIRED {mod}.{fn} has ZERO production callers (reviving "
          "a retired lever is a governance decision, never a drive-by)",
          not hits, hits)

# ── 4. THE FLEET ROT CENSUS (dcd7007's class, fenced forever) ───────
rot = []
for name in _tracked():
    p = BASE / name
    try:
        src = p.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except Exception:
        continue
    ns = set(BUILTINS)
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            ns.add(n.id)
        elif isinstance(n, ast.arg):
            ns.add(n.arg)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                            ast.ClassDef)):
            ns.add(n.name)
        elif isinstance(n, ast.alias):
            ns.add((n.asname or n.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            ns.add(n.name)
        elif isinstance(n, ast.comprehension):
            for t in ast.walk(n.target):
                if isinstance(t, ast.Name):
                    ns.add(t.id)
        elif isinstance(n, ast.withitem) and n.optional_vars:
            for t in ast.walk(n.optional_vars):
                if isinstance(t, ast.Name):
                    ns.add(t.id)
    for n in ast.walk(tree):
        if (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                and n.id not in ns):
            rot.append(f"{name}:{n.lineno} {n.id}")
check("the fleet's bare-load rot census is ZERO (every bare Name load "
      "binds somewhere in its module — the _worth_actual_factor class "
      "cannot recur silently)",
      not rot, rot)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
import scratch_space
scratch_space.report_temp('_retiredkept_test.txt').write_text(
    "\n".join(lines), encoding="utf-8")

print(f"RESULT: {PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
