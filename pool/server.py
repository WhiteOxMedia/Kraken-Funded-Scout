"""
FastAPI backend for the XMR Jackpot Pool.
Serves the web dashboard, static assets, and JSON API endpoints.
"""
import os
import re
import sys
import json
from datetime import datetime, timezone
from html import escape
import time
import secrets
import hashlib
import hmac
import subprocess
from pathlib import Path

import asyncio
from functools import lru_cache

import aiohttp
from fastapi import FastAPI, Request, HTTPException, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import (HTMLResponse, JSONResponse,
                               PlainTextResponse, RedirectResponse,
                               FileResponse, Response)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))

import database as db
import field_crypto as _fc
import jackpot_engine as engine
import jackpot_funding
import security
import monero_wallet as xmr
import address_check as _addr   # offline mainnet payout-address gate
import i18n                   # language negotiation + region inference
import i18n_pages             # the six-language download/status pages
import projection_ad          # the advertisement-only projection surface
import scout_lifecycle        # Phase F: install/activation/connect/share

app = FastAPI(title="XMR Jackpot Pool")


# --- Security headers (Sep 27 audit MAJOR #2) -----------------------
# Every response - pages, API, static files, AND error responses -
# carries the pinned header set. The CSP forbids inline script
# (app.js's injected onclick= handlers were removed for delegated
# data-action listeners first) and forbids framing outright
# (frame-ancestors 'none' + X-Frame-Options: DENY against
# clickjacking on the claim/winner surfaces). style-src keeps
# 'unsafe-inline' because the templates style through 400+
# style attributes. WebSocket upgrades are NOT mutated: the scout
# stream is admin-session-gated at the route, and its 101 response
# headers belong to the route's own accept message.
class SecurityHeadersMiddleware:
    """Pure-ASGI wrapping middleware: threads both send() paths
    (http.response.start and websocket.http.response.start) and
    stamps the headers before the body ever flows."""

    # /scout* is embedded by the admin panel (admin_panel.html
    # iframe) - same-origin framing is the FEATURE there. Everything
    # else is deny-all. (Review finding, Sep 27 batch: a blanket
    # frame-ancestors 'none' blanked the console embed.)
    _CSP_COMMON = (
        b"default-src 'self'; "
        b"script-src 'self'; "
        # the template's Google Fonts (fonts.googleapis.com stylesheet,
        # fonts.gstatic.com files) are load-bearing: without them the
        # Oct 3 review major notes the Open Sans/Press Start faces
        # silently fall back (style-src/font-src 'self' block both)
        b"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        b"img-src 'self' data:; "
        b"font-src 'self' data: https://fonts.gstatic.com; "
        b"connect-src 'self'; "
        b"object-src 'none'; "
        b"base-uri 'self'; "
        b"form-action 'self'; ")
    SECURITY_HEADERS = (
        (b"content-security-policy",
         _CSP_COMMON + b"frame-ancestors 'none'; "
         b"upgrade-insecure-requests"),
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"no-referrer"),
        (b"x-frame-options", b"DENY"),
        (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
        (b"cross-origin-opener-policy", b"same-origin"),
    )
    SCOUT_FRAMING_HEADERS = (
        (b"content-security-policy",
         _CSP_COMMON + b"frame-ancestors 'self'; "
         b"upgrade-insecure-requests"),
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"no-referrer"),
        (b"x-frame-options", b"SAMEORIGIN"),
        (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
        (b"cross-origin-opener-policy", b"same-origin"),
    )

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        hdr_set = (self.SCOUT_FRAMING_HEADERS
                    if scope.get("path", "").startswith("/scout")
                    else self.SECURITY_HEADERS)
        async def send_with_headers(message):
            if message["type"] in ("http.response.start",
                                   "websocket.http.response.start"):
                headers = list(message.get("headers") or [])
                have = {k.lower() for k, _v in headers}
                for hk, hv in hdr_set:
                    if hk not in have:
                        headers.append((hk, hv))
                message = dict(message)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


# registered FIRST so it wraps every other middleware/route
app.add_middleware(SecurityHeadersMiddleware)


# ─── ONE ORIGIN, ONE SCHEME ────────────────────────────────
# Cloudflare terminates TLS and tells the origin which scheme the client
# used through X-Forwarded-Proto (older edges: CF-Visitor's "scheme").
# Two canonicalizations happen here, both 301s that keep the path and the
# query string:
#   * http -> https, so a plaintext request does not serve a second copy
#     of every URL;
#   * www -> apex, so www.xmrjackpot.com is one redirect away from the
#     canonical host instead of a duplicate site. The tunnel forwards the
#     Host header unchanged, so this is decided here, not at the edge.
# Loopback and un-tunnelled requests are never bounced: health checks and
# local development carry neither the proxy header nor a www host.
def _canonical_url(request: Request) -> str:
    target = SITE_ORIGIN + request.url.path
    if request.url.query:
        target += "?" + request.url.query
    return target


# ─── WHICH SURFACE IS BEING SERVED ─────────────────────────
# projection_ad refuses to hand out an advertisement while an OPERATOR
# URL is being served. That refusal has to know the URL, so bind it here
# for every request — before any handler runs, and cleared afterwards so
# a later task in the same context cannot inherit it.
@app.middleware("http")
async def advertisement_surface_marker(request: Request, call_next):
    token = projection_ad.mark_active_path(request.url.path)
    try:
        return await call_next(request)
    finally:
        projection_ad.clear_active_path(token)


@app.middleware("http")
async def canonical_origin(request: Request, call_next):
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if not proto:
        visitor = request.headers.get("cf-visitor") or ""
        m = re.search(r'"scheme"\s*:\s*"([a-z]+)"', visitor)
        proto = m.group(1) if m else ""
    host = re.sub(r":\d+$", "", (request.headers.get("host") or "").lower())
    if host.startswith("www."):
        return RedirectResponse(_canonical_url(request), status_code=301)
    if proto == "http" and host and host not in (
            "127.0.0.1", "localhost", "::1", "[::1]"):
        return RedirectResponse(_canonical_url(request), status_code=301)
    response = await call_next(request)
    # HTML is never cached heuristically. This site is edited live and rolled by
    # restarting :3094; without an explicit no-cache a browser is free to reuse
    # the old document, and the visitor sees the PREVIOUS design until a hard
    # reload - which reads exactly like "someone reverted the UI".
    # Static files keep their own caching (style.css is versioned by ?v=).
    if (response.headers.get("content-type", "").startswith("text/html")
            and "cache-control" not in response.headers):
        response.headers["Cache-Control"] = "no-cache"
    return response

# Mount static files
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# THE STABLE DOWNLOAD SURFACE (acceptance condition 2, Sep 26): the
# built Kraken Scout bundle + published checksum manifest, served from
# the pool itself so the URL never moves. Files live in dist/ (the
# build script's output dir); the routes expose ONLY the artifacts,
# with nosniff + attachment disposition. The manifest is generated at
# build time; /api/downloads/bundle-info reads it for the dashboard.
#
# PUBLIC AGAIN (Oct 24): the Oct 8 admin gate is lifted. It went up after
# the access log caught an external IP (5.102.169.29) pulling the zip,
# at a time when the archive still carried the private ntfy topic from
# before the Phase-0 scrub. Both reasons are gone: the topic is scrubbed
# and untracked (kraken 3b95c02), and finalize_bundle.py now FAILS the
# build when the topic bytes or a denied file (push_topic.txt,
# _ntfydiag.txt, .workshop/) would ship. /download and the Product
# markup both promise an anonymous download, so a gated URL is a dead
# link on the page that offers it.
#
# If the archive ever carries private internals again, restore the gate
# BEFORE serving: see `git show ec82f91:pool/server.py`, "THE ADMIN GATE
# (Oct 8)". A 401 is a bug; leaked bytes are not.
_dist_dir = Path(__file__).parent.parent / "dist"


# ONLY AN UNCORRUPTED ARTIFACT MAY BE SERVED (Oct 25). The builder prunes
# to installer/release_manifest.json, but the bytes on disk are not the
# builder: a stale zip, a hand-copied one, or an interrupted roll can put
# a contaminated archive under the PUBLIC download URL. The route checks
# the same manifest before every (cached) response and answers 503 when
# the artifact carries anything the manifest forbids. Fail closed: a
# missing manifest is a violation, not a pass.
_RELEASE_GUARD_CACHE = {"key": None, "problems": None}


def _release_guard_module():
    import importlib.util
    path = Path(__file__).parent.parent / "installer" / "release_guard.py"
    spec = importlib.util.spec_from_file_location("_xmrj_release_guard", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"release guard missing at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _artifact_problems(zip_path: Path) -> list:
    """Manifest violations for the artifact on disk (cached per bytes)."""
    try:
        st = zip_path.stat()
    except OSError:
        return []                             # 404 path handles absence
    key = (st.st_mtime_ns, st.st_size)
    if _RELEASE_GUARD_CACHE["key"] == key:
        return _RELEASE_GUARD_CACHE["problems"] or []
    try:
        guard = _release_guard_module()
        problems = guard.zip_violations(zip_path, guard.load_manifest())
    except Exception as e:                    # no manifest, broken guard
        problems = [f"release guard unavailable: {e}"]
    _RELEASE_GUARD_CACHE.update(key=key, problems=problems)
    return problems


def _artifact_headers() -> dict:
    """nosniff on every artifact response, and no-cache so a
    checksum-pinned file is never served stale from an edge cache."""
    return {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"}


SITE_ORIGIN = "https://xmrjackpot.com"
ORGANIZATION_ID = SITE_ORIGIN + "/#organization"
WEBSITE_ID = SITE_ORIGIN + "/#website"


def _bundle_manifest() -> dict:
    """The build manifest the artifact routes and the page both read."""
    mf = _dist_dir / "KrakenScoutBundle.zip.json"
    if not mf.exists():
        return {}
    try:
        return json.loads(mf.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def _jsonld_script(node: dict) -> str:
    nl = chr(10)
    return ('<script type="application/ld+json">' + nl
            + json.dumps(node, indent=2, ensure_ascii=False)
            + nl + "</script>")


def _site_jsonld() -> str:
    """Organization + WebSite: one node each, the same @id on every URL."""
    organization = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "@id": ORGANIZATION_ID,
        "name": "XMR Jackpot",
        "url": SITE_ORIGIN + "/",
    }
    website = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "@id": WEBSITE_ID,
        "url": SITE_ORIGIN + "/",
        "name": "XMR Jackpot",
        "inLanguage": "en",
        "publisher": {"@id": ORGANIZATION_ID},
    }
    return _jsonld_script(organization) + chr(10) + _jsonld_script(website)


def _download_jsonld() -> str:
    """Product + SoftwareApplication, for /download only.

    The version comes from the build manifest on every request, so the markup
    cannot drift from the file it describes. No version in the manifest means
    no claim: an empty string, not a guess.
    """
    version = _bundle_manifest().get("version")
    if not version:
        return ""
    product = {
        "@context": "https://schema.org",
        "@type": "Product",
        "@id": SITE_ORIGIN + "/download#bundle",
        "name": "Kraken Scout Bundle",
        "url": SITE_ORIGIN + "/download",
        "brand": {"@id": ORGANIZATION_ID},
        "offers": {
            "@type": "Offer",
            "price": "0",
            "priceCurrency": "USD",
            "availability": "https://schema.org/InStock",
            "url": SITE_ORIGIN + "/downloads/KrakenScoutBundle.zip",
        },
    }
    application = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "@id": SITE_ORIGIN + "/download#app",
        "name": "Kraken Scout",
        "applicationCategory": "UtilitiesApplication",
        "isAccessibleForFree": True,
        "softwareVersion": version,
        "url": SITE_ORIGIN + "/download",
        "downloadUrl": SITE_ORIGIN + "/downloads/KrakenScoutBundle.zip",
        "publisher": {"@id": ORGANIZATION_ID},
    }
    return _jsonld_script(product) + chr(10) + _jsonld_script(application)


# Template-owned JSON-LD is tagged with the one page that emits it:
#   <!-- XMRJ:SCHEMA page=download --> ... <!-- /XMRJ:SCHEMA -->
# Every other page strips the block, so the homepage never advertises a
# product it does not offer, or a FAQ it does not print.
SCHEMA_START = "<!-- XMRJ:SCHEMA page="
SCHEMA_END = "<!-- /XMRJ:SCHEMA -->"
_SCHEMA_BLOCK_RE = re.compile(re.escape(SCHEMA_START) + r"([a-z-]+)[\s\S]*?"
                              + re.escape(SCHEMA_END) + r"\s*")


def _with_jsonld(html: str, spa_page: str = "", canonical: str = "") -> str:
    """Add the structured data the page contract promises.

    Site entities go in the head of every page. Page-specific entities
    (Product on /download, FAQPage on /how-to-play) live in the template
    between XMRJ:SCHEMA markers tagged with their page, so this keeps the
    matching block and strips every other one - the homepage must not
    advertise a product it does not offer or a FAQ it does not print. Site
    entities are recognised by their own @type, which keeps this from
    doubling a block that the template (or an earlier call) already carries.
    """
    if SCHEMA_START in html:
        html = _SCHEMA_BLOCK_RE.sub(
            lambda m: m.group(0) if m.group(1) == spa_page else "", html)
    if canonical and 'rel="canonical"' not in html and "</head>" in html:
        html = html.replace(
            "</head>",
            '<link rel="canonical" href="' + SITE_ORIGIN + canonical + '">'
            + chr(10) + "</head>", 1)
    if '"@type": "Organization"' not in html and "</head>" in html:
        html = html.replace("</head>", _site_jsonld() + chr(10) + "</head>", 1)
    if spa_page == "download" and '"@type": "SoftwareApplication"' not in html:
        download = _download_jsonld()
        if download and "</body>" in html:
            html = html.replace("</body>", download + chr(10) + "</body>", 1)
    return html


@app.get("/downloads/KrakenScoutBundle.zip")
async def downloads_bundle():
    f = _dist_dir / "KrakenScoutBundle.zip"
    if not f.exists():
        raise HTTPException(status_code=404, detail="bundle not built")
    problems = _artifact_problems(f)
    if problems:
        print(f"[Release] REFUSING to serve {f.name}: {len(problems)} "
              f"manifest violation(s); first: {problems[0]}")
        raise HTTPException(
            status_code=503,
            detail="this artifact is quarantined by the release manifest")
    # revalidate per request, 304 when the bytes are unchanged: a
    # checksum-pinned artifact must NEVER be served stale (Cloudflare
    # proved this within the hour).
    return FileResponse(str(f), media_type="application/zip",
                        filename="KrakenScoutBundle.zip",
                        headers=_artifact_headers())


@app.head("/downloads/KrakenScoutBundle.zip")
async def downloads_bundle_head():
    f = _dist_dir / "KrakenScoutBundle.zip"
    if not f.exists():
        raise HTTPException(status_code=404, detail="bundle not built")
    if _artifact_problems(f):
        raise HTTPException(
            status_code=503,
            detail="this artifact is quarantined by the release manifest")
    return Response(status_code=200, media_type="application/zip",
                    headers={**_artifact_headers(),
                             "Content-Length": str(f.stat().st_size)})


@app.get("/downloads/KrakenScoutBundle.zip.sha256")
async def downloads_bundle_sha256():
    f = _dist_dir / "KrakenScoutBundle.zip.sha256"
    if not f.exists():
        raise HTTPException(status_code=404, detail="checksum not published")
    return PlainTextResponse(f.read_text(encoding="ascii"),
                             headers=_artifact_headers())


@app.head("/downloads/KrakenScoutBundle.zip.sha256")
async def downloads_bundle_sha256_head():
    f = _dist_dir / "KrakenScoutBundle.zip.sha256"
    if not f.exists():
        raise HTTPException(status_code=404, detail="checksum not published")
    text = f.read_text(encoding="ascii")
    return Response(status_code=200, media_type="text/plain",
                    headers={**_artifact_headers(),
                             "Content-Length": str(len(text.encode("ascii")))})


@app.get("/api/downloads/bundle-info")
async def downloads_bundle_info():
    """Public metadata for the dashboard download card: SHA256, size,
    build time, repo heads. Parsed from the build-time manifest — the
    dashboard must show the SAME hash the .sha256 file publishes."""
    mf = _dist_dir / "KrakenScoutBundle.zip.json"
    if not mf.exists():
        raise HTTPException(status_code=404, detail="bundle not built")
    try:
        info = json.loads(mf.read_text(encoding="utf-8"))
    except ValueError:
        raise HTTPException(status_code=500, detail="corrupt manifest")
    zip_path = _dist_dir / "KrakenScoutBundle.zip"
    if zip_path.exists():
        info["bytes_on_disk"] = zip_path.stat().st_size
    return JSONResponse(info)

# Initialize database
db.init_db()

# THE ENCRYPTED-AT-REST MIGRATION (R2, Sep 26): seal existing plaintext
# PII + the subscription blob. Idempotent — a no-op when already sealed
# or when no key is configured (plaintext-compat mode).
_enc = db.encrypt_existing_pii()
print("[crypto] at-rest migration:", _enc)


# ─── API Models ──────────────────────────────────────────────
class MinerRegister(BaseModel):
    wallet_address: str
    worker_name: str = ""
    # Phase F: the installer's install id, when it has one — links this
    # activation to the install record instead of creating a second row.
    install_id: str = ""


class ScoutInstallReport(BaseModel):
    """An install beacon: the id the installer minted for this machine
    (empty = the server mints one). No wallet, no account."""
    install_id: str = ""


class UpdateShares(BaseModel):
    wallet_address: str
    worker_name: str = ""
    shares_valid: int = 0
    shares_invalid: int = 0
    hashrate: float = 0


# --- Miner ingest gate (Sep 27 audit MAJOR) -------------------------
# /api/miner/shares is an ECONOMY WRITER: its shares_valid input
# backfills free jackpot tickets (100 shares = 1 ticket), so an
# unauthenticated or forgeable call mints entries without mining
# (the audit proved 50,000 tickets from two POSTs on a sandbox).
# The gate: a valid HMAC-SHA256 over (ts, wallet, shares_valid,
# shares_invalid) with the operator's miner_ingest_key is accepted
# from ANY source; a loopback caller is accepted ONLY while no key
# is provisioned (pre-provisioning ops mode) - once the key exists
# the gate is FAIL-CLOSED everywhere. The proxy poller keeps its
# direct DB write (it has never used this route).
MINER_INGEST_KEY_NAME = "miner_ingest_key"
MINER_SIG_MAX_AGE_S = 300        # replay window for the signed ts
MINER_DELTA_CAP = 500_000        # max per-call delta (5k tickets)


def _wallet_shape_ok(addr: str) -> bool:
    """The house wallet gate (Sep 28): an OFFLINE mainnet-standard
    validation — CN-Base58 decode, netbyte 0x12, Keccak-256 checksum
    — replacing the old prefix/length shape rule that could pass a
    testnet/stagenet/integrated or corrupt address into the payout
    queue. Fail-closed with named reasons in address_check.validate();
    junk rows like the live table's 'PENDING_*' still never enter."""
    return _addr.is_valid(addr)


def _miner_sig_ok(key: str, ts: int, wallet: str, sv: int,
                  si: int, sig: str) -> bool:
    msg = f"{ts}.{wallet}.{sv}.{si}".encode()
    good = hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, sig or "")


def _miner_ingest_authorized(request: Request, wallet: str,
                             sv: int, si: int) -> bool:
    """THE SHARE-FORGE GATE. Raises on EVERY refusal - 422 bad wallet
    shape or over-cap delta, 429 rate-limited, 403 unauthorized (no
    valid HMAC; loopback exempt only while no key exists) - and
    returns True only when authorized. The first cut of this gate
    RETURNED False on the 403 paths and the route ignored the return
    value, silently authorizing everyone; the red pins caught it
    (D.3/D.5/D.6/D.9) before it ever reached a commit."""
    if not _wallet_shape_ok(wallet):
        raise HTTPException(422, "invalid wallet address shape")
    if sv < 0 or si < 0 or sv > MINER_DELTA_CAP or si > MINER_DELTA_CAP:
        raise HTTPException(422, "share delta exceeds the per-call cap")
    ip = request.client.host if request.client else "unknown"
    if not security.shares_limiter.check(ip):
        raise HTTPException(429, "Too many requests. Slow down.")
    key = db.get_setting(MINER_INGEST_KEY_NAME, "")
    if key:
        if ip in ("127.0.0.1", "::1"):
            # an ops signal, not a flood: local tooling that has not
            # learned the key the operator already provisioned
            try:
                kr.emit_audit(None, "miner_ingest.loopback_denied")
            except Exception:                      # noqa: BLE001
                pass
        ts_raw = request.headers.get("x-miner-ts", "")
        sig = request.headers.get("x-miner-sig", "")
        try:
            ts = int(ts_raw)
        except ValueError:
            raise HTTPException(403, "miner ingest not authorized")
        if abs(time.time() - ts) > MINER_SIG_MAX_AGE_S:
            raise HTTPException(403, "miner ingest not authorized")
        if not _miner_sig_ok(key, ts, wallet, sv, si, sig):
            raise HTTPException(403, "miner ingest not authorized")
        return True
    # no key provisioned: loopback ops mode only
    if ip in ("127.0.0.1", "::1"):
        return True
    raise HTTPException(403, "miner ingest not authorized")


# ─── Frontend Route ──────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the main game dashboard."""
    return HTMLResponse(content=_with_jsonld(_template_html(), canonical="/"))


