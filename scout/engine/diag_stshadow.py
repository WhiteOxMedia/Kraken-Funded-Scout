"""AST AUDIT — the swallowed-widget bug class (Sep 12).

The disease: a local named `st` (a state abbreviation) bound anywhere
in a function scope — plain assignment, a plain `for st in ...` loop,
a with-as, or a parameter — makes `st` LOCAL for the whole scope.
Every `st.markdown` / `st.session_state` / widget call in that scope
then raises UnboundLocalError (load before the bind) or AttributeError
(load after the bind, st holding a state string like "OUT"). Inside a
fail-open `try/except: pass` the death is silent: the widget simply
never renders — exactly what killed the trail's toggle and dropdown
before their scars were found by hand.

Safe shapes (own scope, no leak): comprehension targets, lambda
params, nested defs, function-local `import streamlit as st` — each
handled as its own scope or the healthy binding. Body order matters:
a load before the first bind in the body is the UnboundLocalError
variant; after the bind, the AttributeError variant — both flagged,
with swallow-risk (inside a try block) noted. `global st` alone is a
healthy no-op; `global st` plus a bare reassignment rebinds the
MODULE st for every other scope and is flagged on its own. Walrus
(`st := x`) and match captures bind too, and a function-local
`import <anything-else> as st` is a shadow.

THE FLEET SWEEP (Sep 13): the analyzer no longer guards app.py alone
— it discovers EVERY module in the project that imports streamlit at
module level (the import is what makes a leaky local `st` fatal; a
file that never imports st cannot suffer the class) and sweeps each.
Production modules (main-ish names) must be CLEAN; diagnostics,
scratch (_-prefixed) and the test harness are swept and REPORTED but
excluded from the fleet gate so a known-bad teaching snippet can
never fail the battery silently — or a clean one pass it.

Read-only over the fleet. Writes _stshadow.txt.
Run: .venv/Scripts/python.exe -X utf8 diag_stshadow.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent
TARGET = ROOT / "app.py"          # kept for the single-file API
OUT = ROOT / "_stshadow.txt"

WIDGETS = {
    "markdown", "write", "caption", "code", "header", "subheader",
    "text", "metric", "columns", "expander", "container", "empty",
    "checkbox", "selectbox", "multiselect", "radio", "button",
    "form", "text_input", "number_input", "slider", "toggle",
    "rerun", "session_state", "sidebar", "tabs", "toast", "spinner",
    "stop", "plotly_chart", "pyplot", "image", "error", "warning",
    "info", "success", "cache_data", "cache_resource", "divider",
    "badge", "chat_message", "dataframe", "table", "json", "link",
    # the rest of app.py's surface (and room to grow) — a widget load
    # missing from this set would sail past the gate unflagged
    "set_page_config", "form_submit_button", "fragment", "popover",
    "main", "title", "help", "latex", "write_stream", "data_editor",
    "link_button", "page_link", "status", "exception", "area_chart",
    "bar_chart", "line_chart", "map", "file_uploader",
    "download_button", "progress", "audio", "video", "color_picker",
    "date_input", "time_input",
}

# Names every member the `st` MODULE exposes at module level (not just
# widgets/functions — st.secrets is plain data): a local `st` bind can
# poison ANY st.<name> load, so the detector's watch-list must cover
# the module's whole surface, not only the render calls.
ST_NAMES = WIDGETS | {"secrets"}

SCOPE_STMTS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
               ast.Lambda)


def _walk_own(node):
    """Yield every node in `node`'s OWN scope: descend into compound
    statements but PRUNE nested function/class/lambda subtrees (they
    are separate scopes with their own bindings)."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        for child in ast.iter_child_nodes(n):
            if isinstance(child, SCOPE_STMTS):
                continue          # pruned: another scope's territory
            stack.append(child)


def _bare_names(t):
    """Name nodes a target truly binds: the whole target, elements of
    tuple/list unpacking, starred — NEVER names nested inside
    Attribute/Subscript (st.session_state[x] = ... mutates, does not
    bind st)."""
    if isinstance(t, ast.Name):
        yield t
    elif isinstance(t, (ast.Tuple, ast.List)):
        for e in t.elts:
            yield from _bare_names(e)
    elif isinstance(t, ast.Starred):
        yield from _bare_names(t.value)


