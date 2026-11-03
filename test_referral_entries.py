"""test_referral_entries.py — THE REFERRAL PROGRAMME AS STATED (Oct 25).

CHARTER: B23 recorded the stated rules but not the code. This drive pins
the implementation, not the copy:

  1. EMAIL ATTRIBUTION: a signup that carries the referrer's EMAIL awards
     one entry to EACH side at signup (entry_unit() tickets a side) and
     records the method as "email".
  2. CODE FALLBACK: the ?ref=CODE link still resolves to the same award
     and the same cap, so an old link is not a dead end.
  3. ONCE PER FRIEND: a second award for the same referred account is
     refused ("already") and mints nothing.
  4. THE CAP: the 11th award for one referrer in a cycle is refused
     ("capped"); the allowance resets when a new drawing lands.
  5. NO WALLET, NO LOSS: a side without a payout wallet holds its entry
     (pending_referral_entries) and gets the ticket when a wallet lands.
  6. ENTRIES, NOT POT MONEY: awards move no XMR into the jackpot and add
     no funding-ledger inflow.
  7. SELF-REFERRAL: an account cannot refer itself.
"""
import hashlib
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

# HERMETIC FIELD KEY (the fk_regression pattern): this drive's contract is
# the EMAIL-ATTRIBUTION award, which runs through get_user_by_email —
# the keyed blind index. A fresh checkout has no at-rest key (pool/.keys
# is operator-local, absent by design), and without one the blind index
# is empty: index_email returns "", referrer lookup returns None, and
# the signup never awards — five honest checks gone red on a working
# implementation. So the drive provisions its own throwaway key here and
# grades the seam identically on every checkout.
_keys = Path(tempfile.mkdtemp(prefix="refkey_"))
import field_crypto as _fc  # noqa: E402
_fc.KEY_DIR = _keys
_fc.KEY_FILE = _keys / "field_key"
_fc._KEY_CACHE = None
_fc.generate_key_file()


# hermetic DB before the app import touches database
_tmp = Path(tempfile.mkdtemp(prefix="referral_entries_"))
import address_check as ac  # noqa: E402
import database as db  # noqa: E402

db.DB_PATH = _tmp / "jackpot.db"
db.init_db()

from fastapi.testclient import TestClient  # noqa: E402

import os
# XMR_PRICE_LIVE=0 keeps the rate reader on the fallback, off the
# network (this drive reaches it transitively through a gateway
# import below - the top-level pin test_rate_pin_fleet.py enforces).
os.environ["XMR_PRICE_LIVE"] = "0"
import server as srv_mod  # noqa: E402

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}" + (f"  ({why})" if why and not cond else ""))


def wallet(tag: str) -> str:
    """A real, checksum-valid mainnet address derived from the tag.

    The signup route validates the wallet with the pool's own decoder, so
    a padded string would 500. Same construction the address module
    documents: version byte + spend + view + 4-byte Keccak checksum.
    """
    spend = hashlib.sha256(b"spend:" + tag.encode()).digest()
    view = hashlib.sha256(b"view:" + tag.encode()).digest()
    payload = bytes([18]) + spend + view
    return ac.b58encode(payload + ac.keccak256(payload)[:4])


def miner_for(addr):
    return db.get_or_create_miner(addr)


def referral_tickets(addr):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM tickets WHERE miner_id = ? "
            "AND source = 'referral'", (miner_for(addr)["id"],)).fetchone()
    return int(row["n"] or 0)


client = TestClient(srv_mod.app)


def reset_registration_limit():
    """The register route allows 5 accounts per IP per hour by design.

    This drive needs a dozen signups to test the cap, so it clears the
    bucket between requests. The limit itself is not what is under test
    here (test_e2e_acceptance covers it) — the award path is.
    """
    srv_mod.security.register_limiter.buckets.clear()

REF_EMAIL = "referrer@example.com"
REF_WALLET = wallet("referrer")
referrer_id = db.create_user("refowner", srv_mod.hash_password("ownerpass"),
                             email=REF_EMAIL, wallet_address=REF_WALLET)
db.set_referral_code(referrer_id, "refcode123")
unit = srv_mod.engine.entry_unit()

