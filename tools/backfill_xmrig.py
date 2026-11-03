"""backfill_xmrig.py - THE CARRY-SHAPE HEALER (Oct 30).

The wide bundle shape depends on the UNTRACKED toolchain tree:
installer/build_bundle.ps1 copies xmrig/xmrig-6.26.0 ONLY when its
xmrig.exe is present (line "if (Test-Path ...)"). When that local
payload goes missing - a move, a cleanup, a drives' sandboxes - every
rebuild DEGRADES to the slim shape with no red anywhere: the shape
contracts now describe both shapes honestly, so slim is a PASS, and
the carry shape quietly stops shipping the miner.

This module closes that hole: whenever the local payload is missing,
re-stage it from the ONE PINNED RELEASE - the URL and zip hash owned
by pool/operator_config (XMRIG_URL / XMRIG_SHA256, the same pin
installer/install.ps1 binds to the byte) plus this tool's own exe
hash, the one test_bundle.py 4.5 pins for the carried exe.

FAIL CLOSED, twice: the downloaded zip is deleted and nothing is
extracted unless its SHA256 matches the zip pin; nothing is staged
unless the extracted exe's SHA256 matches the exe pin. An existing
xmrig.exe is NEVER overwritten (a byte-verified exe stays untouched).

The caller is tools/rebuild_bundle_watch.py (the top of every watch
cycle); the module is also runnable directly:

  python tools/backfill_xmrig.py            # heal the real tree
  python tools/backfill_xmrig.py --repo <dir>   # sandbox override (drives)

Exit 0 = payload present (was or became); 1 = still missing/failed.
"""
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent          # tools/
REPO = HERE.parent if HERE.name == "tools" else HERE

# THE PIN IS IMPORTED, not restated (the one-reader rule): the module
# constants are the release this backfill stages, so re-pinning the
# release only ever means editing operator_config.
if str(REPO / "pool") not in sys.path:
    sys.path.insert(0, str(REPO / "pool"))
import operator_config as oc                    # noqa: E402

URL = oc.XMRIG_URL
ZIP_SHA256 = oc.XMRIG_SHA256
# this tool's own pin: the hash of the carried xmrig.exe, unchanged here
EXE_SHA256 = ("6fa80698d7268f6e88aa88c06fb27ee99e1bcee747c2e76911e62"
              "06a5b1aeeb3")


def payload_dir(repo=REPO):
    return Path(repo) / "xmrig" / "xmrig-6.26.0"


def payload_present(repo=REPO):
    """True when the local carry payload's exe exists."""
    return (payload_dir(repo) / "xmrig.exe").is_file()


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def stage_from_zip(zip_bytes, repo=REPO, log=print):
    """Extract, hash-verify, stage. Returns "" or "staged".

    Fail closed: a zip whose bytes do not hash to ZIP_SHA256 is
    refused BEFORE extraction; an extracted exe that does not hash to
    EXE_SHA256 is refused BEFORE staging. An existing exe is never
    overwritten. (Test seam: the drive feeds bytes directly - no
    network in any drive.)
    """
    got = _sha256_bytes(zip_bytes)
    if got != ZIP_SHA256:
        log("BACKFILL REFUSED: the download is not the pinned release "
            "(zip sha256=%s…, want %s…) - deleted, nothing extracted"
            % (got[:12], ZIP_SHA256[:12]))
        return ""

    exe = None
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = z.namelist()
        for n in names:
            if n.rsplit("/", 1)[-1] == "xmrig.exe" and not n.endswith("/"):
                exe = z.read(n)
                break
    if exe is None:
        log("BACKFILL REFUSED: no xmrig.exe member in the pinned zip")
        return ""
    got_exe = _sha256_bytes(exe)
    if got_exe != EXE_SHA256:
        log("BACKFILL REFUSED: the extracted exe is not the pinned "
            "build (exe sha256=%s…, want %s…)" % (got_exe[:12],
                                                  EXE_SHA256[:12]))
        return ""

    target = payload_dir(repo) / "xmrig.exe"
    if target.is_file():
        log("BACKFILL SKIP: %s already present - a byte-verified exe "
            "is never overwritten" % target)
        return ""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(exe)
    log("BACKFILL STAGED: %s (sha256 %s…) from the pinned release"
        % (target, _sha256_bytes(target.read_bytes())[:12]))
    return "staged"


def backfill(repo=REPO, log=print):
    """The watch-cycle entry. Returns (status, message); 0 exit = present."""
    if payload_present(repo):
        return 0, ""
    log("BACKFILL: the local xmrig payload is GONE - re-staging from the "
        "pinned release before the carry shape degrades another build")
    try:
        with urllib.request.urlopen(
                urllib.request.Request(
                    URL, headers={"User-Agent": "xmrig-backfill/1.0"}),
                timeout=600) as resp:
            data = resp.read()
    except Exception as exc:                    # noqa: BLE001 - logged, not raised
        log("BACKFILL FAILED: the pinned release did not answer (%s) - "
            "the carry shape stays degraded; slim builds remain honest"
            % exc)
        return 1, str(exc)

    staged = stage_from_zip(data, repo=repo, log=log)
    if staged == "staged":
        return 0, "staged"
    return 1, "stage refused"


def main():
    log = print
    repo = REPO
    argv = sys.argv[1:]
    if "--repo" in argv:
        repo = Path(argv[argv.index("--repo") + 1])
    rc, msg = backfill(repo=repo, log=log)
    if rc == 0 and msg == "":
        if payload_present(repo):
            print("present: %s" % (payload_dir(repo) / "xmrig.exe"))
        sys.exit(0)
    sys.exit(rc)


if __name__ == "__main__":
    main()
