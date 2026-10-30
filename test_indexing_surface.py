"""test_indexing_surface.py — THE CRAWLER SURFACE (Oct 23).

robots.txt and sitemap.xml used to fall through to the SPA catch-all, so
every crawler got the HTML shell at both URLs: no rules, no sitemap, and
the pages that do exist were never submitted. /winners 302'd to a hash
route, so the URL itself carried no title, canonical or body. And the
origin never redirected http to https, leaving two copies of every URL.

This drive pins the fixes against the real app (hermetic TestClient, temp
database, no network):

  1. robots.txt: 200, text/plain, advertises the sitemap, lists no HTML,
     and keeps /api/ and /downloads/ out
  2. sitemap.xml: 200, an XML content type, a <urlset> root, exactly the
     indexable URLs (the four English pages plus every localized download
     page), well-formed, and no HTML anywhere in it
  3. every URL the sitemap lists answers 200 with a self-referencing
     canonical — a sitemap may only point at pages that exist
  4. /winners: 200 (not a redirect), its own title, one h1, the copy,
     site JSON-LD, the empty state before any drawing, and the real
     drawing (winner, prize, proof link) once one completes
  5. one origin, one scheme: a proxied plaintext request 301s to the
     https URL with its path and query intact, a www host 301s to the
     apex the same way, and https, apex, loopback and un-tunnelled
     requests are left alone
  6. /how-to-play: 200, its own title and canonical, one h1, the FAQ
     copy, and a FAQPage whose questions are the ones the page prints -
     emitted here and nowhere else
  7. dead URLs: an unknown path, a retired one and a bare /downloads
     are real 404s (noindex, not the app shell), while the page paths
     still redirect to their hash routes and the real pages still 200

Prints "RESULT: N passed, M failed".
"""
import json
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import database as db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="indexing_")) / "jackpot.db"
db.init_db()

import server as srv
from fastapi.testclient import TestClient

results = []


def check(n, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(n)
          + ("  " + str(msg) if msg else ""))


client = TestClient(srv.app)

print("== 1. robots.txt ==")
r = client.get("/robots.txt")
check(1.1, r.status_code == 200 and r.headers.get("content-type", "").startswith("text/plain"),
      f"{r.status_code} {r.headers.get('content-type')}")
check(1.2, f"Sitemap: {srv.SITE_ORIGIN}/sitemap.xml" in r.text,
      "robots.txt advertises the sitemap")
check(1.3, "<html" not in r.text.lower() and "<!doctype" not in r.text.lower(),
      "robots.txt is not the SPA shell")
check(1.4, "Disallow: /api/" in r.text and "Disallow: /downloads/" in r.text,
      "the API and the artifact bytes stay out of the index")

print("== 2. sitemap.xml ==")
r = client.get("/sitemap.xml")
check(2.1, r.status_code == 200
      and r.headers.get("content-type", "").startswith("application/xml"),
      f"{r.status_code} {r.headers.get('content-type')}")
body = r.text
check(2.2, "<urlset" in body and "<html" not in body.lower(),
      "a urlset root, not the SPA shell")
