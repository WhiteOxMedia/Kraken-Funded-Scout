"""test_scheduler_vapid.py — THE RECURRING LEG + VAPID PROVISIONING (Sep 26).

Proves the new pieces end-to-end on a hermetic sandbox DB:
  1. VAPID ensure/rotate/status (real py_vapid keys, no clobber)
  2. push_test through the REAL sender with generated keys
  3. scheduler tick: worker leg (interval + unconfigured gates)
  4. scheduler tick: notice leg against the operator expiry calendar
  5. admin routes + auth gates + config surface
  6. thread lifecycle (started last so its audits never pollute counts)
  7. the weekly credential self-test leg: default-ON cadence on the
     status read, quiet on a fresh marker, disabled = quiet, due-now
     dials the SHARED selftest (unconfigured is free), the 30s KC
     throttle gates the scheduled call too, and the skip is audited
     once per gap — never per tick.
The keyring is stubbed to NOTHING for the whole run: the box may hold
a real operator pair, and no drive may ever dial Kraken. The weekly
leg stays OFF until section 7 drives it explicitly, so the thread-
lifecycle tick neither dials nor stamps a verdict.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

_tmp = Path(tempfile.mkdtemp(prefix="sched_vapid_"))
import database as db
db.DB_PATH = _tmp / "jackpot.db"
db.init_db()

import push_service as ps
import kraken_referral as kr
import scheduler as sched
import server as srv
from fastapi.testclient import TestClient

results = []
def check(n, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(n) + ("  " + str(msg) if msg else ""))

client = TestClient(srv.app)
import types as _types
sys.modules["keyring"] = _types.SimpleNamespace(   # hermetic: no pair
    get_password=lambda s, u: None, set_password=lambda s, u, v: None)
db.set_setting(sched.KC_ENABLED_KEY, "0")  # weekly leg OFF until section 7
r = client.post("/api/auth/register", json={"username": "schedadmin", "password": "pw"})
admin_id = 1

print("== 1. VAPID ENSURE / ROTATE / STATUS ==")
st = ps.vapid_status()
check(1.1, st["provisioned"] is False, "clean box starts unprovisioned")
r1 = ps.vapid_ensure()
check(1.2, r1["ok"] and r1["created"] is True and r1["source"] == "generated"
      and len(ps.public_key()) == 87,
      f"ensure generates: pub len {len(ps.public_key())}")
import base64 as _b64
point = _b64.urlsafe_b64decode(ps.public_key() + "==")
check("1.2b", len(point) == 65 and point[0] == 0x04,
      "public key decodes to the uncompressed P-256 point")
r2 = ps.vapid_ensure()
check(1.3, r2["ok"] and r2["created"] is False, "ensure is idempotent")
r3 = ps.vapid_ensure(generate=True)
check(1.4, not r3["ok"] and "rotate" in r3["reason"],
      "generate refuses to clobber existing keys")
first_pub = ps.public_key()
r4 = ps.vapid_rotate()
check(1.5, r4["ok"] and r4["source"] == "rotated"
      and ps.public_key() != first_pub
      and db.get_setting("VAPID_PREVIOUS", "") != "",
      f"rotate re-keys + archives the old pair (subs to re-sub: {r4['subscriptions_needing_resubscribe']})")
st = ps.vapid_status()
check(1.6, first_pub not in json.dumps(st) and st["public_key_tail"].startswith("…"),
      "status exposes tails only, never key material")

print("== 2. push_test THROUGH THE REAL SENDER ==")
check(2.1, "push_test" in ps.EVENTS and ps.CATEGORY.get("push_test") == "jackpot",
      "push_test event exists, jackpot family")
client.post("/api/push/subscribe", json={"subscription": {
    "endpoint": "https://push.example/sched-device",
    "keys": {"p256dh": "k", "auth": "a"}}})
res = ps.send_to_user(admin_id, "push_test")
check(2.2, res["sent"] == 0 and res["reason"] == "no deliverable endpoint",
      f"generated keys make the sender RUN (dead push service, not 'VAPID not provisioned'): {res}")
with db.get_db() as conn:
    n = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                     "action='push_send_allowed' AND user_id=1").fetchone()["c"]
check(2.3, n >= 1, f"{n} push_send_allowed audit(s) for the real send")

print("== 3. SCHEDULER TICK: THE WORKER LEG ==")
s = sched.tick_once(reason="drive")
check(3.1, s["worker"] == {"skipped": "unconfigured"}, f"unconfigured skip: {s['worker']}")
s2 = sched.tick_once(reason="drive")
with db.get_db() as conn:
    n_sk = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                        "action='scheduler.worker_skipped'").fetchone()["c"]
check(3.2, n_sk == 1, f"skip audited exactly ONCE per gap (not per tick): {n_sk}")
st = sched.status()
check(3.3, st["heartbeat_age_s"] is not None and st["heartbeat_age_s"] < 10,
      f"heartbeat stamped {st['heartbeat_age_s']}s ago")
db.set_setting(sched.LAST_WORKER_KEY, json.dumps({"ts": time.time(), "reason": "ran"}))
s3 = sched.tick_once(reason="drive")
check(3.4, s3["worker"] == {"skipped": "interval_not_elapsed"},
      "interval gate holds a fresh stamp")
db.set_setting(sched.LAST_WORKER_KEY, json.dumps({"ts": time.time() - 7200, "reason": "ran"}))
db.set_setting("KRAKEN_REFERRAL_MODE", "simulate")   # stale creds would trap live; simulate must win
s4 = sched.tick_once(reason="drive")
check(3.5, isinstance(s4["worker"], dict) and s4["worker"].get("mode") == "simulate"
      and s4["worker"].get("source") == "kraken_referral_sim_v1",
      f"elapsed interval + simulate provider runs the worker: mode {s4['worker'].get('mode')}")
with db.get_db() as conn:
    n_ran = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                         "action='scheduler.worker_ran'").fetchone()["c"]
check(3.6, n_ran == 1, f"{n_ran} scheduler.worker_ran audit")

print("== 4. SCHEDULER TICK: THE NOTICE LEG ==")
db.set_setting("KRAKEN_REFERRAL_MODE", "")          # back to unconfigured for clean notice audits
db.set_setting(sched.LAST_NOTICE_KEY, "")
far = time.time() + 30 * 24 * 3600
db.set_setting(sched.WINDOW_KEY, json.dumps({"expires_at": far}))
s5 = sched.tick_once(reason="drive")
check(4.1, s5["notice"] is None and s5["worker"].get("skipped"),
      "window far away -> notice stays quiet")
near = time.time() + 24 * 3600                      # inside the final 48h
db.set_setting(sched.WINDOW_KEY, json.dumps({"expires_at": near}))
s6 = sched.tick_once(reason="drive")
check(4.2, s6["notice"] and s6["notice"].get("sent") == 0
      and s6["notice"].get("reason") is None,
      f"notice fired inside 48h; sender ran (allowed-audits in 4.5), "
      f"broadcast carries no reason field: {s6['notice']}")
s7 = sched.tick_once(reason="drive")
check(4.3, s7["notice"] == {"skipped": "already_notified_for_this_expiry"},
      "ONE notice per expiry — later ticks inside the window stay quiet")
with db.get_db() as conn:
    n_nf = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                        "action='scheduler.notice_fired'").fetchone()["c"]
check(4.4, n_nf == 1, f"{n_nf} scheduler.notice_fired audit(s)")
with db.get_db() as conn:
    n_bl = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                        "action='push_send_allowed' AND details LIKE '%referral_window%'"
                        ).fetchone()["c"]
check(4.5, n_bl >= 1, f"the notice rode the referral_window event ({n_bl} allowed rows)")
db.set_setting(sched.WINDOW_KEY, json.dumps({"expires_at": time.time() - 100}))
s8 = sched.tick_once(reason="drive")
db.set_setting(sched.LAST_NOTICE_KEY, "")
s9 = sched.tick_once(reason="drive")
check(4.6, s9["notice"] is None or "skipped" in (s9["notice"] or {}),
      "expired windows go quiet — no post-expiry notice")
ws = sched.window_status()
check(4.7, ws["configured"] and ws["expired"] is True and "expires_iso" in ws,
      f"window status honest about expiry: {ws.get('expires_iso')}")
db.set_setting(sched.WINDOW_KEY, "")

print("== 5. ADMIN ROUTES + GATES ==")
anon = TestClient(srv.app)
check(5.1, anon.get("/api/admin/scheduler/status").status_code == 401
      and anon.get("/api/admin/push/vapid").status_code == 401
      and anon.post("/api/admin/push/vapid", json={}).status_code == 401,
      "all three surfaces admin-gated")
r = client.get("/api/admin/scheduler/status")
st = r.json()
check(5.2, r.status_code == 200 and "enabled" in st and "window" in st
      and "vapid" in st and "heartbeat_age_s" in st,
      "status route carries the full posture")
r = client.get("/api/admin/push/vapid")
check(5.3, r.status_code == 200 and r.json()["provisioned"] is True
      and "VAPID_PRIVATE" not in r.text and "PRIVATE KEY" not in r.text,
      "vapid read: provisioned, key material never leaves the server")
r = client.post("/api/admin/push/vapid", json={"action": "bogus"})
check(5.4, r.status_code == 400, "unknown vapid action -> 400")
r = client.post("/api/admin/push/vapid", json={"action": "test"})
check(5.5, r.status_code == 200 and r.json().get("sent") == 0,
      "admin test action runs the real sender for the caller's own devices")
r = client.post("/api/admin/scheduler/tick", json={})
check(5.6, r.status_code == 200 and "worker" in r.json() and "notice" in r.json(),
      "operator tick route returns the cycle summary")
r = client.get("/api/admin/kraken-referral/config")
cfg = r.json()
check(5.7, "scheduler" in cfg and cfg["scheduler"]["enabled"] is False
      and cfg["scheduler"]["window"]["configured"] is False,
      "config read carries the scheduler + window posture")
r = client.post("/api/admin/kraken-referral/config", json={
    "scheduler_enabled": True, "interval_s": 900,
    "expires_at": time.time() + 10 * 24 * 3600})
j = r.json()
check(5.8, r.status_code == 200 and "SCHEDULER_ENABLED=1" in j["updated"]
      and "SCHEDULER_INTERVAL_S=900" in j["updated"]
      and any(u.startswith("KRAKEN_REFERRAL_WINDOW=") for u in j["updated"])
      and "scheduler thread started" in j["updated"],
      f"one save provisions the whole recurring leg: {len(j['updated'])} updates")
r = client.post("/api/admin/kraken-referral/config", json={"interval_s": 10})
check(5.9, r.status_code == 400, "sub-floor interval rejected")
r = client.post("/api/admin/kraken-referral/config", json={"expires_at": -5})
check(5.10, r.status_code == 400, "past/negative expiry rejected")
r = client.post("/api/admin/kraken-referral/config", json={"clear_window": True})
check(5.11, "KRAKEN_REFERRAL_WINDOW (cleared)" in r.json()["updated"],
      "expiry clearable")

print("== 6. THREAD LIFECYCLE ==")
check(6.1, sched.status()["thread_alive"] is True,
      "the admin enable started the daemon thread")
check(6.2, sched.start() is False, "start is idempotent")
st = sched.status()
check(6.3, st["enabled"] is True and st["interval_s"] == 900,
      f"status reflects the live config (interval {st['interval_s']}s)")
sched.stop()
check(6.4, sched.status()["thread_alive"] is False, "stop joins the thread")
db.set_setting(sched.ENABLED_KEY, "0")
check(6.5, sched.start() is False, "disabled -> start refuses")

print("== 7. THE WEEKLY CREDENTIAL SELF-TEST LEG ==")
db.set_setting(sched.KC_ENABLED_KEY, "1")   # the thread window closed in 6.4
st7 = sched.status()
check(7.1, st7["kc"]["enabled"] is True
      and st7["kc"]["interval_s"] == 7 * 86400 and st7["kc"]["last"] == {},
      f"weekly leg defaults ON at the weekly cadence: {st7['kc']}")
# a fresh marker defers the probe: the leg is quiet, no dial, no audit
db.set_setting(sched.KC_LAST_KEY, json.dumps(
    {"ts": time.time(), "verdict": "unconfigured"}))
s10 = sched.tick_once(reason="drive")
check(7.2, "kc" not in s10
      and db.get_setting(sched.KC_LAST_KEY, "").startswith('{"ts":'),
      f"fresh marker -> quiet (no dial, no stamp churn): {s10.get('kc')}")
# disabled = the explicit skip summary, and no throttle credit spent
db.set_setting(sched.KC_ENABLED_KEY, "0")
s11 = sched.tick_once(reason="drive")
check(7.3, s11["kc"] == {"skipped": "disabled"}
      and kr.KC_STATE["last"] == 0.0,
      f"disabled leg stays quiet and free: {s11['kc']}")
# due NOW with nothing configured: the SHARED selftest answers
# 'unconfigured' (free — unconfigured burns no rate credit), the
# verdict is stamped, and the skip is audited once per gap
db.set_setting(sched.KC_ENABLED_KEY, "1")
db.set_setting(sched.KC_LAST_KEY, json.dumps(
    {"ts": 0.0, "verdict": "unconfigured"}))     # marker says: overdue
s12 = sched.tick_once(reason="drive")
check(7.4, s12["kc"] == {"verdict": "unconfigured", "tier": ""},
      f"due + unconfigured -> the shared selftest's free verdict: {s12['kc']}")
s13 = sched.tick_once(reason="drive")
check(7.5, "kc" not in s13, "the stamped verdict defers the next probe")
with db.get_db() as conn:
    n_kcs = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                         "action='scheduler.kc_skipped'").fetchone()["c"]
check(7.6, n_kcs == 1, f"{n_kcs} scheduler.kc_skipped audit(s) — once per gap")
# THE SHARED THROTTLE: a due scheduled leg meets the operator button's
# fresh 30s stamp and must yield, never bypass or double-dial
_real_fetch_guard = kr.KC_STATE["last"]
db.set_setting(sched.KC_LAST_KEY, json.dumps(
    {"ts": 0.0, "verdict": "valid"}))            # marker says: overdue
kr.KC_STATE["last"] = time.time()                # the button just checked
s14 = sched.tick_once(reason="drive")
check(7.7, s14["kc"].get("verdict") == "throttled"
      and s14["kc"].get("retry_after_s", 0) > 0
      and s14["kc"].get("tier") == "",
      f"the 30s KC throttle gates the scheduled call too: {s14['kc']}")
kr.KC_STATE["last"] = 0.0
db.set_setting(sched.KC_LAST_KEY, json.dumps(
    {"ts": time.time(), "verdict": "unconfigured"}))  # tail hygiene

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
