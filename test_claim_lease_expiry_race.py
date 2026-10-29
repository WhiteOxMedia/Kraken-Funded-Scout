"""test_claim_lease_expiry_race.py — THE SWEEP vs THE CLAIM LEASE (Oct 28).

TWO writers used to be able to throw state at a lease-holding drawing:

  the sweep           check_expired_claims — its write is a CAS on
                      'pending', so a 'claiming' row is invisible twice:
                      its READ (the query filters claim_status =
                      'pending') and its WRITE (the update's
                      precondition) both miss it. That holdover is the
                      GOOD half and stays.
  the deadline branch process_winner_claim's own expire path — it used
                      to run db.expire_drawing's UNCONDITIONAL UPDATE
                      before any lease attempt, clobbering a 'claiming'
                      row under a live lease.
  the primitives      db.expire_drawing used to accept any row; the
                      claim flip used to write straight through.

WHAT THIS DRIVE SETTLES now that the flips are lease-bearer (Oct 28):

  1. CONTROL: with the window still OPEN, the lease turns a late claim
     away ("in flight") and stays intact — the gate is real, so any
     red row in 2/3 is about expiry, not the gate.
  2. THE SWEEP DIRECTION: sweep a leased ('claiming') drawing whose
     deadline is long PAST, next to a pending witness with the same
     past deadline. Expected: the witness expires (the sweep provably
     ran), the leased drawing stays 'claiming' with no rollover —
     the claimant mid-flight is not erased by the pool's own timer.
  3. THE DEADLINE-BRANCH CONTRACT: a late claim arriving PAST the
     window over a LIVE lease is turned away at the lease; over a DEAD
     lease it steal-leases and then the post-lease window re-check
     expires the drawing FROM THE HOLDER'S SEAT — the only write
     allowed to move a 'claiming' row (via expire_leased_drawing).
  4. THE PRIMITIVES: db.expire_drawing CASes 'pending' only;
     db.expire_leased_drawing flips a leased row only; db.claim_drawing
     flips a lease-held row only. Every wrong-state call matches zero
     rows and returns False.

THE CONTRACT ROWS ARE THE ENFORCED STATE, not a wish: when any of them
turns red, a writer gained a path around the lease precondition — that
is the deliberate alarm, not a regression to suppress.

HERMETIC: sandbox db, live rate feeds kill-switched before imports,
wallet RPC stubbed into a hard error — no network, no money movement.
This drive claims a plain XMR winner (the deadline path exits before
any coin branch), so no rate state is even touched.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# KILL BOTH LIVE FEEDS before these modules exist, as the VRSC drives do.
os.environ["XMR_PRICE_LIVE"] = "0"
os.environ["VRSC_PRICE_LIVE"] = "0"

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import database as db                                  # noqa: E402
db.DB_PATH = Path(tempfile.mkdtemp(prefix="lease_expiry_")) / "jackpot.db"
db.init_db()
db.set_setting("jackpot_balance", "5.0")               # cover for any mint

import jackpot_engine as engine                        # noqa: E402
import monero_wallet as xmw                            # noqa: E402


# KILL-SAFE: no wallet-rpc in a drive (the XMR branch's standing
# manual leg is the only reachable outcome, and only if a mint ran).
def _fake_rpc(method, params=None):
    return {"error": {"message": "sandbox: no wallet-rpc in a drive"}}


xmw._rpc = _fake_rpc
xmw.rpc_available = lambda: False

results = []
FX_N = [0]


def check(name, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(name)
          + ("  " + str(msg) if msg else ""))


def _fixture(deadline_delta):
    """One fresh XMR miner + one drawn drawing at a chosen deadline.
    Fresh miner per fixture so the 30-day win-limit gate stages no
    unrelated refusal on the claimant."""
    FX_N[0] += 1
    n = FX_N[0]
    addr = "4LEA%02d" % n + "c" * 91
    m = db.get_or_create_miner(addr, "rig-le-%d" % n)
    with db.get_db() as conn:
        conn.execute(
            "UPDATE miners SET coin = 'xmr' WHERE wallet_address = ?",
            (addr,))
        d_dead = time.time() + deadline_delta
        cur = conn.execute(
            "INSERT INTO drawings (jackpot_amount, house_tax, status, "
            "winner_id, drawn_at, claim_deadline) "
            "VALUES (0.5, 0.05, 'drawn', ?, ?, ?)",
            (m["id"], time.time(), d_dead))
    return cur.lastrowid, addr


def _status(did):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT claim_status FROM drawings WHERE id = ?", (did,)).fetchone()
        return row["claim_status"] if row else None


def _leased_at(did):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT claim_leased_at FROM drawings WHERE id = ?",
            (did,)).fetchone()
        return row["claim_leased_at"] if row else None


def _rollover_for(did):
    """Rollover audit rows naming THIS drawing (JSON-parsed, not LIKE)."""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT details FROM audit_log WHERE action = 'jackpot_rollover'"
        ).fetchall()
    hits = 0
    for r in rows:
        try:
            if r["details"] and json.loads(r["details"]).get(
                    "drawing_id") == did:
                hits += 1
        except Exception:
            pass
    return hits


def _payout_rows(did):
    with db.get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM payouts WHERE drawing_id = ?",
            (did,)).fetchone()["c"]


# ══ 1. CONTROL: the lease gate is real while the window is open ═════
_d1, _a1 = _fixture(deadline_delta=+3600)
assert db.acquire_claim_lease(_d1)
_r1 = engine.process_winner_claim(_d1, _a1)
check("1.1 the lease turns a window-open claim away",
      _r1.get("status") == "error" and "in flight" in (_r1.get("reason") or ""),
      _r1)
check("1.2 the rejected claim left the lease intact",
      _status(_d1) == "claiming", _status(_d1))
check("1.3 the control claim wrote no rollover", _rollover_for(_d1) == 0)

# ══ 2. THE SWEEP DIRECTION: a claiming drawing is invisible twice ═══
_s, _sa = _fixture(deadline_delta=-10)                 # leased, overdue
assert db.acquire_claim_lease(_s)
_w, _wa = _fixture(deadline_delta=-10)                 # pending witness
_res2 = engine.check_expired_claims()
_ids2 = [e["drawing_id"] for e in _res2] if _res2 else []
check("2.1 the sweep ran and expired the pending witness",
      _w in _ids2, _ids2)
check("2.2 the sweep left the leased drawing untouched",
      _status(_s) == "claiming", _status(_s))
check("2.3 no rollover was written for the leased drawing",
      _rollover_for(_s) == 0)
check("2.4 the sweep wrote exactly the witness rollover",
      _rollover_for(_w) == 1)

# ══ 3. THE DEADLINE-BRANCH CONTRACT: the lease outranks the clock ════
_t, _ta = _fixture(deadline_delta=-10)                 # leased, overdue
assert db.acquire_claim_lease(_t)
_r3 = engine.process_winner_claim(_t, _ta)
check("3.1 the deadline path refuses to clobber a live lease: the "
      "pre-lease CAS leaves the 'claiming' row exactly as the holder "
      "left it",
      _status(_t) == "claiming", _status(_t))
check("3.2 the late arrival is turned away at the lease — the pool's "
      "rollover never got written over the holder's flight",
      "in flight" in (_r3.get("reason") or "") and _rollover_for(_t) == 0,
      _r3)
check("3.3 the refused claim reached no mint (no payout row)",
      _payout_rows(_t) == 0)
check("3.4 the lease stamp survives the refusal intact",
      _leased_at(_t) is not None, _leased_at(_t))

# ══ 3b. THE DEAD-LEASE HAND-OFF: the one honest recovery ════════════
# A claimant that died mid-flight is recovered by the steal window —
# and the new owner re-checks the window AS THE HOLDER, retiring the
# drawing in its own name (expire_leased_drawing) instead of minting
# a prize on a window the pool already shut.
_dd, _da = _fixture(deadline_delta=-10)                # dead lease, overdue
assert db.acquire_claim_lease(_dd)
with db.get_db() as conn:
    conn.execute(
        "UPDATE drawings SET claim_leased_at = ? WHERE id = ?",
        (time.time() - 400, _dd))       # older than the 300 s steal window
_r3b = engine.process_winner_claim(_dd, _da)
check("3.5 a late claim over a DEAD lease steals, then expires in the "
      "holder's name — the 'claiming' row is retired by the only "
      "writer allowed to touch it",
      _status(_dd) == "expired" and _leased_at(_dd) is None,
      (_status(_dd), _leased_at(_dd)))
check("3.6 the holder's expiry wrote the pool's rollover for this "
      "drawing exactly once",
      _rollover_for(_dd) == 1, _rollover_for(_dd))
check("3.7 the stolen flight reached no mint (no payout row)",
      _payout_rows(_dd) == 0, _payout_rows(_dd))
check("3.8 the stealer was refused honestly at the window, not at a "
      "clobber",
      _r3b.get("status") == "error"
      and "window expired" in (_r3b.get("reason") or ""), _r3b)

# ══ 4. THE PRIMITIVES: every flip carries its precondition ═══════════
_e1, _ = _fixture(deadline_delta=+3600)                # leased, window OPEN
assert db.acquire_claim_lease(_e1)
check("4.1 EXPIRY PRIMITIVE: db.expire_drawing refuses a 'claiming' "
      "row no matter the window — a raw caller cannot clobber the "
      "lease holder's row",
      db.expire_drawing(_e1) is False and _status(_e1) == "claiming",
      _status(_e1))

_e2, _ = _fixture(deadline_delta=+3600)                # plain pending
check("4.2 the expiry primitive still expires an un-leased 'pending' "
      "row (the sweep's CAS is intact, now rowcount-driven)",
      db.expire_drawing(_e2) is True and _status(_e2) == "expired",
      _status(_e2))

_e3, _ = _fixture(deadline_delta=+3600)                # leased
assert db.acquire_claim_lease(_e3)
check("4.3 THE HOLDER'S ROUTE: expire_leased_drawing flips the leased "
      "'claiming' row and spends the lease stamp",
      db.expire_leased_drawing(_e3) is True
      and _status(_e3) == "expired" and _leased_at(_e3) is None,
      (_status(_e3), _leased_at(_e3)))

_e4, _ = _fixture(deadline_delta=+3600)                # pending, no lease
check("4.4 EXPIRY PRIMITIVE: expire_leased_drawing without the lease "
      "matches zero rows — no lease, no rollover",
      db.expire_leased_drawing(_e4) is False and _status(_e4) == "pending",
      _status(_e4))

_e5, _ = _fixture(deadline_delta=+3600)                # pending, no lease
check("4.5 CLAIM PRIMITIVE: claim_drawing from 'pending' does nothing "
      "— the flip requires the lease, whatever wallet carries it",
      db.claim_drawing(_e5, "4aaa" + "c" * 91) is False
      and _status(_e5) == "pending",
      _status(_e5))

_e6, _ = _fixture(deadline_delta=+3600)                # leased
assert db.acquire_claim_lease(_e6)
check("4.6 the lease holder flips 'claiming' -> 'claimed' and the "
      "lease is spent",
      db.claim_drawing(_e6, _a1) is True and _status(_e6) == "claimed"
      and _leased_at(_e6) is None,
      (_status(_e6), _leased_at(_e6)))

print("---")
print("RESULT: %d passed, %d failed" % (sum(results), results.count(False)))
sys.exit(0 if all(results) else 1)
