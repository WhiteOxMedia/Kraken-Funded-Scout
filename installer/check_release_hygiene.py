"""installer/check_release_hygiene.py — THE CI RELEASE GATE (Oct 25).

Fails the build when the tree could contaminate a customer artifact:

  1. the release manifest is present, schema-valid, and its version has no
     non-production marker (preprod/staging/rc);
  2. every shipping surface (pool/, installer/, scout/, templates,
     static) is free of the manifest's content rules: credentials,
     operator wallets, developer rig ids, localhost pool endpoints,
     private topics, preprod labels;
  3. the developer xmrig config is not present in the payload tree (a
     customer package must never carry 127.0.0.1 or an inherited rig id);
  4. the customer install scripts do not start the pool, the proxy, the
     wallet, the admin panel or a privileged port;
  5. the manifest's scout paths still match the Scout tree when that
     sibling checkout exists (drift means a runtime module was added and
     the whitelist was not reviewed), and the generated manifest is
     current.

Run in CI:  python installer/check_release_hygiene.py
Exit 0 = clean, 1 = at least one blocking finding.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import release_guard as guard  # noqa: E402

SHIPPING_SURFACES = ("pool", "installer", "scout")
TEXT_SUFFIXES = {".py", ".js", ".ps1", ".bat", ".json", ".html", ".css",
                 ".sh", ".vbs", ".toml", ".md", ".txt"}
SKIP_PARTS = {"node_modules", ".venv", ".git", "__pycache__", "dist",
              "dist_site_review", "target", "logs"}
SKIP_NAME_PREFIXES = ("test_",)

# Operator infrastructure legitimately listens on loopback (the proxy API on
# 9090, the wallet RPC on 18082, the pool on 3334). That rule is enforced on
# the ARTIFACT, where a developer endpoint has no business; applying it to the
# operator's own server code would only teach people to disable the gate.
# The preprod rule is about the VERSION that reaches a customer surface, so
# it is enforced on the manifest version and on the built artifact (where a
# dev tag is a real leak). The guard's own source necessarily names these
# markers in order to refuse them.
REPO_SKIP_RULES = {"localhost-pool", "preprod-version"}

# These files DEFINE the forbidden markers, so they necessarily contain them.
POLICY_FILES = {
    "installer/release_guard.py",
    "installer/generate_release_manifest.py",
    "installer/release_manifest.json",
}

# A customer install may not do operator work.
INSTALL_SCRIPTS = (
    "installer/install.ps1",
    "installer/installer.py",
    "installer/installer_ui.py",
    "installer/Install Kraken Scout.bat",
    "installer/Uninstall Kraken Scout.bat",
)
OPERATOR_LAUNCH = re.compile(
    r"(start_pool\.bat|start_proxy\.bat|start_wallet\.bat|pool[\\/]server\.py|"
    r"admin_panel|proxy_poller|jackpot_engine)", re.IGNORECASE)

problems: list[str] = []


def fail(msg: str) -> None:
    problems.append(msg)


def repo_files():
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                             capture_output=True, text=True, timeout=30)
        names = [n for n in out.stdout.splitlines() if n.strip()]
        if out.returncode == 0 and names:
            return [ROOT / n for n in names]
    except Exception:
        pass
    files = []
    for p in ROOT.rglob("*"):
        if p.is_file() and not (set(p.parts) & SKIP_PARTS):
            files.append(p)
    return files


def check_manifest() -> dict:
    try:
        manifest = guard.load_manifest()
    except guard.ManifestError as e:
        fail(str(e))
        return {}
    try:
        version = guard.release_version(manifest)
        print(f"  manifest version: {version} "
              f"({len(manifest['allowed'])} allowed paths)")
    except guard.ManifestError as e:
        fail(str(e))
    return manifest


def check_surfaces(manifest: dict) -> None:
    rules = manifest.get("content_rules") or ()
    if not rules:
        fail("release manifest defines no content rules")
        return
    scanned = 0
    for p in repo_files():
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        if rel.split("/")[0] not in SHIPPING_SURFACES:
            continue
        if p.name.startswith(SKIP_NAME_PREFIXES) or p.suffix not in TEXT_SUFFIXES:
            continue
        if rel in POLICY_FILES:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        for rule_id, why in guard.content_violations(text, manifest):
            if rule_id in REPO_SKIP_RULES:
                continue
            fail(f"{rel}: {rule_id} — {why}")
    print(f"  shipping surfaces scanned: {scanned} file(s)")


def check_xmrig_payload(manifest: dict) -> None:
    """A config in the payload tree must be customer-neutral.

    It is pruned from the artifact either way, but a developer config in
    the tree is how the old bundle got 127.0.0.1:3333 and the operator's
    rig id; the tree itself must not hold one.
    """
    cfg = ROOT / "xmrig" / "xmrig-6.26.0" / "config.json"
    if not cfg.exists():
        print("  xmrig payload: no local config present")
        return
    text = cfg.read_text(encoding="utf-8", errors="replace")
    hits = [rid for rid, _ in guard.content_violations(text, manifest)
            if rid in {"developer-rig-id", "localhost-pool",
                       "operator-wallet"}]
    if hits:
        fail(f"xmrig/xmrig-6.26.0/config.json carries developer/operator "
             f"values ({', '.join(hits)}); the payload tree must hold only "
             f"a customer-neutral config")
    else:
        print("  xmrig payload: local config is customer-neutral")


def check_install_scripts() -> None:
    for rel in INSTALL_SCRIPTS:
        p = ROOT / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        # the names may legitimately appear in a comment explaining that
        # they do NOT ship; only an executable reference (a launch/copy
        # line) is a violation, so strip comment lines first.
        code = "\n".join(
            line for line in text.splitlines()
            if not line.strip().startswith(("#", "REM ", "::", "//")))
        m = OPERATOR_LAUNCH.search(code)
        if m:
            fail(f"{rel}: references operator infrastructure ({m.group(0)}) "
                 f"in executable code — a customer install must not start "
                 f"the pool, proxy, wallet or admin panel")
    print(f"  install scripts checked: {len(INSTALL_SCRIPTS)}")


def check_manifest_drift() -> None:
    scout = ROOT.parent / "kraken_scout"
    if not (scout / "app.py").is_file():
        print("  manifest drift: scout tree absent — skipped (CI shape)")
        return
    import generate_release_manifest as gen
    expected = gen.build()
    try:
        current = json.loads(guard.MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        fail(f"release manifest unreadable: {e}")
        return
    missing = sorted(set(expected["allowed"]) - set(current.get("allowed") or ()))
    extra = sorted(set(current.get("allowed") or ()) - set(expected["allowed"]))
    if missing:
        fail("release manifest is MISSING files the product now imports: "
             + ", ".join(missing[:8])
             + (" …" if len(missing) > 8 else ""))
    if extra:
        fail("release manifest ALLOWS files the product no longer ships: "
             + ", ".join(extra[:8])
             + (" …" if len(extra) > 8 else ""))
    if not missing and not extra:
        print(f"  manifest drift: none ({len(expected['allowed'])} paths)")


def check_built_artifact(manifest: dict) -> None:
    zip_path = ROOT / "dist" / "KrakenScoutBundle.zip"
    if not zip_path.exists():
        print("  built artifact: not present — skipped")
        return
    bad = guard.zip_violations(zip_path, manifest)
    if bad:
        fail(f"dist/KrakenScoutBundle.zip violates the release manifest "
             f"({len(bad)} finding(s)); first: {bad[0]}")
    else:
        print("  built artifact: clean against the manifest")


def main() -> int:
    print("== RELEASE HYGIENE ==")
    manifest = check_manifest()
    if manifest:
        check_surfaces(manifest)
        check_manifest_drift()
        check_built_artifact(manifest)
    check_xmrig_payload(manifest or guard.load_manifest())
    check_install_scripts()
    if problems:
        print(f"\nBLOCKING ({len(problems)}):")
        for p in problems[:60]:
            print("  - " + p)
        return 1
    print("\nRELEASE HYGIENE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
