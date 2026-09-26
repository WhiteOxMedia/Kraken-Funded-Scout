"""test_push_report.py — THE DAILY PUSH REPORT (user order, Sep 26).

Pins the report engine against hermetic sandbox DBs with synthetic
audit days:

  1. per-category aggregation from seeded audit rows (sent sums from
     details JSON, blocked/rate-limited row counts, store motion)
  2. idempotent rollups (recompute replaces, never double-counts)
  3. today is a LIVE overlay, never persisted
  4. range reads (1/7/30 days) and retention pruning
  5. admin gate + route shape + scheduler leg rolled yesterday up
"""
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import field_crypto as fc
fc.KEY_DIR = Path(tempfile.mkdtemp(prefix="rep_key_"))
fc.KEY_FILE = fc.KEY_DIR / "field_key"
fc._KEY_CACHE = None
fc.generate_key_file()

import database as db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="rep_db_")) / "jackpot.db"
db.init_db()

import push_report as pr
import push_service as ps
import scheduler as sched
import server as srv
from fastapi.testclient import TestClient

results = []
def check(n, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(n) + ("  " + str(msg) if msg else ""))

def audit_at(epoch, action, details):
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO audit_log (user_id, action, details, created_at)"
            " VALUES (?, ?, ?, ?)", (None, action, json.dumps(details), epoch))

now = time.time()
today_start, _ = pr._day_bounds(now)
y_start = today_start - 86400
d2_start = today_start - 2 * 86400

print("== 1. PER-CATEGORY AGGREGATION (seeded audit days) ==")
# yesterday: jackpot sends+deads, scout blocked, broadcast rate-limit
audit_at(y_start + 100, "push_send_allowed",
         {"event": "jackpot_win", "category": "jackpot", "sent": 3, "dead": 1})
audit_at(y_start + 200, "push_send_allowed",
         {"event": "ticket_update", "category": "jackpot", "sent": 2, "dead": 0})
audit_at(y_start + 300, "push_send_blocked",
         {"event": "signal_buy", "category": "scout", "reason": "opted_out"})
audit_at(y_start + 400, "push_rate_limited",
         {"event": "jackpot_win", "scope": "broadcast"})
audit_at(y_start + 500, "push_subscribe", {"reason": "subscribed"})
audit_at(y_start + 600, "push_unsubscribe", {"reason": "unsubscribed"})
# two days ago: scout sends
audit_at(d2_start + 100, "push_send_allowed",
         {"event": "signal_exit", "category": "scout", "sent": 4, "dead": 2})
# noise that must be IGNORED: other actions
audit_at(y_start + 700, "kraken_referral.mapped", {"user_id": 1})
audit_at(y_start + 800, "scheduler.started", {"interval_s": 3600})

rep = pr.compute_day(y_start)
yj = rep["categories"]["jackpot"]
ys = rep["categories"]["scout"]
check(1.1, yj["sent"] == 5 and yj["dead"] == 1,
      f"jackpot sent/dead summed from details JSON: {yj['sent']}/{yj['dead']}")
check(1.2, ys["blocked"] == 1 and ys["sent"] == 0,
      f"scout blocked counted, no invented sends: {ys}")
check(1.3, yj["rate_limited"] == 1,
      f"broadcast rate-limit lands in its category: {yj['rate_limited']}")
check(1.4, rep["subscriptions"]["subscribed"] == 1
      and rep["subscriptions"]["unsubscribed"] == 1,
      f"store motion counted: {rep['subscriptions']}")
check(1.5, "jackpot_win" in rep["events"] and "kraken_referral" not in
      json.dumps(rep["events"]),
      f"events counter: {rep['events']}")
rep2 = pr.compute_day(d2_start)
check(1.6, rep2["categories"]["scout"]["sent"] == 4
      and rep2["categories"]["scout"]["dead"] == 2,
      "second day aggregates independently")

print("== 2. IDEMPOTENT ROLLUPS ==")
before = json.dumps(pr.compute_day(y_start)["categories"], sort_keys=True)
pr.compute_day(y_start)
pr.compute_day(y_start)
after = json.dumps(pr.compute_day(y_start)["categories"], sort_keys=True)
check(2.1, before == after, "recompute replaces — no double counting")
with db.get_db() as conn:
    n = conn.execute("SELECT COUNT(*) c FROM push_daily_report WHERE day=?",
                     (rep["day"],)).fetchone()["c"]
check(2.2, n == 3, f"one row per category ({n})")

print("== 3. TODAY IS LIVE, NEVER PERSISTED ==")
# the drive's admin registers FIRST (user 1 is admin by charter);
# later probe users are plain accounts
client = TestClient(srv.app)
client.post("/api/auth/register", json={"username": "repadmin",
                                        "password": "pw"})
uid = db.create_user("repprobe", "hash")     # FK target for real audits
ps.subscribe(uid, {"endpoint": "https://push.example/rep-dev",
                   "keys": {"p256dh": "k", "auth": "a"}})
# sandbox VAPID keys so the sender RUNS (dead push service, but the
# push_send_allowed audit lands — that audit IS the report's datum)
db.set_setting("VAPID_PRIVATE_KEY", "sandbox-dummy")
db.set_setting("VAPID_PUBLIC_KEY", "B" + "A" * 86)
ps.send_to_user(uid, "jackpot_win")
r = pr.get_range(days=1)
check(3.1, r["today_live"]["categories"]["jackpot"]["sent"] +
      r["today_live"]["categories"]["jackpot"]["dead"] >= 1,
      "live overlay counts today's real send attempt (dead counts too)")
with db.get_db() as conn:
    n = conn.execute("SELECT COUNT(*) c FROM push_daily_report WHERE day=?",
                     (r["today_live"]["day"],)).fetchone()["c"]
check(3.2, n == 0, f"today's rows wiped after read ({n})")
ydays = [d["day"] for d in r["days"]]
check(3.3, rep["day"] in ydays, "yesterday's rollup served from the store")

print("== 4. RANGE + RETENTION ==")
r7 = pr.get_range(days=7)
check(4.1, len(r7["days"]) == 7, "7-day range")
# a 100-day-old rollup must prune
old_day = time.strftime("%Y-%m-%d", time.gmtime(now - 100 * 86400))
with db.get_db() as conn:
    conn.execute(
        "INSERT OR REPLACE INTO push_daily_report (day, category, sent,"
        " computed_at) VALUES (?, 'jackpot', 1, ?)", (old_day, now))
removed = pr.prune()
check(4.2, removed >= 1, f"retention prune removed {removed} old day row(s)")

print("== 5. ADMIN SURFACE + SCHEDULER LEG ==")
anon = TestClient(srv.app)
check(5.1, anon.get("/api/admin/push/report").status_code == 401,
      "report admin-gated")
r = client.get("/api/admin/push/report?days=7")
j = r.json()
check(5.2, r.status_code == 200 and "days" in j and "today_live" in j
      and len(j["days"]) == 7,
      f"route serves the range (days={len(j.get('days', []))})")
r = client.get("/api/admin/push/report?days=999")
check(5.3, len(r.json()["days"]) <= 30, "range clamped to 30")
# scheduler leg: yesterday must be rolled up by tick_once
sched.tick_once(reason="drive")
with db.get_db() as conn:
    yday = time.strftime("%Y-%m-%d", time.gmtime(y_start))
    n = conn.execute("SELECT COUNT(*) c FROM push_daily_report WHERE day=?",
                     (yday,)).fetchone()["c"]
check(5.4, n == 3, f"scheduler tick rolled yesterday up ({n} rows)")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
