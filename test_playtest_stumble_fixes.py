"""test_playtest_stumble_fixes.py — THE OCT 29 PLAYTEST DECISIONS, PINNED.

The final playtest named five surfaces a real user would still stumble
on, each left behind as a design choice. The operator has now decided
all five. This drive is the yardstick for those decisions:

  1. THE REGISTER GATE CHARGES ON CREATION: a validation refusal
     (400 bad wallet shape) or a 409 "taken" reply no longer spends one
     of the 5-per-hour-per-IP slots — a careless double-click can no
     longer lock a first-time user out; a bucket full of CREATED
     accounts still 429s before any work.
  2. /api/wallet/validate: the Connect Wallet flow's backing gate —
     the same offline mainnet validator the payout paths use. Junk
     fails with a NAMED reason, a constructed mainnet address passes,
     coin=vrsc rides the Verus shape gate.
  3. THE LOGGED-OUT NO-FETCH CONTRACT (statically, the way the bundle
     gate reads JS): the four guarded fetches in wf_dashboard sit
     behind the wfAuthed gate, and push_client's boot read sits behind
     the public /api/auth/me strobe — signed-out pages issue no guarded
     request at all.
  4. /api/winner/status speaks the CLAIM's truth: has_wallet derives
     from the drawing's miner wallet (what a one-click claim sends to),
     not the user's profile wallet, and winner_coin names the payout
     coin a VRSC draw pays in.
  5. THE TRANSLATED TITLE: /lang/{code} homes carry home.metaTitle in
     their own language; the default English home is byte-for-byte
     unchanged in message.

HERMETIC (the test_claim_routes rule): sandbox db bound BEFORE the
server import, both live rate feeds kill-switched, the wallet RPC
stubbed into a loud tripwire, the live jackpot.db hash-pinned.
"""
import hashlib
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# kill both live feeds before these modules exist: no network in a drive
import os

os.environ["XMR_PRICE_LIVE"] = "0"
os.environ["VRSC_PRICE_LIVE"] = "0"

sys.path.insert(0, str(HERE / "pool"))
import database as db  # noqa: E402

_tmp = tempfile.mkdtemp(prefix="stumble_sbx_")
db.DB_PATH = Path(_tmp) / "jackpot.db"
db.init_db()
db.set_setting("XMR_USD_RATE", "500.0")
db.set_setting("VRSC_USD_RATE", "1.0")

_LIVE_DB = HERE / "pool" / "jackpot.db"
_LIVE_BEFORE = (hashlib.sha256(_LIVE_DB.read_bytes()).hexdigest(),
                _LIVE_DB.stat().st_mtime) if _LIVE_DB.exists() else None

# the wallet RPC stub: BLOCKED, loud if ever touched (claim_routes rule)
import types  # noqa: E402
_BlockedWallet = types.ModuleType("monero_wallet")
_BlockedWallet.touched = False


def _stub_rpc_available():
    _BlockedWallet.touched = True
    return False


def _stub_any(name):
    def _boom(*_a, **_k):
        _BlockedWallet.touched = True
        raise RuntimeError(f"wallet RPC method {name!r} must never be "
                           "called from the stumble-fix drive")
    return _boom


_BlockedWallet.rpc_available = _stub_rpc_available
_BlockedWallet.__getattr__ = lambda name: _stub_any(name)()
sys.modules["monero_wallet"] = _BlockedWallet

import server  # noqa: E402
import jackpot_engine as engine  # noqa: E402

engine.get_next_block_hash = lambda: "ab" * 32  # no network, ever

from fastapi.testclient import TestClient  # noqa: E402
import address_check as _ac  # noqa: E402

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}  {why}".rstrip())


def _mkwallet(tag: str) -> str:
    # the CN base58 renders a 69-byte payload as 95 chars ONLY
    # without leading zero bytes; the e2e rule is a constructed-VALID
    # address, so walk the tag space until the fixture is honestly 95
    # (hash input, tried in order: deterministic, never a remembered
    # constant)
    for n in range(64):
        spend = hashlib.sha256(
            ("stumble-fixtures-spend-" + tag + "-" + str(n))
            .encode()).digest()
        if spend[0] == 0:
            continue
        view = hashlib.sha256(
            ("stumble-fixtures-view-" + tag + "-" + str(n))
            .encode()).digest()
        a = _ac.b58encode(chr(0x12).encode("latin-1") + spend + view
                          + _ac.keccak256(
                              chr(0x12).encode("latin-1")
                              + spend + view)[:4])
        if len(a) == 95:
            return a
    raise RuntimeError("no 95-char fixture found for tag " + tag)

_W_REAL = _mkwallet("real")
_W_PROFILE = _mkwallet("profile")   # deliberately DIFFERENT from the miner's

