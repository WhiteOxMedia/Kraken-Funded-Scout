"""test_relay_status.py — the miner-facing status surface.

Phase 1 turns the relay from "running" into "usable", and a miner's first
question is always the same: is the thing I am about to point xmrig at
answering? This pins the answers:

  1. /status renders anonymously (no API key) and names the endpoint
  2. /api/relay-status returns the same facts as JSON, also anonymously
  3. a fresh healthy monitor file reads "operational"
  4. a fresh unhealthy file reads "degraded", and the failing probe is named
  5. a STALE file reads "unknown" — never "down": the relay can be fine while
     the monitor is not running, and this page must not cry wolf
  6. a missing file also reads "unknown" with HTTP 200, not an error page
  7. /download carries a real inline SVG QR of the stratum URL, plus the link
     to /status, and leaves no {{PLACEHOLDER}} behind

Prints "RESULT: N passed, M failed".
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import database as db

db.DB_PATH = Path(tempfile.mkdtemp(prefix="relay_status_")) / "jackpot.db"
db.init_db()

import server as srv
from fastapi.testclient import TestClient

results = []


def check(name, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(name) + ("  " + str(msg) if msg else ""))


client = TestClient(srv.app)
WORK = Path(tempfile.mkdtemp(prefix="relay_fixture_"))
STATUS = WORK / "relay-status.json"


def install_fixture(payload, age_s=0):
    STATUS.write_text(json.dumps(payload), encoding="utf-8")
    if age_s:
        old = time.time() - age_s
        os.utime(STATUS, (old, old))
    srv._RELAY_STATUS_FILE = STATUS
    srv._RELAY_CACHE["mtime"] = None
    srv._RELAY_CACHE["value"] = {}


HEALTHY = {
    "healthy": True,
    "checked": "2026-10-24T20:10:43Z",
    "checks": {
        "pool": {"ok": True, "detail": "job 270"},
        "tunnel": {"ok": True, "detail": "haproxy active, status=UP check=L4OK"},
        "public": {"ok": True, "detail": "job 270"},
        "dns": {"ok": True, "detail": "155.94.157.66"},
        "witness": {"ok": True, "detail": "PASS from cloudflare-edge 0 min ago"},
        "external": {"ok": False, "detail": "skipped: checker refused the request"},
    },
}
DEGRADED = json.loads(json.dumps(HEALTHY))
DEGRADED["healthy"] = False
DEGRADED["checks"]["tunnel"] = {
    "ok": False,
    "detail": "haproxy up but backend is DOWN (check L4KO) - tunnel or pool, not haproxy",
}

print("== 1. ANONYMOUS STATUS PAGE ==")
install_fixture(HEALTHY)
r = client.get("/status")
check(1.1, r.status_code == 200, f"HTTP {r.status_code}")
check(1.2, srv.STRATUM_URL in r.text, "page names the stratum endpoint miners use")
check(1.3, r.headers.get("cache-control") == "no-store", "status is never cached")
check(1.4, "noindex" in r.text.lower(), "kept out of search results")

print("== 2. THE JSON TWIN ==")
j = client.get("/api/relay-status")
check(2.1, j.status_code == 200, f"HTTP {j.status_code}")
body = j.json()
check(2.2, body.get("stratum") == srv.STRATUM_URL, "same endpoint as the page")
check(2.3, body.get("verdict") == "operational", f"verdict {body.get('verdict')}")
check(2.4, set(body.get("checks", {})) >= {"pool", "tunnel", "public", "dns"},
      "carries the per-probe detail")
check(2.5, j.headers.get("cache-control") == "no-store", "JSON also uncached")

print("== 3. HEALTHY = OPERATIONAL ==")
check(3.1, "Relay is answering" in r.text, "headline is the healthy one")
check(3.2, "Mining backend" in r.text and "Relay endpoint" in r.text,
      "every probe is labelled for a human")
check(3.3, "155.94.157.66" in r.text, "resolved address is shown")

print("== 4. UNHEALTHY = DEGRADED, AND IT SAYS WHICH LAYER ==")
install_fixture(DEGRADED)
r = client.get("/status")
check(4.1, "Relay is degraded" in r.text, "headline flips")
check(4.2, "backend is DOWN" in r.text, "failure is named, not just flagged")
check(4.3, client.get("/api/relay-status").json().get("verdict") == "degraded",
      "JSON agrees with the page")

print("== 5. STALE FILE = UNKNOWN, NOT DOWN ==")
install_fixture(HEALTHY, age_s=srv._RELAY_FRESH_S + 120)
r = client.get("/status")
check(5.1, "Relay status unknown" in r.text, "stale monitor data is not read as an outage")
check(5.2, "stale" in r.text, "and the page says why")
check(5.3, client.get("/api/relay-status").json().get("verdict") == "unknown",
      "JSON agrees on unknown")
check(5.4, r.status_code == 200, "still a page, not an error")

print("== 6. MISSING FILE = UNKNOWN, HTTP 200 ==")
missing = WORK / "does-not-exist.json"
srv._RELAY_STATUS_FILE = missing
srv._RELAY_CACHE["mtime"] = None
r = client.get("/status")
check(6.1, r.status_code == 200, f"HTTP {r.status_code}")
check(6.2, "Relay status unknown" in r.text, "no monitor data reads as unknown")
check(6.3, "no monitor data yet" in r.text, "and says there is none")

print("== 7. DOWNLOAD PAGE QR ==")
install_fixture(HEALTHY)
d = client.get("/download")
check(7.1, d.status_code == 200, f"HTTP {d.status_code}")
check(7.2, "<svg" in d.text, "inline SVG QR is rendered")
check(7.3, "{{STRATUM_QR}}" not in d.text, "no unsubstituted placeholder")
check(7.4, srv.STRATUM_URL in d.text, "QR sits beside the endpoint a miner types")
check(7.5, 'href="/status"' in d.text, "download page links to live status")
svg_start = d.text.find("<svg")
qr_len = d.text.find("</svg>", svg_start) - svg_start
check(7.6, qr_len > 400, f"QR has real module geometry ({qr_len} bytes)")

print()
passed = sum(1 for r_ in results if r_)
failed = len(results) - passed
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
