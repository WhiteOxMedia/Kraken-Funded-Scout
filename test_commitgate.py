"""THE COMMIT-GATE QUIET-MODE GUARD (Sep 23).

Pins hooks/pre-commit's GATE_QUIET flag to its contract:

  1. The flag defaults to 0 — fail-open to verbose. An unset or corrupt
     GATE_QUIET can never silence the gate.
  2. ONLY the per-drive PASS echo is guarded. A FAILED drive, a budget
     timeout, and the fail summary ALWAYS print — quiet must never hide
     a red.
  3. The green summary line counts the drives (post-dedup) under quiet,
     so even a fully-silent run proves the gate executed.

Checked two ways: a source census on the real hook, then a BEHAVIORAL
sandbox — a full repo copy in a scratch git repo, trigger files staged
with real diffs, and the hook's gate section run for real against the
repo's true trigger matrix and dedup (CODERABBIT_OFF=1 isolates the
sandbox from the review section; the gate is what's under test). The
mutation drill at the tail re-proves the census bites: the pass echo
wrapped in a bare quiet guard must FAIL the drive — run under
GUARD_RERUN=1 so the rerun grades only the census, fast.

Exit 0 only when every check passed. Prints "RESULT: N passed, M failed".
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE / "hooks" / "pre-commit"
RERUN = os.environ.get("GUARD_RERUN") == "1"
sys.path.insert(0, str(HERE))

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    mark = "PASS" if cond else "FAIL"
    print(f"{mark}  {n}" + (f"  ({why})" if why and not cond else ""))


# ════════ 1. source census: the real hook's flag wiring ════════
print("== 1. census: the quiet flag is wired exactly as contracted ==")
src = HOOK.read_text(encoding="utf-8")

# 1.1 the flag defaults to 0 — fail-open to verbose
check(1.1, 'GATE_QUIET="${GATE_QUIET:-0}"' in src,
      "the :-0 default (fail-open) is missing")
# 1.2 the pass echo is guarded by the flag — the ONLY guarded echo
pass_guard = ('[ "$GATE_QUIET" = "1" ] || '
              'echo "  🧠 gate: $t — $res (${dt}ms, budget ${budget}s)"')
check(1.2, pass_guard in src, "the pass echo is not the guarded one")
# 1.3 every failure-family echo stays UNGUARDED (4 sites: no-python,
#     stage failure, budget timeout, drive FAILED)
n_fail_echoes = src.count('echo "  ⛔ GATE:')
check(1.3, n_fail_echoes == 4, f"expected 4 failure echoes, got {n_fail_echoes}")
for line in src.splitlines():
    if 'echo "  ⛔ GATE:' in line:
        check(1.31, 'GATE_QUIET' not in line,
              "a failure echo is quiet-guarded: " + line.strip()[:70])
# 1.4 the green summary counts drives under quiet
check(1.4, "commit gate (quiet): ${_DRIVES_RUN} drive(s) green" in src,
      "the quiet summary lost its drive count")
check(1.41, "_DRIVES_RUN=$((_DRIVES_RUN + 1))" in src,
      "the post-dedup drive counter is gone")
# 1.5 the doc block states the contract
check(1.5, "QUIET MODE (Sep 23): GATE_QUIET=1 (env, default 0)" in src,
      "the header lost the quiet-mode contract")
# 1.6 the gate guards ITSELF: the trigger matrix runs this drive when the
# hook is staged — a future edit cannot silently drop the gate's own guard
check(1.6, 'maybe_run "$_HK"  test_commitgate.py result  180' in src,
      "the self-arming trigger line is missing from the matrix")

if not RERUN:
    # ════════ 2. behavioral sandbox: the gate body, run for real ════════
    print("== 2. sandbox: the gate runs live in a scratch repo ==")
    _sbx = tempfile.TemporaryDirectory(prefix="commitgate_quiet_")
    sandbox = Path(_sbx.name)

    # FULL repo copy (the gate's own model: drives import real modules via
    # the PYTHONPATH fallback; a stub world would fail them dishonestly).
    # THE GATE-SHAPED SOURCE: under the gate, HERE is the gate's scratch
    # (staged files only — no .venv, no sibling drives, no support
    # modules), so the copy source is the REAL repo from PYTHONPATH's
    # fallback entry; bare HERE everywhere else.
    _pp = os.environ.get("PYTHONPATH", "")
    _repo = Path(_pp.split(";")[-1]) if _pp else HERE
    if not (_repo / "app.py").exists():   # a real repo, not a bare fallback
        _repo = HERE
    shutil.copytree(
        _repo, sandbox, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "logs", "battery_history", "__pycache__",
            "speech_queue", "*.wav"))

    def _git(*args):
        return subprocess.run(["git", *args], cwd=str(sandbox),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")

    _git("init", "-q")
    _git("config", "user.email", "guard@local")
    _git("config", "user.name", "quiet-guard")
    _git("config", "core.hooksPath", "hooks")   # the repo's own arming line
    _git("add", "-A")
    _git("commit", "-q", "-m", "scratch base", "--no-verify")

    # The hook resolves PY from $PWD/.venv — junction the REAL venv in
    # (instant, no copy) from the same repo the copy came from. Created
    # after the commit so git never traverses it; TemporaryDirectory's
    # rmtree removes a junction without descending (CPython >=3.8
    # reparse-tag handling), so the real venv is safe.
    _jsrc = _repo if (_repo / ".venv").exists() else HERE
    _vj = subprocess.run(
        ["cmd", "/c", "mklink", "/J",
         str(sandbox / ".venv"), str(_jsrc / ".venv")],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if _vj.returncode != 0:
        print(f"FAIL  2.00  (venv junction failed: {_vj.stderr.strip()[:120]})")
        results.append(False)

    hook_exe = sandbox / "hooks" / "pre-commit"
    hook_mode = hook_exe.stat().st_mode
    hook_exe.chmod(hook_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def run_gate(extra_env=None, stage=("app.py", "background_scanner.py")):
        """Touch the triggers, stage REAL diffs, run the REAL hook."""
        for f in stage:
            p = sandbox / f
            p.write_text(p.read_text(encoding="utf-8", errors="replace")
                         + "\n# quiet-guard touch\n", encoding="utf-8")
            _git("add", "-f", f)
        env = dict(os.environ)
        env["CODERABBIT_OFF"] = "1"   # sandbox isolation: gate only
        env.update(extra_env or {})
        p = subprocess.run(["bash", str(hook_exe)], cwd=str(sandbox),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=240)
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    # THE EXPECTED COUNT derives from the hook's own matrix — the distinct
    # drive names across the $_APP/$_BS trigger lines (deduped the way
    # maybe_run dedupes). A future registration then tracks automatically
    # and a count/chatter disagreement still fails; the $_HK self-arming
    # lines are excluded (hooks/pre-commit is not staged by run_gate).
    import re as _re
    _hook_txt = (sandbox / "hooks" / "pre-commit").read_text(
        encoding="utf-8", errors="replace")
    _drives = set(_re.findall(
        r'maybe_run "\$_(?:APP|BS)"\s+(\S+\.py)', _hook_txt))
    _n_expected = len(_drives)
    check(2.01, _n_expected >= 7,
          f"the matrix census reads {_n_expected} app/scanner drives "
          f"({sorted(_drives)[:3]}...) — a parse rot shows as a tiny count")

    # 2.1 GATE_QUIET=1: passes stay silent, the counted summary speaks
    print("== 2.1 quiet: GATE_QUIET=1, all drives pass ==")
    rc, out = run_gate({"GATE_QUIET": "1"})
    check(2.1, rc == 0, f"gate rc={rc}")
    check(2.11, f"commit gate (quiet): {_n_expected} drive(s) green" in out,
          f"quiet summary missing or count wrong (expected {_n_expected}: "
          f"{sorted(_drives)})")
    check(2.12, '🧠 gate: ' not in out, "pass chatter leaked through quiet")
    check(2.13, "⛔ GATE:" not in out, "a failure family fired on a green run")

    # 2.2 default (unset): fully verbose — the historical behavior
    print("== 2.2 default: GATE_QUIET unset, all drives pass ==")
    rc, out = run_gate({})
    check(2.2, rc == 0, f"gate rc={rc}")
    n_pass = out.count('🧠 gate: ')
    check(2.21, n_pass == _n_expected,
          f"expected {_n_expected} pass lines, got {n_pass}")
    check(2.22, "commit gate: every triggered drive green" in out,
          "the verbose green summary is gone")

    # 2.3 quiet + a BROKEN drive: the red MUST print even under quiet
    print("== 2.3 failure under quiet: GATE_QUIET=1, autotrack broken ==")
    (sandbox / "test_autotrack.py").write_text(
        'raise SystemExit("sandbox: drive deliberately broken")\n')
    # stage the broken drive TOO: run_drive exports the STAGED copy, so a
    # drive broken in the commit itself must be the one that runs (the
    # real-world shape: the broken test rides the commit it guards)
    rc, out = run_gate({"GATE_QUIET": "1"},
                       stage=("app.py", "test_autotrack.py"))
    check(2.3, rc == 1, f"a broken drive must block the commit (rc={rc})")
    check(2.31, "⛔ GATE: test_autotrack.py FAILED" in out,
          "the drive FAILED line is missing under quiet — quiet hid a red")
    check(2.32, "COMMIT GATE: failed drives:" in out,
          "the fail summary is missing under quiet")
    check(2.33, '🧠 gate: ' not in out,
          "pass chatter leaked through quiet on a red run")

# ════════ 3. the census bites: mutation drill on a scratch copy ════════
if not RERUN:
    print("== 3. mutation drill: a quiet-guarded failure echo fails 1.31 ==")
    mutated = src.replace(
        'echo "  ⛔ GATE: $t FAILED',
        '[ "$GATE_QUIET" = "1" ] || echo "  ⛔ GATE: $t FAILED')
    check(3.0, mutated != src, "mutation did not apply — needle drifted")
    try:
        HOOK.write_text(mutated, encoding="utf-8")
        r = subprocess.run(
            [sys.executable, "-X", "utf8", str(Path(__file__))],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, cwd=str(HERE),
            env={**os.environ, "GUARD_RERUN": "1"})
        check(3.1, r.returncode == 1,
              "the mutated drive must fail (a hidden red must be unthinkable)")
        check(3.2, "1.31" in (r.stdout or ""),
              "the failure must be the census check, not a crash")
    finally:
        # byte-exact restore, always — write_text would flip LF to CRLF
        # on Windows and break the machine-local mirror's byte-identity
        HOOK.write_bytes(src.encode("utf-8"))

# ════════ summary ════════
passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
