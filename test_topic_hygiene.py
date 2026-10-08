"""THE TOPIC-HYGIENE TRIPWIRE (Oct 7, Phase 0 of the ntfy topic
rotation): the private ntfy push topic must never ride a tracked tree.

THE LEAK (audit, Oct 7): push_topic.txt, _ntfydiag.txt, and
.workshop/context.md were TRACKED at kraken HEAD — and the monero
bundle builder ships `git archive kraken_scout HEAD`, so the public
zip at pool /downloads/KrakenScoutBundle.zip (unauthenticated) carried
the topic in 3 entries. Anyone with the URL could subscribe and read
every BUY / GET OUT pushed to the operator's phone.

THE CURE (structural, not a filter): the three files leave the index
(git rm --cached — the live files stay on disk, the scanner re-reads
push_topic.txt on EVERY send, no restart needed) and .gitignore denies
them forever. git archive HEAD then cannot carry them — not now, not
after any future re-add — because the leak door is the commit itself.

Pins, red-first:
  1. the three carriers are UNTRACKED (git ls-files empty)
  2. .gitignore denies each of them (git check-ignore)
  3. the live files still exist on disk with content — the runtime
     contract is untouched (untracking must never delete state)
  4. the STAGED tree carries zero topic bytes (git grep --cached) —
     exactly what archive HEAD ships once committed
  5. the scanner's per-send topic read is pinned by name
"""
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
CARRIERS = ["push_topic.txt", "_ntfydiag.txt", ".workshop/context.md"]


def _live_topic() -> bytes:
    """The private topic, read from the UNTRACKED runtime file — this
    drive must never carry the secret it guards (a hardcoded token
    would re-leak it into the very tree being cleaned, and would go
    stale the day Phase 1 rotates the topic). Empty when no topic is
    provisioned."""
    try:
        return (BASE / "push_topic.txt").read_bytes().strip()
    except OSError:
        return b""

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not ok else ""))


def git(*args):
    r = subprocess.run(["git", *args], cwd=str(BASE), timeout=60,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or ""), (r.stderr or "")


# 1. the carriers are untracked (the archive door is the index)
rc, out, _ = git("ls-files", "--", *CARRIERS)
check("1.1 push_topic.txt / _ntfydiag.txt / .workshop/context.md are "
      "UNTRACKED (git ls-files empty)",
      rc == 0 and out.strip() == "", out.strip()[:200])

rc, out, _ = git("ls-files", "--", ".workshop")
check("1.2 the whole .workshop/ dir is untracked (agent-machine "
      "context never ships)",
      rc == 0 and out.strip() == "", out.strip()[:200])

# 2. .gitignore denies them forever (a future git add . is a no-op)
for i, rel in enumerate(CARRIERS, start=1):
    rc, _, _ = git("check-ignore", "-q", rel)
    check(f"2.{i} .gitignore denies {rel}", rc == 0,
          "check-ignore rc=" + str(rc))

# 3. the runtime contract: the live files stay on disk (the scanner
# re-reads push_topic.txt on every send — untracked, not deleted).
# CONDITIONAL on the runtime being provisioned HERE: a fresh checkout
# or a commitgate sandbox worktree has no runtime files at all — the
# tracked tree is clean there by construction, not by neglect.
_provisioned = (BASE / "push_topic.txt").exists()
for i, rel in enumerate(CARRIERS, start=1):
    p = BASE / rel
    if _provisioned:
        ok = p.exists() and p.stat().st_size > 0
        detail = ""
    else:
        ok = True
        detail = "(runtime not provisioned in this checkout — " \
                 "nothing on disk to own)"
    check(f"3.{i} {rel} still EXISTS on disk with content (runtime "
          "state owned by the running scanner, not by git)", ok, detail)

# 4. the staged tree (what archive HEAD ships once committed) carries
# zero topic bytes — the topic read from the live untracked file, so
# this check follows every future rotation
_topic = _live_topic()
if _topic:
    # -F fixed-string + -e explicit pattern: the topic is DATA, never
    # a regex (a metacharacter topic could match unrelated text or
    # abort the grep), and -e keeps a leading hyphen from becoming an
    # option.
    rc, out, _ = git("grep", "--cached", "-l", "-F", "-e",
                     _topic.decode("utf-8", "replace"))
    _ok, _detail = (rc == 1 and out.strip() == ""), out.strip()[:200]
else:
    _ok, _detail = True, "(no live topic provisioned — nothing to leak)"
check("4.1 the STAGED tree carries zero topic bytes "
      "(git grep --cached: empty — the bundle builder archives HEAD, "
      "so the public zip can never carry the topic again)",
      _ok, _detail)

# 5. the scanner's per-send topic read is pinned by name (the file
# must keep its exact name and location beside background_scanner.py)
src = (BASE / "background_scanner.py").read_text(encoding="utf-8",
                                                 errors="replace")
check("5.1 background_scanner still resolves push_topic.txt beside "
      "the module, re-read per send (the rotation flip needs no "
      "restart)",
      'os.path.join(os.path.dirname(os.path.abspath(__file__)), "push_topic.txt")'
      in src)

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
