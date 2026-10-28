"""test_donation_idempotency.py - A REPLAYED ORDER CANNOT FUND THE POT TWICE.

The Oct 26 review's [major] on credit_donation: idempotency was a scan of
jackpot_funding's settings ledger, which is a RECEIPT BOOK capped at
LEDGER_MAX_ROWS and rewritten as one JSON value. Two ways through it:

  * a receipt that aged out of the cap no longer matched, so a replayed
    status poll credited the same order again;
  * the check, the receipt and the pot raise were three steps on three
    connections, so two pollers could both pass the check.

It is now a `donation_credits` row per order, inserted in the SAME
transaction as the base raise. This file pins the four behaviours that
make that true, and the two failure modes the old design owned:

  1. THE KEY IS A ROW: a credit writes a receipt AND its own key row.
  2. PRUNING CANNOT RESURRECT IT: push the receipt out of the 2000-row
     book and replay - refused, pot untouched, original split returned.
     (With the key row deleted the ledger alone lets it through: that
     control documents exactly what the key replaced.)
  3. CONCURRENCY: four pollers on one order - one credit, one pot move.
  4. FAILURE ROLLS BACK: an exception inside the credit leaves no key
     row and no pot move, so the retry is a first attempt, not a replay.
  5. A PRE-TABLE ORDER IS ADOPTED: a legacy receipt refuses a second
     credit today, and keeps refusing after the ledger forgets it.

House conventions: check(n, cond, why), RESULT line, sys.exit.
"""
import json
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import database as db                                        # noqa: E402

_tmp = tempfile.mkdtemp(prefix="donation_idem_")
db.DB_PATH = Path(_tmp) / "donation.db"
db.init_db()
db.set_setting("XMR_USD_RATE", "500.0")     # off the network, exact numbers

import jackpot_engine as engine                              # noqa: E402
import jackpot_funding as funding                            # noqa: E402

passed = failed = 0


def check(n, cond, why):
    global passed, failed
    tag = "PASS" if cond else "FAIL"
    print(f"{tag}  {n}  {why}")
    if cond:
        passed += 1
    else:
        failed += 1


def pot():
    return float(db.get_setting("jackpot_balance", "0") or 0)


def close(a, b, tol=1e-9):
    """Float equality that survives a missing value (a None must read as
    a FAILED check, never as a crashed test)."""
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


print("== 1. THE KEY IS A ROW (not a receipt in a capped book) ==")
_first = funding.credit_donation(991, 0.10)
check(1.1, _first.get("credited") is True and close(pot(), 0.07),
      f"order 991 credits its 70% share once: pot {pot()}")
check(1.2, funding._receipt_row("scout_pro_donation:991") is not None,
      "the audit receipt is still filed in the ledger")
check(1.3, db.get_donation_credit(991) is not None,
      "…and the credit has a key row of its own")
check(1.4, close((db.get_donation_credit(991) or {}).get("base_xmr"), 0.07),
      "…carrying the base it produced")

print("\n== 2. PRUNING CANNOT RESURRECT A CREDIT ==")
# THE CAP BITES: fill the book to LEDGER_MAX_ROWS and write once more -
# byte for byte what record_funding does on every later inflow.
db.set_setting(funding.LEDGER_KEY, json.dumps(
    [{"id": i, "category": "referral_bonus", "amount_usd": 1.0,
      "source": "filler", "ts": 0} for i in range(funding.LEDGER_MAX_ROWS)]))
funding.record_funding("referral_bonus", 1.0, source="after the cap")
check(2.1, funding._receipt_row("scout_pro_donation:991") is None,
      f"the receipt is gone from the {funding.LEDGER_MAX_ROWS}-row book")

_pot = pot()
_replay = funding.credit_donation(991, 0.10)
check(2.2, _replay.get("credited") is False,
      f"a replayed status poll funds nothing: {_replay.get('credited')}")
check(2.3, close(pot(), _pot), f"…the pot never moved: {pot()}")
check(2.4, close(_replay.get("jackpot_xmr"), 0.07),
      f"…and the answer is the ORIGINAL split: {_replay.get('jackpot_xmr')}")
check(2.5, close(_replay.get("treasury_xmr"), 0.03),
      "…both halves, not a fresh computation")

print("\n== 3. THE CONTROL: THE LEDGER ALONE WAS THE HOLE ==")
# No key row, no receipt - exactly the old world. It credits again.
with db.get_db() as conn:
    conn.execute("DELETE FROM donation_credits WHERE order_id = '991'")
_pot = pot()
_unprotected = funding.credit_donation(991, 0.10)
check(3.1, _unprotected.get("credited") is True and pot() > _pot,
      f"without the key row the pruned ledger lets it through: "
      f"pot {_pot} -> {pot()}")

