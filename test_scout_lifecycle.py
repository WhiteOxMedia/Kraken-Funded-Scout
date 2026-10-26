"""test_scout_lifecycle.py — PHASE F: the fleet can be counted, not guessed.

Pins pool/scout_lifecycle.py and its wiring:

  0. classify()        the six states, DORMANT-over-MINING precedence, and
                       "a never-connected Scout is never dormant"
  1. install beacon    /api/scout/install creates ONE record per install id
                       (idempotent), mints an id when absent, refuses a
                       malformed one, and stores no wallet
  2. activation        registration marks the machine's beacon row
                       ACTIVATED instead of creating a second row
  3. telemetry         first_connect / first_share / first_valid_share are
                       first-occurrence only; a share implies a handshake;
                       an invalid share never sets first_valid_share; a
                       database failure never escapes record()
  4. buckets           never-connected is separate from dormant and split
                       by activation; connected-without-valid-share is its
                       own list
  5. the four answers  installed / activated / ever connected / submitted
                       shares, each from its own fact, with every row in
                       exactly one state
  6. admin surface     401 anonymous, 403 non-admin, 200 admin
  7. backfill          the existing miners table seeds rows once, skips
                       wallets the payout gate refuses, invents no beacon
  8. CLI               the operator report renders and parses as JSON

Hermetic: sandbox DB (DB_PATH rebind) for every section; the backfill
section rebinds to a second fresh sandbox. The live jackpot.db is never
opened. Prints "RESULT: N passed, M failed".
"""
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import database as db                                    # noqa: E402

SANDBOX = Path(tempfile.mkdtemp(prefix="scout_life_"))
db.DB_PATH = SANDBOX / "jackpot.db"
db.init_db()

import scout_lifecycle as life                           # noqa: E402
import server                                            # noqa: E402
from fastapi.testclient import TestClient                 # noqa: E402

CLIENT = TestClient(server.app)
results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


def W(seed):
    """A CONSTRUCTED-VALID mainnet address (netbyte + keys + Keccak
    checksum, via the pool's own encoder) — the registration gate runs the
    real offline validation, not a shape check."""
    spend = hashlib.sha256(f"fleet-{seed}".encode()).digest()
    view = hashlib.sha256(f"fleet-{seed}-view".encode()).digest()
    return server._addr.b58encode(
        b"\x12" + spend + view + server._addr.keccak256(
            b"\x12" + spend + view)[:4])


WA, WB, WC, WD, WE, WF, WG = (W(c) for c in "abcdefg")
assert server._wallet_shape_ok(WA) and not server._wallet_shape_ok(
    "PENDING_AuditJunk")

NOW = time.time()


def row_of(wallet=None, install_id=None):
    return [r for r in life._rows()
            if (wallet is not None and r["wallet_address"] == wallet)
            or (install_id is not None and r["install_id"] == install_id)][0]


# ── 0. the state machine, on synthetic rows ──────────────────────────
def synthetic(**kw):
    base = {"install_id": "x", "wallet_address": None, "first_seen": NOW,
            "installed_at": None, "first_connect": None, "first_share": None,
            "first_valid_share": None, "activation_complete": 0,
            "last_seen": NOW}
    base.update(kw)
    return base


print("== 0. classify(): the six states ==")
check("0.1", life.classify(synthetic(), now=NOW) == life.NEW,
      "no fact at all -> NEW")
check("0.2", life.classify(synthetic(installed_at=NOW - 60), now=NOW)
      == life.INSTALLED, "beacon, no activation -> INSTALLED")
check("0.3", life.classify(synthetic(installed_at=NOW - 60,
                                     activation_complete=1), now=NOW)
      == life.ACTIVATED, "activated, never connected -> ACTIVATED")
check("0.4", life.classify(synthetic(activation_complete=1,
                                     first_connect=NOW - 60), now=NOW)
      == life.CONNECTED, "connected, no valid share -> CONNECTED")
