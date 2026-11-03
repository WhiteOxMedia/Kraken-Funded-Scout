"""ONE-SHOT: make every test_*.py pytest-collection-safe (Oct 8, user ask).

THE TRANSFORM (minimal, tail-only — the module body STAYS module-level,
as today; only the killing TAIL moves):
  - AST: find module-level `sys.exit(...)` calls and `raise SystemExit`
    statements (exits lexically inside def/class bodies or a __main__
    guard are import-safe and excluded);
  - conforming file = every module-level exit lives in one contiguous
    TAIL RUN (first exit-containing module statement through the last
    module statement, ignoring a trailing cosmetic `if __name__ ==
    "__main__": pass` guard). The whole tail run moves VERBATIM into
    `def main():` (order and semantics identical: sys.exit inside a
    function raises the same SystemExit; conditional tail exits keep
    their conditions), followed by
    `if __name__ == "__main__": sys.exit(main())` — a raise propagates
    with its code, a plain return exits 0. A cosmetic guard is dropped.
  - anything else -> NEEDS-HAND, untouched, listed loudly.

--apply performs the writes; default is a dry-run report.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPLY = "--apply" in sys.argv
SELF = "test_pytestsafe.py"


def _guard_node(tree):
    for n in tree.body:
        if (isinstance(n, ast.If)
                and isinstance(n.test, ast.Compare)
                and isinstance(n.test.left, ast.Name)
                and n.test.left.id == "__name__"
                and isinstance(n.test.comparators[0], ast.Constant)
                and n.test.comparators[0].value == "__main__"):
            return n
    return None


def _exit_nodes(tree):
    out = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "exit"
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "sys"):
            out.append(n)
        if (isinstance(n, ast.Raise)
                and isinstance(n.exc, ast.Call)
                and isinstance(n.exc.func, ast.Name)
                and n.exc.func.id == "SystemExit"):
            out.append(n)
    return out


def _module_exits(tree, guard):
    """Exit nodes NOT lexically inside a def/class or the __main__ guard."""
    gl = guard.lineno if guard else 10 ** 9
    gr = guard.end_lineno if guard else -1
    defspans = [(s.lineno, s.end_lineno) for s in tree.body
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef))]
    return [n for n in _exit_nodes(tree)
            if not (guard and gl <= n.lineno <= gr)
            and not any(a <= n.lineno <= b for a, b in defspans)]


report = {"auto": [], "hand": [], "clean": []}
plans = {}
for p in sorted(ROOT.glob("test_*.py")):
    if p.name == SELF:
        continue
    src = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        report["hand"].append((p.name, f"SYNTAX {exc}"))
        continue

    guard = _guard_node(tree)
    mod_exits = _module_exits(tree, guard)
    if not mod_exits:
        report["clean"].append(
            (p.name, "guarded" if guard is not None else "no exit"))
        continue

    stmts = list(tree.body)
    cosmetic = (guard is not None
                and isinstance(guard.body[0], ast.Pass)
                and len(guard.body) == 1)
    # the tail run: first module statement CONTAINING an exit .. last
    # module statement (cosmetic guard excluded from the move)
    first = None
    for i, s in enumerate(stmts):
        contains = any((s.lineno <= n.lineno <= s.end_lineno)
                       for n in mod_exits)
        if contains:
            first = i
            break
    if first is None:
        report["hand"].append((p.name, "exit not found in module stmts"))
        continue
    tail = [s for s in stmts[first:]
            if not (cosmetic and s is guard)]
    # the tail must contain EVERY module exit and NOTHING between
    # stmts[first] and the end may fall outside the tail
    tail_lines = {(s.lineno, s.end_lineno) for s in tail}
    missing = [n for n in mod_exits
               if not any(a <= n.lineno <= b for a, b in tail_lines)]
    if missing:
        report["hand"].append(
            (p.name, [("exit outside tail", n.lineno) for n in missing][:3]))
        continue
    # exits nested inside non-module-level control flow within the tail
    # are fine (they move verbatim); exits nested in DEFS within the tail
    # would be miscounted — but defs are excluded from module exits already
    plans[p.name] = (src, stmts[first:], cosmetic, guard)
    if APPLY:
        report["auto"].append((p.name, "WRITTEN"))
    else:
        report["auto"].append((p.name, stmts[first].lineno))

if APPLY:
    for name, (src, tail_stmts, cosmetic, guard) in plans.items():
        p = ROOT / name
        lines = src.split("\n")
        spans = [(s.lineno, s.end_lineno) for s in tail_stmts]
        lo, hi = min(a for a, _ in spans), max(b for _, b in spans)
        if guard is not None and not cosmetic:
            raise SystemExit(f"{name}: non-cosmetic guard, abort")
        # extract tail text (guard already excluded from tail_stmts)
        seg = lines[lo - 1:hi]
        # drop the cosmetic guard's text out of the segment
        if guard is not None:
            g0 = guard.lineno - lo        # 0-based inside seg
            g1 = guard.end_lineno - lo
            seg = seg[:g0] + seg[g1 + 1 - 1 + 1 - 1:] if g1 < len(seg) else seg
            seg = seg[:max(0, len(seg))]  # noop guard against off-by-one
        # dedent every non-empty line by the block indent
        indent = " " * (len(seg[0]) - len(seg[0].lstrip(" "))) if seg else ""
        body = [("    " + ln if ln.strip() else "") for ln in seg]
        # remove the tail from the module (delete high→low, guard included)
        if guard is not None:
            del lines[guard.lineno - 1:guard.end_lineno]
        del lines[lo - 1:hi]
        while lines and not lines[-1].strip():
            lines.pop()
        main_def = "\n\ndef main():\n" + "\n".join(body)
        lines.append(main_def
                     + '\n\n\nif __name__ == "__main__":\n'
                       "    sys.exit(main())\n")
        p.write_text("\n".join(lines), encoding="utf-8")
    print(f"APPLIED: {len(report['auto'])} files written")
    if report["hand"]:
        print("NEEDS-HAND (untouched):")
        for n, why in report["hand"]:
            print(f"  {n}: {why}")
else:
    print(f"AUTO-CONFORMING: {len(report['auto'])}")
    for n, why in report["auto"][:10]:
        print(f"  {n}: {why}")
    print(f"CLEAN (no change needed): {len(report['clean'])}")
    print(f"NEEDS-HAND: {len(report['hand'])}")
    for n, why in report["hand"]:
        print(f"  {n}: {why}")
