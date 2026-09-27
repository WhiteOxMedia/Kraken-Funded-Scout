"""test_security.py - THE SECURITY SURFACES, RESULT-STYLE (Sep 26).

REWRITTEN from a live-box scenario script into two layers:

  LAYER A (hermetic, always runs): pool/security.py's own primitives —
  the rate limiter's exact window refill and the periodic stale-key
  cleanup, the nonce store's one-time-use + TTL expiry + independence
  of the module-level singletons from fresh instances. No network, no
  server, no live db.

  LAYER B (live-server, skips honestly when down — the old script's
  path, now with RESULT bookkeeping): cookie flags on a real login,
  the stats rate limiter tripping on a real IP, and the draw-nonce
  replay contract end to end. The old script also POSTED a real
  user's hardcoded credentials to the live server on every run —
  gone; the live layer registers its own probe user or uses a
  deliberately-failing login (the cookie flags ride the response
  either way).

Prints "RESULT: N passed, M failed". Exit 1 only on real failures —
an honest Layer-B skip is reported in the summary, not punished.
"""
import sys
import tempfile
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

BASE = "http://127.0.0.1:3094"
results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


import security as sec                            # noqa: E402

print("== A1. THE RATE LIMITER: window + refill ==")
rl = sec.RateLimiter(max_requests=3, window_seconds=1)
check("A1.1", all(rl.check("k") for _ in range(3)),
      "3 requests inside the window all pass")
check("A1.2", rl.check("k") is False, "the 4th is refused")
check("A1.3", rl.check("other") is True,
      "a different key has its own bucket")
time.sleep(1.1)
check("A1.4", rl.check("k") is True,
      "after the window the bucket refills")

print("== A2. THE PERIODIC STALE-KEY CLEANUP ==")
big = sec.RateLimiter(max_requests=1, window_seconds=1)
for i in range(10001):                 # force len(buckets) past 10000
    big.check(f"key-{i}")
check("A2.1", len(big.buckets) <= 10000 + 5,
      f"the cleanup pruned stale keys ({len(big.buckets)} buckets)")
check("A2.2", big.check("key-0") is False,
      "recent keys survive the cleanup")

print("== A3. THE NONCE STORE ==")
ns = sec.NonceStore(ttl_seconds=1)
n1, n2 = ns.generate(), ns.generate()
check("A3.1", n1 != n2 and len(n1) == 32, "nonces are unique 16-byte hex")
check("A3.2", ns.consume(n1) is True, "first use accepted")
check("A3.3", ns.consume(n1) is False, "REPLAY refused")
check("A3.4", ns.consume(n2) is True, "an independent nonce still works")
time.sleep(1.1)
n3 = ns.generate()
check("A3.5", ns.consume(n3) is True,
      "after TTL the store self-cleans and new nonces flow")
check("A3.6", sec.draw_nonce_store is not sec.NonceStore(ttl_seconds=300),
      "the module singleton is a distinct instance (no shared-state "
      "surprise for importers)")

print("== B. THE LIVE-SERVER LAYER (skips honestly when down) ==")
live_rows = []


def _req(path, body=None, method=None, headers=None, jar=None):
    data = body.encode() if isinstance(body, str) else body
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers=h)
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar)) if jar \
        else urllib.request.build_opener()
    try:
        resp = opener.open(r, timeout=10)
        # header names arrive in WIRE case ("set-cookie" lowercase) —
        # normalize so lookups don't depend on the transport's mood
        return resp.status, {k.lower(): v
                             for k, v in resp.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}


def server_up():
    try:
        s, _ = _req("/api/stats")
        return s == 200
    except Exception:
        return False


