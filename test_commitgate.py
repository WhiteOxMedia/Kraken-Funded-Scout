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
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE / "hooks" / "pre-commit"
RERUN = os.environ.get("GUARD_RERUN") == "1"
sys.path.insert(0, str(HERE))
results = []
_RESULTS_LEDGER = []   # (label, ok, why) — the report artifact reads it

def check(n, cond, why=""):
    results.append(bool(cond))
    _RESULTS_LEDGER.append((n, bool(cond), why))
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
#      kill `git worktree add` outright — every git call that must address
#      a DIFFERENT repo (the isolation worktree, the gate's scratch
#      identity repo) must run with the poison env stripped. The
#      definition lives above the gate (hoisted Sep 30 to serve the
#      host-asset staging too), so the census reads the full source.
rg_start = src.find('run_clean_git() {')
check(1.96, rg_start != -1, "run_clean_git() (the poison-env strip) is missing")
rg_end = src.find("\n}", rg_start)
rg = src[rg_start:rg_end if rg_end != -1 else len(src)]
check(1.961, 'GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE' in rg,
      "run_clean_git no longer strips the hook's poison git env")
check(1.962, 'run_clean_git git worktree add' in iso,
      "worktree add no longer runs with the hook-env stripped")

# 1.10 THE HOST ASSETS (Sep 30): the hooks census drive — HOST-BOUND in
#      the mutation sweep — runs GATED. The scratch never had the git
#      identity the drive audits, so the drive read the sandbox probe's
#      shape-C red (no-git 2/1) for environment reasons and was classed
#      out of the gate entirely; now the scratch stages it: a git repo
#      with the staged hooks in the index and the .git/hooks mirror
#      armed FROM THE STAGED HOOKS, so the drive's no-runtime-state scan
#      judges the STAGED hook at commit time, fail-closed.
check(1.10, 'run_clean_git git -C "$SCRATCH" init -q' in src
      and 'run_clean_git git -C "$SCRATCH" add hooks' in src,
      "the gate sandbox no longer stages a git repo with the staged hooks")
check(1.11, 'cp "$SCRATCH/$h" "$SCRATCH/.git/hooks/$(basename "$h")"' in src,
      "the .git/hooks mirror is no longer armed from the staged hooks "
      "(the drive's scan must judge the STAGED hook, not a stale copy)")
# 1.11.1 the staging is poison-env-safe: a real commit exports GIT_DIR
#        et al., and an unstripped init/add would address the OUTER repo.
check(1.111, 'run_clean_git git -C "$SCRATCH" init' in src
      and 'run_clean_git git -C "$SCRATCH" ls-files' in src,
      "the host-asset staging lost its run_clean_git poison-env strip")
# 1.12 the census drive is wired into the trigger matrix: fired by ANY
#      tracked hook (hooks/[^/]+ — a future third hook is censused by
#      construction) and by its own file, mode result, its own budget —
#      and its own file is in the GATE BLOCK's guarded-source condition,
#      or staging the drive alone would skip the gate section entirely
#      (the 2.35 drill would pass vacuously) and a drive-only commit
#      would ship ungated.
check(1.12, "grep -qxE 'hooks/[^/]+|test_hooks_ignore_runtime_state\\.py' && _HH=yes" in src
      and 'maybe_run "$_HH"  test_hooks_ignore_runtime_state.py result  10' in src
      and "|hooks/[^/]+|test_hooks_ignore_runtime_state\\.py" in src,
      "the hooks census drive is not wired into the trigger matrix (_HH) "
      "or the gate block's own condition")
# 1.13 the drive RUN strips the poison env — UNCONDITIONALLY (the
#      task-61 commit lesson: a real commit exports GIT_DIR/GIT_WORK_TREE/
#      GIT_INDEX_FILE and ANY drive that itself runs git drills
#      (commitgate's sandbox) would address the OUTER repo and die for
#      environment reasons; a per-row strip fixed the census and left
#      commitgate dying). No drive legitimately wants the outer git
#      identity; the hook's own index-reading calls keep the env.
check(1.13, 'timeout "$budget" env -u GIT_DIR -u GIT_WORK_TREE '
      '-u GIT_INDEX_FILE' in src,
      "drive runs lost the unconditional poison-env strip")
