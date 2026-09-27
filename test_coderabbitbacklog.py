"""test_coderabbitbacklog.py - THE WATCHDOG'S DRIVE (Sep 25).

Grades diag_coderabbit_backlog.py's classification logic hermetically:
a THROWAWAY git repo with controlled commit times and a THROWAWAY
reviews tree with fake batches stand in for the workspace, so the
module constants are repointed and the live %LOCALAPPDATA% tree and
both real repos are never touched by this drive.

  1. batch-to-repo mapping: a batch matches ONLY by its git.json
     workingDirectory (a third repo's batch belongs to neither)
  2. the coverage ledger: commits newer than the newest batch are the
     open set; a batch with no batch at all means EVERY commit is open
  3. the N-hour gate: age over the threshold FAILS with names; under
     it passes; a never-reviewed repo fails at any threshold
  4. robustness: malformed git.json files are skipped, not crashed on
  4b. THE MAJORS SURFACE (Sep 26): the newest batch's MAJOR findings
     print as file:line + title (capped, counted) - the triage
     surface, so the next triage needs no manual JSON hunt; a body
     marker still never prints, a bodyless/broken finding is skipped,
     no majors means a clean line, and majors never change rc
  5. output hygiene: names, hashes, ages, majors' file:line+title -
     review CONTENTS (bodies) are never printed (a marker inside a
     fake findings BODY must not surface)
  6. hermetic: the real reviews tree and both real repos are never
     opened (the sandbox paths are the only ones in the output)

House conventions: check(n, cond, why), RESULT line, sys.exit(1).
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
passed = failed = 0


def check(n, cond, why=""):
    global passed, failed
    print(f"{'PASS' if cond else 'FAIL'}  {n}  {why}")
    passed, failed = passed + (1 if cond else 0), failed + (0 if cond else 1)


SANDBOX = Path(tempfile.mkdtemp(prefix="cr_backlog_drive_"))
sys.path.insert(0, str(HERE))

import diag_coderabbit_backlog as dcb                     # noqa: E402

# ── the sandbox: two fake repos + a fake reviews tree ────────────────
REPO_A = SANDBOX / "repoA"
REPO_B = SANDBOX / "repoB"
REPO_C = SANDBOX / "repoC"          # has a batch, but is neither target
FAKE_ROOT = SANDBOX / "reviews"

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(repo, *args, when=None):
    env = dict(os.environ, **_GIT_ENV)
    if when:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S +0000",
                              time.gmtime(when))
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
    return subprocess.run(
        ["git", "-C", str(repo), *args], env=env, capture_output=True,
        text=True, timeout=30)


def _mk_repo(path, commit_times):
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    for i, t in enumerate(commit_times):
        _git(path, "commit", "--allow-empty", "-m", f"c{i}", when=t)
    out = _git(path, "log", "--format=%h %ct").stdout.split()
    heads = out[0::2]
    times = [int(x) for x in out[1::2]]
    return list(zip(heads, times))


NOW = int(time.time())
H = 3600
commits_a = _mk_repo(REPO_A, [NOW - 50 * H, NOW - 2 * H])   # old + fresh
commits_b = _mk_repo(REPO_B, [NOW - 100 * H, NOW - 80 * H])  # both stale
_mk_repo(REPO_C, [NOW - 3 * H])


def _mk_batch(repo, ts, head="abc1234", marker=None, broken=False):
    bid = f"{repo.name}-{ts}"
    bdir = FAKE_ROOT / "session" / "sess" / "reviews" / bid
    bdir.mkdir(parents=True, exist_ok=True)
    if broken:
        (bdir / "git.json").write_text("{not json", encoding="utf-8")
        return bdir
    (bdir / "git.json").write_text(json.dumps({
        "workingDirectory": str(repo), "head": head, "timestamp": ts,
    }), encoding="utf-8")
    if marker:
        # a FAKE findings file: its contents must never reach the diag's output
        (bdir / "finding.json").write_text(
            json.dumps({"comment": marker}), encoding="utf-8")
    return bdir


# repoint the diag at the sandbox BEFORE any run
dcb.REVIEW_ROOT = FAKE_ROOT
dcb.SCOUT = REPO_A
dcb.MONERO = REPO_B
dcb.BACKLOG_HOURS = 24


def _run():
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = dcb.main()
    return rc, buf.getvalue()


print("== 1. THE MAPPING: a batch belongs to its workingDirectory only ==")
_ts_a = NOW - 49 * H           # reviews repoA's OLDER commit
_mk_batch(REPO_A, _ts_a, head=commits_a[1][0])
_mk_batch(REPO_C, NOW - 2 * H)  # a third repo's batch - neither target
rc, out = _run()
check(1.1, "repoA" in out and "repoB" in out,
      "both target repos are reported")
check(1.2, "repoC" not in out,
      "a third repo's batch is never mistaken for a target's")

print("== 2. THE LEDGER: open = newer than the newest batch ==")
check(2.1, "1 open commit(s)" in out,
      f"repoA: the fresh commit is open, the covered one is not "
      f"({commits_a[1][0]} open, {commits_a[0][0]} covered)")
check(2.2, "2 open commit(s)" in out and "oldest 100.0h" in out,
      "repoB: never reviewed -> EVERY commit is open, oldest named")
check(2.3, f"head {commits_a[1][0]}" in out,
      "the batch's reviewed head is reported by hash")

print("== 3. THE GATE: age against the threshold ==")
check(3.1, rc == 1 and "no commit waits more than 24h" in out
      and out.count("FAIL") >= 1,
      "repoA passes the age gate (2h < 24h) but repoB fails it - rc 1")
dcb.BACKLOG_HOURS = 1
rc2, out2 = _run()
check(3.2, "no commit waits more than 1h" in out2
      and out2.count("FAIL") >= 2,
      "with a 1h threshold repoA's 2h-old open commit fails too")
dcb.BACKLOG_HOURS = 24
_rc3, out3 = _run()
_b_all = _mk_batch(REPO_B, NOW - 1 * H, head=commits_b[0][0])
_rc4, out4 = _run()
check(3.3, _rc4 == 0 and "RESULT: " in out4 and " 0 failed" in out4,
      "once every commit is covered inside the window, the diag is "
      "all green with rc 0")

print("== 4. ROBUSTNESS: malformed metadata is skipped ==")
_mk_batch(REPO_A, NOW - 0 * H, broken=True)      # broken git.json
rc5, out5 = _run()
check(4.1, rc5 == 0,
      "a broken git.json is skipped - the newest GOOD batch still "
      "governs, no crash")

print("== 4b. THE MAJORS SURFACE: file:line + title, bodies never ==")
_SECRET = "REVIEW-CONTENT-MARKER-do-not-print"   # defined here: 4b uses it
# repoA's newest batch gets three findings: a major, a minor, and a
# malformed file. repoB's newest batch (the one _mk_batch made at
# NOW-1h in check 3.3) gets a body-only finding carrying the SECRET
# marker of section 5 - its body must never surface even though its
# file IS read now.
_b = _mk_batch(REPO_A, NOW - 0.5 * H, head=commits_a[0][0])
(_b / "m1.json").write_text(json.dumps({
    "fileName": "src/trading.py", "startLine": 2754,
    "title": "Keep the throttle independent of topic availability.",
    "severity": "major", "comment": "body-of-m1-" + _SECRET,
}), encoding="utf-8")
(_b / "m2.json").write_text(json.dumps({
    "fileName": "src/grade.py", "title": "a minor nit", "severity": "minor",
    "comment": "minor body"}), encoding="utf-8")
(_b / "broken.json").write_text("{not json", encoding="utf-8")
(_b / "bodyless.json").write_text("[]", encoding="utf-8")
_mk_batch(REPO_B, NOW - 0.5 * H, head=commits_b[0][0])
# repoB's NEWEST batch dir via the module's own mapping (broken
# git.json files are already skipped there - do not re-glob by hand)
_rb = dcb._batches_for(REPO_B)[0][2]
(_rb / "secret_major.json").write_text(json.dumps({
    "fileName": "src/secret.py", "startLine": 1,
    "title": "a titled major", "severity": "major",
    "comment": "body-" + _SECRET}), encoding="utf-8")
rc_m, out_m = _run()
check("4b.1", "majors awaiting triage in the newest batch (1)" in out_m
      and "src/trading.py:2754" in out_m
      and "Keep the throttle independent of topic availability." in out_m,
      "repoA's newest batch: exactly its 1 major surfaces as "
      "file:line + title (the minor and the broken file do not)")
check("4b.2", "a titled major" in out_m
      and "src/secret.py" in out_m,
      "repoB's newest batch major surfaces too (per-repo surface)")
check("4b.3", _SECRET not in out_m,
      "bodies are STILL never printed - only fileName/line/title "
      "metadata is read")
check("4b.4", rc_m == 0,
      "majors are advisory triage work - the coverage gate's rc is "
      "unchanged by their existence")
# the clean line: a fresh batch with NO majors
_mk_batch(REPO_B, NOW + 1)
_rcn, out_n = _run()
check("4b.5", "records no majors" in out_n,
      "a batch with no majors gets its clean line")

print("== 5. OUTPUT HYGIENE: contents never print ==")
# NOTE (4b): the body-marker batches for this section are already in
# place from 4b - m1.json and secret_major.json both carry _SECRET in
# their comment bodies, and 4b.3 proved the bodies never print. Here
# the legacy marker-file shape (a bare {comment: ...} finding) is
# exercised again on top:
_mk_batch(REPO_B, NOW - 70 * H, marker=_SECRET)
_rc6, out6 = _run()
check(5.1, _SECRET not in out6 and "finding.json" not in out6,
      "the fake findings file's contents never surface - names, "
      "hashes, ages, and majors' titles only")
check(5.2, str(SANDBOX) in out6 and str(
    Path(os.environ.get("LOCALAPPDATA", "")) / "coderabbit") not in out6,
      "only sandbox paths appear - the live reviews tree was never read")

print("== 6. HERMETIC: the real repos never opened ==")
check(6.1, "kraken_scout" not in out6 and "monero_project" not in out6,
      "no real repo name appears anywhere in the sandboxed runs")

import shutil
shutil.rmtree(SANDBOX, ignore_errors=True)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
