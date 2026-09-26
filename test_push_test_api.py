"""test_push_test_api.py - THE PIPE TEST, END-TO-END (Sep 26).

POST /api/push/test {"category": "jackpot"|"scout"} - the user-facing
"Test notification" buttons. One REAL push of the chosen family through
the same sender path a live event takes. The contract under test:

  authed POST, known category  -> 200 {ok, sent, reason, category}
  authed POST, unknown category-> 400 "category must be jackpot|scout"
  unauthenticated POST         -> 401
  wire user_id is IGNORED      -> the session is always the subject
  the OPT-OUT GATE is honored, not bypassed:
    jackpot toggled OFF + jackpot test -> sent=0,
        reason "jackpot notifications opted out"
    scout toggled ON  + scout test    -> passes the gate (fails later
        at delivery only; the reason must NOT be the opt-out one)
  the test event SHARES the family's rate-limit bucket
    (push_test rate=4: 4 sends, the 5th is "rate limited")
  every gate/refusal is audited (push_send_blocked with the category
    reason, event push_test) - silence leaves no trace, this must not.

TestClient, hermetic sandbox DB - no live jackpot.db, no network.
Prints "RESULT: N passed, M failed".
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

_tmp = Path(tempfile.mkdtemp(prefix="push_test_drive_"))

import database as db                              # noqa: E402
db.DB_PATH = _tmp / "jackpot.db"
db.init_db()

import push_service as ps                          # noqa: E402
import server as srv                               # noqa: E402
from fastapi.testclient import TestClient          # noqa: E402

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


client = TestClient(srv.app)

# 0. the new scout-family test template exists
check(0.1, "push_test_signal" in ps.EVENTS
      and ps.CATEGORY.get("push_test_signal") == "scout",
      "push_test_signal template exists, scout family")
check(0.2, ps.EVENTS["push_test_signal"].get("tag") == "scout-test"
      and ps.EVENTS["push_test"].get("tag") == "push-test",
      "distinct notification tags (no de-dup clobber between families)")

# 1. unauthenticated -> 401
r = client.post("/api/push/test", json={"category": "jackpot"})
check(1.1, r.status_code == 401, f"cookie-less test POST: {r.status_code}")

# 2. a real session
r = client.post("/api/auth/register", json={
    "username": "pipetest", "password": "correct horse battery"})
check(2.1, r.status_code == 200, f"register: {r.status_code}")
uid = db.get_user_by_username("pipetest")["id"]

# 3. no subscription yet -> honest refusal, not a fake success
r = client.post("/api/push/test", json={"category": "jackpot"})
j = r.json()
check(3.1, r.status_code == 200 and j.get("ok") is False
      and j.get("sent") == 0 and j.get("reason") == "no subscription",
      f"no-sub verdict: {r.status_code} {j}")
check(3.2, j.get("category") == "jackpot", "category echoed")

# 4. unknown category -> 400
r = client.post("/api/push/test", json={"category": "all"})
check(4.1, r.status_code == 400, f"unknown category: {r.status_code}")

# 5. wire user_id is IGNORED - the session is the subject
r = client.post("/api/push/test",
                json={"category": "jackpot", "user_id": "mallory"})
check(5.1, r.status_code == 200 and r.json().get("category") == "jackpot",
      "wire user_id ignored")

# 6. a subscription (fake endpoint - delivery itself fails fail-soft;
#    the point is to get PAST the no-sub short-cut to the GATE)
r = client.post("/api/push/subscribe", json={"subscription": {
    "endpoint": "https://push.example/pipe1",
    "keys": {"p256dh": "a", "auth": "b"}}})
check(6.1, r.status_code == 200 and r.json().get("ok"),
      f"subscribe: {r.status_code} {r.json()}")

# 7. THE GATE: jackpot OFF blocks the jackpot test with the category
#    reason - the button must never bypass the user's own opt-out
r = client.post("/api/push/preferences",
                json={"preferences": {"jackpot": False, "scout": True}})
check(7.1, r.status_code == 200, f"toggle jackpot off: {r.status_code}")
r = client.post("/api/push/test", json={"category": "jackpot"})
j = r.json()
check(7.2, j.get("sent") == 0
      and j.get("reason") == "jackpot notifications opted out",
      f"jackpot test honors the gate: {j}")
r = client.post("/api/push/test", json={"category": "scout"})
j = r.json()
check(7.3, j.get("reason") != "scout notifications opted out",
      f"scout (ON) passes the gate: {j}")

# 8. the gate decision is AUDITED (event + category reason)
rows = [r for r in db.get_audit_logs(limit=100)
        if r["user_id"] == uid and r["action"] == "push_send_blocked"]
check(8.1, any(json.loads(r["details"]).get("event") == "push_test"
               and json.loads(r["details"]).get("reason") == "opted_out"
               for r in rows),
      "push_send_blocked audit row for the test event")

# 9. RATE BUCKET: jackpot back ON, then push_test (rate=4) five times
#    - the first gets PAST the gate (a delivery-stage verdict, never
#    the opt-out one), and the 5th answers "rate limited"
r = client.post("/api/push/preferences",
                json={"preferences": {"jackpot": True, "scout": True}})
check(9.0, r.status_code == 200, "jackpot toggle back on")
verdicts = []
for _ in range(5):
    r = client.post("/api/push/test", json={"category": "jackpot"})
    verdicts.append(r.json().get("reason"))
check(9.1, verdicts[0] not in ("jackpot notifications opted out",
                               "rate limited"),
      f"first attempt passed the gate: {verdicts[0]}")
check(9.2, verdicts[-1] == "rate limited",
      f"5th jackpot test rate limited: {verdicts}")

# 10. send_test() unit shape: unknown category never reaches the sender
res = ps.send_test(uid, "nonsense")
check(10.1, res == {"ok": False, "reason": "unknown category"},
      f"service-level unknown category: {res}")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
