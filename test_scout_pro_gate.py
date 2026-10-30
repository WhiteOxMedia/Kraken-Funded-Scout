"""test_scout_pro_gate.py — THE PAID SIGNALS GATE (Oct 24).

The memo sells Scout Pro as the thing that gets you the signals, but
the signals were not gated anywhere a user cannot edit: the browser
checked a localStorage flag nobody ever wrote, and both push endpoints
sent to whoever was subscribed. This drive pins the two halves of the
fix over the REAL routes and the REAL push service:

  1. THE SERVER DECIDES: one entitlement predicate (db.has_scout_pro),
     and the sender refuses a signal to a locked account — logged, not
     silently dropped — while leaving the free events alone.
  2. THE BROWSER IS TOLD: /api/scout/pro answers the authenticated
     verdict and scout_pro_bridge.js caches it into the exact key every
     alert path reads.

House conventions: check(n, cond, why), RESULT line, sys.exit.
Like the other emoji-bearing drives, stdout is reconfigured to UTF-8
before any Unicode fact can land on a cp1252 console (the 3.3 message
names the 📈 signal — a bare cp1252 printer used to kill the drive
mid-check instead of reporting it).
"""
import json
import re
import sys
import tempfile
import time
import types
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import database as db                                        # noqa: E402

_tmp = tempfile.mkdtemp(prefix="scout_pro_gate_")
db.DB_PATH = Path(_tmp) / "jackpot.db"
db.init_db()

# ── the wallet RPC stub: BLOCKED, this drive touches no node ─────────
_wallet = types.ModuleType("monero_wallet")
_wallet.rpc_available = lambda: False
_wallet.create_subaddress = lambda label="": (None, None)
_wallet.get_transfers = lambda min_height=0, subaddr_indices=None: []
_wallet.get_transfer_by_txid = lambda txid: None
sys.modules["monero_wallet"] = _wallet
sys.modules["pool.monero_wallet"] = _wallet

import server                                                # noqa: E402
import push_service as push                                  # noqa: E402
from fastapi.testclient import TestClient                    # noqa: E402

# server.py and pool/*.py hold two `database` module objects; point both
# at the sandbox (in production they resolve to the same file).
import pool.database as _pkgdb                               # noqa: E402
_pkgdb.DB_PATH = db.DB_PATH
_pkgdb.init_db()

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}  {why}".rstrip())


def _register(client, username):
    r = client.post("/api/auth/register",
                    json={"username": username, "password": "pw-" + username})
    assert r.status_code == 200, r.text
    return db.get_user_by_username(username)


def _sub(client, endpoint):
    return client.post("/api/push/subscribe", json={"subscription": {
        "endpoint": endpoint,
        "keys": {"p256dh": "a", "auth": "b"}}})


print("== 0. TWO ACCOUNTS: ONE PAID, ONE LOCKED ==")

paid_client = TestClient(server.app)
locked_client = TestClient(server.app)
PAID = _register(paid_client, "pro")
LOCKED = _register(locked_client, "locked")
check(0.1, _sub(paid_client, "https://push.example/pro").status_code == 200,
      "the paid account has a real subscription")
check(0.2, _sub(locked_client, "https://push.example/locked").status_code == 200,
      "the locked account has one too — subscribing was never the gate")
check(0.3, db.has_scout_pro(LOCKED["id"]) is False,
      "a fresh account is NOT entitled")
db.unlock_scout_pro(PAID["id"], "donation", grant_days=30)
check(0.4, db.has_scout_pro(PAID["id"]) is True,
      "no entitlement means no account flag: a donation grant entitles")
check(0.5, db.has_scout_pro(None) is False,
      "an absent account is never entitled")

# the window has to still be open, not merely once granted
_tmp_paid = db.create_user("expired", "hash")
db.unlock_scout_pro(_tmp_paid, "donation", grant_days=1)
with db.get_db() as conn:
    conn.execute("UPDATE users SET scout_pro_until = ? WHERE id = ?",
                 (time.time() - 60, _tmp_paid))
check(0.6, db.has_scout_pro(_tmp_paid) is False,
      "an expired checkout window is not entitlement")
_prog = db.create_user("lifer", "hash")
db.unlock_scout_pro(_prog, "progression")
check(0.7, db.has_scout_pro(_prog) is True,
      "the 100-day lifetime grant is entitlement with no expiry")

print("== 1. THE SENDER REFUSES A SIGNAL TO A LOCKED ACCOUNT ==")

res = push.send_to_user(LOCKED["id"], "signal_buy")
check(1.1, res.get("sent") == 0 and "scout pro" in (res.get("reason") or ""),
      f"a locked subscriber gets no BUY signal: {res.get('reason')!r}")
res_exit = push.send_to_user(LOCKED["id"], "signal_exit")
check(1.2, res_exit.get("sent") == 0
      and "scout pro" in (res_exit.get("reason") or ""),
      f"nor an exit signal: {res_exit.get('reason')!r}")
res_paid = push.send_to_user(PAID["id"], "signal_buy")
check(1.3, "scout pro" not in (res_paid.get("reason") or ""),
      f"the paid account passes the gate: {res_paid.get('reason')!r}")