check("0.5", life.classify(synthetic(activation_complete=1,
                                     first_connect=NOW - 60,
                                     first_share=NOW - 60,
                                     first_valid_share=NOW - 60), now=NOW)
      == life.MINING, "valid share inside the window -> MINING")
check("0.6", life.classify(synthetic(first_connect=NOW - 30 * life.DAY,
                                     first_share=NOW - 30 * life.DAY,
                                     first_valid_share=NOW - 30 * life.DAY,
                                     last_seen=NOW - 30 * life.DAY), now=NOW)
      == life.DORMANT, "silent past the window -> DORMANT (not MINING)")
check("0.7", life.classify(synthetic(installed_at=NOW - 30 * life.DAY,
                                     last_seen=NOW - 30 * life.DAY), now=NOW)
      == life.INSTALLED, "a never-connected install is NOT dormant")


# ── 1. the install beacon ────────────────────────────────────────────
print("== 1. install beacon ==")
r = CLIENT.post("/api/scout/install", json={"install_id": "install-aaaa-0001"})
check("1.1", r.status_code == 200 and r.json().get("install_id")
      == "install-aaaa-0001", f"beacon accepted: {r.status_code} {r.text[:80]}")
rec = row_of(install_id="install-aaaa-0001")
check("1.2", rec["installed_at"] and rec["wallet_address"] is None
      and not rec["activation_complete"]
      and life.classify(rec) == life.INSTALLED,
      "one INSTALLED row: no wallet, no activation")

CLIENT.post("/api/scout/install", json={"install_id": "install-aaaa-0001"})
check("1.3", len(life._rows()) == 1,
      "the same install id reports once (idempotent)")
check("1.4", CLIENT.post("/api/scout/install",
                         json={"install_id": "x"}).status_code == 422,
      "a malformed install id is refused")
r = CLIENT.post("/api/scout/install", json={})
gen_id = r.json().get("install_id")
check("1.5", r.status_code == 200 and gen_id and len(life._rows()) == 2,
      "a client with no id gets a server-minted one")


# ── 2. activation links the beacon row ───────────────────────────────
print("== 2. activation ==")
r = CLIENT.post("/api/miner/register", json={
    "wallet_address": WA, "worker_name": "rig-a",
    "install_id": "install-aaaa-0001"})
rec_a = row_of(wallet=WA)
check("2.1", r.status_code == 200 and rec_a["install_id"]
      == "install-aaaa-0001" and rec_a["activation_complete"] == 1,
      f"activation landed on the SAME row: {r.text[:80]}")
check("2.2", len(life._rows()) == 2 and life.classify(rec_a) == life.ACTIVATED,
      "no second record; activated and never connected -> ACTIVATED")

r = CLIENT.post("/api/miner/register", json={"wallet_address": WB})
rec_b = row_of(wallet=WB)
check("2.3", r.status_code == 200 and rec_b["activation_complete"] == 1
      and rec_b["installed_at"] is None,
      "a registration without an install id is still activation — only the "
      "beacon link is missing")


# ── 3. telemetry stamps are first-occurrence only ────────────────────
print("== 3. telemetry stamps ==")
t0 = NOW - 500
life.record(wallet=WA, valid=5, at=t0)
rec_a = row_of(wallet=WA)
check("3.1", rec_a["first_valid_share"] == t0 and rec_a["first_share"] == t0
      and rec_a["first_connect"] == t0
      and life.classify(rec_a, now=t0 + 1) == life.MINING,
      "an accepted share stamps all three and implies the handshake")

life.record(wallet=WC, connected=True, at=t0)
rec_c = row_of(wallet=WC)
check("3.2", rec_c["first_connect"] == t0 and rec_c["first_share"] is None
      and life.classify(rec_c, now=t0 + 1) == life.CONNECTED,
      "connected with no share -> CONNECTED, no share stamp")

life.record(wallet=WC, invalid=3, at=t0 + 10)
rec_c = row_of(wallet=WC)
check("3.3", rec_c["first_share"] == t0 + 10
      and rec_c["first_valid_share"] is None,
      "an invalid share stamps first_share but NEVER first_valid_share")