def _binds_st(node):
    """(kind, name, lineno) for every `st` binding `node` makes in its
    own scope."""
    out = []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                         ast.ClassDef, ast.Lambda)):
        if getattr(node, "name", None) == "st":
            out.append(("def", "st", node.lineno))
        args = getattr(node, "args", None)
        if args is not None:
            for a in (args.posonlyargs + args.args + args.kwonlyargs):
                if a.arg == "st":
                    out.append(("param", "st", node.lineno))
            if args.vararg and args.vararg.arg == "st":
                out.append(("param", "st", node.lineno))
            if args.kwarg and args.kwarg.arg == "st":
                out.append(("param", "st", node.lineno))
    elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        tgts = (node.targets if isinstance(node, ast.Assign)
                else [node.target])
        for t in tgts:
            for n in _bare_names(t):
                if n.id == "st":
                    out.append((type(node).__name__, "st", node.lineno))
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        for n in _bare_names(node.target):
            if n.id == "st":
                out.append(("for", "st", node.lineno))
    elif isinstance(node, ast.withitem) and node.optional_vars is not None:
        for n in _bare_names(node.optional_vars):
            if n.id == "st":
                out.append(("with", "st", node.lineno))
    elif isinstance(node, ast.ExceptHandler):
        if node.name == "st":
            out.append(("except", "st", node.lineno))
    elif isinstance(node, ast.NamedExpr):
        if isinstance(node.target, ast.Name) and node.target.id == "st":
            out.append(("walrus", "st", node.lineno))
    elif getattr(ast, "Match", None) and isinstance(node, ast.Match):
        for case in node.cases:
            for n in ast.walk(case.pattern):
                if (isinstance(n, (ast.MatchAs, ast.MatchStar))
                        and getattr(n, "name", None) == "st"):
                    out.append(("match", "st",
                                getattr(n, "lineno", node.lineno)))
                if isinstance(n, ast.MatchMapping) and n.rest == "st":
                    out.append(("match", "st", n.lineno))
    elif isinstance(node, (ast.Global, ast.Nonlocal)):
        if "st" in node.names:
            out.append((type(node).__name__, "st", node.lineno))
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for al in node.names:
            if (al.asname or al.name.split(".")[0]) == "st":
                # record WHICH module: `import streamlit as st` is the
                # healthy binding, any other module aliased st is a
                # shadow
                src = (al.name if isinstance(node, ast.Import)
                       else (node.module or ""))
                out.append(("import", src, node.lineno))
    return out


def _st_widget_loads(node):
    """(attr, lineno) for every st.<name> load in the node's own scope
    (comprehension bodies INCLUDED — they resolve `st` against the
    enclosing function's locals, so a leaky bind poisons them too).
    Watches ST_NAMES: the module's whole surface, st.secrets included."""
    out = []
    for n in _walk_own(node):
        if (isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name)
                and n.value.id == "st" and n.attr in ST_NAMES):
            out.append((n.attr, n.lineno))
    return out


class Scope:
    def __init__(self, name, node, parent):
        self.name = name
        self.node = node
        self.parent = parent
        self.binds = []       # (kind, lineno)
        self.loads = []       # (attr, lineno)
        self.try_spans = []   # (start, end) try regions
        self.children = []

    def label(self):
        chain, s = [], self
        while s is not None:
            chain.append(s.name)
            s = s.parent
        return " > ".join(reversed(chain))


