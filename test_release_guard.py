"""test_release_guard.py — THE RELEASE MANIFEST CONTRACT (Oct 25).

The public bundle is built from `git archive HEAD` of two repos, so the
old artifact shipped 876 entries: tests, audit and phase reports, the
pool server and jackpot engine, CI files, the developer xmrig config.
The cure is installer/release_manifest.json plus release_guard.py, and
this drive pins the properties that make it a cure rather than a filter:

  1. the manifest is default-deny: an unlisted file never ships;
  2. the named contaminant classes are refused BY NAME (AUDIT_*,
     E2E_*, PHASE4/5, KEY_MANAGEMENT_PLAN, verify_hostinger_corrections,
     proxy_poller, jackpot_engine, pool/server.py, tests);
  3. content rules catch a password-bearing URL, an operator wallet, a
     localhost pool endpoint, the developer rig id and a preprod label;
  4. pruning removes unlisted files and keeps listed ones;
  5. a built zip with a forbidden entry is reported, prefix or not;
  6. a production version cannot carry a preprod marker.

Run: python test_release_guard.py
"""
import json
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "installer"))

import release_guard as guard  # noqa: E402

results = []


def check(n, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(n)
          + ("  " + str(msg) if msg and not cond else ""))


manifest = guard.load_manifest()

# ── 1. default-deny ──────────────────────────────────────────────────
check(1.1, manifest.get("schema") == 1, "manifest schema")
check(1.2, not guard.matches("payload/scout/random_unlisted.py",
                             manifest["allowed"]),
      "an unlisted path is not allowed")
check(1.3, guard.name_violations("payload/scout/app.py", manifest) == [],
      "a listed runtime file is allowed")
check(1.4, guard.name_violations("payload/extra/thing.py", manifest),
      "an unlisted tree is refused")

# ── 2. the named contaminant classes ─────────────────────────────────
CONTAMINANTS = [
    "payload/monero_project/AUDIT_MONERO_PROJECT.md",
    "payload/monero_project/E2E_ACCEPTANCE_REPORT.md",
    "payload/monero_project/PHASE4_LIVE_INTEGRATION_REPORT.md",
    "payload/monero_project/PHASE5_COMPLETION_REPORT.md",
    "payload/monero_project/KEY_MANAGEMENT_PLAN.md",
    "payload/monero_project/verify_hostinger_corrections.py",
    "payload/monero_project/pool/proxy_poller.py",
    "payload/monero_project/pool/jackpot_engine.py",
    "payload/monero_project/pool/server.py",
    "payload/monero_project/pool/static/app.js",
    "payload/monero_project/test_bundle_serve.py",
    "payload/scout/test_apptest.py",
    "payload/xmrig-6.26.0/config.json",
    "payload/monero_project/.github/workflows/ci.yml",
    "payload/monero_project/.git/config",
]
for i, rel in enumerate(CONTAMINANTS, start=1):
    check(f"2.{i}", bool(guard.name_violations(rel, manifest)),
          f"contaminant allowed: {rel}")

# ── 3. content rules ─────────────────────────────────────────────────
BANNED = {
    "database-credential":
        'DB = "postgresql://user:hunter2@db.example.com/app"',
    "operator-wallet":
        "addr = '86NjWuTLKm7eggeGw9PZ5LjFDZTHUtAQtWs7Bbu783yYfDRqSd6itapDek39"
        "rySHWCax6fnDAKQQXLiieRtRqvzCPeqTxkG'",
    "developer-rig-id": '{"rig-id": "Birdsnbubbles"}',
    "localhost-pool": '{"url": "127.0.0.1:3333"}',
    "preprod-version": "release v1.0.0-preprod-2",
    "private-ntfy-topic": "https://ntfy.sh/xmrjackpot_operator_topic_9f2",
}
for rule_id, text in BANNED.items():
    hits = [r for r, _ in guard.content_violations(text, manifest)]
    check(f"3.{rule_id}", rule_id in hits, f"rule {rule_id} did not fire")
check("3.clean", guard.content_violations(
    "Mining sends work to mine.xmrjackpot.com:3333.", manifest) == [],
    "ordinary public copy must not trip a rule")

# ── 4. prune ─────────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / "payload" / "scout").mkdir(parents=True)
    (root / "payload" / "scout" / "app.py").write_text("x = 1\n",
                                                       encoding="utf-8")
    (root / "payload" / "scout" / "AUDIT_LEAK.md").write_text("x\n",
                                                              encoding="utf-8")
    (root / "payload" / "policy.sqlite").write_text("x\n", encoding="utf-8")
    removed = guard.prune(root, manifest)
    check(4.1, (root / "payload" / "scout" / "app.py").is_file(),
          "prune kept a listed file")
    check(4.2, not (root / "payload" / "scout" / "AUDIT_LEAK.md").exists(),
          "prune removed a forbidden file")
    check(4.3, not (root / "payload" / "policy.sqlite").exists(),
          "prune removed an unlisted file")
    check(4.4, "payload/scout/AUDIT_LEAK.md" in removed,
          "prune reports what it removed")
    check(4.5, guard.scan_bundle(root, manifest) == [],
          "the pruned tree is clean")

# ── 5. zip validation ────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as td:
    zpath = Path(td) / "bundle.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("KrakenScoutBundle/payload/scout/app.py", "x = 1\n")
        zf.writestr("KrakenScoutBundle/payload/tests/test_leak.py", "x\n")
    bad = guard.zip_violations(zpath, manifest)
    check(5.1, any("test_leak.py" in b for b in bad),
          "a forbidden zip entry is reported")
    check(5.2, not any("scout/app.py" in b for b in bad),
          "a listed zip entry is not reported")

# ── 6. version discipline ────────────────────────────────────────────
check(6.1, guard.release_version(manifest) == "1.0.0",
      "the production version is 1.0.0 without markers")
for bad_version in ("v1.0.0-preprod", "1.0.0-rc1", "dev-2026"):
    try:
        guard.release_version({"version": bad_version})
        ok = False
    except guard.ManifestError:
        ok = True
    check(f"6.{bad_version}", ok, f"{bad_version} was accepted")

# ── 7. the on-disk artifact, when present ────────────────────────────
zip_path = HERE / "dist" / "KrakenScoutBundle.zip"
if zip_path.exists():
    bad = guard.zip_violations(zip_path, manifest)
    check(7.1, bad == [], f"built bundle violations: {bad[:3]}")
else:
    print("SKIP  7.1  (no built bundle on disk)")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
