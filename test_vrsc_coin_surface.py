"""test_vrsc_coin_surface.py — THE COIN FIELD AS SURFACED (Oct 28).

What may NEVER regress about showing a VRSC payout as a VRSC payout:

  1. LEDGER TRANSPORT: /api/admin/payouts is admin-only, and a VRSC row
     carries coin / amount_coin / the pending_manual_treasury marker
     through the JSON — exactly the facts the admin card renders.
  2. ADMIN RENDER CONTRACT: app.js's payout card names the coin on a
     VRSC row (quantity first, XMR-equivalent secondary), keeps the
     byte-for-byte old markup for plain XMR rows, and renders the
     treasury-send state instead of a bare 'pending'.
  3. THE WINNERS PAGE: a claimed VRSC drawing shows the exact treasury
     quantity with the pending-send marker; an UNCLAIMED Verus win only
     says equivalent — the page never invents a quantity before the
     claim sets it at the live rate; an XMR row stays plain XMR; the
     column header says Prize, not a coin it cannot promise.

HERMETIC (the test_vrsc_miner rule): its ONLY db writes land on a
sandbox; the real pool/jackpot.db is not opened. Both live rate feeds
are kill-switched (VRSC_PRICE_LIVE=0, XMR_PRICE_LIVE=0) before import,
so nothing here touches the network.
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
db.DB_PATH = Path(tempfile.mkdtemp(prefix="coin_surf_")) / "jackpot.db"
db.init_db()
db.set_setting("XMR_USD_RATE", "500.0")                # pinned, the house rule

import server as srv                                   # noqa: E402
from fastapi.testclient import TestClient              # noqa: E402

results = []


def check(name, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(name)
          + ("  " + str(msg) if msg else ""))


client = TestClient(srv.app)

# ---------------------------------------------------------------- 1. LEDGER
r_anon = client.get("/api/admin/payouts")
check("admin payouts is admin-only without a session",
      r_anon.status_code in (401, 403), "anon got %s" % r_anon.status_code)

with db.get_db() as conn:
    conn.execute(
        "INSERT INTO users (username, password_hash, is_admin) "
        "VALUES ('officer', 'x', 1)")
    _uid = conn.execute(
        "SELECT id FROM users WHERE username='officer'").fetchone()[0]
    conn.execute(
        "INSERT INTO sessions (token, user_id, expires_at) "
        "VALUES (?, ?, ?)", ("surf-admin-tok", _uid, time.time() + 3600))
client.cookies.set("session", "surf-admin-tok")

# Two minors: one Monero-coin, one Verus-coin. Plus the admin user above.
with db.get_db() as conn:
    conn.execute(
        "INSERT INTO miners (wallet_address, coin, is_demo) "
        "VALUES ('48XMRKEyWalletLongPoseForMinerOne', 'xmr', 0)")
    _x = conn.execute("SELECT id FROM miners WHERE coin='xmr'").fetchone()[0]
    conn.execute(
        "INSERT INTO miners (wallet_address, coin, is_demo) "
        "VALUES ('VsVrsKEyWalletLongPoseForMinerTwo00', 'vrsc', 0)")
    _v = conn.execute("SELECT id FROM miners WHERE coin='vrsc'").fetchone()[0]

# One XMR payout, one claimed-VRSC jackpot waiting on the treasury leg.
_x_pid = db.add_payout(_x, 0.25, "mining")
_db_name = db.update_payout_status(_x_pid, "confirmed", "xhash-xmr-1")
_v_pid = db.add_payout(_v, 2.375, "jackpot", drawing_id=None,
                       coin="vrsc", amount_coin=123.45)
db.update_payout_status(_v_pid, "pending", "pending_manual_treasury")

rows = client.get("/api/admin/payouts").json()
_xrow = next((r for r in rows if r["id"] == _x_pid), {})
_vrow = next((r for r in rows if r["id"] == _v_pid), {})
check("payouts endpoint returns the ledger rows", bool(rows), rows)
check("XMR row stays the XMR-denominated ledger row",
      _xrow.get("coin") == "xmr" and _xrow.get("amount_coin") is None)
check("VRSC row carries coin + quantity + treasury marker through JSON",
      _vrow.get("coin") == "vrsc" and float(_vrow.get("amount_coin") or 0) == 123.45
      and _vrow.get("tx_hash") == "pending_manual_treasury")

# --------------------------------------------------- 1b. admin render contract
js = (HERE / "pool" / "static" / "app.js").read_text(encoding="utf-8")
start = js.index("async function fetchAdminPayouts")
end = js.index("async function fetchAdminAudit")
cell = js[start:end]
check("admin card reads the coin field",
      "p.coin" in cell and "amount_coin" in cell)
check("admin card marks the treasury-send state on a pending VRSC leg",
      "pending_manual_treasury" in cell)
check("plain XMR rows keep the exact old markup",
      "? fmt(p.amount_xmr,6) + ' XMR'" in cell
      and "(p.coin || 'xmr').toLowerCase()" in cell)
check("VRSC rows put the coin quantity first",
      "coin.toUpperCase()" in cell and "(equiv" in cell)

# ------------------------------------------------------------- 2. WINNERS PAGE
# Drawing #1: Monero winner, claimed nothing new — plain XMR history.
# Drawing #2: Verus winner, claimed, VRSC payout pending on the treasury.
# Drawing #3: Verus miner, drawn but NOT claimed yet — no quantity exists.
with db.get_db() as conn:
    conn.execute(
        "INSERT INTO drawings (jackpot_amount, winner_id, status, drawn_at) "
        "VALUES (3.0, ?, 'drawn', ?)", (_x, time.time()))
    d1 = conn.execute("SELECT id FROM drawings WHERE winner_id=?", (_x,)).fetchone()[0]
    conn.execute(
        "INSERT INTO drawings (jackpot_amount, winner_id, status, drawn_at) "
        "VALUES (2.375, ?, 'drawn', ?)", (_v, time.time()))
    d2 = conn.execute(
        "SELECT id FROM drawings WHERE winner_id=? AND jackpot_amount=2.375",
        (_v,)).fetchone()[0]
    conn.execute(
        "INSERT INTO drawings (jackpot_amount, winner_id, status, drawn_at) "
        "VALUES (1.5, ?, 'drawn', ?)", (_v, time.time()))
    d3 = conn.execute(
        "SELECT id FROM drawings WHERE winner_id=? AND jackpot_amount=1.5",
        (_v,)).fetchone()[0]

# The claim flip is lease-bearer (Oct 28): the surface fixture claims # like the pool itself does — take the lease, then the flip.
db.acquire_claim_lease(d2)
db.claim_drawing(d2, "VsVrsKEyWalletLongPoseForMinerTwo00", coin="vrsc")
_vpid2 = db.add_payout(_v, 2.375, "jackpot", drawing_id=d2,
                       coin="vrsc", amount_coin=123.45)
db.update_payout_status(_vpid2, "pending", "pending_manual_treasury")

html = client.get("/winners").text
check("winners page renders", "Past drawings" in html)
check("XMR prize stays plain XMR (< after the currency; no equiv word)",
      "3.0000 XMR<" in html and "3.0000 XMR-equiv" not in html)
check("claimed VRSC win shows the treasury quantity",
      "123.450000 VRSC" in html)
check("claimed VRSC win marks the pending treasury send",
      "(pending treasury send)" in html)
check("unclaimed Verus win says equivalent, never invents VRSC",
      "1.5000 XMR-equiv · pays in VRSC" in html and
      "1.5000 VRSC" not in html)
check("column header promises no coin", ">Prize</th>" in html
      and "Prize (XMR)" not in html)

# A claimed-VRSC drawing with NO payout row must still render: the
# fixed quantity lives on the payout ledger alone, so the drawing
# degrades to the equivalent-only cell, never a 500, never a lie.
with db.get_db() as conn:
    conn.execute("DELETE FROM payouts WHERE id = ?", (_vpid2,))
_toks = srv._winners_tokens()
check("claimed-VRSC drawing degrades to 'paid in VRSC' without ledger facts",
      "paid in VRSC" in _toks["{{DRAW_ROWS}}"]
      and "123.450000 VRSC" not in _toks["{{DRAW_ROWS}}"])

print("---")
# THE FLEET LINE: parsed for the ledger like every other drive's.
print("RESULT: %d passed, %d failed" % (sum(results), results.count(False)))
sys.exit(0 if all(results) else 1)