# 1.14 the support lane stages EVERY tracked hook — a static
#      hooks/pre-commit entry would leave post-commit absent from the
#      scratch index and the drive would red on it (missing tracked twin).
# 1.15 the isolated review scopes to the staged diff (--base HEAD):
#      the CLI's default isolated review reads the whole tree as
#      context, and a 628-file repo exceeds its 150-file analysis cap —
#      the review then dies (or returns no findings) and the fail-open
#      hook prints "no findings" indistinguishable from a CLEAN review:
#      the silent review-coverage gap. --base HEAD pins the review to
#      the snapshot's own diff — the staged content is always a small
#      diff against its parent, so the cap is unreachable by
#      construction. (The fallback's --uncommitted stays baseless:
#      there is no single base that scopes an in-tree review to the
#      staged work.)
_iso_head = src.split('review --committed', 1)[1] if 'review --committed' in src else ''
_iso_seg = _iso_head.split('--agent', 1)[0] if _iso_head else ''
_fb_seg = src.split('review --agent --uncommitted', 1)[1]     if 'review --agent --uncommitted' in src else ''
_fb_seg = _fb_seg.split('review_json', 1)[0] if _fb_seg else ''
_flag_lines = [ln for ln in src.splitlines()
               if '--base HEAD' in ln and not ln.strip().startswith('#')]
check(1.15, len(_flag_lines) == 1
      and '--base HEAD' in _iso_seg
      and '--base' not in _fb_seg,
      "the isolated review must pass --base HEAD and the fallback "
      "must stay baseless (the 150-file cap silent-coverage fix)")
