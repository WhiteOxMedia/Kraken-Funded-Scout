"""test_bundle_parity.py — THE DEPLOY-ARTIFACT DRIFT GUARD (Sep 26).

dist_site_review/xmrjackpot_review is the site review bundle (a
LOCAL-serve artifact — the site runs on this PC behind the Cloudflare
tunnel; there is no Hostinger deploy — the name dist_hostinger was
the Sep 25 review round's and misread as a deploy target): a
verbatim copy of pool/templates (index + admin_panel) plus every
pool/static file, a README, and a deterministic zip + sha256 sidecar.
History has shown the bundle silently trailing the source (push_client
v12 while the site shipped v13, then v14; app.js v21 vs v23; wf v4 vs
v5) — nothing failed when it drifted. This drive pins the WHOLE chain
so a stale bundle fails the fleet instead of shipping:

  0. the inputs are LF-canonical (worktree AND staged bytes): the bundle
     is built from working-tree bytes, so a smudged checkout would make
     the zip machine-dependent while every other check stayed green
  1. inventory parity: the bundle holds exactly index.html,
     admin_panel.html, README.txt, and every pool/static file
  2. byte parity: every source-mapped file is byte-identical
  3. the bundle HTML's script version strings equal the template's
  4. the zip is byte-reproducible from the bundle dir (deterministic
     recipe: sorted entries, xmrjackpot_review/ prefix, fixed stamp)
  5. the sha256 sidecar matches the zip bytes
  6. the zip extracts to the exact bundle inventory, byte-identical
  7. the README's human inventory mentions every versioned file with
     the SAME version the template ships — make_bundle.py syncs these
     mentions; this check verifies the sync's output afterwards, so a
     hand-edit or a sync bug cannot trail the human-facing inventory
     silently

Skips honestly when the bundle directory is absent (the artifact is
untracked by design; fleet-only checkouts stay green).
Prints "RESULT: N passed, M failed".
"""
import hashlib
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from make_bundle import REQUIRED_STATIC_JS   # one list, both consumers

HERE = Path(__file__).resolve().parent
BUNDLE = HERE / "dist_site_review" / "xmrjackpot_review"
ZIP_PATH = HERE / "dist_site_review" / "xmrjackpot_review.zip"
SHA_PATH = HERE / "dist_site_review" / "xmrjackpot_review.zip.sha256"
results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


if not BUNDLE.is_dir():
    print("SKIP: dist_site_review/xmrjackpot_review not present — "
          "the fleet stays green on checkouts without the deploy artifact")
    print("RESULT: 0 passed, 0 failed")
    sys.exit(0)


def relset(root):
    return {str(f.relative_to(root)).replace("\\", "/")
            for f in root.rglob("*") if f.is_file()}

TPL = HERE / "pool" / "templates"
STAT = HERE / "pool" / "static"

# 0. THE CHECKOUT IS THE ARTIFACT'S INPUT (Oct 20). The bundle is copied
# from WORKING-TREE bytes, so a checkout that smudges line endings makes the
# zip machine-dependent while every check below stays green - they all compare
# the bundle to the same smudged tree. Measured: the same commit rebuilt to
# sha256 fb5e194f… here and 98c14e49… in a fresh clone, 224 bytes apart, with
# parity green in both. .gitattributes pins these inputs to eol=lf; this check
# is what makes the pin load-bearing. It fails on a CR in the file the builder
# reads OR in the staged bytes it would ship, and it deliberately does not
# compare full content (an ordinary uncommitted edit is not a drift).
#
# The README is an input too: make_bundle.py copies installer/
# site_review_readme.txt into the bundle and dates it.
INPUTS = ["pool/templates/index.html", "pool/templates/admin_panel.html",
          "installer/site_review_readme.txt"] + [
    str(f.relative_to(HERE)).replace("\\", "/")
    for f in sorted(STAT.rglob("*"))
    if f.is_file() and f.suffix in (".js", ".css")]
crlf_work, crlf_blob, unreadable = [], [], []
for rel in INPUTS:
    path = HERE / rel
    if not path.is_file():
        unreadable.append(rel + ": missing")
        continue
    if b"\r" in path.read_bytes():
        crlf_work.append(rel)
    staged = subprocess.run(
        ["git", "-C", str(HERE), "cat-file", "blob", ":" + rel],
        capture_output=True)
    if staged.returncode == 0:
        if b"\r" in staged.stdout:
            crlf_blob.append(rel)
    else:
        unreadable.append(rel + ": not in the index")
check(0, not crlf_work and not crlf_blob and not unreadable,
      f"the inputs are not LF-canonical - rebuild the artifact from a "
      f"checkout whose bytes are the committed bytes: worktree CR={crlf_work} "
      f"staged CR={crlf_blob} unreadable={unreadable} "
      f"(fix: git rm --cached <path> && git checkout-index -f -- <path>)")

# 1. inventory parity
expected = {"index.html", "admin_panel.html", "README.txt"} | {
    "static/" + p for p in relset(STAT)}
actual = relset(BUNDLE)
missing, extra = sorted(expected - actual), sorted(actual - expected)
check(1, not missing and not extra,
      f"missing={missing} extra={extra}")

# 2. byte parity for every source-mapped file
pairs = [("index.html", TPL / "index.html"),
         ("admin_panel.html", TPL / "admin_panel.html")]
for f in sorted(STAT.rglob("*")):
    if f.is_file():
        pairs.append(("static/" + str(f.relative_to(STAT)).replace("\\", "/"), f))
diffs = [rel for rel, src in pairs
         if rel in actual and (BUNDLE / rel).read_bytes() != src.read_bytes()]
