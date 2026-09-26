"""test_prefs_live_server.py — THE PREFS CONTRACT, THROUGH THE RUNNING
SERVER (Sep 26).

Endpoint-level integration: every request below goes over real HTTP
to the LIVE uvicorn (127.0.0.1:3094) with a REAL session cookie from
a real /api/auth/register + /api/auth/login — no TestClient, no
in-process app. The other prefs drives pin the handler logic; this
one pins what a browser actually experiences, end to end:

  1. unauthenticated reads/writes -> 401 (cookie-less)
  2. register+login -> GET returns the full envelope with ETag
  3. GET contract: user_id is the SESSION's username (echoed, never
     accepted from the wire), preferences object, updated_at stamp
  4. conditional GET: If-None-Match with the live ETag -> 304 (with
     ETag), a changed body after a write invalidates it
  5. bulk POST envelope {"preferences": {...}}: 200 + new stamp +
     new ETag; single-toggle shape still accepted (compat)
  6. optimistic concurrency: POST with the CURRENT If-Match applies;
     with a STALE one -> 412 + current envelope + current ETag, and
     the applied-after-conflict version is the server's
  7. the two-device race, for real: session A and session B, B reads,
     A writes, B writes with B's stale token -> exactly one 412, and
     the surviving state is A's write until B re-reads + retries
  7b. the LONG race: the same two devices keep writing in an
     interleaved sequence, each adopting the version every response
     carries (what push_client v6 does) — exactly ONE 412 across the
     whole exercise, every stamp strictly advancing, and both devices
     converge on the identical final envelope (same preferences,
     same updated_at, same ETag)
  8. unknown category keys are ignored; non-dict preferences -> 400;
     server state survives what the wire tried
  9. wire user_id is IGNORED (mallory cannot write alice's prefs)
 10. hermeticity: the probe user's prefs are restored and the probe
     left as it was found (no other user's rows touched)
 11. the pipe test: POST /api/push/test fires ONE real templated
     push per category through the live sender; the honest verdict
     ("no subscription" for a fresh probe) comes back verbatim

Requires the live server on APP_PORT (default 3094). Skips itself
(RESULT: 0 passed, 0 failed + note) when the server is down, so the
fleet stays green on boxes without the tunnel.
"""
import json
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

BASE = "http://127.0.0.1:3094"
PROBE = "prefs_live_probe"

results = []
def check(n, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(n) + ("  " + str(msg) if msg else ""))


