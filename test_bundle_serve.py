"""test_bundle_serve.py — THE DOWNLOAD SURFACE (acceptance condition 2).

Pins the stable-URL contract for the Kraken Scout bundle against the
REAL dist/ artifacts on disk (no network):

  1. /downloads/KrakenScoutBundle.zip serves the exact bytes whose
     SHA256 equals the manifest's published hash (ADMIN-gated — see 5)
  2. /downloads/KrakenScoutBundle.zip.sha256 serves that same hash
     (ADMIN-gated — see 5)
  3. /api/downloads/bundle-info publishes identical fields (stays
     public: hash + provenance metadata, never bytes)
  4. the dashboard card filler (bundle_card.js) paints the same hash
     into #bundleSha — UI can never disagree with the published file
  5. THE GATE (Oct 8): the download routes are ADMIN-gated — admin
     session or operator token; anonymous is 401, a non-admin user is
     403, a wrong or unprovisioned token is 401 (fail-closed). The
     public zip era is retired (the access log caught an external IP
     pulling it).
  6. integrity of the xmrig payload INSIDE the bundle (pin 6fa806…)

Prints "RESULT: N passed, M failed".
"""
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import database as db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="dl_drv_")) / "jackpot.db"
db.init_db()

import server as srv
from fastapi.testclient import TestClient

results = []
def check(n, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(n) + ("  " + str(msg) if msg else ""))

client = TestClient(srv.app)
# THE GATED READERS (Oct 8): the download routes answer only to an
# admin session or the operator token now — the drive carries one
# client per leg. `client` stays ANONYMOUS (the gate matrix's red
# witness); the admin/non-admin sessions are crafted directly
# (create_user + create_session) against the sandbox db.
_uid_admin = db.create_user("serve_gate_admin", "gate-hash", is_admin=True)
_uid_peon = db.create_user("serve_gate_peon", "gate-hash", is_admin=False)
db.create_session("serve-gate-admin", _uid_admin, time.time() + 3600)
db.create_session("serve-gate-peon", _uid_peon, time.time() + 3600)
admin = TestClient(srv.app)
admin.cookies.set("session", "serve-gate-admin")
peon = TestClient(srv.app)
peon.cookies.set("session", "serve-gate-peon")
DIST = HERE / "dist"
ZIP = DIST / "KrakenScoutBundle.zip"
MANIFEST = DIST / "KrakenScoutBundle.zip.json"
SHA_FILE = DIST / "KrakenScoutBundle.zip.sha256"

print("== 1. THE ARTIFACT ON DISK ==")
check(1.1, ZIP.exists() and ZIP.stat().st_size > 1024 * 1024
      and ZIP.read_bytes()[:4] == b"PK\x03\x04",
      f"bundle built: real zip, {ZIP.stat().st_size if ZIP.exists() else 0} bytes")
disk_sha = hashlib.sha256(ZIP.read_bytes()).hexdigest()
info = json.loads(MANIFEST.read_text(encoding="utf-8"))
check(1.2, disk_sha == info["sha256"],
      f"manifest hash matches the bytes on disk: {disk_sha[:16]}…")
published = SHA_FILE.read_text(encoding="ascii").strip()
check(1.3, published == disk_sha, ".sha256 file matches")

print("== 2. THE STABLE URLS (admin session — the gate's happy leg) ==")
r = admin.get("/downloads/KrakenScoutBundle.zip")
served = hashlib.sha256(r.content).hexdigest()
check(2.1, r.status_code == 200 and served == disk_sha
      and len(r.content) == ZIP.stat().st_size,
      f"admin download route serves the exact artifact ({r.headers.get('content-type')})")
check(2.2, r.headers.get("x-content-type-options") == "nosniff",
      "nosniff set on the binary")
r = admin.get("/downloads/KrakenScoutBundle.zip.sha256")
check(2.3, r.status_code == 200 and r.text.strip() == disk_sha,
      "admin checksum route serves the published hash")
r = client.get("/api/downloads/bundle-info")
j = r.json()
check(2.4, r.status_code == 200 and j["sha256"] == disk_sha
      and j["bytes"] == ZIP.stat().st_size
      and j.get("monero_head") and j.get("scout_head"),
      f"info endpoint stays PUBLIC (hash only): {j.get('sha256', '')[:12]}… monero {j.get('monero_head')}")

