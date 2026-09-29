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

# 1.7 the MIRROR CHECK (user, Sep 23: "verify the machine-local mirror
#     before running, so drift is caught at commit time"): the hook
#     verifies every tracked hook's fallback copy BEFORE the gate —
#     missing auto-arms, diverged blocks, worktree-reference semantics.
mstart = src.find("check_mirror() {")
check(1.7, mstart != -1, "the mirror-check function is missing from the hook")
if mstart != -1:
    mend = src.find("\n}", mstart)   # the function's closing brace
    body = src[mstart:mend if mend != -1 else len(src)]
    check(1.71, "MIRROR DRIFT" in body and "exit 1" in body,
          "a diverged mirror no longer blocks with the re-arm recipe")
    check(1.72, 'mkdir -p ".git/hooks"' in body and "was missing — armed from" in body,
          "a missing mirror is no longer auto-armed (fresh clones would stay fallback-less)")
    # 1.73 the reference is the WORKTREE hook — the file that actually
    #      RUNS under core.hooksPath — the same reference the battery's
    #      two-way census uses, so the two checks can never disagree
    check(1.73, "tr -d '\\r' < \"$tracked\"" in body,
          "the check lost its worktree-reference (run-content) comparison")
# 1.74 the sweep covers EVERY tracked hook name, not just pre-commit
check(1.74, "git ls-files 'hooks/*'" in src and "*.sample) continue" in src,
      "the mirror sweep no longer covers every tracked hook name")

# 1.8 THE THREE-LAYER RESOLUTION (finding 3, Sep 23): the drive sandbox's
#     PYTHONPATH layers staged -> HEAD -> worktree, so a non-staged import
#     resolves from COMMITTED content first and the dirty worktree can
#     never leak into the gate's verdict. The worktree layer exists only
#     for what HEAD cannot serve: untracked data modules and the LIVE
#     probes' ledgers.
check(1.8, "git archive HEAD" in src and '$SCRATCH/HEAD' in src
      and "tar -xf" in src,
      "the HEAD archive layer is missing (the worktree fallback answers "
      "non-staged imports again)")
check(1.81, 'export PYTHONPATH="$WINSCRATCH;$WINHEAD;$WINREPO"' in src,
      "PYTHONPATH lost the HEAD layer between scratch and worktree")
check(1.82, "':(exclude)app.py'" in src
      and "':(exclude)hooks/*'" in src,
      "the archive's staged-shadow exclusion list is missing — stale HEAD "
      "copies would shadow the scratch and reverse the design")
check(1.83, "no HEAD to archive" in src and "> /dev/null 2>&1" in src,
      "the archive no longer fails closed to the plain fallback (a bare "
      "scratch repo must not break the gate)")
check(1.84, 'export PYTHONPATH="$WINSCRATCH;$WINREPO"' in src,
      "the no-HEAD bail-out (bare scratch repo) lost its plain fallback")

# 1.9 THE WORKTREE-ISOLATION CONTRACT (Sep 28, session history made
#     permanent): the review must run against a snapshot worktree of the
#     STAGED content — and when the chain cannot build it, the fallback
#     banner must say WHERE it failed and WHAT git said, because a silent
#     fallback is indistinguishable from a leak (the real-commit GIT_DIR
#     poison was found only because the banner named its step).
iso = src[src.find('# ── THE ISOLATED REVIEW'):
          src.find('# Render the agent-mode findings.')]
check(1.9, iso != "" and "commit-tree" in iso and "write-tree" in iso,
      "the isolation section (snapshot from the index) is missing")
check(1.91, 'review --committed' in iso and '--uncommitted' in iso,
      "the committed/split review modes are missing from the isolation section")
# 1.92 THE FALLBACK BANNER: names the failing step + git's last stderr line
check(1.92, 'failed at:' in iso and '${_isol_step:-?}' in iso
      and 'git said: $_isol_line' in iso,
      "the fallback banner lost its failing-step + git-stderr contract")
check(1.921, '_isol_line="$(printf \'%s\' "$_isol_err" | tail -1)"' in iso,
      "the banner no longer takes git's LAST stderr line (the fatal rides "
      "behind the progress lines)")
for step in ('mktemp', 'snapshot (write-tree/commit-tree)', 'ref (update-ref)',
             'worktree-add'):
    check(1.93, f'_isol_step="{step}"' in iso,
          f"the chain no longer names its step before: {step}")
