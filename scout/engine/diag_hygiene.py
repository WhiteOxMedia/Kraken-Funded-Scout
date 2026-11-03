"""diag_hygiene.py - THE HYGIENE GATE (rebuilt Sep 15 after the deletion
event took the original with it).

WHY THIS EXISTS: the Sep 14 sweep deleted ~200 stray artifacts across
both repos - one-off diag probes, per-suite _bat_test_*.log captures,
scratch DBs the test suites re-created on every run, the bot's literal
`$null` file. Litter re-accumulates silently unless something FAILS on
it. This gate is that something: run it anytime, stdlib only, and
exit-coded (0 clean / 1 findings) so a .bat or scheduler can gate on it.

THE AUTO-SWEEP (Sep 24): the regenerate-able scratch classes no longer
fail the audit - they are REMOVED and reported (SWEPT lines), so the
battery's hygiene entry self-heals root litter instead of going red on
names the suites re-create on demand. Sweepable = provably safe to
delete: RULE A root scratch (_*.db/_*.log/_sbx_*/_bat_*/$*), RULE C
factory scratch (the suites' own re-created names - the redirect
regression stays pinned by the suites' drives), and the RULE D 0-byte
ghost. NEVER swept: RULE B unreferenced diag probes (code - a human
verdict) and a known-dead name carrying LIVE data (an unknown writer -
a human must identify it first). Fail-soft: a file the OS refuses to
release is reported as a stray, never crashes the audit. A swept name
that reappears next run is simply re-swept - the trace is the SWEPT
line, the protection is the sweepable set staying narrow.

THE SWEPT-LEDGER (Sep 24, user: "a recurring scratch name can be spotted
and traced to its writer"): every removal lands a line in reports/
hygiene_swept_ledger.jsonl (ts, name, rule, repo — bounded at the
tailing 500, deliberately OUTSIDE the stalewatcher's purge set, because
a memory collected at 90 minutes is no memory). When a name reaches
RECUR_AT occurrences in the trailing window, the gate prints RECURRING
with the write-census's writer — detection and identification in one
gate, so a suite that regenerates its litter every run stops being
invisible just because the sweep keeps winning.

THE SCRATCH POLICY (Sep 24, same day): volatile artifacts (check
reports, sandbox shapes, db shapes, logs, temp) route into the repo's
scratch/ tree via scratch_space.py, and kraken_stalewatch purges every
scratch dir of 90-minute-old files each battery rotation, writing a
filename/age/timestamp-only summary into reports/. The repo ROOT stops
being a scratch destination entirely. This gate carries the policy's
teeth: after the sweep it runs a WRITE-CENSUS over both repos' sources
to list every drive that still writes a scratch-shaped file at a root
(the stragglers are mid-flight in another workstream's uncommitted
hunks - they stay visible here until their owners migrate them).
Production writers (the scanner's own ledgers and locks: pending_check,
suppressions, the tidebar/flipgrade family...) are pinned exempt by
name in PRODUCTION_WRITES - the census is about SCRATCH, not runtime
state. The gate's own summary joins the allowed report classes in
reports/ (hygiene summaries, per policy).

RULES (both repo roots; anything NESTED below the root is exempt -
subdirectory scratch is deliberate sandbox output):
  A. ROOT SCRATCH of the litter classes the sweep found:
       _*.db  _*.log  _sbx_*  _bat_*  $*
     (_*.txt is deliberately NOT flagged: _tidebarweekly.txt and
     _flipgrade.txt are live production ledgers.)
  B. NEW ONE-OFF PROBES: a diag_* file (any extension) that is
     referenced by NO *.py, *.bat, *.ps1, or *.md in either repo AND is
     not in KEEP_WORLDS. The 34 diags that survived the Sep 14
     reference-audit are pinned in KEEP_WORLDS so this gate never
     re-litigates that verdict; any NEW diag file must be wired into
     the battery (run_battery's DIAG_RUN or a suite) or it flags.
  C. TEST-FACTORY LITTER: the scratch filenames the suites themselves
     re-created on every run (test_tidestamp's DBs) - now redirected
     into tempdirs; if the names reappear at a root, the redirect
     regressed.
  D. KNOWN DEAD ARTIFACTS: files that LOOK like fleet paperwork but are
     fossils, listed in KNOWN_DEAD_ARTIFACTS so a db/ledger audit that
     meets the name reports it as known-dead instead of puzzling over
     it. (The Aug 20 build stubbed trading_bot.db to 0 bytes in the bot
     root; the bot's real ledger is kraken_scout/signals.db. Deleted
     Sep 20; a reappearing empty ghost flags, and a reappearing
     non-empty one flags LOUDER - someone wrote real rows into a name
     nothing has ever opened.)

Run:  .venv\\Scripts\\python.exe -X utf8 diag_hygiene.py
"""

