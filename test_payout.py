"""test_payout.py - THE HOUSE-TAX PAYOUT, RESULT-STYLE (Sep 26).

REWRITTEN from a scenario script to a hermetic drive. The old version
imported database with NO sandbox rebind and SET the live jackpot.db's
house_tax_accumulated / threshold / deposit address on every run — a
drive writing production money state, masked for weeks by exit-0
grading. (Found while promoting it: the live accumulator read 0.003 —
a leftover scenario value, not a real accrual.)

The contract pinned (engine.check_and_send_house_tax):

  1. below threshold     -> None; accumulator UNTOUCHED; no payout row
  2. at threshold, RPC offline (rpc_available False)
     -> "pending_manual" transfer path: a payout row with status
        'pending', accumulator NOT zeroed (only a REAL send zeroes it)
  3. RPC up, transfer ok -> tx recorded, accumulator ZEROED (the money
     moved), payout row status 'sent', destination = the SETTINGS
     address (the operator's, not a hardcoded one)
  4. RPC answers an error -> action 'failed', the error surfaces,
     accumulator NOT zeroed, NO payout row (nothing moved)
  5. RPC raises          -> action 'failed', same no-side-effect rule
  6. THE MATH            payout = accumulated - fee_buffer, floored at
     the Kraken minimum 0.0001; threshold boundary: accumulated ==
     threshold pays (>=, not >)
  7. zeroing idempotence  a second call after a successful send is a
     below-threshold no-op (accumulator is 0)

The RPC layer is monkeypatched (rpc_available / _rpc on the shared
monero_wallet module object — the engine imports the same sys.modules
entry). Hermetic: sandbox DB_PATH, no network, no live jackpot.db.
Prints "RESULT: N passed, M failed".
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

_tmp = Path(tempfile.mkdtemp(prefix="payout_drive_"))

import database as db                              # noqa: E402
db.DB_PATH = _tmp / "jackpot.db"
db.init_db()

import monero_wallet as xmr                        # noqa: E402
import jackpot_engine as engine                    # noqa: E402

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


# the drive's own hermeticity: the binding IS the sandbox
check(0.1, db.DB_PATH == _tmp / "jackpot.db",
      "sandbox DB binding (this drive never touches the live jackpot.db)")

ADDR = "4" + "7" * 94               # a test-shaped deposit address

# the RPC fakes — installed per section; the engine's in-function
# `import monero_wallet` resolves THIS module object
_state = {"available": False, "reply": None, "raise": None, "calls": []}


def _rpc_available():
    return _state["available"]


def _rpc(method, params):
    _state["calls"].append((method, params))
    if _state["raise"]:
        raise _state["raise"]
    return _state["reply"]


xmr.rpc_available = _rpc_available
xmr._rpc = _rpc


def _payout_rows():
    with db.get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM payouts WHERE payout_type='house_tax' "
            "ORDER BY id")]


def _accum():
    return float(db.get_setting("house_tax_accumulated", "0"))


print("== 1. BELOW THRESHOLD: a clean no-op ==")
db.set_setting("payout_threshold_xmr", "0.01")
db.set_setting("transfer_fee_buffer_xmr", "0.0001")
db.set_setting("kraken_deposit_address", ADDR)
db.set_setting("house_tax_accumulated", "0.003")
r = engine.check_and_send_house_tax()
check(1.1, r is None, "below threshold -> None")
check(1.2, _accum() == 0.003, "accumulator untouched")
check(1.3, _payout_rows() == [], "no payout row")

print("== 2. THE THRESHOLD BOUNDARY: == pays ==")
db.set_setting("house_tax_accumulated", "0.01")
_state.update(available=False, reply=None, raise_=None) if False else None
_state["available"] = False
r = engine.check_and_send_house_tax()
check(2.1, r is not None and r.get("tx_hash") == "pending_manual",
      "at-threshold fires (>=, not >); RPC offline -> pending_manual")
rows = _payout_rows()
check(2.2, len(rows) == 1 and rows[0]["status"] == "pending"
      and rows[0]["amount_xmr"] == 0.0099,
      f"payout row pending at accumulated-buffer ({rows[0]['amount_xmr'] if rows else '-'})")
check(2.3, _accum() == 0.01,
      "accumulator NOT zeroed — only a real send zeroes it")

print("== 3. RPC UP: the real send ==")
db.set_setting("house_tax_accumulated", "0.02")
_state["available"] = True
_state["reply"] = {"tx_hash": "abc123"}
r = engine.check_and_send_house_tax()
check(3.1, r.get("action") == "sent" and r.get("tx_hash") == "abc123"
      and r.get("amount") == 0.0199,
      "sent: accumulated - buffer")
check(3.2, _accum() == 0, "accumulator ZEROED (the money moved)")
rows = _payout_rows()
check(3.3, rows[-1]["status"] == "sent" and rows[-1]["tx_hash"] == "abc123",
      "payout row recorded sent with the tx")
dest = _state["calls"][-1][1]["destinations"][0]
check(3.4, dest["address"] == ADDR
      and dest["amount"] == int(0.0199 * 1e12),
      "destination = the settings address, atomic amount")

print("== 4. RPC ERRORS: nothing moves ==")
db.set_setting("house_tax_accumulated", "0.03")
_state["reply"] = {"error": "wallet not connected"}
r = engine.check_and_send_house_tax()
check(4.1, r.get("action") == "failed" and "wallet not connected"
      in str(r.get("error")), "the error surfaces in the result")
check(4.2, _accum() == 0.03, "accumulator NOT zeroed on failure")
check(4.3, len(_payout_rows()) == 2, "no NEW payout row (still 2)")

_state["raise"] = RuntimeError("rpc socket dead")
r = engine.check_and_send_house_tax()
check(4.4, r.get("action") == "failed" and "rpc socket dead"
      in str(r.get("error")), "a raised RPC also fails soft")
check(4.5, _accum() == 0.03, "still not zeroed")
_state["raise"] = None

print("== 5. THE FLOOR: tiny accumulations ==")
db.set_setting("payout_threshold_xmr", "0.0001")
db.set_setting("house_tax_accumulated", "0.00015")
_state["available"] = True
_state["reply"] = {"tx_hash": "def456"}
r = engine.check_and_send_house_tax()
check(5.1, r.get("amount") == 0.0001,
      f"0.00015 - 0.0001 floors at the Kraken minimum ({r.get('amount')})")

print("== 6. ZEROED = A CLEAN SECOND CALL ==")
r2 = engine.check_and_send_house_tax()
check(6.1, r2 is None, "after a successful send the next call is a no-op")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
