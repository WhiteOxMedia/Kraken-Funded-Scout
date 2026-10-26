"""test_entry_rule.py — ONE RULE, THREE SURFACES (Oct 24).

The memo stated the entry rule twice and differently, and the code only
implemented one of the two readings. The rule is settled now, and this
drive grades that the HOMEPAGE, the ENGINE and the DASHBOARD state it
with the same number:

    THE RULE
      1 unit  = 1 valid share accepted by the pool.
      1 entry = ENTRY_UNIT units (operator setting, default 100).
      A token IS an entry — one ledger, no second balance.
      The Pro unlock counts contributing DAYS; it awards +1 entry and
      is never gated or accelerated by tokens/entries.

What is actually under test: the number is not baked into three places.
The engine reads it (entry_unit), /api/stats carries it to the
dashboard counter, and the homepage's {{ENTRY_UNIT}} is filled from the
same reader — so an operator who tunes the setting cannot leave the
page promising odds the pool does not pay.

House conventions: check(n, cond, why), RESULT line, sys.exit.
"""
import re
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import database as db                                        # noqa: E402

_tmp = tempfile.mkdtemp(prefix="entry_rule_")
db.DB_PATH = Path(_tmp) / "jackpot.db"
db.init_db()

# ── the wallet RPC stub: no node in this drive ───────────────────────
_wallet = types.ModuleType("monero_wallet")
_wallet.rpc_available = lambda: False
_wallet.create_subaddress = lambda label="": (None, None)
_wallet.get_transfers = lambda min_height=0, subaddr_indices=None: []
_wallet.get_transfer_by_txid = lambda txid: None
sys.modules["monero_wallet"] = _wallet
sys.modules["pool.monero_wallet"] = _wallet

import jackpot_engine as engine                              # noqa: E402
import server                                                # noqa: E402
from fastapi.testclient import TestClient                    # noqa: E402

# server.py and pool/*.py hold two `database` module objects; point both
# at the sandbox (in production they resolve to the same file).
import pool.database as _pkgdb                               # noqa: E402
_pkgdb.DB_PATH = db.DB_PATH
_pkgdb.init_db()

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}  {why}".rstrip())