# 1.94 THE NO-LEAK CLEANUP GUARANTEE: every isolation exit path removes
#      the worktree, prunes it, and drops the snapshot ref.
cu_start = iso.find('cleanup_wt() {')
check(1.94, cu_start != -1, "cleanup_wt() is missing from the hook")
cu_end = iso.find("\n}", cu_start)
cu = iso[cu_start:cu_end if cu_end != -1 else len(iso)]
for frag in ('worktree remove --force', 'worktree prune', 'update-ref -d',
             'rm -rf "$WT"', 'WT=""'):
    check(1.941, frag in cu, f"cleanup_wt lost its {frag!r} step")
# 1.95 ONE chained EXIT trap: a hard interrupt mid-review must still run
#      the cleanup — and the gate's own scratch rm must survive chaining
#      (bash allows a single EXIT trap; a second trap would REPLACE it).
check(1.95, "trap 'cleanup_wt; rm -rf \"${SCRATCH:-}\" 2>/dev/null' EXIT" in iso,
      "the EXIT trap no longer chains cleanup_wt with the scratch rm")
n_isolated_cleanup = iso.count("    cleanup_wt")
check(1.951, iso.count("cleanup_wt") >= 5 and n_isolated_cleanup >= 2,
      f"cleanup_wt must run on EVERY path: the def, the chained EXIT trap, "
      f"and explicit calls on the success + fallback paths "
      f"(found {n_isolated_cleanup} explicit call sites)")
# 1.96 THE REAL-COMMIT POISON GUARD: hook-exported GIT_DIR/GIT_WORK_TREE
#      kill `git worktree add` outright — every isolation step outside the
#      index readers must run with the poison env stripped.
rg_start = iso.find('run_clean_git() {')
check(1.96, rg_start != -1, "run_clean_git() (the poison-env strip) is missing")
rg_end = iso.find("\n}", rg_start)
rg = iso[rg_start:rg_end if rg_end != -1 else len(iso)]
check(1.961, 'GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE' in rg,
      "run_clean_git no longer strips the hook's poison git env")