check(1.14, "for src in app.py background_scanner.py meta_layers.py "
      "profit_guard.py carve_out.py tri_watch.py contest_account.py "
      "add_xrp_watch.py diag_mutation_sweep.py" in src
      and "artifacts/mutation_sweep/vacuous_triage.md "
          "artifacts/mutation_sweep/journal.json $(git ls-files " in src,
      "the support lane no longer stages every tracked hook + the "
      "doc-probe's inputs (diag_mutation_sweep, the doc, the journal)")

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

    def run_gate(extra_env=None, stage=("app.py", "background_scanner.py"),
                 mirror_sync=()):
        """Touch the triggers, stage REAL diffs, run the REAL hook.
        mirror_sync: worktree -> sandbox .git/hooks copies made AFTER
        staging (the teeth drill arms the mirror with the drill's bad
        bytes — the worktree hook is check_mirror's reference, and the
        touch means the mirror must be synced post-stage or the mirror
        check blocks on drift before the census ever runs)."""
        for f in stage:
            p = sandbox / f
            p.write_text(p.read_text(encoding="utf-8", errors="replace")
                         + "\n# quiet-guard touch\n", encoding="utf-8")
            _git("add", "-f", f)
        for f in mirror_sync:
            shutil.copyfile(sandbox / f,
                            sandbox / ".git" / f)
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

    # THE EXPECTED DRIVES FOR A STAGED SET: parse the hook's own trigger
    # matrix (flag -> file patterns, maybe_run flag -> drive) and compute
    # which drives run when the scenario's files are staged. A frozen
    # count drifts out of truth every time the matrix grows (Sep 29:
    # _TL the EV ladder, _SW the state-writer linter + resilience).
    _trigger = {}   # flag -> [file regex, ...]
    for _m in _re.finditer(
            r"grep -qxE? '([^']+)' && _([A-Z]{1,3})=yes", _hook_txt):
        _trigger.setdefault("_" + _m.group(2), []).append(_m.group(1))

    def _expected_drives_for(stage_files):
        fired = set()
        for _fl, _pats in _trigger.items():
            if any(_re.fullmatch(_p, _f)
                   for _f in stage_files for _p in _pats):
                fired.add(_fl)
        return {_m.group(2) for _m in _re.finditer(
                    r'maybe_run "\$(_[A-Z]{1,3})"\s+(\S+\.py)', _hook_txt)
                if _m.group(1) in fired}

    _drives = _expected_drives_for(("app.py", "background_scanner.py"))
    _n_expected = len(_drives)
    check(2.01, _n_expected >= 7,
          f"the matrix census reads {_n_expected} app/scanner drives "
          f"({sorted(_drives)[:3]}...) — a parse rot shows as a tiny count")

    # 2.1 GATE_QUIET=1: passes stay silent, the counted summary speaks
    print("== 2.1 quiet: GATE_QUIET=1, all drives pass ==")
    rc, out = run_gate({"GATE_QUIET": "1"})
    check(2.1, rc == 0, f"gate rc={rc}; tail: {out[-500:]}")
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

    # ════════ 2.35 THE HOST-ASSETS DRILL (Sep 30): the hooks census ════
    # runs GATED. Staging the drive alone fires _HH and nothing else, so
    # this drill pays ONE fast drive, not the full matrix: the gate must
    # export the drive into a scratch that carries a git repo (the staged
    # hooks in its index) with the .git/hooks mirror armed from the
    # staged hooks — the sandbox probe's shape A (git+armed-mirror reads
    # 4/0 GREEN; git+no-mirror 2/2; no-git 2/1) — and the drive must come
    # out green on the staged content. The passed-count depends on the
    # tracked-hook count (4 with today's two); the CONTRACT is zero
    # failed.
    print("== 2.35 host assets: the hooks census drive runs gated ==")
    for f in ("app.py", "background_scanner.py", "test_autotrack.py"):
        _git("restore", "--staged", f)   # 2.3 left app/bs staged + autotrack
                                         # broken: _HH must fire ALONE here
    rc, out = run_gate({}, stage=("test_hooks_ignore_runtime_state.py",))
    check(2.35, rc == 0, f"gate rc={rc}; tail: {out[-400:]}")
    _m = _re.search(r"test_hooks_ignore_runtime_state\.py — "
                    r"RESULT: (\d+) passed, (\d+) failed", out)
    check(2.36, bool(_m) and _m.group(2) == "0",
          f"the hooks census must run green in the gate sandbox (the host "
          f"assets are not staged), got "
          f"{_m.groups() if _m else 'no verdict line'}")
    _expected_hh = _expected_drives_for(("test_hooks_ignore_runtime_state.py",))
    check(2.37, _expected_hh == {"test_hooks_ignore_runtime_state.py"},
          f"the _HH row must fire alone for its own drive, got {_expected_hh}")
    check(2.38, "commit gate: every triggered drive green" in out,
          "the verbose green summary is missing on the host-assets run")
    _git("restore", "--staged", "test_hooks_ignore_runtime_state.py")

    # ════════ 2.39 THE TEETH (Sep 30): the gated census judges the ════
    # STAGED hook — the mirror is armed FROM THE STAGED HOOKS, so a
    # staged hook that names runtime state fails the gate at commit time
    # (the 2026-09-21 hook-dance class, now caught before landing). A bad
    # post-commit is the cheapest carrier: hooks/[^/]+ fires the gate
    # block and _HH without paying hookparser/commitgate (_HK is
    # pre-commit-only), so this drill costs one red drive. The sandbox
    # mirror must carry the drill's bad bytes too — the worktree hook is
    # check_mirror's reference, and a stale clean mirror would block at
    # the mirror check before the census ever ran.
    print("== 2.39 teeth: a staged hook naming runtime state blocks ==")
    (sandbox / "hooks" / "post-commit").write_text(
        (sandbox / "hooks" / "post-commit").read_text(
            encoding="utf-8", errors="replace")
        + '\nBAD_DRILL="contest_watch.json"\n', encoding="utf-8")
    shutil.copyfile(sandbox / "hooks" / "post-commit",
                    sandbox / ".git" / "hooks" / "post-commit")
    rc, out = run_gate({}, stage=("hooks/post-commit",),
                       mirror_sync=("hooks/post-commit",))
    check(2.39, rc == 1,
          f"a staged hook naming runtime state must block (rc={rc})")
    check(2.391, "⛔ GATE: test_hooks_ignore_runtime_state.py FAILED" in out,
          "the census drive did not fail the gate on the staged bad hook")
    # the gate echoes a failed drive's LAST 4 lines — the census's FAIL
    # line (which names the literal) is the drive's FIRST line, pushed out
    # by the mirror PASSes — so the observable is the verdict SHAPE in the
    # gate's FAILED line: exactly the runtime-state scan red (3/1), with
    # both mirror censuses green (the mirror matches the staged bytes).
    check(2.392, "⛔ GATE: test_hooks_ignore_runtime_state.py FAILED "
          "(RESULT: 3 passed, 1 failed" in out,
          "the census must fail on exactly the runtime-state scan (3/1 — "
          "both mirror censuses pass against the staged bytes)")
    _expected_pc = _expected_drives_for(("hooks/post-commit",))
    check(2.393, _expected_pc == {"test_hooks_ignore_runtime_state.py"},
          f"a hook-only staged set must run the census alone, got {_expected_pc}")
    _git("restore", "--staged", "hooks/post-commit")
    _git("checkout", "--", "hooks/post-commit")   # worktree back to HEAD's
                                                  # clean bytes
    # re-arm the sandbox mirror with the clean bytes: the 2.4 isolation
    # drills run the hook's mirror check at the top — a stale bad-byte
    # mirror would block every one of them with a phantom drift red.
    shutil.copyfile(sandbox / "hooks" / "post-commit",
                    sandbox / ".git" / "hooks" / "post-commit")

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
    # the drill's clock (set BEFORE its first hook run): the no-leak
    # sweep only owes worktrees created from here on
    _drill_t0 = time.time()
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
        _cands = set(_glob.glob(
            str(Path(tempfile.gettempdir()) / "cr-worktree.*")))
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
        for r in _roots:
            _cands |= set(_glob.glob(str(Path(r) / "cr-worktree.*")))
        # TIME-SCOPED (the round-6 minor): only dirs MODIFIED during
        # this drill count — an older session's live cr-worktree (a
        # concurrent review on this shared box) is not this drill's
        # leak, and claiming it flakes the drive for an hour.
        import os as _os
        return {c for c in _cands
                if _os.path.getmtime(c) >= _drill_t0 - 5}
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
    # 2.499 the --base argument reaches the real CLI (behavioral, on
    #       the stub's own arg log): the isolated call carries
    #       --base HEAD (the silent-coverage fix); the fallback's
    #       --uncommitted call carries none.
    if calls:
        check(2.499, "--base" in calls[0] and "HEAD" in calls[0],
              f"the isolated review must pass --base HEAD: {calls[0]}")
    else:
        check(2.499, False, "no stub call recorded — nothing to grade")

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

