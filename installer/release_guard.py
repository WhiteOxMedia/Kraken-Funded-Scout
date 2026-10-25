"""installer/release_guard.py — THE RELEASE MANIFEST (Oct 25).

The public download is a CUSTOMER artifact. Everything in it must be
there because a customer needs it, so the rule is default-deny: a file
ships only when `installer/release_manifest.json` names it (or a glob
that matches it), and no file ships when a `forbidden` rule matches it,
even if an allow rule also matched.

Three callers, one brain:

  * installer/finalize_bundle.py  prunes the staged bundle to the
    manifest and FAILS the build on any violation, so a contaminated
    tree never becomes a zip;
  * pool/server.py                refuses to serve (503) a zip on disk
    that violates the manifest, so a stale or hand-copied artifact
    cannot be published either;
  * installer/check_release_hygiene.py runs the same rules over the
    repository in CI, so the next leak fails the build before anyone
    builds a bundle.

The manifest is data, not code: release engineering changes it in one
reviewable place. `installer/generate_release_manifest.py` regenerates
the file list from the two source trees.

Usage:
    from release_guard import load_manifest, prune, scan_bundle
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MANIFEST_PATH = HERE / "release_manifest.json"
ZIP_PREFIX = "KrakenScoutBundle/"

# Version strings that must never reach a customer surface.
FORBIDDEN_VERSION_MARKERS = ("preprod", "pre-prod", "staging", "dev-", "-rc")


class ManifestError(RuntimeError):
    """The manifest itself is missing or unusable."""


def load_manifest(path: Path | str | None = None) -> dict:
    p = Path(path) if path else MANIFEST_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError as e:
        raise ManifestError(f"release manifest unreadable at {p}: {e}") from e
    except ValueError as e:
        raise ManifestError(f"release manifest is not JSON at {p}: {e}") from e
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ManifestError(f"release manifest at {p} is not schema 1")
    if not isinstance(data.get("allowed"), list) or not data["allowed"]:
        raise ManifestError(f"release manifest at {p} has no allow rules")
    return data


def _glob_re(pattern: str) -> re.Pattern:
    """`**` crosses directories, `*` and `?` do not."""
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def matches(rel: str, patterns) -> bool:
    rel = rel.replace("\\", "/")
    for pat in patterns or ():
        pat = pat.replace("\\", "/")
        if _glob_re(pat).match(rel):
            return True
    return False


def _is_text(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:4096]
    except OSError:
        return False
    if b"\x00" in chunk:
        return False
    return True


def content_violations(text: str, manifest: dict) -> list[tuple[str, str]]:
    """(rule id, why) for every content rule the text trips."""
    hits: list[tuple[str, str]] = []
    for rule in manifest.get("content_rules") or ():
        try:
            if re.search(rule["pattern"], text):
                hits.append((rule.get("id", "content"), rule.get("why", "")))
        except (KeyError, re.error):
            continue
    return hits


def name_violations(rel: str, manifest: dict) -> list[str]:
    """Reasons this bundle-relative path may not ship (empty = allowed)."""
    reasons = []
    if matches(rel, manifest.get("forbidden")):
        reasons.append("matches a forbidden rule")
    if not matches(rel, manifest.get("allowed")):
        reasons.append("not named by any allow rule")
    return reasons


def scan_bundle(bundle: Path | str, manifest: dict) -> list[str]:
    """Every violation in a staged bundle directory, as readable lines."""
    root = Path(bundle)
    problems: list[str] = []
    if not root.is_dir():
        return [f"bundle directory missing: {root}"]
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = f.relative_to(root).as_posix()
        for reason in name_violations(rel, manifest):
            problems.append(f"{rel}  ({reason})")
        if _is_text(f):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for rule_id, why in content_violations(text, manifest):
                problems.append(f"{rel}  (content: {rule_id} — {why})")
    return problems


def prune(bundle: Path | str, manifest: dict) -> list[str]:
    """Delete every file the manifest does not allow; return the removed paths.

    Content violations are NOT pruned: the build must be re-run from clean
    sources, because deleting a line from a program can leave it broken.
    """
    root = Path(bundle)
    removed = []
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = f.relative_to(root).as_posix()
        if not matches(rel, manifest.get("allowed")) or \
                matches(rel, manifest.get("forbidden")):
            f.unlink()
            removed.append(rel)
    # drop directories the prune emptied, deepest first
    for d in sorted((p for p in root.rglob("*") if p.is_dir()),
                    key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    return removed


def zip_violations(zip_path: Path | str, manifest: dict) -> list[str]:
    """Manifest violations in a built zip, without extracting it."""
    import zipfile

    problems: list[str] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            for n in names:
                rel = n[len(ZIP_PREFIX):] if n.startswith(ZIP_PREFIX) else n
                for reason in name_violations(rel, manifest):
                    problems.append(f"{rel}  ({reason})")
            text_rules = manifest.get("content_rules") or ()
            if text_rules:
                for n in names:
                    rel = n[len(ZIP_PREFIX):] if n.startswith(ZIP_PREFIX) else n
                    if Path(rel).suffix.lower() in {
                            ".exe", ".dll", ".wav", ".ico", ".png", ".zip",
                            ".db", ".pyc", ".so", ".dylib"}:
                        continue
                    try:
                        raw = zf.read(n)
                    except (KeyError, OSError, RuntimeError):
                        continue
                    if b"\x00" in raw[:4096]:
                        continue
                    text = raw.decode("utf-8", errors="replace")
                    for rule_id, why in content_violations(text, manifest):
                        problems.append(f"{rel}  (content: {rule_id} — {why})")
    except OSError as e:
        problems.append(f"artifact unreadable: {e}")
    except Exception as e:                       # zipfile.BadZipFile et al.
        problems.append(f"artifact is not a readable zip: {e}")
    return problems


def release_version(manifest: dict) -> str:
    """The manifest's customer-facing version, preprod markers refused."""
    v = str(manifest.get("version") or "").strip()
    if not v:
        raise ManifestError("release manifest has no version")
    low = v.lower()
    for marker in FORBIDDEN_VERSION_MARKERS:
        if marker in low:
            raise ManifestError(
                f"release version {v!r} carries the non-production marker "
                f"{marker!r} — a production release cannot ship it")
    return v


def manifest_sha256(manifest: dict | None = None) -> str:
    data = manifest if manifest is not None else load_manifest()
    blob = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


if __name__ == "__main__":                       # a quick human check
    import sys
    m = load_manifest()
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "KrakenScoutBundle"
    bad = scan_bundle(target, m)
    print(f"manifest: {len(m['allowed'])} allow rules, version {release_version(m)}")
    print(f"target  : {target}")
    if bad:
        print(f"VIOLATIONS ({len(bad)}):")
        for line in bad[:80]:
            print("  " + line)
        raise SystemExit(1)
    print("CLEAN")
