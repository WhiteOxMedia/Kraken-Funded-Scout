"""diag_testleaks.py — AUDIT: test suites vs production files (Sep 11).

History: test_apptest's history pins once overwrote the REAL
_tidebarweekly.txt (restore-in-finally; a killed battery left the
fixture behind and production prose history was lost). This audit
walks every test_*.py and flags the two leak classes:

  1. PRODUCTION-PATH WRITES — open(..., 'w'/'a'/'wb'), Path.write_text
     /write_bytes, or shutil.copy/copyfile whose target is a literal
     path naming a file the production modules own (collected from the
     non-test sources), except sandbox names (_sbx*, tmp*, tempfile).
  2. UNGUARDED MODULE-GLOBAL SWAPS — assignments to <module>.<CONST>
     (or an ALL-CAPS global) where CONST hints at a filesystem path
     (FILE/PATH/DIR/NAME suffix or a path-ish literal), with NO
     restore in any enclosing try/finally. Each suite runs as its own
     process, so residue only matters in-process — but any suite that
     later boots the app (AppTest) lets production code write through
     the swapped constant.

Also flags os.chdir and os.environ stores (unguarded) for the same
reason. Writes _testleaks.txt (family convention) and prints a
summary. Run: .venv\\Scripts\\python.exe -X utf8 diag_testleaks.py
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "_testleaks.txt"

SANDBOX_HINTS = re.compile(r"^(_sbx|tmp|temp|_tmp|_test|_scratch)", re.I)
PATHISH = re.compile(r"(FILE|PATH|DIR|NAME|JSON|LOG|TXT)$", re.I)
LITERAL_TARGETS = (ast.Constant, ast.JoinedStr)


def collect_production_names():
    """Filenames the production modules own (string literals with a
    data extension), so the audit knows what 'production' means."""
    names = set()
    for py in ROOT.glob("*.py"):
        if py.name.startswith(("test_", "diag_")):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                base = node.value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                if re.search(r"\.(jsonl|json|txt|feather|csv|db)$", base):
                    names.add(base)
    return names


def target_name(node):
    """A write target's literal filename, or None (dynamic/variable)."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        fn = node.func
        if fn.attr in ("write_text", "write_bytes"):
            # p.write_text(content) — the PATH is the receiver
            return target_name(fn.value)
        if fn.attr in ("copy", "copyfile", "copy2") and len(node.args) > 1:
            return target_name(node.args[1])
        return None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in ("Path", "str") and node.args:
        return target_name(node.args[0])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        # BASE / "name.json" — take the literal segment
        return target_name(node.right)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return None


def is_sandbox(name):
    return bool(name) and bool(SANDBOX_HINTS.match(name)) \
        or "tempfile" in (name or "").lower() \
        or name.startswith("__") or "/tmp/" in name or "\\tmp\\" in name


def free_names(node):
    """Load-context Name ids referenced anywhere in the expression."""
    return {n.id for n in ast.walk(node)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def sandbox_names(tree):
    """Names bound to tempfile-derived paths: direct mkdtemp/mkstemp/
    TemporaryDirectory results, `with tempfile.TemporaryDirectory() as x`,
    and (two passes for transitivity) assignments referencing an
    already-sandboxed name."""
    names = set()
    for _ in range(3):
        grew = False
        for node in ast.walk(tree):
            if isinstance(node, ast.With):
                for item in node.items:
                    if is_tempfile_value(item.context_expr) \
                            and isinstance(item.optional_vars, ast.Name) \
                            and item.optional_vars.id not in names:
                        names.add(item.optional_vars.id)
                        grew = True
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name):
                tname = target_name(node.value)
                if is_tempfile_value(node.value) \
                        or (tname and is_sandbox(tname)) \
                        or (free_names(node.value) & names):
                    if node.targets[0].id not in names:
                        names.add(node.targets[0].id)
                        grew = True
        if not grew:
            break
    return names


def finally_targets(tree):
    """All dotted-name stores that appear in some try/finally body —
    direct assignments AND tuple unpacking (a, b = x, y)."""
    restored = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for fin in node.finalbody:
                for sub in ast.walk(fin):
                    if isinstance(sub, ast.Assign):
                        for t in sub.targets:
                            if isinstance(t, ast.Tuple):
                                for elt in t.elts:
                                    if isinstance(elt, (ast.Attribute,
                                                        ast.Name)):
                                        restored.add(ast.unparse(elt))
                            elif isinstance(t, (ast.Attribute, ast.Name,
                                                ast.Subscript)):
                                restored.add(ast.unparse(t))
                    elif isinstance(sub, ast.Delete):
                        for t in sub.targets:
                            restored.add(f"del {ast.unparse(t)}")
    return restored


