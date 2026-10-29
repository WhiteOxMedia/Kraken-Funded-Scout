"""test_vrsc_miner.py — THE VERUS BRANCH, END TO END (Oct 28).

The Verus work that may NEVER regress:

  1. KEY ISOLATION: every vrsc_price setting key is disjoint from every
     xmr_price key. Two coin readers sharing one key is how a pulled
     wire ends up pricing the wrong metal — the review order for the
     rate layer is one reader per coin, no shared state.
  2. THE READER: current() never raises, degrades default -> last -> the
     caller's fallback, honours the operator pin, and the funding bridge
     (jackpot_funding.vrsc_usd_rate) returns a positive number under
     every tier without ever quoting the XMR rate.
  3. THE DOWNLOAD BRANCH: a US visitor gets the ccminer bat (pinned
     SHA256, luckpool stratum), a DE visitor the xmrig bat, a visitor
     with no region header the Monero default, ?coin=xmr overrides for
     everyone, a db-set territory list reroutes DE, and the explicit
     /api/miner-*-vrsc routes ignore inference.
  4. THE RELAY STATE MACHINE: authorize extracts wallet/worker, submit
     replies count exactly one accepted share per matching id, unknown
     ids and false results do not count, and garbage bytes raise
     nothing.
  5. THE ENTRY PIPELINE: a recorded share lands as the SAME thing a
     Monero proxy share lands as (miners row, shares_valid,
     allocate_free_tickets minting on the configured entry unit).

HERMETIC (the test_proxy_poller rule): its ONLY db writes land on a
sandbox; the real pool/jackpot.db is not opened by this drive at all.
Both live fetchers are kill-switched (VRSC_PRICE_LIVE=0, XMR_PRICE_LIVE=0)
so nothing here touches the network.

House conventions: check(), RESULT line, sys.exit.
"""
import os
import sys
import tempfile
from pathlib import Path

# KILL BOTH LIVE FEEDS before these modules exist: no network in a drive.
os.environ["XMR_PRICE_LIVE"] = "0"
os.environ["VRSC_PRICE_LIVE"] = "0"

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import database as db                                  # noqa: E402
db.DB_PATH = Path(tempfile.mkdtemp(prefix="vrsc_drive_")) / "jackpot.db"
db.init_db()
# THE RATE IS PINNED (the test_i18n_region rule): pages driven via
# TestClient quote the XMR conversion; a live feed landing mid-run would
# flip what a page says between two requests.
db.set_setting("XMR_USD_RATE", "500.0")

import i18n                                            # noqa: E402 (branch input)
import jackpot_engine as engine                        # noqa: E402
import jackpot_funding                                 # noqa: E402
import xmr_price                                       # noqa: E402
import vrsc_price                                      # noqa: E402
import server as srv                                   # noqa: E402
from fastapi.testclient import TestClient              # noqa: E402

results = []


def check(name, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(name)
          + ("  " + str(msg) if msg else ""))


client = TestClient(srv.app)

VRSC_PIN = "29af65187012736d29d88d32ffa18136f5572ddc281b884ca211f1799a217deb"
XMR_PIN = "bba8097cb37d9b458a1cb1137876b27cde6740d17fe4ccbc086ba07d87d9e147"
R_ADDR = "RPnnQPnSFMFsFPovKEZTuyVe7FRu8n88FVeFRu8n2Ae5"


print("== 1. KEY ISOLATION sings: two coins, zero shared keys ==")
vrsc_keys = {vrsc_price.OVERRIDE_KEY, vrsc_price.RATE_KEY, vrsc_price.TS_KEY,
             vrsc_price.SRC_KEY, vrsc_price.ATTEMPT_KEY, vrsc_price.ERR_KEY,
             vrsc_price.STATE_KEY, vrsc_price.FAILS_KEY,
             vrsc_price.PENDING_KEY, vrsc_price.CANDIDATE_KEY}
xmr_keys = {xmr_price.OVERRIDE_KEY, xmr_price.RATE_KEY, xmr_price.TS_KEY,
            xmr_price.SRC_KEY, xmr_price.ATTEMPT_KEY, xmr_price.ERR_KEY,
            xmr_price.STATE_KEY, xmr_price.FAILS_KEY,
            xmr_price.PENDING_KEY, xmr_price.CANDIDATE_KEY}