# ─── Standalone pages (/download, /winners) ─────────────────
# The copy and its schema live in the SPA template, so the app
# production already serves is the only copy of them. These routes keep
# exactly one section: the SPA's other pages and its scripts are
# stripped (nothing here is dynamic), the nav becomes plain links back
# into the app, and the head gets each URL's own title, description and
# canonical instead of the dashboard's.
#
# {{...}} tokens are filled from live sources on each request. A token
# with no value never ships: the copy falls back to an em dash, and any
# JSON-LD block still holding a token is dropped, so a page cannot claim
# a version the build manifest does not carry.
HOWTO_TITLE = "How to Play — Mine Monero, Earn Entries, Win the Daily Pot"
def _howto_description() -> str:
    """The /how-to-play meta description, unit from the engine (Oct 24).

    The entry unit is an operator setting, so it cannot be baked into a
    string constant: the description has to quote the number the pool
    actually pays.
    """
    unit = engine.entry_unit()
    return (f"Point a Monero miner at the pool, earn one entry per {unit} "
            "accepted shares, and take part in the daily drawing. No "
            "deposit, no entry fee, no purchase.")
WINNERS_TITLE = "Winners and Past Drawings — XMR Jackpot"
WINNERS_DESCRIPTION = (
    "Every completed XMR Jackpot drawing: when it ran, what it paid, the "
    "winning entry and the public proof that anyone can re-check."
)
_SECTION_RE = re.compile(r'<section id="page-([a-z-]+)"[^>]*>[\s\S]*?</section>\s*')
_LDJSON_RE = re.compile(r'<script type="application/ld\+json">[\s\S]*?</script>\s*')
_SCRIPT_SRC_RE = re.compile(r'\s*<script src="[^"]+"></script>')
_NAV_RE = re.compile(r'<nav class="navbar">[\s\S]*?</nav>')
_STATIC_NAV = '''<nav class="navbar">
  <div class="nav-logo">
    <span style="font-size:1.6em;">⛏️</span> XMR JACKPOT
  </div>
  <div class="nav-links">
    <a href="/">Home</a>
    <a href="/#howto">How to Play</a>
    <a href="/#realplay">Play for XMR</a>
    <a href="/#leaderboard">Winners</a>
    <a href="/#dashboard">Dashboard</a>
  </div>
</nav>'''


STYLE_HREF = "/static/style.css?v=21"   # test_i18n_region pins this to the template


@lru_cache(maxsize=1)
def _site_chrome() -> dict:
    """The navbar, footer and stylesheet every page wears, lifted from the app.

    The old standalone pages were the template minus its other sections, so a
    redesign reached them for free. They are catalog-rendered now, and a bare
    page without the logo, the Kraken Scout footer and the CTA box reads as a
    different, older site - so the chrome is read from the template instead of
    retyped here, and a template edit lands on every page at once.
    """
    tpl = _template_text()
    footer = re.search(r"<footer>[\s\S]*?</footer>", tpl).group(0)
    # No app.js on these pages: a data-action would be a dead link, so the
    # Terms link becomes the real page it means.
    footer = footer.replace('<a href="#" data-action="navigate-howto"',
                            '<a href="/how-to-play"', 1)
    # The footer states the payout split and the entry unit; catalog pages
    # must not ship those as literal tokens (Oct 24).
    try:
        footer = fill_terms(footer)
    except Exception as e:
        print(f"[Chrome] footer tokens not filled: {e}")
    return {"nav": _STATIC_NAV, "footer": footer, "style": STYLE_HREF}


def _utc_stamp(ts) -> str:
    """A drawing timestamp as a fixed UTC string, or an em dash."""
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OSError):
        return "—"


def _xmr(value) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "—"


def _winners_tokens() -> dict:
    """The drawings the page shows, read from the pool's own table.

    Only completed drawings count: a pending round has neither a winner
    nor a proof, and showing one would be a claim the proof link cannot
    back. With none completed the page keeps its empty state.
    """
    try:
        rows = [r for r in db.get_recent_drawings(20)
                if r.get("status") == "drawn"]
    except Exception:                     # a broken db must not 500 the page
        rows = []

    # Seeded history is display data, not a payout: a demo winner is
    # labeled on the public page too (Oct 24 full disclosure).
    def _demo(r) -> str:
        return " <em>(demo)</em>" if r.get("winner_is_demo") else ""

    if rows:
        latest = rows[0]
        wid = int(latest.get("id") or 0)
        wallet = escape(str(latest.get("winner_wallet") or "—")) + _demo(latest)
        latest_html = (
            "<table>\n      <tbody>\n"
            f'        <tr><th scope="row">Drawn</th><td>{_utc_stamp(latest.get("drawn_at"))}</td></tr>\n'
            f'        <tr><th scope="row">Winner</th><td><code>{wallet}</code></td></tr>\n'
            f'        <tr><th scope="row">Prize</th><td>{_xmr(latest.get("jackpot_amount"))} XMR</td></tr>\n'
            f'        <tr><th scope="row">Proof</th><td><a href="/api/verify/{wid}">/api/verify/{wid}</a></td></tr>\n'
            "      </tbody>\n    </table>"
        )
    else:
        latest_html = (
            '<p class="seo-empty"><strong>No drawing has completed yet.</strong> '
            "The first result will appear here with its proof as soon as the "
            "round closes.</p>"
        )

    draw_rows = "\n".join(
        "        <tr><td>#{}</td><td>{}</td><td>{}</td><td><code>{}</code></td>"
        '<td><a href="/api/verify/{id}">verify</a></td></tr>'.format(
            int(r.get("id") or 0), _utc_stamp(r.get("drawn_at")),
            _xmr(r.get("jackpot_amount")),
            escape(str(r.get("winner_wallet") or "—")) + _demo(r),
            id=int(r.get("id") or 0))
        for r in rows)
    return {
        "{{LATEST_DRAWING}}": latest_html,
        "{{DRAW_ROWS}}": draw_rows,
        "{{DRAW_EMPTY}}": "" if draw_rows else
        '<p class="seo-empty">Rows appear here once drawings complete.</p>',
    }


def _drop_unbacked_jsonld(html: str) -> str:
    """Remove every JSON-LD block that still holds an unfilled token."""
    kept, pos = [], 0
    for m in _LDJSON_RE.finditer(html):
        if "{{" in m.group(0):
            kept.append(html[pos:m.start()])
            pos = m.end()
    kept.append(html[pos:])
    return "".join(kept)


def _keep_only_section(html: str, keep: str) -> str:
    """Drop every SPA page section except `keep`."""
    return _SECTION_RE.sub(
        lambda m: m.group(0) if m.group(1) == keep else "", html)


def _standalone_page(template: str, keep: str, title: str,
                     description: str, path: str, tokens: dict) -> str:
    """Turn the SPA template into a page for one URL: one section,
    static nav, no SPA scripts, and this URL's own head."""
    html = template
    for token, value in tokens.items():
        if value:
            html = html.replace(token, value)
    html = _drop_unbacked_jsonld(html)
    html = re.sub(r"\{\{[A-Z0-9_]+\}\}", "—", html)

    html = _keep_only_section(html, keep)
    html = _NAV_RE.sub(lambda _m: _STATIC_NAV, html, count=1)
    html = _SCRIPT_SRC_RE.sub("", html)
    html = html.replace(f'<section id="page-{keep}" class="page">',
                        f'<section id="page-{keep}" class="page active">', 1)

    head, sep, rest = html.partition("</head>")
    if sep:
        head = re.sub(r"<title>[\s\S]*?</title>",
                      "<title>" + title + "</title>", head, count=1)
        head = re.sub(r'<meta name="description" content="[^"]*">',
                      '<meta name="description" content="'
                      + description + '">', head, count=1)
        head += '<link rel="canonical" href="' + SITE_ORIGIN + path + '">' + chr(10)
        html = head + sep + rest
    return html


def _template_text() -> str:
    return (Path(__file__).parent / "templates" / "index.html").read_text(
        encoding="utf-8")


def _pro_price() -> tuple:
    """The Scout Pro checkout price, as the checkout will charge it.

    (xmr, usd). The operator sells Pro at a DOLLAR price ($95 since Oct
    26), so the dollar figure is stored and the XMR amount is derived at
    the tracked rate. Deliberately reads this module's own `db`: the
    checkout module imports database under a second name, and a sandbox
    test that patches one binding must not silently read the other.
    """
    def _num(key, default):
        try:
            return float(db.get_setting(key, str(default)))
        except (TypeError, ValueError):
            return float(default)

    usd = _num("scout_pro_price_usd", 95.0)
    if usd <= 0:
        usd = 95.0
    # the live rate (or the operator's pin), never the old 323 constant
    rate = jackpot_funding.xmr_usd_rate()
    xmr = round(usd / rate, 8) if rate > 0 else 0.0
    return xmr, round(usd, 2)


def _payout_terms() -> dict:
    """Every number the copy states about the draw, from its setting.

    The page used to hardcode "90%", "every 24 hours" and "7 days". All
    three are operator settings (house_tax_percent, drawing_interval_hours,
    claim_window_days), so a tuned pool shipped a page describing the old
    rules. These are the same readers the engine uses, with the engine's
    own defaults, so the sentence and the payout cannot disagree.
    """
    def _num(key, default):
        try:
            return float(db.get_setting(key, str(default)))
        except (TypeError, ValueError):
            return float(default)

    tax = int(max(0, min(100, _num("house_tax_percent", 10))))
    hours = _num("drawing_interval_hours", 24)
    if hours <= 0:
        hours = 24.0
    claim = int(_num("claim_window_days", 7))
    if claim < 0:
        claim = 7
    pro_days = int(_num("pro_unlock_days", 100))
    if pro_days < 1:
        pro_days = 100
    return {
        "house_tax": tax,
        "winner": 100 - tax,
        "hours": ("%g" % hours),
        "claim": claim,
        "pro_days": pro_days,
    }


def _template_html() -> str:
    """The template with its live tokens filled in (Oct 24).

    The homepage states the entry rule, the payout split, the draw
    cadence, the claim window and the Pro price in words, so none of them
    can be a literal: each comes from the setting the engine, the
    scheduler or the checkout actually reads. A page that promises
    different odds, a different schedule or a different charge than the
    pool takes is a lie, and an operator who tunes a setting would
    otherwise ship one.
    """
    unit = engine.entry_unit()
    price_xmr, price_usd = _pro_price()
    terms = _payout_terms()
    # "N miners" in the comparison copy was a hardcoded "100+" while the
    # live counter said 22. The page states the pool's own count now.
    try:
        miners = str(engine.get_pool_stats().get("total_miners", 0))
    except Exception:
        miners = "—"
    # THE WINNERS BLOCK (fixed Oct 24): the SPA shell contains the winners
    # section, but only the /winners route used to fill DRAW_ROWS /
    # LATEST_DRAWING / DRAW_EMPTY. On / those three tokens shipped as
    # literal braces to the browser. Same reader, filled everywhere.
    try:
        winners = _winners_tokens()
    except Exception as e:
        print(f"[Template] winners block unavailable: {e}")
        winners = {}
    html = (_template_text()
            .replace("{{LATEST_DRAWING}}", winners.get("{{LATEST_DRAWING}}", ""))
            .replace("{{DRAW_ROWS}}", winners.get("{{DRAW_ROWS}}", ""))
            .replace("{{DRAW_EMPTY}}", winners.get("{{DRAW_EMPTY}}", ""))
            .replace("{{TOTAL_MINERS}}", miners))
    return fill_terms(html, unit=unit, prices=(price_xmr, price_usd),
                      terms=terms)


def fill_terms(html: str, unit=None, prices=None, terms=None) -> str:
    """Fill the live-number tokens in any chunk of template copy.

    `_site_chrome()` lifts the footer out of index.html for every catalog-
    rendered page (/download, /status, ...). The footer states the payout
    split, so it carries the same tokens — and without this the standalone
    pages rendered a literal "{{WINNER_PCT}}%". One filler for every
    surface that reuses template copy.
    """
    unit = engine.entry_unit() if unit is None else unit
    price_xmr, price_usd = _pro_price() if prices is None else prices
    terms = _payout_terms() if terms is None else terms
    return (html
            .replace("{{ENTRY_UNIT}}", str(unit))
            .replace("{{PRO_PRICE_XMR}}", ("%g" % price_xmr))
            .replace("{{PRO_PRICE_USD}}", "%.2f" % price_usd)
            .replace("{{HOUSE_TAX_PCT}}", str(terms["house_tax"]))
            .replace("{{WINNER_PCT}}", str(terms["winner"]))
            .replace("{{DRAW_HOURS}}", terms["hours"])
            .replace("{{CLAIM_DAYS}}", str(terms["claim"]))
            .replace("{{PRO_DAYS}}", str(terms["pro_days"])))


# ─── Public relay status (fed by relay/monitor-relay.py) ─────────────
#
# The monitor on the pool PC writes .relay/relay-status.json every cycle. This
# page is the miner-facing view of that file: no API key, no admin, no secrets —
# just "is the thing I am about to point xmrig at actually answering".
#
# Two honesties are built in. A missing or stale file reads as "unknown", never
# as "down": the relay can be perfectly healthy while the monitor is not running,
# and a status page that cries wolf is worse than one that says it does not know.
# Check details are escaped; they come from our own monitor, but a status page is
# a bad place to start trusting strings.
STRATUM_URL = "stratum+tcp://mine.xmrjackpot.com:3333"
_RELAY_STATUS_FILE = (Path(__file__).resolve().parent.parent
                      / ".relay" / "relay-status.json")
_RELAY_CACHE: dict = {"mtime": None, "value": {}}
_RELAY_FRESH_S = 300

def _qr_svg(data: str, scale: int = 4) -> str:
    """Inline SVG QR code, or "" if the encoder is unavailable.

    Returning an empty string rather than raising keeps a missing optional
    dependency from taking out the whole download page.
    """
    try:
        import segno
        return segno.make(data, error="m").svg_inline(
            scale=scale, border=2, dark="#1a1a1a", light="#ffffff")
    except Exception:
        return ""


def _relay_snapshot() -> dict:
    """Current monitor verdict, re-read only when the file changes."""
    try:
        st = _RELAY_STATUS_FILE.stat()
    except OSError:
        return {"available": False, "healthy": None, "checked": None,
                "checks": {}, "age_s": None}
    try:
        if _RELAY_CACHE["mtime"] != st.st_mtime:
            _RELAY_CACHE["value"] = json.loads(
                _RELAY_STATUS_FILE.read_text(encoding="utf-8"))
            _RELAY_CACHE["mtime"] = st.st_mtime
        snap = dict(_RELAY_CACHE["value"] or {})
    except Exception:
        return {"available": False, "healthy": None, "checked": None,
                "checks": {}, "age_s": None}
    snap["available"] = True
    snap["age_s"] = max(0, int(time.time() - st.st_mtime))
    return snap


def _relay_verdict(snap: dict) -> str:
    """operational | degraded | unknown — never 'down' from a stale file."""
    if not snap.get("available"):
        return "unknown"
    if snap.get("age_s") is not None and snap["age_s"] > _RELAY_FRESH_S:
        return "unknown"
    return "operational" if snap.get("healthy") else "degraded"


# ─── The miner pages, in six languages ───────────────────────────────
# /download and /status are rendered from pool/templates/i18n/<lang>.json, so
# the copy is data, not markup: a translator edits JSON, and the parity test
# fails if a language is missing a key or drops a {placeholder}. English keeps
# the unprefixed URL; every other language lives at /<code>/<page>, and an
# explicit choice in the selector beats the browser header from then on.
LOCALIZED_PAGES = ("download", "status")


def _download_bundle() -> dict:
    """Manifest facts for the download copy; a missing field renders as an em dash."""
    info = _bundle_manifest()
    size = info.get("size_human")
    if not size and info.get("bytes"):
        size = str(round(info["bytes"] / 1048576)) + " MB"
    return {"size": size, "version": info.get("version"),
            "built": info.get("built_utc"), "sha256": info.get("sha256")}


def _scout_offer() -> dict:
    """Live Scout Pro price and duration for the download page.

    Read from the same settings the checkout reads, so the page cannot quote a
    price the paywall does not charge. Unreadable settings fall back to the
    checkout's own defaults rather than printing nothing.
    """
    def setting(key, default):
        try:
            return str(db.get_setting(key, str(default)) or default)
        except Exception:
            return str(default)

    try:
        price, usd = _pro_price()
    except Exception:
        price, usd = 0.0, 95.0
    try:
        days = int(float(setting("scout_pro_days", "30")))
    except (TypeError, ValueError):
        days = 30
    return {"price": ("%.6f" % price).rstrip("0").rstrip("."),
            "usd": "%.2f" % usd, "days": max(1, days)}


def _render_lang_page(lang: str, page: str, request: Request) -> str:
    if page == "download":
        html = i18n_pages.render_download(
            lang, SITE_ORIGIN, i18n.region_from_headers(request.headers),
            _download_bundle(), _qr_svg(STRATUM_URL, scale=3), _site_chrome(),
            _scout_offer())
        # Site + product entities, appended once the copy exists: the version
        # inside them comes from the manifest, so markup cannot claim a build
        # the artifact is not.
        return html.replace("</head>", _site_jsonld() + chr(10)
                            + _download_jsonld() + chr(10) + "</head>", 1)
    snap = _relay_snapshot()
    return i18n_pages.render_status(lang, SITE_ORIGIN, snap,
                                    _relay_verdict(snap),
                                    engine.get_pool_stats(), _site_chrome())


def _serve_page(lang: str, page: str, request: Request) -> HTMLResponse:
    headers = {"Cache-Control": "no-store"} if page == "status" else {}
    return HTMLResponse(_render_lang_page(lang, page, request), headers=headers)


def _auto_language(request: Request, page: str):
    """Send a visitor to the language they asked for, once per URL.

    A choice made in the selector (the cookie) is as strong as the browser
    header, so a Spanish-speaking miner who opens an English link stays in
    Spanish instead of being quietly reset. English is never redirected, and a
    crawler — which sends no Accept-Language — always gets the page the
    canonical points at.
    """
    cookie = request.cookies.get(i18n.LANG_COOKIE)
    lang = (cookie if cookie in i18n.LANGUAGES
            else i18n.negotiate(request.headers.get("accept-language")))
    if lang == i18n.DEFAULT_LANG:
        return None
    return RedirectResponse(i18n.page_path(lang, page), status_code=302,
                            headers={"Vary": "Accept-Language, Cookie",
                                     "Cache-Control": "no-store"})


@app.get("/status", response_class=HTMLResponse)
@app.get("/status/", response_class=HTMLResponse)
async def status_page(request: Request):
    moved = _auto_language(request, "status")
    return moved or _serve_page(i18n.DEFAULT_LANG, "status", request)


@app.get("/api/relay-status")
async def api_relay_status():
    """Anonymous JSON view of the same data the /status page renders."""
    snap = _relay_snapshot()
    stats = engine.get_pool_stats()
    return JSONResponse({
        "stratum": STRATUM_URL,
        "verdict": _relay_verdict(snap),
        "available": snap.get("available", False),
        "healthy": snap.get("healthy"),
        "checked": snap.get("checked"),
        "age_seconds": snap.get("age_s"),
        "checks": snap.get("checks") or {},
        "pool": {
            "miners": stats.get("real_miners", stats.get("active_miners", 0)),
            "hashrate": stats.get("total_hashrate", 0),
        },
    }, headers={"Cache-Control": "no-store"})


@app.get("/download", response_class=HTMLResponse)
async def download_page(request: Request):
    """The download page, built from the catalog for every language."""
    moved = _auto_language(request, "download")
    return moved or _serve_page(i18n.DEFAULT_LANG, "download", request)


def _localized_handler(lang: str, page: str):
    async def handler(request: Request):
        return _serve_page(lang, page, request)
    return handler


for _code in i18n.LANGUAGES:
    if _code == i18n.DEFAULT_LANG:
        continue
    for _page in LOCALIZED_PAGES:
        app.add_api_route(f"/{_code}/{_page}", _localized_handler(_code, _page),
                          methods=["GET"], response_class=HTMLResponse,
                          name=f"{_code}_{_page}")


@app.get("/en/download")
async def en_download():
    """One URL per page per language, so English lives unprefixed."""
    return RedirectResponse("/download", status_code=301)


@app.get("/en/status")
async def en_status():
    return RedirectResponse("/status", status_code=301)


@app.get("/lang/{code}")
async def set_language(code: str, next: str = "/"):
    """Remember the selector's choice; the URL itself is the real switch."""
    if code not in i18n.LANGUAGES:
        return _not_found()
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(i18n.LANG_COOKIE, code, max_age=31536000, path="/",
                        samesite="lax")
    return response


@app.get("/how-to-play", response_class=HTMLResponse)
async def how_to_play_page():
    """The how-to page, with the FAQ that the page actually prints."""
    return HTMLResponse(content=_with_jsonld(
        _standalone_page(_template_html(), "howto-play", HOWTO_TITLE,
                         _howto_description(), "/how-to-play", {}),
        "how-to-play"))


@app.get("/winners", response_class=HTMLResponse)
async def winners_page():
    """The winners page, filled from the drawings table."""
    return HTMLResponse(content=_with_jsonld(
        _standalone_page(_template_html(), "winners", WINNERS_TITLE,
                         WINNERS_DESCRIPTION, "/winners", _winners_tokens())))


