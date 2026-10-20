#!/usr/bin/env python3
"""githooks/check_sibling_shadow_gate.py — cross-repo pre-commit gate.

Refuses to commit in monero_project when the sibling repo
(Kraken-Challenge-Scout-Bot-Referral-Jackpot, at ../Kraken-Challenge-Scout-Bot-Referral-Jackpot
relative to monero_project) has a ntfytopic-carrying file STAGED IN ITS INDEX
but not yet committed.

The sibling already gitignores tests/_shadow/, but gitignore does not protect a
path that is already in the index — the next `git commit` in that repo writes the
secret into history. This gate is the cross-repo reminder: don't land my own
commit while the sibling still has the secret staged.

This gate NEVER reads the live ntfy topic value. It judges carriers by NAME/PATH
in the sibling's index (denylist basenames + the /.workshop/ dir marker), and by
the presence of the sibling's own tests/_shadow/push_topic.txt staged path. That
is itself the carrier by name; reading the value is not required and is not done.

Exit 1 = block the commit (with the staged carrier path(s) listed, never their
content). Exit 0 = pass.
"""
from pathlib import Path
import subprocess
import sys

SIBLING_REL = Path("..") / "Kraken-Challenge-Scout-Bot-Referral-Jackpot"

DENY_BASENAMES = {"push_topic.txt", "_ntfydiag.txt"}
DENY_DIR_MARKER = "/.workshop/"


def _fail(msg):
    print("BLOCKED (sibling shadow gate): " + msg, file=sys.stderr)
    sys.exit(1)


def sibling_repo():
    """Absolute path to the sibling repo dir, or None if it isn't a git repo here."""
    monero = Path(
        sys.argv[sys.argv.index("--monero-root")]
        if "--monero-root" in sys.argv
        else subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10
        ).stdout.strip()
    )
    sibling = (monero / SIBLING_REL).resolve()
    try:
        subprocess.run(
            ["git", "-C", str(sibling), "rev-parse", "--git-dir"],
            capture_output=True, timeout=10, check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return sibling


def staged_paths(sibling):
    """STAGED paths in the sibling's index (what a `git commit` there would write).

    git ls-files --stage emits:  <mode> <sha> <stage>\t<path>
    We split on the FIRST tab and take the path side only.
    """
    r = subprocess.run(
        ["git", "-C", str(sibling), "ls-files", "--stage"],
        capture_output=True, text=True, timeout=30
    )
    if r.returncode != 0:
        return []
    out = []
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        out.append(parts[1].lstrip("/"))
    return out


def blob_sha_for_staged_path(sibling, path):
    """sha1 of the staged INDEX blob for `path`, or None (unused by the name gate)."""
    r = subprocess.run(
        ["git", "-C", str(sibling), "ls-files", "--stage", path],
        capture_output=True, text=True, timeout=30
    )
    if r.returncode != 0 or not r.stdout.strip():
        return None
    parts = r.stdout.strip().split("\t")
    if len(parts) != 2:
        return None
    return parts[0].split()[1]


def carry_by_name(path):
    """Is `path` a denylist carrier by NAME/PATH (no content read)?"""
    if path.startswith("/"):
        path = path[1:]
    parts = path.split("/")
    basename = parts[-1]
    if basename in DENY_BASENAMES:
        return True
    if DENY_DIR_MARKER.lstrip("/") in path:
        return True
    return False


def main():
    sibling = sibling_repo()
    if sibling is None:
        # No sibling repo here -> nothing staged here, so nothing to block.
        return

    staged = staged_paths(sibling)
    carriers = [p for p in staged if carry_by_name(p)]

    if not carriers:
        return

    carrier_list = " / ".join(sorted(set(carriers)))
    _fail(
        "the sibling repo has a ntfytopic carrier STAGED (not yet committed):\n"
        "    " + carrier_list + "\n"
        "the sibling gitignores tests/_shadow/, but gitignore does not protect a\n"
        "path already in the index — the next `git commit` there writes the secret\n"
        "into history. Unstage it before committing here:\n"
        "    git -C " + str(sibling) + " rm --cached -f " + " ".join(
            sorted(set(carriers))
        ) +
        "\n"
        "or resolve it in the sibling first. (This gate reads only staged paths,\n"
        "never the live topic value.)"
    )


if __name__ == "__main__":
    main()
