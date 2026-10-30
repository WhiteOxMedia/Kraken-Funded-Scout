"""test_jackpot_split.py — WHO FUNDED THE POT (Oct 26).

The admin dashboard's "Current Jackpot" card carries a second line: how
much of that pot the daily funding accrual put there, and how much is
XMR actually received. This file pins the four things that make that
line trustworthy:

  1. IT ADDS UP: accrual + received == the pot the card shows, always,
     including while a drawn prize is held back from the pot.
  2. IT KNOWS ITS WRITERS: a donation's jackpot share lands in
     "received", the daily formula in "accrual". A read cannot move
     either one on its own.
  3. IT SURVIVES A MESSY BASE: an unexplained credit becomes "received"
     (never "accrual" — the formula only ever adds through
     add_funding_base), an unexplained debit shrinks both in proportion
     (a claim is paid from both sources), and a base with no counters at
     all is bootstrapped from the funding ledger's own accrual history
     and then says so.
  4. IT REACHES THE CARD: /api/admin/stats serves the split (behind the
     admin gate), the panel has the element, app.js paints it from that
     key — and none of it smuggles the referral projection in.

House conventions: check(n, cond, why), RESULT line, sys.exit.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

# Same fixture as test_rate_label and test_ui_disclosure: no fetching in this
# file. A donation's split is priced through the rate reader, and a
# background Kraken refresh landing mid-drive would move the rate under the
# exact-number checks. XMR_PRICE_LIVE=0 keeps the pinned operator rate
# (500.0 here) the whole drive's fixed input, off the network.
os.environ["XMR_PRICE_LIVE"] = "0"

import database as db                                        # noqa: E402

_tmp = tempfile.mkdtemp(prefix="jackpot_split_")
db.DB_PATH = Path(_tmp) / "split.db"
db.init_db()

# The card is XMR, but a donation is priced through xmr_usd_rate; pinning
# the operator's rate keeps the drive off the network and the numbers
# exact (0.10 XMR donation -> 0.07 XMR into the pot at 70%).
db.set_setting("XMR_USD_RATE", "500.0")

# THE WALLET RPC IS BLOCKED (this box can have a live one on 18082). A
# claim in a test must settle by the ledger's own "pending_manual" path
# and never ask a real wallet to move money.
import types as _types                                       # noqa: E402

_stub = _types.ModuleType("monero_wallet")


def _blocked(*_a, **_k):
    raise AssertionError("the split drive must never touch the wallet RPC")


_stub.rpc_available = lambda: False
_stub._rpc = _blocked
_stub.get_balance = _blocked
_stub.scan_new_payments = lambda *a, **k: []
sys.modules["monero_wallet"] = _stub

import jackpot_engine as engine                              # noqa: E402
import jackpot_funding as funding                            # noqa: E402
from fastapi.testclient import TestClient                    # noqa: E402
import server as srv                                         # noqa: E402

CLIENT = TestClient(srv.app)
passed = failed = 0


def check(n, cond, why):
    global passed, failed
    tag = "PASS" if cond else "FAIL"
    print(f"{tag}  {n}  {why}")
    if cond:
        passed += 1
    else:
        failed += 1


def split():
    return engine.get_jackpot_split()


def close(a, b, tol=1e-8):
    return abs(float(a) - float(b)) <= tol


def adds_up(s):
    return (close(s["accrual_xmr"] + s["received_xmr"], s["jackpot_xmr"])
            and s["accrual_xmr"] >= 0 and s["received_xmr"] >= 0)


print("== 0. AN EMPTY POT ATTRIBUTES NOTHING ==")
_s = split()
check(0.1, close(_s["jackpot_xmr"], 0.0) and close(_s["accrual_xmr"], 0.0)
      and close(_s["received_xmr"], 0.0),
      f"empty box: no pot, no split: {_s}")
check(0.2, _s["attributed"] is False,
      "…and the card is told it has nothing to attribute (no fake 0/0 bar)")
check(0.3, adds_up(_s), "the invariant holds even at zero")

print("\n== 1. THE DAILY ACCRUAL LANDS IN 'accrual' ==")
engine.add_funding_base(0.6)                 # what accrue_daily_funding calls
_s = split()
check(1.1, close(_s["accrual_xmr"], 0.6) and close(_s["received_xmr"], 0.0),
      f"the formula's 0.6 XMR is accrual, not received: {_s['accrual_xmr']} / {_s['received_xmr']}")
check(1.2, close(_s["jackpot_xmr"], 0.6) and adds_up(_s),
      "…the pot is 0.6 and the two lines add up to it")
check(1.3, _s["accrual_pct"] == 100.0,
      f"100% accrual while nothing has been received: {_s['accrual_pct']}")

print("\n== 2. RECEIVED XMR IS TAGGED BY ITS OWN FLOW ==")
_d = funding.credit_donation("split-order-1", 0.10)
check(2.0, _d.get("credited") is True and close(_d["jackpot_xmr"], 0.07),
      f"a 0.10 XMR Scout Pro unlock credits its 70% share: {_d.get('jackpot_xmr')}")
_s = split()
check(2.1, close(_s["accrual_xmr"], 0.6) and close(_s["received_xmr"], 0.07),
      f"the donation lands in received and leaves accrual alone: {_s['accrual_xmr']} / {_s['received_xmr']}")
check(2.2, close(_s["jackpot_xmr"], 0.67) and adds_up(_s),
      f"pot = 0.60 accrual + 0.07 received = {_s['jackpot_xmr']}")
check(2.3, _s["accrual_pct"] == 89.6,
      f"the share is reported, not left to the reader: {_s['accrual_pct']}%")

print("\n== 3. READING IS FREE (no counter churn, no base movement) ==")
_a0 = db.get_setting("jackpot_src_accrual", "")
_r0 = db.get_setting("jackpot_src_received", "")
_b0 = db.get_setting("jackpot_balance", "")
for _ in range(3):
    split()
check(3.1, db.get_setting("jackpot_src_accrual", "") == _a0
      and db.get_setting("jackpot_src_received", "") == _r0,
      "three reads in a settled state rewrite nothing")
check(3.2, db.get_setting("jackpot_balance", "") == _b0,
      f"…and the base is untouched by a read: {db.get_setting('jackpot_balance')}")

print("\n== 4. AN UNEXPLAINED CREDIT IS 'received', NEVER 'accrual' ==")
db.set_setting("jackpot_balance", "0.72")        # +0.05 no flow accounted for
_s = split()
check(4.1, _s["reconciled"] is True and close(_s["received_xmr"], 0.12),
      f"the 0.05 gap is absorbed as received: {_s['received_xmr']}")
check(4.2, close(_s["accrual_xmr"], 0.60),
      "…the formula is not credited with money it never added")
_s2 = split()
check(4.3, _s2["reconciled"] is False and close(_s2["received_xmr"], 0.12)
      and close(_s2["accrual_xmr"], 0.60),
      "the correction persists: the next read has nothing left to reconcile")
check(4.4, close(float(db.get_setting("jackpot_balance")), 0.72) and adds_up(_s2),
      "reconciling describes the base; it never edits it")

print("\n== 5. A DRAWN PRIZE IS SCALED OUT OF BOTH SIDES ==")
_wallet = "9" + "A" * 94
_miner = db.get_or_create_miner(_wallet, worker_name="splitwinner")
with db.get_db() as conn:
    _cur = conn.execute(
        "INSERT INTO drawings (jackpot_amount, house_tax, status,"
        " claim_status, winner_id, drawn_at) VALUES (0.72, 0.072,"
        " 'drawn', 'pending', ?, ?)", (_miner["id"], time.time()))
    _drawing_id = _cur.lastrowid
_payout = round(0.72 - 0.072, 8)                 # 0.648 held for the winner
_s = split()
check(5.1, close(_s["jackpot_xmr"], round(0.72 - _payout, 8)),
      f"the card's pot drops by the earmark: {_s['jackpot_xmr']}")
check(5.2, close(_s["accrual_xmr"], 0.06) and close(_s["received_xmr"], 0.012),
      f"both sources shrink by the same 10%: {_s['accrual_xmr']} / {_s['received_xmr']}")
check(5.3, adds_up(_s) and close(_s["unclaimed_xmr"], _payout),
      f"0.06 + 0.012 == the pot, and the held prize is named: {_s['unclaimed_xmr']}")
check(5.4, close(engine.get_jackpot_balance(), _s["jackpot_xmr"]),
      "the split and the card's big number are one pot")

print("\n== 6. A CLAIM IS PAID OUT OF BOTH SOURCES ==")
_claim = engine.process_winner_claim(_drawing_id, _wallet)
check(6.0, _claim.get("status") == "ok",
      f"the real claim path settles: {_claim.get('status')} {_claim.get('reason', '')}")
_s = split()
check(6.1, close(float(db.get_setting("jackpot_balance")), 0.072)
      and close(_s["jackpot_xmr"], 0.072),
      f"base and pot both fall to 0.072: base={db.get_setting('jackpot_balance')}")
check(6.2, close(_s["accrual_xmr"], 0.06) and close(_s["received_xmr"], 0.012),
      f"the split follows the money left, ratio intact: {_s['accrual_xmr']} / {_s['received_xmr']}")
check(6.3, adds_up(_s) and close(_s["unclaimed_xmr"], 0.0),
      "the claim released the earmark and the lines still add up")
check(6.4, close(_s["accrual_xmr"] / _s["received_xmr"], 5.0),
      f"5:1 before the payout, 5:1 after: {_s['accrual_xmr'] / _s['received_xmr']:.3f}")

print("\n== 7. A BOX THAT PREDATES THE SPLIT IS INFERRED FROM THE LEDGER ==")
db.set_setting("jackpot_src_accrual", "0")
db.set_setting("jackpot_src_received", "0")
db.set_setting("jackpot_balance", "1.0")
db.set_setting(funding.LEDGER_KEY, json.dumps([
    {"id": 1, "action": funding.ACTION_ACCRUE, "day": "2026-10-01",
     "status": "accrued", "amount_xmr": 0.25, "ts": 1.0},
    {"id": 2, "action": funding.ACTION_ACCRUE, "day": "2026-10-02",
     "status": "accrued", "amount_xmr": 0.15, "ts": 2.0},
    {"id": 3, "action": funding.ACTION_ACCRUE, "day": "2026-10-03",
     "status": "skipped", "amount_xmr": 9.0, "ts": 3.0},
    {"id": 4, "action": "receipt", "category": "referral_bonus",
     "amount_usd": 3.0, "ts": 4.0},
]))
check(7.0, close(funding.lifetime_accrued_xmr(), 0.40),
      f"the ledger reader counts accrued accruals only: {funding.lifetime_accrued_xmr()}")
_s = split()
check(7.1, _s["inferred"] is True and close(_s["accrual_xmr"], 0.40)
      and close(_s["received_xmr"], 0.60),
      f"1.0 XMR with no counters splits 0.40 accrual / 0.60 received: {_s['accrual_xmr']} / {_s['received_xmr']}")
check(7.2, adds_up(_s), "…to the pot, exactly")
_s2 = split()
check(7.3, _s2["inferred"] is False and close(_s2["accrual_xmr"], 0.40),
      "the inference is written once, not re-guessed on every read")
check(7.4, close(engine.get_jackpot_balance(), 1.0),
      "bootstrap never changed the pot")

print("\n== 8. THE CARD GETS IT (gate, payload, client) ==")
check(8.1, CLIENT.get("/api/admin/stats").status_code == 401,
      "anonymous cannot read the split either")
_uid = db.create_user("split_admin", "x", email="split@example.test",
                      is_admin=True)
db.create_session("split-admin-token", _uid, int(time.time() + 3600))
CLIENT.cookies.set("session", "split-admin-token")
_r = CLIENT.get("/api/admin/stats")
_body = _r.json() if _r.status_code == 200 else {}
check(8.2, _r.status_code == 200 and "jackpot_split" in _body,
      f"the admin payload carries jackpot_split: {_r.status_code} {sorted(_body)}")
_split = _body.get("jackpot_split") or {}
check(8.3, close(_split.get("accrual_xmr", -1),
                 engine.get_jackpot_split()["accrual_xmr"]),
      "…with the engine's own numbers, not a second computation")
check(8.4, close(_split.get("accrual_xmr", 0) + _split.get("received_xmr", 0),
                 _body.get("jackpot_current", -1)),
      f"…that add up to the card's big number: "
      f"{_split.get('accrual_xmr')} + {_split.get('received_xmr')} = "
      f"{_body.get('jackpot_current')}")
check(8.5, abs(float(_body["jackpot_current"]) - engine.get_jackpot_balance()) < 1e-9,
      "the big number is still the ledger's balance (the split did not replace it)")
check(8.6, not ({"possible_future_jackpot", "referral_seed",
                 "current_jackpot_usd"} & set(_split)),
      "no projection field rode in on the split")

_app_js = (HERE / "pool" / "static" / "app.js").read_text(encoding="utf-8")
check(8.7, "paintAdminJackpotSplit(d.jackpot_split)" in _app_js,
      "app.js paints the split from d.jackpot_split")
check(8.8, "accrual_xmr" in _app_js and "received_xmr" in _app_js
      and "daily accrual" in _app_js,
      "…naming both sources in the operator's words")
_panel = (HERE / "pool" / "templates" / "admin_panel.html").read_text(encoding="utf-8")
check(8.9, 'id="adminJackpotSplit"' in _panel,
      "the panel has the element the client paints into")
check(8.10, "Current Jackpot" in _panel,
      "…inside the Current Jackpot card, not a new one")

_srv = (HERE / "pool" / "server.py").read_text(encoding="utf-8")
_start = _srv.index('@app.get("/api/admin/stats")')
_handler = _srv[_start:_srv.index("@app.", _start + 10)]
check(8.11, "engine.get_jackpot_split()" in _handler,
      "the handler takes the split from the engine (one source of truth)")
check(8.12, "jackpot_projection" not in _handler,
      "…and never from the advertisement table")

print("\n== 9. HERMETIC ==")
check(9.1, str(db.DB_PATH).startswith(_tmp),
      "every write in this file landed in the sandbox DB")
check(9.2, str(HERE / "pool" / "jackpot.db") != str(db.DB_PATH),
      "the live jackpot.db was never opened")

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