# ─── Crawler surface ─────────────────────────────────────────
# Both files used to fall through to the SPA catch-all, so every crawler
# got the HTML shell at /robots.txt and /sitemap.xml: no rules, no URLs,
# and the pages that do exist were never submitted. The sitemap lists
# exactly the URLs that serve their own title, canonical and copy.
SITEMAP_PATHS = ("/", "/how-to-play", "/winners", "/download")
ROBOTS_TXT = (
    "User-agent: *\n"
    "Allow: /\n"
    "Disallow: /api/\n"
    "Disallow: /admin\n"
    "Disallow: /downloads/\n"
    "Disallow: /lang/\n"
    "\n"
    f"Sitemap: {SITE_ORIGIN}/sitemap.xml\n"
)


def _indexable_paths() -> tuple:
    """Every URL that serves its own title, canonical and copy.

    The localized download pages belong here. /status does not, in any
    language: it is noindex on purpose, because a page whose whole content is
    "the relay answered 40 seconds ago" is a bad search result.
    """
    localized = tuple(i18n.page_path(code, "download")
                      for code in i18n.LANGUAGES
                      if code != i18n.DEFAULT_LANG)
    return SITEMAP_PATHS + localized


def _sitemap_xml() -> str:
    urls = "".join(
        "  <url><loc>" + SITE_ORIGIN + p + "</loc></url>\n"
        for p in _indexable_paths())
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + urls + "</urlset>\n")


# no-cache: Cloudflare pins /robots.txt for four hours, so an edit here
# would otherwise be invisible to crawlers until that entry expired.
@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return PlainTextResponse(ROBOTS_TXT, headers={"Cache-Control": "no-cache"})


@app.get("/sitemap.xml")
async def sitemap_xml():
    return Response(content=_sitemap_xml(), media_type="application/xml",
                    headers={"Cache-Control": "no-cache"})


# ─── Deployment version surface ──────────────────────────────
# The origin here is a workstation, not a managed host, so "is the
# live site running the commit I just made?" had no cheap answer:
# verification meant walking mtimes and sha256s by hand against the
# served bytes. /version answers it in one request with the commit
# this process STARTED FROM, the branch, the commit time, and the
# process start time.
#
# Read once at import, served from memory: no git call and no file
# read happens per request. Read-only by construction - the route
# takes no body, no params, and touches nothing but its own
# module-level snapshot. The snapshot is deliberately not refreshed
# at request time: what matters for a version check is the state the
# running process was launched from, not what is on disk now.
_REPO_ROOT = Path(__file__).parent.parent
_STARTED_AT = time.time()


def _git(*args: str) -> str | None:
    """One read-only git query against this checkout. Returns None
    (never raises) when git or the repo is absent - a deploy that
    ships without .git must still serve the rest of the app."""
    try:
        out = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), *args],
            # explicit utf-8 + replace: this runs at IMPORT, and a decode
            # error on a non-ASCII branch name would take the whole app
            # down before it ever served a request.
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _deploy_snapshot() -> dict:
    """Resolve the deployed identity once. DEPLOY_COMMIT /
    DEPLOY_BRANCH override git so an artifact copied without .git can
    still self-report (source becomes "env")."""
    commit = os.environ.get("DEPLOY_COMMIT") or _git("rev-parse", "HEAD")
    branch = os.environ.get("DEPLOY_BRANCH") or _git("rev-parse", "--abbrev-ref", "HEAD")
    porcelain = _git("status", "--porcelain")
    return {
        "commit": commit,
        "commit_short": commit[:12] if commit else None,
        "branch": branch,
        "commit_time": _git("log", "-1", "--format=%cI"),
        "describe": _git("describe", "--tags", "--always"),
        # Tracked AND untracked on purpose: an untracked file the app
        # imports is exactly the drift this endpoint exists to catch.
        "dirty": None if porcelain is None else bool(porcelain),
        "source": "env" if os.environ.get("DEPLOY_COMMIT") else "git",
    }


_DEPLOY = _deploy_snapshot()


@app.get("/version")
@app.get("/api/version")
async def version():
    """Read-only deployment identity. Deliberately uncached - a
    cached copy of a version stamp is worse than no stamp at all."""
    return JSONResponse(
        {
            "commit": _DEPLOY["commit"],
            "commit_short": _DEPLOY["commit_short"],
            "branch": _DEPLOY["branch"],
            "commit_time": _DEPLOY["commit_time"],
            "describe": _DEPLOY["describe"],
            "dirty": _DEPLOY["dirty"],
            "source": _DEPLOY["source"],
            "started_at": _STARTED_AT,
            "started_at_iso": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(_STARTED_AT)
            ),
            "uptime_seconds": round(time.time() - _STARTED_AT, 3),
            "pid": os.getpid(),
        },
        headers={"Cache-Control": "no-store, max-age=0"},
    )



# ─── API Key Auth ────────────────────────────────────────────

def check_api_key(request: Request):
    """Check for valid API key in Authorization header. Returns True if valid."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        stored_key = db.get_setting("api_key", "")
        active = db.get_setting("api_key_active", "0") == "1"
        if active and stored_key and token == stored_key:
            return True
    return False


# ─── Mobile API Endpoints ────────────────────────────────────

@app.get("/api/v1/status")
async def api_v1_status(request: Request):
    """Mobile API: pool status, jackpot, countdown, miners. Requires API key."""
    if not check_api_key(request):
        raise HTTPException(401, "Invalid or missing API key")
    stats = engine.get_pool_stats()
    sp = await api_social_proof()
    return {
        "jackpot_xmr": stats["jackpot_xmr"],
        "jackpot_usd": stats["jackpot_usd"],
        "total_miners": stats["total_miners"],
        "active_miners": stats["active_miners"],
        "total_hashrate": stats["total_hashrate"],
        "real_miners": stats.get("real_miners", 0),
        "total_entries": sp.get("total_entries", 0),
        "total_paid_out": sp.get("total_paid_out", 0),
        "total_drawings": sp.get("total_drawings", 0),
        "next_drawing_ts": sp.get("next_drawing_ts"),
        "now": sp.get("now"),
        "recent_winners": sp.get("recent_winners", []),
        "top_miner": sp.get("top_miner"),
        "announcement": {
            "active": db.get_setting("announcement_active", "0") == "1",
            "message": db.get_setting("announcement_message", ""),
        },
    }


@app.post("/api/v1/login")
async def api_v1_login(request: Request):
    """Mobile API: login with username/password, returns user info. Requires API key."""
    if not check_api_key(request):
        raise HTTPException(401, "Invalid or missing API key")
    data = await request.json()
    username = data.get("username", "")
    password = data.get("password", "")
    if not username or not password:
        raise HTTPException(400, "Username and password required")
    user = db.get_user_by_username(username)
    if not user or user["password_hash"] != hash_password(password):
        raise HTTPException(401, "Invalid credentials")
    db.update_last_login(user["id"])
    token = create_session_token()
    db.create_session(token, user["id"], time.time() + 86400 * 7, request.client.host if request.client else "mobile")
    is_https = request.url.scheme == 'https' or request.headers.get('x-forwarded-proto') == 'https'
    response = JSONResponse({
        "status": "ok",
        "session_token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "is_admin": bool(user["is_admin"]),
            "wallet_address": user["wallet_address"],
            "total_earned": user["total_earned"],
            "total_jackpots": user["total_jackpots"],
        }
    })
    response.set_cookie("session", token, httponly=True, secure=is_https, samesite="lax", max_age=86400*7)
    return response


@app.get("/api/v1/user")
async def api_v1_user(request: Request):
    """Mobile API: get user stats, entries, gamification. Requires API key + session."""
    if not check_api_key(request):
        raise HTTPException(401, "Invalid or missing API key")
    user = get_user_from_request(request)
    if not user:
        raise HTTPException(401, "Not logged in")
    wallet = user.get("wallet_address", "")
    miner = None
    tickets = 0
    if wallet and len(wallet) > 50:
        miner = db.get_or_create_miner(wallet)
        tickets = db.get_miner_ticket_count(miner["id"])
    with db.get_db() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM tickets WHERE drawing_id IS NULL").fetchone()
        total_entries = row["cnt"]
    win_chance = round((tickets / total_entries * 100), 2) if total_entries > 0 else 0
    # Check for pending winner claim
    winner_status = None
    with db.get_db() as conn:
        row = conn.execute(
            """SELECT d.*, m.worker_name FROM drawings d
               JOIN miners m ON d.winner_id = m.id
               WHERE d.status='drawn' AND d.claim_status='pending' AND m.worker_name=?
               ORDER BY d.drawn_at DESC LIMIT 1""",
            (user["username"],),
        ).fetchone()
        if row:
            winner_status = {
                "drawing_id": row["id"],
                "payout_amount": round(row["jackpot_amount"] - (row["house_tax"] or 0), 6),
                "claim_deadline": row["claim_deadline"],
                "has_wallet": bool(wallet and len(wallet) > 50),
            }
    return {
        "user": {
            "id": user["id"],
            "username": user["username"],
            "is_admin": bool(user["is_admin"]),
            "wallet_address": user["wallet_address"],
            "total_earned": user["total_earned"],
            "total_jackpots": user["total_jackpots"],
        },
        "your_entries": tickets,
        "total_entries": total_entries,
        "win_chance": win_chance,
        "miner": miner,
        "winner_claim": winner_status,
    }


# ─── Announcement ────────────────────────────────────────────

@app.get("/api/announcement")
async def api_announcement():
    """Get site-wide announcement. Public endpoint."""
    msg = db.get_setting("announcement_message", "")
    active = db.get_setting("announcement_active", "0") == "1"
    return {"active": active, "message": msg}


@app.post("/api/admin/announcement")
async def api_admin_set_announcement(request: Request):
    """Set or clear site-wide announcement. Admin only."""
    admin = require_admin(request)
    data = await request.json()
    msg = data.get("message", "")
    active = data.get("active", False)
    db.set_setting("announcement_message", msg)
    db.set_setting("announcement_active", "1" if active else "0")
    db.add_audit_log(admin["id"], "announcement", f"Set announcement: {'ON' if active else 'OFF'}", request.client.host)
    return {"status": "ok"}


# ─── API Endpoints ───────────────────────────────────────────
@app.get("/api/stats")
async def api_stats(request: Request):
    """Get pool statistics and current jackpot. Rate-limited per IP."""
    ip = request.client.host if request.client else "unknown"
    if not security.stats_limiter.check(ip):
        raise HTTPException(429, "Too many requests. Slow down.")
    stats = engine.get_pool_stats()
    # THE SETTLED ENTRY RULE (Oct 24): 1 valid share = 1 unit, and
    # entry_unit() units = ONE entry. The unit size rides public stats
    # from the engine's single reader, so the dashboard counter, the
    # minting engine and the homepage cannot describe different odds.
    stats["free_tickets_per_shares"] = engine.entry_unit()
    # the checkout price the Pro copy quotes (same setting the route reads)
    stats["scout_pro_price_xmr"] = _pro_price()[0]
    # FULL DISCLOSURE (Oct 24): this pool can be seeded with simulated
    # miners, and the site's totals include them. Publish the split so
    # the page can say "N of M miners are simulated" instead of letting
    # a visitor read fake hashrate as community activity.
    try:
        with db.get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n,"
                " COALESCE(SUM(CASE WHEN is_demo = 1 THEN 1 ELSE 0 END),0) AS d"
                " FROM miners"
            ).fetchone()
            stats["demo_miners"] = int(row["d"] or 0)
            stats["demo_mining_sample"] = bool(row["d"])
    except Exception as e:  # an old DB without the column must not 500 stats
        print(f"[Stats] demo split unavailable: {e}")
    return stats


@app.get("/api/social-proof")
async def api_social_proof():
    """Get social proof data for landing page: total paid out, recent winners feed, top miner, next drawing, total entries."""
    import time as _time
    now = _time.time()

    with db.get_db() as conn:
        # Total XMR paid out — sum from drawings (source of truth for awarded
        # prizes). FULL DISCLOSURE (Oct 24): a drawing won by a seeded demo
        # account was never paid, so it is counted separately instead of
        # inflating the "to winners since launch" figure.
        row = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN COALESCE(m.is_demo,0)=0
                                      THEN d.jackpot_amount - d.house_tax END), 0) AS real,
                      COALESCE(SUM(CASE WHEN COALESCE(m.is_demo,0)=1
                                      THEN d.jackpot_amount - d.house_tax END), 0) AS demo
               FROM drawings d LEFT JOIN miners m ON d.winner_id = m.id
               WHERE d.status = 'drawn'"""
        ).fetchone()
        total_paid_out = round(row["real"], 6)
        simulated_paid_out = round(row["demo"], 6)

        # Recent winners feed (last 5 drawn)
        winner_rows = conn.execute(
            """SELECT d.id, d.jackpot_amount, d.house_tax, d.drawn_at, d.claim_status,
                      d.winner_wallet, m.wallet_address as winner_addr,
                      COALESCE(m.is_demo, 0) as winner_is_demo
               FROM drawings d
               LEFT JOIN miners m ON d.winner_id = m.id
               WHERE d.status = 'drawn'
               ORDER BY d.drawn_at DESC LIMIT 5"""
        ).fetchall()
        recent_winners = []
        for r in winner_rows:
            wallet = r["winner_wallet"] or r["winner_addr"] or ""
            recent_winners.append({
                "drawing_id": r["id"],
                "prize_amount": round(r["jackpot_amount"] - (r["house_tax"] or 0), 6),
                "jackpot_amount": round(r["jackpot_amount"], 6),
                "drawn_at": r["drawn_at"],
                "winner_wallet": wallet[:8] + "..." + wallet[-4:] if len(wallet) > 16 else wallet,
                "claim_status": r["claim_status"],
                # A seeded/demo winner is display data, not a payout.
                "simulated": bool(r["winner_is_demo"]),
            })
        recent_winner = recent_winners[0] if recent_winners else None

        # Top miner this week
        row = conn.execute(
            """SELECT wallet_address, shares_valid, hashrate, last_seen, is_demo
               FROM miners
               WHERE shares_valid > 0
               ORDER BY shares_valid DESC LIMIT 1"""
        ).fetchone()
        top_miner = None
        if row:
            wallet = row["wallet_address"] or ""
            top_miner = {
                "wallet": wallet[:8] + "..." + wallet[-4:] if len(wallet) > 16 else wallet,
                "shares": row["shares_valid"],
                "hashrate": round(row["hashrate"] or 0, 1),
                "simulated": bool(row["is_demo"]),
            }

        # Total drawings completed, split real vs seeded demo winners so
        # "drawings completed" cannot claim history that never ran.
        row = conn.execute(
            """SELECT COUNT(*) AS cnt,
                      COALESCE(SUM(CASE WHEN COALESCE(m.is_demo,0)=1 THEN 1 ELSE 0 END),0) AS demo
               FROM drawings d LEFT JOIN miners m ON d.winner_id = m.id
               WHERE d.status = 'drawn'"""
        ).fetchone()
        total_drawings = int(row["cnt"] or 0) - int(row["demo"] or 0)
        simulated_drawings = int(row["demo"] or 0)

        # Total entries in current draw (pending tickets)
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM tickets WHERE drawing_id IS NULL"
        ).fetchone()
        total_entries = row["cnt"]

    # Next drawing countdown — roll forward in 24h increments until it's in the future
    interval_hours = float(db.get_setting("drawing_interval_hours", "24"))
    interval_seconds = interval_hours * 3600
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT MAX(drawn_at) as last_drawn FROM drawings WHERE status = 'drawn'"
        ).fetchone()
    last_drawn = row["last_drawn"] if row and row["last_drawn"] else 0
    if last_drawn:
        next_drawing_ts = last_drawn + interval_seconds
        # Keep rolling forward until the next drawing is in the future
        while next_drawing_ts <= now:
            next_drawing_ts += interval_seconds
    else:
        next_drawing_ts = now + interval_seconds

    return {
        "total_paid_out": total_paid_out,
        "total_drawings": total_drawings,
        # Seeded history, reported separately and labeled in the UI.
        "simulated_paid_out": simulated_paid_out,
        "simulated_drawings": simulated_drawings,
        "recent_winner": recent_winner,
        "recent_winners": recent_winners,
        "top_miner": top_miner,
        "total_entries": total_entries,
        "next_drawing_ts": next_drawing_ts,
        "now": now,
    }


@app.get("/api/miners")
async def api_miners():
    """Get list of all miners."""
    return db.get_all_miners()


@app.get("/api/miner/{wallet_address:path}")
async def api_miner(wallet_address: str):
    """Get individual miner stats and ticket count."""
    miner = db.get_or_create_miner(wallet_address)
    tickets = db.get_miner_ticket_count(miner["id"])
    drawings = db.get_recent_drawings(5)
    return {
        "miner": miner,
        "tickets": tickets,
        "recent_drawings": drawings,
    }


_INSTALL_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


@app.post("/api/scout/install")
async def api_scout_install(body: ScoutInstallReport, request: Request):
    """THE INSTALL BEACON (Phase F) — one per installed Scout.

    Unauthenticated by necessity: this fires before the customer has an
    account. It records ONLY an install — no wallet, no identity, nothing
    that could be mistaken for activation — is rate-limited per IP, and
    returns the id so a client that has none can persist it. Downloads are
    deliberately NOT counted here: a download is not an install.
    """
    client = request.client.host if request.client else "unknown"
    if not security.install_limiter.check(client):
        raise HTTPException(429, "too many install reports")
    iid = (body.install_id or "").strip()
    if iid and not _INSTALL_ID_RE.match(iid):
        raise HTTPException(422, "invalid install_id")
    rec = scout_lifecycle.record(install_id=iid or secrets.token_hex(16),
                                 installed=True)
    if not rec:
        raise HTTPException(503, "install ledger unavailable")
    return {"status": "ok", "install_id": rec["install_id"]}


@app.post("/api/miner/register")
async def api_register(miner: MinerRegister):
    """Register a new miner (shape-gated Sep 27: junk rows like
    'PENDING_*' are created elsewhere, never through this gate).

    Phase F: registration IS activation. Only the bundled installer calls
    this, and it has already written the wallet into the miner config; the
    install_id links the activation to the beacon row for that machine."""
    if not _wallet_shape_ok(miner.wallet_address):
        raise HTTPException(422, "invalid wallet address shape")
    record = db.get_or_create_miner(miner.wallet_address, miner.worker_name)
    scout_lifecycle.record(wallet=miner.wallet_address,
                           install_id=(miner.install_id or "").strip(),
                           activated=True)
    return {"status": "ok", "miner_id": record["id"]}


@app.post("/api/miner/shares")
async def api_update_shares(data: UpdateShares, request: Request):
    """Update miner share deltas - INGEST-GATED (Sep 27): a signed
    poller call (HMAC over ts+wallet+deltas with miner_ingest_key)
    or a loopback caller while no key is provisioned. Every refusal
    happens BEFORE any miner row is created or any ticket minted."""
    _miner_ingest_authorized(request, data.wallet_address,
                             data.shares_valid, data.shares_invalid)
    # the miner row MUST exist before the delta UPDATE: the old order
    # (update first) silently dropped a new wallet's first delta
    # (0 rows matched) - the poller's first report was lost
    miner = db.get_or_create_miner(data.wallet_address)
    db.update_miner_stats(data.wallet_address, data.shares_valid, data.shares_invalid, data.hashrate)
    new_tickets = engine.allocate_free_tickets(miner["id"], data.shares_valid)
    scout_lifecycle.record(wallet=data.wallet_address,
                           valid=data.shares_valid,
                           invalid=data.shares_invalid)
    return {"status": "ok", "new_tickets": new_tickets}


@app.get("/api/referral/stats")
async def api_referral_stats(request: Request):
    """Get referral stats for the authenticated user."""
    user = require_auth(request)
    stats = db.get_referral_stats(user["id"])
    if not stats:
        return {"referral_code": None, "referral_count": 0, "active_referrals": 0}
    reward = int(db.get_setting("referral_ticket_reward", "100"))
    # THE KRAKEN CARD'S OWN VALUES (Oct 25): the ONE link the operator is
    # an affiliate for. Settings win; the site's canonical CTA is the
    # fallback, so the card can never show an empty invite. The code is
    # the link's last path segment — derived, never stored twice.
    import kraken_challenge as _kch
    kraken_link = (_kch.get_referral_link() or _KRAKEN_CTA_URL).strip()
    kraken_code = kraken_link.rstrip("/").rsplit("/", 1)[-1]
    return {
        "referral_code": stats["referral_code"],
        "referral_link": f"https://xmrjackpot.com/?ref={stats['referral_code']}",
        "referral_count": stats["referral_count"],
        "active_referrals": stats["active_referrals"],
        "tickets_earned": stats["active_referrals"] * reward,
        "reward_per_referral": reward,
        # The Oct 25 programme: one entry to each side, capped per cycle.
        **db.referral_award_summary(user["id"]),
        # Program A (Kraken): the operator's affiliate link, its code, and
        # the share of a recorded payout the pool keeps (the referrer keeps
        # the other half from Kraken directly).
        "kraken_invite_link": kraken_link,
        "kraken_invite_code": kraken_code,
        "kraken_pool_share_pct": db.kraken_pool_share_pct(),
        # Program B (XMRJackpot): the XMRJ accrual and the Solana wallet it
        # would be paid to (empty until a wallet is connected).
        **db.referral_xmrj_summary(user["id"]),
    }


@app.get("/api/referral/check/{code}")
async def api_check_referral(code: str):
    """Check if a referral code is valid (public)."""
    referrer = db.get_user_by_referral_code(code)
    return {"valid": referrer is not None}


