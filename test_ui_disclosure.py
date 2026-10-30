"""test_ui_disclosure.py — THE UI TELLS THE TRUTH (Oct 24).

CHARTER: the page must describe the backend the pool actually runs, and
must disclose what is simulated. Every number the copy states has to come
from the setting the engine or the checkout reads — not from a literal
typed into the template — and the browser has to be able to see which
miners and drawings are demo data.

This drive pins that contract end to end against the real app module:

  1. NO LEFTOVER TOKENS: / fills every {{...}} (and the standalone pages
     do too). A shipped brace is a page admitting it does not know.
  2. THE COPY FOLLOWS THE SETTINGS: change house_tax_percent,
     drawing_interval_hours, claim_window_days, pro_unlock_days and the
     Pro price, and the rendered page states the new values — as the
     engine and /api/stats report them.
  3. THE PRICE IS ONE PRICE: the homepage, the dashboard payload
     (/api/gamification progression.pro_price) and the checkout reader
     (server._pro_price) all quote the same XMR.
  4. THE DEMO SPLIT IS PUBLISHED: /api/stats counts simulated miners,
     /api/miners flags them, /api/social-proof keeps simulated payouts
     OUT of "paid out", and /winners labels them (demo).
  5. THE BROWSER SAYS IT: app.js tags demo leaderboard rows and renders
     the disclosure line; checkout tells a signed-out visitor to sign in
     instead of claiming the checkout is broken.
  6. THE DEAD ROUTES EXIST: /api/payment/subaddress, /api/payment/status
     and /scout answer (their handlers shipped without decorators, so
     the Real Play page's deposit box could never fill).
  7. THE REFERRAL BLOCK IS A PARTNER OFFER: Kraken's $300/$900/$3,000
     ladder is framed as Kraken's own term — set, capped and paid by
     Kraken, which the pool neither sets nor enforces — and nothing
     claims that opening an account through the link funds the pot.
  8. EVERY KRAKEN CLAIM SAYS WHOSE IT IS: the page labels Kraken-owned
     terms (challenge, tiers, ladder, bonuses) apart from pool-owned ones
     (entries, drawings, Scout Pro), and no promo line says Kraken pays
     the jackpot — the operator credits what it records.
  9. REFERRALS BUY ENTRIES, NOT POT FUNDS: the pool's own referral card
     promises bonus entries only. It never says a referral grows the pot
     — that was B9's real defect, not the absence of a pot contribution.
  10. THE DOWNLOAD PAGE, SIX LANGUAGES: each catalog page carries the
     claim-source legend and no longer renders the retired XMRJ treasury
     claim or the XMRJ unlock path.
"""
import html as html_mod
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

# No fetching in this file: check 3.2 demands that the rate the homepage
# render quoted is still the rate _pro_price() quotes at check time, and
# a background Kraken-ticker refresh landing between the two made the
# drive flaky (run hermetic 97/1, network-works 98/0, repeatedly).
# Same fixture as test_rate_label: XMR_PRICE_LIVE=0 before the imports.
os.environ["XMR_PRICE_LIVE"] = "0"

# hermetic DB before the app import touches database (same pattern as the
# other server drives: the live ledger is never read or written).
_tmp = Path(tempfile.mkdtemp(prefix="ui_disclosure_"))
import database as db  # noqa: E402

db.DB_PATH = _tmp / "jackpot.db"
db.init_db()

from fastapi.testclient import TestClient  # noqa: E402

import server as srv_mod  # noqa: E402

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}" + (f"  ({why})" if why and not cond else ""))


def text_of(html):
    body = re.sub(r"<script\b.*?</script>", " ", html, flags=re.S | re.I)
    body = re.sub(r"<style\b.*?</style>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<!--.*?-->", " ", body)
    return html_mod.unescape(re.sub(r"<[^>]+>", " ", body))


client = TestClient(srv_mod.app)

