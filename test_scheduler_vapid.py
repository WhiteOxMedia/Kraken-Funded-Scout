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
  8. the daily session prune: expired auth rows swept once per UTC
     day, live sessions spared, the count audited, a same-day re-tick
     a silent no-op.
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

print("== 8. THE DAILY SESSION PRUNE ==")
# expired sessions are pure table growth (get_session refuses them in
# SQL) — the scheduler sweeps them once per UTC day with the retention
# prune's marker pattern, audits real work, and stays silent on a
# no-op day. The world: one live token, two expired (one long past).
with db.get_db() as conn:
    _uid8 = conn.execute(
        "SELECT id FROM users WHERE username = 'schedadmin'").fetchone()["id"]
db.create_session("prune-live-8", _uid8, time.time() + 3600)
db.create_session("prune-dead-8", _uid8, time.time() - 10)
db.create_session("prune-dead-8b", _uid8, time.time() - 999999)
db.set_setting("SESSIONS_PRUNED", "")   # the earlier ticks already pruned
                                         # today — reset to simulate a new day
s15 = sched.tick_once(reason="drive")
check("8.1", s15["sessions_pruned"] == 2,
      f"the tick swept exactly the expired rows: {s15['sessions_pruned']}")
check("8.2", db.get_session("prune-live-8") is not None
      and db.get_session("prune-dead-8") is None
      and db.get_session("prune-dead-8b") is None,
      "live sessions are spared; expired tokens are gone")
with db.get_db() as conn:
    _n8p = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                        "action = 'scheduler.sessions_pruned'").fetchone()["c"]
check("8.3", _n8p == 1,
      f"{_n8p} scheduler.sessions_pruned audit(s) — real work is audited")
s16 = sched.tick_once(reason="drive")
check("8.4", "sessions_pruned" not in s16
      and db.get_setting("SESSIONS_PRUNED", "")
      == time.strftime("%Y-%m-%d", time.gmtime()),
      "the same-day re-tick is a SILENT no-op (no summary entry, "
      "marker stamped) — the leg only speaks on days it sweeps")
with db.get_db() as conn:
    _n8p2 = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                         "action = 'scheduler.sessions_pruned'").fetchone()["c"]
check("8.5", _n8p2 == 1,
      "no extra audit from the no-op day — silence is the contract")

print("== 9. THE WEEKLY ORPHAN-REPORT LEG ==")
check("9.0", sched.status()["orphans"]["enabled"] is True
      and sched.status()["orphans"]["interval_s"] == 7 * 86400,
      "the leg defaults ON at the weekly cadence (an unreported build-up"
      " is how 287 rows pile up unnoticed again)")
db.set_setting(sched.ORPHAN_ENABLED_KEY, "0")
s9d = sched.tick_once(reason="drive")
check("9.1", s9d.get("orphans") == {"skipped": "disabled"},
      f"the operator OFF dial silences the leg: {s9d.get('orphans')}")
db.set_setting(sched.ORPHAN_ENABLED_KEY, "1")
db.set_setting(sched.ORPHAN_LAST_KEY, "{}")          # force due
s9c = sched.tick_once(reason="drive")
check("9.2", s9c.get("orphans") == {"total": 0, "edges": 0, "alert": False},
      f"a clean week reports silently in the summary: {s9c.get('orphans')}")
_n9c = db.get_db() and 0
with db.get_db() as conn:
    _n9c = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                        "action = 'scheduler.orphans_report'").fetchone()["c"]
check("9.3", _n9c == 0,
      "zero orphans -> ZERO audit rows (the prune no-op-day rule)")
s9q = sched.tick_once(reason="drive")
check("9.4", "orphans" not in s9q,
      "an interval that has not elapsed stays quiet - once per week")
# the alert: build real orphans (raw connection = the factory), then
# let the due leg find them
db.set_setting(sched.ORPHAN_LAST_KEY, "{}")          # force due again
import sqlite3 as _sq3
_ouid = db.create_user("sched_orphan_user", "x")
db.create_session("sched-orphan-token", _ouid, 9999999999)
db.add_audit_log(_ouid, "sched_orphan_note", "{}")
_raw = _sq3.connect(str(db.DB_PATH))
_raw.execute("DELETE FROM users WHERE id = ?", (_ouid,))
_raw.commit()
_raw.close()
s9a = sched.tick_once(reason="drive")
check("9.5", s9a.get("orphans") == {"total": 2, "edges": 2,
                                    "alert": True, "push": 0},
      f"the dead user's session + audit row ALERT (push:0 = the "
      f"admin fan-out ran against the dead endpoint): "
      f"{s9a.get('orphans')}")
