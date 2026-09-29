"""THE STATE-WRITER TRIPWIRE (the cross-writer sweep's endgame, Sep 28):
the clobber class — torn writes, stale whole-file rewrites, resurrected
deletions — was closed writer by writer, but nothing stopped the NEXT
script from opening the door again. This diagnostic inventories every
production writer of every shared state file (AST, both repos) and
FAILS on:

  R1 UNREGISTERED WRITER — a (file, writer) pair missing from REGISTRY
     below. A new writer either joins the registry WITH a note or the
     repo refuses the commit.
  R2 NON-ATOMIC SAVE     — a writer whose unit lacks the tmp +
     os.replace rename for its target file.
  R3 RESURRECTION SHAPE  — a merge-file writer whose unit pops dict
     keys with no None-guard (pop-then-merge resurrects rows under
     None-means-delete).

Self-test fixtures (good writer / bypasser / popper / clean merger)
prove the scanner before the fleet is judged. Run:
    uv run python -X utf8 diag_statewriters.py
Exit 1 on any FAIL. The registry is ground truth — extend it, never
bypass it.
"""
from __future__ import annotations

import ast
import shutil
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
BOT = BASE.parent / "kraken_xrp_trading_bot"

WRITE_ATTRS = {"dump", "write", "write_text", "write_bytes"}

# (file, writer) -> meta. `merge: True` marks the None-means-delete
# convention (R3 applies); "allow_non_atomic" spares a one-shot ONLY
# with a written reason.
REGISTRY: dict[tuple[str, str], dict] = {
    ("contest_watch.json", "background_scanner._save_contest_watch"): {
        "merge": True, "note": "scanner side; None-means-delete + atomic"},
    ("contest_watch.json", "app._save_watch"): {
        "merge": True, "note": "console side; tombstones + atomic"},
    ("contest_watch.json", "bot_engine._upsert_bot_watch_row"): {
        "merge": True, "note": "bot side; tombstone-honoring, atomic"},
    ("contest_watch.json", "contest_account.new_contest"): {
        "note": "the new-contest wipe; atomic {}"},
    ("contest_watch.json", "add_xrp_watch.<module>"): {
        "note": "operator one-shot; merge-read, atomic, tombstone-aware"},
    ("contest_watch.json", "set_bnb_size.<module>"): {
        "note": "operator one-shot; merge-read, atomic, tombstone-aware"},
    ("profit_guard_state.json", "profit_guard._save"): {
        "merge": True, "note": "owner-keys merge-save + atomic"},
    ("meta_layers_state.json", "meta_layers._save"): {
        "merge": True, "note": "merge-save with _SAVE_DROPS + atomic"},
    ("scanner_state.json", "background_scanner._save_scanner_state"): {
        "note": "reboot stamps incl. the aa_canary watermark; atomic"},
    ("execution_state.json", "execution_tiers._save_state"): {
        "note": "bot-owned tiers state; atomic"},
    ("execution_state.json", "clean_expired_blacklist.<module>"): {
        "note": "bookkeeping one-shot; atomic + .bak"},
    ("console_state.json", "app._save_console_state"): {
        "note": "console-owned tier state; atomic"},
    ("payout_track.json", "app.track_payouts"): {
        "note": "console-owned payout ledger; atomic"},
    ("scanner_pulse.json", "background_scanner._pulse_beat"): {
        "note": "the heartbeat; atomic"},
}

EXCLUDE_PREFIX = ("test_", "diag_", "_")
EXCLUDE_NAMES = {"health_check.py", "ready_check.py"}


def prod_modules() -> list[tuple[Path, str]]:
    out = [(p, p.stem) for p in sorted(BASE.glob("*.py"))
           if not p.name.startswith(EXCLUDE_PREFIX)
           and p.name not in EXCLUDE_NAMES]
    out += [(p, f"ops/{p.stem}") for p in sorted(BASE.glob("ops/*.py"))
            if not p.name.startswith(EXCLUDE_PREFIX)]
    for name in ("bot_engine.py", "bot_runner.py"):
        p = BOT / name
        if p.exists():
            out.append((p, name.removesuffix(".py")))
    # modules imported under an alias inside functions (invisible to
    # name-based discovery) join the census explicitly
    for extra in ("contest_account.py",):
        p = BASE / extra
        if p.exists() and all(pp != p for pp, _l in out):
            out.append((p, extra.removesuffix(".py")))
    return out


def _attr(n: ast.Call) -> str:
    f = n.func
    return (f.attr if isinstance(f, ast.Attribute)
            else f.id if isinstance(f, ast.Name) else "")


def _basename(name: str) -> str:
    """A full path (the bot's raw-string constant) reduces to its
    basename — the registry governs files by NAME across both repos."""
    if "/" in name or "\\" in name:
        name = name.replace("\\", "/").rsplit("/", 1)[-1]
    return name


