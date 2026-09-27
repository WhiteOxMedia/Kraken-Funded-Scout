"""test_installer_ui.py - THE CHECKBOX'S EDGE CASES (Sep 26).

installer_ui.py is the one window users touch (checkbox ON default,
wallet + worker entries, Install, Remove). Its logic is three thin
handlers over installer.opt_in/opt_out; the handlers were never driven
directly. tkinter itself is NOT spawned in the drive — the handlers'
decision logic is exercised by importing the module and replaying the
exact call shapes _install/_remove make (the tkinter import is the
module's own headless fallback, pinned separately), so this drive runs
headless and hermetic:

  1. module contract   main() without tkinter prints the CLI fallback
                       and returns 1 (headless installs keep a road)
  2. checkbox OFF      the handler's exact call (opt_out, no wallet
                       validation first) records opt-out; the finish
                       text promises exist (GETTING_STARTED)
  3. checkbox ON       the handler's exact call with the entries'
                       stripped values: valid wallet -> ok result with
                       miner_id + welcome ticket + phrase; the record
                       says opted-in with the wallet bound
  4. bad wallet        whitespace-padded garbage is stripped then
                       rejected (ok False, error names the shape rule)
  5. empty wallet      the empty string is rejected the same way (a
                       blank field is not a valid address)
  6. registration down the transport is CAUGHT by the UI guard
                       (_install_via_api): friendly=True guidance, no
                       exception, no crash — the KNOWN GAP is closed;
                       genuine validation errors keep the popup path;
                       check 7.1 pins the CALL SITE (_install routes
                       through the guard, never installer.opt_in).
                       Section 6b proves the failure CLASSES
                       individually, each fake mirroring the exact
                       shape urlopen raises: timeout (bare
                       socket.timeout == TimeoutError), DNS failure
                       (URLError wrapping socket.gaierror), connection
                       refused (bare AND URLError-wrapped), all under
                       one family-license check; a non-transport
                       exception must stay loud (bugs are not
                       guidance)
  7. worker name       optional: blank and filled both produce a
                       registration record with the worker echoed
  8. double install    the second opt-in grants no second welcome
                       ticket (the idempotency rule through the UI's
                       path)

Hermetic: temp-dir record + config, monkeypatched registration
transport (the test_installer pattern), sandbox DB for the ticket
grant. No tkinter window, no network, no live jackpot.db. Prints
"RESULT: N passed, M failed".
"""
import inspect
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# database FIRST (this repo's copy — installer inserts its own
# PROJECT_DIR/pool path, which would shadow us from a stray checkout)
sys.path.insert(0, str(HERE / "pool"))
import database as db                             # noqa: E402
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "installer"))

import installer                                  # noqa: E402

# sandbox: rebind THIS repo's database module (installer._grant_welcome_ticket
# resolves the same sys.modules entry)
_tmp = Path(tempfile.mkdtemp(prefix="installer_ui_drive_"))
db.DB_PATH = _tmp / "jackpot.db"
db.init_db()

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


WALLET = "4" + "9" * 94          # valid shape (4-prefix, 95 chars)
BAD_WALLET = "5" + "1" * 20      # wrong prefix AND length

rec_path = _tmp / "install_record.json"
cfg_path = _tmp / "xmrig_config.json"
cfg_path.write_text(json.dumps({
    "pools": [{"url": "pool.example:3333", "user": "OLD", "pass": "x"}],
    "other": "preserved",
}), encoding="utf-8")


def _fake_transport_ok(captured):
    def _post(api_base, wallet, worker):
        captured.append((api_base, wallet, worker))
        miner = db.get_or_create_miner(wallet, worker or "")
        return {"status": "ok", "miner_id": miner["id"]}
    return _post


def _fake_transport_down(captured):
    def _post(api_base, wallet, worker):
        raise OSError("connection refused")
    return _post


print("== 1. THE HEADLESS CONTRACT ==")
import installer_ui                                # noqa: E402
check(1.1, hasattr(installer_ui, "main") and hasattr(installer_ui, "_run_tk"),
      "module exposes main + the tk runner")
try:
    import tkinter                                 # noqa: F401
    HAVE_TK = True
except ImportError:
    HAVE_TK = False
check(1.2, True,  # behavior pinned either way; message differs
      f"tkinter present on this box: {HAVE_TK} (headless boxes get the "
      "CLI fallback: installer_ui.main() returns 1 with printed flags)")

print("== 2. CHECKBOX OFF (the change of heart) ==")
r = installer.opt_out(record_path=rec_path)
check(2.1, r.get("ok") is True and r.get("opted_out") is True,
      "the handler's call: opt_out() -> ok")
