"""Drive of PYTEST COLLECTION SURVIVAL (Oct 8, user ask): every test_*.py
in the scout root must be importable without side-effect exits.

THE KILL: the house check()-script style runs its whole body at module
level and ends in a bare `sys.exit(...)` or `raise SystemExit(...)`. A
pytest collection that imports the module EXECUTES the entire drive at
import time and then dies on the exit — collection error at best, and
worse: a GREEN drive's sys.exit(0) silently ends collection with zero
tests recorded. THE CURE (Oct 8): the killing tail moved into
def main() + a real `if __name__ == "__main__": sys.exit(main())` guard
on every drive — the exit codes byte-preserved (sys.exit/raise inside a
function raise the same SystemExit through the guard call). THE
RESIDUAL, HONESTLY OUT OF SCOPE: the module body still EXECUTES at
import (checks run, RESULT prints) — the fleet's pre-existing style,
shared by the 79 no-exit drives; a true body-under-main conversion is
a per-file globals-semantics project, not a fleet sweep. Collection
SURVIVES: the import returns, no SystemExit escapes it.

THE ARMS (red-first order, every pin executes REAL code):
 1. the AST census: no test_*.py in the scout root may carry a
    module-level sys.exit call or a module-level raise SystemExit —
    the sweep sees every call site the AST sees, not a grep, and it
    includes THIS file (a drive that kills collection may not exempt
    itself);
 2. the exit-in-main rule: a main()-shaped drive's only sys.exit rides
    INSIDE the __main__ guard (the guard must call, not pass);
 3. the exec-import proof: the drive imports sample drives in a
    subprocess WITHOUT invoking main (import-only, the pytest
    collection shape) and asserts the import SURVIVES — rc 0 and the
    importer's own marker printed AFTER the import returns (a drive
    that still exits at import kills the marker). The drive's check
    output at import is the documented residual, not a failure.
    (THIS file is deliberately NOT a subprocess sample: an import-only
    proof of a drive that re-spawns import-only proofs recurses —
    the census above covers this file's own shape.)
 4. the wire proof: the __main__-invoked sample still exits by its
    verdict (the battery's subprocess contract is untouched).
"""
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    print(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    PASS, FAIL = PASS + (1 if ok else 0), FAIL + (0 if ok else 1)


# ── the AST census ───────────────────────────────────────────────────
def _module_exit_lines(tree):
    """(kind, lineno) of every sys.exit CALL and every `raise SystemExit`
    that sits at module level: outside any top-level def/class body and
    outside the __main__ guard — an exit inside def main() is the CURE,
    import-safe by construction."""
    exits = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "exit"
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "sys"):
            exits.append(("sys.exit", n.lineno))
        if (isinstance(n, ast.Raise)
                and isinstance(n.exc, ast.Call)
                and isinstance(n.exc.func, ast.Name)
                and n.exc.func.id == "SystemExit"):
            exits.append(("raise SystemExit", n.lineno))
    guard_line = None
    for n in tree.body:
        if (isinstance(n, ast.If)
                and isinstance(n.test, ast.Compare)
                and isinstance(n.test.left, ast.Name)
                and n.test.left.id == "__name__"
                and isinstance(n.test.comparators[0], ast.Constant)
                and n.test.comparators[0].value == "__main__"):
            guard_line = n.lineno
            break
    defspans = [(s.lineno, s.end_lineno) for s in tree.body
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef))]
    return [(k, ln) for k, ln in exits
            if (guard_line is None or ln < guard_line)
            and not any(a <= ln <= b for a, b in defspans)], guard_line


def _guard_body(tree):
    for n in tree.body:
        if (isinstance(n, ast.If)
                and isinstance(n.test, ast.Compare)
                and isinstance(n.test.left, ast.Name)
                and n.test.left.id == "__name__"
                and isinstance(n.test.comparators[0], ast.Constant)
                and n.test.comparators[0].value == "__main__"):
            return ast.dump(n.body[0])
    return None


def main():
    _drives = sorted(ROOT.glob("test_*.py"))
    _offenders = []
    for p in _drives:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            _offenders.append((p.name, f"SYNTAX: {exc}"))
            continue
        mod_exits, _guard = _module_exit_lines(tree)
        if mod_exits:
            _offenders.append((p.name, mod_exits[:3]))
        # a guard that exists must DO the exit (not `pass` after a stray
        # module-level exit — the cosmetic guard that fooled the file count)
        if _guard is not None and _guard_body(tree) == ast.dump(
                ast.parse("pass", mode="exec").body[0]):
            _offenders.append((p.name, "guard is cosmetic (`pass`), the "
                                       "exit runs at import time"))

    check("the census sees the whole fleet (itself included)",
          len(_drives) >= 140, len(_drives))
    check("NO test_*.py carries an import-time exit (the collection kill)",
          not _offenders, _offenders[:8])

    # ── the exec-import proof: the pytest collection shape, for real ──
    # (runs only when the census is green — red-first means the
    # subprocess fleet below would otherwise explode on offenders)
    if not _offenders:
        _samples = ["test_topic_hygiene.py",   # the leak cure, main-shaped
                    "test_sink_hygiene.py",    # the 1.1 fleet gate
                    "test_gatestrips.py",      # the exec-from-source drive
                    "test_journalrelease.py",  # global PASS/FAIL mutator
                    "test_statewriters.py",    # the 12-line wrapper class
                    "test_tidefloor.py",       # a raise-SystemExit cure
                    "test_silentalert.py",     # the ex-cosmetic guard
                    "test_apptest.py"]         # the fleet's biggest body
        for s in _samples:
            _r = subprocess.run(
                [sys.executable, "-X", "utf8", "-c",
                 f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
                 f"import {Path(s).stem}; print('IMPORT-ONLY')"],
                capture_output=True, text=True, timeout=180, cwd=str(ROOT))
            check(f"import-only: {s} survives collection (no exit at import)",
                  _r.returncode == 0 and "IMPORT-ONLY" in _r.stdout,
                  (_r.returncode, _r.stderr[-160:] if _r.returncode else ""))

        # ── the wire proof: __main__ invocation still exits by verdict ──
        _r2 = subprocess.run(
            [sys.executable, "-X", "utf8", str(ROOT / "test_sink_hygiene.py")],
            capture_output=True, text=True, timeout=180, cwd=str(ROOT))
        check("the __main__ wire still exits by the verdict (rc 0, RESULT)",
              _r2.returncode == 0 and "RESULT" in _r2.stdout,
              (_r2.returncode, _r2.stdout[-60:]))

    print(f"\nRESULT: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