def _resolve(val, files: dict[str, str],
             helpers: dict[str, str] | None = None) -> set[str]:
    """Governed file names reachable in an expression: string literals,
    Names mapped by module constants or unit-local assignments, and —
    when `helpers` is given — helper CALLS (a zero-arg same-module
    function deriving the file). `.tmp` forms normalize to base."""
    out: set[str] = set()
    for a in ast.walk(val):
        name = None
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            name = a.value
        elif isinstance(a, ast.Name) and a.id in files:
            name = files[a.id]
        elif (isinstance(a, ast.Call) and helpers
              and isinstance(a.func, ast.Name)
              and a.func.id in helpers):
            name = helpers[a.func.id]
        if not name:
            continue
        name = _basename(name)
        if name.endswith(".json.tmp"):
            out.add(name[:-4])
        elif name.endswith(".json"):
            out.add(name)
    return out


def _open_writes(n: ast.Call) -> bool:
    """An open() with a write-intent mode (w/a/x, + variants). Read-mode
    opens are reads, never writers."""
    mode = None
    if len(n.args) >= 2 and isinstance(n.args[1], ast.Constant):
        mode = n.args[1].value
    for kw in n.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    if mode is None:
        return False        # bare open() defaults to read
    return bool(set(str(mode)) & set("wax"))


def _call_paths(n: ast.Call, files: dict[str, str],
                helpers: dict[str, str]) -> set[str]:
    """The PATH arguments of an IO call — positional, so a data payload
    never drags a helper's file into the attribution:
      open(path, ...)            -> args[0]
      os.replace(a, b)           -> args[0] and args[1]
      json.dump(data, fh)        -> args[1]
      fh.write_text/data (attr)  -> the receiver expression
    """
    if isinstance(n.func, ast.Name):
        attr = n.func.id
        if attr == "open" and n.args:
            return _resolve(n.args[0], files, helpers)
        if attr == "replace":
            out: set[str] = set()
            for a in n.args[:2]:
                out |= _resolve(a, files, helpers)
            return out
        if attr == "dump" and len(n.args) >= 2:
            return _resolve(n.args[1], files, helpers)
        return set()
    # the ATTRIBUTE forms: os.replace(a, b) and Path.replace(b) — the
    # rename the atomicity rule looks for usually rides the os module
    if _attr(n) == "replace":
        out: set[str] = set()
        for a in n.args[:2]:
            out |= _resolve(a, files, helpers)
        return out
    recv = getattr(n.func, "value", None)
    out = _resolve(recv, files, helpers) if recv is not None else set()
    if _attr(n) == "dump" and len(n.args) >= 2:
        out |= _resolve(n.args[1], files, helpers)
    return out


def _unit_bodies(tree: ast.Module) -> list[tuple[str, list[ast.stmt]]]:
    """(name, statements) per unit. The module unit is ONLY its
    top-level non-def statements (a def's calls belong to the def)."""
    mod_stmts = [s for s in tree.body
                 if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef))]
    bodies: list[tuple[str, list[ast.stmt]]] = [("<module>", mod_stmts)]
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bodies.append((node.name, node.body))
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    bodies.append((f"{node.name}.{sub.name}", sub.body))
    return bodies


