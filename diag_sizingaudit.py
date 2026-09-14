"""AST AUDIT v4 — precise read-before-assign, UPGRADED (Sep 13) to the
diag_stshadow scope-walking machinery and focused on the sizing-sensitive
gates (the worth-tilt family, the cap-dollar ceiling, the danger zone),
EXTENDED (Sep 13, later) to the bot's order-sizing path: the engine's
_Execute_buy arithmetic, the runner's tick loop that drives it, and the
bridge's position_size_usd seam that carries the scanner's sizing
decision to the bot. An UnboundLocalError there is not a missed popup —
it is a mis-sized real order.

v3 rules, kept: parameters count as stores; nested defs and comprehensions
are their own scopes; `global`/`nonlocal` names are excluded (reading them
is legitimate); events are statement-ordered. Writes _sizingaudit.txt.

v4 additions, borrowed from diag_stshadow:
  1. IMPORT ALIASES bind: `import background_scanner as _bs` stores _bs
     at that line. v3 was blind to it, so every function-local
     import-then-hasattr idiom false-positived (the 14-candidate noise
     floor on app.py — all the same shape, all gone).
  2. WALRUS targets count as STOREs, at their own line, in walk order
     (st-shadow's NamedExpr rule — v4's order logic sees them).
  3. CALL-AWARE gate pass: inside the sizing gates, the FIRST read of a
     name must not sit inside a call argument that evaluates before the
     name's first store. `g(pair, spend)` at the top and
     `spend = f(x)` below is a REAL UnboundLocalError even when a later
     store exists — exactly the send_alert `_bal` disease. The whole-file
     pass keeps the v3 semantics (any store before a read suffices) so
     its noise floor stays low; the gate pass is the sharp instrument.

THE GATES (grep-verified live call paths):
  worth tilt   _worth_pace_factor, _worth_catchup_factor,
               _worth_actual_factor, _worth_buffer_factor,
               worth_floor_auto_factors, _worth_floor_frac,
               _sure_thing_goal_tilt, _market_goal_pct
  cap ceiling  _recent_fired_popups_dollars, _climb_out_check
  danger zone  _danger_zone_panel
  plus contest_account.load_state / max_entry_usd / window_drawdown_pct /
  death_line (the 3% guard's sizing base and the guard that never yields)

Run: .venv\\Scripts\\python.exe -X utf8 diag_sizingaudit.py
"""
import ast
from itertools import count
from pathlib import Path

ROOT = Path(__file__).parent
FILES = ["background_scanner.py", "app.py", "contest_account.py",
         "volatility_engine.py", "signal_bridge.py"]

# ── the bot fleet (sibling dir — the health-check convention) ─────────
BOT_ROOT = ROOT.parent / "kraken_xrp_trading_bot"
BOT_FILES = ["bot_engine.py", "bot_runner.py"]

# ── the sizing-sensitive gates (Sep 13 audit focus) ──────────────────
SIZING_GATE_FILE = "background_scanner.py"
SIZING_GATES = (
    # worth tilt — the floor's auto-dial family + the Sure-Thing tilt
    "_worth_pace_factor", "_worth_catchup_factor", "_worth_actual_factor",
    "_worth_buffer_factor", "worth_floor_auto_factors", "_worth_floor_frac",
    "_sure_thing_goal_tilt", "_market_goal_pct",
    # cap ceiling — the dollar cap on fresh-entry money + the climb-out
    # strictness wrapper that feeds the danger-zone math
    "_recent_fired_popups_dollars", "_climb_out_check",
    # danger zone — the popup's drawdown / distance / impact block
    "_danger_zone_panel",
    # the 3% guard's helpers (sizing base + the guard that never yields)
    "load_state", "max_entry_usd", "window_drawdown_pct", "death_line",
)
SIZING_GATE_FILE_2 = "contest_account.py"

# ── the bot's order-sizing path (Sep 13 extension) ────────────────────
BOT_GATE_FILE = "bot_engine.py"
BOT_GATES = (
    # the engine's order sizing: the carry cap that clamps intended
    # dollars, the minimum-volume floor, and the _execute_buy arithmetic
    # that turns signal size into a real order volume
    "apply_carry_cap", "_min_order_size", "_execute_buy",
)
BOT_GATE_FILE_2 = "bot_runner.py"
BOT_GATE_2 = ("main",)        # the tick loop that drives run_tick
BRIDGE_GATE_FILE = "signal_bridge.py"
BRIDGE_GATE = ("write_buy_signal",)   # the position_size_usd seam

lines = []