import ast
import json
import re
import sys
import tempfile
import time
from pathlib import Path

SCOUT = Path(__file__).resolve().parent
BOT = Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot")
LOGS = "logs"          # everything under logs/ is expected output, never litter

# The 34 diag files that survived the Sep 14 reference audit (the
# battery-gated worlds, the live tools named in .workshop/context.md,
# and the referenced analyzers). New diag files are NOT auto-added:
# wire them into run_battery's DIAG_RUN or a test suite, then add the
# name here deliberately.
KEEP_WORLDS = {
    "diag_aster", "diag_benchfinal", "diag_benchtodate", "diag_cachesweep",
    "diag_calibrate", "diag_fbw3", "diag_firstflipback", "diag_fleetstudy",
    "diag_flipgrade", "diag_missedmoney", "diag_poolgate", "diag_popupfloor",
    "diag_prefsmirror", "diag_rebaseline", "diag_rebaseline2",
    "diag_replaylive", "diag_silentpass", "diag_sizingaudit", "diag_stshadow",
    "diag_surething", "diag_surething_grade", "diag_surething_refit",
    "diag_sweep", "diag_syrup", "diag_testleaks", "diag_tidebarrefit",
    "diag_tidedata", "diag_tidehistory", "diag_tidememory_hindsight",
    "diag_tideretune", "diag_tomorrowpass", "diag_w38grade",
    "diag_widgetsweep", "diag_hygiene",
}

# test-factory scratch names (RULE C): if these reappear at a root, the
# tempdir redirect regressed
FACTORY_SCRATCH = {"_test_tidestamp.db", "_test_tidestamp_old.db"}

# RULE D - known dead artifacts: names with no code reference in either
# repo (verified Sep 20: zero *.py/*.bat/*.ps1 hits) that exist only to
# confuse audits. Empty ghosts flag as litter; the same name carrying
# real rows flags louder, because data in an orphan name means an
# unknown writer is stashing records where no reader looks.
KNOWN_DEAD_ARTIFACTS = {
    "trading_bot.db",
    # the RULE D sweep (Sep 24): verified zero-reference names across both
    # repos' tracked content. scout.db: the winners tables live in remote
    # Postgres (db.py) — a 0-byte scout.db only ever existed because
    # test_xrp_signals's sqlite3.connect auto-created it (rebirth fixed in
    # the same sweep). popups.jsonl.lock: no writer exists anywhere; the
    # only .json.lock writer is contest_account's own STATE_LOCK_FILE.
    "scout.db",
    "popups.jsonl.lock",
}

RULE_A = re.compile(r"^(_.*\.(?:db|log)|_sbx_.*|_bat_.*|\$.*)$")
REF_EXTS = ("*.py", "*.bat", "*.ps1", "*.md")

# ── the scratch-policy WRITE CENSUS (Sep 24) ────────────────────────────
# Root writes of these name classes are scratch-in-waiting: (a) the RULE
# A/C litter classes themselves, (b) the drive check-report family the
# policy evicted from the root (scratch/reports_temp is their home).
CENSUS_A = re.compile(r"^(_.*\.(?:db|log)|_sbx_.*|_bat_.*|\$.*)$")
CENSUS_B = re.compile(r"^[a-z0-9_]+_(?:check|test)\.txt$", re.IGNORECASE)