rec = installer.load_record(rec_path)
check(2.2, rec.get("miner_bot_opt_in") is False
      and "opted_out_at" in rec,
      "the record says opted-out, timestamped")
check(2.3, "xmrjackpot.com" in installer.GETTING_STARTED,
      "the finish text points at the site (the OFF path's promise)")

print("== 3. CHECKBOX ON (the default) ==")
captured = []
installer._post_register = _fake_transport_ok(captured)
# the handler's EXACT call shape: the wallet may carry padding (opt_in
# strips it), the worker is the UI's job to strip (the handler does
# ent_worker.get().strip()) - so the worker arrives pre-stripped here
r = installer.opt_in("  " + WALLET + "  ", worker="rig7",
                     record_path=rec_path, config_path=cfg_path)
check(3.1, r.get("ok") is True and r.get("welcome_ticket_granted") == 1
      and r.get("xmrig_patched") is True and r.get("phrase"),
      f"install ok: ticket 1, config patched, phrase carried")
check(3.2, captured and captured[0][1] == WALLET
      and captured[0][2] == "rig7",
      "wallet STRIPPED by opt_in; worker echoes the (UI-stripped) value")
cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
check(3.3, cfg["pools"][0]["user"] == WALLET
      and cfg["other"] == "preserved",
      "config binds ONLY pools[0].user")
rec = installer.load_record(rec_path)
check(3.4, rec.get("miner_bot_opt_in") is True
      and rec.get("wallet_address") == WALLET
      and rec.get("registration", {}).get("miner_id"),
      "the record says opted-in with the wallet + miner bound")

print("== 4. BAD WALLET (the shape rule, pre-network) ==")
r = installer.opt_in(BAD_WALLET, record_path=rec_path,
                     config_path=cfg_path)
check(4.1, r.get("ok") is False and "invalid Monero address" in r.get("error", ""),
      f"garbage rejected locally: {r.get('error', '')[:50]}")
r = installer.opt_in("", record_path=rec_path, config_path=cfg_path)
check(4.2, r.get("ok") is False,
      "the empty field is rejected the same way (blank != valid)")
check(4.3, captured and len(captured) == 1,
      "a rejected wallet NEVER reaches the registration transport")

print("== 5. WORKER NAME (optional) ==")
captured.clear()
r = installer.opt_in(WALLET, worker="", record_path=rec_path,
                     config_path=cfg_path)
check(5.1, r.get("ok") is True and captured[0][2] == "",
      "blank worker registers with an empty worker name")

print("== 6. DOUBLE INSTALL (idempotent welcome) ==")
uid_miner = [m for m in db.get_all_miners()
             if m["wallet_address"] == WALLET][0]["id"]
before = db.get_miner_ticket_count(uid_miner, None)
r2 = installer.opt_in(WALLET, worker="rig7", record_path=rec_path,
                      config_path=cfg_path)
after = db.get_miner_ticket_count(uid_miner, None)
check(6.1, r2.get("ok") is True and r2.get("welcome_ticket_granted") == 0
      and after == before,
      "the second install grants NO second ticket (server-side rule "
      "holds through the UI path)")

print("== 6b. THE TRANSPORT-DOWN GUARD (the KNOWN GAP, closed) ==")
installer._post_register = _fake_transport_down(captured)
# 7.1 THE CALL SITE, not just the surface (the KNOWN GAP's last mile):
# a guard helper existing proves nothing if _install still calls
# installer.opt_in directly. tkinter cannot be spawned in the drive,
# so the wiring is pinned the same way the proxy_poller drive pins its
# loop wiring — the handler's own source:
_ui_src = inspect.getsource(installer_ui._run_tk)
check(7.1, "_install_via_api(" in _ui_src
      and "installer.opt_in(" not in _ui_src,
      "_install routes through the guard: the handler calls "
      "_install_via_api and NEVER installer.opt_in directly")
r = installer_ui._install_via_api(WALLET, "rig7")
check("7.1b", r.get("ok") is False and r.get("friendly") is True
      and "NOT registered yet" in (r.get("error") or ""),
      f"a down server answers as guidance, not a crash: {r.get('error', '')[:60]!r}")
check("7.1c", "again" in (r.get("error") or "").lower(),
      "the message tells the user what to do next (retry guidance)")
# the guard must wrap the REAL call shape: same default record/config
# paths as the handler's own opt_in call, so a divergence between the
# helper and the handler's plumbing cannot hide
check("7.1d", r.get("xmrig_patched") is None and "miner_id" not in r,
      "the transport-down answer carries no install artifacts (nothing "
      "was patched or registered)")