def build_scope(node, name, parent):
    sc = Scope(name, node, parent)
    if parent is not None:
        parent.children.append(sc)
    body = (node.body if isinstance(node, (ast.FunctionDef,
                                           ast.AsyncFunctionDef,
                                           ast.ClassDef, ast.Module))
            else [node.body] if isinstance(node, ast.Lambda)
            else [])

    def visit(stmts):
        for stmt in stmts:
            if isinstance(stmt, SCOPE_STMTS):
                # a nested def/class/lambda is its own scope — but its
                # decorators and default args evaluate in THIS scope
                for dec in getattr(stmt, "decorator_list", []) or []:
                    sc.loads.extend(_st_widget_loads(dec))
                args = getattr(stmt, "args", None)
                if args is not None:
                    for d in (args.defaults
                              + [x for x in args.kw_defaults
                                 if x is not None]):
                        sc.loads.extend(_st_widget_loads(d))
                build_scope(stmt, getattr(stmt, "name", None)
                            or f"<lambda:{stmt.lineno}>", sc)
                continue
            for kind, nm, ln in _binds_st(stmt):
                sc.binds.append((kind, nm, ln))
            # hidden binds: a walrus nested in any expression of this
            # statement binds in THIS scope (match captures ride the
            # statement dispatch above)
            for n in _walk_own(stmt):
                if isinstance(n, ast.NamedExpr):
                    sc.binds.extend(_binds_st(n))
            if isinstance(stmt, ast.Try):
                end = max((s.end_lineno for s in _walk_own(stmt)
                           if hasattr(s, "end_lineno")),
                          default=stmt.lineno)
                sc.try_spans.append((stmt.lineno, end))
                for h in stmt.handlers:
                    if h.name == "st":
                        sc.binds.append(("except", "st", h.lineno))
                    if h.type is not None:
                        sc.loads.extend(_st_widget_loads(h.type))
                visit(stmt.body)
                for h in stmt.handlers:
                    visit(h.body)
                visit(stmt.orelse)
                visit(stmt.finalbody)
            elif isinstance(stmt, (ast.If, ast.While)):
                sc.loads.extend(_st_widget_loads(stmt.test))
                visit(stmt.body)
                visit(stmt.orelse)
            elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                sc.loads.extend(_st_widget_loads(stmt.iter))
                visit(stmt.body)
                visit(stmt.orelse)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                for i in stmt.items:
                    sc.loads.extend(_st_widget_loads(i.context_expr))
                    for kind, nm, ln in _binds_st(i):
                        sc.binds.append((kind, nm, ln))
                visit(stmt.body)
            else:
                sc.loads.extend(_st_widget_loads(stmt))
            # comprehension targets: implicit scope, resolve against
            # this scope only inside the comp — recorded so they are
            # never mistaken for poison
            for n in _walk_own(stmt):
                if isinstance(n, (ast.ListComp, ast.SetComp,
                                  ast.DictComp, ast.GeneratorExp)):
                    for gen in n.generators:
                        for t in ast.walk(gen.target):
                            if isinstance(t, ast.Name) and t.id == "st":
                                sc.binds.append(("comp-target",
                                                 "st", t.lineno))

    visit(body)
    # order-preserving dedupe: a compound statement's hidden-bind walk
    # overlaps its children's own passes
    seen, dedup = set(), []
    for b in sc.binds:
        if b not in seen:
            seen.add(b)
            dedup.append(b)
    sc.binds[:] = dedup
    return sc


def iter_scopes(root):
    stack = [root]
    while stack:
        s = stack.pop()
        yield s
        stack.extend(s.children)


def analyze_text(src):
    tree = ast.parse(src, filename="memory")
    # module-level `import streamlit as st` is the healthy binding;
    # note the bind now carries the SOURCE module, not the name
    imported = any(nm == "streamlit"
                   for n in tree.body
                   for kind, nm, _ in _binds_st(n)
                   if kind == "import")
    mod = build_scope(tree, "<module>", None)
    findings = []
    for sc in iter_scopes(mod):
        # only poison binds make st local-and-wrong: `import streamlit
        # as st` IS the healthy binding; a comp target shadows inside
        # its own comp; a bare `global st` declares, it does not leak
        # (any OTHER module aliased to st is a shadow and stays poison)
        poison = [b for b in sc.binds
                  if not (b[0] == "import" and b[1] == "streamlit")
                  and b[0] not in ("comp-target", "Global")]
        if not poison:
            continue
        for attr, ln in sc.loads:
            prior = [b for b in poison if b[2] <= ln]
            in_try = any(a <= ln <= b for a, b in sc.try_spans)
            if not prior:
                # nothing bound at-or-before the load — but the scope
                # binds st SOMEWHERE, so Python already made st a local
                # for the whole scope: this load is the
                # UnboundLocalError variant (the silent killer the old
                # analyzer skipped)
                findings.append((sc.label(), attr, ln,
                                 "before-bind (UnboundLocalError)",
                                 in_try))
                continue
            k, nm, _ = prior[-1]
            what = (f"shadowed import of '{nm}'" if k == "import"
                    else f"{k!r}-bound")
            findings.append((sc.label(), attr, ln,
                             f"after-bind (st is {what} -> "
                             f"AttributeError)", in_try))
    # the global-rebind disease, on its own: `global st` + a bare
    # reassignment doesn't just break this scope — it replaces the
    # MODULE st under every other scope's feet
    for sc in iter_scopes(mod):
        for kind, nm, ln in sc.binds:
            if kind != "Global" or nm != "st":
                continue
            rebinds = sorted({b[2] for b in sc.binds
                              if b[0] in ("Assign", "AugAssign",
                                          "AnnAssign", "for", "with",
                                          "walrus", "match")
                              and b[2] > ln})
            if rebinds:
                findings.append((sc.label(), "@global-rebind", ln,
                                 "global st + bare assign rebinds the "
                                 "MODULE st for every other scope",
                                 any(a <= ln <= b
                                     for a, b in sc.try_spans)))
    return imported, mod, findings, tree


