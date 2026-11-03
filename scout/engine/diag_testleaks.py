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

ARM 4 — LIVE-VALUE POISON, the dead-poison hunt (Oct 8, user ask):
the class the sandbox-seed cures (test_topic_hygiene, then the
monero test_bundle_hygiene port) kept treating by hand — a FIXTURE
hardcoding a string constant that equals a LIVE production data
file's CURRENT value. The poison: the pin is green today and silently
false the moment the live file rotates (the topic flip, a canary
re-arm, a ledger row aging out), and a fresh checkout or gate sandbox
with no live file silently skips the very assertion that depended on
it. The universe: string values harvested from the SMALL production
data files the audit already knows (<= 8 KB at the repo root),
filtered to DISTINCTIVE values (long enough, lettered, not a bare
date/timestamp/number, not a pair symbol, not a single short token)
and minus everything that also appears in production SOURCE — a
value present in tracked source is a shared constant, never live
state (that one filter kills nearly every false positive: pair
symbols, wire texts, drive names). Each test_*.py string constant is
then matched against the map (equality or containment); a hit is
LIVE-VALUE POISON unless the 'leak-audit: ok' sentinel stands within
its window. The self-check grows poison probes driven by a SYNTHETIC
live-value map (deterministic, secret-free): the bare poison literal
must flag, the sentinel'd one must stay clean, and the parts-join
seed shape must stay clean — the cure pattern itself is pinned.

