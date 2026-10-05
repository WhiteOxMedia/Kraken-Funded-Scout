"""test_hookreceipts.py - THE COVERAGE RECEIPT DRIVE (Oct 5).

Grades the post-commit review hook's RECEIPT contract hermetically:
the hook reviews the commit that JUST landed (CodeRabbit, agent mode)
and appends one receipt per completed review into the REPO's own
scratch/reviews_receipts.jsonl — because the CLI records a finding
batch only when findings exist, a repo whose reviews all come back
clean would otherwise read as perpetually unreviewed in kraken_scout's
backlog watchdog (diag_coderabbit_backlog.py grades monero_project
every run; its receipt merge counts ONLY outcome=clean as coverage).

  1. the hook exists, is wired via core.hooksPath, and carries the
     receipt writer with every outcome recorded (source pins)
  2. CODERABBIT_OFF=1 skips the review entirely — exit 0 and NO
     receipt (a skipped review is not a clean review)
  3. behavior through a FAKE coderabbit binary on PATH: empty stream
     and explicitly-empty JSON both mint clean receipts; a bounced
     review (rc != 0) mints "timeout" and still exits 0 (a post-commit
     hook must never fail a commit that already happened); garbage
     output mints "unparsed"; the receipt is append-only, torn lines
     never break it, and the shape is exactly what the watchdog parses
  4. the cross-repo contract: kraken_scout's watchdog grades
     monero_project — repointed at this drive's sandbox, a repo whose
     only review record is a fresh clean receipt PASSES the watchdog,
     and the same repo WITHOUT the receipt fails the way monero really
     did before the hook (no batch ever, clean reviews unrecorded)

House conventions: RESULT-style, no network, no live review, the real
reviews tree and the real repos are never opened.
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCOUT = HERE.parent / "kraken_scout"
results = []
passed = failed = 0


def check(n, cond, why=""):
    global passed, failed
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}  {why}")
    passed, failed = passed + (1 if cond else 0), failed + (0 if cond else 1)


HOOK = HERE / "githooks" / "post-commit"
src = HOOK.read_text(encoding="utf-8").replace("\r\n", "\n") \
    if HOOK.exists() else ""

print("== 1. THE HOOK EXISTS AND CARRIES THE RECEIPT WRITER ==")
check(1.1, HOOK.exists(),
      "githooks/post-commit exists (core.hooksPath=githooks serves it)")
check(1.2, src.startswith("#!/bin/sh") or src.startswith("#!/bin/bash"),
      "the hook is a POSIX shell hook (git runs it through sh on Windows)")
check(1.3, "scratch/reviews_receipts.jsonl" in src and "cr_receipt()" in src
      and "|| true" in src,
      "the receipt writer writes the repo's own scratch JSONL, "
      "append-only best-effort (a receipt failure never blocks)")
check(1.4, all(f"cr_receipt {o}" in src for o in
               ("clean", "unparsed", "errors", "timeout"))
      and src.count("cr_receipt clean") >= 2,
      "every review exit records its outcome — both clean paths "
      "(empty stream and explicitly-empty JSON) mint clean receipts")
check(1.5, '[ -z "$BIN" ] && exit 0' in src,
      "no reviewer binary on PATH exits 0 silently — nothing reviewed, "
      "nothing recorded, the commit already happened")
check(1.6, "CODERABBIT_OFF" in src,
      "CODERABBIT_OFF=1 skips the whole review before anything runs")
check(1.7, "git show" in src and "HEAD" in src,
      "the review grades the commit that JUST landed (git show HEAD)")
check(1.8, "FILTERED" in src and '[ -z "$FILTERED" ] && exit 0' in src,
      "the code-only churn filter survives the port — a churn-only "
      "commit is nothing to review and nothing to record")
check(1.9, "git rev-parse --show-toplevel" in src,
      "the ledger lands in THIS repo's scratch (toplevel-derived), "
      "never the caller's cwd")
check(1.10, "--base HEAD~1" in src and "HEAD~1" in src,
      "the review is pinned to the LANDING COMMIT (--base HEAD~1 — bare "
      "--committed diffs HEAD vs HEAD and can hang the CLI; the first "
      "live run bounced at the 300s timeout), with a parentless-repo "
      "skip (the first commit has nothing to diff against)")
check(1.11, src.count("cr_receipt errors") == 1
      and 'if [ -n "$errtypes" ]' in src and "ERRORTYPE " in src
      and '"errorType"' in src,
      "the errors receipt is CONDITIONAL on review-error records (the "
      "scout's shape — findings alone need no receipt, the CLI's batch "
      "is their record), and a PURE errorType stream can never read as "
      "CLEANJSON (COUNT 0 + ERRORTYPE keeps the shape honest)")
check(1.12, "${#" not in src and ":0:40000" not in src,
      "no bash-only expansions under the /bin/sh shebang — the CLI reads "
      "the repo itself, so the scout's prompt-pipe size truncation is "
      "dead code here (the live review's own minor) and is gone; the "
      "churn filter's only job is the churn-only skip gate")

print("== 2. SKIP POSTURE: OFF means OFF (no receipt) ==")
SANDBOX = Path(tempfile.mkdtemp(prefix="hookreceipt_"))
_GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          env=dict(os.environ, **_GIT_ENV),
                          capture_output=True, text=True, timeout=30)


def _mk_repo(path, single=False):
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "app.py").write_text("print('root')\n", encoding="utf-8")
    _git(path, "add", "app.py")
    _git(path, "commit", "-m", "root")
    if not single:
        (path / "app.py").write_text("print('the landing commit')\n",
                                     encoding="utf-8")
        _git(path, "add", "app.py")
        _git(path, "commit", "-m", "the landing commit")
    return _git(path, "rev-parse", "HEAD").stdout.strip()[:7]


def _run_hook(repo, extra_env=None, fake_cr=None):
    """Run the hook the way git does (sh, cwd = the repo), with a fake
    coderabbit binary optionally shadowed onto PATH. Returns (rc, env)."""
    env = dict(os.environ, **_GIT_ENV)
    fake_bin = ""
    if fake_cr is not None:
        fake_bin = tempfile.mkdtemp(prefix="hookreceipt_bin_")
        cr = Path(fake_bin) / "coderabbit"
        cr.write_text(fake_cr, encoding="utf-8", newline="\n")
        cr.chmod(0o755)
        env["PATH"] = fake_bin + os.pathsep + env.get("PATH", "")
    env.update(extra_env or {})
    r = subprocess.run(["sh", str(HOOK)], cwd=str(repo), env=env,
                       capture_output=True, text=True, timeout=120)
    if fake_bin:
        shutil.rmtree(fake_bin, ignore_errors=True)
    return r.returncode, r


REPO1 = SANDBOX / "repo1"
_mk_repo(REPO1)
_ledger = REPO1 / "scratch" / "reviews_receipts.jsonl"


def _recs():
    """The repo's receipt ledger, parsed the watchdog's way — a missing
    or torn ledger is an honest empty (never a crash)."""
    out = []
    if _ledger.exists():
        for ln in _ledger.read_text(encoding="utf-8").splitlines():
            try:
                v = json.loads(ln)
            except ValueError:
                continue
            if isinstance(v, dict):
                out.append(v)
    return out


_rc, _r = _run_hook(REPO1, {"CODERABBIT_OFF": "1"})
check(2.1, _rc == 0 and not _ledger.exists(),
      "a skipped review exits 0 and mints NO receipt (skip is not clean)")
REPO0 = SANDBOX / "repo0"      # the very FIRST commit: no HEAD~1 exists
_mk_repo(REPO0, single=True)
_rc, _r = _run_hook(REPO0, fake_cr="#!/bin/sh\nexit 0\n")
check(2.2, _rc == 0 and not (REPO0 / "scratch" /
                             "reviews_receipts.jsonl").exists(),
      "a parentless first commit skips quietly (nothing to diff --base "
      "against; no receipt — the edge never recurs in a mature repo)")

print("== 3. BEHAVIOR THROUGH A FAKE REVIEWER ==")
# empty stream, rc 0 -> the CLI said nothing: CLEAN receipt
_rc, _r = _run_hook(REPO1, fake_cr="#!/bin/sh\nexit 0\n")
recs = _recs()
check(3.1, _rc == 0 and len(recs) == 1 and recs[0].get("outcome") == "clean",
      "an empty review stream is a clean receipt (the CLI's silence "
      "after a completed run IS the clean review)")
check(3.2, recs and recs[0].get("head") == _git(REPO1, "rev-parse",
      "--short=7", "HEAD").stdout.strip() and isinstance(
          recs[0].get("ts"), int) and abs(recs[0]["ts"] - time.time()) < 600,
      "the receipt names the reviewed HEAD and an epoch ts "
      "(the watchdog's parse shape: ts + outcome, head for forensics)")

# explicitly-empty JSON, rc 0 -> CLEANJSON path -> another clean receipt
_rc, _r = _run_hook(REPO1, fake_cr='#!/bin/sh\nprintf \'[]\\n\'\n')
recs2 = _recs()
check(3.3, _rc == 0 and len(recs2) == 2
      and all(r.get("outcome") == "clean" for r in recs2),
      "an explicitly-empty JSON review ([]) mints the second clean "
      "receipt — append-only across runs, never overwritten")

# a bounced review (rc != 0) -> "timeout" outcome, exit 0, commit stands
_rc, _r = _run_hook(REPO1, fake_cr="#!/bin/sh\necho boom >&2\nexit 7\n")
recs3 = _recs()
check(3.4, _rc == 0 and len(recs3) == 3
      and recs3[-1].get("outcome") == "timeout",
      "a bounced review mints a timeout receipt and still exits 0 — "
      "a post-commit hook never fails a commit that already happened")

# garbage output, rc 0 -> unparsed receipt
_rc, _r = _run_hook(REPO1, fake_cr="#!/bin/sh\necho 'total nonsense'\n")
recs4 = _recs()
check(3.5, _rc == 0 and recs4[-1].get("outcome") == "unparsed",
      "an unparsable stream mints an unparsed receipt (forensics, "
      "never coverage)")

# torn lines must not break the writer (append-only robustness)
with open(_ledger, "a", encoding="utf-8") as fh:
    fh.write("not json\n")
    fh.write('{"ts": "junk", "outcome": "clean"}\n')
_rc, _r = _run_hook(REPO1, fake_cr="#!/bin/sh\nexit 0\n")
recs5 = [r for r in _recs() if isinstance(r.get("ts"), (int, float))]
check(3.6, _rc == 0 and recs5 and recs5[-1].get("outcome") == "clean"
      and all(isinstance(r.get("ts"), (int, float)) for r in recs5),
      "torn or junk receipt lines never break the writer — the fresh "
      "clean receipt lands and every well-formed row still parses")

# the coverage shape the watchdog consumes, end to end
_head_ts = int(_git(REPO1, "log", "-1", "--format=%ct").stdout.strip())
_clean = [r for r in recs5 if r.get("outcome") == "clean"]
check(3.7, _clean and max(int(r["ts"]) for r in _clean) > _head_ts,
      "the receipt's ts postdates the commit it reviewed — exactly the "
      "coverage inequality the watchdog's ledger merge applies")

# a PURE errorType stream (rate_limit bounce) -> errors receipt, never clean
_before = len(_recs())
_rc, _r = _run_hook(REPO1, fake_cr='#!/bin/sh\n'
                    "printf '%s\\n' '{\"errorType\": \"rate_limit\"}'\n")
recs6 = _recs()
check(3.8, _rc == 0 and len(recs6) == _before + 1
      and recs6[-1].get("outcome") == "errors",
      "a pure errorType stream mints an ERRORS receipt — a bounced "
      "review can never masquerade as a clean one (the scout's Sep 28 "
      "drill, ported)")

# a findings record with no errorTypes -> NO receipt (the CLI's batch
# is the record); findings print, the hook stays advisory
_before = len(_recs())
_rc, _r = _run_hook(REPO1, fake_cr='#!/bin/sh\n'
                    "printf '%s\\n' '{\"fileName\": \"app.py\", "
                    "\"severity\": \"minor\", \"title\": \"nit\"}'\n")
recs7 = _recs()
check(3.9, _rc == 0 and len(recs7) == _before,
      "a findings-carrying review mints NO receipt — the CLI's batch "
      "is the record; the ledger never double-books a review")

print("== 4. THE CROSS-REPO CONTRACT: THE WATCHDOG COUNTS IT ==")
diag_src = ""
p = SCOUT / "diag_coderabbit_backlog.py"
if p.exists():
    diag_src = p.read_text(encoding="utf-8")
check(4.1, 'oc == "clean"' in diag_src
      and "scratch/reviews_receipts.jsonl" in diag_src
      and 'monero_project' in diag_src,
      "the watchdog reads repo-scratch receipts and counts ONLY "
      "outcome=clean as coverage (a bounced receipt covers nothing)")

sys.path.insert(0, str(SCOUT))
import diag_coderabbit_backlog as dcb          # noqa: E402

REPO2 = SANDBOX / "repo2"      # receipts only, zero finding batches
_mk_repo(REPO2)
_run_hook(REPO2, fake_cr="#!/bin/sh\nexit 0\n")

dcb.REVIEW_ROOT = SANDBOX / "empty_reviews_tree"   # no batches anywhere
(SANDBOX / "empty_reviews_tree").mkdir(parents=True, exist_ok=True)
dcb.SCOUT = REPO1
dcb.MONERO = REPO2
buf = io.StringIO()
with redirect_stdout(buf):
    _rc_wd = dcb.main()
out2 = buf.getvalue()
check(4.2, _rc_wd == 0 and "every commit in the window is covered" in out2,
      "a repo whose only review record is a fresh clean receipt PASSES "
      "the backlog watchdog (the monero cure, proven end to end)")

REPO3 = SANDBOX / "repo3"      # the pre-cure world: nothing recorded
_mk_repo(REPO3)
dcb.SCOUT = REPO1
dcb.MONERO = REPO3
buf = io.StringIO()
with redirect_stdout(buf):
    _rc_wd2 = dcb.main()
out3 = buf.getvalue()
check(4.3, _rc_wd2 == 1 and "no batch's workingDirectory" in out3,
      "the same repo WITHOUT a receipt still fails the watchdog — "
      "the original finding reproduces hermetically")

shutil.rmtree(SANDBOX, ignore_errors=True)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
