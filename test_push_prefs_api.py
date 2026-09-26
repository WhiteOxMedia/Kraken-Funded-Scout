"""test_push_prefs_api.py - THE PREFS ENVELOPE, END-TO-END (Sep 26).

The user's exact contract, exercised through the REAL FastAPI app
(TestClient, hermetic sandbox DB - no live jackpot.db, no network):

  POST /api/push/preferences
  {"preferences": {"jackpot": false, "scout": true}}
    -> 200 {"user_id": "alice",
            "preferences": {"jackpot": false, "scout": true},
            "updated_at": "<UTC Z stamp>"}

Pins: register->session->POST->GET round-trip; the SEND-TIME GATE obeys
(jackpot OFF blocks jackpot_win with the category reason; scout ON
never trips the opt-out for signals); unauthenticated POST -> 401;
a user_id on the wire is IGNORED (the session is always the subject).
Prints "RESULT: N passed, M failed".
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

_tmp = Path(tempfile.mkdtemp(prefix="prefs_api_drive_"))

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

# 1. register -> session cookie
r = client.post("/api/auth/register", json={
    "username": "alice", "password": "correct horse battery"})
check(1.1, r.status_code == 200, f"register: {r.status_code} {r.text[:80]}")
uid = db.get_user_by_username("alice")["id"]

# a real subscription so the GATE answers, not the empty-sub short-cut
r = client.post("/api/push/subscribe", json={"subscription": {
    "endpoint": "https://push.example/alice1",
    "keys": {"p256dh": "a", "auth": "b"}}})
check(1.2, r.status_code == 200 and r.json().get("ok"),
      f"subscribe: {r.status_code} {r.json()}")

# 2. THE USER'S EXACT POST
r = client.post("/api/push/preferences", json={
    "preferences": {"jackpot": False, "scout": True}})
check(2.1, r.status_code == 200, f"POST: {r.status_code} {r.text[:120]}")
body = r.json()
check(2.2, body.get("user_id") == "alice"
      and body.get("preferences") == {"jackpot": False, "scout": True},
      f"envelope echo: {body}")
stamp = body.get("updated_at") or ""
check(2.3, stamp.endswith("Z") and "T" in stamp,
      f"updated_at is a UTC Z stamp: {stamp!r}")

# 3. GET round-trips the same envelope
got = client.get("/api/push/preferences").json()
check(3.1, got.get("user_id") == "alice"
      and got.get("preferences") == {"jackpot": False, "scout": True}
      and got.get("updated_at") == stamp,
      f"GET: {got}")

# 4. THE GATE OBEYS THE POSTED ENVELOPE
res = ps.send_to_user(uid, "jackpot_win")
check(4.1, res["sent"] == 0 and "opted out" in (res.get("reason") or ""),
      f"jackpot OFF blocks jackpot_win: {res.get('reason')!r}")
res2 = ps.send_to_user(uid, "signal_buy")
check(4.2, "opted out" not in (res2.get("reason") or ""),
      f"scout ON never trips the opt-out for signals: "
      f"{res2.get('reason')!r} (VAPID config error expected in sandbox)")

# 5. unauthenticated POST -> 401
r = TestClient(srv.app).post("/api/push/preferences", json={
    "preferences": {"jackpot": True}})
check(5.1, r.status_code == 401, f"unauthenticated: {r.status_code}")

# 6. a user_id on the wire is IGNORED
r = client.post("/api/push/preferences", json={
    "user_id": "mallory", "preferences": {"jackpot": True}})
check(6.1, r.status_code == 200 and r.json().get("user_id") == "alice",
      f"wire user_id ignored: {r.json().get('user_id')}")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
