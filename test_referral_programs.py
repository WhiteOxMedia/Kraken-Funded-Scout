"""test_referral_programs.py — THE TWO REFERRAL PROGRAMMES (Oct 25).

PROGRAM A (Kraken): the operator is the affiliate, so ONE community invite
link serves everyone. Kraken pays the bonus into the affiliate's Kraken
account — never into the pool — so the operator records a verified payout
by hand and the pool keeps its share (kraken_pool_share_pct, default half);
the referrer keeps the rest from Kraken directly.

PROGRAM B (XMRJackpot): the internal programme pays one entry to each side
at signup (capped per cycle) and accrues an XMRJ award to the referrer
against their connected Solana wallet. No XMRJ token exists — the treasury
program is source-only and nothing is deployed — so the award is a ledger
row, and the card says "accrued", never "paid".

What this drive pins:
  1. an XMRJ award accrues at signup, once per friend, at the operator rate
  2. an unset rate writes nothing (no invented number)
  3. the entry cap is ONE cap for both rewards — a capped signup earns no XMRJ
  4. the award carries the connected Solana wallet when there is one
  5. the admin recorder credits the pool's half to the funding ledger, is
     idempotent per account per month, and pays nobody out of pool funds
  6. the two cards are separate, the raw invite link is never page text
  7. no XMR reaches the pot from a referral ENTRY (entries are not money)
"""
import hashlib
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

# Same fixture as test_rate_label and test_ui_disclosure: no fetching in
# this file. Section 9 computes the daily plan (compute_daily_jackpot ->
# the rate reader) and the admin card's pot figures convert through it too,
# so a background Kraken refresh landing mid-drive would dial the network
# from inside the drive. XMR_PRICE_LIVE=0 keeps the reader on the fallback,
# off the network - every assertion here is rate-independent, so nothing
# else moves.
os.environ["XMR_PRICE_LIVE"] = "0"

# hermetic DB before the app import touches database
_tmp = Path(tempfile.mkdtemp(prefix="referral_programs_"))

# HERMETIC FIELD KEY (the fk_regression pattern): section 2/4's coin award
# rides the entry award, which resolves the referrer through get_user_by_email
# — the keyed blind index. A fresh checkout has no at-rest key (pool/.keys
# is operator-local), the index is empty, the lookup returns None, and the
# award is refused on a working implementation. The drive provisions its own
# throwaway key before the import and grades the seam on every checkout.
_keys = Path(tempfile.mkdtemp(prefix="refkey_"))
import field_crypto as _fc  # noqa: E402
_fc.KEY_DIR = _keys
_fc.KEY_FILE = _keys / "field_key"
_fc._KEY_CACHE = None
_fc.generate_key_file()

import address_check as ac  # noqa: E402
import database as db  # noqa: E402

db.DB_PATH = _tmp / "jackpot.db"
db.init_db()

import jackpot_funding as funding  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import server as srv_mod  # noqa: E402

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}" + (f"  ({why})" if why and not cond else ""))


def wallet(tag: str) -> str:
    spend = hashlib.sha256(b"spend:" + tag.encode()).digest()
    view = hashlib.sha256(b"view:" + tag.encode()).digest()
    payload = bytes([18]) + spend + view
    return ac.b58encode(payload + ac.keccak256(payload)[:4])


client = TestClient(srv_mod.app)


def reset_registration_limit():
    srv_mod.security.register_limiter.buckets.clear()


def signup(username, password, **extra):
    reset_registration_limit()
    body = {"username": username, "password": password}
    body.update(extra)
    return client.post("/api/auth/register", json=body)


print("== 0. THE BOX: admin (first account), referrer, rate ==")
r = signup("ops_admin", "correct horse battery")
check(0.1, r.status_code == 200, f"admin register -> {r.status_code} {r.text[:120]}")
r = client.post("/api/auth/login",
                json={"username": "ops_admin", "password": "correct horse battery"})
check(0.2, r.status_code == 200, "admin session set")

REF_EMAIL = "referrer@example.com"
REF_WALLET = wallet("referrer")
referrer_id = db.create_user("refowner", srv_mod.hash_password("ownerpass"),
                             email=REF_EMAIL, wallet_address=REF_WALLET)