with db.get_db() as conn:
    _n9a = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                        "action = 'scheduler.orphans_report'").fetchone()["c"]
check("9.6", _n9a == 1,
      f"exactly ONE orphans_report receipt row for the week ({_n9a})")
check("9.7", sched.status()["orphans"]["last"].get("total") == 2,
      "the alert surfaces in the admin status (last.total carries it)")
# the error path: a failed scan must NOT stamp its marker - the
# scan is retried next tick (the credential-leg rule)
db.set_setting(sched.ORPHAN_LAST_KEY, "{}")          # force due
_orig = sched.sweep_lib.orphans
def _boom(path, **kw):
    raise RuntimeError("boom")
sched.sweep_lib.orphans = _boom
try:
    s9e = sched.tick_once(reason="drive")
finally:
    sched.sweep_lib.orphans = _orig
_mk = db.get_setting(sched.ORPHAN_LAST_KEY, "{}")
check("9.8", str(s9e.get("orphans", "")).startswith("error:")
      and "boom" in str(s9e.get("orphans"))
      and (_mk == "{}" or not str(_mk).strip("{}")),
      f"a failed scan reports the error and leaves the marker "
      f"unstamped (retried next tick): marker={_mk}")

print("== 9.9-9.11 THE SCAN-WINDOW ALIGNMENT (Sep 27) ==")
# the Task Scheduler job fires Sunday 06:15 local; the weekly leg now
# anchors its due-decision to the SAME boundary (one window, two
# readers) so a mid-week server restart cannot drift the surfaces
# apart. The constants live in the registrar; both sides read them.
import register_sweep_report_task as _reg9
check("9.9", (_reg9.SCAN_DOW, _reg9.SCAN_HOUR, _reg9.SCAN_MINUTE)
      == (6, 6, 15)
      and "-DaysOfWeek Sunday -At 06:15" in _reg9.__doc__ + open(
          HERE / "register_sweep_report_task.py", encoding="utf-8").read(),
      "the registrar owns the window shape and the PS trigger matches it")
_win = sched._orphan_window_index(time.time())
check("9.10", sched._orphan_due({"ts": _win + 1}) is False
      and sched._orphan_due({"ts": _win - 86400}) is True
      and sched._orphan_due({"ts": _win - 7 * 86400 + 3600}) is True
      and sched._orphan_due({"ts": 0.0}) is True,
      "marker inside this window stays quiet; a SATURDAY marker is due "
      "(the interval rule alone would call it quiet - this is the case "
      "that separates the window rule from the old elapsed-interval), "
      "and last week's or none is due (restart-proof)")
db.set_setting(sched.ORPHAN_INTERVAL_KEY, "900")   # floor lifts to 3600
_fast_fresh = sched._orphan_due({"ts": time.time() - 60})
_fast_stale = sched._orphan_due({"ts": time.time() - 2 * 3600})
db.set_setting(sched.ORPHAN_INTERVAL_KEY, "")
check("9.11", _fast_fresh is False and _fast_stale is True,
      "a sub-weekly operator dial keeps pure interval semantics "
      "(fresh marker quiet, stale due)")

print("== 9b. THE DEAD-MAN: the scheduled task itself must stay alive ==")
# the Task Scheduler job is the second alert surface; a task that
# STOPS running is the one failure no report can reveal. The wrapper
# stamps task_sweep_report_last.txt only after a real receipt; the
# scheduler polices that marker every tick.
import register_sweep_report_task as _reg
check("9b.0", sched.ORPHAN_DEADMAN_MAX_AGE_S == _reg.DEADMAN_MAX_AGE_S
      and sched.ORPHAN_TASK_MARKER == _reg.MARKER,
      "the scheduler and the task registrar agree on the horizon and "
      "the marker file (one contract, two readers)")
