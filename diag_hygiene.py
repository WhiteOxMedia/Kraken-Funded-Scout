"""diag_hygiene.py - THE HYGIENE GATE (rebuilt Sep 15 after the deletion
event took the original with it).

WHY THIS EXISTS: the Sep 14 sweep deleted ~200 stray artifacts across
both repos - one-off diag probes, per-suite _bat_test_*.log captures,
scratch DBs the test suites re-created on every run, the bot's literal
`$null` file. Litter re-accumulates silently unless something FAILS on
it. This gate is that something: run it anytime, it is strictly
read-only, stdlib only, and exit-coded (0 clean / 1 findings) so a .bat
or scheduler can gate on it.

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

import re
import sys
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
KNOWN_DEAD_ARTIFACTS = {"trading_bot.db"}

RULE_A = re.compile(r"^(_.*\.(?:db|log)|_sbx_.*|_bat_.*|\$.*)$")
REF_EXTS = ("*.py", "*.bat", "*.ps1", "*.md")


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
            if f.name in FACTORY_SCRATCH or rule_a_hit(f.name):
                strays.append((f, "RULE A: root scratch"))
                continue
            if f.name.startswith("diag_"):
                n_b += 1
                stem = diag_stem(f)
                if stem not in KEEP_WORLDS and stem not in corpus:
                    strays.append((f, "RULE B: unreferenced one-off probe"))
        counts[str(root)] = (n_a, n_b)
    return strays, counts


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
    if ok:
        print("  selftest: rule pins green (13 cases)")
    return 0 if ok else 1


def main() -> int:
    st = selftest()
    strays, counts = audit()
    print("=" * 60)
    print("  HYGIENE AUDIT - root scratch + one-off probes, both repos")
    print("=" * 60)
    for root, (n_a, n_b) in counts.items():
        print(f"  {root}: {n_a} root files scanned, {n_b} diag files")
    if strays:
        for f, why in strays:
            print(f"  STRAY  {f}  [{why}]")
        print(f"\nHYGIENE: {len(strays)} STRAY FILE(S) - clean before shipping")
        return 1
    print("\nHYGIENE: CLEAN")
    return 0 if st == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