print("== 3. THE DASHBOARD SURFACE ==")
html = (HERE / "pool" / "templates" / "index.html").read_text(encoding="utf-8")
check(3.1, 'id="bundleSha"' in html and "/downloads/KrakenScoutBundle.zip"
      in html and "/downloads/KrakenScoutBundle.zip.sha256" in html,
      "dashboard card: stable link + checksum link + hash element")
filler = (HERE / "pool" / "static" / "bundle_card.js").read_text(encoding="utf-8")
check(3.2, "/api/downloads/bundle-info" in filler and "bundleSha" in filler,
      "card filler reads the SAME manifest the server publishes")
check(3.3, 'src="/static/bundle_card.js' in html,
      "filler included by the page")

print("== 5. THE GATE (Oct 8): the bundle is a private artifact ==")
# the anonymous leg is RETIRED: the access log caught an external IP
# (5.102.169.29) pulling the zip, and pre-Phase-0 the bytes carried the
# private ntfy topic. The public URL now answers 401 before any bytes.
r = client.get("/downloads/KrakenScoutBundle.zip")
check(5.1, r.status_code == 401,
      f"anonymous zip GET -> {r.status_code} (was 200 — the public era is over)")
r = client.get("/downloads/KrakenScoutBundle.zip.sha256")
check(5.2, r.status_code == 401,
      f"anonymous checksum GET -> {r.status_code} (the hash alone is operator intel too)")
# authenticated but not the operator: 403 (who you are is known; the
# artifact is not yours)
r = peon.get("/downloads/KrakenScoutBundle.zip")
check(5.3, r.status_code == 403,
      f"non-admin session -> {r.status_code} (401 for anon, 403 for a known non-admin)")
# the machine leg: the operator token rides X-Operator-Token. The
# sandbox PROVISIONS a token so every branch runs for real — happy,
# wrong, and the fail-closed empty-stored case (a supplied token
# against an empty setting is 401, never a degenerate match).
_OP_TOKEN = "serve-gate-op-token"
db.set_setting("PUSH_OPERATOR_TOKEN", _OP_TOKEN)
r = client.get("/downloads/KrakenScoutBundle.zip",
               headers={"X-Operator-Token": _OP_TOKEN})
check(5.4, r.status_code == 200
      and hashlib.sha256(r.content).hexdigest() == disk_sha,
      "operator-token leg serves the exact artifact (headless re-install)")
r = client.get("/downloads/KrakenScoutBundle.zip",
               headers={"X-Operator-Token": "wrong-token-drill"})
check(5.5, r.status_code == 401,
      f"a WRONG token -> {r.status_code} (never a silent pass)")
db.set_setting("PUSH_OPERATOR_TOKEN", "")
r = client.get("/downloads/KrakenScoutBundle.zip",
               headers={"X-Operator-Token": "anything"})
check(5.7, r.status_code == 401,
      f"EMPTY stored setting + supplied token -> {r.status_code} (fail-closed, never a degenerate match)")
db.set_setting("PUSH_OPERATOR_TOKEN", _OP_TOKEN)
# bundle-info stays public: the dashboard card must render for a
# logged-out visitor (hash + provenance, never the bytes)
r = client.get("/api/downloads/bundle-info")
check(5.6, r.status_code == 200,
      "bundle-info remains public metadata (the card filler's anonymous fetch)")

print("== 6. THE PAYLOAD INSIDE ==")
import zipfile
with zipfile.ZipFile(ZIP) as z:
    names = z.namelist()
    miners = [n for n in names if n.endswith("install.ps1")]
    check(4.1, any("KrakenScoutBundle/install.ps1" in n for n in miners),
          f"installer present ({len(names)} entries)")
    if miners:
        ps_text = z.read(miners[0]).decode("utf-8", "replace")
        check(4.2, "6fa80698d7268f6e88aa88c06fb27ee99e1bcee747c2e76911e6206a5b1aeeb3"
              in ps_text,
              "xmrig SHA256 pin rides inside the bundle's install.ps1")
    readme = [n for n in names if n.endswith("README.txt")]
    check(4.3, bool(readme), "README ships at the bundle root")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
