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
  4c. THE TRIAGE MEMORY (Sep 27): a commit LATER than the batch
     whose message names a majored file retires it from the
     surface - and a commit sitting in head..HEAD but predating
     the batch does NOT (the timestamp gate), so the list
     reflects reality after fixes land without re-serving
     triaged work or trusting rebased ghosts
  5. output hygiene: names, hashes, ages, majors' file:line+title -
     review CONTENTS (bodies) are never printed (a marker inside a
     fake findings BODY must not surface)
  6. hermetic: the real reviews tree and both real repos are never
     opened (the sandbox paths are the only ones in the output)
  7. THE RECEIPT LEDGER (Oct 3): the hook's per-review receipts merge
     into the coverage ledger - a clean receipt covers, a bounced one
     does not, torn lines are skipped
  8. THE WORKTREE ATTRIBUTION (Oct 3): an isolated review's batch names
     a temp worktree as its workingDirectory; its reviewed head is a
     snapshot commit of the repo's own object database, and that
     membership attributes it

House conventions: check(n, cond, why), RESULT line, sys.exit(1).
"""
import io
import json
import os
import re
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
check("4b.5", "records no untriaged majors" in out_n,
      "a batch with no majors gets its clean line")

print("== 4c. THE TRIAGE MEMORY: a later commit naming the file retires it ==")
# repoA's tip (the fresh, in-range commit) is AMENDED to carry the
# fix's message naming the majored file. The 4b batch stays the
# newest batch; only the commit's message and date change - so
# whatever the surface does next is the memory's doing alone.
_am = _git(REPO_A, "commit", "--amend", "--allow-empty", "-m",
           "fix: retire the review major in src/trading.py", when=NOW + 10)
check("4c.0", _am.returncode == 0,
      "drive setup: the fix-message amend succeeded")
_rc_a, out_a = _run()
check("4c.1", "src/trading.py" not in out_a
      and "repoA: the newest batch records no untriaged majors" in out_a,
      "a commit NEWER than the batch whose message names "
      "src/trading.py retires that major - repoA's list goes "
      "clean instead of re-serving triaged work")
check("4c.2", "1 open commit(s)" in out_a and _rc_a == 0,
      "the SAME run still reports repoA's open amended commit - "
      "the major was retired by the MEMORY, not by the commit "
      "or the batch vanishing (and majors stay advisory: rc 0)")
# the negative control: the SAME message on a commit whose
# committer date PREdates the batch. It sits inside head..HEAD
# (the amend/rebase shape) but it is not LATER - the memory
# must not trust it, or a rebased ghost retires real work.
_pre = _git(REPO_A, "commit", "--amend", "--allow-empty", "-m",
            "fix: retire the review major in src/trading.py",
            when=NOW - 10 * H)
check("4c.3", _pre.returncode == 0,
      "drive setup: the anti-dated amend succeeded")
_rc_p, out_p = _run()
check("4c.4", "src/trading.py:2754" in out_p
      and "majors awaiting triage in the newest batch (1)" in out_p,
      "the pre-batch-dated receipt does NOT retire the major - "
      "head..HEAD membership alone is not `later`; the timestamp "
      "gate keeps the memory honest")
# the LIVE SHAPE: the receipt names the file only in its BODY - the
# monero triage commit's shape (its subject names none of the four
# files its body fixed). This doubles as the restore, so sections
# 5-6 see the settled post-date receipt state.
_am2 = _git(REPO_A, "commit", "--amend", "--allow-empty", "-m",
            "triage: all four review majors" + chr(10) + chr(10)
            + "fixed src/trading.py and two siblings", when=NOW + 10)
check("4c.5", _am2.returncode == 0,
      "drive setup: the body-only receipt amend succeeded")
_rc_b, out_b = _run()
check("4c.6", "src/trading.py" not in out_b
      and "repoA: the newest batch records no untriaged majors" in out_b,
      "a receipt naming the file ONLY in its body still retires the "
      "major - the live shape where the subject names none of the "
      "fixed files")

print("== 7. THE RECEIPT LEDGER: clean reviews cover too ==")
# THE OCT 3 FINDING: the CLI records a batch ONLY when findings exist —
# a clean review leaves nothing — so a repo whose reviews all come back
# clean reads as perpetually unreviewed. The hook now appends a receipt
# per completed review into the repo's scratch/, and the diag merges
# them into the coverage ledger (coverage outcomes only).
REPO_D = SANDBOX / "repoD"
commits_d = _mk_repo(REPO_D, [NOW - 30 * H, NOW - 1 * H])
(REPO_D / "scratch").mkdir(exist_ok=True)
_saved_mono = dcb.MONERO          # section 7 grades repoD THROUGH the
                                  # MONERO seat; restored before section 8
dcb.MONERO = REPO_D
# 7.2 (armor): a receipt that names a NON-coverage outcome must not
# cover — repoD has no batches, so only receipts could cover it
(REPO_D / "scratch" / "reviews_receipts.jsonl").write_text(
    json.dumps({"ts": NOW - int(0.5 * H), "head": commits_d[1][0],
                "mode": "isolated", "outcome": "timeout"}) + "\n",
    encoding="utf-8")
_rc7a, out7a = _run()
check("7.2", re.search(r"repoD:.*\[2 open commit\(s\)", out7a) is not None,
      "a timeout receipt is NOT coverage - repoD's commits stay open "
      "(a bounced review covered nothing)")
# 7.1 (red-first): a CLEAN receipt IS coverage - the same repoD with a
# clean receipt at -0.5h must read fully covered (both commits predate it)
with open(REPO_D / "scratch" / "reviews_receipts.jsonl", "a",
          encoding="utf-8") as fh:
    fh.write(json.dumps({"ts": NOW - 600, "head": commits_d[1][0],
                         "mode": "isolated", "outcome": "clean"}) + "\n")
_rc7b, out7b = _run()
check("7.1", _rc7b == 0 and "repoD: every commit in the window is covered"
      in out7b,
      "a clean-review receipt covers the commits that predate it - the "
      "repo whose reviews come back clean is no longer perpetually open")
# 7.3 (armor): torn/garbage receipt lines are skipped, never crash
with open(REPO_D / "scratch" / "reviews_receipts.jsonl", "a",
          encoding="utf-8") as fh:
    fh.write("{not json\n")
    fh.write("[]\n")
_rc7c, out7c = _run()
check("7.3", "Traceback" not in out7c and "RESULT: " in out7c,
      "torn or non-object receipt lines never crash the ledger reader "
      "(the run ends in a clean RESULT line either way)")
dcb.MONERO = _saved_mono   # restore the seat before section 8

print("== 8. THE WORKTREE ATTRIBUTION: ODB membership is the repo ==")
# The isolated review's batches name a temp cr-worktree as their
# workingDirectory - unattributable by path. But its reviewed head is a
# SNAPSHOT COMMIT BUILT IN THE REPO'S OWN OBJECT DATABASE, so head
# membership in the repo's ODB is the honest attribution.
def _mk_wt_batch(ts, head):
    bid = f"wt-{ts}"
    bdir = FAKE_ROOT / "session" / "wt" / "reviews" / bid
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "git.json").write_text(json.dumps({
        "workingDirectory": str(SANDBOX / "cr-worktree.XXXX"),
        "head": head, "timestamp": ts}), encoding="utf-8")
    return bdir

# 8.2 (armor): a worktree batch whose head resolves NOWHERE must not
# attribute - a foreign snapshot (or garbage hash) stays foreign, so
# repoA's amended tip stays open (the amended tip is seconds old, so
# the age gate alone cannot fail; the OPEN detail is the assertion)
_mk_wt_batch(NOW + 5, "deadbeef000")
_rc8a, out8a = _run()
check("8.2", re.search(r"repoA:.*\(1 open commit\(s\)", out8a) is not None,
      "an unresolvable worktree batch attributes to neither repo - "
      "repoA's amended tip stays open, attributed to nothing")
# 8.1 (red-first): the SAME shape with a head that IS one of repoA's
# real commits attributes to repoA - its timestamp postdates the
# amended tip (NOW+10), so attribution covers the whole window
_wtb = _mk_wt_batch(NOW + 15, commits_a[1][0])
_rc8b, out8b = _run()
check("8.1", _rc8b == 0
      and "repoA: every commit in the window is covered" in out8b,
      "a cr-worktree batch whose reviewed head lives in repoA's object "
      "database IS repoA's review - the week of isolated reviews stops "
      "reading as an open backlog")

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

print("== 7. THE TOKEN NORM (the Oct 6 minors triage: a sentence period")
print("   rode the regex's own charset and blinded the triage memory) ==")
check(7.1, dcb._norm_file_token("background_scanner.py.") == "background_scanner.py",
      "a sentence-ending period never rides the basename")
check(7.2, dcb._norm_file_token("ops/fleet_sweep.py") == "fleet_sweep.py"
      and dcb._norm_file_token("exit_extension.py-") == "exit_extension.py",
      "path-shaped tokens basename; a trailing hyphen is punctuation")
check(7.3, dcb._norm_file_token("test_canary.py-v2") == "test_canary.py-v2",
      "a mid-name hyphen is part of the name, never stripped")
check(7.4, "named |= {_norm_file_token(ln.split(" in
      open((Path(__file__).resolve().parent / "diag_coderabbit_backlog.py"),
           encoding="utf-8").read(),
      "the triage memory normalizes every token it stores")

import shutil
shutil.rmtree(SANDBOX, ignore_errors=True)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
