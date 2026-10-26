"""test_progression.py — THE PROGRESSION DRIVE (Oct 24).

Grades the two things the operator's Oct 24 memo added to the pool:

  1. the streak bonus: shares mined on a milestone day (every 7th
     consecutive day) convert to tickets at 3x, while the miner itself
     (hashrate) is never touched
  2. the 100-day Progression path: a day counts when the mining bot
     contributed to the jackpot pool; 100 such days unlock Kraken Scout
     Pro once, and the unlock awards exactly one +1 jackpot entry
  3. the donation path awards the same single entry, once per account,
     and is time-boxed rather than lifetime
  4. /api/gamification's progression payload has the shape the dashboard
     renders (streak, bonus, days mined, percent, unlock state)

House conventions: check(n, cond, why), RESULT line, sys.exit.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
passed = failed = 0


def check(n, cond, why):
    global passed, failed
    tag = "PASS" if cond else "FAIL"
    print(f"{tag}  {n}  {why}")
    if cond:
        passed += 1
    else:
        failed += 1


# ── sandbox FIRST: rebind the DB before any pool import ──────────────
SANDBOX = tempfile.mkdtemp(prefix="progression_drive_")
sys.path.insert(0, os.path.join(HERE, "pool"))

import database as db                                  # noqa: E402

db.DB_PATH = __import__("pathlib").Path(os.path.join(SANDBOX, "jackpot.db"))
db.init_db()

import jackpot_engine as engine                        # noqa: E402


def _day(offset=0):
    return (datetime.utcnow() + timedelta(days=offset)).strftime("%Y-%m-%d")


def _set_streak(miner_id, streak, last_day):
    with db.get_db() as conn:
        conn.execute(
            "UPDATE miner_xp SET mining_streak_days = ?, last_mine_day = ? "
            "WHERE miner_id = ?", (streak, last_day, miner_id))


def _tickets(miner_id):
    return db.get_miner_ticket_count(miner_id)


def _xp(miner_id):
    return db.get_or_create_xp(miner_id)


def _mine(wallet, miner_id, shares):
    db.update_miner_stats(wallet, shares, 0, 0)
    return engine.allocate_free_tickets(miner_id, shares)


WALLET = "4" + "progression".ljust(94, "g")
USER_ID = db.create_user("progressor", "hash", wallet_address=WALLET)
MINER = db.get_or_create_miner(WALLET, "rig-1")

print("== 1. THE STREAK BONUS: 3x ON MILESTONE DAYS ONLY ==")

# Day 1 of a new streak: ordinary 1x conversion.
_mine(WALLET, MINER["id"], 250)
check(1.1, _tickets(MINER["id"]) == 2,
      "day 1 at 1x: 250 shares mint 2 complete tickets")
check(1.2, _xp(MINER["id"])["jackpot_days"] == 1,
      "the first contributing day is counted exactly once")
_mine(WALLET, MINER["id"], 50)
check(1.3, _tickets(MINER["id"]) == 3,
      "more shares the same day mint normally (300 units -> 3 tickets)")
check(1.4, _xp(MINER["id"])["jackpot_days"] == 1,
      "a second submission on the same UTC day does not add a day")
check(1.5, db.get_streak_bonus(1)["multiplier"] == 1,
      "a non-milestone streak converts at 1x")
check(1.6, db.get_streak_bonus(7)["multiplier"] == 3,
      "day 7 of a streak converts at 3x")
check(1.7, db.get_streak_bonus(7)["next_milestone_day"] == 14,
      "after day 7 the next milestone is day 14")

# Day 7 of the streak: the same 100 shares now count 300 units.
_set_streak(MINER["id"], 6, _day(-1))
_mine(WALLET, MINER["id"], 100)
check(1.8, _tickets(MINER["id"]) == 6,
      "milestone day: 100 shares -> 300 units -> 3 tickets (300/100 + "
      "the 3 already banked)")
check(1.9, _xp(MINER["id"])["streak_milestones"] == 1,
      "the milestone itself is recorded on the miner")
check(1.10, _xp(MINER["id"])["mining_streak_days"] == 7,
      "the streak advanced to day 7 (the bonus is driven by the streak)")

# Day 8: back to 1x, hashrate untouched throughout.
_set_streak(MINER["id"], 7, _day(-1))
_mine(WALLET, MINER["id"], 100)
check(1.11, _tickets(MINER["id"]) == 7,
      "the day after a milestone converts at 1x again (700 units -> 7)")
check(1.12, _xp(MINER["id"])["streak_milestones"] == 1,
      "no second milestone was recorded on an ordinary day")
check(1.13, db.get_streak_bonus(0)["multiplier"] == 1,
      "no streak means no bonus")

print("== 2. THE 100-DAY PATH UNLOCKS PRO AND AWARDS +1 ENTRY ==")

prog = db.pro_progress(MINER["id"], USER_ID)
check(2.1, prog["days_required"] == 100,
      "the unlock target is the operator setting (100 days)")
check(2.2, prog["days_mined"] == _xp(MINER["id"])["jackpot_days"],
      "days mined is the persisted contributing-day count")

before = _tickets(MINER["id"])
with db.get_db() as conn:
    conn.execute(
        "UPDATE miner_xp SET jackpot_days = 99, last_jackpot_day = ? "
        "WHERE miner_id = ?", (_day(-40), MINER["id"]))
_mine(WALLET, MINER["id"], 100)

after = _tickets(MINER["id"])
check(2.3, _xp(MINER["id"])["jackpot_days"] == 100,
      "the 100th contributing day was counted")
check(2.4, db.get_user(USER_ID)["scout_pro_lifetime"] == 1,
      "the 100-day path sets the lifetime entitlement")
check(2.5, db.get_user(USER_ID)["scout_pro_unlock_source"] == "progression",
      "the unlock records which path granted it")
check(2.6, after - before == 2,
      "the unlock awards exactly +1 entry on top of the day's conversion "
      f"(got {after - before})")
check(2.7, db.get_user(USER_ID)["scout_pro_entry_awarded"] == 1,
      "the unlock entry is marked awarded")

# The next allocation must not mint a second unlock entry.
before = _tickets(MINER["id"])
_mine(WALLET, MINER["id"], 100)
check(2.8, _tickets(MINER["id"]) - before == 1,
      "later mining mints only its own conversion, never another unlock entry")
check(2.9, db.pro_progress(MINER["id"], USER_ID)["pro_unlocked"] is True,
      "pro_progress reports the unlocked state")

print("== 3. THE DONATION PATH AWARDS THE SAME SINGLE ENTRY ==")

W2 = "8" + "donor".ljust(94, "d")
U2 = db.create_user("donor", "hash", wallet_address=W2)
M2 = db.get_or_create_miner(W2, "rig-2")

grant = db.unlock_scout_pro(U2, "donation", grant_days=30)
check(3.1, grant["unlocked"] is True and grant["entry_awarded"] is True,
      "a donation unlock grants the entitlement and its +1 entry")
check(3.2, db.get_miner_ticket_count(M2["id"]) == 1,
      "the donor's miner holds exactly one unlock entry")
check(3.3, db.get_user(U2)["scout_pro_lifetime"] == 0,
      "a donation grant is time-boxed, not lifetime")
check(3.4, db.get_user(U2)["scout_pro_unlock_source"] == "donation",
      "the donation path is recorded")
until = db.get_user(U2)["scout_pro_until"]
check(3.5, until > datetime.utcnow().timestamp() + 29 * 86400,
      "the grant carries the configured 30 days")

again = db.unlock_scout_pro(U2, "donation", grant_days=30)
check(3.6, again["entry_awarded"] is False,
      "a second grant awards no second entry")
check(3.7, db.get_miner_ticket_count(M2["id"]) == 1,
      "the entry count is still one")

print("== 4. THE PAYLOAD THE DASHBOARD RENDERS ==")

p2 = db.pro_progress(M2["id"], U2)
check(4.1, set(("streak", "days_mined", "days_required", "days_remaining",
                "progress_percent", "pro_unlocked", "pro_unlock_source",
                "unlock_entry_awarded")) <= set(p2),
      "progression carries every field the dashboard card reads")
check(4.2, p2["pro_unlocked"] is True and p2["unlock_entry_awarded"] is True,
      "the payload reports the donation unlock and its awarded entry")
check(4.3, p2["progress_percent"] == 0.0,
      "a donor with no mining days shows 0% toward the 100-day path")
check(4.4, p2["streak"]["multiplier"] == 1,
      "a fresh miner starts at the 1x rate")
check(4.5, db.pro_progress(None, U2)["days_mined"] == 0,
      "an account with no miner still gets a safe progression payload")

print("== 5. NO WALLET, NO LOST ENTRY (Oct 24) ==")

U3 = db.create_user("nowallet", "hash")        # no wallet, so no miner

g3 = db.unlock_scout_pro(U3, "donation", grant_days=30)
check(5.1, g3["unlocked"] is True and g3["entry_held"] is True,
      "a wallet-less donor keeps the promised entry instead of losing it")
check(5.2, g3["entry_awarded"] is False,
      "the grant reports the entry as held, not as a credited ticket")
check(5.3, db.get_user(U3)["pending_entries"] == 1,
      "the entry is held on the account")
check(5.4, db.get_user(U3)["scout_pro_entry_awarded"] == 1,
      "the grant is still stamped, so it can never be minted twice")
check(5.5, db.claim_pending_entries(U3)["claimed"] == 0,
      "without a wallet the claim is a no-op")
check(5.6, db.get_user(U3)["pending_entries"] == 1,
      "the held entry survives a claim attempt")
check(5.7, db.pro_progress(None, U3)["entry_held"] is True,
      "the dashboard payload shows the entry as held")

W3 = "9" + "held".ljust(94, "h")
db.update_user_profile(U3, wallet_address=W3)
M3 = db.get_or_create_miner(W3)
check(5.8, db.get_miner_ticket_count(M3["id"]) == 1,
      "attaching a wallet mints the held entry as a real ticket")
check(5.9, db.get_user(U3)["pending_entries"] == 0,
      "the hold is cleared once the ticket exists")
check("5.10", db.claim_pending_entries(U3)["claimed"] == 0
      and db.get_miner_ticket_count(M3["id"]) == 1,
      "the claim is idempotent — never a second ticket")
p3 = db.pro_progress(M3["id"], U3)
check("5.11", p3["unlock_entry_awarded"] is True and p3["entry_held"] is False,
      "the payload flips to credited once the ticket exists")

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