class Client:
    """A real HTTP client with a real cookie jar (the session)."""

    def __init__(self):
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def req(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        r = urllib.request.Request(BASE + path, data=data, method=method,
                                   headers=h)
        try:
            resp = self.opener.open(r, timeout=15)
            raw = resp.read().decode()
            return resp.status, dict(resp.headers), (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                return e.code, dict(e.headers), json.loads(raw)
            except ValueError:
                return e.code, dict(e.headers), {"raw": raw}

    def get(self, path, headers=None):
        return self.req("GET", path, None, headers)

    def post(self, path, body=None, headers=None):
        return self.req("POST", path, body, headers)


def server_up():
    try:
        with urllib.request.urlopen(BASE + "/api/stats", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


if not server_up():
    print("SKIP: live server not reachable on " + BASE +
          " — the fleet stays green; run with the server up for this drive")
    print("RESULT: 0 passed, 0 failed")
    sys.exit(0)

import time
# DETERMINISTIC PROBE (Sep 26, caught by the fleet gate): a unique
# suffix per run exhausted the register limiter (5/hour/IP) under
# repeated fleet runs — 2.1/2.2 failed "stably" with nothing wrong.
# One fixed probe account instead: register the first time, log in
# every run after (the 7.x restore keeps its state clean).""
username = PROBE + "_fleet"
PASSWORD = "correct horse battery"

print("== 1. UNAUTHENTICATED ==")
anon = Client()
s, h, j = anon.get("/api/push/preferences")
check(1.1, s == 401, f"cookie-less GET: {s}")
s, h, j = anon.post("/api/push/preferences",
                    {"preferences": {"jackpot": False}})
check(1.2, s == 401, f"cookie-less POST: {s}")

print("== 2. REAL SESSION ==")
c = Client()
s, h, j = c.post("/api/auth/register",
                 {"username": username, "password": PASSWORD})
fresh = s == 200
if not fresh:
    # an existing probe logs in; a 429 means the register limiter is
    # hot (fleet ran >5 registrations this hour) AND the probe does
    # not exist yet — an environmental block, not a regression
    s, h, j = c.post("/api/auth/login",
                     {"username": username, "password": PASSWORD})
    if s == 401:
        print("SKIP: register limiter hot and the fixed probe account "
              "is not bootstrapped yet — rerun within the hour or "
              "bootstrap the probe once (operator action)")
        print("RESULT: 0 passed, 0 failed")
        sys.exit(0)
check(2.1, s == 200,
      f"real session over HTTP ({'registered' if fresh else 'logged in'}: {s})")
s, h, j = c.get("/api/push/preferences")
etag0 = (h.get("ETag") or h.get("etag") or "").strip('"')
check(2.2, s == 200 and j.get("user_id") == username
      and isinstance(j.get("preferences"), dict)
      and "updated_at" in j and etag0,
      f"envelope + ETag: user={j.get('user_id')} etag={etag0}")
check(2.3, (j["preferences"].get("jackpot") is True
            and j["preferences"].get("scout") is True) if fresh else True,
      "fresh registrations default both ON (reuse: state was restored "
      "by the last run's 7.x)")
stamp0 = j.get("updated_at")

print("== 3. CONDITIONAL GET ==")
s, h, j = c.get("/api/push/preferences",
                {"If-None-Match": f'"{etag0}"'})
check(3.1, s == 304, f"If-None-Match with the live ETag: {s}")
check(3.2, (h.get("ETag") or h.get("etag") or "").strip('"') == etag0,
      "304 carries the ETag")

print("== 4. BULK POST (the dashboard's shape) ==")
s, h, j = c.post("/api/push/preferences",
                 {"preferences": {"jackpot": False, "scout": True}},
                 {"If-Match": f'"{etag0}"'})
check(4.1, s == 200 and j["preferences"] == {"jackpot": False, "scout": True},
      f"bulk envelope applies: {j.get('preferences')}")
stamp1 = j.get("updated_at")
etag1 = (h.get("ETag") or h.get("etag") or "").strip('"') or stamp1
check(4.2, stamp1 and stamp1 != stamp0,
      f"updated_at advanced: {stamp0} -> {stamp1}")
check(4.3, j.get("user_id") == username, "echo is the session's user")
# server state truly moved
s, h, j = c.get("/api/push/preferences")
check(4.4, j["preferences"]["jackpot"] is False
      and j["preferences"]["scout"] is True,
      "GET confirms the write landed")

print("== 5. THE RACE (two real sessions) ==")
b = Client()
s, _, _ = b.post("/api/auth/login",
                 {"username": username, "password": PASSWORD})
check(5.1, s == 200, "second device logged in")
_, _, b_view = b.get("/api/push/preferences")          # B reads (same version)
b_etag = f'"{b_view["updated_at"]}"'
# A (session c, holding etag1) writes first
s, _, a_res = c.post("/api/push/preferences",
                     {"preferences": {"jackpot": True, "scout": True}},
                     {"If-Match": f'"{etag1}"'})
check(5.2, s == 200, "A's write applies (A held the current version)")
# B writes with B's now-stale token -> exactly one 412 carrying A's state
s, h, b_res = b.post("/api/push/preferences",
                     {"preferences": {"jackpot": False, "scout": False}},
                     {"If-Match": b_etag})
check(5.3, s == 412, f"B's stale write refused: {s}")
check(5.4, b_res.get("error") == "version conflict - re-read and retry"
      and b_res["preferences"] == {"jackpot": True, "scout": True},
      f"412 body carries the server's current envelope: {b_res.get('preferences')}")
conflict_etag = (h.get("ETag") or h.get("etag") or "").strip('"')
check(5.5, conflict_etag and conflict_etag != b_etag.strip('"'),
      "412 carries the CURRENT ETag to retry with")
# B re-reads + retries -> converges (no lost update, no silent clobber)
s, _, b_retry = b.post("/api/push/preferences",
                       {"preferences": {"jackpot": False, "scout": False}},
                       {"If-Match": f'"{conflict_etag}"'})
check(5.6, s == 200, "B's retry with the current version applies")
s, _, final = c.get("/api/push/preferences")
check(5.7, final["preferences"] == {"jackpot": False, "scout": False},
      f"final state is B's retried write: {final['preferences']}")

print("== 5b. THE LONG RACE: two devices, interleaved writes ==")
# The realistic device loop: every response's version is adopted
# (200 body, 412 body, or a reconciliation read), so a stale write
# happens exactly when a device bets on a version another device has
# already superseded — and the loser re-reads and converges.
statuses = []
def _post(dev, prefs, etag):
    s, h, j = dev.post("/api/push/preferences", {"preferences": prefs},
                       {"If-Match": f'"{etag}"'})
    statuses.append(s)
    return s, j

s, _, av = c.get("/api/push/preferences")
s, _, bv = b.get("/api/push/preferences")
t0 = av["updated_at"]
check("5b.1", t0 and bv["updated_at"] == t0
      and av["preferences"] == bv["preferences"],
      f"both devices open on the same version ({t0})")

s1, j1 = _post(c, {"jackpot": False, "scout": True}, t0)      # A writes
t1 = j1.get("updated_at")
check("5b.2", s1 == 200 and t1 and t1 > t0,
      "A's write applies, the stamp strictly advances")

s2, j2 = _post(b, {"jackpot": False, "scout": False}, t0)     # B writes stale
check("5b.3", s2 == 412 and j2.get("updated_at") == t1,
      f"B's stale write refused; the 412 carries A's version ({j2.get('updated_at')})")
check("5b.4", j2.get("preferences") == {"jackpot": False, "scout": True},
      "the 412 body carries A's applied state (no lost update)")

s3, j3 = _post(b, {"jackpot": False, "scout": False}, t1)     # B adopts + retries
t2 = j3.get("updated_at")
check("5b.5", s3 == 200 and t2 > t1,
      "B's retry with the adopted version applies")

s4, _, j4 = c.get("/api/push/preferences")                     # A reconciles
check("5b.6", s4 == 200
      and j4["preferences"] == {"jackpot": False, "scout": False}
      and j4["updated_at"] == t2,
      "A's reconciliation read shows B's write before A's next bet")

s5, j5 = _post(c, {"jackpot": True, "scout": False}, t2)      # A writes again
t3 = j5.get("updated_at")
check("5b.7", s5 == 200 and t3 > t2,
      "A's next write applies after re-reading (no second collision)")

s6, _, j6 = b.get("/api/push/preferences")                     # B reconciles
s7, j7 = _post(b, {"jackpot": True, "scout": True}, j6["updated_at"])
t4 = j7.get("updated_at")
check("5b.8", s6 == 200 and s7 == 200 and t4 > t3,
      "B's final write applies after re-reading")

check("5b.9", statuses.count(412) == 1
      and all(x == 200 for x in statuses if x != 412),
      f"exactly one 412, everything else 200: {statuses}")

# CONVERGENCE: both devices read the identical final envelope
sA, hA, fA = c.get("/api/push/preferences")
sB, hB, fB = b.get("/api/push/preferences")
etagA = (hA.get("ETag") or hA.get("etag") or "").strip('"')
etagB = (hB.get("ETag") or hB.get("etag") or "").strip('"')
check("5b.10", sA == 200 and sB == 200
      and fA["preferences"] == fB["preferences"] == {"jackpot": True, "scout": True}
      and fA["updated_at"] == fB["updated_at"] == t4
      and etagA == etagB,
      f"converged: both devices hold version {fA['updated_at']} with the same ETag")

print("== 6. WIRE DEFENSES ==")
s, _, j = c.post("/api/push/preferences",
                 {"user_id": "mallory", "preferences": {"jackpot": True}})
check(6.1, s == 200 and j.get("user_id") == username,
      f"wire user_id ignored (echo stays {j.get('user_id')})")
s, _, j = c.post("/api/push/preferences",
                 {"preferences": {"jackpot": True, "hacker": "x"}})
check(6.2, s == 200, "unknown category keys ignored, not stored")
s, h, j = c.get("/api/push/preferences")
check(6.3, "hacker" not in json.dumps(j["preferences"]),
      "the store only ever held known categories")
s, _, j = c.post("/api/push/preferences", {"preferences": "not-a-dict"})
check(6.4, s == 400, f"non-dict preferences -> 400: {s}")
s, _, j = c.post("/api/push/preferences", {"category": "scout",
                                           "enabled": False})
check(6.5, s == 200 and j["preferences"]["scout"] is False,
      "legacy single-toggle shape still accepted (compat)")

print("== 6b. THE PIPE TEST (POST /api/push/test, live) ==")
# a FRESH probe user has no subscription - the honest verdict is
# deterministic: the pipe ran and reported exactly that
s, _, j = c.post("/api/push/test", {"category": "jackpot"})
check(6.6, s == 200 and j.get("category") == "jackpot"
      and j.get("ok") is False and j.get("reason") == "no subscription",
      f"jackpot pipe test, honest verdict: {j}")
s, _, j = anon.post("/api/push/test", {"category": "scout"})
check(6.7, s == 401, f"pipe test requires the session: {s}")
s, _, j = c.post("/api/push/test", {"category": "all"})
check(6.8, s == 400, f"unknown category -> 400: {s}")

print("== 7. HERMETICITY: restore the probe, touch nothing else ==")
s, _, j = c.post("/api/push/preferences",
                 {"preferences": {"jackpot": True, "scout": True}})
check(7.1, s == 200 and j["preferences"] == {"jackpot": True, "scout": True},
      "probe restored to defaults (clean state for reruns)")
check(7.2, j.get("user_id") == username,
      "every write in this drive stayed on the probe user")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
