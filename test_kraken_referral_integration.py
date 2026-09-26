"""test_kraken_referral_integration.py — the pipeline through the REAL
FastAPI app: simulate provider -> snapshot rows -> projection ->
/api/jackpot/projection -> admin trigger (503 unconfigured / 200
simulate) -> stubbed live provider (success, rate-limit backoff,
persistent 5xx) -> the referral-window notice rate limit."""
import sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))
_tmp = Path(tempfile.mkdtemp(prefix="kr_int_"))
import database as db; db.DB_PATH = _tmp / "jackpot.db"; db.init_db()
import jackpot_funding as funding
import kraken_referral as kr
import server as srv
from fastapi.testclient import TestClient

results = []
def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}" + (f"  ({why})" if why and not cond else ""))

client = TestClient(srv.app)
WALLET = ("4AoPP7szGLmG9qG1HQg6fXH84nyprSP4tbUTiCXmCoagWqAKZYGerv63kradK"
          "AqpufNZ5H3PGMNeDfrAUa9iZtiyCUMcCms")

print("== 0. THE BOX: admin user (first user is admin), velocity seed ==")
r = client.post("/api/auth/register", json={
    "username": "kr_admin", "password": "correct horse battery",
    "email": "ops@example.test", "full_name": "Ops Bot",
    "phone": "+15557770000", "wallet_address": WALLET})
check(0.1, r.status_code == 200, f"register: {r.status_code}")
r = client.post("/api/auth/login", json={
    "username": "kr_admin", "password": "correct horse battery"})
check(0.2, r.status_code == 200, "admin session set")
funding.record_funding("referral_bonus", 70.0, source="int-seed")  # 5.0/day

print("== 1. DISCOVERY + UNMAPPED ==")
with db.get_db() as conn:
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username='kr_admin'").fetchone()["id"]
    for uname, code in (
            ("u_paid", "SIM-PAID-001"), ("u_pend", "SIM-PEND-002"),
            ("u_elig", "SIM-ELIG-003"), ("u_expi", "SIM-EXPI-004"),
            ("u_odd", "SIM-ODD-005"), ("u_none", "")):
        conn.execute(
            "INSERT INTO users (username, password_hash, kraken_referral_id,"
            " created_at) VALUES (?, ?, ?, strftime('%s','now'))",
            (uname, "x", code if code else None))
# the operator maps the admin account through THE MAPPING API (the
# same act the admin UI performs) — registration never invents a
# Kraken id, so kr_admin starts unmapped like any other account
r = client.post("/api/admin/kraken-referral/mappings",
                json={"user_id": admin_id, "kraken_referral_id": "sim-admin-777"})
check(1.1, r.status_code == 200
      and r.json()["kraken_referral_id"] == "SIM-ADMIN-777",
      f"operator attaches kr_admin's real id (normalized upper): {r.status_code}")

print("== 2. OPERATOR TRIGGER: unconfigured -> 503 (never fabricates) ==")
r = client.post("/api/admin/kraken-referral/run", json={})
check(2.1, r.status_code == 503 and "not configured" in r.text,
      f"unconfigured trigger refused: {r.status_code}")

print("== 3. CANARY RUN (simulate provider, whole pipeline) ==")
db.set_setting("KRAKEN_REFERRAL_MODE", "simulate")
r = client.post("/api/admin/kraken-referral/run", json={})
check(3.1, r.status_code == 200, f"trigger: {r.status_code} {r.text[:120]}")
s = r.json()
check(3.2, s["mode"] == "simulate" and s["source"] == "kraken_referral_sim_v1",
      "run honestly labeled simulate")
check(3.3, s["processed"] == 6 and s["unmapped"] == 1,
      f"6 mapped fetched, 1 unmapped: {s['processed']}/{s['unmapped']}")
check(3.4, s["errors"] == 0, "no fetch errors")
with db.get_db() as conn:
    snaps = conn.execute(
        "SELECT user_id, kraken_status, seeded_equivalent, estimate_used"
        " FROM kraken_referral_snapshot").fetchall()
