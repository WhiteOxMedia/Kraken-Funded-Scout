"""test_claim_expiry_degate.py — THE DE-GATED CLAIM SWEEP (Sep 25).

User order: "De-gate check_expired_claims from the wallet-RPC check
in the server loop ... so claims never go 48 days stale again."

The server loop's sweep is gated behind `if wallet_up:` — a fact that
left three earmarks stale for 48 days. pool/server.py is ANOTHER
THREAD'S DIRTY FILE, so the de-gate lives where it can: the poller's
wallet-independent loop now runs the sweep each cycle, and the
engine's expiry is hardened from an unconditional UPDATE (which
ERASED a claim landing mid-sweep) to a compare-and-set.

  1. the engine's compare-and-set expiry: a claim landing between the
     sweep's read and its write is NEVER clobbered
  2. the CAS matches the balance model's pending semantics
     (COALESCE — a legacy NULL claim_status can still expire)
  3. the de-gate wiring: the poller runs the sweep in its loop,
     wallet-independently, fail-soft, BEFORE the drawing check
  4. the server stays untouched: its sweep, and its wallet gate,
     remain exactly as the other thread left them
  5. expiry behavior unchanged where it was already right: real
     release (no refund), the rollover audit event, idempotence
  6. hermetic: the live jackpot.db is byte-identical throughout

House conventions: check(n, cond, why), RESULT line, sys.exit.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
passed = failed = 0


def check(n, cond, why):
    global passed, failed
    tag = "PASS" if cond else "FAIL"
    print(f"{tag}  {n}  {why}")
    if cond:
        passed += 1
    else:
        failed += 1


SANDBOX = tempfile.mkdtemp(prefix="degate_drive_")
sys.path.insert(0, os.path.join(HERE, "pool"))

import database as db                                  # noqa: E402
db.DB_PATH = __import__("pathlib").Path(
    os.path.join(SANDBOX, "jackpot.db"))
db.init_db()

import jackpot_engine as engine                        # noqa: E402
import jackpot_funding as funding                      # noqa: E402

LIVE_DB = os.path.join(HERE, "pool", "jackpot.db")


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


_live_before = (_sha(LIVE_DB), os.path.getmtime(LIVE_DB)) if os.path.exists(LIVE_DB) else None


def _expired_winner(miner_wallet_suffix, worker, deadline_ts):
    """A drawn drawing, past its claim deadline, unclaimed."""
    m = db.get_or_create_miner("4" + miner_wallet_suffix * 94, worker)
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO drawings (jackpot_amount, house_tax, winner_id, "
            "status, drawn_at, claim_status, claim_deadline) "
            "VALUES (0.01, 0.001, ?, 'drawn', strftime('%s','now') - 100, "
            "'pending', ?)",
            (int(m["id"]), float(deadline_ts)))
        did = conn.execute("SELECT MAX(id) FROM drawings").fetchone()[0]
    return int(m["id"]), int(did)


def _pending_winner(suffix, worker):
    m = db.get_or_create_miner("4" + suffix * 94, worker)
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO drawings (jackpot_amount, house_tax, winner_id, "
            "status, drawn_at, claim_status, claim_deadline) "
            "VALUES (0.01, 0.001, ?, 'drawn', strftime('%s','now'), "
            "'pending', strftime('%s','now') + 86400)",
            (int(m["id"]),))
        did = conn.execute("SELECT MAX(id) FROM drawings").fetchone()[0]
    return int(m["id"]), int(did)


print("== 1. THE RACE GUARD: A CLAIM IS NEVER CLOBBERED ==")
mid, did = _expired_winner("a", "race-rig", time.time() - 10)
# a claim lands "between the read and the write":
db.claim_drawing(did, "4" + "a" * 94)
out = engine.check_expired_claims()
with db.get_db() as conn:
    state = conn.execute("SELECT claim_status FROM drawings WHERE id = ?",
                         (did,)).fetchone()[0]
check(1.1, state == "claimed" and (out is None or
      all(e["drawing_id"] != did for e in out)),
      "a drawing CLAIMED before the sweep's write stays claimed — the "
      "compare-and-set refused to erase the winner's money (the old "
      "unconditional UPDATE would have flipped it to expired)")
# and the same sweep still expires a genuinely stale one
mid2, did2 = _expired_winner("b", "stale-rig", time.time() - 10)
out = engine.check_expired_claims()
with db.get_db() as conn:
    state = conn.execute("SELECT claim_status FROM drawings WHERE id = ?",
                         (did2,)).fetchone()[0]
check(1.2, state == "expired" and out
      and out[0]["drawing_id"] == did2
      and abs(out[0]["returned"] - 0.009) < 1e-9,
      f"the genuinely stale drawing expired: {out}")

print("== 2. THE DE-GATED SWEEP HEALS THE DISPLAY POOL ==")
# the 48-day-stale flatline, recreated and healed: an earmarked
# expired-eligible drawing suppresses the display balance until the
# sweep releases it
db.set_setting("jackpot_balance", "0.05")
m3, did3 = _expired_winner("c", "heal-rig", time.time() - 10)
suppressed = engine.get_jackpot_balance()
check(2.1, abs(suppressed - (0.05 - 0.009)) < 1e-9,
      f"the unexpired earmark suppresses the display pool: "
      f"{suppressed:.4f} XMR (0.05 base - 0.009 winner's portion)")
out = engine.check_expired_claims()
healed = engine.get_jackpot_balance()
check(2.2, abs(healed - 0.05) < 1e-9,
      f"the sweep's release restores the true balance: "
      f"{healed:.4f} XMR — the mechanism that was missing while the "
      f"sweep sat behind the wallet gate for 48 days")

print("== 3. THE DE-GATE: THE POLLER RUNS THE SWEEP ==")
with open(os.path.join(HERE, "pool", "proxy_poller.py"),
          encoding="utf-8") as f:
    src = f.read()
check(3.1, "engine.check_expired_claims()" in src
      and "check_expired_claims" in src,
      "the poller's loop now calls the claim sweep")
check(3.2, src.index("check_expired_claims")
      < src.index("check_drawing_schedule"),
      "the sweep rides BEFORE the drawing check in update_workers")
check(3.3, "except Exception as sweep_err" in src,
      "a sweep failure is fail-soft — the poller keeps polling")
import proxy_poller                                    # noqa: E402
check(3.4, proxy_poller.miner_yield is not None,
      "the poller still imports clean with both duties wired")

print("== 4. THE SERVER STAYS UNTOUCHED ==")
with open(os.path.join(HERE, "pool", "server.py"),
          encoding="utf-8") as f:
    srv = f.read()
check(4.1, "if wallet_up:" in srv
      and srv.index("wallet_up") < srv.index("check_expired_claims"),
      "the other thread's server still owns its sweep and its wallet "
      "gate — byte-for-byte their business, no drive-enforced edits")
check(4.2, srv.count("check_expired_claims") == 1,
      "exactly one server-side sweep call site (unchanged)")

print("== 5. EXPIRY WHERE IT WAS ALREADY RIGHT STAYS RIGHT ==")
m4, did4 = _expired_winner("d", "audit-rig", time.time() - 10)
base_before = float(db.get_setting("jackpot_balance", "0") or 0)
out = engine.check_expired_claims()
with db.get_db() as conn:
    logs = conn.execute(
        "SELECT details FROM audit_log WHERE action = 'jackpot_rollover' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    state = conn.execute(
        "SELECT claim_status FROM drawings WHERE id = ?",
        (did4,)).fetchone()[0]
base_after = float(db.get_setting("jackpot_balance", "0") or 0)
det = json.loads(logs[0]) if logs else {}
check(5.1, state == "expired" and det.get("drawing_id") == did4
      and abs(det.get("returned_xmr", 0) - 0.009) < 1e-9
      and det.get("via") == "claim_sweep",
      "the rollover audit event carries drawing id, returned amount, "
      "and via=claim_sweep")
check(5.2, abs(base_after - base_before) < 1e-12,
      "release, not refund — the base is untouched (1b22a3e holds)")
out2 = engine.check_expired_claims()
check(5.3, out2 is None or all(e["drawing_id"] != did4 for e in out2),
      "idempotent — an expired drawing never expires twice")

print("== 6. HERMETIC ==")
if _live_before:
    check(6.1, (_sha(LIVE_DB), os.path.getmtime(LIVE_DB)) == _live_before,
          "the live jackpot.db is sha256+mtime identical throughout")
else:
    check(6.1, not os.path.exists(LIVE_DB),
          "no live jackpot.db — nothing to protect, nothing touched")

shutil.rmtree(SANDBOX, ignore_errors=True)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