# The decorator was missing until Oct 24: the function existed but was
# never registered, so the Real Play page's subaddress call 404'd and the
# deposit box stayed empty forever.
@app.get("/api/payment/subaddress")
async def api_get_subaddress(request: Request):
    """Get the user's unique Monero subaddress for receiving payments."""
    user = require_auth(request)
    if user.get("subaddress"):
        return {"subaddress": user["subaddress"], "confirmations_required": xmr.REQUIRED_CONFIRMATIONS}

    # Generate a new subaddress
    wallet_available = xmr.rpc_available()
    if not wallet_available:
        raise HTTPException(503, "Monero wallet RPC unavailable. Subaddress generation requires a running wallet.")

    label = f"jackpot_user_{user['id']}"
    addr, idx = xmr.create_subaddress(label=label)
    if addr:
        db.set_user_subaddress(user["id"], addr, idx)
        db.add_audit_log(user["id"], "subaddress_assigned", f"Subaddress #{idx}", request.client.host)
        return {"subaddress": addr, "subaddress_index": idx, "confirmations_required": xmr.REQUIRED_CONFIRMATIONS}

    raise HTTPException(500, "Failed to generate subaddress")


# ── THE KRAKEN CTA CLICK COUNTER (Oct 10) ─────────────────────

_KRAKEN_CTA_URL = "https://invite.kraken.com/JDNW/29bygijw"
_KRAKEN_CTA_SLOTS = frozenset({
    "hero-top", "hero-badge", "wf-funded-button", "signup-step-zero",
    "howto-step1", "footer-box", "wallet-modal", "challenge-step1",
    "challenge-step5", "realplay-banner", "referral-block", "unattributed",
})

# THE FUNNEL STAYS HONEST (Oct 11): a public no-session counter is a
# magnet for bots and click-farm loops — the numbers only mean
# "humans clicked our referral link" if the cheap noise is filtered.
# Two gates, in-memory, no DB growth:
#
#   · THE BOT HEURISTIC: sendBeacon/fetch (what the real page sends)
#     always carries the site's origin; a browser always sends a
#     User-Agent. An originless request, a missing UA, or a
#     UA openly announcing itself a script/bot/curl is a script,
#     not a click — refused 403, recorded as "bot" for forensics.
#   · THE PER-IP WINDOW: a human reads a page and clicks a link;
#     even a very enthusiastic one does not click ten times in an
#     hour from one IP. Over the per-IP cap the extras are refused
#     429 and recorded as "limited" — the funnel keeps the first
#     truthful clicks, drops the replayed tail.

import threading as _cta_threading
_CTA_WINDOW_S = 3600          # the per-IP sliding window: 1 hour
_CTA_PER_IP_MAX = 8           # honest clicks per IP per window
_cta_hits: dict = {}          # ip -> [timestamps]; bounded, in-memory
_cta_lock = _cta_threading.Lock()

_BOT_UA_MARKERS = ("bot", "crawl", "spider", "curl/", "wget", "python-requests", "python-urllib", "headless", "phantom", "scrapy", "httpx", "okhttp")


def _cta_click_allowed(ip: str, user_agent: str, origin: str) -> str:
    """THE HONESTY GATE. Returns "ok" if the click counts, else the
    refusal reason ("bot" | "limited"). The bot leg is stateless;
    the limit leg is one LOCKED check-and-append (the push rate
    store's Sep 26 lesson: an unlocked interleave let two sends
    both pass the check)."""
    ua_low = (user_agent or "").lower()
    if not user_agent or any(m in ua_low for m in _BOT_UA_MARKERS):
        return "bot"
    if not origin:
        return "bot"   # a real page always sends Origin on beacon POST
    now = time.time()
    with _cta_lock:
        hits = [t for t in _cta_hits.get(ip, []) if now - t < _CTA_WINDOW_S]
        if len(hits) >= _CTA_PER_IP_MAX:
            _cta_hits[ip] = hits          # refresh the pruning
            return "limited"
        hits.append(now)
        _cta_hits[ip] = hits
        # the map itself must not grow forever: prune IPs whose whole
        # window has aged out (CodeRabbit MAJOR on c269a25 — the first
        # version pruned only EMPTY lists, but every stored list holds
        # at least one timestamp, so an IP that never came back was
        # never pruned and an IPv6-rotating farm could grow the map
        # without bound; the stale-by-window rule is the real expiry)
        if len(_cta_hits) > 4096:
            for k in [k for k, v in _cta_hits.items()
                      if not v or now - v[-1] >= _CTA_WINDOW_S][:1024]:
                _cta_hits.pop(k, None)
    return "ok"


class CTAClickBody(BaseModel):
    slot: str
    url: str = ""


@app.post("/api/cta/kraken-click")
async def api_cta_kraken_click(request: Request, body: CTAClickBody):
    """Public click counter for the Kraken referral CTAs. No session
    required — the counter MEASURES interest, it never spins up a
    login. Only the known referral URL and known slots are counted
    (a wrong url is dropped — the ledger records OUR link, not a
    spoofer's). Refusals (bot / over-limit) are RECORDED as their
    own slot tag so the admin row can show both counted and
    refused traffic — dishonest filters hide, honest filters label."""
    ip = request.client.host if request.client else "unknown"
    verdict = _cta_click_allowed(
        ip,
        request.headers.get("user-agent", ""),
        request.headers.get("origin", ""))
    if verdict != "ok":
        # forensics first: the refusal is data too
        db.record_cta_click("cta-refusal-" + verdict,
                            body.url or _KRAKEN_CTA_URL,
                            ip_address=ip, user_id=None)
        raise HTTPException(403 if verdict == "bot" else 429,
                            {"bot": "not a browser click",
                             "limited": "click limit reached for this IP"}[verdict])
    if body.slot not in _KRAKEN_CTA_SLOTS:
        raise HTTPException(422, "unknown CTA slot")
    if body.url and body.url.rstrip("/") != _KRAKEN_CTA_URL:
        raise HTTPException(422, "unrecognized CTA url")
    user = None
    try:
        user = get_user_from_request(request)  # optional — may stay None
    except Exception:
        user = None
    db.record_cta_click(body.slot, body.url or _KRAKEN_CTA_URL,
                        ip_address=ip,
                        user_id=user["id"] if user else None)
    return {"ok": True, "slot": body.slot}


@app.get("/api/admin/kraken-cta-stats")
async def api_admin_kraken_cta_stats(request: Request):
    """Admin metrics row: click counts per CTA slot (all-time,
    30-day, today)."""
    require_admin(request)
    return {"slots": db.get_cta_click_summary(days=30),
            "url": _KRAKEN_CTA_URL}


@app.get("/api/miner-config/{wallet_address:path}")
async def api_miner_config(wallet_address: str):
    """Generate a downloadable xmrig config.json pre-configured for this pool."""
    config = {
        "api": {"id": None, "worker-id": None},
        "http": {"enabled": True, "host": "127.0.0.1", "port": 18000, "access-token": None, "restricted": True},
        "autosave": True,
        "background": False,
        "colors": True,
        "title": True,
        "randomx": {"init": -1, "mode": "auto", "1gb-pages": False, "rdmsr": True, "wrmsr": True, "cache_qos": True, "numa": True},
        "cpu": {"enabled": True, "huge-pages": True, "hw-aes": True, "priority": 1, "yield": True, "max-threads-hint": 75, "asm": True},
        "opencl": {"enabled": False},
        "cuda": {"enabled": False},
        "donate-level": 0,
        "pools": [{
            "algo": None,
            "coin": "monero",
            "url": "mine.xmrjackpot.com:3333",
            "user": wallet_address,
            "pass": "x",
            "rig-id": None,
            "nicehash": False,
            "keepalive": True,
            "enabled": True,
            "tls": False,
        }],
        "retries": 5,
        "retry-pause": 5,
        "print-time": 60,
        "watch": True,
        "pause-on-battery": False,
        "pause-on-active": False,
    }
    return JSONResponse(config, headers={"Content-Disposition": "attachment; filename=config.json"})


@app.get("/api/miner-bat/{wallet_address:path}")
async def api_miner_bat(wallet_address: str):
    """Generate a downloadable start.bat pre-configured for this pool."""
    from fastapi.responses import PlainTextResponse
    bat_content = f"""@echo off
title XMR Miner — Jackpot Pool
echo ============================================
echo   XMR Daily Jackpot Pool Miner
echo   Mining to: {wallet_address[:20]}...
echo   Pool: mine.xmrjackpot.com:3333
echo ============================================
echo.
xmrig.exe --config=config.json
pause
"""
    return PlainTextResponse(
        bat_content,
        headers={"Content-Disposition": "attachment; filename=start.bat"},
        media_type="application/octet-stream"
    )


@app.get("/api/miner-download")
async def api_miner_download():
    """One-click installer for users who haven't registered yet. Uses a placeholder wallet."""
    from fastapi.responses import PlainTextResponse
    return await api_miner_installer("UNREGISTERED")


@app.get("/api/miner-installer/{wallet_address:path}")
async def api_miner_installer(wallet_address: str):
    """One-click installer: downloads xmrig, verifies SHA256, creates folder, writes config, starts mining."""
    from fastapi.responses import PlainTextResponse
    w = wallet_address
    # Official xmrig 6.26.0 Windows x64 release SHA256 (from GitHub release page)
    # Verified 2026-10-24: the release asset was downloaded and hashed here,
    # byte for byte. The value this replaced was a placeholder, so every
    # run of the one-click installer aborted at the hash check while
    # telling the user the download had been tampered with.
    XMRIG_SHA256 = "bba8097cb37d9b458a1cb1137876b27cde6740d17fe4ccbc086ba07d87d9e147"
    XMRIG_VERSION = "6.26.0"
    XMRIG_URL = f"https://github.com/xmrig/xmrig/releases/download/v{XMRIG_VERSION}/xmrig-{XMRIG_VERSION}-windows-x64.zip"

    installer = '@echo off\r\n'
    installer += 'title XMR Daily Jackpot - Miner Installer\r\n'
    installer += 'cd /d "%USERPROFILE%\\Desktop"\r\n'
    installer += 'echo ============================================\r\n'
    installer += 'echo   XMR Daily Jackpot Pool - Auto Installer\r\n'
    installer += 'echo   Wallet: ' + w[:20] + '...\r\n'
    installer += 'echo   xmrig v' + XMRIG_VERSION + ' (official GitHub release)\r\n'
    installer += 'echo ============================================\r\n'
    installer += 'echo.\r\n'
    installer += 'echo SECURITY INFO:\r\n'
    installer += 'echo   - Downloads xmrig ONLY from github.com/xmrig/xmrig\r\n'
    installer += 'echo   - Verifies SHA256 hash after download\r\n'
    installer += 'echo   - Mines to mine.xmrjackpot.com:3333\r\n'
    installer += 'echo   - Uses 75%% CPU max, runs at low priority\r\n'
    installer += 'echo   - Auto-starts on Windows reboot (removable)\r\n'
    installer += 'echo   - Source code: github.com/xmrig/xmrig (open source)\r\n'
    installer += 'echo.\r\n'
    installer += 'if not exist "XMR Jackpot Miner" mkdir "XMR Jackpot Miner"\r\n'
    installer += 'cd "XMR Jackpot Miner"\r\n'
    installer += 'if exist "xmrig.exe" goto :config\r\n'
    installer += 'echo Downloading xmrig v' + XMRIG_VERSION + ' from GitHub...\r\n'
    installer += 'powershell -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;$p=\'SilentlyContinue\';Invoke-WebRequest -Uri \'' + XMRIG_URL + '\' -OutFile xmrig.zip -UseBasicParsing"\r\n'
    installer += 'if errorlevel 1 echo Download failed - get xmrig from github.com/xmrig/xmrig/releases && pause && exit /b 1\r\n'
    installer += 'echo.\r\n'
    installer += 'echo Verifying SHA256 hash...\r\n'
    installer += 'powershell -Command "$h=(Get-FileHash xmrig.zip -Algorithm SHA256).Hash.ToLower();if($h -ne \'' + XMRIG_SHA256 + '\'){Write-Host \'HASH MISMATCH - ABORTING\';pause;exit 1}else{Write-Host \'SHA256 verified OK\'}"\r\n'
    installer += 'if errorlevel 1 echo Hash verification FAILED - aborting for safety && pause && exit /b 1\r\n'
    installer += 'echo.\r\n'
    installer += 'echo Extracting...\r\n'
    installer += 'powershell -Command "Expand-Archive -Path xmrig.zip -DestinationPath temp -Force"\r\n'
    installer += 'copy /Y "temp\\xmrig-' + XMRIG_VERSION + '\\xmrig.exe" . >nul 2>&1\r\n'
    installer += 'copy /Y "temp\\xmrig-' + XMRIG_VERSION + '\\WinRing0x64.sys" . >nul 2>&1\r\n'
    installer += 'del xmrig.zip\r\n'
    installer += 'rmdir /S /Q temp\r\n'
    installer += 'echo xmrig installed and verified!\r\n'
    installer += ':config\r\n'
    installer += 'echo Writing config.json...\r\n'
    installer += '(echo {"http":{"enabled":true,"host":"127.0.0.1","port":18000},"autosave":true,"background":false,"colors":true,"randomx":{"init":-1,"mode":"auto","rdmsr":true,"wrmsr":true,"cache_qos":true},"cpu":{"enabled":true,"huge-pages":true,"hw-aes":true,"priority":1,"max-threads-hint":75,"asm":true},"donate-level":0,"pools":[{"coin":"monero","url":"mine.xmrjackpot.com:3333","user":"' + w + '","pass":"x","keepalive":true,"enabled":true}]}) > config.json\r\n'
    installer += 'echo Writing start.bat...\r\n'
    installer += '(echo @echo off\r\n'
    installer += 'echo title XMR Daily Jackpot Miner\r\n'
    installer += 'echo echo Mining to: ' + w[:20] + '...\r\n'
    # the entry rule travels into the miner's console too, from the one
    # reader: a .bat that quotes a stale unit is the same lie as a page
    installer += ('echo echo Every ' + str(engine.entry_unit())
                  + ' valid shares = 1 free entry!\r\n')
    installer += 'echo echo To stop: close this window.\r\n'
    installer += 'echo xmrig.exe --config=config.json\r\n'
    installer += 'echo pause) > start.bat\r\n'
    installer += 'echo Writing auto-start.bat (launches miner on Windows startup)...\r\n'
    installer += '(echo @echo off\r\n'
    installer += 'echo title XMR Jackpot Miner - Auto Start\r\n'
    installer += 'echo cd /d "%%USERPROFILE%%\\Desktop\\XMR Jackpot Miner"\r\n'
    installer += 'echo start "" /min xmrig.exe --config=config.json\r\n'
    installer += 'echo) > auto-start.bat\r\n'
    installer += 'echo Adding to Windows Startup...\r\n'
    installer += 'powershell -Command "$s=[Environment]::GetFolderPath(\'Startup\');Copy-Item auto-start.bat $s\\XMR_Jackpot_AutoStart.bat -Force;if(Test-Path $s\\XMR_Jackpot_AutoStart.bat){Write-Host \'Auto-start installed! Miner will launch on every reboot.\'}else{Write-Host \'Could not add to Startup folder. Use start.bat manually.\'}"\r\n'
    installer += 'echo Writing verify.bat (re-verify integrity anytime)...\r\n'
    installer += '(echo @echo off\r\n'
    installer += 'echo echo Verifying xmrig.exe integrity...\r\n'
    installer += 'echo powershell -Command "$h=(Get-FileHash xmrig.exe -Algorithm SHA256).Hash;Write-Host \'SHA256: \'$h"\r\n'
    installer += 'echo echo Compare against github.com/xmrig/xmrig/releases\r\n'
    installer += 'echo pause) > verify.bat\r\n'
    installer += 'echo.\r\n'
    installer += 'echo ============================================\r\n'
    installer += 'echo   DONE! Folder: Desktop\\XMR Jackpot Miner\r\n'
    installer += 'echo   Double-click start.bat to mine anytime.\r\n'
    installer += 'echo   Auto-start installed — miner launches on reboot!\r\n'
    installer += 'echo   Double-click verify.bat to re-check integrity.\r\n'
    installer += 'echo   To remove auto-start: delete XMR_Jackpot_AutoStart.bat\r\n'
    installer += 'echo   from your Windows Startup folder.\r\n'
    installer += 'echo   If blocked by Defender: add folder as exclusion.\r\n'
    installer += 'echo   (xmrig is open-source, false positives are common)\r\n'
    installer += 'echo ============================================\r\n'
    installer += 'echo Starting miner in 3 seconds...\r\n'
    installer += 'timeout /t 3 >nul\r\n'
    installer += 'start.bat\r\n'

    return PlainTextResponse(
        installer,
        headers={"Content-Disposition": "attachment; filename=install_xmr_jackpot.bat"},
        media_type="application/octet-stream"
    )


# Registered Oct 24: this handler shipped without a decorator, so the
# payment history endpoint the refund/support flow needs did not exist.
@app.get("/api/payment/status")
async def api_payment_status(request: Request):
    """Get the user's recent payment history."""
    user = require_auth(request)
    payments = db.get_user_payments(user["id"])
    return {
        "payments": payments,
        "wallet_available": xmr.rpc_available(),
        "confirmations_required": xmr.REQUIRED_CONFIRMATIONS,
    }


@app.get("/api/drawings")
async def api_drawings():
    """Get recent drawing history."""
    return db.get_recent_drawings(20)


@app.get("/api/verify/{drawing_id}")
async def api_verify_drawing(drawing_id: int):
    """
    PUBLIC — No auth required.
    Provably fair verification: re-runs the drawing math
    so anyone can confirm the winner was selected fairly.
    """
    result = engine.verify_drawing(drawing_id)
    return result


@app.post("/api/draw")
async def api_run_drawing(request: Request):
    """Manually trigger a drawing. Requires nonce for replay protection."""
    data = await request.json() if request.headers.get("content-type") == "application/json" else {}
    nonce = data.get("nonce", "")
    if not nonce or not security.draw_nonce_store.consume(nonce):
        raise HTTPException(400, "Invalid or reused nonce. Refresh to get a new one.")
    result = engine.run_drawing()
    return result


@app.get("/api/draw-nonce")
async def api_draw_nonce():
    """Get a one-time nonce for triggering a drawing."""
    return {"nonce": security.draw_nonce_store.generate()}


@app.get("/api/next-drawing")
async def api_next_drawing():
    """Check if a drawing is due."""
    return engine.check_drawing_schedule()


# ═══════════════════════════════════════════════════════════════
#  AUTH SYSTEM
# ═══════════════════════════════════════════════════════════════

def hash_password(password, salt="xmrjackpot"):
    return hashlib.sha256((salt + password).encode()).hexdigest()


def create_session_token():
    return secrets.token_hex(32)


def get_user_from_request(request: Request):
    """Extract user from session cookie."""
    token = request.cookies.get("session")
    if not token:
        return None
    session = db.get_session(token)
    if not session:
        return None
    user = db.get_user(session["user_id"])
    return user


def require_auth(request: Request):
    """Require authentication, return user or raise 401."""
    user = get_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(request: Request):
    """Require admin authentication."""
    user = require_auth(request)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# Monero address alphabet (base58, no 0OIl) - for registration
# validation only; the authoritative check is always the network's.
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str = ""
    wallet_address: str = ""
    referral_code: str = ""
    # THE STATED REFERRAL RULE (Oct 25): the friend gives us the
    # REFERRER'S email at signup. The ?ref=CODE link still resolves to
    # the same award, so an old link is not a dead end.
    referrer_email: str = ""
    security_question: str = ""
    phone: str = ""          # the registration envelope (Sep 26 E2E)
    full_name: str = ""
    security_answer: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


class ProfileUpdate(BaseModel):
    display_name: str = ""
    avatar: str = "⛏️"
    bio: str = ""
    wallet_address: str = ""


class ProfileAlertIn(BaseModel):
    """An alert the client actually fired (checklist 4.1 / 4.2).

    `type` is validated against the two alert classes in the route; `ts`
    is the moment the alert fired on the device, kept verbatim so a
    retried write does not rewrite history with the receipt time.
    """
    type: str
    payload: object | None = None
    ts: str | None = None


class AdminSettingUpdate(BaseModel):
    key: str
    value: str


