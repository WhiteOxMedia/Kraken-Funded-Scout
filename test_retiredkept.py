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
  4. THE ROT CENSUS: fleet-wide (root AND nested modules), every bare
     Name load must bind on its LEXICAL CHAIN — scope-honest (a Store
     in one function never binds a Load in another; class scopes do
     not nest into methods; comprehensions resolve their own targets).
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
    """Every tracked production module — ROOT AND NESTED (ops/, trend/,
    ui/: the gate's [medium] on the first cut dropped every path with a
    separator, silently excluding them from the registry and the census
    despite the fleet-wide claim). The gate sandbox has no .git — fall
    back to a recursive glob with the same exclusions."""
    try:
        out = subprocess.run(["git", "ls-files", "*.py"], cwd=str(BASE),
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            names = [l.strip() for l in out.stdout.splitlines()
                     if l.strip()
                     and "scratch/" not in l and "/tests/" not in l
                     and not Path(l).name.startswith(("test_", "diag_"))
                     and not Path(l).name.endswith("_test.py")
                     and Path(l).name != "scratch_space.py"]
            if names:
                return names
    except Exception:
        pass
    return sorted(
        str(p.relative_to(BASE)).replace("\\", "/")
        for p in BASE.rglob("*.py")
        if "scratch" not in p.parts and "tests" not in p.parts
        and "__pycache__" not in p.parts
        and not p.name.startswith(("test_", "diag_"))
        and not p.name.endswith("_test.py")
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
# SCOPE-HONEST (the gate's [medium] on the pooled version): a Store in
# one function never binds a Load in another. Python's real rules,
# deliberately: functions chain past CLASS scopes (a method cannot see
# the class body's names); comprehensions are their own scope with the
# FIRST iterable evaluated in the ENCLOSING scope and a walrus bound in
# the nearest enclosing non-comprehension scope; global/nonlocal
# declarations route ASSIGNMENTS, never loads (a global-decl'd load
# resolves in the MODULE NAMESPACE — bound only if a store actually
# reached it); a def-time annotation is visited in its enclosing scope
# (unless the module defers annotations, PEP 563); a load may legally
# precede the store that binds it (the deferred single-pass resolution
# — the census fails OPEN, the safe direction); `import *` blinds the
# chain — its OWN scope included — and the blinded module is surfaced
# in the detail, never silently ignored.
_MATCH_NODES = tuple(
    getattr(ast, n) for n in ("MatchAs", "MatchStar", "MatchMapping")
    if hasattr(ast, n))


def _rot_census(relname, tree):
    """Returns (rot_hits, blinded) — the unbindable bare loads and
    whether a star import made any scope's chain unverifiable."""
    loads = []   # (scope, lineno, name)

    def _child(scope, kind):
        return {"kind": kind, "stores": set(), "global_decl": set(),
                "nonlocal_decl": set(), "star": False, "parent": scope}

    def _store(scope, name):
        tgt = scope
        while tgt["kind"] == "comp":     # the walrus-in-comprehension rule
            tgt = tgt["parent"]
        tgt["stores"].add(name)
        if tgt["kind"] == "module":      # a store that reached the module namespace
            mods["gstores"].add(name)

    def _params(a, ch):
        for p in [*a.posonlyargs, *a.args, *a.kwonlyargs] + \
                 ([a.vararg] if a.vararg else []) + \
                 ([a.kwarg] if a.kwarg else []):
            ch["stores"].add(p.arg)

    def visit(node, scope):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _store(scope, node.name)
            for d in node.decorator_list:
                visit(d, scope)
            for d in (node.args.defaults
                      + [d for d in node.args.kw_defaults if d is not None]):
                visit(d, scope)          # def-time: the enclosing scope
            if not mods["future"]:
                # annotations evaluate AT the def — the Oct 3 review's
                # hole: an undefined name there raises on import, in a
                # body that never runs. Deferred (PEP 563) they are
                # strings and nothing evaluates.
                for a in (node.args.posonlyargs + node.args.args
                          + node.args.kwonlyargs
                          + ([node.args.vararg] if node.args.vararg else [])
                          + ([node.args.kwarg] if node.args.kwarg else [])):
                    if a.annotation is not None:
                        visit(a.annotation, scope)
                if node.returns:
                    visit(node.returns, scope)
            ch = _child(scope, "function")
            _params(node.args, ch)
            for s in node.body:
                visit(s, ch)
            return
        if isinstance(node, ast.Lambda):
            for d in (node.args.defaults
                      + [d for d in node.args.kw_defaults if d is not None]):
                visit(d, scope)
            ch = _child(scope, "function")
            _params(node.args, ch)
            visit(node.body, ch)
            return
        if isinstance(node, ast.ClassDef):
            _store(scope, node.name)
            for d in (node.decorator_list + node.bases
                      + [k.value for k in node.keywords]):
                visit(d, scope)
            ch = _child(scope, "class")
            for s in node.body:
                visit(s, ch)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                             ast.GeneratorExp)):
            gens = node.generators
            visit(gens[0].iter, scope)   # the first iterable LEAKS out
            ch = _child(scope, "comp")
            for i, g in enumerate(gens):
                if i:
                    visit(g.iter, ch)
                for t in ast.walk(g.target):
                    if isinstance(t, ast.Name):
                        ch["stores"].add(t.id)
                for cond in g.ifs:
                    visit(cond, ch)
            if isinstance(node, ast.DictComp):
                visit(node.key, ch)
                visit(node.value, ch)
            else:
                visit(node.elt, ch)
            return
        if isinstance(node, ast.Global):
            scope["global_decl"].update(node.names)
            return
        if isinstance(node, ast.Nonlocal):
            scope["nonlocal_decl"].update(node.names)
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for al in node.names:
                if al.name == "*":
                    scope["star"] = True   # the chain goes blind past here
                else:
                    scope["stores"].add(
                        (al.asname or al.name).split(".")[0])
            if (isinstance(node, ast.ImportFrom) and node.module == "__future__"
                    and any(al.name == "annotations" for al in node.names)):
                mods["future"] = True
            return
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Store):
                _store(scope, node.id)
                if node.id in scope["global_decl"]:
                    mods["gstores"].add(node.id)   # the store lands in the module namespace
            elif isinstance(node.ctx, ast.Load):
                loads.append((scope, node.lineno, node.id))
            return
        if isinstance(node, ast.ExceptHandler) and node.name:
            scope["stores"].add(node.name)
        if _MATCH_NODES and isinstance(node, _MATCH_NODES):
            for attr in ("name", "name", "rest"):
                v = getattr(node, attr, None)
                if v:
                    scope["stores"].add(v)
                break
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    mods = _child(None, "module")
    mods["future"] = False    # PEP 563: annotations become strings
    mods["gstores"] = set()   # names with a store that REACHED the module namespace
    visit(tree, mods)

    def _chain(scope):
        if scope["star"]:
            return None             # blinded — fail open, surface it
        names = set(BUILTINS) | scope["stores"]
        gdecl = set(scope["global_decl"])   # assignment routing, not a binding
        p = scope["parent"]
        while p is not None:
            if p["kind"] == "class":    # class scopes do not nest
                p = p["parent"]
                continue
            if p["star"]:
                return None             # blinded — fail open, surface it
            names |= p["stores"]
            gdecl |= p["global_decl"]
            p = p["parent"]
        return names, gdecl

    rot, blinded = [], False
    for scope, ln, nm in loads:
        got = _chain(scope)
        if got is None:
            blinded = True
            continue
        names, gdecl = got
        if nm in names:
            continue
        if nm in gdecl and nm in mods["gstores"]:
            continue    # a global-decl'd load: bound only if a store reached the module namespace
        rot.append(f"{relname}:{ln} {nm}")
    return rot, blinded


