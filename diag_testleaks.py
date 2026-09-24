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

  THE TIGHTENED SWAP RULE (Sep 23, folded permanently): every swap
  needs a finally restore, a sandbox local, or a sentinel — judged PER
  ASSIGNMENT. An earlier sandbox-shaped VALUE on the same dotted name
  no longer immunizes later swaps of that name: the restore-swap
  `SWAP = <production>` that rides a value-shape exemption earned by a
  previous `SWAP = tempfile.mkdtemp()` was exactly the gap through
  which the PULSE_JSON near miss re-entered. Genuine multi-step
  sandbox derivations keep their exemption through sandbox_names()'s
  transitive pass (TMP = mkdtemp(); P = TMP / "f.json" is still a
  sandbox local), so nothing real is newly flagged — only the
  production-valued swap riding a stale exemption.

Also flags os.chdir and os.environ stores (unguarded) for the same
reason. Writes _testleaks.txt (family convention) and prints a
summary. Run: .venv\\Scripts\\python.exe -X utf8 diag_testleaks.py

THE PERMANENT SELF-CHECK (Sep 23): main() also exercises this audit
on probe suites in a sandbox tempdir and fails the run unless the
tightened rule holds exactly — the reopen shape (value-shape sandbox
pass then a production restore-swap) must score TWO hits, the
sandbox-value assignment staying exempt; the finally, sandbox-local,
and sentinel arms must each stay clean. The self-check runs on every
battery pass (DIAG_RUN) and in every pre-commit gate pass, so the
value-shape exemption gap cannot reopen silently.
"""
import ast
import re
import sys
import tempfile
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
    # a name assigned exactly once is a DEFINITION, not a swap — there is
    # nothing to restore, so the swap class (which needs a finally, a
    # sandbox local, or a sentinel) cannot apply to it
    assign_counts = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            for t in node.targets:
                if isinstance(t, (ast.Attribute, ast.Name)):
                    key = ast.unparse(t)
                    assign_counts[key] = assign_counts.get(key, 0) + 1

    def value_is_sandbox(val):
        if val is None:
            return False
        if is_tempfile_value(val) or bool(free_names(val) & sbx):
            return True
        tname = target_name(val)
        return bool(tname and is_sandbox(tname))

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

        # 2) module-global swaps — THE TIGHTENED RULE (Sep 23), judged
        # per assignment: THIS assignment's own value is the only
        # sandbox escape. There is no standing immunization: a
        # value-shape sandbox pass on an earlier line can never exempt
        # a later production-valued swap of the same name (the exact
        # reopen path of the PULSE_JSON near miss — the restore-swap
        # `SWAP = <production>` riding an earlier mkdtemp value).
        # sandbox_names()'s transitive pass still covers genuine
        # multi-step derivations, so real sandbox locals keep their arm.
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
                if assign_counts.get(dotted, 0) < 2:
                    continue   # a definition, not a swap — nothing to restore
                if dotted in restored or f"del {dotted}" in restored:
                    continue
                val = getattr(node, "value", None)
                if value_is_sandbox(val):
                    continue
                # the deliberate 'hand the module back to production at
                # suite end' idiom carries an explicit sentinel comment —
                # a block comment's marker may sit above the FIRST line of
                # the comment, so the window reaches back five lines
                near = "\n".join(src_lines[max(node.lineno - 5, 0):
                                           node.lineno])
                if "leak-audit: ok" in near:
                    continue
                vname = target_name(val) if val is not None else "?"
                hits.append((node.lineno,
                             f"UNGUARDED SWAP: {dotted} = "
                             f"{vname or ast.dump(val)[:40]} — every swap "
                             f"needs a finally restore, a sandbox local, or "
                             f"a 'leak-audit: ok' sentinel"))

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


def audit_selfcheck(prod_names):
    """THE PERMANENT SELF-CHECK (Sep 23): the diag exercises its own
    audit_file on probe suites in a sandbox tempdir and FAILS the run
    unless the tightened swap rule holds exactly.

      probe_gap   the reopen shape — init, a value-shape sandbox pass
                  (tempfile.mkdtemp), then the restore-swap with a
                  PRODUCTION value, no finally: BOTH production
                  assignments must be flagged and the sandbox-value
                  assignment must stay exempt (exactly hits at L3 and
                  L5, never L4). Under the old standing exemption this
                  probe scored 1 — if it ever scores 1 again, the
                  value-shape gap has reopened and the battery goes red.
      probe_sbx   a sandbox local (mkdtemp value) — exempt
      probe_fin   a try/finally restore — exempt
      probe_sent  the 'leak-audit: ok' sentinel — exempt

    The probes run the REAL audit_file on real files, so any edit that
    restores the standing immunization, weakens the finally/sandbox/
    sentinel arms, or breaks audit_file itself fails here — on every
    battery pass and every pre-commit gate pass."""
    failures = []
    probes = {
        "probe_gap": (
            "import m\n"
            "import tempfile\n"
            "SWAP_PATH = m.PULSE_JSON\n"
            "SWAP_PATH = tempfile.mkdtemp()\n"
            "SWAP_PATH = m.PULSE_JSON\n"
        ),
        "probe_sbx": (
            "import tempfile\n"
            "SWAP_PATH = tempfile.mkdtemp()\n"
        ),
        "probe_fin": (
            "import m\n"
            "SWAP_PATH = m.PULSE_JSON\n"
            "try:\n"
            "    SWAP_PATH = m.OTHER_JSON\n"
            "finally:\n"
            "    SWAP_PATH = m.PULSE_JSON\n"
        ),
        "probe_sent": (
            "import m\n"
            "# leak-audit: ok — the deliberate hand-back, documented\n"
            "# above the assignment as the block-comment idiom writes it\n"
            "SWAP_PATH = m.PULSE_JSON\n"
        ),
        "probe_def": (
            "TRACKED_HOOKS_DIR = \"hooks\"\n"
            "LOCAL_HOOKS_DIR = OTHER_DIR\n"
        ),
    }
    with tempfile.TemporaryDirectory(prefix="_testleaks_probe_") as td:
        tdp = Path(td)
        hits = {}
        for name, text in probes.items():
            p = tdp / f"{name}.py"
            p.write_text(text, encoding="utf-8")
            hits[name] = audit_file(p, prod_names)

        def _lines(hs):
            out = []
            for h in hs:
                m = re.match(r"\s*L(\d+):", h)
                if m:
                    out.append(int(m.group(1)))
            return sorted(out)

        gap = _lines(hits["probe_gap"])
        if gap != [3, 5]:
            failures.append(
                f"probe_gap scored L{gap}, expected L[3, 5] — the "
                "value-shape exemption gap has REOPENED (a standing "
                "immunization is exempting the restore-swap again, "
                "scoring 1) or the sandbox-local arm over-tightened "
                "(flagging the sandbox swap)")
        for name in ("probe_sbx", "probe_fin", "probe_sent", "probe_def"):
            if hits[name]:
                failures.append(
                    f"{name} must stay clean but was flagged: "
                    f"{hits[name]} — a legitimate arm regressed")

    # source pins. TWO anti-vacuity disciplines, learned the hard way in
    # this very file: a pin that searches for a literal which also appears
    # in the pin's own source line can NEVER fail (the needle always finds
    # itself), and a pin outside the region it guards passes even when the
    # region is deleted. So: needles are built from parts so the literal
    # never appears whole in this checker, and the branch arms are asserted
    # REGIONALLY — within the swap branch around the UNGUARDED SWAP verdict,
    # not anywhere in the file.
    src = Path(__file__).read_text(encoding="utf-8")
    if ("sandbox_" + "touched") in src:
        failures.append(
            "the standing value-shape immunization is back in the "
            "source — the per-assignment rule has been weakened")
    sstart = src.find('f"UNGUARDED SWAP:')
    if sstart == -1:
        failures.append(
            "the UNGUARDED SWAP verdict is missing from the audit — "
            "the swap class no longer grades anything")
    else:
        region = src[max(sstart - 3000, 0):sstart + 400]
        for needle, what in (
            ('if "leak-audit: ok" in near', "the sentinel arm"),
            ("assign_counts.get(dotted, 0) < 2", "the definition exemption"),
            ("value_is_sandbox(val)",
             "the per-assignment sandbox escape"),
            ("dotted in restored", "the finally arm"),
            ("finally restore, " + "a sandbox local",
             "the tightened rule sentence"),
        ):
            if needle not in region:
                failures.append(
                    f"{what} is missing from the swap branch — the "
                    "tightened rule has been weakened where it is enforced")
    return failures


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
    # THE PERMANENT SELF-CHECK: the audit grades its own tightened rule;
    # a self-check that cannot even run is a red, never a silent skip.
    try:
        selffail = audit_selfcheck(prod)
    except Exception as exc:
        selffail = [f"self-check crashed: {exc!r}"]
    if selffail:
        report.append("SELF-CHECK FAILED — the swap-rule contract drifted:")
        report.extend(f"  {s}" for s in selffail)
        report.append("")
    total += len(selffail)
    if total == 0:
        report.append("CLEAN — no unguarded swaps, no production-path writes, "
                      "self-check green.")
    else:
        report.append(f"TOTAL: {total} finding(s) across the suites above.")
    OUT.write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    # battery-gradable: findings fail the run so a leak can never
    # re-enter the suite family unnoticed — the self-check included
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