by_status = {}
for row in snaps:
    by_status.setdefault(row["kraken_status"], []).append(row)
check(3.5, len(snaps) == 6, f"{len(snaps)} snapshot rows (one per mapped fetch)")
check(3.6, len(by_status.get("Paid", [])) == 1
      and by_status["Paid"][0]["seeded_equivalent"] == 40.0
      and by_status["Paid"][0]["estimate_used"] == 0,
      "Paid uses the actual payout as-is")
check(3.7, len(by_status.get("Pending", [])) == 1
      and by_status["Pending"][0]["seeded_equivalent"] == 25.0,
      "Pending explicit amount wins")
check(3.8, len(by_status.get("Eligible", [])) >= 2
      and all(row["estimate_used"] == 1 for row in by_status["Eligible"]),
      "Eligible rows are flagged estimates")
check(3.9, len(by_status.get("Ineligible", [])) == 1
      and by_status["Ineligible"][0]["seeded_equivalent"] == 0.0,
      "Expired -> Ineligible contributes zero")
check(3.10, len(by_status.get("unknown", [])) == 1
      and by_status["unknown"][0]["seeded_equivalent"] == 0.0,
      "unknown status contributes zero (fail-closed)")
seed_expect = round(sum(row["seeded_equivalent"]
                        for rows in by_status.values() for row in rows
                        if row["kraken_status"] in
                        ("Paid", "Pending", "Eligible")), 2)
check(3.11, abs(s["referral_seed"] - seed_expect) < 0.01,
      f"aggregate seed == sum of contributing rows ({s['referral_seed']})")
with db.get_db() as conn:
    unmapped_audit = conn.execute(
        "SELECT COUNT(*) c FROM audit_log WHERE"
        " action='kraken_referral.unmapped'").fetchone()["c"]
    fetched_audit = conn.execute(
        "SELECT COUNT(*) c FROM audit_log WHERE"
        " action='kraken_referral.fetched'").fetchone()["c"]
check(3.12, unmapped_audit == 1 and fetched_audit == 6,
      f"audits: {fetched_audit} fetched, {unmapped_audit} unmapped")

print("== 4. THE READ MODEL: /api/jackpot/projection ==")
r = client.get("/api/jackpot/projection")
d = r.json()
check(4.1, r.status_code == 200 and d["mode"] == "simulate", "public read works")
check(4.2, abs(d["possible_future_jackpot_usd"] -
      (d["current_jackpot_usd"] + d["referral_seed_usd"])) < 0.01,
      f"possible == current + seed ({d['possible_future_jackpot_usd']} = "
      f"{d['current_jackpot_usd']} + {d['referral_seed_usd']})")
check(4.3, d["source"] == "kraken_referral_sim_v1" and d["fresh"] is True
      and d["count_of_users_included"] == 6, "freshness + provenance surfaced")
check(4.4, d["anomaly"] == 0, "no anomaly on the first run")

print("== 5. AUTH GATES ==")
anon = TestClient(srv.app)
r = anon.post("/api/admin/kraken-referral/run", json={})
check(5.1, r.status_code in (401, 403), f"anonymous trigger rejected: {r.status_code}")
r = anon.post("/api/push/preferences", json={"preferences": {}})
check(5.2, r.status_code == 401, "the projection surface adds no auth bypass")

print("== 6. LIVE PROVIDER (STUBBED KRAKEN) ==")
db.set_setting("KRAKEN_API_KEY", "test-key")
db.set_setting("KRAKEN_API_SECRET", "dGVzdC1zZWNyZXQ=")
db.set_setting("KRAKEN_REFERRAL_ENDPOINT",
               "https://api.kraken.localhost/0/private/ReferralStatus")
db.set_setting("KRAKEN_REFERRAL_MODE", "")
check(6.1, kr.provider_mode() == "live", "credentials -> live mode")
def _ok_post(endpoint, key, sec, body):
    return {"error": [], "result": {
        "referral_id": body["referral_id"], "status": "active",
        "pending_amount": 10.0}}