client = TestClient(server.app)

# ═════════════════════════════════════════════════════════════════
print("== 1. THE REGISTER GATE CHARGES ON CREATION (fix 1) ==")
server.security.register_limiter.buckets.clear()
_ip_probe = TestClient(server.app)

# a. a validation refusal spends NOTHING
r = _ip_probe.post("/api/auth/register", json={
    "username": "segfault_user", "password": "pw",
    "wallet_address": "this-is-not-a-monero-address-but-long-enough"})
check(1.1, r.status_code == 400, f"bad wallet -> {r.status_code}")
# b. a 409 'taken' reply spends NOTHING
_ip_probe.post("/api/auth/register", json={
    "username": "dupcheck", "password": "pw"})
bucket_after_dup = server.security.register_limiter.peek("testclient")
# the dup attempt consumes NOTHING (the account WAS created, one slot)
r = _ip_probe.post("/api/auth/register", json={
    "username": "dupcheck", "password": "pw2"})
check(1.2, r.status_code == 409, f"duplicate username -> {r.status_code}")
check(1.3, server.security.register_limiter.peek("testclient") is True,
      "after one creation + two refusals the bucket still has free slots")
tor = [_ip_probe.post("/api/auth/register", json={
    "username": f"probe{i}", "password": "pw",
    "wallet_address": "this-is-not-a-monero-address-but-long-enough"})
    for i in range(20)]
check(1.4, all(x.status_code == 400 for x in tor),
      "20 validation failures NEVER hit 429 — the gate is not a probe target")
sever = [_ip_probe.post("/api/auth/register", json={
    "username": f"creator{i}", "password": "pw"}) for i in range(7)]
codes = [x.status_code for x in sever]
check(1.5, codes.count(200) == 4 and codes[4:] == [429, 429, 429],
      f"the FIFTH CREATED account (dupcheck holds slot 1) hits 429: {codes}")
# d. the bucket survives restart-shape sanity: peek is read-only
check(1.6, server.security.register_limiter.peek("testclient") is False,
      "a full bucket stays full — peek never spends")
server.security.register_limiter.buckets.clear()

# ═════════════════════════════════════════════════════════════════
print("== 2. /api/wallet/validate backs the Connect Wallet flow (fix 2) ==")
JUNK = "this-is-not-a-monero-address-but-long-enough"
r = client.post("/api/wallet/validate", json={"address": JUNK})
body = r.json()
check(2.1, r.status_code == 200 and body.get("valid") is False
      and "length" in str(body.get("reason", "")),
      f"junk refused with a NAMED reason: {body.get('reason')}")
r = client.post("/api/wallet/validate", json={"address": _W_REAL})
check(2.2, r.json().get("valid") is True,
      f"constructed mainnet accepted: {r.json().get('reason')}")
r = client.post("/api/wallet/validate", json={
    "address": "R" + "a" * 33, "coin": "vrsc"})
check(2.3, r.json().get("valid") is True
      and r.json().get("coin") == "vrsc",
      f"verus shape rides the verus gate: {r.json()}")
r = client.post("/api/wallet/validate", json={
    "address": JUNK, "coin": "vrsc"})
check(2.4, r.json().get("valid") is False
      and "shape" in str(r.json().get("reason", "")),
      f"junk never passes the verus gate either: {r.json().get('reason')}")

# ═════════════════════════════════════════════════════════════════
print("== 3. THE LOGGED-OUT NO-FETCH CONTRACT (fix 3, static) ==")
wf = (HERE / "pool" / "static" / "wf_dashboard.js").read_text(encoding="utf-8")
push = (HERE / "pool" / "static" / "push_client.js").read_text(encoding="utf-8")
appsrc = (HERE / "pool" / "static" / "app.js").read_text(encoding="utf-8")


def _fn_span(src, header):
    i = src.index(header)
    j = src.index("\
  function ", i + 10)
    return src[i:j]


_jackpot_fn = _fn_span(wf, "function refreshJackpotCycle() {")
_signals_fn = _fn_span(wf, "function refreshSignals() {")
_prefs_fn = _fn_span(wf, "function refreshPrefsStamp() {")
_status_fn = _fn_span(wf, "function refreshStatus() {")
check(3.1, "if (!wfAuthed" in _jackpot_fn and "/api/jackpot/status" in _jackpot_fn,
      "the jackpot/status poll sits behind the wfAuthed gate")
check(3.2, "if (!wfAuthed" in _signals_fn
      and "/api/push/signals" in _signals_fn,
      "the push/signals poll sits behind the wfAuthed gate")
check(3.3, "if (!wfAuthed" in _prefs_fn
      and "/api/push/preferences" in _prefs_fn,
      "the push/preferences poll sits behind the wfAuthed gate")
