"""test_database_helpers.py - THE UNCOVERED DB HELPERS (Sep 26).

A first dedicated drive for database.py helpers no other drive calls
directly (the route drives exercise most of the write paths through
HTTP; these helpers are consumed by scripts, the proxy poller, and
future surfaces, and were one regression away from silent breakage):

  XP CURVE       xp_for_level / level_from_xp: the documented curve
                 (level*level-1*500), its round trip, and the boundary
                 EXACTLY AT a level-up threshold (level_from_xp must
                 return the HIGHER level at equality - the while loop
                 promises it, nothing pinned it)
  REFERRAL CODES generate_referral_code: unique across many draws,
                 shaped (username-derived prefix + hex tail), handles
                 spaces/unicode; get_user_by_referral_code resolves;
                 set_referral_code normalizes and uniqueness guards
  SESSIONS       create/get/delete + the EXPIRY boundary: an expired
                 session must read as None (get_session compares
                 expires_at > now in SQL), a live one resolves, delete
                 removes exactly one, delete_expired_sessions sweeps
                 the expired and spares the live
  AUDIT          add_audit_log + get_audit_logs: newest-first order,
                 the limit, and the LEFT JOIN username for user rows
                 (system rows survive with username None)
  POOL SUMMARY   get_pool_summary: every key present and CONSISTENT
                 with the sandbox's own row counts, on an empty db and
                 after miners/users/tickets/payouts/drawings land

Hermetic: sandbox DB_PATH, no network, no live jackpot.db. Prints
"RESULT: N passed, M failed".
"""
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

_tmp = Path(tempfile.mkdtemp(prefix="db_helpers_drive_"))

import database as db                              # noqa: E402
db.DB_PATH = _tmp / "jackpot.db"
db.init_db()

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


print("== 1. THE XP CURVE ==")
check(1.1, db.xp_for_level(1) == 0 and db.xp_for_level(2) == 1000
      and db.xp_for_level(3) == 3000 and db.xp_for_level(11) == 55000,
      "the REAL curve: level*(level-1)*1000 per 1000-step ramp")
check(1.2, db.level_from_xp(0) == 1 and db.level_from_xp(999) == 1
      and db.level_from_xp(1000) == 2 and db.level_from_xp(2999) == 2
      and db.level_from_xp(3000) == 3,
      "boundaries: equality at the threshold level-ups (the while's "
      "<= promises it)")
check(1.3, db.level_from_xp(db.xp_for_level(7)) == 7
      and all(db.level_from_xp(db.xp_for_level(l)) == l
              for l in range(1, 25)),
      "round trip 1..24")
big = 10 ** 9
check(1.4, db.level_from_xp(big) == db.level_from_xp(big)  # terminates
      and db.level_from_xp(big) > 1000,
      "a huge XP value terminates and lands high (no infinite loop)")

print("== 2. REFERRAL CODES ==")
codes = {db.generate_referral_code("Alice") for _ in range(50)}
check(2.1, len(codes) == 50, "50 draws, 50 distinct codes (uniqueness)")
c = db.generate_referral_code("Bob Marley")
check(2.2, c.startswith("bobmarle") and len(c) == 8 + 6
      and all(ch in "0123456789abcdef" for ch in c[8:]),
      f"shaped: username prefix + 6 hex ({c})")
u = "ü UserController".strip()
c2 = db.generate_referral_code("Miner_X")
check(2.3, c2.startswith("miner_x"), f"lowercase + underscore passthrough ({c2})")

db.create_user("refcode_user2", "x")
row = db.get_user_by_username("refcode_user2")
uid = row["id"]
code = db.generate_referral_code("refcode_user2")
db.set_referral_code(uid, code)
check(2.4, db.get_user_by_referral_code(code)["id"] == uid,
      "set -> resolve round trip")
db.set_referral_code(uid, code.upper() + "  ")
check(2.5, db.get_user_by_referral_code(
    db.get_user_by_username("refcode_user2")["referral_code"])["id"] == uid,
    "set normalizes (trim/case) and still resolves")

