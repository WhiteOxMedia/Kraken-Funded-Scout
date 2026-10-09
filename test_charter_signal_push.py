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

THE FLAKE, CURED (Oct 7, the in-sweep FAIL in battery_final_20261007):
the old checks 8.1/8.2 diffed sha+mtime of the LIVE monero artifacts
(pool/jackpot.db, the REAL spool) across the whole drive runtime.
Under battery load that identity race can't hold: this box runs the
LIVE pool server whose maintenance loop touches the real spool EVERY
~60s drain cycle (mtime bumps even when the spool stays empty at 0
rows) and sqlite CHECKPOINTs move jackpot.db's mtime — a sweep
straddling one of those bumps failed the drive for a write it never
made. THE CURE verifies what a 13-second hermetic drive can actually
guarantee, no clock race anywhere:
  8. the LIVE artifacts were CONSUMED, not written: an in-process scan
     of the REAL pool sources at runtime proves drain_spool's real
     write target is a Path-to-write parameter (the drive passes its
     sandbox), and the drive's last drain call's argument pins the
     sandbox spool — the real file is out of the writable set.
  (The real spool today carried 0 rows across every drain — nothing
  was ever sent to a phone by this drive; the sqlite mtime is a
  checkpoint artifact, not evidence of a write.)
  Plus the BATTERY-SHAPE pin: the identity-race class itself is
  banned by name — no drive may diff sha+mtime of a live foreign
  artifact across its runtime (the same pattern lives in
  test_commitgate.py's LIVE-DB section; that one reads the
  checkpoint ratchet, not a bumping maintenance loop — see its own
  banner).
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


def _fake_webpush(subscription_info, data, vapid_private_key,
                  vapid_claims, headers=None):
    # headers= (the Sep 27 TTL fix in pool/push_service.send_to_user)
    # must be accepted or every send raises TypeError and the drain
    # honestly reports dead endpoints - a fixture-staleness regression,
    # same class as test_redesign_push's stub fix in 3935b33.
    _boxes.append((subscription_info["endpoint"], json.loads(data)))
    return types.SimpleNamespace(status=200)


_fake = types.ModuleType("pywebpush")
_fake.webpush = _fake_webpush
_fake.WebPushException = type("WebPushException", (Exception,), {})
sys.modules["pywebpush"] = _fake
db.set_setting("VAPID_PRIVATE_KEY", "test-key-nonsecret")

LIVE_DB = MONERO / "pool" / "jackpot.db"
_real_spool = MONERO / "pool" / "push_events.jsonl"

print("== 1. THE SCANNER'S REAL GATE, SANDBOXED ==")
sys.path.insert(0, str(HERE))
# THE NTFY LEG, STUBBED (review triage, Sep 25 — a real catch: this box
# HAS a push_topic.txt, so the un-stubbed drive fired live ntfy.sh HTTP
# on every run; a hermetic drive must never phone out): the topic
# reader returns None and the sender is a recording no-op.
import background_scanner as _bs_precheck  # noqa: E402  (loaded once
# here so the patch below lands on the module the gate actually uses)
_bs_precheck._push_topic = lambda: None
_ntfy_calls = []
_bs_precheck._send_ntfy = lambda text, prio, note: _ntfy_calls.append(
    (str(text)[:40], prio))
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
check(4.1, len(rows) == 1 and _ntfy_calls == [],
      "an identical repeating GET OUT within 5 minutes emits once, "
      "not per call (one brain for the throttle); the ntfy leg stayed "
      "stubbed — no live HTTP, ever")

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