kr._kraken_post = _ok_post
r = client.post("/api/admin/kraken-referral/run", json={})
s = r.json()
check(6.2, r.status_code == 200 and s["mode"] == "live" and s["errors"] == 0,
      "live run through the stubbed client")
with db.get_db() as conn:
    n = conn.execute("SELECT COUNT(*) c FROM kraken_referral_snapshot "
                     "WHERE source='kraken_referral_api_v1'").fetchone()["c"]
check(6.3, n == 6, f"{n} live-sourced snapshot rows (6 mapped users)")
state = {"tries": 0}
def _rl_post(endpoint, key, sec, body):
    state["tries"] += 1
    if state["tries"] < 3:
        return {"error": ["EAPI:Rate limit exceeded"]}
    return _ok_post(endpoint, key, sec, body)
kr._kraken_post = _rl_post
r = client.post("/api/admin/kraken-referral/run", json={})
s = r.json()
check(6.4, r.status_code == 200 and s["errors"] == 0 and state["tries"] >= 3,
      f"exponential backoff recovered after rate limits (tries={state['tries']})")
def _boom(endpoint, key, sec, body):
    raise RuntimeError("500 server error")
kr._kraken_post = _boom
db.set_setting("KRAKEN_REFERRAL_MAX_RETRIES", "2")
import time as _t
_t0 = _t.time()
r = client.post("/api/admin/kraken-referral/run", json={"subset_limit": 1})
elapsed = _t.time() - _t0
s = r.json()
check(6.5, r.status_code == 200 and s["errors"] >= 1,
      "persistent failures degrade to error rows; the run completes")
check(6.6, elapsed >= 1.0, f"backoff genuinely waits ({elapsed:.1f}s)")
with db.get_db() as conn:
    ferr = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                        "action='kraken_referral.fetch_error'").fetchone()["c"]
check(6.7, ferr >= 1, f"{ferr} fetch_error audits")
db.set_setting("KRAKEN_API_KEY", "")
db.set_setting("KRAKEN_API_SECRET", "")
db.set_setting("KRAKEN_REFERRAL_ENDPOINT", "")
check(6.8, kr.provider_mode() == "unconfigured", "credential removal degrades cleanly")

print("== 6.9 THE DELTA-SPIKE GUARD ==")
# the _boom run collapsed the seed 60 -> 0 (a 100% drop against the
# previous projection): the guard must flag the row and audit it
lp = kr.latest_projection()
check(6.9, lp["anomaly"] == 1,
      f"projection flagged anomalous after the collapse (anomaly={lp['anomaly']})")
with db.get_db() as conn:
    spike = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                         "action='jackpot_projection.delta_spike'").fetchone()["c"]
check(6.10, spike >= 1, f"{spike} delta_spike audit(s)")

print("== 7. THE NOTICE: referral_window rate limits ==")
client.post("/api/push/subscribe", json={"subscription": {
    "endpoint": "https://push.example/kr-phone",
    "keys": {"p256dh": "k", "auth": "a"}}})
db.set_setting("VAPID_PRIVATE_KEY", "sandbox-dummy")
# per-user cap (2/h for referral_window): two sends land, the third
# is stopped by the USER limiter and audited
r1 = kr.push_svc.send_to_user(admin_id, "referral_window")
r2 = kr.push_svc.send_to_user(admin_id, "referral_window")
r3 = kr.push_svc.send_to_user(admin_id, "referral_window")
with db.get_db() as conn:
    allowed = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
        "action='push_send_allowed' AND details LIKE '%referral_window%'"
        " AND user_id = ?", (admin_id,)).fetchone()["c"]
    ulimited = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
        "action='push_rate_limited' AND details LIKE '%referral_window%'"
        " AND user_id = ?", (admin_id,)).fetchone()["c"]
check(7.1, allowed == 2 and ulimited == 1,
      f"2/h/user cap enforced: allowed={allowed}, user-limited={ulimited}")
# the broadcast-level limiter is a logged decision as well: fill the
# broadcast bucket, then the notice's own fan-out must refuse LOUDLY
for _ in range(20):
    kr.push_svc._rate_ok(0, "referral_window")
