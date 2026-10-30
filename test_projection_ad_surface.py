"""test_projection_ad_surface.py — THE ADVERT HAS ONE DOOR, AND ADMIN IS
OUTSIDE IT (Oct 26).

`jackpot_projection` is an advertisement, not an account. Before this
change it was readable from anywhere: the worker read its own previous
row, and any handler could paint a row somewhere. The admin "Current
Jackpot" card once did exactly that and told the operator they held
$337 when the pot held $620. Logic was fixed in server.py — but a table
that is merely *not currently* read is a habit, not a guarantee.

So the table moved behind pool/projection_ad.py, and this file grades the
guarantee from every angle it can:

  1. ONE OWNER: no module but projection_ad performs SQL against the
     table (database.py creates it), and the old worker read
     `latest_projection` is gone — there is no second door.
  2. THE GUARD IS WIRED: same function, two URLs. Served from a public
     path it answers; served from /api/admin it raises, through the real
     FastAPI app and the real middleware.
  3. EVERY ADMIN ENDPOINT: with an absurd projection row planted, every
     /api/admin GET route is exercised with an admin session and none of
     their bodies carries the planted number.
  4. THE PUBLIC CARD STILL WORKS: it gets the seed and the LIVE pot, and
     the row's stale `current_jackpot` snapshot never reaches the page.
  5. THE ESTIMATE IS NOT MONEY: it refuses float(), and the only sum it
     offers has to be handed the real pot.
  6. WRITES STAY OPEN: an operator-triggered run may still publish (it
     generates an estimate); it just cannot read one back.
  7. HERMETIC: one sandbox DB, the rate pinned, no wallet RPC, no network.

House conventions: check/report, sandbox_db, make_client — the ONE copy of
that harness lives in conftest.py now, shared with the other house scripts.
"""
import json
import os
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

from conftest import (                     # the harness, ONE copy: conftest.py
    check,                                 # one grade: PASS/FAIL + counters
    make_client,                           # TestClient, wallet RPC refused
    report,                                # the RESULT line + the exit code
    sandbox_db,                            # a fresh jackpot.db in a fresh tempdir
)

# Same fixture as test_rate_label and test_ui_disclosure: no fetching in this
# file. The projection card converts the pot and the seed into dollars
# through the rate reader, and a background Kraken refresh landing mid-drive
# would move the rate between the /api/stats render and the comparison.
# XMR_PRICE_LIVE=0 keeps the pinned operator rate (500.0 below) the whole
# drive's fixed input, off the network.
os.environ["XMR_PRICE_LIVE"] = "0"

import database as db                                        # noqa: E402

_tmp = sandbox_db("projection_ad_")
db.set_setting("XMR_USD_RATE", "500.0")      # no live rate fetch

import projection_ad                                          # noqa: E402

# No wallet RPC behind this test: the admin surface sweep must not dial
# a node, and an offline box is the honest default here.
CLIENT, srv = make_client()
QUIET, _ = make_client(raise_server_exceptions=False)


SEED = 987_654.32          # the planted advertisement
SNAPSHOT_CURRENT = 777_777.0   # its stale "pot", which must never travel


def plant(seed=SEED, current=SNAPSHOT_CURRENT, possible=None):
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO jackpot_projection (possible_future_jackpot,"
            " referral_seed, current_jackpot, count_of_users_included,"
            " anomaly, source) VALUES (?, ?, ?, ?, ?, ?)",
            (possible if possible is not None else current + seed,
             seed, current, 4, 0, "kraken_referral_sim_v1"))


def body_has(text, needle):
    return str(needle).replace(".", "") in str(text).replace(".", "")


print("== 1. ONE OWNER (static) ==")
# Real SQL STATEMENTS, not the name: audit labels ("jackpot_projection.updated"),
# the endpoint's function name and prose all mention it legitimately.
_SQL = re.compile(
    r"(?is)(select\b[^;\n]{0,120}\bfrom\s+jackpot_projection"
    r"|insert\s+into\s+jackpot_projection"
    r"|update\s+jackpot_projection"
    r"|delete\s+from\s+jackpot_projection)")
_CREATE = re.compile(r"(?is)create\s+table\s+if\s+not\s+exists\s+jackpot_projection")
_owners, _creators = set(), set()
for _p in sorted((HERE / "pool").glob("*.py")):
    _txt = _p.read_text(encoding="utf-8", errors="replace")
    if _SQL.search(_txt):
        _owners.add(_p.name)
    if _CREATE.search(_txt):
        _creators.add(_p.name)

check(1.1, _owners == {"projection_ad.py"},
      f"the only module with SELECT/INSERT against the table: {sorted(_owners)}")
check(1.2, _creators == {"database.py"},
      f"the schema lives where schema lives: {sorted(_creators)}")
_kr = (HERE / "pool" / "kraken_referral.py").read_text(encoding="utf-8")
_srv_txt = (HERE / "pool" / "server.py").read_text(encoding="utf-8")
check(1.3, not hasattr(srv.kr, "latest_projection"),
      "the worker module no longer serves a display read")