ARM 5 — ARGV SECRET EXPOSURE, the command-line hunt (Oct 9, user ask):
a subprocess invocation whose command-line POSITIONAL args carry the
CONTENTS of a production data file — an inline read in the argv
(open('x.json').read(), (BASE / 'x.txt').read_text()) or a one-hop
Name assigned such a read — puts the live value on the process command
line: visible in ps / tasklist imports, echoed in every runner log,
and preserved for posterity inside traceback cmdline dumps. The safe
shape is the fleet's own cure: pass a PATTERN FILE PATH (-f), never
the bytes (-e); the path travels the command line, the secret never
does. Arm 3b's read contract is reused whole (one _read_target:
literal basenames only, attribute chains end the static reach, sandbox
free names stay exempt) and the 'leak-audit: ok' sentinel window
applies as everywhere else. Probed permanently: the inline-read argv
flags at the invocation line, the one-hop-variable argv flags (the
variable barely abets the leak — the exposure is the invocation, not
the line), and the sentinel'd argv, the sandbox-read argv, and the
pattern-file PATH argv stay clean.
"""
import ast
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "_testleaks.txt"

SANDBOX_HINTS = re.compile(r"^(_sbx|tmp|temp|_tmp|_test|_scratch)", re.I)
PATHISH = re.compile(r"(FILE|PATH|DIR|NAME|JSON|LOG|TXT)$", re.I)
LITERAL_TARGETS = (ast.Constant, ast.JoinedStr)

# ARM 5 — the subprocess member set the argv hunt grades, and the
# attributes a read-shaped call can carry. run/Popen/check_output/call
# take the argv list positionally; getoutput/getstatusoutput take a
# shell STRING (a Constant — static reach ends inside it), so
# list-form invocations are the graded shape, the fleet's universal one.
_SUBPROC_CALLS = ("run", "Popen", "check_output", "getoutput",
                  "getstatusoutput", "call")
_READ_ATTRS = ("read", "readline", "readlines", "read_text", "read_bytes")


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


# ════════ ARM 4 — THE LIVE-VALUE MAP (the dead-poison hunt, Oct 8) ════════
_LIVE_CACHE = None


_ISO_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T")


def _distinctive_value(v):
    """THE TRUE-POISON CONTRACT (the Oct 8 dead-poison hunt, refined
    through three false-positive rounds): the only live datum worth
    poisoning a fixture over is an UNSTRUCTURED MINT — a value a random
    generator produced (the ntfy topic was the fleet's one true member:
    secrets-grade letters+digits, no spaces, decomposable into nothing).
    Everything else a data file carries is REPRODUCIBLE from its shape
    and therefore a legitimate fixture constant: prose reports ('pairs
    written : 102'), grades ('thin (4/6 edge weeks)'), serial fragments
    ('"KEY": null,'), drive-name listings (test_*/diag_*), clock-stamp
    ids (canary-<stamp>, pair|ISO|action dedupe keys, bare ISO stamps).
    A fixture may hardcode any of those and stay honest; hardcoding an
    unstructured mint dies on the next rotation."""
    if not (16 <= len(v) <= 200):
        return False                      # mints are long, bounded tokens
    if " " in v or "\n" in v or "\t" in v:
        return False                      # prose is never a mint
    if not re.search(r"[A-Za-z]", v) or not re.search(r"[0-9]", v):
        return False                      # a mint mixes letters + digits
    if re.match(r"^(test_|diag_)", v):
        return False                      # fleet inventory names
    if re.match(r"^(canary|cohort)[-_]", v):
        return False                      # stamp-derived cohort ids
    if "|" in v and _ISO_STAMP.search(v):
        return False                      # pair|stamp|action dedupe keys
    if _ISO_STAMP.search(v):
        return False                      # anything clock-stamped
    if re.fullmatch(r"[\d:./\-T ]+", v) or v.isdigit():
        return False                      # numerics/dates (belt)
    if re.fullmatch(r"[A-Z0-9]{1,10}/[A-Z]{2,6}", v):
        return False                      # pair symbols (belt)
    # THE SERIALIZED-FRAGMENT FILTER: a real datum never opens with a
    # quote or ends in serialization punctuation ('"KEY": null,').
    if v.startswith(('"', "'")) or v.endswith(('":', ",", ":", "{", "}")):
        return False
    return True


def _string_values(raw):
    """Every string value plausibly carried by a data file's text: JSON
    leaf strings when it parses, plus quoted-string extraction (jsonl
    lines) and the whole stripped content + per-line text (txt seeds
    like push_topic.txt)."""
    vals = set()
    s = raw.strip()
    if s:
        vals.add(s)
    for line in raw.splitlines():
        line = line.strip()
        if line:
            vals.add(line)
    try:
        def _walk(o):
            if isinstance(o, str):
                vals.add(o)
            elif isinstance(o, dict):
                for x in o.values():
                    _walk(x)
            elif isinstance(o, list):
                for x in o:
                    _walk(x)
        _walk(json.loads(raw))
    except Exception:
        pass
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', raw):
        try:
            vals.add(json.loads('"' + m.group(1) + '"'))
        except Exception:
            vals.add(m.group(1))
    return vals


def live_poison_values(prod_names=None, root=None):
    """The live-value map: distinctive value -> the production data file
    whose CURRENT content carries it. A value that ALSO appears in
    production SOURCE is a shared constant, never live state — that
    filter is what keeps the arm quiet (pair symbols, wire texts, drive
    names all live in tracked source; only true live-only state
    survives). Computed once per process (only the DEFAULT root is
    cached — a sandbox root always harvests fresh); reads are skip-clean
    (the gate-sandbox lesson: a vanished victim is gone, gone is clean).
    THE ROOT SEAM (the fresh-checkout lesson, Oct 8): root=None means
    the real repo — root=SomeDir harvests THAT tree instead, so the
    self-check can exercise the full harvesting path (glob -> read ->
    filter -> map) end-to-end on a sandbox world it controls, without
    touching or depending on any live secret. An empty harvest in a
    world that SHOULD carry live values is the arm's own red, not a
    silent skip — see the self-check's founding-member pin."""
    global _LIVE_CACHE
    if root is None and _LIVE_CACHE is not None:
        return _LIVE_CACHE
    if prod_names is None:
        prod_names = collect_production_names()
    the_root = ROOT if root is None else Path(root)
    corpus = []
    for py in the_root.rglob("*.py"):
        rel = py.relative_to(the_root).as_posix()
        if rel.startswith((".venv", ".git")) or "/backup" in rel \
                or rel.startswith("backup_") \
                or py.name.startswith(("test_", "diag_")):
            continue
        # THE CORPUS IS EVERY NON-TEST, NON-DIAG SOURCE — scratch/
        # and artifacts/ INCLUDED: the v1 false positives were exactly
        # this gap (the evening-chunk listing's drive names and the
        # battery logs' record copies live only in scratch/artifacts, so
        # a name harvested from a data file looked "live-only" while the
        # tracked sources carry it everywhere). Anything the tracked
        # tree ALSO says is a shared constant, never live state.
        try:
            corpus.append(py.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    # ...AND THE NON-PY TRACKED SOURCES TOO (the chunk-listing lesson,
    # cont'd): _runchunk.py was the only py reader, but the battery
    # shell scripts and the docsrot md files quote the same constants —
    # a shared literal does not care about the file extension.
    for extra in ("*.ps1", "*.md"):
        for f in the_root.glob(extra):
            if f.name.startswith(("test_", "diag_", "leak")):
                continue
            try:
                corpus.append(f.read_text(encoding="utf-8",
                                          errors="replace"))
            except OSError:
                continue
    corpus_text = "\n".join(corpus)
    vals = {}
    for name in sorted(prod_names):
        p = the_root / name
        if not p.is_file():
            continue
        try:
            if p.stat().st_size > 8192:
                continue
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for v in _string_values(raw):
            v = v.strip()
            if not _distinctive_value(v) or v in corpus_text:
                continue
            vals.setdefault(v, name)
    if root is None:
        _LIVE_CACHE = vals
    return vals


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


def _read_target(call, prod_names, sbx):
    """The PRODUCTION basename a read-shaped call reads (the arm-5
    probe), or None. Resolves through target_name: read_text/read_bytes
    receivers, open()'s first arg (read-mode only — a write-capable
    open is arm 1's business), and the open('f').read() chain resolved
    through the open call itself. THE ARM-3B BOUNDS, REUSED WHOLE: one
    read contract for both arms — attribute-chain receivers end the
    static reach, sandbox free names stay exempt, and the name must be
    a bare production basename (directory-bearing paths are pinned
    elsewhere)."""
    fn = call.func
    attr = getattr(fn, "attr", None)
    if attr in _READ_ATTRS:
        rnode = getattr(fn, "value", None)
        if isinstance(rnode, ast.Call) \
                and getattr(rnode.func, "id", None) == "open" \
                and rnode.args:
            rnode = rnode.args[0]       # open('f').read() — unchain it
    elif getattr(fn, "id", None) == "open" and call.args:
        mode = call.args[1].value if (
            len(call.args) > 1 and isinstance(call.args[1], ast.Constant)
            and isinstance(call.args[1].value, str)) else "r"
        if not re.fullmatch(r"[rUb]+", mode or ""):
            return None                 # write-capable: arm 1 owns it
        rnode = call.args[0]
    else:
        return None
    if rnode is None or any(isinstance(n, ast.Attribute)
                            for n in ast.walk(rnode)):
        return None
    name = target_name(rnode)
    if name and name in prod_names and "/" not in name \
            and "\\" not in name and not (free_names(call) & sbx):
        return name
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
                    elif isinstance(sub, ast.Expr) and isinstance(sub.value, ast.Call) \
                            and isinstance(sub.value.func, ast.Attribute) \
                            and sub.value.func.attr == "pop" \
                            and "environ" in ast.unparse(sub.value.func.value):
                        # os.environ.pop("KEY", None) in a finally IS a
                        # restore (the round-8 audit gap: the env arm knew
                        # assignment and del, not the semantically correct
                        # pop-with-default that env restores actually use)
                        for a in sub.value.args:
                            restored.add(f"del {ast.unparse(sub.value.func.value)}"
                                         f"[{ast.unparse(a)}]")
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


def audit_file(py, prod_names, live_values=None):
    """The five arms: production-path writes, unguarded swaps, cwd/env
    + default-path reads, live-value poison, and argv secret exposure
    (live_values=None -> the process-wide cached map)."""
    hits = []
    try:
        src = py.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [f"  SYNTAX ERROR: {exc}"]
    src_lines = src.splitlines()
    if live_values is None:
        live_values = live_poison_values(prod_names)
    restored = finally_targets(tree)
    sbx = sandbox_names(tree)
    # a name assigned exactly once is a DEFINITION, not a swap — there is
    # nothing to restore, so the swap class (which needs a finally, a
    # sandbox local, or a sentinel) cannot apply to it
    assign_counts = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, (ast.Attribute, ast.Name)):
                    key = ast.unparse(t)
                    assign_counts[key] = assign_counts.get(key, 0) + 1
        elif isinstance(node, ast.AnnAssign):
            # AnnAssign carries ONE .target (no .targets list) — the
            # crash that blinded the whole audit (the round-8 sweep)
            t = node.target
            if isinstance(t, (ast.Attribute, ast.Name)):
                key = ast.unparse(t)
                assign_counts[key] = assign_counts.get(key, 0) + 1

    # ARM 5's one-hop map (Oct 9): a Name assigned the result of a
    # production-file READ, then passed through a subprocess argv — the
    # read and the command line need not share a line, and the variable
    # BARELY abets the leak: the exposure is the invocation, not the
    # line, so the hop is followed exactly once (no transitive chase —
    # two hops is a different arm if the fleet ever grows one).
    read_var = {}
    for nd in ast.walk(tree):
        if isinstance(nd, ast.Assign) and len(nd.targets) == 1 \
                and isinstance(nd.targets[0], ast.Name):
            for sub in ast.walk(nd.value):
                if isinstance(sub, ast.Call):
                    owner = _read_target(sub, prod_names, sbx)
                    if owner:
                        read_var[nd.targets[0].id] = owner
                        break

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
                # the SENTINEL arm, honored on the write branch too (the
                # round-8 inconsistency fix): a line tagged 'leak-audit: ok'
                # carries its reviewer's justification — the SAME arm the
                # swap branch already honors, instead of the write branch
                # forcing parameter renames through the naming regex
                _near = "\n".join(src_lines[max(0, node.lineno - 3):
                                             min(len(src_lines),
                                                 node.lineno + 2)])
                if "leak-audit: ok" in _near:
                    continue
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
        # 3b) THE DEFAULT-PATH READ ARM (the hermeticity sweep, Sep 28):
        # the prose-backfill class — a suite reading a PRODUCTION data
        # file at its repo-root default path (open/read_text with a
        # literal production basename and no directory component). The
        # live file MOVES under such a read (the weekly writer regener
        # erates its prose mid-day), so pins reading it are silently
        # un-hermetic even when the assertions pass. Write-shape hits
        # are already covered by arm 1; this arm is the READ side.
        if isinstance(node, ast.Call):
            fname = getattr(node.func, "id", None) or \
                getattr(node.func, "attr", None)
            if fname in ("open", "read_text", "read_bytes"):
                if fname == "open" and node.args:
                    rnode = node.args[0]
                elif isinstance(node.func, ast.Attribute):
                    rnode = node.func.value
                else:
                    rnode = None
                # an ATTRIBUTE-CHAIN receiver (rb.HISTORY_DIR / "latest.json")
                # is out of this arm's static reach: the suite typically
                # pins that attribute to a sandbox, and the audit cannot
                # resolve it — only literal/Name-rooted paths are flagged
                if rnode is not None and any(
                        isinstance(n, ast.Attribute) for n in ast.walk(rnode)):
                    rnode = None
                name = target_name(rnode) if rnode is not None else None
                if name and not is_sandbox(name) and name in prod_names \
                        and "/" not in name and "\\" not in name \
                        and not (free_names(node) & sbx):
                    near = "\n".join(src_lines[max(node.lineno - 5, 0):
                                               min(len(src_lines),
                                                   node.lineno + 2)])
                    if "clock-audit: ok" not in near \
                            and "leak-audit: ok" not in near:
                        hits.append((node.lineno,
                                     f"DEFAULT-PATH READ: {name} — a "
                                     f"live production file moves under "
                                     f"the pin; seed the data or point the "
                                     f"reader at a sandbox"))
        # 4) LIVE-VALUE POISON (the dead-poison hunt, Oct 8): a string
        # constant in the suite whose text matches a LIVE production data
        # file's current value — equality or containment — pins live
        # state by VALUE: green today, silently false after the next
        # rotation, and silently skipped in a checkout where the live
        # file does not exist. The sentinel arm applies as everywhere
        # else ('leak-audit: ok' in the window = the deliberate pin,
        # documented). Sandbox-derived values are unreachable here by
        # construction: the map only carries values harvested from
        # production data files.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            cv = node.value.strip()
            owner = live_values.get(cv)
            if owner is None and len(cv) >= 10:
                for lv, lown in live_values.items():
                    if lv in cv:
                        owner = lown
                        break
            if owner:
                near = "\n".join(src_lines[max(node.lineno - 5, 0):
                                           min(len(src_lines),
                                               node.lineno + 2)])
                if "leak-audit: ok" not in near \
                        and "clock-audit: ok" not in near:
                    hits.append((node.lineno,
                                 f"LIVE-VALUE POISON: fixture hardcodes the "
                                 f"current value of live production file "
                                 f"{owner} — seed it (mkdtemp + parts-join "
                                 f"synthetic value, the test_topic_hygiene "
                                 f"pattern) or sentinel 'leak-audit: ok'"))

        # 5) ARGV SECRET EXPOSURE (the command-line hunt, Oct 9): a
        # subprocess invocation whose POSITIONAL args — the literal
        # command line ps / tasklist / a runner log would echo — carry
        # the CONTENTS of a production data file: an inline read in the
        # argv, or a one-hop Name assigned such a read. The safe shape
        # is the fleet's own cure — pass a PATTERN FILE PATH (-f),
        # never the bytes (-e): the path travels the command line, the
        # secret never does. env=/input= travel by pipe or environment
        # block, NOT the command line: out of this arm's contract.
        # Sandbox reads are exempt as everywhere else, and the
        # sentinel window applies as everywhere else.
        if isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Attribute)
                 and isinstance(node.func.value, ast.Name)
                 and node.func.value.id == "subprocess"
                 and node.func.attr in _SUBPROC_CALLS)
                or (isinstance(node.func, ast.Name)
                    and node.func.id in _SUBPROC_CALLS)):
            argv_hits = []
            for a in node.args:
                for sub in ast.walk(a):
                    if isinstance(sub, ast.Call):
                        owner = _read_target(sub, prod_names, sbx)
                        if owner:
                            argv_hits.append(owner)
                    elif isinstance(sub, ast.Name) and sub.id in read_var:
                        argv_hits.append(read_var[sub.id])
            if argv_hits:
                near = "\n".join(src_lines[max(node.lineno - 5, 0):
                                           min(len(src_lines),
                                               node.lineno + 2)])
                if "leak-audit: ok" not in near \
                        and "clock-audit: ok" not in near:
                    owners = sorted(set(argv_hits))
                    hits.append((node.lineno,
                                 f"ARGV SECRET EXPOSURE: the command line "
                                 f"carries the contents of {owners} — "
                                 f"visible in ps/tasklist listings and in "
                                 f"every log that echoes the invocation; "
                                 f"pass a PATTERN FILE PATH (-f), never the "
                                 f"bytes (-e), or sentinel 'leak-audit: ok'"))

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
    # the arm-5 member (Oct 9), parts-built BEFORE the probes dict
    # below consumes it: 'subprocess.run' never appears contiguous in
    # this checker's tracked source (the leak-arm pool rule — a
    # literal here would find itself in any tree-wide argv scan). The
    # probes prefix the module themselves: "subprocess." + _r5.
    _r5 = "r" + "un"
    # the synthetic live value (deterministic, secret-free) is built
    # FIRST — the arm-4 probes' f-string literals interpolate it below,
    # and this checker's own source never carries the contiguous needle
    _syn = "-".join(["scout", "syn", "poison", "probe", "value", "9x"])
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
        # THE READ ARM'S PROBES (the hermeticity sweep, Sep 28): the
        # default-path READ class gets the same permanent treatment.
        "probe_read": (
            "from pathlib import Path\n"
            "BASE = Path(__file__).parent\n"
            "x = (BASE / 'scanner_pulse.json').read_text()\n"
        ),
        "probe_read_ok": (
            "from pathlib import Path\n"
            "BASE = Path(__file__).parent\n"
            "# clock-audit: ok — LIVE-PROBE (deliberate)\n"
            "x = (BASE / 'scanner_pulse.json').read_text()\n"
        ),
        "probe_read_sbx": (
            "import tempfile\n"
            "from pathlib import Path\n"
            "TMP = Path(tempfile.mkdtemp())\n"
            "x = (TMP / 'scanner_pulse.json').read_text()\n"
        ),
        # THE ARM-4 PROBES' FILES: the poison and sentinel probes carry
        # the synthetic value as ONE contiguous literal (the real poison
        # shape — a runtime join is invisible to a static Constant scan
        # and is exactly what the cure-probe celebrates); the literal is
        # f-string-interpolated from the parts-built _syn (defined just
        # below, before the probes dict is consumed) so this checker's
        # own source never carries the whole needle.
        "probe_poison": (
            "from pathlib import Path\n"
            f"PINNED_TOPIC = '{_syn}'\n"
            "assert PINNED_TOPIC\n"
        ),
        "probe_poison_ok": (
            "from pathlib import Path\n"
            "# leak-audit: ok - the deliberate live-value pin, documented\n"
            f"PINNED_TOPIC = '{_syn}'\n"
            "assert PINNED_TOPIC\n"
        ),
        "probe_poison_sbx": (
            "import tempfile\n"
            "from pathlib import Path\n"
            "TMP = Path(tempfile.mkdtemp())\n"
            "# the seed shape: a mkdtemp sandbox file holding the value,\n"
            "# built from parts — the cure pattern itself\n"
            "SEED = '-'.join(['scout', 'syn', 'poison', 'probe', 'value'])\n"
            "(TMP / 'push_topic.txt').write_text(SEED + '\\n')\n"
        ),
        # ══ THE ARM-5 PROBES (the argv hunt, Oct 9): subprocess argvs
        # built from production-file READS must flag, and the safe
        # PATTERN-FILE PATH shape must stay clean. The invocation
        # member is parts-built via _r5 (defined above, before this
        # dict consumes it) so this checker's tracked source never
        # carries the contiguous needle — mirror of the leak-arm's
        # pool rule.
        "probe_argv": (
            "import subprocess\n"
            "from pathlib import Path\n"
            "BASE = Path(__file__).parent\n"
            "subprocess." + _r5 + "(['git', 'grep', '-F', '-e',\n"
            "    (BASE / 'push_topic.txt').read_text()])\n"
        ),
        "probe_argv_var": (
            "import subprocess\n"
            "from pathlib import Path\n"
            "BASE = Path(__file__).parent\n"
            "PAT = (BASE / 'push_topic.txt').read_text()\n"
            f"Popen(['git', 'grep', '-f', PAT])\n"
        ),
        "probe_argv_ok": (
            "import subprocess\n"
            "from pathlib import Path\n"
            "BASE = Path(__file__).parent\n"
            "# leak-audit: ok - the deliberate argv secret, documented\n"
            "subprocess." + _r5 + "(['git', 'grep', '-F', '-e',\n"
            "    (BASE / 'push_topic.txt').read_text()])\n"
        ),
        "probe_argv_sbx": (
            "import subprocess\n"
            "import tempfile\n"
            "from pathlib import Path\n"
            "TMP = Path(tempfile.mkdtemp())\n"
            "# a sandbox read feeds the argv (legit)\n"
            "" + _r5 + "(['git', 'grep', '-e', (TMP / 'push_topic.txt')"
            ".read_text()])\n"
        ),
        "probe_argv_patfile": (
            "import subprocess\n"
            "from pathlib import Path\n"
            "BASE = Path(__file__).parent\n"
            "PATF = BASE / 'ctl_pattern.txt'\n"
            "subprocess." + _r5 + "(['git', 'grep', '-f', str(PATF)])\n"
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
        for name in ("probe_sbx", "probe_fin", "probe_sent", "probe_def",
                     "probe_read_ok", "probe_read_sbx"):
            if hits[name]:
                failures.append(
                    f"{name} must stay clean but was flagged: "
                    f"{hits[name]} — a legitimate arm regressed")
        read_hits = _lines(hits["probe_read"])
        if read_hits != [3]:
            failures.append(
                f"probe_read scored L{read_hits}, expected L[3] — the "
                "default-path READ arm has drifted: either the live-file "
                "read is no longer flagged (the prose-backfill class "
                "re-entered) or the arm's sandbox/sentinel exemptions "
                "over-tightened")

        # ══ ARM 4 PROBES (the dead-poison hunt, Oct 8): the synthetic
        # live map (deterministic, secret-free — never the real live
        # values) drives three probes: the bare poison literal must flag
        # exactly once, the sentinel'd one must stay clean, and the
        # parts-join seed shape (the cure itself — a runtime join is
        # invisible to the static scan AND matches nothing) must stay
        # clean. _syn was built above, before the probes dict consumed
        # it — the f-strings interpolate the whole needle only at probe
        # file creation, never in this checker's tracked source.
        # the synthetic value enters the map through _distinctive_value
        # itself — a neutered filter (mutant) empties the map, the probe
        # flags nothing, and the self-check goes red: the contract gate
        # and the poison arm die together, never one surviving the other
        _syn_map = ({_syn: "synthetic_live.json"}
                    if _distinctive_value(_syn) else {})
        _pmap = {n: audit_file(p, prod_names, live_values=_syn_map)
                 for n, p in (("probe_poison", tdp / "probe_poison.py"),
                              ("probe_poison_ok", tdp / "probe_poison_ok.py"),
                              ("probe_poison_sbx", tdp / "probe_poison_sbx.py"))
                 if p.exists()}
        poison_flagged = [h for h in _pmap.get("probe_poison", [])
                          if "LIVE-VALUE POISON" in h]
        if len(poison_flagged) != 1:
            failures.append(
                f"probe_poison scored {poison_flagged} — expected exactly "
                "one LIVE-VALUE POISON hit; the dead-poison arm has "
                "drifted (missing, or the sentinel/sandbox exemptions "
                "over-tightened)")
        for name in ("probe_poison_ok", "probe_poison_sbx"):
            bad = [h for h in _pmap.get(name, [])
                   if "LIVE-VALUE POISON" in h]
            if bad:
                failures.append(
                    f"{name} must stay clean of LIVE-VALUE POISON but was "
                    f"flagged: {bad} — a legitimate arm of the poison "
                    "check over-tightened")

        # ══ THE HARVEST-PATH PROBE (the fresh-checkout lesson, Oct 8 —
        # the Copilot medium cured here): the probes above inject the
        # synthetic map DIRECTLY, which can never catch a broken
        # HARVESTER (the exact fail-open a fresh checkout would hit:
        # untracked live file absent -> empty map -> arm reports CLEAN
        # having checked nothing). So the self-check exercises
        # live_poison_values END-TO-END on a sandbox root it controls:
        # world A — a source quoting the value + a data file carrying it
        # (the corpus filter must kill it: shared constant); world B —
        # the data file only (the map must carry exactly the synthetic
        # value, the harvester's whole contract in one assertion).
        for _wname, _quote in (("harvest_shared", True),
                               ("harvest_live", False)):
            _w = tdp / _wname
            _w.mkdir()
            (_w / "push_topic.txt").write_text(_syn + "\n", encoding="utf-8")
            if _quote:
                (_w / "seeded_topic.py").write_text(
                    "SEED = " + repr(_syn) + "\n", encoding="utf-8")
            _m = live_poison_values(prod_names={"push_topic.txt"},
                                    root=_w)
            if _quote:
                if _m:
                    failures.append(
                        f"harvest_shared scored {_m} — a value the tracked "
                        "source also carries must be filtered as a shared "
                        "constant, never live state (the corpus pass has "
                        "drifted)")
            else:
                if _m.get(_syn) != "push_topic.txt":
                    failures.append(
                        f"harvest_live scored {_m} — the harvester failed "
                        "to mint the map from a world where the data file "
                        "is the ONLY carrier (glob->read->filter->map has "
                        "drifted); a fresh checkout would fail-open here: "
                        "empty map, arm CLEAN, nothing checked")

        # ══ THE ARM-5 PROBE VERDICTS (the argv hunt, Oct 9): the
        # inline-read argv and the one-hop-variable argv must flag
        # EXACTLY ONE ARGV SECRET EXPOSURE each; the sentinel'd, the
        # sandbox-read, and the pattern-file PATH argv must stay
        # clean of the class. A blind argv arm (pattern gone, shape
        # match broken, map plumbing cut) goes red HERE, every pass.
        _pmap5 = {n: audit_file(p, prod_names)
                  for n, p in (("probe_argv", tdp / "probe_argv.py"),
                               ("probe_argv_var", tdp / "probe_argv_var.py"),
                               ("probe_argv_ok", tdp / "probe_argv_ok.py"),
                               ("probe_argv_sbx", tdp / "probe_argv_sbx.py"),
                               ("probe_argv_patfile",
                                tdp / "probe_argv_patfile.py"))
                  if p.exists()}
        for _need in ("probe_argv", "probe_argv_var"):
            _flag = [h for h in _pmap5.get(_need, [])
                     if "ARGV SECRET EXPOSURE" in h]
            if len(_flag) != 1:
                failures.append(
                    f"{_need} scored {_flag} — expected exactly one "
                    "ARGV SECRET EXPOSURE hit; the argv hunt has "
                    "drifted (missing, or the sandbox/sentinel "
                    "exemptions over-tightened)")
        for _clean in ("probe_argv_ok", "probe_argv_sbx",
                       "probe_argv_patfile"):
            _bad = [h for h in _pmap5.get(_clean, [])
                    if "ARGV SECRET EXPOSURE" in h]
            if _bad:
                failures.append(
                    f"{_clean} must stay clean of ARGV SECRET EXPOSURE "
                    f"but was flagged: {_bad} — a legitimate arm of "
                    "the argv check over-tightened")

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