print("== 1. EVERY TOKEN IS FILLED ==")
root = client.get("/")
home = root.text
check(1.1, root.status_code == 200, f"GET / -> {root.status_code}")
check(1.2, not re.search(r"\{\{[A-Z0-9_]+\}\}", home),
      "a literal {{TOKEN}} shipped to the browser: "
      + ", ".join(sorted(set(re.findall(r"\{\{[A-Z0-9_]+\}\}", home)))[:5]))
for path in ("/how-to-play", "/winners", "/download"):
    page = client.get(path)
    check(1.3, page.status_code == 200
          and not re.search(r"\{\{[A-Z0-9_]+\}\}", page.text),
          f"{path} ships an unfilled token")

print("== 2. THE COPY FOLLOWS THE SETTINGS ==")
db.set_setting("house_tax_percent", "20")
db.set_setting("drawing_interval_hours", "12")
db.set_setting("claim_window_days", "3")
db.set_setting("pro_unlock_days", "50")
db.set_setting("free_tickets_per_shares", "50")
db.set_setting("scout_pro_price_usd", "50")

home = client.get("/").text
body = text_of(home)
check(2.1, "80%" in body and "20%" in body,
      "the payout split does not follow house_tax_percent=20")
check(2.2, "every 12 hours" in body,
      "the draw cadence does not follow drawing_interval_hours=12")
check(2.3, "3 days" in body,
      "the claim window does not follow claim_window_days=3")
check(2.4, "50 contributing days" in body,
      "the Pro path does not follow pro_unlock_days=50")
check(2.5, "50 valid shares" in body and "50 units" in body,
      "the entry unit does not follow free_tickets_per_shares=50")

stats = client.get("/api/stats").json()
check(2.6, stats["house_tax_percent"] == 20
      and stats["drawing_interval_hours"] == 12
      and stats["free_tickets_per_shares"] == 50,
      f"/api/stats disagrees with the page: {stats}")

print("== 3. ONE PRICE, THREE SURFACES ==")
price_xmr, price_usd = srv_mod._pro_price()
check(3.1, price_usd == 50.0,
      f"_pro_price did not read scout_pro_price_usd ({price_usd})")
check(3.2, ("%g XMR" % price_xmr) in body and ("$%.2f" % price_usd) in body,
      "the homepage does not quote the checkout's XMR/≈USD pair")

uid = db.create_user("truthuser", srv_mod.hash_password("truth-pass"),
                     wallet_address="4" + "truth".ljust(94, "t"))
login = client.post("/api/auth/login",
                    json={"username": "truthuser", "password": "truth-pass"})
check(3.3, login.status_code == 200, f"login -> {login.status_code}")
gam = client.get("/api/gamification")
check(3.4, gam.status_code == 200, f"/api/gamification -> {gam.status_code}")
prog = (gam.json() or {}).get("progression") or {}
check(3.5, (prog.get("pro_price") or {}).get("xmr") == price_xmr,
      f"dashboard price {prog.get('pro_price')} != checkout {price_xmr}")
check(3.6, prog.get("days_required") == 50,
      f"dashboard target {prog.get('days_required')} != setting 50")

print("== 4. THE DEMO SPLIT IS PUBLISHED ==")
real_wallet = "4" + "realminer".ljust(94, "r")
demo_wallet = "4" + "demominer".ljust(94, "d")
real_id = db.get_or_create_miner(real_wallet, "real-rig")["id"]
demo_id = db.get_or_create_miner(demo_wallet, "demo-rig")["id"]
with db.get_db() as conn:
    conn.execute("UPDATE miners SET is_demo = 1, shares_valid = 500, "
                 "shares_total = 500 WHERE id = ?", (demo_id,))
    conn.execute("UPDATE miners SET is_demo = 0, shares_valid = 100, "
                 "shares_total = 100 WHERE id = ?", (real_id,))
    now_ts = int(__import__("time").time())
    conn.execute(
        "INSERT INTO drawings (jackpot_amount, winner_id, house_tax, "
        "total_tickets, status, drawn_at) VALUES (0.20, ?, 0.02, 10, "
        "'drawn', ?)", (real_id, now_ts - 100))
    # the simulated drawing is the NEWEST: recent_winner must be it, and
    # labeled simulated (equal timestamps would make the pick arbitrary)
    conn.execute(
        "INSERT INTO drawings (jackpot_amount, winner_id, house_tax, "
        "total_tickets, status, drawn_at) VALUES (0.30, ?, 0.03, 20, "
        "'drawn', ?)", (demo_id, now_ts))

