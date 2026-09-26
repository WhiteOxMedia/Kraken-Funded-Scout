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
a user_id on the wire is IGNORED (the session is always the subject);
the TWO-DEVICE RACE, ported from the live drive's long-race section
5b: two sessions interleaving writes, each adopting the version every
response carries — exactly ONE 412, stamps strictly advancing (the
stamp clock is strictly monotonic by contract), both devices converge
on the identical final envelope (same preferences, updated_at, ETag).
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

# 7. THE TWO-DEVICE RACE (ported from test_prefs_live_server.py 5b,
#    hermetic: same in-process app, two real session cookies)
# The realistic device loop: every response's version is adopted (200
# body, 412 body, or a reconciliation read), so a stale write happens
# exactly when a device bets on a version the other already
# superseded — and the loser re-reads and converges.
print("== 7. THE TWO-DEVICE RACE (ported live 5b) ==")
statuses = []


def _post(dev, prefs, etag):
    r = dev.post("/api/push/preferences", json={"preferences": prefs},
                 headers={"If-Match": f'"{etag}"'})
    statuses.append(r.status_code)
    return r.status_code, r.json()


b = TestClient(srv.app)                      # device B: second session
r = b.post("/api/auth/login", json={
    "username": "alice", "password": "correct horse battery"})
check(7.0, r.status_code == 200, f"second device logged in: {r.status_code}")

rA = client.get("/api/push/preferences")
sA, hA, av = rA.status_code, rA.headers, rA.json()
rB = b.get("/api/push/preferences")
bv = rB.json()
t0 = av["updated_at"]
check(7.1, sA == 200 and rB.status_code == 200
      and bv["updated_at"] == t0
      and av["preferences"] == bv["preferences"],
      f"both devices open on the same version ({t0})")

s1, j1 = _post(client, {"jackpot": False, "scout": True}, t0)   # A writes
t1 = j1.get("updated_at")
check(7.2, s1 == 200 and t1 and t1 > t0,
      "A's write applies, the stamp strictly advances")

s2, j2 = _post(b, {"jackpot": False, "scout": False}, t0)       # B stale
check(7.3, s2 == 412 and j2.get("updated_at") == t1,
      f"B's stale write refused; the 412 carries A's version ({j2.get('updated_at')})")
check(7.4, j2.get("error") == "version conflict - re-read and retry"
      and j2.get("preferences") == {"jackpot": False, "scout": True},
      "the 412 body carries A's applied state (no lost update)")

s3, j3 = _post(b, {"jackpot": False, "scout": False}, t1)       # B retries
t2 = j3.get("updated_at")
check(7.5, s3 == 200 and t2 > t1,
      "B's retry with the adopted version applies")

r4 = client.get("/api/push/preferences")                        # A reconciles
j4 = r4.json()
check(7.6, r4.status_code == 200
      and j4["preferences"] == {"jackpot": False, "scout": False}
      and j4["updated_at"] == t2,
      "A's reconciliation read shows B's write before A's next bet")

s5, j5 = _post(client, {"jackpot": True, "scout": False}, t2)   # A writes
t3 = j5.get("updated_at")
check(7.7, s5 == 200 and t3 > t2,
      "A's next write applies after re-reading (no second collision)")

r6 = b.get("/api/push/preferences")                             # B reconciles
j6 = r6.json()
s7, j7 = _post(b, {"jackpot": True, "scout": True}, j6["updated_at"])
t4 = j7.get("updated_at")
check(7.8, r6.status_code == 200 and s7 == 200 and t4 > t3,
      "B's final write applies after re-reading")

check(7.9, statuses.count(412) == 1
      and all(x == 200 for x in statuses if x != 412),
      f"exactly one 412, everything else 200: {statuses}")

# CONVERGENCE: both devices read the identical final envelope
rFA = client.get("/api/push/preferences")
xFB = b.get("/api/push/preferences")
etagA = (rFA.headers.get("ETag") or rFA.headers.get("etag") or "").strip('"')
etagB = (xFB.headers.get("ETag") or xFB.headers.get("etag") or "").strip('"')
fA, fB = rFA.json(), xFB.json()
check("7.10", rFA.status_code == 200 and xFB.status_code == 200
      and fA["preferences"] == fB["preferences"] == {"jackpot": True, "scout": True}
      and fA["updated_at"] == fB["updated_at"] == t4
      and etagA == etagB,
      f"converged: both devices hold version {fA['updated_at']} with the same ETag")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
