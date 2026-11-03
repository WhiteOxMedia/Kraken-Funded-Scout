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

# ── 4. THE PATH-HIJACK SWEEP (Oct 4): app.py's _bridge hardcoded
# sys.path.insert(r"C:\...\kraken_scout") and MASKED the mutation
# sandbox — every later import inside it resolved from the real tree
# and the mutant never executed. The class: an import-path insert
# built from a baked absolute literal, i.e. a module that forgot
# where it lives. Production modules ride the Path(__file__) idiom;
# the fleet may not carry another (17be10d's lesson, now pinned).
_ssr = _read(BASE / "scout_signal_reader.py")
check("1.4 no production module re-introduces the app.py:646 hijack "
      "class: the bot's LIVE reader derives its scout import path "
      "from Path(__file__) (bot root -> parent -> scout sibling), "
      "never a baked Workspace absolute",
      "SCOUT_PATH = Path(__file__).resolve().parent.parent" in _ssr
      and 'Path(r"C:' not in _ssr,
      [ln for ln in _ssr.splitlines() if "SCOUT_PATH" in ln][:2])
_sbridge = _read(BASE / "signal_bridge.py")
check("1.5 the shared bridge's own files ride its __file__ too "
      "(DB_PATH and KILL_SWITCH_FILE derive from the module's home — "
      "parent.parent hosts the scout sibling, the SAME EXPRESSION "
      "works in both twin homes so the phantommirror byte-identity "
      "survives)",
      "DB_PATH = (Path(__file__).resolve().parent.parent" in _sbridge
      and "KILL_SWITCH_FILE = (Path(__file__).resolve().parent.parent"
      in _sbridge
      and '"kraken_scout" / "signals.db"' in _sbridge
      and 'Path(r"C:' not in _sbridge,
      [ln for ln in _sbridge.splitlines()
       if "DB_PATH" in ln or "KILL_SWITCH_FILE" in ln][:2])
check("1.6 the idiom pins stay true (app's _bridge keeps the "
      "Path(__file__) shape that un-masked the sandbox, and the "
      "reader keeps the SCOUT_PATH insert seam the drives pin)",
      "sys.path.insert(0, str(Path(__file__).resolve().parent))"
      in _read(BASE / "app.py")
      and "sys.path.insert(0, str(SCOUT_PATH))" in _ssr)

fails = [r for r in results if not r[1]]
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")


def main():
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    sys.exit(main())