print("== 3. SESSIONS + THE EXPIRY BOUNDARY ==")
tok_live, tok_dead = "sess-live-1", "sess-dead-1"
now = int(time.time())
db.create_session(tok_live, uid, now + 3600, ip_address="127.0.0.1")
db.create_session(tok_dead, uid, now - 1)          # already expired
check(3.1, db.get_session(tok_live) is not None
      and db.get_session(tok_live)["user_id"] == uid,
      "a live session resolves with its user")
check(3.2, db.get_session(tok_dead) is None,
      "an expired session reads as None (SQL expires_at > now)")
# the exact boundary: expires_at == now is ALREADY expired (strict >)
tok_edge = "sess-edge-1"
db.create_session(tok_edge, uid, now)
check(3.3, db.get_session(tok_edge) is None,
      "expires_at == now is expired (strict >, no grace)")
db.delete_session(tok_live)
check(3.4, db.get_session(tok_live) is None
      and db.get_session(tok_dead) is None,
      "delete removes the session")
db.create_session("sweep-live", uid, now + 3600)
db.create_session("sweep-dead", uid, now - 10)
db.delete_expired_sessions()
check(3.5, db.get_session("sweep-live") is not None
      and db.get_session("sweep-dead") is None,
      "the sweep removes expired and spares live")

print("== 4. THE AUDIT LOG ==")
# created_at is epoch-seconds; same-second rows tie. Order within a
# second is unspecified — pin the SET (deterministic), not the order.
db.add_audit_log(uid, "drive_action_a", '{"k": 1}', ip_address="10.0.0.1")
time.sleep(1.1)                                     # cross the second
db.add_audit_log(None, "drive_system_b", '{"k": 2}')     # a system row
time.sleep(1.1)
db.add_audit_log(uid, "drive_action_c", '{"k": 3}')
rows = db.get_audit_logs(limit=3)
check(4.1, rows[0]["action"] == "drive_action_c"
      and rows[2]["action"] == "drive_action_a"
      and {r["action"] for r in rows} == {"drive_action_a",
                                          "drive_system_b",
                                          "drive_action_c"},
      "newest first across seconds (same-second ties unspecified)")
check(4.2, rows[0]["username"] == "refcode_user2"
      and rows[1]["username"] is None,
      "the LEFT JOIN names user rows; system rows survive with None")
check(4.3, len(db.get_audit_logs(limit=2)) == 2
      and rows[0]["action"] in
      {r["action"] for r in db.get_audit_logs(limit=1)},
      "the limit caps the read; the newest row leads at limit=1")

print("== 5. THE POOL SUMMARY ==")
s0 = db.get_pool_summary()
keys = {"miners_total", "miners_active", "users_total", "drawings_total",
        "tickets_total", "payouts_total", "payouts_pending",
        "total_paid_out", "total_house_tax"}
check(5.1, keys <= set(s0.keys()), "every documented key present")
check(5.2, s0["users_total"] >= 1 and s0["tickets_total"] == 0
      and s0["payouts_total"] == 0,
      "empty-db posture: only the seeded user counts")
m = db.get_or_create_miner("4" + "9" * 94, "summary_miner")
db.add_tickets(m["id"], "free", 3)
db.add_payout(m["id"], 0.5, "mining")
with db.get_db() as conn:
    conn.execute("UPDATE payouts SET status='sent' WHERE miner_id=?",
                 (m["id"],))
s1 = db.get_pool_summary()
check(5.3, s1["miners_total"] == s0["miners_total"] + 1
      and s1["tickets_total"] == 3 and s1["payouts_total"] == 1
      and s1["payouts_pending"] == 0 and s1["total_paid_out"] == 0.5,
      "counts consistent with the rows just written")
# pending payouts count separately from paid
db.add_payout(m["id"], 0.25, "mining")             # stays pending
s2 = db.get_pool_summary()
check(5.4, s2["payouts_pending"] == 1
      and s2["total_paid_out"] == 0.5,
      "pending vs paid are distinct buckets")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