rb = kr.notify_window_closing()
check(7.2, rb.get("reason") == "rate limited",
      f"broadcast limiter stops the fan-out: {rb}")
with db.get_db() as conn:
    blimited = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
        "action='push_rate_limited' AND details LIKE '%broadcast%'"
        ).fetchone()["c"]
check(7.3, blimited == 1, "broadcast rate-limit audited (scope=broadcast)")
check(7.4, all(x.get("sent") == 0 for x in (r1, r2, r3, rb)),
      "sandbox terminal: dead endpoints, no fabricated delivery")

print("== 8. ADMIN CONFIG + CONNECTION TEST ==")
anon = TestClient(srv.app)
r = anon.get("/api/admin/kraken-referral/config")
check(8.1, r.status_code in (401, 403), f"config read admin-gated: {r.status_code}")
r = client.get("/api/admin/kraken-referral/config")
cfg = r.json()
# (on a box whose OS keyring holds the kraken pair — setup_kraken.py —
# the tier-aware mask HONESTLY reports it; the pin is consistency,
# not environment-specific absence)
check(8.2, r.status_code == 200 and cfg["mode"] == "unconfigured"
      and (not cfg["api_key"]["set"]
           or cfg["api_key"]["source"] == "keyring")
      and (not cfg["api_secret"]["set"]
           or cfg["api_secret"]["source"] == "keyring"),
      f"config read reflects the unconfigured mode with honest tiers: "
      f"{cfg['mode']}, key={cfg['api_key']}")
r = client.post("/api/admin/kraken-referral/config", json={
    "api_key": "ktest1234567890", "api_secret": "c2VjcmV0MTIzNDU2Nzg5MA==",
    "endpoint": "https://api.kraken.localhost/0/private/ReferralStatus",
    "simulate": False})
check(8.3, r.status_code == 200 and r.json()["mode"] == "live"
      and len(r.json()["updated"]) == 4, "save flips mode to live (2 secrets + endpoint + mode)")
r = client.get("/api/admin/kraken-referral/config")
cfg = r.json()
check(8.4, cfg["api_key"]["set"] and cfg["api_key"]["tail"] == "…7890"
      and "ktest1234567890" not in r.text and "c2VjcmV0MTIzNDU2Nzg5MA==" not in r.text,
      f"secrets masked to tails: {cfg['api_key']['tail']}")
r = client.post("/api/admin/kraken-referral/config", json={"api_key": "", "api_secret": ""})
check(8.5, r.status_code == 200, "blank secrets keep stored values")
r = client.post("/api/admin/kraken-referral/config",
                json={"kraken_api_key": "kcanon99999999"})
j = r.json()
check("8.5b", r.status_code == 200 and j["mode"] == "live"
      and "KRAKEN_API_KEY" in j["updated"],
      f"canonical field names accepted too: {j['updated']}")
kr._kraken_post = lambda ep, k, s, b: {"error": [], "result": {
    "referral_id": b["referral_id"], "status": "pending",
    "pending_amount": 10.0}}
r = client.post("/api/admin/kraken-referral/test", json={"referral_id": "R-1"})
j = r.json()
check(8.6, r.status_code == 200 and j["ok"] is True and j["mode"] == "live"
      and "raw_response" in j and j["normalized"]["status"] == "Pending",
      f"connection test: raw + normalized shown: {j.get('raw_response', '')[:60]}")
kr._kraken_post = _boom
r = client.post("/api/admin/kraken-referral/test", json={"referral_id": "R-1"})
j = r.json()
check(8.7, r.status_code == 200 and j["ok"] is False and j.get("hint"),
      f"failed test diagnoses: {j.get('hint', '')[:60]}")
db.set_setting("KRAKEN_REFERRAL_MODE", "simulate")
r = client.post("/api/admin/kraken-referral/test", json={"referral_id": "SIM-PAID-001"})
j = r.json()
check(8.8, j["ok"] is True and j["mode"] == "simulate" and j["stage"] == "fetch",
      "simulate-mode test fetches fixtures honestly")