_mpath = sched.ORPHAN_TASK_MARKER
_saved = _mpath.read_text(encoding="ascii") if _mpath.exists() else None
try:
    _mpath.write_text(str(time.time()), encoding="ascii")
    s_f = sched.tick_once(reason="drive")
    check("9b.1", "orphans_deadman" not in s_f,
          "a fresh marker (a real run inside the week) is silent")
    with db.get_db() as conn:
        _n_f = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                            "action = 'scheduler.orphans_deadman'").fetchone()["c"]
    _mpath.write_text(str(time.time() - 9 * 86400), encoding="ascii")
    s_s = sched.tick_once(reason="drive")
    check("9b.2", s_s.get("orphans_deadman", {}).get("alert") is True,
          f"a marker older than the 8d horizon ALERTS: {s_s.get('orphans_deadman')}")
    with db.get_db() as conn:
        _n_s = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                            "action = 'scheduler.orphans_deadman'").fetchone()["c"]
    check("9b.3", _n_s == _n_f + 1,
          f"exactly ONE orphans_deadman receipt row for the day "
          f"({_n_f} -> {_n_s})")
    s_s2 = sched.tick_once(reason="drive")
    with db.get_db() as conn:
        _n_s2 = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                             "action = 'scheduler.orphans_deadman'").fetchone()["c"]
    check("9b.4", _n_s2 == _n_s,
          "the same-day re-tick stays silent (the once-per-day marker)")
    _mpath.unlink(missing_ok=True)
    s_m = sched.tick_once(reason="drive")
    check("9b.5", s_m.get("orphans_deadman", {}).get("alert") is True
          and s_m["orphans_deadman"]["marker_age_days"] is None,
          "a MISSING marker alerts too (the task never ran)")
finally:
    if _saved is None:
        _mpath.unlink(missing_ok=True)
    else:
        _mpath.write_text(_saved, encoding="ascii")

print("== 9c. THE WEB-PUSH SURFACE: the alert must reach a phone ==")
# the audit row sits in the DB until an admin opens a dashboard; a
# phone notification is the surface an operator actually sees. The
# alert branch now fans the alert out through
# push_service.send_admin_push - ADMIN subscribers only. Section 2
# already subscribed the drive's ADMIN through the real
# /api/push/subscribe route (dead endpoint), so the fan-out runs the
# real VAPID sender and the member-exclusion needs no mocking.
_ev = ps.EVENTS["orphans_alert"]
check("9c.2", ps.CATEGORY.get("orphans_alert") == "scout"
      and all(k in _ev for k in ("title", "body", "tag", "url", "rate"))
      and _ev["tag"] == "orphans-alert" and _ev["url"] == "/#admin",
      "orphans_alert: scout-family template, tagged, /#admin deep-link")
check("9c.0", "push" in s9a.get("orphans", {}),
      f"the alert branch attempted the push fan-out: {s9a.get('orphans')}")
check("9c.1", s9a["orphans"].get("push") == 0,
      f"the dead endpoint makes the admin leg sent:0, not an error: "
      f"{s9a['orphans'].get('push')}")
_ouid2 = db.create_user("sched_orphan_plain", "x")
_res = ps.subscribe(_ouid2, {
    "endpoint": "https://push.example/sched-member-device",
    "keys": {"p256dh": "k", "auth": "a"}})
assert _res.get("ok"), f"member sub refused: {_res}"
res2 = ps.send_admin_push("orphans_alert")
check("9c.3", res2.get("recipients") == 1 and res2.get("sent") == 0,
      f"a subscribed MEMBER is excluded from the admin fan-out: {res2}")
with db.get_db() as conn:
    _nm = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                       "action='push_send_allowed' AND user_id=?",
                       (_ouid2,)).fetchone()["c"]
check("9c.4", _nm == 0,
      f"no push_send_allowed audit for the member ({_nm}) - their "
      f"endpoint was never attempted")
with db.get_db() as conn:
    _nap = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                        "action='push_admin_push'").fetchone()["c"]
check("9c.5", _nap >= 2,
      f"{_nap} push_admin_push receipt rows - one per admin fan-out")


print("== 9d. THE WEEKLY OPS DIGEST (Sep 27): one line, one push, one receipt ==")
# leg 7: orphans + prune counts + the Kraken weekly verdict, composed
# from the audit LEDGER inside the scan window, pushed to ADMIN
# subscribers, receipted as ONE scheduler.ops_digest row per week.
_win_d = sched._digest_window_index(time.time())

# 9d.0 the contract: the template exists, rides the scout family,
# and the body override is scoped to the digest event in source
_ev_d = ps.EVENTS["ops_digest"]
_ps_src = open(HERE / "pool" / "push_service.py", encoding="utf-8").read()
check("9d.0", _ev_d["tag"] == "ops-digest" and _ev_d["url"] == "/#admin"
      and ps.CATEGORY.get("ops_digest") == "scout"
      and "event == \"ops_digest\" and (extra or {}).get(\"_digest_line\")"
      in _ps_src,
      "ops_digest: scout-family template, /#admin deep-link, and the "
      "body override is source-scoped to the digest event alone")

