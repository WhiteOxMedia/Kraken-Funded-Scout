"""THE BOT FLEET'S MUTATION AUDIT (Sep 30) — the scout's mutation-kill
standard carried to the sibling bot repo's five drives.

Mirrors diag_mutation_sweep.py's protocol exactly:
  · sandbox copytree of the bot repo (excl .git/.venv/__pycache__/logs),
  · mutates ALL SITES AT ONCE per module (mutation_ops.mutate_source),
  · output-aware judge — red = rc != 0 OR FAIL_PAT in stdout
    (FAIL lines / N FAILURES / RESULT: N failed / Traceback), so an
    exit-blind red still reads red,
  · verdicts: KILLED (>=1 module's mutant turned the drive red) /
    BASELINE-RED / VACUOUS / LIVE (no verdict contract at all — the
    scoreboard's live-API diagnostic shape; the scout would class it),
  · the drive map is the honest bot equivalent of the sweep's
    build_map: AST runtime imports + the drives' own source-slice
    consumption (learningplate/lockguard exec source; the map adds
    what those slices read so the mutator reaches the guarded code).

Scout-side artifacts (never touches the bot repo):
  scratch/bot_mutation_journal.json
  scratch/bot_mutation_report.txt

Usage: python scratch/bot_mutation_audit.py [--keep-sbx]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import mutation_ops

SCOUT = Path(__file__).resolve().parent.parent
BOT = Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot")
DRIVES = ["test_fills.py", "test_learningplate.py", "test_lockguard.py",
          "test_rearmmirror.py", "test_scoreboard.py"]

# the guarded-module map: runtime imports first (AST-verified against
# the drives below), then the source-slice reads the exec-shape drives
# consume. test_scoreboard maps to nothing: it is a live diagnostic.
DRIVE_MODULES = {
    "test_fills.py": ["fills.py", "scout_signal_reader.py", "bot_engine.py"],
    "test_learningplate.py": ["app.py"],
    "test_lockguard.py": ["bot_runner.py"],
    "test_rearmmirror.py": ["signal_bridge.py", "bot_engine.py",
                            "scout_signal_reader.py"],
    "test_scoreboard.py": [],
}

SKIP_COPY = shutil.ignore_patterns(
    ".git", ".venv", "__pycache__", "logs", "*.db", "*.lock",
    "bot_heartbeat.txt", ".workshop")

FAIL_PAT = re.compile(
    r"^FAIL \b|^\s*FAIL\s{2}|\b\d+ FAILURES\b"
    r"|RESULT:.*\b[1-9]\d* failed\b"
    r"|Traceback \(most recent call last\)", re.M)

SBX_PARENT = Path(tempfile.gettempdir()) / "bot_mutation_audit"


def run_drive(sbx: Path, drive: str, timeout: int) -> dict:
    """The sweep's judge, verbatim: red = rc != 0 OR FAIL_PAT in stdout."""
    t0 = time.time()
    py = sbx / ".venv_real" / "Scripts" / "python.exe"
    exe = str(py) if py.exists() else sys.executable
    try:
        proc = subprocess.run(
            [exe, "-X", "utf8", drive],
            cwd=str(sbx), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
        rc = proc.returncode
        out = proc.stdout or ""
        err = (proc.stderr or "")[-1500:]
    except subprocess.TimeoutExpired:
        rc, out, err = "TIMEOUT", "", f"timeout after {timeout}s"
    except OSError as exc:
        rc, out, err = "OSERROR", "", repr(exc)[:300]
    red = (rc != 0) or bool(FAIL_PAT.search(out))
    exit_blind = (rc == 0) and bool(FAIL_PAT.search(out))
    return {"rc": rc, "red": red, "exit_blind": exit_blind,
            "secs": round(time.time() - t0, 1), "out": out[-2500:],
            "err": err}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-sbx", action="store_true")
    args = ap.parse_args()

    mutation_ops.self_test()   # an inert engine would label drives VACUOUS
    SBX_PARENT.mkdir(exist_ok=True)
    sbx = Path(tempfile.mkdtemp(prefix="botsbx_", dir=SBX_PARENT)) / "repo"

    results: dict = {}
    try:
        print(f"building bot sandbox copy -> {sbx}", flush=True)
        shutil.copytree(BOT, sbx, ignore=SKIP_COPY)
        # venv: the bot's own interpreter rides OUTSIDE the copy (junction,
        # instant) — the drives' imports resolve against the real packages.
        real_venv = BOT / ".venv"
        if real_venv.exists():
            target = sbx / ".venv_real"
            subprocess.run(["cmd", "/c", "mklink", "/J",
                            str(target), str(real_venv)],
                           capture_output=True, text=True)
        print(f"sandbox ready in {time.strftime('%H:%M:%S')}", flush=True)

        for drive in DRIVES:
            timeout = 300
            entry: dict = {"modules": DRIVE_MODULES[drive]}
            b = run_drive(sbx, drive, timeout)
            entry["baseline"] = {k: b[k] for k in ("rc", "red", "secs",
                                                   "exit_blind")}
            print(f"[baseline] {drive}: rc={b['rc']} red={b['red']} "
                  f"({b['secs']}s)", flush=True)
            if DRIVE_MODULES[drive] == []:
                entry["verdict"] = "LIVE"
                entry["detail"] = ("no verdict contract (prints an "
                                   "accounting summary; live keyring + "
                                   "Kraken API) — baseline-only, the "
                                   "mutation sweep would class it")
                results[drive] = entry
                continue
            if b["red"]:
                entry["verdict"] = "BASELINE-RED"
                entry["detail"] = (
                    "EXIT-CODE-BLIND at baseline: prints failures, exits 0"
                    if b["exit_blind"] else
                    f"baseline rc={b['rc']}: {b['err'][-160:]}")
                results[drive] = entry
                continue
            mres = {}
            for mod in DRIVE_MODULES[drive]:
                sp = sbx / mod
                if not sp.exists():
                    mres[mod] = {"rc": "ABSENT", "red": False, "secs": 0.0}
                    continue
                orig = sp.read_text(encoding="utf-8")
                mut_src, n = mutation_ops.mutate_source(orig)
                if not n:
                    mres[mod] = {"rc": "NOMUT", "red": False, "secs": 0.0}
                    continue
                sp.write_text(mut_src, encoding="utf-8")
                m = run_drive(sbx, drive, timeout)
                mres[mod] = {"rc": m["rc"], "red": m["red"],
                             "secs": m["secs"], "mutations": n}
                if m["red"]:
                    mres[mod]["evidence"] = (m["out"] or m["err"])[-160:]
                sp.write_text(orig, encoding="utf-8")
                print(f"  [{drive}] {mod}: rc={m['rc']} red={m['red']} "
                      f"({n} mutation sites, {m['secs']}s)", flush=True)
            entry["mutation"] = mres
            red_mods = [m for m, v in mres.items() if v["red"]]
            if red_mods:
                entry["verdict"] = "KILLED"
                entry["detail"] = "; ".join(
                    f"{m}: rc={v['rc']} ({v.get('mutations', '?')} mutations)"
                    for m, v in mres.items() if v["red"])
            else:
                entry["verdict"] = "VACUOUS"
                entry["detail"] = "stayed green under every mutation"
            results[drive] = entry
            (SCOUT / "scratch" / "bot_mutation_journal.json").write_text(
                json.dumps(results, indent=1), encoding="utf-8")
    finally:
        if not args.keep_sbx:
            shutil.rmtree(SBX_PARENT, ignore_errors=True)

    # ── the report ──────────────────────────────────────────────────
    counts = {v: sum(1 for e in results.values() if e["verdict"] == v)
              for v in ("KILLED", "VACUOUS", "BASELINE-RED", "LIVE")}
    lines = [f"THE BOT FLEET MUTATION AUDIT — "
             f"{time.strftime('%Y-%m-%d %H:%M')}",
             "=" * 70]
    for v in ("LIVE", "VACUOUS", "BASELINE-RED"):
        rows = [k for k, e in results.items() if e["verdict"] == v]
        if rows:
            lines.append(f"\n{v} ({len(rows)}):")
            lines += [f"  {r}  [{results[r]['detail'][:90]}]" for r in rows]
    killed = [k for k, e in results.items() if e["verdict"] == "KILLED"]
    lines.append(f"\nKILLED ({len(killed)}):")
    for r in killed:
        lines.append(f"  {r}  [{results[r]['detail'][:110]}]")
    lines.append("\nper-drive module matrix (mutation sites -> red?):")
    for d, e in results.items():
        if "mutation" not in e:
            continue
        cells = [f"{m.split('.')[0]}:{v.get('mutations', '?')}"
                 f"{'->RED' if v['red'] else ''}"
                 for m, v in e["mutation"].items()]
        lines.append(f"  {d}: " + "  ".join(cells))
    lines.append("\n" + "=" * 70)
    lines.append(f"total {len(results)}: killed {counts['KILLED']}, "
                 f"VACUOUS {counts['VACUOUS']}, baseline-red "
                 f"{counts['BASELINE-RED']}, live {counts['LIVE']}")
    report = "\n".join(lines) + "\n"
    (SCOUT / "scratch" / "bot_mutation_report.txt").write_text(
        report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
