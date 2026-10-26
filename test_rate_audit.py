"""test_rate_audit.py — THE RATE EPISODE RECORD (Oct 26).

A dollar figure now names the rate it was converted at, but a label only
says what is happening RIGHT NOW. The question that follows a bad day —
"why did the site quote $337 yesterday?" — needs a history, so xmr_price
writes its transitions to audit_log:

    xmr_rate.served        live <-> stale <-> fallback, override on/off
    xmr_rate.fetch_failed  the feed broke (once per streak, with the error)
    xmr_rate.recovered     it came back, with the value and the outage

That gives an operator four reconstructable facts: when the site left a
live rate, what it fell back to (and what that rate was), when the feed
broke, and what value it recovered to.

A CHANGE HAS TO STICK. Every TTL tick answers one read stale while its
own refresh runs; logging that would bury the real episode, so a new
state is a CANDIDATE until it has been observed for STICKY_SECONDS, and
`state_since` records when it actually began. `promote()` below is how
this file ages a candidate without sleeping for two minutes.

This file grades:

  1. a fresh box records "fallback, previous: none" at once
  2. a repeated read in the same state writes NOTHING (no page-view spam)
  3. a new state is a candidate first, a served row after it sticks
  4. a blip (stale for a second, then refreshed) writes NOTHING
  5. fallback -> live carries the fetched rate and its source
  6. live -> stale carries the age of the rate still being served
  7. the operator pin is its own state, entered and left
  8. a failure streak is ONE row; recovery names the value, the attempts
     and the outage
  9. the memory survives the process (it lives in settings)
 10. a refused audit row is kept and retried, never lost
 11. the operator can read it all at /api/admin/audit
 12. hermetic: sandbox DB, no network (the fetcher is stubbed)

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

os.environ["XMR_PRICE_LIVE"] = "0"      # the fetcher is stubbed below

import database as db                                        # noqa: E402

_tmp = tempfile.mkdtemp(prefix="rate_audit_")
db.DB_PATH = Path(_tmp) / "jackpot.db"
db.init_db()

import xmr_price                                              # noqa: E402
import server as srv                                          # noqa: E402
from fastapi.testclient import TestClient                     # noqa: E402

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


def rows(action=None):
    with db.get_db() as conn:
        if action:
            cur = conn.execute(
                "SELECT * FROM audit_log WHERE action = ? ORDER BY id",
                (action,))
        else:
            cur = conn.execute("SELECT * FROM audit_log ORDER BY id")
        return [dict(r) for r in cur.fetchall()]


def details(row):
    return json.loads(row["details"] or "{}")


def read():
    xmr_price.reset()
    return xmr_price.current(fallback=323)


def promote(seconds=None):
    """Age the candidate state so it counts as 'stuck' without waiting.

    This is the same key the reader writes; it is time, not a mock.
    """
    seconds = xmr_price.STICKY_SECONDS + 60 if seconds is None else seconds
    raw = db.get_setting(xmr_price.CANDIDATE_KEY, "")
    if not raw:
        return False
    cand = json.loads(raw)
    cand["since"] = float(cand.get("since") or time.time()) - seconds
    db.set_setting(xmr_price.CANDIDATE_KEY,
                   json.dumps(cand, separators=(",", ":")))
    return True


def settle():
    """Look, age the candidate, look again — return the row it wrote.

    Returns the details dict of the transition recorded by the second
    read, or None when nothing was recorded (the state had not changed).
    """
    read()                          # first look: the candidate
    promote()
    _before = len(rows("xmr_rate.served"))
    read()                          # stuck now: the row
    _after = rows("xmr_rate.served")
    return details(_after[-1]) if len(_after) > _before else None


def clear_rate():
    for k in (xmr_price.OVERRIDE_KEY, xmr_price.RATE_KEY, xmr_price.TS_KEY,
              xmr_price.SRC_KEY, xmr_price.ATTEMPT_KEY, xmr_price.ERR_KEY,
              xmr_price.FAILS_KEY, xmr_price.CANDIDATE_KEY):
        db.set_setting(k, "")


print("== 1. A FRESH BOX COMES UP ON THE FALLBACK, AND SAYS SO ==")
_info = read()
_all = rows()
check(1.1, len(_all) == 1 and _all[0]["action"] == "xmr_rate.served",
      f"one transition row: {[r['action'] for r in _all]}")
_d = details(_all[-1])
check(1.2, _d.get("state") == "fallback" and _d.get("previous") is None,
      f"fallback, previous unknown: {_d}")
check(1.3, abs(float(_d.get("rate", 0)) - 323.0) < 1e-9
      and _d.get("source") == "default" and _d.get("stale") is True,
      "…quoting the constant, marked stale")
check(1.4, _d.get("age_s") is None and _d.get("previous_rate") is None,
      "no age and no previous rate to report")

print("\n== 2. READING AGAIN IN THE SAME STATE WRITES NOTHING ==")
for _ in range(5):
    read()
check(2.1, len(rows()) == 1,
      f"six reads, one row (page views do not grow the log): {len(rows())}")

print("\n== 3. A NEW STATE IS A CANDIDATE BEFORE IT IS SERVED ==")
_now = time.time()
db.set_setting(xmr_price.RATE_KEY, "600")
db.set_setting(xmr_price.TS_KEY, "%.3f" % _now)
db.set_setting(xmr_price.SRC_KEY, "kraken_ticker")
read()
check(3.1, len(rows()) == 1,
      "the first look at a new state writes nothing yet")
check(3.2, json.loads(db.get_setting(xmr_price.CANDIDATE_KEY, "{}")
                      or "{}").get("state") == "live",
      "…it is held as a candidate")
check(3.3, settle() is not None and len(rows()) == 2,
      "…and is recorded once it has stuck")

print("\n== 4. A TTL BLIP IS NOT AN EPISODE ==")
_n = len(rows())
db.set_setting(xmr_price.TS_KEY, "%.3f" % (_now - 3600))
read()                                    # stale again, candidate
db.set_setting(xmr_price.TS_KEY, "%.3f" % (time.time() - 1))
read()                                    # refreshed: back to the logged live
check(4.1, len(rows()) == _n,
      f"stale-for-a-second-then-refreshed writes nothing: {len(rows()) - _n} rows")
check(4.2, db.get_setting(xmr_price.CANDIDATE_KEY, "") == "",
      "…and the candidate is dropped")

print("\n== 5. LIVE -> STALE CARRIES THE AGE, ONCE IT PERSISTS ==")
db.set_setting(xmr_price.TS_KEY, "%.3f" % (_now - 3600))
read()
check(5.1, len(rows()) == _n, "the stale read alone writes nothing")
_d = settle() or {}
check(5.2, _d.get("previous") == "live" and _d.get("state") == "stale",
      f"a stale state that persists logs: {_d.get('previous')} -> {_d.get('state')}")
check(5.3, abs(float(_d.get("rate", 0)) - 600.0) < 1e-9,
      "still serving the last good rate, not a new one")
check(5.4, 3500 <= int(_d.get("age_s") or 0) <= 3700,
      f"…and says how old it is: {_d.get('age_s')}s")
check(5.5, float(_d.get("state_since") or 0) > 0,
      "…and when the state began (the row may be up to 2 min later)")

print("\n== 6. FALLBACK -> LIVE NAMES THE VALUE IT RECOVERED TO ==")
clear_rate()
settle()                                   # back to the fallback
_now2 = time.time()
db.set_setting(xmr_price.RATE_KEY, "612.5")
db.set_setting(xmr_price.TS_KEY, "%.3f" % _now2)
db.set_setting(xmr_price.SRC_KEY, "kraken_ticker")
read()
_d = settle() or {}
check(6.1, _d.get("previous") == "fallback" and _d.get("state") == "live",
      f"{_d.get('previous')} -> {_d.get('state')}")
check(6.2, abs(float(_d.get("rate", 0)) - 612.5) < 1e-9
      and abs(float(_d.get("previous_rate", 0)) - 323.0) < 1e-9,
      f"the rates on both sides of the transition: {_d}")
check(6.3, _d.get("source") == "kraken_ticker" and _d.get("stale") is False,
      "with the venue and freshness")

print("\n== 7. THE OPERATOR PIN IS ITS OWN STATE ==")
db.set_setting(xmr_price.OVERRIDE_KEY, "500")
read()
_d = settle() or {}
check(7.1, _d.get("previous") == "live" and _d.get("state") == "override"
      and abs(float(_d.get("rate", 0)) - 500.0) < 1e-9,
      f"pin engaged: {_d.get('previous')} -> {_d.get('state')} @ {_d.get('rate')}")
db.set_setting(xmr_price.OVERRIDE_KEY, "")
read()
_d = settle() or {}
check(7.2, _d.get("previous") == "override" and _d.get("state") == "live",
      f"pin released: {_d.get('previous')} -> {_d.get('state')}")

print("\n== 8. A BROKEN FEED IS ONE ROW, AND RECOVERY NAMES THE VALUE ==")
clear_rate()
settle()                                   # serving the fallback
_served_before = len(rows("xmr_rate.served"))


def _boom():
    raise OSError("no route to host")


_orig_fetch = xmr_price._fetch
xmr_price._fetch = _boom
try:
    xmr_price.refresh_now(force=True)
    xmr_price.refresh_now(force=True)
    xmr_price.refresh_now(force=True)
finally:
    xmr_price._fetch = _orig_fetch
_failed_rows = rows("xmr_rate.fetch_failed")
check(8.1, len(_failed_rows) == 1,
      f"three failed attempts, ONE row: {len(_failed_rows)}")
_d = details(_failed_rows[-1])
check(8.2, "no route to host" in str(_d.get("error", "")),
      f"the error is recorded: {_d.get('error')!r}")
check(8.3, _d.get("serving") == "fallback",
      f"…alongside what was still being served: {_d.get('serving')}")
check(8.4, len(rows("xmr_rate.served")) == _served_before,
      "a failed fetch is not a served-state transition (no double row)")

xmr_price._fetch = lambda: 641.25
try:
    xmr_price.refresh_now(force=True)
finally:
    xmr_price._fetch = _orig_fetch
_rec = rows("xmr_rate.recovered")
_d = details(_rec[-1])
check(8.5, len(_rec) == 1 and abs(float(_d.get("rate", 0)) - 641.25) < 1e-9,
      f"recovered to 641.25: {_d}")
check(8.6, int(_d.get("failed_attempts") or 0) == 3,
      f"…after 3 failed attempts: {_d.get('failed_attempts')}")
check(8.7, isinstance(_d.get("outage_s"), int) and _d["outage_s"] >= 0,
      f"…with the outage length: {_d.get('outage_s')}s")
_d = settle() or {}
check(8.8, _d.get("previous") == "fallback" and _d.get("state") == "live"
      and abs(float(_d.get("rate", 0)) - 641.25) < 1e-9,
      "the next read records the state the site actually serves")

print("\n== 9. THE MEMORY OUTLIVES THE PROCESS ==")
_stored = json.loads(db.get_setting(xmr_price.STATE_KEY) or "{}")
check(9.1, _stored.get("state") == "live"
      and abs(float(_stored.get("rate", 0)) - 641.25) < 1e-9,
      f"the last served state is in settings, not in memory: {_stored}")
check(9.2, db.get_setting(xmr_price.FAILS_KEY) in ("", None),
      "…and the failure streak is cleared on recovery")

print("\n== 10. A REFUSED AUDIT ROW IS KEPT, NOT LOST ==")
db.set_setting(xmr_price.OVERRIDE_KEY, "777")
read()                                     # candidate: override
_saved_audit = db.add_audit_log


def _dead(*a, **k):
    raise RuntimeError("audit table locked")


promote()
db.add_audit_log = _dead
try:
    _info = read()                         # must record -> writer refuses
finally:
    db.add_audit_log = _saved_audit
check(10.1, abs(float(_info["rate"]) - 777.0) < 1e-9,
      f"the reader answered anyway: {_info['rate']}")
_pending = json.loads(db.get_setting(xmr_price.PENDING_KEY, "") or "{}")
check(10.2, _pending.get("state") == "override",
      f"the refused transition was kept: {_pending.get('state')}")
read()                                     # the writer is back
check(10.3, details(rows("xmr_rate.served")[-1]).get("state") == "override",
      "…and lands on the next read")
check(10.4, db.get_setting(xmr_price.PENDING_KEY, "") == "",
      "…with nothing left pending")
db.set_setting(xmr_price.OVERRIDE_KEY, "")

print("\n== 11. THE OPERATOR CAN READ IT ==")
_uid = db.create_user("rate_auditor", "x", email="rates@example.test",
                      is_admin=True)
_tok = "rate-audit-token"
db.create_session(_tok, _uid, int(time.time() + 3600))
CLIENT.cookies.set("session", _tok)
_r = CLIENT.get("/api/admin/audit")
_body = _r.json() if _r.status_code == 200 else []
_actions = [row.get("action") for row in _body]
check(11.1, _r.status_code == 200, f"admin audit answers {_r.status_code}")
check(11.2, all(a in _actions for a in ("xmr_rate.served",
                                       "xmr_rate.fetch_failed",
                                       "xmr_rate.recovered")),
      f"the card carries all three actions: {sorted(set(_actions))}")
check(11.3, all(row.get("user_id") is None
                for row in rows() if str(row["action"]).startswith("xmr_rate.")),
      "system rows carry no user (they are not somebody's action)")

print("\n== 12. HERMETIC ==")
check(12.1, str(db.DB_PATH).startswith(_tmp), "every row landed in the sandbox")
check(12.2, str(HERE / "pool" / "jackpot.db") != str(db.DB_PATH),
      "the live jackpot.db was never opened")
check(12.3, xmr_price.live_enabled() is False,
      "no fetch of this file's own ever dialled the network")

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