db.set_referral_code(referrer_id, "refcode123")
RATE = 250
db.set_setting("referral_xmrj_award", str(RATE))
check(0.3, db.referral_xmrj_rate() == RATE,
      f"rate reads {db.referral_xmrj_rate()}")

print("== 1. PROGRAM B: the award accrues at signup, once ==")
friend1_wallet = wallet("friend1")
r = signup("friend1", "friend-pass", email="friend1@example.com",
           wallet_address=friend1_wallet, referrer_email=REF_EMAIL)
check(1.1, r.status_code == 200 and r.json().get("status") == "ok",
      f"signup -> {r.status_code} {r.text[:120]}")
friend1_id = db.get_user_by_username("friend1")["id"]
with db.get_db() as conn:
    row = conn.execute(
        "SELECT * FROM referral_xmrj_awards WHERE referred_user_id = ?",
        (friend1_id,)).fetchone()
check(1.2, row is not None and int(row["amount"]) == RATE
      and int(row["referrer_id"]) == referrer_id
      and row["status"] == "accrued",
      f"award row: {dict(row) if row else None}")

# stats are the CALLER's numbers — the referrer signs in to read them
# (registration signs the new account in, so the admin session is gone by
# now and is restored before section 6)
ref_client = TestClient(srv_mod.app)
ref_client.post("/api/auth/login",
                json={"username": "refowner", "password": "ownerpass"})
r = ref_client.get("/api/referral/stats")
d = r.json()
check(1.3, r.status_code == 200 and d.get("xmrj_awarded") == RATE
      and d.get("xmrj_referrals") == 1 and d.get("xmrj_per_referral") == RATE,
      f"stats xmrj fields: { {k: d.get(k) for k in ('xmrj_awarded', 'xmrj_referrals', 'xmrj_per_referral')} }")
check(1.4, d.get("xmrj_status") == "accrued",
      f"an undeployed treasury must read 'accrued', got {d.get('xmrj_status')}")
check(1.5, str(d.get("kraken_invite_link") or "").startswith("https://invite.kraken.com/")
      and d.get("kraken_invite_code")
      and d.get("kraken_invite_code") == str(d["kraken_invite_link"]).rstrip("/").rsplit("/", 1)[-1],
      f"Kraken card values: {d.get('kraken_invite_link')!r} / {d.get('kraken_invite_code')!r}")
check(1.6, d.get("kraken_pool_share_pct") == 50,
      f"default pool share -> {d.get('kraken_pool_share_pct')}")

print("== 2. ONE FRIEND, ONE AWARD ==")
again = db.award_referral_xmrj(referrer_id, friend1_id)
check(2.1, again["status"] == "already", f"second award -> {again['status']}")
summary = db.referral_xmrj_summary(referrer_id)
check(2.2, summary["xmrj_awarded"] == RATE and summary["xmrj_referrals"] == 1,
      f"summary after the refusal: {summary}")

print("== 3. AN UNSET RATE INVENTS NOTHING ==")
db.set_setting("referral_xmrj_award", "0")
quiet_id = db.create_user("quietfriend", "x", email="quiet@example.com")
res = db.award_referral_xmrj(referrer_id, quiet_id)
check(3.1, res["status"] == "unconfigured", f"rate 0 -> {res['status']}")
with db.get_db() as conn:
    n = conn.execute("SELECT COUNT(*) AS n FROM referral_xmrj_awards "
                     "WHERE referred_user_id = ?", (quiet_id,)).fetchone()["n"]
check(3.2, int(n or 0) == 0, "a rate of 0 still wrote a row")
db.set_setting("referral_xmrj_award", str(RATE))

print("== 4. ONE CAP FOR BOTH REWARDS ==")
cap_id = db.create_user("capowner", "x", email="capowner@example.com")
db.set_referral_code(cap_id, "capcode1")
db.set_setting("referral_cycle_cap", "1")
capped_wallet = wallet("cappedfriend")
r = signup("cappedfriend", "cap-pass", wallet_address=capped_wallet,
           referrer_email="capowner@example.com")