life.record(wallet=WA, valid=9, invalid=1, at=t0 + 900)
life.record(wallet=WA, connected=True, at=t0 + 1000)
rec_a = row_of(wallet=WA)
check("3.4", rec_a["first_valid_share"] == t0 and rec_a["first_connect"] == t0
      and rec_a["last_seen"] == t0 + 1000,
      "first_* are history: later events never rewrite them; last_seen moves")

_orig_path = db.DB_PATH
db.DB_PATH = SANDBOX / "missing" / "jackpot.db"     # parent does not exist


@contextlib.contextmanager
def _quiet():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


with _quiet():
    _soft = life.record(wallet=WA, valid=1)
db.DB_PATH = _orig_path
check("3.5", _soft == {},
      "record() is fail-soft: a database error is logged, never raised")


# ── 4. never-connected is not dormant ────────────────────────────────
print("== 4. never-connected vs dormant ==")
life.record(wallet=WD, connected=True, valid=2, at=NOW - 30 * life.DAY)
life.record(install_id="install-eeee-0001", installed=True,
            at=NOW - 30 * life.DAY)
life.record(install_id="install-ffff-0001", installed=True, at=NOW - 120)
life.record(wallet=WE, install_id="install-ffff-0001", activated=True,
            at=NOW - 60)

rep = life.report(now=NOW, days=7)
never = rep["never_connected"]
dormant_ids = {i["install_id"] for i in rep["dormant"]["scouts"]}
never_ids = {i["install_id"] for i in never["scouts"]}
rec_d = row_of(wallet=WD)
rec_e = row_of(install_id="install-eeee-0001")
rec_f = row_of(wallet=WE)
check("4.1", rec_d["install_id"] in dormant_ids
      and rec_d["install_id"] not in never_ids,
      "a rig that worked and stopped is DORMANT, not never-connected")
check("4.2", life.classify(rec_d, now=NOW) == life.DORMANT
      and life.classify(rec_e, now=NOW) == life.INSTALLED,
      "DORMANT outranks MINING; a silent install stays INSTALLED")
check("4.3", rec_e["install_id"] in never_ids and rec_f["install_id"] in never_ids,
      "an install that never authenticated is in never-connected")
check("4.4", all(i["first_connect"] is None and i["first_share"] is None
                 for i in never["scouts"]),
      "nothing with telemetry ever counts as never-connected")
check("4.5", never["total"] == 4 and never["without_activation"] == 2
      and never["after_activation"] == 2,
      f"never-connected split by activation: {never['total']}")
check("4.6", rec_e["activation_complete"] == 0
      and rec_f["activation_complete"] == 1,
      "the split distinguishes 'never received activation data' from "
      "'activated, never dialed in'")


# ── 5. the four questions ────────────────────────────────────────────
print("== 5. the four answers ==")
# rows: aaaa (activated->mining), minted (installed), WB (activated),
# WC (connected, invalid only), WD (dormant), eeee (installed),
# ffff/WE (activated, never connected)
check("5.1", rep["answers"] == {
    "scouts_installed": 5, "scouts_activated": 3,
    "scouts_ever_connected": 3, "scouts_submitted_shares": 3},
      f"installed/activated/connected/shared: {rep['answers']}")
check("5.2", rep["by_state"] == {
    life.NEW: 0, life.INSTALLED: 2, life.ACTIVATED: 2,
    life.CONNECTED: 1, life.MINING: 1, life.DORMANT: 1},
      f"every row in exactly one state: {rep['by_state']}")
check("5.3", sum(rep["by_state"].values()) == len(life._rows()) == 7,
      "the census accounts for every row")
check("5.4", rep["connected_without_valid_share"]["total"] == 1
      and rep["connected_without_valid_share"]["scouts"][0]["wallet_address"]
      == WC,
      "connected-with-no-accepted-share is its own bucket")