print("== 1. EMAIL ATTRIBUTION PAYS BOTH SIDES AT SIGNUP ==")
friend_wallet = wallet("friend1")
r = client.post("/api/auth/register", json={
    "username": "friend1", "password": "friend-pass",
    "email": "friend1@example.com", "wallet_address": friend_wallet,
    "referrer_email": REF_EMAIL,
})
check(1.1, r.status_code == 200 and r.json().get("status") == "ok",
      f"signup -> {r.status_code} {r.text[:120]}")
check(1.2, referral_tickets(REF_WALLET) == unit,
      f"referrer tickets {referral_tickets(REF_WALLET)} != {unit}")
check(1.3, referral_tickets(friend_wallet) == unit,
      f"friend tickets {referral_tickets(friend_wallet)} != {unit}")
with db.get_db() as conn:
    row = conn.execute(
        "SELECT method, tickets FROM referral_awards WHERE referred_user_id = "
        "(SELECT id FROM users WHERE username = 'friend1')").fetchone()
check(1.4, row is not None and row["method"] == "email" and row["tickets"] == unit,
      f"award row: {dict(row) if row else None}")

print("== 2. ONE AWARD PER FRIEND, AND SELF-REFERRAL REFUSED ==")
friend1_id = db.get_user_by_username("friend1")["id"]
again = db.award_referral_entries(referrer_id, friend1_id, tickets=unit)
check(2.1, again["status"] == "already",
      f"second award returned {again['status']}")
check(2.2, referral_tickets(REF_WALLET) == unit,
      "the refused second award still minted tickets")
self_ref = db.award_referral_entries(referrer_id, referrer_id, tickets=unit)
check(2.3, self_ref["status"] == "self", f"self referral -> {self_ref['status']}")

print("== 3. THE CODE PATH AWARDS ONCE, THROUGH THE SAME LEDGER ==")
ref_code = db.get_user(referrer_id)["referral_code"]
friend2_wallet = wallet("friend2")
r = client.post("/api/auth/register", json={
    "username": "friend2", "password": "friend-pass",
    "wallet_address": friend2_wallet, "referral_code": ref_code,
})
check(3.1, r.status_code == 200 and referral_tickets(friend2_wallet) == unit,
      f"code signup tickets {referral_tickets(friend2_wallet)}")
with db.get_db() as conn:
    m = conn.execute(
        "SELECT method FROM referral_awards WHERE referred_user_id = "
        "(SELECT id FROM users WHERE username = 'friend2')").fetchone()
check(3.2, m is not None and m["method"] == "code", f"method {m['method'] if m else None}")

print("== 4. THE PER-CYCLE CAP ==")
cap = int(db.get_setting("referral_cycle_cap", "10"))
print(f"    cap setting: {cap}")
for i in range(3, 3 + cap - 2):          # friend1 + friend2 already spent two
    reset_registration_limit()
    body = {"username": f"bulk{i}", "password": "bulk-pass",
            "wallet_address": wallet(f"bulk{i}"), "referrer_email": REF_EMAIL}
    client.post("/api/auth/register", json=body)
summary = db.referral_award_summary(referrer_id)
check(4.1, summary["awards_this_cycle"] == cap,
      f"awards this cycle {summary['awards_this_cycle']} != cap {cap}")
check(4.2, summary["remaining"] == 0, f"remaining {summary['remaining']}")

blocked_wallet = wallet("blocked")
reset_registration_limit()
r = client.post("/api/auth/register", json={
    "username": "blocked", "password": "blocked-pass",
    "wallet_address": blocked_wallet, "referrer_email": REF_EMAIL,
})
check(4.3, r.status_code == 200 and r.json().get("status") == "ok",
      "a capped referral must not fail the signup")
check(4.4, referral_tickets(blocked_wallet) == 0,
      "the capped friend still got an entry")
status = db.award_referral_entries(referrer_id, db.get_user_by_username("blocked")["id"],
                                  tickets=unit)
check(4.5, status["status"] == "capped", f"11th award -> {status['status']}")

print("== 5. A NEW DRAWING RESETS THE ALLOWANCE ==")
with db.get_db() as conn:
    conn.execute(
        "INSERT INTO drawings (jackpot_amount, winner_id, house_tax, "
        "total_tickets, status, drawn_at) VALUES (0.2, NULL, 0.02, 10, "
        "'drawn', ?)", (int(time.time()),))
