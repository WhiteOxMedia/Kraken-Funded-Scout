"""test_sibling_wiring.py - THE SIBLING-REPO WIRING DRIVE (Oct 30).

Two consumers pin location-sensitive wiring to paths OUTSIDE this
repo - the same class test_sweep_wiring pins inside it. The sibling
checkout is NOT this repo, so a break shows up as silent degradation,
not a red file:

  1. tools/rebuild_bundle_watch.py - THE BUNDLE REBUILD WATCHER.
     The served zip is built from `git archive HEAD` on BOTH repos, so
     the watcher resolves the sibling Scout repo as
     REPO.parent / "kraken_scout" (a DIFFERENT name than the shadow
     gate's sibling - that is by design, and both pin is what keeps
     each consumer honest about its own path). If the sibling moves
     or renames, the watcher's `_head(SCOUT)` reads None and the
     watcher SKIPs forever - a silent no-op masking bundle staleness.

  2. githooks/check_sibling_shadow_gate.py - THE SIBLING SHADOW GATE.
     Refuses to commit here while the sibling repo
     (Kraken-Challenge-Scout-Bot-Referral-Jackpot, at ../ relative to
     monero_project) still has a ntfytopic carrier STAGED in its
     index. `sibling_repo()` resolving to None is DESIGNED to mean
     "no sibling here, nothing to block" - so a moved sibling turns
     the security gate into a silent no-op, the exact shape a pin
     must make loud. The gate itself is invoked by githooks/pre-commit
     at $ROOT/githooks/check_sibling_shadow_gate.py with
     --monero-root "$ROOT".

This drive pins the wiring the same way test_sweep_wiring pins the
sweep discipline, statically and hermetically:

  1. THE STATIC WIRING IS INTACT: each consumer still carries its
     load-bearing path expression (the watcher's self-location gate,
     `REPO = HERE.parent if HERE.name == "tools" else HERE`, the
     watcher's SCOUT resolution, the gate's SIBLING_REL, and
     pre-commit's invocation line).
  2. THE RESOLUTIONS ARE REAL WHERE THE ENVIRONMENT ALLOWS:
     hermetically resolved via git rev-parse --git-dir against the
     exact paths each consumer resolves. Dynamically: the shadow gate
     itself is INVOKED the way pre-commit invokes it (read-only - it
     only lists the sibling's staged paths, never any content); exit 0
     passes and the designed BLOCKED banner (rc 1) is also a deliberate
     pass here - the wiring executed and returned one of its two
     designed outcomes. A missing sibling checkout (a standalone clone
     or CI lane) makes the resolution checks print SKIP honestly -
     the static half still pins the wiring unconditionally.

Prints "RESULT: N passed, M failed".
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WATCHER = HERE / "tools" / "rebuild_bundle_watch.py"
GAME = HERE / "githooks" / "check_sibling_shadow_gate.py"
PRE_COMMIT = HERE / "githooks" / "pre-commit"
result_rows = []


def check(n, cond, why=""):
    result_rows.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


def effect_git_repo(path):
    """True when `path` resolves to a working git repo (rc 0 on rev-parse)."""
    if not Path(path).is_dir():
        return False
    r = subprocess.run(["git", "-C", str(path), "rev-parse", "--git-dir"],
                       capture_output=True, text=True, timeout=30)
    return r.returncode == 0


# ── 1. THE STATIC WIRING IS INTACT ─────────────────────────────────
if not WATCHER.is_file() or not GAME.is_file():
    print("SKIP: tools/rebuild_bundle_watch.py or githooks/ not present - "
          "the sibling wiring drive pins both consumers only")
    print("RESULT: 0 passed, 0 failed")
    sys.exit(0)

w_src = WATCHER.read_text(encoding="utf-8", errors="replace")
check("1a the watcher self-locates and only re-roots when housed in tools/",
      'REPO = HERE.parent if HERE.name == "tools" else HERE' in w_src,
      "the watcher only derives the repo root when it is exactly one "
      "level down (tools/) - move it back to the repo root or deeper "
      "and REPO drifts")
check("1b the watcher's sibling Scout repo resolves to "
      "REPO.parent / 'kraken_scout'",
      'SCOUT = REPO.parent / "kraken_scout"' in w_src,
      "the watcher's staleness pair reads BOTH heads - a renamed "
      "sibling makes _head(SCOUT) None and the watcher SKIPs forever")

g_src = GAME.read_text(encoding="utf-8", errors="replace")
check("1c the shadow gate names the sibling exactly "
      "Path('../Kraken-Challenge-Scout-Bot-Referral-Jackpot') relative "
      "to the monero root",
      'SIBLING_REL = Path("..") / "Kraken-Challenge-Scout-Bot-Referral-Jackpot"'
      in g_src,
      "sibling_repo() resolving None is DESIGNED to mean nothing to "
      "block - a moved or renamed sibling silently no-ops the gate")

pc_src = PRE_COMMIT.read_text(encoding="utf-8", errors="replace")
check("1d pre-commit still invokes the gate at "
      "$ROOT/githooks/check_sibling_shadow_gate.py with --monero-root",
      'GAME="$ROOT/githooks/check_sibling_shadow_gate.py"' in pc_src
      and '--monero-root "$ROOT"' in pc_src,
      "pre-commit must invoke the gate with the monero root so "
      "SIBLING_REL resolves - a renamed hook path or flag silently "
      "drops the gate from the gate flow")

# ── 2. THE RESOLUTIONS ARE REAL (where the environment allows) ──────
scout = HERE.parent / "kraken_scout"
if effect_git_repo(scout):
    check("2a the sibling Scout checkout is a real git repo at the "
          "path the watcher resolves",
          True, "the watcher's resolution is hermetically re-run here")
else:
    print(f"SKIP: {scout} - no sibling Scout checkout here (standalone "
          "clone or CI lane); 1b still pins the wiring statically")

# load the gate's SIBLING_REL straight from the gate file (single source)
spec = importlib.util.spec_from_file_location("_shadow_gate_mod", GAME)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
gate_sib = (HERE / mod.SIBLING_REL).resolve()
if effect_git_repo(gate_sib):
    check("2b the shadow gate's sibling repo is a real git repo at the "
          "path SIBLING_REL resolves",
          True, "the gate's own resolution, re-run hermetically - a "
          "None resolve here would silently no-op the security gate")
else:
    print(f"SKIP: {gate_sib} - no shadow-gate sibling checkout here; "
          "1c still pins the wiring statically")

r = subprocess.run([sys.executable, "-X", "utf8", str(GAME),
                    "--monero-root", str(HERE)],
                   capture_output=True, text=True, encoding="utf-8",
                   timeout=60, cwd=str(HERE))
designed = r.returncode == 0 or (
    r.returncode == 1
    and "BLOCKED (sibling shadow gate):" in (r.stderr or ""))
check("2c the shadow gate runs exactly the way pre-commit invokes it "
      "and answers one of its two designed outcomes",
      designed,
      f"rc={r.returncode}; stderr={str(r.stderr or '').strip()[:200]}; "
      "any other outcome is a wiring break, not a designed alarm")

n_pass = result_rows.count(True)
n_fail = result_rows.count(False)
print(f"RESULT: {n_pass} passed, {n_fail} failed")
sys.exit(1 if n_fail else 0)