# 9d.1 the due matrix: the orphan leg's window shape, restart-proof
check("9d.1", sched._digest_due({"ts": _win_d + 1}) is False
      and sched._digest_due({"ts": _win_d - 86400}) is True
      and sched._digest_due({"ts": 0.0}) is True,
      "in-window marker quiet; pre-boundary or none is due "
      "(same window contract as the scan, so the digest composes "
      "from THIS window's fresh orphan scan)")
db.set_setting(sched.DIGEST_INTERVAL_KEY, "900")   # floor lifts to 3600
_dg_fresh = sched._digest_due({"ts": time.time() - 60})
_dg_stale = sched._digest_due({"ts": time.time() - 2 * 3600})
db.set_setting(sched.DIGEST_INTERVAL_KEY, "")
check("9d.1b", _dg_fresh is False and _dg_stale is True,
      "a sub-weekly operator dial keeps pure interval semantics")

# 9d.2 THE LINE IS THE LEDGER: seed one in-window and one pre-window
# KC verdict with DISTINCT markers; only the in-window one may speak.
# The STALE row is inserted LAST (rowid-last) but stamped BEFORE the
# window: a compose that ignores the window prints the stale verdict
# (last-row-wins), so this pin separates the window rule from
# no-filter - the same vacuity trap as the scan-window mutant.
with db.get_db() as conn:
    conn.execute("INSERT INTO audit_log (user_id, action, details, "
                 "created_at) VALUES (NULL, 'scheduler.kc_ran', ?, ?)",
                 ('{"verdict": "drive-in-window-verdict", "tier": "k"}',
                  time.time()))
    conn.execute("INSERT INTO audit_log (user_id, action, details, "
                 "created_at) VALUES (NULL, 'scheduler.kc_ran', ?, ?)",
                 ('{"verdict": "stale-pre-window-marker", "tier": "k"}',
                  _win_d - 100))
    conn.commit()
_line_d, _parts_d = sched._digest_compose()
check("9d.2", "drive-in-window-verdict" in _line_d
      and "stale-pre-window-marker" not in _line_d
      and _line_d.startswith("orphans=") and "pruned=" in _line_d
      and "kc=drive-in-window-verdict (x" in _line_d,
      f"the line composes from the audit ledger INSIDE the window "
      f"(stale verdict excluded): {_line_d}")
# a stale orphan marker must print as unknown, never as an old total
_omark = json.loads(db.get_setting(sched.ORPHAN_LAST_KEY, "{}") or "{}")
db.set_setting(sched.ORPHAN_LAST_KEY, json.dumps(
    {"ts": _win_d - 9000, "total": 999, "edges": 9}))
_l2, _p2 = sched._digest_compose()
check("9d.2b", _p2["orphans"] == "?" and _p2["edges"] == 0,
      f"a pre-window orphan scan is NOT quoted as this week's "
      f"number (unknown beats stale): {_l2}")
db.set_setting(sched.ORPHAN_LAST_KEY, json.dumps(_omark))

# 9d.3-9d.5 the tick fires the digest once per window, clean weeks
# included, and stamps marker + receipt
_n_d0 = 0
with db.get_db() as conn:
    _n_d0 = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                         "action = 'scheduler.ops_digest'").fetchone()["c"]
_sent_cap = []
_real_fan = sched.push_svc.send_admin_push
def _cap_fan(event, extra=None):
    _sent_cap.append((event, dict(extra or {})))
    return _real_fan(event, extra=extra)
sched.push_svc.send_admin_push = _cap_fan
db.set_setting(sched.DIGEST_LAST_KEY, "{}")        # force due
_s_d = sched.tick_once(reason="drive")
sched.push_svc.send_admin_push = _real_fan
with db.get_db() as conn:
    _n_d1 = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                         "action = 'scheduler.ops_digest'").fetchone()["c"]
