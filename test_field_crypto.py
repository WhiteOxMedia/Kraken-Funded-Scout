"""test_field_crypto.py — ENCRYPTION AT REST (R2 closure, Sep 26).

Proves the at-rest surface end-to-end on hermetic sandbox DBs:
  1. key lifecycle (generate, load, env mode, rotation readability)
  2. envelope semantics (roundtrip, passthrough, tamper, double-seal)
  3. PII columns sealed at rest, transparent on every read path
  4. blind index: deterministic lookups, rebuilt across rotation
  5. push-subscription blob sealed at rest, send path transparent
  6. the boot migration: plaintext -> sealed, idempotent, no-key skip
  7. key-loss mode: loud flags, no crashes, no fabricated data
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import field_crypto as fc

results = []
def check(n, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(n) + ("  " + str(msg) if msg else ""))

def fresh_key():
    fc.KEY_DIR = Path(tempfile.mkdtemp(prefix="fc_key_"))
    fc.KEY_FILE = fc.KEY_DIR / "field_key"
    fc._KEY_CACHE = None
    fc.generate_key_file()

def fresh_db(name):
    import database as db
    db.DB_PATH = Path(tempfile.mkdtemp(prefix="fc_db_")) / (name + ".db")
    db.init_db()
    return db

WALLET = ("4AoPP7szGLmG9qG1HQg6fXH84nyprSP4tbUTiCXmCoagWqAKZYGerv63kradK"
          "AqpufNZ5H3PGMNeDfrAUa9iZtiyCUMcCms")

print("== 1. KEY LIFECYCLE ==")
fc.KEY_DIR = Path(tempfile.mkdtemp(prefix="fc_key_"))
fc.KEY_FILE = fc.KEY_DIR / "field_key"
fc._KEY_CACHE = None
check(1.1, fc.available() is False, "no key -> unavailable (plaintext-compat)")
r = fc.generate_key_file()
import os as _os
_mode_ok = _os.name != "posix" or (fc.KEY_FILE.stat().st_mode & 0o777) == 0o600
check(1.2, r["ok"] and fc.available() is True and _mode_ok,
      f"key file generated (id {r['key_id']}; mode 600 asserted on POSIX)")
check(1.3, fc._load_keys()[0][0].startswith("k"),
      "active key carries the k-prefix id")
env1 = fc.encrypt("secret-value")
fc.generate_key_file()
check(1.4, fc.decrypt(env1) == "secret-value",
      "rotation: old envelopes stay readable via the retired key")
check(1.5, len(fc._load_keys()) == 2
      and fc._load_keys()[1][0].startswith("r"),
      "retired key present, decrypt-only ordering")

print("== 2. ENVELOPE SEMANTICS ==")
check(2.1, fc.encrypt("hello") != fc.encrypt("hello"),
      "fresh nonce per seal (no deterministic ciphertext)")
e = fc.encrypt(WALLET)
check(2.2, e.startswith("enc1:") and fc.decrypt(e) == WALLET,
      "roundtrip through the versioned envelope")
check(2.3, fc.encrypt("") == "" and fc.encrypt(None) is None
      and fc.decrypt("") == "" and fc.decrypt(None) is None,
      "empty/None passthrough both directions")
check(2.4, fc.encrypt(e) == e, "double-seal is a no-op")
check(2.5, fc.decrypt("plain@example.com") == "plain@example.com",
      "pre-migration plaintext rows read unchanged")
try:
    fc.decrypt(e[:-4] + "!!!!")
    check(2.6, False, "tamper must raise")
except ValueError:
    check(2.6, True, "tampered envelope refused (AEAD)")
try:
    fc.decrypt("enc1:nocolon")
    check(2.7, False, "malformed envelope must raise")
except ValueError:
    check(2.7, True, "malformed envelope refused")

print("== 3. PII COLUMNS SEALED AT REST ==")
fresh_key()
db = fresh_db("pii")
uid = db.create_user("piiuser", "hash", email="PieUser@Example.test",
                     wallet_address=WALLET, phone="+15550001111",
                     full_name="Pie User")
raw = sqlite3.connect(str(db.DB_PATH)).execute(
    "SELECT email, phone, full_name, wallet_address FROM users WHERE id=?",
    (uid,)).fetchone()
check(3.1, all(str(v).startswith("enc1:") for v in raw),
      "ALL four PII columns sealed in the raw file")
check(3.2, "pieuser@example.test" not in "\n".join(
      sqlite3.connect(str(db.DB_PATH)).iterdump()).lower(),
      "plaintext email absent from a full DB dump")
u = db.get_user(uid)
check(3.3, u["email"] == "PieUser@Example.test" and u["phone"] == "+15550001111"
      and u["full_name"] == "Pie User" and u["wallet_address"] == WALLET,
      "transparent decryption on get_user")
check(3.4, db.get_all_users()[0]["email"] == "PieUser@Example.test",
      "transparent decryption on get_all_users")
import server as srv
from fastapi.testclient import TestClient
client = TestClient(srv.app)
r = client.post("/api/auth/login", json={"username": "piiuser",
                                         "password": "x"})   # wrong pw on purpose
me = client.get("/api/auth/me").json()
check(3.5, me.get("authenticated") is False,
      "unauthenticated /me stays unauthenticated")
r = client.post("/api/auth/register", json={
    "username": "piiuser2", "password": "correct horse battery",
    "email": "second@example.test", "phone": "+15550002222",
    "full_name": "Second User", "wallet_address": WALLET})
check(3.6, r.status_code in (200, 400),
      f"register envelope accepted or wallet-dupe refused: {r.status_code}")
uid2 = sqlite3.connect(str(db.DB_PATH)).execute(
    "SELECT id FROM users WHERE username='piiuser2'").fetchone()
check(3.7, uid2 is not None and db.get_user(uid2[0])["email"]
      == "second@example.test",
      "the registration path sealed AND reads back")

print("== 4. BLIND INDEX ==")
i1 = fc.blind_index(WALLET)
check(4.1, i1 == fc.blind_index(WALLET) and len(i1) == 32,
      "deterministic, fixed-length index")
check(4.2, fc.blind_index(WALLET) != fc.blind_index(WALLET.lower()),
      "distinct values -> distinct indexes (case-sensitive identity)")
check(4.3, fc.blind_index("") == "", "empty wallet -> empty index")
got = db.get_user_by_wallet(WALLET)
check(4.4, got and got["id"] == uid, "by-wallet lookup hits via the index")
check(4.5, db.get_user_by_wallet("4" + "A" * 20) is None
      and db.get_user_by_wallet("") is None,
      "misses stay misses, empty never matches")

print("== 5. SUBSCRIPTION STORE ==")
import push_service as ps
ps.subscribe(uid, {"endpoint": "https://push.example/fc-dev",
                   "keys": {"p256dh": "k1", "auth": "a1"}})
raw = sqlite3.connect(str(db.DB_PATH)).execute(
    "SELECT value FROM settings WHERE key='push_subscriptions'"
).fetchone()[0]
check(5.1, raw.startswith("enc1:")
      and "push.example" not in raw and "p256dh" not in raw,
      "subscription blob sealed: no endpoint, no key material")
subs = ps._load(ps.SUBS_KEY)
check(5.2, subs and subs[0]["endpoint"] == "https://push.example/fc-dev",
      "send path reads through the seal")
res = ps.send_to_user(uid, "push_test")
check(5.3, res["reason"] == "VAPID key not provisioned",
      "the gated step is VAPID, not the (working) subscription lookup")
db.set_setting("VAPID_PRIVATE_KEY", "sandbox-dummy")
db.set_setting("VAPID_PUBLIC_KEY", "B" + "A" * 86)
res = ps.send_to_user(uid, "push_test")
check(5.4, res["sent"] == 0 and res["reason"] == "no deliverable endpoint"
      and res["dead"] == 1,
      "sender ran the full loop against the sealed store")

print("== 6. THE BOOT MIGRATION ==")
db2 = fresh_db("mig")
plain_uid = db2.create_user("legacy", "hash", email="legacy@example.test",
                            wallet_address=WALLET, phone="+15550003333",
                            full_name="Legacy User")
import database as _dbmod
with _dbmod.get_db() as conn:   # force the row back to plaintext
    conn.execute("UPDATE users SET email='legacy@example.test', "
                 "phone='+15550003333', full_name='Legacy User', "
                 "wallet_address=?, wallet_idx='' WHERE id=?",
                 (WALLET, plain_uid))
_dbmod.set_setting("push_subscriptions", json.dumps(
    [{"user_id": plain_uid, "endpoint": "https://push.example/legacy",
      "keys": {"p256dh": "k", "auth": "a"}}]))
out = db2.encrypt_existing_pii()
check(6.1, out.get("users_sealed") == 1 and out.get("subs_blob_sealed") == 1,
      f"migration seals rows + blob: {out}")
u = db2.get_user(plain_uid)
check(6.2, u["email"] == "legacy@example.test"
      and db2.get_user_by_wallet(WALLET)["id"] == plain_uid,
      "post-migration reads + lookups work")
out2 = db2.encrypt_existing_pii()
check(6.3, out2.get("users_sealed") == 0 and out2.get("subs_blob_sealed") == 0,
      "idempotent: second run changes nothing")

# 6.6 THE NULL-EMAIL PRESERVATION (review batch, Sep 27 CRITICAL): the
# live db carries five real NULL-email users, and the dangerous shape is
# "plaintext in ANOTHER column + NULL email" - the old coalescing loop
# wrote encrypt("") into the UNIQUE email column of EVERY such row, so
# the second one IntegrityErrored the whole migration. Two such rows,
# the migration must seal the wallet and leave the email NULL.
null_a = db2.create_user("fc_null_a", "x")
null_b = db2.create_user("fc_null_b", "x")
import database as _dbmod6
with _dbmod6.get_db() as conn:      # force the exact live-DB shape
    conn.execute("UPDATE users SET wallet_address=?, wallet_idx='' "
                 "WHERE id=?", ("4NullProbeWalletAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1", null_a))
    conn.execute("UPDATE users SET wallet_address=?, wallet_idx='' "
                 "WHERE id=?", ("4NullProbeWalletBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB2", null_b))
outn = db2.encrypt_existing_pii()
ua = db2.get_user(null_a)
ub = db2.get_user(null_b)
# assertions at the right layers: get_user DECRYPTS transparently, so
# the envelope check goes to the RAW row, and the sealed wallet is
# proven by the blind-index lookup working
import database as _raw6
with _raw6.get_db() as conn:
    raw_a = conn.execute("SELECT email, wallet_address FROM users WHERE id=?",
                         (null_a,)).fetchone()
    raw_b = conn.execute("SELECT email, wallet_address FROM users WHERE id=?",
                         (null_b,)).fetchone()
WA = "4NullProbeWalletAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1"
WB = "4NullProbeWalletBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB2"
check(6.6, raw_a["email"] is None and raw_b["email"] is None
      and str(raw_a["wallet_address"]).startswith("enc1:")
      and str(raw_b["wallet_address"]).startswith("enc1:")
      and db2.get_user_by_wallet(WA)["id"] == null_a
      and db2.get_user_by_wallet(WB)["id"] == null_b,
      f"wallets sealed + lookups work while NULL emails stay NULL "
      f"(no shared empty-email identity): out={outn}")
fc._KEY_CACHE = []
fc.KEY_FILE = Path(tempfile.mkdtemp(prefix="fc_nokey_")) / "none"
out3 = db2.encrypt_existing_pii()
check(6.4, "skipped" in out3, f"no key -> documented no-op: {out3}")
check(6.5, db2.get_user(plain_uid).get("email_error") == "undecryptable",
      "keyless read is the loud 7.x mode, never fabricated plaintext")
fc._KEY_CACHE = None

print("== 7. KEY-LOSS MODE ==")
lost = db
fc.KEY_DIR = Path(tempfile.mkdtemp(prefix="fc_lost_"))
fc.KEY_FILE = fc.KEY_DIR / "field_key"
fc._KEY_CACHE = None
u = lost.get_user(plain_uid)
check(7.1, u.get("email_error") == "undecryptable"
      and "enc1:" in str(u.get("email")),
      "lost key: loud flag, envelope preserved, no crash")
check(7.2, lost.get_user_by_wallet(WALLET) is None
      or lost.get_user_by_wallet(WALLET).get("id") == plain_uid,
      "lost key: index lookup either misses or confirms by envelope")
res = lost.encrypt_existing_pii()
check(7.3, "skipped" in res, "migration refuses to half-run without a key")

print("== 8. ROTATION FULL CYCLE ==")
fresh_key()
db3 = fresh_db("rot")
rid = db3.create_user("rotuser", "hash", email="rot@example.test",
                      wallet_address=WALLET, phone="+15550004444",
                      full_name="Rot User")
first_idx = sqlite3.connect(str(db3.DB_PATH)).execute(
    "SELECT wallet_idx FROM users WHERE id=?", (rid,)).fetchone()[0]
fc.generate_key_file()                       # rotate
check(8.1, db3.get_user(rid)["email"] == "rot@example.test",
      "old envelopes readable after rotation")
out = db3.reencrypt_pii()
check(8.2, out["rows_resealed"] == 1, f"re-encrypt pass: {out}")
new_idx = sqlite3.connect(str(db3.DB_PATH)).execute(
    "SELECT wallet_idx FROM users WHERE id=?", (rid,)).fetchone()[0]
check(8.3, new_idx != first_idx,
      "blind index rebuilt under the new key (old index invalid)")
check(8.4, db3.get_user_by_wallet(WALLET)["id"] == rid
      and db3.get_user(rid)["wallet_address"] == WALLET,
      "lookups + reads consistent after the full rotation cycle")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