stats = client.get("/api/stats").json()
check(4.1, stats.get("demo_miners") == 1,
      f"/api/stats demo_miners={stats.get('demo_miners')}")
check(4.2, stats.get("demo_mining_sample") is True,
      "/api/stats does not flag that the sample is partly simulated")

miners = client.get("/api/miners").json()
flagged = [m for m in miners if m.get("is_demo")]
check(4.3, len(flagged) == 1 and flagged[0]["id"] == demo_id,
      f"/api/miners demo flags: {[(m.get('id'), m.get('is_demo')) for m in miners]}")

sp = client.get("/api/social-proof").json()
check(4.4, abs(sp["total_paid_out"] - 0.18) < 1e-9,
      f"paid out {sp['total_paid_out']} includes the simulated prize")
check(4.5, abs(sp["simulated_paid_out"] - 0.27) < 1e-9,
      f"simulated paid out {sp.get('simulated_paid_out')} != 0.27")
check(4.6, sp["total_drawings"] == 1 and sp["simulated_drawings"] == 1,
      f"drawings split {sp['total_drawings']}/{sp.get('simulated_drawings')}")
check(4.7, sp["recent_winner"] and sp["recent_winner"]["simulated"] is True,
      "the newest winner (a demo account) is not labeled simulated")

winners = client.get("/winners").text
check(4.8, "(demo)" in winners,
      "the public winners page does not label simulated winners")

print("== 5. THE BROWSER SAYS IT ==")
app_js = (HERE / "pool" / "static" / "app.js").read_text(encoding="utf-8")
check(5.1, "demoDisclosure" in app_js and "simulated demo accounts" in app_js,
      "app.js does not render the homepage disclosure line")
check(5.2, "m.is_demo" in app_js and "lbDemoNote" in app_js,
      "the leaderboard neither tags nor notes simulated rows")
check(5.3, "miners_demo" in app_js,
      "the admin panel hides the simulated-miner split")
check(5.4, "Sign in to start checkout" in
      (HERE / "pool" / "static" / "scout_checkout.js").read_text(encoding="utf-8"),
      "a signed-out checkout still reports a broken checkout")

print("== 6. THE DEAD ROUTES ANSWER ==")
for path in ("/api/payment/subaddress", "/api/payment/status", "/scout"):
    r = client.get(path, follow_redirects=False)
    check(6.1, r.status_code != 404, f"{path} -> {r.status_code} (unregistered)")
check(6.2, client.get("/scout", follow_redirects=False).status_code == 307,
      "/scout does not redirect to the trailing-slash console")

print("== 7. THE REFERRAL BLOCK IS A PARTNER OFFER ==")
partner = text_of(client.get("/").text)
check(7.1, "Side Pot Partners" in partner,
      "the referral block is not framed as a partner section")
check(7.2, "Partner offers are external opportunities associated with the pool."
      in partner and "governed by the partner's terms" in partner,
      "the canonical partner copy is missing or reworded")
check(7.3, "may contribute to the jackpot pool" in partner,
      "the block does not leave room for partner-funded contributions")
check(7.4, "feeds the jackpot" not in partner,
      "the page still says opening an account through the link funds the pot")
check(7.5, partner.count("Kraken pays") >= 3,
      "the $300/$900/$3,000 ladder is not attributed to Kraken's payout")
check(7.6, "Kraken caps its own referral program at $3,000." in partner
      and "administered and enforced by Kraken" in partner
      and "does not set it" in partner,
      "the $3,000 cap is not stated as Kraken's term, unenforced by the pool")
check(7.7, "does not itself credit the pot" in partner,
      "the block does not say an account opened through the link credits nothing")
