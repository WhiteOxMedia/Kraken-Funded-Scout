"""diag_silentpass.py — THE SILENT-SWALLOW GATE (Sep 13).

WHY THIS EXISTS: `except Exception: pass` around widget/loader code
swallows the exact errors an operator needs — a dead keyring client
traded unauthenticated all day, a corrupt watch file showed a zeroed
scoreboard with no trace, a dropped sold-record write never journaled
its outcome. The Sep 13 sweep reworked every bare swallow in app.py:
risky loaders log (warning) and carry narrowed tuples, best-effort
cosmetics at least log (debug), and the only remaining silent `pass`
sites are deliberate parse-probes with narrow tuples + a why-comment.

THE CONTRACT this gate enforces forever after:
  1. no bare `except ...: pass` — a swallow must either carry an
     exception name (so the handler can log it) or a NARROW tuple
     (so it can't mask the NameError/AttributeError class of bug),
     and both need a why-comment (the contract's honest-statement
     half, enforced textually at the site).
  2. parser discipline: handlers wrapping an *inner* `except ... pass`
     are themselves exempt — the trap this rule exists to prevent.

Note: this is a lint-class gate, not a proof — it cannot verify that
a logged handler really surfaces to the operator, only that the
silent-with-no-trace shape cannot return.

Run: .venv\\Scripts\\python.exe -X utf8 diag_silentpass.py
"""
import ast
from pathlib import Path

ROOT = Path(__file__).parent
TARGET = ROOT / "app.py"

# THE FLEET (Sep 13): the silent-swallow class is not app.py's alone —
# the scanner's own loops and the bot's trade loops swallow too, and a
# dropped WARN there is exactly the "it said nothing" incident class.
# bot_runner.py lives in the sibling trading-bot dir (the health
# command's convention). Report-only for diagnostics/test files; the
# gated set is the production surfaces.
FLEET_TARGETS = (
    ROOT / "app.py",
    ROOT / "tidebar_weekly.py",
    ROOT / "background_scanner.py",
    ROOT.parent / "kraken_xrp_trading_bot" / "bot_runner.py",
)


def _handler_is_silent(handler: ast.ExceptHandler) -> bool:
    """True = the handler's body is exactly `pass` (no log, no comment
    can be seen by AST — the comment half is checked textually)."""
    body = handler.body
    return (len(body) == 1 and isinstance(body[0], ast.Pass))


def _handler_is_narrow(handler: ast.ExceptHandler) -> bool:
    """True = catches a narrow type: a tuple/list of names, or a single
    name (semantically the same narrowing — `except OSError:` protects
    exactly like `except (OSError,):`; the gate used to miss it). Bare
    Exception/BaseException/None stay broad."""
    t = handler.type
    if isinstance(t, ast.Name):
        # a single named type is narrow EXCEPT the two broad roots
        # themselves — `except Exception:` is ast.Name too (the
        # selftest caught exactly this)
        return t.id not in ("Exception", "BaseException")
    if isinstance(t, (ast.Tuple, ast.List)):
        return bool(t.elts) and all(isinstance(e, ast.Name) for e in t.elts)
    return False


def analyze(path: Path = TARGET):
    """Returns (findings, total_handlers).

    findings: list of (line_no, reason) for every handler that is BOTH
    silent (body == pass) AND either broad (bare Exception /
    BaseException) or missing the why-comment a deliberate swallow
    must carry. A silent narrow handler with a why-comment is the
    blessed deliberate-probe shape.
    """
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)
    findings: list[tuple[int, str]] = []
    total = 0

    def walk(node):
        nonlocal total
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ExceptHandler):
                total += 1
                is_silent = _handler_is_silent(child)
                narrow = _handler_is_narrow(child)
                if is_silent:
                    # the why-comment must ride the PASS line (AST's
                    # handler.lineno points at the `except` keyword)
                    pass_stmt = next((n for n in ast.walk(child)
                                      if isinstance(n, ast.Pass)), None)
                    pline = pass_stmt.lineno if pass_stmt else child.lineno
                    text = lines[pline - 1] if pline - 1 < len(lines) else ""
                    has_why = "#" in text
                    if not narrow:
                        findings.append(
                            (pline, "bare silent `except: pass` — no name "
                                    "to log, no tuple to narrow"))
                    elif not has_why:
                        findings.append(
                            (pline, "silent narrow pass without a "
                                    "why-comment — deliberate swallows "
                                    "must say why"))
            walk(child)

    walk(tree)
    findings.sort(key=lambda f: f[0])
    return findings, total