def scan_module(path: Path, label: str) -> tuple[set[tuple[str, str, bool]],
                                                 set[tuple[str, str]]]:
    """{(file, writer, atomic)} writers + {(file, writer)} R3 pops.
    Governed/merge sets read from REGISTRY at CALL time (self-tests
    swap it). One-hop helper derivation: `p = _state_file()`."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return set(), set()
    governed = {f for (f, _w) in REGISTRY}
    merge_files = {f for (f, _w), m in REGISTRY.items() if m.get("merge")}

    const_files: dict[str, str] = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            refs = _resolve(node.value, {})
            if len(refs) == 1:
                const_files[node.targets[0].id] = next(iter(refs))
    # one-hop helper derivation: a zero-arg same-module function whose
    # body yields exactly one governed file name maps its name to it
    helper_files: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.args.args:
            refs = _resolve(node, const_files)
            if len(refs) == 1 and next(iter(refs)) in governed:
                helper_files[node.name] = next(iter(refs))

    writers, bad_pops = set(), set()
    for unit, body in _unit_bodies(tree):
        files = dict(const_files)
        # unit-local bindings AT ANY DEPTH (try bodies, ifs, loops —
        # where every real writer lives), in source order: a variable
        # maps to a governed file when its value IS the reference
        # (direct literals/names, a with-open of the tmp, or a bare
        # helper call); a helper call nested inside a larger template
        # is data, never the target
        binds: list[tuple[int, ast.AST]] = []
        for stmt in body:
            for n in ast.walk(stmt):
                if isinstance(n, ast.Assign) and len(n.targets) == 1 \
                        and isinstance(n.targets[0], ast.Name):
                    binds.append((getattr(n, "lineno", 0), n))
                elif isinstance(n, (ast.With, ast.AsyncWith)):
                    binds.append((getattr(n, "lineno", 0), n))
        for _ln, node in sorted(binds, key=lambda b: b[0]):
            if isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if isinstance(item.optional_vars, ast.Name):
                        refs = _call_paths(item.context_expr, files,
                                           helper_files) & governed
                        if len(refs) == 1:
                            files[item.optional_vars.id] = next(iter(refs))
            else:
                v = node.value
                refs = _resolve(v, files)            # direct only
                if not refs and isinstance(v, ast.Call) \
                        and isinstance(v.func, ast.Name) \
                        and v.func.id in helper_files:
                    refs = {helper_files[v.func.id]}  # the bare alias
                # only GOVERNED files matter for the chain (a template
                # like STATE_FILE.parent / "watch.json" also drags the
                # receiver's own non-governed state in — noise), and an
                # ambiguous multi-governed value binds nothing
                refs &= governed
                if len(refs) == 1:
                    files[node.targets[0].id] = next(iter(refs))
        # the write calls: collect every IO target + the rename set
        unit_targets: set[str] = set()
        unit_replaces: set[str] = set()
        for stmt in body:
            for n in ast.walk(stmt):
                if not (isinstance(n, ast.Call)
                        and isinstance(n.func, (ast.Attribute, ast.Name))):
                    continue
                attr = _attr(n)
                if attr == "open":
                    if _open_writes(n) and n.args:
                        unit_targets |= _resolve(n.args[0], files,
                                                 helper_files)
                elif attr == "replace":
                    unit_replaces |= _call_paths(n, files, helper_files)
                elif attr in WRITE_ATTRS:
                    unit_targets |= _call_paths(n, files, helper_files)
        for f in sorted(unit_targets):
            if f not in governed:
                continue
            atomic = f in unit_replaces
            writers.add((f, f"{label}.{unit}", atomic))
            if f in merge_files and _pops_without_guard(body):
                bad_pops.add((f, f"{label}.{unit}"))
    return writers, bad_pops


def _pops_without_guard(body: list[ast.stmt]) -> bool:
    """A dict .pop(...) whose key is not a state/tmp file name, with no
    `is None` comparison anywhere in the unit (the None-guard is the
    merge convention's drop-pass or tombstone check)."""
    has_pop = False
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Call) and _attr(n) == "pop":
                arg = n.args[0] if n.args else None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                        and (arg.value.endswith(".json")
                             or arg.value.endswith(".tmp")):
                    continue        # unlinking a tmp file, not the convention
                has_pop = True
    if not has_pop:
        return False
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Compare):
                for c in (n.left, *n.comparators):
                    if isinstance(c, ast.Constant) and c.value is None:
                        return False
    return True


def scan_fleet() -> tuple[set[tuple[str, str, bool]], set[tuple[str, str]]]:
    writers, bad_pops = set(), set()
    for p, label in prod_modules():
        w, b = scan_module(p, label)
        writers |= w
        bad_pops |= b
    return writers, bad_pops