# ════════ the FLEET-HONESTY AUDITOR (Sep 30, the mutation sweep's
# fleet flaw): a drive that PRINTS a pass/fail summary must also EXIT
# nonzero on failure — rc-only classifiers (run_battery historically,
# any tool tomorrow) read exit-blind tails as green. Checked
# statically, right here, for the whole fleet: the tripwire lives on
# the gate's own drive so a new blind tail blocks the next commit.
_fleet_blind = []
import re as _re_fh
for _p in sorted(Path(__file__).resolve().parent.glob("test_*.py")):
    _t = _p.read_text(encoding="utf-8", errors="replace")
    _has_verdict = (_re_fh.search(r"passed, \d+ failed|failed", _t,
                                   _re_fh.I)
                     and _re_fh.search(r"passed", _t, _re_fh.I))
    _has_exit = _re_fh.search(
        r"sys\.exit\s*\(|^\s*exit\s*\(|SystemExit", _t, _re_fh.M)
    if _has_verdict and not _has_exit:
        _fleet_blind.append(_p.name)
check("the fleet-honesty tripwire: every drive that prints a "
      "pass/fail summary also exits on it (no exit-blind tails — "
      "the Sep-30 fleet flaw stays fixed; the exit probe ignores "
      "should_exit-shaped names)",
      not _fleet_blind, f"exit-blind: {_fleet_blind}")