@app.post("/api/auth/register")
async def api_register(req: RegisterRequest, request: Request):
    """Register a new user account with optional referral. Minimal restrictions."""
    # REGISTRATION RATE LIMIT (Sep 26 E2E acceptance): account creation
    # is its own abuse surface, separate from login attempts.
    ip = request.client.host if request.client else "unknown"
    if not security.register_limiter.check(ip):
        raise HTTPException(429, "Too many registration attempts. Try again soon.")
    if not req.username or not req.password:
        raise HTTPException(400, "Username and password are required")
    if db.get_user_by_username(req.username):
        raise HTTPException(409, "Username already taken")
    # EMAIL UNIQUENESS under encryption-at-rest: the sealed column cannot
    # be UNIQUE-constrained, so duplicates are caught via the keyed
    # blind index (email_exists) — same 409 contract, no plaintext scan.
    if req.email and db.email_exists(req.email):
        raise HTTPException(409, "Email already registered")
    # KRAKEN WALLET FORMAT (Sep 26 E2E; UPGRADED Sep 28): a provided
    # wallet must survive the OFFLINE mainnet validation — decode,
    # netbyte, Keccak checksum — not just look Monero-shaped. An empty
    # value stays fine (wallet optional).
    if req.wallet_address:
        _ok, _why = _addr.validate(req.wallet_address.strip())
        if not _ok:
            raise HTTPException(400, "Wallet address does not look like a "
                                     f"valid Monero address — {_why}")

    # First user becomes admin
    user_count = len(db.get_all_users())
    is_admin = user_count == 0

    user_id = db.create_user(
        req.username,
        hash_password(req.password),
        req.email or None,
        req.wallet_address or None,
        is_admin,
        security_question=req.security_question or None,
        security_answer=req.security_answer or None,
        phone=(req.phone or "").strip() or None,
        full_name=(req.full_name or "").strip() or None,
    )

    # Generate referral code for this user
    ref_code = db.generate_referral_code(req.username)
    db.set_referral_code(user_id, ref_code)

    # Handle referral (Oct 25): one entry to EACH side, at signup, by the
    # referrer's email (code link = legacy form). db.award_referral_entries
    # enforces "once per referred account" and the per-cycle cap; a refusal
    # is normal and never fails the signup.
    if not is_admin:
        referrer = None
        method = ""
        if (req.referrer_email or "").strip():
            referrer = db.get_user_by_email(req.referrer_email)
            method = "email"
        if referrer is None and req.referral_code:
            referrer = db.get_user_by_referral_code(req.referral_code.strip())
            method = "code"
        if referrer and referrer["id"] != user_id:
            code = referrer.get("referral_code") or ""
            if code:
                db.set_referred_by(user_id, code)
                db.increment_referral_count(code)
            db.add_audit_log(
                referrer["id"], "referral",
                f"Referred {req.username} via {method}", request.client.host)
            entry_award = db.award_referral_entries(
                referrer["id"], user_id, tickets=engine.entry_unit(),
                method=method)
            # The INTERNAL programme's coin reward rides the SAME award: it
            # accrues only when the entry award was actually made, so the
            # per-cycle cap is one cap for both rewards and a capped signup
            # cannot farm XMRJ past it. A rate of 0 writes nothing.
            if entry_award.get("status") == "awarded":
                db.award_referral_xmrj(referrer["id"], user_id)

    # Signup bonus — credited as tickets, not withdrawable XMR
    # 0.003 XMR worth = 3 tickets, but shown as "signup bonus"
    # Kraken withdrawal fee is 0.0027 XMR, so withdrawing 0.003 nets only 0.0003 — discourages early withdraw
    signup_bonus = int(float(db.get_setting("signup_bonus_xmr", "0.003")) / 0.001)
    if signup_bonus > 0 and req.wallet_address:
        miner = db.get_or_create_miner(req.wallet_address)
        db.add_tickets(miner["id"], "free", signup_bonus)
        db.mark_signup_bonus_credited(user_id)

    db.update_last_login(user_id)
    db.add_audit_log(user_id, "register", f"User {req.username} registered" + (f" via referral {req.referral_code}" if req.referral_code else ""), request.client.host)

    token = create_session_token()
    db.create_session(token, user_id, time.time() + 86400 * 7, request.client.host)

    # Secure cookie only on HTTPS (Cloudflare tunnel); not on localhost HTTP
    is_https = request.url.scheme == 'https' or request.headers.get('x-forwarded-proto') == 'https'

    response = JSONResponse({"status": "ok", "user_id": user_id, "is_admin": is_admin, "referral_code": ref_code})
    response.set_cookie(
        "session", token,
        httponly=True, secure=is_https, samesite="lax",
        max_age=86400 * 7,
    )
    return response


@app.post("/api/auth/login")
async def api_login(req: LoginRequest, request: Request):
    """Login to an existing account."""
    # Rate limit
    ip = request.client.host if request.client else "unknown"
    if not security.auth_limiter.check(ip):
        raise HTTPException(429, "Too many login attempts. Try again soon.")

    user = db.get_user_by_username(req.username)
    if not user or user["password_hash"] != hash_password(req.password):
        raise HTTPException(401, "Invalid username or password")

    db.update_last_login(user["id"])
    db.add_audit_log(user["id"], "login", f"User {req.username} logged in", request.client.host)

    token = create_session_token()
    db.create_session(token, user["id"], time.time() + 86400 * 7, request.client.host)

    # Secure cookie only on HTTPS (Cloudflare tunnel); not on localhost HTTP
    is_https = request.url.scheme == 'https' or request.headers.get('x-forwarded-proto') == 'https'

    response = JSONResponse({
        "status": "ok",
        "user_id": user["id"],
        "username": user["username"],
        "is_admin": bool(user["is_admin"]),
    })
    response.set_cookie(
        "session", token,
        httponly=True, secure=is_https, samesite="lax",
        max_age=86400 * 7,
    )
    return response


@app.post("/api/auth/logout")
async def api_logout(request: Request):
    """Logout and destroy session."""
    token = request.cookies.get("session")
    if token:
        db.delete_session(token)
    response = JSONResponse({"status": "ok"})
    response.delete_cookie("session")
    return response


# ─── Password Recovery (security questions) ──────────────────

class SecurityCheckRequest(BaseModel):
    username: str

class PasswordResetRequest(BaseModel):
    username: str
    security_answer: str
    new_password: str

@app.post("/api/auth/security-question")
async def api_security_question(req: SecurityCheckRequest):
    """Get the security question for a username (step 1 of recovery)."""
    user = db.get_user_by_username(req.username)
    if not user:
        raise HTTPException(404, "User not found")
    question = db.get_security_question(user["id"])
    if not question:
        raise HTTPException(400, "No security question set for this account")
    return {"username": req.username, "security_question": question}


@app.post("/api/auth/reset-password")
async def api_reset_password(req: PasswordResetRequest, request: Request):
    """Reset password using security question answer (step 2 of recovery)."""
    ip = request.client.host if request.client else "unknown"
    if not security.auth_limiter.check(ip):
        raise HTTPException(429, "Too many attempts. Try again soon.")

    user = db.get_user_by_username(req.username)
    if not user:
        raise HTTPException(404, "User not found")

    if not db.verify_security_answer(user["id"], req.security_answer):
        db.add_audit_log(user["id"], "reset_failed", "Incorrect security answer", ip)
        raise HTTPException(403, "Incorrect security answer")

    if not req.new_password:
        raise HTTPException(400, "New password required")

    db.update_user_password(user["id"], hash_password(req.new_password))
    db.add_audit_log(user["id"], "password_reset", "Password reset via security question", ip)
    return {"status": "ok", "message": "Password reset successful"}


# ─── Comments ────────────────────────────────────────────────

class CommentRequest(BaseModel):
    name: str
    email: str = ""
    message: str

@app.post("/api/comment")
async def api_submit_comment(req: CommentRequest, request: Request):
    """Submit a comment from the landing page. Goes to admin dashboard."""
    if not req.name or not req.message:
        raise HTTPException(400, "Name and message are required")
    ip = request.client.host if request.client else "unknown"
    comment_id = db.add_comment(req.name.strip(), req.email.strip(), req.message.strip(), ip)
    return {"status": "ok", "comment_id": comment_id}


@app.get("/api/admin/comments")
async def api_admin_comments(request: Request):
    """Get all comments for admin dashboard."""
    admin = require_admin(request)
    status = request.query_params.get("status", None)
    return db.get_comments(status=status)


@app.post("/api/admin/comments/{comment_id}/status")
async def api_admin_comment_status(comment_id: int, request: Request):
    """Update a comment's status (read, archived)."""
    admin = require_admin(request)
    status = request.query_params.get("status", "read")
    db.update_comment_status(comment_id, status)
    return {"status": "ok"}


@app.get("/api/auth/me")
async def api_me(request: Request):
    """Get current authenticated user."""
    user = get_user_from_request(request)
    if not user:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "user_id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "is_admin": bool(user["is_admin"]),
        "display_name": user["display_name"],
        "avatar": user["avatar"],
        "wallet_address": user["wallet_address"],
        "total_earned": user["total_earned"],
        "total_jackpots": user["total_jackpots"],
    }


@app.post("/api/profile/update")
async def api_update_profile(req: ProfileUpdate, request: Request):
    """Update user profile."""
    user = require_auth(request)
    db.update_user_profile(
        user["id"],
        display_name=req.display_name,
        avatar=req.avatar,
        bio=req.bio,
        wallet_address=req.wallet_address,
    )
    db.add_audit_log(user["id"], "profile_update", "Updated profile", request.client.host)
    return {"status": "ok"}


@app.post("/api/profile/alert-log")
async def api_profile_alert_log(req: ProfileAlertIn, request: Request):
    """Record one fired alert for the signed-in member.

    The client only posts when an alert really fired with NFTY disabled,
    so this row is the member-visible record the profile renders. Scope
    is always the session's own user id - a request can never write into
    somebody else's history.
    """
    user = require_auth(request)
    alert_type = (req.type or "").strip().upper()
    if alert_type not in ("BUY_NOW", "GET_OUT_NOW"):
        raise HTTPException(
            status_code=400,
            detail="unsupported alert type: only BUY_NOW and GET_OUT_NOW are logged",
        )
    entry = db.add_profile_alert(
        user["id"], alert_type, payload=req.payload,
        ts=req.ts or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    return {"status": "ok", "entry": entry}


@app.get("/api/profile/alert-log")
async def api_profile_alert_log_list(request: Request, limit: int = 0):
    """The signed-in member's own alert history: the full list, newest
    first. `?limit=N` pages it (capped at 200) for callers that ask.
    """
    user = require_auth(request)
    return {"alerts": db.get_profile_alerts(user["id"], limit=limit)}


@app.get("/api/profile/stats")
async def api_profile_stats(request: Request):
    """Get detailed stats for the authenticated user."""
    user = require_auth(request)
    wallet = user.get("wallet_address", "")

    miner = None
    tickets = 0
    if wallet:
        miner = db.get_or_create_miner(wallet)
        tickets = db.get_miner_ticket_count(miner["id"])

    # Calculate total entries in current draw + user's win chance
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM tickets WHERE drawing_id IS NULL"
        ).fetchone()
        total_entries = row["cnt"]
    win_chance = round((tickets / total_entries * 100), 2) if total_entries > 0 else 0

    drawings = db.get_recent_drawings(10)
    user_drawings = [d for d in drawings if d.get("winner_wallet") == wallet]

    # THE CONTRIBUTION THANKS (user request, Sep 26): once Kraken has
    # actually PAID the Referral bonus, the member's dashboard greets
    # them with "Thanks, you've contributed to the jackpot $X.XX". The
    # read is the user's OWN latest snapshot row - the per-user history
    # the referral worker persists - so nothing crosses user boundaries
    # and nothing is invented: only a snapshot whose status is Paid
    # from the LIVE provider (never the simulation's fixtures - the
    # simulate world must never greet anyone with a paid bonus) carries
    # the dollar amount, and an absent/none-Paid history ships null.
    thanks = None
    with db.get_db() as conn:
        _snap = conn.execute(
            "SELECT kraken_status, total_paid FROM kraken_referral_snapshot "
            "WHERE user_id = ? AND source = ? AND kraken_status = 'Paid' "
            "ORDER BY id DESC LIMIT 1",
            (user["id"], kr.SOURCE_LIVE),
        ).fetchone()
    if _snap and (_snap["total_paid"] or 0) > 0:
        thanks = {"amount": round(float(_snap["total_paid"]), 2)}

    return {
        "user": {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "avatar": user["avatar"],
            "bio": user["bio"],
            "wallet_address": user["wallet_address"],
            "total_earned": user["total_earned"],
            "total_jackpots": user["total_jackpots"],
            "is_admin": bool(user["is_admin"]),
            "created_at": user["created_at"],
            "last_login": user["last_login"],
        },
        "miner": miner,
        "tickets": tickets,
        "your_entries": tickets,
        "total_entries": total_entries,
        "win_chance": win_chance,
        "recent_wins": user_drawings,
        "kraken_thanks": thanks,
    }


@app.get("/api/gamification")
async def api_gamification(request: Request):
    """Get gamification stats (XP, level, badges, streak, super tickets) for the authenticated user."""
    user = require_auth(request)
    wallet = user.get("wallet_address", "")
    # The dashboard quotes the price it would actually be charged, from
    # the same reader the checkout and the homepage use (Oct 24).
    price_xmr, price_usd = _pro_price()
    prog = db.pro_progress(None, user["id"])
    prog["pro_price"] = {"xmr": price_xmr, "usd": price_usd}
    if not wallet:
        return {"has_miner": False, "progression": prog}
    miner = db.get_or_create_miner(wallet)
    stats = db.get_gamification_stats(miner["id"])
    prog = db.pro_progress(miner["id"], user["id"])
    prog["pro_price"] = {"xmr": price_xmr, "usd": price_usd}
    return {"has_miner": True, **stats, "progression": prog}


# ─── Winner Claim Flow ───────────────────────────────────────

@app.get("/api/winner/status")
async def api_winner_status(request: Request):
    """Check if the authenticated user has an unclaimed jackpot win."""
    user = require_auth(request)
    wallet = user.get("wallet_address", "")

    # If user has a wallet, check for pending claims on that wallet
    pending = None
    if wallet:
        pending = db.get_pending_claim_for_wallet(wallet)

    # Also check by username-matched pending miners (for winners without wallets yet)
    if not pending:
        with db.get_db() as conn:
            row = conn.execute(
                """SELECT d.*, m.wallet_address as winner_wallet_addr, m.worker_name
                   FROM drawings d
                   JOIN miners m ON d.winner_id = m.id
                   WHERE d.status = 'drawn' AND d.claim_status = 'pending'
                     AND m.worker_name = ?
                   ORDER BY d.drawn_at DESC LIMIT 1""",
                (user["username"],),
            ).fetchone()
            if row:
                pending = dict(row)

    if not pending:
        return {"has_pending_claim": False}

    # Calculate time remaining
    deadline = pending.get("claim_deadline")
    time_remaining = max(0, (deadline - time.time()) if deadline else 0)
    jackpot = pending["jackpot_amount"]
    house_tax = pending.get("house_tax", 0)
    payout = jackpot - house_tax

    return {
        "has_pending_claim": True,
        "drawing_id": pending["id"],
        "jackpot_amount": jackpot,
        "payout_amount": payout,
        "house_tax": house_tax,
        "drawn_at": pending.get("drawn_at"),
        "claim_deadline": deadline,
        "time_remaining_seconds": time_remaining,
        "total_tickets": pending.get("total_tickets", 0),
        "winner_wallet": pending.get("winner_wallet") or pending.get("winner_wallet_addr"),
        "has_wallet": bool(wallet and len(wallet) > 50),
        "message": "Thank you, for trusting Anthony. Claim your prize within 7 days to receive $30 worth of XMR!",
    }


class ClaimRequest(BaseModel):
    wallet_address: str


@app.post("/api/winner/claim")
async def api_winner_claim(req: ClaimRequest, request: Request):
    """Claim a jackpot win by providing a wallet address for payout."""
    user = require_auth(request)

    wallet = req.wallet_address.strip()
    if not wallet:
        raise HTTPException(400, "Wallet address required")

    # THE PAYOUT GATE (Sep 28): offline mainnet validation — decode,
    # netbyte, Keccak checksum — before the address can enter the
    # payout queue; the loose prefix/length rule is gone.
    _ok, _why = _addr.validate(wallet)
    if not _ok:
        raise HTTPException(400, f"Invalid Monero address — {_why}")

    # Update user's wallet
    db.update_user_profile(user["id"], wallet_address=wallet)

    # Find THIS user's pending drawing — match by username ONLY
    # This ensures each winner can only claim their own drawing
    with db.get_db() as conn:
        row = conn.execute(
            """SELECT d.*, m.id as miner_id, m.worker_name
               FROM drawings d
               JOIN miners m ON d.winner_id = m.id
               WHERE d.status = 'drawn' AND d.claim_status = 'pending'
                 AND m.worker_name = ?
               ORDER BY d.drawn_at DESC LIMIT 1""",
            (user["username"],),
        ).fetchone()

    if not row:
        raise HTTPException(400, "No pending claim found for your account")

    pending = dict(row)
    drawing_id = pending["id"]
    miner_id = pending["miner_id"]
    payout_amount = pending["jackpot_amount"] - (pending.get("house_tax") or 0)

    # Update the miner's wallet address to the real one
    with db.get_db() as conn:
        conn.execute(
            "UPDATE miners SET wallet_address = ? WHERE id = ?",
            (wallet, miner_id),
        )
        conn.execute(
            "UPDATE drawings SET winner_wallet = ? WHERE id = ?",
            (wallet, drawing_id),
        )
        conn.commit()

    # Mark as claimed
    db.claim_drawing(drawing_id, wallet)

    # Record payout as pending — Anthony will send manually from Kraken
    db.add_payout(miner_id, payout_amount, "jackpot", drawing_id)

    # Update user's total_earned
    with db.get_db() as conn:
        conn.execute(
            "UPDATE users SET total_earned = total_earned + ?, total_jackpots = total_jackpots + 1 WHERE id = ?",
            (payout_amount, user["id"]),
        )
        conn.commit()

    db.add_audit_log(user["id"], "winner_claim",
                     f"Claimed drawing #{drawing_id} -> {wallet[:16]}... ({payout_amount} XMR pending from Kraken)",
                     request.client.host)

    return {
        "status": "ok",
        "drawing_id": drawing_id,
        "payout_amount": payout_amount,
        "tx_hash": "pending_kraken_payout",
        "message": "Your payout is being processed. Anthony will send the XMR to your wallet from Kraken within 24 hours.",
    }


# ═══════════════════════════════════════════════════════════════
#  ADMIN ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.get("/api/admin/panel")
async def api_admin_panel(request: Request):
    """Serve admin panel HTML — only for authenticated admins.
    Prevents admin UI from appearing in public page source."""
    admin = require_admin(request)
    panel_path = Path(__file__).parent / "templates" / "admin_panel.html"
    return HTMLResponse(content=panel_path.read_text(encoding="utf-8"))


@app.get("/api/admin/stats")
async def api_admin_stats(request: Request):
    """Get full pool statistics for admin dashboard."""
    admin = require_admin(request)
    summary = db.get_pool_summary()
    summary["jackpot_current"] = engine.get_jackpot_balance()
    # The card's second line (Oct 26): how much of that pot the daily
    # funding accrual put there versus XMR actually received. Same
    # balance, read twice — the split cannot disagree with the number
    # above it.
    summary["jackpot_split"] = engine.get_jackpot_split()
    summary["pool_stats"] = engine.get_pool_stats()
    return summary


@app.get("/api/admin/referral-meter")
async def api_admin_referral_meter(request: Request):
    """This cycle's referral awards, per referrer against the cap (admin).

    Read-only by design: the meter reports what the award path already did
    — it cannot grant, cap or reset anything from here.
    """
    require_admin(request)
    return db.referral_admin_meter()


# ── THE KRAKEN PAYOUT RECORDER (Oct 25, admin) ─────────────────
# Kraken pays the referral bonus into the AFFILIATE's Kraken account, never
# into the pool, and no Kraken feed tells us a payout happened — the
# operator verifies it and records it here. The pool keeps its share
# (kraken_pool_share_pct, default half) as a funding-ledger receipt; the
# referrer keeps the rest from Kraken directly. Nothing is paid to a user
# out of pool funds by this route.


@app.get("/api/admin/referral/payouts")
async def api_admin_referral_payouts(request: Request):
    """The recorded Kraken payouts + the two referral settings (admin).

    no-store: the card edits these settings, so a heuristically cached
    copy would show stale values and the next save would post them back.
    """
    require_admin(request)
    return JSONResponse(db.kraken_payout_summary(),
                        headers={"Cache-Control": "no-store"})


@app.post("/api/admin/referral/payout")
async def api_admin_referral_payout_record(request: Request):
    """Record ONE verified Kraken referral payout (admin).

    Body: {user_id | username, amount_usd, note?}. Refuses an account that
    was never referred, a non-positive amount, and a second recording for
    the same account in the same month (the ledger row is unique per
    account per month) — a re-run can never inflate the pot.
    """
    admin = require_admin(request)
    body = await _push_body(request)
    target = None
    raw_id = body.get("user_id")
    if raw_id not in (None, ""):
        try:
            target = db.get_user(int(raw_id))
        except (TypeError, ValueError):
            target = None
    elif str(body.get("username") or "").strip():
        target = db.get_user_by_username(str(body["username"]).strip())
    if not target:
        raise HTTPException(status_code=404, detail="no such user")
    try:
        gross = float(body.get("amount_usd"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="amount_usd required")
    result = db.record_kraken_referral_payout(
        target["id"], gross, note=str(body.get("note") or ""))
    status = result.get("status")
    if status == "no_referrer":
        raise HTTPException(
            status_code=409,
            detail=f"'{target['username']}' was not referred by anyone — "
                   f"there is no referrer to attribute the payout to")
    if status == "already":
        raise HTTPException(
            status_code=409,
            detail=f"a payout for '{target['username']}' is already recorded "
                   f"for {result.get('period')} — nothing was credited")
    if status in ("invalid", "no_pool_share"):
        raise HTTPException(status_code=400,
                            detail=f"refused: {status}")
    if status == "ledger_refused":
        raise HTTPException(status_code=502,
                            detail="the funding ledger refused the receipt — "
                                   "nothing was recorded")
    kr.emit_audit(target["id"], "kraken_referral_payout.recorded",
                  by="admin_panel", by_admin=str(admin["username"]),
                  gross_usd=result.get("gross_usd"),
                  pool_usd=result.get("pool_usd"),
                  ledger_id=result.get("ledger_id"))
    return JSONResponse({"ok": True, **result})


@app.post("/api/admin/referral/config")
async def api_admin_referral_config(request: Request):
    """Set the internal programme's XMRJ rate and the Kraken pool share.

    Both are plain operator settings; a blank field keeps the stored value,
    so saving one never zeroes the other by accident.
    """
    admin = require_admin(request)
    body = await _push_body(request)
    changed = {}
    # VALIDATE BOTH FIELDS BEFORE WRITING EITHER (Oct 26): the panel sends
    # one request carrying both values. Writing the rate and then rejecting
    # the share left a half-applied save behind an error message, and the
    # card (re-reading the old share) made that look like nothing changed.
    rate = pct = None
    if body.get("xmrj_award") not in (None, ""):
        try:
            rate = max(0, int(float(body["xmrj_award"])))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail="xmrj_award must be a whole number")
    if body.get("pool_share_pct") not in (None, ""):
        try:
            pct = int(float(body["pool_share_pct"]))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail="pool_share_pct must be a whole number")
        if not 0 <= pct <= 100:
            raise HTTPException(status_code=400,
                                detail="pool_share_pct must be 0-100")
    if rate is None and pct is None:
        raise HTTPException(status_code=400, detail="nothing to change")
    if rate is not None:
        db.set_setting(db.XMRJ_RATE_KEY, str(rate))
        changed["xmrj_award"] = rate
    if pct is not None:
        db.set_setting(db.KRAKEN_SHARE_KEY, str(pct))
        changed["pool_share_pct"] = pct
    kr.emit_audit(None, "referral.config_updated",
                  by_admin=str(admin["username"]), **changed)
    return JSONResponse({"ok": True, **changed,
                         **db.kraken_payout_summary(limit=1)})


