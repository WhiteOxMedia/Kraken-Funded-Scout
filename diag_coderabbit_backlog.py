"""diag_coderabbit_backlog.py - THE REVIEW-COVERAGE WATCHDOG (Sep 25).

The silent failure mode: commits land all day while the review service
produces nothing, and the gap stays invisible until someone happens to
poll %LOCALAPPDATA%\\coderabbit\\reviews by hand (Sep 25: ten commits
queued across both repos, zero sessions, discovered only because the
triage pass went looking). This diag makes the gap LOUD.

WHAT IT GRADES (per workspace repo - kraken_scout AND the monero
sibling, the same both-roots scope diag_hygiene uses):

  1. review-root sanity   the coderabbit reviews tree exists and each
                          repo is discoverable by its workingDirectory
                          (every recorded batch's git.json names the
                          repo it reviewed - a repo with no dir at all
                          has never been reviewed, which is itself the
                          finding, named in step 3)
  2. latest batch         the newest batch per repo: id, the head it
                          reviewed, and its age; PLUS the triage
                          surface (Sep 26): the newest batch's MAJOR
                          findings print as file:line + title - the
                          metadata an operator needs to triage without
                          the manual JSON hunt. A finding whose file
                          a LATER commit's message names is TRIAGED
                          and stops surfacing (Sep 27) - the list
                          reflects reality after fixes land. The
                          reviewer's WORDS
                          (comment bodies) remain its own data: only
                          fileName/startLine/title/severity are read,
                          and a body never reaches the output
  3. the open set         commits newer than the newest batch that
                          reviewed them (approximated honestly: a
                          commit is covered when a batch's recorded
                          timestamp postdates it on that repo) - for a
                          repo with no batches, every commit is open
  4. the N-hour gate      the oldest open commit's age against
                          BACKLOG_HOURS (default 24, override with
                          CODERABBIT_BACKLOG_HOURS for drills). Under
                          the window: PASS with the count. Over it:
                          FAIL naming the count, the oldest age, and
                          the short hashes - the alert the poll-by-
                          hand ritual kept missing.

Deterministic, read-only, idempotent: git log for commit metadata,
JSON stat for batch metadata; nothing is written anywhere.

Run:  .venv\\Scripts\\python.exe -X utf8 diag_coderabbit_backlog.py
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
import time
from pathlib import Path

SCOUT = Path(__file__).resolve().parent
MONERO = SCOUT.parent / "monero_project"
REVIEW_ROOT = (Path(os.environ.get("LOCALAPPDATA", ""))
               / "coderabbit" / "reviews")
BACKLOG_HOURS = float(os.environ.get("CODERABBIT_BACKLOG_HOURS", "24"))
SKIP_JSON = {"git.json", "internalState.json"}

results = []


def check(step, name, cond, detail=""):
    results.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  step {step} - {name}"
          + (f"  [{detail}]" if detail and not cond else
             (f"  ({detail})" if detail else "")))


def _git(repo, *args):
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _commits(repo):
    """(short_hash, commit_time) newest first - metadata only."""
    out = _git(repo, "log", "--format=%h %ct", "-40")
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            rows.append((parts[0], int(parts[1])))
    return rows


def _batches_for(repo):
    """Every recorded review batch whose git.json workingDirectory is
    this repo: [(timestamp, head, batch_path)]. Never opens finding
    files - the mapping lives in git.json alone."""
    found = []
    if not REVIEW_ROOT.exists():
        return found
    for git_json in REVIEW_ROOT.glob("*/*/reviews/*/git.json"):
        try:
            meta = json.loads(git_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        wd = os.path.normpath(str(meta.get("workingDirectory", ""))).lower()
        if wd == os.path.normpath(str(repo)).lower():
            found.append((int(meta.get("timestamp", 0)),
                          str(meta.get("head", ""))[:7], git_json.parent))
    return sorted(found, reverse=True)


def _triaged_files(repo, batch_ts, batch_head):
    """Filenames a LATER commit's message names (the triage memory).

    A major counts as TRIAGED once a commit NEWER than the batch that
    reviewed it mentions its file by name - subject OR BODY, because
    the triage commits that matter name their fixes in the body (the
    live case's subject names none of the four it fixed), so the
    list reflects reality after fixes land instead of re-serving
    triaged work forever. Two gates, because `head..HEAD` is not
    "later" by itself: a commit IN that range can still carry a
    pre-batch committer date (amend/rebase), so it must also
    postdate the batch's own timestamp. The log is NUL-framed so a
    body line that merely LOOKS like "<epoch> subject" can never
    parse as a commit of its own. Any git hiccup returns an empty
    set and the majors surface unfiltered (fail-soft = fail-visible).
    """
    named = set()
    log = _git(repo, "log", f"{batch_head}..HEAD",
               "--format=%ct%x00%B%x00")
    chunks = log.split(chr(0))
    for i in range(0, len(chunks) - 1, 2):
        ct, body = chunks[i].strip(), chunks[i + 1]
        if not ct.isdigit() or int(ct) <= batch_ts:
            continue
        named |= {Path(ln.split(":")[0].strip()).name for ln in
                  re.findall(r"[\w./\\+-]+", body) if "." in ln}
    return named


def _surface_majors(step, name, batches, repo=None):
    """THE TRIAGE SURFACE (Sep 26): the newest batch is the review the
    operator may not have seen yet; its MAJOR findings are the triage
    backlog. With `repo` handed in (Sep 27) it is also a MEMORY: a
    major whose fileName a later commit's message names counts as
    TRIAGED (via _triaged_files) and stops surfacing, so the list
    reflects reality after fixes land. Prints file:line + title
    only (capped at 10, with a count) - never the comment body.
    Malformed finding files are
    skipped, not crashed on; majors never change the exit code (they
    are advisory triage work; the gate is about review COVERAGE)."""
    if not batches:
        return
    _ts, _head, batch_dir = batches[0]
    triaged = _triaged_files(repo, _ts, _head) if repo else set()
    # BASENAME MATCHING (Sep 29 review minor): commit messages name
    # files inconsistently — a bare `canary_enable.py` versus the
    # finding's path-shaped `ops/canary_enable.py` — so the memory
    # matches on the basename either way. Granularity is PER-FILE by
    # design: naming a file clears ALL of that file's untriaged majors
    # (a fix commit for a file is read as closing its backlog; a
    # mention-only false positive just hides advisory text, never
    # changes the gate's exit code).
    triaged_base = {Path(t).name for t in triaged}
    majors = []
    total = 0
    for fj in sorted(batch_dir.glob("*.json")):
        if fj.name in SKIP_JSON:
            continue
        try:
            meta = json.loads(fj.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        if str(meta.get("severity", "")).lower() == "major":
            if Path(str(meta.get("fileName", ""))).name in triaged_base:
                continue
            total += 1
            if len(majors) < 10:
                ln = meta.get("startLine")
                majors.append((str(meta.get("fileName", "?")),
                               ln if isinstance(ln, int) else None,
                               str(meta.get("title", "(untitled)"))))
    if total == 0:
        check(step, f"{name}: the newest batch records no untriaged "
                    f"majors", True)
        return
    print(f"     majors awaiting triage in the newest batch ({total}):")
    for fn, ln, title in majors:
        print(f"       - {fn + ':' + str(ln) if ln else fn}  {title}")
    if total > len(majors):
        print(f"       ... and {total - len(majors)} more")
    check(step, f"{name}: majors surfaced for triage "
                f"({total} major(s), bodies never printed)", True)


def _repo_report(step, repo):
    # the display name is DERIVED from the path, never hard-coded -
    # the drive repoints the repo and the label must follow
    name = repo.name
    print(f"  -- {name}: {repo}")
    commits = _commits(repo)
    if not commits:
        check(step, f"{name}: git history readable", False,
              "git log returned nothing")
        return
    batches = _batches_for(repo)
    if not batches:
        check(step, f"{name}: at least one review batch recorded", False,
              f"no batch's workingDirectory names this repo "
              f"({len(commits)} commits in the window)")
    else:
        ts, head, _p = batches[0]
        check(step, f"{name}: latest batch {Path(_p).name} "
                    f"(head {head}, {int((time.time() - ts) / 3600)}h old)",
              True)
    covered_before = max((ts for ts, _h, _p in batches), default=0)
    open_set = [(h, t) for h, t in commits if t > covered_before]
    oldest_age_h = ((time.time() - min((t for _h, t in open_set),
                                       default=time.time())) / 3600)
    detail = (f"{len(open_set)} open commit(s), oldest {oldest_age_h:.1f}h: "
              + ", ".join(h for h, _t in sorted(open_set,
                                               key=lambda x: x[1])[:5]))
    # the triage surface runs BEFORE the gate verdicts: a batch can be
    # fully covering (the gate green) and still carry majors the
    # operator has not triaged - coverage and triage are different
    # questions, and both belong in one run's output
    _surface_majors(step, name, batches, repo=repo)
    if not open_set:
        check(step, f"{name}: every commit in the window is covered", True)
        return
    # a repo whose commits are all still inside the latency window is
    # on time, not silent - the gate is the AGE, not the existence
    check(step, f"{name}: no commit waits more than {BACKLOG_HOURS:g}h "
                f"for review", oldest_age_h <= BACKLOG_HOURS, detail)


def main():
    # one run, one verdict: clear the module-level ledger so a repeated
    # main() in one process grades THIS run only (the drive caught the
    # accumulation)
    results.clear()
    print("diag_coderabbit_backlog - the review-coverage watchdog")
    print(f"  threshold: {BACKLOG_HOURS:g}h "
          f"(CODERABBIT_BACKLOG_HOURS overrides; reviews root: "
          f"{REVIEW_ROOT})")
    check(1, "the coderabbit reviews tree exists", REVIEW_ROOT.exists(),
          str(REVIEW_ROOT))
    _repo_report(2, SCOUT)
    if MONERO.exists():
        _repo_report(2, MONERO)
    else:
        check(2, "monero_project: repo found beside kraken_scout", False,
              str(MONERO))
    failed = results.count(False)
    if failed:
        print(f"  ACTION: the reviewer service has not covered the "
              f"commits named above within {BACKLOG_HOURS:g}h - check "
              f"the CodeRabbit service, then triage the batch when it "
              f"lands (verify every claim against the real files).")
    print(f"RESULT: {len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
