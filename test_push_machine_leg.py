"""test_push_machine_leg.py — THE CHARTER'S MACHINE LEG (Oct 7, red-first).

Kraken Scout's push decisions reach xmrjackpot.com's own push service
over TWO back-offs, and this drive pins BOTH on a sandboxed DB (no
network, no VAPID send): the authenticated operator route first, the
file spool on top of it, drained through the same rate-limited, audited
sender for as long as the route refuses.

TWO live defects drove this (Oct 7 session audit):

1. THE TOKEN DEAD END: scout/push_client.py::_operator_token did a bare
   `import database` while Scout's process has monero_project/scout (and
   possibly a DIFFERENT repo root) on sys.path — pool/database.py was
   never importable that way, the ImportError was swallowed, every emit
   posted an empty X-Operator-Token, the live server 401'd it, and the
   event spooled instead of flying (push_events.jsonl: 129 rows, tries
   frozen, zero push_broadcast rows ever — masked because the ntfy leg
   still carried BUY / GET OUT). The cured contract (1a/2a/2b): the
   token comes from the POOL's own database.py LOADED BY PATH from this
   module's directory tree, under a dedicated module name, with
   XMRJACKPOT_OPERATOR_TOKEN in the environment as the headless
   override. The drive POISONS bare `import database` with a module
   that lies ('scoutSkunk') — a wrong resolution can never masquerade
   as an empty one.

2. THE DRAINER THAT NEVER RAN: proxy_poller.py carried a drain call
   but buried in update_workers() — which the LIVE server thread
   (pool/server.py's payment-scanner loop) never runs; the poller
   processes have been up without it since Sep 25, so nothing has been
   drained, ever. The cured contract (1b/1c/1d): ONE reader brain —
   push_service.drain_spool — imported at the TOP by the poller (no
   call-time import), called from the poller's scan AND from the live
   server loop itself; the continue/give-up bookkeeping (drop after 50
   tries, release after a real send) is decided by the service, so the
   WAY an event leaves the wire is identical whichever messenger runs.

Prints "RESULT: N passed, M failed".
"""
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
POOL = HERE / "pool"
sys.path.insert(0, str(POOL))

# THE SANDBOX FIRST: a brand-new field key and a brand-new DB — this
# drive never reads or writes the REAL retail layer.
import field_crypto as fc
fc.KEY_DIR = Path(tempfile.mkdtemp(prefix="ml_key_"))
fc.KEY_FILE = fc.KEY_DIR / "field_key"
fc._KEY_CACHE = None
fc.generate_key_file()

import database as db
_SANDBOX_DB = Path(tempfile.mkdtemp(prefix="ml_db_")) / "jackpot.db"
db.DB_PATH = _SANDBOX_DB
db.init_db()

import push_service as push
_REAL_BROADCAST = push.send_to_subscribers   # restored in the finally

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


def _rows(path):
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in
            p.read_text(encoding="utf-8").splitlines() if l.strip()]


print("== 1. THE CONTRACT, FROM THE COMPILED SOURCES ==")
pc_src = (HERE / "scout" / "push_client.py").read_text(encoding="utf-8")
pp_src = (POOL / "proxy_poller.py").read_text(encoding="utf-8")
srv_src = (POOL / "server.py").read_text(encoding="utf-8")

check("1a", "def _load_pool_database" in pc_src,
      "the token source must load the POOL's database module BY PATH "
      "(HERE.parent / \"pool\") — a bare `import database` resolves to "
      "whatever else a Scout process carries on sys.path, and Scout "
      "carries nothing ( ImportError swallowed -> empty token -> 401)")
check("1b", "import push_service as push_svc" in pp_src
      and "push_svc.drain_spool()" in pp_src,
      "the poller's scan drains through ONE top-level-imported reader")
check("1c", "import push_service as _push" not in pp_src,
      "the drain must not import its brain at call time inside "
      "update_workers")
_pos_def = srv_src.find("def poll_proxy_workers")
_pos_thr = srv_src.find("scanner = threading.Thread")
_srv_drain = srv_src.find("push_svc.drain_spool()")
check("1d", -1 < _pos_def < _srv_drain < _pos_thr,
      "the LIVE server's payment-scanner loop runs the drain itself "
      "(between the poller defs and the thread start) — the poller "
      "process never ran update_workers, so the server loop is the "
      "only guaranteed runner")

