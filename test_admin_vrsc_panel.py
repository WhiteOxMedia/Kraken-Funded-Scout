"""test_admin_vrsc_panel.py — THE VRSC CARD ANSWERS FOR ITS KNOBS (Oct 28).

The admin panel's Verus card exposes three levers over the same-coin
payout machinery. Every write must land where the READERS actually
read and be told "yes" only when it would take effect:

  1. GATE: the card's read model is admin-only (anonymous gets 401,
     a signed-in regular gets 403)
  2. PIN: bad pin values are refused NAMED at the desk (not numeric,
     not positive, absurdly high); a good pin lands in the OVERRiDE
     tier the reader serves, with the memo reset so it takes effect
     immediately; clearing reverts to the tier below
  3. TERRITORIES: normalized to ISO alpha-2 uppercase; garbage codes
     are refused NAMED because a malformed list would silently narrow
     who receives the Verus miner; a saved list is the tier in force
  4. EPISODE LOG: the card's log carries the vrsc_rate.* rows and
     nothing else — the reader's own transitions, not the operator's
  5. EPISODE STATE: the state keys (served / fails / candidate /
     pending) are surfaced, not hidden inside the log
  6. HERMETIC: one sandbox DB, live fetching off, no fetch threads

House conventions: check(n, cond, why), RESULT line, sys.exit.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

# THE FETCH IS OFF: the drive is offline. Set BEFORE importing server
# so the reader's kill switch is honoured (no fetch thread, ever).
os.environ["VRSC_PRICE_LIVE"] = "0"

import database as db                                        # noqa: E402
import vrsc_price                                            # noqa: E402

_tmp = tempfile.mkdtemp(prefix="admin_vrsc_")
db.DB_PATH = Path(_tmp) / "jackpot.db"
db.init_db()

from fastapi.testclient import TestClient                     # noqa: E402
import server as srv                                          # noqa: E402

CLIENT = TestClient(srv.app)
passed = failed = 0


def check(n, cond, why):
    global passed, failed
    tag = "PASS" if cond else "FAIL"
    print(f"{tag}  {n}  {why}")
    if cond:
        passed += 1
    else:
        failed += 1


def vrsc_get():
    r = CLIENT.get("/api/admin/vrsc")
    return r.status_code, (r.json() if r.status_code == 200 else {})


def audit_rows(action=None):
    with db.get_db() as conn:
        if action:
            rows = conn.execute(
                "SELECT action, details FROM audit_log WHERE action = ?",
                (action,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT action, details FROM audit_log").fetchall()
    return [dict(r) for r in rows]


print("== 1. GATE ==")
check(1.1, CLIENT.get("/api/admin/vrsc").status_code == 401,
      "anonymous cannot read the Verus card")
_uid = db.create_user("vrsc_admin", "x", email="vrsc@example.test",
                      is_admin=True)
_tok = "admin-vrsc-token"
db.create_session(_tok, _uid, int(time.time() + 3600))
CLIENT.cookies.set("session", _tok)
_status, _body = vrsc_get()
check(1.2, _status == 200 and {"pin", "rate", "live_enabled",
                               "territories", "state",
                               "episodes"} == set(_body),
      f"the admin reads the read model from /api/admin/vrsc: {_status}")
_reg = db.create_user("plain_vrsc", "x", email="plain@example.test")
_tok2 = "plain-vrsc-token"
db.create_session(_tok2, _reg, int(time.time() + 3600))
CLIENT.cookies.set("session", _tok2)
check(1.3, CLIENT.get("/api/admin/vrsc").status_code == 403,
      "a signed-in NON-admin gets 403, not 401 (authed, unauthorized)")
CLIENT.cookies.set("session", _tok)

print("\n== 2. THE PIN: refused named, pinned effective, cleared clean ==")
for bad in ("banana", "-0.5", "0", "9999"):
    r = CLIENT.post("/api/admin/vrsc/rate-pin", json={"value": bad})
    check(2.1, r.status_code == 400 and "detail" in r.json(),
          f"'{bad}' is refused named: {r.json().get('detail')}")
r = CLIENT.post("/api/admin/vrsc/rate-pin", json={"value": "0.28"})
check(2.2, r.status_code == 200 and r.json().get("pin") == "0.28",
      f"a plausible pin is accepted: {r.json().get('status')}")
_status, _body = vrsc_get()
check(2.3, _body["pin"] == "0.28"
      and _body["rate"]["source"] == "override"
      and abs(_body["rate"]["rate"] - 0.28) < 1e-9,
      f"the reader now SERVES the pin with provenance: {_body['rate']}")
check(2.4, any(a["action"] == "vrsc_rate_pin" for a in audit_rows()),
      "the pin write left an audit row in the operator's name")
r = CLIENT.post("/api/admin/vrsc/rate-pin", json={"value": ""})
check(2.5, r.status_code == 200 and r.json().get("pin") == "",
      "an empty value clears the pin")
_status, _body = vrsc_get()
check(2.6, _body["pin"] == ""
      and _body["rate"]["source"] != "override",
      f"clearing reverts the served tier (travel through the tiers): "
      f"{_body['rate']['source']}")

print("\n== 3. TERRITORIES ==\n")
r = CLIENT.post("/api/admin/vrsc/territories", json={"territories": "banana"})
check(3.1, r.status_code == 400 and "banana" in r.json().get("detail", "").lower(),
      "a non-code token is refused NAMED: " + r.json().get("detail", ""))
r = CLIENT.post("/api/admin/vrsc/territories", json={"territories": "AAAA"})
check(3.2, r.status_code == 400,
      "a 4-letter token is refused (not ISO alpha-2)")
r = CLIENT.post("/api/admin/vrsc/territories",
                json={"territories": "us,, ca"})
_okcodes = r.json().get("territories", [])
check(3.3, r.status_code == 200 and set(_okcodes) == {"US", "CA"},
      f"casual operator input is normalized: 'us,, ca' -> {_okcodes}")
_status, _body = vrsc_get()
check(3.4, _body["territories"]["source"] == "db"
      and set(_body["territories"]["codes"]) == {"US", "CA"},
      f"the card reads back the db tier in force: {_body['territories']}")
check(3.5, {"US", "CA"} <= set(srv._vrsc_territories()),
      "the DOWNLOAD branch's resolver agrees with what was saved")
check(3.6, any(a["action"] == "vrsc_territories_update" for a in audit_rows()),
      "the territory write left an audit row in the operator's name")

print("\n== 4. THE EPISODE LOG carries vrsc_rate.* and NOTHING else ==")
db.add_audit_log(None, "vrsc_rate.served",
                 json.dumps({"tier": "default"}, separators=(",", ":")))
time.sleep(1.1)
db.add_audit_log(None, "vrsc_rate.fetch_failed",
                 json.dumps({"err": "x"}, separators=(",", ":")))
time.sleep(1.1)
db.add_audit_log(None, "vrsc_rate.recovered",
                 json.dumps({"rate": 0.27}, separators=(",", ":")))
db.add_audit_log(None, "manual_draw", "Drawing triggered")
_status, _body = vrsc_get()
_actions = [e["action"] for e in _body["episodes"]]
check(4.1, _actions and all(a.startswith("vrsc_rate.") for a in _actions),
      f"every row in the card's log is a vrsc_rate.* row: {set(_actions)}")
check(4.2, _actions[:3] == ["vrsc_rate.recovered",
                            "vrsc_rate.fetch_failed",
                            "vrsc_rate.served"],
      f"newest first: {_actions}")

print("\n== 5. EPISODE STATE is surfaced, not buried ==")
db.set_setting(vrsc_price.FAILS_KEY, "2|%d" % int(time.time()))
db.set_setting(vrsc_price.STATE_KEY, "default")
_status, _body = vrsc_get()
check(5.1, _body["state"]["fails"] == vrsc_price._setting(vrsc_price.FAILS_KEY),
      f"the card exposes the failure streak state: {_body['state']}")

print("\n== 6. HERMETIC ==")
check(6.1, Path(str(db.DB_PATH)).is_absolute()
          and "admin_vrsc_" in str(db.DB_PATH),
      f"the drive's connection stays on its sandbox ({db.DB_PATH})")
check(6.2, vrsc_price.live_enabled() is False and not vrsc_price._inflight,
      "live fetching stayed OFF and no fetch thread ever spun up")

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