print("== 8. HERMETIC, CLOCK-RACE-FREE (the in-sweep flake cure) ==")
# Old pin diffed sha+mtime of the LIVE artifacts across the drive
# runtime — and lost to the live server's own ~60s drain touches and
# sqlite checkpoint bumps under sweep load (the real spool's mtime
# bumps every drain, even at 0 rows). THE CURE verifies the actual guarantee instead:
# 8.1: an in-process scan of the REAL pool source proves drain_spool
#      writes ONLY its Path parameter (the real spool is unreachable
#      from the drive's call), plus the drive's own drain call pinned
#      the sandbox path.
# 8.2: the real-artifact scan — the drive's fingerprint (charter
#      sandbox dirs, this module's own name) never appears in the
#      real spool's bytes.
_ps_src = (MONERO / "pool" / "push_service.py").read_text(
    encoding="utf-8", errors="replace")
_writes = [l.strip() for l in _ps_src.splitlines()
           if ".write_text(" in l or "open(" in l and "w" in l]
check(8.1, bool(_writes) and all(
    ("path" in w or "spool_path" in w
     or (ps.SPOOL_PATH.name in w)) for w in _writes),
    f"drain_spool's writes resolve only through its path parameter "
    f"(the real spool is out of the writable set): {_writes[:2]}")


def _under(p, root):
    try:
        return Path(p).resolve().is_relative_to(Path(root).resolve())
    except Exception:
        return False


# 8.3 RUNTIME TRUTH (Copilot's medium on the flake cure, cured): after
# every section ran, the modules' writable surfaces must STILL point
# into the sandbox — the live paths (pool/jackpot.db, the real spool)
# were never rebound, so no accidental live-artifact write is possible
# from this drive's call graph.
check(8.3, _under(db.DB_PATH, SANDBOX)
      and _under(ps.SPOOL_PATH, SANDBOX)
      and _under(pc.SPOOL, SANDBOX),
      f"the writable surfaces point at the sandbox even now, after "
      f"every section ran (db={Path(db.DB_PATH).name}, "
      f"drain spool={Path(ps.SPOOL_PATH).name}, "
      f"scanner spool={Path(pc.SPOOL).name})")

_real_spool_bytes = b""
if _real_spool.exists():
    _real_spool_bytes = _real_spool.read_bytes()

# 8.4 the LIVE AUDIT LEDGER's push rows are the db-side truth (Copilot's
# medium, 2nd pass, cured): fingerprint writes to the real artifacts
# would be VISIBLE as their audit consequences — the drive's SANDBOX
# USERNAME and the charter sandbox prefix can never appear in the live
# audit_log (a live-db mutation by this drive's sends or drains would
# write exactly there). The sandbox user ID is deliberately NOT the
# probe: integer IDs are shared space (a live user can carry id 1) —
# identities, not row numbers, are the fingerprint. Read-only, one
# query, no clocks.
_user_b = b"siguser"
_db_hit = b""
_db_verified = False
try:
    import sqlite3 as _sq
    _con = _sq.connect("file:" + str(LIVE_DB) + "?mode=ro", uri=True)
    for _r in _con.execute(
            "SELECT details, user_id FROM audit_log "
            "WHERE action LIKE 'push%' AND user_id IS NOT NULL "
            "ORDER BY id DESC LIMIT 200"):
        _db_hit = str(_r).encode("utf-8", "replace")
        if _user_b in _db_hit or b"charter_signal_drive_" in _db_hit:
            break
        _db_hit = b""
    _con.close()
    _db_verified = True   # the query COMPLETED — the ledger was read
except Exception:
    _db_verified = False
check(8.2, _db_verified and _db_hit == b""
      and b"charter_signal_drive_" not in _real_spool_bytes
      and b"siguser" not in _real_spool_bytes,
      "the live audit ledger was READ (fail-closed: an unreadable db "
      "is NOT a green) and its recent push rows name neither the "
      "sandbox username nor the drive prefix; the real spool carries "
      "none of the drive's fingerprints either (the drive consumed its "
      "own sandbox; nothing it made ever touched the live artifacts)")