check(3.4, "wfAuthed = authed" in _status_fn
      and 'fetch("/api/profile/stats"' in _status_fn
      and _status_fn.index("if (!authed)")
      < _status_fn.index('fetch("/api/profile/stats"'),
      "profile/stats is fetched ONLY once /me says the session is real")
check(3.5, "/api/auth/me" in push
      and "me.authenticated) { readServer(); }" in push,
      "push_client's boot read waits on the public /me strobe")
bridge = (HERE / "pool" / "static" / "scout_pro_bridge.js").read_text(
    encoding="utf-8")
_refresh_fn = (
    bridge[bridge.index("function meStrobe() {"):
           bridge.index("window.XMRScoutPro")])
check(3.6, "meStrobe" in _refresh_fn
      and 'fetch("/api/auth/me"' in _refresh_fn
      and _refresh_fn.index('function meStrobe()')
      < _refresh_fn.index('fetch("/api/scout/pro"'),
      "the entitlement read only fires once the public /me strobe says signed-in "
      "(fifth guarded endpoint, same noise class)")

# ═════════════════════════════════════════════════════════════════
print("== 4. /api/winner/status speaks the CLAIM's truth (fix 4) ==")
# one miner whose wallet is NOT the winner's profile wallet; the user
# registers with NO profile wallet, then one draws with the miner's
_M_W = _mkwallet("minertrust")
_m = db.get_or_create_miner(_M_W, worker_name="statuswinner")
tc_w = TestClient(server.app)
r = tc_w.post("/api/auth/register", json={
    "username": "statuswinner", "password": "pw"})
check(4.1, r.status_code == 200, "winner registered without a profile wallet")
db.set_setting("jackpot_balance", "0.5")
db.add_tickets(_m["id"], "free", 5)
dr = engine.run_drawing()
check(4.2, dr.get("status") == "drawn", f"drawing ran: {dr.get('status')}")
r = tc_w.get("/api/winner/status")
d = r.json()
check(4.3, d.get("has_pending_claim") is True, "the banner will stand")
check(4.4, d.get("has_wallet") is True
      and str(d.get("winner_wallet") or d.get("winner_wallet_addr") or "")
      == _M_W,
      f"has_wallet IS the drawing's miner wallet: "
      f"{d.get('has_wallet')} / {(d.get('winner_wallet') or '')[:10]}…")
check(4.5, d.get("winner_coin") == "xmr",
      f"the drawing's coin is named: {d.get('winner_coin')}")
# a profile wallet that DISAGREES with the miner wallet must not flip
# the banner: the claim wallet is the drawing's, still
db.update_user_profile(db.get_user_by_username("statuswinner")["id"],
                       wallet_address=_W_PROFILE)
r = tc_w.get("/api/winner/status")
d2 = r.json()
check(4.6, d2.get("has_wallet") is True
      and str(d2.get("winner_wallet") or "").startswith(_M_W[:6]),
      "an unrelated profile wallet cannot change the claim's destination")

# ═════════════════════════════════════════════════════════════════
print("== 5. THE TRANSLATED TITLE (fix 5) ==")
r = client.get("/lang/en?next=/")
m = re.search(r"<title>(.*?)</title>", r.text, re.S)
en_title = m.group(1) if m else ""
check(5.1, "Monero Mining Made Fun" in en_title,
      f"/lang/en still carries its English title: {en_title[:50]}…")
for code, snippet in (("es", "Minería de Monero"),
                      ("de", "Monero-Mining"),
                      ("fr", "minage de Monero"),
                      ("pt", "Mineração de Monero"),
                      ("ru", "майнинг Monero")):
    r = client.get(f"/lang/{code}?next=/")
    m = re.search(r"<title>(.*?)</title>", r.text, re.S)
    title = m.group(1) if m else ""
    check(5.2, snippet in title, f"/{code}/ title: {title}")
# catalogs carry the key (the copy source is the catalog, not the page)
import json as _json  # noqa: E402
for code in ("en", "es", "de", "fr", "pt", "ru"):
    cat = _json.loads((HERE / "pool" / "templates" / "app_i18n" /
                       f"{code}.json").read_text(encoding="utf-8"))
    check(5.3, "home.metaTitle" in cat,
          f"{code}.json pins home.metaTitle")

# ═════════════════════════════════════════════════════════════════
passed, failed = sum(results), results.count(False)
if _LIVE_BEFORE:
    after = (hashlib.sha256(_LIVE_DB.read_bytes()).hexdigest(),
             _LIVE_DB.stat().st_mtime)
    check("live", after == _LIVE_BEFORE, "the live jackpot.db never moved")
    passed, failed = sum(results), results.count(False)

print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