with db.get_db() as conn:
    n = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                     "action='kraken_referral.connection_test'").fetchone()["c"]
    n2 = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                      "action='kraken_referral.config_updated'").fetchone()["c"]
check(8.9, n >= 3 and n2 >= 2, f"audits: {n} tests, {n2} config updates")
r = client.post("/api/admin/kraken-referral/test", json={})
check(8.10, r.status_code == 400, "test without referral_id -> 400")

print("== 8b. THE KEYRING TIER (settings -> OS credential store) ==")
# The accessor must see settings FIRST, then the OS keyring (Windows
# Credential Manager, where setup_kraken.py stores the pair) — the
# secret never copied into the database, the keyring never written.
# (section 8.8 left KRAKEN_REFERRAL_MODE=simulate — clear it, the
# operator's labeled dry-run would mask the tier checks below)
db.set_setting("KRAKEN_REFERRAL_MODE", "")
check("8b.1", kr.kraken_credentials() ==
      ("kcanon99999999", "c2VjcmV0MTIzNDU2Nzg5MA==", "settings"),
      "settings tier wins while both are populated")
import types
_fake = types.ModuleType("keyring")
_set_calls = []
_fake.set_password = lambda s, u, v: _set_calls.append((s, u, v))
_fake.get_password = lambda s, u: (
    "KR-KEYRING-KEY-0001" if u == "kraken_api_key" else
    "c2VjcmV0MTIzNDU2Nzg5MA==" if u == "kraken_api_secret" else None)
sys.modules["keyring"] = _fake
db.set_setting("KRAKEN_API_KEY", "")
db.set_setting("KRAKEN_API_SECRET", "")
_k, _s, _src = kr.kraken_credentials()
check("8b.2", (_k, _s, _src) == ("KR-KEYRING-KEY-0001",
                                 "c2VjcmV0MTIzNDU2Nzg5MA==", "keyring")
      and db.get_setting("KRAKEN_API_KEY", "") == "",
      "keyring fallback serves the pair; nothing copied into settings")
check("8b.3", kr.provider_mode() == "live",
      "live mode is tier-agnostic (keyring creds + endpoint)")
r = client.get("/api/admin/kraken-referral/config")
_cfg = r.json()
check("8b.4", _cfg["api_key"]["source"] == "keyring"
      and _cfg["api_key"]["tail"] == "…0001"
      and _cfg["api_secret"]["source"] == "keyring"
      and _cfg["api_secret"]["tail"] == "…MA=="
      and "KR-KEYRING-KEY-0001" not in r.text
      and _cfg["mode"] == "live",
      f"config card masks the keyring tier: {_cfg['api_key']}")
_fake.get_password = lambda s, u: (
    "KR-KEYRING-KEY-0001" if u == "kraken_api_key" else None)
check("8b.5", kr.kraken_credentials() == ("", "", "")
      and kr.provider_mode() == "unconfigured",
      "no mixing: an incomplete keyring pair is not half-adopted")
_fake.get_password = lambda s, u: (
    "KR-KEYRING-KEY-0001" if u == "kraken_api_key" else
    "c2VjcmV0MTIzNDU2Nzg5MA==" if u == "kraken_api_secret" else None)
db.set_setting("KRAKEN_API_KEY", "kcanon99999999")
db.set_setting("KRAKEN_API_SECRET", "c2VjcmV0MTIzNDU2Nzg5MA==")
check("8b.6", kr.kraken_credentials()[2] == "settings",
      "re-provisioned settings win again (tier preference)")
sys.modules["keyring"] = None            # import of keyring raises

sys.modules.pop("keyring")
sys.modules["keyring"] = None
try:
    _res = kr.kraken_credentials()
except Exception as _e:
    _res = ("raised", str(_e))
sys.modules.pop("keyring", None)         # real keyring re-imports later
# settings were restored in 8b.6 — WITHOUT a keyring backend the
# settings tier is exactly what must answer
check("8b.7", _res == ("kcanon99999999", "c2VjcmV0MTIzNDU2Nzg5MA==",
                       "settings"),
      f"no keyring backend degrades to settings-only: {_res}")
