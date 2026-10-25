"""installer/generate_release_manifest.py — REGENERATE THE RELEASE WHITELIST.

Reads the two source trees and writes installer/release_manifest.json:
the explicit list of files the public KrakenScoutBundle may contain.

Scout's module set is the import closure of scout/app.py (recursive,
static imports), plus its declared runtime assets and launchers. That is
deliberately code-derived: a shipped file is one the product imports,
not one that happens to sit in the working tree. A new runtime import
must be regenerated into the manifest in review — CI fails the build
when the manifest and the trees disagree (check_release_hygiene.py).

The monero side ships ONLY what a customer install needs: the installer
modules (which talk to xmrjackpot.com over HTTPS). It never ships the
pool server, the jackpot engine, the proxy poller or any operator tool.

    python installer/generate_release_manifest.py            # rewrite
    python installer/generate_release_manifest.py --check    # CI drift
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCOUT = ROOT.parent / "kraken_scout"
MANIFEST = HERE / "release_manifest.json"

SSL_SAFE_MARKER = {
    "schema": 1,
    "product": "Kraken Scout",
    "version": "1.0.0",
    "artifact": "KrakenScoutBundle.zip",
    "policy": (
        "Default-deny: a file ships only when an allow rule names it. "
        "Forbidden rules win over allow rules. Content rules fail the "
        "build outright — they are not pruned."),
}

# Scout runtime assets: audio cues, window icons, Streamlit theme.
SCOUT_ASSET_DIRS = ("assets", "icons")
SCOUT_ASSET_FILES = (".streamlit/config.toml",)

# Scout launchers that are part of the product (no pool, no admin).
# launch_stack/stop_stack are what the two bats actually call; without
# them the product's only entry point would be a dead shortcut.
SCOUT_LAUNCHERS = (
    "Launch Kraken.bat",
    "Launch Scout.bat",
    "Kraken - STOP.bat",
    "start_detached.ps1",
    "launch_stack.ps1",
    "stop_stack.ps1",
    "launch_kraken_hidden.vbs",
)

# Runtime modules started by NAME (the launcher spawns these, so the
# import closure cannot see them).
SCOUT_RUNTIME_EXTRA = ("hindsight_service.py",)

# Scout's own packaging contract (uv).
SCOUT_PACKAGING = ("pyproject.toml", "uv.lock", ".python-version")

# The monero half: WHOLE FILES ONLY, each one justified.
MONERO_PAYLOAD = (
    "payload/monero_project/installer/installer.py",
    "payload/monero_project/installer/installer_ui.py",
)

XMrig_PAYLOAD = (
    "payload/xmrig-6.26.0/xmrig.exe",
    "payload/xmrig-6.26.0/SHA256SUMS",
)

BUNDLE_ROOT = (
    "install.ps1",
    "README.txt",
    "Install Kraken Scout.bat",
    "Uninstall Kraken Scout.bat",
)

FORBIDDEN = (
    "**/.git/**",
    "**/.github/**",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/node_modules/**",
    "**/.venv/**",
    "**/dist/**",
    "**/dist_site_review/**",
    "**/plans/**",
    "**/scratch/**",
    "**/reports/**",
    "**/artifacts/**",
    "**/artifacts_recon/**",
    "**/logs/**",
    "**/battery_history/**",
    "**/test/**",
    "**/tests/**",
    "**/*.md",
    "**/*.log",
    "**/*.jsonl",
    "**/*.db",
    "**/*.db-wal",
    "**/*.db-shm",
    "**/*.lock",
    "**/AUDIT_*",
    "**/E2E_*",
    "**/PHASE*",
    "**/MEMO_*",
    "**/*_REPORT*",
    "**/KEY_MANAGEMENT_PLAN*",
    "**/COORDINATION_*",
    "**/DIRECTIVE_*",
    "**/release_notes.md",
    "**/test_*.py",
    "**/verify_hostinger_corrections.py",
    "**/proxy_poller.py",
    "**/jackpot_engine.py",
    "**/pool/server.py",
    "**/pool/static/**",
    "**/pool/templates/**",
    "**/config.json",
    "**/start.cmd",
    "**/start_pool.bat",
    "**/start_proxy.bat",
    "**/start_wallet.bat",
    "**/Launch Kraken.bat.tmp",
)

CONTENT_RULES = [
    {
        "id": "database-credential",
        "pattern": r"postgres(?:ql)?://[^\s:@/]+:[^\s@/]+@",
        "why": "a connection string with an embedded password",
    },
    {
        "id": "bearer-token",
        "pattern": r"(?i)\b(?:bearer|authorization)\s*[:=]\s*[\"']?"
                   r"(?:[A-Za-z0-9._~+/-]{20,})",
        "why": "a hard-coded bearer credential",
    },
    {
        "id": "api-key-literal",
        "pattern": r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|"
                   r"operator[_-]?token|password)\b\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']",
        "why": "a secret-looking literal in shipped code",
    },
    {
        "id": "operator-wallet",
        "pattern": r"\b[48][1-9A-HJ-NP-Za-km-z]{94,105}\b",
        "why": "a Monero address baked into a customer artifact",
    },
    {
        "id": "developer-rig-id",
        "pattern": r"Birdsnbubbles",
        "why": "the operator's xmrig rig id",
    },
    {
        "id": "localhost-pool",
        "pattern": r"127\.0\.0\.1:(?:3333|3334|9090|18082)",
        "why": "a developer-local pool/RPC endpoint",
    },
    {
        "id": "preprod-version",
        "pattern": r"(?i)\bpreprod\b",
        "why": "a non-production version label",
    },
    {
        "id": "private-ntfy-topic",
        "pattern": r"ntfy\.sh/[A-Za-z0-9_-]{12,}",
        "why": "a private notification topic URL",
    },
]


def scout_import_closure(root: Path) -> set[str]:
    """Every local module reachable from app.py by static import."""
    modules = {p.stem: p for p in root.glob("*.py")}
    packages = {}
    for init in root.glob("*/__init__.py"):
        packages[init.parent.name] = {p.stem for p in init.parent.glob("*.py")}

    def deps(name: str) -> list[str]:
        path = modules.get(name)
        if not path:
            return []
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            return []
        found: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found += [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                if node.module:
                    top = node.module.split(".")[0]
                    found.append(top)
                else:
                    found += [a.name.split(".")[0] for a in node.names]
        return found

    seen: set[str] = set()
    stack = ["app"]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for dep in deps(name):
            if dep in modules and dep not in seen:
                stack.append(dep)
    return {f"{name}.py" for name in seen if name in modules}


def allowed_paths() -> list[str]:
    if not (SCOUT / "app.py").is_file():
        raise SystemExit(f"scout source tree not found at {SCOUT}")
    allowed = list(BUNDLE_ROOT) + list(MONERO_PAYLOAD) + list(XMrig_PAYLOAD)

    for rel in sorted(scout_import_closure(SCOUT)):
        allowed.append("payload/scout/" + rel)
    for name in SCOUT_RUNTIME_EXTRA:
        if (SCOUT / name).is_file():
            allowed.append("payload/scout/" + name)
    for name in SCOUT_LAUNCHERS:
        if (SCOUT / name).is_file():
            allowed.append("payload/scout/" + name)
    for name in SCOUT_PACKAGING:
        if (SCOUT / name).is_file():
            allowed.append("payload/scout/" + name)
    for name in SCOUT_ASSET_FILES:
        if (SCOUT / name).is_file():
            allowed.append("payload/scout/" + name)
    for dirname in SCOUT_ASSET_DIRS:
        base = SCOUT / dirname
        if not base.is_dir():
            continue
        for f in sorted(p for p in base.rglob("*") if p.is_file()):
            allowed.append("payload/scout/" + f.relative_to(SCOUT).as_posix())
    return sorted(set(allowed))


def build() -> dict:
    manifest = dict(SSL_SAFE_MARKER)
    manifest["allowed"] = allowed_paths()
    manifest["forbidden"] = list(FORBIDDEN)
    manifest["content_rules"] = CONTENT_RULES
    return manifest


def main(argv: list[str]) -> int:
    manifest = build()
    text = json.dumps(manifest, indent=2) + "\n"
    if "--check" in argv:
        try:
            current = MANIFEST.read_text(encoding="utf-8")
        except OSError:
            current = ""
        if current != text:
            print("RELEASE MANIFEST DRIFT: the manifest does not match the "
                  "source trees — run installer/generate_release_manifest.py "
                  "and review the diff (a shipped file needs a reason).")
            return 1
        print(f"release manifest current ({len(manifest['allowed'])} files)")
        return 0
    MANIFEST.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {MANIFEST} ({len(manifest['allowed'])} allowed files)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
