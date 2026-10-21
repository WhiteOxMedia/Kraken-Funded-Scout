"""test_profile_alert_log_api.py - THE ALERT LOG, END-TO-END (Oct 21).

The staging checklist's profile-logging rows, exercised through the REAL
FastAPI app (TestClient, hermetic sandbox DB - no live jackpot.db, no
network):

  4.1  POST /api/profile/alert-log {type: BUY_NOW,   payload, ts} -> stored
  4.2  POST /api/profile/alert-log {type: GET_OUT_NOW, ...}       -> stored
  5.3  GET  /api/profile/alert-log -> the member's own rows, newest first
  4.3  the profile page renders the table that reads them
  gate informational surfaces (MINER_BOT, contest_helper) are REJECTED,
  so they can never appear in an alert history
  scope a second account reads only its own rows
  auth  signed-out POST/GET are 401, and a user_id on the wire is ignored

Prints "RESULT: N passed, M failed".
"""
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

_tmp = Path(tempfile.mkdtemp(prefix="alert_log_drive_"))

import database as db                              # noqa: E402
db.DB_PATH = _tmp / "jackpot.db"
db.init_db()

import server as srv                               # noqa: E402
from fastapi.testclient import TestClient          # noqa: E402

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


client = TestClient(srv.app)

# 1. two accounts
r = client.post("/api/auth/register", json={
    "username": "alice", "password": "correct horse battery"})
check(1.1, r.status_code == 200, f"register alice: {r.status_code} {r.text[:80]}")
alice_uid = db.get_user_by_username("alice")["id"]

r = client.post("/api/auth/register", json={
    "username": "bob", "password": "correct horse battery"})
check(1.2, r.status_code == 200, f"register bob: {r.status_code} {r.text[:80]}")
bob_uid = db.get_user_by_username("bob")["id"]

# registration signs the new account in, so come back to alice before the
# write checks - otherwise the rows below would belong to bob
r = client.post("/api/auth/login", json={
    "username": "alice", "password": "correct horse battery"})
check(1.3, r.status_code == 200 and r.json().get("user_id") == alice_uid,
      f"login back as alice: {r.status_code} {r.text[:80]}")

# 2. BUY_NOW round trip (4.1)
r = client.post("/api/profile/alert-log", json={
    "type": "BUY_NOW",
    "payload": {"pair": "XMR/USDT", "price": 123.45},
    "ts": "2026-10-21T09:00:00Z",
})
check(2.1, r.status_code == 200, f"POST BUY_NOW: {r.status_code} {r.text[:120]}")
entry = (r.json() or {}).get("entry") or {}
check(2.2, entry.get("type") == "BUY_NOW", f"stored type: {entry}")
check(2.3, entry.get("payload") == {"pair": "XMR/USDT", "price": 123.45},
      f"payload round trip: {entry}")
check(2.4, entry.get("ts") == "2026-10-21T09:00:00Z",
      f"client ts kept verbatim: {entry}")

# 3. GET_OUT_NOW (4.2)
r = client.post("/api/profile/alert-log", json={
    "type": "GET_OUT_NOW", "payload": {"pair": "XMR/USDT"}})
check(3.1, r.status_code == 200 and r.json()["entry"]["type"] == "GET_OUT_NOW",
      f"POST GET_OUT_NOW: {r.status_code} {r.text[:120]}")
check(3.2, bool(r.json()["entry"].get("ts")),
      "a missing client ts is stamped server-side")

# 4. informational surfaces never become alerts
for bad in ("MINER_BOT", "contest_helper", "JACKPOT", ""):
    r = client.post("/api/profile/alert-log", json={"type": bad})
    check(4.1, r.status_code == 400,
          f"informational {bad!r} rejected: {r.status_code}")

# 5. the member reads their own history, newest first (5.3)
r = client.get("/api/profile/alert-log")
check(5.1, r.status_code == 200, f"GET: {r.status_code}")
alerts = (r.json() or {}).get("alerts") or []
check(5.2, len(alerts) == 2, f"two rows: {len(alerts)}")
check(5.3, [a["type"] for a in alerts] == ["GET_OUT_NOW", "BUY_NOW"],
      f"newest first: {[a['type'] for a in alerts]}")
check(5.4, all(a.get("ts") for a in alerts), "every row carries a ts")

# 6. no cross-account reads
r = client.post("/api/auth/login", json={
    "username": "bob", "password": "correct horse battery"})
