"""Pins the bundled installer + Scout visibility layer to their contracts:

  1. default is OPT-OUT        a fresh install record says so; --opt-out
                               never resurrects an opt-in
  2. valid_wallet              the server's own shape rule, local check
  3. opt-in (fake transport)   records opt-in, patches ONLY pools[0].user
                               into the xmrig config (every other key
                               preserved), grants exactly ONE welcome
                               ticket (proven in the sandbox DB), binds
                               the wallet
  4. idempotent welcome        a second opt-in grants no second ticket
  5. opt-out                   flips the record, touches nothing else
  6. dry-run                   describes the plan, writes NOTHING (the
                               config file is byte-identical after)
  7. no persistence            the installer writes no registry keys,
                               no scheduled tasks, no Startup entries —
                               source scan + CLI smoke
  8. phrase                    the mandated wording, byte-exact
  9. visibility whitelist      community_view DROPS non-whitelisted keys
                               (proven by injecting a forbidden key);
                               miner_view returns only the caller's own
                               state; no secrets vocabulary anywhere

Hermetic: temp-dir record + config, monkeypatched registration transport,
sandboxed DB (DB_PATH rebind) for the ticket grant and miner_view. The
live jackpot.db is never opened. Prints "RESULT: N passed, M failed".
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# the installer/ DIRECTORY would shadow installer.py as a namespace
# package from the root — the module itself is importable from inside it
sys.path.insert(0, str(HERE / "installer"))
sys.path.insert(0, str(HERE))

import installer

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


def _fake_transport(captured: list):
    """Faithful to the real /api/miner/register: get_or_create the miner
    (in the SANDBOX db) and return its id — a registration that creates
    nothing would hand opt_in a miner_id the ticket grant can't satisfy
    (the FK the drive caught)."""
    def _post(api_base, wallet, worker):
        captured.append((api_base, wallet, worker))
        pool_dir = str(HERE / "pool")
        if pool_dir not in sys.path:
            sys.path.insert(0, pool_dir)
        import database as pool_db
        pool_db.DB_PATH = _pool_db
        if not _pool_db.exists():
            pool_db.init_db()
        rec = pool_db.get_or_create_miner(wallet)
        return {"status": "ok", "miner_id": rec["id"]}
    return _post


print("== 1. default is opt-out ==")
_rec1 = Path(tempfile.mkdtemp(prefix="inst_d_")) / "rec.json"
check(1.1, installer.load_record(_rec1)["miner_bot_opt_in"] is False,
      "a fresh record has the checkbox OFF — nothing exists until chosen")
_rec1b = Path(tempfile.mkdtemp(prefix="inst_d_")) / "rec2.json"
_r1 = installer.opt_out(_rec1b)
check(1.2, _r1["ok"] and installer.load_record(_rec1b)[
          "miner_bot_opt_in"] is False,
      "opt-out on a fresh box stays opt-out (never resurrects)")

print("== 2. wallet validation (the server's own shape rule) ==")
GOOD = ("48jewb9eXcS7XhcLavv4WvqNkV4eY74DnVddKz9L4CVQCJvfcRdpAe3hTbCqfPi2"
        "bfWcUP4fR5fcAHTrjS7o6dZ5MoVcNhP")
check(2.1, installer.valid_wallet(GOOD), "a 95-char 4-prefix address passes")
check(2.2, not installer.valid_wallet("4short")
      and not installer.valid_wallet("x" * 95)
      and not installer.valid_wallet(""),
      "wrong prefix/length/empty all fail")

print("== 3-4. opt-in: record, config patch, ONE welcome ticket ==")
_tmp = tempfile.mkdtemp(prefix="inst_d_main_")
_rec = Path(_tmp) / "rec.json"
_cfg = Path(_tmp) / "config.json"
_cfg.write_text(json.dumps({
    "api": {"worker_id": "box1", "token": "SECRET"},
    "pools": [{"url": "mine.xmrjackpot.com:3333", "user": "OLDWALLET",
               "pass": "x"},
              {"url": "backup:3333", "user": "OTHER"}],
}), encoding="utf-8")
_captured = []
installer._post_register = _fake_transport(_captured)

# the welcome grant runs against a SANDBOX db: rebind the pool database
# module's DB_PATH before the grant's own import lands on it
_pool_db = Path(_tmp) / "jackpot.db"
_orig_grant = installer._grant_welcome_ticket


def _sandboxed_grant(miner_id):
    pool_dir = str(HERE / "pool")
    if pool_dir not in sys.path:
        sys.path.insert(0, pool_dir)
    import database as pool_db
    pool_db.DB_PATH = _pool_db
    if not _pool_db.exists():
        pool_db.init_db()
    return _orig_grant(miner_id)


installer._grant_welcome_ticket = _sandboxed_grant

_r3 = installer.opt_in(GOOD, api_base="http://127.0.0.1:1",
                       worker="rig1", record_path=_rec, config_path=_cfg,
                       live_db=True)
_sbox = sqlite3.connect(str(_pool_db))
_real_mid = _sbox.execute("SELECT id FROM miners WHERE wallet_address=?",
                          (GOOD,)).fetchone()
check(3.1, _r3.get("ok") and _real_mid
      and _r3.get("miner_id") == _real_mid[0],
      f"opt-in succeeded and the miner_id is the REAL registry row's id "
      f"({_r3.get('miner_id')})")
_rec_d = json.loads(_rec.read_text(encoding="utf-8"))
check(3.2, _rec_d["miner_bot_opt_in"] is True
      and _rec_d["wallet_address"] == GOOD
      and _rec_d["registration"]["miner_id"] == _real_mid[0],
      "the record binds the wallet and the registration")
_c = json.loads(_cfg.read_text(encoding="utf-8"))
check(3.3, _c["pools"][0]["user"] == GOOD
      and _c["pools"][1]["user"] == "OTHER"
      and _c["api"]["worker_id"] == "box1"
      and _c["api"]["token"] == "SECRET",
      "ONLY pools[0].user changed — every other key preserved (secrets "
      "in api untouched)")
check(3.4, len(_captured) == 1 and _captured[0][1] == GOOD
      and _captured[0][0] == "http://127.0.0.1:1",
      "exactly one registration call, to the given API base, with the "
      "wallet")
_conn = sqlite3.connect(str(_pool_db))
_n_welcome = _conn.execute("SELECT COUNT(*) FROM tickets WHERE "
                           "miner_id=?", (_real_mid[0],)).fetchone()[0]
check(3.5, _n_welcome == 1,
      f"exactly ONE welcome ticket granted (type 'free' per the schema's "
      f"CHECK; sandbox DB says {_n_welcome})")
# 4. idempotent: a second opt-in grants no second ticket (the miner now
# HAS a ticket — the server-side ticket-less rule stays quiet)
installer.opt_in(GOOD, api_base="http://127.0.0.1:1", worker="rig1",
                 record_path=_rec, config_path=_cfg, live_db=True)
_n2 = _conn.execute("SELECT COUNT(*) FROM tickets WHERE "
                    "miner_id=?", (_real_mid[0],)).fetchone()[0]
check(4.1, _n2 == 1, f"second opt-in granted nothing more ({_n2})")

print("== 5. opt-out touches nothing else ==")
_r5 = installer.opt_out(_rec)
_d5 = json.loads(_rec.read_text(encoding="utf-8"))
_c5 = json.loads(_cfg.read_text(encoding="utf-8"))
check(5.1, _d5["miner_bot_opt_in"] is False and _r5["ok"],
      "the record flips to opted-out")
check(5.2, _c5["pools"][0]["user"] == GOOD
      and _c5["api"]["token"] == "SECRET",
      "the xmrig config is untouched by opt-out (a stopped miner mines "
      "nothing; the installer does not scrub configs)")
check(5.3, installer.load_record(_rec)["miner_bot_opt_in"] is False,
      "reloading the record after opt-out: still out (no resurrection)")

print("== 6. dry-run writes NOTHING ==")
_rec6 = Path(_tmp) / "rec6.json"
_cfg6 = Path(_tmp) / "cfg6.json"
_cfg6.write_text('{"pools": [{"user": "KEEP"}]}', encoding="utf-8")
_before = _cfg6.read_bytes()
_captured.clear()
_r6 = installer.opt_in(GOOD, api_base="http://127.0.0.1:1", worker="d",
                       dry_run=True, record_path=_rec6, config_path=_cfg6)
check(6.1, _r6.get("ok") and _r6.get("dry_run")
      and _r6["plan"]["patch_xmrig"] is True,
      "the plan is described (patch_xmrig true)")
check(6.2, not _rec6.exists() and _cfg6.read_bytes() == _before
      and len(_captured) == 0,
      "no record file, byte-identical config, zero registration calls")

print("== 7. no persistence anywhere ==")
import re as _re
_src = (HERE / "installer" / "installer.py").read_text(encoding="utf-8")
_bad = [tok for tok in ("import winreg", "schtasks ", "HKEY_",
                        "os.startfile") if tok in _src]
check(7.1, not _bad,
      "API-level scan clean - no persistence calls; prose mentions are "
      "excluded by design")
try:
    _pr = subprocess.run(
        [sys.executable, str(HERE / "installer" / "installer.py"),
         "--status"], capture_output=True, text=True, timeout=30,
        cwd=str(HERE))
    _parsed = json.loads(_pr.stdout)
    check(7.2, _pr.returncode == 0 and "miner_bot_opt_in" in _parsed,
          "--status runs clean and speaks the record schema (read-only "
          "CLI smoke)")
except Exception as _e:
    check(7.2, False, f"CLI smoke failed: {_e!r}")

print("== 8. the phrase ==")
check(8.1, installer.GETTING_STARTED
      == "Not sure where to get started? Start here: xmrjackpot.com",
      "byte-exact mandated wording")
check(8.2, "installer.GETTING_STARTED"
      in (HERE / "installer" / "installer_ui.py").read_text(
          encoding="utf-8"),
      "the UI renders the shared constant — one phrase, one source of "
      "truth")

print("== 9. visibility: whitelist + own-data-only ==")
import scout_jackpot_view as sjv
check(9.1, "real_miners" not in sjv.SAFE_STATS_KEYS
      and "p2pool_hashrate" not in sjv.SAFE_STATS_KEYS
      and "house_tax_accumulated" not in sjv.SAFE_STATS_KEYS,
      "the whitelist excludes the network-probing / internal fields")
with tempfile.TemporaryDirectory(prefix="inst_d_view_") as td:
    _sdb = Path(td) / "jackpot.db"
    _orig = sjv.db.DB_PATH
    sjv.db.DB_PATH = _sdb
    sjv.db.init_db()
    _mid = sjv.db.get_or_create_miner(GOOD)["id"]
    sjv.db.add_tickets(_mid, "free", 3)
    # the FILTER proof: inject forbidden keys into the stats source —
    # community_view must drop every key not on the whitelist
    _orig_stats = sjv.engine.get_pool_stats
    sjv.engine.get_pool_stats = lambda: {
        "jackpot_xmr": 1.5, "total_miners": 7, "mining_active": True,
        "drawing_interval_hours": 24,
        "real_miners": 5, "p2pool_hashrate": 999.0,
        "internal_rpc_url": "http://127.0.0.1:18082/json_rpc",
        "wallet_password": "hunter2",
    }
    try:
        _cv = sjv.community_view()
    finally:
        sjv.engine.get_pool_stats = _orig_stats
    check(9.2, "real_miners" not in _cv and "p2pool_hashrate" not in _cv
          and "internal_rpc_url" not in _cv
          and "wallet_password" not in _cv
          and _cv.get("jackpot_xmr") == 1.5 and _cv.get("total_miners") == 7,
          "injected forbidden keys are DROPPED, whitelisted values pass")
    _mv = sjv.miner_view(GOOD)
    check(9.3, _mv["registered"] is True and _mv["pending_tickets"] == 3
          and _mv["total_tickets"] == 3,
          "miner_view reads the caller's own state")
    _mv9 = sjv.miner_view(
        "48jewb9eXcS7XhcLavv4WvqNkV4eY74DnVddKz9L4CVQCJvfcRdpAe3hTbCqfPi2"
        "bfWcUP4fR5fcAHTrjS7o6dZ5MoVcN99")
    check(9.4, _mv9["registered"] is False,
          "an unknown wallet reads nothing (no enumeration)")
    _joined = json.dumps(_mv) + json.dumps(_cv)
    check(9.5, all(w not in _joined.lower()
                   for w in ("rpc", "password", "private", "seed")),
          "no secrets vocabulary anywhere in the payloads")
    sjv.db.DB_PATH = _orig

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
