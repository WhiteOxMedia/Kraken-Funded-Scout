"""test_trimlifecycle.py — THE TRIM'S LIFECYCLE, BEHAVIORALLY (Oct 4).

The archive-strip census pin in test_apptest.py reads TEXT — a mutant
that keeps the pinned strings but kills the write (a dead `if _dropped`,
a wrong path) stays green forever (proved: the M1-c surgical revert
passed 335/0 inside the mutation sweep's own sandbox, and the masking
root cause — app.py's hardcoded sys.path insert — made even a
behavioral pin in test_apptest judge the REAL module, not the sandbox
copy). This drive imports ONLY background_scanner (no app, no
sibling-tree reach) and runs the REAL _log_suppression against
sandboxed files: the cap holds the LAST rows, every eviction lands in
the archive in eviction order, pre-existing archive content survives
the append, and no per-writer tmp is left behind. Sandbox-capped:
MAX lowered to 5 so the lifecycle costs 8 calls, not 1005 — the Oct 2
burial-burst incident must never re-archive silently, and now a
runtime mutant cannot make it.
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import background_scanner as bs

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not cond else ""))


sbx = BASE / "scratch" / "sbx"
sbx.mkdir(parents=True, exist_ok=True)
_ledger = sbx / f"_trimlife_ledger.{__import__('os').getpid()}.jsonl"
_archive = sbx / f"_trimlife_archive.{__import__('os').getpid()}.jsonl"
_REAL_MAX = bs.SUPPRESSION_LOG_MAX
_REAL_LOG = bs.SUPPRESSION_LOG_FILE
_REAL_ARCH = bs.SUPPRESSION_ARCHIVE_FILE
# PID-reuse hardening (Copilot low, Oct 4): Windows can hand this PID
# to a later run — a stale ledger/archive from that run would poison
# T2's eviction-order assertion with rows this run never wrote. Own-PID
# residue is unlinked BEFORE the lifecycle starts (other PIDs' files
# are left alone: concurrent runs stay isolated).
for _stale in (list(sbx.glob(f"_trimlife_ledger.{__import__('os').getpid()}.*"))
               + list(sbx.glob(f"_trimlife_archive.{__import__('os').getpid()}.*"))):
    _stale.unlink(missing_ok=True)
try:
    bs.SUPPRESSION_LOG_FILE = str(_ledger)
    bs.SUPPRESSION_ARCHIVE_FILE = str(_archive)
    bs.SUPPRESSION_LOG_MAX = 5
    _archive.write_text(json.dumps({"seed": "pre-existing"}) + "\n",
                        encoding="utf-8")
    for _i in range(8):
        bs._log_suppression(f"BEH{_i}/USD", "behav-gate", f"call {_i}")

    _rows = [json.loads(l) for l in _ledger.read_text(
        encoding="utf-8").splitlines() if l.strip()]
    check("T1 the ledger caps at MAX holding the LAST rows in order "
          "(the real _log_suppression ran, not its text)",
          len(_rows) == 5
          and [r.get("pair") for r in _rows]
          == [f"BEH{i}/USD" for i in range(3, 8)],
          str([r.get("pair") for r in _rows]))

    _arows = [json.loads(l) for l in _archive.read_text(
        encoding="utf-8").splitlines() if l.strip()]
    check("T2 every eviction lands in the archive in eviction order and "
          "pre-existing archive content survives the append (a dead "
          "write, a wrong path, or a whole-file rewrite fails this)",
          len(_arows) == 4
          and _arows[0].get("seed") == "pre-existing"
          and [r.get("pair") for r in _arows[1:]]
          == [f"BEH{i}/USD" for i in range(3)],
          str([r.get("pair") if isinstance(r, dict) else "?"
               for r in _arows]))

    _tmps = [p.name for p in sbx.iterdir()
             if p.name.startswith("_trimlife") and ".tmp." in p.name]
    check("T3 no per-writer tmp residue (the append never builds an "
          "archive tmp)", _tmps == [], str(_tmps))
finally:
    bs.SUPPRESSION_LOG_FILE = _REAL_LOG
    bs.SUPPRESSION_ARCHIVE_FILE = _REAL_ARCH
    bs.SUPPRESSION_LOG_MAX = _REAL_MAX
    _ledger.unlink(missing_ok=True)
    _archive.unlink(missing_ok=True)

fails = [r for r in results if not r]
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")


def main():
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    sys.exit(main())