@app.get("/api/admin/users")
async def api_admin_users(request: Request):
    """Get all users (admin only)."""
    admin = require_admin(request)
    return db.get_all_users()


@app.get("/api/admin/scout-lifecycle")
async def api_admin_scout_lifecycle(request: Request, days: float | None = None):
    """Phase F fleet report: installed / activated / ever connected /
    submitted shares, with never-connected kept apart from dormant."""
    require_admin(request)
    return scout_lifecycle.report(days=days)


@app.get("/api/admin/miners")
async def api_admin_miners(request: Request):
    """Get all miners (admin only)."""
    admin = require_admin(request)
    return db.get_all_miners()


@app.get("/api/admin/drawings")
async def api_admin_drawings(request: Request):
    """Get all drawings (admin only)."""
    admin = require_admin(request)
    return db.get_recent_drawings(100)


@app.get("/api/admin/payouts")
async def api_admin_payouts(request: Request):
    """Get all payouts (admin only)."""
    admin = require_admin(request)
    with db.get_db() as conn:
        rows = conn.execute(
            """SELECT p.*, m.wallet_address FROM payouts p
            LEFT JOIN miners m ON p.miner_id = m.id
            ORDER BY p.created_at DESC LIMIT 100"""
        ).fetchall()
        return [dict(r) for r in rows]


@app.get("/api/admin/audit")
async def api_admin_audit(request: Request):
    """Get audit log (admin only)."""
    admin = require_admin(request)
    return db.get_audit_logs(100)


@app.get("/api/admin/settings")
async def api_admin_get_settings(request: Request):
    """Get all admin settings."""
    admin = require_admin(request)
    return db.get_all_admin_settings()


@app.post("/api/admin/settings")
async def api_admin_set_settings(req: AdminSettingUpdate, request: Request):
    """Update an admin setting."""
    admin = require_admin(request)
    db.set_admin_setting(req.key, req.value, admin["id"])
    db.add_audit_log(admin["id"], "setting_update", f"{req.key}={req.value}", request.client.host)
    return {"status": "ok"}


@app.post("/api/admin/draw")
async def api_admin_draw(request: Request):
    """Manually trigger a drawing (admin only, nonce-protected)."""
    admin = require_admin(request)
    data = await request.json() if request.headers.get("content-type") == "application/json" else {}
    nonce = data.get("nonce", "")
    if not nonce or not security.draw_nonce_store.consume(nonce):
        raise HTTPException(400, "Invalid or reused nonce. Refresh to get a new one.")
    result = engine.run_drawing()
    db.add_audit_log(admin["id"], "manual_draw", f"Drawing triggered", request.client.host)
    return result


@app.post("/api/admin/payout/{payout_id}/status")
async def api_admin_update_payout(payout_id: int, status: str, tx_hash: str = "", request: Request = None):
    """Update payout status (admin only)."""
    admin = require_admin(request)
    db.update_payout_status(payout_id, status, tx_hash if tx_hash else None)
    db.add_audit_log(admin["id"], "payout_update", f"Payout #{payout_id} -> {status}", request.client.host)
    return {"status": "ok"}


@app.get("/api/admin/cold-wallet")
async def api_admin_cold_wallet(request: Request):
    """THE COLD-WALLET VIEW (user order, Sep 25 evening VI): the
    operator's money picture on one admin card - the pool wallet's own
    balance (monero-wallet-rpc), the Kraken total (every house-tax
    payout sent to the Kraken deposit address, from the payouts
    ledger), and the house fees collected (accumulated + paid
    lifetime). Read-only, admin-gated, no secrets returned."""
    require_admin(request)
    # 1. the pool wallet's own balance (cold wallet on the dashboard)
    rpc_ok = False
    wallet_balance = 0.0
    wallet_unlocked = 0.0
    wallet_address_setting = db.get_setting("house_wallet", "")
    try:
        if xmr.rpc_available():
            rpc_ok = True
            bal = xmr.get_balance() or {}
            wallet_balance = float(bal.get("balance", 0) or 0)
            wallet_unlocked = float(bal.get("unlocked_balance", 0) or 0)
    except Exception:
        rpc_ok = False
    # 2. the Kraken total: house-tax payouts actually sent to the
    #    Kraken deposit address (ledger truth, not vibes)
    kraken_sent = 0.0
    kraken_pending = 0.0
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT status, amount_xmr FROM payouts WHERE payout_type = 'house_tax'"
        ).fetchall()
    for r in rows:
        if r["status"] == "sent":
            kraken_sent += float(r["amount_xmr"] or 0)
        else:
            kraken_pending += float(r["amount_xmr"] or 0)
    # 3. house fees collected: accumulated (not yet sent) + lifetime sent
    accumulated = float(db.get_setting("house_tax_accumulated", "0") or 0)
    house_fees_collected = round(accumulated + kraken_sent + kraken_pending, 12)
    # on-chain confirmation of the Kraken total, when RPC is up: sum
    # outgoing transfers to the configured Kraken deposit address
    kraken_onchain = None
    kraken_addr = db.get_setting("kraken_deposit_address", "")
    if rpc_ok and kraken_addr:
        try:
            transfers = xmr.get_transfers(min_height=0, in_only=False) or {}
            out = transfers.get("out", []) or []
            kraken_onchain = round(sum(
                float(t.get("amount", 0) or 0) / 1e12
                for t in out
                if any(
                    str(d.get("address", "")) == kraken_addr
                    for d in (t.get("destinations") or [])
                )
            ), 12) or 0.0
        except Exception:
            kraken_onchain = None
    return {
        "rpc_available": rpc_ok,
        "wallet_address_masked": (wallet_address_setting[:8] + "..." + wallet_address_setting[-6:])
                                 if len(wallet_address_setting) > 20 else wallet_address_setting,
        "wallet_balance": round(wallet_balance, 12),
        "wallet_unlocked": round(wallet_unlocked, 12),
        "kraken_total_sent": round(kraken_sent, 12),
        "kraken_pending": round(kraken_pending, 12),
        "kraken_onchain_total": kraken_onchain,
        "house_fees_accumulated": round(accumulated, 12),
        "house_fees_collected_total": house_fees_collected,
    }


@app.get("/api/admin/wallet-status")
async def api_admin_wallet_status(request: Request):
    """Check monero-wallet-rpc status (admin only)."""
    admin = require_admin(request)
    available = xmr.rpc_available()
    result = {"rpc_available": available}
    if available:
        balance = xmr.get_balance()
        height = xmr.get_height()
        result["balance"] = balance["balance"] if balance else 0
        result["unlocked_balance"] = balance["unlocked_balance"] if balance else 0
        result["height"] = height
    return result


# ─────────────────────────────────────────────────────────────
#  KRAKEN REFERRAL PROJECTION (Sep 26, user charter)
#  Read model for the "Possible future jackpot" card. The worker
#  lives in kraken_referral.py; these handlers only gate and read —
#  except the operator trigger, which runs the pipeline inline and
#  returns its run summary for the canary report.
# ─────────────────────────────────────────────────────────────
import kraken_referral as kr
import push_report
import scheduler as kr_sched


@app.get("/api/kraken/rewards-today")
async def api_kraken_rewards_today():
    """Public, read-only figure for the hero referral block's
    'rewards claimed today' counter: how many referral bonuses the pool
    RECORDED into the funding ledger in the trailing 24h (and their USD
    sum). Plain ledger read — no Kraken scraping, no estimate posing
    as a fact; an unreadable ledger degrades to zeros, never lies."""
    try:
        stats = jackpot_funding.referral_rewards_last24h()
        claimed = int(stats.get("count", 0))
        total_usd = round(float(stats.get("total_usd", 0.0)), 2)
    except Exception:
        claimed, total_usd = 0, 0.0
    return {"rewards_claimed": max(0, claimed), "total_usd": total_usd}


@app.get("/api/jackpot/projection")
async def api_jackpot_projection():
    """Public, read-only projection read: current jackpot (real,
    unchanged), projected referral seed (estimate), and the sum.
    TTL/staleness is surfaced, never hidden; when no projection has
    been persisted yet the API answers 200 with source 'none' and
    the card renders current-only."""
    # THE TABLE IS AN ADVERTISEMENT AND THIS IS ITS ONE PUBLIC DOOR
    # (Oct 26): projection_ad.read_estimate takes an AdSlot and refuses
    # outright when an admin URL is being served. A forbidden read is a
    # programming error, not "no data" — it must never be swallowed into
    # the current-only response below.
    try:
        est = projection_ad.read_estimate(projection_ad.AdSlot.HERO)
    except projection_ad.AdminReadForbidden:
        raise
    except Exception:
        est = None
    current = engine.get_pool_stats()
    cur_usd = round(float(current.get("jackpot_usd") or 0.0), 2)
    # THE CONVERSION IS DISCLOSED (Oct 26): every USD on this card was
    # converted at this rate, and the descriptor says whose rate it is,
    # how old, and whether it is the fallback. Same object /api/stats
    # serves, so the hero plate and this card can never label differently.
    rate_info = (current.get("xmr_usd_rate_info")
                 or jackpot_funding.rate_info())
    rate_num = round(float(rate_info.get("rate") or 0.0), 2)
    if not est:
        return JSONResponse({
            "possible_future_jackpot_usd": cur_usd,
            "current_jackpot_usd": cur_usd,
            "referral_seed_usd": 0.0,
            "count_of_users_included": 0,
            "updated_at": None, "source": "none",
            "mode": kr.provider_mode(), "fresh": False, "anomaly": 0,
            "xmr_usd_rate": rate_num,
            "xmr_usd_rate_info": rate_info})
    # THE CURRENT LEG IS READ LIVE (Oct 26): the row is an hourly
    # SNAPSHOT converted at whatever rate the worker ran under, and the
    # pot moves every block. Echoing the snapshot's "current" beside a
    # live hero plate showed two different pots on one page (337 vs 620
    # the day the rate stopped being a literal). The SEED still comes
    # from the row — it is a projection and must not be recomputed per
    # page — and possible is current + seed, the worker's own formula.
    # `Estimate.total_with` has to be HANDED the live pot: this module
    # cannot invent the money half of the sum.
    seed = est.referral_seed_usd
    return JSONResponse({
        "possible_future_jackpot_usd": est.total_with(cur_usd),
        "current_jackpot_usd": cur_usd,
        "referral_seed_usd": seed,
        "count_of_users_included": est.count_of_users,
        "updated_at": est.updated_at,
        "source": est.source, "mode": kr.provider_mode(),
        "fresh": est.fresh,
        "anomaly": est.anomaly,
        "xmr_usd_rate": rate_num,
        "xmr_usd_rate_info": rate_info})


@app.post("/api/admin/kraken-referral/run")
async def api_admin_kraken_referral_run(request: Request):
    """OPERATOR trigger for the referral-projection worker (admin
    session required). In canary this is the only sanctioned live-mode
    entry point; it refuses (503) when no provider is configured, so
    the operator trigger never fabricates numbers. Optional JSON body:
    {"subset_limit": N} caps the run at the first N mapped users."""
    require_admin(request)
    body = await _push_body(request)
    subset = int(body.get("subset_limit") or 0) or None
    if kr.provider_mode() == "unconfigured":
        raise HTTPException(
            status_code=503,
            detail="Kraken referral API not configured: provision "
                   "KRAKEN_API_KEY / KRAKEN_API_SECRET (settings, or "
                   "the OS keyring where setup_kraken.py stores them) "
                   "AND KRAKEN_REFERRAL_ENDPOINT in settings (or "
                   "KRAKEN_REFERRAL_MODE=simulate for the "
                   "clearly-labeled simulation)")
    # OFF THE EVENT LOOP (review batch, Sep 27 major): run_worker
    # dials Kraken synchronously - an admin trigger must not freeze
    # every other request for the duration of the HTTP call.
    summary = await asyncio.to_thread(kr.run_worker,
                                      subset_limit=subset)
    return JSONResponse(summary)


@app.get("/api/admin/kraken-referral/config")
async def api_admin_kraken_referral_config(request: Request, verify: bool = False):
    """Admin read of the referral-provider configuration. Secrets are
    NEVER returned — only their last four characters and whether they
    are set, so the form can show state without re-exposing material."""
    require_admin(request)
    if verify:
        # the operator's explicit one-shot proof: the SAME self-test the
        # key-check endpoint runs (one 30s-throttled Budget call, verdict
        # stored + audited) — 429-shaped, then echoed back below via
        # last_credential_check(). A plain GET never touches the network.
        # off the event loop (Sep 27 review major: one signed
        # Budget call must not block the whole server)
        v = await asyncio.to_thread(kr.credential_selftest)
        if v.get("verdict") == "throttled":
            raise HTTPException(status_code=429, detail=v.get("detail", ""))
    def _tail(name):
        # TIER-AWARE MASK (Sep 26): the credential may live in settings
        # (SEALED at rest since the Sep 27 review fix - the raw setting
        # is a field_crypto envelope) or in the OS keyring (Windows
        # Credential Manager, via setup_kraken.py) — the card reports
        # WHICH tier holds it and still only ever shows the last four
        # characters. The tail comes from the UNWRAPPED value via the
        # shared read seam (the raw envelope's tail is not the secret's
        # tail - the fleet's 8.4 caught exactly that).
        v = kr._read_secret(name)
        if v:
            return {"set": True, "tail": "…" + v[-4:], "source": "settings"}
        key, sec, src = kr.kraken_credentials()
        held = (key if name == "KRAKEN_API_KEY" else sec)
        if held:
            return {"set": True, "tail": "…" + held[-4:], "source": src}
        return {"set": False, "tail": "", "source": ""}
    return JSONResponse({
        "api_key": _tail("KRAKEN_API_KEY"),
        "api_secret": _tail("KRAKEN_API_SECRET"),
        "endpoint": str(db.get_setting("KRAKEN_REFERRAL_ENDPOINT", "") or ""),
        "mode": kr.provider_mode(),
        "simulate_flag": (db.get_setting("KRAKEN_REFERRAL_MODE", "") or "")
                         == "simulate",
        "ttl_seconds": kr._ttl(),
        "batch_size": kr._cfg(kr.PROJECTION_BATCH_KEY, 25, int),
        "confidence": kr._confidence(),
        "spike_pct": kr._cfg(kr.PROJECTION_SPIKE_KEY, 25.0, float),
        "scheduler": kr_sched.status(),
        # THE POSTURE, VERBATIM (Sep 26): the same dict setup_kraken.py
        # prints — one source of truth, so the admin panel and the
        # installer tool can never disagree about the provider state
        "posture": kr.provider_posture(),
        # the stored LAST self-test verdict (no dial). verify=true is
        # the operator's explicit one-shot proof: same 30s throttle as
        # the key-check endpoint (429-shaped), one fresh Balance call,
        # verdict stored + audited, then echoed back here.
        "credential_check": kr.last_credential_check(),
    })


@app.post("/api/admin/kraken-referral/config")
async def api_admin_kraken_referral_config_set(request: Request):
    """Admin write of the provider config. Secrets are write-only:
    a blank field leaves the stored value untouched (the OS keyring
    is NEVER written here — tiered storage stays where the operator
    put it); anything typed is stored verbatim (the response only
    ever echoes masked tails)."""
    require_admin(request)
    body = await _push_body(request)
    updates = []
    for name in ("KRAKEN_API_KEY", "KRAKEN_API_SECRET"):
        # Accept both the canonical (kraken_api_key) and short (api_key)
        # field spellings so panel and API clients never diverge.
        v = str(body.get(name.lower())
                or body.get(name.split("_", 1)[1].lower()) or "").strip()
        if v:
            # SEALED AT REST (review batch, Sep 27 major): settings-tier
            # secrets are stored as field_crypto envelopes - the same
            # AEAD-at-rest discipline as every other credential in the
            # users table. The readers unwrap-or-passthrough, so legacy
            # plaintext values stay readable until the next write
            # re-seals them, and the keyring tier is untouched (it was
            # never plaintext-at-rest in the DB).
            db.set_setting(name, _fc.encrypt(v) if _fc.available() else v)
            updates.append(name)
    ep = str(body.get("endpoint") or "").strip()
    if ep:
        if not ep.startswith(("http://", "https://")) or len(ep) > 300:
            raise HTTPException(status_code=400,
                                detail="endpoint must be an http(s) URL")
        db.set_setting("KRAKEN_REFERRAL_ENDPOINT", ep)
        updates.append("KRAKEN_REFERRAL_ENDPOINT")
    elif body.get("clear_endpoint"):
        db.set_setting("KRAKEN_REFERRAL_ENDPOINT", "")
        updates.append("KRAKEN_REFERRAL_ENDPOINT (cleared)")
    sim = body.get("simulate")
    if sim is not None:
        db.set_setting("KRAKEN_REFERRAL_MODE",
                       "simulate" if sim else "")
        updates.append("KRAKEN_REFERRAL_MODE="
                       + ("simulate" if sim else "live-or-unconfigured"))
    # THE SCHEDULER + EXPIRY CALENDAR (user flow, Sep 26): enable/disable
    # and interval ride the same admin surface; the expiry is the
    # operator-defined calendar the notice leg obeys.
    if "scheduler_enabled" in body:
        db.set_setting(kr_sched.ENABLED_KEY,
                       "1" if body.get("scheduler_enabled") else "0")
        updates.append("SCHEDULER_ENABLED="
                       + ("1" if body.get("scheduler_enabled") else "0"))
    if "interval_s" in body:
        try:
            iv = int(body.get("interval_s"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail="interval_s must be an integer >= 300")
        if iv < 300:
            raise HTTPException(status_code=400,
                                detail="interval_s must be an integer >= 300")
        db.set_setting(kr_sched.INTERVAL_KEY, str(iv))
        updates.append("SCHEDULER_INTERVAL_S=" + str(iv))
    if "clear_window" in body and body.get("clear_window"):
        db.set_setting(kr_sched.WINDOW_KEY, "")
        updates.append("KRAKEN_REFERRAL_WINDOW (cleared)")
    elif "expires_at" in body:
        try:
            exp = float(body.get("expires_at"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail="expires_at must be epoch seconds")
        if exp <= 0:
            raise HTTPException(status_code=400,
                                detail="expires_at must be in the future")
        db.set_setting(kr_sched.WINDOW_KEY,
                       json.dumps({"expires_at": exp}))
        updates.append("KRAKEN_REFERRAL_WINDOW="
                       + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exp)))
    kr.emit_audit(None, "kraken_referral.config_updated",
                  updated=updates, by="admin_panel")
    if any(u.startswith("SCHEDULER_ENABLED=") for u in updates):
        if kr_sched._enabled():
            kr_sched.start()
            updates.append("scheduler thread started")
        else:
            kr_sched.stop()
            updates.append("scheduler thread stopped")
    return JSONResponse({"ok": True, "updated": updates,
                         "mode": kr.provider_mode()})


@app.get("/api/admin/kraken-referral/mappings")
async def api_admin_kraken_referral_mappings(request: Request):
    """THE MAPPING SHEET (user order, Sep 26): every account with its
    attached Kraken referral id (or the gap the operator must fill).
    Email is masked to its local-part tail — PII discipline."""
    require_admin(request)
    def _mask_mail(m):
        m = str(m or "")
        if "@" not in m:
            return m[:4] + "…" if m else ""
        local, _, dom = m.partition("@")
        return (local[:2] + "…" + local[-2:] + "@" + dom) if len(local) > 4 \
            else "…@" + dom
    users = db.get_all_users()
    mapped = sum(1 for u in users if (u.get("kraken_referral_id") or "").strip())
    return JSONResponse({
        "total": len(users),
        "mapped": mapped,
        "users": [{"id": u["id"], "username": u["username"],
                   "email_masked": _mask_mail(u.get("email")),
                   "kraken_referral_id": (u.get("kraken_referral_id") or "").strip()}
                  for u in users]})


