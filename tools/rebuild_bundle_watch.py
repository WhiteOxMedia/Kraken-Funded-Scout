#!/usr/bin/env python3
"""rebuild_bundle_watch.py — THE BUNDLE REBUILD WATCHER (Oct 9).

The served zip is built from `git archive HEAD` on BOTH repos, so it
goes stale the moment EITHER head moves: a new kraken_scout commit (or
a new monero_project commit — the manifest pins both) means whatever
the pool serves right now is not HEAD. This watcher closes that gap:

  1. read the last-built head PAIR from the stamp (gitignored state),
  2. compare against both repos' current HEAD,
  3. on drift: run installer/build_bundle.ps1 (whose [6/6] is
     installer/finalize_bundle.py — the fail-close topic guard +
     zip/manifest/sidecar writer), then re-verify with the three
     bundle drives: test_bundle_hygiene (14) + test_bundle_serve (20)
     + test_bundle_parity (7),
  4. stamp the pair + append one JSONL receipt, then exit 1 if any
     drive failed, 0 otherwise. No drift => quiet no-op, exit 0.

HOUSE SHAPE (test_bundle_parity's idiom): check()-based subprocess
verifier, no pytest; every write to a tempdir it owns is accounted
(the drive-reborrow case is out of scope here — this script never
writes tempdirs; the drives' _OWN_WORLDS stay untouched).

Usage:
    python rebuild_bundle_watch.py            # one watch cycle
    python rebuild_bundle_watch.py --force    # rebuild WITHOUT drift
                                              # (manual refresh; still
                                              # re-verifies + receipts)
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent          # tools/ on a staged checkout
REPO = HERE.parent if HERE.name == "tools" else HERE   # monero_project/
SCOUT = REPO.parent / "kraken_scout"
STAMP = REPO / "scratch" / "_bundle_built_heads.json"
RECEIPTS = REPO / "scratch" / "reviews_receipts.jsonl"
WATCH_LOG = REPO / "scratch" / "_bundle_rebuild_watch.log"

DRIVES = ("test_bundle_hygiene.py", "test_bundle_serve.py",
          "test_bundle_parity.py")
BUILDER = REPO / "installer" / "build_bundle.ps1"

DRIVE_TIMEOUT = 600       # the 42-drive fleet gives each ~2min; the
                          # bundle trio is a fraction of that
BUILD_TIMEOUT = 600       # the builder is git-archive + copy + zip


def _log(msg):
    line = datetime.now().strftime("%Y-%m-%dT%H:%M:%S") + " " + msg
    try:
        WATCH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with WATCH_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line)


def _head(repo):
    """Short HEAD sha of a repo, or None when it cannot be read
    (fail-soft: an unreadable repo is a SKIP, never a rebuild)."""
    try:
        r = subprocess.run(["git", "-C", str(repo),
                            "rev-parse", "--short=7", "HEAD"],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _stamp():
    try:
        return json.loads(STAMP.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None         # absent, torn, or non-JSON -> rebuild


def _write_stamp(scout_head, monero_head):
    STAMP.parent.mkdir(parents=True, exist_ok=True)
    STAMP.write_text(json.dumps({
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scout_head": scout_head,
        "monero_head": monero_head,
    }, indent=2) + "\n", encoding="utf-8")


def _receipt(outcome, scout_head, monero_head, detail=""):
    """One entry in the monero reviews-receipt ledger — the SAME parse
    shape the post-commit hook writes ({ts, head, mode, outcome}, a
    torn line is skipped by every reader), head = MONERO head (the
    receipt is authored in this repo), the PAIR in the detail text."""
    try:
        RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": int(datetime.now().timestamp()),
               "head": monero_head or "unknown",
               "mode": "rebuild-watch", "outcome": outcome}
        if detail:
            row["detail"] = detail
        with RECEIPTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def main():
    force = "--force" in sys.argv[1:]

    scout_head = _head(SCOUT)
    monero_head = _head(REPO)
    if not scout_head or not monero_head:
        _log("SKIP: an unreadable repo head (scout=%s monero=%s) — "
             "nothing exempt from the pair check"
             % (scout_head, monero_head))
        return 1

    built = _stamp() or {}
    pair_new = (scout_head, monero_head)
    pair_old = (built.get("scout_head"), built.get("monero_head"))

    if pair_new == pair_old and not force:
        # NO receipt on the quiet path: the ledger is the review/build
        # coverage record — a skip row per watch cycle would drown it
        _log("NO-DRIFT: bundle covers scout %s + monero %s (stamp: %s)"
             % (scout_head, monero_head, built.get("ts", "?")))
        return 0

    why = ("--force requested" if force else
           ("first stamp" if not built else
            "scout %s->%s | monero %s->%s"
            % (pair_old[0] or "?", scout_head,
               pair_old[1] or "?", monero_head)))
    _log("DRIFT on %s: %s" % (datetime.now().strftime("%Y-%m-%d %H:%M"), why))

    # ── the rebuild (build_bundle.ps1 archives BOTH repos' HEAD; the
    #    [6/6] finalizer write-pins the served artifact to this build)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(BUILDER)],
            capture_output=True, encoding="utf-8",
            errors="replace",   # the builder prints '…' — a decode
            # error here must never kill the rebuild (the first --force
            # probe surfaced this: the watch run itself stayed green,
            # only the failure-diagnostics readback threw)
            timeout=BUILD_TIMEOUT)
        if r.returncode != 0:
            for ln in (r.stdout or "").splitlines()[-15:]:
                _log("  build| " + ln)
            for ln in (r.stderr or "").splitlines()[-10:]:
                _log("  build! " + ln)
            _log("BUILD FAILED rc=%s — guard hit or builder threw; the "
                 "STAMP IS NOT MOVED (the served artifact is the stale "
                 "one until a green rebuild)" % r.returncode)
            _receipt("build-failed", monero_head, scout_head,
                     "rc=%s" % r.returncode)
            return 1
    except (OSError, subprocess.SubprocessError) as exc:
        _log("BUILD FAILED (no run: %s) — stamp not moved" % exc)
        _receipt("build-failed", monero_head, scout_head, str(exc)[:120])
        return 1

    # ── the verification: the three drives, in the monero venv, the
    #    --check shape each one prints itself when it ends
    py = REPO / ".venv" / "Scripts" / "python.exe"
    if not py.is_file():
        py = Path(sys.executable)
    failed = []
    for d in DRIVES:
        _log("drive %s ..." % d)
        try:
            r = subprocess.run(
                [str(py), "-X", "utf8", str(REPO / d)],
                capture_output=True, encoding="utf-8", errors="replace",
                timeout=DRIVE_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:
            failed.append(d + " (no run: %s)" % exc)
            continue
        if r.returncode != 0:
            tail = (r.stdout or "").splitlines()[-12:]
            for ln in tail:
                _log("  FAIL| " + ln)
            failed.append(d)

    if failed:
        outcome = "drives-failed"
        _log("REBUILD VERDICT: %d drive(s) FAILED after a green build "
             "(scout %s + monero %s): %s"
             % (len(failed), scout_head, monero_head, ", ".join(failed)))
    else:
        # ONLY a green build + all three green drives moves the stamp:
        # a failed-verification build must NEVER claim coverage
        _write_stamp(scout_head, monero_head)
        outcome = "clean"
        _log("REBUILD VERDICT: clean — the served artifact NOW covers "
             "scout %s + monero %s (all %d drives green)"
             % (scout_head, monero_head, len(DRIVES)))
    _receipt(outcome, monero_head, scout_head,
             "scout=%s drives=%s" % (scout_head, ",".join(DRIVES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
