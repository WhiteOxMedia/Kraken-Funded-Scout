"""test_scout_ws_proxy.py — THE SUBPROTOCOL CONTRACT (Sep 26 review triage).

The scout websocket proxy once answered the browser's FIRST offered
subprotocol even when the upstream (Streamlit) rejected every offer:
aiohttp reports upstream.protocol as None then, and the old
`or client_protos[0]` fallback claimed a selection nobody made — the
two peers could disagree about the wire protocol.

Pinned here through the REAL route (@app.websocket("/scout/_stcore/stream"))
against a STUBBED upstream (the aiohttp.ClientSession seam is replaced;
no port 8501 is ever touched):

  1. upstream selects one of the offered protocols -> handshake accepted
     (the proxy must never turn a healthy relay away)
  2. upstream selects NOTHING while the client offered -> the proxy
     REFUSES the downstream handshake (WebSocketDisconnect) instead of
     echoing a protocol the upstream rejected — the exact bug shape,
     now unrepresentable
  3. the client offered nothing -> accepted with no subprotocol (the
     curl/aiohttp path the original fix served)

Admin gate satisfied by a real session row in the sandbox DB (the same
cookie contract _scout_admin_ws enforces in production).

Hermetic: sandbox DB_PATH, no network, no tkinter, no live jackpot.db.
Prints "RESULT: N passed, M failed".
"""
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))

import field_crypto as fc                          # noqa: E402
fc.KEY_DIR = Path(tempfile.mkdtemp(prefix="wsgate_key_"))
fc.KEY_FILE = fc.KEY_DIR / "field_key"
fc._KEY_CACHE = None
fc.generate_key_file()

_tmp = Path(tempfile.mkdtemp(prefix="wsgate_drive_"))
import database as db                              # noqa: E402
db.DB_PATH = _tmp / "jackpot.db"
db.init_db()

import server as srv                               # noqa: E402
from fastapi.testclient import TestClient          # noqa: E402

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


# ── the admin session the gate demands ───────────────────────────────
_uid = db.create_user("wsgate_admin", "x", is_admin=True)
import time as _time                              # noqa: E402
TOKEN = "wsgate-admin-session-token"
db.create_session(TOKEN, _uid, _time.time() + 3600)

# ── the stubbed upstream: ClientSession.ws_connect is the seam ───────
class _FakeUpstream:
    """The aiohttp ClientWebSocketResponse stand-in: a configurable
    .protocol (the selection under contract) and a receive() that
    PARKS - the relay must stay open for the with-block's lifetime,
    because the proxy's cleanup closes the downstream socket the
    moment a pump returns (an early CLOSE would race the TestClient's
    own handshake bookkeeping)."""

    def __init__(self, protocol):
        self.protocol = protocol
        self._parked = None

    async def receive(self):
        import asyncio as _aio
        self._parked = _aio.Event()
        await self._parked.wait()          # parked until cleanup cancels

    async def close(self):
        if self._parked is not None:
            self._parked.set()             # unblock the parked pump

    async def send_bytes(self, data):
        raise AssertionError("the relay must be closed before sending")

    async def send_str(self, text):
        raise AssertionError("the relay must be closed before sending")


class _FakeSession:
    selected = "unset"          # what the upstream will select
    last_protocols = None       # what the proxy offered upstream

    def __init__(self, *a, **k):
        pass

    async def ws_connect(self, url, headers=None, **k):
        _FakeSession.last_protocols = k.get("protocols")
        proto = _FakeSession.selected
        return _FakeUpstream(None if proto == "none" else proto)

    async def close(self):
        pass


srv.aiohttp = types.SimpleNamespace(
    ClientSession=_FakeSession,
    WSMsgType=srv.aiohttp.WSMsgType,
)
srv._scout_ws_live["n"] = 0                        # clean relay ledger

WS_URL = "/scout/_stcore/stream"


def _auth(offered=None):
    """A FRESH headers dict per connect. Not style: Starlette's
    websocket_connect does headers.setdefault("sec-websocket-protocol",
    ...), MUTATING the caller's dict - a shared dict would poison every
    later connect with section 2's offer (the ghost offer that fails
    3.1). Each call gets its own dict; offered adds the header."""
    h = {"cookie": f"session={TOKEN}"}
    if offered:
        h["sec-websocket-protocol"] = offered
    return h

print("== 1. UPSTREAM SELECTS -> THE HANDSHAKE IS ACCEPTED ==")
_FakeSession.selected = "streamlit"
try:
    with TestClient(srv.app).websocket_connect(WS_URL, headers=_auth()) as ws:
        check("1.1", True,
              "a healthy upstream selection connects (no false refusal)")
except Exception as _e:                     # diagnostic, not a pin:
    check("1.1", False,
          f"unexpected refusal: {_e.__class__.__name__}: {_e}")

print("== 2. UPSTREAM REJECTS EVERYTHING + CLIENT OFFERED -> REFUSED ==")
_FakeSession.selected = "none"
try:
    with TestClient(srv.app).websocket_connect(
            WS_URL, headers=_auth("streamlit")) as ws:
        ws.receive()
        _refused = False
except Exception as e:
    _refused = type(e).__name__ in ("WebSocketDisconnect",) or "closed" in str(e).lower()
check("2.1", _refused,
      "the proxy refuses rather than claim a subprotocol the upstream "
      "rejected (the fallback's bug shape is dead)")
check("2.2", _FakeSession.last_protocols == ["streamlit"],
      "the client's offers were still forwarded upstream (forwarding "
      "stays; only the false echo is gone)")

print("== 3. NO CLIENT OFFER -> ACCEPTED WITH NO SUBPROTOCOL ==")
_FakeSession.selected = "none"
try:
    with TestClient(srv.app).websocket_connect(WS_URL, headers=_auth()) as ws:
        check("3.1", True,
              "the no-offer path (curl/aiohttp) still connects cleanly")
except Exception as _e:                     # diagnostic, not a pin:
    check("3.1", False,
          f"unexpected refusal: {_e.__class__.__name__}: "
          f"code={getattr(_e, 'code', '?')} {_e} "
          f"offered_upstream={_FakeSession.last_protocols!r}")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