check("8b.8", _set_calls == [],
      "the keyring was NEVER written by the server surface")
db.set_setting("KRAKEN_REFERRAL_MODE", "")

print("== 9. THE MAPPING SHEET: real kraken_referral_id column ==")
anon2 = TestClient(srv.app)
check(9.1, anon2.get("/api/admin/kraken-referral/mappings").status_code == 401,
      "mappings admin-gated")
r = client.get("/api/admin/kraken-referral/mappings")
m = r.json()
check(9.2, r.status_code == 200 and m["total"] >= 7 and m["mapped"] == 6,
      f"sheet: {m['mapped']}/{m['total']} mapped (5 seeded + the operator-mapped admin)")
with db.get_db() as conn:
    target_uid = conn.execute(
        "SELECT id FROM users WHERE username='u_none'").fetchone()["id"]
r = client.post("/api/admin/kraken-referral/mappings",
                json={"user_id": target_uid,
                      "kraken_referral_id": " kr-admin-live-01 "})
check(9.3, r.status_code == 200
      and r.json()["kraken_referral_id"] == "KR-ADMIN-LIVE-01"
      and r.json()["action"] == "kraken_referral.mapped",
      f"attach normalizes (trim+upper): {r.json().get('kraken_referral_id')}")
r = client.post("/api/admin/kraken-referral/mappings",
                json={"user_id": target_uid - 1,
                      "kraken_referral_id": "KR-ADMIN-LIVE-01"})
check(9.4, r.status_code == 409,
      f"an id cannot be orphaned onto a second user: {r.status_code}")
r = client.post("/api/admin/kraken-referral/mappings",
                json={"user_id": target_uid, "kraken_referral_id": ""})
check(9.5, r.status_code == 200
      and r.json()["action"] == "kraken_referral.unmapped_manual",
      "clear is its own audited action")
with db.get_db() as conn:
    n_map = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                         "action='kraken_referral.mapped'").fetchone()["c"]
    n_unm = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                         "action='kraken_referral.unmapped_manual'").fetchone()["c"]
check(9.6, n_map >= 1 and n_unm >= 1,
      f"audits: {n_map} mapped, {n_unm} unmapped_manual")
# THE ISOLATION PIN: a legacy internal invite code must never reach
# the provider — a legacy-only user stays honestly unmapped
with db.get_db() as conn:
    conn.execute(
        "INSERT INTO users (username, password_hash, referral_code,"
        " created_at) VALUES ('u_legacy', 'x', 'INT-LEGACY-CODE',"
        " strftime('%s','now'))")
db.set_setting("KRAKEN_REFERRAL_MODE", "simulate")
s9 = kr.run_worker()
with db.get_db() as conn:
    legacy_fetched = conn.execute(
        "SELECT COUNT(*) c FROM kraken_referral_snapshot "
        "WHERE referral_id = 'INT-LEGACY-CODE'").fetchone()["c"]
    legacy_unmapped = conn.execute(
        "SELECT COUNT(*) c FROM audit_log WHERE "
        "action='kraken_referral.unmapped' AND details "
        "LIKE '%u_legacy%'").fetchone()["c"]
check(9.7, legacy_fetched == 0 and legacy_unmapped >= 1,
      f"internal invite code NEVER sent to Kraken (fetched={legacy_fetched}, "
      f"unmapped-audited={legacy_unmapped})")
check(9.8, s9["processed"] >= 6 and s9["unmapped"] >= 2,
      f"worker ran on the real column: {s9['processed']} fetched, "
      f"{s9['unmapped']} unmapped")
db.set_setting("KRAKEN_REFERRAL_MODE", "")
# restore the clean posture the earlier sections expect
db.set_setting("KRAKEN_API_KEY", ""); db.set_setting("KRAKEN_API_SECRET", "")
db.set_setting("KRAKEN_REFERRAL_ENDPOINT", ""); db.set_setting("KRAKEN_REFERRAL_MODE", "")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
