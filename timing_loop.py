"""timing_loop.py — the per-cycle caller. Inert while overwatch_timing.ARMED is False.

Why this file exists: `timing_live.tick` is hooked into
`scout_signal_reader.check_scout_signals`, but nothing ever called that function, so
the wiring could not fire. This is that caller and nothing else — one cycle every
INTERVAL seconds, one authenticated client for the whole process, and no work at all
while ARMED is False.

INERT MEANS INERT: while ARMED is False a cycle reads the account, logs the buffer and
returns. It does not build a client, does not fetch, does not size, does not order.

    python timing_loop.py --once              one cycle, print what it did
    python timing_loop.py                     the loop (60s)
    python timing_loop.py --interval 120      slower loop
    python timing_loop.py --live               let fills be REAL (default: validate only)

REAL vs VALIDATED: `TradingConfig` carries no `paper_mode`, and timing_live defaults it
to True, so every fill is a `validate=True` dry run unless `--live` is passed. Real
money therefore needs both switches: ARMED is True *and* --live.

ONE CLIENT: the client is built once, lazily, only when a cycle is actually going to
trade, and reused for the life of the process. No unauthenticated fallback is allowed
here — a fallback client cannot place an order and would hide a dead keyring.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import contest_account as ca
import overwatch_timing as ot

LOG = os.path.join(HERE, "timing_loop.log")
STATE = os.path.join(HERE, "timing_loop_state.json")
LOCK = os.path.join(HERE, "timing_loop.lock")
LOCK_STALE_S = 300

log = logging.getLogger("timing_loop")
_client = None


def _log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def get_client():
    """The process's single authenticated Kraken client, built once.

    Same credential source as app.get_kraken_client (keyring, "workshop"), but no
    unauthenticated fallback: a bare client cannot trade and would mask the failure.
    """
    global _client
    if _client is not None:
        return _client
    import keyring
    from kraken_api import KrakenAPI
    key = keyring.get_password("workshop", "KRAKEN_API_KEY") or ""
    secret = keyring.get_password("workshop", "KRAKEN_API_SECRET") or ""
    if not key or not secret:
        raise RuntimeError("no Kraken credentials in the keyring — refusing to trade")
    _client = KrakenAPI(key, secret)
    return _client


def load_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"cycles": 0, "errors": 0, "last": {}}


def save_state(s: dict) -> None:
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=1)
    os.replace(tmp, STATE)


def other_loop_running() -> bool:
    """A second loop would double-size every entry — refuse to start one."""
    try:
        return (time.time() - os.path.getmtime(LOCK)) < LOCK_STALE_S
    except OSError:
        return False


def touch_lock() -> None:
    with open(LOCK, "w", encoding="utf-8") as fh:
        fh.write(f"{os.getpid()} {time.time()}\n")


def cycle(*, allow_live: bool = False, dry: bool = False) -> dict:
    """One pass. Gate first, then (only if it can trade) build the client and call.

    Returns a small verdict; never raises into the loop.
    """
    ctx = ot.death_context()
    if not ot.ARMED:
        return {"armed": False, "acted": False, "client_built": False,
                "why": (f"ARMED is False — inert (buffer ${ctx['buffer']:,.2f}, "
                        f"balance ${ctx['balance']:,.2f})")}
    if ctx["balance"] <= ctx["halt_line"]:
        return {"armed": True, "acted": False, "client_built": False,
                "why": f"at/below the halt line ${ctx['halt_line']:,.2f}"}
    if ctx["buffer"] < ot.ARM_CRITERIA["min_buffer_usd"]:
        return {"armed": True, "acted": False, "client_built": False,
                "why": (f"buffer ${ctx['buffer']:,.2f} under the "
                        f"${ot.ARM_CRITERIA['min_buffer_usd']:,.0f} floor")}
    if dry:
        return {"armed": True, "acted": False, "client_built": False,
                "why": "dry run: gates passed, client not built"}

    from config import TradingConfig

    cfg = TradingConfig()
    cfg.paper_mode = not allow_live          # explicit: real orders only with --live
    kraken = get_client()                    # built once for the process

    try:
        import scout_signal_reader as ssr
    except ImportError as exc:
        # scout_signal_reader imports restricted_pairs, which is not in this tree, so
        # the hook inside it is unreachable for now. Call the one thing that hook
        # calls rather than leave the engine unreachable; say so, don't hide it.
        import timing_live
        res = timing_live.tick(kraken, cfg)
        return {"armed": True, "acted": bool(res), "client_built": True,
                "degraded": f"scout_signal_reader unimportable: {exc}",
                "why": "timing_live.tick called directly (reader import failed)"}

    res = ssr.check_scout_signals(kraken, cfg)
    return {"armed": True, "acted": bool(res), "client_built": True,
            "why": "check_scout_signals ran (timing_live.tick is inside it)",
            "result": bool(res)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--live", action="store_true",
                    help="allow real fills; without it every order is validated only")
    ap.add_argument("--dry", action="store_true",
                    help="run the gates but never build a client (safe rehearsal)")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO)
    if not a.once and other_loop_running():
        _log("refusing to start: another timing_loop held the lock within "
             f"{LOCK_STALE_S}s ({LOCK})")
        return 2

    _log(f"timing_loop starting (ARMED={ot.ARMED}, live={a.live}, dry={a.dry}, "
         f"interval={a.interval}s)")
    s = load_state()
    while True:
        touch_lock()
        s["cycles"] = s.get("cycles", 0) + 1
        try:
            out = cycle(allow_live=a.live, dry=a.dry)
        except Exception as exc:                       # never kill the loop
            s["errors"] = s.get("errors", 0) + 1
            out = {"armed": ot.ARMED, "acted": False, "error": str(exc)}
            log.exception("cycle failed")
        s["last"] = out
        save_state(s)
        _log(f"cycle {s['cycles']} armed={out.get('armed')} "
             f"acted={out.get('acted')} client={out.get('client_built')} "
             f"— {out.get('why', out.get('error', ''))}")
        if a.once:
            return 0
        time.sleep(max(5, a.interval))


if __name__ == "__main__":
    sys.exit(main())