post_wallet = wallet("postdraw")
reset_registration_limit()
r = client.post("/api/auth/register", json={
    "username": "postdraw", "password": "post-pass",
    "wallet_address": post_wallet, "referrer_email": REF_EMAIL,
})
check(5.1, r.status_code == 200 and referral_tickets(post_wallet) == unit,
      "the first award after a drawing was refused")
summary = db.referral_award_summary(referrer_id)
check(5.2, summary["awards_this_cycle"] == 1,
      f"new cycle count {summary['awards_this_cycle']} != 1")

print("== 6. A HELD ENTRY SURVIVES UNTIL A WALLET LANDS ==")
held_id = db.create_user("helduser", srv_mod.hash_password("held-pass"),
                         email="held@example.com")
held = db.award_referral_entries(referrer_id, held_id, tickets=unit)
check(6.1, held["status"] == "awarded" and held["referred"] == "held",
      f"wallet-less friend state {held.get('referred')}")
with db.get_db() as conn:
    row = conn.execute("SELECT pending_referral_entries FROM users WHERE id = ?",
                       (held_id,)).fetchone()
check(6.2, int(row["pending_referral_entries"] or 0) == unit,
      f"held {row['pending_referral_entries']} != {unit}")
held_wallet = wallet("helduser")
db.update_user_profile(held_id, wallet_address=held_wallet)
check(6.3, referral_tickets(held_wallet) == unit,
      f"claim on wallet attach gave {referral_tickets(held_wallet)}")
with db.get_db() as conn:
    row = conn.execute("SELECT pending_referral_entries FROM users WHERE id = ?",
                       (held_id,)).fetchone()
check(6.4, int(row["pending_referral_entries"] or 0) == 0,
      "the hold was not cleared after the claim")

print("== 7. ENTRIES, NOT POT MONEY ==")
with db.get_db() as conn:
    before = conn.execute(
        "SELECT COALESCE(SUM(amount_xmr), 0) AS s FROM jackpot_inflows"
    ).fetchone()["s"] if conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='jackpot_inflows'"
    ).fetchone() else None
new_id = db.create_user("potcheck", srv_mod.hash_password("pot-pass"),
                        email="potcheck@example.com", wallet_address=wallet("potcheck"))
award = db.award_referral_entries(referrer_id, new_id, tickets=unit)
with db.get_db() as conn:
    after = conn.execute(
        "SELECT COALESCE(SUM(amount_xmr), 0) AS s FROM jackpot_inflows"
    ).fetchone()["s"] if conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='jackpot_inflows'"
    ).fetchone() else None
check(7.1, award["status"] == "awarded", f"award -> {award['status']}")
check(7.2, before == after,
      f"a referral moved XMR into the pot ({before} -> {after})")
balance = db.get_setting("jackpot_balance", "0")
check(7.3, str(balance) == "0", f"jackpot balance changed: {balance}")

print("== 8. THE DASHBOARD SEES THE CAP ==")
summary = db.referral_award_summary(referrer_id)
client.post("/api/auth/login",
            json={"username": "refowner", "password": "ownerpass"})
stats = client.get("/api/referral/stats").json()
check(8.1, stats.get("cycle_cap") == cap,
      f"cycle_cap {stats.get('cycle_cap')} != {cap}")
check(8.2, stats.get("awards_this_cycle") == summary["awards_this_cycle"],
      f"awards_this_cycle {stats.get('awards_this_cycle')}")
check(8.3, stats.get("remaining") == summary["remaining"],
      f"remaining {stats.get('remaining')}")

print("== 9. THE ADMIN METER SHOWS THE RULE ==")
db.create_user("meteradmin", srv_mod.hash_password("meter-pass"),
               email="meter@example.com", is_admin=True)
client.post("/api/auth/login",
            json={"username": "meteradmin", "password": "meter-pass"})
res = client.get("/api/admin/referral-meter")
check(9.1, res.status_code == 200, f"admin meter -> {res.status_code}")
meter = res.json()
check(9.2, meter.get("cap") == cap, f"meter cap {meter.get('cap')} != {cap}")
check(9.3, meter.get("awards_this_cycle") == db.referral_admin_meter()["awards_this_cycle"],
      f"meter count {meter.get('awards_this_cycle')}")
