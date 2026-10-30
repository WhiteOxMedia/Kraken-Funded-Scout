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

2. THE RELEASE MANIFEST (Oct 25): after assembly the staged tree is
   pruned to installer/release_manifest.json — default-deny, so tests,
   audits, phase docs, operator tooling, the pool server and the
   developer xmrig config are removed rather than trusted to a
   denylist — and the surviving tree is scanned for forbidden content
   (credentials, operator wallets, localhost pool endpoints, preprod
   versions). A content hit FAILS the build: a pruned program is a
   broken program, so that has to be fixed at the source.

3. THE PUBLIC ARTIFACT: the folder alone is not served — the pool
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
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_guard as guard          # noqa: E402  (the one manifest brain)

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


def _git_version(repo, manifest_version):
    """The customer-facing version. It comes from the release manifest —
    never from `git describe`, whose dev-tag suffixes (preprod, -rc,
    -<n>-g<hash>) used to reach the download page and the Product markup.
    The build's exact commit is published separately as `monero_head`.

    XMRJ_RELEASE_VERSION lets an operator stamp a versioned release
    without editing the manifest, but it is validated by the guard's own
    marker rule, so it cannot smuggle a non-production label either."""
    env = (os.environ.get("XMRJ_RELEASE_VERSION") or "").strip()
    version = env or manifest_version
    try:
        guard.release_version({"version": version})
    except guard.ManifestError as e:
        _fail(str(e))
    return version


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

    # ── 2b. THE RELEASE MANIFEST: default-deny prune, then validate ──
    manifest = guard.load_manifest()
    removed = guard.prune(bundle, manifest)
    print(f"  release manifest: pruned {len(removed)} file(s) not on the "
          f"customer whitelist")
    problems = guard.scan_bundle(bundle, manifest)
    if problems:
        for p in problems[:40]:
            print("  RELEASE HIT: " + p)
        _fail(f"the bundle violates installer/release_manifest.json "
              f"({len(problems)} violation(s)) — fix the sources and rebuild")
    print("  release guard: clean (" + str(len(manifest['allowed']))
          + " allowed paths, " + str(len(manifest.get('content_rules') or ()))
          + " content rules)")

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
    # The manifest must describe the shape it published: some builds
    # carry the local xmrig tree, fresh-clone builds do not (install.ps1
    # fetches the pinned release at install time) - a static contents
    # string would claim a payload the slim shape never zipped.
    xmrig_contents = ("installer + xmrig 6.26.0 payload" if xmrig_exe.exists()
                      else "installer; the xmrig payload is fetched from "
                      "the pinned release at install time")
    release = {
        "artifact": "KrakenScoutBundle.zip",
        "version": _git_version(monero, guard.release_version(manifest)),
        "sha256": sha,
        "bytes": zip_path.stat().st_size,
        "size_human": "%.1f MB" % (zip_path.stat().st_size / 1048576),
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "monero_head": _git_head(monero),
        "scout_head": _git_head(scout),
        "release_manifest_sha256": guard.manifest_sha256(manifest),
        "release_policy": "default-deny whitelist (installer/release_manifest.json)",
        "xmrig_version": "6.26.0" if xmrig_dir.is_dir() else None,
        "xmrig_sha256": (hashlib.sha256(xmrig_exe.read_bytes()).hexdigest()
                         if xmrig_exe.exists() else None),
        "contents": ("Kraken Scout (runtime modules + launchers) + the "
                     f"{xmrig_contents}; no pool, no "
                     "operator tooling, no tests; root dir KrakenScoutBundle/"),
        "install": "unzip, open KrakenScoutBundle/, run 'Install Kraken Scout.bat'",
        "verify": "CertUtil -hashfile KrakenScoutBundle.zip SHA256",
        "urls": {
            "artifact": "/downloads/KrakenScoutBundle.zip",
            "sha256": "/downloads/KrakenScoutBundle.zip.sha256",
        },
    }
    (dist / "KrakenScoutBundle.zip.json").write_text(
        json.dumps(release, indent=2) + "\n", encoding="utf-8")
    print("BUNDLE FINALIZED: " + str(zip_path)
          + "  (" + release["size_human"] + ", sha256 " + sha[:16] + "…)")


if __name__ == "__main__":
    main()