# ════════ the NO-AGGREGATE probe (Sep 30, the vacuous-triage second
# flaw class): a drive that renders per-check PASS/FAIL into text but
# aggregates through no recognized idiom (counters, results lists,
# RESULT/FAILURES verdicts) can NEVER fail — its verdicts are prose,
# and the mutation sweep proved that shape reads VACUOUS forever
# (test_longonly/test_contest_mode, repaired same day). A tail that
# still prints a bare pass/fail verdict with no aggregation behind it
# blocks the next commit.
_agg_pat = _re_fh.compile(
    r"RESULT: |passed, \d+ failed|FAILURES|results\.count|\.count\(False\)"
    r"|fails\s*\+=|FAIL\s*=\s*0|fails\s*=\s*\[")
_no_agg = []
for _p in sorted(Path(__file__).resolve().parent.glob("test_*.py")):
    _t = _p.read_text(encoding="utf-8", errors="replace")
    _prints = _re_fh.findall(r"print\([^\n]*(?:PASS|FAIL)[^\n]*\)", _t)
    if not _prints:
        continue
    _last = _prints[-1]
    _final_verdict = ("PASS" in _last or "FAIL" in _last) \
        and not (_agg_pat.search(_last))
    _has_agg = bool(_agg_pat.search(_t))
    _has_exit = _re_fh.search(
        r"sys\.exit\s*\(|^\s*exit\s*\(|SystemExit", _t, _re_fh.M)
    if _final_verdict and not _has_agg and not _has_exit:
        _no_agg.append(_p.name)
check("the no-aggregate tripwire: every drive that renders PASS/FAIL "
      "verdicts aggregates them (counters / RESULT / exit) — the "
      "verdict-less shape the vacuous triage found can't regrow",
      not _no_agg, f"no-aggregate: {_no_agg}")

# ════════ the DRIVE-CLASS MANIFEST probe (Sep 30, vacuity as policy):
# diag_mutation_sweep's UNTESTABLE_CLASSES pre-judges the genuinely
# untestable drives so future sweeps skip them instead of manufacturing
# VACUOUS verdicts. Statically audited here: every classed drive must
# still exist (a deleted drive's class entry is stale), the class
# strings must come from the sealed set, and the manifest must exist
# at all — a sweep without it would silently return to manufacturing.
_manifest_ok = False
_manifest_bad = []
# THE MANIFEST'S CWD-INDEPENDENT RESOLUTION (Sep 30 — the first gate run
# that ever exercised this probe IN the gate: _HK needs hooks/pre-commit
# staged, and the probe passed locally while reading cwd-relative paths
# that do not exist in the scratch). Under the gate the drive runs from
# the scratch (staged files only), so the manifest is resolved through
# the import path — PYTHONPATH's HEAD layer carries every tracked .py —
# and each classed drive's existence is checked as a SIBLING of the
# manifest file: a classed drive missing THERE is truly gone, while a
# cwd-relative check would mass-report "drive gone" for drives the
# scratch simply never staged.
_dms_path = None
# THE EXISTENCE BASE (task 62 lesson): read the manifest from the
# staged-first importlib resolution below (the gate judges the index),
# but check drive existence against the REAL REPO — when the support
# lane stages diag_mutation_sweep.py into the scratch, the scratch copy
# wins sys.path and its siblings are only the staged files, so a
# sibling-relative check would mass-report "drive gone" for all 57
# classed drives (the real repo is the LAST PYTHONPATH entry holding
# the sweep tool; validated against test_5mstore.py, which the lane
# never stages).
_repo_base = None
for _entry in reversed(
        [p for p in os.environ.get("PYTHONPATH", "").split(";") if p]):
    _cand = Path(_entry) / "diag_mutation_sweep.py"
    if _cand.exists() and (_cand.parent / "test_5mstore.py").exists():
        _repo_base = _cand.parent
        break
