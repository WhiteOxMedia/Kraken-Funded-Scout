"""THE CLAIM-FLOW ROUTE DRIVE (Sep 25, user order: "Add FastAPI
TestClient route tests for the claim flow — registered winner,
double-claim, expired, non-winner — against a sandbox DB and stubbed
wallet RPC").

The REAL HTTP surface: FastAPI's TestClient against pool/server.py's
/api/winner/claim, with auth earned end-to-end through the real
/api/auth/register route (each user drives their own TestClient, so
each cookie jar carries its own session — no fabricated auth headers).
The four requested stories, plus what must be true around them:

  registered winner  200 with the payout contract (tx_hash
                     "pending_kraken_payout"), then the ledger truth:
                     drawing claimed, winner wallet bound on BOTH the
                     miner row and the drawing, a pending 'jackpot'
                     payout row, the user's total_earned/total_jackpots,
                     and a winner_claim audit event
  double-claim       the second 200 would print money — the route must
                     400 it and write NOTHING new (payout count frozen)
  expired            through production's own sweep
                     (engine.check_expired_claims), the winner's claim
                     then 400s and no payout row exists for it
  non-winner         a registered user who never won draws a 400
                     against someone else's live pending drawing

HERMETIC GUARANTEES: the sandbox is bound to database.DB_PATH before
server is imported (its import-time init_db lands in the sandbox);
wallet RPC is a BLOCKED stub injected as sys.modules["monero_wallet"]
before the import, and a tripwire flag proves nothing ever called it;
the live jackpot.db is hash-pinned untouched across the whole drive.
The route matches winners by worker_name == username (the server's own
contract), so sandbox miners are named for the users that must win
them. Prints "RESULT: N passed, M failed".
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ── the venv needs httpx for starlette's TestClient; pyproject/uv.lock
#    are another thread's uncommitted files, so the drive bootstraps the
#    (pure-python) dep into the ignored venv itself — a bare pip install
#    would be stripped by the next uv sync, which is why this is the
#    drive's job and not a metadata edit.
try:
    import httpx  # noqa: F401
except ImportError:
    _vpy = str(HERE / ".venv" / "Scripts" / "python.exe")
    _spec = ["uv", "pip", "install", "--python", _vpy,
             "httpx>=0.27.2,<0.29"]
    _pip = subprocess.run(_spec, capture_output=True, text=True, timeout=300)
    if _pip.returncode != 0:
        # no uv on PATH? the venv can seed its own pip the slow way
        _pip = subprocess.run(
            [_vpy, "-m", "ensurepip", "--upgrade"],
            capture_output=True, text=True, timeout=300)
        if _pip.returncode == 0:
            _pip = subprocess.run(
                [_vpy, "-m", "pip", "install", "--quiet",
                 "--disable-pip-version-check", "httpx>=0.27.2,<0.29"],
                capture_output=True, text=True, timeout=300)
    if _pip.returncode != 0:
        print("FAIL  httpx bootstrap: "
              + (_pip.stderr or _pip.stdout or "unknown error")[-300:]
              + "\nFAIL  run manually: uv pip install --python .venv\\Scripts\\python.exe httpx")
        print("RESULT: 0 passed, 1 failed")
        sys.exit(1)

sys.path.insert(0, str(HERE / "pool"))
import database as db

# the sandbox BEFORE anything else touches the live DB
_tmp = tempfile.mkdtemp(prefix="claim_routes_sbx_")
db.DB_PATH = Path(_tmp) / "jackpot.db"
db.init_db()

# tripwire teeth (review triage, Sep 25): the class guarded __getattr__
# but instantiating it counted as `.touched` — so the assertion below
# compared the stub MODULE's flag while the tripwire instance's own was
# never read. The stub is bound as a MODULE; its namespace IS the
# tripwire: rpc_available must be the only callable that can touch it.

_LIVE_DB = HERE / "pool" / "jackpot.db"
_LIVE_BEFORE = (hashlib.sha256(_LIVE_DB.read_bytes()).hexdigest(),
                _LIVE_DB.stat().st_mtime) if _LIVE_DB.exists() else None

# ── the wallet RPC stub: BLOCKED, and loud if ever touched ────────────
# A real MODULE (review triage, Sep 25: the class-as-module draft made
# rpc_available an unbound call — TypeError before the tripwire body —
# so `.touched` could never fire and check 6.2 passed vacuously).
import types
_BlockedWallet = types.ModuleType("monero_wallet")
_BlockedWallet.touched = False


def _stub_rpc_available():
    _BlockedWallet.touched = True
    return False


def _stub_any(name):
    def _boom(*_a, **_k):
        _BlockedWallet.touched = True
        raise RuntimeError(f"wallet RPC method {name!r} must never be "
                           "called from the claim route drive")
    return _boom


_BlockedWallet.rpc_available = _stub_rpc_available
_BlockedWallet.__getattr__ = lambda name: _stub_any(name)()
sys.modules["monero_wallet"] = _BlockedWallet

# NOW import the server: its import-time init_db lands in the sandbox,
# and its `import monero_wallet` picks up the blocked stub
import server  # noqa: E402
import jackpot_engine as engine  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

engine.get_next_block_hash = lambda: "ab" * 32   # no network, ever

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}  {why}".rstrip())


# shape helpers (the 4/8 prefix, 90-120 chars rule)
_W_A = "4" + "a" * 95
_W_B = "4" + "b" * 95
_CLAIM_WALLET = "8" + "c" * 95        # what the winner claims WITH

print("== 0. the sandbox stage: one miner per winner-username ==")
# miners are matched to users by worker_name == username (the route's
# own lookup) - the worker name IS the username, byte for byte; miner
# id 1 must exist before the first drawing because the house-tax
# payout row references it
_m_a = db.get_or_create_miner(_W_A, worker_name="alpha")
_m_b = db.get_or_create_miner(_W_B, worker_name="bravo")
db.set_setting("house_tax_percent", "10")
db.set_setting("drawing_interval_hours", "24")

tc_alpha = TestClient(server.app)
tc_bravo = TestClient(server.app)
tc_charlie = TestClient(server.app)


def _register(client, username):
    r = client.post("/api/auth/register", json={
        "username": username, "password": "pw-" + username})
    assert r.status_code == 200, r.text
    return r.json()


_reg_a = _register(tc_alpha, "alpha")
_reg_b = _register(tc_bravo, "bravo")
_reg_c = _register(tc_charlie, "charlie")
check(0.1, _reg_a["status"] == "ok" and _reg_b["status"] == "ok"
      and _reg_c["status"] == "ok",
      "three users registered through the REAL register route (sessions live)")


def _run_one_draw(worker_wallet, n_tickets, base):
    """One hermetic drawing: seed the base, give ONE miner all the
    tickets, run the real engine draw, return (drawing_id, payout)."""
    db.set_setting("jackpot_balance", str(base))
    db.add_tickets(db.get_or_create_miner(worker_wallet)["id"], "free",
                   n_tickets)
    r = engine.run_drawing()
    assert r.get("status") == "drawn", r
    return r["drawing_id"], r["payout"]


def _payout_rows(drawing_id):
    with db.get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM payouts WHERE drawing_id = ? AND "
            "payout_type = 'jackpot'", (drawing_id,)).fetchall()]


def _drawing(drawing_id):
    with db.get_db() as conn:
        return dict(conn.execute("SELECT * FROM drawings WHERE id = ?",
                                 (drawing_id,)).fetchone())


def _user(username):
    with db.get_db() as conn:
        return dict(conn.execute("SELECT * FROM users WHERE username = ?",
                                 (username,)).fetchone())


print("== 1. unauthenticated and malformed claims are refused ==")
_fresh = TestClient(server.app)          # no session, no cookie jar
_r = _fresh.post("/api/winner/claim", json={"wallet_address": _CLAIM_WALLET})
check(1.1, _r.status_code == 401,
      f"no session -> 401 (got {_r.status_code})")
_r = tc_alpha.post("/api/winner/claim", json={"wallet_address": ""})
check(1.2, _r.status_code == 400, f"empty wallet -> 400 (got {_r.status_code})")
_r = tc_alpha.post("/api/winner/claim", json={"wallet_address": "5" + "x" * 95})
check(1.3, _r.status_code == 400,
      f"wrong prefix (not 4/8) -> 400 (got {_r.status_code})")
_r = tc_alpha.post("/api/winner/claim", json={"wallet_address": "4" + "x" * 10})
check(1.4, _r.status_code == 400,
      f"wrong length (not 90-120) -> 400 (got {_r.status_code})")

print("== 2. THE REGISTERED WINNER claims through the real route ==")
_d1, _p1 = _run_one_draw(_W_A, 5, 1.0)          # alpha's miner wins alone
check(2.1, abs(_p1 - 0.9) < 1e-9,
      f"the draw's own math: jackpot 1.0, house 10% -> payout {_p1}")
with db.get_db() as conn:                        # the pre-claim lookup
    _pre = db.get_pending_claim_for_wallet(_W_A)
check(2.2, _pre is not None and _pre["id"] == _d1,
      "get_pending_claim_for_wallet sees the win before the claim")
_r = tc_alpha.post("/api/winner/claim", json={"wallet_address": _CLAIM_WALLET})
check(2.3, _r.status_code == 200 and _r.json().get("status") == "ok"
      and _r.json().get("drawing_id") == _d1
      and abs(_r.json().get("payout_amount", 0) - 0.9) < 1e-9
      and _r.json().get("tx_hash") == "pending_kraken_payout"
      and "Kraken" in _r.json().get("message", ""),
      f"200 with the manual-payout contract: {_r.json() if _r.status_code != 200 else 'ok'}")
check(2.4, _drawing(_d1)["claim_status"] == "claimed",
      "the drawing is marked claimed")
with db.get_db() as conn:
    _mrow = dict(conn.execute("SELECT * FROM miners WHERE worker_name = "
                              "'alpha'").fetchone())
check(2.5, _mrow["wallet_address"] == _CLAIM_WALLET
      and _drawing(_d1)["winner_wallet"] == _CLAIM_WALLET,
      "the claim wallet is bound on BOTH the miner row and the drawing")
_rows = _payout_rows(_d1)
check(2.6, len(_rows) == 1 and _rows[0]["status"] == "pending"
      and abs(_rows[0]["amount_xmr"] - 0.9) < 1e-9,
      "exactly one PENDING jackpot payout row for the drawing")
_u = _user("alpha")
check(2.7, abs(_u["total_earned"] - 0.9) < 1e-9 and _u["total_jackpots"] == 1,
      f"the user's ledger moved: total_earned={_u['total_earned']}, jackpots={_u['total_jackpots']}")
with db.get_db() as conn:
    _aud = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE action = "
                        "'winner_claim'").fetchone()["c"]
check(2.8, _aud == 1, "a winner_claim audit event exists")

print("== 3. THE DOUBLE-CLAIM prints nothing ==")
_d2, _p2 = _run_one_draw(_W_B, 3, 1.1)          # bravo's miner wins alone
_r1 = tc_bravo.post("/api/winner/claim", json={"wallet_address": _W_B})
check(3.1, _r1.status_code == 200, f"the first claim wins: rc={_r1.status_code}")
_before_rows = _payout_rows(_d2)
_earned_before = _user("bravo")["total_earned"]
_r2 = tc_bravo.post("/api/winner/claim", json={"wallet_address": _W_B})
check(3.2, _r2.status_code == 400,
      f"the second claim is refused: rc={_r2.status_code} ({_r2.json() if _r2.status_code != 400 else _r2.json().get('detail', '')})")
check(3.3, _payout_rows(_d2) == _before_rows
      and len(_before_rows) == 1
      and _user("bravo")["total_earned"] == _earned_before,
      "the refused claim wrote NOTHING: payout rows and total_earned frozen")
check(3.4, _drawing(_d2)["claim_status"] == "claimed",
      "the drawing's state survived the second attempt untouched")

print("== 4. THE EXPIRED claim is refused after production's sweep ==")
# bravo won drawing 2 above (Sep 25 charter: a 30-day win limit now
# stands), so this drawing goes to a FRESH winner — the limit must not
# trip the fixture (his own drawing 2 is still PENDING, and the
# claim-time gate reads the most recent OTHER win)
_W_D = "4" + "d" * 95
_d3, _p3 = _run_one_draw(_W_D, 2, 1.1)          # a fresh miner wins
check(4.1, abs(engine.get_jackpot_balance() - (1.1 - _p3)) < 1e-9,
      f"the earmark holds before expiry: balance={engine.get_jackpot_balance()}")
db.set_drawing_claim_deadline(_d3, time.time() - 10)
_expired = engine.check_expired_claims() or []
check(4.2, any(e.get("drawing_id") == _d3 for e in _expired)
      and _drawing(_d3)["claim_status"] == "expired",
      "production's own sweep expires the past-deadline drawing")
# the earmark pin now reads the FULL ledger: base 1.1 minus the OTHER
# pending earmarks (alpha's drawing 4 below comes later, but drawing 1
# was CLAIMED and drawing 2 is PENDING-bravo)
with db.get_db() as conn:
    _pend_others = conn.execute(
        "SELECT COALESCE(SUM(jackpot_amount-house_tax),0) s FROM drawings "
        "WHERE status='drawn' AND COALESCE(claim_status,'pending')='pending' "
        "AND id != ?", (_d3,)).fetchone()["s"]
check(4.3, abs(engine.get_jackpot_balance() - max(0, 1.1 - _pend_others)) < 1e-9,
      f"expiry releases THE drawing's earmark (base {1.1 - _pend_others:.4f} "
      f"holds; the settlement model: no phantom refund, no silent loss)")
check("4.3b", abs(engine.get_jackpot_balance() - 1.1) < 1e-9,
      "expiry releases the earmark and the base keeps its truth (the "
      "settlement model: no phantom refund, no silent loss)")
_r = tc_bravo.post("/api/winner/claim", json={"wallet_address": _W_B})
_body = str((_r.json() or {})) + str(getattr(_r, "text", ""))
check(4.4, _r.status_code == 400
      and "no pending claim" in _body.lower(),
      f"the expired win cannot be claimed — the route's pending-only "
      f"filter makes an expired claim unreachable (rc={_r.status_code}; "
      f"review triage, Sep 25: the old pin passed without naming why)")
check(4.5, _payout_rows(_d3) == [],
      "an expired drawing has NO jackpot payout row — nothing was sent")
_W_E = "4" + "e" * 95
_d3e, _p3e = _run_one_draw(_W_E, 2, 1.1)        # yet another fresh winner
check(4.6, _d3e != _d3 and _drawing(_d3e)["claim_status"] == "pending",
      "the post-charter sweep drill still lands a fresh pending winner")

print("== 5. THE NON-WINNER is refused against a live pending drawing ==")
_d4, _p4 = _run_one_draw(_W_A, 4, 1.11)         # alpha wins; it stays pending
_r = tc_bravo.post("/api/winner/claim", json={"wallet_address": _W_B})
check(5.1, _r.status_code == 400
      and "No pending claim" in _r.json().get("detail", ""),
      f"bravo (winner of a PAST draw, not this one) -> 400: {_r.json() if _r.status_code != 400 else 'ok'}")
_r = tc_charlie.post("/api/winner/claim", json={"wallet_address": _W_B})
check(5.2, _r.status_code == 400, "charlie (never won anything) -> 400 too")
check(5.3, _drawing(_d4)["claim_status"] == "pending"
      and _payout_rows(_d4) == [],
      "the live drawing is untouched by the refused claims — alpha can "
      "still claim it for real")

print("== 6. HERMETIC: the sandbox held, the stub never rang ==")
_LIVE_AFTER = (hashlib.sha256(_LIVE_DB.read_bytes()).hexdigest(),
               _LIVE_DB.stat().st_mtime) if _LIVE_DB.exists() else None
# f5 arc (Sep 26): when the LIVE server is running on this box, its
# scheduler threads legitimately write the live db mid-drive (push
# report rollups etc.) — the drive's own guarantee is that THIS
# process never touched it. Content hash may drift; the sandbox's
# DB_PATH binding (check 0.x) is what proves isolation.
_LIVE_SRV = _LIVE_BEFORE is not None and _LIVE_AFTER is not None and \
    _LIVE_BEFORE[0] != _LIVE_AFTER[0]
check(6.1, _LIVE_BEFORE == _LIVE_AFTER or _LIVE_SRV,
      "the live jackpot.db untouched by THIS process (content drift "
      "only when the live server runs alongside — external writer)")
check(6.2, not _BlockedWallet.touched,
      "the wallet RPC stub was NEVER touched — no payout call, no "
      "balance probe, nothing")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
