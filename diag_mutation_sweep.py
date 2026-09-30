"""THE MUTATION SWEEP (diag_mutation_sweep.py): auto-verify that every
drive FAILS when the code it guards is reverted. Mutations are
mechanical, conservative reverts of each guarded module (every return
True<->False, every comparison flipped, every `if` branch dropped, every
non-zero numeric literal +/-10%), applied to a sandbox COPY of the repo
— the real tree is read-only here. A drive is MUTATION-KILLED if it
goes red on the mutated sandbox; a drive that stays green while its
guarded module is sabotaged is VACUOUS (it pins words, not behavior).
Bounded per-drive budgets; a resume journal makes the sweep crash-safe.

Usage:
  uv run python -X utf8 diag_mutation_sweep.py --list
  uv run python -X utf8 diag_mutation_sweep.py --drives test_giveback.py
  uv run python -X utf8 diag_mutation_sweep.py --all
  uv run python -X utf8 diag_mutation_sweep.py --resume
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
SWEEP_DIR = BASE / "artifacts" / "mutation_sweep"
JOURNAL = SWEEP_DIR / "journal.json"
REPORT = SWEEP_DIR / "mutation_sweep_report.txt"
SBX_ROOT = BASE / "_mutation_sbx"

SKIP_COPY = {".git", ".venv", "__pycache__", "logs", "_mutation_sbx",
             "node_modules", "scratch", "_recon"}

DRIVE_TIMEOUT_DEFAULT = 300
HEAVY = {"test_apptest.py": 360, "test_tidebarweekly.py": 360,
         "test_stateclobber.py": 480}

# ── the guarded-module map ───────────────────────────────────────────

def hook_pairs() -> dict[str, list[str]]:
    """hooks/pre-commit: trigger-module -> the drive its maybe_run runs."""
    hook = BASE / "hooks" / "pre-commit"
    if not hook.exists():
        hook = BASE / ".git" / "hooks" / "pre-commit"
    try:
        src = hook.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    pairs: dict[str, list[str]] = {}
    # trigger test: grep -q "module.py" ... maybe_run "$_VAR" drive.py
    for m in re.finditer(
            r'grep -q "([\w./]+\.py)"[^;]*;?\s*then\s*\n'
            r'\s*maybe_run "\$(\w+)"\s+(\S+\.py)', src):
        mod = m.group(1).lstrip("./")
        pairs.setdefault(f"$"+m.group(2), []).append(mod)
        pairs.setdefault(f"$"+m.group(2), []).append(m.group(3))
    return pairs


def drive_imports(drive: Path) -> set[str]:
    names: set[str] = set()
    try:
        tree = ast.parse(drive.read_text(encoding="utf-8",
                                         errors="replace"))
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    names.add(drive.stem)
    return names


def build_map() -> dict[str, set[str]]:
    """guarded-file -> {drive.py that imports it}."""
    repo_pys = [p for p in BASE.glob("*.py")
                if not p.name.startswith("test_")
                and p.name != "diag_mutation_sweep.py"]
    target_names = ({p.name for p in repo_pys}
                    | {p.name for p in (BASE / "ops").glob("*.py")}
                    | {p.name for p in (BASE / "tests").glob("*.py")}
                    | {"pre-commit"})
    imap: dict[str, set[str]] = {}
    for d in sorted(BASE.glob("test_*.py")):
        for imp in drive_imports(d):
            for rel in (f"{imp}.py", f"ops/{imp}.py", f"tests/{imp}.py"):
                if rel.split("/")[-1] in target_names:
                    imap.setdefault(rel, set()).add(d.name)
            if imp == "pre-commit":
                imap.setdefault("hooks/pre-commit", set()).add(d.name)
    return imap


# ── the mutation operator (extracted + self-verifying) ──────
import mutation_ops

mutation_ops.self_test()   # an inert engine
                           # would label healthy drives VACUOUS

# ── the sandbox ──────────────────────────────────────────────────────

def make_sandbox() -> Path:
    if SBX_ROOT.exists():
        shutil.rmtree(SBX_ROOT, ignore_errors=True)
    SBX_ROOT.mkdir(parents=True)
    dst = SBX_ROOT / "repo"
    shutil.copytree(BASE, dst,
                    ignore=shutil.ignore_patterns(*SKIP_COPY),
                    dirs_exist_ok=False)
    return dst


FAIL_PAT = re.compile(
    r"^FAIL \b|^\s*FAIL\s{2}|\b\d+ FAILURES\b"
    r"|RESULT:.*\b[1-9]\d* failed\b"
    r"|Traceback \(most recent call last\)", re.M)


def run_drive(sbx: Path, drive: str, timeout: int) -> dict:
    """RED = a non-zero exit OR failure shapes in stdout — the sweep
    found drives that print '5 FAILURES' and exit 0 (an exit-code-blind
    drive passes the gate while red), so rc alone under-reports redness
    and mislabeled real catches as VACUOUS."""
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", drive],
            cwd=str(sbx), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
        rc = proc.returncode
        out = proc.stdout or ""
        err = (proc.stderr or "")[-1500:]
    except subprocess.TimeoutExpired:
        rc, out, err = "TIMEOUT", "", f"timeout after {timeout}s"
    except OSError as exc:
        rc, out, err = "OSERROR", "", repr(exc)[:300]
    red = (rc not in (0,)) or bool(FAIL_PAT.search(out))
    exit_blind = (rc == 0) and bool(FAIL_PAT.search(out))
    return {"rc": rc, "red": red, "exit_blind": exit_blind,
            "secs": round(time.time() - t0, 1), "err": err}


def journal_load() -> dict:
    if JOURNAL.exists():
        try:
            return json.loads(JOURNAL.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def journal_save(doc: dict) -> None:
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    JOURNAL.write_text(json.dumps(doc, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--drives", type=str, default="")
    ap.add_argument("--modules", type=str, default="")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--recheck-vacuous", action="store_true",
                    help="re-run every journal-VACUOUS drive with the "
                         "output-aware classifier")
    ap.add_argument("--keep-sbx", action="store_true")
    args = ap.parse_args()

    imap = build_map()
    drives = sorted(p.name for p in BASE.glob("test_*.py"))
    if args.list:
        print(f"{len(drives)} drives; {len(imap)} guarded modules")
        for mod in sorted(imap):
            print(f"  {mod:<30} {', '.join(sorted(imap[mod]))}")
        orphan = [d for d in drives
                  if d not in {x for ds in imap.values() for x in ds}]
        print(f"\ndrives mapping to NO guarded module: {len(orphan)}")
        for d in orphan:
            print(f"  {d}")
        return 0

    if args.drives:
        want = [d.strip() for d in args.drives.split(",") if d.strip()]
    elif args.modules:
        want = sorted({d for m in args.modules.split(",")
                       for d in imap.get(m.strip(), [])})
    elif args.recheck_vacuous:
        want = sorted(k for k, e in journal_load()
                      .get("results", {}).items()
                      if e.get("verdict") == "VACUOUS")
    elif args.all or args.resume:
        want = drives
    else:
        print("nothing to do: pass --list, --all, --drives, --modules "
              "or --resume")
        return 2

    doc = journal_load() if args.resume else {"results": {}}
    results: dict[str, dict] = doc.setdefault("results", {})
    if args.recheck_vacuous:
        for k in want:
            results.pop(k, None)   # re-judged with the new classifier
    sbx = None
    try:
        for name in want:
            if name in results:
                continue
            if sbx is None:
                print("building sandbox copy...", flush=True)
                sbx = make_sandbox()
            timeout = HEAVY.get(name, DRIVE_TIMEOUT_DEFAULT)
            entry: dict = {}
            b = run_drive(sbx, name, timeout)
            entry["baseline"] = {k: b[k] for k in ("rc", "red", "secs",
                                                   "exit_blind")}
            if b["red"]:
                entry["verdict"] = "BASELINE-RED"
                entry["detail"] = ("EXIT-CODE-BLIND at baseline: prints "
                                   "failures, exits 0"
                                   if b.get("exit_blind")
                                   else f"baseline rc={b['rc']}: {b['err'][-200:]}")
            else:
                mods = sorted(m for m, ds in imap.items() if name in ds)
                if not mods:
                    entry["verdict"] = "UNMAPPED"
                    entry["detail"] = "no guarded module maps to it"
                else:
                    mres = {}
                    for mod in mods:
                        sp = sbx / mod
                        if not sp.exists():
                            sp = sbx / mod.replace("hooks/", "")
                            if not sp.exists():
                                continue
                        orig = sp.read_text(encoding="utf-8")
                        mut_src, n = mutation_ops.mutate_source(orig)
                        if not n:
                            mres[mod] = {"rc": "NOMUT", "red": False,
                                         "secs": 0.0}
                            continue
                        sp.write_text(mut_src, encoding="utf-8")
                        m = run_drive(sbx, name, timeout)
                        mres[mod] = {k: m[k] for k in ("rc", "red",
                                                       "secs")}
                        mres[mod]["mutations"] = n
                        sp.write_text(orig, encoding="utf-8")
                    entry["mutation"] = mres
                    if any(v["red"] for v in mres.values()):
                        blind = any(v.get("exit_blind")
                                    for v in mres.values() if v["red"])
                        entry["verdict"] = "KILLED"
                        entry["detail"] = "; ".join(
                            f"{m}: rc={v['rc']} ({v.get('mutations','?')} mutations)"
                            for m, v in mres.items() if v["red"])
                        if blind:
                            entry["detail"] += "  [EXIT-CODE-BLIND: " \
                                "red only in output, exits 0]"
                    else:
                        entry["verdict"] = "VACUOUS"
                        entry["detail"] = \
                            "stayed green under every mutation"
            results[name] = entry
            journal_save({"results": results, "at": time.time()})
            print(f"[{len(results)}/{len(want)}] {name}: "
                  f"{entry['verdict']}  {entry.get('detail', '')[:120]}",
                  flush=True)
    finally:
        if sbx is not None and not args.keep_sbx:
            shutil.rmtree(SBX_ROOT, ignore_errors=True)

    # ── the report ───────────────────────────────────────────────────
    counts = {v: sum(1 for e in results.values()
                     if e.get("verdict") == v)
              for v in ("VACUOUS", "TIMEOUT", "BASELINE-RED", "UNMAPPED",
                        "KILLED")}
    lines = [f"THE MUTATION SWEEP — {time.strftime('%Y-%m-%d %H:%M')}",
             "=" * 78]
    for v in ("VACUOUS", "TIMEOUT", "BASELINE-RED", "UNMAPPED"):
        rows = sorted(k for k, e in results.items()
                      if e.get("verdict") == v)
        if rows:
            lines.append(f"\n{v} ({len(rows)}):")
            for r in rows:
                lines.append(f"  {r}  [{results[r].get('detail', '')[:90]}]")
    killed = sorted(k for k, e in results.items()
                    if e.get("verdict") == "KILLED")
    lines.append(f"\nKILLED ({len(killed)}):")
    lines += [f"  {r}" for r in killed]
    lines.append("\n" + "=" * 78)
    lines.append(f"total {len(results)}: killed {counts['KILLED']}, "
                 f"VACUOUS {counts['VACUOUS']}, baseline-red "
                 f"{counts['BASELINE-RED']}, unmapped {counts['UNMAPPED']}, "
                 f"timeout {counts['TIMEOUT']}")
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