try:
    import importlib.util as _ilu
    _spec = _ilu.find_spec("diag_mutation_sweep")
    if _spec and _spec.origin:
        _dms_path = Path(_spec.origin)
except (ImportError, ValueError):
    pass
if _dms_path is None or not _dms_path.exists():
    _dms_path = Path("diag_mutation_sweep.py")   # legacy cwd fallback
if _repo_base is None:
    _repo_base = _dms_path.parent
_dms = _dms_path
if _dms.exists():
    _dms_txt = _dms.read_text(encoding="utf-8", errors="replace")
    _manifest_ok = "UNTESTABLE_CLASSES" in _dms_txt
    _m = _re_fh.search(
        r"UNTESTABLE_CLASSES[^=]*=\s*\{(.*?)\n\}", _dms_txt, _re_fh.S)
    if _m:
        _SEALED = {"LIVE-MARKET", "LIVE-STATE", "SOURCE-TEXT",
                   "STATIC-AUDIT", "SIBLING-CHECKOUT", "HOST-BOUND",
                   "STANDALONE"}
        for _dm in _re_fh.findall(r'"(test_\w+\.py)":\s*"(\w+-?\w*)"',
                                  _m.group(1)):
            if not (_dm[0].startswith("test_") and _dm[0].endswith(".py")):
                _manifest_bad.append(f"{_dm[0]}: not a drive name")
            elif not (_repo_base / _dm[0]).exists():
                _manifest_bad.append(f"{_dm[0]}: drive gone — stale class")
            elif _dm[1] not in _SEALED:
                _manifest_bad.append(f"{_dm[0]}: class '{_dm[1]}' not sealed")
    else:
        _manifest_bad.append("UNTESTABLE_CLASSES block not parseable")
check("the drive-class manifest: diag_mutation_sweep pre-judges the "
      "genuinely untestable drives (classes sealed, entries alive) — "
      "future sweeps skip them instead of manufacturing VACUOUS",
      _manifest_ok and not _manifest_bad,
      f"manifest={_manifest_ok} bad={_manifest_bad}")

# ════════ the DOC-JOURNAL consistency probe (Sep 30, task 62): the
# fleet-verdict document is a VERDICT RECORD, so its numbers are checked
# against the journal they describe — parsed from the STAGED content
# (git show :path, the gate's own ground truth), never the worktree: a
# half-staged doc/journal pair must be judged as it will land, and the
# probe must work in the gate's scratch cwd (the hook exports the doc,
# the journal, and diag_mutation_sweep.py beside the drive). Any doc
# claim that drifts from the journal blocks the commit.
_doc_bad = []
_doc_ok = False
import collections
import json
import subprocess as _sp


def _staged(path: str) -> bytes:
    """The file as it will land: the staged blob, worktree fallback."""
    r = _sp.run(["git", "show", f":{path}"], capture_output=True)
    return r.stdout if r.stdout else open(path, "rb").read()


