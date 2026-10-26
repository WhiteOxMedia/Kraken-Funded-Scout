"""test_i18n_region.py — the language + region layer (Oct 24).

Six languages, two miner-facing pages, one relay in Los Angeles. This drive
pins the parts that are easy to get plausibly wrong:

  1. CATALOG PARITY: every language carries every key, no value is empty, and
     every `{placeholder}` survives translation. A missing string would
     otherwise ship as an English sentence inside a Spanish page (the silent
     fallback), and a dropped placeholder as a visible `{size}`.
  2. NEGOTIATION: q-weights are honoured, region subtags collapse to their
     language, an unsupported language falls back to English instead of
     guessing, and a selector choice beats the header.
  3. THE PAGES: every language x page answers 200, declares its own `lang`,
     canonical and seven hreflang alternates (six languages + x-default), and
     keeps the QR, the stratum endpoint and the status link.
  4. URL HYGIENE: /en/... is a 301 to the unprefixed URL (one URL per page per
     language), the selector sets a cookie and cannot be turned into an open
     redirect, and an unknown code is a real 404.
  5. AUTO-SELECTION: a browser asking for Spanish is sent to /es/download once,
     a remembered choice is as strong as the header, and a crawler (no header)
     always gets English.
  6. REGION: CF-IPCountry picks the region, the copy is latency advice, and an
     unknown country says so instead of inventing a location. No language may
     claim mining is blocked anywhere.
  7. CRAWLER SURFACE: the sitemap lists the localized downloads and no status
     page in any language; robots.txt keeps /lang/ out.

Prints "RESULT: N passed, M failed".
"""
import re
import sys
import tempfile
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import database as db

db.DB_PATH = Path(tempfile.mkdtemp(prefix="i18n_")) / "jackpot.db"
db.init_db()
# THE RATE IS PINNED (Oct 26): the download pages quote the Scout price by
# converting $95 at the live rate, and this drive compares the rendered
# page against `_scout_offer()` a moment later. With an unpinned rate the
# first render answers from the 323 fallback while the background fetch
# lands mid-run, so the two disagree by the market and the check flakes.
# What is under test is that the page quotes the SAME conversion, not what
# XMR is worth today.
db.set_setting("XMR_USD_RATE", "500.0")

import i18n
import server as srv
from fastapi.testclient import TestClient

results = []


def check(name, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(name)
          + ("  " + str(msg) if msg else ""))


client = TestClient(srv.app)
PLACEHOLDER = re.compile(r"\{(\w+)\}")
LANGS = list(i18n.LANGUAGES)

print("== 1. CATALOG PARITY ==")
en = i18n.catalog("en")
check(1.1, len(en) > 50, f"the English reference carries {len(en)} keys")
for lang in LANGS:
    cat = i18n.catalog(lang)
    missing = sorted(set(en) - set(cat))
    extra = sorted(set(cat) - set(en))
    empty = sorted(k for k, v in cat.items() if not str(v).strip())
    check(f"1.{lang}.keys", not missing and not extra,
          f"{lang}: missing {missing[:4]}, extra {extra[:4]}")
    check(f"1.{lang}.values", not empty, f"{lang}: empty {empty[:4]}")
    bad = sorted(k for k, v in cat.items()
                 if k in en and set(PLACEHOLDER.findall(v))
                 != set(PLACEHOLDER.findall(en[k])))
    check(f"1.{lang}.placeholders", not bad, f"{lang}: placeholders differ on {bad[:4]}")

print("== 2. NEGOTIATION ==")
check(2.1, i18n.negotiate("es-MX,es;q=0.9,en;q=0.8") == "es", "es-MX collapses to es")
check(2.2, i18n.negotiate("pt-BR") == "pt", "pt-BR collapses to pt")
check(2.3, i18n.negotiate("fr-CA,fr;q=0.9,en-US;q=0.8") == "fr", "fr-CA -> fr")
check(2.4, i18n.negotiate("de;q=0.4, ru;q=0.9") == "ru", "q-weights pick the preferred tag")
check(2.5, i18n.negotiate("es;q=0, en;q=1") == "en", "q=0 means 'not this one'")
check(2.6, i18n.negotiate("ja, ko, zh-CN") == "en", "unsupported languages fall back to English")
check(2.7, i18n.negotiate("") == "en" and i18n.negotiate(None) == "en",
      "no header (crawlers) is English")
check(2.8, i18n.negotiate("es", cookie="de") == "de", "a remembered choice beats the header")
check(2.9, i18n.negotiate("es", cookie="xx") == "es", "a junk cookie is ignored")
check(2.10, i18n.negotiate("EN-gb") == "en", "case and region are normalised")
check(2.11, srv.STYLE_HREF in srv._template_text(),
      f"the pinned stylesheet href matches the template: {srv.STYLE_HREF}")
check(2.12, i18n.page_path("en", "download") == "/download"
      and i18n.page_path("es", "download") == "/es/download",
      "English is unprefixed, everything else is prefixed")