rows = meter.get("top_referrers") or []
check(9.4, bool(rows) and rows[0]["referrer_id"] == referrer_id
      and rows[0]["awards"] == meter["awards_this_cycle"]
      and rows[0]["remaining"] == cap - rows[0]["awards"]
      and rows[0]["at_cap"] == (rows[0]["awards"] >= cap),
      f"top row {rows[0] if rows else None}")
check(9.5, "entries_held" in meter and "recent" in meter,
      "the meter omits the held entries or the recent awards")
panel = client.get("/api/admin/panel")
check(9.6, 'id="refMeterSummary"' in panel.text
      and 'id="refMeterReferrers"' in panel.text,
      "the admin panel has no referral meter card")
check(9.7, client.post("/api/admin/referral-meter").status_code == 405,
      "the meter must be read-only")
client.post("/api/auth/login",
            json={"username": "refowner", "password": "ownerpass"})
check(9.8, client.get("/api/admin/referral-meter").status_code == 403,
      "a non-admin read the meter")

print("== 10. THE CARD'S REWARD CLAIMS AGAINST WHAT THE CODE PAYS ==")
# The card used to promise "referral streak bonuses" and a "weekly
# leaderboard". Neither pays anything: the only streak is the MINING
# streak (a 3x ticket conversion on milestone days), and the leaderboard
# is a read. Both lines now say that; this section pins the code side.
with db.get_db() as conn:
    cols = ([r["name"] for r in conn.execute("PRAGMA table_info(users)")]
            + [r["name"] for r in conn.execute("PRAGMA table_info(referral_awards)")])
check(10.1, not [c for c in cols if "streak" in c.lower()],
      f"a referral streak column exists: {[c for c in cols if 'streak' in c.lower()]}")
streak_badges = [d[0] for d in db.BADGE_DEFS if "streak" in d[0]]
check(10.2, set(streak_badges) == {"streak_7", "streak_30"},
      f"streak badges: {streak_badges}")
check(10.3, db.get_streak_bonus(7)["multiplier"] == 3
      and db.get_streak_bonus(7)["streak_days"] == 7,
      "day 7 of the streak no longer converts at 3x")

# The card's replacement claim: Recruiter at the first ACTIVE referral,
# Network Builder at 5. "Active" is the operative word — the badge is
# adjudicated on get_referral_stats' active_referrals, so a signup that
# never mines earns nothing.
badge_miner = db.get_or_create_miner(wallet("badgeman"))
none_yet = db.check_and_award_badges(badge_miner["id"], shares_valid=0,
                                     ticket_count=0, referral_count=0,
                                     streak=0)
check(10.4, "first_referral" not in none_yet,
      f"Recruiter was paid with no referral: {none_yet}")
first = db.check_and_award_badges(badge_miner["id"], shares_valid=0,
                                  ticket_count=0, referral_count=1,
                                  streak=0)
check(10.5, first == ["first_referral"], f"first active referral -> {first}")
five = db.check_and_award_badges(badge_miner["id"], shares_valid=0,
                                 ticket_count=0, referral_count=5,
                                 streak=0)
check(10.6, five == ["referral_5"], f"five active referrals -> {five}")

card = (HERE / "pool" / "templates" / "index.html").read_text(encoding="utf-8")
check(10.7,
      "Badges, not bonuses: <b>Recruiter</b> at your first active "
      "referral, <b>Network Builder</b> at 5 active" in card
      and "The leaderboard ranks the top miners — it pays no separate "
      "weekly prize" in card,
      "the referral card does not state the two audited rules")
check(10.8, "Referral streak bonuses" not in card
      and "Weekly leaderboard" not in card,
      "the card still promises a referral streak or a weekly prize")

# A read pays nothing: the leaderboard route must leave the ledger alone.
before = db.get_miner_ticket_count(badge_miner["id"])
res = client.get("/api/miners")
after = db.get_miner_ticket_count(badge_miner["id"])
check(10.9, res.status_code == 200 and before == after,
      f"/api/miners minted tickets ({before} -> {after})")
with db.get_db() as conn:
    weekly = [r["key"] for r in conn.execute(
        "SELECT key FROM settings WHERE lower(key) LIKE '%week%'").fetchall()]
check("10.10", not weekly, f"a weekly-prize setting exists: {weekly}")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