# Production writers pinned EXEMPT (exact names): the running scanner
# owns these; a drive writing them is restoring/verifying live state,
# not scratching.
PRODUCTION_WRITES = {
    "pending_check.txt",            # RUNTIME_INVARIANTS runtime state
    "trading_bot.db",               # RULE D special-cases it above
}

# Segments that prove the path is NOT a bare root write: a sandbox or
# OS-temp route. (A `BASE / "name"` join is NOT a skip - the resolver
# below chases the base's binding to decide root vs nested.)
CENSUS_SKIP = re.compile(
    r"scratch_space|report_temp|\bsbx\(|dbshape|os_temp|"
    r"gettempdir|mkdtemp|TemporaryDirectory|NamedTemporaryFile")
# bases whose binding IS the repo root (a join onto them is a root write)
CENSUS_ROOT_BASE = re.compile(
    r"^(BASE|ROOT|HERE|SCOUT|REPO|BASE_DIR|REPO_ROOT)$"
    r"|Path\(__file__\)|dirname\(os\.path\.abspath\(__file__\)\)")

# ── THE SWEPT-LEDGER (Sep 24) ─────────────────────────────────────────
# Runtime state (git-ignored reports/), bounded like the summaries, and
# placed OUTSIDE the stalewatcher's scan set — see the docstring.
LEDGER_PATH = SCOUT / "reports" / "hygiene_swept_ledger.jsonl"
LEDGER_KEEP = 500      # trailing entries kept
RECUR_AT = 3           # occurrences in the trailing window => RECURRING


def rule_a_hit(name: str) -> bool:
    """True when a ROOT-LEVEL name is scratch of the litter classes."""
    return bool(RULE_A.match(name))


def diag_stem(p: Path) -> str:
    return p.stem if p.suffix else p.name