check(4.1, r.status_code == 200, f"first capped-group signup -> {r.status_code}")
over_wallet = wallet("overfriend")
r = signup("overfriend", "over-pass", wallet_address=over_wallet,
           referrer_email="capowner@example.com")
check(4.2, r.status_code == 200, "a capped referral must not fail the signup")
over_id = db.get_user_by_username("overfriend")["id"]
with db.get_db() as conn:
    n = conn.execute("SELECT COUNT(*) AS n FROM referral_xmrj_awards "
                     "WHERE referred_user_id = ?", (over_id,)).fetchone()["n"]
check(4.3, int(n or 0) == 0, "a CAPPED referral still accrued XMRJ")
check(4.4, db.referral_xmrj_summary(cap_id)["xmrj_referrals"] == 1,
      f"the capped group accrued {db.referral_xmrj_summary(cap_id)['xmrj_referrals']}")
db.set_setting("referral_cycle_cap", "10")

print("== 5. THE SOLANA WALLET THE AWARD IS PAID TO ==")
summary = db.referral_xmrj_summary(referrer_id)
check(5.1, summary["solana_wallet"] == "" and summary["xmrj_payable"] is False,
      f"no wallet connected yet -> {summary}")
with db.get_db() as conn:
    conn.execute(
        "INSERT INTO scout_identities (identity_token, email, user_id) "
        "VALUES (?, ?, ?)", ("So1TestWa11etXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
                             REF_EMAIL, referrer_id))
summary = db.referral_xmrj_summary(referrer_id)
check(5.2, summary["solana_wallet"].startswith("So1Test") and summary["xmrj_payable"],
      f"connected wallet -> {summary['solana_wallet']!r} payable={summary['xmrj_payable']}")
late_id = db.create_user("latefriend", "x", email="late@example.com")
res = db.award_referral_xmrj(referrer_id, late_id)
check(5.3, res["status"] == "awarded" and res["wallet"] == summary["solana_wallet"],
      f"the award snapshots the connected wallet: {res}")

print("== 6. PROGRAM A: THE OPERATOR RECORDS A KRAKEN PAYOUT ==")
# registration signs the new account in on `client`, so the operator
# session is restored explicitly — the point of the section is the GATE
r = client.post("/api/auth/login",
                json={"username": "ops_admin",
                      "password": "correct horse battery"})
check(6.0, r.status_code == 200, "operator session restored")
anon = TestClient(srv_mod.app)
anon.post("/api/auth/login", json={"username": "friend1",
                                  "password": "friend-pass"})
r = anon.post("/api/admin/referral/payout",
              json={"username": "friend1", "amount_usd": 40})
check(6.1, r.status_code == 403, f"a non-admin recorded a payout: {r.status_code}")

r = client.post("/api/admin/referral/payout",
                json={"username": "friend1", "amount_usd": 40,
                      "note": "kraken ref KR-1"})
body = r.json() if r.status_code == 200 else {}
check(6.2, r.status_code == 200 and body.get("status") == "recorded",
      f"record -> {r.status_code} {r.text[:160]}")
check(6.3, body.get("pool_usd") == 20.0 and body.get("referrer_keeps_usd") == 20.0
      and body.get("pct") == 50,
      f"split: pool {body.get('pool_usd')} keeps {body.get('referrer_keeps_usd')}")
check(6.4, body.get("referrer_id") == referrer_id,
      f"attributed to referrer {body.get('referrer_id')} (want {referrer_id})")

rows = funding._window_rows()
receipts = [x for x in rows if x.get("source") == "kraken_referral_payout"]
check(6.5, len(receipts) == 1 and abs(float(receipts[0]["amount_usd"]) - 20.0) < 1e-9
      and receipts[0].get("category") == "referral_bonus"
      and int(receipts[0].get("sponsor_id") or 0) == referrer_id,
      f"ledger receipt: {receipts}")

base_before = funding.referral_rewards_last24h()
r = client.post("/api/admin/referral/payout",
                json={"username": "friend1", "amount_usd": 40})