try:
    _doc = _staged("artifacts/mutation_sweep/vacuous_triage.md") \
        .decode("utf-8")
    _jdoc = json.loads(_staged("artifacts/mutation_sweep/journal.json")
                       .decode("utf-8"))
    _jres = _jdoc.get("results", {})
    _ledger = collections.Counter(
        e.get("verdict") for e in _jres.values())

    # 1. the ledger table: | KILLED | 103 | ... — every verdict row's
    #    count must equal the journal's, the rows must SUM to the
    #    journal's total (no verdict hides between the rows), and every
    #    journal verdict must be one of the six named rows (an unknown
    #    verdict is drift the doc cannot represent). (Task 62 same-day
    #    triage of the CR [low]: TIMEOUT was only substring-checked and
    #    the total was never derived.)
    _VERDICTS = ("KILLED", "UNTESTABLE", "VACUOUS", "BASELINE-RED",
                 "UNMAPPED", "TIMEOUT")
    _row_sum = 0
    for _v in _VERDICTS:
        _m2 = _re_fh.search(rf"\| {_v} \| (\d+) \|", _doc)
        if _m2 is None:
            _doc_bad.append(f"{_v}: ledger row missing from the doc")
            continue
        _row_sum += int(_m2.group(1))
        if int(_m2.group(1)) != _ledger.get(_v, 0):
            _doc_bad.append(f"{_v}: doc says {_m2.group(1)}, "
                            f"journal says {_ledger.get(_v, 0)}")
    if _row_sum != len(_jres):
        _doc_bad.append(f"ledger rows sum to {_row_sum}, "
                        f"journal has {len(_jres)} entries")
    _unknown = sorted(set(_ledger) - set(_VERDICTS))
    if _unknown:
        _doc_bad.append(f"unknown verdict(s) in the journal: {_unknown}")

    # 2. the class headings: '### 3.1 LIVE-MARKET (6)' — every sealed
    #    class's count must equal the journal's drive_class mix, every
    #    journal class must have a heading
    _cls = collections.Counter(
        e.get("drive_class") for e in _jres.values()
        if e.get("verdict") == "UNTESTABLE")
    _doc_classes = dict(_re_fh.findall(
        r"### 3\.\d+ ([A-Z-]+) \((\d+)\)", _doc))
    for _c, _n in _cls.items():
        if _c not in _doc_classes:
            _doc_bad.append(f"class {_c}: no section in the doc")
        elif int(_doc_classes[_c]) != _n:
            _doc_bad.append(f"class {_c}: doc says "
                            f"{_doc_classes[_c]}, journal says {_n}")
    for _c in _doc_classes:
        if _c not in _cls:
            _doc_bad.append(f"class {_c}: in the doc, not in the journal")
    # (SCAFFOLD-ONLY needs no special case: the generic both-directions
    # loop above already demands the section iff the journal carries the
    # class — an empty class legitimately has no section.)

    # 3. (the ledger table above already pins every current number; the
    #    doc's HISTORICAL notes — the superseded task-56 snapshot — are
    #    deliberately not matched: they describe a past journal by
    #    design, and pinning them to today's journal would falsify
    #    history.)

    _doc_ok = not _doc_bad
except Exception as _exc:
    _doc_bad.append(f"probe crashed: {_exc!r}")
check("the doc-journal consistency probe: vacuous_triage.md's ledger "
      "and class counts are re-derived from the STAGED journal — "
      "the verdict record cannot drift from the record it describes",
      _doc_ok,
      f"doc_ok={_doc_ok} bad={_doc_bad}")

# ════════ summary ════════
passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
# THE FULL REPORT, ALWAYS (the gate-vs-local debug gap): the gate only
# echoes a failed drive's last 4 lines — a locally-green drive failing
# under the gate was undebuggable. The verdict, the failures with their
# labels, and the check ledger land in scratch where the gate's own
# mirror tripwire and the operator can read them.
import scratch_space as _ss
_fails = [r for r in _RESULTS_LEDGER if not r[1]]
(_ss.report_temp("_commitgate_check.txt").write_text(
    "\n".join([f"{'PASS' if ok else 'FAIL'}  {n}"
               + (f"  [{d}]" if d and not ok else "")
               for n, ok, d in _RESULTS_LEDGER]
              + [f"\nRESULT: {passed} passed, {failed} failed"
                 + ("" if not _fails else
                    f"\nFAILED: {[n for n, ok, d in _RESULTS_LEDGER if not ok]}")]),
 encoding="utf-8"))
sys.exit(1 if failed else 0)