res_free = push.send_to_user(LOCKED["id"], "jackpot_win")
check(1.4, "scout pro" not in (res_free.get("reason") or ""),
      "free-product events are NOT gated (a jackpot win is not paid)")

with db.get_db() as conn:
    row = conn.execute(
        "SELECT details FROM audit_log WHERE user_id = ? AND action = "
        "'push_send_blocked' ORDER BY id DESC LIMIT 1",
        (LOCKED["id"],)).fetchone()
_blocked = json.loads(row["details"]) if row else {}
check(1.5, _blocked.get("reason") == "scout_pro_required"
      and _blocked.get("event") in ("signal_buy", "signal_exit"),
      "the refusal is a logged decision, not silence")

print("== 2. THE BROADCAST FANS OUT TO PRO ONLY ==")

bc = push.send_to_subscribers("signal_buy")
check(2.1, bc.get("blocked") == 1,
      f"one subscribed-but-locked account is counted out: {bc}")
check(2.2, bc.get("eligible") == 1,
      f"one subscribed-and-paid account is the audience: {bc}")
with db.get_db() as conn:
    row = conn.execute(
        "SELECT details FROM audit_log WHERE action = 'push_broadcast' "
        "ORDER BY id DESC LIMIT 1").fetchone()
_bc = json.loads(row["details"]) if row else {}
check(2.3, _bc.get("scout_pro_blocked") == 1,
      "the broadcast row records how many were gated")

print("== 3. THE FEED IS THE PAID PRODUCT'S HISTORY ==")

r_locked = locked_client.get("/api/push/signals")
j_locked = r_locked.json() if r_locked.status_code == 200 else {}
check(3.1, r_locked.status_code == 200
      and j_locked.get("signals") == []
      and j_locked.get("scout_pro_required") is True,
      f"a locked account gets an empty feed and the reason: {j_locked}")

_paid_before = len(paid_client.get("/api/push/signals").json().get("signals") or [])
db.add_audit_log(None, "push_broadcast",
                 json.dumps({"event": "signal_buy", "delivered": 1,
                             "via": "seed"}))
j_locked2 = locked_client.get("/api/push/signals").json()
check(3.2, j_locked2.get("signals") == [],
      "a REAL signal row is still invisible to the locked account")
j_paid = paid_client.get("/api/push/signals").json()
check(3.3, len(j_paid.get("signals") or []) == _paid_before + 1
      and j_paid["signals"][0]["title"].startswith("📈"),
      f"the paid account sees the same row: {j_paid.get('signals')}")
check(3.4, TestClient(server.app).get("/api/push/signals").status_code == 401,
      "unauthenticated stays 401")

print("== 4. THE BROWSER IS TOLD, BY THE SERVER ==")

p_locked = locked_client.get("/api/scout/pro")
check(4.1, p_locked.status_code == 200
      and p_locked.json().get("active") is False,
      f"/api/scout/pro answers the authenticated verdict: {p_locked.json()}")
p_paid = paid_client.get("/api/scout/pro").json()
check(4.2, p_paid.get("active") is True and p_paid.get("until", 0) > time.time(),
      f"the paid account reads active with its window: {p_paid}")
check(4.3, TestClient(server.app).get("/api/scout/pro").status_code == 401,
      "the entitlement read is authenticated")

bridge = HERE / "pool" / "static" / "scout_pro_bridge.js"
check(4.4, bridge.is_file(),
      "the bridge that writes the flag exists (the audit's missing writer)")
_bsrc = bridge.read_text(encoding="utf-8") if bridge.is_file() else ""
_psrc = (HERE / "pool" / "static" / "push_client.js").read_text(encoding="utf-8")
check(4.5, 'KEY = "scout_pro"' in _bsrc
      and 'localStorage.getItem("scout_pro")' in _psrc,
      "the bridge writes the exact key push_client reads")

_tpl = (HERE / "pool" / "templates" / "index.html").read_text(encoding="utf-8")
# Oct 29: the cache-bust version moved to v=2 with the push rewire, so
# pin the PROPERTY the check was for — the bridge tag ships WITH a
# cache-busting query — not one specific version number (a versionless
# tag would serve stale bridges after every bump).
_tpl_bridge = re.search(r'<script src="/static/scout_pro_bridge\.js\?v=\d+"></script>', _tpl)
check(4.6, _tpl_bridge is not None,
      "the page ships the bridge WITH a cache-bust version (plain or "
      "bumped tag order)")
check(4.7, _tpl.index("scout_pro_bridge.js") < _tpl.index("push_client.js"),
      "the bridge loads BEFORE the alert router that reads its cache")

from make_bundle import REQUIRED_STATIC_JS                   # noqa: E402
check(4.8, "scout_pro_bridge.js" in REQUIRED_STATIC_JS,
      "the deploy artifact is required to carry the bridge")

check(4.9, "scout_pro_required" in
      (HERE / "pool" / "static" / "wf_dashboard.js").read_text(encoding="utf-8"),
      "the signals panel names the lock instead of faking an empty feed")
check("4.10", 'addEventListener("xmrjackpot:auth", refresh)' in _bsrc,
      "the bridge re-reads on every login/logout — no stale paywall")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
