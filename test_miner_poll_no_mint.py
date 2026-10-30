"""test_miner_poll_no_mint.py — THE RESURRECTION GUARD, PINNED (Oct 29).

CHARTER: GET /api/miner/<wallet> used to call get_or_create_miner, so
every unauthenticated poll of a wallet that had no row MINTED one —
the unauthenticated way the R111 playtest zombie kept coming back
after each sweep. The route is READ-ONLY now: wallets are minted only
by activation (POST /api/miner/register) and signed ingest
(POST /api/miner/shares). This drive makes a quiet revert impossible
to miss, and pins that the two legal creators still create:

  1. THE POLLS ARE 404: shape-valid mainnet wallets with no row answer
     404 for a GET, repeatedly.
  2. THE POLLS MINT NOTHING: the miners table stays empty after the
     poll volley.
  3. THE 404 IS HONEST ABOUT WHY: the body names the wallet as
     unregistered yet ("no miner for this wallet yet"), not a generic
     error that could mask a 500.
  4. JUNK STILL CAN'T MINT VIA THE POLL: the literal R111 zombie
     string and row-junk shapes like 'PENDING_*' get 404 too, and the
     table still never grows.
  5. ACTIVATION STILL MINTS: POST /api/miner/register creates the row
     (200), after which THAT wallet's GET answers 200 — a
     404-everywhere regression would break real miners too.
  6. THE ROW STAYS DEAD AFTER A SWEEP: delete the activated row (what
     the orphan sweep does) and a poll answers 404 again and never
     re-mints — the exact resurrection scenario this bug rode in on.
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

# hermetic DB, exactly like the other server-importing drives: the app
# module touches database on import, and this gate must never read or
# write the live jackpot ledger.
_tmp = Path(tempfile.mkdtemp(prefix="poll_nomint_"))
import database as db  # noqa: E402

db.DB_PATH = _tmp / "jackpot.db"
db.init_db()

import address_check as ac  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import server as srv_mod  # noqa: E402

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}" + (f"  ({why})" if why and not cond else ""))


def mint_mainnet_address(tag: int) -> str:
    """A deterministic, checksum-valid mainnet address (netbyte 0x12 +
    two 32-byte keys + keccak tail), registered in NO database. Built
    with the house decoder's own b58encode, so the shape gate can't
    call the fixture invalid."""
    payload = (bytes([0x12])
               + bytes([tag]) * 32
               + bytes([tag + 1]) * 32)
    checksum = ac.keccak256(payload)[:4]
    return ac.b58encode(payload + checksum)


def miner_count() -> int:
    with db.get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM miners").fetchone()[0]


client = TestClient(srv_mod.app)

UNKNOWN_A = mint_mainnet_address(0x41)   # 'A' keys
UNKNOWN_B = mint_mainnet_address(0x42)   # 'B' keys
ZOMBIE_STRING = "R111111111111111111111111111111111"   # the localStorage zombie

print("== 0. THE FIXTURES ARE REAL ==")
check(0.1, ac.is_valid(UNKNOWN_A) and ac.is_valid(UNKNOWN_B),
      "fixture addresses must be shape-valid (mainnet netbyte + checksum)")
check(0.2, UNKNOWN_A != UNKNOWN_B, "fixtures must be distinct wallets")
check(0.3, miner_count() == 0, f"fresh DB must start empty (got {miner_count()})")

print("== 1. THE POLLS ARE 404 ==")
for i, w in enumerate((UNKNOWN_A, UNKNOWN_A, UNKNOWN_B)):
    r = client.get(f"/api/miner/{w}")
    check(f"1.{i}", r.status_code == 404,
          f"poll {i} of {w[:12]}... -> {r.status_code}")

print("== 2. THE POLLS MINT NOTHING ==")
check(2.1, miner_count() == 0, f"table grew to {miner_count()} from GET polls alone")

print("== 3. THE 404 IS HONEST ABOUT WHY ==")
r = client.get(f"/api/miner/{UNKNOWN_A}")
detail = (r.json().get("detail") or "").lower() if r.headers.get(
    "content-type", "").startswith("application/json") else ""
check(3.1, "no miner" in detail and "yet" in detail,
      f"404 detail {detail!r} does not name the unregistered wallet")
check(3.2, "500" not in detail and "internal" not in detail,
      f"404 detail {detail!r} smells like a disguised server error")

print("== 4. JUNK STILL CAN'T MINT VIA THE POLL ==")
for i, junk in enumerate((
        ZOMBIE_STRING,                       # the localStorage zombie
        "PENDING_DEADBEEF",                  # the old row-junk shape
        "4" + "A" * 94,                      # malformed 95-char lookalike
        ("x" * 300),                         # absurd length, path route
)):
    r = client.get(f"/api/miner/{junk}")
    check(f"4.{i}", r.status_code == 404,
          f"junk {junk[:16]}... -> {r.status_code}")
check("4.n", miner_count() == 0, f"junk polls minted {miner_count()} row(s)")

print("== 5. ACTIVATION STILL MINTS ==")
r_reg = client.post("/api/miner/register", json={"wallet_address": UNKNOWN_B})
check(5.1, r_reg.status_code == 200 and r_reg.json().get("status") == "ok",
      f"activation -> {r_reg.status_code} {r_reg.text[:80]}")
r_get = client.get(f"/api/miner/{UNKNOWN_B}")
check(5.2, r_get.status_code == 200 and r_get.json().get("miner", {}).get("wallet_address") == UNKNOWN_B,
      f"registered wallet's own GET -> {r_get.status_code}")
r_other = client.get(f"/api/miner/{UNKNOWN_A}")
check(5.3, r_other.status_code == 404,
      f"unregistered sibling -> {r_other.status_code}")
check(5.4, miner_count() == 1, f"exactly one row after activation (got {miner_count()})")

print("== 6. THE ROW STAYS DEAD AFTER A SWEEP ==")
with db.get_db() as conn:
    conn.execute("DELETE FROM miners WHERE wallet_address = ?", (UNKNOWN_B,))
check(6.1, miner_count() == 0, "the sweep must actually remove the row")
for i, w in enumerate((UNKNOWN_B, UNKNOWN_B, UNKNOWN_A)):
    r = client.get(f"/api/miner/{w}")
    check(f"6.2.{i}", r.status_code == 404,
          f"post-sweep poll {i} -> {r.status_code}")
check(6.3, miner_count() == 0, f"post-sweep polls resurrected {miner_count()} row(s)")

n_fail = results.count(False)
print(f"\nRESULT: {len(results) - n_fail} passed, {n_fail} failed")
sys.exit(1 if n_fail else 0)
