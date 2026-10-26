"""test_scout_donation.py — THE SCOUT PRO DONATION IS JACKPOT MONEY (Oct 24).

Grades the two promises the memo makes about the $95 donation, driven
through the REAL checkout routes, a stubbed wallet RPC and one
sandboxed DB — no network, no real Monero node:

  1. THE DONATION FUNDS THE JACKPOT. A paid checkout raises the pot by
     the amount actually received, on the one transition that grants
     Pro, and never a second time. It does not travel through the
     14-day formula, so the daily accrual cannot double-count it.
  2. THE +1 ENTRY IS NEVER LOST. A donor with no payout wallet has no
     miner to hang a ticket on; the entry is HELD on the account and
     becomes a real ticket the moment a wallet is attached.

House conventions: check(n, cond, why), RESULT line, sys.exit.
"""
import sys
import tempfile
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import database as db                                        # noqa: E402

_tmp = tempfile.mkdtemp(prefix="scout_donation_")
db.DB_PATH = Path(_tmp) / "jackpot.db"
db.init_db()

# ── the wallet RPC stub: a real MODULE, every call hermetic ──────────
_ADDR = "4" + "D" * 94
_WALLET_IDX = 5
_THAT_TXID = "ab" * 32


# filled once the checkout price is known: the transfer must equal the
# order's XMR amount, which is the $95 price at the tracked rate
_AMOUNT = {"xmr": 0.01}


def _transfer():
    return {
        "txid": _THAT_TXID,
        "amount": _AMOUNT["xmr"],
        "subaddr_index": _WALLET_IDX,
        "confirmations": 1,
        "timestamp": time.time(),          # lands after the order opened
    }


_wallet = types.ModuleType("monero_wallet")
_wallet.rpc_available = lambda: True
_wallet.create_subaddress = lambda label="": (_ADDR, _WALLET_IDX)
_wallet.get_transfers = lambda min_height=0, subaddr_indices=None: [_transfer()]
_wallet.get_transfer_by_txid = lambda txid: (
    _transfer() if txid == _THAT_TXID else None)
# BOTH names: server.py imports `monero_wallet` top-level, while
# pool/scout_endpoints.py imports it relatively (`pool.monero_wallet`).
sys.modules["monero_wallet"] = _wallet
sys.modules["pool.monero_wallet"] = _wallet

# NOW import the server: its import-time init_db lands in the sandbox,
# and its `import monero_wallet` picks up the stub above
import server                                                # noqa: E402
import jackpot_funding as funding                            # noqa: E402
import jackpot_engine as engine                              # noqa: E402
from fastapi.testclient import TestClient                    # noqa: E402

# THE TWO-MODULE TRAP: server.py imports `database` as a top-level
# module, while pool/scout_endpoints.py imports it relatively
# (`pool.database`). Two module objects, two DB_PATH globals — in
# production both resolve to the same file, but a sandbox must point
# BOTH at the temp DB or the checkout writes to the real one.
import pool.database as _pkgdb                               # noqa: E402
_pkgdb.DB_PATH = db.DB_PATH
_pkgdb.init_db()

engine.get_next_block_hash = lambda: "ab" * 32               # no network

# THE PRICE (Oct 26): $95, charged in XMR at the tracked rate.
db.set_setting("XMR_USD_RATE", "500")
PRICE_XMR, PRICE_USD = server._pro_price()
_AMOUNT["xmr"] = PRICE_XMR

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}  {why}".rstrip())


def _base():
    return float(db.get_setting("jackpot_balance", "0"))


client = TestClient(server.app)
r = client.post("/api/auth/register",
                json={"username": "donor", "password": "pw-donor"})
check(0.1, r.status_code == 200,
      "a wallet-less account registers through the REAL route")

USER = db.get_user_by_username("donor")
check(0.2, USER and not (USER.get("wallet_address") or "").strip(),
      "the account starts with no payout wallet and therefore no miner")

print("== 1. THE CHECKOUT OPENS AND THE PAYMENT IS SEEN ==")

before = _base()
r = client.post("/scout/purchase", json={"action": "donate_unlock"})
check(1.1, r.status_code == 200, f"the donation checkout opens ({r.status_code})")
order = r.json() if r.status_code == 200 else {}
check(1.2, order.get("status") == "pending" and order.get("address") == _ADDR,
      "the order is pending on a dedicated subaddress")