def build_reference_corpus(roots) -> str:
    chunks = []
    for root in roots:
        if not root.is_dir():
            continue
        for pat in REF_EXTS:
            for f in root.glob(pat):
                try:
                    chunks.append(f.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    pass
    return "\n".join(chunks)


def audit(roots=()):
    """Returns (strays, checked_counts). Read-only."""
    roots = list(roots) or [SCOUT, BOT]
    corpus = build_reference_corpus(roots)
    strays, counts = [], {}
    for root in roots:
        if not root.is_dir():
            continue
        n_a = n_b = 0
        for f in sorted(root.iterdir()):
            if not f.is_file() or f.name.startswith("."):
                continue
            n_a += 1
            if f.name in KNOWN_DEAD_ARTIFACTS:
                try:
                    _live = f.stat().st_size > 0
                except OSError:
                    _live = False  # vanished mid-audit: nothing to flag
                if _live:
                    strays.append(
                        (f, "RULE D: known dead artifact name with LIVE "
                            "data - identify the writer before trusting it"))
                else:
                    strays.append(
                        (f, "RULE D: known dead artifact (0-byte ghost) - "
                            "delete it"))
                continue
            if f.name in FACTORY_SCRATCH:
                strays.append(
                    (f, "RULE C: factory scratch (the tempdir redirect "
                        "regressed)"))
                continue
            if rule_a_hit(f.name):
                strays.append((f, "RULE A: root scratch"))
                continue
            if f.name.startswith("diag_"):
                n_b += 1
                stem = diag_stem(f)
                if stem not in KEEP_WORLDS and stem not in corpus:
                    strays.append((f, "RULE B: unreferenced one-off probe"))
        counts[str(root)] = (n_a, n_b)
    return strays, counts


def sweepable(why: str) -> bool:
    """True when this stray's CLASS is one deletion can never regret:
    transient scratch the suites re-create on demand, or a 0-byte
    fossil. Code (RULE B) and unknown-writer data (RULE D live) are
    NEVER swept - they fail, as always, for a human verdict."""
    return (why.startswith("RULE A") or why.startswith("RULE C")
            or (why.startswith("RULE D") and "0-byte ghost" in why))


def sweep(strays: list) -> tuple:
    """Remove the sweepable strays; return (swept, remaining). Fail-soft:
    a file the OS refuses to release (held open by a live process) is
    reported as a remaining stray with the reason, never a crash."""
    swept, remaining = [], []
    for f, why in strays:
        if not sweepable(why):
            remaining.append((f, why))
            continue
        try:
            f.unlink()
            swept.append((f, why))
        except OSError:
            remaining.append(
                (f, why + " [auto-sweep failed: file held open? - delete "
                          "by hand]"))
    return swept, remaining


def selftest() -> int:
    """Pins for the rules themselves (pure-name; hermetic)."""
    ok = True
    cases = [
        ("_probe.db", True), ("_myrun.log", True), ("_sbx_stage.json", True),
        ("_bat_test_x.log", True), ("$null", True),
        ("_tidebarweekly.txt", False),   # live ledger: NOT litter
        ("_flipgrade.txt", False),       # live ledger
        ("popup_suppressions.jsonl", False), ("scanner.lock", False),
        ("_test_tidestamp.db", True),    # factory scratch (also RULE C)
        ("_notes.md", False),            # underscore but not a litter class
    ]
    # RULE D: the known-dead name must stay listed - no pattern rule
    # catches a plain filename, so the list is the only guard
    if "trading_bot.db" not in KNOWN_DEAD_ARTIFACTS:
        print("  FAIL  selftest: trading_bot.db missing from KNOWN_DEAD_ARTIFACTS")
        ok = False
    for name, want in cases:
        got = rule_a_hit(name) or name in FACTORY_SCRATCH
        if got != want:
            print(f"  FAIL  selftest {name}: got {got}, want {want}")
            ok = False
    # nested scratch is exempt (the classifier only sees root names;
    # audit() never descends - pin that here by construction)
    if SCOUT.name in ("", "/"):
        print("  FAIL  selftest: SCOUT resolved oddly")
        ok = False

    # ── the auto-sweep contract (Sep 24) ─────────────────────────────
    # 1. classification: regenerate-able classes sweep; code and
    #    unknown-writer data never do
    cls = [
        ("RULE A: root scratch", True),
        ("RULE C: factory scratch (the tempdir redirect regressed)", True),
        ("RULE D: known dead artifact (0-byte ghost) - delete it", True),
        ("RULE B: unreferenced one-off probe", False),
        ("RULE D: known dead artifact name with LIVE data - identify "
         "the writer before trusting it", False),
    ]
    for why, want in cls:
        got = sweepable(why)
        if got != want:
            print(f"  FAIL  selftest sweepable({why[:28]}...): "
                  f"got {got}, want {want}")
            ok = False

    # 2. behavioral, hermetic: a fake root's regenerate-able strays are
    #    swept, the human-verdict classes survive, the real roots are
    #    never touched
    with tempfile.TemporaryDirectory(prefix="_sbx_hyg_") as td:
        troot = Path(td)
        (troot / "_probe.db").write_bytes(b"x")          # RULE A
        (troot / "_sbx_stage.json").write_bytes(b"x")    # RULE A
        (troot / "_test_tidestamp.db").write_bytes(b"x") # RULE C name
        (troot / "trading_bot.db").write_bytes(b"")      # RULE D ghost
        KNOWN_DEAD_ARTIFACTS.add("_hyg_ghost.db")
        try:
            (troot / "_hyg_ghost.db").write_bytes(b"real rows")  # RULE D live
            (troot / "diag_oneshot.py").write_text(
                "# a one-off probe, referenced nowhere\n", encoding="utf-8")
            strays_t, _c = audit([troot])
            swept_t, left_t = sweep(strays_t)
            got_swept = {f.name for f, _ in swept_t}
            got_left = {f.name for f, _ in left_t}
            want_swept = {"_probe.db", "_sbx_stage.json",
                          "_test_tidestamp.db", "trading_bot.db"}
            want_left = {"_hyg_ghost.db",       # RULE D live: unswept
                         "diag_oneshot.py"}     # RULE B: unswept code
            if got_swept != want_swept or got_left != want_left:
                print(f"  FAIL  selftest sweep behavior: "
                      f"swept={sorted(got_swept)} left={sorted(got_left)}")
                ok = False
            if (not (troot / "diag_oneshot.py").exists()
                    or not (troot / "_hyg_ghost.db").exists()):
                print("  FAIL  selftest sweep behavior: a human-verdict "
                      "stray was deleted")
                ok = False
        finally:
            KNOWN_DEAD_ARTIFACTS.discard("_hyg_ghost.db")

    # ── the write-census pins (scratch policy, Sep 24) ──────────────
    # a fake root: a root check-report write and a root _sbx_ shape flag;
    # a production writer, a monthly jsonl, and a nested write do not.
    with tempfile.TemporaryDirectory(prefix="_sbx_hyg_census_") as td:
        troot = Path(td)
        (troot / "test_somechecker.py").write_text(
            "from pathlib import Path\n"
            "Path('_some_check.txt').write_text('r', encoding='utf-8')\n"
            "open('_sbx_shape.jsonl', 'w').write('r')\n"
            "Path('pending_check.txt').write_text('live', encoding='utf-8')\n"
            "Path('_ledger_monthly.jsonl').write_text('prod', encoding='utf-8')\n"
            "Path('_nested.json').write_text('nested', encoding='utf-8')\n",
            encoding="utf-8")
        got_c = {(f, n) for f, _l, n, _s in census_writers([troot])}
        want_c = {("test_somechecker.py", "_some_check.txt"),
                  ("test_somechecker.py", "_sbx_shape.jsonl")}
        if got_c != want_c:
            print(f"  FAIL  selftest census: got {sorted(got_c)}, "
                  f"want {sorted(want_c)}")
            ok = False

    # ── the swept-ledger pins (Sep 24) ────────────────────────────
    with tempfile.TemporaryDirectory(prefix="_sbx_hyg_ledger_") as td:
        lp = Path(td) / "ledger.jsonl"
        # 1. fail-soft load on an absent file
        if _ledger_load(lp) != []:
            print("  FAIL  selftest ledger: absent file must load empty")
            ok = False
        # 2. round-trip + shape (ts, name, rule, repo)
        _ledger_append([
            {"ts": "t1", "name": "_probe.db", "rule": "RULE A",
             "repo": "r"},
            {"ts": "t2", "name": "_probe.db", "rule": "RULE A",
             "repo": "r"},
            {"ts": "t3", "name": "_probe.db", "rule": "RULE A",
             "repo": "r"},
            {"ts": "t4", "name": "_other.log", "rule": "RULE A",
             "repo": "r"}], lp)
        rows_t = _ledger_load(lp)
        if len(rows_t) != 4 or rows_t[0].get("name") != "_probe.db":
            print(f"  FAIL  selftest ledger: round-trip got {len(rows_t)} rows")
            ok = False
        # 3. recurrence: 3x the same name trips RECUR_AT, the other doesn't
        rec_t = _ledger_recurring(rows_t, {"_probe.db"})
        if rec_t != {"_probe.db": 3}:
            print(f"  FAIL  selftest ledger: recurrence got {rec_t}")
            ok = False
        if _ledger_recurring(rows_t, {"_other.log"}) != {}:
            print("  FAIL  selftest ledger: a 1x name must not recur")
            ok = False
        # 4. the bound: a ledger over KEEP trims to the trailing window
        _real_keep = LEDGER_KEEP
        globals()["LEDGER_KEEP"] = 3
        try:
            _ledger_append([{"ts": "t5", "name": "_n.db", "rule": "RULE A",
                             "repo": "r"}], lp)
            rows_b = _ledger_load(lp)
            if len(rows_b) != 3 or rows_b[-1].get("name") != "_n.db":
                print(f"  FAIL  selftest ledger: bound kept {len(rows_b)}")
                ok = False
        finally:
            globals()["LEDGER_KEEP"] = _real_keep
        # 5. the live ledger lives in reports/ (outside the purge set,
        #    git-ignored) — the memory-survival contract
        if "reports" not in str(LEDGER_PATH):
            print("  FAIL  selftest ledger: LEDGER_PATH must sit in reports/")
            ok = False

    if ok:
        print(f"  selftest: rule pins green ({len(cases)} name cases, "
              f"{len(cls)} sweep classes, 1 behavioral sweep, "
              "write-census, swept-ledger)")
    return 0 if ok else 1


def census_writers(roots=()) -> list:
    """Drives still writing scratch-shaped files at a repo root.
    AST-proper: a bare-Name receiver (OUT.write_text), a root-alias
    join (BASE / "name"), a Path("literal") write, and a bare
    open("name") are all chased one binding level. Known blind spots:
    two-level indirection, f-string/computed names, and the bot repo's
    absolute-path writes. Informational - never fails the audit."""
    roots = list(roots) or [SCOUT, BOT]
    hits = []
    for root in roots:
        if not root.is_dir():
            continue
        for f in sorted(root.glob("*.py")):
            try:
                src = f.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(src)
            except Exception:
                continue   # a drive that cannot parse is not this census's court
            binds: dict = {}
            for node in ast.walk(tree):
                if (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)):
                    binds.setdefault(node.targets[0].id, []).append(node.value)

            def chases_root(var: str) -> bool:
                """True when var's binding names the repo root (or hits
                a sandbox/OS-temp route, which settles it as NOT root)."""
                for b in binds.get(var, []):
                    bsrc = ast.get_source_segment(src, b) or ""
                    if CENSUS_SKIP.search(bsrc):
                        return False
                    if CENSUS_ROOT_BASE.search(bsrc):
                        return True
                return False

            def root_name(val) -> str | None:
                """The root filename when val is a root write, else None."""
                if isinstance(val, ast.Name):
                    vs = binds.get(val.id, [])
                    return root_name(vs[0]) if vs else None
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    return val.value if "/" not in val.value \
                        and "\\" not in val.value else None
                if (isinstance(val, ast.Call)
                        and isinstance(val.func, ast.Name)
                        and val.func.id == "Path" and val.args
                        and isinstance(val.args[0], ast.Constant)
                        and isinstance(val.args[0].value, str)):
                    n = val.args[0].value
                    return n if "/" not in n and "\\" not in n else None
                if (isinstance(val, ast.Call)
                        and isinstance(val.func, ast.Attribute)
                        and val.func.attr == "with_name" and val.args
                        and isinstance(val.args[0], ast.Constant)
                        and isinstance(val.args[0].value, str)):
                    # Path(__file__).with_name("x") - the object decides
                    lsrc = ast.get_source_segment(src, val.func.value) or ""
                    return val.args[0].value if CENSUS_ROOT_BASE.search(lsrc) \
                        else None
                if isinstance(val, ast.BinOp) and isinstance(val.op, ast.Div):
                    left, right = val.left, val.right
                    if not (isinstance(right, ast.Constant)
                            and isinstance(right.value, str)):
                        return None
                    n = right.value
                    if "/" in n or "\\" in n:
                        return None
                    if isinstance(left, ast.Name):
                        if (CENSUS_ROOT_BASE.match(left.id)
                                or chases_root(left.id)):
                            return n
                        return None
                    lsrc = ast.get_source_segment(src, left) or ""
                    return n if CENSUS_ROOT_BASE.search(lsrc) else None
                return None

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                if isinstance(fn, ast.Name) and fn.id == "open" and node.args:
                    shape, val = "open", node.args[0]
                elif isinstance(fn, ast.Attribute) and fn.attr in (
                        "write_text", "write_bytes", "connect", "touch"):
                    shape, val = fn.attr, fn.value
                else:
                    continue
                name = root_name(val)
                if not name or name in PRODUCTION_WRITES:
                    continue
                if not (CENSUS_A.match(name) or CENSUS_B.match(name)):
                    continue
                hits.append((f.name, node.lineno, name, shape))
    return hits