check(1.4, not re.search(r"def\s+latest_projection", _kr)
      and not re.search(r"def\s+latest_projection", _srv_txt),
      "…and no such function survives anywhere (no second door)")
check(1.5, all(("\"jackpot_projection." in ln) or ln.lstrip().startswith("(")
               for ln in _kr.splitlines() if "jackpot_projection" in ln),
      "kraken_referral mentions it only in prose and audit ACTION names")
check(1.6, all("api_jackpot_projection" in ln
               for ln in _srv_txt.splitlines()
               if "jackpot_projection" in ln),
      "in server.py the name is only the public handler's own name")

print("\n== 2. THE GUARD IS WIRED THROUGH THE REAL APP ==")


@srv.app.get("/api/_probe_projection")
def _probe_public():
    est = projection_ad.read_estimate(projection_ad.AdSlot.HERO)
    return {"seed": est.referral_seed_usd if est else None}


@srv.app.get("/api/admin/_probe_projection")
def _probe_admin():
    # The SAME read, one URL over. If the guard were a naming convention
    # this would answer; it must not.
    est = projection_ad.read_estimate(projection_ad.AdSlot.HERO)
    return {"seed": est.referral_seed_usd if est else None}


# the catch-all /{spa_page} is registered before these; front-load them
for _name in ("/api/_probe_projection", "/api/admin/_probe_projection"):
    _r = next(r for r in srv.app.routes
              if getattr(r, "path", None) == _name)
    if _r in srv.app.router.routes:
        srv.app.router.routes.remove(_r)
        srv.app.router.routes.insert(0, _r)

plant()
_r = QUIET.get("/api/_probe_projection")
check(2.1, _r.status_code == 200 and abs(float(_r.json()["seed"]) - SEED) < 0.01,
      f"a public surface reads it: {_r.status_code} {_r.text[:60]}")
_r = QUIET.get("/api/admin/_probe_projection")
check(2.2, _r.status_code == 500,
      f"the SAME read from an admin URL is refused ({_r.status_code})")
check(2.3, not body_has(_r.text, SEED),
      "…and the refusal body never carries the number")
check(2.4, "advertisement_surface_marker" in _srv_txt,
      "the marker middleware is what tells the module the URL")

# the two probes were scaffolding for this section; a sweep of REAL
# endpoints must not include a route whose 500 is the point.
for _probe in ("/api/_probe_projection", "/api/admin/_probe_projection"):
    for _r in [r for r in srv.app.router.routes
               if getattr(r, "path", None) == _probe]:
        srv.app.router.routes.remove(_r)
check(2.8, not [r for r in srv.app.routes
                if "_probe_projection" in getattr(r, "path", "")],
      "the probes are gone before the endpoint sweep")

# direct unit view of the guard, including its exact-prefix matches
for _path in ("/api/admin", "/api/admin/", "/api/admin/stats",
              "/api/admin/kraken-referral/config?verify=1", "/admin",
              "/admin/settings"):
    _tok = projection_ad.mark_active_path(_path)
    try:
        projection_ad.read_estimate(projection_ad.AdSlot.HERO)
        _refused = False
    except projection_ad.AdminReadForbidden:
        _refused = True
    finally:
        projection_ad.clear_active_path(_tok)
    check(f"2.5{_path}", _refused, f"refused while serving {_path}")

_tok = projection_ad.mark_active_path("/api/jackpot/projection")
try:
    _ok = projection_ad.read_estimate(projection_ad.AdSlot.HERO) is not None
finally:
    projection_ad.clear_active_path(_tok)
check(2.6, _ok, "allowed on the public advertisement path")

for _bad in ("admin", "hero", None, "", "ADMIN"):
    try:
        projection_ad.read_estimate(_bad)
        _refused = False
    except projection_ad.NotAnAdSurface:
        _refused = True
    check(f"2.7{_bad!r}", _refused,
          f"a non-AdSlot {_bad!r} is not a slot an admin can claim")

print("\n== 3. EVERY ADMIN GET ENDPOINT ==")
_uid = db.create_user("surf_admin", "x", email="surf@example.test",
                      is_admin=True)
_tok = "surface-admin-token"
db.create_session(_tok, _uid, int(time.time() + 3600))
CLIENT.cookies.set("session", _tok)
QUIET.cookies.set("session", _tok)      # its own jar: same session, no re-raise

_admin_routes = sorted({getattr(r, "path", "") for r in srv.app.routes
                        if getattr(r, "path", "").startswith("/api/admin")
                        and "GET" in (getattr(r, "methods", set()) or set())})
check(3.1, len(_admin_routes) >= 15,
      f"{len(_admin_routes)} admin GET routes under test")
print("     " + ", ".join(_admin_routes))