rot, _blinded = [], False
for name in _tracked():
    p = BASE / name
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except Exception:
        continue
    r, bl = _rot_census(name, tree)
    rot.extend(r)
    _blinded = _blinded or bl
check("the fleet's bare-load rot census is ZERO, scope-honest (a "
      "Store in one function never binds a Load in another — the "
      "_worth_actual_factor class cannot recur silently; a star-import "
      "module would surface here as blinded, never silently)",
      not rot, rot or ("blinded-by-import-* (unverifiable, disclosed): "
                       + str(_blinded)))

# THE CENSUS'S OWN SEMANTICS, PINNED (the gate's [medium] on the pooled
# first cut: pooled Store bags masked exactly the shapes below). A
# synthetic module proves each scoping rule the census implements.
import textwrap


def _census_of(src):
    return _rot_census("<synthetic>",
                       ast.parse(textwrap.dedent(src)))

_r, _b = _census_of("""
    def a():
        x = 1
    def b():
        return x            # a's store must NOT bind b's load
    """)
check("census semantics: a Store in one function never binds a Load "
      "in another (the pooled-mask shape, planted)",
      _r == ["<synthetic>:5 x"], _r)

_r, _b = _census_of("""
    class C:
        attr = 1
        def m(self):
            return attr     # class-body names do not reach methods
    """)