@app.post("/api/admin/kraken-referral/mappings")
async def api_admin_kraken_referral_mapping_set(request: Request):
    """Attach, replace, or clear one account's Kraken referral id.
    Refuses to orphan a stored id to a DIFFERENT user (Kraken ids are
    unique per referred account), and every change is audited."""
    admin = require_admin(request)
    body = await _push_body(request)
    try:
        user_id = int(body.get("user_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="user_id required")
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="no such user")
    rid = str(body.get("kraken_referral_id") or "").strip().upper()
    if len(rid) > 120:
        raise HTTPException(status_code=400, detail="referral id too long")
    if rid:
        with db.get_db() as conn:
            clash = conn.execute(
                "SELECT id, username FROM users WHERE kraken_referral_id = ? "
                "AND id != ?", (rid, user_id)).fetchone()
        if clash:
            raise HTTPException(
                status_code=409,
                detail=f"id already mapped to user '{clash['username']}' "
                       f"(id {clash['id']}) — clear it there first")
    db.set_kraken_referral_id(user_id, rid)
    action = "kraken_referral.mapped" if rid else "kraken_referral.unmapped_manual"
    kr.emit_audit(user_id, action,
                  kraken_referral_id=rid or None,
                  by="admin_panel", by_admin=str(admin["username"]))
    return JSONResponse({"ok": True, "user_id": user_id,
                         "username": target["username"],
                         "kraken_referral_id": rid,
                         "action": action})


@app.get("/api/admin/push/vapid")
async def api_admin_push_vapid(request: Request):
    """Admin read of the VAPID posture (no key material — tails only,
    exactly like the referral secrets)."""
    require_admin(request)
    st = push_svc.vapid_status()
    st["library_ok"] = True
    try:
        import py_vapid  # noqa: F401
    except Exception:
        st["library_ok"] = False
    return JSONResponse(st)


@app.post("/api/admin/push/vapid")
async def api_admin_push_vapid_act(request: Request):
    """THE PROVISIONING LEG (user flow, Sep 26): ensure (create the
    pair when none exists), rotate (deliberate, audited, keeps the old
    key for a re-subscribe campaign), or test (one real push through
    the sender to the calling admin's own devices — the same path a
    phone will see)."""
    admin = require_admin(request)
    body = await _push_body(request)
    action = str(body.get("action") or "ensure")
    if action == "ensure":
        res = push_svc.vapid_ensure()
    elif action == "rotate":
        res = push_svc.vapid_rotate()
    elif action == "test":
        if not push_svc.vapid_status()["provisioned"]:
            res = {"ok": False,
                   "reason": "VAPID keys not provisioned — ensure first"}
        else:
            res = push_svc.send_to_user(int(admin["id"]), "push_test")
    else:
        raise HTTPException(status_code=400,
                            detail="action must be ensure|rotate|test")
    kr.emit_audit(None, "kraken_referral.vapid_action",
                  vapid_action=action, ok=bool(res.get("ok")),
                  source=res.get("source"), sent=res.get("sent"),
                  reason=res.get("reason"))
    return JSONResponse(res)


@app.get("/api/admin/push/decisions")
async def api_admin_push_decisions(request: Request):
    """THE SUPPRESSION/SEND LEDGER (user order, Sep 26): the raw
    push decision rows — send_allowed / send_blocked / rate_limited /
    broadcast / subscribe / unsubscribe — newest first, filterable.
    Usernames only (never emails/phones); details JSON parsed server-
    side so the client renders fixed fields."""
    require_admin(request)
    qp = request.query_params
    try:
        limit = max(1, min(200, int(qp.get("limit", "50"))))
    except ValueError:
        limit = 50
    action = str(qp.get("action") or "")            # exact action or ""
    category = str(qp.get("category") or "")        # jackpot|scout|""
    try:
        user_id = int(qp.get("user_id")) if qp.get("user_id") else None
    except ValueError:
        user_id = None
    # user 0 is the BROADCAST pseudo-user in push_send rows; a filter
    # value of 0 must mean "broadcast rows", not "no filter"
    if qp.get("user_id") == "0":
        user_id = 0
    allowed_actions = {"push_send_allowed", "push_send_blocked",
                       "push_rate_limited", "push_broadcast",
                       "push_subscribe", "push_unsubscribe"}
    where = ["a.action LIKE 'push_%'"]
    params = []
    if action in allowed_actions:
        where.append("a.action = ?")
        params.append(action)
    if user_id is not None:
        where.append("a.user_id = ?")
        params.append(user_id)
    where_sql = " AND ".join(where)
    with db.get_db() as conn:
        rows = conn.execute(
            f"SELECT a.id, a.user_id, a.action, a.details, a.created_at, "
            f"u.username FROM audit_log a "
            f"LEFT JOIN users u ON a.user_id = u.id "
            f"WHERE {where_sql} ORDER BY a.created_at DESC LIMIT ?",
            (*params, limit)).fetchall()
    out = []
    for r in rows:
        try:
            d = json.loads(r["details"] or "{}")
        except ValueError:
            d = {}
        item = {"id": r["id"], "username": r["username"] or
                (str(r["user_id"]) if r["user_id"] else "broadcast"),
                "action": r["action"], "ts": r["created_at"],
                "event": str(d.get("event") or "")}
        cat = d.get("category")
        if cat:
            item["category"] = cat
        for k in ("reason", "sent", "dead", "scope", "endpoint_host"):
            if d.get(k) is not None:
                item[k] = d[k]
        if category and item.get("category") != category:
            continue
        out.append(item)
    return JSONResponse({"decisions": out, "count": len(out),
                         "filters": {"action": action,
                                     "category": category,
                                     "user_id": user_id,
                                     "limit": limit}})


@app.get("/api/admin/push/report")
async def api_admin_push_report(request: Request):
    """THE DAILY PUSH REPORT (user order, Sep 26): sends, blocks,
    rate-limits per category, from the audit trail. `days` (1-30)
    controls history depth; today's unfinished day comes as a live
    overlay that is never persisted."""
    require_admin(request)
    try:
        days = max(1, min(30, int(request.query_params.get("days", "1"))))
    except ValueError:
        days = 1
    return JSONResponse(push_report.get_range(days=days))


@app.get("/api/admin/scheduler/status")
async def api_admin_scheduler_status(request: Request):
    require_admin(request)
    return JSONResponse(kr_sched.status())


@app.post("/api/admin/scheduler/tick")
async def api_admin_scheduler_tick(request: Request):
    """Operator trigger for ONE scheduler cycle (the exact code the
    timer runs): worker leg per its interval/unconfigured gates, then
    the notice leg per the expiry calendar. Tests use tick_once
    directly; this route is the production-equivalent probe."""
    require_admin(request)
    return JSONResponse(kr_sched.tick_once(reason="admin_manual"))


@app.post("/api/admin/kraken-referral/test")
async def api_admin_kraken_referral_test(request: Request):
    """THE CONNECTION TEST: one live fetch through the configured
    provider for the referral id the operator supplies, returning the
    RAW provider response (truncated) so the operator sees exactly
    what Kraken answers — no interpretation, no masking of Kraken's
    own fields, secrets never included. Diagnoses the failure mode
    (auth vs schema vs transport) instead of a bare error string."""
    require_admin(request)
    body = await _push_body(request)
    rid = str(body.get("referral_id") or "").strip()
    if not rid:
        raise HTTPException(status_code=400,
                            detail="referral_id required")
    if kr.provider_mode() == "unconfigured":
        return JSONResponse({"ok": False, "stage": "config",
                             "error": "no KRAKEN_API_KEY / KRAKEN_API_SECRET "
                                      "/ KRAKEN_REFERRAL_ENDPOINT provisioned"})
    mode = kr.provider_mode()
    # off the event loop (Sep 27 review major)
    res = await asyncio.to_thread(kr.fetch_user_status, 0, rid)  # user_id 0: read-only probe
    out = {"ok": bool(res.get("ok")), "mode": mode,
           "stage": "fetch" if res.get("ok") else "provider"}
    if res.get("ok"):
        raw = json.dumps(res.get("raw"), default=str)
        out["raw_response"] = raw[:1200]
        out["raw_truncated"] = len(raw) > 1200
        rid_n, status, total_paid, pending, explicit, _lu = \
            kr.normalize_kraken_response(res["raw"])
        cons, best, est = kr.compute_expected_bonus(
            status, total_paid, pending, explicit)
        out["normalized"] = {"referral_id": rid_n, "status": status,
                             "total_paid": total_paid,
                             "pending_amount": pending,
                             "expected_bonus_conservative": cons,
                             "expected_bonus_best": best,
                             "estimate_used": bool(est)}
    else:
        err = str(res.get("error") or "")
        out["error"] = err
        if mode == "simulate":
            out["hint"] = ("provider is in SIMULATE mode — clear "
                           "KRAKEN_REFERRAL_MODE to go live")
        elif "InvalidKey" in err or "Invalid API" in err:
            out["hint"] = "Kraken rejected the key — check API-Key/API-Secret"
        elif "Rate" in err:
            out["hint"] = "rate limited — wait a moment and retry"
        elif res.get("reason") == "fetch_failed":
            out["hint"] = ("transport/schema failure — verify the endpoint "
                           "URL and that it accepts POST with "
                           "API-Key/API-Sign headers")
    kr.emit_audit(None, "kraken_referral.connection_test",
                  referral_id=rid, ok=out["ok"], mode=mode)
    return JSONResponse(out)


_KC_MIN_INTERVAL_S = kr.KC_MIN_INTERVAL_S
                               # compatibility alias (the self-test owns
                               # the real knob)
_KC_STATE = kr.KC_STATE        # THE SHARED THROTTLE: the key-check endpoint
                               # and any verify=True posture read budget the
                               # same 30s — one probe site, one rate spend


@app.post("/api/admin/kraken-referral/key-check")
async def api_admin_kraken_referral_key_check(request: Request):
    """THE KEY CHECK (Sep 26, operator-only): ONE signed read-only
    Balance call through kr.credential_selftest — the single probe
    site shared with the posture's opt-in verify path, so the endpoint
    and the posture spend the SAME throttle and store ONE verdict.
    Four honest verdicts: valid / invalid (Kraken's own EAPI error
    verbatim, truncated) / unreachable (transport, with the error) /
    unconfigured (no complete pair in any tier). Tails only; the
    secret NEVER leaves the credential accessor. Throttled to one call
    per 30s per process (429-shaped) so button-mashing cannot burn
    Kraken's rate credit. Audited like every operator action."""
    require_admin(request)
    # off the event loop (Sep 27 review major)
    out = await asyncio.to_thread(kr.credential_selftest)
    if out.get("verdict") == "throttled":
        raise HTTPException(status_code=429, detail=out.get("detail", ""))
    return JSONResponse(out)


# ─────────────────────────────────────────────────────────────
#  NATIVE PUSH (xmrjackpot.com's own web push service)
#  Thin route layer over pool/push_service.py — one brain per
#  layer: the service owns subscriptions, templates, rate limits,
#  audits and VAPID; these handlers only authenticate and forward.
#  Browser leg: the session cookie — a user subscribes only their
#  own devices. Machine leg: the operator token — Kraken Scout
#  emits charter events (jackpot win, signals) from code.
#  Wired Sep 25 when this file freed up; the handlers are the
#  verbatim ones push_service.py exposed for exactly this moment.
# ─────────────────────────────────────────────────────────────
import push_service as push_svc


async def _push_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    return body if isinstance(body, dict) else {}


def _operator_or_401(request: Request):
    """Machine auth for the event route: the operator token from
    settings, compared with secrets.compare_digest (never ==)."""
    supplied = request.headers.get("X-Operator-Token", "")
    stored = str(db.get_setting("PUSH_OPERATOR_TOKEN", "") or "")
    if not stored or not supplied or \
            not secrets.compare_digest(supplied, stored):
        raise HTTPException(status_code=401, detail="operator token required")
    return True


@app.get("/api/push/public-key")
async def api_push_public_key():
    """The VAPID public key for browser subscribe() calls. Empty
    until provisioned (the browser client degrades quietly)."""
    return JSONResponse(push_svc.handle_public_key())


@app.get("/push_sw.js", include_in_schema=False)
async def push_service_worker():
    """The push service worker served from the ROOT path: a worker's
    default scope is its script's directory, so /static/push_sw.js
    scoped itself to /static/ and could never control pages at / —
    the live end-to-end proof caught the Enable button subscribing
    under a scope that could not notify (e2e proof, Sep 25). From
    the root, the default scope is / — no Service-Worker-Allowed
    dance needed; the header is set anyway for belt-and-suspenders.
    (The file itself still lives in static/; only its URL moved.)"""
    path = Path(__file__).parent / "static" / "push_sw.js"
    return Response(content=path.read_text(encoding="utf-8"),
                    media_type="text/javascript",
                    headers={"Service-Worker-Allowed": "/",
                             "Cache-Control": "no-cache"})


@app.post("/api/push/subscribe")
async def api_push_subscribe(request: Request):
    user = get_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    return JSONResponse(
        push_svc.handle_subscribe(int(user["id"]), await _push_body(request)))


@app.post("/api/push/unsubscribe")
async def api_push_unsubscribe(request: Request):
    user = get_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    return JSONResponse(
        push_svc.handle_unsubscribe(int(user["id"]), await _push_body(request)))


@app.get("/api/push/preferences")
async def api_push_prefs_get(request: Request):
    """The user's notification preferences, in the envelope contract:
    {"user_id": "<username>", "preferences": {"jackpot": b, "scout": b},
    "updated_at": "<UTC Z stamp>|null"}. user_id is ECHOED from the
    authenticated session - never accepted from the wire."""
    user = require_auth(request)
    prefs, updated = push_svc.get_prefs_updated(int(user["id"]))
    # OPTIMISTIC CONCURRENCY (Sep 26 E2E acceptance): updated_at is the
    # version - served as both the ETag header and the body field.
    etag = f'"{updated or "v0"}"'
    inm = request.headers.get("if-none-match")
    if inm and inm == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse({
        "user_id": user.get("username"),
        "preferences": prefs,
        "updated_at": updated,
    }, headers={"ETag": etag})


@app.post("/api/push/preferences")
async def api_push_prefs_set(request: Request):
    """Set notification preferences from the dashboard panel. Accepts
    BOTH shapes: the bulk envelope {"preferences": {...}} and the
    single toggle {"category": ..., "enabled": ...}. The authenticated
    user is always the subject - a user_id on the wire is ignored.
    CONCURRENCY: send If-Match: "<updated_at>" to require the write to
    apply to the version you read - a stale version answers 412 with
    the current envelope, so two devices cannot silently clobber each
    other (lost-update prevention, Sep 26 E2E acceptance)."""
    user = require_auth(request)
    if_match = request.headers.get("if-match")
    # ATOMIC CONCURRENCY (Sep 26, the simultaneous race): the version
    # check moved INTO the store writers (set_prefs/set_pref accept
    # if_match and hold the prefs lock across check + write), so two
    # devices firing the same version at the same instant cannot both
    # apply — the awaits between the old pre-check and the write are
    # gone. The handler only shapes the store's conflict answer into
    # the wire 412.
    body = await _push_body(request)
    if isinstance(body.get("preferences"), dict):
        res = push_svc.set_prefs(int(user["id"]), body["preferences"],
                                 if_match=if_match)
    else:
        category = str(body.get("category") or "")
        enabled = body.get("enabled")
        res = push_svc.set_pref(int(user["id"]), category,
                                bool(enabled) if enabled is not None else True,
                                if_match=if_match)
    if res.get("conflict"):
        return JSONResponse({
            "user_id": user.get("username"),
            "preferences": res.get("prefs"),
            "updated_at": res.get("updated_at"),
            "error": "version conflict - re-read and retry",
        }, status_code=412, headers={"ETag": f'"{res.get("updated_at") or "v0"}"'})
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("reason"))
    return JSONResponse({
        "user_id": user.get("username"),
        "preferences": res.get("prefs"),
        "updated_at": res.get("updated_at"),
    })


@app.post("/api/push/test")
async def api_push_test(request: Request):
    """THE PIPE TEST (Sep 26, user request): one REAL push of the
    calling user's chosen family — {"category": "jackpot"|"scout"} —
    through the same sender path a live event takes (opt-out gate,
    rate limiter, audit, VAPID delivery). The user's own toggles are
    honored, not bypassed: an opted-out family answers
    {'ok': False, 'reason': '<category> notifications opted out'} so
    the client can explain WHY nothing landed. The subject is always
    the authenticated session; the sender's verdict is passed through
    verbatim."""
    user = require_auth(request)
    body = await _push_body(request)
    category = str(body.get("category") or "")
    res = push_svc.send_test(int(user["id"]), category)
    if not res.get("ok") and res.get("reason") == "unknown category":
        raise HTTPException(status_code=400,
                            detail="category must be jackpot|scout")
    return JSONResponse(res)


@app.post("/api/push/event")
async def api_push_event(request: Request):
    """The machine leg: scout/push_client.py posts charter events
    here (X-Operator-Token). Events must be one of the service's
    templates; extras are whitelisted inside the sender. No user_id
    rides -> broadcast to every subscriber (signals); a user_id ->
    that user only (jackpot_win, claim reminders)."""
    _operator_or_401(request)
    body = await _push_body(request)
    event = str(body.get("event") or "")
    if event not in push_svc.EVENTS:
        raise HTTPException(status_code=400, detail="unknown event")
    extra = body.get("extra") or {}
    if not isinstance(extra, dict):
        extra = {}
    uid = body.get("user_id")
    if uid is not None:
        try:
            uid = int(uid)
        except (TypeError, ValueError):
            # second triage (Sep 25 evening batch): a MALFORMED target
            # must never degrade into a broadcast — targeted events go
            # to their target or fail loudly
            raise HTTPException(status_code=400,
                                detail="invalid user_id")
    result = (push_svc.send_to_user(uid, event, extra) if uid is not None
              else push_svc.send_to_subscribers(event, extra))
    return JSONResponse({"ok": bool(result.get("sent")), "result": result})


@app.get("/api/push/signals")
async def api_push_signals(request: Request):
    """The Scout Signals dashboard panel's feed: the caller's own
    signal push history, whitelisted to (action, timestamp, title)
    by push_service.scrape_recent_signals — no endpoints, no keys,
    no other user's rows. The whitelist is the guard."""
    user = get_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    # THE SIGNAL GATE (Oct 24): the feed is the paid product's history —
    # a locked account gets an EMPTY list plus the reason, never a row.
    # 200 with a flag (not 403): the dashboard renders an unlock prompt
    # instead of an error, and nothing about the signals leaks either way.
    if not db.has_scout_pro(int(user["id"])):
        return JSONResponse({"signals": [], "scout_pro_required": True})
    return JSONResponse({"signals":
                         push_svc.scrape_recent_signals(int(user["id"]))})


# ─────────────────────────────────────────────────────────────
#  KRAKEN SCOUT CONSOLE PROXY (admin only)
#  Reverse-proxies the local Streamlit trading console
#  (http://127.0.0.1:8501) under /scout/ so it can be viewed
#  remotely from the Admin panel through the Cloudflare tunnel.
#  Every route — HTTP and WebSocket — requires an admin session.
# ─────────────────────────────────────────────────────────────

SCOUT_UPSTREAM = os.environ.get("SCOUT_UPSTREAM", "http://127.0.0.1:8501")
_SCOUT_TIMEOUT = aiohttp.ClientTimeout(total=30)
_SCOUT_MAX_WS_MSG = 200 * 1024 * 1024  # mirror Streamlit's default max message size
_SCOUT_WS_IDLE_S = 75        # client silent 2.5 heartbeats -> dead browser, tear down
_SCOUT_WS_UPSTREAM_IDLE_S = 120  # upstream silent 2 min -> dead console, tear down
_SCOUT_WS_MAX_LIVE = 16      # concurrent scout websockets (retrying tabs stack up;
                             # 16 leaves room for fresh tabs to always get in)
_scout_ws_live = {"n": 0}    # active relay counter

# Response headers never forwarded from the Streamlit upstream
_SCOUT_DROP_RESP_HEADERS = {
    "content-length", "transfer-encoding", "connection", "keep-alive",
    "content-encoding", "set-cookie", "server", "date",
    "x-frame-options", "content-security-policy",
    "access-control-allow-origin", "access-control-allow-credentials",
}