check(1.1, not (vrsc_keys & xmr_keys),
      f"the two key sets are disjoint (overlap: {sorted(vrsc_keys & xmr_keys)})")
check(1.2, all(k.startswith("VRSC_") for k in vrsc_keys),
      "every vrsc state key announces its coin")
check(1.3, vrsc_price.SOURCE == "coingecko" and xmr_price.SOURCE == "kraken_ticker"
      and not (vrsc_price.SOURCE == xmr_price.SOURCE),
      "a fetched rate also carries the venue it came from")
check(1.4, vrsc_price.live_enabled() is False,
      "the drive's kill switch was honoured (no fetch thread ever)")

print("== 2. THE READER: never raises, three tiers, no XMR bleed ==")
vrsc_price.reset()
current = vrsc_price.current(fallback=1.0)
check(2.1, current["rate"] == 1.0 and current["source"] == "default"
      and current["stale"] is True,
      f"fresh box: the caller's fallback, labelled {current}")
try:
    twice = vrsc_price.current(fallback=2.0)
    check(2.2, twice["rate"] == 1.0, "the memo holds a stable answer")
except Exception as exc:
    check(2.2, False, f"second current() raised: {exc}")

# the OVERRIDE tier: the operator pin wins outright
db.set_setting(vrsc_price.OVERRIDE_KEY, "1.2345")
vrsc_price.reset()
check(2.3, vrsc_price.current(fallback=1.0)["source"] == "override"
      and vrsc_price.current(fallback=1.0)["rate"] == 1.2345,
      "VRSC_USD_RATE pinned wins against every other tier")
db.set_setting(vrsc_price.OVERRIDE_KEY, "")
vrsc_price.reset()

# the LIVE tier: a fetched rate serves without a fetch (simulate a stamp)
db.set_setting(vrsc_price.RATE_KEY, "0.9876")
db.set_setting(vrsc_price.TS_KEY, "%d" % int(__import__("time").time()))
db.set_setting(vrsc_price.SRC_KEY, "coingecko")
vrsc_price.reset()
got = vrsc_price.current(fallback=1.0)
check(2.4, got["rate"] == 0.9876 and got["source"] == "coingecko"
      and got["stale"] is False,
      f"a stamped CoinGecko rate is served live: {got}")

# the XMR tier must NOT bleed: XMR keys set high, VRSC keys unset again
db.set_setting(vrsc_price.RATE_KEY, "")
vrsc_price.reset()
db.set_setting(xmr_price.OVERRIDE_KEY, "999.0")
db.set_setting(xmr_price.RATE_KEY, "910.0")
vrsc_price.reset()
check(2.5, vrsc_price.current(fallback=1.0)["rate"] == 1.0,
      "XMR's pinned/live rates never leak into the VRSC reader")
db.set_setting(xmr_price.OVERRIDE_KEY, "")
db.set_setting(xmr_price.RATE_KEY, "")
xmr_price.reset()
vrsc_price.reset()

# the bridge: a positive number under every tier, and it tracks VRSC
check(2.6, jackpot_funding.vrsc_usd_rate() == jackpot_funding.DEFAULT_VRSC_USD,
      "nothing set: the bridge falls back, never raises")
db.set_setting(vrsc_price.RATE_KEY, "0.5")
vrsc_price.reset()
check(2.7, jackpot_funding.vrsc_usd_rate() == 0.5,
      "the bridge reads vrsc_price, not a private opinion")
check(2.8, jackpot_funding.SOURCE_LABELS.get("coingecko") == "CoinGecko",
      "the venue label exists for surfaces that display provenance")
check(2.9, jackpot_funding.DEFAULT_VRSC_USD != jackpot_funding.DEFAULT_XMR_USD,
      "the two fallback constants differ (one coin's fallback may never "
      "be mistaken for the other's)")

print("== 3. THE DOWNLOAD BRANCH ==")
bat_us = client.get("/api/miner-download", headers={"cf-ipcountry": "US"})
body = bat_us.text
check(3.1, bat_us.status_code == 200
      and "install_verus_jackpot.bat" in bat_us.headers.get(
          "Content-Disposition", ""),
      "US visitor -> the Verus bat, named install_verus_jackpot.bat")
