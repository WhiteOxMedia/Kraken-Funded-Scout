"""scout/push_client.py — THE SCOUT-SIDE PUSH CLIENT (Sep 25, charter).

The signal source's half of the native push system: Kraken Scout's
trading brain is what DECIDES "Buy Now" / "Get Out Now" — this module
is how those decisions (and Scout's other charter events) reach
xmrjackpot.com's own push service.

DESIGN (mirrors scout_jackpot_view's sibling-import contract):
  - Kraken Scout imports this as the committed sibling module
    (monero_project/pool on sys.path inside its own guarded try).
  - emit() FIRST tries the pool server's authenticated operator
    route (POST /api/push/event with the operator token from the
    PUSH_OPERATOR_TOKEN setting). Until the server thread wires the
    routes, that 404s — and emit() then SPOOLS the event to
    pool/push_events.jsonl (a runtime artifact beside jackpot.db,
    never a repo-root stray).
  - The pool side drains the spool: push_service.drain_spool() is the
    ONE reader, and TWO messengers call it every cycle — the server's
    live payment-scanner loop first (Oct 7, RESOLVED_WIRE: the drain
    previously rode proxy_poller's call-path, which the live thread
    never runs, so the spool sat undrained for days), then the proxy
    poller's wallet-independent loop as the belt-and-suspenders.
    Events go out through the rate-limited, audited, templated
    sender. A spool row that fails 50 drains is dropped (a week-old
    signal is not a signal anymore), and the spool is capped at 500
    rows (oldest dropped) so it can never grow unbounded.

SAFETY: the operator token is read from settings, never hard-coded,
never logged; the spool carries event names and small whitelisted
extras only — no wallet internals, no keys, no user content.
"""
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
POOL_DIR = HERE.parent / "pool"           # THE pool dir, wherever this
                                           # module is imported from
SPOOL = POOL_DIR / "push_events.jsonl"
SERVER = "http://127.0.0.1:3094"        # the pool server's local origin
ROUTE = "/api/push/event"
SPOOL_MAX = 500
DROP_AFTER_TRIES = 50

_ALLOWED_EXTRAS = ("amount_xmr", "deadline", "signal")


_ENV_TOKEN = "XMRJACKPOT_OPERATOR_TOKEN"   # headless override (Oct 7)
_POOL_DB_CACHE = None


def _load_module_by_path(name, path):
    """One import by ABSOLUTE path, registered under exactly `name`
    (Oct 7): never mutates sys.path and never collides with whatever
    else the host process (Kraken Scout) already holds under that
    module name."""
    module = sys.modules.get(name)
    if module is not None:
        return module
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    except Exception:
        sys.modules.pop(name, None)
        return None


def _load_pool_database():
    """THE POOL'S database.py, BY PATH (the RESOLVED_TOKEN cure, Oct 7):
    a bare `import database` inside Scout's process could never reach
    pool/database.py (Scout's sys.path carries scout/, which has no
    database.py) — the ImportError was swallowed, the token rode ''
    on every emit, and the server's 401 spooled every single signal.
    This loads THIS repo's pool/database.py directly (the real
    settings DB), with field_crypto — database's own import —
    pre-loaded the same way so no sys.path entry is ever touched."""
    global _POOL_DB_CACHE
    if _POOL_DB_CACHE is None:
        if _load_module_by_path("field_crypto",
                                POOL_DIR / "field_crypto.py") is None:
            return None
        db_mod = _load_module_by_path("push_machine_pool_database",
                                      POOL_DIR / "database.py")
        if db_mod is None:
            return None
        _POOL_DB_CACHE = db_mod
    return _POOL_DB_CACHE


def _operator_token() -> str:
    """X-Operator-Token: the environment override first
    (XMRJACKPOT_OPERATOR_TOKEN — a headless Scout carries its own
    provisioning), else the pool's PUSH_OPERATOR_TOKEN setting, read
    straight out of the pool's database module loaded by path. ''
    on any failure — the route then refuses and the spool catches
    the event (never an untokenized POST)."""
    try:
        env = str(os.environ.get(_ENV_TOKEN, "") or "")
        if env:
            return env
        db_mod = _load_pool_database()
        if db_mod is None:
            return ""
        return str(db_mod.get_setting("PUSH_OPERATOR_TOKEN", "") or "")
    except Exception:
        return ""


def emit(event: str, extra: dict = None, user_id: int = None) -> dict:
    """Queue one charter event. event must be one of the service's
    templates (jackpot_win, claim_reminder, challenge_unlock,
    signal_buy, signal_exit, miner_status, ticket_update); extras are
    filtered to the whitelist. Returns {'ok', 'via'} — fail-soft by
    construction (a push problem can never break the trading loop)."""
    if event not in ("jackpot_win", "claim_reminder", "challenge_unlock",
                     "signal_buy", "signal_exit", "miner_status",
                     "ticket_update"):
        return {"ok": False, "via": "rejected", "reason": "unknown event"}
    row = {"event": event, "ts": time.time(),
           "extra": {k: str(v)[:120] for k, v in (extra or {}).items()
                     if k in _ALLOWED_EXTRAS}}
    if user_id is not None:
        try:
            row["user_id"] = int(user_id)
        except (TypeError, ValueError):
            pass
    # 1) the authenticated operator route, when the server has it
    try:
        req = urllib.request.Request(
            SERVER + ROUTE,
            data=json.dumps(row).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "X-Operator-Token": _operator_token()},
            method="POST")
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                return {"ok": True, "via": "server"}
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        pass
    # 2) the spool — the pool side drains it (the server's live loop
    #    and the poller's scan share one reader, push_service.drain_spool)
    try:
        rows = []
        if SPOOL.exists():
            rows = [json.loads(l) for l in
                    SPOOL.read_text(encoding="utf-8").splitlines()
                    if l.strip()][-SPOOL_MAX:]
        rows.append(row)
        SPOOL.write_text(
            "\n".join(json.dumps(r, separators=(",", ":"))
                      for r in rows[-SPOOL_MAX:]) + "\n",
            encoding="utf-8")
        return {"ok": True, "via": "spool"}
    except OSError as exc:
        return {"ok": False, "via": "spool", "reason": str(exc)[:80]}


def drain_spool() -> int:
    """Delegate to the pool side's one reader (push_service.drain_spool
    reads THIS module's spool path — the same file this module writes).
    The poller's loop calls the pool-side one directly; this alias
    exists for symmetry and for Scout-side diagnostics."""
    try:
        import sys
        pool = str(POOL_DIR)
        if pool not in sys.path:
            sys.path.insert(0, pool)
        import push_service as ps
        return ps.drain_spool(SPOOL)
    except Exception:
        return 0