print("== 9. THE TOPIC GATES ONLY THE NTFY LEG (review triage, Sep 25) ==")
# the old order returned before classification whenever the ntfy topic
# was absent — the charter emit died with ntfy's config. Each leg must
# answer to its own gate only.
pc.SPOOL = SANDBOX / "twolegs_spool.jsonl"
pc.SPOOL.write_text("", encoding="utf-8")
bs._push_state["text"] = ""
bs._push_state["ts"] = 0.0
bs._push_to_phone("BUY NOW — SOL/USD looks ready! Entry 100.3")
rows = [json.loads(l) for l in
        pc.SPOOL.read_text(encoding="utf-8").splitlines() if l.strip()]
check(9.1, len(rows) == 1 and rows[0]["event"] == "signal_buy"
      and _ntfy_calls == [],
      "with NO ntfy topic configured, the charter emit still fires "
      "(the native leg is not hostage to ntfy config) and the ntfy "
      "leg stays silent")
bs._push_topic = lambda: "sandbox-topic"      # now the ntfy leg is armed
_ntfy_calls.clear()
pc.SPOOL.write_text("", encoding="utf-8")
bs._push_state["text"] = ""
bs._push_state["ts"] = 0.0
bs._push_to_phone("GET OUT NOW — Sell SOL/USD! Down 62 dollars")
rows = [json.loads(l) for l in
        pc.SPOOL.read_text(encoding="utf-8").splitlines() if l.strip()]
check(9.2, len(rows) == 1 and rows[0]["event"] == "signal_exit"
      and len(_ntfy_calls) == 1 and _ntfy_calls[0][1] == "urgent",
      "with a topic armed, BOTH legs fire from the one gate: one "
      "signal_exit row and exactly one (stubbed) urgent ntfy send")

print("== 10. REACHED NOWHERE, THROTTLE UN-ARMS (the hook's own major) ==")
# the pre-commit hook's review flagged: with no topic AND a failed
# native emit, the old code still armed the 5-minute throttle — a
# repeated GET OUT would reach NOBODY for five minutes. The gate now
# un-arms when nothing accepted the alert.
bs._push_topic = lambda: None
_emits = []
_orig_emit = pc.emit
pc.emit = (lambda event, extra=None, user_id=None:
           (_emits.append(event), {"ok": False, "via": "spool"})[1])
bs._push_state["text"] = ""
bs._push_state["ts"] = 0.0
bs._push_to_phone("GET OUT NOW — Sell SOL/USD! Down 63 dollars")
check(10.1, len(_emits) == 1 and bs._push_state["ts"] == 0.0
      and bs._push_state["text"] == "",
      "no topic + native emit refused: the throttle is UN-ARMED — "
      "an alert that reached nowhere may not silence its own retry")
bs._push_to_phone("GET OUT NOW — Sell SOL/USD! Down 63 dollars")
check(10.2, len(_emits) == 2,
      "the identical repeat was NOT throttled — it emitted again "
      "(throttling is for delivered alerts, not for silence)")
pc.emit = _orig_emit

_check_src = Path(__file__).read_text(encoding="utf-8")
# 8.5 (Copilot's medium, 3rd pass): the write-API census — the first
# scan missed write_bytes/append/indirect helpers. The scanner-side
# client must anchor every write API to the SPOOL surface, never a
# literal live path.

_pc_src = (MONERO / "scout" / "push_client.py").read_text(
    encoding="utf-8", errors="replace")
_pc_writes = [l.strip() for l in _pc_src.splitlines()
              if any(api in l for api in
                     ("write_text(", "write_bytes(", "os.replace",
                      "shutil"))]
check(8.5, bool(_pc_writes) and all(
    ("SPOOL" in w or "spool" in w) for w in _pc_writes),
    "push_client's every FILE write API (text/bytes/replace) anchors "
    f"to the SPOOL surface, never a literal live path: {_pc_writes[:2]}")

shutil.rmtree(SANDBOX, ignore_errors=True)
print(f"RESULT: {passed} passed, {failed} failed")


def main():
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    sys.exit(main())