print("== 3. EVERY LANGUAGE x EVERY PAGE ==")
for lang in LANGS:
    for page in ("download", "status"):
        path = i18n.page_path(lang, page)
        r = client.get(path)
        check(f"3.{lang}.{page}.200", r.status_code == 200,
              f"{path} -> {r.status_code}")
        check(f"3.{lang}.{page}.lang", f'<html lang="{lang}">' in r.text,
              f"{path} declares lang={lang}")
        canonical = f'<link rel="canonical" href="{srv.SITE_ORIGIN}{path}">'
        check(f"3.{lang}.{page}.canonical", canonical in r.text,
              f"{path} owns its canonical")
        alts = re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)">',
                          r.text)
        pairs = dict(alts)
        want = {code: srv.SITE_ORIGIN + i18n.page_path(code, page) for code in LANGS}
        want["x-default"] = srv.SITE_ORIGIN + i18n.page_path("en", page)
        check(f"3.{lang}.{page}.alternates", pairs == want,
              f"{path} alternates: {len(pairs)} of {len(want)}")
        title = re.search(r"<title>(.*?)</title>", r.text).group(1)
        check(f"3.{lang}.{page}.translated", title == i18n.t(lang, f"{page}.title"),
              f"{path} title comes from the {lang} catalog")
        check(f"3.{lang}.{page}.noplaceholder", "{{" not in r.text,
              f"{path} ships no unfilled token")
        # The page must look like the site it belongs to: navbar, footer and
        # the same versioned stylesheet the app uses. A catalog page without
        # chrome reads as an older, unrelated site.
        check(f"3.{lang}.{page}.chrome",
              'class="navbar"' in r.text and "XMR JACKPOT" in r.text
              and "<footer>" in r.text and "footer-box" in r.text,
              f"{path} carries the navbar and the Kraken CTA footer")
        check(f"3.{lang}.{page}.style",
              f'href="{srv.STYLE_HREF}"' in r.text,
              f"{path} links the versioned stylesheet {srv.STYLE_HREF}")
        check(f"3.{lang}.{page}.htmlcache",
              r.headers.get("cache-control") in ("no-cache", "no-store"),
              f"{path} must not be heuristically cached: {r.headers.get('cache-control')}")
        if page == "download":
            check(f"3.{lang}.download.qr", "<svg" in r.text
                  and srv.STRATUM_URL in r.text,
                  f"{path} carries the QR and the endpoint")
            check(f"3.{lang}.download.statuslink",
                  f'href="{i18n.page_path(lang, "status")}"' in r.text,
                  f"{path} links to its own status page")
            # Two products, not one: the free miner bot and the paid Scout.
            check(f"3.{lang}.download.products",
                  'href="/api/miner-download"' in r.text
                  and 'href="/downloads/KrakenScoutBundle.zip"' in r.text
                  and 'href="/#dashboard"' in r.text,
                  f"{path} offers the miner, the bundle and the Scout unlock")
            check(f"3.{lang}.download.paywall",
                  srv._scout_offer()["price"] + " XMR" in r.text
                  and escape(i18n.t(lang, "download.treasuryTitle")) in r.text,
                  f"{path} quotes the live Scout price and the treasury split")
            # The footer's own comment mentions app.js; what must not ship is
            # the script tag itself.
            check(f"3.{lang}.download.h1",
                  r.text.count("<h1") == 1
                  and 'src="/static/app.js' not in r.text,
                  f"{path} has one h1 and no SPA scripts")
        else:
            check(f"3.{lang}.status.noindex", 'content="noindex"' in r.text,
                  f"{path} stays out of the index")
            check(f"3.{lang}.status.nostore",
                  r.headers.get("cache-control") == "no-store",
                  f"{path} is never cached")

print("== 4. URL HYGIENE ==")
for page in ("download", "status"):
    r = client.get(f"/en/{page}", follow_redirects=False)
    check(f"4.en.{page}", r.status_code == 301
          and r.headers.get("location") == f"/{page}",
          f"/en/{page} -> {r.status_code} {r.headers.get('location')}")
r = client.get("/lang/fr", params={"next": "/de/download"}, follow_redirects=False)
check(4.2, r.status_code == 303 and r.headers.get("location") == "/de/download",
      f"selector redirects and remembers: {r.status_code} {r.headers.get('location')}")
check(4.3, r.cookies.get("xmrj_lang") == "fr", "the cookie carries the choice")
r = client.get("/lang/fr", params={"next": "//evil.example/x"}, follow_redirects=False)
check(4.4, r.headers.get("location") == "/", "protocol-relative next is refused")
r = client.get("/lang/fr", params={"next": "https://evil.example/"}, follow_redirects=False)
check(4.5, r.headers.get("location") == "/", "absolute next is refused")
r = client.get("/lang/xx", follow_redirects=False)
check(4.6, r.status_code == 404 and 'content="noindex"' in r.text,
      "an unknown language code is a real 404")