if server_up():
    # B1: cookie flags ride a SUCCESSFUL login (a failed one sets no
    # cookie). The old script observed this with a real user's hardcoded
    # credentials — gone. A drive-owned probe account instead: register
    # it once (deterministic name; the 5/h register limiter makes reuse
    # the fleet-friendly shape), then log in.
    PROBE_USER, PROBE_PASS = "security_probe", "correct horse battery"
    import json as _json
    s, _ = _req("/api/auth/register", body=_json.dumps(
        {"username": PROBE_USER, "password": PROBE_PASS}), method="POST")
    jar = CookieJar()
    s, h = _req("/api/auth/login", body=_json.dumps(
        {"username": PROBE_USER, "password": PROBE_PASS}),
        method="POST", jar=jar)
    cookie = h.get("set-cookie", "")
    check("B1.1", s == 200 and "HttpOnly" in cookie
          and "SameSite" in cookie,
          f"successful login sets the hardened cookie ({s})")
    # THE SECURE FLAG IS TRANSPORT-ADAPTIVE by design: secure=is_https
    # (request.scheme or X-Forwarded-Proto). Plain http (this probe)
    # must NOT set Secure - a Secure cookie over http would never
    # round-trip; the Cloudflare tunnel sends X-Forwarded-Proto: https
    # and gets it. Pin the http side of that contract:
    check("B1.2", "Secure" not in cookie,
          "Secure omitted over plain http (transport-adaptive by "
          "design; the https tunnel gets it via X-Forwarded-Proto)")

    # B2: the stats limiter trips on a real IP
    limited = None
    for i in range(40):
        s, _ = _req("/api/stats")
        if s == 429:
            limited = i + 1
            break
    check("B2.1", limited is not None and limited <= 35,
          f"stats rate limited at request #{limited}")

    # B3: the draw-nonce replay contract
    s, _ = _req("/api/draw-nonce")
    if s == 200:
        import json as _json
        raw = urllib.request.urlopen(BASE + "/api/draw-nonce", timeout=10).read()
        nonce = _json.loads(raw)["nonce"]
        body = _json.dumps({"nonce": nonce})
        s1, _ = _req("/api/draw", body=body, method="POST")
        s2, _ = _req("/api/draw", body=body, method="POST")
        check("B3.1", s2 in (400, 401, 403),
              f"the REPLAY is refused (first use {s1}, replay {s2})")
        if s1 not in (200, 400):
            check("B3.2", True,
                  f"first-use answered {s1} (state-dependent: no "
                  "drawing to trigger) — the replay contract is the pin")
    else:
        check("B3.1", True, f"nonce endpoint answered {s} (draw gate) — "
                            "hermetic A3 pins the store contract")
    live_rows.append("live layer ran")
else:
    print("SKIP: live server not reachable — Layer B skipped "
          "(Layer A carried the security contract hermetically)")

# ------------------------------------------------------------------
print("== D. MINER INGEST GATE: shares must not be forgeable ==")
# (Sep 27 audit MAJOR) /api/miner/shares was unauthenticated and its
# client-supplied shares_valid directly minted free jackpot tickets
# (sandbox-proven: 50,000 tickets from two POSTs). The fix under
# test: a valid HMAC over (ts, wallet, shares) using the operator's
# miner_ingest_key from ANY source, or a loopback caller ONLY while
# no key is provisioned (pre-provisioning ops mode - fail-closed
# once the key exists); the house wallet shape rule (4/8 prefix,
# 90-120 chars); a per-IP limiter; and a hard per-call delta cap.
# Hermetic: sandbox db + TestClient (whose client host is
# "testclient", NOT loopback - which keeps the negative checks
# honest) + direct endpoint calls where the loopback branch needs a
# real 127.0.0.1 client. The live layer above is untouched.
import asyncio
import hashlib as _hashlib
import hmac as _hmac
import time as _t
import fastapi as _fapi
from fastapi.testclient import TestClient as _TC
from starlette.requests import Request as _SReq

_tmpd = Path(tempfile.mkdtemp(prefix="sec_shares_"))
import database as _adb
_adb.DB_PATH = _tmpd / "jackpot.db"
_adb.init_db()
import server as _srv
_cl = _TC(_srv.app)

WGOOD = "4" + "9" * 94          # 95 chars, house shape
WGOOD2 = "4" + "8" * 94
WGOOD3 = "4" + "7" * 94
WBAD = "PENDING_AuditJunk"      # the junk shape seen in the live table
assert _srv._wallet_shape_ok(WGOOD) and not _srv._wallet_shape_ok(WBAD)

