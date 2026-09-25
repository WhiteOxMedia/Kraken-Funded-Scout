"""test_charter_signal_push.py — THE SIGNALS, WIRED (Sep 25 charter).

Grades the one-brain wiring: background_scanner's REAL push gate
(_push_to_phone — the exact point where the trading brain's BUY NOW /
GET OUT NOW decisions become phone-bound signals) now also emits the
charter's native push events, and the pool side drains them.

  1. the REAL _push_to_phone, no ntfy topic configured: its own early
     exits are respected (no topic = no ntfy leg), and the two charter
     emits ride AFTER the 5-minute identical-text throttle
  2. a BUY-class text emits exactly one signal_buy row
  3. a GET OUT-class text emits exactly one signal_exit row
  4. a repeating GET OUT within the 5-minute throttle window emits
     NOTHING more (the throttle governs both legs)
  5. non-signal text (warnings, TAKE IT) emits nothing — the charter
     emits only the two signal classes
  6. the spool the scanner wrote is drained by the pool side into a
     real templated send (the fake pywebpush catches the payload)
  7. emit failure is fail-soft: a broken spool path can never touch
     the caller
  8. hermetic: the live jackpot.db and the REAL spool are untouched
     (the drive repoints every writable surface first)

House conventions: check(n, cond, why), RESULT line, sys.exit(1).
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
MONERO = HERE.parent / "monero_project"
passed = failed = 0


def check(n, cond, why=""):
    global passed, failed
    print(f"{'PASS' if cond else 'FAIL'}  {n}  {why}")
    passed, failed = passed + (1 if cond else 0), failed + (0 if cond else 1)


SANDBOX = Path(tempfile.mkdtemp(prefix="charter_signal_drive_"))
sys.path.insert(0, str(MONERO / "pool"))

import database as db                                    # noqa: E402
db.DB_PATH = SANDBOX / "jackpot.db"
db.init_db()

import push_service as ps                                # noqa: E402
ps.SPOOL_PATH = SANDBOX / "spool.jsonl"

# a real sandbox user for the FK-honest audit rows
import hashlib as _hl                                    # noqa: E402
UID = int(db.create_user("siguser", _hl.sha256(b"x").hexdigest(),
                         "sig@example.com"))
ps.subscribe(UID, {"endpoint": "https://push.example/s/sig",
                   "keys": {"p256dh": "k", "auth": "a"}})

# the fake webpush BEFORE background_scanner imports anything pushy
_boxes = []


def _fake_webpush(subscription_info, data, vapid_private_key, vapid_claims):
    _boxes.append((subscription_info["endpoint"], json.loads(data)))
    return types.SimpleNamespace(status=200)


_fake = types.ModuleType("pywebpush")
_fake.webpush = _fake_webpush
_fake.WebPushException = type("WebPushException", (Exception,), {})
sys.modules["pywebpush"] = _fake
db.set_setting("VAPID_PRIVATE_KEY", "test-key-nonsecret")

LIVE_DB = MONERO / "pool" / "jackpot.db"


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


_live_before = ((_sha(LIVE_DB), os.path.getmtime(LIVE_DB))
                if LIVE_DB.exists() else None)
_real_spool = MONERO / "pool" / "push_events.jsonl"
_spool_before = (_sha(_real_spool), os.path.getmtime(_real_spool)) \
    if _real_spool.exists() else None

print("== 1. THE SCANNER'S REAL GATE, SANDBOXED ==")
sys.path.insert(0, str(HERE))
# a sandboxed client BEFORE the scanner module loads: its import of
# push_client (inside _charter_push) must bind THIS spool, not the
# real one
sys.path.insert(0, str(MONERO / "scout"))
import push_client as pc                                 # noqa: E402
pc.SPOOL = SANDBOX / "scanner_spool.jsonl"
pc.SERVER = "http://127.0.0.1:1"          # nothing listens: spool path

import background_scanner as bs                          # noqa: E402
check(1.1, callable(getattr(bs, "_charter_push", None))
      and callable(getattr(bs, "_push_to_phone", None)),
      "the scanner carries the charter hook and its real gate")
# belt-and-suspenders: whatever module object the hook binds, force
# its writable surfaces into the sandbox
try:
    pc.SPOOL = SANDBOX / "scanner_spool.jsonl"
except Exception:
    pass

print("== 2. BUY NOW, EMITTED ==")
pc.SPOOL.write_text("", encoding="utf-8")
bs._push_state["text"] = ""
bs._push_state["ts"] = 0.0
bs._push_to_phone("BUY NOW — SOL/USD looks ready! Entry 100.2")
rows = [json.loads(l) for l in
        pc.SPOOL.read_text(encoding="utf-8").splitlines() if l.strip()]
check(2.1, len(rows) == 1 and rows[0]["event"] == "signal_buy",
      f"one BUY-class alert -> exactly one signal_buy row ({rows})")

print("== 3. GET OUT, EMITTED ==")
pc.SPOOL.write_text("", encoding="utf-8")
bs._push_state["text"] = ""
bs._push_state["ts"] = 0.0
bs._push_to_phone("GET OUT NOW — Sell SOL/USD! Down 60 dollars")
rows = [json.loads(l) for l in
        pc.SPOOL.read_text(encoding="utf-8").splitlines() if l.strip()]
check(3.1, len(rows) == 1 and rows[0]["event"] == "signal_exit",
      "one GET OUT-class alert -> exactly one signal_exit row")

print("== 4. THE THROTTLE GOVERNS BOTH LEGS ==")
pc.SPOOL.write_text("", encoding="utf-8")
bs._push_state["text"] = ""
bs._push_state["ts"] = 0.0
bs._push_to_phone("GET OUT NOW — Sell SOL/USD! Down 61 dollars")
bs._push_to_phone("GET OUT NOW — Sell SOL/USD! Down 61 dollars")
rows = [json.loads(l) for l in
        pc.SPOOL.read_text(encoding="utf-8").splitlines() if l.strip()]
check(4.1, len(rows) == 1,
      "an identical repeating GET OUT within 5 minutes emits once, "
      "not per call (one brain for the throttle)")

print("== 5. ONLY THE SIGNAL CLASSES EMIT ==")
pc.SPOOL.write_text("", encoding="utf-8")
bs._push_state["text"] = ""
bs._push_state["ts"] = 0.0
bs._push_to_phone("Warning: the tide is turning, get out or hold!")
bs._push_to_phone("TAKE IT — up 25 dollars, consider banking it")
rows = [json.loads(l) for l in
        pc.SPOOL.read_text(encoding="utf-8").splitlines() if l.strip()]
check(5.1, rows == [],
      "warnings and TAKE IT never emit — the charter pushes only the "
      "two signal classes (the Aug-30 rule, now in the native system)")

print("== 6. THE POOL SIDE DRAINS TO A REAL SEND ==")
before = len(_boxes)
pc.SPOOL.write_text(
    "\n".join(json.dumps(r) for r in
              [{"event": "signal_buy", "ts": time.time()},
               {"event": "signal_exit", "ts": time.time()}]) + "\n",
    encoding="utf-8")
handed = ps.drain_spool(pc.SPOOL)
sigs = [p for _e, p in _boxes[before:]
        if p["tag"] in ("signal-buy", "signal-exit")]
check(6.1, handed == 2 and len(sigs) == 2
      and sigs[0]["title"] == ps.EVENTS["signal_buy"]["title"]
      and sigs[1]["title"] == "📉 GET OUT NOW",
      f"the scanner's spool drained into {len(sigs)} real templated "
      f"sends (BUY NOW + GET OUT NOW titles byte-exact)")

print("== 7. FAIL-SOFT BY CONTRACT ==")
pc.SPOOL = SANDBOX / "no_dir_here" / "x" / "spool.jsonl"   # unwritable
try:
    bs._push_state["text"] = ""
    bs._push_state["ts"] = 0.0
    bs._push_to_phone("GET OUT NOW — Sell DOGE/USD! Down 10 dollars")
    ok = True
except Exception:
    ok = False
check(7.1, ok,
      "an unwritable spool never raises out of the trading loop — the "
      "push system can fail, the trade cannot")

print("== 8. HERMETIC ==")
if _live_before:
    check(8.1, (_sha(LIVE_DB), os.path.getmtime(LIVE_DB)) == _live_before,
          "the live jackpot.db is sha256+mtime identical throughout")
else:
    check(8.1, not LIVE_DB.exists(), "no live jackpot.db to protect")
if _spool_before:
    check(8.2, (_sha(_real_spool), os.path.getmtime(_real_spool))
          == _spool_before,
          "the REAL spool (pool/push_events.jsonl) is byte-identical — "
          "the drive wrote only its sandbox")
else:
    check(8.2, not _real_spool.exists(),
          "the real spool does not exist — nothing to protect, nothing "
          "created")

shutil.rmtree(SANDBOX, ignore_errors=True)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