# ── the drive harness ────────────────────────────────────────────────
results: list[tuple[str, bool, object]] = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def _selftest():
    """Fixtures: good writer, non-atomic bypasser, popper, clean merger.
    The scanner catches each BY NAME — a tripwire that cannot trip is
    decoration."""
    sbx = Path(tempfile.mkdtemp())
    (sbx / "mod_good.py").write_text(
        "STATE = 'shared_state.json'\n"
        "import os, json\n"
        "def save_state(st):\n"
        "    tmp = STATE + '.tmp'\n"
        "    with open(tmp, 'w') as fh:\n"
        "        json.dump(st, fh)\n"
        "    os.replace(tmp, STATE)\n"
        "def load_state():\n"
        "    with open(STATE, 'r') as fh:\n"
        "        return json.load(fh)\n", encoding="utf-8")
    (sbx / "mod_bypass.py").write_text(
        "STATE = 'shared_state.json'\n"
        "import json\n"
        "def save_state(st):\n"
        "    with open(STATE, 'w') as fh:\n"
        "        json.dump(st, fh)\n", encoding="utf-8")
    (sbx / "mod_pop.py").write_text(
        "STATE = 'merge_state.json'\n"
        "import os, json\n"
        "def save_state(st, pair):\n"
        "    st.pop(pair, None)\n"
        "    tmp = STATE + '.tmp'\n"
        "    with open(tmp, 'w') as fh:\n"
        "        json.dump(st, fh)\n"
        "    os.replace(tmp, STATE)\n", encoding="utf-8")
    (sbx / "mod_merger.py").write_text(
        "STATE = 'merge_state.json'\n"
        "import os, json\n"
        "def save_state(st):\n"
        "    drops = {k for k, v in st.items() if v is None}\n"
        "    merged = {k: v for k, v in st.items() if k not in drops}\n"
        "    tmp = STATE + '.tmp'\n"
        "    with open(tmp, 'w') as fh:\n"
        "        json.dump(merged, fh)\n"
        "    os.replace(tmp, STATE)\n", encoding="utf-8")

    saved = dict(REGISTRY)
    REGISTRY.clear()
    REGISTRY.update({
        ("shared_state.json", "mod_good.save_state"): {"note": "ok"},
        ("shared_state.json", "mod_bypass.save_state"): {"note": "violation"},
        ("merge_state.json", "mod_pop.save_state"): {"merge": True,
                                                     "note": "violation"},
        ("merge_state.json", "mod_merger.save_state"): {"merge": True,
                                                        "note": "ok"},
    })
    try:
        w_good, _ = scan_module(sbx / "mod_good.py", "mod_good")
        check("selftest: the good writer is discovered and ATOMIC",
              ("shared_state.json", "mod_good.save_state", True) in w_good,
              sorted(w_good))
        check("selftest: a read-mode open is never a writer",
              not any(w.endswith("load_state") for _f, w, _a in w_good),
              sorted(w_good))
        w_bypass, _ = scan_module(sbx / "mod_bypass.py", "mod_bypass")
        check("selftest: the bypasser is discovered NON-ATOMIC (R2 bites)",
              ("shared_state.json", "mod_bypass.save_state", False) in w_bypass,
              sorted(w_bypass))
        _, pops_bad = scan_module(sbx / "mod_pop.py", "mod_pop")
        check("selftest: the popper trips the resurrection rule (R3 bites)",
              ("merge_state.json", "mod_pop.save_state") in pops_bad,
              sorted(pops_bad))
        _, pops_ok = scan_module(sbx / "mod_merger.py", "mod_merger")
        check("selftest: the None-guarded merger does NOT trip R3 (the "
              "convention itself is not the crime)",
              not pops_ok, sorted(pops_ok))
    finally:
        REGISTRY.clear()
        REGISTRY.update(saved)
    shutil.rmtree(sbx, ignore_errors=True)


def main() -> int:
    _selftest()

    found, bad_pops = scan_fleet()
    reg_pairs = set(REGISTRY)

    unreg = sorted(f"{f} <- {w}" for f, w, _a in sorted(found)
                   if (f, w) not in reg_pairs)
    check("R1 every production writer of a governed state file is "
          "REGISTERED (new writer: join the registry with a note, or "
          "the repo refuses)",
          not unreg, unreg)

    ghost = sorted(f"{f} <- {w}" for (f, w) in sorted(reg_pairs)
                   if "<module>" not in w
                   and not any(ff == f and ww == w for ff, ww, _a in found))
    check("R1b the registry names no ghost writers (a rename updates it)",
          not ghost, ghost)

    non_atomic = sorted(
        f"{f} <- {w}" for (f, w), meta in sorted(REGISTRY.items())
        if not meta.get("allow_non_atomic")
        and not any(ff == f and ww == w and atomic
                    for ff, ww, atomic in found))
    check("R2 every registered writer saves ATOMICALLY (tmp + "
          "os.replace) unless allow_non_atomic with a reason",
          not non_atomic, non_atomic)

    check("R3 no merge-file writer POPS the dict it saves without a "
          "None-guard (pop-then-merge resurrects deleted rows)",
          not bad_pops, sorted(bad_pops))

    fails = [r for r in results if not r[1]]
    rep = BASE / "artifacts" / "statewriters" / "diag_statewriters.txt"
    rep.parent.mkdir(parents=True, exist_ok=True)
    rep.write_text("\n".join(
        f"{'PASS' if ok else 'FAIL'}  {n}"
        + (f"  [{d}]" if d and not ok else "")
        for n, ok, d in results)
        + f"\n\nRESULT: {len(results) - len(fails)} passed, "
          f"{len(fails)} failed\n\n"
        + "CENSUS (the frozen ground truth):\n"
        + "\n".join(f"  {'ATOMIC' if a else 'RAW   '}  {f} <- {w}"
                    for f, w, a in sorted(found))
        + "\n", encoding="utf-8")
    print(f"RESULT: {len(results) - len(fails)} passed, "
          f"{len(fails)} failed")
    if fails:
        for f, w, a in sorted(found):
            print(f"  {'ATOMIC' if a else 'RAW   '}  {f} <- {w}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