_row = None
with db.get_db() as conn:
    _row = conn.execute("SELECT details FROM audit_log WHERE "
                        "action = 'scheduler.ops_digest' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
check("9d.3", isinstance(_s_d.get("ops_digest"), dict)
      and _n_d1 == _n_d0 + 1
      and _sent_cap and _sent_cap[-1][0] == "ops_digest"
      and "_digest_line" in _sent_cap[-1][1]
      and _sent_cap[-1][1]["_digest_line"] == _s_d["ops_digest"]["line"],
      f"the tick fired the digest: summary carries the line, ONE "
      f"receipt row ({_n_d0} -> {_n_d1}), and the dynamic line rode "
      f"the scoped override to the real sender")
check("9d.4", _row is not None
      and _s_d["ops_digest"]["line"] in (_row["details"] or "")
      and json.loads(db.get_setting(sched.DIGEST_LAST_KEY, "{}")
                     ).get("ts", 0) > 0
      and sched.status()["digest"]["last"].get("line")
      == _s_d["ops_digest"]["line"],
      "the receipt row carries the line; the marker is stamped and "
      "surfaces in the admin status")
_s_d2 = sched.tick_once(reason="drive")
with db.get_db() as conn:
    _n_d2 = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                         "action = 'scheduler.ops_digest'").fetchone()["c"]
check("9d.5", _n_d2 == _n_d1,
      f"the same-window re-tick stays silent ({_n_d1} -> {_n_d2})")

# 9d.6 the retry rule: a compose failure leaves the marker unstamped
_real_comp = sched._digest_compose
def _boom_comp():
    raise RuntimeError("compose boom")
sched._digest_compose = _boom_comp
db.set_setting(sched.DIGEST_LAST_KEY, "{}")
_s_de = sched.tick_once(reason="drive")
sched._digest_compose = _real_comp
_check_de = json.loads(db.get_setting(sched.DIGEST_LAST_KEY, "{}"))
check("9d.6", isinstance(_s_de.get("ops_digest"), str)
      and _s_de["ops_digest"].startswith("error:")
      and not _check_de.get("ts"),
      "a failed compose surfaces as an error summary and does NOT "
      "stamp the marker - the digest is retried next tick")
_s_dr = sched.tick_once(reason="drive")
check("9d.7", isinstance(_s_dr.get("ops_digest"), dict),
      "the next tick retries and fires the digest")

# 9d.8 the operator dial: disabled means skipped, no receipt
db.set_setting(sched.DIGEST_ENABLED_KEY, "0")
db.set_setting(sched.DIGEST_LAST_KEY, "{}")
with db.get_db() as conn:
    _n_d3 = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                         "action = 'scheduler.ops_digest'").fetchone()["c"]
_s_dd = sched.tick_once(reason="drive")
with db.get_db() as conn:
    _n_d4 = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE "
                         "action = 'scheduler.ops_digest'").fetchone()["c"]
db.set_setting(sched.DIGEST_ENABLED_KEY, "")
check("9d.8", _s_dd.get("ops_digest") == {"skipped": "disabled"}
      and _n_d4 == _n_d3,
      "the OFF dial skips the leg loudly in the summary and writes "
      "no receipt")

# 9d.9 the override cannot leak: _digest_line on a NON-digest event
# must never change that event's body (captured through a fake
# pywebpush so no network sees the probe; rate gate patched hermetic)
_real_rate = ps._rate_ok
ps._rate_ok = lambda *a, **k: True
class _FakePP(_types.SimpleNamespace):
    pass
_captured = []
def _cap_webpush(**kw):
    _captured.append(kw)
    class _R: status_code = 201
    return _R()
_fake_mod = _FakePP(webpush=_cap_webpush, WebPushException=Exception)
_real_mod = sys.modules.get("pywebpush")
sys.modules["pywebpush"] = _fake_mod
try:
    ps.send_to_user(admin_id, "ops_digest",
                    extra={"_digest_line": "DIGESTLINE-OK-123"})
    ps.send_to_user(admin_id, "push_test",
                    extra={"_digest_line": "EVILLINE-LEAK-999"})
finally:
    sys.modules.pop("pywebpush", None)
    if _real_mod is not None:
        sys.modules["pywebpush"] = _real_mod
    ps._rate_ok = _real_rate
_bodies = []
for _kw in _captured:
    try:
        _bodies.append(_kw["data"].decode("utf-8")
                       if isinstance(_kw["data"], bytes) else str(_kw["data"]))
    except Exception:
        _bodies.append(str(_kw))
_ok_hit = any("DIGESTLINE-OK-123" in b for b in _bodies)
_evil_hit = any("EVILLINE-LEAK-999" in b for b in _bodies)
check("9d.9", len(_captured) >= 2 and _ok_hit and not _evil_hit,
      f"the override carried the digest line on ops_digest but the "
      f"same extra on push_test changed nothing ({len(_captured)} "
      f"captures)")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