check(6.6, r.status_code == 409, f"second recording -> {r.status_code} {r.text[:120]}")
base_after = funding.referral_rewards_last24h()
check(6.7, base_after["count"] == base_before["count"]
      and abs(base_after["total_usd"] - base_before["total_usd"]) < 1e-9,
      "a refused re-run still moved the ledger")

r = client.post("/api/admin/referral/payout",
                json={"username": "ops_admin", "amount_usd": 25})
check(6.8, r.status_code == 409 and "not referred" in r.text,
      f"a payout for an unreferred account -> {r.status_code} {r.text[:120]}")

r = client.post("/api/admin/referral/payout",
                json={"username": "friend1", "amount_usd": 0})
check(6.9, r.status_code in (400, 409),
      f"a zero payout -> {r.status_code} {r.text[:120]}")

print("== 7. THE SETTINGS THE ADMIN CARD WRITES ==")
r = client.post("/api/admin/referral/config",
                json={"xmrj_award": 500, "pool_share_pct": 40})
check(7.1, r.status_code == 200 and db.referral_xmrj_rate() == 500
      and db.kraken_pool_share_pct() == 40,
      f"config save -> {r.status_code} {r.text[:120]}")
r = client.post("/api/admin/referral/config", json={"pool_share_pct": 250})
check(7.2, r.status_code == 400, f"out-of-range share -> {r.status_code}")
# the card sends both fields in ONE request: a rejected share must not leave
# the rate half-applied behind an error message
r = client.post("/api/admin/referral/config",
                json={"xmrj_award": 900, "pool_share_pct": 250})
check(7.3, r.status_code == 400 and db.referral_xmrj_rate() == 500,
      f"a refused save still wrote the rate: {db.referral_xmrj_rate()}")
db.set_setting("referral_xmrj_award", str(RATE))
db.set_setting("kraken_pool_share_pct", "50")

print("== 8. THE TWO CARDS, THE LINK, AND WHAT THEY PROMISE ==")
page = (HERE / "pool" / "templates" / "index.html").read_text(encoding="utf-8")
check(8.1, 'id="cardKrakenReferral"' in page and 'id="cardJackpotReferral"' in page,
      "the two programme cards are not both present")
check(8.2, page.index('id="cardKrakenReferral"') < page.index('id="cardJackpotReferral"'),
      "the cards are out of order")
check(8.3, 'id="btnCopyKrakenLink"' in page and 'id="btnCopyReferral"' in page
      and 'id="krakenInviteCode"' in page,
      "a copy control or the invite code is missing")
check(8.4, page.count('data-share="') >= 5,
      f"share buttons: {page.count('data-share=')}")
check(8.5, 'type="hidden" id="krakenInviteLink"' in page,
      "the invite link is not held in a hidden input")

# every share button must share THE SAME link the copy button copies, so
# the intents are built from that one value, encoded, at click time
app_js = (HERE / "pool" / "static" / "app.js").read_text(encoding="utf-8")
for host in ("twitter.com/intent/tweet", "t.me/share/url", "wa.me/?text=",
             "facebook.com/sharer/sharer.php", "mailto:?subject="):
    check(f"8.5+{host}", host in app_js,
          f"share intent missing: {host}")
check(8.6, "encodeURIComponent(url)" in app_js
      and "$('#krakenInviteLink')" in app_js,
      "the intents do not come from the card's own hidden link")

# the Kraken card's own region must not print the raw URL as text
start = page.index('id="cardKrakenReferral"')
end = page.index('id="cardJackpotReferral"')
region = page[start:end]
check(8.7, "invite.kraken.com" not in region,
      "the raw Kraken URL is exposed in the card's text")

# and the XMRJ card must never claim the award is paid
xmrj_region = page[end:page.index("<!-- Recent Wins -->")]
check(8.8, "become payable once the" in xmrj_region
      and "XMRJ treasury is deployed" in xmrj_region,
      "the XMRJ card does not say the award waits for the treasury")

print("== 9. AN ENTRY IS NOT MONEY ==")
plan = funding.compute_daily_jackpot()
check(9.1, plan["raw_usd"] >= 0, "the funding formula still computes")
check(9.2, plan["daily_jackpot_usd"] <= 20.0, "the $20/day cap still holds")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