def _visible(html):
    """Rendered words: tags, comments, scripts and styles stripped."""
    html = re.sub(r"<script\b.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style\b.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html)


print("== 1. THE ENGINE MINTS BY THE UNIT ==")

db.set_setting("free_tickets_per_shares", "50")
check(1.1, engine.entry_unit() == 50,
      "entry_unit() reads the operator setting (50)")
check(1.2, engine.get_pool_stats()["free_tickets_per_shares"] == 50,
      "the pool stats carry the same number the minting reads")

WALLET = "4" + "entry".ljust(94, "e")
USER = db.create_user("miner50", "hash", wallet_address=WALLET)
MINER = db.get_or_create_miner(WALLET, "rig-1")


def _mine(shares):
    db.update_miner_stats(WALLET, shares, 0, 0)
    return engine.allocate_free_tickets(MINER["id"], shares)


def _tickets():
    return db.get_miner_ticket_count(MINER["id"])


_mine(49)
check(1.3, _tickets() == 0,
      "49 of 50 units mint nothing — an entry is whole or not at all")
_mine(1)
check(1.4, _tickets() == 1,
      "the 50th unit completes exactly one entry")
_mine(100)
check(1.5, _tickets() == 3,
      "100 more shares buy two more entries (the partial unit carried)")
check(1.6, db.get_or_create_xp(MINER["id"])["ticket_units"] == 150,
      "units are banked on the miner row, not lost on disconnect"
      " (49 + 1 + 100 = 150 units, 3 entries minted)")

# 0 / negative / junk are MISCONFIGURATION, not switches: one number,
# one meaning — the pool falls back to the default and keeps minting.
# A fresh miner per case: switching units mid-stream would leave the
# already-minted count above the new expectation, which mints nothing
# (correct behaviour, but not what this check is about).
for _i, _bad in enumerate(("0", "-5", "junk")):
    db.set_setting("free_tickets_per_shares", _bad)
    _w = "4" + f"bad{_i}".ljust(94, "b")
    db.update_miner_stats(_w, 100, 0, 0)
    _m = db.get_or_create_miner(_w, f"rig-bad{_i}")
    engine.allocate_free_tickets(_m["id"], 100)
    check(f"1.7 ({_bad})", engine.entry_unit() == 100
          and db.get_miner_ticket_count(_m["id"]) == 1,
          f"setting {_bad!r} falls back to 100 and still mints by it")

print("== 2. THE HOMEPAGE QUOTES THE SAME NUMBER ==")

db.set_setting("free_tickets_per_shares", "50")
client = TestClient(server.app)
home = client.get("/")
check(2.1, home.status_code == 200, f"the homepage serves ({home.status_code})")
_html = home.text
check(2.2, "{{ENTRY_UNIT}}" not in _html,
      "no unfilled unit token on the rendered page")
_text = _visible(_html)
check(2.3, "Every 50 valid shares you mine becomes one raffle entry" in _text,
      "the hero states the rule with the operator's unit")
check(2.4, "1 free entry per 50 shares mined" in _text,
      "the economics tile agrees")
check(2.5, "Step 2 — Every 50 shares is one entry" in _text,
      "the how-to step agrees")
check(2.6, "one entry for every 50 accepted shares" in _text,
      "the winners page's draw rule agrees")
check(2.7, "Raffle ticket: 0 / 50 units collected" in _text,
      "the default counter reads 50 before any JS runs")
check(2.8, "100 valid shares" not in _text and "Every share you mine becomes" not in _text,
      "no surface still states the old 1-share rule")

howto = client.get("/how-to-play")
check(2.9, howto.status_code == 200
      and "one entry per 50 accepted shares" in howto.text,
      "the /how-to-play meta description quotes the same unit")
check("2.10", "{{ENTRY_UNIT}}" not in howto.text,
      "the standalone page fills the token too")

print("== 3. THE DASHBOARD DRAWS FROM THE SAME SOURCE ==")

stats = client.get("/api/stats").json()
check(3.1, stats.get("free_tickets_per_shares") == 50,
      f"/api/stats publishes the unit the page and engine use: {stats.get('free_tickets_per_shares')}")
_appjs = (HERE / "pool" / "static" / "app.js").read_text(encoding="utf-8")
check(3.2, "free_tickets_per_shares" in _appjs
      and "TICKETS_PER = d.free_tickets_per_shares" in _appjs,
      "the dashboard counter takes its unit from that field, not a constant")
check(3.3, "const FREE_TICKET_SHARES = 100;" in _appjs,
      "the only hardcoded 100 left is the documented offline fallback")

print("== 4. A TOKEN IS AN ENTRY, AND THE UNLOCK IS DAYS-ONLY ==")

# The word may only survive where it means the Scout IDENTITY ("Token
# ownership" on the wallet panel), never as a balance the member earns.
_currency = [p for p in ("token balance", "earn tokens", "tokens per",
                         "100 tokens", "token gate", "spend tokens",
                         "token reward", "token count") if p in _text.lower()]
check(4.1, not _currency,
      f"no visible copy sells a second currency called a token: {_currency}")
check(4.2, engine.ENTRY_UNIT_SETTING == "free_tickets_per_shares",
      "the unit setting is a named constant, not a magic string")
_prog = db.pro_progress(MINER["id"], USER)
check(4.3, not [k for k in _prog if "token" in k],
      f"the progression payload has no token balance: {sorted(_prog)}")

# THE PROOF THAT TOKENS DO NOT GATE THE UNLOCK: an account with 99
# contributing days and NO earned entries submits a single unit — it
# mints no entry from shares — and still unlocks Pro on the 100th day.
db.set_setting("free_tickets_per_shares", "100")
W2 = "4" + "daysonly".ljust(94, "d")
U2 = db.create_user("daycounter", "hash", wallet_address=W2)
M2 = db.get_or_create_miner(W2, "rig-2")
# the xp row must exist before it can be seeded (get_or_create_xp would
# otherwise create a fresh 0-day row on the first allocation)
db.get_or_create_xp(M2["id"])
with db.get_db() as conn:
    conn.execute("UPDATE miner_xp SET jackpot_days = 99, last_jackpot_day = ? "
                 "WHERE miner_id = ?", ("2000-01-01", M2["id"]))
db.update_miner_stats(W2, 1, 0, 0)
_minted = engine.allocate_free_tickets(M2["id"], 1)
check(4.4, _minted == 0,
      "one share buys no entry at unit 100 — the miner holds nothing")
check(4.5, db.get_user(U2)["scout_pro_lifetime"] == 1,
      "the 100th contributing day unlocks Pro anyway — no token gate")
check(4.6, db.get_miner_ticket_count(M2["id"]) == 1,
      "the unlock's +1 entry is the ONLY entry the account holds")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
