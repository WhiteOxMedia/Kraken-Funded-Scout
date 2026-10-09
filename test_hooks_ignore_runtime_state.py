"""GUARD (RUNTIME_INVARIANTS.md, rule 3): hooks never touch runtime state.

Fails the suite if any ACTIVE git hook references a runtime-state path by
name (literal filename, runtime directory, or the watch/ledger/db names).
Hooks review code only — they must not read, write, move, copy, redirect
into, or even name the live files the running scanner owns.

The filename list here mirrors RUNTIME_INVARIANTS.md ("What counts as
runtime state"); class-based patterns (extensions) in hooks are fine —
literal paths are not.

DRIFT CENSUS (Sep 23): also fails if the machine-local .git/hooks copy of
pre-commit ever drifts from the tracked hooks/pre-commit. The tracked file
is the reviewable source (core.hooksPath executes it); the .git/hooks copy
is the fallback if the hooksPath config is ever lost. It lives OUTSIDE git
— an out-of-band edit there is invisible to every review — so this census
is the only thing pinning the two together byte-for-byte.
"""
import os
import re
import subprocess
import sys

# literal runtime-state names no hook may ever mention
RUNTIME_LITERALS = [
    "contest_watch.json", "contest_account.json", "contest_trades.jsonl",
    "take_payouts.json", "signal_snapshots.jsonl", "snapshot_outcomes.jsonl",
    "signals.db", "ohlcv_cache.db", "rotation_thresholds.json",
    "rotation_lockout.json", "climb_levels.json", "volatility_universe.json",
    "sure_thing_autoweights.json", "coin_floors.json", "discipline_state.json",
    "scanner_state.json", "scanner.lock", "voice_log.jsonl", "scanner.log",
    "console_state.json", "console_prefs.json", "popup_suppressions.jsonl",
    "payout_track.json", "hype_now.txt", "why_rejected.txt", "pending_check.txt",
    "speech_queue", "tide_bar_reckoning_window.jsonl",
]
RUNTIME_DIRS = ["logs/", "speech_queue/", "watch/", "state/", "scanner/"]

HOOK_NAMES = [
    "applypatch-msg", "commit-msg", "fsmonitor-watchman", "post-update",
    "pre-applypatch", "pre-commit", "pre-merge-commit", "pre-push",
    "pre-rebase", "pre-receive", "prepare-commit-msg", "push-to-checkout",
    "update",
]

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail if not ok else ''}".rstrip())


def active_hook_dirs():
    dirs = [os.path.join(".git", "hooks")]
    try:
        hp = subprocess.run(["git", "config", "--get", "core.hooksPath"],
                            capture_output=True, text=True).stdout.strip()
        if hp:
            dirs.insert(0, hp)
    except Exception:
        pass
    return [d for d in dirs if os.path.isdir(d)]


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


hits = []
MUTATORS = ("Move-Item", "Copy-Item", "Rename-Item", "Remove-Item",
            "shutil.move", "shutil.copy", "shutil.rmtree", "os.remove",
            "os.rename")
for d in active_hook_dirs():
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".sample"):
            continue
        path = os.path.join(d, fn)
        if not os.path.isfile(path):
            continue
        text = read_text(path)
        # mutation keywords anywhere outside comments are fatal — hooks review,
        # they never move/copy/delete (runtime files were swept by exactly
        # such moves on 2026-09-21)
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for mut in MUTATORS:
                if mut in line:
                    hits.append(f"{path}:{i} mutates files via {mut}")
        # a runtime literal is forbidden on any EXECUTABLE line, except
        # inside the churn-filter regex (awk program text) where naming the
        # runtime CLASS is the mechanism that keeps churn out of reviews.
        # A literal in a plain comment is documentation, allowed.
        in_awk = False
        for i, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if not in_awk and "awk '" in line and s.startswith("FILTERED"):
                in_awk = True
                continue
            if in_awk and s == "')":
                in_awk = False
                continue
            if in_awk or s.startswith("#"):
                continue
            low = line.lower()
            for lit in RUNTIME_LITERALS:
                if re.search(re.escape(lit), low):
                    hits.append(f"{path}:{i} names {lit} on an executable line")
            for rd in RUNTIME_DIRS:
                if re.search(re.escape(rd), low):
                    hits.append(f"{path}:{i} references dir {rd} on an executable line")

check("no active hook touches or names runtime state", not hits,
      "; ".join(hits[:6]))

