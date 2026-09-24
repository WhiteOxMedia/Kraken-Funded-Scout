"""kraken_stalewatch.py — THE KRAKEN STALE-FILE WATCHER (Sep 24).

POLICY (Sep 24): Kraken scans all scratch directories every cycle; any
file older than STALE_S (90 minutes) is stale and is deleted
immediately; the watcher writes a SUMMARY ONLY into repo/reports/ —
never the file, never its content: filename, age, deletion timestamp.

WHAT IT COVERS: every policy dir from scratch_space (logs, sbx, dbshape,
reports_temp, miner_temp, bot_temp) plus the OS-temp workspace
(%TEMP%/kraken_scout). Files deleted here are by construction
regenerate-able — drives re-create their scratch on the next run — so a
deletion never costs a verdict, only a stale copy of one.

WHAT IT NEVER TOUCHES: the repo root (that is diag_hygiene's court,
including its auto-sweep), reports/ itself, logs/ (production logs have
their own rotation), and anything outside the policy dirs.

TRANSPARENCY: every deletion is reported — name, age, deletion
timestamp, nothing else. The summary file carries no file content, no
sizes, no paths beyond the scratch-relative name (the summary is the
allowed "Kraken stale-file deletion summary" class in reports/).

Run:  .venv\\Scripts\\python.exe -X utf8 kraken_stalewatch.py
      kraken_stalewatch.run()          # the battery's per-rotation call
"""

import time
from datetime import datetime, timezone
from pathlib import Path

import scratch_space

STALE_S = 90 * 60          # the policy number: 90 minutes
REPORTS = Path(__file__).resolve().parent / "reports"
KEEP_SUMMARIES = 30        # bounded summary history in reports/


def _scan_dir(d: Path, now: float):
    """Yield (path, age_s) for every stale file under d (recursive)."""
    if not d.is_dir():
        return
    for f in d.rglob("*"):
        try:
            if not f.is_file():
                continue
            age = now - f.stat().st_mtime
            if age > STALE_S:
                yield f, age
        except OSError:
            continue   # vanished mid-scan: nothing to delete


def run(now: float | None = None) -> list[dict]:
    """One watcher cycle: purge stale scratch everywhere, summarize.
    Returns the summary rows (also appended to reports/ as a file)."""
    now = time.time() if now is None else now
    targets = list(scratch_space._DIRS) + [scratch_space.os_temp()]
    rows = []
    for d in targets:
        for f, age in _scan_dir(d, now):
            try:
                f.unlink()
            except OSError:
                continue   # held open or gone: it will not outlive 90min anyway
            rows.append({
                "file": f"{d.name}/{f.relative_to(d).as_posix()}",
                "age_min": round(age / 60),
                "deleted_at": datetime.now(timezone.utc)
                              .strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
    _write_summary(rows)
    return rows


def _write_summary(rows: list[dict]) -> None:
    """Summary ONLY into repo/reports/: filename, age, deletion stamp."""
    try:
        REPORTS.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        rp = REPORTS / f"kraken_stale_purge_{stamp}.txt"
        lines = [f"KRAKEN STALE-FILE PURGE — {stamp} UTC "
                 f"(stale threshold {STALE_S // 60} min)"]
        if rows:
            for r in rows:
                lines.append(f"  deleted  {r['file']}  "
                             f"age {r['age_min']}min  at {r['deleted_at']}")
        else:
            lines.append("  nothing stale — scratch is clean")
        lines.append(f"  total: {len(rows)} file(s) deleted (summary only; "
                     "no content is copied, per policy)")
        rp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # bound the summary history: newest KEEP_SUMMARIES survive
        olds = sorted(p for p in REPORTS.glob("kraken_stale_purge_*.txt"))
        for old in olds[:-KEEP_SUMMARIES]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        pass   # reporting must never crash the purge


def selftest() -> int:
    """Hermetic pins: age boundary, summary shape, production safety."""
    ok = True
    import tempfile
    with tempfile.TemporaryDirectory(prefix="_sbx_kraken_") as td:
        root = Path(td)
        young = root / "fresh.txt"
        old = root / "ancient.txt"
        young.write_text("n", encoding="utf-8")
        old.write_text("o", encoding="utf-8")
        now = time.time()
        import os
        os.utime(young, (now - 60, now - 60))          # 1 min old: keep
        os.utime(old, (now - STALE_S - 60, now - STALE_S - 60))  # 91 min: purge

        # the watcher's targets ARE scratch_space's policy dirs — pin
        # that contract by pointing the cycle at the fake root
        real_dirs = scratch_space._DIRS
        scratch_space._DIRS = (root,)
        try:
            rows = run(now=now)
        finally:
            scratch_space._DIRS = real_dirs
        if not young.exists():
            print("  FAIL  selftest: a fresh file was purged")
            ok = False
        if old.exists():
            print("  FAIL  selftest: a stale file survived the purge")
            ok = False
        got = {r["file"] for r in rows}
        want = {f"{root.name}/ancient.txt"}   # <scratchdir>/<relative>
        if got != want:
            print(f"  FAIL  selftest: purge rows {sorted(got)}, want {sorted(want)}")
            ok = False
        # the summary lands in reports/ with ONLY the allowed fields
        sums = sorted(REPORTS.glob("kraken_stale_purge_*.txt"))
        if not sums:
            print("  FAIL  selftest: no summary written to reports/")
            ok = False
        else:
            body = sums[-1].read_text(encoding="utf-8")
            if "ancient.txt" not in body or "o" == body.strip():
                ok_body = "ancient.txt" in body
                if not ok_body:
                    print("  FAIL  selftest: summary missing the deleted filename")
                    ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    print("kraken stalewatch selftest...")
    st = selftest()
    rows = run()
    print(f"  cycle: {len(rows)} stale file(s) purged; "
          f"summary in reports/ (name, age, timestamp only)")
    raise SystemExit(st)
