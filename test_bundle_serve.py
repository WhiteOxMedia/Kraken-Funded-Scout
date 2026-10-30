"""test_bundle_serve.py — THE DOWNLOAD SURFACE (acceptance condition 2).

Pins the stable-URL contract for the Kraken Scout bundle against the
REAL dist/ artifacts on disk (no network):

  1. /downloads/KrakenScoutBundle.zip serves the exact bytes whose
     SHA256 equals the manifest's published hash (ANONYMOUS — see 5)
  2. /downloads/KrakenScoutBundle.zip.sha256 serves that same hash
     (ANONYMOUS — see 5)
  3. /api/downloads/bundle-info publishes identical fields (stays
     public: hash + provenance metadata, never bytes)
  4. the dashboard card filler (bundle_card.js) paints the same hash
     into #bundleSha — UI can never disagree with the published file
  5. THE PUBLIC CONTRACT (Oct 24): both artifact routes answer
     anonymously with 200 and the exact bytes; a non-admin session and
     a bogus operator token change nothing, because there is no gate
     left to pass; HEAD advertises size and type with no body, and
     every response carries nosniff + no-cache. The Oct 8 gate was
     lifted once the ntfy topic was scrubbed (kraken 3b95c02) and
     finalize_bundle.py began failing the build if the topic or a
     denied file would ship.
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

print("== 2. THE STABLE URLS (anonymous — what the page promises) ==")
r = client.get("/downloads/KrakenScoutBundle.zip")
served = hashlib.sha256(r.content).hexdigest()
check(2.1, r.status_code == 200 and served == disk_sha
      and len(r.content) == ZIP.stat().st_size,
      f"anon download route serves the exact artifact ({r.headers.get('content-type')})")
check(2.2, r.headers.get("x-content-type-options") == "nosniff",
      "nosniff set on the binary")
check(2.2, r.headers.get("cache-control") == "no-cache",
      "no-cache set on the binary (a pinned hash is never stale)")
r = client.get("/downloads/KrakenScoutBundle.zip.sha256")
check(2.3, r.status_code == 200 and r.text.strip() == disk_sha,
      "anon checksum route serves the published hash")
r = client.get("/api/downloads/bundle-info")
j = r.json()
check(2.4, r.status_code == 200 and j["sha256"] == disk_sha
      and j["bytes"] == ZIP.stat().st_size
      and j.get("monero_head") and j.get("scout_head"),
      f"info endpoint stays PUBLIC (hash only): {j.get('sha256', '')[:12]}… monero {j.get('monero_head')}")
# The version comes from installer/release_manifest.json, not from
# `git describe` (whose preprod/-g<hash> suffixes used to reach the page).
# The build's commit is published separately as monero_head/scout_head, and
# the Product markup states this same string as softwareVersion.
check(2.5, j.get("version") == "1.0.0",
      f"manifest publishes the production version: {j.get('version')}")
check(2.6, "preprod" not in str(j.get("version", "")).lower()
      and bool(j.get("monero_head")) and bool(j.get("scout_head")),
      f"version is marker-free and the built commits are published "
      f"({j.get('monero_head')}/{j.get('scout_head')})")

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
check(3.4, 'id="bundleVersion"' in html,
      "the card shows the published build version, not just the hash")
check(3.5, 'bundleVersion' in filler and "info.version" in filler,
      "the filler paints the version it read from the manifest")

print("== 5. THE PUBLIC CONTRACT (Oct 24): the gate is lifted ==")
# The Oct 8 gate is gone: the ntfy topic is scrubbed and untracked, and
# finalize_bundle.py fails the build if the topic bytes or a denied file
# would ship. These checks pin the public surface — including the legs
# that used to be special (admin session, non-admin session, token).
r = client.get("/downloads/KrakenScoutBundle.zip")
check(5.1, r.status_code == 200
      and hashlib.sha256(r.content).hexdigest() == disk_sha,
      f"anonymous zip GET -> {r.status_code}, exact bytes")
r = client.get("/downloads/KrakenScoutBundle.zip.sha256")
check(5.2, r.status_code == 200 and r.text.strip() == disk_sha,
      f"anonymous checksum GET -> {r.status_code}")
r = peon.get("/downloads/KrakenScoutBundle.zip")
check(5.3, r.status_code == 200,
      f"a logged-in non-admin gets the same 200 -> {r.status_code}")
r = admin.get("/downloads/KrakenScoutBundle.zip")
check(5.3, r.status_code == 200, "the admin leg still works")
# The token was the machine leg. It is now irrelevant to these routes,
# and that is the point: a wrong or absent token cannot change the
# answer, because the bytes are public.
_OP_TOKEN = "serve-gate-op-token"
db.set_setting("PUSH_OPERATOR_TOKEN", _OP_TOKEN)
r = client.get("/downloads/KrakenScoutBundle.zip",
               headers={"X-Operator-Token": "wrong-token-drill"})
check(5.4, r.status_code == 200,
      f"a bogus token changes nothing -> {r.status_code}")
db.set_setting("PUSH_OPERATOR_TOKEN", "")
r = client.get("/downloads/KrakenScoutBundle.zip",
               headers={"X-Operator-Token": "anything"})
check(5.5, r.status_code == 200,
      "an empty token setting changes nothing either (public route)")
db.set_setting("PUSH_OPERATOR_TOKEN", _OP_TOKEN)
h = client.head("/downloads/KrakenScoutBundle.zip")
check(5.6, h.status_code == 200 and h.content == b""
      and h.headers.get("content-length") == str(ZIP.stat().st_size)
      and h.headers.get("content-type", "").startswith("application/zip"),
      f"HEAD -> {h.status_code}, {h.headers.get('content-length')} bytes, no body")
h = client.head("/downloads/KrakenScoutBundle.zip.sha256")
check(5.7, h.status_code == 200 and h.content == b"",
      f"sidecar HEAD -> {h.status_code}")
r = client.get("/api/downloads/bundle-info")
check(5.8, r.status_code == 200,
      "bundle-info remains public metadata (the card filler's anonymous fetch)")

print("== 7. THE PAGE'S STRUCTURED DATA (served, not just in the template) ==")
import re as _re


def _ld(response):
    return [json.loads(b) for b in _re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', response.text, _re.S)]


home = client.get("/")
home_types = [n["@type"] for n in _ld(home)]
check(7.1, home.status_code == 200 and home_types == ["Organization", "WebSite"],
      f"every page carries the site entities: {home_types}")

page = client.get("/download")
page_nodes = _ld(page)
page_types = [n["@type"] for n in page_nodes]
check(7.2, page_types == ["Organization", "WebSite", "Product", "SoftwareApplication"],
      f"/download carries the product entities: {page_types}")
check(7.3, page_types.count("Product") == 1,
      "the Product node appears exactly once")

software = next((n for n in page_nodes if n["@type"] == "SoftwareApplication"), {})
product = next((n for n in page_nodes if n["@type"] == "Product"), {})
served_info = client.get("/api/downloads/bundle-info").json()
check(7.4, software.get("softwareVersion") == served_info.get("version"),
      f"softwareVersion matches the manifest: {software.get('softwareVersion')}")
check(7.5, (product.get("offers") or {}).get("availability", "").endswith("/InStock"),
      "the offer is InStock because the route is public")
check(7.6, (product.get("offers") or {}).get("url", "").endswith(
      "/downloads/KrakenScoutBundle.zip"),
      "the offer points at the artifact it serves")
check(7.7, '"@id": "https://xmrjackpot.com/#organization"' in page.text,
      "the organization has a stable @id the page references")
check(7.8, page.text.count("<h1") == 1
      and "Download the Miner and Start Earning Entries" in page.text,
      "the copy comes from the template: one h1, the download page's own")
check(7.9, '<link rel="canonical" href="https://xmrjackpot.com/download">'
      in page.text and "<title>Download the Kraken Scout Bundle" in page.text,
      "/download owns its title and canonical")
check("7.10", served_info.get("version") in page.text
      and served_info.get("sha256") in page.text,
      "the visible copy states the manifest's version and checksum")
check(7.11, 'src="/static/app.js' not in page.text
      and 'id="page-landing"' not in page.text,
      "the SPA's scripts and its other pages are not shipped at /download")
check(7.12, "Product" not in home_types
      and '"@type": "SoftwareApplication"' not in home.text,
      "the homepage does not advertise the product")

print("== 6. THE PAYLOAD INSIDE ==")
import zipfile
with zipfile.ZipFile(ZIP) as z:
    names = z.namelist()
    miners = [n for n in names if n.endswith("install.ps1")]
    check(4.1, any("KrakenScoutBundle/install.ps1" in n for n in miners),
          f"installer present ({len(names)} entries)")
    if miners:
        ps_text = z.read(miners[0]).decode("utf-8", "replace")
        # The pin must be the SHA256 of the ZIP the fallback downloads, taken
        # from that release asset on 2026-10-24. It used to be the hash of the
        # exe INSIDE the zip, which could never match the zip itself, so every
        # fallback install aborted as if the download had been tampered with.
        zip_pin = ("bba8097cb37d9b458a1cb1137876b27cde6740d17fe4ccbc086ba07d87d9e147")
        check(4.2, "xmrig-6.26.0-windows-x64.zip" in ps_text
              and f'$WantHash = "{zip_pin}"' in ps_text,
              "the fallback fetches the real release asset and pins its SHA256")
    readme = [n for n in names if n.endswith("README.txt")]
    check(4.3, bool(readme), "README ships at the bundle root")

print("== 8. SHARING-SAFE SERVING + THE SERVING-PID LOG (Oct 30) ==")
# The zip route used to hold the file through a CRT open that denied
# FILE_SHARE_DELETE, so for the life of every streamed download Explorer
# refused delete/rename with "You require permission from \<machine\>\<user>".
# The sharing-safety is a property of the OPEN (the share flags), and the
# diagnostic is a property of the LOG: both are pinned below.
import os as _os
import io as _io
import contextlib as _ctx
import ctypes as _ct
import anyio as _anyio

# 8.1 THE OPEN IS SHARING-SAFE: delete mid-read succeeds and the read
# carries on to the same bytes it pinned at open.
async def _sharing_open_leg():
    tmp = Path(tempfile.mkdtemp(prefix="sharing_leg_")) / "artifact.bin"
    body = b"HEAD-FIRST-" + b"x" * (256 * 1024) + b"-TAIL-LAST"
    tmp.write_bytes(body)
    resp = srv._SharingFileResponse(str(tmp), media_type="application/octet-stream")
    file = await resp._sharing_open()
    deleted_rc = None
    try:
        head = await file.read(16)
        _k32 = _ct.windll.kernel32
        _k32.DeleteFileW.argtypes = [_ct.c_wchar_p]
        deleted_rc = _k32.DeleteFileW(str(tmp))    # any other party's delete
        name_gone = not tmp.exists()
        await file.seek(128 * 1024)
        mid = await file.read(16)
        await file.seek(-10, _os.SEEK_END)
        tail = await file.read(10)
    finally:
        await file.aclose()
    return dict(head=head, deleted_rc=deleted_rc, name_gone=name_gone,
                mid=mid, tail=tail, body=body)

leg = _anyio.run(_sharing_open_leg)
check(8.1, leg["deleted_rc"] != 0 and leg["name_gone"] is True,
      f"delete-while-open succeeds and the name unlinks "
      f"(rc={leg['deleted_rc']}, gone={leg['name_gone']})")
check(8.2, leg["head"] == leg["body"][:16]
      and leg["mid"] == leg["body"][128 * 1024:128 * 1024 + 16]
      and leg["tail"] == leg["body"][-10:],
      "reads before and after the mid-flight delete return the exact "
      "bytes the open pinned")

# 8.3 THE COPIED HANDLERS STREAM correctly: 200 full-body, 206 single
# range and 206 multipart — through the class's own opens.
async def _response_leg():
    tmp = Path(tempfile.mkdtemp(prefix="resp_leg_")) / "artifact.bin"
    body = bytes(range(256)) * 512
    tmp.write_bytes(body)
    stat = tmp.stat()

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def run_scope(headers):
        msgs = []

        async def send(m):
            msgs.append(m)

        scope = {"type": "http", "method": "GET", "headers": headers,
                 "extensions": {}}
        resp = srv._SharingFileResponse(str(tmp), stat_result=stat,
                                        media_type="application/zip")
        await resp(scope, receive, send)
        start = next(m for m in msgs if m["type"] == "http.response.start")
        chunks = b"".join(m["body"] for m in msgs if m["type"] == "http.response.body")
        hdrs = dict((k.lower(), v) for k, v in start["headers"])
        return start["status"], hdrs, chunks

    out = {}
    out["full"] = await run_scope([])
    out["range"] = await run_scope([(b"range", b"bytes=0-63")])
    out["multi"] = await run_scope([(b"range", b"bytes=0-63, 128-191")])
    return out

rl = _anyio.run(_response_leg)
check(8.4, rl["full"][0] == 200 and len(rl["full"][2]) == 256 * 512
      and rl["full"][2] == bytes(range(256)) * 512,
      "plain GET streams the exact file, status 200")
check(8.5, rl["range"][0] == 206
      and rl["range"][1][b"content-range"] == b"bytes 0-63/131072"
      and rl["range"][2] == (bytes(range(256)) * 512)[:64],
      "single range -> 206 with content-range and the exact slice")
check(8.6, rl["multi"][0] == 206
      and rl["multi"][1][b"content-type"].startswith(b"multipart/byteranges")
      and (b"0-63" in rl["multi"][2] and b"128-191" in rl["multi"][2]),
      "multipart request answers 206 with both ranges' headers present")
check(8.7, issubclass(srv._SharingFileResponse, srv.FileResponse)
      and hasattr(srv.FileResponse, "_parse_range_header")
      and hasattr(srv.FileResponse, "generate_multipart"),
      "the subclass rides Starlette's own range machinery (the parent's "
      "internals the copied handlers rely on still exist)")

# 8.8 THE LIVE ROUTE IS WIRED: the GET answers synchronously-logged with
# THIS process's pid (uppercase path, zip file), and the route source
# instantiation is the sharing-safe class itself.
import io as _iobuf
BUF = _iobuf.StringIO()
with _ctx.redirect_stdout(BUF):
    r = client.get("/downloads/KrakenScoutBundle.zip", headers={"range": "bytes=0-0"})
log_out = BUF.getvalue()
check(8.8, r.status_code == 206 and f"handler_pid={_os.getpid()}" in log_out
      and "/downloads/KrakenScoutBundle.zip" in log_out
      and "[Downloads] GET" in log_out,
      f"the live zip route prints the serving pid log line "
      f"({r.status_code}, range honored)")
_route_src = (srv.__file__ and Path(srv.__file__).read_text(encoding="utf-8")) or ""
check(8.9, "async def downloads_bundle(request: Request)" in _route_src
      and "_SharingFileResponse(" in _route_src.split(
          "@app.get(\"/downloads/KrakenScoutBundle.zip\")", 1)[-1].split("def ", 1)[-1].split("@app.", 1)[0],
      "the zip GET instantiates the sharing-safe class directly (not the "
      "plain FileResponse)")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