ref_home = client.get("/").text
check(7.8, 'rel="nofollow sponsored"' in ref_home
      and 'data-cta-slot="referral-block"' in ref_home,
      "the partner link lost its sponsored/no-follow attribution or its click slot")
check(7.9, "recorded today" in
      (HERE / "pool" / "static" / "refblock.js").read_text(encoding="utf-8"),
      "the counter does not describe bonuses the operator recorded")

print("== 8. EVERY KRAKEN CLAIM SAYS WHOSE IT IS ==")
labels = text_of(client.get("/").text)
check(8.1, "Kraken-owned (partner)" in labels and "Pool-owned (XMR Jackpot)" in labels,
      "the page does not carry the two claim-source labels")
check(8.2, "Kraken never pays the jackpot directly" in labels,
      "the legend does not name the interface between partner money and the pot")
check(8.3, "pays you and pays the jackpot" not in labels,
      "a CTA still says Kraken pays the jackpot")
check(8.4, "referral bonuses add to the pot as they come in" not in labels,
      "the hero still implies referral bonuses flow into the pot automatically")
check(8.5, "feeds this jackpot" not in labels
      and "funds the pot" not in labels,
      "the page still says an account opened on Kraken funds the pot")
check(8.6, labels.count("Kraken does not pay the jackpot") >= 5,
      "fewer than five partner links disclaim who pays the jackpot")
check(8.7, "Kraken-owned" in labels and "pool-owned" in labels,
      "the entry/bonus split is not labelled at the funding line")
check(8.8, "Kraken-owned tiers" in labels or "Kraken-owned: the funded-account tiers" in labels,
      "the $20/$50/$90 funding tiers are not attributed to Kraken")
app_js = (HERE / "pool" / "static" / "app.js").read_text(encoding="utf-8")
check(8.9, "unlocks community bonuses" not in app_js
      and "once the operator records" in app_js,
      "the wallet note still promises bonuses for connecting a wallet")

print("== 9. REFERRALS BUY ENTRIES, NOT POT FUNDS ==")
refs = text_of(client.get("/").text)
check(9.1, "Referral rewards are entries, not jackpot funding" in refs,
      "the referral card does not say rewards are entries, not pot money")
check(9.2, "grow the pool" not in refs and "increase the grand prize" not in refs,
      "the referral card still says referrals grow the prize")
check(9.3, "Referral bonuses are paid into the daily jackpot" not in refs,
      "a referral line still claims pool bonuses are paid into the pot")
check(9.4, "Referral rewards do not fund the jackpot" in refs,
      "the reward list does not state that referrals do not fund the pot")
check(9.5, "Kraken does not pay the pot" in refs,
      "the same list lost its partner attribution")
check(9.6, "you both get an entry when they sign up with your email" in refs,
      "the card does not state the two-sided signup entry")
check(9.7, "Limit 10 referral rewards per jackpot cycle" in refs,
      "the card does not state the per-cycle limit")
check("9.7b", "1 XMRJ for each friend who signs up" in refs,
      "the card does not state the 1-XMRJ-per-friend award")
check(9.8, 'id="signupReferrerEmail"' in client.get("/").text,
      "the signup form has no field for the referrer's email")

print("== 10. THE DOWNLOAD PAGE, SIX LANGUAGES ==")
POT_WORDS = ("treasury", "Treasur", "tesorer", "tr\u00e9sor", "tesour",
             "\u043a\u0430\u0437\u043d\u0430\u0447\u0435")
for lang in ("en", "de", "es", "fr", "pt", "ru"):
    path = srv_mod.i18n.page_path(lang, "download")
    resp = client.get(path)
    page_text = text_of(resp.text)
    check(10.1, resp.status_code == 200, f"{path} -> {resp.status_code}")
    check(10.2, srv_mod.i18n.t(lang, "download.ownerTitle") in page_text
          and srv_mod.i18n.t(lang, "download.ownerMeet") in page_text,
          f"{path} has no claim-source legend")
    check(10.3, "XMRJ" not in page_text and "XMRJ" not in resp.text,
          f"{path} still renders the retired XMRJ term")
    check(10.4, not any(w in page_text for w in POT_WORDS),
          f"{path} still claims XMRJ treasury settlement")

