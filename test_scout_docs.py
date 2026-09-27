"""Test THE SCOUT DOCS ROT GUARD (Sep 27): README.md claims are pinned so
the exit-ladder documentation cannot silently rot. Four contract layers:

  1. FILES — every backticked .py/.md/.bat name in the README exists in
     the checkout (the pool docs-rot drive's file-honesty rule, native).
  2. CONSTANTS WITH VALUES — the dials the README names must exist AND
     still carry the documented values (CONTEST_TRAIL_PCT 3.0,
     GOAL_TRAIL_ARM_FRAC 0.5, TAKE_IT_USD 20, SCALP_TARGET_PCT 0.4,
     TRAIL_SIM_ABLE (1.0,1.5,2.0,3.0)); a silent dial change breaks the
     README on purpose, forcing the doc to move WITH the code.
  3. THE LADDER CHAIN — the README's exit order (SL -> TP -> knife ->
     ATR trail -> strategy sell -> exhaustion -> momentum-rider ->
     THESIS DEAD) must match the exit-synthesis contract's own docstring,
     token for token in order; the doc and the contract rot together or
     not at all.
  4. THE EVIDENCE STANCE — the README names all five studies and the
     tripwire drive, carries the snapshots-frozen statement (study
     numbers are Sep 27 snapshots; the drives and studies are the live
     truth — pinning live ledger outputs would rot BY DESIGN), and the
     tripwire drive itself runs green from here.

Read-only beyond running the one subprocess drive."""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


HERE = Path(__file__).parent
readme = (HERE / "README.md").read_text(encoding="utf-8")

print("== 1. FILES: every backticked path exists ==")
names = sorted(set(re.findall(r"`([A-Za-z_0-9]+\.(?:py|md|bat))`", readme)))
check("1.1", len(names) >= 15,
      f"the README names {len(names)} files (a collapse means the doc shrank)")
_missing = [n for n in names if not (HERE / n).exists()]
check("1.2", not _missing, f"missing: {_missing or 'none'}")
for must in ("test_trailk_sweep.py", "test_trailstop.py",
             "test_exitsynthesis.py", "test_profitguard.py",
             "_trail_k_sweep.py", "_hybrid_trail_sweep.py",
             "_preset_backtest.py", "_preset_tierscaled.py",
             "_guardfree_trail.py", "test_pool_docsrot_live.py"):
    check(f"1.3.{must[:18]}", must in names,
          "a load-bearing artifact vanished from the doc")

print("== 2. CONSTANTS: named dials exist AND carry the documented values ==")
import discipline as disc              # noqa: E402
import background_scanner as bs        # noqa: E402
for const, val in (("CONTEST_TRAIL_PCT", 3.0), ("GOAL_TRAIL_ARM_FRAC", 0.5),
                   ("TAKE_IT_USD", 20), ("SCALP_TARGET_PCT", 0.4)):
    check(f"2.1.{const[:14]}", getattr(bs, const, None) == val,
          f"README documents {const}={val}; source says "
          f"{getattr(bs, const, None)!r}")
check("2.2", list(disc.TRAIL_SIM_ABLE) == [1.0, 1.5, 2.0, 3.0],
      "the sweep set the README documents matches discipline.TRAIL_SIM_ABLE")
check("2.3", bs._ratchet_trail_pct(100.0, 101.0, 0) == 2.0
      and bs._ratchet_trail_pct(100.0, 102.0, 0) == 1.5,
      "the documented ratchet steps (+1% -> 2.0, +2% -> 1.5) are the "
      "function's real boundaries")

print("== 3. THE LADDER CHAIN: doc and exit-synthesis contract agree ==")
_TOKENS = ["SL", "TP", "knife", "ATR trail", "strategy sell",
           "exhaustion", "momentum-rider", "THESIS DEAD"]


def _chain_in(text):
    pos = 0
    for tok in _TOKENS:
        idx = text.find(tok, pos)
        if idx < 0:
            return False
        pos = idx + len(tok)
    return True


check("3.1", _chain_in(readme), "the README carries the full ordered chain")
_exits_doc = (HERE / "test_exitsynthesis.py").read_text(encoding="utf-8")
check("3.2", _chain_in(_exits_doc),
      "the exit-synthesis contract's docstring carries the same chain")
check("3.3", _chain_in(readme) == _chain_in(_exits_doc),
      "doc and contract agree (if one moved without the other, this is red)")

print("== 4. THE EVIDENCE STANCE ==")
check("4.1", "snapshots frozen at Sep 27" in readme,
      "the README declares its study numbers as frozen snapshots - the "
      "live ledger grows, the drives and studies are the live truth")
check("4.2", "relative bar" in readme and "-95.96" in readme,
      "the corrected control accounting (relative bar, incumbent "
      "baseline) is documented, not the pre-correction story")
_r = subprocess.run([sys.executable, "test_trailk_sweep.py"],
                    cwd=str(HERE), capture_output=True, text=True,
                    timeout=300)
check("4.3", _r.returncode == 0 and "20 passed, 0 failed" in _r.stdout,
      f"the tripwire drive runs green from the docs drive "
      f"({_r.stdout.strip().splitlines()[-1] if _r.stdout else 'no output'})")

print("\n".join(lines))
print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