check(6.0, r.status_code == 200,
      f"login as bob: {r.status_code} {r.text[:80]}")
r = client.get("/api/profile/alert-log")
check(6.1, r.status_code == 200 and (r.json() or {}).get("alerts") == [],
      f"bob sees nothing of alice's: {r.text[:120]}")

# a rival cannot write into alice's history by naming her on the wire
r = client.post("/api/profile/alert-log", json={
    "type": "BUY_NOW", "user_id": "alice", "payload": {"forged": True}})
check(6.2, r.status_code == 200, f"bob's own write accepted: {r.status_code}")
bob_rows = db.get_profile_alerts(bob_uid)
alice_rows = db.get_profile_alerts(alice_uid)
check(6.3, len(bob_rows) == 1 and len(alice_rows) == 2,
      f"wire user_id ignored (bob={len(bob_rows)}, alice={len(alice_rows)})")
r = client.get("/api/profile/alert-log")
bob_alerts = (r.json() or {}).get("alerts") or []
check(6.4, [a["type"] for a in bob_alerts] == ["BUY_NOW"]
      and bob_alerts[0]["payload"] == {"forged": True},
      f"bob reads only the row he wrote: {bob_alerts}")

# 7. signed-out requests are refused
anon = TestClient(srv.app)
r = anon.post("/api/profile/alert-log", json={"type": "BUY_NOW"})
check(7.1, r.status_code in (401, 403), f"anon POST: {r.status_code}")
r = anon.get("/api/profile/alert-log")
check(7.2, r.status_code in (401, 403), f"anon GET: {r.status_code}")

# 8. the profile page renders the table the endpoint feeds (4.3)
html = (HERE / "pool" / "templates" / "index.html").read_text(
    encoding="utf-8", errors="replace")
check(8.1, 'id="dashAlertLog"' in html, "profile has the alert-log table body")
check(8.2, "Your Alert Log" in html, "profile has the alert-log card")
app_js = (HERE / "pool" / "static" / "app.js").read_text(
    encoding="utf-8", errors="replace")
check(8.3, "/profile/alert-log" in app_js,
      "profile reads the endpoint through the app's API base")
check(8.4, "fetchProfileAlerts()" in app_js, "the dashboard actually calls it")

# 9. the store is APPEND-ONLY and GET returns the FULL history
src = (HERE / "pool" / "database.py").read_text(encoding="utf-8", errors="replace")
check(9.1, "UPDATE profile_alert_log" not in src
      and "DELETE FROM profile_alert_log" not in src,
      "the store is append-only: no UPDATE and no DELETE touches it")
for i in range(30):
    db.add_profile_alert(bob_uid, "BUY_NOW", payload={"n": i},
                         ts="2026-10-21T10:%02d:00Z" % i)
r = client.get("/api/profile/alert-log")
full = (r.json() or {}).get("alerts") or []
check(9.2, r.status_code == 200 and len(full) == 31,
      f"GET returns the FULL history, not a page: {len(full)} of 31")
check(9.3, full[0]["payload"] == {"n": 29}
      and full[-1]["payload"] == {"forged": True},
      f"newest first by append order: newest={full[0]['payload']}")
r = client.get("/api/profile/alert-log?limit=5")
check(9.4, len((r.json() or {}).get("alerts") or []) == 5,
      "an explicit ?limit= still pages the history")

# 10. CONCURRENT APPENDS: nothing lost, no "database is locked"
errors = []


def _append(i):
    try:
        db.add_profile_alert(alice_uid, "GET_OUT_NOW", payload={"t": i},
                             ts="2026-10-21T11:00:00Z")
    except Exception as exc:       # the failure IS the assertion below
        errors.append(repr(exc))


before = len(db.get_profile_alerts(alice_uid))
threads = [threading.Thread(target=_append, args=(i,)) for i in range(24)]
for t in threads:
    t.start()
for t in threads:
    t.join()
after = db.get_profile_alerts(alice_uid)
check(10.1, not errors, f"24 concurrent appends raised nothing: {errors[:2]}")
check(10.2, len(after) == before + 24,
      f"every concurrent write landed: {len(after) - before} of 24")
check(10.3, sorted(a["payload"]["t"] for a in after[:24]) == list(range(24)),
      "the 24 newest rows are exactly the 24 concurrent payloads")

passed = sum(1 for r in results if r)
failed = len(results) - passed
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