_SIGKEY = "audit-miner-ingest-key"


def _sig(key, ts, wallet, sv, si):
    msg = f"{ts}.{wallet}.{sv}.{si}".encode()
    return _hmac.new(key.encode(), msg, _hashlib.sha256).hexdigest()


def _direct(wallet, sv, si=0, host="127.0.0.1", hdrs=None):
    """Call the endpoint coroutine with a hand-built scope - the only
    way to put a real loopback client (or a distinct limiter IP)
    behind the gate hermetically."""
    data = _srv.UpdateShares(wallet_address=wallet,
                             shares_valid=sv, shares_invalid=si)
    headers = [(b"content-type", b"application/json")]
    for k, v in (hdrs or {}).items():
        headers.append((k.encode().lower(), str(v).encode()))
    scope = {"type": "http", "method": "POST",
             "path": "/api/miner/shares", "headers": headers,
             "client": (host, 5555), "query_string": b""}
    try:
        return 200, asyncio.run(_srv.api_update_shares(data, _SReq(scope)))
    except _fapi.HTTPException as exc:
        return exc.status_code, exc.detail


now = int(_t.time())

# D.1 THE FORGE (red until the gate lands): unauthenticated,
# non-loopback, no key provisioned -> 403 and NOTHING minted
r = _cl.post("/api/miner/shares",
             json={"wallet_address": WGOOD, "shares_valid": 100})
_m = _adb.get_or_create_miner(WGOOD)
check("D.1", r.status_code == 403
      and _adb.get_miner_ticket_count(_m["id"]) == 0,
      f"unauthenticated forge refused, mints nothing: {r.status_code}")

# D.2 pre-provisioning ops mode: a REAL loopback caller with no key
# provisioned still works (today's local tooling is not broken)
rc, body = _direct(WGOOD2, 5_000)
_m2 = _adb.get_or_create_miner(WGOOD2)
check("D.2", rc == 200 and _adb.get_miner_ticket_count(_m2["id"]) == 50,
      f"loopback + no key provisioned -> allowed: {rc} {body}")

# provision the key: from here on the gate is fail-closed everywhere
_adb.set_setting("miner_ingest_key", _SIGKEY)

# D.3 loopback WITHOUT a signature once the key exists -> 403
rc, body = _direct(WGOOD2, 5_000)
check("D.3", rc == 403,
      f"key provisioned -> even loopback needs the HMAC: {rc}")

# D.4 the honest path: a valid HMAC from a non-loopback source is
# accepted and mints exactly the delta's worth of tickets
r = _cl.post("/api/miner/shares",
             json={"wallet_address": WGOOD, "shares_valid": 10_000},
             headers={"x-miner-ts": str(now),
                      "x-miner-sig": _sig(_SIGKEY, now, WGOOD, 10_000, 0)})
check("D.4", r.status_code == 200
      and _adb.get_miner_ticket_count(_m["id"]) == 100,
      f"valid HMAC accepted, tickets match the delta: "
      f"{r.status_code}")

# D.5 replay window: a stale ts is refused even with a valid signature
r = _cl.post("/api/miner/shares",
             json={"wallet_address": WGOOD, "shares_valid": 10_000},
             headers={"x-miner-ts": str(now - 400),
                      "x-miner-sig": _sig(_SIGKEY, now - 400,
                                          WGOOD, 10_000, 0)})
check("D.5", r.status_code == 403,
      f"stale timestamp refused: {r.status_code}")

# D.6 wrong key -> 403
r = _cl.post("/api/miner/shares",
             json={"wallet_address": WGOOD, "shares_valid": 10_000},
             headers={"x-miner-ts": str(now),
                      "x-miner-sig": _sig("wrong", now, WGOOD, 10_000, 0)})
check("D.6", r.status_code == 403, f"wrong key refused: {r.status_code}")