def analyze():
    return analyze_text(TARGET.read_text(encoding="utf-8"))


def _is_app_candidate(path: Path) -> bool:
    """Production gate membership: the live module, main-ish scripts,
    and project packages — NOT diagnostics, scratch (_-prefixed), or
    the test harness. Those still get swept and reported; a finding
    there just doesn't fail the fleet."""
    if path.name.startswith("_"):
        return False
    if path.name.startswith("test_") or path.name == "conftest.py":
        return False
    if path.name.startswith("diag_"):
        return False
    return True


def discover_modules(root: Path = ROOT):
    """Every project module importing streamlit at MODULE level — the
    import is what makes a leaky local `st` fatal. Deterministic order,
    venvs/caches/hidden dirs pruned."""
    found = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
        if (any(part in {"__pycache__", ".venv", "venv", ".git",
                         "node_modules", "logs"} or part.startswith(".")
                for part in rel.parts[:-1])):
            continue
        try:
            src = p.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        if any(nm == "streamlit"
               for n in tree.body
               for kind, nm, _ in _binds_st(n)
               if kind == "import"):
            found.append(p)
    return found


def analyze_path(path: Path):
    """The single-file contract, from a path: (imported, mod, findings,
    tree) exactly as analyze() returns for app.py."""
    return analyze_text(path.read_text(encoding="utf-8"))


def analyze_all(root: Path = ROOT):
    """The fleet sweep: [(path, imported, mod, findings, tree)] for
    every streamlit-importing module, deterministic order."""
    results = []
    for p in discover_modules(root):
        imported, mod, findings, tree = analyze_path(p)
        results.append((p, imported, mod, findings, tree))
    return results


BAD_SNIPPET = '''
import streamlit as st
import json as st          # a shadowed IMPORT of the wrong module

def trail_block(evidence):
    st.caption("loading")            # load BEFORE any bind: UnboundLocal
    try:
        rows = []
        for st in evidence:          # the leak: st becomes local
            st = str(st.get("state") or "")
        st.markdown("".join(rows))   # AttributeError, swallowed
    except Exception:
        pass
    st.selectbox("verdict", ["a"])   # after-bind variant, uncaught

def hidden_walrus(state):
    if (st := state):                # walrus poisons the rest of the fn
        pass
    return st.session_state["k"]     # must be flagged

def global_rebind():
    global st
    st = []                          # rebinds the MODULE st for everyone
    return len(st)

def wrong_import(rows):
    import json as st                # the wrong module, aliased to st
    return st.markdown("".join(rows))  # must be flagged

def clean_block(evidence):
    for st in evidence:              # comp target only: safe
        pass
    return [str(st) for st in evidence]
'''

GOOD_SNIPPET = '''
import streamlit as st

def global_noop():
    global st                        # a declaration alone: healthy
    return 0
'''


def selftest():
    """The analyzer must flag the bad snippet's swallow, uncaught,
    before-bind, walrus and global-rebind variants and leave the
    comp-safe and global-noop functions unflagged."""
    _, _, bad, _t = analyze_text(BAD_SNIPPET)
    _, _, good, _t2 = analyze_text(GOOD_SNIPPET)
    detail = [f"line {ln}: st.{attr} in {lbl} — {variant}"
              f"{'; SWALLOWED by try/except' if tr else ''}"
              for lbl, attr, ln, variant, tr in bad]
    detail += [f"(clean side) line {ln}: st.{attr} in {lbl}"
               for lbl, attr, ln, _, _ in good]
    ok = (any(attr == "markdown" and tr for _, attr, _, _, tr in bad)
          and any(attr == "selectbox" and not tr
                  for _, attr, _, _, tr in bad)
          and any("before-bind (UnboundLocalError)" in v
                  for _, _, _, v, _ in bad)
          and not any("clean_block" in lbl
                      for lbl, _, _, _, _ in bad)
          and any("hidden_walrus" in lbl
                  for lbl, _, _, _, _ in bad)
          and any(attr == "@global-rebind"
                  for _, attr, _, _, _ in bad)
          and any("shadowed import of 'json'" in v
                  for _, _, _, v, _ in bad)
          and not good   # the clean snippet must surface nothing
          and len(bad) == 6)
    return detail, ok