def is_tempfile_value(node):
    """A swap whose value is a tempfile path (mkdtemp/mkstemp/
    TemporaryDirectory/gettempdir, bare or module-qualified) — sandbox
    by construction, never production."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            if isinstance(sub.func, ast.Attribute) and sub.func.attr in (
                    "mkdtemp", "mkstemp", "TemporaryDirectory",
                    "gettempdir", "gettempdirb"):
                return True
            if isinstance(sub.func, ast.Name) and sub.func.id in (
                    "TemporaryDirectory", "mkdtemp", "mkstemp",
                    "gettempdir"):
                return True
    return False


def audit_file(py, prod_names):
    hits = []
    try:
        src = py.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [f"  SYNTAX ERROR: {exc}"]
    src_lines = src.splitlines()
    restored = finally_targets(tree)
    sbx = sandbox_names(tree)
    sandbox_touched = set()   # dotted targets ever assigned a sandbox value

    def value_is_sandbox(val):
        if val is None:
            return False
        if is_tempfile_value(val) or bool(free_names(val) & sbx):
            return True
        tname = target_name(val)
        return bool(tname and is_sandbox(tname))

    # pre-pass: which dotted swap targets were EVER given a sandbox value
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) \
                else [node.target]
            for t in targets:
                if isinstance(t, (ast.Attribute, ast.Name)) \
                        and value_is_sandbox(getattr(node, "value", None)):
                    sandbox_touched.add(ast.unparse(t))

    for node in ast.walk(tree):
        # 1) writes to literal paths — for p.write_text(content) the
        # PATH is the RECEIVER (args[0] is the content)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("write_text", "write_bytes", "copy",
                                       "copyfile", "copy2"):
            args = node.args or []
            if node.func.attr in ("write_text", "write_bytes"):
                tnode = node.func.value
            else:
                tnode = node.args[1] if len(args) > 1 else None
            name = target_name(tnode) if tnode is not None else None
            # a target built FROM a sandbox-derived name (TMP = mkdtemp())
            # is sandbox even when the literal basename names a production
            # file — the directory, not the basename, is what clobbers
            if tnode is not None and (free_names(tnode) & sbx):
                continue
            if name and not is_sandbox(name):
                if name in prod_names:
                    hits.append((node.lineno,
                                 f"PRODUCTION-PATH WRITE: {name}"))
                elif "." not in name:
                    hits.append((node.lineno,
                                 f"RELATIVE-PATH WRITE: {name} — the cwd is "
                                 f"the production repo dir when the suite "
                                 f"runs; use BASE / or a sandbox"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "open" and len(node.args) >= 2 \
                and isinstance(node.args[1], ast.Constant) \
                and isinstance(node.args[1].value, str) \
                and re.search(r"[wa]", node.args[1].value):
            name = target_name(node.args[0])
            if name and not is_sandbox(name):
                if free_names(node.args[0]) & sbx:
                    continue
                if name in prod_names:
                    hits.append((node.lineno,
                                 f"PRODUCTION-PATH WRITE: open('{name}')"))
                elif "." not in name:
                    hits.append((node.lineno,
                                 f"RELATIVE-PATH WRITE: open('{name}') — use "
                                 f"BASE / or a sandbox"))

        # 2) module-global swaps
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) \
                else [node.target]
            for t in targets:
                dotted = ast.unparse(t) if isinstance(t, (ast.Attribute,
                                                          ast.Name)) else None
                if not dotted:
                    continue
                is_attr = isinstance(t, ast.Attribute)
                base_is_module = is_attr and isinstance(t.value, ast.Name)
                caps = (not is_attr and re.match(r"^[A-Z_]+$", dotted))
                pathish = bool(PATHISH.search(dotted))
                if not ((base_is_module and pathish) or (caps and pathish)):
                    continue
                if dotted in restored or f"del {dotted}" in restored:
                    continue
                val = getattr(node, "value", None)
                if value_is_sandbox(val) or dotted in sandbox_touched:
                    continue
                # the deliberate 'hand the module back to production at
                # suite end' idiom carries an explicit sentinel comment
                near = "\n".join(src_lines[max(node.lineno - 2, 0):
                                           node.lineno])
                if "leak-audit: ok" in near:
                    continue
                vname = target_name(val) if val is not None else "?"
                hits.append((node.lineno,
                             f"UNGUARDED SWAP: {dotted} = "
                             f"{vname or ast.dump(val)[:40]}"))

        # 3) cwd / env
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "chdir" \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "os":
            hits.append((node.lineno, "os.chdir (unguarded audit note)"))
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Subscript) \
                and "environ" in ast.unparse(node.targets[0].value):
            dotted = f"del {ast.unparse(node.targets[0])}"
            if ast.unparse(node.targets[0]) not in restored \
                    and dotted not in restored:
                hits.append((node.lineno,
                             f"UNGUARDED ENV: {ast.unparse(node.targets[0])}"))
    return [f"  L{n}: {msg}" for n, msg in sorted(hits)]


def main():
    prod = collect_production_names()
    report = ["TEST-SUITE LEAK AUDIT — module globals & production paths",
              f"({ROOT.name}, {len(prod)} production data filenames known)",
              ""]
    total = 0
    for py in sorted(ROOT.glob("test_*.py")):
        hits = audit_file(py, prod)
        total += len(hits)
        if hits:
            report.append(f"{py.name}: {len(hits)} finding(s)")
            report.extend(hits)
            report.append("")
    if total == 0:
        report.append("CLEAN — no unguarded swaps, no production-path writes.")
    else:
        report.append(f"TOTAL: {total} finding(s) across the suites above.")
    OUT.write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    # battery-gradable: findings fail the run so a leak can never
    # re-enter the suite family unnoticed
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