def out(s=""):
    print(s)
    lines.append(s)


# ══ the st-shadow scope-walking upgrades ══════════════════════════════

class WalrusStoreVisitor(ast.NodeVisitor):
    """Every NamedExpr target is a STORE in the scope that contains it.
    (v3 walked the Name inside the walrus with expr ctx — invisible.)"""

    def __init__(self):
        self.stores = []

    def visit_NamedExpr(self, node):
        t = node.target
        if isinstance(t, ast.Name):
            self.stores.append((t.id, t.lineno))
        self.generic_visit(node)


def _walrus_stores(node):
    v = WalrusStoreVisitor()
    v.visit(node)
    return v.stores


def collect_scope(fn):
    """(seq, kind, name, lineno) for ONE function scope, source order.
    Params = stores. Nested def bodies are other scopes (skipped, except
    their def-expression Name is none of ours). global/nonlocal names
    excluded — reading them is legitimate. v4: walrus targets are
    STOREs, at their own line, in walk order."""
    events = []
    excluded = set()
    for n in ast.walk(fn):
        if isinstance(n, (ast.Global, ast.Nonlocal)):
            excluded.update(n.names)

    def params(f):
        a = f.args
        for p in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
            events.append((0, "STORE", p.arg, f.lineno))
        if a.vararg:
            events.append((0, "STORE", a.vararg.arg, f.lineno))
        if a.kwarg:
            events.append((0, "STORE", a.kwarg.arg, f.lineno))

    params(fn)
    seq = count(1)

    def descend(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue   # nested scope — its own analysis
            if isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp,
                                  ast.GeneratorExp)):
                continue   # comprehension scope — targets don't leak out
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                # v4 (st-shadow lesson): an import alias IS a store —
                # `import background_scanner as _bs` binds _bs at this
                # line. v3 couldn't see it, so every function-local
                # import-then-hasattr idiom false-positived as
                # read-before-assign (the 14-candidate noise floor).
                for al in child.names:
                    nm = al.asname or al.name.split(".")[0]
                    if nm not in excluded:
                        events.append((next(seq), "STORE", nm,
                                       child.lineno))
                continue
            if isinstance(child, ast.NamedExpr):
                for nm, ln in _walrus_stores(child):
                    if nm not in excluded:
                        events.append((next(seq), "STORE", nm, ln))
                continue
            if isinstance(child, ast.Name):
                if child.id in excluded:
                    continue
                kind = "STORE" if isinstance(child.ctx, ast.Store) else "LOAD"
                events.append((next(seq), kind, child.id, child.lineno))
            descend(child)

    descend(fn)
    return events


# ══ the gate-focused, call-aware pass (the sharp instrument) ══════════

class SizingCallVisitor(ast.NodeVisitor):
    """Calls with a Name callee, in walk order, with each call's
    argument (and keyword) reads — the raw material for the
    call-aware flow check inside the sizing gates."""

    def __init__(self):
        self.calls = []       # (lineno, func_name, [arg Name reads])

    def visit_Call(self, node):
        fname = None
        if isinstance(node.func, ast.Name):
            fname = node.func.id
        elif isinstance(node.func, ast.Attribute):
            fname = node.func.attr
        reads = []
        for a in node.args:
            for n in ast.walk(a):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                    reads.append((n.id, n.lineno))
        for kw in node.keywords:
            for n in ast.walk(kw.value):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                    reads.append((n.id, n.lineno))
        self.calls.append((node.lineno, fname, reads))
        self.generic_visit(node)


def _qualified_loads(fn, roots):
    """(lineno, 'root.attr') for every qualified LOAD whose ROOT is a
    Name in `roots` — module-handles (e.g. `_ca.load_state(...)`) read
    the module, not the name, so their `.attr` names never count as
    bare reads of `load_state` etc."""
    out_ = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            if n.value.id in roots:
                out_.append((n.lineno, f"{n.value.id}.{n.attr}"))
    return out_