def analyze_all():
    """The fleet sweep: [(path, findings, total)] for every target that
    exists. A missing file is skipped (a sibling project not checked
    out is not this gate's business); a file that fails to parse is a
    finding in itself — an unparseable production module is worse than
    any swallow."""
    out = []
    for p in FLEET_TARGETS:
        if not p.exists():
            continue
        try:
            findings, total = analyze(p)
        except SyntaxError as exc:
            out.append((p, [(0, f"UNPARSEABLE: {exc}")], 0))
            continue
        out.append((p, findings, total))
    return out


def selftest():
    """The gate must flag the bug classes and pass the blessed shapes."""
    import tempfile
    cases = [
        # (source, expected_finding_count, label)
        ("try:\n    x()\nexcept Exception:\n    pass\n", 1,
         "bare silent pass flagged"),
        ("try:\n    x()\nexcept BaseException:\n    pass\n", 1,
         "BaseException silent pass flagged"),
        ("try:\n    x()\nexcept Exception as exc:\n    logger.warning('%s', exc)\n", 0,
         "logged broad handler blessed"),
        ("try:\n    x()\nexcept (OSError, ValueError) as exc:\n    logger.warning('%s', exc)\n", 0,
         "narrow+logged blessed"),
        ("try:\n    x()\nexcept (KeyError, TypeError, ValueError):\n    pass  # probe: fall to the detail text\n", 0,
         "narrow silent pass with why-comment blessed"),
        ("try:\n    x()\nexcept (KeyError, TypeError):\n    pass\n", 1,
         "narrow silent pass WITHOUT why-comment flagged"),
        ("try:\n    x()\nexcept OSError:\n    pass  # absent is the normal shape\n", 0,
         "single-name narrow silent pass with why-comment blessed — "
         "the same narrowing a tuple gives"),
        ("try:\n    x()\nexcept OSError:\n    pass\n", 1,
         "single-name narrow silent pass WITHOUT why-comment flagged"),
        ("try:\n    x()\nexcept Exception:\n    pass  # notes-only\n", 1,
         "broad silent pass flagged EVEN when commented — a comment "
         "documents, only a narrow tuple or a log name protects"),
        ("try:\n    try:\n        y()\n    except (ValueError,):\n        pass\nexcept Exception:\n    pass\n", 2,
         "nested silent passes both flagged — the rule is uniform, "
         "no nesting exemption"),
    ]
    ok, detail = True, ""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "case.py"
        for src, want, label in cases:
            p.write_text(src, encoding="utf-8")
            got, _ = analyze(p)
            if len(got) != want:
                ok, detail = False, f"{label}: want {want} finding(s), got {len(got)}"
                break
    return ok, detail


def main():
    ok = True
    for p, findings, total in analyze_all():
        name = str(p.relative_to(ROOT)) if str(p).startswith(str(ROOT)) \
            else str(p)
        print(f"THE SILENT-SWALLOW GATE — {name}: {total} handlers scanned")
        if findings:
            ok = False
            for ln, why in findings:
                print(f"  line {ln}: {why}")
            print(f"RESULT: {len(findings)} finding(s)")
        else:
            print("RESULT: CLEAN — no bare silent swallows; every `pass` "
                  "handler carries a name to log, a narrow tuple, or a "
                  "documented reason")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