try:
    root = ET.fromstring(body)
    locs = [el.text for el in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    parsed = True
except ET.ParseError as exc:
    locs, parsed = [], False
    check(2.3, False, f"sitemap is not well-formed XML: {exc}")
if parsed:
    check(2.3, True, "sitemap parses as XML")
expected = {srv.SITE_ORIGIN + p for p in srv._indexable_paths()}
check(2.4, set(locs) == expected,
      f"sitemap lists exactly the indexable URLs: {sorted(locs)}")

print("== 3. WHAT THE SITEMAP PROMISES ==")
for n, path in enumerate(srv._indexable_paths(), 1):
    r = client.get(path)
    url = srv.SITE_ORIGIN + path
    check(f"3.{n}", r.status_code == 200
          and f'<link rel="canonical" href="{url}">' in r.text,
          f"{path} -> {r.status_code}, canonical {url}")

print("== 4. /winners IS A PAGE ==")
r = client.get("/winners", follow_redirects=False)
check(4.1, r.status_code == 200, f"/winners returns {r.status_code}, not a redirect")
check(4.2, r.text.count("<h1") == 1 and "Winners and Past Drawings" in r.text,
      "one h1 and the winners copy")
check(4.3, "<title>Winners and Past Drawings — XMR Jackpot</title>" in r.text
      and f'<link rel="canonical" href="{srv.SITE_ORIGIN}/winners">' in r.text,
      "its own title and canonical")
check(4.4, '"@type": "Organization"' in r.text and '"@type": "WebSite"' in r.text,
      "site JSON-LD on the page")
check(4.5, "No drawing has completed yet" in r.text
      and "{{" not in r.text,
      "the empty state before any drawing, and no unfilled token")

# One completed drawing: the page must show what the table holds.
mid = db.get_or_create_miner("8" + "A" * 94)["id"]
did = db.create_drawing(1.2345, time.time())
with db.get_db() as conn:
    conn.execute("UPDATE drawings SET status = 'drawn', winner_id = ?, "
                 "drawn_at = ? WHERE id = ?", (mid, time.time(), did))
r = client.get("/winners")
check(4.6, f"/api/verify/{did}" in r.text
      and "1.2345 XMR" in r.text
      and "8" + "A" * 94 in r.text,
      f"the completed drawing is filled in (drawing {did})")
check(4.7, "No drawing has completed yet" not in r.text,
      "the empty state is gone once a drawing exists")

print("== 5. http -> https ==")
r = client.get("/", headers={"X-Forwarded-Proto": "http"},
               follow_redirects=False)
check(5.1, r.status_code == 301
      and r.headers.get("location") == srv.SITE_ORIGIN + "/",
      f"plaintext / -> {r.status_code} {r.headers.get('location')}")
r = client.get("/how-to-play?x=1", headers={"X-Forwarded-Proto": "http"},
               follow_redirects=False)
check(5.2, r.status_code == 301
      and r.headers.get("location") == srv.SITE_ORIGIN + "/how-to-play?x=1",
      f"path and query survive the redirect: {r.headers.get('location')}")
r = client.get("/", headers={"X-Forwarded-Proto": "https"},
               follow_redirects=False)
check(5.3, r.status_code == 200, f"an https request is served, not looped: {r.status_code}")
r = client.get("/", headers={"CF-Visitor": json.dumps({"scheme": "http"})},
               follow_redirects=False)
check(5.4, r.status_code == 301, f"the older CF-Visitor header also redirects: {r.status_code}")
r = client.get("/", follow_redirects=False)
check(5.5, r.status_code == 200, f"an un-tunnelled request is left alone: {r.status_code}")
r = client.get("/", headers={"X-Forwarded-Proto": "http", "Host": "127.0.0.1:3094"},
               follow_redirects=False)
check(5.6, r.status_code == 200, f"loopback is never bounced (health checks): {r.status_code}")
r = client.get("/", headers={"Host": "www.xmrjackpot.com",
                              "X-Forwarded-Proto": "https"},
               follow_redirects=False)
check(5.7, r.status_code == 301
      and r.headers.get("location") == srv.SITE_ORIGIN + "/",
      f"a www host 301s to the apex: {r.status_code} {r.headers.get('location')}")
r = client.get("/a/b?x=1", headers={"Host": "www.xmrjackpot.com",
                                   "X-Forwarded-Proto": "https"},
               follow_redirects=False)
check(5.8, r.status_code == 301
      and r.headers.get("location") == srv.SITE_ORIGIN + "/a/b?x=1",
      f"the www redirect keeps path and query: {r.headers.get('location')}")
r = client.get("/", headers={"Host": "xmrjackpot.com",
                              "X-Forwarded-Proto": "https"},
               follow_redirects=False)
check(5.9, r.status_code == 200, f"the apex is served, not redirected: {r.status_code}")

print("== 6. /how-to-play IS A PAGE (with its FAQ) ==")
import re as _re


def _ld(text):
    return [json.loads(b) for b in _re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', text, _re.S)]


r = client.get("/how-to-play", follow_redirects=False)
page = r.text
faqs = [n for n in _ld(page) if n.get("@type") == "FAQPage"]
questions = (faqs[0].get("mainEntity") if faqs else []) or []
check(6.1, r.status_code == 200, f"/how-to-play returns {r.status_code}, not a redirect")
check(6.2, page.count("<h1") == 1
      and "How to Play: Mine Monero, Earn Entries, Win the Daily Pot" in page,
      "one h1 and the how-to-play copy")
check(6.3, "<title>How to Play — Mine Monero, Earn Entries, Win the Daily Pot</title>"
      in page, "a title of its own, not the homepage's")
check(6.4, len(questions) == 4
      and all(q.get("name") in page and (q.get("acceptedAnswer") or {}).get("text")
              for q in questions),
      f"the FAQPage lists the {len(questions)} questions the page prints")
check(6.5, 'id="page-landing"' not in page and 'src="/static/app.js' not in page,
      "the SPA's other pages and scripts are not shipped here")
check(6.6, '"@type": "FAQPage"' not in client.get("/").text
      and '"@type": "FAQPage"' not in client.get("/download").text,
      "the FAQ is emitted on /how-to-play only")
check(6.7, '"@type": "Product"' in client.get("/download").text,
      "the download page still carries its product entities")

print("== 7. DEAD URLS ARE REAL 404S ==")
for n, path in enumerate(("/this-path-should-not-exist-xmr", "/privacy",
                          "/terms", "/downloads", "/old-promo-2024"), 1):
    r = client.get(path, follow_redirects=False)
    check(f"7.{n}", r.status_code == 404
          and 'content="noindex"' in r.text
          and 'src="/static/app.js' not in r.text,
          f"{path} -> {r.status_code}, noindex, not the app shell")
r = client.get("/does/not/exist", follow_redirects=False)
check(7.6, r.status_code == 404, f"a deeper unknown path -> {r.status_code}")
r = client.get("/dashboard", follow_redirects=False)
check(7.7, r.status_code == 302 and r.headers.get("location") == "/#dashboard",
      f"a real page path still redirects to its hash: {r.headers.get('location')}")
for n, path in enumerate(("/", "/download", "/winners", "/how-to-play"), 1):
    r = client.get(path, follow_redirects=False)
    check(f"7.{7 + n}", r.status_code == 200, f"{path} still serves its page: {r.status_code}")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
