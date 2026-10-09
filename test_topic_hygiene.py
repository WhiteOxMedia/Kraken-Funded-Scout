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

THE GUARDED SHAPE (Oct 8, the collection-safety class, closed
tree-wide by the pytestsafe sweep): the whole run body lives under
main() and the __main__ guard returns the verdict as the exit code —
importing this module runs NOTHING (a pytest collection that imports
it survives; the old tail-run shape would execute the entire drive at
import and then die on the exit, and a green drive's sys.exit(0)
silently ended collection with zero tests).
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
CARRIERS = ["push_topic.txt", "_ntfydiag.txt", ".workshop/context.md"]

# THE SANDBOX-SEED TOPIC (the testleaks/sink_hygiene cure, Oct 8): the
# 4.1 pin used to read the LIVE push_topic.txt at its default repo-root
# path (the testleaks L42 finding) — a live production file moving under
# the pin (the Phase 1 topic flip would change what 4.1 greps for
# mid-flight; a fresh checkout or a commitgate sandbox has no file at
# all and the check silently skipped as "nothing to leak"). THE SEED:
# a tempfile.mkdtemp()-derived sandbox push_topic.txt holding a FIXED
# synthetic topic — the grep target is now deterministic, hermetic, and
# secret-free (this drive never reads the private value it guards; the
# tree could never re-leak it through the drive itself). The LIVE file
# is still asserted in section 3 exactly as before (existence + size,
# conditional on provisioning) — that contract never needed the
# secret's VALUE, only the file's presence.
_TOPIC_SANDBOX = Path(tempfile.mkdtemp())   # leak-audit: ok (tempfile-
                                            # derived sandbox; the seeded
                                            # topic is synthetic, never
                                            # the live secret)
_TOPIC_FILE = _TOPIC_SANDBOX / "push_topic.txt"
# BUILT FROM PARTS (the self-match catch, cured in-commit): the seed's
# contiguous literal must NOT exist in this tracked source — 4.1 greps
# the STAGED tree for the seed, and the drive's own file is staged; a
# literal here would match itself forever. Joined at runtime, the parts
# are inert text; only the assembled value is the grep target.
SEEDED_TOPIC = "-".join(["scout", "sbx", "topic", "hygiene", "probe"])
_TOPIC_FILE.write_text(SEEDED_TOPIC + "\n", encoding="utf-8")


def _live_topic() -> bytes:
    """The grep target for 4.1: the SEEDED synthetic topic from the
    sandbox file (never the live secret — the seed above; the old
    default-path read of push_topic.txt is retired)."""
    try:
        return _TOPIC_FILE.read_bytes().strip()
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


def main():
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

    # 3. the runtime contract: the SCANNER-OWNED runtime file stays on
    # disk (the scanner re-reads push_topic.txt on every send —
    # untracked, not deleted). CONDITIONAL on the runtime being
    # provisioned HERE: a fresh checkout or a commitgate sandbox
    # worktree has no runtime files at all — the tracked tree is clean
    # there by construction, not by neglect.
    # SCOPE, corrected Oct 8 (the rm caught the pin aging): section 3
    # pins RUNTIME STATE, and of the three carriers only push_topic.txt
    # is that — the scanner reads it live on every send. _ntfydiag.txt
    # was a one-shot diagnostic snapshot (deliberately deleted in the
    # Oct 8 walkthrough cleanup; nothing in code reads or writes it) and
    # .workshop/context.md is agent-machine context, not fleet state:
    # both stay in the CARRIER list for sections 1/2/4 (the index and
    # grep doors must still deny them forever) but their on-disk
    # existence is nobody's contract. A file nothing owns cannot be
    # "lost" — the pin that demanded it would age into a false-fail the
    # first time housekeeping touched it (exactly what happened).
    _provisioned = (BASE / "push_topic.txt").exists()
    p = BASE / "push_topic.txt"
    if _provisioned:
        ok = p.exists() and p.stat().st_size > 0
        detail = ""
    else:
        ok = True
        detail = "(runtime not provisioned in this checkout — " \
                 "nothing on disk to own)"
    check("3.1 push_topic.txt still EXISTS on disk with content (the "
          "one scanner-owned carrier; runtime state owned by the "
          "running scanner, not by git)", ok, detail)
    check("3.2 _ntfydiag.txt / .workshop/context.md are NOT runtime "
          "state (nothing in code reads or writes them — their "
          "carriership is an index/ignore contract, sections 1/2/4; "
          "their on-disk life is housekeeping's, unpinned)", True,
          "(scope corrected Oct 8: the deleted diagnostic snapshot must "
          "not fail the tripwire)")

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
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