def audit_gates(files=None):
    """Per sizing gate: the v4 call-aware read-before-assign check.
    files: optional [(fname, gate_names, root)] override for hermetic
    selftests. Returns findings [(file, func, line, name, why)]."""
    findings = []
    roster = files or (
        (SIZING_GATE_FILE, SIZING_GATES, ROOT),
        (SIZING_GATE_FILE_2, SIZING_GATES, ROOT),
        (BOT_GATE_FILE, BOT_GATES, BOT_ROOT),
        (BOT_GATE_FILE_2, BOT_GATE_2, BOT_ROOT),
        (BRIDGE_GATE_FILE, BRIDGE_GATE, ROOT),
    )
    for fname, names, root in roster:
        p = root / fname
        if not p.exists():
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in names:
                continue
            # ── pass A: the v4 order check (walrus-aware) ──
            events = collect_scope(node)
            stores = {}
            for s, k, n, ln in events:
                if k == "STORE":
                    stores.setdefault(n, []).append((s, ln))
            for s, k, n, ln in sorted(events, key=lambda e: e[0]):
                if k != "LOAD":
                    continue
                st = stores.get(n)
                if not st:
                    continue
                first_store = min(st)
                if s < first_store[0]:
                    findings.append((fname, node.name, ln, n,
                                     f"READ before first STORE "
                                     f"(store at line {first_store[1]})"))
            # ── pass B: call-aware — a read feeding a call ABOVE the
            #    first plain STORE of the same name is a real
            #    UnboundLocalError even though a later store exists ──
            sv = SizingCallVisitor()
            sv.visit(node)
            # every STORE the scope makes — params, assigns, walrus
            # (collect_scope v4 emits all three as STORE events)
            all_stores = {}
            for s, k, n, ln in events:
                if k == "STORE":
                    all_stores.setdefault(n, []).append(ln)
            for ln, callee, reads in sv.calls:
                for nm, rln in reads:
                    sl = all_stores.get(nm)
                    if not sl:
                        continue
                    if rln < min(sl):
                        findings.append(
                            (fname, node.name, rln, nm,
                             f"call-arg READ feeding {callee}() at line "
                             f"{ln} before first STORE (line {min(sl)}) "
                             f"— UnboundLocalError at fire time"))
    return findings


# ══ v3 whole-file pass, walrus-aware ══════════════════════════════════

def audit_file(fname, root=None):
    p = (root or ROOT) / fname
    if not p.exists():
        return 0
    tree = ast.parse(p.read_text(encoding="utf-8"))
    problems = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        events = collect_scope(node)
        stores = {}
        for s, k, n, ln in events:
            if k == "STORE":
                stores.setdefault(n, []).append((s, ln))
        for s, k, n, ln in sorted(events, key=lambda e: (e[0])):
            if k != "LOAD":
                continue
            st = stores.get(n)
            if not st:
                continue   # never stored here -> module global/builtin: fine
            first_store = min(st)
            if s < first_store[0]:
                problems += 1
                out(f"  {fname}:{ln}  '{n}' READ before first STORE "
                    f"(store at line {first_store[1]}) in {node.name}")
    return problems


def selftest():
    """Aggregate verdict over every hermetic fixture (see
    SELFTEST_FIXTURES / selftest_cases)."""
    cases = selftest_cases()
    ok = all(c[3] for c in cases)
    detail = "; ".join(f"{nm}={'PASS' if cok else 'FAIL'} ({det})"
                       for nm, _lbl, _f, cok, det in cases)
    return detail, ok


# ── the permanent hermetic canon (Sep 13): every fixture the gate must
#    judge forever, each in its own sandboxed file, each with its own
#    predicate. The battery pins them BY NAME — a new fixture is
#    visible there, a regression names its case, and no case can hide
#    inside a single aggregate boolean. ──────────────────────────────
SELFTEST_FIXTURES = (
    # the disease itself: a call argument reads a local above its only
    # store — the send_alert _bal shape, a real UnboundLocalError
    ("fx_ule", SIZING_GATES,
     "def _danger_zone_panel(exchange, pair, goal_pct):\n"
     "    worst = impact(pair, spend)   # spend read pre-store\n"
     "    spend = balance()\n"
     "    return worst, spend\n",
     lambda finds: any("UnboundLocalError" in w for *_r, w in finds),
     "the planted call-arg ULE flags at the call argument"),
    # parameters ARE stores: the same text with spend as a param is clean
    ("fx_param", SIZING_GATES,
     "def _danger_zone_panel(exchange, pair, spend, goal_pct):\n"
     "    worst = impact(pair, spend)  # param IS a store\n"
     "    spend = balance()\n"
     "    return worst, spend\n",
     lambda finds: not finds,
     "params-as-stores stay quiet"),
    # function-local import aliases bind at the import line — the
    # import-then-hasattr idiom must never read as read-before-assign
    ("fx_import", ("_worth_pace_factor",),
     "def _worth_pace_factor():\n"
     "    import volatility_engine as _ve\n"
     "    if not hasattr(_ve, 'x'):\n"
     "        import importlib\n"
     "        _ve = importlib.reload(_ve)\n"
     "    return _ve.x\n",
     lambda finds: not finds,
     "import-alias bindings stay quiet"),
    # walrus targets are stores at their own position: Python evaluates
    # the condition first, so the walrus BINDS before the body's read
    ("fx_walrus", SIZING_GATES,
     "def _worth_buffer_factor():\n"
     "    total = spend() * 2 if (spend := budget()) else 0.0\n"
     "    return total\n",
     lambda finds: not finds,
     "walrus targets count as stores (condition binds first)"),
    # qualified module-handle reads (`_ca.load_state(...)`) read the
    # MODULE, not the bare name — the 3% guard's helper idiom is clean
    ("fx_module", SIZING_GATES,
     "def _market_goal_pct():\n"
     "    import contest_account as _ca\n"
     "    return _ca.load_state().size\n",
     lambda finds: not finds,
     "qualified module-handle reads never count as bare gate-name reads"),
)