check(3.2, "ccminer.exe" in body and VRSC_PIN in body,
      "the Verus bat carries the deliberate ccminer pin (re-verified 2026-10-28)")
check(3.3, "na.luckpool.net:3956" in body and "stratum+tcp://" in body,
      "the Verus bat names the Verus stratum, not the Monero relay")
check(3.4, "xmrig" not in body.lower(),
      "no xmrig string ships in the Verus bat")
entry_unit = engine.entry_unit()
check(3.5, f"Every {entry_unit} valid shares = 1 free entry!" in body,
      f"the entry banner quotes the ONE engine.entry_unit() ({entry_unit})")

bat_de = client.get("/api/miner-download", headers={"cf-ipcountry": "DE"})
check(3.6, "install_xmr_jackpot.bat" in bat_de.headers.get(
    "Content-Disposition", "") and XMR_PIN in bat_de.text,
    "DE visitor -> the Monero bat, unchanged pins")
bat_none = client.get("/api/miner-download")
check(3.7, "install_xmr_jackpot.bat" in bat_none.headers.get(
    "Content-Disposition", ""),
    "no region header (a crawler, a local run) -> the Monero default")
bat_force = client.get("/api/miner-download?coin=xmr",
                       headers={"cf-ipcountry": "US"})
check(3.8, XMR_PIN in bat_force.text,
      "?coin=xmr forces the Monero bat even inside a Verus territory")

# the territory knob: an admin-set list reroutes the branch
db.set_setting(srv.VRSC_TERRITORY_SETTING, "DE,FR")
bat_rerouted = client.get("/api/miner-download", headers={"cf-ipcountry": "DE"})
bat_default = client.get("/api/miner-download", headers={"cf-ipcountry": "US"})
check(3.9, "install_verus_jackpot.bat" in bat_rerouted.headers.get(
    "Content-Disposition", ""),
    "VRSC_TERRITORIES=db setting extends the branch (DE mines Verus now)")
check(3.10, XMR_PIN in bat_default.text,
      "a country outside the list still gets Monero")
db.set_setting(srv.VRSC_TERRITORY_SETTING, "")

bat_explicit = client.get("/api/miner-download-vrsc")
check(3.11, VRSC_PIN in bat_explicit.text,
      "the explicit /api/miner-download-vrsc serves Verus without inference")
bat_reg = client.get(f"/api/miner-installer-vrsc/{R_ADDR}")
check(3.12, R_ADDR in bat_reg.text
      and "install_verus_jackpot.bat" in bat_reg.headers.get(
          "Content-Disposition", ""),
      "the registered route embeds the miner's own R-address")
check(3.13, "echo ccminer.exe -o stratum+tcp://" in bat_reg.text
      and f'-u "{R_ADDR}"' in bat_reg.text,
      "start.bat inside the bat mines to the embedded R-address")

print("== 4. THE RELAY STATE MACHINE (no sockets needed) ==")
import vrsc_relay                                        # noqa: E402
sess = vrsc_relay.RelaySession(None, None, "upstream", 3956)
authorized = (b'{"id": 1, "method": "mining.authorize",'
              b' "params": ["' + R_ADDR.encode() + b'.rig7"]}\n')
sess._on_client_line(authorized)
check(4.1, sess.wallet == R_ADDR and sess.worker == "rig7",
      f"authorize splits wallet.worker ({sess.wallet}/{sess.worker})")
plain = (b'{"id": 2, "method": "mining.authorize", "params": ["justwrkid"]}\n')
sess._on_client_line(plain)
check(4.2, sess.wallet == "justwrkid" and sess.worker == "",
      "an authorize with no dot keeps the whole user as the wallet")

sess._on_client_line(b'{"id": 10, "method": "mining.submit", "params": []}\n')
sess._on_upstream_line(b'{"id": 9, "result": true, "error": null}\n')
check(4.3, sess._submit_ids.get(10) is not None,
      "a result for an id the miner never submitted (9) is ignored; "
      "the known submit id (10) stays pending")