check("5.5", rep["dormant"]["total"] == 1 and never["total"] == 4,
      "inactive (dormant) and never-connected are reported apart")


# ── 6. the admin surface ─────────────────────────────────────────────
print("== 6. admin gate ==")
check("6.1", CLIENT.get("/api/admin/scout-lifecycle").status_code == 401,
      "anonymous -> 401")

uid = db.create_user("fleet_manager", "x", email="fm@example.test",
                     is_admin=False)
tok = "fleet-user-token"
db.create_session(tok, uid, int(NOW + 3600))
CLIENT.cookies.set("session", tok)
check("6.2", CLIENT.get("/api/admin/scout-lifecycle").status_code == 403,
      "signed-in non-admin -> 403")

aid = db.create_user("fleet_admin", "x", email="fa@example.test",
                     is_admin=True)
atok = "fleet-admin-token"
db.create_session(atok, aid, int(NOW + 3600))
CLIENT.cookies.set("session", atok)
r = CLIENT.get("/api/admin/scout-lifecycle")
body = r.json() if r.status_code == 200 else {}
check("6.3", r.status_code == 200 and body.get("answers") == rep["answers"],
      f"admin sees the same answers: {r.status_code}")
check("6.4", body.get("never_connected", {}).get("total") == 4
      and len(body.get("never_connected", {}).get("scouts", [])) == 4,
      "the never-connected list travels with the counts")
CLIENT.cookies.clear()


# ── 7. backfill from the miners table ────────────────────────────────
print("== 7. backfill ==")
db.DB_PATH = SANDBOX / "backfill.db"
db.init_db()
db.get_or_create_miner(WF)
db.update_miner_stats(WF, 10, 0, 500.0)          # mined, then registered
db.get_or_create_miner(WG)                       # registered, never mined
db.get_or_create_miner("PENDING_AuditJunk")      # junk shape: skipped
db.get_or_create_miner("4" + "C" * 94)           # shape-valid, bad checksum
db.get_or_create_miner("4audit" + "a" * 88)      # makes is_valid() raise
check("7.1", life.backfill() == 2,
      "two real wallets seeded; junk, bad checksum and a raising wallet "
      "are all skipped")
rep2 = life.report()
lrow = row_of(wallet=WF)
check("7.2", lrow["first_valid_share"] and lrow["first_connect"]
      and lrow["installed_at"] is None
      and life.classify(lrow) == life.MINING,
      "share history classifies MINING without inventing an install beacon")
grow = row_of(wallet=WG)
check("7.3", grow["activation_complete"] == 1
      and grow["first_connect"] is None
      and life.classify(grow) == life.ACTIVATED,
      "registered-but-never-connected is ACTIVATED and in never-connected")
check("7.4", rep2["answers"] == {
    "scouts_installed": 2, "scouts_activated": 2,
    "scouts_ever_connected": 1, "scouts_submitted_shares": 1},
      f"backfilled answers: {rep2['answers']}")
check("7.5", life.backfill() == 0 and len(life._rows()) == 2,
      "a second backfill seeds nothing (idempotent)")
check("7.6", all(r["wallet_address"] not in (
        "PENDING_AuditJunk", "4" + "C" * 94, "4audit" + "a" * 88)
        for r in life._rows()),
      "no junk wallet becomes a lifecycle record, and none aborts the seed")


# ── 8. the operator report ───────────────────────────────────────────
print("== 8. operator CLI ==")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = life.main(["--json", "--days", "7"])
try:
    parsed = json.loads(buf.getvalue())
except ValueError:
    parsed = {}
check("8.1", rc == 0 and parsed.get("answers") == rep2["answers"],
      "`--json` renders the same answers the endpoint returns")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = life.main([])
text = buf.getvalue()
check("8.2", rc == 0 and "SCOUT FLEET" in text
      and "NEVER CONNECTED" in text
      and "submitted shares" in text,
      "the human report shows the four answers and the split")

passed = sum(1 for r in results if r)
failed = len(results) - passed
print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