check("census semantics: a class body's name never binds inside its "
      "methods (class scopes do not nest)",
      _r == ["<synthetic>:5 attr"], _r)

_r, _b = _census_of("""
    def f(xs):
        y = 1
        return [z for z in xs] + [y]   # y binds in; z stays in the comp
    """)
check("census semantics: comprehension targets stay in the "
      "comprehension; the enclosing function's names still reach in",
      not _r and not _b, (_r, _b))

_r, _b = _census_of("""
    def f():
        return [i for i in xs]      # the FIRST iterable evaluates OUTSIDE
    """)
check("census semantics: the first iterable resolves in the ENCLOSING "
      "scope (an unbound one is rot, even though the comp's own target "
      "binds fine)",
      _r == ["<synthetic>:3 xs"], _r)

_r, _b = _census_of("""
    def f(xs):
        return [n for x in xs if (n := x)]   # walrus binds OUT of the comp
    """)
check("census semantics: a walrus inside a comprehension binds in the "
      "nearest enclosing non-comprehension scope",
      not _r and not _b, (_r, _b))

_r, _b = _census_of("""
    counter = 0
    def bump():
        global counter
        counter = counter + 1
        return counter
    """)
check("census semantics: a global declaration re-binds on the chain "
      "(loads resolve through it)",
      not _r and not _b, (_r, _b))

_r, _b = _census_of("""
    def f():
        return later()         # the deferred single-pass load is LEGAL
    def later():
        return 1
    """)
check("census semantics: a load may legally precede the store that "
      "binds it (the census fails OPEN, the safe direction)",
      not _r and not _b, (_r, _b))

_r, _b = _census_of("""
    from os import *           # the chain goes blind past this line
    def f():
        return definitely_not_a_real_name
    """)
check("census semantics: a star import BLINDS the chain and is "
      "surfaced as unverifiable — never silently ignored, never "
      "silently passed",
      not _r and _b is True, (_r, _b))

# the Oct 3 review's three holes in the hardened census, each pinned
# red-first against the b48f858 version
_r, _b = _census_of("""
    def f():
        global q
        return q            # the decl governs ASSIGNMENTS, not loads
    """)
check("census semantics: `global q` alone binds NOTHING — a bare load "
      "of a declared-but-never-stored-anywhere name still raises "
      "NameError at runtime (LOAD_GLOBAL finds no module binding)",
      _r == ["<synthetic>:4 q"], _r)

_r, _b = _census_of("""
    def f(x: Undefined) -> AlsoMissing:
        return x
    """)
check("census semantics: argument and return annotations evaluate AT "
      "THE def — undefined names there are rot even though the body "
      "never runs",
      _r == ["<synthetic>:2 Undefined", "<synthetic>:2 AlsoMissing"], _r)

_r, _b = _census_of("""
    from os import *
    result = definitely_not_a_real_name
    """)
check("census semantics: a star import blinds its OWN scope too — the "
      "module-level load is unverifiable (blinded, disclosed), never "
      "flagged as rot",
      not _r and _b is True, (_r, _b))

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
import scratch_space
scratch_space.report_temp('_retiredkept_test.txt').write_text(
    "\n".join(lines), encoding="utf-8")

print(f"RESULT: {PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