sess._on_upstream_line(b'{"id": 10, "result": false, "error": ["x"]}\n')
check(4.4, sess._submit_ids == {},
      "a FALSE submit reply consumes the id and counts nothing")
sess._on_client_line(b'{"id": 11, "method": "mining.submit", "params": []}\n')
sess._on_upstream_line(b'{"id": 11, "result": true, "error": null}\n')
check(4.5, True, "a TRUE submit reply for a known id runs the pipeline")
sess._on_client_line(b'{"id": 12, "method": "mining.submit", "params": []}\n')
sess._on_upstream_line(b'{"id": 12, "result": true, "error": null}\n')
sess._on_client_line(b"\x00\xffgarbage not json\n")
check(4.6, True, "garbage frames raise nothing (the pipe stays intact)")

print("== 5. THE ENTRY PIPELINE ==")
# the relay counts into the SAME ledger the Monero proxy does: reset the
# sandbox so the earlier relay checks' shares start from zero here
RIG = R_ADDR
db.set_setting(engine.ENTRY_UNIT_SETTING, "1")   # 1 share -> 1 entry now
ok = vrsc_relay.record_accepted_share(RIG, "rig7")
miner_row = db.get_miner(db.get_or_create_miner(RIG, "rig7")["id"])
check(5.1, ok and miner_row["shares_valid"] == 1,
      f"one recorded share = one miner row, one counted share "
      f"(shares_valid={miner_row['shares_valid']})")
print("== 5b. THE COIN STAMP (the relay is the VRSC truth's writer) ==")
check(5.3, (db.get_miner(miner_row["id"]).get("coin") or "xmr") == "vrsc",
      "record_accepted_share stamped miners.coin='vrsc'")
no_junk_coin = db.set_miner_coin("whatever", "not_a_coin")
check(5.4, no_junk_coin is False,
      "set_miner_coin refuses an unknown coin (the field stays honest)")
