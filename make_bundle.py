"""make_bundle.py — THE ONE-COMMAND DEPLOY-ARTIFACT BUILDER (Sep 26).

Regenerates the site review bundle (dist_site_review/
xmrjackpot_review) from the live sources and runs the verify gate.
(The serving model is LOCAL: the site runs on this PC's uvicorn and
reaches the world through the Cloudflare tunnel — there is no
Hostinger deploy. This dir is a review artifact only; the name used
to read dist_hostinger/ from the Sep 25 Hostinger Horizons review
round and misread as a deploy target.)

  1. copies pool/templates/index.html + admin_panel.html verbatim
  2. copies every pool/static file verbatim (static/ is RECREATED, so
     a file deleted at the source cannot linger as a stale copy)
  3. dates the README's **Date:** line (today) and syncs its per-file
     version mentions from the template's ?v= script tags, so the
     human-facing inventory cannot trail the shipped versions
  4. rebuilds xmrjackpot_review.zip deterministically — sorted
     entries, xmrjackpot_review/ prefix, FIXED stamp (2026, 9, 26)
     shared with test_bundle_parity.py as the byte-reproducibility
     anchor; change BOTH together or reproducibility breaks
  5. rewrites the sha256 sidecar
  6. runs the verify gate: test_bundle_parity.py (inventory, byte
     parity, version equality, zip reproducibility, checksum, zip
     round-trip) and exits with its code — a bundle that fails the
     gate exits nonzero and must not ship

Run:  python make_bundle.py
"""
import hashlib
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC_TPL = HERE / "pool" / "templates"
SRC_STATIC = HERE / "pool" / "static"
BUNDLE = HERE / "dist_site_review" / "xmrjackpot_review"
ZIP_PATH = HERE / "dist_site_review" / "xmrjackpot_review.zip"
SHA_PATH = HERE / "dist_site_review" / "xmrjackpot_review.zip.sha256"
GATE = HERE / "test_bundle_parity.py"
ZIP_STAMP = (2026, 9, 26, 0, 0, 0)   # the determinism anchor (see docstring)


def copy_sources():
    """Template + statics, verbatim; static/ is recreated wholesale."""
    if (BUNDLE / "static").is_dir():
        shutil.rmtree(BUNDLE / "static")
    (BUNDLE / "static").mkdir(parents=True)
    copies = [(SRC_TPL / "index.html", BUNDLE / "index.html"),
              (SRC_TPL / "admin_panel.html", BUNDLE / "admin_panel.html")]
    for f in sorted(SRC_STATIC.rglob("*")):
        if f.is_file():
            copies.append((f, BUNDLE / "static" / f.relative_to(SRC_STATIC)))
    for src, dst in copies:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
    return len(copies)


def template_versions():
    """{file.js: version} from the template's cache-busting tags."""
    html = (SRC_TPL / "index.html").read_text(encoding="utf-8")
    return {name: ver
            for name, ver in re.findall(
                r'src="/static/([A-Za-z0-9_\-]+\.js)\?v=(\d+)"', html)}


def date_readme():
    """Today's date on the **Date:** line + version mentions synced
    from the template (only rows whose file is versioned there)."""
    readme = BUNDLE / "README.txt"
    lines = readme.read_text(encoding="utf-8").splitlines()
    now = datetime.now()
    stamp = f"{now.strftime('%B')} {now.day}, {now.year}"
    versions = template_versions()
    touched = 0
    for i, line in enumerate(lines):
        if line.startswith("**Date:**"):
            lines[i] = (f"**Date:** {stamp} (regenerated from the live "
                        "template + current statics)")
            continue
        m = re.search(r"`static/([A-Za-z0-9_\-]+\.js)`", line)
        if m and m.group(1) in versions:
            new = re.sub(r"\(v\d+\)", f"(v{versions[m.group(1)]})", line)
            if new != line:
                lines[i] = new
                touched += 1
            continue
        if m and m.group(1) not in versions:
            # sync cannot verify a version for this file — leave the row
            # untouched; a downstream parity check still asserts presence.
            continue
    readme.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return stamp, touched


def build_zip():
    """The deterministic zip + sha256 sidecar (the recipe the parity
    gate rebuilds byte-for-byte)."""
    files = sorted(p for p in BUNDLE.rglob("*") if p.is_file())
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            zi = zipfile.ZipInfo(
                "xmrjackpot_review/" + f.relative_to(BUNDLE).as_posix(),
                date_time=ZIP_STAMP)
            zi.external_attr = 0o644 << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, f.read_bytes())
    digest = hashlib.sha256(ZIP_PATH.read_bytes()).hexdigest()
    SHA_PATH.write_text(
        f"{digest} *dist_site_review/xmrjackpot_review.zip\n", encoding="ascii")
    return digest


REQUIRED_STATIC_JS = [
    "scout_ids.js",
    "scout_paywall.js",
    "scout_paywall_wire_patch.js",
    "scout_checkout.js",
    "app.js",
]


def _assert_scout_static_bundle():
    """HARD GATE: every required scout module must be in the built static
    surface — a missing file is a FAIL, not a warning."""
    missing = [n for n in REQUIRED_STATIC_JS
               if not (BUNDLE / "static" / n).is_file()]
    if missing:
        print(f"BUNDLE HARD FAIL: required static JS missing: {missing}")
        return 1
    return 0


def run_gate():
    """THE VERIFY GATE: the parity drive is the builder's own proof."""
    return subprocess.run(
        [sys.executable, str(GATE)], cwd=str(HERE)).returncode


def main():
    n = copy_sources()
    stamp, touched = date_readme()
    digest = build_zip()
    print(f"bundle: {n} files copied, README dated {stamp} "
          f"({touched} version mention(s) synced)")
    print(f"zip: {ZIP_PATH.stat().st_size} bytes, "
          f"sha256 {digest[:16]}… (sidecar written)")
    print("gate: required statics + test_bundle_parity.py …")
    code = _assert_scout_static_bundle() or run_gate()
    print("GATE: PASS — the bundle is at parity and may ship" if code == 0
          else f"GATE: FAIL (exit {code}) — do not ship this bundle")
    return code


if __name__ == "__main__":
    sys.exit(main())