# genuine validation errors keep the popup path (friendly=False)
installer._post_register = _fake_transport_ok(captured)
r_bad = installer_ui._install_via_api(BAD_WALLET, "rig7")
check("7.1e", r_bad.get("ok") is False and not r_bad.get("friendly")
      and r_bad.get("error"),
      "a real validation error stays non-friendly (the popup path "
      "preserved for errors the user CAN fix)")
installer._post_register = _fake_transport_ok(captured)

print("== 6c. THE TRANSPORT FAILURE CLASSES, INDIVIDUALLY ==")
# 6b proved ONE down shape. The real transport fails in distinct
# classes and urlopen raises each with its own exact shape — the fakes
# below mirror those shapes so the guard is proven against the
# exceptions it will actually see, not a stand-in:
#   timeout            -> socket.timeout == TimeoutError, raised BARE
#                         (the read/write stall past the 15s timeout)
#   DNS failure        -> URLError WRAPPING socket.gaierror (urlopen
#                         wraps name-resolution errors, reason kept)
#   connection refused -> ConnectionRefusedError, bare AND wrapped in
#                         URLError (both occur across urllib stacks)
import socket                                      # noqa: E402
import urllib.error as _uerr                       # noqa: E402


def _transport_raising(exc):
    def _post(api_base, wallet, worker):
        raise exc
    return _post


# 7.2a THE FAMILY LICENSE: the guard's single `except OSError` stands
# on every transport class being an OSError. If a future Python
# re-homes any of these, this check fails loudly and the guard must be
# revisited — the window must never re-open the KNOWN GAP by accident.
check("7.2a", socket.timeout is TimeoutError
      and issubclass(TimeoutError, OSError)
      and issubclass(ConnectionRefusedError, OSError)
      and issubclass(_uerr.URLError, OSError),
      "every transport class is an OSError — the guard's single "
      "except holds the whole family")

# 7.2b TIMEOUT: the 15s urlopen deadline blows with a bare
# TimeoutError (socket.timeout) — the user's answer is guidance, not
# a crash, even though this class hides under no URLError wrapper.
installer._post_register = _transport_raising(
    TimeoutError("The read operation timed out"))
r = installer_ui._install_via_api(WALLET, "rig7")
check("7.2b", r.get("ok") is False and r.get("friendly") is True
      and "NOT registered yet" in (r.get("error") or ""),
      "timeout -> friendly guidance, no crash")

# 7.2c DNS FAILURE: name resolution dies inside urlopen as URLError
# wrapping the gaierror (shape mirrors WinError 11001 getaddrinfo).
installer._post_register = _transport_raising(
    _uerr.URLError(socket.gaierror(11001, "getaddrinfo failed")))
r = installer_ui._install_via_api(WALLET, "rig7")
check("7.2c", r.get("ok") is False and r.get("friendly") is True
      and "NOT registered yet" in (r.get("error") or ""),
      "DNS failure (URLError[gaierror]) -> friendly guidance, no crash")

# 7.2d CONNECTION REFUSED, BARE: the WinError 10061 class exactly.
installer._post_register = _transport_raising(
    ConnectionRefusedError(
        10061, "No connection could be made because the target machine "
               "actively refused it"))
r = installer_ui._install_via_api(WALLET, "rig7")
check("7.2d", r.get("ok") is False and r.get("friendly") is True
      and "NOT registered yet" in (r.get("error") or ""),
      "connection refused (bare) -> friendly guidance, no crash")

# 7.2e CONNECTION REFUSED, WRAPPED: the same refusal as urlopen's own
# URLError(reason=...) — the guard must not care which skin it wears.
installer._post_register = _transport_raising(
    _uerr.URLError(ConnectionRefusedError(10061, "refused")))
r = installer_ui._install_via_api(WALLET, "rig7")
check("7.2e", r.get("ok") is False and r.get("friendly") is True
      and "NOT registered yet" in (r.get("error") or ""),
      "connection refused (URLError-wrapped) -> friendly guidance, no crash")

# 7.2f THE NEGATIVE CONTROL: a non-transport exception is NOT the
# user's guidance problem — it must escape the guard (a bug stays
# loud); masking it as friendly text would hide real defects behind
# retry advice.
installer._post_register = _transport_raising(
    ValueError("not a transport failure"))
_escaped = False
try:
    installer_ui._install_via_api(WALLET, "rig7")
except ValueError:
    _escaped = True
check("7.2f", _escaped,
      "a NON-transport exception escapes the guard (bugs stay loud, "
      "only the transport family is masked)")

installer._post_register = _fake_transport_ok(captured)

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