def _ledger_load(path: Path | None = None) -> list:
    """The trailing swept rows (fail-soft: unreadable = empty)."""
    p = path or LEDGER_PATH
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def _ledger_append(rows: list, path: Path | None = None) -> None:
    """Append swept rows, then bound the file to the trailing
    LEDGER_KEEP. Reporting must never crash the gate."""
    p = path or LEDGER_PATH
    if not rows:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")
        all_rows = _ledger_load(p)
        if len(all_rows) > LEDGER_KEEP:
            keep = all_rows[-LEDGER_KEEP:]
            p.write_text(
                "".join(json.dumps(r, sort_keys=True) + "\n" for r in keep),
                encoding="utf-8")
    except OSError:
        pass


def _ledger_recurring(rows: list, names: set) -> dict:
    """Names from THIS sweep whose trailing-window count reached
    RECUR_AT — the recurring-litter signal."""
    counts: dict = {}
    for r in rows:
        n = r.get("name")
        if n in names:
            counts[n] = counts.get(n, 0) + 1
    return {n: c for n, c in counts.items() if c >= RECUR_AT}


def _write_summary(swept, strays, cens, rec=None) -> None:
    """The policy's allowed hygiene-summary class, into reports/.
    Verdicts and names only - never report CONTENT."""
    try:
        rdir = SCOUT / "reports"
        rdir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        p = rdir / f"hygiene_summary_{stamp}.txt"
        lines = [f"HYGIENE SUMMARY - {stamp}",
                 f"  strays remaining: {len(strays)}",
                 f"  auto-swept: {len(swept)}",
                 f"  root-writers pending migration (census): {len(cens)}",
                 f"  recurring swept names: {len(rec or {})}"]
        for f, why in strays:
            lines.append(f"  STRAY  {f}  [{why}]")
        for fname, ln, name, shape in cens:
            lines.append(f"  ROOT-WRITER  {fname}:{ln} -> {name} ({shape})")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        olds = sorted(rdir.glob("hygiene_summary_*.txt"))
        for old in olds[:-30]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        pass   # reporting never crashes the gate