check(2, not diffs, f"files differing from source: {diffs}")

# 3. version strings in the bundle HTML equal the template's
bh = (BUNDLE / "index.html").read_text(encoding="utf-8")
th = (TPL / "index.html").read_text(encoding="utf-8")
STATIC_JS_TAG_RE = r'src="/static/([A-Za-z0-9_\-]+\.js)\?v=(\d+)"'
bv = sorted(re.findall(STATIC_JS_TAG_RE, bh))
tv = sorted(re.findall(STATIC_JS_TAG_RE, th))
check(3, bv == tv, f"bundle={bv} template={tv}")

# 4+5. the zip is byte-reproducible and the sidecar matches
if ZIP_PATH.exists():
    files = sorted(p for p in BUNDLE.rglob("*") if p.is_file())
    repro = Path(tempfile.mkdtemp(prefix="bundle_repro_")) / "repro.zip"
    with zipfile.ZipFile(repro, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            zi = zipfile.ZipInfo(
                "xmrjackpot_review/" + f.relative_to(BUNDLE).as_posix(),
                date_time=(2026, 9, 26, 0, 0, 0))
            zi.external_attr = 0o644 << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, f.read_bytes())
    check(4, repro.read_bytes() == ZIP_PATH.read_bytes(),
          "the shipped zip is not the deterministic rebuild of the "
          "current bundle dir — regenerate it")
    digest = hashlib.sha256(ZIP_PATH.read_bytes()).hexdigest()
    recorded = SHA_PATH.read_text().split()[0] if SHA_PATH.exists() else ""
    check(5, digest == recorded,
          f"sidecar says {recorded[:16]}… zip hashes {digest[:16]}…")
else:
    check(4, False, "xmrjackpot_review.zip missing — regenerate the bundle")
    check(5, False, "no zip to hash")

# 6. zip round-trip: extracts to the exact bundle inventory, byte-identical
if ZIP_PATH.exists():
    td = Path(tempfile.mkdtemp(prefix="bundle_zip_"))
    with zipfile.ZipFile(ZIP_PATH) as z:
        bad = z.testzip()
        z.extractall(td)
    ext_root = td / "xmrjackpot_review"
    ext = relset(ext_root) if ext_root.is_dir() else set()
    inv_ok = bad is None and ext == actual
    bytes_bad = [str(f.relative_to(ext_root)).replace("\\", "/")
                 for f in ext_root.rglob("*")
                 if f.is_file() and f.read_bytes()
                 != (BUNDLE / f.relative_to(ext_root)).read_bytes()] \
        if ext_root.is_dir() else ["<no extraction>"]
    check(6, inv_ok and not bytes_bad,
          f"zip integrity={bad!r} inventory_ok={ext == actual} "
          f"bytes differing={bytes_bad[:4]}")
else:
    check(6, False, "no zip to verify")

# 7. the README inventory cannot trail the shipped versions
readme_lines = (BUNDLE / "README.txt").read_text(encoding="utf-8").splitlines()  # clock-audit: ok — README.txt is the SHIPPED artifact the inventory pins (not live state)
readme_bad = []
for name, ver in sorted(dict(tv).items()):
    row = next((ln for ln in readme_lines
                if "`static/" + name + "`" in ln), None)
    if row is None:
        readme_bad.append(name + ": no mention at all")
        continue
    m = re.search(r"\(v(\d+)\)", row)
    if not m:
        readme_bad.append(name + ": mention carries no (vN)")
    elif m.group(1) != ver:
        readme_bad.append(f"{name}: README says v{m.group(1)}, "
                          f"template ships v{ver}")
check(7, not readme_bad,
      f"README inventory drift: {readme_bad}")

# 8. CONTRACTUAL SCOUT/SURFACE PARITY (the regression gate).
# This is the explicit, enumerated assertion the request asked for:
# every required scout module must be present in the built static dir,
# the template must reference every required file, and the required set
# must match the explicitly enumerated contract (so a stale-delete or
# a path rename cannot silently drop a required file from the bundle).
static_dir = BUNDLE / "static"
present = {p.name for p in static_dir.glob("*.js") if p.is_file()}
missing_required = [n for n in REQUIRED_STATIC_JS if n not in present]
check(8.1, not missing_required,
      f"required scout/static JS missing from built bundle: "
      f"{missing_required}")

bundle_tag = sorted(re.findall(STATIC_JS_TAG_RE, bh))
required_tagged = sorted([(r, ver) for r, ver in tv if r in REQUIRED_STATIC_JS])
check(8.2, required_tagged and all((r, v) in bundle_tag for r, v in required_tagged),
      f"required scout/static files not all present in the built bundle "
      f"with matching version tags — required_tagged={required_tagged}, "
      f"missing_from_bundle={[(r,v) for r,v in required_tagged if (r,v) not in bundle_tag]}")

# 8.3 — the required contract and the template's full static JS surface
# must agree on names (no required file omitted from the template, and
# the template must not reference a required file that is absent from
# the static source tree).
template_static_names = {name for name, _ver in tv}
source_static_names = {p.name for p in STAT.rglob("*.js") if p.is_file()}
check(8.3, set(REQUIRED_STATIC_JS) <= template_static_names,
      f"required files not all referenced by the template: "
      f"{sorted(set(REQUIRED_STATIC_JS) - template_static_names)}")
check(8.4, set(REQUIRED_STATIC_JS) <= source_static_names,
      f"required files missing from pool/static source: "
      f"{sorted(set(REQUIRED_STATIC_JS) - source_static_names)}")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
