"""test_push_decisions.py — THE SUPPRESSION/SEND LEDGER (Sep 26).

Pins /api/admin/push/decisions against seeded audit rows on a hermetic
sandbox: action filtering, category filtering, username join (broadcast
rows have no user), detail-field extraction, limit clamp, admin gate,
and dashboard-card wiring.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import field_crypto as fc
fc.KEY_DIR = Path(tempfile.mkdtemp(prefix="dec_key_"))
fc.KEY_FILE = fc.KEY_DIR / "field_key"
fc._KEY_CACHE = None
fc.generate_key_file()

import database as db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="dec_db_")) / "jackpot.db"
db.init_db()

import server as srv
from fastapi.testclient import TestClient

results = []
def check(n, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(n) + ("  " + str(msg) if msg else ""))

# user 1 = admin (first user), user 2 = plain
client = TestClient(srv.app)
client.post("/api/auth/register", json={"username": "decadmin",
                                        "password": "pw"})

def audit(action, details, user_id=2):
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO audit_log (user_id, action, details, created_at)"
            " VALUES (?, ?, ?, ?)",
            (user_id, action, json.dumps(details), time.time()))

print("== 0. SEEDED DECISION ROWS ==")
# user 2 must EXIST before audit rows reference it (FK on audit_log)
uid2 = db.create_user("decuser", "hash")
audit("push_send_blocked", {"event": "jackpot_win", "category": "jackpot",
                            "reason": "opted_out"})
audit("push_send_allowed", {"event": "signal_buy", "category": "scout",
                            "sent": 2, "dead": 1})
audit("push_rate_limited", {"event": "jackpot_win"})
audit("push_broadcast", {"event": "signal_exit", "delivered": 5,
                         "recipients": 3}, user_id=None)
audit("kraken_referral.mapped", {"user_id": 2})          # noise: must hide

print("== 1. THE LEDGER ==")
r = client.get("/api/admin/push/decisions")
j = r.json()
check(1.1, r.status_code == 200 and j["count"] == 4,
      f"4 decision rows, noise excluded ({j.get('count')})")
acts = [d["action"] for d in j["decisions"]]
check(1.2, acts[0] == "push_broadcast",
      f"newest first (broadcast last-inserted): {acts}")
bcast = next(d for d in j["decisions"] if d["action"] == "push_broadcast")
check(1.3, bcast["username"] == "broadcast",
      f"userless rows render as broadcast: {bcast['username']}")
blocked = next(d for d in j["decisions"] if d["action"] == "push_send_blocked")
check(1.4, blocked["event"] == "jackpot_win" and blocked["category"] == "jackpot"
      and blocked["reason"] == "opted_out" and blocked["username"] == "decuser",
      f"block row: full fixed fields extracted ({blocked['event']})")
allowed = next(d for d in j["decisions"] if d["action"] == "push_send_allowed")
check(1.5, allowed["sent"] == 2 and allowed["dead"] == 1,
      f"allowed row carries sent/dead: {allowed['sent']}/{allowed['dead']}")
check(1.6, all("email" not in json.dumps(d) for d in j["decisions"]),
      "no PII beyond usernames in the ledger")

print("== 2. FILTERS ==")
r = client.get("/api/admin/push/decisions?action=push_send_blocked")
j = r.json()
check(2.1, j["count"] == 1 and j["decisions"][0]["reason"] == "opted_out",
      "action filter: only blocked rows")
r = client.get("/api/admin/push/decisions?action=push_send_allowed"
               "&category=scout")
j = r.json()
check(2.2, j["count"] == 1 and j["decisions"][0]["event"] == "signal_buy",
      "category filter composes with action")
r = client.get("/api/admin/push/decisions?action=push_rate_limited")
j = r.json()
check(2.3, j["count"] == 1 and "reason" not in j["decisions"][0],
      "rate-limit row has no invented reason field")
r = client.get("/api/admin/push/decisions?user_id=2")
check(2.4, r.json()["count"] == 3, "user filter (broadcast row excluded)")
r = client.get("/api/admin/push/decisions?action=DROP TABLE")
j = r.json()
check(2.5, r.status_code == 200 and j["count"] == 4
      and j["filters"]["action"] == "DROP TABLE",
      "garbage action is NOT whitelisted: no action clause is added "
      "(falls back to the unfiltered push_* ledger; SQL stays "
      "parameterized)")
r = client.get("/api/admin/push/decisions?limit=999")
check(2.6, r.json()["filters"]["limit"] == 200, "limit clamped to 200")

print("== 3. GATE + WIRING ==")
anon = TestClient(srv.app)
check(3.1, anon.get("/api/admin/push/decisions").status_code == 401,
      "ledger admin-gated")
html = (HERE / "pool" / "templates" / "index.html").read_text(encoding="utf-8")
check(3.2, "/static/push_decisions_admin.js" in html,
      "card included by the page")
js = (HERE / "pool" / "static" / "push_decisions_admin.js").read_text(encoding="utf-8")
check(3.3, "/api/admin/push/decisions" in js and "push_send_blocked" in js
      and "setInterval" in js,
      "card fetches the ledger with the blocked filter + live refresh")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
