"""test_vrsc_rate_stale.py — THE STALE-RATE GUARD (Oct 28).

The one conversion in the pool that SETS A PAYOUT may never convert
silently on an old rate. What this drive pins:

  1. FRESH MATH: fresh snapshots pay silently; a snapshot inside the
     warn window pays LOUDLY (guard returns ok with a warn sentence);
     a snapshot inside the block window refuses, naming the leg and
     both ages; a leg with no snapshot refuses (pricing at a default
     constant is not a price); an operator pin (override) freshens
     its leg.
  2. RETRYABILITY: a blocked claim returns BEFORE any db mutation —
     the drawing stays unclaimed, no jackpot payout row exists, and
     stamping a fresh rate re-runs the claim through, inside the
     claim window.
  3. THE AUDIT TRAIL: vrsc_rate_stale_warned and
     vrsc_rate_stale_blocked land exactly when the guard warns or
     blocks, with both legs' ages in the JSON payload.
  4. THE OPERATOR THRESHOLD: the db setting VRSC_RATE_STALE_S moves
     both windows (block = warn x 3 follows it).

HERMETIC (the test_vrsc_miner rule): sandbox db, both live rate feeds
kill-switched before import, wallet-rpc stubbed into a hard error —
no network, no money movement.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

# KILL BOTH LIVE FEEDS before these modules exist: no network in a drive.
os.environ["XMR_PRICE_LIVE"] = "0"
os.environ["VRSC_PRICE_LIVE"] = "0"

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import database as db                                  # noqa: E402
db.DB_PATH = Path(tempfile.mkdtemp(prefix="rate_stale_")) / "jackpot.db"
db.init_db()
db.set_setting("jackpot_balance", "5.0")               # real funds, cover

import jackpot_engine as engine                        # noqa: E402
import jackpot_funding                                 # noqa: E402
import vrsc_price                                      # noqa: E402
import xmr_price                                       # noqa: E402
import monero_wallet as xmw                            # noqa: E402

# KILL-SAFE CLAIMS: no wallet-rpc in a drive (every claim is VRSC, but
# break the RPC anyway — the same guarantee test_vrsc_miner carries).
xmw._rpc = lambda method, params=None: {
    "error": {"message": "sandbox: no wallet-rpc in a drive"}}
xmw.rpc_available = lambda: False

results = []
FIXTURE_N = [0]


def check(name, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(name)
          + ("  " + str(msg) if msg else ""))


def _stamp(vrsc_s=None, xmr_s=None):
    """Stamp LIVE-rate snapshots the given number of seconds old.

    The epoch is written with the live feed's own precision (%.3f —
    vrsc_price/xmr_price write TS_KEY that way). A %d floor here would
    drop up to a full second, and the guard's printed age would then
    round to s or s+1 depending on the wall-clock frac — the exact
    integer the 3.1/6.2 literals assert. Millisecond stamps make the
    age deterministically s (+ the few ms until the guard reads)."""
    now = time.time()
    if vrsc_s is not None:
        db.set_setting(vrsc_price.RATE_KEY, "0.25")
        db.set_setting(vrsc_price.TS_KEY, "%.3f" % (now - vrsc_s))
    if xmr_s is not None:
        db.set_setting(xmr_price.RATE_KEY, "500.0")
        db.set_setting(xmr_price.TS_KEY, "%.3f" % (now - xmr_s))
    vrsc_price.reset()
    xmr_price.reset()


def _clear_legs():
    for k in (vrsc_price.OVERRIDE_KEY, vrsc_price.RATE_KEY, vrsc_price.TS_KEY,
              xmr_price.OVERRIDE_KEY, xmr_price.RATE_KEY, xmr_price.TS_KEY):
        db.set_setting(k, "")
    db.set_setting(jackpot_funding.RATE_KEY, "")
    vrsc_price.reset()
    xmr_price.reset()


def _fixture(coin):
    """One miner + one drawn drawing. Fresh miner each time so the
    30-day win-limit gate never stages an unrelated block on a claim."""
    FIXTURE_N[0] += 1
    n = FIXTURE_N[0]
    addr = ("VsVrs%02d" % n + "a" * 40) if coin == "vrsc" else \
           ("4XMR%02d" % n + "a" * 91)
    m = db.get_or_create_miner(addr, "rig-rs-%d" % n)
    with db.get_db() as conn:
        conn.execute(
            "UPDATE miners SET coin = ? WHERE wallet_address = ?",
            (coin, addr))
        cur = conn.execute(
            "INSERT INTO drawings (jackpot_amount, house_tax, status, "
            "winner_id, drawn_at) VALUES (0.5, 0.05, 'drawn', ?, ?)",
            (m["id"], time.time()))
    return cur.lastrowid, addr


def _audit_count(action):
    with db.get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM audit_log WHERE action=?",
            (action,)).fetchone()["c"]


# ── threshold baseline: the setting is absent, so the constants run ──
db.set_setting(jackpot_funding.VRSC_RATE_STALE_KEY, "")

#                                             1. fresh snapshots pay silently
_stamp(vrsc_s=30, xmr_s=30)
_g = jackpot_funding.vrsc_conversion_guard()
check("1.1 fresh guard: ok with no warn and no reason",
      _g["ok"] and _g["warn"] is None, _g)
_dA, _wA = _fixture("vrsc")
_rA = engine.process_winner_claim(_dA, _wA)
check("1.2 fresh claim pays the full $-equivalent VRSC",
      _rA.get("status") == "ok" and abs((_rA.get("amount_coin") or 0) - 900.0) < 1e-6,
      _rA)
check("1.3 fresh payload carries no rate_warning",
      _rA.get("rate_warning") is None, _rA)
check("1.4 no stale audits at all",
      _audit_count("vrsc_rate_stale_warned") == 0
      and _audit_count("vrsc_rate_stale_blocked") == 0)

#                                             2. warn window pays LOUDLY
_stamp(vrsc_s=1000, xmr_s=30)          # 900 < 1000 < 2700
_g = jackpot_funding.vrsc_conversion_guard()
check("2.1 warn guard: ok but with a warn sentence naming the VRSC age",
      _g["ok"] and _g["warn"] and "VRSC" in _g["warn"] and "1000" in _g["warn"],
      _g)
warned0, blocked0 = _audit_count("vrsc_rate_stale_warned"), \
    _audit_count("vrsc_rate_stale_blocked")
_dB, _wB = _fixture("vrsc")
_rB = engine.process_winner_claim(_dB, _wB)
check("2.2 warn claim still pays",
      _rB.get("status") == "ok" and abs((_rB.get("amount_coin") or 0) - 900.0) < 1e-6,
      _rB)
check("2.3 the warn rides the response payload",
      bool(_rB.get("rate_warning")), _rB.get("rate_warning"))
check("2.4 exactly one warned audit with the age inside, none blocked",
      _audit_count("vrsc_rate_stale_warned") == warned0 + 1
      and _audit_count("vrsc_rate_stale_blocked") == blocked0)

#                                             3. block window refuses, retriable
_stamp(vrsc_s=5000, xmr_s=30)          # >= 900 x 3
_g = jackpot_funding.vrsc_conversion_guard()
check("3.1 block guard: not ok, reason names VRSC and its age",
      not _g["ok"] and "VRSC" in (_g["reason"] or "")
      and "5000" in (_g["reason"] or ""), _g)
blocked_before = _audit_count("vrsc_rate_stale_blocked")
_dC, _wC = _fixture("vrsc")
_rC = engine.process_winner_claim(_dC, _wC)
check("3.2 stale claim is refused", _rC.get("status") == "error", _rC)
with db.get_db() as conn:
    _prows = conn.execute(
        "SELECT COUNT(*) c FROM payouts WHERE drawing_id = ?",
        (_dC,)).fetchone()["c"]
    _drow = conn.execute(
        "SELECT claim_status FROM drawings WHERE id = ?",
        (_dC,)).fetchone()
check("3.3 the refusal lands BEFORE any mutation: no payout row, "
      "drawing still claimable",
      _prows == 0 and (_drow["claim_status"] or "pending") == "pending",
      _drow)
check("3.4 one blocked audit with the ages in the payload",
      _audit_count("vrsc_rate_stale_blocked") == blocked_before + 1)
_stamp(vrsc_s=30, xmr_s=30)            # the officer refreshes the feed
_rC2 = engine.process_winner_claim(_dC, _wC)
check("3.5 the same claim retries through once rates are fresh",
      _rC2.get("status") == "ok", _rC2)

#                                             4. no snapshot refuses; pins freshen
_clear_legs()
_g = jackpot_funding.vrsc_conversion_guard()
check("4.1 snapshot-less guard blocks both legs for default-constant pricing",
      not _g["ok"] and "VRSC" in (_g["reason"] or "")
      and "XMR" in (_g["reason"] or ""), _g)
_dD, _wD = _fixture("vrsc")
_rD = engine.process_winner_claim(_dD, _wD)
check("4.2 no-snapshot claim refused", _rD.get("status") == "error", _rD)
db.set_setting(vrsc_price.OVERRIDE_KEY, "0.25")
db.set_setting("XMR_USD_RATE", "500.0")
vrsc_price.reset()
xmr_price.reset()
_g = jackpot_funding.vrsc_conversion_guard()
check("4.3 operator pins freshen both legs: ok, no warn",
      _g["ok"] and _g["warn"] is None, _g)
_rD2 = engine.process_winner_claim(_dD, _wD)
check("4.4 the pinned-basket claim pays through (0.45 XMR @ 500/0.25)",
      _rD2.get("status") == "ok" and abs((_rD2.get("amount_coin") or 0) - 900.0) < 1e-6,
      _rD2)

#                                             5. the XMR leg is guarded too
_stamp(vrsc_s=30, xmr_s=None)
db.set_setting("XMR_USD_RATE", "")
db.set_setting(vrsc_price.OVERRIDE_KEY, "")
db.set_setting(xmr_price.RATE_KEY, "500.0")   # a rate WITH no timestamp
db.set_setting(xmr_price.TS_KEY, "")
vrsc_price.reset()
xmr_price.reset()
_g = jackpot_funding.vrsc_conversion_guard()
check("5.1 an XMR rate without a snapshot epoch refuses (ageless is not fresh)",
      not _g["ok"] and "XMR" in (_g["reason"] or ""), _g)
_dE, _wE = _fixture("vrsc")
_rE = engine.process_winner_claim(_dE, _wE)
check("5.2 the XMR-leg refusal blocks the claim", _rE.get("status") == "error", _rE)
db.set_setting("XMR_USD_RATE", "500.0")  # the operator vouches again
xmr_price.reset()
_rE2 = engine.process_winner_claim(_dE, _wE)
check("5.3 the vouched XMR bridge lets the claim through",
      _rE2.get("status") == "ok", _rE2)

#                                             6. the operator threshold moves both windows
db.set_setting(jackpot_funding.VRSC_RATE_STALE_KEY, "50")
_stamp(vrsc_s=100, xmr_s=30)           # block moves to 150, so 100 warns
_g = jackpot_funding.vrsc_conversion_guard()
check("6.1 the setting shrinks the warn window accordingly",
      _g["ok"] and _g["warn"] is not None, _g)
_stamp(vrsc_s=200, xmr_s=30)           # >= 50 x 3 now blocks
_g = jackpot_funding.vrsc_conversion_guard()
check("6.2 the block window follows the setting",
      not _g["ok"] and "200" in (_g["reason"] or ""), _g)
check("6.3 a zero/nonsense setting falls back to the 900 constant",
      jackpot_funding.vrsc_conversion_guard(warn_s=0)["ok"] is True,
      "warn_s=0 -> default 900; snapshot is 200 s old")

print("---")
# THE FLEET LINE: parsed for the ledger like every other drive's.
print("RESULT: %d passed, %d failed" % (sum(results), results.count(False)))
sys.exit(0 if all(results) else 1)
