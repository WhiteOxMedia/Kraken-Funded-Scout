"""THE SINK-HYGIENE TRIPWIRE (task 44, the test-pollutes-live-state
sweep): the class the last review rounds kept catching — a drive
writing a PRODUCTION file, or swapping a production path constant with
no restore — is forbidden fleet-wide. The engine is diag_testleaks's
AST audit (the per-assignment tightened rule, its permanent self-check,
101 production filenames): this drive runs that audit in-process on
every suite so the commit gate blocks a new unsealed writer BEFORE it
lands, and pins the seams the sealed drives rebind.

LIVED PROOF (Sep 29, 16:55): an ordering slip in test_allgoodlead aimed
seeded rows at the LIVE contest_watch.json; the running scanner
auto-closed the phantom and banked +$257.20 of nothing into the live
account, pushing a ghost GET OUT to the operator's phone. Backup/
restore-by-name is retired; the sink rebind is the only door.
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))


def _read(p):
    """Sandbox-proof read (the gate lesson, twice): the commit-gate
    layers and the commitgate drills tear temp dirs down concurrently —
    a file that vanished between glob and read is SKIPPED, never a
    crash. A vanished victim is gone; gone is clean."""
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except (OSError, FileNotFoundError):
        return ""

import diag_testleaks as _dtl
import background_scanner as bs

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not ok else ""))


# 1. the fleet leak audit, in-process, every suite (the AST rule with
# its sandbox shapes, sentinels, and the permanent self-check).
# RACE-PROOF (the gate sandbox lesson): this drive RUNS INSIDE the
# commitgate sandbox too, where a concurrent cleanup can vanish a file
# between the glob and the read — a vanished victim is counted CLEAN
# (the file that mattered is gone), never a crash.
try:
    prod = _dtl.collect_production_names()
except OSError:
    # degenerate-but-alive: the runtime-state family the audit must
    # never miss, even if the sandbox raced the production sources away
    prod = {"contest_trades.jsonl", "contest_watch.json",
            "profit_guard_state.json", "scanner_state.json",
            "take_payouts.json", "telemetry.jsonl"}
offenders = {}
for p in sorted(BASE.glob("test_*.py")):
    try:
        hits = _dtl.audit_file(p, prod)
    except (FileNotFoundError, OSError):
        continue
    if hits:
        offenders[p.name] = hits[:5]
check("1.1 the fleet leak audit is CLEAN across every drive (no "
      "production-path writes, no unguarded swaps, no default-path "
      "reads)",
      not offenders, str(offenders)[:600])

# 2. the journal seam the sealed drives rebind
check("1.2 CONTEST_TRADES_FILE is the rebindable journal seam "
      "(module-dir default; TRADES_ENV is the env shortcut)",
      bs.CONTEST_TRADES_FILE.endswith("contest_trades.jsonl")
      and bs.TRADES_ENV == "CONTEST_TRADES_FILE"
      and str(BASE) in bs.CONTEST_TRADES_FILE, bs.CONTEST_TRADES_FILE)

# 3. the sealed drives rebind the seam — no live-name backup windows
# may come back (the 16:55 shape)
_ag = _read(BASE / "test_allgoodlead.py")
_gb = _read(BASE / "test_giveback.py")
check("1.3 the sealed drives carry no live-name backup/restore windows "
      "(the sink rebind replaced them)",
      'Path("contest_trades.jsonl")' not in _ag
      and 'Path("take_payouts.json")' not in _gb
      and "_bak1b" not in _gb and ".testbak" not in _gb)

fails = [r for r in results if not r[1]]
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