check(1.3, abs(float(order.get("amount_xmr") or 0) - PRICE_XMR) < 1e-12,
      f"the order carries $95 at the rate ({PRICE_XMR} XMR, got "
      f"{order.get('amount_xmr')})")
check(1.4, _base() == before,
      "an OPEN checkout funds nothing yet — only real money moves the pot")
_usd_before = client.get("/api/jackpot/status").json()["jackpot_usd"]

r = client.post("/scout/purchase",
                json={"action": "status", "order_id": order.get("order_id")})
paid = r.json() if r.status_code == 200 else {}
check(1.5, paid.get("status") == "paid",
      "the on-chain transfer fulfils the order (1 confirmation)")

print("== 2. THE DONATION GOES INTO THE JACKPOT ==")

check(2.1, abs(_base() - (before + PRICE_XMR * 0.70)) < 1e-9,
      f"the pot rises by the jackpot's 70%, not the whole payment "
      f"({_base() - before} vs {PRICE_XMR * 0.70})")
check(2.2, paid.get("scout_pro", {}).get("active") is True,
      "the buyer's Scout Pro is unlocked by the same transition")
_rows = [x for x in funding._load_ledger()
         if x.get("source") == f"scout_pro_donation:{order.get('order_id')}"]
check(2.3, len(_rows) == 1
      and _rows[0].get("category") == funding.DONATION_CATEGORY
      and abs(float(_rows[0].get("amount_xmr") or 0) - PRICE_XMR) < 1e-12,
      "one receipt is filed under the donation category with the full XMR")
check("2.3b", len(_rows) == 1
      and abs(float(_rows[0].get("jackpot_xmr") or 0) - PRICE_XMR * 0.70) < 1e-9
      and abs(float(_rows[0].get("treasury_xmr") or 0) - PRICE_XMR * 0.30) < 1e-9,
      f"the receipt records the 70/30 split: "
      f"{_rows[0].get('jackpot_xmr')} / {_rows[0].get('treasury_xmr')}")

credit = funding.credit_donation(order.get("order_id"), PRICE_XMR,
                                 sponsor_id=USER["id"])
check(2.4, credit.get("credited") is False
      and abs(_base() - (before + PRICE_XMR * 0.70)) < 1e-12,
      "a replayed fulfilment cannot fund the same checkout twice")
check(2.5, abs(funding.referral_velocity_usd()) < 1e-9,
      "the donation is not ReferralVelocity — the daily formula never "
      "double-counts money already in the base")
_usd_after = client.get("/api/jackpot/status").json()["jackpot_usd"]
check(2.6, _usd_after > _usd_before,
      f"the pot the visitor sees grew (${_usd_before} -> ${_usd_after})")

print("== 3. NO WALLET, STILL AN ENTRY ==")

check(3.1, paid.get("scout_pro", {}).get("entry_held") is True,
      "the +1 entry is reported as held, not claimed as credited")
check(3.2, db.get_user(USER["id"])["pending_entries"] == 1,
      "the entry is held on the account that has no miner yet")
check(3.3, db.get_ticket_count() == 0,
      "no phantom ticket exists (a ticket needs a miner, a miner a wallet)")

r = client.post("/api/profile/update",
                json={"display_name": "donor", "wallet_address": "4" + "C" * 94})
check(3.4, r.status_code == 200, "the donor attaches a payout wallet")

miner = db.get_or_create_miner("4" + "C" * 94)
check(3.5, db.get_miner_ticket_count(miner["id"]) == 1,
      "the moment the wallet lands the held entry becomes a real ticket")
check(3.6, db.get_user(USER["id"])["pending_entries"] == 0,
      "the hold is cleared")
check(3.7, abs(_base() - (before + PRICE_XMR * 0.70)) < 1e-12,
      "claiming the entry mints a ticket, never money")

r = client.get("/api/jackpot/status")
st = r.json() if r.status_code == 200 else {}
sp = st.get("scout_pro", {})
check(3.8, sp.get("entry_awarded") is True and sp.get("entry_held") is False,
      "the dashboard payload now reports the entry as credited")
check(3.9, st.get("your_pending_tickets") == 1,
      "the donor's ticket count is one, and it is in the next drawing")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