check(1.962, 'run_clean_git git worktree add' in iso,
      "worktree add no longer runs with the hook-env stripped")

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
        # (Sep 23, the gate's own first quiet commit) the scenarios OWN the
        # quiet flag: a caller-set GATE_QUIET leaks through os.environ into
        # 2.2's "default unset" scenario, flips it quiet, and the verbose
        # census fails against a healthy gate — the sandbox would lie about
        # the hook while the hook is fine. Force each scenario's flag.
        env["GATE_QUIET"] = (extra_env or {}).get("GATE_QUIET", "0")
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

    # ════════ 2.4 THE ISOLATION DRILLS (Sep 28, session history made ════
    # permanent): the fallback banner contract and the no-leak cleanup
    # guarantee, proven BEHAVIORALLY in the same scratch repo the gate
    # drills run in. The review section is entered by NOT setting
    # CODERABBIT_OFF (the gate section is skipped by staging nothing),
    # and the CLI is a stub that records WHERE it was invoked from — so
    # `--committed` must answer from the snapshot worktree (isolation)
    # and the fallback's `--uncommitted` from the repo root (leak scope,
    # honest banner). The staged-index snapshot is real git: write-tree/
    # commit-tree read THE staged index, so a staged canary rides the
    # snapshot while a worktree-only canary never can.
    print("== 2.4 isolation: fallback banner + no-leak cleanup (stub CLI) ==")
    _bin_dir = sandbox / "_stubbin"
    _bin_dir.mkdir(exist_ok=True)
    (_bin_dir / "coderabbit").write_text(
        '#!/usr/bin/env bash\n'
        '_h="$(git rev-parse HEAD 2>/dev/null)"\n'
        'echo "stub-review cwd=$(pwd -W 2>/dev/null || pwd) head=$_h" "$@"'
        ' >> "${CR_STUB_LOG:?stub log not set}"\n',
        encoding="utf-8")

    def run_review(extra_env, path_prefix=None):
        """Run the REAL hook in the sandbox with a stub CLI. Staging is
        re-derived per call: run_gate leaves triggers staged, which the
        gate section would execute — the review drills stage only their
        own canary. path_prefix (the sabotage shim) goes in FRONT of the
        stub dir — it must never REPLACE the stub dir or the hook fails
        open at 'CLI not found' before the banner ever speaks."""
        (sandbox / "_stub_calls.txt").unlink(missing_ok=True)
        # test_autotrack rides along: scenario 2.3 left its BROKEN copy
        # staged — with anything staged the gate section would execute and
        # the drills would pay for a full drive matrix (or die on timeout).
        for f in ("app.py", "background_scanner.py", "test_autotrack.py"):
            _git("restore", "--staged", f)
        (sandbox / "_review_canary.py").write_text(
            "# isolation drill canary\n", encoding="utf-8")
        _git("add", "-f", "_review_canary.py")
        env = dict(os.environ)
        # (Sep 29, the hindsight-service commit's lesson) the drills OWN
        # the review-section flags, exactly as run_gate owns the quiet
        # flag: a caller-set CODERABBIT_OFF=1 (any commitgate run BY the
        # gate itself) leaks through os.environ and silently skips the
        # sandbox's review section -- the banner drills would grade a
        # hook that never spoke. Force the section ON; per-scenario
        # overrides still ride extra_env below.
        env["CODERABBIT_OFF"] = "0"
        env["PATH"] = ((path_prefix + os.pathsep) if path_prefix else "") \
            + str(_bin_dir) + os.pathsep + env.get("PATH", "")
        env["CR_STUB_LOG"] = str(sandbox / "_stub_calls.txt")
        env.update(extra_env)
        p = subprocess.run(
            ["bash", str(hook_exe)], cwd=str(sandbox),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env, timeout=240)
        out = (p.stdout or "") + (p.stderr or "")
        calls = []
        stub = sandbox / "_stub_calls.txt"
        if stub.exists():
            calls = [ln.strip() for ln in
                     stub.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return p.returncode, out, calls

    # 2.4.1 FORCED FALLBACK: worktree add is sabotaged via a PATH shim
    #       (the snapshot/ref steps above it are real), so the chain
    #       fails AT THE NAMED STEP and the banner carries git's own
    #       fatal line. Then: the CLI must have run IN THE REPO (the
    #       fallback is an in-tree review by contract) and the half-
    #       built worktree dir + snapshot ref must be gone (no leak).
    shim = _bin_dir / "shim"
    shim.mkdir(exist_ok=True)
    shim_git = shim / "git"
    # The passthrough is the REAL git resolved NOW (shutil.which — a
    # full path): `command -v -p git` fails on Windows Git Bash (the
    # POSIX default path omits git's install dir) and /usr/bin/git does
    # not exist — a broken passthrough would silently break EVERY git
    # call in the hook (its own `git diff --cached` goes empty and the
    # hook exits at nothing-staged before the banner ever speaks).
    _real_git = (shutil.which("git") or "git").replace("\\", "/")
    shim_git.write_text(
        '#!/usr/bin/env bash\n'
        'if [ "$1" = worktree ] && [ "$2" = add ]; then\n'
        '  echo "fatal: drill: worktree add is sabotaged" >&2\n'
        '  exit 64\n'
        'fi\n'
        f'exec "{_real_git}" "$@"\n',
        encoding="utf-8")
    rc, out, calls = run_review({"CODERABBIT_ISOLATED": "1"},
                                path_prefix=str(shim))
    check(2.4, rc == 0, f"a forced isolation fallback must fail OPEN (rc={rc})")
    check(2.41, "worktree isolation unavailable (failed at:" in out,
          "the fallback banner is missing")
    check(2.42, "worktree-add;" in out,
          "the banner must name the failing step (worktree-add)")
    check(2.43, "git said: fatal: drill: worktree add is sabotaged" in out,
          "the banner must carry git's LAST stderr line")
    check(2.44, len(calls) == 1 and "--uncommitted" in calls[0]
          and "--committed" not in calls[0],
          f"the fallback must be ONE in-tree review: {calls}")
    if calls:
        _cwd = calls[0].split("cwd=")[1].split()[0] if "cwd=" in calls[0] else ""
        _c = _cwd.replace("\\", "/").lower()
        if _c.startswith("/"):   # MSYS form: /c/users/... -> c:/users/...
            import re as _re2
            _c = _re2.sub(r"^/([a-z])/", r"\1:/", _c)
        _sbx_w = str(sandbox).replace("\\", "/").lower()
        check(2.45, _c.startswith(_sbx_w),
              f"the fallback review must run in the REPO root, not {_cwd}")
    # 2.46 no-leak AFTER fallback. The worktree is created in TMPDIR —
    #       OUTSIDE this sandbox (the hook writes no cr-worktree dir in
    #       the repo; if one appears HERE that is itself a leak) — so
    #       the guarantee is swept globally: every live cr-worktree.*
    #       temp dir created by THIS drill's hook runs must be gone.
    import glob as _glob
    def _wt_sweep():
        _live = set(_glob.glob(
            str(Path(tempfile.gettempdir()) / "cr-worktree.*")))
        if _live:
            return _live                       # hot: a leak
        # cold sweep: MSYS /tmp and the user Temp dir can be different
        # roots on Windows — a leak in either must be seen
        _roots = {tempfile.gettempdir()}
        try:
            import subprocess as _sp
            _mt = _sp.run(["cygpath", "-w", "/tmp"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
            if _mt.returncode == 0 and _mt.stdout.strip():
                _roots.add(_mt.stdout.strip())
        except Exception:
            pass
        _found = set()
        for r in _roots:
            _found |= set(_glob.glob(str(Path(r) / "cr-worktree.*")))
        return _found
    _wt_left = _wt_sweep()
    _ref_left = _git("for-each-ref", "refs/cr-snapshot-*").stdout.strip()
    check(2.46, not _wt_left and not _ref_left,
          f"no-leak guarantee broken: wt={sorted(_wt_left)} ref={_ref_left!r}")

    # 2.4.2 ISOLATED SUCCESS: with real git restored, the snapshot chain
    #       must complete — the banner speaks the isolated contract, the
    #       CLI runs INSIDE the temp snapshot worktree (never the repo,
    #       never the scratch), in --committed mode only, and the 
    #       cleanup leaves nothing behind (the banner's promise, kept).
    rc, out, calls = run_review({"CODERABBIT_ISOLATED": "1"})
    check(2.47, rc == 0 and "worktree isolation unavailable" not in out,
          f"isolation must succeed in the sandbox (rc={rc})")
    check(2.48, "reviewing the STAGED content in an isolated" in out,
          "the isolated banner is missing on the success path")
    check(2.49, len(calls) == 1 and "--committed" in calls[0]
          and "--uncommitted" not in calls[0],
          f"the isolated review must be ONE --committed call: {calls}")
    if calls:
        _cwd = calls[0].split("cwd=")[1].split()[0] if "cwd=" in calls[0] else ""
        _wt_shape = _cwd.replace("\\", "/").rsplit("/", 1)[-1].startswith(
            "cr-worktree.")
        _in_sbx = str(sandbox) in _cwd
        check(2.491, _wt_shape and not _in_sbx,
              f"the isolated review must run in the TEMP worktree, got {_cwd}")
    _wt_left = _wt_sweep()
    _ref_left = _git("for-each-ref", "refs/cr-snapshot-*").stdout.strip()
    check(2.492, not _wt_left and not _ref_left,
          f"cleanup must leave no worktree/ref after success: "
          f"wt={sorted(_wt_left)} ref={_ref_left!r}")

    # 2.4.3 THE CANARY (isolation semantics, not just plumbing): the
    #       snapshot is built FROM THE INDEX, so the staged canary rides
    #       it and the sandbox's runtime/untracked churn cannot. The
    #       snapshot ref is DELETED by the cleanup before we get here,
    #       so the commit is identified through the stub's own record:
    #       the review CLI runs IN the snapshot worktree and prints the
    #       snapshot hash on ITS HEAD (git log inside the stub).
    if calls and "--committed" in calls[0]:
        _stub_line = calls[0]
        _snap_head = _stub_line.split("head=")[1].split()[0] \
            if "head=" in _stub_line else ""
        check(2.493, len(_snap_head) == 40,
              "the stub recorded no snapshot head — the chain skipped the "
              "real index snapshot")
        if _snap_head:
            _have = _git("cat-file", "-p", f"{_snap_head}^{{tree}}").stdout
            check(2.494, "_review_canary.py" in _have,
                  "the snapshot must carry the STAGED canary (index-built)")
            check(2.495, "_stub_calls.txt" not in _have,
                  "the snapshot must not carry the drill's runtime churn")
    for f in ("_review_canary.py",):
        _git("rm", "-f", "--cached", f)
        (sandbox / f).unlink(missing_ok=True)
    shim_git.unlink(missing_ok=True)
    shutil.rmtree(shim, ignore_errors=True)

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