_leaks, _scanned, _codes, _broken = [], 0, [], []
for _path in _admin_routes:
    # QUIET, not CLIENT: an endpoint that reaches for the advert is a
    # RESULT to grade (the guard turns it into a 500), never a crash that
    # would hide every check after it.
    _r = QUIET.get(_path)
    _codes.append((_path, _r.status_code))
    if _r.status_code >= 500:
        _broken.append(_path)
        continue
    if _r.status_code >= 400:
        continue
    _scanned += 1
    if body_has(_r.text, SEED) or body_has(_r.text, SNAPSHOT_CURRENT):
        _leaks.append(_path)
check(3.2, _scanned >= 15, f"{_scanned} admin bodies actually read")
check(3.3, not _leaks,
      f"no admin body carries the planted advertisement: leaks={_leaks}")
check(3.4, not _broken,
      f"no admin endpoint broke trying to read it: {_broken}")
check(3.5, srv.app.routes and "/api/admin/stats" in _admin_routes,
      "the pot endpoint itself was in the sweep")

_handlers = re.split(r"(?=@app\.(?:get|post|put|delete|api_route)\()", _srv_txt)
_admin_handlers = [h for h in _handlers if '"/api/admin' in h.split("\n")[0]]
check(3.6, len(_admin_handlers) >= 15,
      f"{len(_admin_handlers)} admin handlers inspected in source")
check(3.7, not [h for h in _admin_handlers
                if "projection_ad" in h or "jackpot_projection" in h],
      "no admin handler body names the module or the table")
_public = next(h for h in _handlers if '"/api/jackpot/projection"' in h[:200])
check(3.8, "projection_ad.read_estimate" in _public
      and "AdSlot.HERO" in _public,
      "the public card reads through the ad module, at the hero slot")
check(3.9, "AdminReadForbidden" in _public,
      "…and never swallows a forbidden read as 'no data'")

print("\n== 4. THE PUBLIC CARD STILL WORKS, FROM LIVE MONEY ==")
db.set_setting("jackpot_balance", "1.5")
_r = CLIENT.get("/api/jackpot/projection")
_d = _r.json() if _r.status_code == 200 else {}
check(4.1, _r.status_code == 200 and abs(_d.get("referral_seed_usd", 0) - SEED) < 0.01,
      f"the seed is the advert's ({_d.get('referral_seed_usd')})")
_stats = CLIENT.get("/api/stats").json()
check(4.2, abs(_d.get("current_jackpot_usd", 0) - float(_stats["jackpot_usd"])) < 0.01,
      f"the current leg is the LIVE pot, not the snapshot "
      f"({_d.get('current_jackpot_usd')} = {_stats['jackpot_usd']})")
check(4.3, abs(_d.get("possible_future_jackpot_usd", 0)
               - (float(_stats["jackpot_usd"]) + SEED)) < 0.01,
      "possible = live pot + seed")
check(4.4, not body_has(_r.text, SNAPSHOT_CURRENT),
      f"the row's stale {SNAPSHOT_CURRENT:,.0f} snapshot never reaches the page")
check(4.5, _d.get("source") == "kraken_referral_sim_v1"
      and _d.get("count_of_users_included") == 4,
      "provenance and sample size still surfaced")

print("\n== 5. AN ESTIMATE IS NOT MONEY ==")
_est = projection_ad.read_estimate(projection_ad.AdSlot.REFERRAL_PAGE)
try:
    float(_est)
    _converted = True
except TypeError:
    _converted = False
check(5.1, not _converted, "Estimate refuses float()")
check(5.2, _est.total_with(10.0) == round(10.0 + SEED, 2),
      f"total_with() adds only the pot it was handed: {_est.total_with(10.0)}")
check(5.3, _est.slot is projection_ad.AdSlot.REFERRAL_PAGE,
      "the estimate remembers which advertisement surface asked")

print("\n== 6. WRITES STAY OPEN (an operator run may publish) ==")
_tokp = projection_ad.mark_active_path("/api/admin/kraken-referral/run")
try:
    _out = projection_ad.publish_estimate(possible_usd=5.0, seed_usd=1.0,
                                          current_usd=4.0, users=2,
                                          source="kraken_referral_sim_v1")
finally:
    projection_ad.clear_active_path(_tokp)
check(6.1, isinstance(_out, dict) and _out["anomaly"] in (0, 1),
      f"publishing from an admin-triggered run is allowed: {_out}")
with db.get_db() as conn:
    _n = conn.execute("SELECT COUNT(*) c FROM jackpot_projection").fetchone()["c"]
check(6.2, _n >= 2, f"the row landed ({_n} rows)")
_out2 = projection_ad.publish_estimate(possible_usd=50.0, seed_usd=1.0,
                                       current_usd=4.0, users=2,
                                       source="kraken_referral_sim_v1",
                                       spike_limit=25.0)
check(6.3, _out2["anomaly"] == 1 and _out2["previous_usd"] == 5.0,
      f"the spike guard still works from inside the write: {_out2}")

print("\n== 7. HERMETIC ==")
check(7.1, str(db.DB_PATH).startswith(_tmp), "every write landed in the sandbox")
check(7.2, str(HERE / "pool" / "jackpot.db") != str(db.DB_PATH),
      "the live jackpot.db was never opened")

report()