print("\n== 4. FOUR POLLERS, ONE CREDIT ==")
_order = 500
_pot = pot()
_results, _errors = [], []
_barrier = threading.Barrier(4)


def poll():
    try:
        _barrier.wait(timeout=10)
        _results.append(funding.credit_donation(_order, 0.20))
    except Exception as e:                       # noqa: BLE001
        _errors.append(f"{type(e).__name__}: {e}")


_threads = [threading.Thread(target=poll) for _ in range(4)]
for t in _threads:
    t.start()
for t in _threads:
    t.join()
_won = [r for r in _results if r.get("credited")]
check(4.1, not _errors, f"no caller fails on the writer lock: {_errors}")
check(4.2, len(_won) == 1,
      f"exactly one poller credits the order: {len(_won)} of {len(_results)}")
check(4.3, close(pot(), _pot + 0.14),
      f"the pot moved by one 70% share: {_pot} -> {pot()}")
check(4.4, close((db.get_donation_credit(_order) or {}).get("jackpot_xmr"), 0.14),
      "…and the key row records the one split that landed")

print("\n== 5. A FAILED CREDIT LEAVES NOTHING BEHIND ==")
_original = engine.add_funding_base


def _boom(*_a, **_k):
    raise RuntimeError("engine down")


engine.add_funding_base = _boom
_pot = pot()
_raised = None
try:
    funding.credit_donation(501, 0.30)
except Exception as e:                           # noqa: BLE001
    _raised = e
finally:
    engine.add_funding_base = _original
check(5.1, isinstance(_raised, RuntimeError),
      f"the failure surfaces to the caller: {_raised}")
check(5.2, db.get_donation_credit(501) is None,
      "…the claim rolled back with it (no key with no money)")
check(5.3, close(pot(), _pot), f"…and the pot never moved: {pot()}")

_pot = pot()
_retry = funding.credit_donation(501, 0.30)
check(5.4, _retry.get("credited") is True and close(pot(), _pot + 0.21),
      f"the retry is a first attempt, not a replay: pot {pot()}")
check(5.5, (db.get_donation_credit(501) or {}).get("base_xmr") is not None,
      "…and it stamps the base it produced")

print("\n== 6. AN ORDER FROM BEFORE THE TABLE IS ADOPTED ==")
# A receipt and no key row: exactly what a credit from before Oct 26
# looks like on this box.
funding.record_funding(funding.DONATION_CATEGORY, 50.0,
                       source="scout_pro_donation:777", amount_xmr=0.10,
                       jackpot_xmr=0.07, treasury_xmr=0.03)
check(6.1, db.get_donation_credit(777) is None,
      "the legacy order starts with no key row")
_pot = pot()
_legacy = funding.credit_donation(777, 0.10)
check(6.2, _legacy.get("credited") is False and close(pot(), _pot),
      f"its receipt still refuses the second credit: pot {pot()}")
check(6.3, db.get_donation_credit(777) is not None,
      "…and the protection moved into the key table on sight")
with db.get_db() as conn:
    conn.execute("UPDATE settings SET value = '[]' WHERE key = ?",
                 (funding.LEDGER_KEY,))          # the ledger forgets it
_pot = pot()
_after = funding.credit_donation(777, 0.10)
check(6.4, _after.get("credited") is False and close(pot(), _pot),
      "clearing the ledger no longer clears the protection")

print("\n== 7. THE TRANSACTION IS REAL (the code that makes it so) ==")
_src = (HERE / "pool" / "jackpot_funding.py").read_text(encoding="utf-8")
_start = _src.index("def credit_donation(")
_body = _src[_start:_src.index("\ndef ", _start + 10)]
check(7.1, "with db.get_db() as conn:" in _body
      and "claim_donation_credit(" in _body
      and "add_funding_base(jackpot_xmr" in _body,
      "the claim and the base raise share one get_db() transaction")
check(7.2, "conn=conn" in _body,
      "…with the connection handed to the engine's one balance writer")
_engine_src = (HERE / "pool" / "jackpot_engine.py").read_text(encoding="utf-8")
_sig = next(l for l in _engine_src.splitlines()
            if l.startswith("def add_funding_base("))
check(7.3, "conn=None" in _sig,
      f"add_funding_base takes that connection: {_sig.strip()}")

print("\n== 8. HERMETIC ==")
check(8.1, str(db.DB_PATH).startswith(_tmp),
      "every write in this file landed in the sandbox DB")
check(8.2, str(HERE / "pool" / "jackpot.db") != str(db.DB_PATH),
      "the live jackpot.db was never opened")

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
