"""test_e2e_acceptance.py - THE E2E ACCEPTANCE RUN (Sep 26).

The user's verification task, automated hermetically through the REAL
FastAPI app (TestClient; sandbox DB; no live jackpot.db, no network,
no real VAPID send). Prints "RESULT: N passed, M failed".
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

_tmp = Path(tempfile.mkdtemp(prefix="e2e_acceptance_"))

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


# Sep 28: the payout gate runs the OFFLINE mainnet validation (decode,
# netbyte, Keccak checksum), so the fixture wallet is CONSTRUCTED-VALID
# through pool/address_check's own encoder — not a remembered constant.
import hashlib  # noqa: E402
from address_check import b58encode as _b58e, keccak256 as _k256  # noqa: E402
_sp = hashlib.sha256(b"e2e-acceptance spend fixture").digest()
_vw = hashlib.sha256(b"e2e-acceptance view fixture").digest()
WALLET = _b58e(b"\x12" + _sp + _vw + _k256(b"\x12" + _sp + _vw)[:4])
assert len(WALLET) == 95

client = TestClient(srv.app)

print("== 1. REGISTRATION (name, email, phone, Kraken wallet) ==")
r = client.post("/api/auth/register", json={
    "username": "e2e_alice", "password": "correct horse battery",
    "email": "alice@example.test", "full_name": "Alice Henderson",
    "phone": "+15551234567", "wallet_address": WALLET})
check(1.1, r.status_code == 200, f"register: {r.status_code} {r.text[:100]}")
row = db.get_user_by_username("e2e_alice")
check(1.2, row is not None, "user row exists")
check(1.3, row.get("full_name") == "Alice Henderson"
      and row.get("email") == "alice@example.test"
      and row.get("phone") == "+15551234567"
      and row.get("wallet_address") == WALLET
      and bool(row.get("created_at")),
      "DB row carries name/email/phone/wallet/created_at")

print("== 2. WALLET FORMAT VALIDATION ==")
r = client.post("/api/auth/register", json={
    "username": "bad_wallet", "password": "x",
    "wallet_address": "5564so brookridge Dr #14r Murray Utah 84107"})
check(2.1, r.status_code == 400,
      f"street-address wallet rejected: {r.status_code}")
r = client.post("/api/auth/register", json={
    "username": "bad_wallet2", "password": "x",
    "wallet_address": "4short"})
check(2.2, r.status_code == 400, f"short wallet rejected: {r.status_code}")
r = client.post("/api/auth/register", json={
    "username": "no_wallet", "password": "x"})
check(2.3, r.status_code == 200, "empty wallet stays optional")

print("== 3. REGISTRATION RATE LIMIT (5/hour/IP) ==")
saw_429 = False
for i in range(6):
    r = client.post("/api/auth/register", json={
        "username": f"ratelimit_{i}", "password": "x"})
    if r.status_code == 429:
        saw_429 = True
        break
check(3.1, saw_429,
      f"the 6th registration attempt hit 429 (stopped at {i + 1})")

# Session hygiene: every registration sets ITS session cookie, so the
# shared client now carries the LAST rate-limit user's session. Re-login
# alice so her subscriptions/preferences land on HER user_id.
r = client.post("/api/auth/login", json={
    "username": "e2e_alice", "password": "correct horse battery"})
check(3.2, r.status_code == 200, f"alice re-login: {r.status_code}")

print("== 4. PUSH SUBSCRIPTION, MULTI-DEVICE ==")
uid = db.get_user_by_username("e2e_alice")["id"]
r1 = client.post("/api/push/subscribe", json={"subscription": {
    "endpoint": "https://push.example/alice-phone",
    "keys": {"p256dh": "k1", "auth": "a1"}}})
r2 = client.post("/api/push/subscribe", json={"subscription": {
    "endpoint": "https://push.example/alice-desktop",
    "keys": {"p256dh": "k2", "auth": "a2"}}})
check(4.1, r1.status_code == 200 and r1.json()["ok"]
      and r2.status_code == 200 and r2.json()["ok"],
      "two devices subscribe")
subs = ps._load(ps.SUBS_KEY)
mine = [s for s in subs if int(s["user_id"]) == uid]
check(4.2, len(mine) == 2
      and all(set(s["keys"].keys()) == {"p256dh", "auth"} for s in mine),
      "both endpoints stored for the user with key pairs")
check(4.3, all(s["endpoint"].startswith("https://") for s in mine),
      "endpoints are https-only")

print("== 5. SEND FLOW (gate -> limiter -> sender) ==")
# Sandbox provision: production reads operator-installed VAPID keys
# from settings; the sandbox provisions dummies so the sender loop
# RUNS (webpush() fails against push.example -> dead), proving the
# full order gate -> limiter -> VAPID-signed sender -> send_allowed.
db.set_setting("VAPID_PRIVATE_KEY", "sandbox-dummy-private-key")
db.set_setting("VAPID_PUBLIC_KEY", "sandbox-dummy-public-key")
res = ps.send_to_user(uid, "ticket_update")
check(5.1, res["sent"] == 0 and res.get("dead", 0) >= 1
      and res.get("reason") == "no deliverable endpoint",
      f"flow reaches the sender: dead endpoint counted, allowed-audit "
      f"lands (reason={res.get('reason')!r} dead={res.get('dead')})")

print("== 6. OPT-OUT ENFORCEMENT ==")
r = client.post("/api/push/preferences", json={
    "preferences": {"jackpot": True, "scout": False}})
check(6.1, r.status_code == 200
      and r.json()["preferences"] == {"jackpot": True, "scout": False},
      "scout OFF, jackpot ON saved")
res = ps.send_to_user(uid, "signal_buy")
check(6.2, res["sent"] == 0 and "opted out" in (res.get("reason") or ""),
      "scout signal blocked by the pref gate")
res = ps.send_to_user(uid, "jackpot_win")
check(6.3, "opted out" not in (res.get("reason") or ""),
      "jackpot ON passes the pref gate")
with db.get_db() as conn:
    blocked = conn.execute(
        "SELECT COUNT(*) c FROM audit_log "
        "WHERE action = 'push_send_blocked'").fetchone()["c"]
check(6.4, blocked >= 1, f"{blocked} push_send_blocked audit row(s)")

r = client.post("/api/push/preferences", json={
    "preferences": {"jackpot": False, "scout": True}})
res = ps.send_to_user(uid, "jackpot_win")
check(6.5, res["sent"] == 0 and "opted out" in (res.get("reason") or ""),
      "jackpot OFF blocks jackpot_win")
res = ps.send_to_user(uid, "signal_buy")
check(6.6, "opted out" not in (res.get("reason") or ""),
      "scout ON passes the pref gate")
check(6.7, len([s for s in ps._load(ps.SUBS_KEY)
                if int(s["user_id"]) == uid]) == 2,
      "prefs govern both devices (subscriptions unchanged)")

print("== 7. OPTIMISTIC CONCURRENCY ==")
g1 = client.get("/api/push/preferences")
etag = g1.headers.get("etag")
check(7.1, etag and etag.strip('"') == (g1.json().get("updated_at") or "v0"),
      f"GET serves ETag == updated_at: {etag}")
g304 = client.get("/api/push/preferences",
                  headers={"If-None-Match": etag})
check(7.2, g304.status_code == 304,
      f"If-None-Match -> 304: {g304.status_code}")
r_other = client.post("/api/push/preferences", json={
    "preferences": {"jackpot": True, "scout": True}})
check(7.3, r_other.status_code == 200, "device B writes first")
r_stale = client.post("/api/push/preferences",
                      headers={"If-Match": etag},
                      json={"preferences": {"jackpot": False}})
check(7.4, r_stale.status_code == 412
      and "conflict" in (r_stale.json().get("error") or ""),
      f"stale If-Match -> 412: {r_stale.status_code} "
      f"{r_stale.json().get('error')!r}")
check(7.5, r_stale.headers.get("etag") is not None,
      "the 412 carries the CURRENT etag for the retry")
r_ok = client.post("/api/push/preferences",
                   headers={"If-Match": r_stale.headers.get("etag")},
                   json={"preferences": {"jackpot": False}})
check(7.6, r_ok.status_code == 200
      and r_ok.json()["preferences"]["jackpot"] is False,
      "retry with the current etag succeeds")

print("== 8. BROADCAST PATH ==")
client.post("/api/push/preferences", json={
    "preferences": {"jackpot": False, "scout": True}})
res = ps.send_to_subscribers("jackpot_win")
with db.get_db() as conn:
    bc = conn.execute(
        "SELECT details FROM audit_log WHERE action = 'push_broadcast' "
        "ORDER BY id DESC LIMIT 1").fetchone()
check(8.1, bc is not None, "the broadcast audited once")
check(8.2, res.get("sent") is not None, "broadcast completed")

print("== 9. AUDIT TRAIL ==")
with db.get_db() as conn:
    acts = {r[0] for r in conn.execute(
        "SELECT DISTINCT action FROM audit_log").fetchall()}
for need in ("register", "push_subscribe", "push_pref_changed",
             "push_send_blocked", "push_send_allowed"):
    check(9.1, need in acts, f"audit action present: {need}")

print("== 10. SECURITY ==")
with db.get_db() as conn:
    det = [r[0] for r in conn.execute(
        "SELECT details FROM audit_log WHERE action LIKE 'push_%' "
        "OR action = 'register'").fetchall()]
blob = " ".join(det)
check(10.1, "+15551234567" not in blob and "Alice Henderson" not in blob
      and WALLET not in blob,
      "no phone / full name / wallet in audit details")
r = client.post("/api/auth/register", json={
    "username": "leakcheck", "password": "secret-value"})
check(10.2, "secret-value" not in r.text,
      "error responses never echo secrets")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