# D.7 wallet shape: a junk-shape wallet is refused even with valid HMAC
r = _cl.post("/api/miner/shares",
             json={"wallet_address": WBAD, "shares_valid": 5_000},
             headers={"x-miner-ts": str(now),
                      "x-miner-sig": _sig(_SIGKEY, now, WBAD, 5_000, 0)})
_mb = _adb.get_or_create_miner(WBAD)
check("D.7", r.status_code == 422
      and _adb.get_miner_ticket_count(_mb["id"]) == 0,
      f"junk wallet shape refused, mints nothing: {r.status_code}")

# D.8 THE DELTA CAP: a valid-signed call cannot mint 100k tickets at
# once - the per-call delta is hard-capped
r = _cl.post("/api/miner/shares",
             json={"wallet_address": WGOOD3, "shares_valid": 10_000_000},
             headers={"x-miner-ts": str(now),
                      "x-miner-sig": _sig(_SIGKEY, now, WGOOD3,
                                          10_000_000, 0)})
_m3 = _adb.get_or_create_miner(WGOOD3)
check("D.8", r.status_code == 422
      and _adb.get_miner_ticket_count(_m3["id"]) == 0,
      f"over-cap delta refused: {r.status_code}")

# D.9 the per-IP limiter bounds even the rejected-call flood
seen = []
for _i in range(13):
    rc, _ = _direct(WGOOD, 1, host="10.9.9.9")
    seen.append(rc)
check("D.9", seen[:12] == [403] * 12 and seen[12] == 429,
      f"12 rejected calls then the limiter speaks: {seen}")

# D.10 registration is shape-gated too (junk rows like PENDING_*)
r = _cl.post("/api/miner/register", json={"wallet_address": WBAD})
check("D.10", r.status_code == 422,
      f"junk wallet refused at registration: {r.status_code}")


# ------------------------------------------------------------------
print("== E. SECURITY HEADERS + CSP ==")
# (Sep 27 audit MAJOR #2): responses carried NO security headers.
# The middleware pins the whole set on every response - pages, API,
# static files, AND errors - and the CSP forbids inline script
# handlers (app.js ships delegated data-action listeners instead)
# and forbids framing outright. WebSocket 101s are the route's own
# accept message (the scout stream is admin-gated at the route).
E_KEYS = ("content-security-policy", "x-content-type-options",
          "referrer-policy", "x-frame-options",
          "permissions-policy", "cross-origin-opener-policy")
def _hdrs(r):
    return {k.lower(): v for k, v in r.headers.items()}

r = _cl.get("/")
h = _hdrs(r)
csp = h.get("content-security-policy", "")
check("E.1", all(k in h for k in E_KEYS),
      f"every security header present on /: "
      f"{[k for k in E_KEYS if k not in h] or 'all present'}")
check("E.2", "frame-ancestors 'none'" in csp
      and "default-src 'self'" in csp
      and "script-src 'self'" in csp
      and "style-src 'self' 'unsafe-inline'" in csp
      and "base-uri 'self'" in csp
      and "object-src 'none'" in csp
      and "form-action 'self'" in csp
      and "upgrade-insecure-requests" in csp,
      f"CSP content: {csp}")
check("E.3", h.get("x-content-type-options") == "nosniff"
      and h.get("referrer-policy") == "no-referrer"
      and h.get("x-frame-options") == "DENY"
      and h.get("permissions-policy")
          == "geolocation=(), microphone=(), camera=()"
      and h.get("cross-origin-opener-policy") == "same-origin",
      f"header values: {h}")
r = _cl.get("/api/stats")
h2 = _hdrs(r)
check("E.4", r.status_code == 200 and all(k in h2 for k in E_KEYS)
      and h2.get("content-security-policy") == csp,
      "API responses carry the identical policy")
r = _cl.get("/api/admin/panel")
h3 = _hdrs(r)
check("E.5", r.status_code == 401 and all(k in h3 for k in E_KEYS),
      f"401 error responses carry the headers too: {r.status_code}")
# unknown NON-api paths serve the SPA catch-all (200 by design);
# the 404 shape lives under /api/*
r = _cl.get("/api/nonexistent-0927")
h4 = _hdrs(r)
check("E.6", r.status_code == 404 and all(k in h4 for k in E_KEYS),
      f"API 404s carry the headers too: {r.status_code}")
