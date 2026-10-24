"""installer/finalize_bundle.py — THE TOPIC GUARD + THE PUBLIC ARTIFACT
(Oct 7, Phase 0 of the ntfy topic rotation, the monero half).

Called by build_bundle.ps1 after the payload is assembled. Two jobs,
both fail-closed:

1. THE TOPIC GUARD: the private ntfy topic must never ship. The pool
   serves dist/KrakenScoutBundle.zip UNAUTHENTICATED, and the scout
   payload is `git archive kraken_scout HEAD` — when the topic files
   were tracked at kraken HEAD, the public zip carried the topic in 3
   entries (push_topic.txt, _ntfydiag.txt, .workshop/context.md; proven
   pre-cure, Oct 7). The topic is read at build time from the SCOUT's
   LIVE push_topic.txt (untracked since kraken 3b95c02 — the guard
   follows every future rotation and never itself carries the secret).
   The staged payload is scanned for: the denylist FILENAMES, the
   .workshop/ directory, and the topic bytes inside every source file
   (scout + monero trees; the xmrig binary payload is filename-checked
   only). ANY hit exits 1 — the build refuses to ship a leak.

2. THE PUBLIC ARTIFACT: the folder alone is not served — the pool
   serves the zip + .sha256 + .zip.json manifest from disk. The
   finalizer zips the bundle (forward-slash entry names under the
   KrakenScoutBundle/ prefix, fixed stamp) and rewrites manifest and
   checksum sidecar in the SAME pass, so the served artifact can never
   drift from the built one (test_bundle_serve pins zip == manifest ==
   sidecar).

SCOPE NOTE (static surface): This finalizer produces ONLY the public
KrakenScoutBundle.zip. It is OUT OF SCOPE for the pool/static
scout-bundle surface (app.js, scout_ids.js, scout_paywall.js,
scout_paywall_wire_patch.js, scout_checkout.js, wf_dashboard.js,
and the other template-referenced static JS files). Those files ship
through make_bundle.py -> dist_site_review/xmrjackpot_review/static/,
not through this artifact. Any edit that would add pool/static JS to
this artifact is a regression and must be reverted.

Usage: python installer/finalize_bundle.py BUNDLE_DIR DIST_DIR SCOUT_ROOT MONERO_ROOT
"""
import hashlib
import json
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

DENY_NAMES = {"push_topic.txt", "_ntfydiag.txt"}
DENY_DIR_MARKER = "/.workshop/"
ZIP_STAMP = (2026, 10, 7, 0, 0, 0)   # fixed: identical trees -> identical bytes


def _fail(msg):
    print("TOPIC GUARD: " + msg)
    sys.exit(1)


def _git_head(repo):
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _git_version(repo):
    """The exact commit this artifact was built from, as a version string
    (`git describe --tags --always`, e.g. v1.0.0-preprod-1-gec82f91). It is
    published by /api/downloads/bundle-info and stated as softwareVersion in
    the Product markup, so the version and `monero_head` can never disagree."""
    try:
        r = subprocess.run(["git", "-C", str(repo), "describe", "--tags",
                            "--always"],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def main():
    bundle, dist, scout, monero = (Path(a) for a in sys.argv[1:5])
    payload = bundle / "payload"
    if not payload.is_dir():
        _fail("no payload under " + str(bundle))

    # ── 1. the topic, read from the SCOUT's live untracked file ──────
    topic_file = scout / "push_topic.txt"
    topic = b""
    if topic_file.exists():
        topic = topic_file.read_bytes().strip()
    if not topic:
        _fail("no live topic provisioned at " + str(topic_file)
              + " — the topic guard cannot scan source-tree content without it"
              + " (the served zip is PUBLIC; the build must fail, not proceed on denylist alone)")

    # ── 2. the scan (filename denylist everywhere, content in the
    #      source trees — the xmrig payload is a binary blob) ─────────
    hits = []
    scan_roots = [payload / "scout", payload / "monero_project"]
    for root in [payload] + scan_roots:
        if not root.is_dir():
            continue
        for f in root.rglob("*"):
            if not f.is_file():
                continue
            rel = "/" + f.relative_to(payload).as_posix()
            if f.name in DENY_NAMES or DENY_DIR_MARKER in rel:
                hits.append(rel + "  (denylist name/dir)")
                continue
            if root in scan_roots and topic:
                try:
                    if topic in f.read_bytes():
                        hits.append(rel + "  (topic bytes)")
                except OSError:
                    pass
    if hits:
        for h in hits:
            print("  GUARD HIT: " + h)
        _fail("the payload carries the private ntfy topic — fix the "
              "sources and rebuild (the served zip is PUBLIC)")
    print("  topic guard: clean "
          + ("(live topic scanned)" if topic else "(no topic provisioned)"))

    # ── 3. the public artifact: zip + manifest + sidecar, one pass ───
    dist.mkdir(parents=True, exist_ok=True)
    zip_path = dist / "KrakenScoutBundle.zip"
    files = sorted(p for p in bundle.rglob("*") if p.is_file())
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            info = zipfile.ZipInfo(
                "KrakenScoutBundle/" + f.relative_to(bundle).as_posix(),
                date_time=ZIP_STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, f.read_bytes())
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (dist / "KrakenScoutBundle.zip.sha256").write_text(sha + "\n",
                                                       encoding="ascii")

    xmrig_dir = payload / "xmrig-6.26.0"
    xmrig_exe = xmrig_dir / "xmrig.exe"
    manifest = {
        "artifact": "KrakenScoutBundle.zip",
        "version": _git_version(monero),
        "sha256": sha,
        "bytes": zip_path.stat().st_size,
        "size_human": "%.1f MB" % (zip_path.stat().st_size / 1048576),
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "monero_head": _git_head(monero),
        "scout_head": _git_head(scout),
        "xmrig_version": "6.26.0" if xmrig_dir.is_dir() else None,
        "xmrig_sha256": (hashlib.sha256(xmrig_exe.read_bytes()).hexdigest()
                         if xmrig_exe.exists() else None),
        "contents": ("Kraken Scout (git archive HEAD) + monero_project "
                     "pool view + xmrig 6.26.0 payload + one-click "
                     "installers; bundle root dir KrakenScoutBundle/"),
        "install": "unzip, open KrakenScoutBundle/, run 'Install Kraken Scout.bat'",
        "verify": "CertUtil -hashfile KrakenScoutBundle.zip SHA256",
        "urls": {
            "artifact": "/downloads/KrakenScoutBundle.zip",
            "sha256": "/downloads/KrakenScoutBundle.zip.sha256",
        },
    }
    (dist / "KrakenScoutBundle.zip.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("BUNDLE FINALIZED: " + str(zip_path)
          + "  (" + manifest["size_human"] + ", sha256 " + sha[:16] + "…)")


if __name__ == "__main__":
    main()
