"""test_admin_jackpot_source.py — THE ADMIN POT IS THE LEDGER (Oct 26).

The admin dashboard's "Current Jackpot" card must be the pot the pool
actually holds. It must NEVER be the referral PROJECTION — that table
(jackpot_projection) is an advertisement: a simulated or estimated
"possible future jackpot", refreshed hourly from Kraken referral
status. A projection posing as the pot in an operator's dashboard would
tell them they hold money they do not.

What this file pins:
  1. GATE: the number is behind /api/admin/stats (401 anonymous)
  2. THE VALUE IS THE LEDGER: with an absurd projection row planted in
     the table (9,999,999), the endpoint still answers the ledger's
     2.0 XMR — and that absurd number appears NOWHERE in the payload
  3. IT FOLLOWS THE LEDGER: raise the balance, subtract a drawn-unpaid
     payout, and the card follows each move while the projection row
     sits unchanged
  4. NO PROJECTION KEYS: neither the payload nor the client line that
     paints the card carries possible_future_jackpot / referral_seed /
     current_jackpot_usd
  5. THE PROJECTION CARD NEVER MOUNTS IN ADMIN: referral_projection.js
     targets the public hero (#heroJackpot); the admin panel has no
     such element
  6. HERMETIC: one sandbox DB, the rate pinned, no network

House conventions: check(n, cond, why), RESULT line, sys.exit.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import database as db                                        # noqa: E402

_tmp = tempfile.mkdtemp(prefix="admin_jackpot_")
db.DB_PATH = Path(_tmp) / "jackpot.db"
db.init_db()

# THE RATE IS PINNED: the admin card is XMR, but /api/stats also quotes
# dollars and this file must not depend on the market to grade a source.
db.set_setting("XMR_USD_RATE", "500.0")

from fastapi.testclient import TestClient                     # noqa: E402
import server as srv                                          # noqa: E402

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


def plant_projection(current, possible, seed=0.0, source="kraken_referral_sim_v1"):
    """One row in the ADVERTISEMENT table: a simulated projection that
    claims a pot far larger than anything the pool holds."""
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO jackpot_projection (possible_future_jackpot,"
            " referral_seed, current_jackpot, count_of_users_included,"
            " anomaly, source) VALUES (?, ?, ?, ?, ?, ?)",
            (possible, seed, current, 6, 0, source))


def admin_stats():
    r = CLIENT.get("/api/admin/stats")
    return r.status_code, (r.json() if r.status_code == 200 else {})


print("== 1. GATE ==")
check(1.1, CLIENT.get("/api/admin/stats").status_code == 401,
      "anonymous cannot read the admin pot")
_uid = db.create_user("pot_admin", "x", email="pot@example.test", is_admin=True)
_tok = "admin-jackpot-token"
db.create_session(_tok, _uid, int(time.time() + 3600))
CLIENT.cookies.set("session", _tok)
_status, _body = admin_stats()
check(1.2, _status == 200 and "jackpot_current" in _body,
      f"the admin reads it from /api/admin/stats: {_status}")

print("\n== 2. THE VALUE IS THE LEDGER, NEVER THE PROJECTION ==")
db.set_setting("jackpot_balance", "2.0")                      # the real pot
plant_projection(9_999_999.0, 12_000_000.0)                    # the advert
_, _body = admin_stats()
check(2.1, abs(float(_body["jackpot_current"]) - 2.0) < 1e-9,
      f"a 9,999,999 projection row does not move the card: {_body['jackpot_current']} XMR")
check(2.2, "9999999" not in json.dumps(_body).replace(".", "")
      and "12000000" not in json.dumps(_body).replace(".", ""),
      "…and the projection's numbers appear NOWHERE in the payload")
check(2.3, not ({"possible_future_jackpot", "referral_seed",
                 "current_jackpot_usd", "possible_future_jackpot_usd"} & set(_body)),
      f"the payload carries no projection keys: {sorted(_body)}")

print("\n== 3. IT FOLLOWS THE LEDGER (the source is the balance, not a constant) ==")
db.set_setting("jackpot_balance", "3.5")
_, _body = admin_stats()
check(3.1, abs(float(_body["jackpot_current"]) - 3.5) < 1e-9,
      f"raise the balance to 3.5 -> the card follows: {_body['jackpot_current']}")
with db.get_db() as conn:
    _row = conn.execute("SELECT current_jackpot FROM jackpot_projection "
                        "ORDER BY id DESC LIMIT 1").fetchone()
check(3.2, abs(float(_row["current_jackpot"]) - 9_999_999.0) < 1e-9,
      "…while the projection row sits unchanged (the two are not one number)")

with db.get_db() as conn:
    conn.execute(
        "INSERT INTO drawings (jackpot_amount, house_tax, status,"
        " claim_status, drawn_at) VALUES (0.5, 0.05, 'drawn', 'pending', ?)",
        (time.time(),))
_, _body = admin_stats()
check(3.3, abs(float(_body["jackpot_current"]) - 3.05) < 1e-6,
      f"a drawn-but-unpaid prize subtracts (3.5 - 0.45): {_body['jackpot_current']}")

with db.get_db() as conn:
    conn.execute(
        "INSERT INTO drawings (jackpot_amount, house_tax, status,"
        " claim_status, drawn_at) VALUES (0.2, 0.02, 'drawn', 'claimed', ?)",
        (time.time(),))
_, _body = admin_stats()
check(3.4, abs(float(_body["jackpot_current"]) - 3.05) < 1e-6,
      f"a CLAIMED prize subtracts nothing (the money already left): "
      f"{_body['jackpot_current']}")

_public = CLIENT.get("/api/stats").json()
check(3.5, abs(float(_body["jackpot_current"]) - float(_public["jackpot_xmr"])) < 1e-6,
      "the admin card and the public hero read ONE balance "
      f"({_body['jackpot_current']} = {_public['jackpot_xmr']})")
check(3.6, abs(float(_public["jackpot_usd"])
               - round(float(_public["jackpot_xmr"]) * 500.0, 2)) < 0.01,
      f"…and the dollar figure is that XMR at the pinned rate: "
      f"${_public['jackpot_usd']}")

print("\n== 4. THE CLIENT AND THE HANDLER, STRUCTURALLY ==")
_app_js = (HERE / "pool" / "static" / "app.js").read_text(encoding="utf-8")
_line = next((ln.strip() for ln in _app_js.splitlines()
              if "adminJackpot" in ln and "textContent" in ln), "")
check(4.1, "jackpot_current" in _line,
      f"the card is painted from jackpot_current: {_line}")
check(4.2, not any(k in _line for k in ("possible_future_jackpot",
                                        "referral_seed", "current_jackpot_usd")),
      "…and that line names no projection field")

_srv = (HERE / "pool" / "server.py").read_text(encoding="utf-8")
_start = _srv.index('@app.get("/api/admin/stats")')
_end = _srv.index("@app.", _start + 10)
_handler = _srv[_start:_end]
check(4.3, "engine.get_jackpot_balance()" in _handler,
      "the handler takes the pot from the engine's balance")
check(4.4, not any(k in _handler for k in ("jackpot_projection",
                                           "latest_projection",
                                           "projection_ad", "kr.")),
      "…and never reads the projection table or the referral module")

print("\n== 5. THE PROJECTION CARD NEVER MOUNTS IN ADMIN ==")
_proj_js = (HERE / "pool" / "static" / "referral_projection.js").read_text(encoding="utf-8")
check(5.1, 'getElementById("heroJackpot")' in _proj_js,
      "the advertisement card targets the public hero plate")
_panel = (HERE / "pool" / "templates" / "admin_panel.html").read_text(encoding="utf-8")
check(5.2, "heroJackpot" not in _panel,
      "the admin panel carries no hero plate for it to mount on")
check(5.3, "Current Jackpot" in _panel,
      "the admin card is labelled 'Current Jackpot' — the real pot, by name")

print("\n== 6. HERMETIC ==")
check(6.1, str(db.DB_PATH).startswith(_tmp),
      "every write in this file landed in the sandbox DB")
check(6.2, str(HERE / "pool" / "jackpot.db") != str(db.DB_PATH),
      "the live jackpot.db was never opened")

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