def main():
    check_lines = []

    def check(name, ok, detail=""):
        check_lines.append(f"{'PASS' if ok else 'FAIL'}  {name}"
                           + (f"  [{detail}]" if detail and not ok
                              else ""))

    st_detail, st_ok = selftest()
    check("selftest: bad snippet flags swallow, uncaught, before-bind, "
          "walrus + global-rebind variants; clean side stays clean",
          st_ok, st_detail)

    fleet = analyze_all()
    total_loads = 0
    gate_finds = []       # findings in PRODUCTION (gated) modules
    report_finds = []     # findings anywhere (reported per file)
    per_file = []
    for path, imported, mod, findings, tree in fleet:
        scopes = list(iter_scopes(mod))
        scoped = sum(len(s.loads) for s in scopes)
        total_loads += scoped
        tree_loads = [n for n in ast.walk(tree)
                      if isinstance(n, ast.Attribute)
                      and isinstance(n.value, ast.Name)
                      and n.value.id == "st" and n.attr in ST_NAMES]
        # ground truth per file: an independent whole-tree walk must
        # agree with the scoped collection exactly
        check(f"{path.name}: scoped loads equal the independent "
              "whole-tree walk",
              scoped == len(tree_loads),
              f"{scoped} scoped vs {len(tree_loads)} tree-wide")
        # the attr-coverage net per file: every st.<anything> the file
        # uses must be on the watch-list, or a poisoned load there
        # would sail past the gate unflagged
        missing = sorted({n.attr for n in ast.walk(tree)
                          if isinstance(n, ast.Attribute)
                          and isinstance(n.value, ast.Name)
                          and n.value.id == "st"} - ST_NAMES)
        check(f"{path.name}: every st.<attr> it uses is on the "
              "analyzer's watch-list",
              not missing, f"missing: {missing}")
        gated = _is_app_candidate(path)
        per_file.append((path, imported, len(scopes), scoped,
                         findings, gated))
        report_finds.extend((path, f) for f in findings)
        if gated:
            gate_finds.extend((path, f) for f in findings)

    # THE GATE: production modules must be clean of the class on every
    # run — diagnostics/scratch/tests are swept and reported but can't
    # fail the fleet (a known-bad teaching snippet must never fail the
    # battery silently, and a clean one must never pass by accident)
    check(f"FLEET CLEAN: {sum(1 for t in per_file if t[5])} production "
          "module(s) — no scope binds a local st and calls st widgets "
          "(the swallowed-widget class is closed fleet-wide)",
          not gate_finds,
          f"{len(gate_finds)} finding(s): "
          + "; ".join(f"{p.name}:{ln} st.{a}"
                      for p, (_, a, ln, _, _) in gate_finds[:6]))

    lines = [f"ST-SHADOW AUDIT — the fleet ({len(fleet)} streamlit "
             "module(s)) (the swallowed-widget class)",
             f"st.<name> loads in scopes, fleet-wide: {total_loads}",
             "", "SELFTEST (the analyzer against a known-bad snippet):"]
    lines.extend("  " + s for s in st_detail)
    lines.append("")
    lines.append("FLEET (G = production gate member, r = report-only):")
    for path, imported, nscopes, scoped, findings, gated in per_file:
        tag = "G" if gated else "r"
        verdict = ("CLEAN" if not findings else
                   f"{len(findings)} finding(s)")
        lines.append(f"  [{tag}] {path.name}: {nscopes} scope(s), "
                     f"{scoped} load(s) — {verdict}")
        for _, a, ln, variant, tr in findings:
            lines.append(f"        line {ln}: st.{a} — {variant}; "
                         + ("SWALLOWED by try/except" if tr
                            else "uncaught"))
    lines.append("")
    lines.extend(check_lines)
    verdict = ("FLEET CLEAN — the bug class is absent from production"
               if not gate_finds else
               f"{len({p for p, _ in gate_finds})} production module(s) "
               f"affected, {len(gate_finds)} call(s) at risk")
    lines.append(f"VERDICT: {verdict}")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if all(cl.startswith("PASS") for cl in check_lines) else 1


if __name__ == "__main__":
    sys.exit(main())