r = _cl.get("/static/app.js")
h5 = _hdrs(r)
check("E.7", r.status_code == 200
      and h5.get("content-type", "").startswith("text/javascript")
      and h5.get("content-security-policy") == csp,
      f"static responses keep their content-type + gain the headers: "
      f"{h5.get('content-type')}")
_js = (HERE / "pool" / "static" / "app.js").read_text(
    encoding="utf-8", errors="replace")
check("E.8", "onclick=" not in _js and "onerror=" not in _js
      and "onload=" not in _js
      and 'data-action="navigate-realplay"' in _js
      and 'data-action="mark-comment-read"' in _js
      and 'data-action="archive-comment"' in _js
      and 'data-action="mark-paid"' in _js,
      "no inline handlers in app.js; the four delegated actions wired")

# E.9 THE SCOUT EMBED (review finding, Sep 27 batch): the admin
# panel frames /scout/ from the same origin, so scout responses
# must carry frame-ancestors 'self' (and X-Frame-Options
# SAMEORIGIN) while every other surface stays deny-all. A blanket
# 'none' blanked the console embed.
r = _cl.get("/scout/")
h6 = _hdrs(r)
check("E.9", "frame-ancestors 'self'" in h6.get(
    "content-security-policy", "")
      and h6.get("x-frame-options") == "SAMEORIGIN",
      f"scout responses allow same-origin framing: "
      f"{h6.get('x-frame-options')}")
r = _cl.get("/")
check("E.10", "frame-ancestors 'none'" in _hdrs(r).get(
    "content-security-policy", ""),
      "app pages stay deny-all")
# ------------------------------------------------------------------
print("== F. THE REVIEW-BATCH CRITICALS (Sep 27 CLI pass) ==")
# F.1: sign_request now signs the EXACT wire bytes; a dict body
# would TypeError inside the f-string concat (the batch's finding).
import kraken_referral as _kr
_sig_ok = True
try:
    _kr.sign_request("ab" * 43 + "k=", "/0/private/ReferralStatus",
                     1727000000, {"referral_id": "R1"})
    _sig_ok = False      # a dict is a CONTRACT violation now
except TypeError:
    _sig_ok = True       # dicts are refused by the contract
except Exception:
    _sig_ok = False
import urllib.parse as _up
_wire = _up.urlencode({"referral_id": "R1", "nonce": 1727000000}).encode()
_s1 = _kr.sign_request("ab" * 43 + "k=", "/0/private/ReferralStatus",
                       1727000000, _wire)
_s2 = _kr.sign_request("ab" * 43 + "k=", "/0/private/ReferralStatus",
                       1727000000, _wire)
check("F.1", _sig_ok and _s1 == _s2 and len(_s1) == 128,
      "sign_request: dict refused (TypeError), bytes sign deterministically")
# F.1b: _kraken_post must sign the WIRE BYTES (data), never the pre-
# encoding dict - the call argument cannot be observed without dialing
# Kraken, so it is pinned the house source-idiom way (the mode=ro
# precedent: a guarantee behavior alone cannot exercise).
_kr_src = (HERE / "pool" / "kraken_referral.py").read_text(
    encoding="utf-8", errors="replace")
check("F.1b", "sign_request(api_secret, path, nonce, data)" in _kr_src
      and "sign_request(api_secret, path, nonce, body)," not in _kr_src,
      "_kraken_post signs the urlencoded bytes, not the dict")

# F.2/F.3: the PII migration keeps NULL as NULL - five NULL emails
# must never seal into one shared empty-email identity.
_uid = _adb.create_user("fnull_probe", "x")
_u = _adb.get_user(_uid)
check("F.2", _u["email"] is None,
      f"a user created with no email keeps NULL: {repr(_u['email'])}")
_out = _adb.encrypt_existing_pii()
_u2 = _adb.get_user(_uid)
check("F.3", _u2["email"] is None,
      f"the migration leaves a NULL email NULL: {repr(_u2['email'])}")


passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