print("== 2. THE TOKEN: BY PATH FROM THE POOL DIR, UNDER A POISONED IMPORTER ==")
# Poison FIRST, then load the client against it: scoping sys.path is
# exactly what a Scout process does, and this sandbox goes one byte
# further — A scout-side database that LIES, so a wrong (or even
# empty-but-answered) resolution can never masquerade as the truth.
_poison_dir = Path(tempfile.mkdtemp(prefix="ml_poison_"))
(_poison_dir / "database.py").write_text(
    "def get_setting(key, default=None):\n"
    "    return 'scoutSkunk'\n",
    encoding="utf-8")
_spec = importlib.util.spec_from_file_location(
    "database", _poison_dir / "database.py")
_poison_db = importlib.util.module_from_spec(_spec)
sys.modules["database"] = _poison_db
_spec.loader.exec_module(_poison_db)

MODULE = "scout_push_client_machineleg"


def _load_client():
    spec = importlib.util.spec_from_file_location(
        MODULE, HERE / "scout" / "push_client.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[MODULE] = m
    spec.loader.exec_module(m)
    return m


client = _load_client()
# hermetic emit: the route leg posts to a port nothing serves, the
# spool leg writes a sandbox file — never the REAL pool spool, whose
# live drainer would push sandbox signals at REAL phones.
client.SERVER = "http://127.0.0.1:9"
client.SPOOL = Path(tempfile.mkdtemp(prefix="ml_spool_")) / "push_events.jsonl"

TOKEN = "op_token_machine_leg_cure"
ENV_NAME = "XMRJACKPOT_OPERATOR_TOKEN"
os.environ[ENV_NAME] = TOKEN
_got_env = client._operator_token()
check("2a", _got_env == TOKEN,
      f"env first (a headless Scout carries the token in its "
      f"environment); got {_got_env!r} — '' is the 401 defect, "
      f"'scoutSkunk' means the poisoned bare import still wins")

# the db leg WITHOUT env: repoint the by-path-loaded pool database at
# the SANDBOX db (the drive owns a real pool/database.py instance; the
# fix must fetch THAT token, not the real retail db's).
del os.environ[ENV_NAME]
_loaded_db = client._load_pool_database()
_loaded_db.DB_PATH = _SANDBOX_DB
db.set_setting("PUSH_OPERATOR_TOKEN", "sentinel-db")
_got_db = client._operator_token()
check("2b", _got_db == "sentinel-db",
      f"without env, the POOL's database module (BY PATH, repointed to "
      f"this sandbox db) supplies the token; got {_got_db!r} — '' is "
      f"the 401 defect, 'scoutSkunk' the poisoned bare import")

print("== 3. THE EVENT STILL FLIES: one event, ONE destination ==")
push.send_to_subscribers = lambda ev, ex=None: {"sent": 3}
_res = client.emit("signal_buy", {"signal": "GO"})
check("3a", _res.get("ok") is True and _res.get("via") == "spool",
      f"the spool back-off caught the event when nothing served the "
      f"route: {_res}")
check("3b", push.drain_spool(client.SPOOL) == 1,
      "the drain hands the signal (one spool row -> the sender; the "
      "subscriber fan-out count belongs to the sender, not the drain)")
check("3c", not _rows(client.SPOOL),
      "a delivered event leaves the wire — no build-up, no replay")

print("== 4. THE STALE-SIGNAL BOOKKEEPING (tries bank, 50 is the door) ==")
push.send_to_subscribers = lambda ev, ex=None: {"sent": 0}
client.emit("signal_exit", {"signal": "GET OUT NOW"})
check("4a", push.drain_spool(client.SPOOL) == 0,
      "an unroutable signal waits with its try count banked")
_st = _rows(client.SPOOL)
check("4b", len(_st) == 1 and int(_st[0].get("tries", 0)) == 1,
      f"one waiting row, tries == 1: {_st}")

_sp2 = Path(tempfile.mkdtemp(prefix="ml_spool_")) / "old.jsonl"
_sp2.write_text(
    json.dumps({"event": "signal_buy", "ts": 0, "extra": {},
                "tries": 50}) + "\n" +
    json.dumps({"event": "signal_buy", "ts": 0, "extra": {},
                "tries": 49}) + "\n",
    encoding="utf-8")
check("4c", push.drain_spool(_sp2) == 0
      and [int(r.get("tries", 0)) for r in _rows(_sp2)] == [50],
      "at 50 failed drains the stale signal is DROPPED (not sent, not "
      "kept); 49 stays exactly one drain from the door")

try:
    pass
finally:
    os.environ.pop(ENV_NAME, None)
    push.send_to_subscribers = _REAL_BROADCAST
    with db.get_db() as _c:
        _c.execute("DELETE FROM settings WHERE key = 'PUSH_OPERATOR_TOKEN'")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