# ── the drift census: the machine-local fallback must mirror the commit ──
# The tracked hooks/pre-commit is what core.hooksPath executes and what
# every review reviews. The .git/hooks copy is the fallback that keeps the
# guards running if core.hooksPath is ever lost — but it lives outside git,
# so an out-of-band edit there (the 2026-09-21 hook-dance failure mode) is
# invisible to git, to reviews, and to every other guard. Byte-for-byte,
# CRLF-normalized (Windows checkouts may hold CRLF while the blob is LF).
# THE GENERALIZED MIRROR CENSUS (Sep 23, the user's sweep: "extend the
# same census to the other machine-local hook copies") — two directions,
# over EVERY hook name, not just pre-commit:
#
#   1. tracked -> machine-local: every TRACKED hooks/<name> must be
#      mirrored byte-for-byte (CRLF-normalized) at .git/hooks/<name>.
#      The machine-local copy is the fallback if core.hooksPath is ever
#      lost; a stale or missing copy leaves the fallback running an
#      unreviewed hook (or no fallback at all).
#
#   2. machine-local -> tracked: every non-sample executable in
#      .git/hooks must have a tracked twin in hooks/. A real hook that
#      exists ONLY machine-locally is unreviewable and unversionable —
#      exactly how the post-commit Copilot reviewer (the Sep-7 rule)
#      silently DIED when core.hooksPath was armed at hooks/: git ran
#      the tracked dir, the live hook sat in .git/hooks, and no check
#      said a word. This census makes that class impossible to repeat.
#
# commit-msg, prepare-commit-msg, post-commit, and any future hook name
# are all covered by construction — nothing new to pin per-name.
def _norm(path):
    try:
        with open(path, "rb") as _f:
            return _f.read().replace(b"\r\n", b"\n"), None
    except OSError as _exc:
        return None, str(_exc)


TRACKED_HOOKS_DIR = "hooks"
LOCAL_HOOKS_DIR = os.path.join(".git", "hooks")
_ls = subprocess.run(["git", "ls-files", "hooks"], capture_output=True,
                     text=True)
TRACKED_HOOKS = sorted(
    os.path.basename(p) for p in _ls.stdout.split()
    if p.startswith("hooks/") and not p.endswith(".sample"))
try:
    _local_names = sorted(
        fn for fn in os.listdir(LOCAL_HOOKS_DIR)
        if not fn.endswith(".sample"))
except OSError as _exc:
    _local_names = []
    print(f"FAIL  the machine-local hooks dir is unreadable: {_exc}")
    results.append(("the machine-local hooks dir is unreadable", False,
                    str(_exc)))

# direction 1: tracked -> machine-local
for _name in TRACKED_HOOKS:
    _t, _terr = _norm(os.path.join(TRACKED_HOOKS_DIR, _name))
    _l, _lerr = _norm(os.path.join(LOCAL_HOOKS_DIR, _name))
    if _t is None:
        check(f"machine-local {_name} mirrors the tracked hook", False,
              f"cannot read {TRACKED_HOOKS_DIR}/{_name}: {_terr}")
    elif _l is None:
        check(f"machine-local {_name} mirrors the tracked hook", False,
              f"the fallback copy is missing ({_lerr}) — re-arm it: "
              f"cp {TRACKED_HOOKS_DIR}/{_name} {LOCAL_HOOKS_DIR}/{_name}")
    else:
        check(f"machine-local {_name} mirrors the tracked hook",
              _t == _l,
              f"DRIFT — the machine-local {_name} no longer matches the "
              "tracked hook; an out-of-band edit or a stale refresh leaves "
              "the fallback running a hook nobody reviewed. Re-arm: cp "
              f"{TRACKED_HOOKS_DIR}/{_name} {LOCAL_HOOKS_DIR}/{_name}")

# direction 2: machine-local -> tracked (the dead-hook detector)
_orphans = [fn for fn in _local_names if fn not in TRACKED_HOOKS]
check("every machine-local hook has a tracked twin (no dead hooks)",
      not _orphans,
      f"REAL HOOKS LIVING ONLY IN {LOCAL_HOOKS_DIR}: {_orphans} — git does "
      f"not run them (core.hooksPath executes {TRACKED_HOOKS_DIR}/), so "
      "they are dead while looking armed, and invisible to every review. "
      "Version them: mv into hooks/ and commit, then re-sync the mirror.")

n_pass = sum(1 for _, ok, _ in results if ok)
print(f"\nRESULT: {n_pass} passed, {len(results) - n_pass} failed")


def main():
    sys.exit(0 if n_pass == len(results) else 1)


if __name__ == "__main__":
    sys.exit(main())
