"""THE APPTEST PHASE RUNNER (Sep 30) — the one window-hostile drive.

test_apptest's honest full evidence takes ~722s (77s baseline + 9 module
reruns) — no 600s tool window fits it. The sweep has no per-module CLI,
but it is importable: this phase runs the drive ONCE for the baseline,
then iterates apptest's mapped modules ONE PER INVOCATION (the same
primitives as the sweep: mutation_ops all-sites-at-once mutate_source,
run_drive's output-aware judge, HEAVY timeout), merging each module's
result into the journal. Invoked repeatedly (once per phase) from
foreground tool calls; --finish assembles the final entry (KILLED when
any module's mutant went red — the sweep's own verdict rule) and writes
the report when the fleet is complete.

Phase 0 = baseline (+9 green modules). Phases 1..9 = one module each.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.getcwd())
import diag_mutation_sweep as dms   # noqa: E402
import mutation_ops                  # noqa: E402

DRIVE = "test_apptest.py"
JOURNAL = "artifacts/mutation_sweep/journal.json"
REPORT = "artifacts/mutation_sweep/mutation_sweep_report.txt"
OVERLAY = ("hooks/pre-commit", "test_commitgate.py",
           "diag_mutation_sweep.py",
           "artifacts/mutation_sweep/vacuous_triage.md")
HEAVY = 360
SBX = dms.SBX_ROOT / "repo"


def journal() -> dict:
    return json.load(open(JOURNAL, encoding="utf-8")) \
        if os.path.exists(JOURNAL) else {"results": {}}


def save(doc: dict) -> None:
    dms.journal_save(doc)


def main() -> int:
    # the standing preconditions, every phase: no stray sweep processes,
    # a healed sandbox, HEAD as the judged tree
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "(Get-CimInstance Win32_Process | Where-Object "
                        "{ $_.Name -match 'python' -and $_.CommandLine "
                        "-match 'diag_mutation_sweep' } | "
                        "Measure-Object).Count"],
                       capture_output=True, text=True)
    if r.stdout.strip().splitlines()[-1:] != ["0"]:
        print("REFUSED: sweep process(es) alive")
        return 2
    subprocess.run([sys.executable, "-X", "utf8",
                    "scratch/sbx_integrity.py", "--fix"])
    if not SBX.exists():
        dms.make_sandbox()
    for f in OVERLAY:
        blob = subprocess.run(["git", "show", "HEAD:" + f],
                              capture_output=True).stdout
        p = SBX / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
    if os.path.exists(REPORT):
        os.remove(REPORT)

    doc = journal()
    e = doc["results"].get(DRIVE)
    imap = dms.build_map()
    # build_map maps by BASENAME, so path-variants (ops/app.py,
    # tests/app.py) appear whenever the root module exists — the sweep's
    # own mutation loop skips nonexistent paths silently; same here.
    mods = sorted(m for m, ds in imap.items()
                  if DRIVE in ds and (SBX / m).exists())

    # phase 0: baseline
    if e is None:
        b = dms.run_drive(SBX, DRIVE, HEAVY)
        if b["red"]:
            doc["results"][DRIVE] = {
                "baseline": {k: b[k] for k in ("rc", "red", "secs",
                                               "exit_blind")},
                "verdict": "BASELINE-RED",
                "detail": f"baseline rc={b['rc']}: {b['err'][-160:]}"}
            save(doc)
            print("BASELINE-RED — aborting phases:", doc["results"][DRIVE])
            return 1
        e = {"baseline": {k: b[k] for k in ("rc", "red", "secs",
                                            "exit_blind")},
             "mutation": {}}
        doc["results"][DRIVE] = e
        save(doc)
        print(f"baseline green in {b['secs']}s — phases 1..{len(mods)} next")
    mods_pending = [m for m in mods if m not in e.get("mutation", {})]
    if not mods_pending:
        reds = [m for m, v in e["mutation"].items() if v["red"]]
        e["verdict"] = "KILLED" if reds else "VACUOUS"
        e["detail"] = ("; ".join(f"{m}: rc={v['rc']} "
                                 f"({v.get('mutations', '?')} mutations)"
                                 for m, v in e["mutation"].items()
                                 if v["red"])
                       or "stayed green under every mutation")
        save(doc)
        n = len(doc["results"])
        print(f"apptest verdict: {e['verdict']} ({n}/162 entries)")
        if n >= 162:
            subprocess.run(
                [os.path.abspath(os.path.join(".venv", "Scripts",
                                              "python.exe")),
                 "-X", "utf8", "diag_mutation_sweep.py",
                 "--all", "--resume", "--keep-sbx"],
                env={**os.environ, "SWEEP_REUSE_SBX": "1"},
                capture_output=True, text=True, encoding="utf-8")
            print("FLEET COMPLETE — report written")
        return 0

    mod = mods_pending[0]
    sp = SBX / mod
    orig = sp.read_text(encoding="utf-8")
    mut_src, count = mutation_ops.mutate_source(orig)
    if not count:
        e["mutation"][mod] = {"rc": "NOMUT", "red": False, "secs": 0.0}
        save(doc)
        print(f"{mod}: no mutation sites — phase done")
        return 0
    sp.write_text(mut_src, encoding="utf-8")
    try:
        m = dms.run_drive(SBX, DRIVE, HEAVY)
    finally:
        sp.write_text(orig, encoding="utf-8")
    e["mutation"][mod] = {"rc": m["rc"], "red": m["red"],
                          "secs": m["secs"], "mutations": count}
    save(doc)
    print(f"{mod}: rc={m['rc']} red={m['red']} ({count} sites, "
          f"{m['secs']}s) — {len(mods_pending) - 1} phases left")
    return 0


if __name__ == "__main__":
    sys.exit(main())