def main() -> int:
    st = selftest()
    strays, counts = audit()
    swept, strays = sweep(strays)
    # the swept-ledger: record THIS sweep's removals, then detect the
    # recurring names against the trailing window (before the census —
    # the RECURRING lines want the writers)
    _rows = [{"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "name": f.name,
              "rule": why.split(":")[0], "repo": str(f.parent)}
             for f, why in swept]
    _ledger_append(_rows)
    _rec = _ledger_recurring(_ledger_load(),
                             {r["name"] for r in _rows}) if _rows else {}
    cens = census_writers()
    print("=" * 60)
    print("  HYGIENE AUDIT - root scratch + one-off probes, both repos")
    print("=" * 60)
    for root, (n_a, n_b) in counts.items():
        print(f"  {root}: {n_a} root files scanned, {n_b} diag files")
    for f, why in swept:
        print(f"  SWEPT  {f.name}  [{why}] - regenerate-able, removed")
    for name, cnt in sorted(_rec.items()):
        writers = [f"{fn}:{ln} ({sh})" for fn, ln, n, sh in cens if n == name]
        wtxt = ", ".join(writers) if writers else \
            "no current root-writer in the census (the writer stopped, " \
            "or the name is not source-visible)"
        print(f"  RECURRING  {name}  x{cnt} in the trailing {LEDGER_KEEP}"
              f"  — writer: {wtxt}")
    # the summary lands ONCE per run (Sep 24: this main used to call
    # _write_summary twice — the rec-bearing one AND a trailing plain
    # one — stamping a duplicate file every run)
    _write_summary(swept, strays, cens, _rec)
    if cens:
        print(f"  CENSUS: {len(cens)} drive(s) still write scratch at a "
              "root (scratch policy pending on those files):")
        for fname, ln, name, shape in cens:
            print(f"    {fname}:{ln}  {shape} -> {name}")
    if strays:
        for f, why in strays:
            print(f"  STRAY  {f}  [{why}]")
        print(f"\nHYGIENE: {len(strays)} STRAY FILE(S) - clean before shipping")
        return 1
    if swept:
        print(f"\nHYGIENE: CLEAN (auto-swept {len(swept)} regenerate-able "
              "stray(s))")
    else:
        print("\nHYGIENE: CLEAN")
    return 0 if st == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