_SCOUT_LOGIN_PAGE = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kraken Scout — Login Required</title>
<style>
  body{font-family:system-ui,sans-serif;background:#11111e;color:#e0e0e0;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:16px;}
  .box{background:#1a1a2e;border:2px solid #ffd700;border-radius:12px;padding:32px;max-width:400px;text-align:center;}
  h1{color:#ffd700;font-size:1.3em;margin:0 0 12px;}
  p{font-size:0.95em;line-height:1.6;margin:0 0 20px;}
  a.btn{display:inline-block;background:linear-gradient(135deg,#8B6914,#B8860B);color:#fff;text-decoration:none;font-weight:700;padding:12px 28px;border-radius:8px;border:2px solid #ffd700;}
  .hint{font-size:0.8em;color:#888;margin-top:16px;}
</style>
</head><body>
<div class="box">
  <h1>⚡ Kraken Scout Console</h1>
  <p>Admin login required to view the live trading console.</p>
  <a class="btn" href="/#admin">Log in on the site →</a>
  <div class="hint">Tap the button, log in with your admin account,<br>then come back to this page.</div>
</div>
</body></html>"""


def _scout_admin_or_403(request: Request):
    """Admin gate for scout HTTP endpoints."""
    user = get_user_from_request(request)
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _scout_admin_or_login_page(request: Request):
    """Admin gate for the scout console pages — shows a friendly login
    redirect page instead of a raw 403, so mobile users can log in."""
    user = get_user_from_request(request)
    if not user or not user.get("is_admin"):
        return None
    return user


def _scout_admin_ws(ws: WebSocket):
    """Admin gate for the scout websocket (session cookie)."""
    token = ws.cookies.get("session")
    if not token:
        return None
    session = db.get_session(token)
    if not session:
        return None
    user = db.get_user(session["user_id"])
    if not user or not user.get("is_admin"):
        return None
    return user


@app.get("/api/scout/health")
async def api_scout_health(request: Request):
    """Is the Streamlit console process reachable?"""
    _scout_admin_or_403(request)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as s:
            async with s.get(f"{SCOUT_UPSTREAM}/_stcore/health") as r:
                return {"up": r.status == 200}
    except Exception:
        return {"up": False}


# ─── Scout identity + jackpot-cycle surfaces (Oct 17) ──────────────────────
# Mounted from pool/scout_endpoints.py so server.py stays focused.
import pool.scout_endpoints as _scout_ep
_scout_ep.register_scout_endpoints(app)



@app.get("/scout", include_in_schema=False)
async def scout_index_redirect():
    """Trailing slash matters for Streamlit's relative asset URLs."""
    return RedirectResponse(url="/scout/", status_code=307)


@app.api_route(
    "/scout/{rest_of_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def scout_proxy(rest_of_path: str, request: Request):
    """Reverse-proxy the local Streamlit console behind admin auth."""
    # Static assets (js/css/fonts) are classified FIRST (review triage,
    # Sep 27): the old order answered every non-admin request with the
    # friendly login HTML, so an asset request got content-type
    # text/html and the plain-403 branch below was dead code.
    is_asset = (
        rest_of_path.startswith("static/")
        or rest_of_path.startswith("_stcore/")
        or rest_of_path.endswith((".js", ".css", ".png", ".woff2", ".ttf", ".map", ".ico"))
    )
    if is_asset:
        _scout_admin_or_403(request)

    user = _scout_admin_or_login_page(request)
    if user is None:
        # Not logged in (or not admin) — show friendly login redirect page
        return HTMLResponse(content=_SCOUT_LOGIN_PAGE, status_code=403)

    target = f"{SCOUT_UPSTREAM}/{rest_of_path}"
    if request.url.query:
        target += f"?{request.url.query}"

    # Look like a direct local call so Streamlit's host/origin
    # checks pass; never forward the admin session cookie upstream.
    fwd_headers = {
        "Host": "127.0.0.1:8501",
        "Accept": request.headers.get("accept", "*/*"),
        "Accept-Encoding": "identity",
    }
    if "content-type" in request.headers:
        fwd_headers["Content-Type"] = request.headers["content-type"]
    if request.headers.get("origin"):
        fwd_headers["Origin"] = SCOUT_UPSTREAM

    body = await request.body()

    try:
        async with aiohttp.ClientSession(timeout=_SCOUT_TIMEOUT) as s:
            async with s.request(
                request.method, target, headers=fwd_headers, data=body,
                allow_redirects=False,
            ) as upstream:
                resp_headers = {
                    k: v for k, v in upstream.headers.items()
                    if k.lower() not in _SCOUT_DROP_RESP_HEADERS
                }
                payload = await upstream.read()
                return Response(
                    content=payload, status_code=upstream.status,
                    headers=resp_headers,
                )
    except aiohttp.ClientConnectorError:
        return HTMLResponse(
            "<h3 style='font-family:sans-serif;'>Scout console is not running "
            "on the server.</h3>",
            status_code=502,
        )


@app.websocket("/scout/_stcore/stream")
async def scout_ws_proxy(ws: WebSocket):
    """Websocket tunnel for the Streamlit console (admin cookie required).

    Zombie guard (Aug 27): the phone browser retries connect in a loop, and
    close-frames from Cloudflare don't always propagate. Without cleanup each
    retry leaked a live upstream session — 167 of them wedged the console.
    Now: idle timeout on both pumps + a hard cap on concurrent relays.

    Subprotocol fix (Aug 27): Streamlit's browser client sends
    'Sec-WebSocket-Protocol' and REQUIRES the server to echo one back —
    without it every real browser failed the handshake with
    "Sent non-empty 'Sec-WebSocket-Protocol' header but no response was
    received" (grey blinking boxes on the phone). curl/aiohttp tests never
    caught it because they don't request a subprotocol. We now forward the
    client's offered protocols upstream and echo back Streamlit's choice.
    """
    if not _scout_admin_ws(ws):
        await ws.close(code=1008)
        return
    if _scout_ws_live["n"] >= _SCOUT_WS_MAX_LIVE:
        await ws.close(code=1013)  # try again later
        return
    _scout_ws_live["n"] += 1

    # Capture the subprotocols the browser offered (comma-separated)
    raw_protos = ws.headers.get("sec-websocket-protocol", "")
    client_protos = [p.strip() for p in raw_protos.split(",") if p.strip()]

    session = aiohttp.ClientSession()
    try:
        upstream = await session.ws_connect(
            f"{SCOUT_UPSTREAM}/_stcore/stream",
            headers={"Host": "127.0.0.1:8501", "Origin": SCOUT_UPSTREAM},
            compress=0,
            max_msg_size=_SCOUT_MAX_WS_MSG,
            protocols=client_protos,  # offer the same subprotocols upstream
        )
    except Exception:
        try:
            await ws.close(code=1011)
        except Exception:
            pass
        await session.close()
        _scout_ws_live["n"] -= 1
        return

    # Echo back ONLY the subprotocol Streamlit actually selected
    # (review triage, Sep 26: the old `or client_protos[0]` fallback
    # claimed the browser's first offer even when the upstream rejected
    # every offer - the two peers could then disagree about the wire
    # protocol. aiohttp reports upstream.protocol as None when nothing
    # was selected; if the client required one, we reject the
    # downstream handshake cleanly instead of lying to it.)
    chosen = getattr(upstream, "protocol", None)
    if client_protos and not chosen:
        try:
            await ws.close(code=1002)   # protocol error
        except Exception:
            pass
        await session.close()
        _scout_ws_live["n"] -= 1
        return
    await ws.accept(subprotocol=chosen)

    async def pump_client_to_upstream():
        try:
            while True:
                msg = await asyncio.wait_for(
                    ws.receive(), timeout=_SCOUT_WS_IDLE_S
                )
                if msg["type"] == "websocket.disconnect":
                    return
                if msg["type"] == "websocket.receive":
                    if msg.get("bytes") is not None:
                        await upstream.send_bytes(msg["bytes"])
                    elif msg.get("text") is not None:
                        await upstream.send_str(msg["text"])
        except asyncio.TimeoutError:
            return  # idle zombie -> tear down
        except WebSocketDisconnect:
            return
        except Exception:
            return

    async def pump_upstream_to_client():
        try:
            while True:
                msg = await asyncio.wait_for(
                    upstream.receive(), timeout=_SCOUT_WS_UPSTREAM_IDLE_S
                )
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await ws.send_text(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    await ws.send_bytes(msg.data)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    return
        except asyncio.TimeoutError:
            return  # idle zombie -> tear down
        except Exception:
            return

    try:
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(pump_client_to_upstream()),
                asyncio.create_task(pump_upstream_to_client()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    finally:
        try:
            await upstream.close()
        except Exception:
            pass
        await session.close()
        try:
            await ws.close()
        except Exception:
            pass
        _scout_ws_live["n"] -= 1




# -- SPA PATH FALLBACK (Sep 25 evening IV) -- the app is hash-routed
# (/#dashboard) but users and bookmarks type plain paths. This sits
# AFTER every specific route (so /push_sw.js, /scout, /api/* and
# /static are never shadowed) and BEFORE the __main__ guard (so the
# route registers before uvicorn.run blocks the interpreter).
SPA_PAGES = ("dashboard", "admin", "signup", "howto", "realplay",
             "leaderboard", "landing")
# Anything that is not a real page is a real 404. Serving the app shell
# with a 200 at every unknown address made every typo, every dead link
# and every retired URL an indexable copy of the homepage (the soft 404
# verify.sh kept flagging); the hash routes are redirects, so the client
# still boots on every page that actually exists.
NOT_FOUND_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex">
<title>Page not found — XMR Jackpot</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<main class="card" style="max-width:640px;margin:10vh auto;">
  <h1>Page not found</h1>
  <p>There is nothing at this address. The game lives at
     <a href="/">xmrjackpot.com</a>.</p>
</main>
</body>
</html>
"""


def _not_found() -> HTMLResponse:
    return HTMLResponse(NOT_FOUND_HTML, status_code=404,
                        headers={"Cache-Control": "no-store"})


@app.get("/{spa_page}", response_class=HTMLResponse)
async def spa_path_fallback(spa_page: str):
    if spa_page in SPA_PAGES:
        return RedirectResponse(url=f"/#{spa_page}", status_code=302)
    return _not_found()


if __name__ == "__main__":
    import uvicorn
    import threading

    # Background payment scanner thread
    def payment_scanner_loop():
        """Periodically scan for confirmed Monero payments + Kraken payouts + proxy workers."""
        import time as _time
        import socket as _socket
        import json as _json
        import urllib.request as _urlreq
        scan_count = 0
        PROXY_API = os.environ.get("XMRJ_PROXY_API", "http://127.0.0.1:9090")
        # NO CREDENTIAL LITERAL (Oct 25): this token used to be hard-coded
        # here, and pool/server.py also shipped inside the public bundle.
        # It is a deployment secret now - set XMRJ_PROXY_API_TOKEN on the
        # operator box (the value in monero/proxy_config.json's api section
        # must match), and rotate the old one, which must be treated as
        # compromised. An unset token sends no Authorization header; the
        # proxy then refuses and the poller reports it, which is visible.
        def _proxy_token() -> str:
            """Env first, then the operator's token file (never the repo)."""
            env = (os.environ.get("XMRJ_PROXY_API_TOKEN") or "").strip()
            if env:
                return env
            tf = Path(__file__).parent.parent / "monero" / "proxy_api_token.txt"
            try:
                return tf.read_text(encoding="utf-8").strip()
            except OSError:
                return ""

        PROXY_TOKEN = _proxy_token()
        PROXY_LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "monero", "proxy.log")
        last_log_pos = 0  # track position in log file to only read new lines

        def parse_proxy_log_for_miners():
            """Parse xmrig-proxy log for new miner connections and register them in DB."""
            nonlocal last_log_pos
            try:
                if not os.path.exists(PROXY_LOG):
                    return
                fsize = os.path.getsize(PROXY_LOG)
                if fsize < last_log_pos:
                    last_log_pos = 0  # log was rotated/reset
                with open(PROXY_LOG, 'r', encoding='utf-8', errors='ignore') as f:
                    f.seek(last_log_pos)
                    new_lines = f.readlines()
                    last_log_pos = f.tell()

                import re as _re
                # xmrig-proxy logs miner connections like:
                # "miner <ip>:<port> <wallet_address>.<worker_name> x"
                # or "new worker <ip> <wallet.worker>"
                wallet_pattern = _re.compile(r'([48][0-9A-Za-z]{90,120})(?:\.([a-zA-Z0-9_-]+))?')

                for line in new_lines:
                    # Only process lines that mention miners/workers connecting
                    lower = line.lower()
                    if 'miner' not in lower and 'worker' not in lower and 'login' not in lower:
                        continue
                    match = wallet_pattern.search(line)
                    if match:
                        wallet = match.group(1)
                        worker = match.group(2) or ""
                        # Register this miner in DB
                        miner = db.get_or_create_miner(wallet, worker)
                        # Update last_seen to now
                        with db.get_db() as conn:
                            conn.execute(
                                "UPDATE miners SET last_seen = ? WHERE id = ?",
                                (_time.time(), miner["id"]),
                            )
                        print(f"[ProxyLog] Registered miner: {wallet[:16]}... worker={worker}")
            except Exception as e:
                print(f"[ProxyLog] Error parsing log: {e}")

        def poll_proxy_workers():
            """Fetch real workers from xmrig-proxy and update DB."""
            try:
                req = _urlreq.Request(
                    f"{PROXY_API}/2/summary",
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Authorization": f"Bearer {PROXY_TOKEN}",
                    },
                )
                with _urlreq.urlopen(req, timeout=5) as resp:
                    summary = _json.loads(resp.read())

                # xmrig-proxy v2 API: "workers" is a count, not an array
                worker_count = summary.get("workers", 0)
                hashrate_total = summary.get("hashrate", {}).get("total", 0) if isinstance(summary.get("hashrate"), dict) else 0
                shares_accepted = summary.get("results", {}).get("accepted", 0) if isinstance(summary.get("results"), dict) else 0

                # xmrig-proxy returns hashrate.total as a per-window
                # LIST (10s/60s/15m/1h/6h/24h) — formatting the list
                # itself crashed every cycle with "unsupported format
                # string passed to list.__format__" (review, Sep 25
                # evening VI). Sum the observed window buckets.
                if isinstance(hashrate_total, (list, tuple)):
                    hashrate_total = sum(float(x or 0) for x in hashrate_total)
                if worker_count > 0:
                    print(f"[ProxyPoller] {worker_count} real miner(s) connected, {hashrate_total:.1f} H/s total, {shares_accepted} shares accepted")

                # Check if a drawing is due
                result = engine.check_drawing_schedule()
                if result.get("status") == "drawn":
                    print(f"[ProxyPoller] Drawing #{result.get('drawing_id')} complete")

            except Exception as e:
                print(f"[ProxyPoller] Error: {e}")

        while True:
            try:
                scan_count += 1

                # Poll xmrig-proxy for real miners every cycle (60s)
                poll_proxy_workers()

                # Parse proxy log for new miner connections
                parse_proxy_log_for_miners()

                # THE CHARTER'S SPOOL, DRAINED (Oct 7, RESOLVED_WIRE):
                # scout/push_client.py spools charter events whenever
                # the operator route refuses; the reader that owns the
                # release bookkeeping (drop after 50 tries, release
                # after a real send) must ride THIS loop — the proxy
                # poller processes never ran their call-path from
                # here, so the spool sat undrained for the whole
                # outage. Fail-soft: a push problem can never break
                # the pool.
                try:
                    push_svc.drain_spool()
                except Exception as _drain_err:
                    print(f"[PushSpool] drain failed: {_drain_err}")

                # Refresh demo miner timestamps every 3 min so they stay "active"
                # (demo data is flagged with demo_data_seeded setting)
                if scan_count % 3 == 0 and db.get_setting("demo_data_seeded"):
                    now = _time.time()
                    with db.get_db() as conn:
                        conn.execute(
                            "UPDATE miners SET last_seen = ? WHERE last_seen < ?",
                            (now - 60, now - 900)
                        )
                        # Vary hashrate ±5% using abs(random()) for natural fluctuation
                        # Clamp to realistic range to prevent corruption from bad seed values
                        conn.execute(
                            """UPDATE miners SET hashrate = CASE
                                WHEN hashrate > 0 AND hashrate < 10000 THEN
                                    MIN(hashrate * (0.95 + 0.1 * (abs(RANDOM()) % 1000) / 1000.0), 10000)
                                WHEN hashrate >= 10000 OR hashrate < 0 THEN
                                    abs(RANDOM()) % 4500 + 500.0
                                ELSE hashrate
                            END WHERE last_seen > ?""",
                            (now - 900,)
                        )

                # Quick port check — don't hang if wallet RPC is down
                sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                sock.settimeout(2)
                wallet_up = sock.connect_ex(('127.0.0.1', 18082)) == 0
                sock.close()

                if wallet_up:
                    # Auto-load wallet if not already loaded
                    if not xmr.rpc_available():
                        try:
                            import keyring
                            pwd = keyring.get_password("memex", "monero_wallet_password")
                            if pwd:
                                load_data = _json.dumps({
                                    "jsonrpc": "2.0", "id": "0",
                                    "method": "open_wallet",
                                    "params": {"filename": "jackpot_pool", "password": pwd}
                                }).encode()
                                load_req = _urlreq.Request(
                                    "http://127.0.0.1:18082/json_rpc",
                                    data=load_data,
                                    headers={"Content-Type": "application/json"},
                                )
                                _urlreq.urlopen(load_req, timeout=30)
                                print("[Wallet] Auto-loaded wallet: jackpot_pool")
                        except Exception as e:
                            print(f"[Wallet] Auto-load failed: {e}")

                    # THE DEPOSIT SCAN IS QUARANTINED (Oct 26): this
                    # block used to share one try with the house-tax
                    # send, the claim rollover and the seed below, so a
                    # query that raised here (get_pending_payments'
                    # created_at) silently killed all three for 24 days
                    # while drawing 15 sat past its deadline. A broken
                    # deposit scan may never again stop the rollover.
                    try:
                        pending = db.get_pending_payments()
                        if pending:
                            for p in pending:
                                tx_info = xmr.get_transfer_by_txid(p["txid"])
                                if tx_info and tx_info["confirmed"]:
                                    # PER PAYMENT, NOT PER LOOP (review, Oct 26):
                                    # `tickets` used to be assigned only under
                                    # `if miner:` and read outside it — so the
                                    # first deposit with no miner raised
                                    # NameError (killing the scan), and every
                                    # later one stamped the PREVIOUS payment's
                                    # count onto its row. Zero until this
                                    # payment mints its own.
                                    tickets = 0
                                    user = db.get_user(p["user_id"])
                                    miner = db.get_or_create_miner(user.get("wallet_address", "")) if user else None
                                    if miner:
                                        # Credit free tickets for deposits (1 ticket per 0.001 XMR)
                                        tickets = int(tx_info["amount"] / 0.001)
                                        db.add_tickets(miner["id"], "free", tickets)
                                    db.credit_payment(p["id"], tickets)

                        known = db.get_all_subaddress_mappings()
                        if known:
                            payments = xmr.scan_new_payments(known)
                            for pmt in payments:
                                db.record_payment(
                                    pmt["user"], pmt["txid"], pmt["amount"],
                                    pmt["subaddr_index"], pmt["height"], pmt["confirmations"]
                                )
                    except Exception as e:
                        print(f"[Wallet] Deposit scan failed: {type(e).__name__}: {e} — "
                              "house tax, claim rollover and seed continue")

                    result = engine.check_and_send_house_tax()
                    if result:
                        print(f"[Payout] House tax {result.get('action','?')}: {result.get('amount',0):.6f} XMR")

                    # Check for expired winner claims — return jackpot to pool
                    expired = engine.check_expired_claims()
                    if expired:
                        for e in expired:
                            print(f"[Claim] Drawing #{e['drawing_id']} expired — {e['returned']:.6f} XMR returned to pool")

                    # Seed jackpot if empty — try wallet first, then house tax
                    current_jackpot = float(db.get_setting("jackpot_balance", "0"))
                    if current_jackpot < 0.001:
                        # Try wallet first
                        seed = engine.seed_jackpot_from_wallet()
                        if seed:
                            print(f"[Seed] Jackpot seeded from wallet: {seed['seeded']:.6f} XMR")
                        else:
                            # Wallet empty — pull from accumulated house tax
                            house_tax = float(db.get_setting("house_tax_accumulated", "0"))
                            if house_tax > 0.001:
                                seed_amount = min(house_tax, float(db.get_setting("jackpot_seed_xmr", "0.05")))
                                new_jackpot = round(current_jackpot + seed_amount, 8)
                                db.set_setting("jackpot_balance", str(new_jackpot))
                                db.set_setting("house_tax_accumulated", str(round(house_tax - seed_amount, 8)))
                                print(f"[Seed] Jackpot seeded from house tax: {seed_amount:.6f} XMR (house tax remaining: {house_tax - seed_amount:.6f})")

                _time.sleep(60)
            except Exception as e:
                # A SILENT SWALLOW COST 24 DAYS (Oct 26): the bare
                # handler slept and said nothing, so a query raising on
                # every cycle hid the whole branch from the log. The
                # next silent death must be visible instead.
                print(f"[Scanner] cycle failed: {type(e).__name__}: {e}")
                _time.sleep(60)

    scanner = threading.Thread(target=payment_scanner_loop, daemon=True)
    scanner.start()

    # THE RECURRING LEG: hourly referral worker + expiry-calendar notice,
    # started only when SCHEDULER_ENABLED=1 (admin card flips it).
    kr_sched.start()

    # ── THE LISTENER CONTRACT (Oct 25) ────────────────────────────────
    # APP_PORT is REQUIRED. It used to default to 80, which silently
    # produced a second, unmanaged origin on the privileged port the first
    # time the service was started without an environment; that orphan
    # served a stale homepage and an admin-gated bundle for five days. A
    # missing port is a deployment error and must stop the process, not
    # pick one.
    _raw_port = (os.environ.get("APP_PORT") or "").strip()
    if not _raw_port:
        raise SystemExit(
            "APP_PORT is not set. The web origin must be given explicitly "
            "(production: 3094, bound behind the Cloudflare tunnel). "
            "Refusing to guess a port.")
    try:
        port = int(_raw_port)
    except ValueError:
        raise SystemExit(f"APP_PORT must be an integer, got {_raw_port!r}")
    if not (1 <= port <= 65535):
        raise SystemExit(f"APP_PORT out of range: {port}")
    if port < 1024 and os.environ.get("XMRJ_ALLOW_PRIVILEGED_PORT") != "1":
        raise SystemExit(
            f"APP_PORT={port} is a privileged port. Scout's origin does not "
            "need one: the tunnel connects to loopback. Set "
            "XMRJ_ALLOW_PRIVILEGED_PORT=1 only for a deliberate, documented "
            "reason.")

    # Loopback by default: the Cloudflare tunnel reaches 127.0.0.1:3094 on
    # this box, so binding every interface only widened the admin surface
    # to the LAN. APP_HOST opts back in explicitly where that is intended.
    host = (os.environ.get("APP_HOST") or "127.0.0.1").strip()
    print(f"[Origin] serving Scout on http://{host}:{port}"
          f"{' (ALL interfaces)' if host == '0.0.0.0' else ''}")
    uvicorn.run(app, host=host, port=port)