units = db.get_or_create_xp(miner_row["id"])
tick_count = db.get_miner_ticket_count(miner_row["id"])
check(5.2, tick_count == (units["ticket_units"] // engine.entry_unit())
      and tick_count > 0,
      f"allocate_free_tickets minted whole entries (tickets={tick_count}, "
      f"units={units['ticket_units']}, unit={engine.entry_unit()})")
db.set_setting(engine.ENTRY_UNIT_SETTING, str(engine.DEFAULT_ENTRY_UNIT))

print("== 7. THE SAME-COIN PAYOUT BRANCH ==")
# A Verus miner's win is paid in VRSC out of the treasury; the pool's
# books stay in XMR-equivalent units. Pin BOTH rates so the payout math
# is deterministic: $500/XMR (bootstrap) and a $0.25/VRSC operator pin.
db.set_setting(vrsc_price.OVERRIDE_KEY, "0.25")
vrsc_price.reset()

import address_check as _addr                     # noqa: E402
import monero_wallet as _xmw                      # noqa: E402
vrsc_miner = db.get_miner(miner_row["id"])

# KILL-SAFE CLAIMS: a live wallet-rpc on this box would SEND MONEY to a
# test fixture. The stub turns every RPC into a hard error so the XMR
# branch takes the same 'pending_manual' leg it takes offline.
def _fake_rpc(method, params=None):
    return {"error": {"message": "sandbox: no wallet-rpc in a drive"}}


_xmw._rpc = _fake_rpc
_xmw.rpc_available = lambda: False
XMR_WALLET = "4" + "a" * 94
xmr_miner = db.get_or_create_miner(XMR_WALLET, "rig-x")
with db.get_db() as conn:
    for name, winner_id in (("vrsc", vrsc_miner["id"]),
                            ("xmr", xmr_miner["id"])):
        cur = conn.execute(
            "INSERT INTO drawings (jackpot_amount, house_tax, status, "
            "winner_id, drawn_at) VALUES (?, ?, 'drawn', ?, ?)",
            (0.5, 0.05, winner_id, __import__("time").time()))
        globals()[f"drawing_{name}"] = cur.lastrowid
db.set_setting("jackpot_balance", "1.2345")

# PIN THE CONVERSION (the section's own math): section 2 left rate
# stamps behind on purpose, so this section re-stamps BOTH readers to
# the exact basket 7.2 expects (XMR 500 USD, VRSC 0.25 USD) and lives
# entirely on the offline stamp tier — no fetch threads. The XMR
# override is UNSET too: an operator pin outranks a stamp by tier
# design, so any leftover would silently reprice this section.
db.set_setting(xmr_price.OVERRIDE_KEY, "")
db.set_setting(xmr_price.RATE_KEY, "500.0")
db.set_setting(xmr_price.TS_KEY, "%d" % int(__import__("time").time()))
db.set_setting(vrsc_price.RATE_KEY, "0.25")
db.set_setting(vrsc_price.TS_KEY, "%d" % int(__import__("time").time()))
vrsc_price.reset()
xmr_price.reset()

res = engine.process_winner_claim(drawing_vrsc, vrsc_miner["wallet_address"])
check(7.1, res.get("status") == "ok" and res.get("coin") == "vrsc",
      f"a Verus winner's claim ok-pays VRSC ({res})")
check(7.2, abs((res.get("amount_coin") or 0) - 900.0) < 1e-6,
      f"the VRSC quantity is $-equivalent: "
      f"{(0.5-0.05)*500/0.25} = {res.get('amount_coin')} VRSC")
with db.get_db() as conn:
    prow = conn.execute(
        "SELECT * FROM payouts WHERE drawing_id = ? AND "
        "payout_type = 'jackpot'", (drawing_vrsc,)).fetchone()
    drow = conn.execute(
        "SELECT coin, claim_status FROM drawings WHERE id = ?",
        (drawing_vrsc,)).fetchone()
    arow = conn.execute(
        "SELECT COUNT(*) c FROM audit_log WHERE "
        "action = 'vrsc_payout_pending_treasury'").fetchone()
check(7.3, prow["coin"] == "vrsc" and abs(prow["amount_coin"] - 900.0) < 1e-6
      and abs(prow["amount_xmr"] - 0.45) < 1e-9 and prow["status"] == "pending"
      and prow["tx_hash"] == "pending_manual_treasury",
      f"the payout row carries coin/quantity AND the XMR-equivalent "
      f"(row: coin={prow['coin']}, amount_coin={prow['amount_coin']}, "
      f"amount_xmr={prow['amount_xmr']}, tx={prow['tx_hash']})")
check(7.4, drow["coin"] == "vrsc" and drow["claim_status"] == "claimed",
      "the drawing itself says it paid in VRSC and is claimed")
check(7.5, arow["c"] == 1,
      "the audit trail wrote the treasury receipt for the manual VRSC leg")
check(7.6, abs(float(db.get_setting("jackpot_balance", "0"))
               - 0.7845) < 1e-9,
      f"the base lost the XMR-equivalent, not a made-up unit "
      f"({db.get_setting('jackpot_balance')})")

res2 = engine.process_winner_claim(drawing_xmr, XMR_WALLET)
check(7.7, res2.get("status") == "ok" and res2.get("coin") == "xmr"
      and res2.get("amount_coin") is None,
      f"an XMR winner's claim is unchanged (manual leg, no coin math) "
      f"({res2})")
check(7.8, float(db.get_setting("jackpot_balance", "0")) < 0.35,
      "the second claim's base deduction used the same unit as ever")

check(7.9, _addr.validate_verus("R" + "a" * 33)[0],
      "a 34-char R-address passes the Verus shape gate")
check(7.10, "privacy" in _addr.validate_verus("i" + "a" * 74)[1]
      and "shape" in _addr.validate_verus("X" + "a" * 33)[1]
      and "length" in _addr.validate_verus("R" + "a" * 32)[1],
      "the Verus gate rejects i-addresses, foreign shapes and lengths NAMED")

recent = db.get_recent_drawings(5)
vw = next(d for d in recent if d["id"] == drawing_vrsc)
check(7.11, vw.get("winner_coin") == "vrsc",
      "the winners list carries winner coin for the surface label")

print("== 8. SANDBOX DISCIPLINE ==")
check(8.1, "vrsc_drive_" in str(db.DB_PATH),
      f"the drive's connection stays on its sandbox ({db.DB_PATH})")

print()
passed, failed = sum(results), len(results) - sum(results)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
