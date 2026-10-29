"""test_vrsc_concurrent_claim.py — ONE DRAWING, ONE PAYOUT (Oct 28).

Two claims of the SAME drawing arriving together — a double-clicked
button, a fast retry, two screens — must never both mint a payout
row. What this drive pins:

  1. THE LEASE CONTRACT: acquire_claim_lease is one conditional UPDATE —
     first caller owns it, the loser matches 0 rows; release hands a
     refused claim back to 'pending'; a lease older than the steal
     window is stealable (a claimant that died mid-flight must not
     strand the prize), a young one is not.
  2. THE DOUBLE-MINT RACE: two threads claim one drawing with the
     check→mint window deliberately widened (a barrier inside
     claim_drawing). After the claim: exactly ONE jackpot payout row,
     the treasury trimmed exactly ONCE, one same-coin receipt, and
     the loser hold an error — never a second mint.
  3. THE RAW-PAIR STRESS: two threads per fresh drawing, no pacing
     help, eight drawings in a row — every one still mints exactly
     one payout.
  4. RELEASE ON REFUSAL: a claim the stale-rate guard blocks must give
     the lease back, or the retry path the previous drive pinned
     would be dead under the new lock.

HERMETIC (the test_vrsc_miner rule): sandbox db, both live rate feeds
kill-switched, wallet-rpc stubbed into a hard error. Threads are plain
threading.Thread pairs — the same concurrency shape the server's
claim route really sees, on the same SQLite/WAL store.
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

# KILL BOTH LIVE FEEDS before these modules exist: no network in a drive.
os.environ["XMR_PRICE_LIVE"] = "0"
os.environ["VRSC_PRICE_LIVE"] = "0"

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import database as db                                  # noqa: E402
db.DB_PATH = Path(tempfile.mkdtemp(prefix="claim_race_")) / "jackpot.db"
db.init_db()
db.set_setting("jackpot_balance", "5.0")

import jackpot_engine as engine                        # noqa: E402
import jackpot_funding                                 # noqa: E402
import xmr_price                                       # noqa: E402
import vrsc_price                                      # noqa: E402
import monero_wallet as xmw                            # noqa: E402

# KILL-SAFE CLAIMS: no wallet-rpc in a drive.
xmw._rpc = lambda method, params=None: {
    "error": {"message": "sandbox: no wallet-rpc in a drive"}}
xmw.rpc_available = lambda: False

results = []
FIXTURE_N = [0]


def check(name, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(name)
          + ("  " + str(msg) if msg else ""))


def _fresh_rates():
    for k, v in ((vrsc_price.RATE_KEY, "0.25"), (vrsc_price.TS_KEY,
                                                 "%d" % int(time.time())),
                 (xmr_price.RATE_KEY, "500.0"), (xmr_price.TS_KEY,
                                                 "%d" % int(time.time()))):
        db.set_setting(k, v)
    vrsc_price.reset()
    xmr_price.reset()


def _fixture(coin="vrsc"):
    """One miner + one drawn, claimable drawing. Fresh miner per fixture
    so the 30-day win-limit gate never stages an unrelated block."""
    FIXTURE_N[0] += 1
    n = FIXTURE_N[0]
    addr = "VsVrs%02d" % n + "a" * 40 if coin == "vrsc" \
        else "4XMR%02d" % n + "a" * 91
    m = db.get_or_create_miner(addr, "rig-cc-%d" % n)
    with db.get_db() as conn:
        conn.execute(
            "UPDATE miners SET coin = ? WHERE wallet_address = ?",
            (coin, addr))
        cur = conn.execute(
            "INSERT INTO drawings (jackpot_amount, house_tax, status, "
            "winner_id, drawn_at) VALUES (0.5, 0.05, 'drawn', ?, ?)",
            (m["id"], time.time()))
    return cur.lastrowid, addr


def _payout_rows(drawing_id):
    with db.get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM payouts WHERE drawing_id = ? "
            "AND payout_type = 'jackpot'",
            (drawing_id,)).fetchone()["c"]


def _claim_status(drawing_id):
    with db.get_db() as conn:
        return conn.execute(
            "SELECT claim_status FROM drawings WHERE id = ?",
            (drawing_id,)).fetchone()["claim_status"]


def _pair(drawing_id, addr, widen=False):
    """Fire TWO claims of one drawing concurrently; return both results."""
    out = [None, None]
    barrier = threading.Barrier(2)
    orig = db.claim_drawing

    def _paced(*a, **kw):
        if widen:
            try:
                barrier.wait(timeout=0.15)
            except Exception:
                # a loser that exits early at the lease never arrives:
                # the pacing is best-effort, never correctness
                pass
        return orig(*a, **kw)

    def _runner(i):
        try:
            out[i] = engine.process_winner_claim(drawing_id, addr)
        except Exception as e:            # noqa: BLE001 - a raise is a result
            out[i] = {"status": "exception", "reason": repr(e)}

    db.claim_drawing = _paced
    ts = [threading.Thread(target=_runner, args=(i,)) for i in (0, 1)]
    ts[0].start()
    time.sleep(0.002)
    ts[1].start()
    ts[0].join(timeout=15)
    ts[1].join(timeout=15)
    db.claim_drawing = orig
    return out


#                                            1. the lease contract itself
_d1, _ = _fixture()
check("1.1 acquire on a fresh drawing wins",
      db.acquire_claim_lease(_d1), _claim_status(_d1))
check("1.2 a held lease blocks the second acquirer",
      db.acquire_claim_lease(_d1) is False)
check("1.3 release returns the drawing to 'pending'",
      db.release_claim_lease(_d1) and _claim_status(_d1) == "pending")
check("1.4 released lease is reacquirable",
      db.acquire_claim_lease(_d1) is True)
db.claim_drawing(_d1, "w")
check("1.5 a claimed drawing is never leaseable again",
      db.acquire_claim_lease(_d1) is False and _claim_status(_d1) == "claimed")

# Stale leases: a young one is untouchable, an old one is stealable.
_d2, _ = _fixture()
db.acquire_claim_lease(_d2)
with db.get_db() as conn:
    conn.execute(
        "UPDATE drawings SET claim_leased_at = ? WHERE id = ?",
        (time.time() - 400, _d2))      # older than the 300 s steal window
check("1.6 a dead claimant's lease older than the window is stolen",
      db.acquire_claim_lease(_d2) is True)

#                                            2. THE DOUBLE-MINT RACE
_fresh_rates()
_d3, _w3 = _fixture()
out = _pair(_d3, _w3, widen=True)
_oks = [r for r in out if r.get("status") == "ok"]
check("2.1 exactly one of the two racing claims pays",
      len(_oks) == 1, out)
check("2.2 exactly ONE jackpot payout row was minted",
      _payout_rows(_d3) == 1, _payout_rows(_d3))
check("2.3 the drawing ends claimed", _claim_status(_d3) == "claimed")
with db.get_db() as conn:
    _base = float(db.get_setting("jackpot_balance", "0"))
    _receipts = conn.execute(
        "SELECT COUNT(*) c FROM audit_log WHERE "
        "action = 'vrsc_payout_pending_treasury'").fetchone()["c"]
check("2.4 the treasury is trimmed exactly once",
      abs(_base - (5.0 - 0.45)) < 1e-9, _base)
check("2.5 one same-coin receipt, not two", _receipts == 1, _receipts)
with db.get_db() as conn:
    _lost = conn.execute(
        "SELECT COUNT(*) c FROM audit_log WHERE action='claim_race_lost'"
    ).fetchone()["c"]
check("2.6 the loser never mints and never poisons: race audits sane",
      _lost <= 1, _lost)

#                        2b. THE FX-AT-MINT STAMP (reconciliation)
with db.get_db() as conn:
    _frow = conn.execute(
        "SELECT fx_xmr_usd, fx_vrsc_usd, amount_coin FROM payouts "
        "WHERE drawing_id = ? AND payout_type = 'jackpot'",
        (_d3,)).fetchone()
check("2.7 the mint stamped the exact rates that priced the row — "
      "the fresh snapshots the guard read, verbatim, at mint time",
      _frow is not None
      and abs(_frow["fx_xmr_usd"] - 500.0) < 1e-9
      and abs(_frow["fx_vrsc_usd"] - 0.25) < 1e-9,
      (_frow["fx_xmr_usd"], _frow["fx_vrsc_usd"]) if _frow else None)
check("2.8 the stamped rates reprice the row: amount_xmr against "
      "them rebuilds amount_coin — reconciliation's own formula",
      _frow is not None
      and abs(_frow["amount_coin"]
              - 0.45 * _frow["fx_xmr_usd"] / _frow["fx_vrsc_usd"]) < 1e-6,
      _frow["amount_coin"],)

# and the honest NULL: the XMR leg pays face value and priced nothing.
_xm, _xw = _fixture(coin="xmr")
_xr = engine.process_winner_claim(_xm, _xw)
with db.get_db() as conn:
    _xrow = conn.execute(
        "SELECT coin, fx_xmr_usd, fx_vrsc_usd FROM payouts "
        "WHERE drawing_id = ? AND payout_type = 'jackpot'",
        (_xm,)).fetchone()
check("2.9 an XMR leg prices nothing: its row carries a NULL fx and "
      "the xmr coin",
      _xr.get("status") == "ok" and _xrow is not None
      and _xrow["coin"] == "xmr" and _xrow["fx_xmr_usd"] is None
      and _xrow["fx_vrsc_usd"] is None,
      (_xr, _xrow))

#                                            3. raw-pair stress, no pacing
_badrows, _badclaim = 0, 0
for _i in range(8):
    _d, _w = _fixture()
    out = _pair(_d, _w)
    if _payout_rows(_d) != 1:
        _badrows += 1
    if _claim_status(_d) != "claimed":
        _badclaim += 1
check("3.1 eight concurrent pairs, eight single mints", _badrows == 0,
      "double-mint drawings: %d" % _badrows)
check("3.2 eight concurrent pairs, eight clean claims", _badclaim == 0,
      "unclaimed-in-end drawings: %d" % _badclaim)

#                                            4. a refused claim releases the lease
db.set_setting(vrsc_price.TS_KEY, "%d" % int(time.time() - 5000))
vrsc_price.reset()                     # block via the stale-rate guard
_d4, _w4 = _fixture()
_r4 = engine.process_winner_claim(_d4, _w4)
check("4.1 the stale-rate block still refuses",
      _r4.get("status") == "error", _r4)
check("4.2 the refusal RELEASED the lease (drawing back to 'pending')",
      _claim_status(_d4) == "pending", _claim_status(_d4))
db.set_setting(vrsc_price.TS_KEY, "%d" % int(time.time()))
vrsc_price.reset()
_r4b = engine.process_winner_claim(_d4, _w4)
check("4.3 the retry then pays through under the lease",
      _r4b.get("status") == "ok", _r4b)

#                                            5. hard in-flight lock: an actual error reply
db.set_setting(jackpot_funding.VRSC_RATE_STALE_KEY, "")
_fresh_rates()
_d5, _w5 = _fixture()
ok5 = 0
db.acquire_claim_lease(_d5)            # someone else holds it right now
_r5 = engine.process_winner_claim(_d5, _w5)
check("5.1 a claim into a held lease is turned away",
      _r5.get("status") == "error" and "in flight" in _r5.get("reason", ""),
      _r5)
check("5.2 no payout minted for the turned-away claim",
      _payout_rows(_d5) == 0, _payout_rows(_d5))

print("---")
# THE FLEET LINE: parsed for the ledger like every other drive's.
print("RESULT: %d passed, %d failed" % (sum(results), results.count(False)))
sys.exit(0 if all(results) else 1)