print("== 11. THE FUNDED TIERS ARE KRAKEN'S, AS PUBLISHED ==")
funded = text_of(client.get("/").text)
check(11.1, all(t in funded for t in ("$1,000 funded", "$5,000 funded",
                                       "$10,000 funded")),
      "the published $20/$50/$90 -> $1K/$5K/$10K tiers are not on the page")
check(11.2, "Kraken Funded" in funded and
      "$20/$50/$90 for $1K/$5K/$10K, as published by Kraken Funded" in funded,
      "the tiers are not attributed to Kraken Funded's price list")
check(11.3, "Payward Oceanic Ltd." in funded
      and "Breakout Trading Group, LLC" in funded
      and "kraken.com/features/funded" in funded,
      "the operating provider, evaluator or source link is missing")
check(11.4, "Kraken.com created the challenge" not in funded
      and "capital provider: Kraken.com" not in funded
      and "Kraken.com funds the account" not in funded,
      "an old line still says Kraken.com itself runs the programme")
check(11.5, "you keep 80% of the profits" in funded
      and "the profits are yours" not in funded,
      "the profit split is not stated as Kraken Funded publishes it (80%)")
check(11.6, "not available in Maine, Massachusetts or New York" in funded,
      "the page does not carry Kraken Funded's state restriction")

print("== 12. THE WALLET NOTE FOLLOWS THE RELAY ==")
# THE WALLET-NOTE GATE (Oct 29 audit): the Verus-shaped wallet input used
# to reveal ONE note whose text promised relay-lane jackpot entries from
# the reality that no relay lane may exist. Now app.js picks between two
# note WORDS on the runtime truth (/api/stats -> vrsc_relay_live, the
# same XMRJ_VRSC_STRATUM the installers branch on) and the default is the
# OFFLINE note: until the flag lands, the page may only under-promise.
_page = client.get("/").text
check(12.1, 'id="realVerusNote"' in _page and 'id="realVerusNoteOffline"' in _page,
      "the homepage carries both note bodies")
check(12.2,
      re.search(r'id="realVerusNote"[^>]*display:none', _page)
      and re.search(r'id="realVerusNoteOffline"[^>]*display:none', _page),
      "a note body ships visible before any gate can hide it")
_app_js = (HERE / "pool" / "static" / "app.js").read_text(encoding="utf-8")
check(12.3, "(on && relayUp) ? '' : 'none'" in _app_js
      and "(on && !relayUp) ? '' : 'none'" in _app_js,
      "app.js does not map relayUp onto exactly one of the two notes")
check(12.4, "let VRSC_RELAY_LIVE = false;" in _app_js
      and "VRSC_RELAY_LIVE = !!d.vrsc_relay_live" in _app_js
      and "'xmrjackpot:relay'" in _app_js,
      "the offline default, the /api/stats latch or the re-notify is gone "
      "(a note revealed before stats landed would never flip)")
old = os.environ.get("XMRJ_VRSC_STRATUM")
os.environ.pop("XMRJ_VRSC_STRATUM", None)
check(12.5, client.get("/api/stats").json().get("vrsc_relay_live") is False,
      "stats claims a relay lane with none configured (the gate reads env "
      "at request time or the flag defaulted to truthy)")
os.environ["XMRJ_VRSC_STRATUM"] = "relay.example.com:3956"
check(12.6, client.get("/api/stats").json().get("vrsc_relay_live") is True,
      "stats refuses to admit the configured relay lane")
if old is None:
    os.environ.pop("XMRJ_VRSC_STRATUM", None)
else:
    os.environ["XMRJ_VRSC_STRATUM"] = old
check(12.7, "cannot earn jackpot entries here" in _page
      and "NOT live on this deployment" in _page,
      "the offline note lost its plain wording of the no-relay truth")
check(12.8, "home.realVerusNoteOffline" in _page,
      "the catalog injection lost the offline key (the six-language "
      "note would fall back to untranslated English)")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