r = client.get("/xx/download", follow_redirects=False)
check(4.7, r.status_code == 404, f"an unknown language prefix -> {r.status_code}")

print("== 5. AUTO-SELECTION ==")
# The selector test above left its cookie in the jar; sections 5 and 6 are about
# first-time visitors, so start them with an empty jar.
client.cookies.clear()
r = client.get("/download", headers={"Accept-Language": "es-MX,es;q=0.9"},
               follow_redirects=False)
check(5.1, r.status_code == 302 and r.headers.get("location") == "/es/download",
      f"Spanish browser -> {r.status_code} {r.headers.get('location')}")
check(5.2, "accept-language" in (r.headers.get("vary") or "").lower(),
      "the redirect varies on Accept-Language")
r = client.get("/status", headers={"Accept-Language": "de"}, follow_redirects=False)
check(5.3, r.status_code == 302 and r.headers.get("location") == "/de/status",
      f"German browser on /status -> {r.headers.get('location')}")
r = client.get("/download", headers={"Accept-Language": "es"},
               cookies={"xmrj_lang": "en"}, follow_redirects=False)
check(5.4, r.status_code == 200, "a remembered English choice is not overridden")
r = client.get("/download", headers={"Accept-Language": "es"},
               cookies={"xmrj_lang": "fr"}, follow_redirects=False)
check(5.5, r.status_code == 302 and r.headers.get("location") == "/fr/download",
      "a remembered choice follows the visitor to the English URL")
r = client.get("/download", follow_redirects=False)
check(5.6, r.status_code == 200, "no header (crawler, curl) gets English")
r = client.get("/es/download", headers={"Accept-Language": "en"},
               follow_redirects=False)
check(5.7, r.status_code == 200, "an explicit localized URL is never bounced away")

print("== 6. REGION INFERENCE ==")
client.cookies.clear()
r = client.get("/de/download", headers={"CF-IPCountry": "DE"})
check(6.1, "Diese Anfrage kam aus DE." in r.text, "the country is named")
check(6.2, i18n.t("de", "download.regionFar").split("{")[0].strip()[:12] in r.text,
      "a European visitor gets the far-from-Los-Angeles advice")
r = client.get("/download", headers={"CF-IPCountry": "US"})
check(6.3, "This request came from US." in r.text
      and "expect the lowest handshake latency" in r.text,
      "a US visitor gets the same-region advice")
r = client.get("/es/download", headers={"CF-IPCountry": "BR"})
check(6.4, "Latinoamérica" in r.text, "Brazil maps to Latin America")
r = client.get("/download", headers={"CF-IPCountry": "T1"})
check(6.5, i18n.t("en", "download.regionUnknown") in r.text,
      "an anonymity-network code is treated as unknown, not guessed")
r = client.get("/download")
check(6.6, i18n.t("en", "download.regionUnknown") in r.text,
      "no header at all says so")
r = client.get("/download", headers={"CF-IPCountry": "de"})
check(6.7, "This request came from DE." in r.text, "a lowercase country code works")
check(6.8, i18n.region_of("DE")["region"] == "europe"
      and i18n.region_of("JP")["region"] == "asia"
      and i18n.region_of("NG")["region"] == "africa"
      and i18n.region_of("AU")["region"] == "oceania"
      and i18n.region_of("CA")["near"] is True
      and i18n.region_of("ZZ")["region"] == "global",
      "the country map buckets the continents")
check(6.9, i18n.region_of("us")["country"] == "US", "country codes are normalised")
blocked_claims = ("banned", "blocked", "illegal", "prohibited", "restricted")
hits = [(lang, k) for lang in LANGS for k, v in i18n.catalog(lang).items()
        if any(word in str(v).lower() for word in blocked_claims)]
check(6.10, not hits, f"no language claims mining is blocked anywhere: {hits[:3]}")
check(6.11, i18n.region_from_headers({"cf-ray": "a4fbd5d5781e8a29-FRA"})["colo"] == "FRA",
      "the Cloudflare colo is read from CF-Ray")

print("== 7. CRAWLER SURFACE ==")
paths = srv._indexable_paths()
for code in LANGS:
    if code == "en":
        continue
    p = i18n.page_path(code, "download")
    check(f"7.{code}", p in paths, f"the sitemap lists {p}")
check("7.no-status", all("/status" not in p for p in paths),
      "no status page is submitted to search engines")
sm = client.get("/sitemap.xml").text
check("7.xml", all(srv.SITE_ORIGIN + p in sm for p in paths)
      and sm.count("<loc>") == len(paths),
      f"the sitemap carries exactly {len(paths)} URLs")
robots = client.get("/robots.txt").text
check("7.robots", "Disallow: /lang/" in robots and "Disallow: /api/" in robots,
      "robots.txt keeps the selector redirects and the API out")

passed = results.count(True)
failed = results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
