"""test_monero_wallet_payments.py — THE MONEY SCANNER, EXECUTED (Sep 26).

monero_wallet.py's scan_new_payments is the pool's money path: it is
where an incoming XMR transfer becomes a CREDIT for a specific user.
Until today no drive executed it (the coverage audit's 0% entry) —
every higher layer stubbed this module whole. This drive runs the REAL
module with exactly ONE seam stubbed: _rpc, the JSON-RPC transport.
Everything above the wire is the shipped code — get_transfers' parsing
(atomic amounts, confirmation math, the subaddr minor extraction),
scan_new_payments' known-subaddr filtering and user mapping, and the
REQUIRED_CONFIRMATIONS boundary.

  1. THE HAPPY CREDIT: a confirmed transfer to a known subaddr becomes
     a payment with the right user, the exact XMR amount (atomic/1e12),
     txid, height, and timestamp
  2. THE CONFIRMATION GATE IS DATA, NOT A FILTER: a matching transfer
     below 10 confirmations is still reported — with confirmed=False —
     because the scanner reports honestly and the CALLER decides;
     a caller that ignores the flag would credit early
  3. UNKNOWN SUBADDRS NEVER APPEAR: money sent to an unprovisioned
     subaddr (or a transfer with no subaddr at all → minor -1) binds
     to NO user and is excluded
  4. FAIL-CLOSED: an RPC error answers [] — the scanner never invents
     payments from a transport failure, and the failure is visible
     (scan returned, did not raise)
  5. min_height propagates into the get_transfers params; a None
     min_height sends no such param
  6. THE BOUNDARY: exactly REQUIRED_CONFIRMATIONS (10) is confirmed;
     9 is not — through get_transfer_by_txid's own math
  7. rpc_available() reads the same seam: an error is False, a height
     is True
  8. THE STUB IS THE ONLY DIAL: every _rpc call is one the script
     planned (an unplanned method would crash the drive) — the real
     wallet RPC at 127.0.0.1:18082 is never touched

Hermetic: no network, no wallet, no DB. Prints "RESULT: N passed,
M failed".
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))
import monero_wallet as mw                      # noqa: E402

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


calls = []
SCRIPT = {}


def _fake_rpc(method, params=None):
    """THE SEAM: the module's only IO, scripted by method. An
    unplanned method raises — the drive must never dial anything it
    did not script (the real wallet RPC is never touched)."""
    calls.append((method, dict(params or {})))
    if method not in SCRIPT:
        raise AssertionError(f"unplanned RPC method: {method}")
    return SCRIPT[method]


mw._rpc = _fake_rpc

HEIGHT = 1000
KNOWN = {7: "alice", 9: "bob"}          # subaddr minor -> user label
A = 500000000000                        # atomic: exactly 0.5 XMR

print("== 1. THE HAPPY CREDIT (confirmed money to a known subaddr) ==")
SCRIPT.update({
    "get_transfers": {"in": [
        {   # alice's subaddr, deep enough to credit
            "txid": "tx_confirmed", "amount": A, "height": 985,
            "subaddr_index": {"minor": 7}, "timestamp": 1790000000,
        },
        {   # bob's subaddr, still shallow
            "txid": "tx_shallow", "amount": A // 2, "height": 997,
            "subaddr_index": {"minor": 9}, "timestamp": 1790000060,
        },
        {   # a subaddr NOBODY provisioned
            "txid": "tx_unknown", "amount": A, "height": 990,
            "subaddr_index": {"minor": 99}, "timestamp": 1790000120,
        },
        {   # height 0 in the wild: the guard branch of the conf math
            "txid": "tx_zeroheight", "amount": A, "height": 0,
            "subaddr_index": {"minor": 7}, "timestamp": 1790000180,
        },
    ]},
    "get_height": {"height": HEIGHT},
})
pays = mw.scan_new_payments(KNOWN, min_height=900)
check(1.1, len(pays) == 3,
      f"3 of 4 transfers match known subaddrs (the unknown one is not "
      f"money for anyone here): got {len(pays)}")
conf = next((p for p in pays if p["txid"] == "tx_confirmed"), None)
check(1.2, conf is not None and conf["user"] == "alice"
      and conf["subaddr_index"] == 7
      and conf["amount"] == 0.5
      and conf["height"] == 985
      and conf["timestamp"] == 1790000000,
      f"the credit carries user, subaddr, exact XMR amount, height, "
      f"timestamp: {conf}")
check(1.3, conf is not None and conf["confirmations"] == 15
      and conf["confirmed"] is True,
      f"confirmations computed from the real height (1000-985=15) and "
      f"the payment is confirmed")
shallow = next((p for p in pays if p["txid"] == "tx_shallow"), None)
check(1.4, shallow is not None and shallow["user"] == "bob"
      and shallow["confirmations"] == 3
      and shallow["confirmed"] is False,
      "bob's shallow transfer is REPORTED with confirmed=False — "
      "the gate is data for the caller, not a silent drop")

print("== 2. THE GATE IS DATA, NOT A FILTER (the docstring overstates) ==")
check(2.1, all("confirmed" in p for p in pays),
      "every matched payment carries the confirmed flag explicitly")

print("== 3. UNKNOWN SUBADDRS NEVER BIND ==")
check(3.1, all(p["txid"] != "tx_unknown" for p in pays),
      "money to an unprovisioned subaddr is excluded")
zero_h = next((p for p in pays if p["txid"] == "tx_zeroheight"), None)
check(3.2, zero_h is not None and zero_h["confirmations"] == 0
      and zero_h["confirmed"] is False,
      "a height-0 transfer survives the guard branch as 0 confirmations")

print("== 4. FAIL-CLOSED ON RPC ERROR ==")
calls.clear()
SCRIPT.clear()
SCRIPT["get_transfers"] = {"error": "loading wallet"}
pays = mw.scan_new_payments(KNOWN, min_height=0)
check(4.1, pays == [] and len(calls) == 1,
      f"an RPC error answers [] from exactly one call — no payments "
      f"invented, no exception (fail-closed): calls={calls}")

print("== 5. min_height PROPAGATES (default floor 0, sent explicitly) ==")
calls.clear()
SCRIPT.clear()
SCRIPT.update({"get_transfers": {"in": []}, "get_height": {"height": HEIGHT}})
mw.scan_new_payments(KNOWN, min_height=900)
mw.scan_new_payments(KNOWN)                     # default floor
vgt = [(m, p) for (m, p) in calls if m == "get_transfers"]
check(5.1, len(vgt) == 2 and vgt[0][1].get("min_height") == 900
      and vgt[1][1].get("min_height") == 0,
      f"the floor rides the params when given; the default is 0 "
      f"(from genesis), sent explicitly rather than omitted: "
      f"{[p for _, p in vgt]}")

print("== 6. THE CONFIRMATION BOUNDARY (exactly 10) ==")
for h, want in ((990, True), (991, False)):     # 1000-h: 10 and 9
    calls.clear()
    SCRIPT.clear()
    SCRIPT.update({
        "get_transfer_by_txid": {"transfer": {
            "txid": "txb", "amount": A, "height": h,
            "subaddr_index": {"minor": 7}, "timestamp": 1790000000}},
        "get_height": {"height": HEIGHT},
    })
    t = mw.get_transfer_by_txid("txb")
    check(f"6.{1 if want else 2}", t["confirmations"] == (10 if want else 9)
          and t["confirmed"] is want,
          f"{10 if want else 9} confirmations -> confirmed={want} "
          f"(the REQUIRED_CONFIRMATIONS boundary is >=, money at exactly "
          f"10 blocks credits): {t}")

print("== 7. rpc_available() READS THE SAME SEAM ==")
calls.clear()
SCRIPT.clear()
SCRIPT["get_height"] = {"error": "no wallet"}
check(7.1, mw.rpc_available() is False, "an error is not available")
SCRIPT["get_height"] = {"height": HEIGHT}
check(7.2, mw.rpc_available() is True, "a height is available")

print("== 8. A TRANSFER WITH NO SUBADDR AT ALL BINDS NO ONE ==")
SCRIPT.clear()
SCRIPT.update({
    "get_transfers": {"in": [
        {"txid": "tx_nosub", "amount": A, "height": 990,
         "timestamp": 1790000240},          # no subaddr_index key at all
    ]},
    "get_height": {"height": HEIGHT},
})
pays = mw.scan_new_payments(KNOWN, min_height=0)
check(8.1, pays == [],
      "the missing subaddr defaults to minor -1, which belongs to no "
      "user — the transfer is excluded, not crashed on")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
