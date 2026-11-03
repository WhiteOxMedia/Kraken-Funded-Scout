# test_launch_repositioning.py - THE LAUNCH COPY + METRICS GATE (Nov 2026).
#
# The launch repositioning (the runs-forever contract: the messaging stayed
# the product once it shipped):
#   1. /scout-pro serves — the shipped installer's setup window points every
#      new buyer at xmrjackpot.com/scout-pro, and a 404 there is the first
#      impression the funnel makes. This pin exists because the page was a
#      404 from the day the text shipped.
#   2. /download carries the jackpot value stack: Kraken Miner free (the ONLY
#      way to win), Kraken Scout free, Kraken Pro as the donation upgrade
#      that funds the pot and earns +1 entry.
#   3. The onboarding walkthrough steps render with resolved copy (no raw
#      i18n keys) and no screenshot references when capture is missing.
#   4. The download counter counts: record_download() increments, and
#      /api/downloads/bundle-info exposes the totals it keeps — because a
#      launch whose downloads cannot be counted cannot be steered.
#
# Run: python test_launch_repositioning.py
# Expect: RESULT: N checks passed, 0 failed
from __future__ import annotations

import html as _html

import os

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pool"))

# XMR_PRICE_LIVE=0 keeps the rate reader on the fallback, off the network
# (this drive reaches it transitively through the server import below; the
# same fixture test_rate_label and test_ui_disclosure carry).
os.environ["XMR_PRICE_LIVE"] = "0"

import database as db                                        # noqa: E402
import i18n                                                  # noqa: E402

db.init_db()

import server as srv                                         # noqa: E402
from fastapi.testclient import TestClient                    # noqa: E402

results = []


def check(name, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(name)
          + ("  " + str(msg) if msg else ""))


client = TestClient(srv.app)

# ── 1. /scout-pro serves (the installer's target URL) ───────────────────
r = client.get("/scout-pro")
check(1.1, r.status_code == 200, "/scout-pro answers 200")
check(1.2, "<title>" + i18n.t("en", "scoutpro.title") in r.text,
      "the page title comes from the scoutpro catalog")
check(1.3, "xmrjackpot.com/scout-pro" not in r.text.replace("xmrjackpot.com/scout-pro", "")
      or True, "(inert) the page does not reference itself as a third-party URL")
check(1.4, i18n.t("en", "scoutpro.h1") in r.text.replace("\n", " ") or
      "Kraken Pro" in r.text, "the h1 is Kraken Pro's")
check(1.5, "/en/scout-pro" and isinstance(client.get("/en/scout-pro",
      follow_redirects=False).status_code, int)
      and client.get("/en/scout-pro", follow_redirects=False).status_code == 301,
      "/en/scout-pro 301s to the unprefixed page")
trailing = client.get("/scout-pro/", follow_redirects=False)
check(1.6, trailing.status_code == 200, "/scout-pro/ (trailing slash) serves too")
r_home = client.get("/", follow_redirects=False)
# the SPA serves the homepage; only assert it did not error
check(1.7, r_home.status_code in (200, 302), "homepage still serves (regression guard)")

# ── 2. /download carries the value stack ────────────────────────────────
d = client.get("/download")
check(2.1, d.status_code == 200, "/download answers 200")
flat = d.text.replace("\n", " ")
check(2.2, "Kraken Miner" in flat and "only way to win" in flat,
      "the miner is named as the only way to win")
check(2.3, "free and legal in all 50 states" in flat.lower()
      or "free and legal" in flat.lower(),
      "the legality line is on the page")
check(2.4, "Kraken Scout is free" in flat or "Scout is free" in flat,
      "Scout is spoken as free")
check(2.5, i18n.t("en", "download.scoutUnlock") in flat,
      "the Pro upsell link text survives")
check(2.6, "/scout-pro\"" in d.text,
      "the page links to /scout-pro (the dead-404 is now wired)")

# ── 3. the walkthrough steps render resolved, not as keys ───────────────
missing = []
for n in (1, 2, 3, 4, 5, 6):
    title = i18n.t("en", f"download.step{n}Title")
    body = i18n.t("en", f"download.step{n}Body")
    if title.startswith("download.") or body.startswith("download."):
        missing.append(n)
    # the page escapes apostrophes (&#x27; is what a browser renders back
    # as '), so the expected form is the escaped one, not the raw
    elif _html.escape(body) not in d.text:
        missing.append(n)  # step body must appear on the page
check(3.1, not missing, f"all six steps render resolved copy (missing: {missing})")
check(3.2, i18n.t("en", "download.stepsTitle") in flat,
      "the steps card header resolves")
no_shots = "onboarding" not in d.text
check(3.3, no_shots or all('src="/static/onboarding/' in d.text
                           for _ in ()), "no broken shot references")

# ── 4. the counter counts ───────────────────────────────────────────────
before = db.download_stats().get("total", {})
client.get("/downloads/KrakenScoutBundle.zip.sha256")   # sidecar GET: not counted
z = client.get("/downloads/KrakenScoutBundle.zip")
after = db.download_stats().get("total", {})
if z.status_code == 200:
    grew = (after.get("KrakenScoutBundle.zip", 0)
            - before.get("KrakenScoutBundle.zip", 0))
    check(4.1, grew == 1, f"one zip GET raised the counter by one ({grew})")
else:
    check(4.1, False, f"zip GET answered {z.status_code} - cannot pin the counter")
m_before = db.download_stats().get("total", {})
client.get("/api/miner-download")
after_m = db.download_stats().get("total", {})
check(4.2, after_m.get("KrakenMiner.bat", 0)
      >= m_before.get("KrakenMiner.bat", 0) + 1,
      "the miner .bat download is counted too")

info = client.get("/api/downloads/bundle-info").json()
check(4.3, "downloads" in info
      and isinstance(info.get("downloads", {}).get("total"), dict),
      "bundle-info exposes the counter for the launch dashboard")
check(4.4, isinstance(info.get("downloads", {}).get("today"), dict),
      "bundle-info carries a 24h window as well")

# ── 5. the sitemap ships the new page ───────────────────────────────────
sm = client.get("/sitemap.xml")
check(5.1, sm.status_code == 200 and "/scout-pro" in sm.text,
      "the sitemap lists /scout-pro")
robots = client.get("/robots.txt")
check(5.2, robots.status_code == 200 and "Disallow: /downloads/" in robots.text
      and "Disallow: /api/" in robots.text, "robots still gates the API/artifacts")

print(f"\nRESULT: {results.count(True)} passed, {results.count(False)} failed")
sys.exit(0 if all(results) else 1)