def selftest_cases():
    """Run every hermetic fixture in its own sandboxed file.
    Returns [(name, label, findings, ok, detail)] — per-case verdicts
    the battery pins individually; selftest() aggregates them."""
    import tempfile
    import shutil
    results = []
    td = tempfile.mkdtemp()
    try:
        troot = Path(td)
        for name, gates, src, pred, label in SELFTEST_FIXTURES:
            (troot / f"{name}.py").write_text(src, encoding="utf-8")
            try:
                finds = audit_gates(files=[(f"{name}.py", gates, troot)])
                ok = bool(pred(finds))
                detail = f"{len(finds)} finding(s)"
                if finds:
                    detail += ": " + "; ".join(
                        f"{fn}.{nm} {why}"
                        for _f, fn, _l, nm, why in finds[:3])
            except Exception as exc:
                finds, ok, detail = [], False, repr(exc)
            results.append((name, label, finds, ok, detail))
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return results


def main():
    out("=== READ-BEFORE-ASSIGN v4 (st-shadow scope machinery) ===")
    total = 0
    for f in FILES:
        total += audit_file(f) or 0
    for f in BOT_FILES:
        total += audit_file(f, BOT_ROOT) or 0
    out(f"--- {total} candidate problems (whole-file, any store counts) ---")
    out("")
    out("=== SIZING GATES — CALL-AWARE FLOW CHECK "
        "(worth tilt / cap ceiling / danger zone / the bot's "
        "order-sizing path) ===")
    gate_findings = audit_gates()
    for fname, func, ln, nm, why in gate_findings:
        out(f"  {fname}:{ln}  {func} — '{nm}': {why}")
    if not gate_findings:
        out("  CLEAN — every gate's first read of every local follows its "
            "first store, call arguments included (walrus counts as a store)")
    out(f"--- {len(gate_findings)} gate-flow findings ---")
    out("")
    out("=== HARDCODED DOLLAR FALLBACKS IN SIZING CONTEXTS ===")
    SIZING_HINTS = ("spend", "bal", "size", "cap", "carry", "entry", "risk",
                    "budget", "challenge")
    found = 0
    for fname, root in ([(f, ROOT) for f in FILES]
                        + [(f, BOT_ROOT) for f in BOT_FILES]):
        p = root / fname
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8")
        tree = ast.parse(src)
        src_lines = src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for n in ast.walk(node):
                if (isinstance(n, ast.Constant)
                        and isinstance(n.value, (int, float))
                        and n.value in (1000.0, 5000.0, 10000.0, 1000,
                                        5000, 10000)):
                    line_txt = src_lines[n.lineno - 1].lower()
                    if any(h in line_txt for h in SIZING_HINTS):
                        found += 1
                        out(f"  {fname}:{n.lineno}  ${n.value} in: "
                            f"{line_txt.strip()[:100]}")
    out(f"--- {found} dollar-literal sites (inspect each: fallback or "
        "legit constant?) ---")
    st_d, st_ok = selftest()
    out("")
    out("SELFTEST (the hermetic canon — each case named, all must pass):")
    for nm, lbl, _f, cok, det in selftest_cases():
        out(f"  [{'PASS' if cok else 'FAIL'}] {nm} — {lbl} ({det})")
    out(f"  aggregate: [{'PASS' if st_ok else 'FAIL'}] {st_d}")
    out("")
    verdict = ("GATES CLEAN" if not gate_findings and st_ok else
               f"{len(gate_findings)} GATE FINDING(S)")
    out(f"VERDICT: {verdict}")
    Path("_sizingaudit.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n-> _sizingaudit.txt")
    return 0 if (not gate_findings and st_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
